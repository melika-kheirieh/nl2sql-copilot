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
from pathlib import Path
import time
import json
import uuid
from typing import Union, Optional, Dict, TypedDict, Any, cast

router = APIRouter(prefix="/nl2sql")

# --- Database adapter selection ---
DB_MODE = os.getenv("DB_MODE", "sqlite").lower()

_db: Union[PostgresAdapter, SQLiteAdapter]
if DB_MODE == "postgres":
    dsn = os.environ.get("POSTGRES_DSN")
    if not dsn:
        raise RuntimeError(
            "POSTGRES_DSN environment variable is required in postgres mode"
        )
    _db = PostgresAdapter(dsn)
else:
    _db = SQLiteAdapter("data/chinook.db")


# --- Build a single shared pipeline for all routes ---
def _make_pipeline() -> Pipeline:
    llm = OpenAIProvider()
    return Pipeline(
        detector=AmbiguityDetector(),
        planner=Planner(llm=llm),
        generator=Generator(llm=llm),
        safety=Safety(),
        executor=Executor(db=_db),
        verifier=Verifier(),
        repair=Repair(llm=llm),
    )


_pipeline: Pipeline = _make_pipeline()


# -------------------------------
# Runtime DB registry (for uploaded SQLite files)
# Files are stored under /tmp, mapped by a short-lived db_id
# -------------------------------
_DB_UPLOAD_DIR = os.getenv("DB_UPLOAD_DIR", "/tmp/nl2sql_dbs")
_DB_TTL_SECONDS: int = int(os.getenv("DB_TTL_SECONDS", "7200"))  # default 2 hours
os.makedirs(_DB_UPLOAD_DIR, exist_ok=True)


class DBEntry(TypedDict):
    path: str
    ts: float


# In-memory map: db_id -> {"path": str, "ts": float}
_DB_MAP: Dict[str, DBEntry] = {}

# -------------------------------
# Default DB resolution
# -------------------------------
DB_MODE = os.getenv("DB_MODE", "sqlite").lower()  # "sqlite" or "postgres"
POSTGRES_DSN = os.getenv("POSTGRES_DSN")
DEFAULT_SQLITE_DB: str = os.getenv("DEFAULT_SQLITE_DB", "data/chinook.db")

# -------------------------------
# Path to persist db_id → file map
# -------------------------------
_DB_MAP_PATH = Path("data/uploads/db_map.json")
_DB_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)

UPLOAD_DIR = Path("data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)  # ensure folder exists

DEFAULT_SQLITE_PATH = "data/Chinook_Sqlite.sqlite"


def _save_db_map() -> None:
    """Persist the in-memory DB map to disk as JSON."""
    try:
        with open(_DB_MAP_PATH, "w") as f:
            json.dump(_DB_MAP, f)
    except Exception as e:
        print(f"⚠️ Failed to save DB map: {e}")


def _load_db_map() -> None:
    """Load the DB map from disk if it exists (called on startup)."""
    global _DB_MAP
    if _DB_MAP_PATH.exists():
        try:
            with open(_DB_MAP_PATH, "r") as f:
                data = json.load(f)
            # Be liberal in what we accept; validate into TypedDict
            if isinstance(data, dict):
                restored: Dict[str, DBEntry] = {}
                for k, v in data.items():
                    path = v.get("path")
                    ts = v.get("ts")
                    if isinstance(path, str) and isinstance(ts, (int, float)):
                        restored[k] = {"path": path, "ts": float(ts)}
                _DB_MAP.update(restored)
                print(f"📂 Restored {_DB_MAP_PATH} with {len(_DB_MAP)} entries.")
        except Exception as e:
            print(f"⚠️ Failed to load DB map: {e}")


def _cleanup_db_map() -> None:
    """Remove expired uploaded DB files (best-effort)."""
    now = time.time()
    expired = [k for k, v in _DB_MAP.items() if (now - v["ts"]) > _DB_TTL_SECONDS]
    for k in expired:
        path: str = _DB_MAP[k]["path"]
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        _DB_MAP.pop(k, None)


def _resolve_sqlite_path(db_id: Optional[str]) -> str:
    """Resolve a SQLite file path from db_id or fallback to default."""
    _cleanup_db_map()
    if db_id and db_id in _DB_MAP:
        return _DB_MAP[db_id]["path"]
    return DEFAULT_SQLITE_DB


def _select_adapter(db_id: Optional[str]):
    mode = os.getenv("DB_MODE", "sqlite").lower()
    if mode == "postgres":
        dsn = os.environ.get("POSTGRES_DSN")
        if not dsn:
            raise HTTPException(status_code=500, detail="POSTGRES_DSN env is missing")
        return PostgresAdapter(dsn)

    # sqlite mode
    if db_id:
        _cleanup_db_map()
        db_path: Optional[str] = None
        # first check runtime map
        if db_id in _DB_MAP:
            db_path = _DB_MAP[db_id]["path"]
        # fallback: check /tmp or uploads
        if not db_path or not os.path.exists(db_path):
            fallback_tmp = os.path.join(_DB_UPLOAD_DIR, f"{db_id}.sqlite")
            fallback_uploads = str(UPLOAD_DIR / f"{db_id}.sqlite")
            for candidate in (fallback_tmp, fallback_uploads):
                if os.path.exists(candidate):
                    db_path = candidate
                    break
        if not db_path or not os.path.exists(db_path):
            raise HTTPException(
                status_code=400, detail="invalid db_id (file not found)"
            )
        return SQLiteAdapter(db_path)

    # fallback to default Chinook
    if not Path(DEFAULT_SQLITE_PATH).exists():
        raise HTTPException(status_code=500, detail="default DB not found")
    return SQLiteAdapter(DEFAULT_SQLITE_PATH)


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
def _to_dict(obj: Any) -> Any:
    """Safely convert dataclass instance → dict, otherwise return as-is.

    Note: dataclasses.is_dataclass returns True for both classes and instances.
    We must exclude classes; mypy cannot refine this perfectly, so we ignore arg-type.
    """
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)  # type: ignore[arg-type]
    return obj


