import sqlglot
from sqlglot import expressions as exp
from nl2sql.types import StageResult, StageTrace


class Verifier:
    name = "verifier"

    def run(self, sql: str, exec_result: StageResult) -> StageResult:
        if not exec_result.ok:
            return StageResult(
                ok=False,
                data=None,
                trace=StageTrace(
                    stage=self.name, duration_ms=0, notes={"reason": "execution_error"}
                ),
                error=exec_result.error,
            )

        # Rule 1: check SELECT / GROUP consistency
        issues = []
        try:
            tree = sqlglot.parse_one(sql)
            if isinstance(tree, exp.Select):
                group = tree.args.get("group")
                aggs = [a for a in tree.find_all(exp.AggFunc)]
                if aggs and not group:
                    issues.append("Aggregation without GROUP BY.")
        except Exception as e:
            issues.append(f"Parse error during verification: {e}")

        if issues:
            return StageResult(
                ok=False,
                data=None,
                trace=StageTrace(
                    stage=self.name, duration_ms=0, notes={"issues": issues}
                ),
                error=issues,
            )
        return StageResult(
            ok=True,
            data={"verified": True},
            trace=StageTrace(stage=self.name, duration_ms=0),
        )
