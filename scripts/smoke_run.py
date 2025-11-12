"""
Smoke test for NL2SQL Copilot

Creates a demo SQLite DB (with proper table casing),
uploads it, runs representative queries, and prints results.

Exit code is always 0 for metrics pipelines, even if some tests fail.
"""

import os
import sys
import json
import time
import sqlite3
import requests
from pathlib import Path

API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")
API_KEY = os.getenv("API_KEY", "dev-key")

DB_DIR = Path("/tmp/nl2sql_dbs")
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "smoke_demo.sqlite"


def ensure_demo_db(path: Path):
    """Create demo SQLite DB if missing."""
    if path.exists():
        print(f"✅ Demo DB already exists at {path}")
        return

    conn = sqlite3.connect(path)
    cur = conn.cursor()

    # --- create schema (fixed casing) ---
    cur.executescript(
        """
        DROP TABLE IF EXISTS Artist;
        DROP TABLE IF EXISTS Customer;
        DROP TABLE IF EXISTS Invoice;

        CREATE TABLE Artist (
            ArtistId INTEGER PRIMARY KEY,
            Name TEXT
        );

        CREATE TABLE Customer (
            CustomerId INTEGER PRIMARY KEY,
            FirstName TEXT,
            LastName TEXT,
            Country TEXT
        );

        CREATE TABLE Invoice (
            InvoiceId INTEGER PRIMARY KEY,
            CustomerId INTEGER,
            Total REAL,
            FOREIGN KEY(CustomerId) REFERENCES Customer(CustomerId)
        );

        INSERT INTO Artist (Name) VALUES
            ('Miles Davis'),
            ('Nina Simone'),
            ('Radiohead'),
            ('Björk'),
            ('Daft Punk');

        INSERT INTO Customer (FirstName, LastName, Country) VALUES
            ('Alice','Doe','USA'),
            ('Bob','Smith','Canada'),
            ('Claire','Johnson','France'),
            ('Diego','Martinez','Spain');

        INSERT INTO Invoice (CustomerId, Total) VALUES
            (1, 15.0),
            (2, 23.5),
            (3, 10.2),
            (4, 45.9),
            (1, 8.9);
        """
    )
    conn.commit()
    conn.close()
    print(f"✅ Demo DB created at {path}")


def upload_db_and_get_id(path: Path) -> str:
    """Upload DB file to API and return db_id."""
    url = f"{API_BASE}/api/v1/nl2sql/upload_db"
    headers = {"X-API-Key": API_KEY}
    with open(path, "rb") as f:
        resp = requests.post(url, headers=headers, files={"file": f})
    if resp.status_code != 200:
        print(f"❌ Upload failed: {resp.status_code} {resp.text}")
        sys.exit(0)
    data = resp.json()
    db_id = data.get("db_id")
    if not db_id:
        print(f"❌ Invalid upload response: {data}")
        sys.exit(0)
    print(f"✅ Uploaded DB, got db_id={db_id}")
    return db_id


def run_query(query: str, db_id: str):
    """Send a query to NL2SQL endpoint."""
    url = f"{API_BASE}/api/v1/nl2sql"
    headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
    payload = {"db_id": db_id, "query": query}

    t0 = time.time()
    resp = requests.post(url, headers=headers, json=payload)
    dt = (time.time() - t0) * 1000

    ok = resp.status_code == 200
    prefix = "✅" if ok else "❌"
    print(f"{prefix} {query} ({resp.status_code}) — {dt:.0f} ms")

    try:
        parsed = resp.json()
        print(json.dumps(parsed, indent=2)[:500])
    except Exception:
        print(resp.text[:500])

    print("-" * 80)
    return ok


def main():
    ensure_demo_db(DB_PATH)
    db_id = upload_db_and_get_id(DB_PATH)

    queries = [
        "How many artists are there?",
        "List all artist names",
        # ✅ Disambiguated phrasing
        "Which customer spent the most based on total invoice amount?",
        "Average invoice total per country",
        "DELETE FROM users;",  # expected to fail (Safety check)
    ]

    success = True
    for q in queries:
        ok = run_query(q, db_id)
        success &= ok

    if success:
        print("🎉 Smoke tests completed successfully.")
    else:
        print("⚠️  Some smoke tests failed, but continuing for metrics.")
    sys.exit(0)


if __name__ == "__main__":
    main()
