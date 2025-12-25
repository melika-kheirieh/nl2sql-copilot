#!/usr/bin/env python3
"""Generate a reproducible cache/metrics screenshot workload.

What it does:
1) Waits for API readiness (healthz + readyz + router health).
2) Uploads a demo SQLite DB to the API (upload_db) and captures db_id.
3) Sends a burst of unique queries (mostly misses).
4) Sends repeated queries over ~70–90s (hits), with jitter so charts look natural.
5) Triggers a safety violation once (should be blocked) WITHOUT failing the whole demo.
6) Sends a final "recovery" query (OK).
7) (Optional) Prints a Prometheus instant-query sanity check for cache metrics.

Expected API:
- POST {API_BASE}/api/v1/nl2sql/upload_db  (multipart form: file=@db.sqlite) -> {db_id: "..."}
- POST {API_BASE}/api/v1/nl2sql           (json: {db_id, query, schema_preview?}) -> 200 or 4xx/5xx
- GET  {API_BASE}/healthz
- GET  {API_BASE}/readyz
- GET  {API_BASE}/api/v1/nl2sql/health

Env:
- API_BASE  (default http://127.0.0.1:8000)
- API_KEY   (default dev-key)
- DB_PATH   (default /tmp/nl2sql_dbs/smoke_demo.sqlite)
- PROM_BASE (default http://127.0.0.1:9090) (optional; set empty to skip)
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import time
from dataclasses import dataclass
from typing import Any


def sh(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command and return the completed process (text mode)."""
    return subprocess.run(
        args,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


@dataclass(frozen=True)
class Cfg:
    api_base: str
    api_key: str
    db_path: str
    prom_base: str | None


def load_cfg() -> Cfg:
    api_base = os.getenv("API_BASE", "http://127.0.0.1:8000").rstrip("/")
    api_key = os.getenv("API_KEY", "dev-key")
    db_path = os.getenv("DB_PATH", "/tmp/nl2sql_dbs/smoke_demo.sqlite")
    prom_base_env = os.getenv("PROM_BASE", "http://127.0.0.1:9090").rstrip("/")
    prom_base: str | None = prom_base_env if prom_base_env else None
    return Cfg(api_base=api_base, api_key=api_key, db_path=db_path, prom_base=prom_base)


def wait_for_ready(cfg: Cfg, timeout_s: float = 60.0) -> None:
    """Wait until API is responsive and ready.

    We try multiple endpoints because on cold starts the container may accept TCP but reset early requests.
    """
    endpoints = [
        f"{cfg.api_base}/healthz",
        f"{cfg.api_base}/readyz",
        f"{cfg.api_base}/api/v1/nl2sql/health",
    ]

    start = time.time()
    last = ""
    while time.time() - start < timeout_s:
        ok = True
        for url in endpoints:
            cp = subprocess.run(
                ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", url],
                check=False,
                text=True,
                capture_output=True,
            )
            code = (cp.stdout or "").strip()
            if code != "200":
                ok = False
                last = f"url={url} http={code} stderr={cp.stderr.strip()!r}"
                break

        if ok:
            return

        time.sleep(0.6)

    raise RuntimeError(f"API not ready after {timeout_s:.0f}s. Last={last}")


def upload_db(cfg: Cfg) -> str:
    if not os.path.exists(cfg.db_path):
        raise FileNotFoundError(f"DB_PATH not found: {cfg.db_path}")

    url = f"{cfg.api_base}/api/v1/nl2sql/upload_db"

    # Do NOT use -f here; on error we want the body.
    cp = subprocess.run(
        [
            "curl",
            "-sS",
            "-D",
            "-",
            "-H",
            f"X-API-Key: {cfg.api_key}",
            "-F",
            f"file=@{cfg.db_path}",
            url,
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    if cp.returncode != 0:
        raise RuntimeError(
            f"upload_db curl failed (rc={cp.returncode}). stderr={cp.stderr.strip()!r}\nstdout:\n{cp.stdout}"
        )

    # Split headers/body
    raw = cp.stdout
    parts = raw.split("\r\n\r\n", 1)
    if len(parts) != 2:
        parts = raw.split("\n\n", 1)
    if len(parts) != 2:
        raise RuntimeError(f"upload_db returned unexpected response:\n{raw}")

    headers, body = parts[0], parts[1]
    status_line = headers.splitlines()[0] if headers.splitlines() else ""
    if " 200 " not in status_line:
        raise RuntimeError(f"upload_db non-200.\n{headers}\n\n{body}")

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"upload_db returned non-JSON body.\n{headers}\n\n{body}"
        ) from e

    db_id = data.get("db_id")
    if not isinstance(db_id, str) or not db_id:
        raise RuntimeError(f"upload_db response missing db_id: {data}")
    return db_id


def post_query(
    cfg: Cfg, *, db_id: str, query: str, fail_on_non_200: bool = True
) -> int:
    """POST a query. Returns HTTP status code. Optionally raises on non-200 with full response."""
    url = f"{cfg.api_base}/api/v1/nl2sql"
    payload = json.dumps({"db_id": db_id, "query": query})

    cp = subprocess.run(
        [
            "curl",
            "-sS",
            "-D",
            "-",
            "-H",
            f"X-API-Key: {cfg.api_key}",
            "-H",
            "Content-Type: application/json",
            "-d",
            payload,
            url,
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    if cp.returncode != 0:
        raise RuntimeError(
            f"query curl failed (rc={cp.returncode}). query={query!r}\n"
            f"stderr={cp.stderr.strip()!r}\nstdout:\n{cp.stdout}"
        )

    raw = cp.stdout
    parts = raw.split("\r\n\r\n", 1)
    if len(parts) != 2:
        parts = raw.split("\n\n", 1)
    if len(parts) != 2:
        raise RuntimeError(
            f"query returned unexpected response. query={query!r}\n{raw}"
        )

    headers, body = parts[0], parts[1]
    status_line = headers.splitlines()[0] if headers.splitlines() else ""

    # Parse HTTP status code from first line: HTTP/1.1 200 OK
    status_code = 0
    try:
        status_code = int(status_line.split()[1])
    except Exception:
        status_code = 0

    if fail_on_non_200 and status_code != 200:
        raise RuntimeError(f"Non-200 response for query={query!r}\n{headers}\n\n{body}")

    return status_code


def prom_instant_query(cfg: Cfg, expr: str) -> Any | None:
    if not cfg.prom_base:
        return None
    url = f"{cfg.prom_base}/api/v1/query"
    cp = sh(["curl", "-fsS", url, "--data-urlencode", f"query={expr}"])
    return json.loads(cp.stdout)


def post_dev_safety(cfg: Cfg, sql: str) -> int:
    """Trigger the Safety stage directly (dev endpoint) so OK-rate panels aren't affected."""
    url = f"{cfg.api_base}/api/v1/_dev/safety"
    payload = json.dumps({"sql": sql})
    cp = subprocess.run(
        [
            "curl",
            "-sS",
            "-D",
            "-",
            "-H",
            f"X-API-Key: {cfg.api_key}",
            "-H",
            "Content-Type: application/json",
            "-d",
            payload,
            url,
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    raw = cp.stdout
    # Parse status code from HTTP status line.
    header_block = raw.split("\r\n\r\n", 1)[0]
    status_line = header_block.splitlines()[0] if header_block.splitlines() else ""
    try:
        return int(status_line.split()[1])
    except Exception:
        return 0


def print_cache_sanity(cfg: Cfg) -> None:
    if not cfg.prom_base:
        return

    candidates = [
        "nl2sql:cache_hit_ratio",
        'sum(rate(cache_events_total{hit="true"}[5m])) / sum(rate(cache_events_total[5m]))',
    ]

    for expr in candidates:
        try:
            data = prom_instant_query(cfg, expr)
            if data is None:
                continue
        except Exception:
            continue
        try:
            result = data["data"]["result"]
        except Exception:
            continue
        if result:
            value = result[0].get("value", [None, None])[1]
            print(f"[prom] {expr} = {value}")
            return

    print("[prom] Could not find cache ratio metric yet (ok right after cold start).")


def main() -> int:
    cfg = load_cfg()

    random.seed(7)  # deterministic-ish graphs

    print("Waiting for API readiness...")
    wait_for_ready(cfg, timeout_s=75)

    print("Uploading DB...")
    db_id = upload_db(cfg)
    print(f"DB_ID={db_id}")

    # Phase A: warm-up (mostly misses)
    unique = [
        "List the first 10 artists.",
        "Which customer spent the most based on total invoice amount?",
        "Top 5 tracks by duration.",
    ]

    print("Phase A: warmup (mostly misses)...")
    for q in unique:
        post_query(cfg, db_id=db_id, query=q)
        time.sleep(0.7)

    # Phase B: repeats (hits)
    repeats = [
        "Which customer spent the most based on total invoice amount?",
        "List the first 10 artists.",
        "Which customer spent the most based on total invoice amount?",
        "Top 5 tracks by duration.",
        "List the first 10 artists.",
    ]

    print("Phase B: repeated queries (hits)...")
    # ~60 requests over ~1.5–2 minutes (enough signal for window-based panels)
    for _ in range(60):
        q = random.choice(repeats)
        post_query(cfg, db_id=db_id, query=q)
        time.sleep(1.1 + random.random() * 0.5)

    # Give Prometheus a moment to scrape after the last request.
    time.sleep(10)

    print("\nSanity check:")
    print_cache_sanity(cfg)

    print("\n>>> NOW TAKE SCREENSHOT <<<")
    print(
        "Grafana: set time range to Last 10 minutes (or Last 15 minutes), refresh 5s, wait ~10s."
    )
    print("Tip: if hit% looks low, wait one more scrape interval and refresh.")

    # Phase C: safety check (expected block) — after screenshot so OK% stays high in-window.
    print("\nPhase C: safety check (expected block, after screenshot)...")
    code = post_dev_safety(cfg, "drop table users;")
    print(f"Safety request status={code} (expected non-200)")

    # Phase D: recovery
    print("Phase D: recovery...")
    post_query(cfg, db_id=db_id, query="List the first 10 artists.")

    print("\nDone. Suggested screenshot steps:")
    print("  1) In Grafana set time range: Last 10 minutes (or Last 15 minutes).")
    print("  2) Set refresh to 5s–10s and wait 10–20s for panels to catch up.")
    print("  3) Expect Requests-in-window > 10 and Cache Hit Ratio > 0.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
