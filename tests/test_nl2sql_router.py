from fastapi.testclient import TestClient
from app.main import app
from nl2sql.pipeline import FinalResult

client = TestClient(app)

def fake_trace(stage: str) -> dict:
    # FinalResult.traces is a list of dicts (StageTrace.__dict__)
    return {"stage": stage, "duration_ms": 10.0, "cost_usd": None, "notes": None}

path = app.url_path_for("nl2sql_handler")


# --- 1) Clarify / ambiguity case ---------------------------------------------
def test_ambiguity_route(monkeypatch):
    from app.routers import nl2sql

    # mock pipeline to return FinalResult with ambiguous=True
    def fake_run(*args, **kwargs):
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
    assert isinstance(data["questions"], list)


# --- 2) Error / failure case -------------------------------------------------
def test_error_route(monkeypatch):
    from app.routers import nl2sql

    def fake_run(*args, **kwargs):
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
    assert any(t["stage"] == "generator" for t in data["traces"])
