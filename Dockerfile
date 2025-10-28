# ---------- Stage 1: Build wheels ----------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

# If you truly need to compile deps, keep build-essential.
# If you use psycopg[binary], you can usually drop libpq-dev safely.
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
    PYTHONUNBUFFERED=1

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

# Optional: healthcheck (needs curl)
# RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
# HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
#   CMD curl -fsS http://localhost:8000/healthz || exit 1

# Drop privileges
USER appuser

EXPOSE 8000

# Start FastAPI with Uvicorn
# Tip: you can tweak workers via env in deployment (e.g., UVICORN_WORKERS=2)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
