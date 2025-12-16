from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi.testclient import TestClient

from app.dependencies import get_nl2sql_service
from app.main import app
from nl2sql.errors.codes import ErrorCode
from nl2sql.pipeline import FinalResult

client = TestClient(app)
path = app.url_path_for("nl2sql_handler")


def fake_trace(stage: str) -> dict:
    """
    Minimal trace stub used across tests.

    Keep it close to the public response shape to avoid future breakage when
    the router adds/normalizes trace fields.
    """
    return {
        "stage": stage,
        "duration_ms": 10.0,
        "token_in": None,
        "token_out": None,
        "cost_usd": None,
        "notes": None,
    }


def assert_error_contract(
    resp,
    *,
    expected_status: int,
    expected_code: str | None = None,
    retryable: bool | None = None,
    details_contains: str | None = None,
) -> dict[str, Any]:
    """
    Assert the stable error contract returned by the router.

    Required:
      - error.code: str (non-empty)
      - error.retryable: bool

    Optional (if present, must have the correct type):
      - error.message: str
      - error.details: list[str]
      - error.request_id: str
      - error.extra: dict
    """
    assert resp.status_code == expected_status, resp.text
    body = resp.json()

    assert isinstance(body, dict), f"Expected JSON object, got: {type(body)}"
    assert "error" in body and isinstance(body["error"], dict), (
        f"Missing 'error' in: {body}"
    )

    err = body["error"]

    # --- required ---
    assert isinstance(err.get("code"), str) and err["code"], f"Bad error.code: {err}"
    assert isinstance(err.get("retryable"), bool), f"Bad error.retryable: {err}"

    # --- optional type checks ---
    if err.get("details") is not None:
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
    """Minimal NL2SQLService substitute for router tests."""

    def __init__(self, runner: Callable[..., FinalResult]):
        self._runner = runner

    def get_schema_preview(self, db_id: Optional[str], override: Optional[str]) -> str:
        return override or "DUMMY(table1(col1, col2))"

    def run_query(
        self,
        *,
        query: str,
        db_id: Optional[str],
        schema_preview: str,
    ) -> FinalResult:
        return self._runner(user_query=query, schema_preview=schema_preview)


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
        assert isinstance(data.get("questions"), list) and data["questions"]
    finally:
        app.dependency_overrides.pop(get_nl2sql_service, None)


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
        assert isinstance(data.get("traces"), list) and data["traces"]
        assert any(t.get("stage") == "planner" for t in data["traces"])
        assert any(t.get("stage") == "generator" for t in data["traces"])
    finally:
        app.dependency_overrides.pop(get_nl2sql_service, None)


def test_success_route_with_db_id():
    """Should forward db_id to the service when provided in the request body."""
    called: Dict[str, Any] = {}

    class DbAwareDummyService(DummyService):
        def run_query(
            self, *, query: str, db_id: Optional[str], schema_preview: str
        ) -> FinalResult:
            called["db_id"] = db_id
            return super().run_query(
                query=query, db_id=db_id, schema_preview=schema_preview
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
        assert called.get("db_id") == "sqlite"
    finally:
        app.dependency_overrides.pop(get_nl2sql_service, None)


def test_pipeline_crash_returns_500_with_error_contract():
    """Unhandled exceptions inside the service/pipeline should yield 500 with error contract."""

    def crash_run(*, user_query: str, schema_preview: str | None = None) -> FinalResult:
        raise RuntimeError("boom")

    app.dependency_overrides[get_nl2sql_service] = lambda: DummyService(crash_run)
    try:
        resp = client.post(path, json={"query": "x"})
        err = assert_error_contract(resp, expected_status=500, retryable=False)

        # If your router includes message, it's fine; but we do not require it here.
        if "message" in err:
            assert isinstance(err["message"], str)
    finally:
        app.dependency_overrides.pop(get_nl2sql_service, None)


def test_pipeline_returns_non_finalresult():
    """If pipeline returns a non-FinalResult, it must yield HTTP 500 (error contract)."""

    def bad_run(*, user_query: str, schema_preview: str | None = None):
        return {"ok": True}

    app.dependency_overrides[get_nl2sql_service] = lambda: DummyService(bad_run)  # type: ignore[arg-type]
    try:
        assert_error_contract(
            client.post(path, json={"query": "x"}), expected_status=500
        )
    finally:
        app.dependency_overrides.pop(get_nl2sql_service, None)


def test_ambiguity_without_questions_is_normalized_to_empty_list():
    """
    Router policy: if ambiguous=True but questions is None, do not crash.
    Normalize questions to an empty list and return 200.
    """

    def bad_ambiguous(
        *, user_query: str, schema_preview: str | None = None
    ) -> FinalResult:
        return FinalResult(
            ok=True,
            ambiguous=True,
            error=False,
            details=["ambiguous but no questions"],
            questions=None,  # intentionally missing
            sql=None,
            rationale=None,
            verified=None,
            traces=[fake_trace("detector")],
        )

    app.dependency_overrides[get_nl2sql_service] = lambda: DummyService(bad_ambiguous)
    try:
        resp = client.post(path, json={"query": "x"})
        assert resp.status_code == 200, resp.text

        data = resp.json()
        assert data.get("ambiguous") is True
        assert "questions" in data
        assert isinstance(data["questions"], list)
        assert data["questions"] == []
    finally:
        app.dependency_overrides.pop(get_nl2sql_service, None)


def test_validation_422_missing_query():
    """Pydantic/FastAPI should return 422 when required field is missing."""
    resp = client.post(path, json={"schema_preview": "CREATE TABLE t(id int);"})
    assert resp.status_code == 422


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
        assert isinstance(traces[0]["duration_ms"], int)
    finally:
        app.dependency_overrides.pop(get_nl2sql_service, None)
