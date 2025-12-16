import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import nl2sql as nl2sql_router

client = TestClient(app)


def _create_demo_db(db_path: Path) -> None:
    """
    Create a tiny demo SQLite DB for deterministic e2e testing.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE albums (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                sales INTEGER NOT NULL
            );
            """
        )
        cur.executemany(
            "INSERT INTO albums (title, sales) VALUES (?, ?);",
            [
                ("A", 100),
                ("B", 250),
                ("C", 180),
                ("D", 90),
                ("E", 300),
                ("F", 210),
            ],
        )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.slow
def test_e2e_nl2sql_with_demo_db(tmp_path, monkeypatch):
    """
    Full end-to-end smoke test with the real NL2SQLService wiring, but deterministic:

    - Creates a temporary demo SQLite DB in tmp_path (no dependency on repo files).
    - Disables API key auth for this test (no env/config dependency).
    - Hits the canonical nl2sql_handler path and asserts basic response shape.
    """
    # 1) Create a deterministic demo DB
    db_path = tmp_path / "demo.sqlite"
    _create_demo_db(db_path)

    # 2) Point Settings to this DB via env (works if Settings reads env vars)
    monkeypatch.setenv("DEFAULT_SQLITE_PATH", str(db_path))

    # 3) Disable API key auth just for this test
    app.dependency_overrides[nl2sql_router.require_api_key] = lambda: None
    try:
        path = app.url_path_for("nl2sql_handler")
        payload = {"query": "Top 5 albums by sales"}
        response = client.post(path, json=payload)

        assert response.status_code == 200, (
            f"status={response.status_code}, body={response.text}"
        )

        data = response.json()

        # Basic shape checks
        assert data.get("ambiguous") is False

        sql = data.get("sql")
        assert isinstance(sql, str) and sql.strip(), f"Invalid sql: {sql!r}"
        lowered = sql.lower()
        assert "select" in lowered, f"SQL does not look like SELECT: {sql!r}"

        traces = data.get("traces")
        assert isinstance(traces, list) and traces, f"Invalid traces: {traces!r}"
        first = traces[0]
        assert isinstance(first, dict)
        assert "stage" in first, f"trace missing stage: {json.dumps(first)}"
        assert "duration_ms" in first, f"trace missing duration_ms: {json.dumps(first)}"
    finally:
        app.dependency_overrides.pop(nl2sql_router.require_api_key, None)
