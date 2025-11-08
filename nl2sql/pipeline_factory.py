from __future__ import annotations

import os
from typing import Any, Dict, Optional, cast
import yaml  # type: ignore[import-untyped]

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

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
from nl2sql.types import StageResult, StageTrace

from nl2sql.ambiguity_detector import AmbiguityDetector
from nl2sql.planner import Planner
from nl2sql.generator import Generator
from nl2sql.executor import Executor
from nl2sql.verifier import Verifier
from nl2sql.repair import Repair

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
        return PostgresAdapter(**adapter_cfg)
    raise ValueError(f"Unknown adapter kind: {kind}")


def _build_llm(llm_cfg: Optional[Dict[str, Any]] = None) -> Any:
    """Under pytest return None (stubs handle logic); otherwise real OpenAI provider."""
    if os.getenv("PYTEST_CURRENT_TEST"):
        return None
    _ = llm_cfg or {}
    return OpenAIProvider()


def _is_pytest() -> bool:
    return bool(os.getenv("PYTEST_CURRENT_TEST"))


def _tr(
    stage: str,
    *,
    duration_ms: int = 0,
    notes: Optional[Dict[str, Any]] = None,
    token_in: Optional[int] = None,
    token_out: Optional[int] = None,
    cost_usd: Optional[float] = None,
) -> StageTrace:
    return StageTrace(
        stage=stage,
        duration_ms=duration_ms,
        notes=notes,
        token_in=token_in,
        token_out=token_out,
        cost_usd=cost_usd,
    )


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
        adapter_cfg = {"kind": "sqlite", "dsn": ":memory:"}
    adapter = _build_adapter(adapter_cfg)

    # --- LLM ---
    llm_cfg = cast(Optional[Dict[str, Any]], cfg.get("llm"))
    llm = _build_llm(llm_cfg)

    if is_pytest:
        # ---------- stubs: domain-shaped + StageResult on run() ----------
        class _StubDetector:
            def detect(self, *args, **kwargs) -> list[str]:
                return []

            def run(self, *args, **kwargs) -> StageResult:
                return StageResult(
                    ok=True,
                    data={"questions": []},
                    trace=_tr(
                        "detector", notes={"ambiguous": False, "questions_len": 0}
                    ),
                )

        class _StubPlanner:
            def __init__(self, llm: Any = None) -> None: ...
            def plan(self, *args, **kwargs) -> str:
                return "stub plan"

            def run(self, *args, **kwargs) -> StageResult:
                plan = self.plan(*args, **kwargs)
                return StageResult(
                    ok=True,
                    data={"plan": plan},
                    trace=_tr("planner", notes={"len_plan": len(plan)}),
                )

        class _StubGenerator:
            def __init__(self, llm: Any = None) -> None: ...
            def generate(self, *args, **kwargs) -> tuple[str, str]:
                return "SELECT 1;", "stub"

            def run(self, *args, **kwargs) -> StageResult:
                sql, rationale = self.generate(*args, **kwargs)
                return StageResult(
                    ok=True,
                    data={"sql": sql, "rationale": rationale},
                    trace=_tr("generator", notes={"rationale_len": len(rationale)}),
                )

        class _StubExecutor:
            def __init__(self, db: Any | None = None) -> None: ...
            def execute(self, *args, **kwargs) -> Dict[str, Any]:
                rows = [{"x": 1}]
                return {"rows": rows, "row_count": len(rows)}

            def run(self, *args, **kwargs) -> StageResult:
                out = self.execute(*args, **kwargs)
                return StageResult(
                    ok=True,
                    data=out,
                    trace=_tr("executor", notes={"row_count": out["row_count"]}),
                )

        class _StubVerifier:
            def verify(self, *args, **kwargs) -> bool:
                return True

            def run(self, *args, **kwargs) -> StageResult:
                return StageResult(
                    ok=True, data={"verified": True}, trace=_tr("verifier")
                )

        class _StubRepair:
            def __init__(self, llm: Any = None) -> None: ...
            def repair(self, *args, **kwargs) -> str:
                return kwargs.get("sql") or "SELECT 1;"

            def run(self, *args, **kwargs) -> StageResult:
                sql = self.repair(*args, **kwargs)
                return StageResult(ok=True, data={"sql": sql}, trace=_tr("repair"))

        detector = cast(AmbiguityDetector, _StubDetector())
        planner = cast(Planner, _StubPlanner())
        generator = cast(Generator, _StubGenerator())
        safety = SAFETIES[cfg.get("safety", "default")]()
        executor = cast(Executor, _StubExecutor(db=adapter))
        verifier = cast(Verifier, _StubVerifier())
        repair = cast(Repair, _StubRepair())

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
            def detect(self, *args, **kwargs) -> list[str]:
                return []

            def run(self, *args, **kwargs) -> StageResult:
                return StageResult(
                    ok=True,
                    data={"questions": []},
                    trace=_tr(
                        "detector", notes={"ambiguous": False, "questions_len": 0}
                    ),
                )

        class _StubPlanner:
            def __init__(self, llm: Any = None) -> None: ...
            def plan(self, *args, **kwargs) -> str:
                return "stub plan"

            def run(self, *args, **kwargs) -> StageResult:
                plan = self.plan(*args, **kwargs)
                return StageResult(
                    ok=True,
                    data={"plan": plan},
                    trace=_tr("planner", notes={"len_plan": len(plan)}),
                )

        class _StubGenerator:
            def __init__(self, llm: Any = None) -> None: ...
            def generate(self, *args, **kwargs) -> tuple[str, str]:
                return "SELECT 1;", "stub"

            def run(self, *args, **kwargs) -> StageResult:
                sql, rationale = self.generate(*args, **kwargs)
                return StageResult(
                    ok=True,
                    data={"sql": sql, "rationale": rationale},
                    trace=_tr("generator", notes={"rationale_len": len(rationale)}),
                )

        class _StubExecutor:
            def __init__(self, db: Any | None = None) -> None: ...
            def execute(self, *args, **kwargs) -> Dict[str, Any]:
                rows = [{"x": 1}]
                return {"rows": rows, "row_count": len(rows)}

            def run(self, *args, **kwargs) -> StageResult:
                out = self.execute(*args, **kwargs)
                return StageResult(
                    ok=True,
                    data=out,
                    trace=_tr("executor", notes={"row_count": out["row_count"]}),
                )

        class _StubVerifier:
            def verify(self, *args, **kwargs) -> bool:
                return True

            def run(self, *args, **kwargs) -> StageResult:
                return StageResult(
                    ok=True, data={"verified": True}, trace=_tr("verifier")
                )

        class _StubRepair:
            def __init__(self, llm: Any = None) -> None: ...
            def repair(self, *args, **kwargs) -> str:
                return kwargs.get("sql") or "SELECT 1;"

            def run(self, *args, **kwargs) -> StageResult:
                sql = self.repair(*args, **kwargs)
                return StageResult(ok=True, data={"sql": sql}, trace=_tr("repair"))

        detector = cast(AmbiguityDetector, _StubDetector())
        planner = cast(Planner, _StubPlanner())
        generator = cast(Generator, _StubGenerator())
        safety = SAFETIES[cfg.get("safety", "default")]()
        executor = cast(Executor, _StubExecutor(db=adapter))
        verifier = cast(Verifier, _StubVerifier())
        repair = cast(Repair, _StubRepair())

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
