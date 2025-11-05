from dataclasses import asdict, is_dataclass
import os
from pathlib import Path
import time
import json
import uuid
from typing import Union, Optional, Dict, TypedDict, Any, cast

from fastapi import APIRouter, HTTPException, UploadFile, File

from app.schemas import NL2SQLRequest, NL2SQLResponse, ClarifyResponse
from nl2sql.pipeline import Pipeline as _Pipeline, FinalResult as _FinalResult
from nl2sql.ambiguity_detector import AmbiguityDetector
from nl2sql.safety import Safety
from nl2sql.planner import Planner
from nl2sql.generator import Generator
from nl2sql.executor import Executor
from nl2sql.verifier import Verifier
from nl2sql.repair import Repair
from adapters.llm.openai_provider import OpenAIProvider
from adapters.db.sqlite_adapter import SQLiteAdapter
from adapters.db.postgres_adapter import PostgresAdapter

Pipeline = _Pipeline
FinalResult = _FinalResult
__all__ = ["Pipeline", "FinalResult"]


router = APIRouter(prefix="/nl2sql")

# -------------------------------
# Config / Defaults
# -------------------------------
DB_MODE = os.getenv("DB_MODE", "sqlite").lower()  # "sqlite" or "postgres"
POSTGRES_DSN = os.getenv("POSTGRES_DSN")
DEFAULT_SQLITE_PATH: str = os.getenv("DEFAULT_SQLITE_DB", "data/Chinook_Sqlite.sqlite")

# Runtime upload storage
_DB_UPLOAD_DIR = os.getenv("DB_UPLOAD_DIR", "/tmp/nl2sql_dbs")
_DB_TTL_SECONDS: int = int(os.getenv("DB_TTL_SECONDS", "7200"))  # default 2 hours
os.makedirs(_DB_UPLOAD_DIR, exist_ok=True)

# Persisted map
_DB_MAP_PATH = Path("data/uploads/db_map.json")
_DB_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)

