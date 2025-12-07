---
title: NL2SQL Copilot — Full Stack Demo
emoji: 🧩
colorFrom: indigo
colorTo: blue
sdk: docker
pinned: false
---
# **NL2SQL Copilot — Multi-Stage, Agentic Text-to-SQL System**
[![CI](https://github.com/melika-kheirieh/nl2sql-copilot/actions/workflows/ci.yml/badge.svg)](https://github.com/melika-kheirieh/nl2sql-copilot/actions/workflows/ci.yml)
[![Docker](https://img.shields.io/badge/docker-ready-blue?logo=docker)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A production-ready **Text-to-SQL Copilot** implemented with **FastAPI**, **Pydantic-AI**, and a fully traceable **agentic pipeline**.

The system converts natural-language questions into **safe**, **verified**, and **executable** SQL — with introspection, observability, repair loops, caching, and Spider-style benchmarks.

<p align="center">
  <img src="docs/assets/screenshots/demo_list_albums_total_sales.png" width="620">
</p>

---

## **📌 Features**

* **Multi-stage agentic pipeline**
  Detector → Planner → Generator → Safety → Executor → Verifier → Repair
* **Safe SQL generation**
  Enforces single-statement, SELECT-only SQL
* **Self-repair loop**
  Attempts to fix invalid SQL using structural & execution feedback
* **Database upload + multi-DB support**
  Uses `db_id`, automatic TTL cleanup, and schema preview
* **Schema introspection endpoint**
  Returns a compact developer-friendly schema summary
* **LRU query cache**
  Lightweight in-memory cache with hit/miss metrics
* **Full observability**
  Prometheus metrics + Grafana dashboard
* **Benchmarking on Spider-style queries**
  EM, SM, ExecAcc, latency distributions
* **FastAPI backend + Gradio demo**
  Fully deployable to Hugging Face Spaces
* **API-Key auth** for production-ish hygiene

---

# **📂 Repository Structure**

```
nl2sql-copilot/
│
├── app/                    # FastAPI service
│   ├── routers/
│   ├── adapters/
│   ├── schemas/
│   ├── state.py
│   └── main.py
│
├── nl2sql/                 # Agentic pipeline
│   ├── pipeline.py
│   ├── detector.py
│   ├── planner.py
│   ├── generator.py
│   ├── safety.py
│   ├── executor.py
│   ├── verifier.py
│   ├── repair.py
│   ├── metrics.py
│   └── cache_lru.py
│
├── benchmarks/             # Spider evaluations + plots
│   └── results_pro/
│
├── demo/                   # Gradio app for local + HF demo
│   └── app.py
│
├── docs/
│   └── assets/
│       ├── demo.png
│       └── grafana.png
│
└── data/
    └── Chinook_Sqlite.sqlite   # Default bundled DB
```

---

# **🚀 Quick Start**

### **1) Install**

```bash
uv venv
uv pip install -r requirements.txt
```

### **2) Run API**

```bash
uv run uvicorn app.main:app --reload
```

### **3) Run Demo UI**

```bash
uv run python demo/app.py
```

### **4) Optional: Set API key**

```bash
export API_KEYS="dev-key"
```

---

# **🧠 Architecture (Agentic Pipeline)**

<p align="center">
  <img src="docs/assets/architecture.png" width="680">
</p>

The pipeline is **config-driven** (`pipeline_factory`) and fully modular:

| Stage         | Responsibility                            |
| ------------- | ----------------------------------------- |
| **Detector**  | Ambiguity check (NL side)                 |
| **Planner**   | SQL plan sketch based on schema preview   |
| **Generator** | Final SQL generation + rationale          |
| **Safety**    | SELECT-only validation + block risky SQL  |
| **Executor**  | Run SQL on the chosen DB                  |
| **Verifier**  | Structural inspection + consistency rules |
| **Repair**    | Self-healing attempt using error feedback |

Each stage logs:

* duration
* tokens
* internal notes
* success/failure

All are exported via Prometheus metrics.

---

# **🧪 Demo Examples**

These run **without any uploaded DB**, using the bundled Chinook SQLite dataset.

### Use Case 1 — “Top customers by spending”

```
Top 5 customers by total invoice amount
```

### Use Case 2 — “Albums of a specific artist”

```
List all albums by AC/DC
```

### Use Case 3 — “Track count per genre”

```
How many tracks are in each genre?
```

### Use Case 4 — “Best-selling genres”

```
Top 5 genres by total invoice revenue
```

---

# **📡 API Usage**

### **POST /api/v1/nl2sql**

```bash
curl -X POST "http://localhost:8000/api/v1/nl2sql" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key" \
  -d '{
        "query": "Top 5 customers by total invoice amount",
        "db_id": null
      }'
```

### Response (truncated)

```json
{
  "ambiguous": false,
  "sql": "SELECT ...",
  "rationale": "...",
  "result": {...},
  "traces": [
    {"stage": "detector", "duration_ms": 1},
    {"stage": "planner", "duration_ms": 8943},
    {"stage": "generator", "duration_ms": 1722},
    {"stage": "safety",   "duration_ms": 2},
    {"stage": "executor", "duration_ms": 1},
    {"stage": "verifier", "duration_ms": 1},
    {"stage": "repair",   "duration_ms": 522}
  ]
}
```

---

# **🧩 DB Schema Introspection**

### **GET /api/v1/nl2sql/schema**

```bash
curl "http://localhost:8000/api/v1/nl2sql/schema" \
  -H "X-API-Key: dev-key"
```

Returns a compact preview like:

```
Artist(name, artistid)
Album(albumid, title, artistid)
Track(trackid, name, albumid)
...
```

---

# **⚡ LRU Query Cache**

A lightweight in-memory LRU cache improves latency for repeated queries.

* eviction policy: **LRU**
* TTL optional (environment controlled)
* metrics:

  * `cache_hits_total`
  * `cache_misses_total`

Easy to swap for Redis in production.

---

# **📈 Observability (Prometheus + Grafana)**

> Full, production-style observability baked into every pipeline stage.

<p align="center">
  <img src="docs/assets/grafana.png" width="680">
</p>

Exported metrics include:

| Metric                                    | Description                 |
| ----------------------------------------- | --------------------------- |
| `stage_duration_ms`                       | per-stage latency histogram |
| `pipeline_runs_total`                     | requests by status          |
| `repair_attempts_total`                   | repair success/failure      |
| `cache_hits_total` / `cache_misses_total` | LRU cache behavior          |
| `executor_rowcount`                       | executed result stats       |

Dashboard panels:

* p50 / p95 latency
* per-stage breakdown
* repair success rate
* cache hit-ratio
* errors & blocked SQL
* pipeline success ratio

---

# **📊 Spider Benchmarks**

Example run (20 Spider dev samples):

```
EM:        0.10
SM:        0.70
ExecAcc:   0.725
Latency:   p50 = 9.2s, p95 = 14.9s
```

Generated outputs:

```
benchmarks/results_pro/<timestamp>/
    summary.json
    eval.jsonl
    metrics_overview.png
    latency_histogram.png
    latency_per_stage.png
    errors_overview.png
```

Run benchmarks:

```bash
export SPIDER_ROOT="$PWD/data/spider"

PYTHONPATH=$PWD \
  python benchmarks/evaluate_spider_pro.py --spider --split dev --limit 20 --debug

PYTHONPATH=$PWD \
  python benchmarks/plot_results.py
```

---

# **📜 Evolution (Prototype → Copilot)**

This project is the **second-generation**, production-grade successor of an earlier prototype:
👉 [https://github.com/melika-kheirieh/nl2sql-copilot-prototype](https://github.com/melika-kheirieh/nl2sql-copilot-prototype)

**Prototype:**

* single-prompt SQL
* no safety
* no repair
* no multi-DB
* no observability
* no benchmarks

**Current version:**

* complete architectural redesign
* multi-stage agentic pipeline
* schema introspection
* executor + verifier
* repair loop
* LRU caching
* Prometheus/Grafana
* benchmark harness
* FastAPI + Gradio deployment

---

# **🧭 Roadmap**

* Streaming SQL generation
* Multi-model support (OpenAI, Anthropic, local LLMs)
* Advanced semantic caching
* Automated nightly Spider benchmarks
* Query rewriting step prior to planning
* Multi-user sandboxing & rate-limit layer
