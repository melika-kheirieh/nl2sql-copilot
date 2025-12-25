# Observability and Metrics

This document describes the **metrics actually emitted at runtime** by the
NL2SQL Copilot pipeline, and how they are intended to be interpreted.

The goal is **truthful observability**:
everything documented here can be verified via `/metrics`.

---

## 📊 Metrics exposed

| Metric | Type | Labels | Description |
|------|------|--------|-------------|
| `stage_duration_ms` | histogram | `stage` | Duration per pipeline stage: `detector`, `planner`, `generator`, `safety`, `executor`, `verifier`, `pipeline_total`, `repair` |
| `stage_calls_total` | counter | `stage`, `ok` | Number of executions per stage, split by success/failure |
| `stage_errors_total` | counter | `stage`, `error_code` | Stage-level errors grouped by error code |
| `pipeline_runs_total` | counter | `status` | Pipeline runs by outcome: `ok`, `error`, `ambiguous` |
| `safety_checks_total` | counter | `ok` | Safety checks pass/fail count |
| `verifier_checks_total` | counter | `ok` | Verification pass/fail count |
| `cache_events_total` | counter | `hit` | Cache hit/miss events |

All metrics above are **actively emitted** and visible via:

```bash
curl -fsS http://127.0.0.1:8000/metrics
````

---

## ⚙️ Recording & alerting rules

Recording and alerting rules are defined in:

```text
infra/prometheus/rules.yml
```

### Recording rules

* **`nl2sql:stage_p95_ms`**
  95th percentile latency per pipeline stage.

* **`nl2sql:pipeline_success_ratio`**
  Rolling success ratio over a 5-minute window.

### Alerts

* **`PipelineLowSuccessRatio`**
  Triggered when success ratio drops below threshold for a sustained window.

* **`GeneratorLatencyHigh`**
  Triggered when generator p95 latency exceeds the configured bound.

> Note: Alert thresholds are tuned conservatively for **demo and development**
> environments. LLM-backed stages (e.g. planner, generator) may show higher
> latencies locally. Adjust thresholds for production workloads.

---

## 🧪 Local verification

To verify that metrics are being emitted correctly:

```bash
make demo-up
make curl-metrics
```

To inspect targets and rule evaluation:

```bash
curl -fsS "http://127.0.0.1:9090/api/v1/targets"
```

---

## 🛠 Planned metrics (not emitted yet)

The following metrics are part of the **intended observability design**,
but are **not currently emitted** by the system:

* `safety_blocks_total{reason=...}`
  Breakdown of blocked queries by safety rule.

* `verifier_failures_total{reason=...}`
  Verification failures grouped by reason.

These may be added in future iterations once label semantics and aggregation
strategy are finalized.

---

## Interpretation notes

* Metrics are designed to support **debugging, evaluation, and capacity
  reasoning**, not marketing claims.
* Some failures are **intentional** (e.g. safety or cost guardrails) and should
  be interpreted as correct behavior.
* Always prefer **distributions and trends** (p95, ratios, windows) over
  single-point values.

If you change prompts, models, or pipeline structure, re-evaluate metrics
and dashboards accordingly.
