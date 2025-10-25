from __future__ import annotations
import time
from typing import Optional, Dict, Any
from nl2sql.types import StageResult, StageTrace
from adapters.llm.base import LLMProvider


class Generator:
    name = "generator"

    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    def run(
        self,
        *,
        user_query: str,
        schema_preview: str,
        plan_text: str,
        clarify_answers: Optional[Dict[str, Any]] = None,
    ) -> StageResult:
        t0 = time.perf_counter()
        try:
            res = self.llm.generate_sql(
                user_query=user_query,
                schema_preview=schema_preview,
                plan_text=plan_text,
                clarify_answers=clarify_answers or {},
            )
        except Exception as e:
            return StageResult(ok=False, error=[f"Generator failed: {e}"])

        # Expect a 5-tuple
        if not isinstance(res, tuple) or len(res) != 5:
            return StageResult(
                ok=False,
                error=[
                    "Generator contract violation: expected 5-tuple (sql, rationale, t_in, t_out, cost)"
                ],
            )

        sql, rationale, t_in, t_out, cost = res

        # Type/shape checks
        if not isinstance(sql, str) or not sql.strip():
            return StageResult(
                ok=False, error=["Generator produced empty or non-string SQL"]
            )
        if not sql.lower().lstrip().startswith("select"):
            return StageResult(ok=False, error=[f"Generated non-SELECT SQL: {sql}"])

        rationale = rationale or ""  # safe length
        trace = StageTrace(
            stage=self.name,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
            token_in=t_in,
            token_out=t_out,
            cost_usd=cost,
            notes={"rationale_len": len(rationale)},
        )

        return StageResult(
            ok=True, data={"sql": sql, "rationale": rationale}, trace=trace
        )
