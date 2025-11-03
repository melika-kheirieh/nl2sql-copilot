# 🧩 NL2SQL Copilot

A modular **Text-to-SQL Copilot** that converts natural-language questions into **safe, verified SQL queries**.
Built with **FastAPI**, **LangGraph**, and **SQLAlchemy**, designed for **read-only databases** and benchmarked on **Spider** and **Dr.Spider** datasets.

---

> 💡 **Why it matters**
> In real analytics teams, analysts often need quick insights without writing SQL.
> **NL2SQL Copilot** bridges that gap by translating plain-English questions into validated, read-only SQL — reducing query errors and saving hours of analyst time.
>
> 🧬 **Evolution Note**
> This repository is the next-generation version of [NL2SQL Copilot Prototype](https://github.com/melika-kheirieh/nl2sql-copilot-prototype).
> It refactors the original prototype into a **production-grade, modular architecture** —
> adding configuration-driven pipelines, safety layers, benchmarks, and a Streamlit UI for evaluation.

---

## 🚀 Quick Start

### 1️⃣ Clone the repo
```bash
git clone https://github.com/melika-kheirieh/nl2sql-copilot.git
cd nl2sql-copilot
````

### 2️⃣ Build and run with Docker

```bash
docker build -t nl2sql-copilot .
docker run --rm -p 8000:8000 nl2sql-copilot
```

Then open [http://localhost:8000/docs](http://localhost:8000/docs) 🚀
Or launch the [Streamlit Demo](http://localhost:7860) to test it interactively.

---

## 🧠 Demo

🎯 **Live Demo:** [Try it on Hugging Face Spaces →](https://huggingface.co/spaces/melika-kheirieh/nl2sql-copilot)

You can ask a question in plain English — the Copilot plans, generates, verifies, and safely executes an SQL query.

**User Query**

> show top 5 albums by total sales

**Generated SQL**

```sql
SELECT albums.Title, SUM(invoice_items.UnitPrice * invoice_items.Quantity) AS total_sales
FROM albums
JOIN tracks ON albums.AlbumId = tracks.AlbumId
JOIN invoice_items ON invoice_items.TrackId = tracks.TrackId
GROUP BY albums.Title
ORDER BY total_sales DESC
LIMIT 5;
```

**Execution Result (preview)**

| Album             | Total Sales |
| ----------------- | ----------- |
| Greatest Hits     | 155.34      |
| Let There Be Rock | 133.09      |
| Big Ones          | 128.44      |

**Trace**

```json
[
  {"stage": "planner", "duration_ms": 38, "summary": "Identified SQL intent"},
  {"stage": "generator", "duration_ms": 201, "summary": "LLM generated SQL"},
  {"stage": "safety", "duration_ms": 6, "summary": "Validated SELECT-only"},
  {"stage": "executor", "duration_ms": 92, "summary": "Executed successfully"}
]
```

![Demo Screenshot](docs/demo-screenshot.png)

---

## 🧱 Project Structure

```
nl2sql-copilot/
│
├── app/                 # FastAPI app, routers, schemas
├── nl2sql/              # Core pipeline (Planner → Generator → Safety → Executor → Verifier)
├── adapters/            # Database and LLM adapters
├── benchmarks/          # Evaluation scripts and results
├── ui/                  # Streamlit dashboard (demo + benchmark)
├── configs/             # Pipeline configs (YAML-based)
│
├── Dockerfile
├── requirements.in / .txt
└── README.md
```

---

## ⚙️ How It Works

The Copilot runs a **multi-stage pipeline** ensuring every SQL query is both correct and safe:

```
Natural Language
   ↓
[ Planner ] → [ Generator (LLM) ] → [ Safety ] → [ Executor ] → [ Verifier ] → [ Repair ]
```

Each stage is modular and configurable via `configs/pipeline.yaml`.
All queries execute inside a **read-only sandbox**.

---

## 🔒 Safety Layer

Before execution, every SQL statement is validated:

| Rule               | Example Blocked               |
| ------------------ | ----------------------------- |
| DML not allowed    | `DELETE FROM users`           |
| Multi-statement    | `SELECT *; DROP TABLE users`  |
| Forbidden keywords | `ALTER`, `TRUNCATE`, `UPDATE` |

✅ Only safe, single-statement `SELECT` queries are executed.

---

## 📊 Benchmark (sample)

Evaluated on a subset of the [Spider](https://yale-lily.github.io/spider) dataset using `gpt-4o-mini`:

| Query                       | Type          | Correct | Latency (ms) | Model       |
| --------------------------- | ------------- | ------- | ------------ | ----------- |
| list all artists            | simple select | ✅       | 118          | gpt-4o-mini |
| total invoices per country  | aggregation   | ✅       | 127          | gpt-4o-mini |
| top 3 customers by spending | aggregation   | ✅       | 141          | gpt-4o-mini |
| albums released before 2000 | filter        | ✅       | 122          | gpt-4o-mini |
| top 5 sales by genre        | join          | ✅       | 149          | gpt-4o-mini |

*(see `benchmarks/results.csv` for detailed results)*

---

## 🧩 Key Features

* ✅ **Modular pipeline** (Planner → Generator → Safety → Executor → Verifier → Repair)
* 🛡️ **SQL safety filters** (SELECT-only, blacklist, AST validation)
* 🔁 **Self-repair loop** for failed executions
* 🧠 **LLM-driven generator** (OpenAI / Ollama / Anthropic)
* 📊 **Evaluation toolkit** for latency / accuracy / cost
* ⚙️ **Config-driven architecture** (`Pipeline.from_config("configs/pipeline.yaml")`)
* 🧰 **PostgreSQL + SQLite adapters**
* 🎛️ **Streamlit UI** for interactive demo & benchmark
* 🧩 Built with **FastAPI**, **LangGraph**, **Pydantic-AI**, **SQLAlchemy**

---

## 🧰 Tech Stack

| Layer         | Tools / Libraries                         |
| ------------- | ----------------------------------------- |
| Backend API   | FastAPI, Uvicorn                          |
| Pipeline Core | Python 3.12, Pydantic, SQLGlot            |
| LLM Interface | Pydantic-AI (OpenAI / Anthropic / Ollama) |
| Database      | SQLite (default), PostgreSQL              |
| Evaluation    | Spider / Dr.Spider                        |
| UI            | Streamlit + Plotly                        |
| CI/CD         | GitHub Actions, Makefile, Docker          |

---

## 🧪 Development

```bash
pip install -r requirements.txt
pytest -q
ruff check .
mypy .
```

---

## 🧭 Roadmap

* [ ] Add multilingual query support (Persian / English)
* [ ] Improve self-repair accuracy
* [ ] Add cost tracking per query
* [ ] Integrate Prometheus metrics

---

## 📄 License

MIT © 2025 [Melika Kheirieh](https://github.com/melika-kheirieh)
