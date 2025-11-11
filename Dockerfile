# ---------- Base ----------
FROM python:3.12-slim AS base

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ---------- Install dependencies ----------
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir supervisor

# ---------- Copy source ----------
COPY . .

# ---------- Metadata & Healthcheck ----------
LABEL maintainer="melika kheirieh"
LABEL description="NL2SQL Copilot full stack (FastAPI + Gradio)"

# lightweight healthcheck without curl
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')" || exit 1

# ---------- Supervisor config ----------
RUN echo "[supervisord]" > /etc/supervisord.conf \
 && echo "nodaemon=true" >> /etc/supervisord.conf \
 && echo "" >> /etc/supervisord.conf \
 && echo "[program:fastapi]" >> /etc/supervisord.conf \
 && echo "command=uvicorn main:app --host 0.0.0.0 --port 8000" >> /etc/supervisord.conf \
 && echo "autostart=true" >> /etc/supervisord.conf \
 && echo "" >> /etc/supervisord.conf \
 && echo "[program:gradio]" >> /etc/supervisord.conf \
 && echo "command=python app.py" >> /etc/supervisord.conf \
 && echo "autostart=true" >> /etc/supervisord.conf

# ---------- Ports ----------
EXPOSE 7860
EXPOSE 8000

# ---------- Entrypoint ----------
CMD ["supervisord", "-c", "/etc/supervisord.conf"]
