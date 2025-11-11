import requests
import gradio as gr
import os
import json
from pathlib import Path

# Prefer internal backend when running inside Docker
API_HOST = os.getenv("API_HOST", "localhost")
API_PORT = os.getenv("API_PORT", "8000")

USE_MOCK = os.environ.get("USE_MOCK", "0") == "1"
API_UPLOAD = f"http://{API_HOST}:{API_PORT}/api/v1/nl2sql/upload_db"
API_QUERY = f"http://{API_HOST}:{API_PORT}/api/v1/nl2sql"

HARDCODED_MOCK = {
    "sql": "SELECT name, country FROM singer WHERE age > 20;",
    "rationale": "Example: select singers older than 20.",
    "result": {
        "rows": 5,
        "columns": ["name", "country"],
        "rows_data": [["Alice", "France"], ["Bob", "USA"]],
    },
    "traces": [
        {"stage": "detector", "summary": "ok", "duration_ms": 5},
        {"stage": "planner", "summary": "intent parsed", "duration_ms": 120},
        {"stage": "generator", "summary": "sql generated", "duration_ms": 420},
        {"stage": "verifier", "summary": "passed", "duration_ms": 10},
    ],
    "metrics": {"EM": 0.15, "SM": 0.70, "ExecAcc": 0.73, "avg_latency_ms": 8113},
}


def load_mock_from_summary():
    """Try to read latest benchmark summary.json; fallback to hardcoded mock."""
    try:
        files = sorted(
            Path("benchmarks/results_pro").glob("*/summary.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if files:
            p = files[0]
            with open(p, "r", encoding="utf-8") as f:
                sj = json.load(f)
            return {
                "sql": sj.get("example_sql", HARDCODED_MOCK["sql"]),
                "rationale": sj.get("note", HARDCODED_MOCK["rationale"]),
                "result": {"rows": sj.get("total_samples", 0), "columns": []},
                "traces": HARDCODED_MOCK["traces"],
                "metrics": {
                    "EM": sj.get("avg_em", HARDCODED_MOCK["metrics"]["EM"]),
                    "SM": sj.get("avg_sm", HARDCODED_MOCK["metrics"]["SM"]),
                    "ExecAcc": sj.get(
                        "avg_execacc", HARDCODED_MOCK["metrics"]["ExecAcc"]
                    ),
                    "avg_latency_ms": sj.get(
                        "avg_latency_ms", HARDCODED_MOCK["metrics"]["avg_latency_ms"]
                    ),
                },
            }
    except Exception:
        pass
    return HARDCODED_MOCK


def call_pipeline_api_or_mock(query: str, db_id: str | None = None, timeout=10):
    """Call backend if available; otherwise return mock."""
    if USE_MOCK:
        return load_mock_from_summary()
    try:
        payload = {"query": query}
        if db_id:
            payload["db_id"] = db_id
        r = requests.post(API_QUERY, json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[demo] API call failed ({e}); using mock instead.")
        return load_mock_from_summary()


def upload_db(file_obj):
    if file_obj is None:
        return None, "No DB uploaded. Default DB will be used."
    name = getattr(file_obj, "name", "db.sqlite")
    if not (name.endswith(".db") or name.endswith(".sqlite")):
        return None, "Only .db or .sqlite files are allowed."
    size = getattr(file_obj, "size", None)
    if size and size > 20 * 1024 * 1024:
        return None, "File too large (>20MB) for this demo."

    # Gradio gives a temp file path as file_obj.name
    files = {"file": (name, open(file_obj.name, "rb"), "application/octet-stream")}
    try:
        r = requests.post(API_UPLOAD, files=files, timeout=120)
    finally:
        # best-effort close
        try:
            files["file"][1].close()
        except Exception:
            pass

    if r.ok:
        data = r.json()
        return data.get("db_id"), f"Uploaded OK. db_id={data.get('db_id')}"
    try:
        body = r.json()
    except ValueError:
        body = r.text
    return None, f"Upload failed ({r.status_code}): {body}"


def query_to_sql(user_query: str, db_id: str | None, _debug_flag: bool):
    """Unified query handler: tries backend or mock fallback."""
    if not user_query.strip():
        return "❌ Please enter a query.", "", "", {}, [], [], "", []

    data = call_pipeline_api_or_mock(user_query, db_id)
    sql = data.get("sql") or ""
    explanation = data.get("rationale") or ""
    result = data.get("result", {})
    trace_list = data.get("traces", [])

    metrics = data.get("metrics", {})
    badges_text = (
        f"EM={metrics.get('EM', '?')} | SM={metrics.get('SM', '?')} | "
        f"ExecAcc={metrics.get('ExecAcc', '?')} | latency={metrics.get('avg_latency_ms', '?')}ms"
    )

    timings_table = []
    if trace_list and all("duration_ms" in t for t in trace_list):
        timings_table = [[t["stage"], t["duration_ms"]] for t in trace_list]

    return badges_text, sql, explanation, result, trace_list, [], "", timings_table


# ---- UI definition (unchanged) ----
with gr.Blocks(title="NL2SQL Copilot") as demo:
    gr.Markdown("# NL2SQL Copilot\nUpload a SQLite DB (optional) or use default.")

    db_state = gr.State(value=None)

    with gr.Row():
        db_file = gr.File(
            label="Upload SQLite (.db/.sqlite)", file_types=[".db", ".sqlite"]
        )
        upload_btn = gr.Button("Upload DB")
    db_msg = gr.Markdown()
    upload_btn.click(upload_db, inputs=[db_file], outputs=[db_state, db_msg])

    with gr.Row():
        q = gr.Textbox(label="Question", scale=4)
        debug = gr.Checkbox(label="Debug (UI only)", value=True, scale=1)
        run = gr.Button("Run")

    badges = gr.Markdown()
    sql_out = gr.Code(label="Final SQL", language="sql")
    exp_out = gr.Textbox(label="Explanation", lines=3)

    with gr.Tab("Result"):
        res_out = gr.JSON()

    with gr.Tab("Trace"):
        trace = gr.JSON(label="Stage trace")

    with gr.Tab("Repair"):
        repair_candidates = gr.JSON(label="Candidates")
        repair_diff = gr.Textbox(label="Diff (if any)", lines=10)

    with gr.Tab("Timings"):
        timings = gr.Dataframe(headers=["metric", "ms"], datatype=["str", "number"])

    run.click(
        query_to_sql,
        inputs=[q, db_state, debug],
        outputs=[
            badges,
            sql_out,
            exp_out,
            res_out,
            trace,
            repair_candidates,
            repair_diff,
            timings,
        ],
    )

if __name__ == "__main__":
    import os

    demo.launch(
        server_name=os.getenv("GRADIO_SERVER_NAME", "0.0.0.0"),
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7860")),
    )
