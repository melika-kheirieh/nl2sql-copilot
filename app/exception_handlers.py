from __future__ import annotations

from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.errors import AppError


def register_exception_handlers(app: FastAPI) -> None:
    """
    Register global exception handlers for the FastAPI application.
    """

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        """
        Map domain-level AppError instances to HTTP responses.
        This keeps routers thin and lets the domain raise AppError freely.
        """
        status = getattr(exc, "http_status", 500)
        code = getattr(exc, "code", "app_error")
        message = getattr(exc, "message", str(exc))
        extra: Dict[str, Any] = getattr(exc, "extra", {}) or {}

        payload = {
            "code": code,
            "message": message,
            "extra": extra,
        }
        return JSONResponse(status_code=status, content=payload)
