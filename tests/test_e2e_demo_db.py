import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.settings import get_settings

client = TestClient(app)


@pytest.mark.slow
def test_e2e_nl2sql_with_demo_db():
    """
    Full end-to-end smoke test with the real NL2SQLService wiring:

    - Uses the default SQLite demo DB configured in Settings.
    - Hits the canonical nl2sql_handler path (usually /api/v1/nl2sql).
    - Asserts we get 200 + a non-empty SQL string and traces list.
    """

    settings = get_settings()

    # 1) Ensure demo DB exists at the configured location
    db_path = Path(settings.default_sqlite_path)
    assert db_path.exists(), f"Demo DB not found at {db_path!s}"

    # 2) Prepare headers (handle optional API key)
    headers = {}
    raw_keys = (settings.api_keys_raw or "").split(",")
    keys = [k.strip() for k in raw_keys if k.strip()]
    if keys:
        # Use the first configured key
        headers["X-API-Key"] = keys[0]

    # 3) Call the real endpoint
    path = app.url_path_for("nl2sql_handler")
    payload = {"query": "Top 5 albums by sales"}
    response = client.post(path, json=payload, headers=headers)

    # If this fails, seeing the body helps a lot
    assert response.status_code == 200, (
        f"status={response.status_code}, body={response.text}"
    )

    data = response.json()

    # 4) Basic shape checks on the response
    assert data.get("ambiguous") is False

    sql = data.get("sql")
    assert isinstance(sql, str) and sql.strip(), f"Invalid sql in response: {sql!r}"
    lowered = sql.lower()
    assert "select" in lowered, f"SQL does not look like a SELECT: {sql!r}"

    traces = data.get("traces")
    assert isinstance(traces, list) and traces, f"Invalid traces: {traces!r}"
    first = traces[0]
    assert isinstance(first, dict)
    assert "stage" in first, f"trace missing stage: {json.dumps(first)}"
    assert "duration_ms" in first, f"trace missing duration_ms: {json.dumps(first)}"
