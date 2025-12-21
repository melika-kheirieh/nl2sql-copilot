from __future__ import annotations

from adapters.metrics.base import Metrics


class NoOpMetrics(Metrics):
    def observe_stage_duration_ms(self, *, stage: str, dt_ms: float) -> None:
        return

    def inc_pipeline_run(self, *, status: str) -> None:
        return

    def inc_repair_attempt(self, *, outcome: str) -> None:
        return
