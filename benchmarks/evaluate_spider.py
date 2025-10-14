from __future__ import annotations
import time, json, subprocess
from pathlib import Path
from tqdm import tqdm

from app import get_schema_preview, on_generate_query, make_sql_chain
from langchain_community.utilities import SQLDatabase
from benchmarks import load_spider_sqlite

from sqlglot import parse_one, exp
from sqlglot.errors import ParseError

LOG_DIR = Path("logs/spider_eval")
LOG_DIR.mkdir(parents=True, exist_ok=True)

def normalize_sql(sql: str) -> str:
    # نسخه ساده؛ می‌تونی قوی‌ترش کنی با پارس + بازسازی
    return " ".join(sql.lower().strip().split())

def compare_results(pred_rows, gold_rows):
    if pred_rows is None or gold_rows is None:
        return False
    # اگر ترتیب مهم نیست
    return set(pred_rows) == set(gold_rows)

def try_execute_sql(sql_db, sql, timeout: float = None):
    start = time.time()
    try:
        rows = sql_db.run(sql)
        return rows, time.time() - start, None
    except Exception as e:
        return None, time.time() - start, str(e)

def exact_match_structural(sql_pred: str, sql_gold: str) -> bool:
    try:
        ast_pred = parse_one(sql_pred)
        ast_gold = parse_one(sql_gold)
    except Exception:
        return False

    def normalize_ast(node: exp.Expression):
        for name, arg in node.args.items():
            if isinstance(arg, list):
                arg.sort(key=lambda x: str(x))
                for child in arg:
                    normalize_ast(child)
            elif isinstance(arg, exp.Expression):
                normalize_ast(arg)
        if isinstance(node, exp.Alias):
            return normalize_ast(node.this)
        return node

    norm_prd = normalize_ast(ast_pred)
    norm_gold = normalize_ast(ast_gold)
    return norm_prd == norm_gold

def get_git_commit_hash() -> str:
    try:
        out = subprocess.check_output(["git", "rev-parse", "HEAD"]).strip().decode("ascii")
        return out
    except Exception:
        return "UNKNOWN"

FORBIDDEN_NODES = (
    exp.Insert,
    exp.Delete,
    exp.Update,
    exp.Drop,
    exp.Alter,
    exp.Attach,
    exp.Pragma,
    exp.Create,
)

def is_safe_sql(sql: str, dialect: str | None = None) -> bool:
    try:
        ast = parse_one(sql, read=dialect)
    except ParseError:
        return False
    if not isinstance(ast, exp.Select):
        return False
    for node in ast.walk():
        if isinstance(node, FORBIDDEN_NODES):
            return False
    return True

