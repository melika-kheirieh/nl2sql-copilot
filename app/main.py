import os
import time

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import PlainTextResponse, RedirectResponse, JSONResponse
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

from nl2sql.prom import REGISTRY
from app.routers import dev, nl2sql
from app.settings import get_settings
from app.exception_handlers import register_exception_handlers

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    # Best-effort .env loading; app must not crash if dotenv is missing.
    pass


settings = get_settings()

# ----------------------------------------------------------------------------
#  App definition
# ----------------------------------------------------------------------------
application = FastAPI(
    title="NL2SQL Copilot Prototype",
    version=settings.app_version,
    description="Convert natural language to safe & verified SQL",
)
register_exception_handlers(application)

# Register only versioned API
application.include_router(nl2sql.router, prefix="/api/v1")

# Register Dev-only routes (only when APP_ENV=dev)
if os.getenv("APP_ENV", "dev").lower() == "dev":
    application.include_router(dev.router, prefix="/api/v1")


@application.exception_handler(HTTPException)
async def http_exception_to_error_contract(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


# ----------------------------------------------------------------------------
#  Prometheus Metrics Middleware
# ----------------------------------------------------------------------------
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["path", "method", "status_code"],
    registry=REGISTRY,
)
REQUEST_LATENCY = Histogram(
    "http_request_latency_seconds",
    "Request latency (seconds)",
    ["path", "method"],
    registry=REGISTRY,
)


@application.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response: Response = await call_next(request)
    elapsed = time.perf_counter() - start
    route = request.scope.get("route")
    path = getattr(route, "path", None) or request.url.path
    name = getattr(route, "name", None) or path

    REQUEST_COUNT.labels(
        path=name,
        method=request.method,
        status_code=str(getattr(response, "status_code", 500)),
    ).inc()
    REQUEST_LATENCY.labels(path=name, method=request.method).observe(elapsed)
    return response


# ----------------------------------------------------------------------------
#  System Endpoints
# ----------------------------------------------------------------------------
@application.get("/healthz", response_class=PlainTextResponse, tags=["system"])
def healthz() -> str:
    return "ok"


@application.get("/readyz", response_class=PlainTextResponse, tags=["system"])
def readyz() -> str:
    """
    Lightweight readiness probe:

    - For postgres mode → ping PostgresAdapter using configured DSN.
    - For sqlite mode   → ping SQLiteAdapter using configured default path.
    """
    mode = settings.db_mode.lower()
    try:
        if mode == "postgres":
            from adapters.db.postgres_adapter import PostgresAdapter

            dsn = (settings.postgres_dsn or "").strip()
            if not dsn:
                raise RuntimeError("POSTGRES_DSN is not configured for readiness check")

            pg = PostgresAdapter(dsn)
            ping_fn = getattr(pg, "ping", None)
            if callable(ping_fn):
                ping_fn()
        else:
            from adapters.db.sqlite_adapter import SQLiteAdapter

            db_path = settings.default_sqlite_path or "data/Chinook_Sqlite.sqlite"
            sq = SQLiteAdapter(db_path)
            ping_fn = getattr(sq, "ping", None)
            if callable(ping_fn):
                ping_fn()
        return "ready"
    except Exception:
        raise HTTPException(status_code=503, detail="not ready")


@application.get("/")
def root():
    return {"status": "ok", "message": "NL2SQL Copilot API is running"}


@application.get("/health")
def health():
    # This is a higher-level health stub; real checks can be wired later
    return {"status": "ok", "db": "connected", "llm": "reachable", "uptime_sec": 123.4}


@application.get("/metrics", tags=["system"])
def metrics():
    data = generate_latest(REGISTRY)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


# ----------------------------------------------------------------------------
#  Legacy Redirects (clean compatibility)
# ----------------------------------------------------------------------------
@application.api_route("/nl2sql", methods=["GET", "POST"])
async def legacy_nl2sql_redirect(request: Request):
    return RedirectResponse(url="/api/v1/nl2sql", status_code=307)


@application.api_route(
    "/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
)
async def legacy_catch_all(request: Request, path: str):
    # If it's already versioned, don't redirect (prevents infinite 307 loop)
    if request.url.path.startswith("/api/v1/") or request.url.path == "/api/v1":
        raise HTTPException(status_code=404, detail="Not Found")

    # Redirect only legacy root-level endpoints
    return RedirectResponse(url=f"/api/v1/{path}", status_code=307)
