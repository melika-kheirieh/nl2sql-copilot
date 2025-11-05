from nl2sql.pipeline_factory import (
    pipeline_from_config,
    pipeline_from_config_with_adapter,
)
from adapters.db.sqlite_adapter import SQLiteAdapter


def test_pipeline_from_config_builds_and_runs(tmp_path):
    p = pipeline_from_config("configs/sqlite_pipeline.yaml")
    result = p.run(user_query="Top 3 albums by sales")
    assert result.sql is not None
    assert isinstance(result.traces, list)


def test_pipeline_from_config_with_adapter_override(tmp_path):
    adapter = SQLiteAdapter("data/chinook.db")
    p = pipeline_from_config_with_adapter(
        "configs/sqlite_pipeline.yaml", adapter=adapter
    )
    result = p.run(user_query="Count customers")
    assert "SELECT" in result.sql.upper()
    assert isinstance(result.traces, list)


def test_full_pipeline_from_yaml(monkeypatch):
    from nl2sql.pipeline_factory import pipeline_from_config

    p = pipeline_from_config("configs/sqlite_pipeline.yaml")
    res = p.run(user_query="List all artists")
    assert res.ok
    assert isinstance(res.sql, str)
    assert any(t["stage"] == "executor" for t in res.traces)
