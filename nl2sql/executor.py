import sqlglot
from sqlglot import exp

import time
from nl2sql.types import StageResult, StageTrace
from nl2sql.errors.codes import ErrorCode
from adapters.db.base import DBAdapter


class Executor:
    name = "executor"

    def __init__(self, db: DBAdapter):
        self.db = db

    def _preflight_cost_check(self, sql: str) -> tuple[bool, str, dict]:
        """Return (ok, reason, notes). Reason is machine-readable."""
        sql_stripped = (sql or "").strip().rstrip(";")
        notes: dict = {"sql_length": len(sql_stripped)}
        if not sql_stripped:
            return False, "empty_sql", notes

        # Parse for cheap structural signals (LIMIT/JOIN/ORDER)
        try:
            tree = sqlglot.parse_one(
                sql_stripped, read=getattr(self.db, "dialect", None) or "sqlite"
            )
        except Exception:
            # Safety should usually catch parse errors; executor treats as reject.
            return False, "parse_error", notes

        has_limit = tree.find(exp.Limit) is not None
        join_count = sum(1 for _ in tree.find_all(exp.Join))
        has_order = tree.find(exp.Order) is not None
        has_star = tree.find(exp.Star) is not None
        notes.update(
            {"has_limit": has_limit, "join_count": join_count, "has_order": has_order}
        )

        # Ask DB for a plan preview
        try:
            plan_lines = self.db.explain_query_plan(sql_stripped)
        except Exception as e:
            # Planning failures are treated as non-OK but not as cost guardrail.
            notes.update({"plan_error": str(e), "plan_error_type": type(e).__name__})
            return True, "plan_unavailable", notes

        plan_preview = plan_lines[:6] if isinstance(plan_lines, list) else []
        notes.update({"plan_preview": plan_preview})

        plan_text = "".join(plan_lines).lower() if isinstance(plan_lines, list) else ""
        full_scan = ("scan" in plan_text) and ("index" not in plan_text)
        notes.update({"full_scan": full_scan})

        # MVP heuristics
        # Block only the highest-risk pattern for v1: full scan + no LIMIT + SELECT *
        if full_scan and (not has_limit) and has_star:
            return False, "full_scan_without_limit", notes
        # Very high join count is a strong proxy for expensive queries
        if join_count >= 6:
            return False, "too_many_joins", notes
        return True, "ok", notes

    def run(self, sql: str) -> StageResult:
        t0 = time.perf_counter()

        preflight_ok, preflight_reason, preflight_notes = self._preflight_cost_check(
            sql
        )
        if not preflight_ok:
            trace = StageTrace(
                stage=self.name,
                duration_ms=(time.perf_counter() - t0) * 1000,
                summary="blocked",
                notes={
                    **preflight_notes,
                    "blocked_reason": preflight_reason,
                },
            )
            return StageResult(
                ok=False,
                data=None,
                trace=trace,
                error=[preflight_reason],
                error_code=ErrorCode.EXECUTOR_COST_GUARDRAIL_BLOCKED,
                retryable=False,
            )

        try:
            rows, cols = self.db.execute(sql)
            trace = StageTrace(
                stage=self.name,
                duration_ms=(time.perf_counter() - t0) * 1000,
                notes={
                    "row_count": len(rows),
                    "col_count": len(cols),
                    "sql_length": len(sql or ""),
                    "preflight": preflight_reason,
                    **preflight_notes,
                },
            )
            return StageResult(
                ok=True, data={"rows": rows, "columns": cols}, trace=trace
            )
        except Exception as e:
            trace = StageTrace(
                stage=self.name,
                duration_ms=(time.perf_counter() - t0) * 1000,
                notes={
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "sql_length": len(sql or ""),
                    "preflight": preflight_reason,
                    **preflight_notes,
                },
            )
            return StageResult(ok=False, data=None, trace=trace, error=[str(e)])
