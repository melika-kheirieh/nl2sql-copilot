import json
import pytest

from adapters.llm.openai_provider import OpenAIProvider


class FakeCompletion:
    """Minimal fake object that matches what OpenAIProvider reads from SDK response."""

    def __init__(
        self, content: str, prompt_tokens: int = 5, completion_tokens: int = 7
    ):
        self.choices = [
            type("Choice", (), {"message": type("Msg", (), {"content": content})})
        ]
        self.usage = type(
            "Usage",
            (),
            {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        )


def _patch_completion(
    provider: OpenAIProvider,
    monkeypatch,
    content: str,
    *,
    t_in: int = 5,
    t_out: int = 7,
):
    """Patch provider seam to return a fake completion with deterministic content."""
    fake = FakeCompletion(content, prompt_tokens=t_in, completion_tokens=t_out)

    def fake_create_chat_completion(**kwargs):
        return fake

    monkeypatch.setattr(
        provider, "_create_chat_completion", fake_create_chat_completion
    )


def test_generate_sql_valid_json(monkeypatch):
    provider = OpenAIProvider()

    content = json.dumps(
        {"sql": "select * from singer;", "rationale": "List all singers."}
    )
    _patch_completion(provider, monkeypatch, content, t_in=5, t_out=7)

    sql, rationale, t_in, t_out, cost = provider.generate_sql(
        user_query="show all singers",
        schema_preview="CREATE TABLE singer(id int, name text);",
        plan_text="-- plan --",
        clarify_answers={},
    )

    assert sql == "select * from singer"
    assert "list" in rationale.lower()
    assert t_in == 5
    assert t_out == 7
    assert isinstance(cost, float)

    usage = provider.get_last_usage()
    assert usage.get("kind") == "generate"
    assert usage.get("prompt_tokens") == 5
    assert usage.get("completion_tokens") == 7
    assert isinstance(usage.get("cost_usd"), float)
    assert isinstance(usage.get("sql_length"), int) and usage["sql_length"] > 0


def test_generate_sql_recovers_from_wrapped_json(monkeypatch):
    provider = OpenAIProvider()

    content = (
        "Here is the result:\n"
        '{ "sql": "select * from users;", "rationale": "list users" }\n'
        "Thanks!"
    )
    _patch_completion(provider, monkeypatch, content)

    sql, rationale, *_ = provider.generate_sql(
        user_query="show all users",
        schema_preview="CREATE TABLE users(id int, name text);",
        plan_text="-- plan --",
    )

    assert sql == "select * from users"
    assert rationale == "list users"


def test_generate_sql_invalid_json_raises_value_error(monkeypatch):
    provider = OpenAIProvider()

    content = "This is nonsense output without braces"
    _patch_completion(provider, monkeypatch, content)

    with pytest.raises(ValueError):
        provider.generate_sql(
            user_query="show X",
            schema_preview="CREATE TABLE t(id int);",
            plan_text="-- plan --",
        )


def test_generate_sql_empty_sql_raises_value_error(monkeypatch):
    provider = OpenAIProvider()

    content = json.dumps({"sql": "   ", "rationale": "oops"})
    _patch_completion(provider, monkeypatch, content)

    with pytest.raises(ValueError):
        provider.generate_sql(
            user_query="show X",
            schema_preview="CREATE TABLE t(id int);",
            plan_text="-- plan --",
        )
