# ---------- Stage 1: Builder ----------
FROM python:3.12-slim AS builder
WORKDIR /app

# Install system deps (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ---------- Stage 2: Runtime ----------
FROM python:3.12-slim AS runtime
WORKDIR /app

# Copy from builder
COPY --from=builder /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy project files
COPY . .

# ---------- Metadata & Healthcheck ----------
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:7860/ || exit 1

# ---------- Run the App ----------
# 👉 Gradio version
# CMD ["python", "app.py"]

# 👉 FastAPI version
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
