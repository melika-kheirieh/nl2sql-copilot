from __future__ import annotations

# --- Stdlib ---
from dataclasses import asdict, is_dataclass
import os
from pathlib import Path
import uuid
from typing import Any, Dict, Optional, cast
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
    PipelineRunError,
)
from nl2sql.errors.mapper import map_error
from nl2sql.errors.codes import ErrorCode

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


router = APIRouter(prefix="/nl2sql")

# -------------------------------
# Config / Defaults
# -------------------------------
DB_MODE = settings.db_mode.lower()

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


@router.get("/schema")
def schema_endpoint(
    db_id: Optional[str] = None,
    svc: NL2SQLService = Depends(get_nl2sql_service),
):
    try:
        preview = svc.get_schema_preview(db_id=db_id, override=None)
    except AppError:
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


def _ck(db_id: Optional[str], query: str, schema_preview: str) -> str:
    db_part = db_id or "__default__"
    seed = f"{db_part}\n{query}\n{schema_preview}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()


@router.post(
    "",
    name="nl2sql_handler",
    dependencies=[Depends(require_api_key)],
    response_model=NL2SQLResponse | ClarifyResponse,
)
def nl2sql_handler(
    request: NL2SQLRequest,
    svc: NL2SQLService = Depends(get_nl2sql_service),
    cache: NL2SQLCache = Depends(get_cache),
) -> NL2SQLResponse | ClarifyResponse:
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
        # Cache stores dicts; convert back to response models for type safety.
        if isinstance(cached_payload, dict) and cached_payload.get("ambiguous") is True:
            return ClarifyResponse.model_validate(cached_payload)
        return NL2SQLResponse.model_validate(cached_payload)

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
        resp = ClarifyResponse(questions=(result.questions or []))
        # Optional-but-useful: cache clarify responses too
        cache.set(cache_key, resp.model_dump())
        return resp

    # ---- error path: contract-based mapping (Phase 3) ----
    if (not result.ok) or result.error:
        logger.debug(
            "Pipeline reported failure",
            extra={
                "ok": result.ok,
                "error": result.error,
                "error_code": getattr(result, "error_code", None),
                "details": result.details,
            },
        )

        # 1) Normalize code (never string-match here)
        code = result.error_code or ErrorCode.PIPELINE_CRASH

        # 2) Single source of truth for HTTP semantics
        status, retryable = map_error(code)

        # 3) Stable error payload for UI/clients
        raise HTTPException(
            status_code=status,
            detail={
                "error": {
                    "code": code.value,
                    "retryable": retryable,
                    "details": list(result.details or []),
                }
            },
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
