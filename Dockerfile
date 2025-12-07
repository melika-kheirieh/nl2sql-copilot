# -------------------------------------
# Base image (runtime)
# -------------------------------------
FROM python:3.12-slim AS base

# Prevent Python from writing .pyc files and force stdout flush
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=7860

# Install tini (proper init process) + curl (for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends tini curl \
   && rm -rf /car/lib/apt/lists/*

WORKDIR /app


# -------------------------------------
# Builder stage (dependencies)
# -------------------------------------
FROM base AS builder

WORKDIR /app

COPY requirements.txt .

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc \
    && pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && rm -rf /var/lib/apt/lists/*


# -------------------------------------
# Builder stage (dependencies)
# -------------------------------------
FROM base AS final

WORKDIR /app

# Copy installed dependencies from builder
COPY --from=builder /usr/local /usr/local

COPY . .

# Expose ports (FastAPI + Gradio)
EXPOSE 7860
EXPOSE 8000

# tini handles PID1, zombie reaping, and signals
ENTRYPOINT ["tini", "--"]

CMD ["python", "-u", "start.py"]
