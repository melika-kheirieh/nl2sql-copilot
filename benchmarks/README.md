# Benchmarks

This project provides three evaluation layers: **Smoke**, **Eval Lite**, and **Eval Pro**.

## 1) Smoke (API-level)
- **Purpose:** system health check + basic metrics wiring
- **Files:** `scripts/smoke_run.py`, `scripts/smoke_metrics.sh`
- **Command:** `make demo`

## 2) Eval Lite (Direct pipeline)
- **Purpose:** engineering signals (end-to-end latency, success rate, repair usage) without relying on gold SQL
- **Notes:** no semantic accuracy / no EM/SM; intended as a fast, CI-friendly signal
- **Command:** `make eval-smoke`
- **Output:** `benchmarks/results/<timestamp>/` (JSONL + summary + CSV)

## 3) Eval Pro (Spider)
- **Purpose:** accuracy-oriented evaluation on Spider (EM / SM / ExecAcc), plus latency breakdowns
- **File:** `benchmarks/eval_spider_pro.py`
- **Commands:**
  - `make eval-pro-smoke` (quick preset, e.g., 20 samples)
  - `make eval-pro` (default preset, e.g., 200 samples)
- **Output:** `benchmarks/results_pro/<timestamp>/`

### Plots (Eval Pro)
Generate PNG artifacts for the **latest** eval-pro run:

- **Command:** `make plot-pro`
- **Outputs (saved under the latest `benchmarks/results_pro/<timestamp>/` folder):**
  - `metrics_overview.png` — EM/SM/ExecAcc + latency stats (avg/p50/p95)
  - `latency_histogram.png` — end-to-end latency distribution
  - `latency_per_stage.png` — average latency per pipeline stage
  - `errors_overview.png` — error type counts (if traces are available)

### Dashboard
Interactive inspection of JSONL results (lite/pro):

- **Command:** `make bench-ui`
- **Port override:** `PORT=8502 make bench-ui`
