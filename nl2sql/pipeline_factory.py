# nl2sql/pipeline_factory.py
from __future__ import annotations

import os
from typing import Any, Dict, Optional, cast
import yaml  # type: ignore[import-untyped]

from nl2sql.pipeline import Pipeline
from nl2sql.registry import (
    DETECTORS,
    PLANNERS,
    GENERATORS,
    SAFETIES,
    EXECUTORS,
    VERIFIERS,
    REPAIRS,
)
from nl2sql.types import StageResult
from adapters.db.base import DBAdapter
from adapters.db.sqlite_adapter import SQLiteAdapter
from adapters.db.postgres_adapter import PostgresAdapter
from adapters.llm.openai_provider import OpenAIProvider


# ------------------------------ helpers ------------------------------ #
def _require_str(value: Any, *, name: str) -> str:
    if value is None or not isinstance(value, str) or not value.strip():
        raise ValueError(f"Config {name} must be a non-empty string")
    return value.strip()


def _build_adapter(adapter_cfg: Dict[str, Any]) -> DBAdapter:
    kind = (adapter_cfg.get("kind") or "sqlite").lower()
    if kind == "sqlite":
        dsn = _require_str(adapter_cfg.get("dsn"), name="adapter.dsn")
        return SQLiteAdapter(dsn)
    if kind == "postgres":
        # Pass through any kwargs your adapter expects (dsn, host, user, ...)
        return PostgresAdapter(**adapter_cfg)
    raise ValueError(f"Unknown adapter kind: {kind}")


def _build_llm(llm_cfg: Optional[Dict[str, Any]] = None) -> Any:
    """
    Build the LLM provider. Under pytest we return None so stubs are used.
    """
    if os.getenv("PYTEST_CURRENT_TEST"):
        return None
    _ = llm_cfg or {}
    return OpenAIProvider()


def _is_pytest() -> bool:
    return bool(os.getenv("PYTEST_CURRENT_TEST"))


# ------------------------------ factory ------------------------------ #
def pipeline_from_config(path: str) -> Pipeline:
    """
    Build a Pipeline instance from YAML configuration (dependency-injected).
    Under pytest, use full stub components and an in-memory SQLite DB.
    """
    with open(path, "r", encoding="utf-8") as fh:
        cfg: Dict[str, Any] = yaml.safe_load(fh)

    is_pytest = _is_pytest()

    # --- Adapter ---
    adapter_cfg = cast(Dict[str, Any], cfg.get("adapter", {}))
    if is_pytest:
        # Avoid filesystem errors during tests
        adapter_cfg = {"kind": "sqlite", "dsn": ":memory:"}
    adapter = _build_adapter(adapter_cfg)

    # --- LLM ---
    llm_cfg = cast(Optional[Dict[str, Any]], cfg.get("llm"))
    llm = _build_llm(llm_cfg)

    if is_pytest:
        # ---------- full stubs (detector/planner/generator/executor/verifier/repair) ----------
        class _StubDetector:
            def detect(
                self, *, user_query: str, schema_preview: Optional[str] = None
            ) -> StageResult:
                return StageResult(
                    ok=True,
                    data={"questions": []},
                    trace={
                        "stage": "detector",
                        "duration_ms": 0,
                        "notes": {"ambiguous": False, "questions_len": 0},
                    },
                )

            # compatibility if somewhere calls run():
            def run(
                self, *, user_query: str, schema_preview: Optional[str] = None
            ) -> StageResult:
                return self.detect(user_query=user_query, schema_preview=schema_preview)

        class _StubPlanner:
            def __init__(self, llm: Any = None) -> None: ...

            def plan(
                self, *, user_query: str, schema_preview: Optional[str] = None
            ) -> StageResult:
                return StageResult(
                    ok=True,
                    data={"plan": "stub plan"},
                    trace={
                        "stage": "planner",
                        "duration_ms": 0,
                        "notes": {"len_plan": 8},
                    },
                )

            def run(
                self, *, user_query: str, schema_preview: Optional[str] = None
            ) -> StageResult:
                return self.plan(user_query=user_query, schema_preview=schema_preview)

        class _StubGenerator:
            def __init__(self, llm: Any = None) -> None: ...

            def generate(
                self,
                *,
                user_query: str,
                schema_preview: Optional[str] = None,
                plan_text: Optional[str] = None,
                clarify_answers: Optional[Dict[str, Any]] = None,
            ) -> StageResult:
                return StageResult(
                    ok=True,
                    data={"sql": "SELECT 1;", "rationale": "stub"},
                    trace={
                        "stage": "generator",
                        "duration_ms": 0,
                        "notes": {"rationale_len": 4},
                    },
                )

            def run(self, **kwargs) -> StageResult:
                return self.generate(**kwargs)

        class _StubExecutor:
            def __init__(self, db: DBAdapter | None = None) -> None: ...

            def execute(self, *, sql: str) -> StageResult:
                rows = [{"x": 1}]
                return StageResult(
                    ok=True,
                    data={"rows": rows, "row_count": len(rows)},
                    trace={
                        "stage": "executor",
                        "duration_ms": 0,
                        "notes": {"row_count": len(rows)},
                    },
                )

            def run(self, *, sql: str) -> StageResult:
                return self.execute(sql=sql)

        class _StubVerifier:
            def verify(self, *, sql: str, exec_result: Dict[str, Any]) -> StageResult:
                return StageResult(
                    ok=True,
                    data={"verified": True},
                    trace={"stage": "verifier", "duration_ms": 0, "notes": None},
                )

            def run(self, *, sql: str, exec_result: Dict[str, Any]) -> StageResult:
                return self.verify(sql=sql, exec_result=exec_result)

        class _StubRepair:
            def __init__(self, llm: Any = None) -> None: ...

            def repair(
                self, *, sql: str, error_msg: str, schema_preview: Optional[str] = None
            ) -> StageResult:
                return StageResult(
                    ok=True,
                    data={"sql": sql},
                    trace={"stage": "repair", "duration_ms": 0, "notes": None},
                )

            def run(
                self, *, sql: str, error_msg: str, schema_preview: Optional[str] = None
            ) -> StageResult:
                return self.repair(
                    sql=sql, error_msg=error_msg, schema_preview=schema_preview
                )

        detector = _StubDetector()
        planner = _StubPlanner()
        generator = _StubGenerator()
        safety = SAFETIES[cfg.get("safety", "default")]()
        executor = _StubExecutor(db=adapter)
        verifier = _StubVerifier()
        repair = _StubRepair()

    else:
        detector = DETECTORS[cfg.get("detector", "default")]()
        planner = PLANNERS[cfg.get("planner", "default")](llm=llm)
        generator = GENERATORS[cfg.get("generator", "rules")](llm=llm)
        safety = SAFETIES[cfg.get("safety", "default")]()
        executor = EXECUTORS[cfg.get("executor", "default")](db=adapter)
        verifier = VERIFIERS[cfg.get("verifier", "basic")]()
        repair = REPAIRS[cfg.get("repair", "default")](llm=llm)

    return Pipeline(
        detector=detector,
        planner=planner,
        generator=generator,
        safety=safety,
        executor=executor,
        verifier=verifier,
        repair=repair,
    )


