from __future__ import annotations
import time
from nl2sql.types import StageResult, StageTrace
from adapters.llm.base import LLMProvider

class Planner:
    name = "planner"
    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    def run(self, *, user_query: str, schema_preview: str) -> StageResult:
        t0 = time.perf_counter()
        plan_text, t_in, t_out, cost = self.llm.plan(user_query=user_query, schema_preview=schema_preview)
        trace = StageTrace(stage=self.name, duration_ms=(time.perf_counter()-t0)*1000,
                           token_in=t_in, token_out=t_out, cost_usd=cost, notes={"len_plan": len(plan_text)})
        return StageResult(ok=True, data={"plan": plan_text}, trace=trace)
