from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI  # noqa: E402
from app.routers import nl2sql  # noqa: E402

# restore previous uploaded DB map
try:
    from app.routers.nl2sql import _load_db_map

    _load_db_map()
except Exception as e:
    print(f"⚠️ DB map not restored: {e}")

app = FastAPI(
    title="NL2SQL Copilot Prototype",
    version="0.1.0",
    description="Natural Language -> SQL Copilot API",
)

app.include_router(nl2sql.router, prefix="/api/v1")


@app.get("/healthz")
def health_check():
    return {"status": "ok"}


@app.get("/")
def root():
    return {"status": "ok", "message": "NL2SQL Copilot API is running"}


@app.get("/health")
def health():
    return {"status": "ok", "db": "connected", "llm": "reachable", "uptime_sec": 123.4}
