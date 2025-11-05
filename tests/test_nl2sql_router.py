from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.routers import nl2sql
from nl2sql.pipeline import FinalResult

client = TestClient(app)
path = app.url_path_for("nl2sql_handler")


def fake_trace(stage: str) -> dict:
    """Minimal trace stub used across tests."""
    return {"stage": stage, "duration_ms": 10.0, "cost_usd": None, "notes": None}


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

    app.dependency_overrides[nl2sql.get_runner] = lambda: fake_run
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
        app.dependency_overrides.pop(nl2sql.get_runner, None)


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

    app.dependency_overrides[nl2sql.get_runner] = lambda: fake_run
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
        app.dependency_overrides.pop(nl2sql.get_runner, None)


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

    app.dependency_overrides[nl2sql.get_runner] = lambda: fake_run
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
        app.dependency_overrides.pop(nl2sql.get_runner, None)


# --- 4) Success with db_id (per-request pipeline) ----------------------------
def test_success_route_with_db_id(monkeypatch):
    """Should build a per-request pipeline when db_id is provided."""

    def fake_select_adapter(db_id: str):
        class DummyAdapter:
            pass

        return DummyAdapter()

    class DummyPipeline:
        def run(
            self, *, user_query: str, schema_preview: str | None = None
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
                traces=[fake_trace("executor")],
            )

    monkeypatch.setattr(nl2sql, "_select_adapter", fake_select_adapter)
    monkeypatch.setattr(nl2sql, "_build_pipeline", lambda _a: DummyPipeline())
    monkeypatch.setattr(
        nl2sql, "_derive_schema_preview", lambda _a: "CREATE TABLE t(id int);"
    )

    resp = client.post(path, json={"query": "anything", "db_id": "sqlite"})
    assert resp.status_code == 200
    assert resp.json()["sql"].startswith("SELECT")


# --- 5) Pipeline crash → 500 -------------------------------------------------
def test_pipeline_crash_returns_500():
    """Exceptions inside pipeline should result in HTTP 500 with a clear message."""

    def crash_run(*, user_query: str, schema_preview: str | None = None):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    app.dependency_overrides[nl2sql.get_runner] = lambda: crash_run
    try:
        resp = client.post(path, json={"query": "x"})
        assert resp.status_code == 500
        assert "Pipeline crash" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(nl2sql.get_runner, None)


# --- 6) Unexpected output type → 500 -----------------------------------------
def test_pipeline_returns_non_finalresult():
    """If pipeline returns a non-FinalResult, it must yield HTTP 500."""

    def bad_run(
        *, user_query: str, schema_preview: str | None = None
    ):  # no FinalResult
        return {"ok": True}

    app.dependency_overrides[nl2sql.get_runner] = lambda: bad_run
    try:
        resp = client.post(path, json={"query": "x"})
        assert resp.status_code == 500
        assert "unexpected type" in resp.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(nl2sql.get_runner, None)


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

    app.dependency_overrides[nl2sql.get_runner] = lambda: bad_ambiguous
    try:
        resp = client.post(path, json={"query": "x"})
        assert resp.status_code in (200, 400)
    finally:
        app.dependency_overrides.pop(nl2sql.get_runner, None)


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

    app.dependency_overrides[nl2sql.get_runner] = lambda: run_with_float_traces
    try:
        resp = client.post(path, json={"query": "x"})
        assert resp.status_code == 200
        traces = resp.json()["traces"]
        assert isinstance(traces, list) and traces
        assert isinstance(traces[0]["duration_ms"], int)
    finally:
        app.dependency_overrides.pop(nl2sql.get_runner, None)
