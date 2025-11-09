from dataclasses import dataclass
from typing import Any, Dict, Optional, List


@dataclass(frozen=True)
class StageTrace:
    stage: str
    duration_ms: float  # keep float internally if you like
    summary: str = ""  # ← default to keep legacy call-sites working
    notes: Optional[Dict[str, Any]] = None
    token_in: Optional[int] = None
    token_out: Optional[int] = None
    cost_usd: Optional[float] = None

    # Enriched fields
    sql_length: Optional[int] = None
    row_count: Optional[int] = None
    verified: Optional[bool] = None
    error_type: Optional[str] = None
    repair_attempts: Optional[int] = None
    skipped: bool = False


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
