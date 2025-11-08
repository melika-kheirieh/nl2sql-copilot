"""
Plot and summarize results from benchmarks/results_pro/<run>/summary.json

- Auto-detects the latest run directory (unless --run-dir is provided).
- Prints a compact textual report (EM/SM/ExecAcc, latency, success rate).
- Saves two charts next to summary.json:
    - latency_per_stage.png
    - metrics_overview.png (EM/SM/ExecAcc as a bar chart)

Usage:
    PYTHONPATH=$PWD python benchmarks/plot_results.py
    PYTHONPATH=$PWD python benchmarks/plot_results.py --run-dir benchmarks/results_pro/20251108-105442
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Any, List

import matplotlib.pyplot as plt


STAGES: List[str] = [
    "detector",
    "planner",
    "generator",
    "safety",
    "executor",
    "verifier",
]


def _find_latest_run(results_root: Path) -> Path:
    runs = sorted([p for p in results_root.iterdir() if p.is_dir()])
    if not runs:
        raise FileNotFoundError(f"No runs found under {results_root}")
    return runs[-1]


def _load_summary(run_dir: Path) -> Dict[str, Any]:
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        # Back-compat (legacy name used by tests)
        summary_path = run_dir / "metrics_summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"Missing summary JSON in {run_dir}")
    return json.loads(summary_path.read_text(encoding="utf-8"))


def _print_report(summary: Dict[str, Any]) -> None:
    # Gracefully read metrics (demo runs may not have EM/SM/ExecAcc)
    em = summary.get("EM", 0.0)
    sm = summary.get("SM", 0.0)
    exec_acc = summary.get("ExecAcc", 0.0)
    success_rate = summary.get("success_rate", 0.0)
    avg_ms = summary.get("avg_latency_ms", 0.0)
    p95_ms = summary.get("p95_latency_ms", None)

    total = summary.get("queries_total", summary.get("total", 0))
    src = summary.get("pipeline_source", "adapter")
    ts = summary.get("timestamp", "-")

    print("\n================ Benchmark Summary ================")
    print(f"Timestamp        : {ts}")
    print(f"Pipeline source  : {src}")
    print(f"Queries total    : {total}")
    print(f"Success rate     : {success_rate:.0%}")
    print(f"EM / SM / ExecAcc: {em:.2f} / {sm:.2f} / {exec_acc:.2f}")
    print(f"Avg latency (ms) : {avg_ms:.1f}")
    if p95_ms is not None:
        print(f"p95 latency (ms) : {p95_ms:.1f}")
    print("===================================================\n")


def _plot_latency_per_stage(run_dir: Path, summary: Dict[str, Any]) -> Path:
    latencies = [summary.get(f"{s}_avg_ms", 0.0) for s in STAGES]
    out_path = run_dir / "latency_per_stage.png"

    # Single-plot bar chart (no explicit colors)
    plt.figure()
    plt.bar(STAGES, latencies)
    plt.title("Average Latency per Stage (ms)")
    plt.xlabel("Stage")
    plt.ylabel("Latency (ms)")
    plt.tight_layout()
    plt.savefig(out_path, dpi=160)
    plt.close()

    return out_path


def _plot_metrics_overview(run_dir: Path, summary: Dict[str, Any]) -> Path:
    # Even if zeros (demo mode), chart is still useful in README.
    em = summary.get("EM", 0.0)
    sm = summary.get("SM", 0.0)
    exec_acc = summary.get("ExecAcc", 0.0)
    out_path = run_dir / "metrics_overview.png"

    labels = ["EM", "SM", "ExecAcc"]
    values = [em, sm, exec_acc]

    plt.figure()
    plt.bar(labels, values)
    plt.title("EM / SM / ExecAcc")
    plt.xlabel("Metric")
    plt.ylabel("Score")
    plt.ylim(0, 1)  # normalized range
    plt.tight_layout()
    plt.savefig(out_path, dpi=160)
    plt.close()

    return out_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--run-dir",
        type=str,
        default=None,
        help="Path to a specific run directory under benchmarks/results_pro/ "
        "(defaults to latest).",
    )
    args, _ = ap.parse_known_args()

    results_root = Path("benchmarks") / "results_pro"
    run_dir = (
        Path(args.run_dir).resolve() if args.run_dir else _find_latest_run(results_root)
    )

    summary = _load_summary(run_dir)
    _print_report(summary)

    lat_path = _plot_latency_per_stage(run_dir, summary)
    met_path = _plot_metrics_overview(run_dir, summary)

    print("✅ Saved plots:")
    print(f"- {lat_path}")
    print(f"- {met_path}")


if __name__ == "__main__":
    main()
