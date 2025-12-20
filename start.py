import subprocess
import threading
import os


def run_fastapi():
    """Run FastAPI backend on port 8000."""
    subprocess.run(
        [
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--proxy-headers",
            "--workers",
            str(os.getenv("UVICORN_WORKERS", 1)),
        ],
        check=True,
    )


def run_gradio():
    """Build and launch Gradio UI on the Space's PORT."""
    print("[start] about to launch gradio on PORT=", os.getenv("PORT"), flush=True)
    from demo.app import build_ui
    import inspect

    demo = build_ui()

    launch_kwargs = dict(
        server_name="0.0.0.0",
        server_port=int(os.getenv("PORT", "7860")),
        debug=False,
    )

    # Backward/forward compatibility: some gradio versions don't support `show_api`
    sig = inspect.signature(demo.launch)
    if "show_api" in sig.parameters:
        launch_kwargs["show_api"] = False

    demo.launch(**launch_kwargs)


if __name__ == "__main__":
    # Start FastAPI in background thread
    print("[start] launching uvicorn thread...", flush=True)
    t = threading.Thread(target=run_fastapi, daemon=True)
    t.start()

    # Run Gradio in foreground (keeps container alive)
    print("[start] about to launch gradio on PORT=", os.getenv("PORT"), flush=True)
    run_gradio()
