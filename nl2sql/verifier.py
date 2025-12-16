from __future__ import annotations

import re
import time
from typing import Any, Dict

from nl2sql.errors.codes import ErrorCode
from nl2sql.metrics import verifier_checks_total, verifier_failures_total
from nl2sql.types import StageResult, StageTrace

from adapters.db.base import DBAdapter


class Verifier:
    """
    Verifier stage:
    - Lightweight sanity checks (lint-like; NOT safety policy)
    - Optional DB-backed plan validation via adapter.explain_query_plan(sql)
      (read-only, no query execution)
    """

    required = False

    def verify(self, sql: str, *, adapter: DBAdapter | None = None) -> StageResult:
        t0 = time.perf_counter()
        notes: Dict[str, Any] = {}
        reason = "ok"

        s = (sql or "").strip()
        sl = s.lower()
        notes["sql_length"] = len(s)

        try:
            # --- quick sanity: require SELECT and FROM (lint-like) ---
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
                    error_code=ErrorCode.PLAN_SYNTAX_ERROR,  # best-fit for malformed SQL
                )

            # --- semantic sanity: aggregation without GROUP BY (unless allowed) ---
            # This is NOT a safety rule; it is a quality check to catch common mistakes.
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
                    error_code=ErrorCode.PLAN_SYNTAX_ERROR,
                )

            # --- DB-backed plan validation (read-only), if adapter provided ---
            # Safety policy (SELECT-only, no multi-statement, etc.) must be enforced upstream.
            if adapter is not None:
                try:
                    adapter.explain_query_plan(s)
                    notes["plan_check"] = "ok"
                except Exception as e:
                    reason = "plan-error"
                    notes["plan_check"] = "failed"

                    code = self._classify_plan_error(e)

                    return self._fail(
                        t0,
                        notes,
                        error=[str(e)],
                        reason=reason,
                        exc_type=type(e).__name__,
                        error_code=code,
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
            # Unexpected verifier crash (bug)
            reason = "exception"
            return self._fail(
                t0,
                notes,
                error=[str(e)],
                reason=reason,
                exc_type=type(e).__name__,
                error_code=ErrorCode.PIPELINE_CRASH,
            )

    def run(
        self,
        *,
        sql: str,
        exec_result: Dict[str, Any],
        adapter: DBAdapter | None = None,
    ) -> StageResult:
        # exec_result kept for signature compatibility, not used here.
        return self.verify(sql, adapter=adapter)

    def _classify_plan_error(self, e: Exception) -> ErrorCode:
        msg = str(e).lower()

        # SQLite-style messages
        if "no such table" in msg:
            return ErrorCode.PLAN_NO_SUCH_TABLE
        if "no such column" in msg:
            return ErrorCode.PLAN_NO_SUCH_COLUMN
        if "syntax error" in msg:
            return ErrorCode.PLAN_SYNTAX_ERROR

        # Postgres-style messages (common cases)
        if "relation" in msg and "does not exist" in msg:
            return ErrorCode.PLAN_NO_SUCH_TABLE
        if "column" in msg and "does not exist" in msg:
            return ErrorCode.PLAN_NO_SUCH_COLUMN

        return ErrorCode.PLAN_SYNTAX_ERROR

    def _fail(
        self,
        t0: float,
        notes: Dict[str, Any],
        *,
        error: list[str],
        reason: str,
        exc_type: str | None = None,
        error_code: ErrorCode | None = None,
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
            error_code=error_code,
            retryable=False,
        )
