"""
Pro evaluation runner with two modes:
Extension of `evaluate_spider.py` with additional metrics (EM, SM, ExecAcc) and richer logging for research-style benchmarking.

1) Single-DB demo mode (default)
   - Runs a list of questions against one SQLite DB
   - Reports latency/ok (no EM/SM/ExecAcc because there's no gold SQL)

2) Spider mode (--spider)
   - Loads a subset of the Spider dataset via SPIDER_ROOT
   - For each item, builds a per-DB pipeline and computes:
       * EM (exact SQL string match, case-insensitive)
       * SM (structural match via sqlglot AST)
       * ExecAcc (result equivalence by executing gold vs. predicted SQL)
   - Also logs latency, (optional) traces, and aggregates a summary

Works with:
- Real LLM (OPENAI_API_KEY set)
- Stub mode (PYTEST_CURRENT_TEST=1) for zero-cost offline runs

Outputs:
  benchmarks/results_pro/<timestamp>/
    - eval.jsonl        # per-sample rows
    - summary.json      # aggregate metrics
    - results.csv       # human-friendly table

Examples:
  # Demo (single DB), stub mode
  PYTHONPATH=$PWD PYTEST_CURRENT_TEST=1 \
  python benchmarks/evaluate_spider_pro.py --db-path demo.db

  # Spider subset (20 items), stub mode
  export SPIDER_ROOT=$PWD/data/spider
  PYTHONPATH=$PWD PYTEST_CURRENT_TEST=1 \
  python benchmarks/evaluate_spider_pro.py --spider --split dev --limit 20
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import sqlglot
from sqlglot.errors import ParseError

from nl2sql.pipeline_factory import pipeline_from_config_with_adapter
from adapters.db.sqlite_adapter import SQLiteAdapter

# Only needed for Spider mode
try:
    from benchmarks.spider_loader import load_spider_sqlite, open_readonly_connection
except Exception:
    load_spider_sqlite = None  # type: ignore[assignment]
    open_readonly_connection = None  # type: ignore[assignment]

# Resolve repo root and default config path relative to this file (not CWD)
THIS_DIR = Path(__file__).resolve().parent  # .../benchmarks
REPO_ROOT = THIS_DIR.parent  # repo root
CONFIG_PATH = str(REPO_ROOT / "configs" / "sqlite_pipeline.yaml")


# Default demo questions for single-DB mode
DEFAULT_DATASET: List[str] = [
    "list all customers",
    "show total invoices per country",
    "top 3 albums by total sales",
    "artists with more than 3 albums",
    "number of employees per city",
]

RESULT_ROOT = Path("benchmarks") / "results_pro"
TIMESTAMP = time.strftime("%Y%m%d-%H%M%S")
RESULT_DIR = RESULT_ROOT / TIMESTAMP


# -------------------- Utilities --------------------


def _int_ms(start: float) -> int:
    """Convert elapsed seconds to integer milliseconds."""
    return int((time.perf_counter() - start) * 1000)


def _derive_schema_preview_safe(pipeline_obj: Any) -> Optional[str]:
    """Safely call derive_schema_preview() if available on adapter/executor."""
    try:
        for c in (
            getattr(pipeline_obj, "executor", None),
            getattr(pipeline_obj, "adapter", None),
        ):
            if c and hasattr(c, "derive_schema_preview"):
                return c.derive_schema_preview()  # type: ignore[no-any-return]
    except Exception:
        pass
    return None


def _to_stage_list(trace_obj: Any) -> List[Dict[str, Any]]:
    """Normalize pipeline trace into a list of dicts for logging/export."""
    out: List[Dict[str, Any]] = []
    if not isinstance(trace_obj, list):
        return out
    for t in trace_obj:
        if isinstance(t, dict):
            stage = t.get("stage", "?")
            ms = t.get("duration_ms", 0)
        else:
            stage = getattr(t, "stage", "?")
            ms = getattr(t, "duration_ms", 0)
        try:
            out.append({"stage": str(stage), "ms": int(ms)})
        except Exception:
            out.append({"stage": str(stage), "ms": 0})
    return out


def _parse_sql(sql: str):
    try:
        return sqlglot.parse_one(sql, read="sqlite")
    except ParseError:
        return None


def _structural_match(pred: str, gold: str) -> bool:
    """AST-level equality via sqlglot; returns False if either side can't be parsed."""
    a, b = _parse_sql(pred), _parse_sql(gold)
    return (a == b) if (a is not None and b is not None) else False


