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
from adapters.metrics.base import Metrics
from adapters.metrics.noop import NoOpMetrics
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
        metrics: Metrics | None = None,
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
        self.metrics: Metrics = metrics or NoOpMetrics()

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
    def _accepts_kwargs(fn) -> bool:
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            return True
        return any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )

    @staticmethod
    def _filter_kwargs(fn, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Make stage calls backward-compatible with older stubs/fakes that don't accept
        extra kwargs like `traces`, `schema_preview`, etc.
        """
        if Pipeline._accepts_kwargs(fn):
            return kwargs
        try:
            sig = inspect.signature(fn)
            allowed = set(sig.parameters.keys())
            return {k: v for k, v in kwargs.items() if k in allowed}
        except (TypeError, ValueError):
            return kwargs

    @staticmethod
    def _safe_stage(fn, **kwargs) -> StageResult:
        try:
            call_kwargs = Pipeline._filter_kwargs(fn, kwargs)
            r = fn(**call_kwargs)
            if isinstance(r, StageResult):
                return r
            return StageResult(ok=True, data=r, trace=None)
        except Exception as e:
            tb = traceback.format_exc()
            return StageResult(ok=False, data=None, trace=None, error=[f"{e}", tb])

    @staticmethod
    def _is_repairable_sql_error(msg: str) -> bool:
        """Allowlist heuristic for SQL errors that are plausibly fixable by rewriting SQL."""
        m = (msg or "").lower()
        patterns = [
            "syntax error",
            "no such table",
            "no such column",
            "ambiguous column",
            "misuse of aggregate",
            "does not exist",  # postgres
            "unknown column",  # mysql-like
        ]
        # sqlite frequently includes: near "X": syntax error
        if 'near "' in m and "syntax error" in m:
            return True
        return any(p in m for p in patterns)

    def _should_repair(self, stage_name: str, r: StageResult) -> tuple[bool, str]:
        # Never repair safety blocks
        if stage_name == "safety":
            return (False, "blocked_by_safety")

        if (
            stage_name == "executor"
            and getattr(r, "error_code", None)
            == ErrorCode.EXECUTOR_COST_GUARDRAIL_BLOCKED
        ):
            return (False, "blocked_by_cost")

        if stage_name not in self.SQL_REPAIR_STAGES:
            return (False, "not_sql_stage")

        if stage_name == "verifier":
            data = r.data if isinstance(r.data, dict) else {}
            if data.get("verified") is False or (r.ok is False):
                return (True, "semantic_failure")

        errs = r.error or []
        if any(isinstance(e, str) and self._is_repairable_sql_error(e) for e in errs):
            return (True, "sql_error_repairable")

        return (False, "not_repairable")

    def _run_with_repair(
        self,
        stage_name: str,
        fn,
        *,
        repair_input_builder,
        max_attempts: int = 1,
        **kwargs,
    ) -> StageResult:
        """
        Run a stage with per-stage repair + full observability integration.
        SQL-only repair occurs for safety/executor/verifier.

        IMPORTANT: `traces` must be provided in kwargs as a list.
        """
        traces = kwargs.get("traces")
        if traces is None or not isinstance(traces, list):
            raise TypeError("_run_with_repair requires `traces` (list) in kwargs")

        attempt = 0

        while True:
            # --- 1) Run stage normally ---
            t0 = time.perf_counter()
            r = self._safe_stage(fn, **kwargs)
            dt = (time.perf_counter() - t0) * 1000.0

            self.metrics.observe_stage_duration_ms(stage=stage_name, dt_ms=dt)

            self.metrics.inc_stage_call(stage=stage_name, ok=r.ok)
            if not r.ok and getattr(r, "error_code", None) is not None:
                self.metrics.inc_stage_error(
                    stage=stage_name,
                    error_code=str(r.error_code),
                )

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

            # --- 1.5) Verifier semantic failure is repairable even if ok=True ---
            if r.ok and stage_name == "verifier":
                data0 = r.data if isinstance(r.data, dict) else {}
                if data0.get("verified") is True:
                    return r
                # ok=True but verified=False → treat as eligible for repair path
                eligible, reason = self._should_repair(stage_name, r)
                if not eligible:
                    self.metrics.inc_repair_attempt(stage=stage_name, outcome="skipped")
                    if traces and isinstance(traces[-1], dict):
                        notes = traces[-1].get("notes") or {}
                        if not isinstance(notes, dict):
                            notes = {}
                        notes["repair_eligible"] = False
                        notes["repair_skip_reason"] = reason
                        traces[-1]["notes"] = notes
                    return r
                # fallthrough into repair branch below

            elif r.ok:
                return r

            # stage failed → check repair availability
            eligible, reason = self._should_repair(stage_name, r)
            if not eligible:
                self.metrics.inc_repair_attempt(stage=stage_name, outcome="skipped")
                # annotate latest stage trace entry
                if traces and isinstance(traces[-1], dict):
                    notes = traces[-1].get("notes") or {}
                    if not isinstance(notes, dict):
                        notes = {}
                    notes["repair_eligible"] = False
                    notes["repair_skip_reason"] = reason
                    traces[-1]["notes"] = notes
                return r

            attempt += 1
            if attempt > max_attempts:
                return r

            # --- 2) Build repair input ---
            repair_args = repair_input_builder(r, kwargs)

            # --- 3) Run repair (always logged) ---
            self.metrics.inc_repair_trigger(stage=stage_name, reason=reason)
            self.metrics.inc_repair_attempt(stage=stage_name, outcome="attempt")
            t1 = time.perf_counter()
            r_fix = self._safe_stage(self.repair.run, **repair_args)
            dt_fix = (time.perf_counter() - t1) * 1000.0

            self.metrics.observe_stage_duration_ms(stage="repair", dt_ms=dt_fix)

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
                self.metrics.inc_repair_attempt(stage=stage_name, outcome="failed")
                return r  # repair itself failed → stop here

            # --- 4) Only inject SQL if the stage is an SQL-producing stage ---
            if stage_name in self.SQL_REPAIR_STAGES:
                if "sql" in repair_args and "sql" in kwargs:
                    kwargs["sql"] = (r_fix.data or {}).get("sql", kwargs["sql"])

            self.metrics.inc_repair_attempt(stage=stage_name, outcome="success")

            # re-run stage with updated kwargs

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

    def _call_verifier(
        self,
        *,
        sql: str,
        exec_result: Dict[str, Any],
        traces: List[dict] | None = None,
    ) -> StageResult:
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
        constraints: list[str] = []

        if self.context_engineer is not None:
            packet = self.context_engineer.build(schema_preview=schema_preview)
            schema_for_llm = render_schema_pack(packet.schema_pack)
            # Optional constraints from context packet
            if hasattr(packet, "constraints") and isinstance(packet.constraints, list):
                constraints = [str(x) for x in packet.constraints]

        try:
            # --- 1) detector ---
            t0 = time.perf_counter()
            questions = self.detector.detect(user_query, schema_preview)
            dt = (time.perf_counter() - t0) * 1000.0
            is_amb = bool(questions)
            self.metrics.observe_stage_duration_ms(stage="detector", dt_ms=dt)
            self.metrics.inc_stage_call(stage="detector", ok=True)
            traces.append(
                self._mk_trace(
                    stage="detector",
                    duration_ms=dt,
                    summary=("ambiguous" if is_amb else "clear"),
                    notes={"ambiguous": is_amb, "questions_len": len(questions or [])},
                )
            )
            if questions:
                self.metrics.inc_pipeline_run(status="ambiguous")
                self.metrics.inc_stage_call(stage="detector", ok=False)
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
                self.metrics.inc_pipeline_run(status="error")
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
            gen_kwargs: Dict[str, Any] = {
                "user_query": user_query,
                "schema_preview": schema_for_llm,
                "plan_text": (r_plan.data or {}).get("plan"),
                "clarify_answers": clarify_answers,
                "traces": traces,
                "constraints": constraints,
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
                self.metrics.inc_pipeline_run(status="error")
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

            # Guard: empty SQL
            if not sql or not str(sql).strip():
                self.metrics.inc_pipeline_run(status="error")
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
            r_safe = self._run_with_repair(
                "safety",
                self.safety.run,
                repair_input_builder=self._sql_repair_input_builder,
                max_attempts=1,
                sql=sql,
                schema_preview=schema_for_llm,
                traces=traces,
            )
            if not r_safe.ok:
                self.metrics.inc_pipeline_run(status="error")
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
            r_exec = self._run_with_repair(
                "executor",
                self.executor.run,
                repair_input_builder=self._sql_repair_input_builder,
                max_attempts=1,
                sql=sql,
                schema_preview=schema_for_llm,
                traces=traces,
            )
            if not r_exec.ok and r_exec.error:
                details.extend(r_exec.error)
            if r_exec.ok and isinstance(r_exec.data, dict):
                exec_result = dict(r_exec.data)

            # --- 6) verifier (only if execution succeeded) ---
            verified = False
            if r_exec.ok:
                r_ver = self._run_with_repair(
                    "verifier",
                    self._call_verifier,
                    repair_input_builder=self._sql_repair_input_builder,
                    max_attempts=1,
                    sql=sql,
                    exec_result=(r_exec.data or {}),
                    schema_preview=schema_for_llm,
                    traces=traces,
                )
                data_v = r_ver.data if isinstance(r_ver.data, dict) else {}
                verified = bool(data_v.get("verified") is True)

            # --- 9) finalize ---
            has_errors = bool(details)
            need_ver = bool(self.require_verification)

            final_ok_by_verifier = bool(verified)
            ok = (
                bool(sql)
                and (not has_errors)
                and (final_ok_by_verifier or not need_ver)
            )
            err = (not ok) and has_errors

            # If verification is NOT required and pipeline is ok, report verified=True
            if not need_ver and ok and not final_ok_by_verifier:
                verified_final = True
            else:
                verified_final = bool(verified)

            self.metrics.inc_pipeline_run(status=("ok" if ok else "error"))

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
            self.metrics.inc_pipeline_run(status="error")
            raise

        finally:
            # Always record total latency, even on early return/exception
            self.metrics.observe_stage_duration_ms(
                stage="pipeline_total", dt_ms=(time.perf_counter() - t_all0) * 1000.0
            )
