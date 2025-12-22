"""
Spider benchmark evaluator (pro):
- Computes EM / SM / ExecAcc vs. gold SQL
- Records per-sample latency and (if present) per-stage timings from pipeline traces
- Persists eval.jsonl (per-sample), summary.json (aggregates incl. p50/p95, per-stage means), results.csv
- No external deps; percentile and normalization are implemented locally.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from nl2sql.pipeline_factory import pipeline_from_config_with_adapter
from adapters.db.sqlite_adapter import SQLiteAdapter
from benchmarks.spider_loader import load_spider_sqlite

# -------------------------- Config --------------------------

RESULT_ROOT = Path("benchmarks/results_pro")
TIMESTAMP = time.strftime("%Y%m%d-%H%M%S")
RESULT_DIR = RESULT_ROOT / TIMESTAMP
STAGES = [
    "detector",
    "planner",
    "generator",
    "safety",
    "executor",
    "verifier",
    "repair",
]

# -------------------------- SQL utils -----------------------


def extract_clean_sql(text: str | None) -> str:
    """Extract a clean SQL string from LLM-ish output (may include fences/JSON)."""
    sql = (text or "").strip()

    # strip ```sql fences
    sql = re.sub(r"```(?:sql)?\s*", "", sql, flags=re.I)
    sql = sql.replace("```", "")

    # JSON-like {"sql": "..."}
    m = re.search(r'"sql"\s*:\s*"([^"]+)"', sql)
    if m:
        sql = m.group(1)

    # unescape
    sql = sql.replace('\\"', '"').replace("\\n", " ").replace("\\t", " ")

    # find first SQL-ish keyword
    m2 = re.search(r"\b(select|with|insert|update|delete)\b[\s\S]+", sql, re.I)
    if m2:
        sql = m2.group(0)

    sql = re.sub(r"\s+", " ", sql).strip().rstrip(";")
    return sql


def normalize_sql(sql: str) -> str:
    """
    Conservative normalization for exact-match (EM):
    - Trim, collapse spaces, drop trailing ';'
    - Drop trailing 'LIMIT n'
    - Remove table prefixes only in single-table, no-join queries
    - Unquote identifiers like `name` or "name"
    - Uppercase common SQL keywords (string literals unaffected)
    """
    if not sql:
        return ""
    s = sql.strip()

    # Collapse whitespace early and drop trailing ';'
    s = re.sub(r"\s+", " ", s).strip().rstrip(";")

    # Drop trailing LIMIT n
    s = re.sub(r"(?i)\s+LIMIT\s+\d+\s*$", "", s)

    # Remove table prefixes only if single FROM and no JOIN
    lower = s.lower()
    if lower.count(" from ") == 1 and " join " not in lower:
        m = re.search(r"(?i)\bfrom\s+([a-z_][a-z0-9_]*)", s, flags=re.IGNORECASE)
        if m:
            table = m.group(1)
            s = re.sub(rf"\b{re.escape(table)}\.(\w+)\b", r"\1", s)

    # Unquote identifiers: `foo` -> foo, "foo" -> foo (strings '...' remain)
    s = re.sub(r"`([A-Za-z_]\w*)`", r"\1", s)
    s = re.sub(r'"([A-Za-z_]\w*)"', r"\1", s)

    # Normalize comma spacing: "a ,  b" -> "a, b"
    s = re.sub(r"\s*,\s*", ", ", s)

    # Final whitespace collapse
    s = re.sub(r"\s+", " ", s).strip()

    # Uppercase common keywords (word-boundary safe)
    for kw in [
        "select",
        "from",
        "where",
        "group by",
        "order by",
        "having",
        "limit",
        "join",
        "on",
        "and",
        "or",
        "asc",
        "desc",
    ]:
        s = re.sub(rf"(?i)\b{kw}\b", kw.upper(), s)

    return s


# ---------------------- Schema extraction -------------------


def get_database_schema(db_path: Path) -> Dict[str, Any]:
    """Extract schema from SQLite database (tables, columns, FKs)."""
    schema: Dict[str, Any] = {"tables": {}}
    if not db_path.exists():
        return schema

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        for (table,) in cur.fetchall():
            cur.execute(f"PRAGMA table_info('{table}')")
            cols = [
                {"name": c[1], "type": c[2], "primary_key": bool(c[5])}
                for c in cur.fetchall()
            ]
            cur.execute(f"PRAGMA foreign_key_list('{table}')")
            fks = [
                {"column": fk[3], "referenced_table": fk[2], "referenced_column": fk[4]}
                for fk in cur.fetchall()
            ]
            schema["tables"][table] = {"columns": cols, "foreign_keys": fks}
    finally:
        conn.close()
    return schema


def format_schema_for_prompt(schema: Dict[str, Any]) -> str:
    """Plain-text schema for prompt (minimal but helpful)."""
    if not schema.get("tables"):
        return ""
    lines: List[str] = []
    for t, info in schema["tables"].items():
        cols = [
            f"{c['name']} {c['type']}{' PK' if c.get('primary_key') else ''}"
            for c in info.get("columns", [])
        ]
        lines.append(f"Table: {t}")
        lines.append(f"Columns: {', '.join(cols)}")
        fks = info.get("foreign_keys") or []
        if fks:
            lines.append(
                "FKs: "
                + ", ".join(
                    f"{fk['column']} -> {fk['referenced_table']}.{fk['referenced_column']}"
                    for fk in fks
                )
            )
        lines.append("")
    return "\n".join(lines).strip()


# ---------------------- Exec/eval metrics -------------------


def _exec_sql(db: Path, sql: str) -> Tuple[bool, List[Tuple]]:
    if not sql:
        return False, []
    try:
        conn = sqlite3.connect(str(db))
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        conn.close()
        return True, rows
    except Exception:
        return False, []


def _same_rows(a: List[Tuple], b: List[Tuple]) -> bool:
    return set(a) == set(b) and len(a) == len(b)


def evaluate_sql(pred: str, gold: str, db: Path) -> Dict[str, float]:
    """Return {'em', 'sm', 'exec'} in {0.0,1.0} (sm ~ set-match)."""
    em = 1.0 if normalize_sql(pred) == normalize_sql(gold) else 0.0

    gold_ok, gold_rows = _exec_sql(db, gold)
    pred_ok, pred_rows = _exec_sql(db, pred)

    sm = 0.0
    exec_acc = 0.0
    if gold_ok and pred_ok:
        if _same_rows(gold_rows, pred_rows):
            sm = 1.0
            exec_acc = 1.0
        else:
            exec_acc = 0.5  # partial credit for executing but mismatched rows
    return {"em": em, "sm": sm, "exec": exec_acc}


# ---------------------- Trace flatten helpers -------------------
def _flatten_trace_entry(d: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(d or {})
    notes = out.pop("notes", {}) or {}
    # promote selected keys to top-level for easier analysis
    for k in (
        "tokens_in",
        "tokens_out",
        "cost_usd",
        "sql_length",
        "row_count",
        "verified",
        "error_type",
        "repair_attempts",
        "skipped",
        "col_count",
    ):
        if k in notes:
            out[k] = notes[k]
    if notes:
        out["notes"] = notes
    return out


def _per_stage_ms(trace_list: List[Dict[str, Any]]) -> Dict[str, float]:
    acc = {s: 0.0 for s in STAGES}
    cnt = {s: 0 for s in STAGES}
    for t in trace_list:
        s = t.get("stage")
        if s in acc:
            ms = t.get("duration_ms", t.get("ms", 0.0))
            try:
                acc[s] += float(ms)
                cnt[s] += 1
            except Exception:
                pass
    return {s: round(acc[s] / cnt[s], 2) if cnt[s] else 0.0 for s in STAGES}


# ---------------------- Dataclass + runner ------------------


@dataclass
class SpiderSample:
    question: str
    db_id: str
    db_path: Path
    gold_sql: str


def _percentile(values: List[float], p: float) -> float:
    """Compute p-th percentile (0..100) without numpy."""
    if not values:
        return 0.0
    vals = sorted(values)
    k = (len(vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    if f == c:
        return float(vals[int(k)])
    return float(vals[f] * (c - k) + vals[c] * (k - f))


def _stage_ms_from_trace(trace_item: Dict[str, Any]) -> float:
    """Accepts {'stage':..., 'ms':...} OR {'stage':..., 'duration_ms':...}."""
    if not trace_item:
        return 0.0
    if "ms" in trace_item:
        try:
            return float(trace_item["ms"])
        except Exception:
            return 0.0
    if "duration_ms" in trace_item:
        try:
            return float(trace_item["duration_ms"])
        except Exception:
            return 0.0
    return 0.0


def _collect_stage_means(eval_rows: List[Dict[str, Any]]) -> Dict[str, float]:
    """Average per-stage ms across all records (0 if absent)."""
    totals = {s: 0.0 for s in STAGES}
    counts = {s: 0 for s in STAGES}
    for r in eval_rows:
        trace_list = r.get("trace") or r.get("traces") or []
        for t in trace_list:
            s = t.get("stage")
            if s in totals:
                ms = _stage_ms_from_trace(t)
                totals[s] += ms
                counts[s] += 1
    return {s: round(totals[s] / counts[s], 2) if counts[s] else 0.0 for s in STAGES}


def run_pipeline_on_sample(
    pipeline: Any,
    sample: SpiderSample,
    schema_cache: Dict[str, str],
    debug: bool = False,
) -> Dict[str, Any]:
    """Run pipeline on one sample and extract normalized prediction + traces."""
    # cache schema
    if sample.db_id not in schema_cache:
        schema_dict = get_database_schema(sample.db_path)
        schema_cache[sample.db_id] = format_schema_for_prompt(schema_dict)
        if debug:
            print(
                f"    [schema] Loaded {len(schema_cache[sample.db_id])} chars for {sample.db_id}"
            )

    schema = schema_cache[sample.db_id]

    try:
        res = pipeline.run(user_query=sample.question, schema_preview=schema)
        # extract SQL
        pred_sql = ""
        if hasattr(res, "sql") and res.sql:
            pred_sql = extract_clean_sql(res.sql)
        else:
            for attr in ("final_sql", "generated_sql", "answer"):
                if getattr(res, attr, None):
                    pred_sql = extract_clean_sql(str(getattr(res, attr)))
                    if pred_sql:
                        break
        return {
            "ok": bool(getattr(res, "ok", True)),
            "sql": pred_sql,
            "trace": getattr(res, "traces", []) or getattr(res, "trace", []),
            "error": None,
        }
    except Exception as e:
        if debug:
            import traceback

            traceback.print_exc()
        return {"ok": False, "sql": "", "trace": [], "error": str(e)}


# --------------------------- Main --------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate NL2SQL on Spider (pro)")
    ap.add_argument("--spider", action="store_true", help="Use Spider dataset loader")
    ap.add_argument("--split", default="dev", choices=["dev", "train"])
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--config", default="configs/sqlite_pipeline.yaml")
    args = ap.parse_args()

    if not args.spider:
        print("Use --spider to run Spider evaluation.")
        return

    # load items
    print(f"Loading Spider {args.split} split...")
    items = load_spider_sqlite(split=args.split, limit=args.limit)
    if not items:
        print("❌ No samples loaded. Check SPIDER_ROOT.")
        return
    print(f"✔ Loaded {len(items)} samples")

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    schema_cache: Dict[str, str] = {}
    eval_rows: List[Dict[str, Any]] = []

    for i, it in enumerate(items, 1):
        sample = SpiderSample(
            question=it.question,
            db_id=it.db_id,
            db_path=Path(it.db_path),
            gold_sql=it.gold_sql,
        )
        print(f"\n🧠 [{i}/{len(items)}] [{sample.db_id}] {sample.question}")

        adapter = SQLiteAdapter(str(sample.db_path))
        pipeline = pipeline_from_config_with_adapter(args.config, adapter=adapter)

        t0 = time.perf_counter()
        out = run_pipeline_on_sample(pipeline, sample, schema_cache, args.debug)
        latency_ms = int((time.perf_counter() - t0) * 1000)

        metrics = evaluate_sql(out["sql"], sample.gold_sql, sample.db_path)
        row = {
            "source": "spider",
            "db_id": sample.db_id,
            "query": sample.question,
            "gold_sql": sample.gold_sql,
            "pred_sql": out["sql"],
            "ok": out["ok"],
            "latency_ms": latency_ms,
            "em": metrics["em"],
            "sm": metrics["sm"],
            "exec_acc": metrics["exec"],
            "error": out.get("error"),
            "trace": out.get("trace", []),
        }
        eval_rows.append(row)

        if args.debug:
            status = "✅" if row["ok"] and row["em"] == 1.0 else "⚠️"
            print(
                f"{status} ({latency_ms} ms) | EM={row['em']} SM={row['sm']} ExecAcc={row['exec_acc']}"
            )
            if row["em"] < 1.0:
                print(f"    gold: {sample.gold_sql}")
                print(f"    pred: {out['sql'] or 'EMPTY'}")

    # persist eval.jsonl
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    with (RESULT_DIR / "eval.jsonl").open("w", encoding="utf-8") as f:
        for r in eval_rows:
            json.dump(r, f, ensure_ascii=False)
            f.write("\n")

    # aggregates
    total = len(eval_rows)
    success = sum(1 for r in eval_rows if r["ok"])
    avg_em = sum(r["em"] for r in eval_rows) / total if total else 0.0
    avg_sm = sum(r["sm"] for r in eval_rows) / total if total else 0.0
    avg_exec = sum(r["exec_acc"] for r in eval_rows) / total if total else 0.0
    avg_lat = sum(r["latency_ms"] for r in eval_rows) / total if total else 0.0
    p50 = _percentile([r["latency_ms"] for r in eval_rows], 50.0)
    p95 = _percentile([r["latency_ms"] for r in eval_rows], 95.0)

    stage_means = _collect_stage_means(eval_rows)

    summary = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "split": args.split,
        "config": args.config,
        "total": total,
        "success": success,
        "success_rate": round(success / total, 3) if total else 0.0,
        "avg_latency_ms": round(avg_lat, 1),
        "p50_latency_ms": round(p50, 1),
        "p95_latency_ms": round(p95, 1),
        "EM": round(avg_em, 3),
        "SM": round(avg_sm, 3),
        "ExecAcc": round(avg_exec, 3),
        **{f"{s}_avg_ms": stage_means[s] for s in STAGES},
    }

    (RESULT_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # CSV
    with (RESULT_DIR / "results.csv").open("w", encoding="utf-8") as f:
        f.write("db_id,query,ok,em,sm,exec_acc,latency_ms\n")
        for r in eval_rows:
            f.write(
                f"{r['db_id']},{json.dumps(r['query'])},{'✅' if r['ok'] else '❌'},"
                f"{r['em']},{r['sm']},{r['exec_acc']},{r['latency_ms']}\n"
            )

    print("\n================== Evaluation Summary ==================")
    print(f"Total samples:   {total}")
    print(f"Successful runs: {success} ({summary['success_rate'] * 100:.1f}%)")
    print(f"Avg EM:          {summary['EM']}")
    print(f"Avg SM:          {summary['SM']}")
    print(f"Avg ExecAcc:     {summary['ExecAcc']}")
    print(
        f"Avg Latency:     {summary['avg_latency_ms']} ms | p50={summary['p50_latency_ms']} ms | p95={summary['p95_latency_ms']} ms"
    )
    print(f"Results saved to {RESULT_DIR}")
    print("========================================================")


if __name__ == "__main__":
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    main()
