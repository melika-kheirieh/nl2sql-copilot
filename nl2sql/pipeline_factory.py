import yaml
from typing import Any, Dict
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
from adapters.db.sqlite_adapter import SQLiteAdapter
from adapters.db.postgres_adapter import PostgresAdapter
from adapters.db.base import DBAdapter


def _build_adapter(adapter_cfg: Dict[str, Any]) -> DBAdapter:
    kind = adapter_cfg.get("kind", "sqlite")
    if kind == "sqlite":
        return SQLiteAdapter(adapter_cfg.get("dsn"))
    if kind == "postgres":
        return PostgresAdapter(**adapter_cfg)
    raise ValueError(f"Unknown adapter kind: {kind}")


def pipeline_from_config(path: str) -> Pipeline:
    with open(path, "r", encoding="utf-8") as fh:
        cfg: Dict[str, Any] = yaml.safe_load(fh)

    detector = DETECTORS[cfg.get("detector", "default")]()
    planner = PLANNERS[cfg.get("planner", "default")]()
    generator = GENERATORS[cfg.get("generator", "rules")]()
    safety = SAFETIES[cfg.get("safety", "default")]()
    executor = EXECUTORS[cfg.get("executor", "default")]()
    verifier = VERIFIERS[cfg.get("verifier", "basic")]()
    repair = REPAIRS[cfg.get("repair", "default")]()

    # If your Executor needs an adapter inside, set it there (common pattern):
    adapter_cfg = cfg.get("adapter", {"kind": "sqlite", "dsn": "data/chinook.db"})
    adapter = _build_adapter(adapter_cfg)
    if hasattr(executor, "bind_adapter"):
        executor.bind_adapter(adapter)
    elif hasattr(executor, "adapter"):
        executor.adapter = adapter  # fallback

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
    """Same as pipeline_from_config, but force a specific adapter (per-request override)."""
    with open(path, "r", encoding="utf-8") as fh:
        cfg: Dict[str, Any] = yaml.safe_load(fh)

    detector = DETECTORS[cfg.get("detector", "default")]()
    planner = PLANNERS[cfg.get("planner", "default")]()
    generator = GENERATORS[cfg.get("generator", "rules")]()
    safety = SAFETIES[cfg.get("safety", "default")]()
    executor = EXECUTORS[cfg.get("executor", "default")]()
    verifier = VERIFIERS[cfg.get("verifier", "basic")]()
    repair = REPAIRS[cfg.get("repair", "default")]()

    if hasattr(executor, "bind_adapter"):
        executor.bind_adapter(adapter)
    elif hasattr(executor, "adapter"):
        executor.adapter = adapter

    return Pipeline(
        detector=detector,
        planner=planner,
        generator=generator,
        safety=safety,
        executor=executor,
        verifier=verifier,
        repair=repair,
    )
