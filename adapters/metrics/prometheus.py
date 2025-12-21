from __future__ import annotations

from adapters.metrics.base import Metrics
from nl2sql.metrics import stage_duration_ms, pipeline_runs_total, repair_attempts_total


class PrometheusMetrics(Metrics):
    def observe_stage_duration_ms(self, *, stage: str, dt_ms: float) -> None:
        stage_duration_ms.labels(stage).observe(dt_ms)

    def inc_pipeline_run(self, *, status: str) -> None:
        pipeline_runs_total.labels(status=status).inc()

    def inc_repair_attempt(self, *, outcome: str) -> None:
        # outcome: attempt | success | failed | skipped
        repair_attempts_total.labels(outcome=outcome).inc()
