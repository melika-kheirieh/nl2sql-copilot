"""
Minimal smoke/demo runner for the NL2SQL pipeline.

- Builds the pipeline via the official factory (no app/router imports).
- Runs a small set of demo questions against a SQLite DB.
- Works in two modes:
    * Stub mode (set PYTEST_CURRENT_TEST=1) → no API key needed.
    * Real mode   (set OPENAI_API_KEY=...)  → uses actual LLM provider.

Outputs:
  benchmarks/results_demo/<timestamp>/
    - demo.jsonl     # one JSON record per query
    - summary.json   # latency & success overview
    - results.csv    # compact table for quick inspection

Usage examples:
  PYTHONPATH=$PWD PYTEST_CURRENT_TEST=1 \
  python scripts/smoke_run.py --db-path demo.db

  # With a custom dataset file (JSON: list[str] or list[{question: "..."}])
  PYTHONPATH=$PWD PYTEST_CURRENT_TEST=1 \
  python scripts/smoke_run.py --db-path demo.db --dataset-file benchmarks/demo.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import sqlite3

from nl2sql.pipeline_factory import pipeline_from_config_with_adapter
from adapters.db.sqlite_adapter import SQLiteAdapter

CONFIG_PATH = "configs/sqlite_pipeline.yaml"
DEFAULT_QUESTIONS: List[str] = [
    "list all customers",
    "show total invoices per country",
    "top 3 albums by total sales",
    "artists with more than 3 albums",
    "number of employees per city",
]

RESULT_ROOT = Path("benchmarks") / "results_demo"
TIMESTAMP = time.strftime("%Y%m%d-%H%M%S")
RESULT_DIR = RESULT_ROOT / TIMESTAMP


def ensure_demo_db(db_path: Path) -> None:
    """Create a tiny demo SQLite DB if it doesn't exist."""
    if db_path.exists():
        return
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # Minimal schema that matches our default demo questions
    cur.executescript("""
    DROP TABLE IF EXISTS customers;
    DROP TABLE IF EXISTS invoices;
    DROP TABLE IF EXISTS employees;
    DROP TABLE IF EXISTS artists;
    DROP TABLE IF EXISTS albums;

    CREATE TABLE customers (
        id INTEGER PRIMARY KEY,
        name TEXT,
        country TEXT
    );

    CREATE TABLE invoices (
        id INTEGER PRIMARY KEY,
        customer_id INTEGER,
        total REAL,
        country TEXT,
        FOREIGN KEY (customer_id) REFERENCES customers(id)
    );

    CREATE TABLE employees (
        id INTEGER PRIMARY KEY,
        name TEXT,
        city TEXT
    );

    CREATE TABLE artists (
        id INTEGER PRIMARY KEY,
        name TEXT
    );

    CREATE TABLE albums (
        id INTEGER PRIMARY KEY,
        artist_id INTEGER,
        title TEXT,
        sales REAL DEFAULT 0,
        FOREIGN KEY (artist_id) REFERENCES artists(id)
    );
    """)

    # Seed a bit of data
    cur.executemany(
        "INSERT INTO customers (id, name, country) VALUES (?, ?, ?)",
        [
            (1, "Alice", "USA"),
            (2, "Bob", "Germany"),
            (3, "Carlos", "Brazil"),
            (4, "Darya", "Iran"),
        ],
    )
    cur.executemany(
        "INSERT INTO invoices (id, customer_id, total, country) VALUES (?, ?, ?, ?)",
        [
            (1, 1, 120.5, "USA"),
            (2, 2, 75.0, "Germany"),
            (3, 1, 33.2, "USA"),
            (4, 3, 48.0, "Brazil"),
            (5, 4, 90.0, "Iran"),
        ],
    )
    cur.executemany(
        "INSERT INTO employees (id, name, city) VALUES (?, ?, ?)",
        [
            (1, "Eve", "New York"),
            (2, "Frank", "Berlin"),
            (3, "Gita", "Tehran"),
        ],
    )
    cur.executemany(
        "INSERT INTO artists (id, name) VALUES (?, ?)",
        [
            (1, "ABand"),
            (2, "BGroup"),
            (3, "CEnsemble"),
        ],
    )
    cur.executemany(
        "INSERT INTO albums (id, artist_id, title, sales) VALUES (?, ?, ?, ?)",
        [
            (1, 1, "First Light", 500.0),
            (2, 1, "Second Wind", 300.0),
            (3, 2, "Blue Lines", 900.0),
            (4, 3, "Echoes", 150.0),
        ],
    )

    conn.commit()
    conn.close()


def _ms(start_s: float) -> int:
    """Convert elapsed seconds to integer milliseconds."""
    return int((time.perf_counter() - start_s) * 1000)


