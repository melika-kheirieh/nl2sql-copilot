import json
import pytest
from adapters.llm.openai_provider import OpenAIProvider


# Helper class to fake the completion object returned by OpenAI SDK
class FakeCompletion:
    def __init__(self, content: str, prompt_tokens=5, completion_tokens=7):
        self.choices = [type("Choice", (), {"message": type("Msg", (), {"content": content})})]
        self.usage = type("Usage", (), {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens
        })


# --- Case 1: clean valid JSON --------------------------------------------------
def test_generate_sql_valid_json(monkeypatch):
    provider = OpenAIProvider()

    fake_content = json.dumps({
        "sql": "SELECT * FROM singer;",
        "rationale": "List all singers."
    })
    fake_completion = FakeCompletion(fake_content)

    # Monkeypatch client.chat.completions.create
    def fake_create(*args, **kwargs):
        return fake_completion

    monkeypatch.setattr(provider.client.chat.completions, "create", fake_create)

    sql, rationale, t_in, t_out, cost = provider.generate_sql(
        user_query="show all singers",
        schema_preview="CREATE TABLE singer(id int, name text);",
        plan_text="-- plan --",
        clarify_answers={}
    )

    assert sql.strip().lower().startswith("select")
    assert "singer" in sql.lower()
    assert "list" in rationale.lower()
    assert t_in == 5 and t_out == 7
    assert isinstance(cost, float)


# --- Case 2: malformed JSON with extra text (should still recover) ------------
def test_generate_sql_recover_from_partial_json(monkeypatch):
    provider = OpenAIProvider()

    # invalid JSON with text around it
    fake_content = "Here is the result:\n{ \"sql\": \"SELECT * FROM users;\", \"rationale\": \"list users\" }\nThanks!"
    fake_completion = FakeCompletion(fake_content)

    def fake_create(*args, **kwargs):
        return fake_completion

    monkeypatch.setattr(provider.client.chat.completions, "create", fake_create)

    sql, rationale, *_ = provider.generate_sql(
        user_query="show all users",
        schema_preview="CREATE TABLE users(id int, name text);",
        plan_text="-- plan --"
    )

    assert sql.lower().startswith("select")
    assert "user" in sql.lower()
    assert "list" in rationale.lower()


# --- Case 3: completely invalid JSON (should raise ValueError) ----------------
def test_generate_sql_invalid_json(monkeypatch):
    provider = OpenAIProvider()

    fake_content = "This is nonsense output without braces"
    fake_completion = FakeCompletion(fake_content)

    def fake_create(*args, **kwargs):
        return fake_completion

    monkeypatch.setattr(provider.client.chat.completions, "create", fake_create)

    with pytest.raises(ValueError):
        provider.generate_sql(
            user_query="show X",
            schema_preview="CREATE TABLE t(id int);",
            plan_text="-- plan --"
        )
