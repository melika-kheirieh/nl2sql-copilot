# Observability & Metrics

> **Single source of truth.**
> This document defines the **observability contract**, the **runtime metrics**, and the **debugging playbooks** for the NL2SQL Copilot system.
> If dashboards, alerts, or code change, **this document must be updated first**.

---

## 0. TL;DR (30-second read)

* The pipeline is **multi-stage and conditional**; not every non-OK outcome is an error.
* `ambiguous` is an **expected early-exit**, not a failure.
* **p95 per-stage latency** and **repair attempts** matter more than averages.
* Repair is **bounded** and observable; amplification is a first-class signal.

If users report slowness or failures, jump to **Section 6 — Debug Playbooks**.

---

## 1. Goals & Philosophy

Observability here is:

* **Truthful** — everything documented is visible in `/metrics`.
* **Decision-oriented** — every metric maps to a concrete debugging action.
* **Pipeline-aware** — stage-level signals beat HTTP-only metrics.
* **Tail-focused** — p95 reveals pain that averages hide.
* **Ambiguity-tolerant** — some non-OK outcomes are correct behavior.

Vanity metrics and high-cardinality labels are intentionally avoided.

---

## 2. Pipeline Model (As Implemented)

The NL2SQL pipeline is **multi-stage, conditional, and bounded**:

```
request
 → detector        (ambiguity detection)
 → planner         (plan)
 → generator       (SQL generation)
 → safety          (block / allow)
 → executor        (DB execution)
 → verifier        (semantic/result checks)
 → repair (loop)   (conditional + bounded; only after verifier failures)
```

### Semantics that matter

* **Early-exit is valid:** `detector` may return `status="ambiguous"`.
* **Repair is not happy-path:** it runs only after verifier failures.
* **Repair is bounded:** loops are limited to prevent amplification.
* **Stage names are canonical:**
  `detector, planner, generator, safety, executor, verifier, repair`

These names must remain stable to keep `/metrics`, dashboards, and rules aligned.

---

## 3. Runtime Metrics (Exposed at `/metrics`)

All metrics below are **defined and exposed** by the running system:

```bash
curl -fsS http://127.0.0.1:8000/metrics
```

Some counters may remain zero unless specific paths are exercised.

---

### 3.1 Stage Latency (Tail-Focused)

| Metric              | Type      | Labels  | Description                                    |
| ------------------- | --------- | ------- | ---------------------------------------------- |
| `stage_duration_ms` | histogram | `stage` | Duration per pipeline stage + `pipeline_total` |

**Recording rule**

* `nl2sql:stage_p95_ms` — p95 latency per stage

**Why**

* Averages hide tail pain.
* p95 isolates where the system degrades under load or retries.

**Decisions enabled**

* `generator` p95 ↑ → LLM latency / prompt size / provider variance
* `executor` p95 ↑ → DB execution / IO / timeout
* `verifier` or `repair` p95 ↑ → semantic mismatch driving repair

---

### 3.2 Pipeline Outcome (Correctness vs Failure)

| Metric                | Type    | Labels   | Description       |
| --------------------- | ------- | -------- | ----------------- |
| `pipeline_runs_total` | counter | `status` | Pipeline outcomes |

**Allowed statuses**

* `ok`
* `error`
* `ambiguous`

**Key rule**

> `ambiguous ≠ error`

**Decisions enabled**

* `error` ↑ → real reliability issue (infra, adapter, provider)
* `ambiguous` ↑ → input ambiguity / UX / clarification path

---

### 3.3 Repair Amplification (Hidden Performance Killer)

| Metric                  | Type    | Labels             | Description              |
| ----------------------- | ------- | ------------------ | ------------------------ |
| `repair_attempts_total` | counter | `stage`, `outcome` | Repair loop activity     |
| `repair_trigger_total`  | counter | `stage`, `reason`  | Why repair was triggered |

**`outcome` values**

`attempt | success | failed | skipped`

**Why**

Repair loops can inflate latency without changing error rate.
Counting attempts is the most direct amplification signal.

**Decisions enabled**

