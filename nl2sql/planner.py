from __future__ import annotations

from typing import Dict, Any


class Planner:
    """Planner wrapper around the LLM provider.

    The factory constructs it with `Planner(llm=llm)`, so we accept `llm` here.
    """

    def __init__(self, *, llm, model_id: str | None = None) -> None:
        self.llm = llm
        self.model_id = model_id or getattr(llm, "model", "unknown")

    def run(self, *, user_query: str, schema_preview: str) -> Dict[str, Any]:
        plan_text, pin, pout, cost = self.llm.plan(
            user_query=user_query, schema_preview=schema_preview
        )
        return {
            "plan": plan_text,
            "usage": {
                "prompt_tokens": pin,
                "completion_tokens": pout,
                "cost_usd": cost,
            },
        }
