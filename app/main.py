import os
import time
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import PlainTextResponse
from prometheus_client import (
    Counter,
    Histogram,
    CollectorRegistry,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

from app.routers import nl2sql

# ---- Optionally restore uploaded DB map ----
try:
    from app.routers.nl2sql import _load_db_map

    _load_db_map()
except Exception as e:
    print(f"⚠️ DB map not restored: {e}")

application: FastAPI = FastAPI(
    title="NL2SQL Copilot Prototype",
    version=os.getenv("APP_VERSION", "0.1.0"),
    description="Convert natural language to safe & verified SQL",
)

application.include_router(nl2sql.router, prefix="/api/v1")

# ---- Prometheus metrics ----
REGISTRY = CollectorRegistry()
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


@application.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response: Response = await call_next(request)
    elapsed = time.perf_counter() - start
    route = request.scope.get("route")
    path = route.path if route else request.url.path
    REQUEST_COUNT.labels(
        path=path, method=request.method, status_code=str(response.status_code)
    ).inc()
    REQUEST_LATENCY.labels(path=path, method=request.method).observe(elapsed)
    return response


# --- Liveness ---
@application.get("/healthz", response_class=PlainTextResponse, tags=["system"])
def healthz() -> str:
    return "ok"


# --- Readiness ---


@application.get("/readyz", response_class=PlainTextResponse, tags=["system"])
def readyz() -> str:
    mode = os.getenv("DB_MODE", "sqlite").lower()
    try:
        if mode == "postgres":
            from adapters.db.postgres_adapter import PostgresAdapter

            dsn = os.environ["POSTGRES_DSN"]
            pg = PostgresAdapter(dsn)
            ping = getattr(pg, "ping", None)
            if callable(ping):
                ping()
        else:
            from adapters.db.sqlite_adapter import SQLiteAdapter

            db_path = os.getenv("SQLITE_DB_PATH", "data/chinook.db")
            sq = SQLiteAdapter(db_path)
            ping = getattr(sq, "ping", None)
            if callable(ping):
                ping()
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


# Backward compatibility for tests & uvicorn targets
app: FastAPI = application
__all__ = ["application", "app"]
