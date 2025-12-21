from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

PipelineStatus = Literal["ok", "error", "ambiguous"]
RepairOutcome = Literal["attempt", "success", "failed", "skipped"]


class Metrics(ABC):
    @abstractmethod
    def observe_stage_duration_ms(self, *, stage: str, dt_ms: float) -> None: ...

    @abstractmethod
    def inc_pipeline_run(self, *, status: PipelineStatus) -> None: ...

    @abstractmethod
    def inc_stage_call(self, *, stage: str, ok: bool) -> None: ...

    @abstractmethod
    def inc_stage_error(self, *, stage: str, error_code: str) -> None: ...

    @abstractmethod
    def inc_repair_trigger(self, *, stage: str, reason: str) -> None: ...

    @abstractmethod
    def inc_repair_attempt(self, *, stage: str, outcome: RepairOutcome) -> None: ...
