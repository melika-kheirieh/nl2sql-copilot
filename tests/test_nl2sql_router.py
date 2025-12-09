from __future__ import annotations

from typing import Callable, Optional, Dict, Any

from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import get_nl2sql_service
from nl2sql.pipeline import FinalResult

client = TestClient(app)
path = app.url_path_for("nl2sql_handler")


def fake_trace(stage: str) -> dict:
    """Minimal trace stub used across tests."""
    return {"stage": stage, "duration_ms": 10.0, "cost_usd": None, "notes": None}


class DummyService:
    """
    Minimal NL2SQLService substitute for router tests.

    It delegates pipeline execution to a provided runner function and exposes
    the same public methods used by the router: get_schema_preview, run_query.
    """

    def __init__(self, runner: Callable[..., FinalResult]):
        self._runner = runner

    def get_schema_preview(self, db_id: Optional[str], override: Optional[str]) -> str:
        """
        For router tests we do not care about the actual schema content.

        - If override is provided, return it to mimic the real service behavior.
        - Otherwise return a fixed dummy schema preview.
        """
        if override is not None:
            return override
        return "DUMMY(table1(col1, col2))"

    def run_query(
        self,
        *,
        query: str,
        db_id: Optional[str],
        schema_preview: str,
    ) -> FinalResult:
        """
        Delegate to the underlying runner, adapting argument names to the old
        runner signature used in tests.
        """
        return self._runner(user_query=query, schema_preview=schema_preview)


# --- 1) Clarify / ambiguity case ---------------------------------------------
def test_ambiguity_route():
    """Should return 200 with ambiguous=True and questions present."""

    def fake_run(*, user_query: str, schema_preview: str | None = None) -> FinalResult:
        return FinalResult(
            ok=True,
            ambiguous=True,
            error=False,
            details=["Ambiguities found: 1"],
            questions=["Which table do you mean?"],
            sql=None,
            rationale=None,
            verified=None,
            traces=[fake_trace("detector")],
        )

    app.dependency_overrides[get_nl2sql_service] = lambda: DummyService(fake_run)
    try:
        resp = client.post(
            path,
            json={"query": "show all records", "schema_preview": "CREATE TABLE ..."},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ambiguous"] is True
        assert "questions" in data and isinstance(data["questions"], list)
    finally:
        app.dependency_overrides.pop(get_nl2sql_service, None)


# --- 2) Error / failure case -------------------------------------------------
def test_error_route():
    """Should return 400 and include aggregated details in 'detail'."""

    def fake_run(*, user_query: str, schema_preview: str | None = None) -> FinalResult:
        return FinalResult(
            ok=False,
            ambiguous=False,
            error=True,
            details=["Bad SQL"],
            questions=None,
            sql=None,
            rationale=None,
            verified=None,
            traces=[fake_trace("safety")],
        )

    app.dependency_overrides[get_nl2sql_service] = lambda: DummyService(fake_run)
    try:
        resp = client.post(
            path,
            json={
                "query": "drop table users;",
                "schema_preview": "CREATE TABLE users(id int);",
            },
        )
        assert resp.status_code == 400
        assert "Bad SQL" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_nl2sql_service, None)


