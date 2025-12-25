# 🧭 NL2SQL Copilot — Demo Runbook (Canonical)

**Scope**
This runbook documents **only the Docker demo stack**.

* Local development (`uvicorn`, `make dev-up`) is **explicitly out of scope**
* This document is **operational**, not educational
* Audience: reviewer, infra-minded engineer, future you

**Single Source of Truth**

* `Makefile` (canonical targets only)
* `docker-compose.yml` (demo stack implementation)

If a command appears here, it **exists and is canonical**.
If it does not exist in the Makefile, it **must not appear here**.

---

## 🧠 Mental Model (Read This First)

This project is **demo-first**, not dev-first.

Key principles:

* `make demo-up` brings up the **entire real system**
* Some failures are **intentional guardrails**
* Low cache hit ratio at start is **expected**
* 4xx / 5xx responses are **not automatically bugs**

If you approach this like a toy demo, things will look “wrong”.
They are not.

---

## 0️⃣ Golden Path (10-minute, reviewer-proof)

This is the **minimum trusted path**.

```bash
make clean-docker
make demo-up
make demo-smoke
```

### Expected behavior

* Valid NL queries → `HTTP 200`
* Unsafe intent (DELETE / UPDATE / etc.) → blocked
* Smoke still **PASS**

If this fails:

* Either the system is broken
* Or the documentation is lying

Both are unacceptable.

---

## 1️⃣ Bring up the demo stack

```bash
make demo-up
```

This is the **single source of truth** for the demo.

It brings up:

* NL2SQL API (Docker)
* Prometheus
* Grafana
* Alertmanager (if enabled)

Verify containers:

```bash
make infra-ps
```

Verify API health:

```bash
make curl-health
```

---

## 2️⃣ Observability sanity check

Metrics endpoint:

```bash
make curl-metrics
```

Grafana:

* URL: `http://127.0.0.1:3000`
* Time range: **Last 15m**
* Refresh: **Off**

Expected:

* Stage latency panels have data
* Ratios may be noisy or low (normal with small workload)

---

## 3️⃣ Intentional demo behavior (this is not a bug)

The following behaviors are **by design**:

* Non-SELECT intent is blocked early (safety guardrail)
* Cache hit ratio starts low
* Some errors are mapped and returned as contract-based failures

If these behaviors disappear, the pipeline is **less correct**, not more.

---

## 4️⃣ API smoke (end-to-end correctness)

```bash
make demo-smoke
```

This validates:

* Full pipeline execution
* Safety enforcement
* Verifier behavior
* Error mapping semantics

Intentional failures are treated as **PASS**.

Any unexpected failure here is a real issue.

---

## 5️⃣ UI layers (optional, real backend)

### Gradio demo (user-facing)

Gradio talks to the **real running backend**.
No mocks. No stubs.

```bash
make demo-up
```

Gradio characteristics:

* Real API
* Real metrics
* Real error contracts

---

### Benchmark UI (Streamlit)

```bash
make bench-ui
```

Purpose:

* Inspect benchmark results
* Visualization only

Not:

* A correctness gate
* A user demo

---

## 6️⃣ Evaluation

### Smoke evaluation

```bash
make eval-smoke
```

### Pro evaluation (optional)

```bash
make eval-pro-smoke
make eval-pro
```

Notes:

* Spider dataset is intentionally **not** committed
* Smoke evaluation remains meaningful without Spider

---

## 7️⃣ Shutdown and reset

Stop demo stack:

```bash
make demo-down
```

Full cleanup (containers, volumes):

```bash
make clean-docker
```

⚠️ This removes Docker volumes.

---

## 8️⃣ Explicitly out of scope

The following are **intentionally excluded** from this runbook:

* `uvicorn`
* `make dev-up`
* Local debugging workflows
* Ad-hoc Docker commands

Reason:
This runbook exists for **demo, release, and review**, not development.

---

## 🧨 Troubleshooting (minimal, opinionated)

### Grafana shows “No data”

* Set time range to **Last 15m**
* Run `make demo-smoke` to generate traffic

### 500 response

* Unsafe query → expected
* Pipeline crash → bug

### Confusion between dev and demo

* You are operating outside this runbook

---

## ✅ Contract Summary

If this works:

```bash
make clean-docker
make demo-up
make demo-smoke
```

Then the project is:

* Demo-ready
* Reviewer-ready
* Release-ready

If not:

* Either the code or the docs must change
