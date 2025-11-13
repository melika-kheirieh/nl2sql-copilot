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
Generates *safe, verified, executable SQL* via a multi-stage agentic pipeline.
Includes: self-repair, Spider benchmarks, Prometheus metrics, and a full demo UI.

🚀 **Live Demo (Hugging Face Space):**
👉 *(your HF link here)*

---

# **1) Quick Start**

```bash
git clone https://github.com/melika-kheirieh/nl2sql-copilot
cd nl2sql-copilot
make setup      # install deps
make run        # start API + UI
```

Open:
👉 [http://localhost:8000](http://localhost:8000)
👉 [http://localhost:8000/docs](http://localhost:8000/docs) (Swagger UI)

---

# **2) Demo (Gradio UI)**

The live UI supports:

* Uploading a SQLite database
* Asking natural-language questions
* Viewing generated SQL
* Viewing execution results
* Full trace per pipeline stage
* Example queries for quick testing
* No need to upload a DB for the demo (ships with a small example DB)

Everything runs through the same agentic backend as the API.

---

# **3) Architecture Overview**

```
user query
    ↓
detector      (ambiguous? dataset missing?)
planner       (task decomposition + schema reasoning)
generator     (SQL generation)
safety        (SELECT-only, no mutations)
executor      (SQLiteAdapter with sandboxing)
verifier      (semantic + execution checks)
repair        (minimal-diff SQL repair loop)
    ↓
final SQL + result + traces
```

### 🔧 Technical Stack

* **FastAPI** — HTTP API
* **Pydantic-AI** — agentic stages
* **SQLiteAdapter** — isolated DB execution
* **Prometheus** — metrics
* **Grafana** — dashboard
* **Makefile + pytest + mypy** — dev workflow

The entire pipeline is modular; each stage has a clean interface and can be swapped (e.g., planner or generator model).

---

# **4) Key Features**

### ✔ Multi-Stage Agentic Pipeline

Planner → Generator → Safety → Executor → Verifier → Repair.

### ✔ Safety by Design

* Only `SELECT` queries allowed
* Column/table validation
* No multi-table hallucination
* Deterministic schema preview

### ✔ Repair Loop

Automatically fixes malformed or non-executable SQL using minimal edits and retries.

### ✔ Caching

* TTL-based
* Exact query deduplication
* Miss/hit metrics

### ✔ Observability

* Per-stage latency
* Pipeline success ratio
* Repair success rate
* p95 latency
* Cache hit ratio
* Full Grafana dashboard

### ✔ Spider Benchmarks

Reproducible evaluation on Spider (dev split).
Comes with plotting utilities, histogram, latency per stage, and summary.json.

---

باشه—الان **همین بخش Benchmarks که ساختم** را برایت
**کاملاً آمادهٔ قرار گرفتن در README** می‌کنم:

* با heading درست
* با anchor مناسب
* با ساختار کاملاً هم‌تراز با بقیهٔ README تو
* با badge
* بدون هیچ وابستگی اضافی
* ۱۰۰٪ کپی‌ـ‌پیست مستقیم

این نسخه **نهایی، آمادهٔ چسباندن** است:

---

# 📊 Benchmarks (Spider dev, 20 samples)

[![Benchmarks](https://img.shields.io/badge/Benchmarks-Spider%20dev-blue)](#benchmarks-spider-dev-20-samples)

This copilot is evaluated on a 20-sample slice of the Spider **dev** split
(focused on the `concert_singer` schema) using the production pipeline end-to-end.

### 🧮 Summary

- **Total samples:** 20
- **Successful runs:** 20 / 20 (**100%**)
- **Exact Match (EM):** **0.10**
- **Structural Match (SM):** **0.70**
- **Execution Accuracy (ExecAcc):** **0.725**

These results reflect a *production-oriented Text-to-SQL system*:
the model optimizes for **valid, executable SQL**, not strict syntactic match.

---

### ⏱ Latency

End-to-end pipeline time (all stages):

- **Avg latency:** ~**8066 ms**
- **p50 latency:** ~**9229 ms**
- **p95 latency:** ~**14936 ms**

Latency distribution is **bimodal**:
1) fast lookups,
2) multi-hop reasoning dominated by the planner stage.

(See `latency_histogram.png` in the benchmark folder.)

---

### ⚙️ Per-Stage Latency (from Prometheus histograms)

| Stage      | Avg latency (ms) |
|------------|------------------|
| detector   | ~1               |
| planner    | ~8360            |
| generator  | ~1645            |
| safety     | ~2               |
| executor   | ~1               |
| verifier   | ~1               |
| repair     | ~1200            |

The **planner** is the dominant contributor—expected for a reasoning-heavy
agentic pipeline. Safety/executor/verifier remain **single-digit ms**.

---

### ❌ Failure Modes (Why EM is low but ExecAcc is high)

Even when EM=0, **SM و ExecAcc غالباً 1.0** هستند.

Typical causes:

- Column name capitalization differences
- Different LIMIT usage
- Different column order
- Aliases not matching the gold query
- Spider gold query being `EMPTY`, but the model (correctly) infers a SQL query

In real systems, **execution correctness** matters more than literal match.

---

### 📂 Reproducibility

Run the exact same benchmark:

```bash
export SPIDER_ROOT="$PWD/data/spider"

PYTHONPATH=$PWD \
  python benchmarks/evaluate_spider_pro.py --spider --split dev --limit 20 --debug

PYTHONPATH=$PWD \
  python benchmarks/plot_results.py
````

Artifacts stored under:

```
benchmarks/results_pro/20251113-113600/
    summary.json
    eval.jsonl
    metrics_overview.png
    latency_histogram.png
    latency_per_stage.png
    errors_overview.png
```

These plots are directly embedded into the README if needed.

---

# **6) API Usage**

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

Sample response:

```json
{
  "ambiguous": false,
  "sql": "...",
  "rationale": "...",
  "result": [...],
  "traces": [
    {"stage": "detector", "duration_ms": 1},
    {"stage": "planner", "duration_ms": 8943},
    {"stage": "generator", "duration_ms": 1722},
    {"stage": "safety", "duration_ms": 2},
    {"stage": "executor", "duration_ms": 1},
    {"stage": "verifier", "duration_ms": 1},
    {"stage": "repair", "duration_ms": 522}
  ]
}
```

---

## 📤 Upload SQLite DB

```bash
curl -X POST "http://localhost:8000/api/v1/nl2sql/upload_db" \
  -H "X-API-Key: dev-key" \
  -F "file=@/path/to/db.sqlite"
```

Response:

```json
{
  "db_id": "9a21d49f-38d3-4ce0-a459-3688e02fd44a",
  "message": "Database uploaded successfully."
}
```

---

## 📑 Schema Preview

```bash
curl "http://localhost:8000/api/v1/nl2sql/schema?db_id=<uuid>" \
  -H "X-API-Key: dev-key"
```

---

## ⚙️ Environment Variables

| Variable               | Purpose                           |
| ---------------------- | --------------------------------- |
| `API_KEYS`             | Comma-separated auth keys         |
| `DEV_MODE`             | Enables strict ambiguity detector |
| `NL2SQL_CACHE_TTL_SEC` | Cache TTL                         |
| `NL2SQL_CACHE_MAX`     | Cache size                        |
| `SPIDER_ROOT`          | Spider dataset path               |
| `USE_MOCK`             | Skip DB execution                 |

---

# **7) Future Work**

The copilot is intentionally kept lean. Several scoped enhancements are planned:

### 1) Streaming SQL (SSE)

Show partial SQL generation live.

### 2) Redis Distributed Cache

Shared cache across replicas, eviction, warm-ups.

### 3) Multi-Model Planner/Generator

Support OpenAI, vLLM, LLaMA, hybrid pipelines.

### 4) A/B Testing Framework

Compare prompts/models with automated drift tracking.

### 5) Schema Embeddings

Vector-based reasoning for table/column retrieval.

### 6) Nightly CI Benchmarks

GitHub Actions → run Spider → save plots → detect drift.

### 7) Stronger Diff-based Repair

Trace-aware SQL recovery with history logging.

### 8) Deployment Template

Helm chart / compose stack for production rollout.

---

# **8) License**

MIT License.
