"""
Evaluate NL2SQL pipeline performance on Spider-like queries.
Uses config-driven Pipeline, native Safety checks, and per-stage latency tracing.
Outputs: JSONL (detailed logs), JSON (metrics summary), and CSV (for README).
"""

import json
import csv
import time
from pathlib import Path
from nl2sql.pipeline import Pipeline

# ---------- Config ----------
DATASET = [
    "list all customers",
    "show total invoices per country",
    "top 3 albums by total sales",
    "artists with more than 3 albums",
    "number of employees per city",
]

CONFIG_PATH = "configs/pipeline.yaml"
RESULT_DIR = Path("benchmarks/results")
RESULT_DIR.mkdir(parents=True, exist_ok=True)

# ---------- Initialize pipeline ----------
pipeline = Pipeline.from_config(CONFIG_PATH)
print(f"✅ Loaded pipeline from {CONFIG_PATH}")

# Optional: schema preview if adapter supports it
schema_preview = None
try:
    adapter = getattr(pipeline, "executor", None)
    if adapter and hasattr(adapter, "derive_schema_preview"):
        schema_preview = adapter.derive_schema_preview()
        print("📄 Derived schema preview successfully.")
except Exception as e:
    print(f"⚠️ Could not derive schema preview: {e}")

# ---------- Evaluation ----------
records = []
for q in DATASET:
    print(f"\n🧠 Query: {q}")
    start = time.perf_counter()
    try:
        result = pipeline.run(user_query=q, schema_preview=schema_preview)
        latency = int((time.perf_counter() - start) * 1000)

        trace = getattr(result, "trace", None)
        stages = []
        if trace:
            # trace might be list of StageTrace or dicts
            try:
                for t in trace:
                    stages.append(
                        {"stage": t.get("stage", "?"), "ms": t.get("duration_ms", 0)}
                        if isinstance(t, dict)
                        else {
                            "stage": getattr(t, "stage", "?"),
                            "ms": getattr(t, "duration_ms", 0),
                        }
                    )
            except Exception:
                pass

        records.append(
            {
                "query": q,
                "ok": True,
                "latency_ms": latency,
                "trace": stages,
                "error": None,
            }
        )
        print(f"✅ Success ({latency} ms)")
    except Exception as e:
        latency = int((time.perf_counter() - start) * 1000)
        records.append(
            {
                "query": q,
                "ok": False,
                "latency_ms": latency,
                "trace": [],
                "error": str(e),
            }
        )
        print(f"❌ Failed: {e} ({latency} ms)")

# ---------- Aggregate metrics ----------
avg_latency = round(sum(r["latency_ms"] for r in records) / len(records), 1)
success_rate = sum(1 for r in records if r["ok"]) / len(records)
print(f"\n📊 Average latency: {avg_latency} ms | Success rate: {success_rate:.0%}")

summary = {
    "queries_total": len(records),
    "success_rate": success_rate,
    "avg_latency_ms": avg_latency,
    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
}

# ---------- Save outputs ----------
jsonl_path = RESULT_DIR / "spider_eval.jsonl"
with open(jsonl_path, "w", encoding="utf-8") as f:
    for r in records:
        json.dump(r, f, ensure_ascii=False)
        f.write("\n")

summary_path = RESULT_DIR / "metrics_summary.json"
with open(summary_path, "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2)

csv_path = RESULT_DIR / "results.csv"
with open(csv_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["query", "ok", "latency_ms"])
    writer.writeheader()
    for r in records:
        writer.writerow(
            {
                "query": r["query"],
                "ok": "✅" if r["ok"] else "❌",
                "latency_ms": r["latency_ms"],
            }
        )

print(f"\n💾 Saved logs to:\n- {jsonl_path}\n- {summary_path}\n- {csv_path}")
