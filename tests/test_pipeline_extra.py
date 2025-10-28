from nl2sql.pipeline import Pipeline
from nl2sql.types import StageResult


class DetectorOK:
    def detect(self, *a, **k):
        return []


class PlannerOK:
    def run(self, *a, **k):
        return StageResult(ok=True, data={"plan": "p"})


class GeneratorOK:
    def run(self, *a, **k):
        return StageResult(ok=True, data={"sql": "SELECT * FROM t", "rationale": "ok"})


class SafetyOK:
    def run(self, *a, **k):
        sql = k.get("sql", "SELECT * FROM t")
        return StageResult(ok=True, data={"sql": sql})


class ExecOK:
    def run(self, *a, **k):
        return StageResult(ok=True, data={"rows": [{"x": 1}]})


class VerifierThenOK:
    """اولین بار fail، بعد از repair pass می‌کند."""

    def __init__(self):
        self.calls = 0

    def run(self, *, sql, exec_result):
        self.calls += 1
        if self.calls == 1:
            return StageResult(ok=False, error=["first verify fail"])
        return StageResult(ok=True, data={"verified": True})


class RepairOK:
    def run(self, *, sql, error_msg, schema_preview):
        return StageResult(ok=True, data={"sql": "SELECT * FROM t LIMIT 1"})


def test_pipeline_repair_success_path():
    p = Pipeline(
        detector=DetectorOK(),
        planner=PlannerOK(),
        generator=GeneratorOK(),
        safety=SafetyOK(),
        executor=ExecOK(),
        verifier=VerifierThenOK(),
        repair=RepairOK(),
    )
    out = p.run(user_query="?", schema_preview="")
    assert out.ok
    assert out.verified
    assert not out.error
