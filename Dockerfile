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

# ---------- Stage 2: Runtime ----------
FROM python:3.12-slim AS runtime

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates && rm -rf /var/lib/apt/lists/*

RUN useradd -m appuser

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --find-links=/wheels -r requirements.txt && \
    rm -rf /wheels

COPY . .

RUN chown -R appuser:appuser /app

USER appuser

ENV GRADIO_SERVER_NAME=0.0.0.0 \
    GRADIO_SERVER_PORT=7860 \
    USE_MOCK=1

# Healthcheck ensures Gradio frontend is alive
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860', timeout=2)"

# Hugging Face Spaces expect public app on port 7860
EXPOSE 7860

# Unified startup script
CMD ["python", "start.py"]
