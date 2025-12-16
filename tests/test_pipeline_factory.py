from nl2sql.pipeline_factory import (
    pipeline_from_config,
    pipeline_from_config_with_adapter,
)
from adapters.db.sqlite_adapter import SQLiteAdapter
import yaml
from pathlib import Path
import os

CONFIG_PATH = os.getenv("PIPELINE_CONFIG", "configs/sqlite_pipeline.yaml")
config_path = Path(CONFIG_PATH)


def test_pipeline_from_config_builds_and_runs():
    p = pipeline_from_config(CONFIG_PATH)
    result = p.run(user_query="Top 3 albums by sales")

    assert result.ok is True
    assert result.error is False
    assert result.error_code is None

    assert isinstance(result.sql, str)
    assert isinstance(result.traces, list)


def test_pipeline_from_config_with_adapter_override():
    with config_path.open("r") as f:
        config = yaml.safe_load(f)
    dsn = config.get("adapter", {}).get("dsn")

    adapter = SQLiteAdapter(dsn)
    p = pipeline_from_config_with_adapter(CONFIG_PATH, adapter=adapter)
    result = p.run(user_query="Count customers")

    assert result.ok
    assert result.error_code is None
    assert isinstance(result.sql, str)
    assert isinstance(result.traces, list)


def test_full_pipeline_from_yaml():
    p = pipeline_from_config(CONFIG_PATH)
    res = p.run(user_query="List all artists")

    assert res.ok
    assert res.error is False
    assert isinstance(res.sql, str)

    assert any("executor" in t.get("stage", "") for t in res.traces)
