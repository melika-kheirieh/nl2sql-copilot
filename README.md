# NL2SQL Copilot — Safety-First, Production-Grade Text-to-SQL

[![CI](https://github.com/melika-kheirieh/nl2sql-copilot/actions/workflows/ci.yml/badge.svg)](https://github.com/melika-kheirieh/nl2sql-copilot/actions/workflows/ci.yml)
[![Docker](https://img.shields.io/badge/docker-ready-blue?logo=docker)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A **production-oriented Natural Language → SQL system** built around **explicit safety guarantees, verification, evaluation, and observability**.

This project treats LLMs as **untrusted components** inside a constrained, measurable system — not as autonomous agents.

---

## Demo (End-to-End)
A live interactive demo is available on Hugging Face Spaces: 👉 [**Try the Demo**](https://huggingface.co/spaces/melikakheirieh/nl2sql-copilot)
<p align="center">
  <img src="docs/assets/screenshots/demo_list_albums_total_sales.png" width="700">
</p>



## Quickstart (Local)

### 1) Install
```bash
make install
```

### 2) Run API (Terminal 1)
```bash
make demo-up
```

### 3) Smoke (Terminal 2)
```bash
make demo-smoke
```

### 4) Observability stack (optional)
```bash
make infra-up
```

Then (optional Prometheus snapshot):
```bash
make demo-metrics
```

---

## Why this exists

Most Text-to-SQL demos answer:
> *“Can the model generate SQL?”*

This project answers a harder question:
> **“Can NL→SQL be operated safely as a production system?”**

That means:
- controlling **what the model sees** (context engineering),
- constraining **what it is allowed to execute** (safety),
- verifying results before returning them,
- and continuously measuring **accuracy, latency, and cost**.

---

## What the system does

- Converts natural-language questions into **safe, verified SQL**
- Enforces **SELECT-only execution policies** (no DDL / DML)
- Uses **explicit context engineering** (schema packing + rules)
- Applies **execution and verification guardrails**
- Tracks **per-stage latency, errors, and cost signals**
- Evaluates accuracy on **Spider** with a structured error taxonomy
- Exposes **Prometheus metrics** and **Grafana dashboards**

---

## Architecture & Pipeline

<p align="center">
  <img src="docs/assets/architecture.png" width="720">
</p>

```

Detector
→ Planner
→ Generator
→ Safety Guard
→ Executor
→ Verifier
→ Repair (bounded)

````

Each stage:
- has a single responsibility,
- emits structured traces,
- is independently testable.

---

## Core design principles

### 1) Context engineering over prompt cleverness
The model never sees the raw database blindly.

Instead, it receives:
- a **deterministic schema pack**,
- explicit constraints (e.g. SELECT-only, LIMIT rules),
- and a bounded context budget.

---

### 2) Safety is enforced, not suggested
Safety policies are **system-level constraints**, not prompt instructions.

Current guarantees:
- Single-statement execution
- `SELECT` / `WITH` only
- No DDL / DML
- Execution time & result guards

Violations are **blocked**, not repaired.

---

### 3) Verification before trust
Queries are executed in a controlled environment and verified for:
- structural validity,
- schema consistency,
- execution correctness.

Errors are surfaced explicitly and classified — not hidden.

---

### 4) Repair for reliability, not illusion
Repair exists to improve **system robustness**, not to chase accuracy at all costs.

- Triggered only for eligible error classes
- Disabled for safety violations
- Strictly bounded (no infinite loops)

---

## Repository structure

```text
app/                 # FastAPI service (routes, schemas, wiring)
nl2sql/              # Core NL→SQL pipeline
adapters/            # Adapter implementations (DBs, LLMs)

benchmarks/          # Evaluation runners & outputs
tests/               # Unit & integration tests


infra/               # Docker Compose + observability stack (Prometheus/Grafana/Alertmanager)
configs/             # Runtime configs
scripts/             # Tooling & helpers

demo/                # Demo app
ui/                  # UI surface
docs/                # Docs & screenshots
data/                # Local data & demo DBs
````

---

## Observability & GenAIOps

<p align="center">
  <img src="docs/assets/grafana.png" width="720">
</p>

Tracked signals include:

* End-to-end latency (p50 / p95)
* Per-stage latency
* Success / failure counts
* Safety blocks
* Repair attempts & win-rate
* Cache hit / miss ratio
* Token usage (prompt / completion)

These metrics make **accuracy vs latency vs cost trade-offs** explicit.

---

## Evaluation

The system is evaluated on the **Spider benchmark**.

```bash
make eval-spider
```

Metrics:

* Exact Match (EM)
* Execution Accuracy (ExecAcc)
* Semantic Match (SM)
* Latency distributions
* Error taxonomy breakdown

A **golden regression set** is used to detect accuracy regressions.

---

## Roadmap

* AST-based SQL allowlisting
* Query cost heuristics (EXPLAIN-based)
* Cross-database adapters
* CI-level eval gating

---

## What this project is *not*

* Not a prompt-only demo
* Not an autonomous agent playground
* Not optimized for leaderboard chasing

It is a **deliberately constrained, observable, and defendable AI system** —
built to be discussed seriously in production engineering interviews.
