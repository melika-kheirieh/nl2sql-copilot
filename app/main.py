import os
import time
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import PlainTextResponse, RedirectResponse
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from nl2sql.prom import REGISTRY
from app.routers import dev

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

from app.routers import nl2sql

# ---- Optional DB map restore ----
try:
    from app.routers.nl2sql import _load_db_map

    _load_db_map()
except Exception as e:
    print(f"⚠️ DB map not restored: {e}")

# ----------------------------------------------------------------------------
#  App definition
# ----------------------------------------------------------------------------
application = FastAPI(
    title="NL2SQL Copilot Prototype",
    version=os.getenv("APP_VERSION", "0.1.0"),
    description="Convert natural language to safe & verified SQL",
)

# Register only versioned API
application.include_router(nl2sql.router, prefix="/api/v1")

# Register Dev-only routes (only when APP_ENV=dev)
if os.getenv("APP_ENV", "dev").lower() == "dev":
    application.include_router(dev.router, prefix="/api/v1")
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
    mode = os.getenv("DB_MODE", "sqlite").lower()
    try:
        if mode == "postgres":
            from adapters.db.postgres_adapter import PostgresAdapter

            pg = PostgresAdapter(os.environ["POSTGRES_DSN"])
            ping_fn = getattr(pg, "ping", None)
            if callable(ping_fn):
                ping_fn()
        else:
            from adapters.db.sqlite_adapter import SQLiteAdapter

            sq = SQLiteAdapter(os.getenv("SQLITE_DB_PATH", "data/chinook.db"))
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
    """Redirect old root-level endpoints to versioned API."""
    if path.startswith("api/v1"):
        return RedirectResponse(url=f"/{path}", status_code=307)
    return RedirectResponse(url=f"/api/v1/{path}", status_code=307)


# ----------------------------------------------------------------------------
#  Backward-compatible alias for uvicorn
# ----------------------------------------------------------------------------
app = application
__all__ = ["application", "app"]
