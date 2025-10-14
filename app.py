from config import (
    LLM_MODEL,
    LLM_TEMPERATURE,
    FORBIDDEN_KEYWORDS,
    FORBIDDEN_TABLES
)
import os
import sqlite3
import json
import re
from typing import Optional, Tuple, List

import gradio as gr
import sqlglot
from sqlglot import exp

from langchain_openai import ChatOpenAI
from langchain_community.utilities import SQLDatabase
from langchain.chains import create_sql_query_chain
from langchain.prompts import ChatPromptTemplate


def get_readonly_sqlite_url(db_path: str) -> str:
    return f"file:{db_path}?mode=ro&uri=true"

def get_schema_preview(db_path: str, limit_per_table: int = 0) -> str:
    uri = get_readonly_sqlite_url(db_path)
    with sqlite3.connect(uri, uri=True, timeout=3) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
        tables = [r["name"] for r in cur.fetchall()]
        lines = []
        for t in tables:
            # skip SQLite internals
            if t in FORBIDDEN_TABLES:
                continue
            cur.execute(f"PRAGMA table_info({t});")
            cols = cur.fetchall()
            col_line = ", ".join([f"{c['name']}:{c['type']}" for c in cols])
            lines.append(f"- {t} ({col_line})")
            if limit_per_table > 0:
                try:
                    cur.execute(f"SELECT * FROM {t} LIMIT {limit_per_table};")
                    sample = cur.fetchall()
                    if sample:
                        lines.append(f"  sample rows: {len(sample)}")
                except Exception:
                    pass
        if not lines:
            return "(no user tables found)"
        return "\n".join(lines)


def validate_sql_safe(sql: str) -> Tuple[bool, str]:
    if sql.count(";") > 0:
        if sql.strip().endswith(";"):
            if sql.strip()[:-1].count(";") > 0:
                return False, "Multiple statements are not allowed."
        else:
            return False, "Multiple statements are not allowed."

    upper = re.sub(r"\s+", " ", sql).strip()
    for kw in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{kw}\b", upper):
            return False, f"Keyword '{kw}' is not allowed."

    try:
        parsed = sqlglot.parse(sql, read='sqlite')
    except Exception as e:
        return False, f"SQL parse error: {e}"

    if not parsed or len(parsed) != 1:
        return False, "Exactly one SQL statement is allowed."

    stmt = parsed[0]
    if not isinstance(stmt, exp.Select):
        return False, "Only SELECT statements are allowed."

    for table in stmt.find_all(exp.Table):
        table_name = table.name.lower() if table.name else ""
        if table_name in FORBIDDEN_TABLES:
            return False, f"Access to {table_name} is not allowed."

    return True, "OK"

def execute_select(db_path: str, sql: str, max_rows: int = 1000, timeout: float = 5.0) -> Tuple[list[str], List[List]]:
    uri = get_readonly_sqlite_url(db_path)
    if not re.search(r"\bLIMIT\b", sql, re.IGNORECASE):
        sql = f"{sql.rstrip(';')} LIMIT {max_rows}"

    with sqlite3.connect(uri, uri=True, timeout=timeout) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        if rows:
            cols = rows[0].keys()
            data = [list(r) for r in rows]
            return list(cols), data
        else:
            return [], []



custom_prompt = ChatPromptTemplate.from_template("""
Given the following question, return ONLY a valid SQL query in JSON form.

Question: {input}
Database schema: {table_info}

You may sample/preview at most {top_k} rows if you need examples.

Respond in this exact JSON format:
{{
  "sql": "<SQL_QUERY_HERE>"
}}
""")


def make_sql_chain(sql_db: SQLDatabase):
    assert hasattr(sql_db, "get_table_info"), "Expected LangChain SQLDatabase"
    llm = ChatOpenAI(model=LLM_MODEL, temperature=LLM_TEMPERATURE)
    chain = create_sql_query_chain(llm, sql_db, prompt=custom_prompt, k=20)
    return chain


def on_upload_database(db_file, state):
    if db_file is None:
        return state, "No file provided.", "(no schema)"
    path = db_file.name

    sql_db = SQLDatabase.from_uri(f"sqlite:///{path}")

    schema_text = get_schema_preview(path, limit_per_table=0)

    chain = make_sql_chain(sql_db)

    new_state = {
        "db_path": path,
        "sql_db": sql_db,
        "schema_text": schema_text,
        "chain": chain,
    }
    return new_state, f"Database '{os.path.basename(path)}' uploaded successfully.", schema_text

def extract_sql_safe(output_text: str) -> str:
    try:
        obj = json.loads(output_text)
        if isinstance(obj, dict) and "sql" in obj:
            return obj["sql"].strip()
    except Exception:
        pass
    m = re.search(r"```sql\s*(.*?)\s*```", output_text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return output_text.strip()

def on_generate_query(question , max_rows, state):
    if not state or not state.get("db_path") or not state.get("chain"):
        return "Please upload a database first.", "", ""
    if not question or not question.strip():
        return "Please enter a question.", "", ""

    try:
        generated_sql = state["chain"].invoke({"question": question})

        sql = extract_sql_safe(str(generated_sql))

        ok, msg = validate_sql_safe(sql)
        if not ok:
            return f"Blocked SQL: {msg}", sql, ""

        cols, rows = execute_select(state["db_path"], sql, max_rows=max_rows)
        if not cols:
            return f"No rows returned.", sql, "[]"

        sample = [dict(zip(cols, r)) for r in rows[:50]]
        return f"Returned {len(rows)} row(s). Showing up to 50.", sql, json.dumps(sample, indent=2)

    except Exception as e:
        return f"Error: {e}", "", ""


with gr.Blocks(title="nl2sql-copilot-prototype (safe)") as demo:
    gr.Markdown("# nl2sql-copilot-prototype (Sqlite, safe)")
    gr.Markdown(
        "Upload a **SQLite** file, ask a question in natural language, "
        "and I will: (1) generate SQL, (2) validate it (SELECT-only), (3) execute read-only, "
        "and (4) show you the results."
    )

    state = gr.State({"db_path": None, "sql_db": None, "schema_text": "", "chain": None})

    with gr.Row():
        db_file = gr.File(label="Upload SQlite Database", file_types=[".sqlite", ".db"])
        upload_status = gr.Textbox(label="upload Status", interactive=False)

    schema_box = gr.Accordion("Database schema (preview)", open=False)
    with schema_box:
        schema_md = gr.Markdown("(no schema)")

    gr.Markdown("---")

    with gr.Row():
        question = gr.Textbox(label="Your question", placeholder="e.g., Top 10 tracks by total sales")
    with gr.Row():
        max_row= gr.Slider(10, 5000, value=1000, step=10, label="Max rows")

    with gr.Row():
        run_btn = gr.Button("Generate & Run SQL", variant="primary")

    with gr.Row():
        status_out = gr.Textbox(label="Status")
    with gr.Row():
        sql_out = gr.Code(label="Generated SQL (validated)")
    with gr.Row():
        result_out = gr.Code(label="Result (JSON sample)")

    db_file.change(
        fn=on_upload_database,
        inputs=[db_file, state],
        outputs=[state, upload_status, schema_md],
    )

    run_btn.click(
        fn=on_generate_query,
        inputs=[question, max_row, state],
        outputs=[status_out, sql_out, result_out],
    )



if __name__ == "__main__":
    demo.launch()