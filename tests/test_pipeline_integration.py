from nl2sql.pipeline import Pipeline, FinalResult
from nl2sql.types import StageResult, StageTrace


# --- Dummy stages to isolate pipeline -----------------------------------------

class DummyDetector:
    """Simulates ambiguity detector stage."""

    def __init__(self, ambiguous: bool = False):
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
        return StageResult(
            ok=True, data={"sql": sql, "rationale": rationale}, trace=trace
        )


class DummySafety:
    """Simulates safety stage."""

    # NOTE: pipeline now calls safety.run(sql=...)
    def run(self, *, sql):
        trace = StageTrace(stage="safety", duration_ms=1.0)
        if "DROP" in sql.upper():
            return StageResult(ok=False, error=["Unsafe SQL"], trace=trace)
        # echo back sql in data to feed executor
        return StageResult(ok=True, data={"sql": sql, "rationale": "safe"}, trace=trace)


# --- 1) Success path ----------------------------------------------------------
def test_pipeline_success():
    pipeline = Pipeline(
        detector=DummyDetector(ambiguous=False),
        planner=DummyPlanner(),
        generator=DummyGenerator(),
        safety=DummySafety(),
    )

    r = pipeline.run(
        user_query="show all singers",
        schema_preview="CREATE TABLE singer(id int, name text);",
    )

    assert isinstance(r, FinalResult)
    assert r.ok is True
    assert r.sql is not None and r.sql.lower().startswith("select")
    # traces is a list of dicts (StageTrace.__dict__)
    assert any(t.get("stage") == "planner" for t in r.traces)
    assert any(t.get("stage") == "generator" for t in r.traces)
    assert any(t.get("stage") == "safety" for t in r.traces)


# --- 2) Ambiguity case --------------------------------------------------------
def test_pipeline_ambiguity():
    pipeline = Pipeline(
        detector=DummyDetector(ambiguous=True),
        planner=DummyPlanner(),
        generator=DummyGenerator(),
        safety=DummySafety(),
    )

    r = pipeline.run(user_query="show data", schema_preview="CREATE TABLE x(id int);")

    assert isinstance(r, FinalResult)
    assert r.ok is True
    assert r.ambiguous is True
    assert isinstance(r.questions, list) and len(r.questions) > 0


# --- 3) Planner failure -------------------------------------------------------
def test_pipeline_plan_fail():
    pipeline = Pipeline(
        detector=DummyDetector(),
        planner=DummyPlanner(),
        generator=DummyGenerator(),
        safety=DummySafety(),
    )
    r = pipeline.run(
        user_query="fail_plan", schema_preview="CREATE TABLE singer(id int);"
    )
    assert isinstance(r, FinalResult)
    assert r.ok is False
    assert r.details is not None
    assert "Planner failed" in " ".join(r.details)


# --- 4) Generator failure -----------------------------------------------------
def test_pipeline_gen_fail():
    pipeline = Pipeline(
        detector=DummyDetector(),
        planner=DummyPlanner(),
        generator=DummyGenerator(),
        safety=DummySafety(),
    )
    r = pipeline.run(
        user_query="fail_gen", schema_preview="CREATE TABLE singer(id int);"
    )
    assert isinstance(r, FinalResult)
    assert r.ok is False
    assert r.details is not None
    assert "Generator failed" in " ".join(r.details)


# --- 5) Safety failure --------------------------------------------------------
def test_pipeline_safety_fail():
    class UnsafeGen(DummyGenerator):
        def run(self, **kw):
            trace = StageTrace(stage="generator", duration_ms=1.0)
            # Generate a DROP TABLE → unsafe
            return StageResult(
                ok=True, data={"sql": "DROP TABLE x;", "rationale": "oops"}, trace=trace
            )

    pipeline = Pipeline(
        detector=DummyDetector(),
        planner=DummyPlanner(),
        generator=UnsafeGen(),
        safety=DummySafety(),
    )
    r = pipeline.run(
        user_query="drop something", schema_preview="CREATE TABLE x(id int);"
    )
    assert isinstance(r, FinalResult)
    assert r.ok is False
    assert r.details is not None
    assert "unsafe" in " ".join(r.details).lower()
