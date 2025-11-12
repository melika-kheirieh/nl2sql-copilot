import os
import time
from typing import Dict, Any

DB_TTL_SECONDS = int(os.getenv("NL2SQL_DB_TTL_SEC", "86400"))
DB_MAP: Dict[str, Dict[str, Any]] = {}


def register_db(db_id: str, path: str) -> None:
    DB_MAP[db_id] = {"path": path, "created_at": time.time()}


def get_db_path(db_id: str) -> str | None:
    entry = DB_MAP.get(db_id)
    return entry["path"] if entry else None


def cleanup_stale_dbs() -> None:
    now = time.time()
    stale = [
        k for k, v in DB_MAP.items() if now - v.get("created_at", now) > DB_TTL_SECONDS
    ]
    for k in stale:
        DB_MAP.pop(k, None)
