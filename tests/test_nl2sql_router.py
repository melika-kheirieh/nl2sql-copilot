from __future__ import annotations

from typing import Callable, Optional, Dict, Any

from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import get_nl2sql_service
from nl2sql.pipeline import FinalResult
from nl2sql.errors.codes import ErrorCode

client = TestClient(app)
path = app.url_path_for("nl2sql_handler")


def fake_trace(stage: str) -> dict:
    """Minimal trace stub used across tests."""
    return {"stage": stage, "duration_ms": 10.0, "cost_usd": None, "notes": None}


def assert_error_contract(
    resp,
    *,
    expected_status: int,
    expected_code: str | None = None,
    retryable: bool | None = None,
    details_contains: str | None = None,
) -> dict[str, Any]:
    """
    Assert the stable error contract produced by the AppError handler.

    Contract shape:
      {"error": {"code", "message", "details", "retryable", "request_id", "extra"}}
    """
    assert resp.status_code == expected_status, resp.text
    body = resp.json()

    assert isinstance(body, dict), f"Expected JSON object, got: {type(body)}"
    assert "error" in body and isinstance(body["error"], dict), (
        f"Missing 'error' in: {body}"
    )

    err = body["error"]

    # --- required ---
    assert "code" in err and isinstance(err["code"], str) and err["code"], (
        f"Bad error.code: {err}"
    )
    assert "retryable" in err and isinstance(err["retryable"], bool), (
        f"Bad error.retryable: {err}"
    )

    # --- optional (type-checked if present) ---
    if "details" in err and err["details"] is not None:
        assert isinstance(err["details"], list), (
            f"error.details must be list[str]: {err}"
        )
        assert all(isinstance(x, str) for x in err["details"]), (
            f"error.details must be list[str]: {err}"
        )

    if "message" in err:
        assert isinstance(err["message"], str), f"error.message must be str: {err}"

    if "request_id" in err:
        assert isinstance(err["request_id"], str), (
            f"error.request_id must be str: {err}"
        )

    if "extra" in err:
        assert isinstance(err["extra"], dict), f"error.extra must be dict: {err}"

    # --- expectations ---
    if expected_code is not None:
        assert err["code"] == expected_code, (
            f"Expected code={expected_code}, got {err['code']}"
        )

    if retryable is not None:
        assert err["retryable"] is retryable, (
            f"Expected retryable={retryable}, got {err['retryable']}"
        )

    if details_contains is not None:
        details = err.get("details") or []
        assert any(details_contains in d for d in details), (
            f"'{details_contains}' not in details={details}"
        )

    return err


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
def test_error_route_safety_violation_is_422():
    """Safety-stage failures should return 422 with the structured error contract."""

    def fake_run(*, user_query: str, schema_preview: str | None = None) -> FinalResult:
        return FinalResult(
            ok=False,
            ambiguous=False,
            error=True,
            error_code=ErrorCode.SAFETY_NON_SELECT,
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
        assert_error_contract(
            resp,
            expected_status=422,
            expected_code=ErrorCode.SAFETY_NON_SELECT.value,
            retryable=False,
            details_contains="Bad SQL",
        )
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
        assert called.get("db_id") == "sqlite"
    finally:
        app.dependency_overrides.pop(get_nl2sql_service, None)


# --- 5) Pipeline crash → 500 -------------------------------------------------
def test_pipeline_crash_returns_500_with_error_contract():
    """Unhandled exceptions inside the service/pipeline should yield 500 with error contract."""

    def crash_run(*, user_query: str, schema_preview: str | None = None) -> FinalResult:  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    app.dependency_overrides[get_nl2sql_service] = lambda: DummyService(crash_run)
    try:
        resp = client.post(path, json={"query": "x"})
        # Code may differ depending on router mapping; enforce contract + 500.
        assert resp.status_code == 500
        body = resp.json()
        assert "error" in body
        assert body["error"]["retryable"] is False
        assert isinstance(body["error"]["code"], str) and body["error"]["code"]
        assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    finally:
        app.dependency_overrides.pop(get_nl2sql_service, None)


# --- 6) Unexpected output type → 500 -----------------------------------------
def test_pipeline_returns_non_finalresult():
    """If pipeline returns a non-FinalResult, it must yield HTTP 500 (error contract)."""

    def bad_run(*, user_query: str, schema_preview: str | None = None):
        return {"ok": True}

    app.dependency_overrides[get_nl2sql_service] = lambda: DummyService(bad_run)  # type: ignore[arg-type]
    try:
        resp = client.post(path, json={"query": "x"})
        assert resp.status_code == 500
        body = resp.json()
        assert "error" in body
        assert isinstance(body["error"]["code"], str) and body["error"]["code"]
    finally:
        app.dependency_overrides.pop(get_nl2sql_service, None)


# --- 7) Ambiguous without questions (edge case) ------------------------------
def test_ambiguity_without_questions_edge_case():
    """
    If ambiguous=True but questions is None, handler should not crash.

    NOTE:
    Behavior may vary depending on router validation:
    - 200: treated as clarify response
    - 500: treated as internal inconsistency
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
        assert resp.status_code in (200, 500)
        if resp.status_code == 500:
            assert "error" in resp.json()
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
