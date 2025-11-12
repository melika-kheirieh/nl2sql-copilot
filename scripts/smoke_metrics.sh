#!/usr/bin/env bash
set -euo pipefail

BASE=${BASE:-http://localhost:8000}
API="$BASE/api/v1"

# Send a few successful queries to populate basic metrics
for q in \
  "List all artists" \
  "Top 5 albums by sales" \
  "Count customers"
do
  curl -s -X POST "$API/nl2sql" \
    -H 'Content-Type: application/json' \
    -H 'X-API-Key: dev-key' \
    -d "{\"query\":\"$q\"}" >/dev/null || true
done

# Send queries that trigger safety and verifier checks
curl -s -X POST "$API/nl2sql" \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: dev-key' \
  -d '{"query":"DELETE FROM users;"}' >/dev/null || true

curl -s -X POST "$API/nl2sql" \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: dev-key' \
  -d '{"query":"SELECT COUNT(*), country FROM customers;"}' >/dev/null || true

# Print a snapshot of key Prometheus metrics
echo -e "\n--- Metrics snapshot ---"
curl -s "$BASE/metrics" | grep -E \
  'stage_duration_ms_(sum|count|bucket)|pipeline_runs_total|safety_(checks|blocks)_total|verifier_(checks|failures)_total' \
  || true
