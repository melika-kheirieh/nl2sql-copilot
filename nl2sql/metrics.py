from prometheus_client import Counter, Histogram
from nl2sql.prom import REGISTRY


# -----------------------------------------------------------------------------
#  Stage-level metrics
# -----------------------------------------------------------------------------
stage_duration_ms = Histogram(
    "stage_duration_ms",
    "Duration (ms) of each pipeline stage",
    ["stage"],  # e.g. detector|planner|generator|safety|verifier
    buckets=(1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000),
    registry=REGISTRY,
)

# -----------------------------------------------------------------------------
#  Safety stage metrics
# -----------------------------------------------------------------------------
safety_blocks_total = Counter(
    "safety_blocks_total",
    "Count of blocked SQL queries by safety checks",
    [
        "reason"
    ],  # e.g. forbidden_keyword, multiple_statements, non_readonly, explain_not_allowed
    registry=REGISTRY,
)

safety_checks_total = Counter(
    "safety_checks_total",
    "Total SQL queries checked by safety",
    ["ok"],  # "true" or "false"
    registry=REGISTRY,
)

# -----------------------------------------------------------------------------
#  Verifier stage metrics
# -----------------------------------------------------------------------------
verifier_checks_total = Counter(
    "verifier_checks_total",
    "Count of verifier checks (success/failure)",
    ["ok"],  # "true" | "false"
    registry=REGISTRY,
)

verifier_failures_total = Counter(
    "verifier_failures_total",
    "Count of verifier failures by type",
    ["reason"],  # e.g. parse_error, semantic_check_error, adapter_failure
    registry=REGISTRY,
)

# -----------------------------------------------------------------------------
#  Repair stage metrics
# -----------------------------------------------------------------------------
repair_attempts_total = Counter(
    "repair_attempts_total",
    "Number of repair loop attempts",
    ["outcome"],  # attempt | success | failed
    registry=REGISTRY,
)

# -----------------------------------------------------------------------------
#  Pipeline-level metrics
# -----------------------------------------------------------------------------
pipeline_runs_total = Counter(
    "pipeline_runs_total",
    "Total number of full pipeline runs",
    ["status"],  # ok | error | ambiguous
    registry=REGISTRY,
)

# -----------------------------------------------------------------------------
#  Cache metrics (optional)
# -----------------------------------------------------------------------------
cache_events_total = Counter(
    "cache_events_total",
    "Cache hit/miss events in the pipeline",
    ["hit"],  # "true" | "false"
    registry=REGISTRY,
)

# -----------------------------------------------------------------------------
#  Prime all counters with zero to ensure Grafana panels always have data
# -----------------------------------------------------------------------------
for reason in (
    "forbidden_keyword",
    "multiple_statements",
    "non_readonly",
    "explain_not_allowed",
    "parse_error",
    "semantic_check_error",
    "adapter_failure",
    "unsafe-sql",
    "malformed-sql",
    "unknown",
):
    safety_blocks_total.labels(reason=reason).inc(0)
    verifier_failures_total.labels(reason=reason).inc(0)

for ok in ("true", "false"):
    safety_checks_total.labels(ok=ok).inc(0)
    verifier_checks_total.labels(ok=ok).inc(0)

for outcome in ("attempt", "success", "failed"):
    repair_attempts_total.labels(outcome=outcome).inc(0)

for status in ("ok", "error", "ambiguous"):
    pipeline_runs_total.labels(status=status).inc(0)

for hit in ("true", "false"):
    cache_events_total.labels(hit=hit).inc(0)
