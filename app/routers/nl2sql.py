from __future__ import annotations

# --- Stdlib ---
from dataclasses import asdict, is_dataclass
import os
from pathlib import Path
import time
import uuid
from typing import Any, Dict, Optional, Union, cast, Callable, Tuple
import hashlib

# --- Third-party ---
from fastapi import APIRouter, HTTPException, UploadFile, File, Depends, Query
from fastapi import Security
from fastapi.security import APIKeyHeader
from prometheus_client import Counter

# --- Local ---
from app.schemas import NL2SQLRequest, NL2SQLResponse, ClarifyResponse
from app.state import cleanup_stale_dbs, register_db
from nl2sql.pipeline import FinalResult, FinalResult as _FinalResult
from adapters.llm.openai_provider import OpenAIProvider
from adapters.db.sqlite_adapter import SQLiteAdapter
from adapters.db.postgres_adapter import PostgresAdapter
from nl2sql.pipeline_factory import (
    pipeline_from_config,
    pipeline_from_config_with_adapter,
)
from nl2sql.prom import REGISTRY


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(key: Optional[str] = Security(api_key_header)):
    raw = os.getenv("API_KEYS", "")
    allowed = {k.strip() for k in raw.split(",") if k.strip()}
    if not allowed:  # no keys set → auth disabled (dev mode)
        return
    if not key or key not in allowed:
        raise HTTPException(status_code=401, detail="invalid API key")


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


####################################
# ---- Simple in-memory cache for NL→SQL responses ----

cache_hits_total = Counter("cache_hits_total", "NL2SQL cache hits", registry=REGISTRY)
cache_misses_total = Counter(
    "cache_misses_total", "NL2SQL cache misses", registry=REGISTRY
)
_CACHE_TTL = int(os.getenv("NL2SQL_CACHE_TTL_SEC", "300"))  # 5 minutes
_CACHE_MAX = int(os.getenv("NL2SQL_CACHE_MAX", "256"))
_CACHE: Dict[Tuple[str, str, str], Tuple[float, Dict[str, Any]]] = {}


def _norm_q(s: str) -> str:
    return (s or "").strip().lower()


def _schema_key(preview: str) -> str:
    return hashlib.md5((preview or "").encode()).hexdigest()


def _ck(db_id: Optional[str], query: str, preview: str) -> Tuple[str, str, str]:
    return (db_id or "default", _norm_q(query), _schema_key(preview))


def _cache_gc(now: float) -> None:
    # TTL eviction
    for k, (ts, _) in list(_CACHE.items()):
        if now - ts > _CACHE_TTL:
            _CACHE.pop(k, None)
    # size eviction
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)), None)


####################################

router = APIRouter(prefix="/nl2sql")

# -------------------------------
# Config / Defaults
# -------------------------------
DB_MODE = os.getenv("DB_MODE", "sqlite").lower()  # "sqlite" or "postgres"
POSTGRES_DSN = os.getenv("POSTGRES_DSN")
# Default demo DB used when no db_id is provided (can be full Chinook or a tiny demo DB)
DEFAULT_SQLITE_PATH: str = os.getenv(
    "DEFAULT_SQLITE_PATH", "data/Chinook_Sqlite.sqlite"
)

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


# -------------------------------
# Adapter selection (lazy)
# -------------------------------
def _select_adapter(db_id: Optional[str]) -> Union[PostgresAdapter, SQLiteAdapter]:
    """
    Resolve DB adapter path for SQLite or Postgres.
    """
    if DB_MODE == "postgres":
        dsn = os.environ.get("POSTGRES_DSN")
        if not dsn:
            raise HTTPException(status_code=500, detail="POSTGRES_DSN env is missing")
        return PostgresAdapter(dsn)

    if db_id:
        cleanup_stale_dbs()
        import logging

        log = logging.getLogger(__name__)

        candidates = [
            Path("/tmp/nl2sql_dbs") / f"{db_id}.sqlite",
            Path("/tmp/nl2sql_dbs") / f"{db_id}.db",
            Path("data/uploads") / f"{db_id}.sqlite",
            Path("data/uploads") / f"{db_id}.db",
            Path("data") / f"{db_id}.sqlite",
            Path("data") / f"{db_id}.db",
        ]

        for candidate in candidates:
            if candidate.exists():
                log.info(f"✅ Using DB file: {candidate}")
                return SQLiteAdapter(str(candidate))

        raise HTTPException(status_code=404, detail=f"db_id not found: {db_id}")

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
@router.post("/upload_db", dependencies=[Depends(require_api_key)])
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

    register_db(db_id, out_path)
    return {"db_id": db_id}


def _final_schema_preview(db_id: Optional[str], provided_preview: Optional[str]) -> str:
    if provided_preview and provided_preview.strip():
        return provided_preview

    adapter = _select_adapter(db_id)  # works for both None and explicit db_id
    return _derive_schema_preview(adapter) or ""


@router.get("/health")
def health():
    return {"status": "ok", "version": os.getenv("APP_VERSION", "dev")}


# -------------------------------
# Main NL2SQL endpoint
# -------------------------------
@router.post("", name="nl2sql_handler", dependencies=[Depends(require_api_key)])
def nl2sql_handler(
    request: NL2SQLRequest,
    run: Runner = Depends(get_runner),
):
    """
    NL→SQL handler using YAML-driven DI. If 'db_id' is provided, we override only the adapter
    while keeping all other stages from the YAML configs intact.
    """
    db_id = getattr(request, "db_id", None)
    final_preview = _final_schema_preview(
        db_id, cast(Optional[str], getattr(request, "schema_preview", None))
    )

    # ---- cache lookup ----
    now = time.time()
    _cache_gc(now)
    ck = _ck(db_id, request.query, final_preview)
    hit = _CACHE.get(ck)
    if hit and now - hit[0] <= _CACHE_TTL:
        cache_hits_total.inc()
        return hit[1]  # early return
    cache_misses_total.inc()

    # Choose runner: default pipeline from YAML OR per-request override with a specific adapter
    if db_id:
        adapter = _select_adapter(db_id)
        pipeline = _build_pipeline(adapter)
        runner = pipeline.run
    else:
        runner = run

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

    # Normalize execution result (if executor attached one)
    response_result: Dict[str, Any] = {}
    raw_result = getattr(result, "result", None)
    if raw_result is not None:
        if isinstance(raw_result, dict):
            response_result = raw_result
        else:
            response_result = cast(Dict[str, Any], _to_dict(raw_result))

    payload = NL2SQLResponse(
        ambiguous=False,
        sql=result.sql,
        rationale=result.rationale,
        traces=traces,
        result=response_result,
    )

    # store in cache
    _CACHE[ck] = (time.time(), cast(Dict[str, Any], payload.model_dump()))
    return payload


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
