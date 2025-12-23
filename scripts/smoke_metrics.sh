#!/usr/bin/env bash
set -euo pipefail

# Deprecated: prefer `python scripts/smoke_metrics.py` (portable, no jq required).

PROMETHEUS_URL=${PROMETHEUS_URL:-"http://127.0.0.1:9090"}
export PROMETHEUS_URL

python scripts/smoke_metrics.py
