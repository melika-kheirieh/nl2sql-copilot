from nl2sql.pipeline import Pipeline
from nl2sql.types import StageResult
from nl2sql.errors.codes import ErrorCode


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
    """First call fails, second call passes (after repair)."""

    def __init__(self):
        self.calls = 0
        self.last_sql_seen = None

    def run(self, *, sql, exec_result):
        self.calls += 1
        self.last_sql_seen = sql
        if self.calls == 1:
            return StageResult(
                ok=False,
                error=["first verify fail"],
                error_code=ErrorCode.PLAN_SYNTAX_ERROR,
                retryable=False,
            )
        return StageResult(ok=True, data={"verified": True})


class RepairOK:
    def __init__(self):
        self.calls = 0
        self.last_error_msg = None
        self.last_sql_in = None

    def run(self, *, sql, error_msg, schema_preview):
        self.calls += 1
        self.last_sql_in = sql
        self.last_error_msg = error_msg
        return StageResult(ok=True, data={"sql": "SELECT * FROM t LIMIT 1"})


def test_pipeline_repair_success_path():
    verifier = VerifierThenOK()
    repair = RepairOK()

    p = Pipeline(
        detector=DetectorOK(),
        planner=PlannerOK(),
        generator=GeneratorOK(),
        safety=SafetyOK(),
        executor=ExecOK(),
        verifier=verifier,
        repair=repair,
    )

    out = p.run(user_query="?", schema_preview="")

    assert out.ok is True
    assert out.error is False
    assert out.verified is True

    # Ensure the repair path actually happened
    assert verifier.calls == 2
    assert repair.calls == 1

    # Ensure repair changed the SQL
    assert repair.last_sql_in == "SELECT * FROM t"
    assert verifier.last_sql_seen == "SELECT * FROM t LIMIT 1"

    # Ensure the failing verifier message was passed into repair
    assert repair.last_error_msg is not None
    assert "first verify fail" in repair.last_error_msg
