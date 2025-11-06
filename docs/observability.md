# Observability and Metrics

This module adds full observability for the NL2SQL Copilot pipeline.

## 📊 Metrics exposed

| Metric | Type | Labels | Description |
|--------|------|---------|--------------|
| `stage_duration_ms` | histogram | `stage` | Duration per stage (detector, planner, generator, safety, executor, verifier) |
| `pipeline_runs_total` | counter | `status` | Pipeline runs by outcome (`ok`, `error`, `ambiguous`) |
| `safety_checks_total`, `safety_blocks_total` | counter | `reason` | Number of safety checks and blocked queries |
| `verifier_checks_total`, `verifier_failures_total` | counter | `reason` | Number of verification passes and failures |

---

## ⚙️ Recording & Alerting Rules

Defined in `prometheus/rules.yml`:

- **`nl2sql:stage_p95_ms`** – 95th percentile latency per stage
- **`nl2sql:pipeline_success_ratio`** – 5-minute success ratio
- Alerts:
  - `PipelineLowSuccessRatio` (<90% for 10m)
  - `GeneratorLatencyHigh` (>1500 ms for 5m)
  - `SafetyBlocksSpike` (>0.5/min)

---

## 🧪 Local Testing

1. Start Prometheus
   ```bash
   make prom-up
