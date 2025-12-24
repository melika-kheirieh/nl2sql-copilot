# Full Stack Bring-Up Runbook (Local)

This runbook brings up **everything** end-to-end:
- FastAPI app
- Prometheus + Grafana + Alertmanager (infra stack)
- Smoke traffic (so dashboards have real data)
- Metrics validation (Prometheus queries)
- Gradio demo UI
- Streamlit benchmark UI
- Evaluation smoke

> Tip: Keep **multiple terminals** open. Some commands are **blocking** by design.

---

## Prerequisites

```bash
docker --version
docker compose version
python3 --version
```

(Optional but recommended)

```bash
cp .env.example .env
# edit .env if you want (API keys, etc.)
```

---

## 0) One-time (recommended) — Python deps

```bash
make venv
make install
```

---

## 1) Start infra (Prometheus + Grafana + Alertmanager)

**Terminal A**

```bash
make infra-up
make infra-ps
```

Health checks:

```bash
curl -fsS http://127.0.0.1:9090/-/ready && echo "PROM READY ✅"
curl -fsS http://127.0.0.1:3000/api/health && echo "GRAFANA READY ✅"
```

Open in browser:
- Grafana: http://127.0.0.1:3000
- Prometheus: http://127.0.0.1:9090

---

## 2) Start the API (FastAPI)

**Terminal B**

```bash
make demo-up
```

API health:

```bash
make curl-health
```

---

## 3) Generate real traffic (Smoke)

**Terminal C**

```bash
make demo-smoke
```

Expected:
- “normal” NL queries return `HTTP 200`
- unsafe input (e.g., DELETE) is blocked, but the smoke should still pass ✅

---

## 4) Validate Prometheus signals (recording rules + queries)

**Terminal C**

```bash
make demo-metrics
```

If you want to quickly confirm the app is exposing metrics:

```bash
make curl-metrics
```

---

## 5) Grafana sanity (avoid “No data”)

In Grafana:
- Time range: **Last 15m**
- Refresh: **Off**
- Confirm at least **stage latency** and **events** panels have data.

> If success ratios look ugly/unstable, don’t screenshot them for the README.

---

## 6) Run Gradio demo UI (optional)

**Terminal D**

```bash
make demo-ui-up
```

(Gradio will print a local URL.)

---

## 7) Run Benchmark UI (Streamlit)

**Terminal E**

```bash
make bench-ui
```

---

## 8) Run evaluation smoke

```bash
make eval-smoke
```

(Optional “pro” mode)

```bash
make eval-pro-smoke
# or
make eval-pro
```

---

## 9) Shut down

Stop infra:

```bash
make infra-down
```

Clean docker artifacts (careful; may remove volumes):

```bash
make clean-docker
```

---

## Troubleshooting

### Prometheus connection refused
Infra isn’t up:

```bash
make infra-up
```

### Grafana shows “No data”
Check Prometheus targets:

```bash
curl -fsS "http://127.0.0.1:9090/api/v1/targets" | head -c 2000
```

Look for `job=nl2sql` with `health=up`.

### Ports
Common ports:
- API: 8000
- Prometheus: 9090
- Grafana: 3000
