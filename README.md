---
title: NL2SQL Copilot — Full Stack Demo
emoji: 🧩
colorFrom: indigo
colorTo: blue
sdk: docker
pinned: false
---
# NL2SQL Copilot

[![CI](https://github.com/melika-kheirieh/nl2sql-copilot/actions/workflows/ci.yml/badge.svg)](https://github.com/melika-kheirieh/nl2sql-copilot/actions/workflows/ci.yml)
![Docker](https://img.shields.io/badge/docker-ready-blue?logo=docker)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A modular, production-oriented **Text-to-SQL Copilot** that converts natural-language queries into **safe, verified SQL**, runs them on a database (built-in or uploaded), and returns structured results with full traceability, repair loops, and observability.

Built with **FastAPI**, **Pydantic-AI**, **SQLite**, **LRU caching**, and **Prometheus + Grafana** instrumentation.

---

## 🔥 Features

### **Agentic Multi-Stage Pipeline**
- **Detector** — ambiguity check
- **Planner** — reasoning plan
- **Generator** — SQL + rationale
- **Safety** — SELECT-only, forbidden pattern detection
- **Executor** — runs SQL on uploaded or default DB
- **Verifier** — validates output & schema compliance
- **Repair Loop** — automatic self-correction if SQL fails
- **LRU Cache** — fast responses on repeated queries

### **Observability**
- Stage-level timings
- p95 latency
- Safety / Verifier events
- Error counters
- Repair attempts
- Exported via Prometheus → visualized in Grafana

### **Flexible Database Input**
- Use built-in sample SQLite DB (Chinook)
- Upload your own `.db` file (auto-validated, TTL cleanup)

### **Reproducible Benchmarks**
- Evaluated on **Spider** (dev split)
- Exact-Match, Structural-Match, ExecAcc, latency & failure breakdown
- Plotting scripts included

---

## 🧩 Architecture (High-Level)

<img src="docs/assets/architecture.png" width="750"/>

---

## 🚀 Demo

A live interactive demo is available on Hugging Face Spaces:

👉 https://huggingface.co/spaces/melikakheirieh/nl2sql-copilot

### Example Query
**Input:**
```

List all artists

````

**Generated SQL:**
```sql
SELECT Name FROM Artist
````

**Result (truncated):**

```json
{
  "rows": [
    ["AC/DC"],
    ["Accept"],
    ["Aerosmith"],
    ["Alanis Morissette"]
  ]
}
```

---

## 📡 API Usage

### **POST /api/v1/nl2sql**

Convert natural language into verified SQL and get query results.

```bash
curl -X POST "http://localhost:8000/api/v1/nl2sql" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key" \
  -d '{
        "query": "Top 5 customers by total invoice amount",
        "db_id": null
      }'
```

### Sample Response

```json
{
  "ambiguous": false,
  "sql": "SELECT ...",
  "rationale": "Explanation of why this SQL was generated.",
  "result": { "rows": 5, "columns": [...], "rows_data": [...] },
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

## 🧪 Benchmarks (Spider)

Run evaluation:

```bash
export SPIDER_ROOT="$PWD/data/spider"

PYTHONPATH=$PWD \
  python benchmarks/evaluate_spider_pro.py --spider --split dev --limit 20 --debug
```

Plot results:

```bash
PYTHONPATH=$PWD \
  python benchmarks/plot_results.py
```

Outputs:

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

## 🏗️ Local Development

### Install

```bash
make install
```

### Run FastAPI server

```bash
make api
```

### Run Gradio UI + API (combined)

```bash
python start.py
```

### Run tests

```bash
make test
```

### Lint + mypy

```bash
make lint
make typecheck
```

---

## 📦 Docker

Build:

```bash
docker build -t nl2sql-copilot .
```

Run:

```bash
docker run -p 8000:8000 nl2sql-copilot
```

---

## 📊 Observability (Prometheus + Grafana)

Metrics available:

* Stage timings
* p95 latency
* Repair attempts
* Error counters
* Cache hits/misses

Sample Grafana view:

<img src="docs/assets/grafana.png" width="750"/>

---

## 🧬 Evolution (Prototype → Copilot)

This project is the second-generation version of an earlier prototype:

👉 [https://github.com/melika-kheirieh/nl2sql-copilot-prototype](https://github.com/melika-kheirieh/nl2sql-copilot-prototype)

Improvements:

* Multi-stage agentic pipeline
* Safety + repair loop
* Schema introspection
* LRU cache
* Observability
* Benchmarks
* Uploadable DB engine

---

## 🗺️ Roadmap

* Multi-turn disambiguation
* Cross-DB schema adaptation
* Lite mode (edge inference)
* Streaming reasoning traces
* Semantic caching (vector-based)
* Distributed execution mode

---

## 📄 License

MIT — free for personal and commercial use.
