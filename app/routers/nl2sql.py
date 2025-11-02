from dataclasses import asdict, is_dataclass
from fastapi import APIRouter, HTTPException, UploadFile, File
from app.schemas import NL2SQLRequest, NL2SQLResponse, ClarifyResponse
from nl2sql.pipeline import Pipeline, FinalResult
from nl2sql.ambiguity_detector import AmbiguityDetector
from nl2sql.safety import Safety
from nl2sql.planner import Planner
from nl2sql.generator import Generator
from adapters.llm.openai_provider import OpenAIProvider
from nl2sql.executor import Executor
from nl2sql.verifier import Verifier
from nl2sql.repair import Repair
from adapters.db.sqlite_adapter import SQLiteAdapter
from adapters.db.postgres_adapter import PostgresAdapter

import os
import time
import uuid
from typing import Union, Optional, Dict

router = APIRouter(prefix="/nl2sql")

# -------------------------------
# Runtime DB registry (for uploaded SQLite files)
# Files are stored under /tmp, mapped by a short-lived db_id
# -------------------------------
_DB_UPLOAD_DIR = os.getenv("DB_UPLOAD_DIR", "/tmp/nl2sql_dbs")
_DB_TTL_SECONDS = int(os.getenv("DB_TTL_SECONDS", "7200"))  # default 2 hours
os.makedirs(_DB_UPLOAD_DIR, exist_ok=True)

# In-memory map: db_id -> {"path": str, "ts": float}
_DB_MAP: Dict[str, Dict[str, object]] = {}

# -------------------------------
# Default DB resolution
# -------------------------------
DB_MODE = os.getenv("DB_MODE", "sqlite").lower()  # "sqlite" or "postgres"
POSTGRES_DSN = os.getenv("POSTGRES_DSN")
DEFAULT_SQLITE_DB = os.getenv("DEFAULT_SQLITE_DB", "data/chinook.db")  # keep your current default

def _cleanup_db_map() -> None:
    """Remove expired uploaded DB files (best-effort)."""
    now = time.time()
    expired = [k for k, v in _DB_MAP.items() if now - float(v.get("ts", 0)) > _DB_TTL_SECONDS]
    for k in expired:
        path = _DB_MAP[k].get("path")
        try:
            if isinstance(path, str) and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        _DB_MAP.pop(k, None)

def _resolve_sqlite_path(db_id: Optional[str]) -> str:
    """Resolve a SQLite file path from db_id or fallback to default."""
    _cleanup_db_map()
    if db_id and db_id in _DB_MAP:
        return str(_DB_MAP[db_id]["path"])
    return DEFAULT_SQLITE_DB

def _select_adapter(db_id: Optional[str]) -> Union[PostgresAdapter, SQLiteAdapter]:
    """
    Build a DB adapter for this request.
    - In postgres mode: always PostgresAdapter(POSTGRES_DSN).
    - In sqlite mode: use uploaded SQLite by db_id if present, otherwise DEFAULT_SQLITE_DB.
    """
    if DB_MODE == "postgres":
        if not POSTGRES_DSN:
            raise HTTPException(status_code=500, detail="POSTGRES_DSN is not configured")
        return PostgresAdapter(POSTGRES_DSN)

    # sqlite mode
    sqlite_path = _resolve_sqlite_path(db_id)
    # NOTE: SQLiteAdapter should open DB in read-only mode internally if supported.
    # If not, ensure your adapter enforces PRAGMA query_only=ON and prevents DDL/DML.
    return SQLiteAdapter(sqlite_path)

# -------------------------------
# LLM providers & shared components (stateless)
# -------------------------------
def get_llm():
    return OpenAIProvider()

_detector = AmbiguityDetector()
_planner = Planner(get_llm())
_generator = Generator(get_llm())
_safety = Safety()
_verifier = Verifier()
_repair = Repair(get_llm())

