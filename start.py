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
    """Launch Gradio UI (demo/app.py)."""
    import demo.app  # noqa: F401
    # This module runs demo.launch() on import


if __name__ == "__main__":
    # Start FastAPI in background thread
    t = threading.Thread(target=run_fastapi, daemon=True)
    t.start()

    # Run Gradio in foreground (keeps container alive)
    run_gradio()
