from __future__ import annotations

import uuid
from typing import Any, Dict, Optional, List

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.errors import AppError


def register_exception_handlers(app: FastAPI) -> None:
    """Register global exception handlers for the FastAPI application."""

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

        status = getattr(exc, "http_status", 500)
        code = getattr(exc, "code", "app_error")
        message = getattr(exc, "message", str(exc))
        retryable = bool(getattr(exc, "retryable", False))
        extra: Dict[str, Any] = getattr(exc, "extra", {}) or {}
        details: Optional[List[str]] = getattr(exc, "details", None)

        payload = {
            "error": {
                "code": code,
                "message": message,
                "details": details,
                "retryable": retryable,
                "request_id": request_id,
                "extra": extra,
            }
        }

        headers = {"X-Request-ID": request_id}
        if retryable:
            headers["Retry-After"] = "2"

        return JSONResponse(status_code=status, content=payload, headers=headers)
