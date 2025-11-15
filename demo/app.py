import os
from typing import Any, Dict, List, Optional, Tuple

import gradio as gr
import requests
from requests.exceptions import ConnectionError, RequestException, Timeout

# Backend configuration
API_HOST = os.getenv("API_HOST", "localhost")
API_PORT = os.getenv("API_PORT", "8000")
API_BASE = f"http://{API_HOST}:{API_PORT}"

API_QUERY = f"{API_BASE}/api/v1/nl2sql"
API_UPLOAD = f"{API_BASE}/api/v1/nl2sql/upload_db"
API_KEY = os.getenv("API_KEY", "dev-key")  # align with backend API_KEYS env


def call_pipeline_api(
    query: str,
    db_id: Optional[str] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    Call the real FastAPI backend. No mock, no silent fallback.

    If db_id is None, the backend will use its default database.
    Any connection or HTTP error is surfaced back to the UI as an error payload.
    """
    payload: Dict[str, Any] = {"query": query}
    if db_id:
        payload["db_id"] = db_id

    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY

    try:
        resp = requests.post(API_QUERY, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except (ConnectionError, Timeout) as e:
        msg = f"Backend not reachable: {e}"
        print(f"[demo] {msg}", flush=True)
        return {
            "sql": "",
            "rationale": msg,
            "result": {},
            "traces": [],
            "error": msg,
        }
    except RequestException:
        try:
            body: Any = resp.json()
        except Exception:
            body = resp.text
        msg = f"Backend error {resp.status_code}: {body}"
        print(f"[demo] {msg}", flush=True)
        return {
            "sql": "",
            "rationale": msg,
            "result": {},
            "traces": [],
            "error": msg,
        }


def upload_db(file_obj: Any) -> Tuple[Optional[str], str]:
    """
    Upload a SQLite database to the backend and return (db_id, message).

    The returned db_id is stored in Gradio state and used for subsequent queries.
    """
    if file_obj is None:
        return None, "No DB uploaded. The backend default DB will be used."

    name = getattr(file_obj, "name", "db.sqlite")
    if not (name.endswith(".db") or name.endswith(".sqlite")):
        return None, "Only .db or .sqlite files are allowed."

    size = getattr(file_obj, "size", None)
    if size and size > 20 * 1024 * 1024:
        return None, "File too large (>20MB) for this demo."

    # Gradio's File component provides a temporary file on disk.
    try:
        f = open(file_obj.name, "rb")
    except Exception as e:
        return None, f"Could not open uploaded file: {e}"

    files = {"file": (os.path.basename(name), f, "application/octet-stream")}

    headers: Dict[str, str] = {}
    if API_KEY:
        headers["X-API-Key"] = API_KEY

    try:
        resp = requests.post(API_UPLOAD, files=files, headers=headers, timeout=120)
    finally:
        try:
            f.close()
        except Exception:
            pass

    if resp.ok:
        try:
            data = resp.json()
        except Exception:
            return None, f"Upload succeeded but response was not JSON: {resp.text}"
        db_id = data.get("db_id")
        return db_id, f"Uploaded OK. db_id={db_id}"
    else:
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        return None, f"Upload failed ({resp.status_code}): {body}"


def query_to_sql(
    user_query: str,
    db_id: Optional[str],
    _debug_flag: bool,
) -> Tuple[str, str, str, Any, List[Dict[str, Any]], List[List[Any]]]:
    """
    Run the full NL2SQL pipeline via the backend and format outputs for the UI.

    Returns:
        badges_text, sql, explanation, result_json, traces_json, timings_table
    """
    if not user_query.strip():
        msg = "❌ Please enter a query."
        return msg, "", msg, {}, [], []

    data = call_pipeline_api(user_query, db_id)

    # Explicit error propagation from backend
    if data.get("error") and not data.get("sql"):
        err_msg = str(data.get("error"))
        return f"❌ {err_msg}", "", err_msg, {}, [], []

    sql = str(data.get("sql") or "")
    explanation = str(data.get("rationale") or "")
    result = data.get("result", {})
    traces = data.get("traces", []) or []

    # Compute simple latency badge from traces (sum of duration_ms)
    badges_text = ""
    if traces and all("duration_ms" in t for t in traces):
        total_ms = sum(float(t.get("duration_ms", 0.0)) for t in traces)
        badges_text = f"latency≈{int(total_ms)}ms"

    # Build timings table for the Timings tab
    timings_table: List[List[Any]] = []
    if traces and all("duration_ms" in t for t in traces):
        timings_table = [
            [t.get("stage", "?"), t.get("duration_ms", 0.0)] for t in traces
        ]

    return badges_text, sql, explanation, result, traces, timings_table


def build_ui() -> gr.Blocks:
    """
    Build the Gradio UI for the NL2SQL Copilot demo.

    - Optional DB upload (SQLite)
    - Textbox for the natural language question
    - Example queries aligned with the default Chinook DB
    - Tabs for result, trace, repair notes, and per-stage timings
    """
    with gr.Blocks(title="NL2SQL Copilot") as demo:
        gr.Markdown(
            "# NL2SQL Copilot\n"
            "Upload a SQLite DB (optional) or use the backend default database."
        )

        db_state = gr.State(value=None)

        # DB upload section
        with gr.Row():
            db_file = gr.File(
                label="Upload SQLite (.db/.sqlite)",
                file_types=[".db", ".sqlite"],
            )
            upload_btn = gr.Button("Upload DB")

        db_msg = gr.Markdown()
        upload_btn.click(
            upload_db,
            inputs=[db_file],
            outputs=[db_state, db_msg],
        )

        # Query input and run button
        with gr.Row():
            q = gr.Textbox(
                label="Question",
                placeholder="e.g. Top 3 albums by total sales",
                scale=4,
            )
            debug = gr.Checkbox(
                label="Debug (UI only)",
                value=True,
                scale=1,
            )
            run = gr.Button("Run")

        # Example queries compatible with the Chinook schema
        gr.Examples(
            examples=[
                ["List all artists"],
                [
                    "List customers whose total spending is above the average invoice total."
                ],
                ["Total number of tracks per genre"],
                ["List all albums with their total sales"],
                ["Customers spending above average"],
            ],
            inputs=[q],
            label="Try these example queries",
        )

        badges = gr.Markdown()
        sql_out = gr.Code(label="Final SQL", language="sql")
        exp_out = gr.Textbox(label="Explanation", lines=4)

        with gr.Tab("Result"):
            res_out = gr.JSON()

        with gr.Tab("Trace"):
            trace_out = gr.JSON(label="Stage trace")

        with gr.Tab("Repair"):
            gr.Markdown(
                """
                ### Repair & self-healing (pipeline-level)

                The repair loop is fully implemented in the backend:

                * If a candidate SQL fails safety or execution checks,
                  the pipeline attempts to **repair** it.
                * All repair attempts and outcomes are tracked in Prometheus
                  (for example, `nl2sql_repair_attempts_total` and related rates).

                For now, detailed before/after SQL diffs and repair candidates
                are exposed via traces and metrics dashboards.

                This tab is reserved for a future, richer UI:
                side-by-side SQL diff, repair candidates, and explanations.
                """
            )

        with gr.Tab("Timings"):
            timings = gr.Dataframe(
                headers=["stage", "duration_ms"],
                datatype=["str", "number"],
            )

        run.click(
            query_to_sql,
            inputs=[q, db_state, debug],
            outputs=[badges, sql_out, exp_out, res_out, trace_out, timings],
        )

    return demo


demo = build_ui()

if __name__ == "__main__":
    print("[demo] Launching Gradio demo on 0.0.0.0:7860 ...", flush=True)
    demo.launch(
        server_name=os.getenv("GRADIO_SERVER_NAME", "0.0.0.0"),
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7860")),
        share=False,
        debug=True,
    )
