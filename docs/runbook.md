# Full Stack Bring-Up Runbook (Docker-first Demo)

This runbook brings up **everything** end-to-end using the **Docker demo stack**:
- nl2sql API (Docker)
- Prometheus + Grafana + Alertmanager (infra stack)
- Demo traffic warmer (so dashboards have real data)
- Metrics validation (Prometheus queries)
- Gradio demo UI
- Streamlit benchmark UI
- Evaluation smoke

> Tip: Keep **multiple terminals** open. Some commands are **blocking** by design.

> Important:
> - `make demo-up` starts the **Docker demo stack** (single source of truth).
> - Local dev (uvicorn) is separate (`make dev-up`) and should not run alongside the demo stack unless you know what you’re doing.

---

## Prerequisites

```bash
docker --version
docker compose version
python3 --version
````

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

## 1) Start demo stack (API + Prometheus + Grafana)

**Terminal A**

```bash
make demo-up
make infra-ps
```

Health checks:

```bash
curl -fsS http://127.0.0.1:9090/-/ready && echo "PROM READY ✅"
curl -fsS http://127.0.0.1:3000/api/health && echo "GRAFANA READY ✅"
```

Open in browser:

* Grafana: [http://127.0.0.1:3000](http://127.0.0.1:3000)
* Prometheus: [http://127.0.0.1:9090](http://127.0.0.1:9090)

API health:

```bash
make curl-health
```

---

## 2) Generate real traffic (recommended for dashboards / screenshots)

**Terminal B**

```bash
make demo-traffic-up
```

This runs a small in-docker traffic generator so Prometheus sees real events (avoids “0” / “No data”).

---

## 3) Validate Prometheus signals (queries)

**Terminal B**

```bash
make demo-metrics
```

If you want to quickly confirm the app is exposing metrics:

```bash
make curl-metrics
```

---

## 4) API smoke (correctness check)

Run smoke only when you want to verify behavior end-to-end:

```bash
make demo-smoke
```

Expected:

* “normal” NL queries return `HTTP 200`
* unsafe input (e.g., DELETE) is blocked, but smoke should still pass ✅

---

## 5) Grafana sanity (avoid “No data”)

In Grafana:

* Time range: **Last 15m**
* Refresh: **Off**
* Confirm at least stage latency and events panels have data.

> Tip: If traffic is low, prefer window-based panels (e.g., ratios using `increase(...)`).

---

## 6) Run Gradio demo UI (optional)

**Terminal C**

```bash
make demo-ui-up
```

(Gradio will print a local URL.)

---

## 7) Run Benchmark UI (Streamlit)

**Terminal D**

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

Stop demo traffic (if running):

```bash
make demo-traffic-down
```

Stop demo stack:

```bash
make demo-down
```

Clean docker artifacts (careful; may remove volumes):

```bash
make clean-docker
```

---

## Troubleshooting

### Prometheus connection refused

Demo stack isn’t up:

```bash
make demo-up
```

### Grafana shows “No data”

Check Prometheus targets:

```bash
curl -fsS "http://127.0.0.1:9090/api/v1/targets" | head -c 2000
```

Look for `job=nl2sql` with `health=up`.

### Ports

Common ports:

* API (Docker demo): 8000
* Prometheus: 9090
* Grafana: 3000

---

## Local development (separate from demo)

If you want local uvicorn for dev/debug:

```bash
make dev-up
```

This is **not part of the demo stack**.
