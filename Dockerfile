FROM python:3.12-slim

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=7860 \
    GRADIO_SERVER_NAME=0.0.0.0

# HuggingFace mounts your repository at /home/user/app in RUN time
WORKDIR /home/user/app

# Install Python dependencies — requirements.txt is mounted at run time → accessible here
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc build-essential && \
    pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir -r requirements.txt && \
    apt-get purge -y gcc build-essential && \
    apt-get autoremove -y && apt-get clean -y

EXPOSE 7860

ENTRYPOINT []
CMD ["python", "-u", "start.py"]
