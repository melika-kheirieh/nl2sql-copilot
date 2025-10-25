from __future__ import annotations
import traceback
from typing import Dict, Any, Optional, List
from nl2sql.types import StageResult
from nl2sql.ambiguity_detector import AmbiguityDetector
from nl2sql.planner import Planner
from nl2sql.generator import Generator
from nl2sql.safety import Safety
from nl2sql.executor import Executor
from nl2sql.verifier import Verifier
from nl2sql.repair import Repair


class Pipeline:
    """
    NL2SQL Copilot pipeline with guaranteed dict output.
    All stages return structured traces and errors but final result is JSON-safe dict.
    """

    def __init__(
        self,
        *,
        detector: AmbiguityDetector,
        planner: Planner,
        generator: Generator,
        safety: Safety,
        executor: Executor,
        verifier: Verifier,
        repair: Repair,
    ):
        self.detector = detector
        self.planner = planner
        self.generator = generator
        self.safety = safety
        self.executor = executor
        self.verifier = verifier
        self.repair = repair

    # ------------------------------------------------------------
    def _trace_list(self, *stages: StageResult) -> List[dict]:
        traces = []
        for s in stages:
            if not s:
                continue
            t = getattr(s, "trace", None)
            if t:
                traces.append(t.__dict__)
        return traces

    # ------------------------------------------------------------
    def _safe_stage(self, fn, **kwargs) -> StageResult:
        """Run a stage safely; if it throws, catch and convert to StageResult."""
        try:
            r = fn(**kwargs)
            if isinstance(r, StageResult):
                return r
            else:
                # not ideal, but wrap it
                return StageResult(ok=True, data=r, trace=None)
        except Exception as e:
            tb = traceback.format_exc()
            return StageResult(ok=False, data=None, trace=None, errors=[f"{e}", tb])

    # ------------------------------------------------------------
    def run(
        self,
        *,
        user_query: str,
        schema_preview: str,
        clarify_answers: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Always returns:
        {
            "ambiguous": bool,
            "error": bool,
            "details": list[str] | None,
            "sql": str | None,
            "rationale": str | None,
            "verified": bool | None,
            "traces": list[dict]
        }
        """
        traces: List[dict] = []
        details: List[str] = []
        sql, rationale, verified = None, None, None

        # --- 1) ambiguity detection
        try:
            questions = self.detector.detect(user_query, schema_preview)
            if questions:
                return {
                    "ambiguous": True,
                    "error": False,
                    "details": [f"Ambiguities found: {len(questions)}"],
                    "questions": questions,
                    "traces": [],
                }
        except Exception as e:
            return {
                "ambiguous": True,
                "error": True,
                "details": [f"Detector failed: {e}"],
                "traces": [],
            }

        # --- 2) planner
        r_plan = self._safe_stage(
            self.planner.run, user_query=user_query, schema_preview=schema_preview
        )
        traces.extend(self._trace_list(r_plan))
        if not r_plan.ok:
            return {
                "ambiguous": False,
                "error": True,
                "details": r_plan.errors,
                "traces": traces,
            }

        # --- 3) generator
        r_gen = self._safe_stage(
            self.generator.run,
            user_query=user_query,
            schema_preview=schema_preview,
            plan_text=r_plan.data.get("plan"),
            clarify_answers=clarify_answers or {},
        )
        traces.extend(self._trace_list(r_gen))
        if not r_gen.ok:
            return {
                "ambiguous": False,
                "error": True,
                "details": r_gen.errors,
                "traces": traces,
            }
        sql = r_gen.data.get("sql")
        rationale = r_gen.data.get("rationale")

        # --- 4) safety
        r_safe = self._safe_stage(self.safety.check, sql=sql)
        traces.extend(self._trace_list(r_safe))
        if not r_safe.ok:
            return {
                "ambiguous": False,
                "error": True,
                "details": r_safe.errors,
                "traces": traces,
            }

        # --- 5) executor
        r_exec = self._safe_stage(self.executor.run, sql=r_safe.data["sql"])
        traces.extend(self._trace_list(r_exec))
        if not r_exec.ok:
            details.extend(r_exec.errors or [])

        # --- 6) verifier
        r_ver = self._safe_stage(self.verifier.run, sql=sql, exec_result=r_exec)
        traces.extend(self._trace_list(r_ver))
        verified = bool(r_ver.ok)

        # --- 7) repair loop if verification failed
        if not verified:
            for attempt in range(2):
                r_fix = self._safe_stage(
                    self.repair.run,
                    sql=sql,
                    error_msg="; ".join(details or ["unknown"]),
                    schema_preview=schema_preview,
                )
                traces.extend(self._trace_list(r_fix))
                if not r_fix.ok:
                    break
                sql = r_fix.data.get("sql")
                r_safe = self._safe_stage(self.safety.check, sql=sql)
                traces.extend(self._trace_list(r_safe))
                if not r_safe.ok:
                    details.extend(r_safe.errors or [])
                    continue
                r_exec = self._safe_stage(self.executor.run, sql=r_safe.data["sql"])
                traces.extend(self._trace_list(r_exec))
                if not r_exec.ok:
                    details.extend(r_exec.errors or [])
                    continue
                r_ver = self._safe_stage(self.verifier.run, sql=sql, exec_result=r_exec)
                traces.extend(self._trace_list(r_ver))
                verified = bool(r_ver.ok)
                if verified:
                    break

        # --- Final result dict
        return {
            "ambiguous": False,
            "error": len(details) > 0 and not verified,
            "details": details or None,
            "sql": sql,
            "rationale": rationale,
            "verified": verified,
            "traces": traces,
        }
