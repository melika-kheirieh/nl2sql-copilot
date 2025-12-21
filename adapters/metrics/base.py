from __future__ import annotations

from typing import Protocol


class Metrics(Protocol):
    def observe_stage_duration_ms(self, *, stage: str, dt_ms: float) -> None: ...

    def inc_pipeline_run(self, *, status: str) -> None: ...

    def inc_repair_attempt(self, *, outcome: str) -> None: ...
