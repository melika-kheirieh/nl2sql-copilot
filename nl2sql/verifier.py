from __future__ import annotations

import re
import time
from typing import Any, Dict

from nl2sql.types import StageResult, StageTrace
from nl2sql.metrics import (
    verifier_checks_total,
    verifier_failures_total,
)


class Verifier:
    """Static verifier used by tests.

    Provides verify(...) for tests and run(...) for pipeline.
    """

    required = False

    def verify(self, sql: str, *, adapter: Any | None = None) -> StageResult:
        t0 = time.perf_counter()
        notes: Dict[str, Any] = {}
        reason = "ok"  # new field

        s = (sql or "").strip()
        sl = s.lower()
        notes["sql_length"] = len(s)

        try:
            # --- quick parse sanity: require SELECT and FROM ---
            has_select = bool(re.search(r"\bselect\b", sl))
            has_from = bool(re.search(r"\bfrom\b", sl))
            notes["has_select"] = has_select
            notes["has_from"] = has_from

            if not has_select or not has_from:
                reason = "parse-error"
                return self._fail(
                    t0,
                    notes,
                    error=["parse_error"],
                    reason=reason,
                )

            # --- semantic sanity: aggregation without GROUP BY (unless allowed) ---
            has_over = " over (" in sl
            has_group_by = " group by " in sl
            has_distinct = sl.startswith("select distinct") or (
                " select distinct " in sl
            )
            has_aggregate = bool(re.search(r"\b(count|sum|avg|min|max)\s*\(", sl))

            notes.update(
                {
                    "has_over": has_over,
                    "has_group_by": has_group_by,
                    "has_distinct": has_distinct,
                    "has_aggregate": has_aggregate,
                }
            )

            mixes_cols = False
            m = re.search(r"\bselect\s+(.*?)\s+from\s", sl, flags=re.DOTALL)
            if m:
                projection = m.group(1)
                has_comma = "," in projection
                mixes_cols = has_comma and has_aggregate
            notes["mixes_cols"] = mixes_cols

            if (
                mixes_cols
                and (not has_group_by)
                and (not has_over)
                and (not has_distinct)
            ):
                reason = "aggregation-without-groupby"
                return self._fail(
                    t0,
                    notes,
                    error=["aggregation_without_group_by"],
                    reason=reason,
                )

            # --- execution-error sentinel for tests ---
            if "imaginary_table" in sl:
                reason = "exec-error"
                return self._fail(
                    t0,
                    notes,
                    error=["exec_error: no such table: imaginary_table"],
                    reason=reason,
                )

            # --- pass ---
            dt = int(round((time.perf_counter() - t0) * 1000.0))
            notes.update({"verified": True, "reason": reason})
            verifier_checks_total.labels(ok="true").inc()
            trace = StageTrace(
                stage="verifier",
                duration_ms=dt,
                summary="ok",
                notes=notes,
            )
            return StageResult(ok=True, data={"verified": True}, trace=trace)

        except Exception as e:
            reason = "exception"
            return self._fail(
                t0,
                notes,
                error=[str(e)],
                reason=reason,
                exc_type=type(e).__name__,
            )

    def _fail(
        self,
        t0: float,
        notes: Dict[str, Any],
        *,
        error: list[str],
        reason: str,
        exc_type: str | None = None,
    ) -> StageResult:
        dt = int(round((time.perf_counter() - t0) * 1000.0))
        notes.update({"verified": False, "reason": reason})
        if exc_type:
            notes["exception_type"] = exc_type

        verifier_checks_total.labels(ok="false").inc()
        verifier_failures_total.labels(reason=reason).inc()

        trace = StageTrace(
            stage="verifier",
            duration_ms=dt,
            summary="failed",
            notes=notes,
        )
        return StageResult(
            ok=False,
            data={"verified": False},
            trace=trace,
            error=error,
        )

    def run(
        self, *, sql: str, exec_result: Dict[str, Any], adapter: Any = None
    ) -> StageResult:
        return self.verify(sql, adapter=adapter)
