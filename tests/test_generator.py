import pytest
from nl2sql.generator import Generator
from nl2sql.types import StageResult


# --- Dummy LLMs (respect the 5-tuple contract) --------------------------------


class LLM_OK:
    def generate_sql(self, **kwargs):
        # contract: (sql, rationale, t_in, t_out, cost)
        return "SELECT * FROM singer;", "list all", 10, 5, 0.00001


class LLM_EMPTY_SQL:
    def generate_sql(self, **kwargs):
        # empty SQL → should be error
        return "", "reason", 10, 5, 0.0


class LLM_NON_SELECT:
    def generate_sql(self, **kwargs):
        # non-SELECT SQL → should be error
        return "UPDATE users SET name='x' WHERE id=1;", "bad", 8, 3, 0.0


class LLM_CONTRACT_NONE:
    def generate_sql(self, **kwargs):
        # contract violation: None instead of 5-tuple
        return None


class LLM_CONTRACT_SHORT:
    def generate_sql(self, **kwargs):
        # contract violation: too few items
        return ("SELECT * FROM singer;", "list all")  # only 2


# --- Parametrized negative cases ----------------------------------------------


@pytest.mark.parametrize(
    "llm, err_keyword",
    [
        (LLM_EMPTY_SQL(), "empty"),  # empty or non-string sql
        (LLM_NON_SELECT(), "non-select"),  # generated non-SELECT
        (LLM_CONTRACT_NONE(), "contract violation"),
        (LLM_CONTRACT_SHORT(), "contract violation"),
    ],
)
def test_generator_errors_do_not_create_trace(llm, err_keyword):
    gen = Generator(llm=llm)
    r = gen.run(
        user_query="show all singers",
        schema_preview="CREATE TABLE singer(id int, name text);",
        plan_text="-- plan --",
        clarify_answers={},
    )
    assert isinstance(r, StageResult)
    assert r.ok is False
    # Error message is flexible; just check a keyword
    joined = " ".join(r.error or []).lower()
    assert err_keyword in joined
    # On errors, Generator should not attach a trace (we measure only successful stage)
    assert r.trace is None


# --- Positive case (success) ---------------------------------------------------


def test_generator_success_has_valid_trace_and_data():
    gen = Generator(llm=LLM_OK())
    r = gen.run(
        user_query="show all singers",
        schema_preview="CREATE TABLE singer(id int, name text);",
        plan_text="-- plan --",
        clarify_answers={},
    )

    # Basic success checks
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
