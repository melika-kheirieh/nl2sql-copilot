"""Portable smoke request for NL2SQL Copilot.

- Ensures a demo SQLite DB exists under /tmp/nl2sql_dbs/smoke_demo.sqlite
- Uploads it to the API
- Runs a few representative queries
- Exits non-zero on failure (so Make/CI can trust it)

Env:
  API_BASE: base URL of API (default: http://127.0.0.1:8000)
  API_KEY:  API key header value (default: dev-key)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests


API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000").rstrip("/")
API_KEY = os.getenv("API_KEY", "dev-key")

DB_DIR = Path("/tmp/nl2sql_dbs")
DB_PATH = DB_DIR / "smoke_demo.sqlite"


def _ensure_demo_db(path: Path) -> None:
    """Delegate to scripts/smoke_run.py if available; otherwise fail."""
    # Your repo already has scripts/smoke_run.py which creates the DB deterministically.
    from smoke_run import ensure_demo_db  # type: ignore

    ensure_demo_db(path)


def _upload_db_and_get_id(path: Path) -> str:
    url = f"{API_BASE}/api/v1/nl2sql/upload_db"
    headers = {"X-API-Key": API_KEY}
    with path.open("rb") as f:
        resp = requests.post(url, headers=headers, files={"file": f}, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Upload failed: {resp.status_code} {resp.text[:400]}")
    data = resp.json()
    db_id = data.get("db_id")
    if not db_id:
        raise RuntimeError(f"Invalid upload response: {data}")
    return str(db_id)


def _run_query(db_id: str, query: str) -> dict:
    url = f"{API_BASE}/api/v1/nl2sql"
    headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
    payload = {"db_id": db_id, "query": query}

    t0 = time.time()
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    dt_ms = int(round((time.time() - t0) * 1000))

    out: dict = {}
    try:
        out = resp.json()
    except Exception:
        out = {"raw": resp.text}

    return {"status": resp.status_code, "latency_ms": dt_ms, "body": out}


def main() -> int:
    DB_DIR.mkdir(parents=True, exist_ok=True)

    try:
        _ensure_demo_db(DB_PATH)
    except Exception as e:
        print(f"❌ Failed to create demo DB: {e}")
        return 2

    try:
        db_id = _upload_db_and_get_id(DB_PATH)
    except Exception as e:
        print(f"❌ Failed to upload demo DB: {e}")
        return 3

    checks = [
        ("How many artists are there?", True),
        ("Which customer spent the most based on total invoice amount?", True),
        ("DELETE FROM users;", False),  # must be blocked
    ]

    ok_all = True
    for q, should_succeed in checks:
        r = _run_query(db_id=db_id, query=q)
        status = r["status"]
        body = r["body"]
        print(f"\nQuery: {q}")
        print(f"HTTP {status} | {r['latency_ms']} ms")
        print(json.dumps(body, indent=2)[:800])

        if should_succeed:
            if status != 200:
                ok_all = False
        else:
            # A safety violation should not be a 200
            if status == 200:
                ok_all = False

    if ok_all:
        print("\n✅ demo-smoke passed")
        return 0

    print("\n❌ demo-smoke failed (see output above)")
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
