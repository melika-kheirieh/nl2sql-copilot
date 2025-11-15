FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860 \
    GRADIO_SERVER_NAME=0.0.0.0

WORKDIR /home/user/app

# Copy requirements first
COPY requirements.txt .

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc build-essential && \
    pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir -r requirements.txt && \
    apt-get purge -y gcc build-essential && \
    apt-get autoremove -y && apt-get clean -y

# Copy full repo — but due to .dockerignore, ONLY demo.db from data/ is included
COPY . .

# Optional debug
# RUN ls -R /home/user/app/data

EXPOSE 7860

ENTRYPOINT []
CMD ["python", "-u", "start.py"]
