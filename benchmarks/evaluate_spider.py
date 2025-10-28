from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable, Optional, Tuple, cast

from tqdm import tqdm
from langchain_community.utilities import SQLDatabase
from sqlglot import parse_one, exp
from sqlglot.errors import ParseError
from sqlalchemy import create_engine, inspect
from spider_loader import load_spider_sqlite


def _try_import_pipeline():
    """
    Try multiple plausible entrypoints from nl2sql.
    Returns a tuple of callables or None:
      (make_pipeline | None, run_function | None, PipelineClass | None)
    """
    make_pipeline = None
    run_fn = None
    PipelineCls = None
    try:
        from nl2sql.pipeline import make_pipeline as _mk  # type: ignore

        make_pipeline = _mk
    except Exception:
        pass
    try:
        from nl2sql.pipeline import run_nl2sql as _run  # type: ignore

        run_fn = _run
    except Exception:
        pass
    try:
        from nl2sql.pipeline import Pipeline as _P  # type: ignore

        PipelineCls = _P
    except Exception:
        pass
    return make_pipeline, run_fn, PipelineCls


LOG_DIR = Path("logs/spider_eval")
LOG_DIR.mkdir(parents=True, exist_ok=True)

FORBIDDEN_NODES: Tuple[type, ...] = (
    exp.Insert,
    exp.Delete,
    exp.Update,
    exp.Drop,
    exp.Alter,
    exp.Attach,
    exp.Pragma,
    exp.Create,
)


def normalize_sql(sql: str) -> str:
    return " ".join(sql.lower().strip().split())


def compare_results(
    pred_rows: Optional[Iterable[Any]], gold_rows: Optional[Iterable[Any]]
) -> bool:
    if pred_rows is None or gold_rows is None:
        return False
    return set(pred_rows) == set(gold_rows)


def try_execute_sql(
    sql_db: SQLDatabase,
    sql: str,
    timeout: Optional[float] = None,  # kept for API compatibility
) -> tuple[Optional[list[tuple[Any, ...]]], float, Optional[str]]:
    start = time.time()
    try:
        raw_rows = sql_db.run(sql)

        # Normalize result shape for MyPy and downstream code
        if isinstance(raw_rows, list):
            rows = [tuple(r) for r in raw_rows]
        elif isinstance(raw_rows, tuple):
            rows = [tuple(raw_rows)]
        else:
            # Fallback cast — if library returns ResultSet or something similar
            rows = cast(list[tuple[Any, ...]], raw_rows)

        return rows, time.time() - start, None

    except Exception as e:
        return None, time.time() - start, str(e)


def exact_match_structural(sql_pred: str, sql_gold: str) -> bool:
    try:
        ast_pred = parse_one(sql_pred)
        ast_gold = parse_one(sql_gold)
    except Exception:
        return False

    def normalize_ast(node: exp.Expression) -> exp.Expression:
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
        out = (
            subprocess.check_output(["git", "rev-parse", "HEAD"])
            .strip()
            .decode("ascii")
        )
        return out
    except Exception:
        return "UNKNOWN"


def is_safe_sql(sql: str, dialect: Optional[str] = None) -> bool:
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


# --- جایگزین get_schema_preview از app.routers ---
def get_schema_preview_sqlalchemy(db_path: str, max_cols: int = 0) -> str:
    """
    Lightweight schema preview using SQLAlchemy inspector.
    max_cols=0 => unlimited
    """
    engine = create_engine(f"sqlite:///{db_path}")
    insp = inspect(engine)
    lines: list[str] = []
    for tbl in sorted(insp.get_table_names()):
        cols = insp.get_columns(tbl)
        if max_cols > 0:
            cols = cols[:max_cols]
        col_str = ", ".join(f"{c['name']}:{c.get('type')}" for c in cols)
        pks = insp.get_pk_constraint(tbl).get("constrained_columns") or []
        pk_str = f" | PK: {', '.join(pks)}" if pks else ""
        fks = insp.get_foreign_keys(tbl)
        fk_str = ""
        if fks:
            fks_desc = []
            for fk in fks:
                ref = fk.get("referred_table")
                cols_fk = ", ".join(fk.get("constrained_columns") or [])
                ref_cols = ", ".join(fk.get("referred_columns") or [])
                fks_desc.append(f"{cols_fk} -> {ref}({ref_cols})")
            fk_str = " | FK: " + " ; ".join(fks_desc)
        lines.append(f"{tbl}({col_str}){pk_str}{fk_str}")
    engine.dispose()
    return "\n".join(lines)


