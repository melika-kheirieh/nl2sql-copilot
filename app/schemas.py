from typing import List, Optional, Any, Dict

from pydantic import BaseModel, Field


class NL2SQLRequest(BaseModel):
    query: str
    db_id: Optional[str] = None
    schema_preview: Optional[str] = None

    class Config:
        extra = "ignore"


class TraceModel(BaseModel):
    stage: str
    duration_ms: int
    token_in: int | None = 0
    token_out: int | None = 0
    cost_usd: float | None = 0
    notes: Dict[str, Any] | None = None


class NL2SQLResponse(BaseModel):
    ambiguous: bool = False
    sql: Optional[str] = None
    rationale: Optional[str] = None
    traces: List[Dict[str, Any]] = Field(default_factory=list)
    result: Dict[str, Any] = Field(default_factory=dict)


class ClarifyResponse(BaseModel):
    ambiguous: bool = True
    questions: List[str] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: str
    details: List[str] | None = None
