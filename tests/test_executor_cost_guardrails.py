from __future__ import annotations


from nl2sql.executor import Executor
from nl2sql.errors.codes import ErrorCode


class FakeDB:
    dialect = "sqlite"

    def __init__(self, plan_lines: list[str]) -> None:
        self._plan_lines = plan_lines
        self.executed = False

    def explain_query_plan(self, sql: str) -> list[str]:
        return self._plan_lines

    def execute(self, sql: str):
        self.executed = True
        return ([], [])


def test_executor_blocks_full_scan_without_limit():
    db = FakeDB(plan_lines=["SCAN singer"])
    ex = Executor(db=db)

    res = ex.run(sql="SELECT * FROM singer")

    assert res.ok is False
    assert res.error_code == ErrorCode.EXECUTOR_COST_GUARDRAIL_BLOCKED
    assert db.executed is False
    assert res.trace is not None
    assert res.trace.notes is not None
    assert res.trace.notes.get("blocked_reason") == "full_scan_without_limit"


def test_executor_allows_scan_with_limit():
    db = FakeDB(plan_lines=["SCAN singer"])
    ex = Executor(db=db)

    res = ex.run(sql="SELECT * FROM singer LIMIT 10")

    assert res.ok is True
    assert db.executed is True
