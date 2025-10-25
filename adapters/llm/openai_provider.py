from __future__ import annotations
import os
from typing import Tuple, Dict, Any, List
import json
from adapters.llm.base import LLMProvider
from openai import OpenAI

# NOTE: Read keys/base URL from env. Do NOT pass base_url in constructors.
#  - OPENAI_API_KEY   (required)
#  - OPENAI_BASE_URL  (optional; defaults to OpenAI public)
#  - OPENAI_MODEL_ID  (e.g., "gpt-4o-mini")


class OpenAIProvider(LLMProvider):
    provider_id = "openai"

    def __init__(self) -> None:
        self.client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        )
        self.model = os.getenv("OPENAI_MODEL_ID", "gpt-4o-mini")

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
        usage = completion.usage  # ← لازم داریم
        t_in = usage.prompt_tokens if usage else None
        t_out = usage.completion_tokens if usage else None
        cost = self._estimate_cost(usage) if usage else None

        # Robust JSON parse (with fallback to substring)
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

        # IMPORTANT: return the expected 5-tuple
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
        # Rough estimation example — can be refined with official token pricing
        total = usage.prompt_tokens + usage.completion_tokens
        return total * 0.000001
