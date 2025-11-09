#!/usr/bin/env python3
"""
Enhanced Spider benchmark evaluator for NL2SQL pipeline.
No external dependencies - uses internal evaluation logic.
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

# ==================== Configuration ====================

RESULT_ROOT = Path("benchmarks/results_pro")
TIMESTAMP = time.strftime("%Y%m%d-%H%M%S")
RESULT_DIR = RESULT_ROOT / TIMESTAMP


# ==================== SQL Processing ====================


def extract_clean_sql(text: str | None) -> str:
    """Safely extract a clean SQL string from input text possibly containing markdown fences or JSON."""
    # Always initialize variable to empty string
    sql = text or ""

    # Remove markdown code fences
    sql = re.sub(r"```(?:sql)?\s*\n?", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"```\s*$", "", sql)

    # Try JSON pattern like {"sql": "..."}
    m_json = re.search(r'"sql"\s*:\s*"([^"]+)"', sql)
    if m_json:
        sql = m_json.group(1)

    # Clean escaped characters
    sql = sql.replace('\\"', '"').replace("\\n", " ").replace("\\t", " ")

    # Try to locate SQL statement keywords
    m_sql = re.search(
        r"\b(select|with|insert|update|delete)\b[\s\S]+", sql, re.IGNORECASE
    )
    if m_sql:
        sql = m_sql.group(0)
    sql = re.sub(r"\s+", " ", sql).strip().rstrip(";")
    return sql


def normalize_sql(sql: str) -> str:
    """Enhanced SQL normalization for better matching."""
    if not sql:
        return ""

    sql = sql.strip().upper()
    # Remove all whitespace variations
    sql = re.sub(r"\s+", " ", sql)
    # Remove trailing semicolon
    sql = sql.rstrip(";")

    # Remove table prefixes (e.g., singer.name -> name)
    sql = re.sub(r"\b\w+\.(\w+)\b", r"\1", sql)

    # Remove AS aliases
    sql = re.sub(r"\s+AS\s+\w+", "", sql, flags=re.IGNORECASE)

    # Remove DISTINCT if used with COUNT(*)
    sql = re.sub(r"COUNT\s*\(\s*DISTINCT\s+", "COUNT(", sql)

    # Normalize COUNT variations
    sql = re.sub(r"COUNT\s*\(\s*\w+\s*\)", "COUNT(*)", sql)

    # Remove LIMIT at end
    sql = re.sub(r"\s+LIMIT\s+\d+$", "", sql)

    # Normalize quotes
    sql = re.sub(r'"(\w+)"', r"\1", sql)
    sql = re.sub(r"`(\w+)`", r"\1", sql)

    return sql


# ==================== Schema Extraction ====================


def get_database_schema(db_path: Path) -> Dict[str, Any]:
    """Extract complete schema from SQLite database."""
    if not db_path.exists():
        return {}

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    schema: dict[str, Any] = {"tables": {}}

    try:
        # Get all tables
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        tables = cursor.fetchall()

        for (table_name,) in tables:
            # Get columns
            cursor.execute(f"PRAGMA table_info('{table_name}')")
            columns = cursor.fetchall()

            col_info = []
            for col in columns:
                col_name = col[1]
                col_type = col[2]
                is_pk = col[5]

                col_dict = {
                    "name": col_name,
                    "type": col_type,
                    "primary_key": bool(is_pk),
                }
                col_info.append(col_dict)

            # Get foreign keys
            cursor.execute(f"PRAGMA foreign_key_list('{table_name}')")
            fks = cursor.fetchall()

            fk_info = []
            for fk in fks:
                fk_info.append(
                    {
                        "column": fk[3],
                        "referenced_table": fk[2],
                        "referenced_column": fk[4],
                    }
                )

            schema["tables"][table_name] = {
                "columns": col_info,
                "foreign_keys": fk_info,
            }

    finally:
        conn.close()

    return schema


def format_schema_for_prompt(schema: Dict[str, Any]) -> str:
    """Format schema for LLM prompt."""
    if not schema or not schema.get("tables"):
        return ""

    lines = []
    for table_name, table_info in schema["tables"].items():
        cols = []
        for col in table_info["columns"]:
            col_str = f"{col['name']} {col['type']}"
            if col.get("primary_key"):
                col_str += " PRIMARY KEY"
            cols.append(col_str)

        lines.append(f"Table: {table_name}")
        lines.append(f"Columns: {', '.join(cols)}")

        if table_info.get("foreign_keys"):
            fks = []
            for fk in table_info["foreign_keys"]:
                fks.append(
                    f"{fk['column']} -> {fk['referenced_table']}.{fk['referenced_column']}"
                )
            lines.append(f"Foreign Keys: {', '.join(fks)}")

        lines.append("")  # Empty line between tables

    return "\n".join(lines).strip()


# ==================== SQL Evaluation ====================


def execute_sql(db_path: Path, sql: str) -> Tuple[bool, List[Tuple]]:
    """Execute SQL and return success flag and results."""
    if not sql:
        return False, []

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute(sql)
        results = cursor.fetchall()
        conn.close()
        return True, results
    except Exception:
        return False, []


def compare_sql_results(gold_results: List[Tuple], pred_results: List[Tuple]) -> bool:
    """Compare SQL execution results."""
    if len(gold_results) != len(pred_results):
        return False

    # Convert to sets for comparison (order independent)
    gold_set = set(gold_results)
    pred_set = set(pred_results)

    return gold_set == pred_set


def evaluate_sql_match(pred_sql: str, gold_sql: str, db_path: Path) -> Dict[str, float]:
    """Evaluate predicted SQL against gold SQL."""
    metrics = {"exact_match": 0.0, "set_match": 0.0, "exec_accuracy": 0.0}

    if not pred_sql:
        return metrics

    # Exact match
    if normalize_sql(pred_sql) == normalize_sql(gold_sql):
        metrics["exact_match"] = 1.0

    # Execution-based evaluation
    gold_success, gold_results = execute_sql(db_path, gold_sql)
    pred_success, pred_results = execute_sql(db_path, pred_sql)

    if gold_success and pred_success:
        # Set match (results match)
        if compare_sql_results(gold_results, pred_results):
            metrics["set_match"] = 1.0
            metrics["exec_accuracy"] = 1.0
        else:
            # Partial credit for successful execution
            metrics["exec_accuracy"] = 0.5

    return metrics


# ==================== Pipeline Runner ====================


@dataclass
class SpiderSample:
    """Spider dataset sample."""

    question: str
    db_id: str
    db_path: Path
    gold_sql: str


def run_pipeline_on_sample(
    pipeline: Any,
    sample: SpiderSample,
    schema_cache: Dict[str, str],
    debug: bool = False,
) -> Dict[str, Any]:
    """Run NL2SQL pipeline on a single sample."""

    # Get/cache schema
    if sample.db_id not in schema_cache:
        schema_dict = get_database_schema(sample.db_path)
        schema_str = format_schema_for_prompt(schema_dict)
        schema_cache[sample.db_id] = schema_str
        if debug:
            print(f"    [schema] Loaded {len(schema_str)} chars for {sample.db_id}")

    schema: str = schema_cache[sample.db_id]

    # Run pipeline
    try:
        result = pipeline.run(user_query=sample.question, schema_preview=schema)

        # Extract SQL from result
        if hasattr(result, "sql") and result.sql:
            pred_sql = extract_clean_sql(result.sql)
        else:
            # Try to extract from various fields
            for attr in ["final_sql", "generated_sql", "answer"]:
                if hasattr(result, attr):
                    val = getattr(result, attr)
                    if val:
                        pred_sql = extract_clean_sql(str(val))
                        if pred_sql:
                            break
            else:
                pred_sql = ""

        return {
            "ok": bool(getattr(result, "ok", True)),
            "sql": pred_sql,
            "raw_response": getattr(result, "sql", ""),
            "traces": getattr(result, "traces", []),
            "error": None,
        }

    except Exception as e:
        if debug:
            import traceback

            traceback.print_exc()
        return {
            "ok": False,
            "sql": "",
            "raw_response": "",
            "traces": [],
            "error": str(e),
        }


# ==================== Main Evaluation ====================


def main():
    parser = argparse.ArgumentParser(description="Evaluate NL2SQL on Spider")
    parser.add_argument("--spider", action="store_true", help="Run Spider evaluation")
    parser.add_argument("--split", default="dev", choices=["dev", "train"])
    parser.add_argument("--limit", type=int, help="Limit number of samples")
    parser.add_argument("--debug", action="store_true", help="Enable debug output")
    parser.add_argument("--config", default="configs/sqlite_pipeline.yaml")

    args = parser.parse_args()

    if not args.spider:
        print("Please use --spider flag to run Spider evaluation")
        return

    # Load Spider samples
    print(f"Loading Spider {args.split} split...")
    samples = load_spider_sqlite(split=args.split, limit=args.limit)

    if not samples:
        print("❌ No samples loaded. Check SPIDER_ROOT environment variable.")
        return

    print(f"✔ Loaded {len(samples)} samples")

    # Prepare results directory
    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    # Initialize schema cache
    schema_cache = {}

    # Process each sample
    results = []
    for i, spider_item in enumerate(samples, 1):
        # Convert to our sample format
        sample = SpiderSample(
            question=spider_item.question,
            db_id=spider_item.db_id,
            db_path=Path(spider_item.db_path),
            gold_sql=spider_item.gold_sql,
        )

        print(f"\n🧠 [{i}/{len(samples)}] [{sample.db_id}] {sample.question}")

        # Create adapter and pipeline for this database
        adapter = SQLiteAdapter(sample.db_path)
        pipeline = pipeline_from_config_with_adapter(args.config, adapter=adapter)

        # Run pipeline
        t0 = time.perf_counter()
        result = run_pipeline_on_sample(pipeline, sample, schema_cache, args.debug)
        latency_ms = int((time.perf_counter() - t0) * 1000)

        # Evaluate
        metrics = evaluate_sql_match(result["sql"], sample.gold_sql, sample.db_path)

        # Store result
        eval_result = {
            "source": "spider",
            "db_id": sample.db_id,
            "query": sample.question,
            "gold_sql": sample.gold_sql,
            "pred_sql": result["sql"],
            "ok": result["ok"],
            "latency_ms": latency_ms,
            "em": metrics["exact_match"],
            "sm": metrics["set_match"],
            "exec_acc": metrics["exec_accuracy"],
            "error": result.get("error"),
            "trace": result.get("traces", []),
        }
        results.append(eval_result)

        # Debug output
        if args.debug:
            status = "✅" if result["ok"] and metrics["exact_match"] == 1 else "⚠️"
            print(
                f"{status} ({latency_ms} ms) | EM={metrics['exact_match']:.0f} SM={metrics['set_match']:.0f} ExecAcc={metrics['exec_accuracy']:.1f}"
            )
            if metrics["exact_match"] < 1:
                print(f"    gold: {sample.gold_sql[:100]}")
                print(f"    pred: {result['sql'][:100] if result['sql'] else 'EMPTY'}")

    # Calculate aggregates
    total = len(results)
    successful = sum(1 for r in results if r["ok"])
    avg_em = sum(r["em"] for r in results) / total if total > 0 else 0
    avg_sm = sum(r["sm"] for r in results) / total if total > 0 else 0
    avg_ea = sum(r["exec_acc"] for r in results) / total if total > 0 else 0
    avg_latency = sum(r["latency_ms"] for r in results) / total if total > 0 else 0

    # Save results
    eval_jsonl = RESULT_DIR / "eval.jsonl"
    with open(eval_jsonl, "w") as f:
        for r in results:
            json.dump(r, f, ensure_ascii=False)
            f.write("\n")

    summary = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "total": total,
        "success": successful,
        "success_rate": round(successful / total, 3) if total else 0,
        "avg_latency_ms": round(avg_latency, 1),
        "EM": round(avg_em, 3),
        "SM": round(avg_sm, 3),
        "ExecAcc": round(avg_ea, 3),
        "split": args.split,
        "config": args.config,
    }

    (RESULT_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("\n================== Evaluation Summary ==================")
    print(f"Total samples:   {total}")
    print(f"Successful runs: {successful} ({summary['success_rate'] * 100:.1f}%)")
    print(f"Avg EM:          {summary['EM']}")
    print(f"Avg SM:          {summary['SM']}")
    print(f"Avg ExecAcc:     {summary['ExecAcc']}")
    print(f"Avg Latency:     {summary['avg_latency_ms']} ms")
    print(f"Results saved to {RESULT_DIR}")
    print("========================================================")


if __name__ == "__main__":
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    main()
