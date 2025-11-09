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
    NL2SQL Copilot pipeline:
      detector → planner → generator → safety → executor → verifier → (optional repair loop).
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
        # If the verifier explicitly requires verification, enforce it in finalize.
        self.require_verification = bool(getattr(self.verifier, "required", False))

    # ---------------------------- helpers ----------------------------
    @staticmethod
    def _trace_list(*stages: Optional[StageResult]) -> List[dict]:
        traces: List[dict] = []
        for s in stages:
            if not s:
                continue
            t = getattr(s, "trace", None)
            if t is not None:
                traces.append(getattr(t, "__dict__", t))
        return traces

    @staticmethod
    def _mk_trace(
        stage: str,
        duration_ms: float,
        summary: str,
        notes: Optional[Dict[str, Any]] = None,
    ) -> dict:
        return {
            "stage": stage,
            "duration_ms": float(duration_ms),
            "summary": summary,
            "notes": notes or {},
        }

    @staticmethod
    def _normalize_traces(traces: List[dict]) -> List[dict]:
        norm: List[dict] = []
        for t in traces:
            stage = str(t.get("stage", "unknown"))
            dur = t.get("duration_ms", 0)
            # robust to any type; enforce minimum 1ms
            dur_val = 0.0
            try:
                dur_val = float(dur)
            except Exception:
                dur_val = 0.0
            dur_int = max(1, int(round(dur_val)))
            notes = t.get("notes") or {}
            summary = t.get("summary") or ("ok" if t.get("ok") else "failed")
            norm.append(
                {
                    "stage": stage,
                    "duration_ms": dur_int,
                    "summary": summary,
                    "notes": notes or {},
                }
            )
        return norm

    @staticmethod
    def _safe_stage(fn, **kwargs) -> StageResult:
        try:
            r = fn(**kwargs)
            if isinstance(r, StageResult):
                return r
            return StageResult(ok=True, data=r, trace=None)
        except Exception as e:
            tb = traceback.format_exc()
            return StageResult(ok=False, data=None, trace=None, error=[f"{e}", tb])

    # ------------------------------ run ------------------------------
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

        def _fallback_trace(stage_name: str, dt_ms: float, ok: bool) -> None:
            traces.append(
                self._mk_trace(
                    stage=stage_name,
                    duration_ms=dt_ms,
                    summary=("ok" if ok else "failed"),
                )
            )

        schema_preview = schema_preview or ""
        clarify_answers = clarify_answers or {}

        try:
            # --- 1) detector ---
            t0 = time.perf_counter()
            questions = self.detector.detect(user_query, schema_preview)
            dt = (time.perf_counter() - t0) * 1000.0
            is_amb = bool(questions)
            stage_duration_ms.labels("detector").observe(dt)
            traces.append(
                self._mk_trace(
                    stage="detector",
                    duration_ms=dt,
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
            t0 = time.perf_counter()
            r_plan = self._safe_stage(
                self.planner.run, user_query=user_query, schema_preview=schema_preview
            )
            dt = (time.perf_counter() - t0) * 1000.0
            stage_duration_ms.labels("planner").observe(dt)
            traces.extend(self._trace_list(r_plan))
            if not getattr(r_plan, "trace", None):
                _fallback_trace("planner", dt, r_plan.ok)
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
            t0 = time.perf_counter()
            r_gen = self._safe_stage(
                self.generator.run,
                user_query=user_query,
                schema_preview=schema_preview,
                plan_text=(r_plan.data or {}).get("plan"),
                clarify_answers=clarify_answers,
            )
            dt = (time.perf_counter() - t0) * 1000.0
            stage_duration_ms.labels("generator").observe(dt)
            traces.extend(self._trace_list(r_gen))
            if not getattr(r_gen, "trace", None):
                _fallback_trace("generator", dt, r_gen.ok)
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

            # Guard: empty SQL
            if not sql or not str(sql).strip():
                pipeline_runs_total.labels(status="error").inc()
                traces.append(
                    self._mk_trace("generator", 0.0, "failed", {"reason": "empty_sql"})
                )
                return FinalResult(
                    ok=False,
                    ambiguous=False,
                    error=True,
                    details=["empty_sql"],
                    questions=None,
                    sql=None,
                    rationale=rationale,
                    verified=None,
                    traces=self._normalize_traces(traces),
                )

            # --- 4) safety ---
            t0 = time.perf_counter()
            r_safe = self._safe_stage(self.safety.run, sql=sql)
            dt = (time.perf_counter() - t0) * 1000.0
            stage_duration_ms.labels("safety").observe(dt)
            traces.extend(self._trace_list(r_safe))
            if not getattr(r_safe, "trace", None):
                _fallback_trace("safety", dt, r_safe.ok)
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

            # Use sanitized SQL from safety
            sql = (r_safe.data or {}).get("sql", sql)

            # --- 5) executor ---
            t0 = time.perf_counter()
            r_exec = self._safe_stage(self.executor.run, sql=sql)
            dt = (time.perf_counter() - t0) * 1000.0
            stage_duration_ms.labels("executor").observe(dt)
            traces.extend(self._trace_list(r_exec))
            if not getattr(r_exec, "trace", None):
                _fallback_trace("executor", dt, r_exec.ok)
            if not r_exec.ok and r_exec.error:
                details.extend(r_exec.error)  # soft: keep for repair/verifier context

            # --- 6) verifier ---
            t0 = time.perf_counter()
            r_ver = self._safe_stage(
                self.verifier.run,
                sql=sql,
                exec_result=(r_exec.data or {}),
                adapter=getattr(
                    self.executor, "adapter", None
                ),  # let verifier use adapter
            )
            dt = (time.perf_counter() - t0) * 1000.0
            stage_duration_ms.labels("verifier").observe(dt)
            traces.extend(self._trace_list(r_ver))
            if not getattr(r_ver, "trace", None):
                _fallback_trace("verifier", dt, r_ver.ok)
            verified = bool(r_ver.data and r_ver.data.get("verified")) or r_ver.ok

            # consume repaired SQL from verifier if any
            if r_ver.data and "sql" in r_ver.data and r_ver.data["sql"]:
                sql = r_ver.data["sql"]

            # --- 7) repair loop (if not verified) ---
            if not verified:
                for _attempt in range(2):
                    # repair
                    t0 = time.perf_counter()
                    r_fix = self._safe_stage(
                        self.repair.run,
                        sql=sql,
                        error_msg="; ".join(details or ["unknown"]),
                        schema_preview=schema_preview,
                    )
                    dt = (time.perf_counter() - t0) * 1000.0
                    stage_duration_ms.labels("repair").observe(dt)
                    traces.extend(self._trace_list(r_fix))
                    if not getattr(r_fix, "trace", None):
                        _fallback_trace("repair", dt, r_fix.ok)
                    if not r_fix.ok:
                        break

                    # update SQL
                    sql = (r_fix.data or {}).get("sql", sql)

                    # safety again
                    t0 = time.perf_counter()
                    r_safe2 = self._safe_stage(self.safety.run, sql=sql)
                    dt2 = (time.perf_counter() - t0) * 1000.0
                    stage_duration_ms.labels("safety").observe(dt2)
                    traces.extend(self._trace_list(r_safe2))
                    if not getattr(r_safe2, "trace", None):
                        _fallback_trace("safety", dt2, r_safe2.ok)
                    if not r_safe2.ok:
                        if r_safe2.error:
                            details.extend(r_safe2.error)
                        continue
                    sql = (r_safe2.data or {}).get("sql", sql)

                    # executor again
                    t0 = time.perf_counter()
                    r_exec2 = self._safe_stage(self.executor.run, sql=sql)
                    dt2 = (time.perf_counter() - t0) * 1000.0
                    stage_duration_ms.labels("executor").observe(dt2)
                    traces.extend(self._trace_list(r_exec2))
                    if not getattr(r_exec2, "trace", None):
                        _fallback_trace("executor", dt2, r_exec2.ok)
                    if not r_exec2.ok:
                        if r_exec2.error:
                            details.extend(r_exec2.error)
                        continue

                    # verifier again
                    t0 = time.perf_counter()
                    r_ver2 = self._safe_stage(
                        self.verifier.run,
                        sql=sql,
                        exec_result=(r_exec2.data or {}),
                        adapter=getattr(self.executor, "adapter", None),
                    )
                    dt2 = (time.perf_counter() - t0) * 1000.0
                    stage_duration_ms.labels("verifier").observe(dt2)
                    traces.extend(self._trace_list(r_ver2))
                    if not getattr(r_ver2, "trace", None):
                        _fallback_trace("verifier", dt2, r_ver2.ok)
                    verified = (
                        bool(r_ver2.data and r_ver2.data.get("verified")) or r_ver2.ok
                    )
                    if r_ver2.data and "sql" in r_ver2.data and r_ver2.data["sql"]:
                        sql = r_ver2.data["sql"]
                    if verified:
                        break

            # --- 8) optional soft auto-verify (executor success, no details) ---
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
            need_ver = bool(self.require_verification)

            # base success condition
            final_ok_by_verifier = bool(verified)
            base_ok = (
                bool(sql) and not has_errors and (final_ok_by_verifier or not need_ver)
            )
            ok = base_ok
            err = (not ok) and has_errors

            # align `verified` with baseline semantics:
            # if verification is NOT required and pipeline is ok, report verified=True
            if not need_ver and ok and not final_ok_by_verifier:
                verified_final = True
            else:
                verified_final = bool(verified)

            pipeline_runs_total.labels(status=("ok" if ok else "error")).inc()

            traces.append(
                self._mk_trace(
                    stage="pipeline",
                    duration_ms=0.0,
                    summary="finalize",
                    notes={
                        "final_verified": bool(verified_final),
                        "details_len": len(details),
                        "need_verification": need_ver,
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
                verified=verified_final,
                questions=None,
                traces=self._normalize_traces(traces),
            )

        except Exception:
            pipeline_runs_total.labels(status="error").inc()
            # bubble up to make failures visible in tests and logs
            raise

        finally:
            # Always record total latency, even on early return/exception
            stage_duration_ms.labels("pipeline_total").observe(
                (time.perf_counter() - t_all0) * 1000.0
            )
