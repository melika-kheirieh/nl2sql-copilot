import re
from typing import Any, Dict


from nl2sql.pipeline import Pipeline
from nl2sql.context_engineering.engineer import ContextEngineer
from nl2sql.context_engineering.types import ContextBudget
from nl2sql.stubs import NoOpExecutor, NoOpVerifier, NoOpRepair


# ---------- helpers ----------


def make_schema_preview(n_tables: int) -> str:
    lines = []
    for i in range(n_tables):
        lines.append(f"table_{i}(col1, col2, col3)")
    return "\n".join(lines)


def count_tables(schema_preview: str) -> int:
    return len(
        re.findall(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*\(", schema_preview, flags=re.M)
    )


# ---------- fakes ----------


class FakePlanner:
    def __init__(self):
        self.last_schema_preview = None

    def run(self, *, user_query: str, schema_preview: str, **kwargs):
        self.last_schema_preview = schema_preview
        return {"plan": "fake plan"}


class FakeGenerator:
    def __init__(self):
        self.last_schema_preview = None

    def run(
        self,
        *,
        user_query: str,
        schema_preview: str,
        plan_text: str,
        clarify_answers: Dict[str, Any],
        **kwargs,
    ):
        self.last_schema_preview = schema_preview
        return {
            "sql": "SELECT 1;",
            "rationale": "fake rationale",
        }


class FakeDetector:
    def detect(self, user_query: str, schema_preview: str):
        return []


class FakeSafety:
    def run(self, *, sql: str):
        return {"sql": sql}


# ---------- test ----------


def test_schema_budget_is_applied_before_llm():
    # given: a large schema (50 tables)
    schema_preview = make_schema_preview(50)

    # and: a strict budget (10 tables)
    budget = ContextBudget(
        max_tables=10,
        max_columns_per_table=10,
        max_total_columns=100,
    )
    context_engineer = ContextEngineer(budget=budget)

    planner = FakePlanner()
    generator = FakeGenerator()

    pipeline = Pipeline(
        detector=FakeDetector(),
        planner=planner,
        generator=generator,
        safety=FakeSafety(),
        executor=NoOpExecutor(),
        verifier=NoOpVerifier(),
        repair=NoOpRepair(),
        context_engineer=context_engineer,
    )

    # when
    result = pipeline.run(
        user_query="just test",
        schema_preview=schema_preview,
    )

    # then: pipeline worked
    assert result.ok is True

    # and: planner saw only the budgeted schema
    assert planner.last_schema_preview is not None
    assert count_tables(planner.last_schema_preview) == 10

    # and: generator saw the same budgeted schema
    assert generator.last_schema_preview is not None
    assert count_tables(generator.last_schema_preview) == 10
