from dataclasses import dataclass
from typing import Any, Dict, Optional, List


@dataclass(frozen=True)
class StageTrace:
    stage: str
    duration_ms: float
    notes: Optional[Dict[str, Any]] = None
    token_in: Optional[int] = None
    token_out: Optional[int] = None
    cost_usd: Optional[float] = None


@dataclass(frozen=True)
class StageResult:
    ok: bool
    data: Optional[Any] = None
    trace: Optional[StageTrace] = None
    error: Optional[List[str]] = None
    notes: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class FinalResult:
    """
    Final domain result of the whole pipeline.
    Adapters (HTTP/CLI/UI) should serialize this to dict/JSON at the boundary.
    """

    ok: bool  # end-to-end success
    ambiguous: bool
    error: bool
    sql: Optional[str]
    rationale: Optional[str]
    verified: Optional[bool]
    details: Optional[List[str]]
    questions: Optional[List[str]]
    traces: List[Dict[str, Any]]
