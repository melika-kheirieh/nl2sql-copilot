from __future__ import annotations

from prometheus_client import Counter
from adapters.metrics.base import Metrics, RepairOutcome
from nl2sql.metrics import stage_duration_ms, pipeline_runs_total


stage_calls_total = Counter(
    "stage_calls_total",
    "Total number of stage calls by stage and success",
    ["stage", "ok"],
)

stage_errors_total = Counter(
    "stage_errors_total",
    "Total number of stage errors by stage and error code",
    ["stage", "error_code"],
)

repair_attempts_total = Counter(
    "repair_attempts_total",
    "Total repair attempts by stage and outcome",
    ["stage", "outcome"],
)

repair_trigger_total = Counter(
    "repair_trigger_total",
    "Total repair triggers by stage and reason",
    ["stage", "reason"],
)


class PrometheusMetrics(Metrics):
    def observe_stage_duration_ms(self, *, stage: str, dt_ms: float) -> None:
        stage_duration_ms.labels(stage=stage).observe(dt_ms)

    def inc_pipeline_run(self, *, status: str) -> None:
        pipeline_runs_total.labels(status=status).inc()

    def inc_stage_call(self, *, stage: str, ok: bool) -> None:
        stage_calls_total.labels(stage=stage, ok=str(ok).lower()).inc()

    def inc_stage_error(self, *, stage: str, error_code: str) -> None:
        stage_errors_total.labels(stage=stage, error_code=error_code).inc()

    def inc_repair_trigger(self, *, stage: str, reason: str) -> None:
        repair_trigger_total.labels(stage=stage, reason=reason).inc()

    def inc_repair_attempt(self, *, stage: str, outcome: RepairOutcome) -> None:
        repair_attempts_total.labels(stage=stage, outcome=outcome).inc()
