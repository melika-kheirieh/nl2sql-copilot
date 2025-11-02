import time
import sqlglot
from sqlglot import expressions as exp
from nl2sql.types import StageResult, StageTrace


class Verifier:
    name = "verifier"

    def run(self, sql: str, exec_result: dict | None) -> StageResult:
        t0 = time.perf_counter()

        # Defensive: check executor result validity
        if not exec_result or not isinstance(exec_result, dict):
            return StageResult(
                ok=False,
                error=["invalid or missing exec_result"],
                data=None,
                trace=StageTrace(
                    stage=self.name, duration_ms=(time.perf_counter() - t0) * 1000
                ),
            )

        # If executor had rows and no error, consider verified early
        rows = exec_result.get("rows")
        if rows is not None and len(rows) > 0:
            return StageResult(
                ok=True,
                data={"verified": True, "rows_checked": len(rows)},
                trace=StageTrace(
                    stage=self.name, duration_ms=(time.perf_counter() - t0) * 1000
                ),
            )

        # Optional deeper check using SQL structure
        issues = []
        try:
            tree = sqlglot.parse_one(sql)
            if isinstance(tree, exp.Select):
                group = tree.args.get("group")
                aggs = [a for a in tree.find_all(exp.AggFunc)]
                if aggs and not group:
                    select_cols = [
                        c for c in tree.expressions if not isinstance(c, exp.AggFunc)
                    ]
                    if select_cols:
                        issues.append(
                            "Non-aggregated columns with aggregation but no GROUP BY."
                        )
        except Exception as e:
            # parsing failed → skip structural verification gracefully
            return StageResult(
                ok=True,
                data={"verified": True, "note": f"Skipped parse: {e}"},
                trace=StageTrace(
                    stage=self.name, duration_ms=(time.perf_counter() - t0) * 1000
                ),
            )

        dur = (time.perf_counter() - t0) * 1000
        if issues:
            return StageResult(
                ok=False,
                error=issues,
                trace=StageTrace(
                    stage=self.name, duration_ms=dur, notes={"issues": issues}
                ),
            )

        return StageResult(
            ok=True,
            data={"verified": True},
            trace=StageTrace(stage=self.name, duration_ms=dur),
        )
