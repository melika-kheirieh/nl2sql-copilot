from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import get_nl2sql_service
from nl2sql.errors.codes import ErrorCode
from nl2sql.pipeline import FinalResult

client = TestClient(app)
path = app.url_path_for("nl2sql_handler")


def _trace(stage: str) -> dict:
    return {"stage": stage, "duration_ms": 1.0, "cost_usd": None, "notes": None}


class DummyService:
    def __init__(self, fn):
        self._fn = fn

    def get_schema_preview(self, db_id, override):
        if override is not None:
            return override
        return "DUMMY(table1(col1))"

    def run_query(self, *, query: str, db_id, schema_preview: str):
        return self._fn(user_query=query, schema_preview=schema_preview)


def test_db_locked_is_503_and_retryable():
    def fake_run(*, user_query: str, schema_preview: str | None = None) -> FinalResult:
        return FinalResult(
            ok=False,
            ambiguous=False,
            error=True,
            error_code=ErrorCode.DB_LOCKED,
            details=["database is locked"],
            questions=None,
            sql=None,
            rationale=None,
            verified=None,
            traces=[_trace("executor")],
        )

    app.dependency_overrides[get_nl2sql_service] = lambda: DummyService(fake_run)
    try:
        resp = client.post(
            path,
            headers={"X-Request-ID": "req-123"},
            json={"query": "select 1", "schema_preview": "CREATE TABLE t(x int);"},
        )

        assert resp.status_code == 503
        body = resp.json()

        assert body["error"]["code"] == ErrorCode.DB_LOCKED.value
        assert body["error"]["retryable"] is True
    finally:
        app.dependency_overrides.pop(get_nl2sql_service, None)
