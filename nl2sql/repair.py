import time


from nl2sql.types import StageTrace, StageResult
from adapters.llm.base import LLMProvider

GUIDELINES = """
When repairing:
1. Keep query SELECT-only.
2. Explicitly qualify ambiguous columns with table names.
3. Match GROUP BY fields with aggregations.
4. Use known foreign keys for JOIN.
5. Add a reasonable LIMIT if missing.
Return only the corrected SQL.
"""

class Repair:
    name = "repair"
    def __init__(self, llm: LLMProvider):
        self.llm = llm

    def run(self, sql:str, error_msg: str, schema_preview: str) -> StageResult:
        t0 = time.perf_counter()
        fixed_sql, t_in, t_out, cost = self.llm.repair(sql=sql, error_msg=f"{GUIDELINES}\n\n{error_msg}",
                                                      schema_preview=schema_preview)
        trace = StageTrace(stage=self.name, duration_ms=(time.perf_counter()-t0)*1000,
                           token_in=t_in, token_out=t_out, cost_usd=cost,
                           notes={"old_sql_len": len(sql), "new_sql_len": len(fixed_sql)})
        return StageResult(ok=True, data={"sql": fixed_sql}, trace=trace)
