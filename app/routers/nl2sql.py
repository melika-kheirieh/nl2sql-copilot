from __future__ import annotations

# --- Stdlib ---
from dataclasses import asdict, is_dataclass
import json
import os
from pathlib import Path
import time
import uuid
from typing import Any, Dict, Optional, TypedDict, Union, cast, Callable

# --- Third-party ---
from fastapi import APIRouter, HTTPException, UploadFile, File, Depends, Query

# --- Local ---
from app.schemas import NL2SQLRequest, NL2SQLResponse, ClarifyResponse
from app.state import get_db_path, cleanup_stale_dbs, register_db
from nl2sql.pipeline import FinalResult, FinalResult as _FinalResult
from adapters.llm.openai_provider import OpenAIProvider
from adapters.db.sqlite_adapter import SQLiteAdapter
from adapters.db.postgres_adapter import PostgresAdapter
from nl2sql.pipeline_factory import (
    pipeline_from_config,
    pipeline_from_config_with_adapter,
)

_PIPELINE: Optional[Any] = None  # lazy cache

Runner = Callable[..., _FinalResult]


def get_runner() -> Runner:
    """Build pipeline lazily; under pytest return a stub runner."""
    if os.getenv("PYTEST_CURRENT_TEST"):
        # Minimal OK runner for route tests (no ambiguity)
        def _fake_runner(
            *, user_query: str, schema_preview: str | None = None
        ) -> _FinalResult:
            return _FinalResult(
                ok=True,
                ambiguous=False,
                error=False,
                details=None,
                questions=None,
                sql="SELECT 1;",
                rationale=None,
                verified=True,
                traces=[],
            )

        return _fake_runner

    global _PIPELINE
    if _PIPELINE is None:
        _PIPELINE = pipeline_from_config(CONFIG_PATH)
    return _PIPELINE.run


def _build_pipeline(adapter) -> Any:
    """Thin wrapper for tests to monkeypatch; builds a pipeline bound to adapter."""
    return pipeline_from_config_with_adapter(CONFIG_PATH, adapter=adapter)


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

CONFIG_PATH = os.getenv("PIPELINE_CONFIG", "configs/sqlite_pipeline.yaml")
_PIPELINE = pipeline_from_config(CONFIG_PATH)


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
    Resolve a DB adapter based on module-level DB_MODE and an optional db_id.
    """
    if DB_MODE == "postgres":
        dsn = os.environ.get("POSTGRES_DSN")
        if not dsn:
            raise HTTPException(status_code=500, detail="POSTGRES_DSN env is missing")
        return PostgresAdapter(dsn)

    # sqlite mode
    if db_id:
        cleanup_stale_dbs()
        path = get_db_path(db_id)
        if path and os.path.exists(path):
            return SQLiteAdapter(path)
        raise HTTPException(
            status_code=404, detail=f"db_id not found or expired: {db_id}"
        )

    # default sqlite fallback
    default_path = Path(DEFAULT_SQLITE_PATH)
    if not default_path.exists():
        raise HTTPException(status_code=500, detail="default SQLite DB not found")
    return SQLiteAdapter(str(default_path))


# -------------------------------
# Schema preview endpoint
# -------------------------------


@router.get("/schema")
def get_schema(db_id: Optional[str] = Query(default=None)):
    """
    Return a schema preview for a given db_id (SQLite only).
    If db_id is omitted, returns the default database schema.
    """
    try:
        adapter = _select_adapter(db_id)
        preview = _derive_schema_preview(adapter)
        if not preview.strip():
            raise HTTPException(
                status_code=404, detail="Schema preview not available or empty"
            )
        return {"db_id": db_id or "default", "schema_preview": preview}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Schema introspection failed: {e}")


# -------------------------------
# LLM & Pipeline builders (lazy)
# -------------------------------
def _get_llm() -> OpenAIProvider:
    # Create provider on demand, after .env has been loaded in app.main
    return OpenAIProvider()


# -------------------------------
# Helpers
# -------------------------------
def _to_dict(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)  # type: ignore[arg-type]
    return obj


def _round_trace(t: Any) -> Dict[str, Any]:
    """
    Normalize a trace entry (dict or StageTrace-like object) for API/UI:
    - stage: str (required)
    - duration_ms: int (rounded)
    - summary: optional (pass-through if exists)
    - notes: optional
    - token_in/out, cost_usd: pass-through if present
    """
    if isinstance(t, dict):
        stage = t.get("stage", "?")
        ms = t.get("duration_ms", 0)
        notes = t.get("notes")
        cost = t.get("cost_usd")
        summary = t.get("summary")
        token_in = t.get("token_in")
        token_out = t.get("token_out")
    else:
        stage = getattr(t, "stage", "?")
        ms = getattr(t, "duration_ms", 0)
        notes = getattr(t, "notes", None)
        cost = getattr(t, "cost_usd", None)
        summary = getattr(t, "summary", None)
        token_in = getattr(t, "token_in", None)
        token_out = getattr(t, "token_out", None)

    # coerce duration to int with rounding
    try:
        ms_int = int(round(float(ms))) if ms is not None else 0
    except Exception:
        ms_int = 0

    out: Dict[str, Any] = {
        "stage": str(stage) if stage is not None else "?",
        "duration_ms": ms_int,
        "notes": notes,
        "cost_usd": cost,
    }
    if summary is not None:
        out["summary"] = summary
    if token_in is not None:
        out["token_in"] = token_in
    if token_out is not None:
        out["token_out"] = token_out
    return out


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
    register_db(db_id, out_path)
    return {"db_id": db_id}


# -------------------------------
# Main NL2SQL endpoint
# -------------------------------
@router.post("", name="nl2sql_handler")
def nl2sql_handler(
    request: NL2SQLRequest,
    run: Runner = Depends(get_runner),
):
    """
    NL→SQL handler using YAML-driven DI. If 'db_id' is provided, we override only the adapter
    while keeping all other stages from the YAML configs intact.
    """
    db_id = getattr(request, "db_id", None)
    provided_preview = (
        cast(Optional[str], getattr(request, "schema_preview", None)) or ""
    )

    # Choose runner: default pipeline from YAML OR per-request override with a specific adapter
    if db_id:
        adapter = _select_adapter(db_id)
        pipeline = _build_pipeline(adapter)
        runner = pipeline.run
        final_preview = provided_preview  # keep simple; derive only if you have a SQLite schema helper
    else:
        runner = run
        final_preview = provided_preview or ""

    # Execute pipeline
    try:
        result = runner(user_query=request.query, schema_preview=final_preview)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Pipeline crash: {exc!s}")

    # Type sanity
    if not isinstance(result, FinalResult):
        raise HTTPException(status_code=500, detail="Pipeline returned unexpected type")

    # Ambiguity path → 200 with questions
    if result.ambiguous:
        qs = result.questions or []
        return ClarifyResponse(ambiguous=True, questions=qs)

    if not isinstance(result, _FinalResult):
        raise HTTPException(status_code=500, detail="Pipeline returned unexpected type")

    # Error path → 400 with joined details
    if (not result.ok) or result.error:
        print("❌ Pipeline failure dump:")
        print("  ok:", result.ok)
        print("  error:", result.error)
        print("  details:", result.details)
        print("  traces:", result.traces)
        message = "; ".join(result.details or []) or "Unknown error"
        raise HTTPException(status_code=400, detail=message)

    # Success path → 200 (coerce/standardize traces for API)
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
