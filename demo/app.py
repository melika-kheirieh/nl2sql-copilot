import io
import requests
import gradio as gr

API_UPLOAD = "http://localhost:8000/api/v1/nl2sql/upload_db"
API_QUERY  = "http://localhost:8000/api/v1/nl2sql"


def upload_db(file_obj):
    if file_obj is None:
        return None, "No DB uploaded. Default DB will be used."
    name = getattr(file_obj, "name", "db.sqlite")
    if not (name.endswith(".db") or name.endswith(".sqlite")):
        return None, "Only .db or .sqlite files are allowed."
    size = getattr(file_obj, "size", None)
    if size and size > 20 * 1024 * 1024:
        return None, "File too large (>20MB). Use a smaller demo DB."

    # Read bytes
    with open(file_obj.name, "rb") as f:
        data = f.read()

    r = requests.post(
        API_UPLOAD,
        files={"file": (name, io.BytesIO(data), "application/octet-stream")},
        timeout=60,
    )
    r.raise_for_status()
    db_id = r.json().get("db_id")
    return db_id, f"Uploaded OK. db_id={db_id}"


def query_to_sql(user_query, db_id, debug):
    payload = {"query": user_query, "debug": bool(debug)}
    if db_id:
        payload["db_id"] = db_id
    r = requests.post(API_QUERY, json=payload, timeout=120)
    r.raise_for_status()
    d = r.json()

    sql = d.get("sql_final") or d.get("sql") or ""
    explanation = d.get("explanation", "")
    result = d.get("result", [])

    # Flags summary
    ambiguous = "Yes" if d.get("ambiguous") else "No"
    safety = ("Allowed" if d.get("safety", {}).get("allowed", True) else f"Blocked: {d.get('safety', {}).get('blocked_reason')}")
    verification = ("Passed" if d.get("verification", {}).get("passed") else "Failed")
    repair = d.get("repair", {})
    repair_text = f"Applied: {repair.get('applied', False)}, Attempts: {repair.get('attempts', 0)}"

    timings = d.get("timings_ms", {})
    timings_table = [[k, timings[k]] for k in sorted(timings.keys())]

    return (
        f"Ambiguous: {ambiguous} | Safety: {safety} | Verification: {verification} | Repair: {repair_text}",
        sql,
        explanation,
        result,
        d.get("trace", []),
        repair.get("candidates", []),
        repair.get("diff", ""),
        timings_table,
    )


with gr.Blocks(title="NL2SQL Copilot") as demo:
    gr.Markdown("# NL2SQL Copilot\nUpload a SQLite DB (optional) or use default.")

    db_state = gr.State(value=None)

    with gr.Row():
        db_file = gr.File(label="Upload SQLite (.db/.sqlite)", file_types=[".db", ".sqlite"])
        upload_btn = gr.Button("Upload DB")
    db_msg = gr.Markdown()
    upload_btn.click(upload_db, inputs=[db_file], outputs=[db_state, db_msg])

    with gr.Row():
        q = gr.Textbox(label="Question", scale=4)
        debug = gr.Checkbox(label="Debug", value=True, scale=1)
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
        repair_diff = gr.Code(label="SQL Diff", language="sql")

    with gr.Tab("Timings"):
        timings = gr.Dataframe(headers=["stage", "ms"], datatype=["str", "number"])

    run.click(
        query_to_sql,
        inputs=[q, db_state, debug],
        outputs=[badges, sql_out, exp_out, res_out, trace, repair_candidates, repair_diff, timings],
    )

if __name__ == "__main__":
    # Let Gradio pick a free port by default to avoid collisions
    demo.launch()