def _load_dataset_from_file(path: Optional[str]) -> List[str]:
    """Load questions from a JSON file: list[str] or list[{question: str}]."""
    if not path:
        return DEFAULT_DATASET
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"dataset file not found: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, list):
        if all(isinstance(x, str) for x in data):
            return list(data)
        if all(isinstance(x, dict) and "question" in x for x in data):
            return [str(x["question"]) for x in data]
    raise ValueError(
        "Dataset file must be a JSON array of strings or objects with 'question' field."
    )


def _extract_sql(result: Any) -> str:
    """
    Extract SQL from pipeline result in a mypy-friendly way.
    Supports both result.sql and result.data.sql shapes.
    """
    sql_pred: Optional[str] = getattr(result, "sql", None)
    if not sql_pred:
        data = getattr(result, "data", None)
        if data is not None:
            sql_pred = getattr(data, "sql", None)
    return (sql_pred or "").strip()


def _save_outputs(rows: List[Dict[str, Any]], summary: Dict[str, Any]) -> None:
    """Persist JSONL + JSON summary + CSV for pro runner."""
    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    jsonl_path = RESULT_DIR / "eval.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    with (RESULT_DIR / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    csv_path = RESULT_DIR / "results.csv"
    # For pro, include pro columns when present (Spider mode)
    fieldnames = [
        "source",
        "db_id",
        "query",
        "em",
        "sm",
        "exec_acc",
        "ok",
        "latency_ms",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=fieldnames)
        wr.writeheader()
        for r in rows:
            wr.writerow(
                {
                    "source": r.get("source", "demo"),
                    "db_id": r.get("db_id", ""),
                    "query": r.get("query", ""),
                    "em": "✅" if r.get("em") else "❌" if "em" in r else "",
                    "sm": "✅" if r.get("sm") else "❌" if "sm" in r else "",
                    "exec_acc": "✅"
                    if r.get("exec_acc")
                    else "❌"
                    if "exec_acc" in r
                    else "",
                    "ok": "✅" if r.get("ok") else "❌",
                    "latency_ms": int(r.get("latency_ms", 0)),
                }
            )

    print(
        "\n💾 Saved outputs:\n"
        f"- {jsonl_path}\n- {RESULT_DIR / 'summary.json'}\n- {csv_path}\n"
        f"📊 Avg latency: {summary.get('avg_latency_ms', 0.0)} ms "
        f"| EM: {summary.get('EM', 0.0):.3f} "
        f"| SM: {summary.get('SM', 0.0):.3f} "
        f"| ExecAcc: {summary.get('ExecAcc', 0.0):.3f} "
        f"| Success: {summary.get('success_rate', 0.0):.0%}\n"
    )


# -------------------- Runners --------------------


