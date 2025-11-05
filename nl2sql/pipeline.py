from __future__ import annotations
import traceback
from dataclasses import dataclass
from typing import Dict, Any, Optional, List
import time

from nl2sql.types import StageResult
from nl2sql.ambiguity_detector import AmbiguityDetector
from nl2sql.planner import Planner
from nl2sql.generator import Generator
from nl2sql.safety import Safety
from nl2sql.executor import Executor
from nl2sql.verifier import Verifier
from nl2sql.repair import Repair
from nl2sql.stubs import NoOpExecutor, NoOpRepair, NoOpVerifier


@dataclass(frozen=True)
class FinalResult:
    ok: bool
    ambiguous: bool
    error: bool
    details: Optional[List[str]]
    sql: Optional[str]
    rationale: Optional[str]
    verified: Optional[bool]
    questions: Optional[List[str]]
    traces: List[dict]


class Pipeline:
    """
    NL2SQL Copilot pipeline.
    Stages return StageResult; final result is a type-safe FinalResult.
    DI-ready: all dependencies are injected via __init__.
    """

    def __init__(
        self,
        *,
        detector: AmbiguityDetector,
        planner: Planner,
        generator: Generator,
        safety: Safety,
        executor: Optional[Executor] = None,
        verifier: Optional[Verifier] = None,
        repair: Optional[Repair] = None,
    ):
        self.detector = detector
        self.planner = planner
        self.generator = generator
        self.safety = safety
        self.executor = executor or NoOpExecutor()
        self.verifier = verifier or NoOpVerifier()
        self.repair = repair or NoOpRepair()

    # ------------------------------------------------------------
    @staticmethod
    def _trace_list(*stages: Optional[StageResult]) -> List[dict]:
        """Collect .trace objects (as dict) from StageResult items if present."""
        traces: List[dict] = []
        for s in stages:
            if not s:
                continue
            t = getattr(s, "trace", None)
            if t is not None:
                # t is likely a dataclass – expose as plain dict for JSON safety
                traces.append(getattr(t, "__dict__", t))
        return traces

    # ------------------------------------------------------------
    @staticmethod
    def _safe_stage(fn, **kwargs) -> StageResult:
        """
        Run a stage safely; if it throws, return a StageResult(ok=False, error=[...]).
        If fn returns a non-StageResult (e.g., dict), coerce to StageResult(ok=True, data=...).
        """
        try:
            r = fn(**kwargs)
            if isinstance(r, StageResult):
                return r
            return StageResult(ok=True, data=r, trace=None)
        except Exception as e:
            tb = traceback.format_exc()
            return StageResult(ok=False, data=None, trace=None, error=[f"{e}", tb])

    # ------------------------------------------------------------
    @staticmethod
    def _mk_trace(
        stage: str, duration_ms: float, notes: Optional[Dict[str, Any]] = None
    ) -> dict:
        """Create a normalized trace dict."""
        return {
            "stage": stage,
            "duration_ms": float(duration_ms),
            "notes": notes or {},
        }

    # ------------------------------------------------------------
    def run(
        self,
        *,
        user_query: str,
        schema_preview: str | None = None,
        clarify_answers: Optional[Dict[str, Any]] = None,
    ) -> FinalResult:
        traces: List[dict] = []
        details: List[str] = []
        sql: Optional[str] = None
        rationale: Optional[str] = None
        verified: Optional[bool] = None

        # Normalize inputs
        schema_preview = schema_preview or ""
        clarify_answers = clarify_answers or {}

        # --- 1) ambiguity detection (with explicit timing & trace) ---
        try:
            t0 = time.perf_counter()
            questions = self.detector.detect(user_query, schema_preview)
            t1 = time.perf_counter()
            traces.append(
                self._mk_trace(
                    "detector",
                    (t1 - t0) * 1000.0,
                    {
                        "ambiguous": bool(questions),
                        "questions_len": len(questions or []),
                    },
                )
            )

            if questions:
                return FinalResult(
                    ok=True,
                    ambiguous=True,
                    error=False,
                    details=[f"Ambiguities found: {len(questions)}"],
                    questions=questions,
                    sql=None,
                    rationale=None,
                    verified=None,
                    traces=traces,
                )
        except Exception as e:
            # detector crash – mark as error but keep trace so far
            traces.append(self._mk_trace("detector", 0.0, {"error": str(e)}))
            return FinalResult(
                ok=False,
                ambiguous=True,
                error=True,
                details=[f"Detector failed: {e}"],
                questions=None,
                sql=None,
                rationale=None,
                verified=None,
                traces=traces,
            )

        # --- 2) planner ---
        r_plan = self._safe_stage(
            self.planner.run, user_query=user_query, schema_preview=schema_preview
        )
        traces.extend(self._trace_list(r_plan))
        if not r_plan.ok:
            return FinalResult(
                ok=False,
                ambiguous=False,
                error=True,
                details=r_plan.error,
                questions=None,
                sql=None,
                rationale=None,
                verified=None,
                traces=traces,
            )

        # --- 3) generator ---
        r_gen = self._safe_stage(
            self.generator.run,
            user_query=user_query,
            schema_preview=schema_preview,
            plan_text=(r_plan.data or {}).get("plan"),
            clarify_answers=clarify_answers,
        )
        traces.extend(self._trace_list(r_gen))
        if not r_gen.ok:
            return FinalResult(
                ok=False,
                ambiguous=False,
                error=True,
                details=r_gen.error,
                questions=None,
                sql=None,
                rationale=None,
                verified=None,
                traces=traces,
            )

        sql = (r_gen.data or {}).get("sql")
        rationale = (r_gen.data or {}).get("rationale")

        # --- 4) safety ---
        r_safe = self._safe_stage(self.safety.run, sql=sql)
        traces.extend(self._trace_list(r_safe))
        if not r_safe.ok:
            return FinalResult(
                ok=False,
                ambiguous=False,
                error=True,
                details=r_safe.error,
                questions=None,
                sql=sql,
                rationale=rationale,
                verified=None,
                traces=traces,
            )

        # --- 5) executor ---
        r_exec = self._safe_stage(
            self.executor.run, sql=(r_safe.data or {}).get("sql", sql)
        )
        traces.extend(self._trace_list(r_exec))
        if not r_exec.ok:
            # executor failure does not hard-fail the pipeline; accumulate details
            if r_exec.error:
                details.extend(r_exec.error)

        # --- 6) verifier ---
        r_ver = self._safe_stage(
            self.verifier.run, sql=sql, exec_result=(r_exec.data or {})
        )
        traces.extend(self._trace_list(r_ver))
        verified = bool(r_ver.data and r_ver.data.get("verified")) or r_ver.ok

        # --- 7) repair loop if verification failed ---
        if not verified:
            for _attempt in range(2):
                r_fix = self._safe_stage(
                    self.repair.run,
                    sql=sql,
                    error_msg="; ".join(details or ["unknown"]),
                    schema_preview=schema_preview,
                )
                traces.extend(self._trace_list(r_fix))
                if not r_fix.ok:
                    # repair failed – stop trying further
                    break

                # re-run safety → executor → verifier on the fixed SQL
                sql = (r_fix.data or {}).get("sql", sql)

                r_safe = self._safe_stage(self.safety.run, sql=sql)
                traces.extend(self._trace_list(r_safe))
                if not r_safe.ok:
                    if r_safe.error:
                        details.extend(r_safe.error)
                    continue

                r_exec = self._safe_stage(
                    self.executor.run, sql=(r_safe.data or {}).get("sql", sql)
                )
                traces.extend(self._trace_list(r_exec))
                if not r_exec.ok:
                    if r_exec.error:
                        details.extend(r_exec.error)
                    continue

                r_ver = self._safe_stage(
                    self.verifier.run, sql=sql, exec_result=(r_exec.data or {})
                )
                traces.extend(self._trace_list(r_ver))
                verified = bool(r_ver.data and r_ver.data.get("verified")) or r_ver.ok
                if verified:
                    break

        # --- 8) fallback: verifier silent but executor succeeded ---
        if (verified is None or not verified) and not details:
            any_exec_ok = any(
                t.get("stage") == "executor" and (t.get("notes") or {}).get("row_count")
                for t in traces
            )
            if any_exec_ok:
                traces.append(
                    self._mk_trace(
                        "pipeline",
                        0.0,
                        {
                            "auto_fix": "verified=True (executor succeeded, verifier silent)"
                        },
                    )
                )
                verified = True

        # --- 9) finalize result ---
        has_errors = bool(details)
        ok = bool(verified) and not has_errors
        err = has_errors and not bool(verified)

        traces.append(
            self._mk_trace(
                "pipeline",
                0.0,
                {"final_verified": bool(verified), "details_len": len(details)},
            )
        )

        return FinalResult(
            ok=ok,
            ambiguous=False,
            error=err,
            details=details or None,
            sql=sql,
            rationale=rationale,
            verified=verified,
            questions=None,
            traces=traces,
        )
