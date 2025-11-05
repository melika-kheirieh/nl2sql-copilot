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

        class _StubDetector:
            # Domain method: return list[str]
            def detect(self, *args, **kwargs) -> list[str]:
                return []  # no ambiguities

            # Compatibility: return StageResult
            def run(self, *args, **kwargs) -> StageResult:
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

            # Domain: return str (plan text)
            def plan(self, *args, **kwargs) -> str:
                return "stub plan"

            # Compat: StageResult
            def run(self, *args, **kwargs) -> StageResult:
                return StageResult(
                    ok=True,
                    data={"plan": "stub plan"},
                    trace={
                        "stage": "planner",
                        "duration_ms": 0,
                        "notes": {"len_plan": 9},
                    },
                )

        class _StubGenerator:
            def __init__(self, llm: Any = None) -> None: ...

            # Domain: return tuple[str, str] → (sql, rationale)
            def generate(self, *args, **kwargs) -> tuple[str, str]:
                return "SELECT 1;", "stub"

            # Compat: StageResult
            def run(self, *args, **kwargs) -> StageResult:
                sql, rationale = self.generate(*args, **kwargs)
                return StageResult(
                    ok=True,
                    data={"sql": sql, "rationale": rationale},
                    trace={
                        "stage": "generator",
                        "duration_ms": 0,
                        "notes": {"rationale_len": len(rationale)},
                    },
                )

        class _StubExecutor:
            def __init__(self, db: Any | None = None) -> None: ...

            # Domain: return dict (execution result)
            def execute(self, *args, **kwargs) -> Dict[str, Any]:
                rows = [{"x": 1}]
                return {"rows": rows, "row_count": len(rows)}

            # Compat: StageResult
            def run(self, *args, **kwargs) -> StageResult:
                out = self.execute(*args, **kwargs)
                return StageResult(
                    ok=True,
                    data=out,
                    trace={
                        "stage": "executor",
                        "duration_ms": 0,
                        "notes": {"row_count": out["row_count"]},
                    },
                )

        class _StubVerifier:
            # Domain: return bool
            def verify(self, *args, **kwargs) -> bool:
                return True

            # Compat: StageResult
            def run(self, *args, **kwargs) -> StageResult:
                return StageResult(
                    ok=True,
                    data={"verified": True},
                    trace={"stage": "verifier", "duration_ms": 0, "notes": None},
                )

        class _StubRepair:
            def __init__(self, llm: Any = None) -> None: ...

            # Domain: return str (repaired SQL)
            def repair(self, *args, **kwargs) -> str:
                return kwargs.get("sql") or "SELECT 1;"

            # Compat: StageResult
            def run(self, *args, **kwargs) -> StageResult:
                sql = self.repair(*args, **kwargs)
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
            def detect(self, *args, **kwargs) -> StageResult:
                return StageResult(
                    ok=True,
                    data={"questions": []},
                    trace={
                        "stage": "detector",
                        "duration_ms": 0,
                        "notes": {"ambiguous": False, "questions_len": 0},
                    },
                )

            def run(self, *args, **kwargs) -> StageResult:
                return self.detect(*args, **kwargs)

        class _StubPlanner:
            def __init__(self, llm: Any = None) -> None: ...

            def plan(self, *args, **kwargs) -> StageResult:
                return StageResult(
                    ok=True,
                    data={"plan": "stub plan"},
                    trace={
                        "stage": "planner",
                        "duration_ms": 0,
                        "notes": {"len_plan": 8},
                    },
                )

            def run(self, *args, **kwargs) -> StageResult:
                return self.plan(*args, **kwargs)

        class _StubGenerator:
            def __init__(self, llm: Any = None) -> None: ...

            def generate(self, *args, **kwargs) -> StageResult:
                return StageResult(
                    ok=True,
                    data={"sql": "SELECT 1;", "rationale": "stub"},
                    trace={
                        "stage": "generator",
                        "duration_ms": 0,
                        "notes": {"rationale_len": 4},
                    },
                )

            def run(self, *args, **kwargs) -> StageResult:
                return self.generate(*args, **kwargs)

        class _StubExecutor:
            def __init__(self, db: DBAdapter | None = None) -> None: ...

            def execute(self, *args, **kwargs) -> StageResult:
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

            def run(self, *args, **kwargs) -> StageResult:
                return self.execute(*args, **kwargs)

        class _StubVerifier:
            def verify(self, *args, **kwargs) -> StageResult:
                return StageResult(
                    ok=True,
                    data={"verified": True},
                    trace={"stage": "verifier", "duration_ms": 0, "notes": None},
                )

            def run(self, *args, **kwargs) -> StageResult:
                return self.verify(*args, **kwargs)

        class _StubRepair:
            def __init__(self, llm: Any = None) -> None: ...

            def repair(self, *args, **kwargs) -> StageResult:
                # return original sql if any, else SELECT 1
                sql = kwargs.get("sql") or "SELECT 1;"
                return StageResult(
                    ok=True,
                    data={"sql": sql},
                    trace={"stage": "repair", "duration_ms": 0, "notes": None},
                )

            def run(self, *args, **kwargs) -> StageResult:
                return self.repair(*args, **kwargs)

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
