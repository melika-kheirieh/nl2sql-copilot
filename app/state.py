from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Dict, Tuple, Optional

log = logging.getLogger(__name__)


class DbUploadStore:
    """
    In-memory registry for uploaded DB files with simple TTL-based cleanup.

    Responsibilities:
    - Track uploaded DBs by db_id -> filesystem path.
    - Enforce a TTL for uploaded DBs.
    - Remove stale entries and delete underlying files when expired.
    """

    def __init__(self, upload_dir: str, ttl_seconds: int) -> None:
        self.upload_dir = upload_dir
        self.ttl_seconds = ttl_seconds
        self._entries: Dict[str, Tuple[str, float]] = {}

        Path(self.upload_dir).mkdir(parents=True, exist_ok=True)
        log.debug(
            "Initialized DbUploadStore",
            extra={
                "upload_dir": self.upload_dir,
                "ttl_seconds": self.ttl_seconds,
            },
        )

    def _now(self) -> float:
        return time.time()

    def _is_expired(self, ts: float, now: Optional[float] = None) -> bool:
        if now is None:
            now = self._now()
        return (now - ts) > self.ttl_seconds

    def _gc_locked(self, now: Optional[float] = None) -> None:
        """
        Internal garbage collector.

        Removes stale entries and deletes the corresponding files on disk
        if they still exist.
        """
        if now is None:
            now = self._now()

        to_delete = []
        for db_id, (path, ts) in list(self._entries.items()):
            if self._is_expired(ts, now) or (not os.path.exists(path)):
                to_delete.append((db_id, path))

        for db_id, path in to_delete:
            self._entries.pop(db_id, None)
            try:
                if os.path.exists(path):
                    os.remove(path)
                    log.debug(
                        "Deleted expired uploaded DB file",
                        extra={"db_id": db_id, "path": path},
                    )
            except Exception as exc:
                # Best-effort cleanup; do not crash the app because of FS issues.
                log.debug(
                    "Failed to delete expired uploaded DB file",
                    extra={"db_id": db_id, "path": path},
                    exc_info=exc,
                )

    def cleanup_stale(self) -> None:
        """
        Public cleanup entry point.

        Can be called periodically (or on access) to remove expired DBs.
        """
        self._gc_locked()

    def register(self, db_id: str, path: str) -> None:
        """
        Register a new uploaded DB with the given db_id and filesystem path.
        """
        now = self._now()
        self._entries[db_id] = (path, now)
        log.debug(
            "Registered uploaded DB in DbUploadStore",
            extra={"db_id": db_id, "path": path},
        )
        # Optionally clean up old entries as we go.
        self._gc_locked(now=now)

    def resolve(self, db_id: str) -> Optional[str]:
        """
        Resolve db_id to a filesystem path if it exists and is not expired.

        Returns:
            str path if valid, or None if missing/expired.
        """
        self._gc_locked()
        entry = self._entries.get(db_id)
        if not entry:
            return None

        path, ts = entry
        if self._is_expired(ts):
            # Expired between last GC and now; treat as missing.
            self._entries.pop(db_id, None)
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception as exc:
                log.debug(
                    "Failed to delete DB file on late-expiration",
                    extra={"db_id": db_id, "path": path},
                    exc_info=exc,
                )
            return None

        if not os.path.exists(path):
            # File disappeared; drop the entry.
            self._entries.pop(db_id, None)
            return None

        return path


# --------------------------------------------------------------------
# Module-level singleton and legacy helper functions
# --------------------------------------------------------------------

_DB_UPLOAD_DIR = os.getenv("DB_UPLOAD_DIR", "/tmp/nl2sql_dbs")
_DB_TTL_SECONDS = int(os.getenv("DB_TTL_SECONDS", "7200"))  # default: 2 hours

_STORE = DbUploadStore(upload_dir=_DB_UPLOAD_DIR, ttl_seconds=_DB_TTL_SECONDS)


def register_db(db_id: str, path: str) -> None:
    """
    Backwards-compatible helper:

    Register an uploaded DB in the process-wide DbUploadStore.
    """
    _STORE.register(db_id, path)


def cleanup_stale_dbs() -> None:
    """
    Backwards-compatible helper:

    Trigger TTL-based cleanup of stale DB entries.
    """
    _STORE.cleanup_stale()


def get_db_path(db_id: str) -> Optional[str]:
    """
    Backwards-compatible helper:

    Resolve db_id to a filesystem path if it is still valid.
    """
    return _STORE.resolve(db_id)