def _derive_schema_preview(pipeline_obj: Any) -> Optional[str]:
    """Try to derive schema preview from adapter/executor if available."""
    for attr in ("executor", "adapter"):
        obj = getattr(pipeline_obj, attr, None)
        if obj and hasattr(obj, "derive_schema_preview"):
            try:
                return obj.derive_schema_preview()  # type: ignore[no-any-return]
            except Exception:
                pass
    return None


def _normalize_trace(trace_obj: Any) -> List[Dict[str, Any]]:
    """Convert trace to a list of {stage, ms} dicts for logging/export."""
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


def _load_questions(path: Optional[str]) -> List[str]:
    """Load questions from a JSON file or return defaults."""
    if not path:
        return DEFAULT_QUESTIONS
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
        "Dataset must be a JSON array of strings or objects with a 'question' field."
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--db-path",
        type=str,
        default="demo.db",
        help="Path to SQLite DB (default: demo.db)",
    )
    ap.add_argument(
        "--dataset-file",
        type=str,
        default=None,
        help="Optional JSON file: list[str] or list[{question: str}]",
    )
    ap.add_argument(
        "--config",
        type=str,
        default=CONFIG_PATH,
        help=f"Pipeline YAML (default: {CONFIG_PATH})",
    )
    args = ap.parse_args()

    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    # Resolve DB path and ensure demo DB exists for quick smoke runs
    db_path = Path(args.db_path).resolve()
    ensure_demo_db(db_path)

    # Build pipeline via the official factory (factory decides real vs stub by env)
    adapter = SQLiteAdapter(str(db_path))
    pipeline = pipeline_from_config_with_adapter(args.config, adapter=adapter)

    schema_preview = _derive_schema_preview(pipeline)
    print(f"✅ Pipeline ready (db={db_path.name}, config={args.config})")
    print(
        "📄 Schema preview:",
        "yes" if schema_preview else "no",
        "| provider:",
        "STUBS" if os.getenv("PYTEST_CURRENT_TEST") else "REAL",
    )

    questions = _load_questions(args.dataset_file)
    print(f"🗂  Loaded {len(questions)} questions.")

    rows: List[Dict[str, Any]] = []
    for q in questions:
        print(f"\n🧠 Query: {q}")
        t0 = time.perf_counter()
        try:
            result = pipeline.run(user_query=q, schema_preview=schema_preview or "")
            latency_ms = _ms(t0) or 1  # clamp to 1ms when stubs are instant
            stages = _normalize_trace(
                getattr(result, "traces", getattr(result, "trace", []))
            )
            rows.append(
                {
                    "query": q,
                    "ok": bool(getattr(result, "ok", True)),
                    "latency_ms": latency_ms,
                    "trace": stages,
                    "error": None,
                }
            )
            print(f"✅ Success ({latency_ms} ms)")
        except Exception as exc:
            latency_ms = _ms(t0) or 1
            rows.append(
                {
                    "query": q,
                    "ok": False,
                    "latency_ms": latency_ms,
                    "trace": [],
                    "error": str(exc),
                }
            )
            print(f"❌ Failed: {exc!s} ({latency_ms} ms)")

    # Aggregate and persist
    avg_latency = (
        round(sum(r["latency_ms"] for r in rows) / max(len(rows), 1), 1)
        if rows
        else 0.0
    )
    success_rate = (
        (sum(1 for r in rows if r["ok"]) / max(len(rows), 1)) if rows else 0.0
    )
    meta = {
        "db_path": str(db_path),
        "config": args.config,
        "provider_hint": "STUBS" if os.getenv("PYTEST_CURRENT_TEST") else "REAL",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    jsonl_path = RESULT_DIR / "demo.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for r in rows:
            json.dump(r, f, ensure_ascii=False)
            f.write("\n")

    summary_path = RESULT_DIR / "summary.json"
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(
            {"avg_latency_ms": avg_latency, "success_rate": success_rate, **meta},
            f,
            indent=2,
        )

    csv_path = RESULT_DIR / "results.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=["query", "ok", "latency_ms"])
        wr.writeheader()
        for r in rows:
            wr.writerow(
                {
                    "query": r["query"],
                    "ok": "✅" if r["ok"] else "❌",
                    "latency_ms": int(r["latency_ms"]),
                }
            )

    print(
        "\n💾 Saved outputs:\n"
        f"- {jsonl_path}\n- {summary_path}\n- {csv_path}\n"
        f"📊 Avg latency: {avg_latency} ms | Success rate: {success_rate:.0%}\n"
    )


if __name__ == "__main__":
    main()
