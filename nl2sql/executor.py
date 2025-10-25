import time
from nl2sql.types import StageResult, StageTrace
from adapters.db.base import DBAdapter


class Executor:
    name = "executor"

    def __init__(self, db: DBAdapter):
        self.db = db

    def run(self, sql: str) -> StageResult:
        t0 = time.perf_counter()
        try:
            rows, cols = self.db.execute(sql)
            trace = StageTrace(
                stage=self.name,
                duration_ms=(time.perf_counter() - t0) * 1000,
                notes={"row_count": len(rows), "col_count": len(cols)},
            )
            return StageResult(
                ok=True, data={"rows": rows, "columns": cols}, trace=trace
            )
        except Exception as e:
            trace = StageTrace(
                stage=self.name,
                duration_ms=(time.perf_counter() - t0) * 1000,
                notes={"error": str(e)},
            )
            return StageResult(ok=False, data=None, trace=trace, error=[str(e)])
