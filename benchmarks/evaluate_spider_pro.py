"""
Full benchmark for NL2SQL pipeline.

Metrics:
- EM (exact match)
- Structural Match (sqlglot AST)
- Execution Accuracy
- Safety consistency (pipeline vs AST)
- Latency (end-to-end) + per-stage trace (via pipeline if available)

Outputs:
  JSONL (logs), JSON (summary), CSV (compact table)

Run example:
    python benchmarks/evaluate_spider_pro.py --limit 10 --sleep 0.1 --db sqlite --adapter data/chinook.db
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

import sqlglot
from sqlglot.errors import ParseError

# Reuse existing factories from FastAPI router (no new DI needed)
from app.routers.nl2sql import (  # type: ignore
    _pipeline as DEFAULT_PIPELINE,
    _build_pipeline,
    _select_adapter,
)
from nl2sql.safety import Safety


# -------------------- Helpers --------------------


def _int_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _parse_sql(sql: str) -> Optional[sqlglot.Expression]:
    try:
        return sqlglot.parse_one(sql, read="sqlite")
    except ParseError:
        return None


def _is_structural_match(sql1: str, sql2: str) -> bool:
    a, b = _parse_sql(sql1), _parse_sql(sql2)
    return (a == b) if (a is not None and b is not None) else False


def _exec_sql(conn: sqlite3.Connection, sql: str) -> List[tuple]:
    try:
        cur = conn.execute(sql)
        return [tuple(r) for r in cur.fetchall()]
    except Exception:
        return []


def _derive_schema_preview_safe(pipeline_obj: Any) -> Optional[str]:
    for attr in ("executor", "adapter"):
        obj = getattr(pipeline_obj, attr, None)
        if obj is not None and hasattr(obj, "derive_schema_preview"):
            try:
                # type: ignore[no-any-return]
                return obj.derive_schema_preview()  # pragma: no cover
            except Exception:
                pass
    return None


def _to_stage_list(trace_obj: Any) -> List[Dict[str, Any]]:
    """
    Normalize pipeline trace (list of dataclass or dict) to:
    [{'stage': str, 'ms': int}, ...]
    """
    stages: List[Dict[str, Any]] = []
    if not isinstance(trace_obj, list):
        return stages

    for t in trace_obj:
        if isinstance(t, dict):
            stage = t.get("stage", "?")
            ms = t.get("duration_ms", 0)
        else:
            stage = getattr(t, "stage", "?")
            ms = getattr(t, "duration_ms", 0)
        try:
            stages.append({"stage": str(stage), "ms": int(ms)})
        except Exception:
            stages.append({"stage": str(stage), "ms": 0})
    return stages


# -------------------- Main --------------------


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10, help="Max number of examples")
    parser.add_argument("--resume", type=int, default=0, help="Skip first N examples")
    parser.add_argument(
        "--sleep", type=float, default=0.0, help="Delay (seconds) between queries"
    )
    parser.add_argument(
        "--split", type=str, default="test", help="Dataset split (placeholder)"
    )
    parser.add_argument(
        "--db", type=str, default="sqlite", help="Database ID (e.g., sqlite/postgres)"
    )
    parser.add_argument(
        "--adapter",
        type=str,
        default="data/chinook.db",
        help="SQLite file path for local eval",
    )
    args = parser.parse_args()

    # SQLite connection for execution-accuracy
    conn = sqlite3.connect(args.adapter)

    # Build pipeline from router factories
    try:
        adapter = _select_adapter(args.db)
        pipeline = _build_pipeline(adapter)
        using_default = False
    except Exception:
        pipeline = DEFAULT_PIPELINE
        using_default = True

    safety = Safety()
    schema_preview = _derive_schema_preview_safe(pipeline)
    print(f"✅ Pipeline ready (db={args.db}, default={using_default})")

    # Minimal sample dataset for demonstration; replace with real Spider subset if available
    DATASET: List[Dict[str, Any]] = [
        {
            "id": 1,
            "question": "list all customers",
            "gold_sql": "SELECT * FROM customers;",
        },
        {
            "id": 2,
            "question": "top 3 albums by total sales",
            "gold_sql": """
                SELECT a.Title, SUM(i.Quantity * i.UnitPrice) AS total
                FROM albums a
                JOIN tracks t ON a.AlbumId = t.AlbumId
                JOIN invoice_items i ON t.TrackId = i.TrackId
                GROUP BY a.AlbumId
                ORDER BY total DESC
                LIMIT 3;
            """,
        },
        {
            "id": 3,
            "question": "number of employees per city",
            "gold_sql": """
                SELECT City, COUNT(*) AS cnt
                FROM employees
                GROUP BY City
                ORDER BY cnt DESC;
            """,
        },
    ]

    sliced = DATASET[args.resume : args.resume + args.limit]

    # Eval loop
    results: List[Dict[str, Any]] = []
    for idx, ex in enumerate(sliced, start=1):
        qid = cast(int, ex.get("id", idx))
        q: str = cast(str, ex.get("question", ""))
        gold_sql: str = cast(str, ex.get("gold_sql", "")).strip()
        print(f"\n[{idx}] {q}")

        t0 = time.perf_counter()
        try:
            out = pipeline.run(user_query=q, schema_preview=(schema_preview or ""))  # type: ignore[misc]
            latency = _int_ms(t0)

            # Safely extract predicted SQL:
            sql_pred_obj = getattr(out, "sql", None)
            if sql_pred_obj is None:
                data_obj = getattr(out, "data", None)
                if data_obj is not None:
                    sql_pred_obj = getattr(data_obj, "sql", None)

            sql_pred: str = str(sql_pred_obj) if sql_pred_obj is not None else ""
            if not sql_pred.strip():
                raise ValueError("No SQL generated")

            # Metrics
            em = sql_pred.strip().lower() == gold_sql.strip().lower()
            sm = _is_structural_match(sql_pred, gold_sql)

            safe_ast = safety.check(sql_pred)  # pipeline has its own safety as well
            safe_pipeline = bool(getattr(out, "ok", True))
            safety_consistent = safe_ast.ok == safe_pipeline

            gold_exec = _exec_sql(conn, gold_sql)
            pred_exec = _exec_sql(conn, sql_pred)
            exec_acc = gold_exec == pred_exec

            stages = _to_stage_list(getattr(out, "trace", None))

            results.append(
                {
                    "id": qid,
                    "question": q,
                    "sql_pred": sql_pred,
                    "sql_gold": gold_sql,
                    "em": em,
                    "sm": sm,
                    "exec_acc": exec_acc,
                    "safety_consistent": safety_consistent,
                    "latency_ms": latency,
                    "trace": stages,
                    "error": None,
                }
            )
            print(f"✅ OK | EM={em} | SM={sm} | Exec={exec_acc} | {latency} ms")

        except Exception as e:
            latency = _int_ms(t0)
            results.append(
                {
                    "id": qid,
                    "question": q,
                    "sql_pred": None,
                    "sql_gold": gold_sql,
                    "em": False,
                    "sm": False,
                    "exec_acc": False,
                    "safety_consistent": None,
                    "latency_ms": latency,
                    "trace": [],
                    "error": str(e),
                }
            )
            print(f"❌ Fail ({latency} ms): {e}")
        time.sleep(args.sleep)

    # Summary
    total = len(results)
    avg_latency = round(sum(r["latency_ms"] for r in results) / max(total, 1), 1)
    em_rate = (sum(1 for r in results if r["em"]) / max(total, 1)) if total else 0.0
    sm_rate = (sum(1 for r in results if r["sm"]) / max(total, 1)) if total else 0.0
    exec_acc_rate = (
        (sum(1 for r in results if r["exec_acc"]) / max(total, 1)) if total else 0.0
    )

    summary: Dict[str, Any] = {
        "total": total,
        "avg_latency_ms": avg_latency,
        "EM": em_rate,
        "SM": sm_rate,
        "ExecAcc": exec_acc_rate,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "db": args.db,
        "using_default_pipeline": using_default,
    }

    # Persist outputs (timestamped dir)
    out_dir = Path("benchmarks") / "results_pro" / time.strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    jsonl_path = out_dir / "spider_eval_pro.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for r in results:
            json.dump(r, f, ensure_ascii=False)
            f.write("\n")

    json_path = out_dir / "summary.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    csv_path = out_dir / "summary.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["id", "question", "em", "sm", "exec_acc", "latency_ms"],
        )
        writer.writeheader()
        for r in results:
            writer.writerow(
                {
                    "id": r["id"],
                    "question": r["question"],
                    "em": "✅" if r["em"] else "❌",
                    "sm": "✅" if r["sm"] else "❌",
                    "exec_acc": "✅" if r["exec_acc"] else "❌",
                    "latency_ms": r["latency_ms"],
                }
            )

    print("\n📊 Summary:", json.dumps(summary, indent=2))
    print(f"💾 Saved to:\n- {jsonl_path}\n- {json_path}\n- {csv_path}")


if __name__ == "__main__":
    main()
