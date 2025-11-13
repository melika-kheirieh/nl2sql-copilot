---
title: NL2SQL Copilot — Full Stack Demo
emoji: 🧩
colorFrom: indigo
colorTo: blue
sdk: docker
pinned: false
---
# 🧩 **NL2SQL Copilot — Natural-Language → Safe SQL**
[![CI](https://github.com/melika-kheirieh/nl2sql-copilot/actions/workflows/ci.yml/badge.svg)](https://github.com/melika-kheirieh/nl2sql-copilot/actions/workflows/ci.yml)
[![Docker](https://img.shields.io/badge/docker-ready-blue?logo=docker)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)


**Modular Text-to-SQL Copilot built with FastAPI & Pydantic-AI.**
Generates *safe, verified, executable SQL* through a multi-stage agentic pipeline.
Includes: schema introspection, self-repair, Spider benchmarks, Prometheus metrics, and a full demo UI.

🚀 **Live Demo:**
👉 **[https://huggingface.co/spaces/melika-kheirieh/nl2sql-copilot](https://huggingface.co/spaces/melika-kheirieh/nl2sql-copilot)**

---

# **1) Quick Start**

```bash
git clone https://github.com/melika-kheirieh/nl2sql-copilot
cd nl2sql-copilot
make setup       # install dependencies
make run         # start API + Gradio UI
```

Open:

* [http://localhost:8000](http://localhost:8000) (FastAPI Swagger UI)
* [http://localhost:7860](http://localhost:7860) (Gradio Demo)

---

# **2) Demo (Gradio UI)**

The demo supports:

* Uploading any SQLite database
* Asking natural-language questions
* Viewing generated SQL
* Viewing query results
* Full multi-stage trace (detector → planner → generator → safety → executor → verifier → repair)
* Per-stage timings
* Example queries
* And a default demo DB (no upload required)

Everything runs on the same backend as the API.

---

# **3) Agentic Architecture**

```
user query
    ↓
detector      (ambiguity, missing info)
planner       (schema reasoning + task decomposition)
generator     (SQL generation)
safety        (SELECT-only validation)
executor      (sandboxed DB execution)
verifier      (semantic + execution checks)
repair        (minimal-diff SQL repair loop)
    ↓
final SQL + result + traces
```

### ⚙️ Tech Stack

* FastAPI
* Pydantic-AI
* SQLiteAdapter
* Prometheus + Grafana
* pytest + mypy + Makefile
* Gradio UI

The pipeline is fully modular: each stage has a clean, swappable interface.

---

# **4) Evolution (Prototype → Copilot)**

This project is the **second-generation, production-grade** version of an earlier prototype:
👉 [https://github.com/melika-kheirieh/nl2sql-copilot-prototype](https://github.com/melika-kheirieh/nl2sql-copilot-prototype)

The prototype explored single-step, prompt-based SQL generation.
The current version is a **complete architectural redesign**, adding:

* multi-stage agentic pipeline
* schema introspection
* safety guardrails
* self-repair loop
* caching
* observability
* Spider benchmarks
* multi-DB support with upload + TTL handling

This repository is the first **end-to-end, production-oriented** version.

---

# **5) Key Features**

### ✔ Agentic Pipeline

Planner → Generator → Safety → Executor → Verifier → Repair.

### ✔ Schema-Aware

Automatic schema preview for any uploaded SQLite database.

### ✔ Safety by Design

* SELECT-only
* Column/table validation
* No multi-statement SQL
* Prevents schema hallucination

### ✔ Self-Repair

Automatic minimal-diff correction when SQL fails.

### ✔ Caching

TTL-based, with key = (db_id, normalized_query, schema_hash).
Hit/miss metrics included.

### ✔ Observability

* Per-stage latency
* Pipeline success ratio
* Repair success rate
* Cache hit ratio
* p95 latency
* Full Grafana dashboard

### ✔ Benchmarks

Reproducible Spider evaluation with plots + summary.

---

# **6) Benchmarks (Spider dev, 20 samples)**

[![Benchmarks](https://img.shields.io/badge/Benchmarks-Spider%20dev-blue)](#benchmarks)

Evaluated on a curated 20-sample subset of the Spider **dev** split
(focused on `concert_singer`), using the full production pipeline.

### 🧮 Summary

* **Total samples:** 20
* **Successful runs:** 20/20 (**100%**)
* **Exact Match (EM):** **0.10**
* **Structural Match (SM):** **0.70**
* **Execution Accuracy:** **0.725**

This reflects a *production-oriented* NL2SQL system:
the model optimizes for **executable SQL**, not literal gold-string alignment.

---

### ⏱ Latency

* **Avg latency:** ~**8066 ms**
* **p50:** ~**9229 ms**
* **p95:** ~**14936 ms**

Latency is **bimodal**:
simple queries → fast, reasoning-heavy queries → planner-dominated.

---

### ⚙️ Per-Stage Latency

| Stage     | Avg latency (ms) |
| --------- | ---------------- |
| detector  | ~1               |
| planner   | ~8360            |
| generator | ~1645            |
| safety    | ~2               |
| executor  | ~1               |
| verifier  | ~1               |
| repair    | ~1200            |

Planner is the main bottleneck (expected for schema-level reasoning).
Safety/executor/verifier stay **single-digit ms**.

---

### ❌ Failure Modes (Why EM is low)

Even when EM = 0, **SM and ExecAcc are often 1.0**.

Typical causes:

* Capitalization differences (`Age` vs `age`)
* Different column ordering
* LIMIT differences
* Alias mismatch
* Gold SQL is `EMPTY` but the model infers a valid SQL

In real-world systems, **execution correctness matters more than exact string match**.

---

### 📂 Reproducing the Benchmark

```bash
export SPIDER_ROOT="$PWD/data/spider"

PYTHONPATH=$PWD \
  python benchmarks/evaluate_spider_pro.py --spider --split dev --limit 20 --debug

PYTHONPATH=$PWD \
  python benchmarks/plot_results.py
```

Artifacts saved under:

```
benchmarks/results_pro/<timestamp>/
    summary.json
    eval.jsonl
    metrics_overview.png
    latency_histogram.png
    latency_per_stage.png
    errors_overview.png
```

---

# **7) API Usage**

## 🔍 NL → SQL

```bash
curl -X POST "http://localhost:8000/api/v1/nl2sql" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key" \
  -d '{
        "query": "Top 5 customers by total invoice amount",
        "db_id": null
      }'
```

### ✔ Sample Response (accurate)

```json
{
  "ambiguous": false,
  "sql": "SELECT ...",
  "rationale": "Explanation of why this SQL was generated.",
  "result": {
    "rows": 5,
    "columns": ["CustomerId", "Total"],
    "rows_data": [
      [1, 39.6],
      [2, 38.7],
      [3, 35.4]
    ]
  },
  "traces": [
    {"stage": "detector", "duration_ms": 1},
    {"stage": "planner",  "duration_ms": 8943},
    {"stage": "generator","duration_ms": 1722},
    {"stage": "safety",   "duration_ms": 2},
    {"stage": "executor", "duration_ms": 1},
    {"stage": "verifier", "duration_ms": 1},
    {"stage": "repair",   "duration_ms": 522}
  ]
}
```

---

## 📁 Upload a SQLite DB

```bash
curl -X POST "http://localhost:8000/api/v1/nl2sql/upload_db" \
  -H "X-API-Key: dev-key" \
  -F "file=@/path/to/db.sqlite"
```

---

## 📑 Schema Preview

```bash
curl "http://localhost:8000/api/v1/nl2sql/schema?db_id=<uuid>" \
  -H "X-API-Key: dev-key"
```

---

# **8) Environment Variables**

| Variable               | Purpose                                  |
| ---------------------- | ---------------------------------------- |
| `API_KEYS`             | Comma-separated list of backend API keys |
| `API_KEY`              | Used by Gradio UI to call the backend    |
| `DEV_MODE`             | Enables strict ambiguity detection       |
| `NL2SQL_CACHE_TTL_SEC` | Cache TTL                                |
| `NL2SQL_CACHE_MAX`     | Max cache entries                        |
| `SPIDER_ROOT`          | Path to Spider dataset                   |
| `USE_MOCK`             | Skip execution (for testing)             |

> Gradio uses `API_KEY` → backend expects it as `X-API-Key`.
> Backend accepts multiple keys via `API_KEYS`.

---

# **9) Future Work**

### 1) Streaming SQL Generation (SSE)

### 2) Redis Distributed Cache

### 3) Multi-Model Planner/Generator

### 4) A/B Testing Framework

### 5) Schema Embeddings

### 6) Nightly CI Benchmarks

### 7) Advanced Repair (diff-based)

### 8) Helm / Compose Deployment Template

---

# **10) License**

MIT License.
