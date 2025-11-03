from __future__ import annotations
import os
import json
from adapters.llm.base import LLMProvider
from openai import OpenAI

# NOTE:
# - Prefer proxy if PROXY_API_KEY and PROXY_BASE_URL are set.
# - Otherwise, fallback to OPENAI_API_KEY (+ OPENAI_BASE_URL defaulting to https://api.openai.com/v1).
# - Do NOT pass base_url/api_key in the constructor; rely on env vars.


def _resolve_api_config() -> tuple[str, str, str]:
    """
    Returns (api_key, base_url, model_id) according to env.
    Resolution order:
      1) Proxy: PROXY_API_KEY + PROXY_BASE_URL [+ PROXY_MODEL_ID]
      2) Direct: OPENAI_API_KEY [+ OPENAI_BASE_URL] [+ OPENAI_MODEL_ID]
    Additionally, LLM_MODEL_ID (if set) overrides model choice.
    """
    # Optional global override for model id
    override_model = os.getenv("LLM_MODEL_ID")

    proxy_key = os.getenv("PROXY_API_KEY")
    proxy_url = os.getenv("PROXY_BASE_URL")
    if proxy_key and proxy_url:
        model = (
            override_model
            or os.getenv("PROXY_MODEL_ID")
            or os.getenv("OPENAI_MODEL_ID")
            or "gpt-4o-mini"
        )
        return proxy_key, proxy_url, model

    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        raise RuntimeError(
            "No API credentials found. Set either PROXY_API_KEY/PROXY_BASE_URL or OPENAI_API_KEY."
        )
    openai_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = override_model or os.getenv("OPENAI_MODEL_ID") or "gpt-4o-mini"
    return openai_key, openai_url, model


class OpenAIProvider(LLMProvider):
    provider_id = "openai"

    def __init__(self) -> None:
        # Resolve and export to env so we don't pass into constructor.
        api_key, base_url, model = _resolve_api_config()
        os.environ["OPENAI_API_KEY"] = api_key
        os.environ["OPENAI_BASE_URL"] = base_url
        # Create client using env only
        self.client = OpenAI()
        self.model = model

    def plan(self, *, user_query, schema_preview):
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "You create SQL query plans."},
                {
                    "role": "user",
                    "content": f"Query: {user_query}\nSchema:\n{schema_preview}",
                },
            ],
            temperature=0,
        )
        msg = completion.choices[0].message.content
        usage = completion.usage
        return (
            msg,
            usage.prompt_tokens,
            usage.completion_tokens,
            self._estimate_cost(usage),
        )

    def generate_sql(
        self, *, user_query, schema_preview, plan_text, clarify_answers=None
    ):
        prompt = f"""
        You are a precise SQL generator.
        Return ONLY valid JSON with two keys: "sql" and "rationale".
        Do not include any markdown, backticks, or extra text.

        Example:
        {{
          "sql": "SELECT * FROM singer;",
          "rationale": "The user requested to list all singers."
        }}

        Now generate JSON for this input:

        User query: {user_query}
        Schema preview:
        {schema_preview}
        Plan: {plan_text}
        Clarifications: {clarify_answers}
        """
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "You convert natural language to SQL."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )
        content = completion.choices[0].message.content.strip()
        usage = completion.usage
        t_in = usage.prompt_tokens if usage else None
        t_out = usage.completion_tokens if usage else None
        cost = self._estimate_cost(usage) if usage else None

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1:
                try:
                    parsed = json.loads(content[start : end + 1])
                except Exception:
                    raise ValueError(f"Invalid LLM JSON output: {content[:200]}")
            else:
                raise ValueError(f"Invalid LLM JSON output: {content[:200]}")

        sql = (parsed.get("sql") or "").strip()
        rationale = parsed.get("rationale") or ""
        if not sql:
            raise ValueError("LLM returned empty 'sql'")

        return sql, rationale, t_in, t_out, cost

    def repair(self, *, sql, error_msg, schema_preview):
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "You fix SQL queries keeping them SELECT-only.",
                },
                {
                    "role": "user",
                    "content": f"SQL:\n{sql}\nError:\n{error_msg}\nSchema:\n{schema_preview}",
                },
            ],
            temperature=0,
        )
        msg = completion.choices[0].message.content
        usage = completion.usage
        return (
            msg,
            usage.prompt_tokens,
            usage.completion_tokens,
            self._estimate_cost(usage),
        )

    def _estimate_cost(self, usage):
        total = usage.prompt_tokens + usage.completion_tokens
        return total * 0.000001
