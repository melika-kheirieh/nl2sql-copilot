"""
Plot evaluation summaries for NL2SQL Copilot benchmark runs.

Automatically detects the latest results folder under benchmarks/results_pro/,
reads summary.json + eval.jsonl, and plots:
  1. Average latency per pipeline stage (ms)
  2. EM / SM / ExecAcc overview

If summary.json lacks per-stage averages, they are derived from eval.jsonl traces.
"""

import json
import time
from pathlib import Path
import matplotlib.pyplot as plt

# -------------------------------------------------------------------
# Locate latest results directory
# -------------------------------------------------------------------

ROOT = Path("benchmarks/results_pro")
run_dirs = sorted(
    ROOT.glob("*/summary.json"), key=lambda p: p.stat().st_mtime, reverse=True
)
if not run_dirs:
    raise SystemExit("❌ No benchmark results found under benchmarks/results_pro/")
summary_path = run_dirs[0]
run_dir = summary_path.parent
print(f"📂 Using latest run: {run_dir.name}")

# -------------------------------------------------------------------
# Load summary
# -------------------------------------------------------------------
with summary_path.open(encoding="utf-8") as f:
    summary = json.load(f)

# -------------------------------------------------------------------
# Derive per-stage averages if not present
# -------------------------------------------------------------------
STAGES = ["detector", "planner", "generator", "safety", "executor", "verifier"]
stage_means = {s: summary.get(f"{s}_avg_ms") for s in STAGES}
need_fallback = any(v is None for v in stage_means.values())

if need_fallback:
    eval_path = run_dir / "eval.jsonl"
    totals = {s: 0.0 for s in STAGES}
    counts = {s: 0 for s in STAGES}
    if eval_path.exists():
        with eval_path.open(encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                for t in rec.get("trace", []) or []:
                    s = t.get("stage")
                    ms = t.get("ms", t.get("duration_ms", 0.0))
                    if s in totals:
                        totals[s] += float(ms)
                        counts[s] += 1
    stage_means = {
        s: round(totals[s] / max(counts[s], 1), 2) if counts[s] else 0.0 for s in STAGES
    }

latencies = [stage_means[s] for s in STAGES]

# -------------------------------------------------------------------
# Plot average latency per stage
# -------------------------------------------------------------------
plt.figure(figsize=(7, 5))
plt.bar(STAGES, latencies, color="#6fa8dc")
plt.title("Average Latency per Stage (ms)")
plt.xlabel("Stage")
plt.ylabel("Latency (ms)")
plt.tight_layout()
plt.savefig(run_dir / "latency_per_stage.png")
print(f"📊 Saved latency chart → {run_dir / 'latency_per_stage.png'}")

# -------------------------------------------------------------------
# Plot EM / SM / ExecAcc metrics
# -------------------------------------------------------------------
metrics = ["EM", "SM", "ExecAcc"]
scores = [summary.get(k, 0.0) for k in metrics]

plt.figure(figsize=(7, 5))
plt.bar(metrics, scores, color="#93c47d")
plt.title("EM / SM / ExecAcc")
plt.xlabel("Metric")
plt.ylabel("Score")
plt.ylim(0, 1)
plt.tight_layout()
plt.savefig(run_dir / "metrics_overview.png")
print(f"📊 Saved metrics chart → {run_dir / 'metrics_overview.png'}")

# -------------------------------------------------------------------
# Quick textual summary
# -------------------------------------------------------------------
print(
    f"\n✅ Summary for {run_dir.name}\n"
    f"Avg latency: {summary.get('avg_latency_ms', 'n/a')} ms\n"
    f"Success rate: {summary.get('success_rate', 0.0):.0%}\n"
    f"EM: {summary.get('EM', 0.0):.3f} | SM: {summary.get('SM', 0.0):.3f} | ExecAcc: {summary.get('ExecAcc', 0.0):.3f}\n"
)
time.sleep(0.2)
