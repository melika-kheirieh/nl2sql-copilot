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
from nl2sql.metrics import stage_duration_ms, pipeline_runs_total


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
    def _mk_trace(
        stage: str,
        duration_ms: float,
        summary: str,
        notes: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """Create a normalized trace dict (internal: duration may be float)."""
        return {
            "stage": stage,
            "duration_ms": float(duration_ms),
            "summary": summary,
            "notes": notes or {},
        }

    @staticmethod
    def _normalize_traces(traces: List[dict]) -> List[dict]:
        """
        Normalize trace list for API/UI:
        - coerce duration_ms to int
        - ensure `summary` exists (fallback to a minimal one)
        """
        norm: List[dict] = []
        for t in traces:
            stage = str(t.get("stage", "unknown"))
            dur = t.get("duration_ms", 0)
            try:
                dur_int = int(round(float(dur)))
            except Exception:
                dur_int = 0
            summary = t.get("summary")
            if not summary:
                # fallback summary if not provided by stage
                notes = t.get("notes") or {}
                failed = bool(notes.get("error") or notes.get("errors"))
                summary = "failed" if failed else "ok"
            notes = t.get("notes") or {}
            # preserve any accounting fields if present (token_in/out, cost_usd, ...)
            payload = {
                "stage": stage,
                "duration_ms": dur_int,
                "summary": summary,
                "notes": notes,
            }
            # keep extra accounting if exists
            if "token_in" in t:
                payload["token_in"] = t["token_in"]
            if "token_out" in t:
                payload["token_out"] = t["token_out"]
            if "cost_usd" in t:
                payload["cost_usd"] = t["cost_usd"]
            norm.append(payload)
        return norm

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
    def run(
        self,
        *,
        user_query: str,
        schema_preview: str | None = None,
        clarify_answers: Optional[Dict[str, Any]] = None,
    ) -> FinalResult:
        t_all0 = time.perf_counter()
        traces: List[dict] = []
        details: List[str] = []

        # Always push a normalized per-stage timing, even if StageResult.trace is empty
        def _fallback_trace(stage_name: str, dt_ms: float, ok: bool) -> None:
            traces.append(
                self._mk_trace(
                    stage=stage_name,
                    duration_ms=dt_ms,
                    summary=("ok" if ok else "failed"),
                )
            )

        # Normalize inputs
        schema_preview = schema_preview or ""
        clarify_answers = clarify_answers or {}

        try:
            # --- 1) detector ---
            t_det0 = time.perf_counter()
            questions = self.detector.detect(user_query, schema_preview)
            det_ms = (time.perf_counter() - t_det0) * 1000.0
            is_amb = bool(questions)
            stage_duration_ms.labels("detector").observe(det_ms)
            traces.append(
                self._mk_trace(
                    stage="detector",
                    duration_ms=det_ms,
                    summary=("ambiguous" if is_amb else "clear"),
                    notes={"ambiguous": is_amb, "questions_len": len(questions or [])},
                )
            )

            if questions:
                pipeline_runs_total.labels(status="ambiguous").inc()
                return FinalResult(
                    ok=True,
                    ambiguous=True,
                    error=False,
                    details=[f"Ambiguities found: {len(questions)}"],
                    questions=questions,
                    sql=None,
                    rationale=None,
                    verified=None,
                    traces=self._normalize_traces(traces),
                )

            # --- 2) planner ---
            t_pln0 = time.perf_counter()
            r_plan = self._safe_stage(
                self.planner.run, user_query=user_query, schema_preview=schema_preview
            )
            pln_ms = (time.perf_counter() - t_pln0) * 1000.0
            stage_duration_ms.labels("planner").observe(pln_ms)
            traces.extend(self._trace_list(r_plan))
            if not getattr(r_plan, "trace", None):
                _fallback_trace("planner", pln_ms, r_plan.ok)
            if not r_plan.ok:
                pipeline_runs_total.labels(status="error").inc()
                return FinalResult(
                    ok=False,
                    ambiguous=False,
                    error=True,
                    details=r_plan.error,
                    questions=None,
                    sql=None,
                    rationale=None,
                    verified=None,
                    traces=self._normalize_traces(traces),
                )

            # --- 3) generator ---
            t_gen0 = time.perf_counter()
            r_gen = self._safe_stage(
                self.generator.run,
                user_query=user_query,
                schema_preview=schema_preview,
                plan_text=(r_plan.data or {}).get("plan"),
                clarify_answers=clarify_answers,
            )
            gen_ms = (time.perf_counter() - t_gen0) * 1000.0
            stage_duration_ms.labels("generator").observe(gen_ms)
            traces.extend(self._trace_list(r_gen))
            if not getattr(r_gen, "trace", None):
                _fallback_trace("generator", gen_ms, r_gen.ok)
            if not r_gen.ok:
                pipeline_runs_total.labels(status="error").inc()
                return FinalResult(
                    ok=False,
                    ambiguous=False,
                    error=True,
                    details=r_gen.error,
                    questions=None,
                    sql=None,
                    rationale=None,
                    verified=None,
                    traces=self._normalize_traces(traces),
                )

            sql = (r_gen.data or {}).get("sql")
            rationale = (r_gen.data or {}).get("rationale")

            # --- 4) safety ---
            t_saf0 = time.perf_counter()
            r_safe = self._safe_stage(self.safety.run, sql=sql)
            saf_ms = (time.perf_counter() - t_saf0) * 1000.0
            stage_duration_ms.labels("safety").observe(saf_ms)
            traces.extend(self._trace_list(r_safe))
            if not getattr(r_safe, "trace", None):
                _fallback_trace("safety", saf_ms, r_safe.ok)
            if not r_safe.ok:
                pipeline_runs_total.labels(status="error").inc()
                return FinalResult(
                    ok=False,
                    ambiguous=False,
                    error=True,
                    details=r_safe.error,
                    questions=None,
                    sql=sql,
                    rationale=rationale,
                    verified=None,
                    traces=self._normalize_traces(traces),
                )

            # --- 5) executor ---
            t_exe0 = time.perf_counter()
            r_exec = self._safe_stage(
                self.executor.run, sql=(r_safe.data or {}).get("sql", sql)
            )
            exe_ms = (time.perf_counter() - t_exe0) * 1000.0
            stage_duration_ms.labels("executor").observe(exe_ms)
            traces.extend(self._trace_list(r_exec))
            if not getattr(r_exec, "trace", None):
                _fallback_trace("executor", exe_ms, r_exec.ok)
            if not r_exec.ok and r_exec.error:
                # executor failure is soft; collect for repair/verifier context
                details.extend(r_exec.error)

            # --- 6) verifier ---
            t_ver0 = time.perf_counter()
            r_ver = self._safe_stage(
                self.verifier.run, sql=sql, exec_result=(r_exec.data or {})
            )
            ver_ms = (time.perf_counter() - t_ver0) * 1000.0
            stage_duration_ms.labels("verifier").observe(ver_ms)
            traces.extend(self._trace_list(r_ver))
            if not getattr(r_ver, "trace", None):
                _fallback_trace("verifier", ver_ms, r_ver.ok)
            verified = bool(r_ver.data and r_ver.data.get("verified")) or r_ver.ok

            # --- 7) repair loop if verification failed ---
            if not verified:
                for _attempt in range(2):
                    # repair
                    t_fix0 = time.perf_counter()
                    r_fix = self._safe_stage(
                        self.repair.run,
                        sql=sql,
                        error_msg="; ".join(details or ["unknown"]),
                        schema_preview=schema_preview,
                    )
                    fix_ms = (time.perf_counter() - t_fix0) * 1000.0
                    stage_duration_ms.labels("repair").observe(fix_ms)
                    traces.extend(self._trace_list(r_fix))
                    if not getattr(r_fix, "trace", None):
                        _fallback_trace("repair", fix_ms, r_fix.ok)
                    if not r_fix.ok:
                        break  # give up on repair

                    # fixed SQL
                    sql = (r_fix.data or {}).get("sql", sql)

                    # safety
                    t_saf0 = time.perf_counter()
                    r_safe = self._safe_stage(self.safety.run, sql=sql)
                    saf_ms2 = (time.perf_counter() - t_saf0) * 1000.0
                    stage_duration_ms.labels("safety").observe(saf_ms2)
                    traces.extend(self._trace_list(r_safe))
                    if not getattr(r_safe, "trace", None):
                        _fallback_trace("safety", saf_ms2, r_safe.ok)
                    if not r_safe.ok:
                        if r_safe.error:
                            details.extend(r_safe.error)
                        continue

                    # executor
                    t_exe0 = time.perf_counter()
                    r_exec = self._safe_stage(
                        self.executor.run, sql=(r_safe.data or {}).get("sql", sql)
                    )
                    exe_ms2 = (time.perf_counter() - t_exe0) * 1000.0
                    stage_duration_ms.labels("executor").observe(exe_ms2)
                    traces.extend(self._trace_list(r_exec))
                    if not getattr(r_exec, "trace", None):
                        _fallback_trace("executor", exe_ms2, r_exec.ok)
                    if not r_exec.ok:
                        if r_exec.error:
                            details.extend(r_exec.error)
                        continue

                    # verifier
                    t_ver0 = time.perf_counter()
                    r_ver = self._safe_stage(
                        self.verifier.run, sql=sql, exec_result=(r_exec.data or {})
                    )
                    ver_ms2 = (time.perf_counter() - t_ver0) * 1000.0
                    stage_duration_ms.labels("verifier").observe(ver_ms2)
                    traces.extend(self._trace_list(r_ver))
                    if not getattr(r_ver, "trace", None):
                        _fallback_trace("verifier", ver_ms2, r_ver.ok)
                    verified = (
                        bool(r_ver.data and r_ver.data.get("verified")) or r_ver.ok
                    )
                    if verified:
                        break

            # --- 8) fallback: verifier silent but executor succeeded ---
            if (verified is None or not verified) and not details:
                any_exec_ok = any(
                    t.get("stage") == "executor"
                    and (t.get("notes") or {}).get("row_count")
                    for t in traces
                )
                if any_exec_ok:
                    traces.append(
                        self._mk_trace(
                            stage="pipeline",
                            duration_ms=0.0,
                            summary="auto-verified",
                            notes={"reason": "executor succeeded, verifier silent"},
                        )
                    )
                    verified = True

            # --- 9) finalize ---
            has_errors = bool(details)
            ok = bool(verified) and not has_errors
            err = has_errors and not bool(verified)

            if ok:
                pipeline_runs_total.labels(status="ok").inc()
            else:
                pipeline_runs_total.labels(status="error").inc()

            traces.append(
                self._mk_trace(
                    stage="pipeline",
                    duration_ms=0.0,
                    summary="finalize",
                    notes={
                        "final_verified": bool(verified),
                        "details_len": len(details),
                    },
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
                traces=self._normalize_traces(traces),
            )

        except Exception:
            # Any unexpected crash
            pipeline_runs_total.labels(status="error").inc()
            raise

        finally:
            # Always record total latency even on early-return / exceptions
            stage_duration_ms.labels("pipeline_total").observe(
                (time.perf_counter() - t_all0) * 1000.0
            )