def run_eval(split="dev", limit=100, resume=True, sleep_time: float = 0.01):
    data = load_spider_sqlite(split)
    if len(data) < limit:
        limit = len(data)
    data = data[:limit]
    print(f"Running eval on {len(data)} examples in split={split}...")

    commit_hash = get_git_commit_hash()
    start_ts = int(time.time())

    pred_txt   = LOG_DIR / f"{split}_pred_{start_ts}.txt"
    gold_txt   = LOG_DIR / f"{split}_gold_{start_ts}.txt"
    results_fn = LOG_DIR / f"{split}_results_{start_ts}.jsonl"
    metrics_fn = LOG_DIR / f"{split}_metrics_{start_ts}.json"

    done = set()
    if resume and results_fn.exists():
        with results_fn.open("r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("#"):
                    continue
                try:
                    r = json.loads(line)
                    done.add((r.get("db_id"), r.get("question")))
                except Exception:
                    pass

    write_header = not results_fn.exists()
    with results_fn.open("a", encoding="utf-8") as fout, \
         pred_txt.open("a", encoding="utf-8") as fpred, \
         gold_txt.open("a", encoding="utf-8") as fgold:

        if write_header:
            header = {
                "commit_hash": commit_hash,
                "split": split,
                "limit": limit,
                "start_time": start_ts,
            }
            fout.write("# " + json.dumps(header, ensure_ascii=False) + "\n")
            fout.flush()

        agg = []
        for ex in tqdm(data):
            key = (ex.db_id, ex.question)
            if resume and key in done:
                continue

            db_path = str(ex.db_path)
            schema = get_schema_preview(db_path, 0)
            sql_db = SQLDatabase.from_uri(f"sqlite:///{db_path}")
            chain = make_sql_chain(sql_db)
            state = {
                "db_path": db_path,
                "sql_db": sql_db,
                "schema_text": schema,
                "chain": chain,
            }

            t0 = time.time()
            msg, sql, output = on_generate_query(ex.question, 1000, state)
            gen_time = time.time() - t0

            safe_flag = is_safe_sql(sql)
            if not safe_flag:
                rec = {
                    "db_id": ex.db_id,
                    "question": ex.question,
                    "gold_sql": ex.gold_sql,
                    "pred_sql": sql,
                    "status": "rejected_safe_check",
                    "output": output,
                    "gen_time": gen_time,
                    "exec_time": None,
                    "error": "unsafe_sql",
                    "gold_error": None,
                    "pred_rows": None,
                    "gold_rows": None,
                    "exact_match": False,
                    "exact_match_structural": False,
                    "execution_accuracy": False,
                    "safe_check_failed": True,
                }
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fout.flush()
                fpred.write(f"{sql}\t{ex.db_id}\n")
                fgold.write(f"{ex.gold_sql}\t{ex.db_id}\n")
                fpred.flush()
                fgold.flush()
                agg.append(rec)
                if sleep_time > 0:
                    time.sleep(sleep_time)
                continue

            pred_rows, exec_time, error = try_execute_sql(sql_db, sql)
            gold_rows, gold_time, gold_error = try_execute_sql(sql_db, ex.gold_sql)

            skip = gold_error is not None

            em = False
            if not skip:
                try:
                    em = normalize_sql(sql) == normalize_sql(ex.gold_sql)
                except Exception:
                    pass

            em_struct = False
            if not skip:
                em_struct = exact_match_structural(sql, ex.gold_sql)

            exec_acc = False
            if not skip:
                exec_acc = compare_results(pred_rows, gold_rows)

            rec = {
                "db_id": ex.db_id,
                "question": ex.question,
                "gold_sql": ex.gold_sql,
                "pred_sql": sql,
                "status": msg,
                "output": output,
                "gen_time": gen_time,
                "exec_time": exec_time,
                "error": error,
                "gold_error": gold_error,
                "pred_rows": pred_rows,
                "gold_rows": gold_rows,
                "exact_match": em,
                "exact_match_structural": em_struct,
                "execution_accuracy": exec_acc,
                "safe_check_failed": False,
            }

            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fout.flush()
            fpred.write(f"{sql}\t{ex.db_id}\n")
            fgold.write(f"{ex.gold_sql}\t{ex.db_id}\n")
            fpred.flush()
            fgold.flush()
            agg.append(rec)

            if sleep_time > 0:
                time.sleep(sleep_time)


    valid = [r for r in agg if (not r.get("safe_check_failed", False)) and r.get("gold_error") is None]
    total_valid = len(valid)
    total_all = len(agg)
    if total_valid == 0:
        print("No valid examples to compute metrics")
        return

    em_count        = sum(1 for r in valid if r["exact_match"])
    em_struct_count = sum(1 for r in valid if r["exact_match_structural"])
    exec_acc_count  = sum(1 for r in valid if r["execution_accuracy"])
    error_count     = sum(1 for r in agg if (r.get("error") is not None) and (not r.get("safe_check_failed", False)))
    safe_fail_count = sum(1 for r in agg if r.get("safe_check_failed", False))
    avg_gen_time    = sum(r["gen_time"] for r in valid) / total_valid
    avg_exec_time   = sum(r["exec_time"] for r in valid) / total_valid

    metrics = {
        "commit_hash": commit_hash,
        "split": split,
        "limit": limit,
        "total_examples": total_all,
        "valid_examples": total_valid,
        "exact_match_rate": em_count / total_valid,
        "exact_match_structural_rate": em_struct_count / total_valid,
        "execution_accuracy_rate": exec_acc_count / total_valid,
        "error_rate": error_count / total_valid,
        "safe_check_fail_rate": safe_fail_count / total_all,
        "avg_gen_time": avg_gen_time,
        "avg_exec_time": avg_exec_time,
        "run_id": start_ts,
    }

    with metrics_fn.open("w", encoding="utf-8") as fm:
        json.dump(metrics, fm, ensure_ascii=False, indent=2)

    print("Metrics:", metrics)
    print(f"Wrote results → {results_fn}")
    print(f"Wrote pred file → {pred_txt}")
    print(f"Wrote gold file → {gold_txt}")
    print(f"Wrote metrics → {metrics_fn}")


if __name__ == "__main__":
    run_eval("dev", limit=10, resume=True, sleep_time=0.05)
