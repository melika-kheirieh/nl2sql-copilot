# benchmarks/run.py
from __future__ import annotations
import argparse
import os
import json
import time
from pathlib import Path

# ---- app imports
from nl2sql.pipeline import Pipeline
from nl2sql.ambiguity_detector import AmbiguityDetector
from nl2sql.planner import Planner
from nl2sql.generator import Generator
from nl2sql.safety import Safety
from nl2sql.executor import Executor
from nl2sql.verifier import Verifier
from nl2sql.repair import Repair

# ---- adapters
from adapters.db.sqlite_adapter import SQLiteAdapter
from adapters.llm.openai_provider import OpenAIProvider

# ---- fallbacks: Dummy LLM (so it runs without API keys)
class DummyLLM:
    provider_id = "dummy-llm"

    def plan(self, *, user_query: str, schema_preview: str):
        text = f"- understand question: {user_query}\n- identify tables\n- join if needed\n- filter\n- order/limit"
        return text, 0, 0, 0.0

    def generate_sql(self, *, user_query: str, schema_preview: str, plan_text: str, clarify_answers=None):
        # naive demo SQL (so pipeline flows end-to-end)
        sql = "SELECT 1 AS one;"
        rationale = "Demo SQL from DummyLLM"
        return sql, rationale, 0, 0, 0.0

    def repair(self, *, sql: str, error_msg: str, schema_preview: str):
        return sql, 0, 0, 0.0


def ensure_demo_db(path: Path) -> None:
    """Create a tiny SQLite db if missing, so executor has something to run."""
    if path.exists():
        return
    import sqlite3
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT, spend REAL);")
    cur.executemany("INSERT INTO users(id,name,spend) VALUES(?,?,?)",
                    [(1,"Alice",120.5),(2,"Bob",80.0),(3,"Carol",155.0)])
    con.commit()
    con.close()


def build_pipeline(db_path: Path, use_openai: bool) -> Pipeline:
    # DB adapter
    db = SQLiteAdapter(str(db_path))
    executor = Executor(db)
    # LLM provider
    if use_openai and os.getenv("OPENAI_API_KEY"):
        llm = OpenAIProvider()
    else:
        llm = DummyLLM()
    # stages
    detector = AmbiguityDetector()
    planner = Planner(llm)
    generator = Generator(llm)
    safety = Safety()
    verifier = Verifier()
    repair = Repair(llm)
    # pipeline
    return Pipeline(
        detector=detector,
        planner=planner,
        generator=generator,
        safety=safety,
        executor=executor,
        verifier=verifier,
        repair=repair,
    )


def run_benchmark(queries, schema_preview, pipeline: Pipeline, outfile: Path):
    results = []
    for q in queries:
        t0 = time.perf_counter()
        r = pipeline.run(user_query=q, schema_preview=schema_preview)
        latency_ms = (time.perf_counter()-t0)*1000
        ok = (not r.get("ambiguous")) and ("error" not in r)

        traces = r.get("traces", [])
        cost_sum = 0.0
        for t in traces:
            try:
                cost_sum += float(t.get("cost_usd", 0.0))
            except Exception:
                pass

        results.append({
            "query": q,
            "exec_acc": 1.0 if ok else 0.0,
            "safe_fail": 0.0 if ok else 1.0 if "unsafe" in str(r).lower() else 0.0,
            "latency_ms": latency_ms,
            "cost_usd": cost_sum,
            "repair_attempts": sum(1 for t in traces if t.get("stage") == "repair"),
            "provider": pipeline.generator.llm.provider_id if hasattr(pipeline.generator, "llm") else "unknown",
        })

    outfile.parent.mkdir(parents=True, exist_ok=True)
    with open(outfile, "w") as f:
        for row in results:
            f.write(json.dumps(row) + "\n")
    print(f"[OK] wrote {len(results)} rows → {outfile}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--outfile", default="benchmarks/results/demo.jsonl")
    parser.add_argument("--db", default="data/bench_demo.db")
    parser.add_argument("--use-openai", action="store_true", help="Use OpenAI provider if API key present")
    args = parser.parse_args()

    ROOT = Path(__file__).resolve().parents[1]   # project root
    outfile = (ROOT / args.outfile).resolve()
    db_path = (ROOT / args.db).resolve()

    ensure_demo_db(db_path)
    pipe = build_pipeline(db_path, use_openai=args.use_openai)

    # a small demo set; replace with Spider when ready
    queries = [
        "show all users",
        "top spenders",
        "sum of spend",
    ]
    schema_preview = "CREATE TABLE users(id INT, name TEXT, spend REAL);"

    run_benchmark(queries, schema_preview, pipe, outfile)


if __name__ == "__main__":
    main()