def _round_trace(t: Dict[str, Any]) -> Dict[str, Any]:
    """Round float fields to keep responses tidy and stable."""
    if t.get("cost_usd") is not None:
        # Ensure numeric before rounding
        cost = t["cost_usd"]
        if isinstance(cost, (int, float)):
            t["cost_usd"] = round(float(cost), 6)
    if t.get("duration_ms") is not None:
        dur = t["duration_ms"]
        if isinstance(dur, (int, float)):
            t["duration_ms"] = round(float(dur), 2)
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
        raise HTTPException(
            status_code=400, detail="DB upload is only supported in sqlite mode"
        )

    filename = file.filename or "db.sqlite"
    if not (filename.endswith(".db") or filename.endswith(".sqlite")):
        raise HTTPException(
            status_code=400, detail="Only .db or .sqlite files are allowed"
        )

    data = await file.read()
    max_bytes = int(os.getenv("UPLOAD_MAX_BYTES", str(20 * 1024 * 1024)))  # 20 MB
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=400, detail=f"File too large (> {max_bytes} bytes)"
        )

    db_id = str(uuid.uuid4())
    out_path = os.path.join(_DB_UPLOAD_DIR, f"{db_id}.sqlite")
    try:
        with open(out_path, "wb") as f:
            f.write(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to store DB: {e}")

    _DB_MAP[db_id] = {"path": out_path, "ts": time.time()}
    _save_db_map()
    return {"db_id": db_id}


# -------------------------------
# Main NL2SQL endpoint
# Path will be /api/nl2sql if your root APIRouter is mounted at /api
# -------------------------------
@router.post("", name="nl2sql_handler")
def nl2sql_handler(request: NL2SQLRequest):
    db_id = getattr(request, "db_id", None)

    # 1) Pick pipeline (+ optional per-request adapter)
    pipeline: Pipeline
    if db_id:
        adapter = _select_adapter(db_id)  # returns PostgresAdapter | SQLiteAdapter
        # If _select_adapter could theoretically return None, uncomment the next line:
        # assert adapter is not None, "adapter must be set when db_id is provided"
        pipeline = _build_pipeline(adapter)
        derived_preview_val: str = _derive_schema_preview(adapter)
    else:
        pipeline = _pipeline
        derived_preview_val = ""  # no adapter → no derive

    # 2) Resolve schema_preview
    provided_preview_any: Any = getattr(request, "schema_preview", None)
    provided_preview: Optional[str] = cast(Optional[str], provided_preview_any)
    final_preview: str = provided_preview or derived_preview_val

    # 3) Run pipeline
    try:
        result = pipeline.run(
            user_query=request.query,
            schema_preview=final_preview,
        )
    except Exception as exc:
        # Hard failure in pipeline itself
        raise HTTPException(status_code=500, detail=f"Pipeline crash: {exc!s}")

    # 4) Type check
    if not isinstance(result, FinalResult):
        raise HTTPException(status_code=500, detail="Pipeline returned unexpected type")

    # 5) Ambiguity → ask for clarification
    if result.ambiguous and result.questions:
        return ClarifyResponse(ambiguous=True, questions=result.questions)

    # 6) Soft errors → bubble up details with 400
    if not result.ok or result.error:
        print("❌ Pipeline failure dump:")
        print("  ok:", result.ok)
        print("  error:", result.error)
        print("  details:", result.details)
        print("  traces:", result.traces)
        raise HTTPException(
            status_code=400,
            detail="; ".join(result.details or []) or (result.error or "Unknown error"),
        )

    # 7) Success
    traces = [_round_trace(t) for t in (result.traces or [])]
    return NL2SQLResponse(
        ambiguous=False,
        sql=result.sql,
        rationale=result.rationale,
        traces=traces,
    )


def _derive_schema_preview(adapter: Union[PostgresAdapter, SQLiteAdapter]) -> str:
    """
    Build a strict, exact-cased schema preview for the LLM.
    Works for SQLite adapters by querying sqlite_master / pragma table_info.
    """
    import sqlite3
    import os

    # Adapters may expose db_path or path; both are str in our codebase
    db_path: Optional[str] = cast(
        Optional[str], getattr(adapter, "db_path", None)
    ) or cast(Optional[str], getattr(adapter, "path", None))
    if not db_path or not os.path.exists(db_path):
        return ""

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        tables = cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        lines = []
        for (tname,) in tables:
            cols = cur.execute(f"PRAGMA table_info('{tname}')").fetchall()
            # sqlite: pragma columns → (cid, name, type, notnull, dflt_value, pk)
            colnames = [c[1] for c in cols]
            lines.append(f"{tname}({', '.join(colnames)})")
        conn.close()
        return "\n".join(lines)
    except Exception:
        return ""
