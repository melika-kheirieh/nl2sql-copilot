from dataclasses import asdict, is_dataclass
from fastapi import APIRouter, HTTPException
from app.schemas import NL2SQLRequest, NL2SQLResponse, ClarifyResponse
from nl2sql.pipeline import Pipeline
from nl2sql.ambiguity_detector import AmbiguityDetector
from nl2sql.safety import Safety
from nl2sql.planner import Planner
from nl2sql.generator import Generator
from adapters.llm.openai_provider import OpenAIProvider
from nl2sql.types import StageResult
from nl2sql.executor import Executor
from nl2sql.verifier import Verifier
from nl2sql.repair import Repair
from adapters.db.sqlite_adapter import SQLiteAdapter
from adapters.db.postgres_adapter import PostgresAdapter
import os


router = APIRouter(prefix="/nl2sql")


if os.getenv("DB_MODE", "sqlite") == "postgres":
    _db = PostgresAdapter(os.environ["POSTGRES_DSN"])
else:
    _db = SQLiteAdapter("data/chinook.db")

# --- Composition Root ---
_llm = OpenAIProvider()
# _db = SQLiteAdapter("data/chinook.db")
_executor = Executor(_db)
_verifier = Verifier()
_repair = Repair(_llm)


_pipeline = Pipeline(
    detector=AmbiguityDetector(),
    planner=Planner(_llm),
    generator=Generator(_llm),
    safety=Safety(),
    executor=_executor,
    verifier=_verifier,
    repair=_repair,
)


def _to_dict(obj):
    """Helper: safely convert dataclass → dict."""
    return asdict(obj) if is_dataclass(obj) else obj


def _round_trace(t: dict) -> dict:
    if t.get("cost_usd") is not None:
        t["cost_usd"] = round(t["cost_usd"], 6)
    if t.get("duration_ms") is not None:
        t["duration_ms"] = round(t["duration_ms"], 2)
    return t


@router.post("", name="nl2sql_handler")
def nl2sql_handler(request: NL2SQLRequest):
    result = _pipeline.run(
        user_query=request.query, schema_preview=request.schema_preview
    )

    # --- Ensure result type ---
    if not isinstance(result, StageResult):
        raise HTTPException(status_code=500, detail="Pipeline returned unexpected type")

    data = result.data or {}

    # --- Handle ambiguity ---
    if isinstance(data, dict) and data.get("ambiguous") and data.get("questions"):
        return ClarifyResponse(ambiguous=True, questions=data["questions"])

    # --- Handle error ---
    if not result.ok:
        detail = "; ".join(result.error) if result.error else "Unknown error"
        raise HTTPException(status_code=400, detail=detail)

    # --- Success case ---
    return NL2SQLResponse(
        ambiguous=False,
        sql=data.get("sql"),
        rationale=data.get("rationale"),
        traces=[_to_dict(t) for t in data.get("traces", [])],
    )
