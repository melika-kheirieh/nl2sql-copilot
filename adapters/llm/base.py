from __future__ import annotations

from typing import Any, Dict, List, Protocol, Tuple


class LLMProvider(Protocol):
    PROVIDER_ID: str

    def plan(
        self,
        *,
        user_query: str,
        schema_preview: str,
        constraints: List[str] | None = None,
    ) -> Tuple[str, List[str], int, int, float]:
        """Return (plan_text, used_tables, token_in, token_out, cost_usd)."""

    def generate_sql(
        self,
        *,
        user_query: str,
        schema_preview: str,
        plan_text: str,
        constraints: List[str] | None = None,
        clarify_answers: Dict[str, Any] | None = None,
    ) -> Tuple[str, str, int, int, float]:
        """Return (sql, rationale, token_in, token_out, cost_usd)."""

    def repair(
        self, *, sql: str, error_msg: str, schema_preview: str
    ) -> Tuple[str, int, int, float]:
        """Return (patched_sql, token_in, token_out, cost_usd)."""
