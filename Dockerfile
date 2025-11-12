FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860 \
    GRADIO_SERVER_NAME=0.0.0.0

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -U pip && pip install --no-cache-dir -r requirements.txt

EXPOSE 7860

# Ensure base image ENTRYPOINT (if any) doesn't override ours
ENTRYPOINT []
RUN echo "=== REBUILD $(date) ==="
CMD ["python", "-u", "start.py"]
