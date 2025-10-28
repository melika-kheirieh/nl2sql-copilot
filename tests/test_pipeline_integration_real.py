import sqlite3
from nl2sql.pipeline import Pipeline
from nl2sql.types import StageResult, StageTrace


# --- Realistic dummy stages ----------------------------------
class DetectorOK:
    """Always returns no ambiguities."""

    def detect(self, *a, **k):
        return []


class PlannerLLM:
    def run(self, *, user_query, schema_preview):
        plan = f"Understand user query '{user_query}' and map to table."
        return StageResult(
            ok=True,
            data={"plan": plan},
            trace=StageTrace(stage="planner", duration_ms=0),
        )


class GeneratorSimple:
    def run(self, *, user_query, schema_preview, plan_text, clarify_answers):
        sql = "SELECT city, COUNT(*) AS cnt FROM users GROUP BY city"
        return StageResult(
            ok=True,
            data={"sql": sql, "rationale": plan_text},
            trace=StageTrace(stage="generator", duration_ms=0),
        )


class SafetyReadOnly:
    def run(self, *, sql):
        if sql.strip().lower().startswith("select"):
            return StageResult(
                ok=True,
                data={"sql": sql},
                trace=StageTrace(stage="safety", duration_ms=0),
            )
        return StageResult(
            ok=False,
            error=["Unsafe query"],
            trace=StageTrace(stage="safety", duration_ms=0, notes={"reason": "unsafe"}),
        )


class ExecutorSQLite:
    """Executes the SQL query on a temporary in-memory SQLite database."""

    def __init__(self):
        # create in-memory DB and seed some rows
        self.conn = sqlite3.connect(":memory:")
        self._seed()

    def _seed(self):
        cur = self.conn.cursor()
        cur.execute("CREATE TABLE users (id INTEGER, city TEXT)")
        cur.executemany(
            "INSERT INTO users VALUES (?, ?)",
            [
                (1, "Berlin"),
                (2, "Berlin"),
                (3, "Munich"),
            ],
        )
        self.conn.commit()

    def run(self, *, sql):
        cur = self.conn.cursor()
        cur.execute(sql)
        rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
        return StageResult(
            ok=True,
            data={"rows": rows},
            trace=StageTrace(stage="executor", duration_ms=0),
        )


class VerifierCheckCount:
    def run(self, *, sql, exec_result):
        rows = exec_result.get("rows", [])
        ok = bool(rows and "city" in rows[0] and "cnt" in rows[0])
        return StageResult(
            ok=ok,
            data={"verified": ok},
            trace=StageTrace(
                stage="verifier", duration_ms=0, notes={"rows_len": len(rows)}
            ),
        )


class RepairNoOp:
    """Dummy repair stage (not triggered in this scenario)."""

    def run(self, *a, **k):
        return StageResult(ok=False, error=["no repair needed"])


# --- Integration test ----------------------------------------
def test_pipeline_end_to_end_real_sqlite():
    """Full NL2SQL pipeline test on real SQLite DB with no mocks."""
    pipe = Pipeline(
        detector=DetectorOK(),
        planner=PlannerLLM(),
        generator=GeneratorSimple(),
        safety=SafetyReadOnly(),
        executor=ExecutorSQLite(),
        verifier=VerifierCheckCount(),
        repair=RepairNoOp(),
    )

    result = pipe.run(
        user_query="count users per city", schema_preview="users(id, city)"
    )

    # --- Assertions ---
    assert result.ok
    assert result.verified
    assert not result.error
    assert "SELECT" in result.sql

    # Ensure pipeline produced valid SQL and traces
    assert isinstance(result.traces, list)
    assert result.traces  # not empty

    # Logical validation
    assert "city" in result.sql.lower()
