from __future__ import annotations

import json
import os
import re
from typing import Any, List, Tuple

from adapters.llm.base import LLMProvider
from openai import OpenAI


def _resolve_api_config() -> tuple[str, str, str]:
    """Returns (api_key, base_url, model_id) according to env."""
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
    """OpenAI LLM provider implementation."""

    PROVIDER_ID = "openai"

    def get_last_usage(self) -> dict[str, Any]:
        """Return metadata of the last LLM call (tokens, cost, sql_length, kind)."""
        return dict(self._last_usage)

    def _create_chat_completion(self, **kwargs):
        """OpenAI SDK seam for stable unit testing."""
        return self.client.chat.completions.create(**kwargs)

    def __init__(self) -> None:
        """Initialize OpenAI client with config from environment."""
        api_key, base_url, model = _resolve_api_config()
        os.environ["OPENAI_API_KEY"] = api_key
        os.environ["OPENAI_BASE_URL"] = base_url
        self.client = OpenAI(timeout=120.0)
        self.model = model
        # last call usage/metadata for tracing
        self._last_usage: dict[str, Any] = {}

    def plan(
        self, *, user_query: str, schema_preview: str
    ) -> Tuple[str, int, int, float]:
        """Generate a query plan for the SQL generation.

        Args:
            user_query: The user's natural language question
            schema_preview: Database schema information

        Returns:
            Tuple of (plan_text, prompt_tokens, completion_tokens, cost)
        """
        system_prompt = """You are a SQL query planning expert. Analyze the user's question and database schema to create a clear execution plan.

Your plan should:
1. Identify the tables and columns needed
2. Determine any JOINs required
3. Specify filtering conditions (WHERE)
4. Identify aggregations (GROUP BY, COUNT, etc.)
5. Note sorting requirements (ORDER BY)
6. Check for special cases (DISTINCT, LIMIT, etc.)

Be concise but thorough."""

        user_prompt = f"""Question: {user_query}

Database Schema:
{schema_preview}

Create a step-by-step plan to answer this question with SQL."""

        completion = self._create_chat_completion(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
        )

        msg = completion.choices[0].message.content or ""
        usage = completion.usage

        if usage:
            prompt_tokens = usage.prompt_tokens
            completion_tokens = usage.completion_tokens
            cost = self._estimate_cost(usage)
            self._last_usage = {
                "kind": "plan",
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost_usd": cost,
            }
            return (msg, prompt_tokens, completion_tokens, cost)
        else:
            self._last_usage = {
                "kind": "plan",
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost_usd": 0.0,
            }
            return (msg, 0, 0, 0.0)

    def generate_sql(
        self,
        *,
        user_query: str,
        schema_preview: str,
        plan_text: str,
        clarify_answers: dict[str, Any] | None = None,
    ) -> Tuple[str, str, int, int, float]:
        """Generate SQL with improved prompt for Spider benchmark.

        Args:
            user_query: The user's natural language question
            schema_preview: Database schema information
            plan_text: Query execution plan
            clarify_answers: Optional additional context_engineering

        Returns:
            Tuple of (sql, rationale, prompt_tokens, completion_tokens, cost)
        """
        system_prompt = """You are an expert SQL query generator for SQLite databases.
You must follow these STRICT rules to generate clean, simple SQL:

CRITICAL RULES:
1. Write the SIMPLEST possible SQL that answers the question
2. NEVER use table prefixes unless absolutely necessary for disambiguation
3. NEVER add aliases (AS) unless specifically requested
4. NEVER add LIMIT unless the question asks for a specific number of results
5. NEVER use DISTINCT with COUNT(*) unless explicitly needed
6. Use lowercase for SQL keywords (select, from, where, etc.)
7. Do not add unnecessary parentheses or formatting
8. Match exact column and table names from the schema (case-sensitive)
9. NEVER return empty SQL. If unsure, return the simplest valid SQL that answers the question.
10. Use exact identifiers from `schema_preview` (case-insensitive match).
11. Do NOT invent or pluralize table names. E.g., use `Artist`, not `artists`.

IMPORTANT:
- For counting all rows: Use COUNT(*) not COUNT(column_name)
- For ordering: Only add ORDER BY if the question asks for sorted results
- Keep the SQL as close as possible to the minimal required syntax

You must return ONLY valid JSON with exactly two keys: "sql" and "rationale".
The SQL should be a single line without unnecessary spaces."""

        user_prompt = f"""Based on this information, generate a simple SQL query:

Question: {user_query}

Database Schema:
{schema_preview}

Query Plan:
{plan_text}

Remember: Generate the SIMPLEST possible SQL. Avoid table prefixes, aliases, and unnecessary clauses.

Example of what we want:
Question: "How many singers are there?"
Correct: {{"sql": "select count(*) from singer", "rationale": "Count all rows in singer table"}}
Wrong: {{"sql": "SELECT COUNT(singer.singer_id) AS total_singers FROM singer", "rationale": "..."}}

Now generate the SQL for the given question:"""

        if clarify_answers:
            user_prompt += f"\n\nAdditional context_engineering: {clarify_answers}"

        completion = self._create_chat_completion(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=500,
        )

        text = completion.choices[0].message.content
        content = text.strip() if text else ""
        usage = completion.usage

        # Parse JSON response
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

        # Post-process SQL to ensure simplicity
        sql = self._simplify_sql(sql)

        if not sql:
            raise ValueError("LLM returned empty 'sql'")

        sql_length = len(sql)
        if usage:
            prompt_tokens = usage.prompt_tokens
            completion_tokens = usage.completion_tokens
            cost = self._estimate_cost(usage)
            self._last_usage = {
                "kind": "generate",
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost_usd": cost,
                "sql_length": sql_length,
            }
            return (sql, rationale, prompt_tokens, completion_tokens, cost)
        else:
            self._last_usage = {
                "kind": "generate",
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost_usd": 0.0,
                "sql_length": sql_length,
            }
            return (sql, rationale, 0, 0, 0.0)

    def _simplify_sql(self, sql: str) -> str:
        """Post-process SQL to remove common unnecessary additions."""
        if not sql:
            return sql

        # Remove trailing semicolon
        sql = sql.rstrip(";")

        # Remove unnecessary table prefixes in simple queries
        # e.g., "singer.name" -> "name" when there's only one table
        if sql.lower().count(" from ") == 1 and " join " not in sql.lower():
            match = re.search(r"\bfrom\s+(\w+)", sql, re.IGNORECASE)
            if match:
                table = match.group(1)
                sql = re.sub(rf"\b{table}\.(\w+)\b", r"\1", sql)

        # Remove unnecessary DISTINCT in COUNT(*)
        sql = re.sub(
            r"count\s*\(\s*distinct\s+\*\s*\)",
            "count(*)",
            sql,
            flags=re.IGNORECASE,
        )

        # Remove big default LIMITs that weren't requested
        sql = re.sub(
            r"\s+limit\s+(100|1000|10000)\b",
            "",
            sql,
            flags=re.IGNORECASE,
        )

        return sql

    def repair(
        self,
        *,
        sql: str,
        error_msg: str,
        schema_preview: str,
    ) -> Tuple[str, int, int, float]:
        """Repair SQL with focus on simplicity.

        Args:
            sql: Broken SQL query
            error_msg: Error message from execution
            schema_preview: Database schema information

        Returns:
            Tuple of (fixed_sql, prompt_tokens, completion_tokens, cost)
        """
        system_prompt = """You are a SQL repair expert. Fix the given SQL query to resolve the error.

IMPORTANT RULES:
1. Keep the fix as minimal as possible
2. Don't add complexity - keep it simple
3. Preserve the original intent of the query
4. Follow SQLite syntax rules
5. Don't add aliases or table prefixes unless necessary
6. Use exact identifiers from `schema_preview` (case-insensitive match).
7. Do NOT invent or pluralize table names. E.g., use `Artist`, not `artists`.

Return ONLY the corrected SQL query, nothing else."""

        user_prompt = f"""Fix this SQL query:

Original SQL: {sql}

Error: {error_msg}

Database Schema:
{schema_preview}

Return the corrected SQL (keep it simple):"""

        completion = self._create_chat_completion(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
        )

        text = completion.choices[0].message.content
        fixed_sql = text.strip() if text else ""

        # Clean up accidental code fences
        if fixed_sql.startswith("```sql"):
            fixed_sql = fixed_sql[6:]
        if fixed_sql.startswith("```"):
            fixed_sql = fixed_sql[3:]
        if fixed_sql.endswith("```"):
            fixed_sql = fixed_sql[:-3]

        fixed_sql = fixed_sql.strip()
        fixed_sql = self._simplify_sql(fixed_sql)

        usage = completion.usage

        if usage:
            prompt_tokens = usage.prompt_tokens
            completion_tokens = usage.completion_tokens
            cost = self._estimate_cost(usage)
            self._last_usage = {
                "kind": "repair",
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost_usd": cost,
                "sql_length": len(fixed_sql),
            }
            return (fixed_sql, prompt_tokens, completion_tokens, cost)
        else:
            self._last_usage = {
                "kind": "repair",
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost_usd": 0.0,
                "sql_length": len(fixed_sql),
            }
            return (fixed_sql, 0, 0, 0.0)

    def _estimate_cost(self, usage: Any) -> float:
        """Estimate cost based on token usage.

        Args:
            usage: OpenAI usage object with token counts

        Returns:
            Estimated cost in USD
        """
        if not usage:
            return 0.0

        # Pricing per 1K tokens (adjust based on model)
        pricing = {
            "gpt-4": {"input": 0.03, "output": 0.06},
            "gpt-4-turbo": {"input": 0.01, "output": 0.03},
            "gpt-4o": {"input": 0.005, "output": 0.015},
            "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
            "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
        }

        model_pricing = pricing.get(self.model, pricing["gpt-4o-mini"])

        input_cost = (usage.prompt_tokens / 1000) * model_pricing["input"]
        output_cost = (usage.completion_tokens / 1000) * model_pricing["output"]

        return input_cost + output_cost

    def clarify(
        self,
        *,
        user_query: str,
        schema_preview: str,
        questions: List[str],
    ) -> Tuple[str, int, int, float]:
        """Clarify ambiguities in the user query.

        Args:
            user_query: The user's natural language question
            schema_preview: Database schema information
            questions: List of clarification questions

        Returns:
            Tuple of (answers, prompt_tokens, completion_tokens, cost)
        """
        system_prompt = """You are a helpful assistant that clarifies SQL query requirements.
Answer the questions clearly and concisely based on the user's query and database schema."""

        user_prompt = f"""User Query: {user_query}

Database Schema:
{schema_preview}

Please answer these clarification questions:
{chr(10).join(f"{i + 1}. {q}" for i, q in enumerate(questions))}"""

        completion = self._create_chat_completion(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
        )

        answers = completion.choices[0].message.content or ""
        usage = completion.usage

        if usage:
            prompt_tokens = usage.prompt_tokens
            completion_tokens = usage.completion_tokens
            cost = self._estimate_cost(usage)
            return (answers, prompt_tokens, completion_tokens, cost)
        else:
            return (answers, 0, 0, 0.0)
