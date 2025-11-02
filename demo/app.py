import requests
import gradio as gr

API_UPLOAD = "http://localhost:8000/api/v1/nl2sql/upload_db"
API_QUERY = "http://localhost:8000/api/v1/nl2sql"


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
        db_id = data.get("db_id")
        if not db_id:
            return None, f"Upload returned no db_id: {data}"
        return db_id, f"Uploaded OK. db_id={db_id}"
    # Show backend error body
    try:
        body = r.json()
    except ValueError:
        body = r.text
    return None, f"Upload failed ({r.status_code}): {body}"


def _post_query(payload: dict):
    """Helper: POST and return (ok, data_or_error_string)."""
    r = requests.post(API_QUERY, json=payload, timeout=120)
    if r.ok:
        try:
            return True, r.json()
        except ValueError:
            return False, "Backend returned non-JSON body."
    try:
        body = r.json()
    except ValueError:
        body = r.text
    return False, f"{r.status_code} {body}"


def query_to_sql(user_query: str, db_id: str | None, _debug_flag: bool):
    # Build minimal schema-compliant payload.
    # Server expects request.query (name is 'query' per router code).
    base_payload = {"query": user_query.strip() if user_query else ""}

    # First try WITH db_id (if present). If backend rejects (422), retry WITHOUT.
    if db_id:
        ok, data = _post_query({**base_payload, "db_id": db_id})
        if not ok and isinstance(data, str) and data.startswith("422"):
            # Retry without db_id in case request model forbids extra fields.
            ok, data = _post_query(base_payload)
    else:
        ok, data = _post_query(base_payload)

    if not ok:
        # Surface backend error text to the UI
        err_badges = f"Error: {data}"
        return (
            err_badges,  # badges
            "",  # sql_out
            "",  # exp_out
            {},  # result (tab)
            [],  # trace (tab)
            [],  # repair_candidates (tab)
            "",  # repair_diff (tab)
            [],  # timings (tab)
        )

    d = data

    # Map fields to UI (server returns: ambiguous, sql, rationale, traces)
    sql = d.get("sql") or d.get("sql_final") or ""
    explanation = d.get("rationale") or d.get("explanation") or ""
    result = d.get("result", {})  # optional/maybe absent
    trace_list = d.get("traces") or d.get("trace") or []

    ambiguous_flag = "Yes" if d.get("ambiguous") else "No"
    safety = (
        "Allowed"
        if d.get("safety", {}).get("allowed", True)
        else f"Blocked: {d.get('safety', {}).get('blocked_reason')}"
    )
    verification = "Passed" if d.get("verification", {}).get("passed") else "Failed"
    repair = d.get("repair", {}) or {}
    repair_text = f"Applied: {repair.get('applied', False)}, Attempts: {repair.get('attempts', 0)}"

    timings = d.get("timings_ms", {}) or {}
    timings_table = [[k, timings[k]] for k in sorted(timings.keys())]

    badges_text = f"Ambiguous: {ambiguous_flag} | Safety: {safety} | Verification: {verification} | Repair: {repair_text}"

    return (
        badges_text,
        sql,
        explanation,
        result,
        trace_list,
        repair.get("candidates", []),
        repair.get("diff", ""),
        timings_table,
    )


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
        # keep the checkbox in UI if you like, but we don't send it to backend
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
        repair_diff = gr.Code(label="Diff (if any)", language="diff")

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
    # Let Gradio pick a free port by default to avoid collisions
    demo.launch()
