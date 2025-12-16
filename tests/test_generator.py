import pytest

from nl2sql.generator import Generator
from nl2sql.types import StageResult
from nl2sql.errors.codes import ErrorCode


# --- Dummy LLMs (respect the 5-tuple contract) --------------------------------


class LLM_OK:
    def generate_sql(self, **kwargs):
        # Contract: (sql, rationale, t_in, t_out, cost)
        return "SELECT * FROM singer;", "list all", 10, 5, 0.00001


class LLM_EMPTY_SQL:
    def generate_sql(self, **kwargs):
        # Empty SQL -> should be classified as LLM_BAD_OUTPUT
        return "", "reason", 10, 5, 0.0


class LLM_NON_SELECT:
    def generate_sql(self, **kwargs):
        # Non-SELECT SQL -> should be classified as SAFETY_NON_SELECT (or LLM_BAD_OUTPUT if you prefer)
        return "UPDATE users SET name='x' WHERE id=1;", "bad", 8, 3, 0.0


class LLM_CONTRACT_NONE:
    def generate_sql(self, **kwargs):
        # Contract violation: None instead of 5-tuple
        return None


class LLM_CONTRACT_SHORT:
    def generate_sql(self, **kwargs):
        # Contract violation: too few items
        return ("SELECT * FROM singer;", "list all")  # only 2


# --- Parametrized negative cases ----------------------------------------------


@pytest.mark.parametrize(
    "llm, expected_code",
    [
        (LLM_EMPTY_SQL(), ErrorCode.LLM_BAD_OUTPUT),
        (LLM_NON_SELECT(), ErrorCode.SAFETY_NON_SELECT),
        (LLM_CONTRACT_NONE(), ErrorCode.LLM_BAD_OUTPUT),
        (LLM_CONTRACT_SHORT(), ErrorCode.LLM_BAD_OUTPUT),
    ],
)
def test_generator_errors_are_code_driven_and_do_not_create_trace(llm, expected_code):
    gen = Generator(llm=llm)
    r = gen.run(
        user_query="show all singers",
        schema_preview="CREATE TABLE singer(id int, name text);",
        plan_text="-- plan --",
        clarify_answers={},
    )

    assert isinstance(r, StageResult)
    assert r.ok is False
    assert r.error_code == expected_code
    assert r.trace is None


# --- Positive case (success) ---------------------------------------------------


def test_generator_success_has_valid_trace_and_data():
    """On success, Generator should return SQL/rationale and attach a coherent trace."""
    gen = Generator(llm=LLM_OK())
    r = gen.run(
        user_query="show all singers",
        schema_preview="CREATE TABLE singer(id int, name text);",
        plan_text="-- plan --",
        clarify_answers={},
    )

    assert isinstance(r, StageResult)
    assert r.ok is True
    assert r.data and r.data["sql"].lower().startswith("select")
    assert "rationale" in r.data

    # Trace should exist and be coherent
    assert r.trace is not None
    assert r.trace.stage == "generator"
    assert isinstance(r.trace.duration_ms, float)
    assert r.trace.token_in == 10
    assert r.trace.token_out == 5

    # cost can be float or None depending on provider; if present must be numeric
    if r.trace.cost_usd is not None:
        assert isinstance(r.trace.cost_usd, float)

    # Optional notes check – rationale_len should match length of rationale
    notes = r.trace.notes or {}
    if "rationale_len" in notes:
        assert notes["rationale_len"] == len(r.data.get("rationale", ""))
