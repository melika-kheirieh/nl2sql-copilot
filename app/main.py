import os
import time
from typing import Protocol, runtime_checkable, cast

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import PlainTextResponse

from app.routers import nl2sql

# Prometheus
from prometheus_client import (
    Counter,
    Histogram,
    CollectorRegistry,
    generate_latest,
    CONTENT_TYPE_LATEST,
)


@runtime_checkable
class HasPing(Protocol):
    """Minimal interface for adapters that support a connectivity check."""

    def ping(self) -> None: ...


# ---- Optionally restore uploaded DB map ----
try:
    from app.routers.nl2sql import _load_db_map

    _load_db_map()
except Exception as e:
    print(f"⚠️ DB map not restored: {e}")

app = FastAPI(
    title="NL2SQL Copilot Prototype",
    version=os.getenv("APP_VERSION", "0.1.0"),
    description="Convert natural language to safe & verified SQL",
)

app.include_router(nl2sql.router, prefix="/api/v1")

# ---- Prometheus metrics ----
REGISTRY: CollectorRegistry = CollectorRegistry()

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["path", "method", "status_code"],
    registry=REGISTRY,
)

REQUEST_LATENCY = Histogram(
    "http_request_latency_seconds",
    "Request latency",
    ["path", "method"],
    registry=REGISTRY,
)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response: Response = await call_next(request)
    elapsed = time.perf_counter() - start

    # Use route path if available, else raw path (typed guard for mypy)
    route = request.scope.get("route")
    path = (
        route.path
        if (route is not None and hasattr(route, "path"))
        else request.url.path
    )

    REQUEST_COUNT.labels(
        path=path, method=request.method, status_code=str(response.status_code)
    ).inc()
    REQUEST_LATENCY.labels(path=path, method=request.method).observe(elapsed)
    return response


# --- Liveness (super light) ---
@app.get("/healthz", response_class=PlainTextResponse, tags=["system"])
def healthz() -> str:
    return "ok"


# --- Readiness (checks DB/env lightly) ---
@app.get("/readyz", response_class=PlainTextResponse, tags=["system"])
def readyz() -> str:
    mode = os.getenv("DB_MODE", "sqlite").lower()
    try:
        if mode == "postgres":
            from adapters.db.postgres_adapter import PostgresAdapter

            dsn = os.environ["POSTGRES_DSN"]
            # Call ping inline; avoid cross-branch variable typing
            cast(HasPing, PostgresAdapter(dsn)).ping()
        else:
            from adapters.db.sqlite_adapter import SQLiteAdapter

            db_path = os.getenv("SQLITE_DB_PATH", "data/chinook.db")
            cast(HasPing, SQLiteAdapter(db_path)).ping()

        # if not os.getenv("PROXY_API_KEY"): pass
        return "ready"
    except Exception:
        raise HTTPException(status_code=503, detail="not ready")


@app.get("/")
def root():
    return {"status": "ok", "message": "NL2SQL Copilot API is running"}


@app.get("/health")
def health():
    # You might want to replace the placeholders with real checks later.
    return {"status": "ok", "db": "connected", "llm": "reachable", "uptime_sec": 123.4}


@app.get("/metrics", tags=["system"])
def metrics():
    data = generate_latest(REGISTRY)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)
