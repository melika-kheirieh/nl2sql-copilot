from __future__ import annotations

# --- Stdlib ---
from dataclasses import asdict, is_dataclass
import os
from pathlib import Path
import uuid
from typing import Any, Dict, Optional, Tuple, cast
import hashlib
import logging

# --- Third-party ---
from fastapi import APIRouter, Depends, HTTPException, Security, UploadFile, File
from fastapi.security import APIKeyHeader

# --- Local ---
from app.schemas import NL2SQLRequest, NL2SQLResponse, ClarifyResponse
from app.state import register_db
from nl2sql.pipeline import FinalResult
from app.dependencies import get_cache, get_nl2sql_service
from app.cache import NL2SQLCache
from app.services.nl2sql_service import NL2SQLService
from app.settings import get_settings
from app.errors import (
    AppError,
    BadRequestError,
    SafetyViolationError,
    DependencyError,
    PipelineRunError,
)

logger = logging.getLogger(__name__)
settings = get_settings()

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(key: Optional[str] = Security(api_key_header)):
    """
    Simple API key check using X-API-Key header and configured API keys.

    - Settings.api_keys_raw is a comma-separated list of keys.
    - If api_keys_raw is empty → auth disabled (dev mode).
    """
    raw = settings.api_keys_raw or ""
    allowed = {k.strip() for k in raw.split(",") if k.strip()}
    if not allowed:
        # No keys configured → treat as dev mode (auth off).
        return
    if not key or key not in allowed:
        raise HTTPException(status_code=401, detail="invalid API key")


####################################
# ---- Simple in-memory cache for NL→SQL responses ----

# Cache TTL and max size from centralized settings
_CACHE_TTL = settings.cache_ttl_sec
_CACHE_MAX = settings.cache_max_entries
_CACHE: Dict[Tuple[str, str, str], Tuple[float, Dict[str, Any]]] = {}


def _norm_q(s: str) -> str:
    """Normalize a user query for cache key purposes."""
    return (s or "").strip().lower()


def _schema_key(preview: str) -> str:
    """Hash the schema preview so we do not store huge strings in the cache key."""
    return hashlib.md5((preview or "").encode()).hexdigest()


def _ck(
    db_id: Optional[str],
    query: str,
    schema_preview: str,
) -> str:
    """
    Build a stable cache key for (db_id, query, schema_preview).

    We keep the external cache API string-based, and hash the
    potentially large schema_preview to avoid huge dictionary keys.
    """
    # Normalize db_id
    db_part = db_id or "__default__"

    # Build a single string seed
    seed = f"{db_part}\n{query}\n{schema_preview}"

    # Short, deterministic key
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()


def _cache_gc(now: float) -> None:
    """
    Garbage-collect cache entries by TTL and max size.
    """
    # TTL eviction
    for k, (ts, _) in list(_CACHE.items()):
        if now - ts > _CACHE_TTL:
            _CACHE.pop(k, None)

    # Size eviction (naive FIFO-style)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)), None)


####################################

router = APIRouter(prefix="/nl2sql")

# -------------------------------
# Config / Defaults
# -------------------------------
DB_MODE = settings.db_mode.lower()  # "sqlite" or "postgres"

# Runtime upload storage for SQLite DBs
_DB_UPLOAD_DIR = settings.db_upload_dir
os.makedirs(_DB_UPLOAD_DIR, exist_ok=True)