def _run_single_db_mode(db_path: Path, questions: List[str], config_path: str) -> None:
    """
    Single-DB demo mode.
    Only latency/ok is reported (no EM/SM/ExecAcc, because we don't have gold SQL).
    """
    adapter = SQLiteAdapter(str(db_path))
    pipeline = pipeline_from_config_with_adapter(config_path, adapter=adapter)

    schema_preview = _derive_schema_preview_safe(pipeline)
    if schema_preview:
        print("📄 Derived schema preview ✓")
    else:
        print("ℹ️ No schema preview (adapter does not expose it or not needed)")

    rows: List[Dict[str, Any]] = []
    for q in questions:
        print(f"\n🧠 Query: {q}")
        t0 = time.perf_counter()
        try:
            result = pipeline.run(user_query=q, schema_preview=schema_preview or "")
            latency_ms = _int_ms(t0) or 1  # clamp to 1ms for nicer CSV in stub mode
            stages = _to_stage_list(
                getattr(result, "traces", getattr(result, "trace", []))
            )
            rows.append(
                {
                    "source": "demo",
                    "db_id": Path(db_path).stem,
                    "query": q,
                    "ok": bool(getattr(result, "ok", True)),
                    "latency_ms": latency_ms,
                    "trace": stages,
                    "error": None,
                }
            )
            print(f"✅ Success ({latency_ms} ms)")
        except Exception as exc:
            latency_ms = _int_ms(t0) or 1
            rows.append(
                {
                    "source": "demo",
                    "db_id": Path(db_path).stem,
                    "query": q,
                    "ok": False,
                    "latency_ms": latency_ms,
                    "trace": [],
                    "error": str(exc),
                }
            )
            print(f"❌ Failed: {exc!s} ({latency_ms} ms)")

    success_rate = (
        (sum(1 for r in rows if r.get("ok")) / max(len(rows), 1)) if rows else 0.0
    )
    avg_latency = (
        round(sum(int(r.get("latency_ms", 0)) for r in rows) / max(len(rows), 1), 1)
        if rows
        else 0.0
    )
    summary = {
        "mode": "single-db",
        "db_path": str(db_path),
        "config": config_path,
        "provider_hint": ("STUBS" if os.getenv("PYTEST_CURRENT_TEST") else "REAL"),
        "total": len(rows),
        "EM": 0.0,
        "SM": 0.0,
        "ExecAcc": 0.0,  # not applicable in demo
        "success_rate": success_rate,
        "avg_latency_ms": avg_latency,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_outputs(rows, summary)


def _run_spider_mode(split: str, limit: int, config_path: str) -> None:
    """
    Spider mode: compute EM/SM/ExecAcc with per-DB pipelines.
    Requires SPIDER_ROOT pointing to a folder that contains dev.json/train_spider.json and database/.
    """
    if load_spider_sqlite is None or open_readonly_connection is None:
        raise RuntimeError(
            "Spider utilities are not available. Ensure benchmarks/spider_loader.py exists."
        )

    items = load_spider_sqlite(split=split, limit=limit)
    print(f"🗂  Loaded {len(items)} Spider items (split={split}).")

    rows: List[Dict[str, Any]] = []

    for i, ex in enumerate(items, 1):
        print(f"\n[{i}] {ex.db_id} :: {ex.question}")
        adapter = SQLiteAdapter(ex.db_path)
        pipeline = pipeline_from_config_with_adapter(config_path, adapter=adapter)

        # Optional schema preview per DB
        schema_preview = _derive_schema_preview_safe(pipeline)

        # Open read-only connection for ExecAcc computation
        conn = open_readonly_connection(ex.db_path)

        t0 = time.perf_counter()
        try:
            result = pipeline.run(
                user_query=ex.question, schema_preview=schema_preview or ""
            )
            latency_ms = _int_ms(t0) or 1
            stages = _to_stage_list(
                getattr(result, "traces", getattr(result, "trace", []))
            )

            # Extract predicted SQL from result (support both .sql and .data.sql)
            sql_pred = _extract_sql(result)

            # Pro metrics
            gold_sql = ex.gold_sql.strip()
            em = (sql_pred.lower() == gold_sql.lower()) if sql_pred else False
            sm = _structural_match(sql_pred, gold_sql) if sql_pred else False

            try:
                gold_exec = conn.execute(gold_sql).fetchall()
            except Exception:
                gold_exec = []
            try:
                pred_exec = conn.execute(sql_pred).fetchall() if sql_pred else []
            except Exception:
                pred_exec = []
            exec_acc = gold_exec == pred_exec

            rows.append(
                {
                    "source": "spider",
                    "db_id": ex.db_id,
                    "query": ex.question,
                    "sql_pred": sql_pred,
                    "sql_gold": gold_sql,
                    "em": em,
                    "sm": sm,
                    "exec_acc": exec_acc,
                    "ok": bool(getattr(result, "ok", True)),
                    "latency_ms": latency_ms,
                    "trace": stages,
                    "error": None,
                }
            )
            print(f"✅ OK | EM={em} | SM={sm} | Exec={exec_acc} | {latency_ms} ms")
        except Exception as exc:
            latency_ms = _int_ms(t0) or 1
            rows.append(
                {
                    "source": "spider",
                    "db_id": ex.db_id,
                    "query": ex.question,
                    "sql_pred": None,
                    "sql_gold": ex.gold_sql,
                    "em": False,
                    "sm": False,
                    "exec_acc": False,
                    "ok": False,
                    "latency_ms": latency_ms,
                    "trace": [],
                    "error": str(exc),
                }
            )
            print(f"❌ Fail: {exc!s} ({latency_ms} ms)")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # Aggregate pro metrics
    total = len(rows)
    em_rate = (sum(1 for r in rows if r.get("em")) / max(total, 1)) if rows else 0.0
    sm_rate = (sum(1 for r in rows if r.get("sm")) / max(total, 1)) if rows else 0.0
    exec_rate = (
        (sum(1 for r in rows if r.get("exec_acc")) / max(total, 1)) if rows else 0.0
    )
    success_rate = (
        (sum(1 for r in rows if r.get("ok")) / max(total, 1)) if rows else 0.0
    )
    avg_latency = (
        round(sum(int(r.get("latency_ms", 0)) for r in rows) / max(total, 1), 1)
        if rows
        else 0.0
    )

    summary = {
        "mode": "spider",
        "split": split,
        "limit": limit,
        "config": config_path,
        "provider_hint": ("STUBS" if os.getenv("PYTEST_CURRENT_TEST") else "REAL"),
        "spider_root": os.getenv("SPIDER_ROOT", ""),
        "total": total,
        "EM": round(em_rate, 3),
        "SM": round(sm_rate, 3),
        "ExecAcc": round(exec_rate, 3),
        "success_rate": success_rate,
        "avg_latency_ms": avg_latency,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_outputs(rows, summary)


# -------------------- CLI --------------------


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--spider",
        action="store_true",
        help="Enable Spider mode (reads from SPIDER_ROOT; ignores --db-path).",
    )
    ap.add_argument(
        "--split",
        type=str,
        default="dev",
        choices=["dev", "train"],
        help="Spider split to use (default: dev).",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Number of Spider items to evaluate (default: 20).",
    )

    ap.add_argument(
        "--db-path",
        type=str,
        default="demo.db",
        help="Path to SQLite database file (single-DB mode).",
    )
    ap.add_argument(
        "--dataset-file",
        type=str,
        default=None,
        help="Optional JSON file with questions (single-DB mode).",
    )
    ap.add_argument(
        "--config",
        type=str,
        default=CONFIG_PATH,
        help=f"Pipeline YAML config (default: {CONFIG_PATH})",
    )
    args = ap.parse_args()

    if args.spider:
        if not os.getenv("SPIDER_ROOT"):
            raise RuntimeError(
                "SPIDER_ROOT is not set. It must point to the folder that directly contains "
                "dev.json/train_spider.json and the database/ directory."
            )
        _run_spider_mode(args.split, args.limit, args.config)
    else:
        db_path = Path(args.db_path).resolve()
        if not db_path.exists():
            raise FileNotFoundError(f"SQLite DB not found: {db_path}")
        questions = _load_dataset_from_file(args.dataset_file)
        _run_single_db_mode(db_path, questions, args.config)


if __name__ == "__main__":
    main()