# --- 3) Success / happy path -------------------------------------------------
def test_success_route():
    """Should return 200, include SQL and traces with expected stages."""

    def fake_run(*, user_query: str, schema_preview: str | None = None) -> FinalResult:
        return FinalResult(
            ok=True,
            ambiguous=False,
            error=False,
            details=None,
            questions=None,
            sql="SELECT * FROM users;",
            rationale="Simple listing",
            verified=True,
            traces=[fake_trace("planner"), fake_trace("generator")],
        )

    app.dependency_overrides[get_nl2sql_service] = lambda: DummyService(fake_run)
    try:
        resp = client.post(
            path,
            json={
                "query": "show all users",
                "schema_preview": "CREATE TABLE users(id int, name text);",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["sql"].lower().startswith("select")
        assert isinstance(data["traces"], list)
        assert any(t["stage"] == "planner" for t in data["traces"])
        assert any(t["stage"] == "generator" for t in data["traces"])
    finally:
        app.dependency_overrides.pop(get_nl2sql_service, None)


# --- 4) Success with db_id (per-request pipeline) ----------------------------
def test_success_route_with_db_id():
    """Should forward db_id to the service when provided in the request body."""

    called: Dict[str, Any] = {}

    class DbAwareDummyService(DummyService):
        def run_query(
            self,
            *,
            query: str,
            db_id: Optional[str],
            schema_preview: str,
        ) -> FinalResult:
            # Record db_id for assertion, then delegate to the base runner
            called["db_id"] = db_id
            return super().run_query(
                query=query,
                db_id=db_id,
                schema_preview=schema_preview,
            )

    def fake_run(*, user_query: str, schema_preview: str | None = None) -> FinalResult:
        return FinalResult(
            ok=True,
            ambiguous=False,
            error=False,
            details=None,
            questions=None,
            sql="SELECT 1;",
            rationale=None,
            verified=True,
            traces=[fake_trace("executor")],
        )

    app.dependency_overrides[get_nl2sql_service] = lambda: DbAwareDummyService(fake_run)
    try:
        resp = client.post(path, json={"query": "anything", "db_id": "sqlite"})
        assert resp.status_code == 200
        assert resp.json()["sql"].startswith("SELECT")
        # Ensure db_id was forwarded correctly to the service
        assert called.get("db_id") == "sqlite"
    finally:
        app.dependency_overrides.pop(get_nl2sql_service, None)


# --- 5) Pipeline crash → 500 -------------------------------------------------
def test_pipeline_crash_returns_500():
    """Exceptions inside pipeline should result in HTTP 500 with a clear message."""

    def crash_run(*, user_query: str, schema_preview: str | None = None) -> FinalResult:  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    app.dependency_overrides[get_nl2sql_service] = lambda: DummyService(crash_run)
    try:
        resp = client.post(path, json={"query": "x"})
        assert resp.status_code == 500
        # New handler uses a generic message for internal pipeline errors
        assert "internal pipeline error" in resp.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(get_nl2sql_service, None)


# --- 6) Unexpected output type → 500 -----------------------------------------
def test_pipeline_returns_non_finalresult():
    """If pipeline returns a non-FinalResult, it must yield HTTP 500."""

    def bad_run(
        *, user_query: str, schema_preview: str | None = None
    ):  # no FinalResult
        return {"ok": True}

    app.dependency_overrides[get_nl2sql_service] = lambda: DummyService(bad_run)  # type: ignore[arg-type]
    try:
        resp = client.post(path, json={"query": "x"})
        assert resp.status_code == 500
        assert "unexpected type" in resp.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(get_nl2sql_service, None)


# --- 7) Ambiguous without questions (edge case) ------------------------------
def test_ambiguity_without_questions_edge_case():
    """
    If ambiguous=True but questions is None, handler should not crash.
    Accept either 200 (if handler treats it as clarify) or 400 (if treated as error).
    """

    def bad_ambiguous(
        *, user_query: str, schema_preview: str | None = None
    ) -> FinalResult:
        return FinalResult(
            ok=True,
            ambiguous=True,
            error=False,
            details=["ambiguous but no questions"],
            questions=None,
            sql=None,
            rationale=None,
            verified=None,
            traces=[fake_trace("detector")],
        )

    app.dependency_overrides[get_nl2sql_service] = lambda: DummyService(bad_ambiguous)
    try:
        resp = client.post(path, json={"query": "x"})
        assert resp.status_code in (200, 400)
    finally:
        app.dependency_overrides.pop(get_nl2sql_service, None)


# --- 8) FastAPI validation (422) ---------------------------------------------
def test_validation_422_missing_query():
    """Pydantic/FastAPI should return 422 when required field is missing."""
    resp = client.post(path, json={"schema_preview": "CREATE TABLE t(id int);"})
    assert resp.status_code == 422


# --- 9) Trace rounding to int ------------------------------------------------
def test_traces_are_rounded_to_ints():
    """duration_ms in traces must be coerced/rounded to int in the response."""

    def run_with_float_traces(
        *, user_query: str, schema_preview: str | None = None
    ) -> FinalResult:
        return FinalResult(
            ok=True,
            ambiguous=False,
            error=False,
            details=None,
            questions=None,
            sql="SELECT 1;",
            rationale=None,
            verified=True,
            traces=[
                {"stage": "x", "duration_ms": 12.7, "notes": None, "cost_usd": None}
            ],
        )

    app.dependency_overrides[get_nl2sql_service] = lambda: DummyService(
        run_with_float_traces
    )
    try:
        resp = client.post(path, json={"query": "x"})
        assert resp.status_code == 200
        traces = resp.json()["traces"]
        assert isinstance(traces, list) and traces
        assert isinstance(traces[0]["duration_ms"], int)
    finally:
        app.dependency_overrides.pop(get_nl2sql_service, None)


def test_nl2sql_handler_returns_sql():
    """
    Integration-style smoke test on the router shape:
    - hit the canonical path for nl2sql_handler
    - ensure we get 200 with SQL + traces fields in the body.

    We still rely on FastAPI wiring + dependency injection, but we stub the
    underlying NL2SQLService with DummyService to avoid depending on the
    real filesystem / demo DB / pipeline config.
    """

    def fake_run(*, user_query: str, schema_preview: str | None = None) -> FinalResult:
        return FinalResult(
            ok=True,
            ambiguous=False,
            error=False,
            details=None,
            questions=None,
            sql="SELECT * FROM albums ORDER BY sales DESC LIMIT 5;",
            rationale="Top 5 albums by sales",
            verified=True,
            traces=[fake_trace("planner"), fake_trace("executor")],
        )

    # Use the same DummyService plumbing as other tests
    app.dependency_overrides[get_nl2sql_service] = lambda: DummyService(fake_run)
    try:
        payload = {"query": "Top 5 albums by sales"}
        resp = client.post(path, json=payload)
        assert resp.status_code == 200

        data = resp.json()
        assert "sql" in data
        assert "traces" in data
        assert data["sql"].lower().startswith("select")
        assert isinstance(data["traces"], list) and data["traces"]
    finally:
        app.dependency_overrides.pop(get_nl2sql_service, None)
