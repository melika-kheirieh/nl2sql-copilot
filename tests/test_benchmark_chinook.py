import json
import sqlite3

import benchmarks.benchmark_chinook as bench
import benchmarks.chinook_subset as dataset


def test_ensure_chinook_subset_db_creates_tables(tmp_path):
    db_path = tmp_path / "chinook.sqlite"
    dataset.ensure_chinook_subset_db(db_path)

    assert db_path.exists()

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("SELECT COUNT(*) FROM customers")
        customers = cur.fetchone()[0]
        assert customers == 4

        cur = conn.execute("SELECT COUNT(*) FROM albums")
        albums = cur.fetchone()[0]
        assert albums == 4
    finally:
        conn.close()


def test_run_benchmark_writes_artifacts(tmp_path, monkeypatch):
    # redirect dataset DB to the temp directory to avoid polluting repo state
    db_path = tmp_path / "chinook.sqlite"
    monkeypatch.setattr(dataset, "DEFAULT_DB_PATH", db_path, raising=True)
    monkeypatch.setattr(bench, "DEFAULT_DB_PATH", db_path, raising=True)

    out_root = tmp_path / "results"
    summary = bench.run_benchmark(limit=3, output_root=out_root, provider="local")

    latest = out_root / "latest"
    assert (latest / "summary.json").exists()
    assert (latest / "summary.csv").exists()
    assert (latest / "benchmark.jsonl").exists()
    assert (latest / "latency.svg").exists()

    summary_json = json.loads((latest / "summary.json").read_text())
    assert summary_json["dataset_size"] == 3
    assert summary["exec_accuracy"] >= 0.0
