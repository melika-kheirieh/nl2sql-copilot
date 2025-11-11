# ---------- Base ----------
FROM python:3.12-slim
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# ---------- Dependencies ----------
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    pip install --no-cache-dir -r requirements.txt && \
    rm -rf /var/lib/apt/lists/*

# ---------- Copy app ----------
COPY . .

# ---------- Metadata & Healthcheck ----------
LABEL org.opencontainers.image.title="nl2sql-copilot" \
      org.opencontainers.image.description="NL2SQL Copilot full-stack demo (FastAPI + Gradio)" \
      org.opencontainers.image.version="1.0.0"

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -fs http://localhost:8000/healthz || exit 1

# ---------- Run both backend & frontend ----------
EXPOSE 7860 8000
CMD ["bash", "-c", "uvicorn main:app --host 0.0.0.0 --port 8000 & python app.py"]
