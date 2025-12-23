"""
Plot latest Spider benchmark results.

Outputs in the latest folder under benchmarks/results_pro/:
- metrics_overview.png: EM/SM/ExecAcc + latency (avg, p50, p95)
- latency_per_stage.png: bar of average per-stage latency
- latency_histogram.png: latency distribution across samples
- errors_overview.png: error counts by type (if trace.jsonl exists)
"""

from __future__ import annotations

import json
from pathlib import Path
from collections import Counter
import matplotlib.pyplot as plt

ROOT = Path("benchmarks/results_pro")

STAGES = [
    "detector",
    "planner",
    "generator",
    "safety",
    "executor",
    "verifier",
    "repair",
]


def _latest_run_dir() -> Path:
    summaries = sorted(
        ROOT.glob("*/summary.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not summaries:
        raise SystemExit("❌ No benchmark results found under benchmarks/results_pro/")
    return summaries[0].parent


def _load_summary(run: Path) -> dict:
    return json.loads((run / "summary.json").read_text(encoding="utf-8"))


def _load_eval_rows(run: Path) -> list[dict]:
    p = run / "eval.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()
    return [json.loads(x) for x in lines]


def _normalize_trace(trace) -> dict[str, int]:
    """
    Normalize trace into {stage: total_ms}.
    - Enforces known stages
    - Coerces duration_ms to int
    - Safe against malformed input
    """
    out = {s: 0 for s in STAGES}
    if not isinstance(trace, list):
        return out

    for t in trace:
        if not isinstance(t, dict):
            continue
        stage = str(t.get("stage", "")).lower()
        if stage not in out:
            continue
        ms = t.get("duration_ms", t.get("ms", 0))
        try:
            out[stage] += int(round(float(ms)))
        except Exception:
            continue
    return out


def plot_metrics_overview(run: Path, summary: dict) -> None:
    # EM/SM/ExecAcc on [0,1]; latency shown in seconds for readability
    labels = ["EM", "SM", "ExecAcc", "avg (s)", "p50 (s)", "p95 (s)"]
    values = [
        float(summary.get("EM", 0.0)),
        float(summary.get("SM", 0.0)),
        float(summary.get("ExecAcc", 0.0)),
        float(summary.get("avg_latency_ms", 0.0)) / 1000.0,
        float(summary.get("p50_latency_ms", 0.0)) / 1000.0,
        float(summary.get("p95_latency_ms", 0.0)) / 1000.0,
    ]

    plt.figure(figsize=(9, 5))
    bars = plt.bar(labels, values)
    for b, v in zip(bars, values):
        plt.text(
            b.get_x() + b.get_width() / 2,
            v,
            f"{v:.2f}",
            ha="center",
            va="bottom",
        )
    plt.title("Metrics Overview (Spider)")
    ymax = max(1.0, max(values) * 1.15 if values else 1.0)
    plt.ylim(0, ymax)
    plt.tight_layout()
    plt.savefig(run / "metrics_overview.png")
    plt.close()


def plot_latency_hist(run: Path, rows: list[dict]) -> None:
    latencies = [
        r.get("latency_ms", 0)
        for r in rows
        if isinstance(r.get("latency_ms"), (int, float))
    ]
    if not latencies:
        return
    plt.figure(figsize=(9, 4))
    bins = min(20, max(5, int(len(latencies) ** 0.5)))
    plt.hist(latencies, bins=bins)
    plt.title("Latency Distribution (ms)")
    plt.xlabel("Latency (ms)")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(run / "latency_histogram.png")
    plt.close()


def plot_latency_per_stage(run: Path, summary: dict, rows: list[dict]) -> None:
    # Prefer summary keys if available
    raw_values = [summary.get(f"{s}_avg_ms") for s in STAGES]
    values: list[float] = [float(v or 0.0) for v in raw_values]

    # Fallback: derive from normalized traces
    if not any(values):
        totals = {s: 0 for s in STAGES}
        counts = {s: 0 for s in STAGES}
        for r in rows:
            durations = _normalize_trace(r.get("trace") or r.get("traces"))
            for s in STAGES:
                if durations[s] > 0:
                    totals[s] += durations[s]
                    counts[s] += 1
        values = [round(totals[s] / counts[s], 2) if counts[s] else 0.0 for s in STAGES]

    plt.figure(figsize=(10, 5))
    bars = plt.bar(STAGES, values)
    for b, v in zip(bars, values):
        plt.text(
            b.get_x() + b.get_width() / 2,
            float(v),
            f"{v:.1f}",
            ha="center",
            va="bottom",
        )
    plt.title("Average Latency per Stage (ms)")
    plt.xlabel("Stage")
    plt.ylabel("Latency (ms)")
    ymax = max(1.0, max(values) * 1.15 if values else 1.0)
    plt.ylim(0, ymax)
    plt.tight_layout()
    plt.savefig(run / "latency_per_stage.png")
    plt.close()


def plot_errors_overview(run: Path) -> None:
    p = run / "trace.jsonl"
    if not p.exists():
        return

    counts: Counter[str] = Counter()
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            for t in obj.get("trace", []):
                et = t.get("error_type")
                if et:
                    counts[et] += 1

    if not counts:
        return

    labels, values = zip(*sorted(counts.items(), key=lambda x: x[1], reverse=True))
    plt.figure(figsize=(9, 4))
    plt.bar(labels, values)
    plt.title("Errors by Type")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(run / "errors_overview.png")
    plt.close()


def main() -> None:
    run = _latest_run_dir()
    print(f"📂 Using latest run: {run.name}")
    summary = _load_summary(run)
    rows = _load_eval_rows(run)

    plot_metrics_overview(run, summary)
    plot_latency_hist(run, rows)
    plot_latency_per_stage(run, summary, rows)
    plot_errors_overview(run)

    print(
        "✅ Saved: metrics_overview.png, latency_histogram.png, "
        "latency_per_stage.png, errors_overview.png"
    )


if __name__ == "__main__":
    main()
