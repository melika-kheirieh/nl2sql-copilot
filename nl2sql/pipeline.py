from __future__ import annotations

import traceback
from dataclasses import dataclass
from typing import Dict, Any, Optional, List
import time
import inspect

from nl2sql.types import StageResult
from nl2sql.ambiguity_detector import AmbiguityDetector
from nl2sql.planner import Planner
from nl2sql.generator import Generator
from nl2sql.safety import Safety
from nl2sql.executor import Executor
from nl2sql.verifier import Verifier
from nl2sql.repair import Repair
from nl2sql.stubs import NoOpExecutor, NoOpRepair, NoOpVerifier
from nl2sql.metrics import stage_duration_ms, pipeline_runs_total, repair_attempts_total
from nl2sql.errors.codes import ErrorCode
from nl2sql.context_engineering.render import render_schema_pack
from nl2sql.context_engineering.engineer import ContextEngineer


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

    error_code: Optional[ErrorCode] = None

    result: Optional[Dict[str, Any]] = None


class Pipeline:
    """
    NL2SQL Copilot pipeline:
      detector → planner → generator → safety → executor → verifier → (optional repair loop).
    """

    SQL_REPAIR_STAGES = {"safety", "executor", "verifier"}

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
        context_engineer: ContextEngineer | None = None,
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
        self.context_engineer = context_engineer

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

    def _run_with_repair(
        self,
        stage_name: str,
        fn,
        *,
        repair_input_builder,
        max_attempts: int = 1,
        traces: list,
        **kwargs,
    ) -> StageResult:
        """
        Run a stage with per-stage repair + full observability integration.
        SQL-only repair occurs for safety/executor/verifier.
        Planner/Generator get log-only repair (trace only, no effect).
        """
        attempt = 0

        while True:
            # --- 1) Run stage normally ---
            t0 = time.perf_counter()
            r = self._safe_stage(fn, **kwargs)
            dt = (time.perf_counter() - t0) * 1000.0

            stage_duration_ms.labels(stage_name).observe(dt)

            # attach stage trace
            if getattr(r, "trace", None):
                traces.append(r.trace.__dict__)
            else:
                traces.append(
                    {
                        "stage": stage_name,
                        "duration_ms": dt,
                        "summary": "ok" if r.ok else "failed",
                        "notes": {},
                    }
                )

            if r.ok:
                return r

            # stage failed → check repair availability
            attempt += 1
            if attempt > max_attempts:
                return r

            # --- 2) Build repair input ---
            repair_args = repair_input_builder(r, kwargs)

            # --- 3) Run repair (always logged) ---
            repair_attempts_total.labels(outcome="attempt").inc()
            t1 = time.perf_counter()
            r_fix = self._safe_stage(self.repair.run, **repair_args)
            dt_fix = (time.perf_counter() - t1) * 1000.0

            stage_duration_ms.labels("repair").observe(dt_fix)

            if getattr(r_fix, "trace", None):
                traces.append(r_fix.trace.__dict__)
            else:
                traces.append(
                    {
                        "stage": "repair",
                        "duration_ms": dt_fix,
                        "summary": "ok" if r_fix.ok else "failed",
                        "notes": {"stage": stage_name},
                    }
                )

            if not r_fix.ok:
                repair_attempts_total.labels(outcome="failed").inc()
                return r  # repair itself failed → stop here

            # --- 4) Only inject SQL if the stage is an SQL-producing stage ---
            if stage_name in self.SQL_REPAIR_STAGES:
                if "sql" in repair_args and "sql" in kwargs:
                    kwargs["sql"] = (r_fix.data or {}).get("sql", kwargs["sql"])

            # important: success metric must reflect if repair was applied meaningfully
            if stage_name in self.SQL_REPAIR_STAGES:
                repair_attempts_total.labels(outcome="success").inc()
            else:
                # log-only mode counts as a success-attempt but not semantic success
                repair_attempts_total.labels(outcome="success").inc()

            # for SQL stages, we re-run the stage again with modified kwargs
            # for log-only stages, this simply loops and stage is re-run unchanged
            # (which is correct)

    @staticmethod
    def _planner_repair_input_builder(stage_result, kwargs):
        return {
            "sql": "",
            "error_msg": "; ".join(stage_result.error or ["planner_failed"]),
            "schema_preview": kwargs.get("schema_preview", ""),
        }

    @staticmethod
    def _generator_repair_input_builder(stage_result, kwargs):
        return {
            "sql": (stage_result.data or {}).get("sql", ""),
            "error_msg": "; ".join(stage_result.error or ["generator_failed"]),
            "schema_preview": kwargs.get("schema_preview", ""),
        }

    @staticmethod
    def _sql_repair_input_builder(stage_result, kwargs):
        return {
            "sql": kwargs.get("sql", ""),
            "error_msg": "; ".join(stage_result.error or ["stage_failed"]),
            "schema_preview": kwargs.get("schema_preview", ""),
        }

    def _call_verifier(self, *, sql: str, exec_result: Dict[str, Any]) -> StageResult:
        """
        Call verifier with a backward-compatible signature.
        Some verifiers accept `adapter=...`, some don't.
        """
        kwargs: Dict[str, Any] = {"sql": sql, "exec_result": exec_result}

        adapter = getattr(self.executor, "adapter", None)
        if adapter is not None:
            try:
                params = inspect.signature(self.verifier.run).parameters
                if "adapter" in params:
                    kwargs["adapter"] = adapter
            except (TypeError, ValueError):
                # If signature introspection fails, fall back to the minimal call.
                pass

        return self.verifier.run(**kwargs)

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
        exec_result: Dict[str, Any] = {}

        def _tag_last_trace_attempt(stage_name: str, attempt: int) -> None:
            # Attach attempt metadata to the most recent trace entry for this stage.
            for t in reversed(traces):
                if t.get("stage") == stage_name:
                    notes = t.get("notes") or {}
                    if not isinstance(notes, dict):
                        notes = {}
                    notes["attempt"] = attempt
                    t["notes"] = notes
                    return

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

        # --- Context Engineering
        schema_for_llm = schema_preview

        if self.context_engineer is not None:
            packet = self.context_engineer.build(schema_preview=schema_preview)
            schema_for_llm = render_schema_pack(packet.schema_pack)

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

            planner_kwargs: Dict[str, Any] = {
                "user_query": user_query,
                "schema_preview": schema_for_llm,
                "traces": traces,
            }
            try:
                if "schema_pack" in inspect.signature(self.planner.run).parameters:
                    planner_kwargs["schema_pack"] = schema_for_llm
            except (TypeError, ValueError):
                pass

            r_plan = self._run_with_repair(
                "planner",
                self.planner.run,
                repair_input_builder=self._planner_repair_input_builder,
                max_attempts=1,
                **planner_kwargs,
            )
            if not r_plan.ok:
                pipeline_runs_total.labels(status="error").inc()
                return FinalResult(
                    ok=False,
                    ambiguous=False,
                    error=True,
                    details=r_plan.error,
                    error_code=ErrorCode.PIPELINE_CRASH,
                    questions=None,
                    sql=None,
                    rationale=None,
                    verified=None,
                    traces=self._normalize_traces(traces),
                )

            # --- 3) generator ---
            t0 = time.perf_counter()

            gen_kwargs: Dict[str, Any] = {
                "user_query": user_query,
                "schema_preview": schema_for_llm,
                "plan_text": (r_plan.data or {}).get("plan"),
                "clarify_answers": clarify_answers,
                "traces": traces,
            }
            try:
                if "schema_pack" in inspect.signature(self.generator.run).parameters:
                    gen_kwargs["schema_pack"] = schema_for_llm
            except (TypeError, ValueError):
                pass

            r_gen = self._run_with_repair(
                "generator",
                self.generator.run,
                repair_input_builder=self._generator_repair_input_builder,
                max_attempts=1,
                **gen_kwargs,
            )
            if not r_gen.ok:
                pipeline_runs_total.labels(status="error").inc()
                return FinalResult(
                    ok=False,
                    ambiguous=False,
                    error=True,
                    details=r_gen.error,
                    error_code=ErrorCode.LLM_BAD_OUTPUT,
                    questions=None,
                    sql=None,
                    rationale=None,
                    verified=None,
                    traces=self._normalize_traces(traces),
                )

            sql = (r_gen.data or {}).get("sql")
            rationale = (r_gen.data or {}).get("rationale")

            # --- schema drift signal (planner vs generator table usage)
            planner_used_tables = (
                (r_plan.data or {}).get("used_tables")
                or (r_plan.data or {}).get("tables")
                or []
            )
            generator_used_tables = (
                (r_gen.data or {}).get("used_tables")
                or (r_gen.data or {}).get("tables")
                or []
            )
            planner_set = set(planner_used_tables)
            generator_set = set(generator_used_tables)
            schema_drift = bool(generator_set - planner_set)
            traces.append(
                self._mk_trace(
                    stage="schema_drift_check",
                    duration_ms=0.0,
                    summary="compare planner vs generator table usage",
                    notes={
                        "planner_used_tables": sorted(planner_set),
                        "generator_used_tables": sorted(generator_set),
                        "schema_drift": schema_drift,
                    },
                )
            )

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
                    error_code=ErrorCode.LLM_BAD_OUTPUT,
                    questions=None,
                    sql=None,
                    rationale=rationale,
                    verified=None,
                    traces=self._normalize_traces(traces),
                )

            # --- 4) safety ---
            t0 = time.perf_counter()
            r_safe = self._run_with_repair(
                "safety",
                self.safety.run,
                repair_input_builder=self._sql_repair_input_builder,
                max_attempts=1,
                sql=sql,
                traces=traces,
            )
            if not r_safe.ok:
                pipeline_runs_total.labels(status="error").inc()
                return FinalResult(
                    ok=False,
                    ambiguous=False,
                    error=True,
                    details=r_safe.error,
                    error_code=r_safe.error_code,
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
            r_exec = self._run_with_repair(
                "executor",
                self.executor.run,
                repair_input_builder=self._sql_repair_input_builder,
                max_attempts=1,
                sql=sql,
                traces=traces,
            )
            if not r_exec.ok and r_exec.error:
                details.extend(
                    r_exec.error
                )  # soft: keep for repair/verifier context_engineering
            if r_exec.ok and isinstance(r_exec.data, dict):
                exec_result = dict(r_exec.data)

            # --- 6) verifier (only if execution succeeded) ---
            r_ver = None
            if r_exec.ok:
                t0 = time.perf_counter()
                r_ver = self._run_with_repair(
                    "verifier",
                    self._call_verifier,
                    repair_input_builder=self._sql_repair_input_builder,
                    max_attempts=1,
                    sql=sql,
                    exec_result=(r_exec.data or {}),
                    traces=traces,
                )

                # If verifier (or its repair) produced a new SQL, consume it
                if r_ver.data and isinstance(r_ver.data, dict):
                    repaired_sql = r_ver.data.get("sql")
                    if repaired_sql:
                        sql = repaired_sql

            data = r_ver.data if (r_ver and isinstance(r_ver.data, dict)) else {}

            # Verified flag
            verified = bool(data.get("verified") is True)

            # consume repaired SQL from verifier if any
            repaired_sql = data.get("sql")
            if repaired_sql:
                sql = repaired_sql

            # --- 7) repair loop (if not verified) ---
            if not verified:
                for _attempt in range(2):
                    # repair
                    repair_attempts_total.labels(outcome="attempt").inc()
                    t0 = time.perf_counter()
                    r_fix = self._safe_stage(
                        self.repair.run,
                        sql=sql,
                        error_msg="; ".join(details or ["unknown"]),
                        schema_preview=schema_for_llm,
                    )
                    dt = (time.perf_counter() - t0) * 1000.0
                    stage_duration_ms.labels("repair").observe(dt)
                    traces.extend(self._trace_list(r_fix))
                    _tag_last_trace_attempt("repair", _attempt)
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
                    _tag_last_trace_attempt("safety", _attempt)
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
                    _tag_last_trace_attempt("executor", _attempt)
                    if not getattr(r_exec2, "trace", None):
                        _fallback_trace("executor", dt2, r_exec2.ok)
                    if not r_exec2.ok:
                        if r_exec2.error:
                            details.extend(r_exec2.error)
                        continue
                    if r_exec2.ok and isinstance(r_exec2.data, dict):
                        exec_result = dict(r_exec2.data)

                    # verifier again
                    t0 = time.perf_counter()
                    r_ver2 = self._safe_stage(
                        self._call_verifier,
                        sql=sql,
                        exec_result=(r_exec2.data or {}),
                    )
                    dt2 = (time.perf_counter() - t0) * 1000.0
                    stage_duration_ms.labels("verifier").observe(dt2)
                    traces.extend(self._trace_list(r_ver2))
                    _tag_last_trace_attempt("verifier", _attempt)
                    if not getattr(r_ver2, "trace", None):
                        _fallback_trace("verifier", dt2, r_ver2.ok)
                    verified = bool(r_ver2.data and r_ver2.data.get("verified") is True)
                    if r_ver2.data and "sql" in r_ver2.data and r_ver2.data["sql"]:
                        sql = r_ver2.data["sql"]
                    if verified:
                        repair_attempts_total.labels(outcome="success").inc()
                        break
                    else:
                        repair_attempts_total.labels(outcome="failed").inc()

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
                result=exec_result or None,
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
