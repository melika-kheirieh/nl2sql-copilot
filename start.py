import subprocess
import threading
import os


def run_fastapi():
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
        ]
    )


def run_gradio():
    pass  # noqa: just import to launch


if __name__ == "__main__":
    t = threading.Thread(target=run_fastapi, daemon=True)
    t.start()
    run_gradio()
