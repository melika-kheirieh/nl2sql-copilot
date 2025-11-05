from __future__ import annotations

from typing import Any, Dict, Optional, cast
import yaml

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
from adapters.db.base import DBAdapter
from adapters.db.sqlite_adapter import SQLiteAdapter
from adapters.db.postgres_adapter import PostgresAdapter

# 🔁 Use your real LLM provider here
from adapters.llm.openai_provider import OpenAIProvider  # noqa: F401


# ------------------ helpers ------------------ #
def _require_str(value: Any, *, name: str) -> str:
    if value is None:
        raise ValueError(f"Missing required string config: {name}")
    if not isinstance(value, str):
        raise TypeError(f"Config {name} must be a string, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise ValueError(f"Config {name} cannot be empty")
    return v


def _build_adapter(adapter_cfg: Dict[str, Any]) -> DBAdapter:
    kind = (adapter_cfg.get("kind") or "sqlite").lower()
    if kind == "sqlite":
        dsn = _require_str(adapter_cfg.get("dsn"), name="adapter.dsn")
        return SQLiteAdapter(dsn)
    if kind == "postgres":
        # expect keys like {"kind":"postgres","dsn":"postgresql://..."} OR kwargs your adapter needs
        return PostgresAdapter(**adapter_cfg)
    raise ValueError(f"Unknown adapter kind: {kind}")


def _build_llm(llm_cfg: Optional[Dict[str, Any]] = None) -> Any:
    """
    Create an LLM client/provider instance.
    Adjust this to your real signature (model name, base_url, api_key in env, etc.).
    """
    _ = llm_cfg or {}
    # Example: OpenAIProvider() reads env; or pass model via cfg.
    return OpenAIProvider()


# ------------------ main: config → Pipeline ------------------ #
def pipeline_from_config(path: str) -> Pipeline:
    """
    Build a Pipeline from YAML configuration.
    Inject proper constructor dependencies (llm, db/adapter) to satisfy mypy signatures.
    """
    with open(path, "r", encoding="utf-8") as fh:
        cfg: Dict[str, Any] = yaml.safe_load(fh)

    # Optional sections
    adapter_cfg = cast(Dict[str, Any], cfg.get("adapter", {}))
    llm_cfg = cast(Optional[Dict[str, Any]], cfg.get("llm"))

    # Core deps
    adapter = _build_adapter(adapter_cfg)
    llm = _build_llm(llm_cfg)

    # Instantiate stages with required ctor args
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
    Same as pipeline_from_config, but force a specific adapter (per-request override).
    """
    with open(path, "r", encoding="utf-8") as fh:
        cfg: Dict[str, Any] = yaml.safe_load(fh)

    llm_cfg = cast(Optional[Dict[str, Any]], cfg.get("llm"))
    llm = _build_llm(llm_cfg)

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
