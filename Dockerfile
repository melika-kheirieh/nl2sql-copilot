FROM python:3.12-slim

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=7860 \
    GRADIO_SERVER_NAME=0.0.0.0

WORKDIR /home/user/app

# Step 1: Copy requirements to ensure pip install works
COPY requirements.txt /home/user/app/requirements.txt

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc build-essential && \
    pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir -r requirements.txt && \
    apt-get purge -y gcc build-essential && \
    apt-get autoremove -y && apt-get clean -y

# Step 2: Copy the rest of the repo including data folder
COPY . /home/user/app/

# Optional check:
RUN ls -R /home/user/app/data

EXPOSE 7860

ENTRYPOINT []
CMD ["python", "-u", "start.py"]
