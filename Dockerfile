# ---------- Stage 1: Build wheels ----------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

# If you truly need to compile deps, keep build-essential.
# If you use psycopg[binary], you can safely drop libpq-dev.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
  && rm -rf /var/lib/apt/lists/*

# Leverage Docker layer caching
COPY requirements.txt .

# Build all dependencies as wheel files inside /wheels
RUN pip install --upgrade pip && \
    pip wheel --wheel-dir /wheels -r requirements.txt


# ---------- Stage 2: Runtime image ----------
FROM python:3.12-slim AS runtime

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Create a non-root user (security best practice)
RUN useradd -m appuser

# Copy prebuilt wheels from the builder stage
COPY --from=builder /wheels /wheels

# Install dependencies from wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --find-links=/wheels -r requirements.txt && \
    rm -rf /wheels

# Copy the actual application code
COPY . .

# ---------- Metadata & Healthcheck ----------
LABEL org.opencontainers.image.title="nl2sql-copilot" \
      org.opencontainers.image.description="Modular Text-to-SQL Copilot (FastAPI)" \
      org.opencontainers.image.source="https://github.com/melika-kheirieh/nl2sql-copilot" \
      org.opencontainers.image.authors="melika.kheirieh" \
      org.opencontainers.image.licenses="MIT"

# Lightweight healthcheck (no curl)
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request, sys; \
  sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).getcode() == 200 else sys.exit(1)"

# Drop privileges
USER appuser

EXPOSE 8000

# Start FastAPI with Uvicorn
# (UVICORN_WORKERS can be overridden at runtime)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --workers ${UVICORN_WORKERS:-1}"]