def _generate_sql(
    question: str, sql_db: SQLDatabase, schema_text: str, max_output_tokens: int = 1000
) -> tuple[str, str, dict[str, Any]]:
    """
    Returns: (status_msg, sql_text, extra_output)
    Strategy:
      1) If nl2sql.pipeline.run_nl2sql exists: call it.
      2) Else if nl2sql.pipeline.make_pipeline exists: build and run.
      3) Else if nl2sql.pipeline.Pipeline exists: instantiate minimal pipeline and run.
      4) Else: raise NotImplementedError.
    """
    make_pipeline, run_fn, PipelineCls = _try_import_pipeline()

    # Case 1: direct run function
    if run_fn is not None:
        res = run_fn(
            question=question,
            schema_text=schema_text,
            sql_db=sql_db,
            max_output_tokens=max_output_tokens,
        )
        # Expecting a dict-like or object with attributes; normalize:
        if isinstance(res, dict):
            msg = res.get("status", "ok")
            sql = res.get("sql", "")
            return msg, sql, res
        # fallback generic
        msg = getattr(res, "status", "ok")
        sql = getattr(res, "sql", "")
        return msg, sql, {"result": res}

    # Case 2: factory + run
    if make_pipeline is not None:
        pipe = make_pipeline(sql_db=sql_db, schema_text=schema_text)  # type: ignore[arg-type]
        # Common conventions:
        if hasattr(pipe, "run"):
            out = pipe.run(question)  # type: ignore[call-arg]
        elif hasattr(pipe, "execute"):
            out = pipe.execute(question)  # type: ignore[call-arg]
        else:
            raise RuntimeError("Pipeline object has no run/execute()")
        msg = getattr(out, "status", "ok")
        sql = getattr(out, "sql", "")
        return msg, sql, {"result": out}

    # Case 3: class-based pipeline
    if PipelineCls is not None:
        # Try minimal constructor names; adjust to your class signature if needed
        # We pass what we have; extra kwargs should be ignored or have defaults.
        pipe = PipelineCls(sql_db=sql_db, schema_text=schema_text)
        if hasattr(pipe, "run"):
            out = pipe.run(question)  # type: ignore[call-arg]
        else:
            raise RuntimeError("Pipeline class has no run()")
        msg = getattr(out, "status", "ok")
        sql = getattr(out, "sql", "")
        return msg, sql, {"result": out}

    raise NotImplementedError(
        "Cannot locate a public NL2SQL entrypoint in nl2sql.pipeline. "
        "Expose one of: run_nl2sql(), make_pipeline(), or Pipeline.run()."
    )


def run_eval(
    split: str = "dev", limit: int = 100, resume: bool = True, sleep_time: float = 0.01
) -> None:
    data = load_spider_sqlite(split)
    if len(data) < limit:
        limit = len(data)
    data = data[:limit]
    print(f"Running eval on {len(data)} examples in split={split}...")

    commit_hash = get_git_commit_hash()
    start_ts = int(time.time())

    pred_txt = LOG_DIR / f"{split}_pred_{start_ts}.txt"
    gold_txt = LOG_DIR / f"{split}_gold_{start_ts}.txt"
    results_fn = LOG_DIR / f"{split}_results_{start_ts}.jsonl"
    metrics_fn = LOG_DIR / f"{split}_metrics_{start_ts}.json"

    done: set[tuple[str, str]] = set()
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
    agg: list[dict[str, Any]] = []

    with (
        results_fn.open("a", encoding="utf-8") as fout,
        pred_txt.open("a", encoding="utf-8") as fpred,
        gold_txt.open("a", encoding="utf-8") as fgold,
    ):
        if write_header:
            header = {
                "commit_hash": commit_hash,
                "split": split,
                "limit": limit,
                "start_time": start_ts,
            }
            fout.write("# " + json.dumps(header, ensure_ascii=False) + "\n")
            fout.flush()

        for ex in tqdm(data):
            key = (ex.db_id, ex.question)
            if resume and key in done:
                continue

            db_path = str(ex.db_path)
            schema = get_schema_preview_sqlalchemy(db_path, max_cols=0)
            sql_db = SQLDatabase.from_uri(f"sqlite:///{db_path}")

            t0 = time.time()
            try:
                msg, sql, output = _generate_sql(
                    ex.question, sql_db, schema, max_output_tokens=1000
                )
            except NotImplementedError as e:
                rec = {
                    "db_id": ex.db_id,
                    "question": ex.question,
                    "gold_sql": ex.gold_sql,
                    "pred_sql": "",
                    "status": "no_entrypoint",
                    "output": {"error": str(e)},
                    "gen_time": time.time() - t0,
                    "exec_time": None,
                    "error": "no_entrypoint",
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
                fgold.write(f"{ex.gold_sql}\t{ex.db_id}\n")
                fgold.flush()
                agg.append(rec)
                if sleep_time > 0:
                    time.sleep(sleep_time)
                continue

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
            em = normalize_sql(sql) == normalize_sql(ex.gold_sql) if not skip else False
            em_struct = exact_match_structural(sql, ex.gold_sql) if not skip else False
            exec_acc = compare_results(pred_rows, gold_rows) if not skip else False

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

    valid = [
        r
        for r in agg
        if (not r.get("safe_check_failed", False)) and (r.get("gold_error") is None)
    ]
    total_valid = len(valid)
    total_all = len(agg)
    if total_valid == 0:
        print("No valid examples to compute metrics")
        return

    em_count = sum(1 for r in valid if r["exact_match"])
    em_struct_count = sum(1 for r in valid if r["exact_match_structural"])
    exec_acc_count = sum(1 for r in valid if r["execution_accuracy"])
    error_count = sum(
        1
        for r in agg
        if (r.get("error") is not None) and (not r.get("safe_check_failed", False))
    )
    safe_fail_count = sum(1 for r in agg if r.get("safe_check_failed", False))
    avg_gen_time = sum(float(r["gen_time"]) for r in valid) / total_valid
    avg_exec_time = sum(float(r["exec_time"]) for r in valid) / total_valid

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

    metrics_fn = LOG_DIR / f"{split}_metrics_{start_ts}.json"
    with metrics_fn.open("w", encoding="utf-8") as fm:
        json.dump(metrics, fm, ensure_ascii=False, indent=2)

    print("Metrics:", metrics)
    print(f"Wrote results → {results_fn}")
    print(f"Wrote pred file → {pred_txt}")
    print(f"Wrote gold file → {gold_txt}")
    print(f"Wrote metrics → {metrics_fn}")


if __name__ == "__main__":
    run_eval("dev", limit=10, resume=True, sleep_time=0.05)