def _build_pipeline(adapter: Union[PostgresAdapter, SQLiteAdapter]) -> Pipeline:
    """Build a fresh Pipeline with a per-request Executor bound to the chosen adapter."""
    executor = Executor(adapter)
    return Pipeline(
        detector=_detector,
        planner=_planner,
        generator=_generator,
        safety=_safety,
        executor=executor,
        verifier=_verifier,
        repair=_repair,
    )

# -------------------------------
# Helpers
# -------------------------------
def _to_dict(obj):
    """Safely convert dataclass → dict."""
    return asdict(obj) if is_dataclass(obj) else obj

def _round_trace(t: dict) -> dict:
    """Round float fields to keep responses tidy and stable."""
    if t.get("cost_usd") is not None:
        t["cost_usd"] = round(t["cost_usd"], 6)
    if t.get("duration_ms") is not None:
        t["duration_ms"] = round(t["duration_ms"], 2)
    return t

# -------------------------------
# Upload endpoint (SQLite only)
# Path will be /api/nl2sql/upload_db if your root APIRouter is mounted at /api
# -------------------------------
@router.post("/upload_db")
async def upload_db(file: UploadFile = File(...)):
    """
    Upload a SQLite database (.db/.sqlite). Returns a short-lived db_id.
    Notes:
    - Only SQLite files are allowed here (not for Postgres mode).
    - Max size ~20MB recommended for demo environments like HF Spaces.
    - Files are stored under /tmp and cleaned by TTL.
    """
    if DB_MODE != "sqlite":
        raise HTTPException(status_code=400, detail="DB upload is only supported in sqlite mode")

    filename = file.filename or "db.sqlite"
    if not (filename.endswith(".db") or filename.endswith(".sqlite")):
        raise HTTPException(status_code=400, detail="Only .db or .sqlite files are allowed")

    data = await file.read()
    max_bytes = int(os.getenv("UPLOAD_MAX_BYTES", str(20 * 1024 * 1024)))  # 20 MB
    if len(data) > max_bytes:
        raise HTTPException(status_code=400, detail=f"File too large (> {max_bytes} bytes)")

    db_id = str(uuid.uuid4())
    out_path = os.path.join(_DB_UPLOAD_DIR, f"{db_id}.sqlite")
    try:
        with open(out_path, "wb") as f:
            f.write(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to store DB: {e}")

    _DB_MAP[db_id] = {"path": out_path, "ts": time.time()}
    return {"db_id": db_id}

# -------------------------------
# Main NL2SQL endpoint
# Path will be /api/nl2sql if your root APIRouter is mounted at /api
# -------------------------------
@router.post("", name="nl2sql_handler")
def nl2sql_handler(request: NL2SQLRequest):
    """
    Handle NL → SQL pipeline execution.
    Optional: if the incoming request model supports `db_id`, we switch DB for this call.
    Otherwise we will silently ignore and use default DB (or Postgres, based on mode).
    """
    # Try to extract db_id if present in request (without breaking strict models)
    db_id = getattr(request, "db_id", None)  # Optional[str]
    # Build per-request pipeline bound to the selected adapter
    adapter = _select_adapter(db_id)
    pipeline = _build_pipeline(adapter)

    result = pipeline.run(
        user_query=request.query,
        schema_preview=getattr(request, "schema_preview", None),
    )

    # Ensure result type
    if not isinstance(result, FinalResult):
        raise HTTPException(status_code=500, detail="Pipeline returned unexpected type")

    # Ambiguity: return clarify payload
    if result.ambiguous and result.questions:
        return ClarifyResponse(ambiguous=True, questions=result.questions)

    # Error: bubble up details
    if not result.ok or result.error:
        detail = "; ".join(result.details or ["Unknown error"])
        raise HTTPException(status_code=400, detail=detail)

    # Success
    traces = [_round_trace(t) for t in (result.traces or [])]
    return NL2SQLResponse(
        ambiguous=False,
        sql=result.sql,
        rationale=result.rationale,
        traces=traces,
    )
