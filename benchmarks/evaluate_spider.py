from __future__ import annotations
import time, json, csv
from pathlib import Path
from tqdm import tqdm

from  app import get_schema_preview, on_generate_query, make_sql_chain
from langchain_community.utilities import SQLDatabase
from benchmarks import load_spider_sqlite


LOG_DIR = Path("logs/spider_eval")
LOG_DIR.mkdir(parents=True, exist_ok=True)

def run_eval(split="dev", limit=20):
    data = load_spider_sqlite(split)
    data = data[:limit]
    print(f"Running eval on {len(data)} examples...")

    results = []
    for ex in tqdm(data):
        db_path = str(ex.db_path)

        schema = get_schema_preview(str(ex.db_path), 0)

        sql_db = SQLDatabase.from_uri(f"sqlite:///{db_path}")
        chain = make_sql_chain(sql_db)

        state = {
            "db_path": db_path,
            "sql_db": sql_db,
            "schema_text": schema,
            "chain": chain,
        }

        msg, sql, output = on_generate_query(ex.question, 1000, state)

        results.append({
            "db_id": ex.db_id,
            "question": ex.question,
            "gold_sql": ex.gold_sql,
            "pred_sql": sql,
            "status": msg,
            "output": output,
        })

        time.sleep(0.3)

    ts = int(time.time())
    out_path = LOG_DIR / f"{split}_results_{ts}.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"Wrote results → {out_path}")

if __name__ == "__main__":
    run_eval("train", 20)