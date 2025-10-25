import pytest
from nl2sql.pipeline import Pipeline
from nl2sql.types import StageResult, StageTrace


# --- Dummy stages to isolate pipeline -----------------------------------------

class DummyDetector:
    """Simulates ambiguity detector stage."""
    def __init__(self, ambiguous=False):
        self.ambiguous = ambiguous

    def detect(self, user_query, schema_preview):
        # If ambiguous=True, return clarification questions
        return ["Which column?"] if self.ambiguous else []


class DummyPlanner:
    """Simulates planner stage."""
    def run(self, *, user_query, schema_preview):
        trace = StageTrace(stage="planner", duration_ms=1.0)
        if "fail_plan" in user_query:
            return StageResult(ok=False, error=["Planner failed"], trace=trace)
        return StageResult(ok=True, data={"plan": "plan text"}, trace=trace)


class DummyGenerator:
    """Simulates generator stage."""
    def run(self, *, user_query, schema_preview, plan_text, clarify_answers):
        trace = StageTrace(stage="generator", duration_ms=1.0)
        if "fail_gen" in user_query:
            return StageResult(ok=False, error=["Generator failed"], trace=trace)
        sql = "SELECT * FROM singer;"
        rationale = "List all singers."
        return StageResult(ok=True, data={"sql": sql, "rationale": rationale}, trace=trace)


class DummySafety:
    """Simulates safety stage."""
    def check(self, sql):
        trace = StageTrace(stage="safety", duration_ms=1.0)
        if "DROP" in sql.upper():
            return StageResult(ok=False, error=["Unsafe SQL"], trace=trace)
        return StageResult(ok=True, data={"sql": sql, "rationale": "safe"}, trace=trace)


# --- 1) Success path ----------------------------------------------------------
def test_pipeline_success():
    pipeline = Pipeline(
        detector=DummyDetector(ambiguous=False),
        planner=DummyPlanner(),
        generator=DummyGenerator(),
        safety=DummySafety()
    )

    r = pipeline.run(
        user_query="show all singers",
        schema_preview="CREATE TABLE singer(id int, name text);"
    )

    assert isinstance(r, StageResult)
    assert r.ok is True
    data = r.data or {}
    assert data["sql"].lower().startswith("select")
    assert any(t.stage == "planner" for t in data["traces"])
    assert any(t.stage == "generator" for t in data["traces"])
    assert any(t.stage == "safety" for t in data["traces"])


# --- 2) Ambiguity case --------------------------------------------------------
def test_pipeline_ambiguity():
    pipeline = Pipeline(
        detector=DummyDetector(ambiguous=True),
        planner=DummyPlanner(),
        generator=DummyGenerator(),
        safety=DummySafety()
    )

    r = pipeline.run(
        user_query="show data",
        schema_preview="CREATE TABLE x(id int);"
    )

    assert isinstance(r, StageResult)
    assert r.ok is True
    assert r.data["ambiguous"] is True
    assert isinstance(r.data["questions"], list)


# --- 3) Planner failure -------------------------------------------------------
def test_pipeline_plan_fail():
    pipeline = Pipeline(
        detector=DummyDetector(),
        planner=DummyPlanner(),
        generator=DummyGenerator(),
        safety=DummySafety()
    )
    r = pipeline.run(
        user_query="fail_plan",
        schema_preview="CREATE TABLE singer(id int);"
    )
    assert isinstance(r, StageResult)
    assert r.ok is False
    assert "Planner failed" in " ".join(r.error or [])


# --- 4) Generator failure -----------------------------------------------------
def test_pipeline_gen_fail():
    pipeline = Pipeline(
        detector=DummyDetector(),
        planner=DummyPlanner(),
        generator=DummyGenerator(),
        safety=DummySafety()
    )
    r = pipeline.run(
        user_query="fail_gen",
        schema_preview="CREATE TABLE singer(id int);"
    )
    assert r.ok is False
    assert "Generator failed" in " ".join(r.error or [])


# --- 5) Safety failure --------------------------------------------------------
def test_pipeline_safety_fail():
    class UnsafeGen(DummyGenerator):
        def run(self, **kw):
            trace = StageTrace(stage="generator", duration_ms=1.0)
            # Generate a DROP TABLE → unsafe
            return StageResult(ok=True, data={"sql": "DROP TABLE x;", "rationale": "oops"}, trace=trace)

    pipeline = Pipeline(
        detector=DummyDetector(),
        planner=DummyPlanner(),
        generator=UnsafeGen(),
        safety=DummySafety()
    )
    r = pipeline.run(
        user_query="drop something",
        schema_preview="CREATE TABLE x(id int);"
    )
    assert r.ok is False
    assert "unsafe" in " ".join(r.error or []).lower()
