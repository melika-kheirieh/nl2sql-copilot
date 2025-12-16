from dataclasses import dataclass
from typing import Any, Dict, Optional, List

from nl2sql.errors.codes import ErrorCode


# =====================
# Tracing / Observability
# =====================


@dataclass(frozen=True)
class StageTrace:
    stage: str
    duration_ms: float
    summary: str = ""
    notes: Optional[Dict[str, Any]] = None

    # Optional observability fields
    token_in: Optional[int] = None
    token_out: Optional[int] = None
    cost_usd: Optional[float] = None

    # Enriched / debug-only fields
    sql_length: Optional[int] = None
    row_count: Optional[int] = None
    verified: Optional[bool] = None
    repair_attempts: Optional[int] = None
    skipped: bool = False


# =====================
# Stage-level contract
# =====================


@dataclass(frozen=True)
class StageResult:
    ok: bool

    data: Optional[Any] = None
    trace: Optional[StageTrace] = None

    # Human-readable error messages (debug / UI only)
    error: Optional[List[str]] = None

    # === Contract-level semantics ===
    error_code: Optional[ErrorCode] = None
    retryable: Optional[bool] = None

    # Free-form notes (internal use)
    notes: Optional[Dict[str, Any]] = None


# =====================
# Final pipeline result
# =====================


@dataclass(frozen=True)
class FinalResult:
    """
    Final domain result of the whole pipeline.
    Adapters (HTTP/CLI/UI) should serialize this to dict/JSON at the boundary.
    """

    ok: bool
    ambiguous: bool
    error: bool

    # Output
    sql: Optional[str]
    rationale: Optional[str]
    verified: Optional[bool]

    # Error surface
    error_code: Optional[ErrorCode]
    details: Optional[List[str]]

    # UX helpers
    questions: Optional[List[str]]

    # Observability
    traces: List[Dict[str, Any]]
