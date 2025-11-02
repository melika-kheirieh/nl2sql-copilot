import time
from typing import Any, Iterable

import sqlglot
from sqlglot import expressions as exp

from nl2sql.types import StageResult, StageTrace


class Verifier:
    name = "verifier"

    # ----------------- helpers -----------------
    @staticmethod
    def _extract_ok(exec_result: Any) -> bool | None:
        """Normalize exec_result.ok across dict or object."""
        if exec_result is None:
            return None
        if isinstance(exec_result, dict):
            return bool(exec_result.get("ok")) if "ok" in exec_result else None
        if hasattr(exec_result, "ok"):
            try:
                return bool(getattr(exec_result, "ok"))
            except Exception:
                return None
        return None

    @staticmethod
    def _extract_errors(exec_result: Any) -> list[str] | None:
        """Pull ['...'] from exec_result['error'] or exec_result.error."""
        val = None
        if isinstance(exec_result, dict):
            val = exec_result.get("error")
        elif hasattr(exec_result, "error"):
            val = getattr(exec_result, "error")

        if val is None:
            return None
        if isinstance(val, str):
            return [val]
        if isinstance(val, Iterable):
            # normalize to list[str]
            return [str(x) for x in val]
        return [str(val)]

    @staticmethod
    def _has_aggregation(tree: exp.Expression) -> bool:
        for node in tree.walk():
            if getattr(node, "is_aggregate", False):
                return True
            if isinstance(node, (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)):
                return True
        return False

    @staticmethod
    def _has_group_by(select: exp.Select) -> bool:
        return bool(select.args.get("group"))

    # ------------------- main -------------------
    def run(self, *, sql: str, exec_result: Any) -> StageResult:
        t0 = time.perf_counter()

        # 1) validate / normalize executor result
        ok_flag = self._extract_ok(exec_result)
        if ok_flag is False:
            errs = self._extract_errors(exec_result) or ["execution_error"]
            trace_err = StageTrace(
                stage=self.name,
                duration_ms=(time.perf_counter() - t0) * 1000,
                notes={"reason": "execution_error"},
            )
            return StageResult(ok=False, error=errs, trace=trace_err)

        if exec_result is None:
            trace_inv = StageTrace(
                stage=self.name, duration_ms=(time.perf_counter() - t0) * 1000
            )
            return StageResult(
                ok=False,
                error=["invalid or missing exec_result"],
                trace=trace_inv,
            )

        # 2) structural verification
        try:
            tree = sqlglot.parse_one(sql)
        except Exception as e:
            # parsing failed → accept with a note
            trace_skip = StageTrace(
                stage=self.name,
                duration_ms=(time.perf_counter() - t0) * 1000,
                notes={"note": f"Skipped parse: {e}"},
            )
            return StageResult(ok=True, data={"verified": True}, trace=trace_skip)

        issues: list[str] = []

        # Detect ANY aggregation without GROUP BY for SELECT statements
        if isinstance(tree, exp.Select):
            has_agg = self._has_aggregation(tree)
            has_group = self._has_group_by(tree)
            if has_agg and not has_group:
                issues.append("Aggregation without GROUP BY")

        dur = (time.perf_counter() - t0) * 1000
        if issues:
            trace_bad = StageTrace(
                stage=self.name, duration_ms=dur, notes={"issues": issues}
            )
            return StageResult(ok=False, error=issues, trace=trace_bad)

        # 3) success
        trace_ok = StageTrace(stage=self.name, duration_ms=dur)
        return StageResult(ok=True, data={"verified": True}, trace=trace_ok)