* attempts ↑ + stable errors → inefficiency (tighten bounds / improve verifier)
* low success rate → repair is ineffective (change strategy or stop retrying)

---

### 3.4 Stage Health & Error Breakdown

| Metric               | Type    | Labels                | Description                         |
| -------------------- | ------- | --------------------- | ----------------------------------- |
| `stage_calls_total`  | counter | `stage`, `ok`         | Stage executions by success/failure |
| `stage_errors_total` | counter | `stage`, `error_code` | Error classification                |

**Decisions enabled**

* executor failures ↑ → DB / adapter / timeout issues
* generator error_code ↑ → LLM or prompt contract regression

---

### 3.5 Safety & Verifier Events (Optional but Supported)

| Metric                    | Type    | Labels   | Notes                   |
| ------------------------- | ------- | -------- | ----------------------- |
| `safety_checks_total`     | counter | `ok`     | Allow / block checks    |
| `safety_blocks_total`     | counter | `reason` | Blocked queries by rule |
| `verifier_checks_total`   | counter | `ok`     | Verification results    |
| `verifier_failures_total` | counter | `reason` | Failure breakdown       |

These metrics may legitimately remain **zero** in early versions.
Zero does **not** imply broken wiring.

---

### 3.6 Cache Events (Optional)

| Metric               | Type    | Labels | Description      |
| -------------------- | ------- | ------ | ---------------- |
| `cache_events_total` | counter | `hit`  | Cache hit / miss |

Used to compute cache hit ratio.

---

## 4. HTTP-Level Metrics (API Surface)

| Metric                                         | Purpose                 |
| ---------------------------------------------- | ----------------------- |
| `http_requests_total{path,method,status_code}` | Traffic & 5xx detection |
| `http_request_latency_seconds{path,method}`    | End-to-end latency      |

**Rule**

HTTP metrics confirm *symptoms*.
Pipeline metrics identify *causes*.

---

## 5. Recording Rules & Alerts

Defined in:

```
infra/prometheus/rules.yml
```

### Recording rules

* `nl2sql:stage_p95_ms`
* `nl2sql:pipeline_success_ratio` (rolling window)

### Alerts (examples)

* **PipelineLowSuccessRatio** — sustained drop
* **GeneratorLatencyHigh** — generator p95 exceeds bound

Thresholds are conservative for demo/dev and must be tuned for production.

---

## 6. What Is *Not* an Error

The following are **expected system behaviors**:

* `pipeline_runs_total{status="ambiguous"}`
* Safety blocks (`safety_blocks_total`)
* Verifier failures that trigger **bounded repair**

They are observable, but they do not represent crashes.

---

## 7. Debug Playbooks (Fast Isolation)

### A. Users report slowness

**Signals**

* `nl2sql:stage_p95_ms` ↑ (which stage?)
* `repair_attempts_total{outcome="attempt"}` ↑

**Interpretation**

* generator p95 ↑ → LLM latency dominates
* executor p95 ↑ → DB dominates
* verifier + repair ↑ → repair amplification

**Next action**

Reduce repair amplification or fix the dominant stage.

---

### B. OK ratio drops

**Signals**

* `status="error"` ↑ vs `status="ambiguous"` ↑

**Interpretation**

* error ↑ → real reliability issue
* ambiguous ↑ → product / input ambiguity

---

### C. Safety / verifier spike

**Signals**

* `safety_blocks_total` ↑
* `verifier_failures_total` ↑

**Next action**

Inspect reasons, validate policy, and review prompt or input shifts.

---

## 8. Labeling Rules (Hard Constraints)

**Allowed labels only**

* `stage`, `status`, `ok`, `outcome`, `reason`, `error_code`

**Never add**

* user identifiers
* raw queries
* schema text
* table names
* database identifiers

Violating this will break Prometheus via cardinality explosion.

---

## 9. Local Verification

```bash
make demo-up
make curl-metrics
curl -fsS http://127.0.0.1:9090/api/v1/targets
```

---

## Final Takeaway

This observability design is:

* Pipeline-aware
* Tail-focused
* Ambiguity-tolerant
* Repair-aware
* Decision-driven

Dashboards and alerts are **derived views**.
This document is the contract.
