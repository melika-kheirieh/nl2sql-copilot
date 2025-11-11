# ---------- Stage 1: Build wheels ----------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip wheel --wheel-dir /wheels -r requirements.txt

# ---------- Stage 2: Runtime image ----------
FROM python:3.12-slim AS runtime

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# HTTPS certs for outbound calls
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m appuser

# Install deps from wheels
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --find-links=/wheels -r requirements.txt && \
    rm -rf /wheels

# App code
COPY . .

# Permissions
RUN chown -R appuser:appuser /app

# ---- HF expects the *public* web app on port 7860 ----
ENV GRADIO_SERVER_NAME=0.0.0.0 \
    GRADIO_SERVER_PORT=7860 \
    USE_MOCK=1

USER appuser

# Healthcheck on Gradio UI port
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request, sys; \
import urllib.error; \
import time; \
url='http://127.0.0.1:7860/'; \
import urllib.request as u; \
sys.exit(0) if u.urlopen(url, timeout=2).getcode() == 200 else sys.exit(1)"

# Expose HF-facing port (Gradio)
EXPOSE 7860

# Run FastAPI on 8000 (internal) AND Gradio on 7860 (public)
CMD ["sh", "-c", "\
uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --workers ${UVICORN_WORKERS:-1} & \
python -m demo.app \
"]
