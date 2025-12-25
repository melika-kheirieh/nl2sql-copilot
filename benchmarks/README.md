# Benchmarks & Evaluation

This document describes the evaluation layers used in **NL2SQL Copilot**.
The goal is not to maximize headline accuracy numbers, but to provide
**stable, inspectable, and reality-aligned signals** about system behavior.

Evaluation is intentionally split into three layers with increasing cost and fidelity.

---

## 1) Smoke Evaluation (API-level)

### Purpose

* End-to-end sanity check of the running system
* Verify API health, pipeline wiring, and error handling
* Ensure the **metrics endpoint is healthy and core metrics are exposed**

### Characteristics

* Very fast
* Low cost
* Deterministic
* CI-friendly

### What it validates

* API boot & health (`/healthz`)
* Full pipeline execution (detector → planner → generator → safety → executor → verifier)
* Error paths (e.g. blocked full-table scans)
* **Metrics endpoint and core metrics exposure**

### Command

```bash
make demo-smoke
```

### Notes

* `make demo-up` only starts the demo stack (API + Prometheus + Grafana).
* `make demo-smoke` is the actual API-level smoke test that issues queries
  and validates pipeline behavior.

---

## 2) Eval Lite (Pipeline-level, Demo DB)

### Purpose

* Validate semantic correctness of the pipeline on a small, controlled database
* Catch regressions in planning, SQL generation, safety, and verification logic

### Characteristics

* Fast
* Stable
* No external datasets
* CI-friendly

### Dataset

* Built-in demo database (`data/demo.db`)
* Small, deterministic schema

### What it measures

* SQL validity
* Pipeline success vs failure
* Safety guardrails (e.g. full scans, missing limits)
* Repair and verification behavior

### What it explicitly does NOT measure

* Benchmark accuracy against gold labels
* Cross-domain generalization

This is an **intentional design choice** to keep Eval Lite stable, repeatable,
and suitable for automated checks.

### Command

```bash
make eval-smoke
```

---

## 3) Eval Pro (Spider)

### Purpose

* Research-oriented evaluation against a standard NL2SQL benchmark
* Measure execution accuracy and SQL correctness

### Characteristics

* Expensive
* Slow
* Non-deterministic
* Not CI-friendly

### Dataset

* Spider benchmark (not committed to the repo)

### Usage

```bash
make eval-pro-smoke   # small subset
make eval-pro         # full evaluation
```

### Notes

* This layer is **optional** and intended for deeper analysis.
* It is not part of the default development loop.
* Runtime and cost variability are expected.

---

## Benchmark UI

A Streamlit-based UI is provided to inspect benchmark results visually.

### Command

```bash
make bench-ui
```

### Features

* Load and inspect stored evaluation results
* Compare runs
* Visualize metrics and distributions

---

## Plots & Artifacts

Evaluation results and plots are stored under:

```text
benchmarks/results/
```

Plot generation:

```bash
make plot-pro
```

Artifacts are intentionally treated as **outputs**, not source of truth.
Raw metrics and logs should be consulted for detailed analysis.

---

## Design Philosophy

* Prefer **truthful signals** over inflated metrics
* Separate *system health* from *research evaluation*
* Make failure modes visible and inspectable
* Keep the default workflow fast and developer-friendly

This benchmark structure is designed to support
both **engineering iteration** and **deeper research analysis**
without conflating the two.
