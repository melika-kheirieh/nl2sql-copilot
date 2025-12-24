from __future__ import annotations

from prometheus_client import Counter, Histogram
from nl2sql.prom import REGISTRY

from adapters.metrics.base import Metrics, PipelineStatus, RepairOutcome

# -----------------------------------------------------------------------------
# Stage-level metrics
# -----------------------------------------------------------------------------
stage_duration_ms = Histogram(
    "stage_duration_ms",
    "Duration (ms) of each pipeline stage",
    ["stage"],
    buckets=(1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 60000),
    registry=REGISTRY,
)

stage_calls_total = Counter(
    "stage_calls_total",
    "Count of stage calls labeled by stage and ok",
    ["stage", "ok"],
    registry=REGISTRY,
)

stage_errors_total = Counter(
    "stage_errors_total",
    "Count of stage errors labeled by stage and error_code",
    ["stage", "error_code"],
    registry=REGISTRY,
)

# -----------------------------------------------------------------------------
# Repair metrics (Day 3 contract)
# -----------------------------------------------------------------------------
repair_attempts_total = Counter(
    "repair_attempts_total",
    "Count of repair attempts labeled by stage and outcome",
    ["stage", "outcome"],
    registry=REGISTRY,
)

repair_trigger_total = Counter(
    "repair_trigger_total",
    "Count of repair triggers labeled by stage and reason",
    ["stage", "reason"],
    registry=REGISTRY,
)

# -----------------------------------------------------------------------------
# Safety stage metrics (existing / optional)
# -----------------------------------------------------------------------------
safety_blocks_total = Counter(
    "safety_blocks_total",
    "Count of blocked SQL queries by safety checks",
    ["reason"],
    registry=REGISTRY,
)

safety_checks_total = Counter(
    "safety_checks_total",
    "Total SQL queries checked by safety",
    ["ok"],
    registry=REGISTRY,
)

# -----------------------------------------------------------------------------
# Verifier stage metrics (existing / optional)
# -----------------------------------------------------------------------------
verifier_checks_total = Counter(
    "verifier_checks_total",
    "Count of verifier checks (success/failure)",
    ["ok"],
    registry=REGISTRY,
)

verifier_failures_total = Counter(
    "verifier_failures_total",
    "Count of verifier failures by type",
    ["reason"],
    registry=REGISTRY,
)

# -----------------------------------------------------------------------------
# Pipeline-level metrics
# -----------------------------------------------------------------------------
pipeline_runs_total = Counter(
    "pipeline_runs_total",
    "Total number of full pipeline runs",
    ["status"],
    registry=REGISTRY,
)

# -----------------------------------------------------------------------------
# Cache metrics (optional)
# -----------------------------------------------------------------------------
cache_events_total = Counter(
    "cache_events_total",
    "Cache hit/miss events in the pipeline",
    ["hit"],
    registry=REGISTRY,
)


class PrometheusMetrics(Metrics):
    def observe_stage_duration_ms(self, *, stage: str, dt_ms: float) -> None:
        stage_duration_ms.labels(stage=stage).observe(float(dt_ms))

    def inc_pipeline_run(self, *, status: PipelineStatus) -> None:
        pipeline_runs_total.labels(status=status).inc()

    def inc_stage_call(self, *, stage: str, ok: bool) -> None:
        stage_calls_total.labels(stage=stage, ok=("true" if ok else "false")).inc()

    def inc_stage_error(self, *, stage: str, error_code: str) -> None:
        stage_errors_total.labels(stage=stage, error_code=str(error_code)).inc()

    def inc_repair_trigger(self, *, stage: str, reason: str) -> None:
        repair_trigger_total.labels(stage=stage, reason=str(reason)).inc()

    def inc_repair_attempt(self, *, stage: str, outcome: RepairOutcome) -> None:
        repair_attempts_total.labels(stage=stage, outcome=outcome).inc()


# -----------------------------------------------------------------------------
# Label priming to keep /metrics stable
# -----------------------------------------------------------------------------
for ok in ("true", "false"):
    safety_checks_total.labels(ok=ok).inc(0)
    verifier_checks_total.labels(ok=ok).inc(0)

for status in ("ok", "error", "ambiguous"):
    pipeline_runs_total.labels(status=status).inc(0)

for hit in ("true", "false"):
    cache_events_total.labels(hit=hit).inc(0)

# Prime Day 3 series
for stage in (
    "detector",
    "planner",
    "generator",
    "safety",
    "executor",
    "verifier",
    "repair",
):
    for ok in ("true", "false"):
        stage_calls_total.labels(stage=stage, ok=ok).inc(0)
    for outcome in ("attempt", "success", "failed", "skipped"):
        repair_attempts_total.labels(stage=stage, outcome=outcome).inc(0)

for reason in ("semantic_failure", "unknown"):
    repair_trigger_total.labels(stage="verifier", reason=reason).inc(0)
