from fastapi.testclient import TestClient
from app.main import app
from nl2sql.types import StageResult, StageTrace

client = TestClient(app)


def fake_trace(stage: str):
    return StageTrace(stage=stage, duration_ms=10.0)


path = app.url_path_for("nl2sql_handler")


# --- 1) Clarify / ambiguity case ---------------------------------------------
def test_ambiguity_route(monkeypatch):
    from app.routers import nl2sql

    # mock pipeline to return StageResult with ambiguous=True
    def fake_run(*args, **kwargs):
        return StageResult(
            ok=True,
            data={
                "ambiguous": True,
                "questions": ["Which table do you mean?"],
                "traces": [fake_trace("detector")],
            },
        )

    monkeypatch.setattr(nl2sql._pipeline, "run", fake_run)

    resp = client.post(
        path,
        json={
            "query": "show all records",
            "schema_preview": "CREATE TABLE ...",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ambiguous"] is True
    assert "questions" in data


# --- 2) Error / failure case -------------------------------------------------
def test_error_route(monkeypatch):
    from app.routers import nl2sql

    def fake_run(*args, **kwargs):
        return StageResult(
            ok=False, error=["Bad SQL"], data={"traces": [fake_trace("safety")]}
        )

    monkeypatch.setattr(nl2sql._pipeline, "run", fake_run)

    resp = client.post(
        path,
        json={
            "query": "drop table users;",
            "schema_preview": "CREATE TABLE users(id int);",
        },
    )

    assert resp.status_code == 400
    assert "Bad SQL" in resp.json()["detail"]


# --- 3) Success / happy path -------------------------------------------------
def test_success_route(monkeypatch):
    from app.routers import nl2sql

    def fake_run(*args, **kwargs):
        return StageResult(
            ok=True,
            data={
                "ambiguous": False,
                "sql": "SELECT * FROM users;",
                "rationale": "Simple listing",
                "traces": [fake_trace("planner"), fake_trace("generator")],
            },
        )

    monkeypatch.setattr(nl2sql._pipeline, "run", fake_run)

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
