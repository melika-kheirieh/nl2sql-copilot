from pydantic import BaseModel
from typing import List, Optional, Any, Dict, Union


class NL2SQLRequest(BaseModel):
    query: str
    schema_preview: str
    db_name: Optional[str] = "default"


class TraceModel(BaseModel):
    stage: str
    duration_ms: float
    token_in: int | None = 0
    token_out: int | None = 0
    cost_usd: float | None = 0
    notes: Dict[str, Any] | None = None


class NL2SQLResponse(BaseModel):
    ambiguous: bool = False
    sql: Optional[str] = None
    rationale: Optional[str] = None
    traces: List[Union[TraceModel, dict]] = []


class ClarifyResponse(BaseModel):
    ambiguous: bool = True
    questions: List[str]


class ErrorResponse(BaseModel):
    error: str
    details: List[str] | None = None
