from __future__ import annotations

from adapters.metrics.base import Metrics, RepairOutcome


class NoOpMetrics(Metrics):
    def observe_stage_duration_ms(self, *, stage: str, dt_ms: float) -> None:
        return

    def inc_pipeline_run(self, *, status: str) -> None:
        return

    def inc_stage_call(self, *, stage: str, ok: bool) -> None:
        return

    def inc_stage_error(self, *, stage: str, error_code: str) -> None:
        return

    def inc_repair_trigger(self, *, stage: str, reason: str) -> None:
        return

    def inc_repair_attempt(self, *, stage: str, outcome: RepairOutcome) -> None:
        return