UPLOAD_DIR = Path("data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class DBEntry(TypedDict):
    path: str
    ts: float


# In-memory map: db_id -> {"path": str, "ts": float}
_DB_MAP: Dict[str, DBEntry] = {}


def _save_db_map() -> None:
    try:
        with open(_DB_MAP_PATH, "w") as f:
            json.dump(_DB_MAP, f)
    except Exception as e:
        print(f"⚠️ Failed to save DB map: {e}")


def _load_db_map() -> None:
    global _DB_MAP
    if _DB_MAP_PATH.exists():
        try:
            with open(_DB_MAP_PATH, "r") as f:
                data = json.load(f)
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


# Call once at import (safe & light); heavy things remain lazy.
_load_db_map()


# -------------------------------
# Adapter selection (lazy)
# -------------------------------
def _select_adapter(db_id: Optional[str]) -> Union[PostgresAdapter, SQLiteAdapter]:
    """
    Resolve a DB adapter:
      - postgres: requires POSTGRES_DSN
      - sqlite with db_id: uploaded file or fallback locations
      - sqlite default: DEFAULT_SQLITE_PATH must exist
    """
    mode = os.getenv("DB_MODE", "sqlite").lower()
    if mode == "postgres":
        dsn = os.environ.get("POSTGRES_DSN")
        if not dsn:
            raise HTTPException(status_code=500, detail="POSTGRES_DSN env is missing")
        return PostgresAdapter(dsn)

    # sqlite mode
    _cleanup_db_map()
    if db_id:
        # Check runtime map
        entry = _DB_MAP.get(db_id)
        candidates = []
        if entry and os.path.exists(entry["path"]):
            candidates.append(entry["path"])
        # Fallback locations based on convention
        candidates.append(os.path.join(_DB_UPLOAD_DIR, f"{db_id}.sqlite"))
        candidates.append(str(UPLOAD_DIR / f"{db_id}.sqlite"))

        for p in candidates:
            if p and os.path.exists(p):
                return SQLiteAdapter(p)

        raise HTTPException(status_code=400, detail="invalid db_id (file not found)")

    # default sqlite
    if not Path(DEFAULT_SQLITE_PATH).exists():
        raise HTTPException(status_code=500, detail="default DB not found")
    return SQLiteAdapter(DEFAULT_SQLITE_PATH)


# -------------------------------
# LLM & Pipeline builders (lazy)
# -------------------------------
def _get_llm() -> OpenAIProvider:
    # Create provider on demand, after .env has been loaded in app.main
    return OpenAIProvider()


def _build_pipeline(adapter: Union[PostgresAdapter, SQLiteAdapter]) -> Pipeline:
    """
    Build a fresh Pipeline bound to the given adapter.
    All stateful/external pieces (LLM, executor) are instantiated here (lazy).
    """
    llm = _get_llm()
    detector = AmbiguityDetector()
    planner = Planner(llm=llm)
    generator = Generator(llm=llm)
    safety = Safety()
    executor = Executor(adapter)
    verifier = Verifier()
    repair = Repair(llm=llm)
    return Pipeline(
        detector=detector,
        planner=planner,
        generator=generator,
        safety=safety,
        executor=executor,
        verifier=verifier,
        repair=repair,
    )


# --- Module-level default Pipeline instance for no-db_id requests ---
# It lets tests monkeypatch `Pipeline.run` and avoids building adapters on each call.
try:
    _pipeline: Pipeline = _build_pipeline(SQLiteAdapter(":memory:"))
except Exception as e:
    # Fallback to a file-based sqlite if in-memory init fails in some environments
    print(
        f"⚠️ default _pipeline init failed on :memory: → {e}; falling back to data/chinook.db"
    )
    _pipeline = _build_pipeline(SQLiteAdapter("data/chinook.db"))


# -------------------------------
# Helpers (unchanged)
# -------------------------------
def _to_dict(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)  # type: ignore[arg-type]
    return obj


def _round_trace(t: Dict[str, Any]) -> Dict[str, Any]:
    if t.get("cost_usd") is not None:
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
# -------------------------------
@router.post("/upload_db")
async def upload_db(file: UploadFile = File(...)):
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
# -------------------------------
@router.post("", name="nl2sql_handler")
def nl2sql_handler(request: NL2SQLRequest):
    db_id = getattr(request, "db_id", None)

    # Declare once to avoid mypy no-redef
    pipeline_obj: Pipeline
    derived_preview_val: str

    if not db_id:
        # Use module-level pipeline instance (already initialized)
        pipeline_obj = cast(Pipeline, _pipeline)
        derived_preview_val = ""
    else:
        adapter = _select_adapter(db_id)
        pipeline_obj = _build_pipeline(adapter)
        derived_preview_val = (
            _derive_schema_preview(adapter)
            if isinstance(adapter, SQLiteAdapter)
            else ""
        )

    # Resolve schema_preview
    provided_preview_any: Any = getattr(request, "schema_preview", None)
    provided_preview: Optional[str] = cast(Optional[str], provided_preview_any)
    final_preview: Optional[str] = provided_preview or (derived_preview_val or None)

    # Run pipeline (ensure schema_preview is str for typing)
    try:
        result = pipeline_obj.run(
            user_query=request.query,
            schema_preview=(final_preview or ""),  # pipeline expects str
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Pipeline crash: {exc!s}")

    if not isinstance(result, FinalResult):
        raise HTTPException(status_code=500, detail="Pipeline returned unexpected type")

    # Ambiguous → 200
    if result.ambiguous and (result.questions is not None):
        return ClarifyResponse(ambiguous=True, questions=result.questions)

    # Error → 400 (with debug dump)
    if (not result.ok) or result.error:
        print("❌ Pipeline failure dump:")
        print("  ok:", result.ok)
        print("  error:", result.error)
        print("  details:", result.details)
        print("  traces:", result.traces)
        raise HTTPException(
            status_code=400,
            detail="; ".join(result.details or []) or (result.error or "Unknown error"),
        )

    # Success → 200
    traces = [_round_trace(t) for t in (result.traces or [])]
    return NL2SQLResponse(
        ambiguous=False,
        sql=result.sql,
        rationale=result.rationale,
        traces=traces,
    )


def _derive_schema_preview(adapter: Union[PostgresAdapter, SQLiteAdapter]) -> str:
    """
    Build a strict, exact-cased schema preview for the LLM (SQLite only).
    """
    import sqlite3

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
            colnames = [c[1] for c in cols]  # (cid, name, type, notnull, dflt, pk)
            lines.append(f"{tname}({', '.join(colnames)})")
        conn.close()
        return "\n".join(lines)
    except Exception:
        return ""
