# Observability and Metrics

This document describes the **metrics exposed at runtime** by the NL2SQL Copilot
pipeline and how they should be interpreted.

The goal is **truthful observability**:
everything documented here can be verified via `/metrics`, and counters that
remain zero are explicitly called out.

---

## 📊 Metrics exposed

| Metric                  | Type      | Labels                | Description                                                                                                                   |
| ----------------------- | --------- | --------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `stage_duration_ms`     | histogram | `stage`               | Duration per pipeline stage: `detector`, `planner`, `generator`, `safety`, `executor`, `verifier`, `repair`, `pipeline_total` |
| `stage_calls_total`     | counter   | `stage`, `ok`         | Number of executions per stage, split by success/failure                                                                      |
| `stage_errors_total`    | counter   | `stage`, `error_code` | Stage-level errors grouped by error code                                                                                      |
| `pipeline_runs_total`   | counter   | `status`              | Pipeline runs by outcome: `ok`, `error`, `ambiguous`                                                                          |
| `safety_checks_total`   | counter   | `ok`                  | Safety checks pass/fail count (may remain zero; see notes below)                                                              |
| `verifier_checks_total` | counter   | `ok`                  | Verification pass/fail count (may remain zero; see notes below)                                                               |
| `cache_events_total`    | counter   | `hit`                 | Cache hit/miss events                                                                                                         |

All metrics above are **defined and exposed** via:

```bash
curl -fsS http://127.0.0.1:8000/metrics
```

Some counters may remain zero unless triggered by specific stages (see below).

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
  Triggered when the success ratio drops below a threshold for a sustained window.

* **`GeneratorLatencyHigh`**
  Triggered when generator p95 latency exceeds the configured bound.

> Note: Alert thresholds are tuned conservatively for **demo and development**
> environments. LLM-backed stages (e.g. planner, generator) may show higher
> latencies locally. Adjust thresholds for production workloads.

---

## 🧪 Local verification

To verify that metrics are being exposed correctly:

```bash
make demo-up
make curl-metrics
```

To inspect Prometheus targets and rule evaluation:

```bash
curl -fsS "http://127.0.0.1:9090/api/v1/targets"
```

---

## 🛠 Defined metrics (currently not incremented)

The following series are **defined and visible** in `/metrics`, but may remain
**zero** in the current implementation because they are not incremented by the
pipeline stages yet:

* `safety_blocks_total{reason=...}`
  Breakdown of blocked queries by safety rule.

* `verifier_failures_total{reason=...}`
  Verification failures grouped by reason.

Dashboard panels referencing these counters may therefore show no activity; this
is expected and does **not** indicate a metrics wiring issue.

---

## Interpretation notes

* Metrics are designed to support **debugging, evaluation, and capacity
  reasoning**, not marketing claims.
* Some failures are **intentional** (e.g. safety or cost guardrails) and should
  be interpreted as correct behavior.
* Always prefer **distributions and trends** (p95, ratios, windows) over
  single-point values.

If you change prompts, models, or pipeline structure, re-evaluate metrics and
dashboards accordingly.
