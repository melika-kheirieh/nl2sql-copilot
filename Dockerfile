# ---------- Stage 1: Build wheels ----------
FROM python:3.12-slim AS builder

# Set working directory for the build stage
WORKDIR /build

# Install system dependencies required to compile some Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Copy only requirements first (so Docker caching works efficiently)
COPY requirements.txt .

# Build all dependencies as wheel files inside /wheels
RUN pip install --upgrade pip && \
    pip wheel --wheel-dir /wheels -r requirements.txt


# ---------- Stage 2: Runtime image ----------
FROM python:3.12-slim AS runtime

# Set working directory for the application
WORKDIR /app

# Copy prebuilt wheels from the builder stage
COPY --from=builder /wheels /wheels

# Install dependencies from prebuilt wheels (no need to compile again)
COPY requirements.txt .
RUN pip install --no-cache-dir --find-links=/wheels -r requirements.txt

# Copy the actual application code
COPY . .

# Expose the FastAPI port
EXPOSE 8000

# Start FastAPI with Uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