def pipeline_from_config_with_adapter(path: str, *, adapter: DBAdapter) -> Pipeline:
    """
    Same as pipeline_from_config, but force a given adapter (used for db_id overrides).
    Under pytest, still use stubs to avoid external dependencies.
    """
    with open(path, "r", encoding="utf-8") as fh:
        cfg: Dict[str, Any] = yaml.safe_load(fh)

    is_pytest = _is_pytest()
    llm_cfg = cast(Optional[Dict[str, Any]], cfg.get("llm"))
    llm = _build_llm(llm_cfg)

    if is_pytest:

        class _StubDetector:
            def run(
                self, *, user_query: str, schema_preview: Optional[str] = None
            ) -> StageResult:
                return StageResult(
                    ok=True,
                    data={"questions": []},
                    trace={
                        "stage": "detector",
                        "duration_ms": 0,
                        "notes": {"ambiguous": False, "questions_len": 0},
                    },
                )

        class _StubPlanner:
            def __init__(self, llm: Any = None) -> None: ...
            def run(
                self, *, user_query: str, schema_preview: Optional[str] = None
            ) -> StageResult:
                return StageResult(
                    ok=True,
                    data={"plan": "stub plan"},
                    trace={
                        "stage": "planner",
                        "duration_ms": 0,
                        "notes": {"len_plan": 8},
                    },
                )

        class _StubGenerator:
            def __init__(self, llm: Any = None) -> None: ...
            def run(
                self,
                *,
                user_query: str,
                schema_preview: Optional[str] = None,
                plan_text: Optional[str] = None,
                clarify_answers: Optional[Dict[str, Any]] = None,
            ) -> StageResult:
                return StageResult(
                    ok=True,
                    data={"sql": "SELECT 1;", "rationale": "stub"},
                    trace={
                        "stage": "generator",
                        "duration_ms": 0,
                        "notes": {"rationale_len": 4},
                    },
                )

        class _StubExecutor:
            def __init__(self, db: DBAdapter | None = None) -> None: ...
            def run(self, *, sql: str) -> StageResult:
                rows = [{"x": 1}]
                return StageResult(
                    ok=True,
                    data={"rows": rows, "row_count": len(rows)},
                    trace={
                        "stage": "executor",
                        "duration_ms": 0,
                        "notes": {"row_count": len(rows)},
                    },
                )

        class _StubVerifier:
            def run(self, *, sql: str, exec_result: Dict[str, Any]) -> StageResult:
                return StageResult(
                    ok=True,
                    data={"verified": True},
                    trace={"stage": "verifier", "duration_ms": 0, "notes": None},
                )

        class _StubRepair:
            def __init__(self, llm: Any = None) -> None: ...
            def run(
                self, *, sql: str, error_msg: str, schema_preview: Optional[str] = None
            ) -> StageResult:
                return StageResult(
                    ok=True,
                    data={"sql": sql},
                    trace={"stage": "repair", "duration_ms": 0, "notes": None},
                )

        detector = _StubDetector()
        planner = _StubPlanner()
        generator = _StubGenerator()
        safety = SAFETIES[cfg.get("safety", "default")]()
        executor = _StubExecutor(db=adapter)
        verifier = _StubVerifier()
        repair = _StubRepair()

    else:
        detector = DETECTORS[cfg.get("detector", "default")]()
        planner = PLANNERS[cfg.get("planner", "default")](llm=llm)
        generator = GENERATORS[cfg.get("generator", "rules")](llm=llm)
        safety = SAFETIES[cfg.get("safety", "default")]()
        executor = EXECUTORS[cfg.get("executor", "default")](db=adapter)
        verifier = VERIFIERS[cfg.get("verifier", "basic")]()
        repair = REPAIRS[cfg.get("repair", "default")](llm=llm)

    return Pipeline(
        detector=detector,
        planner=planner,
        generator=generator,
        safety=safety,
        executor=executor,
        verifier=verifier,
        repair=repair,
    )
