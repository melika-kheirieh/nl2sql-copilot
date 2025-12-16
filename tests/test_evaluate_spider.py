import csv
import json
import sys
import types
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def temp_cwd(tmp_path, monkeypatch):
    """
    Isolate working directory to a temp folder so benchmark outputs
    never leak into the real project tree.
    """
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Fake router module installer
# ---------------------------------------------------------------------------


def _install_fake_router_module(monkeypatch):
    """
    Install a fake 'app.routers.nl2sql' module into sys.modules BEFORE
    importing evaluate_spider, so its top-level imports resolve without
    touching real app code.
    """
    # Create package hierarchy
    app_mod = types.ModuleType("app")
    routers_mod = types.ModuleType("app.routers")
    nl2sql_mod = types.ModuleType("app.routers.nl2sql")

    class _FakeExecutor:
        def derive_schema_preview(self):
            return "TABLE users(id INT);"

    class _FakeResult:
        def __init__(self, ok=True):
            self.ok = ok
            # Mixed dict / object traces to exercise normalization
            self.trace = [
                {"stage": "planner", "duration_ms": 11},
                types.SimpleNamespace(stage="generator", duration_ms=23),
                {"stage": "safety", "duration_ms": 5},
            ]
            self.error = None

    class _FakePipeline:
        def __init__(self):
            self.executor = _FakeExecutor()

        def run(self, *, user_query: str, schema_preview: str = ""):
            return _FakeResult(ok=True)

    # Symbols expected by evaluate_spider
    nl2sql_mod._pipeline = _FakePipeline()
    nl2sql_mod._build_pipeline = lambda adapter: _FakePipeline()
    nl2sql_mod._select_adapter = lambda dbid: object()

    # Register package chain
    sys.modules["app"] = app_mod
    sys.modules["app.routers"] = routers_mod
    sys.modules["app.routers.nl2sql"] = nl2sql_mod


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_evaluate_spider_writes_outputs(temp_cwd, monkeypatch):
    """
    End-to-end smoke test for the Spider evaluation script:
    - runs with a fake pipeline
    - writes JSONL, summary JSON, and CSV
    - validates minimal contracts of each output
    """
    # 1) Install fake router BEFORE import
    _install_fake_router_module(monkeypatch)

    # 2) Import module under test
    import benchmarks.evaluate_spider as mod

    # 3) Shrink dataset and redirect outputs
    monkeypatch.setattr(mod, "DATASET", ["q1", "q2"], raising=True)

    out_root = Path("benchmarks") / "results"
    monkeypatch.setattr(mod, "RESULT_ROOT", out_root, raising=True)

    run_dir = out_root / "test-run"
    monkeypatch.setattr(mod, "RESULT_DIR", run_dir, raising=True)

    # 4) Execute
    mod.main()

    # 5) Files must exist
    jsonl_path = run_dir / "spider_eval.jsonl"
    summary_path = run_dir / "metrics_summary.json"
    csv_path = run_dir / "results.csv"

    assert jsonl_path.exists()
    assert summary_path.exists()
    assert csv_path.exists()

    # 6) Validate JSONL records
    lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    rec = json.loads(lines[0])
    assert {"query", "ok", "latency_ms", "trace", "error"} <= set(rec.keys())
    assert isinstance(rec["ok"], bool)
    assert isinstance(rec["latency_ms"], int)
    assert isinstance(rec["trace"], list)
    assert all("stage" in t and "ms" in t for t in rec["trace"])

    # 7) Validate summary JSON
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["queries_total"] == 2
    assert 0.0 <= summary["success_rate"] <= 1.0
    assert isinstance(summary["avg_latency_ms"], (int, float))
    assert summary["pipeline_source"] in {"default", "adapter"}

    # 8) Validate CSV
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 2
    assert set(rows[0].keys()) == {"query", "ok", "latency_ms"}
    assert rows[0]["ok"] in {"✅", "❌"}
    assert int(rows[0]["latency_ms"]) >= 0


def test_to_stage_list_normalizes_mixed_items(temp_cwd, monkeypatch):
    """
    _to_stage_list must normalize dict- and object-based traces
    into a uniform [{stage, ms}, ...] structure.
    """
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
    """
    _int_ms should return an int when given a valid float start time.
    """
    _install_fake_router_module(monkeypatch)
    import benchmarks.evaluate_spider as mod

    assert isinstance(mod._int_ms(0.0), int)
    assert isinstance(mod._int_ms(12.3), int)
