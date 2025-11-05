import csv
import json
import types
import sys
from pathlib import Path

import pytest


@pytest.fixture()
def temp_cwd(tmp_path, monkeypatch):
    """Isolate working directory to a temp folder so outputs don't leak."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _install_fake_router_module(monkeypatch):
    """
    Install a fake 'app.routers.nl2sql' module into sys.modules
    BEFORE importing evaluate_spider, so its top-level imports resolve.
    """
    # Create package hierarchy: app, app.routers, app.routers.nl2sql
    app_mod = types.ModuleType("app")
    routers_mod = types.ModuleType("app.routers")
    nl2sql_mod = types.ModuleType("app.routers.nl2sql")

    class _FakeExec:
        def derive_schema_preview(self):
            return "TABLE users(id INT);"

    class _FakeResult:
        def __init__(self, ok=True):
            self.ok = ok
            # mix dicts/objects to exercise _to_stage_list normalization
            self.trace = [
                {"stage": "planner", "duration_ms": 11},
                types.SimpleNamespace(stage="generator", duration_ms=23),
                {"stage": "safety", "duration_ms": 5},
            ]

    class _FakePipeline:
        def __init__(self):
            self.executor = _FakeExec()

        def run(self, *, user_query: str, schema_preview: str = ""):
            return _FakeResult(ok=True)

    # exported symbols used by evaluate_spider
    nl2sql_mod._pipeline = _FakePipeline()
    nl2sql_mod._build_pipeline = lambda adapter: _FakePipeline()
    nl2sql_mod._select_adapter = lambda dbid: object()

    # register in sys.modules (package chain)
    sys.modules["app"] = app_mod
    sys.modules["app.routers"] = routers_mod
    sys.modules["app.routers.nl2sql"] = nl2sql_mod


def test_evaluate_spider_writes_outputs(temp_cwd, monkeypatch):
    # 1) install fake router module BEFORE import
    _install_fake_router_module(monkeypatch)

    # 2) import module under test (now its top-level imports succeed)
    import benchmarks.evaluate_spider as mod

    # 3) shrink dataset for speed and redirect outputs into tmp dir
    monkeypatch.setattr(mod, "DATASET", ["q1", "q2"], raising=True)
    out_root = Path("benchmarks") / "results"
    monkeypatch.setattr(mod, "RESULT_ROOT", out_root, raising=True)
    # Recompute RESULT_DIR to reflect new root (keep its naming scheme)
    run_dir = out_root / "test-run"
    monkeypatch.setattr(mod, "RESULT_DIR", run_dir, raising=True)

    # 4) execute main
    mod.main()

    # 5) verify files exist
    jsonl_path = run_dir / "spider_eval.jsonl"
    summary_path = run_dir / "metrics_summary.json"
    csv_path = run_dir / "results.csv"

    assert jsonl_path.exists(), "jsonl not written"
    assert summary_path.exists(), "summary not written"
    assert csv_path.exists(), "csv not written"

    # 6) validate JSONL (2 lines, keys present, normalized trace)
    lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    rec0 = json.loads(lines[0])
    assert set(rec0.keys()) >= {"query", "ok", "latency_ms", "trace", "error"}
    assert isinstance(rec0["ok"], bool)
    assert isinstance(rec0["latency_ms"], int)
    assert isinstance(rec0["trace"], list)
    assert all("stage" in t and "ms" in t for t in rec0["trace"])

    # 7) validate summary.json
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["queries_total"] == 2
    assert 0.0 <= summary["success_rate"] <= 1.0
    assert isinstance(summary["avg_latency_ms"], (int, float))
    assert summary["pipeline_source"] in {"default", "adapter"}  # per code path

    # 8) validate CSV
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert set(rows[0].keys()) == {"query", "ok", "latency_ms"}
    assert rows[0]["ok"] in {"✅", "❌"}
    assert int(rows[0]["latency_ms"]) >= 0


def test_to_stage_list_normalizes_mixed_items(temp_cwd, monkeypatch):
    _install_fake_router_module(monkeypatch)
    import benchmarks.evaluate_spider as mod

    mixed = [
        {"stage": "planner", "duration_ms": 10},
        types.SimpleNamespace(stage="generator", duration_ms=20),
        {"stage": "safety", "duration_ms": "7"},
    ]
    out = mod._to_stage_list(mixed)
    assert out == [
        {"stage": "planner", "ms": 10},
        {"stage": "generator", "ms": 20},
        {"stage": "safety", "ms": 7},
    ]


def test_int_ms_returns_int(temp_cwd, monkeypatch):
    _install_fake_router_module(monkeypatch)
    import benchmarks.evaluate_spider as mod

    # use a small synthetic duration to assert type not magnitude
    t0 = 0.0
    assert isinstance(mod._int_ms(t0), int)
