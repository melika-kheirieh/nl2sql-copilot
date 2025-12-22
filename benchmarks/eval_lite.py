"""
Lightweight eval runner for two modes:
  1) Single-DB demo mode (default): run a list of questions against one SQLite DB.
  2) Spider mode (--spider): load a subset of the Spider dataset and run each question
     against its own database (resolved via SPIDER_ROOT).

- Uses your official pipeline factory (no app/router imports).
- Works with real LLM (OPENAI_API_KEY) or stub mode (PYTEST_CURRENT_TEST=1).
- Produces JSONL + JSON summary + CSV under benchmarks/results/<timestamp>/

Examples:
  # Demo (single DB), stub mode
  PYTHONPATH=$PWD PYTEST_CURRENT_TEST=1 \
  python benchmarks/eval_lite.py --db-path demo.db

  # Spider subset (20 items), stub mode
  export SPIDER_ROOT=$PWD/data/spider
  PYTHONPATH=$PWD PYTEST_CURRENT_TEST=1 \
  python benchmarks/eval_lite.py --spider --split dev --limit 20
Notes:
  - In stub mode, all LLM calls are mocked for offline evaluation.
  - Results are saved under benchmarks/results/<timestamp>/.
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

# Only needed in --spider mode
try:
    from benchmarks.spider_loader import load_spider_sqlite, open_readonly_connection
except Exception:
    load_spider_sqlite = None  # type: ignore[assignment]
    open_readonly_connection = None  # type: ignore[assignment]

# Resolve repo root and default config path relative to this file (not CWD)
THIS_DIR = Path(__file__).resolve().parent  # .../benchmarks
REPO_ROOT = THIS_DIR.parent  # repo root
CONFIG_PATH = str(REPO_ROOT / "configs" / "sqlite_pipeline.yaml")

DEFAULT_DATASET: List[str] = [
    "list all customers",
    "show total invoices per country",
    "top 3 albums by total sales",
    "artists with more than 3 albums",
    "number of employees per city",
]
# Back-compat for tests: monkeypatchable dataset at module level
DATASET: List[str] = list(DEFAULT_DATASET)

RESULT_ROOT = Path("benchmarks") / "results"
TIMESTAMP = time.strftime("%Y%m%d-%H%M%S")
RESULT_DIR = RESULT_ROOT / TIMESTAMP


def _int_ms(start: float) -> int:
    """Convert elapsed seconds to integer milliseconds."""
    return int((time.perf_counter() - start) * 1000)


def _derive_schema_preview_safe(pipeline_obj: Any) -> Optional[str]:
    """Safely call derive_schema_preview() if available on adapter/executor."""
    try:
        candidates = [
            getattr(pipeline_obj, "executor", None),
            getattr(pipeline_obj, "adapter", None),
        ]
        for c in candidates:
            if c and hasattr(c, "derive_schema_preview"):
                return c.derive_schema_preview()  # type: ignore[no-any-return]
    except Exception:
        pass
    return None


def _to_stage_list(trace_obj: Any) -> List[Dict[str, Any]]:
    """Normalize pipeline trace into a list of dicts for logging/CSV export."""
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


def _load_dataset_from_file(path: Optional[str]) -> List[str]:
    """
    Load dataset questions.
    Accepts either a list of strings or a list of {"question": "..."} objects.
    """
    if not path:
        # Use module-level DATASET so tests can monkeypatch it
        return list(DATASET)

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


def _ensure_demo_db(db_path: Path) -> None:
    """Create an empty SQLite DB for demo runs if it doesn't exist."""
    if db_path.exists():
        return
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        # Keep it minimal; SELECT 1 works without any tables.
        conn.execute("SELECT 1;")
    finally:
        conn.close()


