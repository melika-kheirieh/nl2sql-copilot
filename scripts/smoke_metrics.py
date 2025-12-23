"""Portable Prometheus metrics validation for NL2SQL Copilot.

This script does NOT require jq.
It queries Prometheus HTTP API and prints a small snapshot.

Pre-req:
  - API is running and you've already exercised it (e.g. via smoke_api.py)
  - Prometheus is reachable

Env:
  PROMETHEUS_URL (default: http://127.0.0.1:9090)
"""

from __future__ import annotations

import os
import json
from typing import Any, Dict

import requests


PROM = os.getenv("PROMETHEUS_URL", "http://127.0.0.1:9090").rstrip("/")


def prom_query(expr: str) -> Dict[str, Any]:
    url = f"{PROM}/api/v1/query"
    resp = requests.get(url, params={"query": expr}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def main() -> int:
    queries = [
        "nl2sql:pipeline_success_ratio",
        "nl2sql:stage_p95_ms",
    ]

    print("📊 Prometheus snapshot")
    print(f"PROMETHEUS_URL={PROM}")

    ok = True
    for q in queries:
        try:
            out = prom_query(q)
            print(f"\nQuery: {q}")
            print(json.dumps(out, indent=2)[:1200])
        except Exception as e:
            ok = False
            print(f"❌ Prometheus query failed for {q}: {e}")

    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