# Optional: separate directory for other uploads (kept as-is for now)
UPLOAD_DIR = Path("data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

logger.debug(
    "NL2SQL router configured",
    extra={"db_mode": DB_MODE, "upload_dir": _DB_UPLOAD_DIR},
)


# -------------------------------
# Schema preview endpoint
# -------------------------------


@router.get("/schema")
def schema_endpoint(
    db_id: Optional[str] = None,
    svc: NL2SQLService = Depends(get_nl2sql_service),
):
    """
    Return a lightweight schema preview string for the given DB.

    - If db_id is provided, service will resolve the uploaded DB.
    - If not, service falls back to the default DB.
    - In postgres mode, caller must usually provide schema_preview explicitly.
    Domain errors (AppError subclasses) are handled by the global exception handler.
    This endpoint only wraps truly unexpected errors into a generic HTTP 500
    """
    try:
        preview = svc.get_schema_preview(db_id=db_id, override=None)
    except AppError:
        # Let the global AppError handler deal with it.
        raise
    except Exception as exc:
        logger.exception("Unexpected error in schema_endpoint", exc_info=exc)
        raise HTTPException(
            status_code=500,
            detail="failed to derive schema preview",
        ) from exc

    return {"schema_preview": preview}


# -------------------------------
# Helpers
# -------------------------------


def _to_dict(obj: Any) -> Any:
    """
    Convert dataclass-like objects (and similar) to plain dicts for JSON.
    """
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

    # Coerce duration to int with rounding
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
    """
    Upload a SQLite DB file and register it under a generated db_id.

    Only available when DB_MODE is 'sqlite':
    - Allowed extensions: .db, .sqlite
    - File size capped by configured upload_max_bytes (default 20 MB)
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
    max_bytes = settings.upload_max_bytes
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
        logger.debug("Failed to store uploaded DB file", exc_info=e)
        raise HTTPException(status_code=500, detail=f"Failed to store DB: {e}")

    register_db(db_id, out_path)
    logger.debug("Registered uploaded DB", extra={"db_id": db_id, "path": out_path})
    return {"db_id": db_id}


@router.get("/health")
def health():
    """Simple router-level health endpoint."""
    return {"status": "ok", "version": settings.app_version}


# -------------------------------
# Main NL2SQL endpoint
# -------------------------------


@router.post("", name="nl2sql_handler", dependencies=[Depends(require_api_key)])
def nl2sql_handler(
    request: NL2SQLRequest,
    svc: NL2SQLService = Depends(get_nl2sql_service),
    cache: NL2SQLCache = Depends(get_cache),
) -> NL2SQLResponse | ClarifyResponse | Dict[str, Any]:
    """
    Main NL→SQL handler.

    Flow:
    - Resolve schema preview (client override or derived from DB).
    - Check in-memory cache (db_id + query + schema hash).
    - Run the pipeline through NL2SQLService.
    - Map FinalResult to API response or HTTP error.
    """
    db_id = getattr(request, "db_id", None)

    # ---- schema preview ----
    try:
        final_preview = svc.get_schema_preview(
            db_id=db_id,
            override=request.schema_preview,
        )
    except AppError:
        # Domain-level errors are handled by the global AppError handler.
        raise
    except Exception as exc:
        logger.exception(
            "Unexpected error while preparing schema preview",
            exc_info=exc,
        )
        raise HTTPException(
            status_code=500,
            detail="failed to prepare schema",
        ) from exc

    # ---- cache lookup ----
    cache_key = _ck(db_id, request.query, final_preview)
    cached_payload = cache.get(cache_key)
    if cached_payload is not None:
        return cached_payload

    # ---- pipeline execution via service ----
    try:
        result = svc.run_query(
            query=request.query,
            db_id=db_id,
            schema_preview=final_preview,
        )
    except AppError:
        # Let the global handler convert it to an HTTP response.
        raise
    except Exception as exc:
        logger.exception("Unexpected pipeline crash in NL2SQLService.run_query")
        raise PipelineRunError(
            message="Internal pipeline error.",
            details=[str(exc)],
            extra={"stage": "unknown"},
        )

    # ---- type sanity check ----
    if not isinstance(result, FinalResult):
        logger.debug(
            "Pipeline returned unexpected type",
            extra={"type": type(result).__name__},
        )
        raise PipelineRunError(
            message="Pipeline returned unexpected type.",
            details=[type(result).__name__],
            extra={"stage": "unknown"},
        )

    # ---- ambiguity path → 200 with clarification questions ----
    if result.ambiguous:
        qs = result.questions or []
        return ClarifyResponse(ambiguous=True, questions=qs)

    # ---- error path: map pipeline failures to stable HTTP+JSON error contract ----
    if (not result.ok) or result.error:
        logger.debug(
            "Pipeline reported failure",
            extra={"ok": result.ok, "error": result.error, "details": result.details},
        )

        details = list(result.details or [])
        traces = list(result.traces or [])
        last_stage = str(traces[-1].get("stage", "unknown")) if traces else "unknown"
        details_l = " ".join(d.lower() for d in details)

        # 1) Safety violations → 422
        if last_stage == "safety":
            raise SafetyViolationError(
                message="Rejected by safety checks.",
                details=details or None,
                extra={"stage": last_stage},
            )

        # 2) Retryable dependency failures → 503
        retry_hints = (
            "timeout",
            "timed out",
            "rate limit",
            "429",
            "too many requests",
            "locked",
            "busy",
            "unavailable",
            "connection",
        )
        if any(h in details_l for h in retry_hints):
            raise DependencyError(
                message="Temporary dependency failure. Please retry.",
                details=details or None,
                extra={"stage": last_stage},
            )

        # 3) User-fixable parse/syntax-ish errors → 400
        user_hints = (
            "parse_error",
            "non-select",
            "explain not allowed",
            "multiple statements",
            "forbidden",
        )
        if any(h in details_l for h in user_hints):
            raise BadRequestError(
                message="Request could not be processed.",
                details=details or None,
                extra={"stage": last_stage},
            )

        # 4) Default → 500
        raise PipelineRunError(
            message="Pipeline failed unexpectedly.",
            details=details or None,
            extra={"stage": last_stage},
        )

    # ---- success path → 200 (normalize traces and executor result) ----
    traces = [_round_trace(t) for t in (result.traces or [])]

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

    # Store in cache (as plain dict)
    cache.set(cache_key, payload.model_dump())
    return payload