def _save_outputs(rows: List[Dict[str, Any]], meta: Dict[str, Any]) -> None:
    """Persist JSONL + JSON summary + CSV (write both new and legacy filenames)."""
    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    # Filenames (new + legacy for back-compat with tests)
    jsonl_path = RESULT_DIR / "eval.jsonl"
    summary_path = RESULT_DIR / "summary.json"
    csv_path = RESULT_DIR / "results.csv"

    jsonl_path_legacy = RESULT_DIR / "spider_eval.jsonl"
    summary_path_legacy = RESULT_DIR / "metrics_summary.json"

    # --- Write JSONL (both names) ---
    with jsonl_path.open("w", encoding="utf-8") as f:
        for r in rows:
            json.dump(r, f, ensure_ascii=False)
            f.write("\n")
    # duplicate for legacy name
    with jsonl_path_legacy.open("w", encoding="utf-8") as f:
        for r in rows:
            json.dump(r, f, ensure_ascii=False)
            f.write("\n")

    # --- Build summary dict ---
    summary = {
        # keep both for compatibility with old tests/consumers
        "queries_total": len(rows),
        "total": len(rows),
        "pipeline_source": meta.get(
            "pipeline_source", "adapter"
        ),  # for backward-compat with tests
        "success_rate": (sum(1 for r in rows if r.get("ok")) / max(len(rows), 1))
        if rows
        else 0.0,
        "avg_latency_ms": (
            round(sum(int(r.get("latency_ms", 0)) for r in rows) / max(len(rows), 1), 1)
        )
        if rows
        else 0.0,
        **meta,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    # --- Write summary (both names) ---
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    with summary_path_legacy.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    # --- Write CSV (single name) ---
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["query", "ok", "latency_ms"])
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    "query": r.get("query", ""),
                    "ok": "✅" if r.get("ok") else "❌",
                    "latency_ms": int(r.get("latency_ms", 0)),
                }
            )

    print(
        "\n💾 Saved outputs:\n"
        f"- {jsonl_path} (and {jsonl_path_legacy})\n"
        f"- {summary_path} (and {summary_path_legacy})\n"
        f"- {csv_path}\n"
        f"📊 Avg latency: {summary['avg_latency_ms']} ms | "
        f"Success rate: {summary['success_rate']:.0%}\n"
    )


def _run_single_db_mode(db_path: Path, questions: List[str], config_path: str) -> None:
    """Evaluate a list of questions against a single SQLite DB."""
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

    meta = {
        "mode": "single-db",
        "db_path": str(db_path),
        "config": config_path,
        "provider_hint": ("STUBS" if os.getenv("PYTEST_CURRENT_TEST") else "REAL"),
    }
    _save_outputs(rows, meta)


def _run_spider_mode(split: str, limit: int, config_path: str) -> None:
    """Evaluate a Spider subset. Each example points to its own DB under SPIDER_ROOT."""
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

        # derive schema per-DB (optional)
        schema_preview = _derive_schema_preview_safe(pipeline)

        t0 = time.perf_counter()
        try:
            result = pipeline.run(
                user_query=ex.question, schema_preview=schema_preview or ""
            )
            latency_ms = _int_ms(t0) or 1
            stages = _to_stage_list(
                getattr(result, "traces", getattr(result, "trace", []))
            )
            rows.append(
                {
                    "source": "spider",
                    "db_id": ex.db_id,
                    "query": ex.question,
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
                    "source": "spider",
                    "db_id": ex.db_id,
                    "query": ex.question,
                    "ok": False,
                    "latency_ms": latency_ms,
                    "trace": [],
                    "error": str(exc),
                }
            )
            print(f"❌ Failed: {exc!s} ({latency_ms} ms)")

    meta = {
        "mode": "spider",
        "split": split,
        "limit": limit,
        "config": config_path,
        "provider_hint": ("STUBS" if os.getenv("PYTEST_CURRENT_TEST") else "REAL"),
        "spider_root": os.getenv("SPIDER_ROOT", ""),
    }
    _save_outputs(rows, meta)


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
    args, _unknown = ap.parse_known_args()

    if args.spider:
        # Spider mode: read items from SPIDER_ROOT and evaluate per-DB
        if not os.getenv("SPIDER_ROOT"):
            raise RuntimeError(
                "SPIDER_ROOT is not set. It must point to the folder that contains "
                "dev.json/train_spider.json and the database/ directory."
            )
        _run_spider_mode(args.split, args.limit, args.config)
    else:
        # Single-DB demo mode
        db_path = Path(args.db_path).resolve()
        # Auto-create demo DB for test/smoke runs; otherwise keep strict check
        if db_path.name == "demo.db":
            _ensure_demo_db(db_path)
        elif not db_path.exists():
            raise FileNotFoundError(f"SQLite DB not found: {db_path}")
        questions = _load_dataset_from_file(args.dataset_file)
        _run_single_db_mode(db_path, questions, args.config)


if __name__ == "__main__":
    main()
