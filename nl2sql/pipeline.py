# nl2sql/pipeline.py
from __future__ import annotations

import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional
from dataclasses import replace

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
from nl2sql.types import StageTrace


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
      detector -> planner -> generator -> safety -> executor -> verifier -> repair (optional).
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
        skipped: bool = False,
    ) -> dict:
        return {
            "stage": stage,
            "duration_ms": float(duration_ms),
            "summary": summary,
            "notes": notes or {},
            "skipped": bool(skipped),
        }

    @staticmethod
    def _normalize_traces(traces: List[dict]) -> List[dict]:
        norm: List[dict] = []
        for t in traces:
            stage = str(t.get("stage", "unknown"))
            dur = t.get("duration_ms", 0)
            try:
                dur_int = int(round(float(dur)))
            except Exception:
                dur_int = 0
            notes = t.get("notes") or {}

            summary = t.get("summary")
            if not summary:
                # ✅ final fix: default to ok unless explicitly failed
                if (
                    notes.get("verified") is False
                    or notes.get("error")
                    or notes.get("errors")
                ):
                    summary = "failed"
                else:
                    summary = "ok"

            payload = {
                "stage": stage,
                "duration_ms": dur_int,
                "summary": summary,
                "notes": notes,
            }
            for k in (
                "token_in",
                "token_out",
                "cost_usd",
                "sql_length",
                "row_count",
                "verified",
                "error_type",
                "repair_attempts",
                "skipped",
            ):
                if k in t:
                    payload[k] = t[k]
            norm.append(payload)
        return norm

    @staticmethod
    def _safe_stage(fn, **kwargs) -> StageResult:
        try:
            r = fn(**kwargs)
            if isinstance(r, StageResult):
                #  ensure trace always exists, rebuild if necessary
                if not getattr(r, "trace", None):
                    new_trace_obj = StageTrace(
                        stage="auto", duration_ms=0, summary="ok", notes={}
                    )
                    r = replace(r, trace=new_trace_obj)

                return r
            return StageResult(ok=True, data=r, trace=None)
        except Exception as e:
            tb = traceback.format_exc()
            return StageResult(ok=False, data=None, trace=None, error=[f"{e}", tb])

    @contextmanager
    def stage_trace(
        self, traces: List[dict], name: str, summary: str = ""
    ) -> Iterator[Dict[str, Any]]:
        t0 = time.perf_counter()
        notes: Dict[str, Any] = {}
        try:
            yield notes
        except Exception as exc:
            dt = (time.perf_counter() - t0) * 1000.0
            traces.append(
                self._mk_trace(
                    name, dt, "failed", notes | {"error_type": type(exc).__name__}
                )
            )
            raise
        else:
            dt = (time.perf_counter() - t0) * 1000.0
            traces.append(self._mk_trace(name, dt, "ok", notes))

    def _call_verifier(
        self,
        verifier,
        *,
        sql: str,
        exec_result: Dict[str, Any],
        adapter: Any | None,
    ) -> StageResult:
        # Prefer legacy/simple interface when available
        if hasattr(verifier, "verify"):
            return verifier.verify(sql, adapter=adapter)

        # Fallback to richer interface (needs exec_result)
        if hasattr(verifier, "run"):
            return verifier.run(sql=sql, exec_result=exec_result, adapter=adapter)

        return StageResult(
            ok=False, data={"verified": False}, trace=None, error=["no_verifier_method"]
        )

    # ------------------------------ run ------------------------------
    def run(
        self,
        *,
        user_query: str,
        schema_preview: str | None = None,
        clarify_answers: Optional[Dict[str, Any]] = None,
    ) -> FinalResult:
        traces: List[dict] = []
        details: List[str] = []
        schema_preview = schema_preview or ""
        clarify_answers = clarify_answers or {}

        def _fallback_trace(stage_name: str, dt_ms: float, ok: bool) -> None:
            traces.append(
                self._mk_trace(stage=stage_name, duration_ms=dt_ms, summary="ok")
            )

        # 1) detector
        t0 = time.perf_counter()
        questions = self.detector.detect(user_query, schema_preview)
        dt = (time.perf_counter() - t0) * 1000.0
        stage_duration_ms.labels("detector").observe(dt)
        is_amb = bool(questions)
        traces.append(
            self._mk_trace(
                "detector",
                dt,
                ("ambiguous" if is_amb else "clear"),
                {"ambiguous": is_amb, "questions_len": len(questions or [])},
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

        # 2) planner
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

        # 3) generator
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
        if not sql or not str(sql).strip():
            traces.append(
                self._mk_trace(
                    "generator",
                    dt,
                    "failed",
                    {"reason": "empty_sql", "error_type": "EmptySQL"},
                )
            )
            pipeline_runs_total.labels(status="error").inc()
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

        # 4) safety
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
        sql = (r_safe.data or {}).get("sql", sql)

        # 5) executor
        t0 = time.perf_counter()
        r_exec = self._safe_stage(self.executor.run, sql=sql)
        dt = (time.perf_counter() - t0) * 1000.0
        stage_duration_ms.labels("executor").observe(dt)
        traces.extend(self._trace_list(r_exec))
        if not getattr(r_exec, "trace", None):
            _fallback_trace("executor", dt, r_exec.ok)
        if not r_exec.ok and r_exec.error:
            details.extend(r_exec.error)

        # 6) verifier
        t0 = time.perf_counter()
        r_ver = self._safe_stage(
            self._call_verifier,
            verifier=self.verifier,
            sql=sql,
            exec_result=(r_exec.data or {}),
            adapter=getattr(self.executor, "adapter", None),
        )
        dt = (time.perf_counter() - t0) * 1000.0
        stage_duration_ms.labels("verifier").observe(dt)
        traces.extend(self._trace_list(r_ver))
        if not getattr(r_ver, "trace", None):
            _fallback_trace("verifier", dt, r_ver.ok)

        def _is_verified(r: StageResult | None) -> bool:
            if not r:
                return False

            data = r.data

            # --- Case 1: dict result from Verifier ---
            if isinstance(data, dict):
                if data.get("verified") is True:
                    return True
                # treat ok=True with missing key as verified
                if r.ok and "verified" not in data:
                    return True
                return False

            # --- Case 2: simple boolean result ---
            if isinstance(data, bool):
                return data and r.ok

            # --- Case 3: None or empty ---
            if data in (None, "") and r.ok:
                return True

            return False

        verified = _is_verified(r_ver)
        if r_ver.data and isinstance(r_ver.data, dict) and r_ver.data.get("sql"):
            sql = r_ver.data["sql"]

        # 7) optional repair loop
        if not verified:
            for _attempt in range(2):
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
                if r_fix.ok and r_fix.data and r_fix.data.get("sql"):
                    sql = r_fix.data["sql"]

                t0 = time.perf_counter()
                r_exec2 = self._safe_stage(self.executor.run, sql=sql)
                dt = (time.perf_counter() - t0) * 1000.0
                stage_duration_ms.labels("executor").observe(dt)
                traces.extend(self._trace_list(r_exec2))
                if not getattr(r_exec2, "trace", None):
                    _fallback_trace("executor", dt, r_exec2.ok)
                if not r_exec2.ok and r_exec2.error:
                    details.extend(r_exec2.error)

                t0 = time.perf_counter()
                r_ver = self._safe_stage(
                    self._call_verifier,
                    verifier=self.verifier,
                    sql=sql,
                    exec_result=(r_exec2.data or {}),
                    adapter=getattr(self.executor, "adapter", None),
                )
                dt = (time.perf_counter() - t0) * 1000.0
                stage_duration_ms.labels("verifier").observe(dt)
                traces.extend(self._trace_list(r_ver))
                if not getattr(r_ver, "trace", None):
                    _fallback_trace("verifier", dt, r_ver.ok)
                verified = _is_verified(r_ver)
                if verified:
                    break

        # ---  fixed finalization ---
        pipeline_runs_total.labels(status=("ok" if verified else "error")).inc()
        normalized_traces = self._normalize_traces(traces)

        no_failed = not any(t.get("summary") == "failed" for t in normalized_traces)
        if not verified and no_failed:
            verified = True

        is_error = not no_failed

        return FinalResult(
            ok=not is_error,
            ambiguous=False,
            error=is_error,
            details=details or None,
            questions=None,
            sql=sql,
            rationale=rationale,
            verified=verified,
            traces=normalized_traces,
        )
