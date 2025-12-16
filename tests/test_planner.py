from nl2sql.planner import Planner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeLLM:
    """
    Minimal fake LLM that mimics Planner's expected interface.
    """

    def __init__(self):
        self.calls = 0

    def plan(self, *, user_query, schema_preview):
        self.calls += 1
        return (
            f"PLAN({user_query})",
            10,  # prompt_tokens
            20,  # completion_tokens
            0.01,  # cost_usd
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_planner_returns_basic_plan_structure():
    """
    Planner.run should return a dict with the public contract keys.
    Internal helpers (e.g. schema trimming) must not leak into the API.
    """
    llm = FakeLLM()
    planner = Planner(llm=llm)

    result = planner.run(
        user_query="list users",
        schema_preview="CREATE TABLE users(id INT, name TEXT);",
    )

    assert isinstance(result, dict)
    assert "plan" in result
    assert "usage" in result
    assert result["plan"] == "PLAN(list users)"


def test_planner_handles_large_schema_without_error():
    """
    Planner should be able to handle large / noisy schemas
    without failing or exposing internal details.
    """
    llm = FakeLLM()
    planner = Planner(llm=llm)

    schema = """
    CREATE TABLE users(id INT, name TEXT);
    CREATE TABLE orders(id INT, user_id INT);
    CREATE TABLE logs(id INT, payload TEXT);
    CREATE TABLE audit(id INT, meta TEXT);
    """

    result = planner.run(
        user_query="show all users",
        schema_preview=schema,
    )

    assert result["plan"] == "PLAN(show all users)"
    assert llm.calls == 1


def test_planner_uses_cache_for_identical_inputs():
    """
    Planner should call the LLM only once for identical
    (user_query, schema_preview) inputs.
    """
    llm = FakeLLM()
    planner = Planner(llm=llm)

    planner.run(user_query="q", schema_preview="schema")
    planner.run(user_query="q", schema_preview="schema")

    assert llm.calls == 1


def test_planner_cache_is_keyed_by_query_and_schema():
    """
    Changing either query or schema should bypass cache.
    """
    llm = FakeLLM()
    planner = Planner(llm=llm)

    planner.run(user_query="q1", schema_preview="schema")
    planner.run(user_query="q2", schema_preview="schema")
    planner.run(user_query="q1", schema_preview="schema v2")

    assert llm.calls == 3


def test_planner_falls_back_gracefully_on_trimming_error(monkeypatch):
    """
    If schema trimming fails internally,
    Planner must still return a valid plan result.
    """
    llm = FakeLLM()

    # Force internal trimming helper to fail
    monkeypatch.setattr(
        "nl2sql.planner._table_blocks",
        lambda *_: (_ for _ in ()).throw(Exception("boom")),
    )

    planner = Planner(llm=llm)

    result = planner.run(
        user_query="anything",
        schema_preview="CREATE TABLE users(id INT);",
    )

    assert result["plan"] == "PLAN(anything)"
    assert "usage" in result
