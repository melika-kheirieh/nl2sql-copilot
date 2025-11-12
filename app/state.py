import os
import time
import logging
from pathlib import Path
from typing import Optional, TypedDict

log = logging.getLogger(__name__)

# ------------------------------
# Config
# ------------------------------

# default upload directory (can override via .env)
_DB_UPLOAD_DIR = Path(os.getenv("DB_UPLOAD_DIR", "/tmp/nl2sql_dbs"))
_DB_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class DBEntry(TypedDict):
    path: str
    ts: float


# in-memory map: {db_id: {"path": str, "ts": float}}
DB_MAP: dict[str, DBEntry] = {}

# cleanup threshold (hours)
DB_TTL_HOURS = 6


# ------------------------------
# Helpers
# ------------------------------


def register_db(db_id: str, path: str) -> None:
    """Register new DB in memory (and ensure dir exists)."""
    _DB_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    DB_MAP[db_id] = {"path": path, "ts": time.time()}
    log.info(f"📦 Registered DB {db_id} -> {path}")


def cleanup_stale_dbs() -> None:
    """Remove expired DBs from /tmp/nl2sql_dbs and memory map."""
    now = time.time()
    cutoff = DB_TTL_HOURS * 3600
    stale_ids = [db_id for db_id, entry in DB_MAP.items() if now - entry["ts"] > cutoff]
    for db_id in stale_ids:
        path_str = DB_MAP[db_id]["path"]
        path = Path(path_str)
        try:
            if path.exists():
                path.unlink()
                log.info(f"🧹 Deleted stale DB: {path}")
        except FileNotFoundError:
            pass
        DB_MAP.pop(db_id, None)


def get_db_path(db_id: str) -> Optional[str]:
    """Return full path of an uploaded DB (persistent lookup)."""
    entry = DB_MAP.get(db_id)
    if entry:
        path_str = entry["path"]
        if Path(path_str).exists():
            return path_str

    candidates = [
        _DB_UPLOAD_DIR / f"{db_id}.sqlite",
        _DB_UPLOAD_DIR / f"{db_id}.db",
        Path("data/uploads") / f"{db_id}.sqlite",
        Path("data/uploads") / f"{db_id}.db",
    ]
    for p in candidates:
        if p.exists():
            log.info(f"🔍 Recovered DB path for {db_id}: {p}")
            return str(p)

    log.warning(f"⚠️ DB file not found for id={db_id}")
    return None
