from __future__ import annotations

import csv
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Reuse existing factories from your FastAPI router (no new DI needed)
from app.routers.nl2sql import (  # type: ignore
    _pipeline as DEFAULT_PIPELINE,
    _build_pipeline,
    _select_adapter,
)

# -------------------- Config --------------------

DATASET: List[str] = [
    "list all customers",
    "show total invoices per country",
    "top 3 albums by total sales",
    "artists with more than 3 albums",
    "number of employees per city",
]

# DB id/mode follows your router convention; adjust if needed
DB_ID: str = os.getenv("DB_MODE", "sqlite")

# Results directory with timestamped subfolder (keeps previous runs)
RESULT_ROOT = Path("benchmarks") / "results"
TIMESTAMP = time.strftime("%Y%m%d-%H%M%S")
RESULT_DIR = RESULT_ROOT / TIMESTAMP


# -------------------- Helpers --------------------


def _int_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _derive_schema_preview_safe(pipeline_obj: Any) -> Optional[str]:
    """
    Try to derive schema preview from the adapter/executor if such a method exists.
    Kept intentionally permissive to avoid tight coupling.
    """
    try:
        # common places the adapter might live
        candidates: List[Any] = [
            getattr(pipeline_obj, "executor", None),
            getattr(pipeline_obj, "adapter", None),
        ]
        for c in candidates:
            if c and hasattr(c, "derive_schema_preview"):
                return c.derive_schema_preview()  # type: ignore[no-any-return, call-arg]
    except Exception:
        pass
    return None


def _to_stage_list(trace_obj: Any) -> List[Dict[str, Any]]:
    """
    Normalize pipeline trace (list of dataclass or dict) to a list of dicts:
    [{ "stage": str, "ms": int }, ...]
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
    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    # Build pipeline from router factories (no new DI required)
    try:
        adapter = _select_adapter(DB_ID)  # e.g., "sqlite" / "postgres"
        pipeline = _build_pipeline(adapter)
        using_default = False
    except Exception:
        pipeline = DEFAULT_PIPELINE
        using_default = True

    print(
        f"✅ Pipeline ready "
        f"(db_id={DB_ID}, source={'default' if using_default else 'custom adapter'})"
    )

    # Optional schema preview
    schema_preview = _derive_schema_preview_safe(pipeline)
    if schema_preview:
        print("📄 Derived schema preview ✓")
    else:
        print("ℹ️ No schema preview (adapter does not expose it or not needed)")

    # Evaluate
    records: List[Dict[str, Any]] = []
    for q in DATASET:
        print(f"\n🧠 Query: {q}")
        t0 = time.perf_counter()
        try:
            result = pipeline.run(
                user_query=q,
                schema_preview=schema_preview or "",  # <- force str
            )
            latency_ms = _int_ms(t0)

            # ok flag -> coerce to bool for mypy and consistency
            ok_flag = bool(getattr(result, "ok", True))
            stages = _to_stage_list(getattr(result, "trace", None))

            rec: Dict[str, Any] = {
                "query": q,
                "ok": ok_flag,
                "latency_ms": latency_ms,
                "trace": stages,
                "error": None,
            }
            records.append(rec)
            print(f"✅ Success ({latency_ms} ms)")
        except Exception as exc:
            latency_ms = _int_ms(t0)
            rec = {
                "query": q,
                "ok": False,
                "latency_ms": latency_ms,
                "trace": [],
                "error": str(exc),
            }
            records.append(rec)
            print(f"❌ Failed: {exc!s} ({latency_ms} ms)")

    # Aggregate metrics
    avg_latency = (
        round(sum(r["latency_ms"] for r in records) / max(len(records), 1), 1)
        if records
        else 0.0
    )
    success_rate = (
        sum(1 for r in records if bool(r.get("ok"))) / max(len(records), 1)
        if records
        else 0.0
    )

    summary: Dict[str, Any] = {
        "queries_total": len(records),
        "success_rate": success_rate,
        "avg_latency_ms": avg_latency,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "db_id": DB_ID,
        "pipeline_source": "default" if using_default else "adapter",
    }

    # Persist outputs
    jsonl_path = RESULT_DIR / "spider_eval.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for r in records:
            json.dump(r, f, ensure_ascii=False)
            f.write("\n")

    summary_path = RESULT_DIR / "metrics_summary.json"
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    csv_path = RESULT_DIR / "results.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["query", "ok", "latency_ms"])
        writer.writeheader()
        for r in records:
            writer.writerow(
                {
                    "query": r["query"],
                    "ok": "✅" if bool(r["ok"]) else "❌",
                    "latency_ms": int(r["latency_ms"]),
                }
            )

    print(
        "\n💾 Saved outputs:\n"
        f"- {jsonl_path}\n- {summary_path}\n- {csv_path}\n"
        f"📊 Avg latency: {avg_latency} ms | Success rate: {success_rate:.0%}"
    )


if __name__ == "__main__":
    main()
