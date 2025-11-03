# 🧩 NL2SQL Copilot

A modular **Text-to-SQL Copilot** that converts natural language questions into safe and verified SQL queries.
Built with **FastAPI**, **LangGraph**, and **SQLAlchemy**, designed for read-only databases and evaluation on Spider/Dr.Spider benchmarks.

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

---

## 🧱 Project Structure

```
nl2sql-copilot/
│
├── app/                 # FastAPI app, routers, schemas
├── nl2sql/              # Core pipeline (planner → generator → safety → executor → verifier)
├── adapters/            # Database and LLM adapters
├── benchmarks/          # Evaluation scripts and results
├── ui/                  # Streamlit dashboard
│
├── Dockerfile
├── requirements.in
├── requirements.txt
└── README.md
```

---

## 🧪 Development

### Install dependencies

(Recommended: Python 3.12+ and virtualenv)

```bash
pip install -r requirements.txt
```

### Run tests

```bash
pytest -q
```

### Lint and type-check

```bash
ruff check .
mypy .
```

---

## 🧠 Features

* ✅ Modular multi-stage pipeline (Planner → Generator → Safety → Executor → Verifier → Repair)
* 🛡️ SQL safety filters (SELECT-only, forbidden keywords)
* 🔁 Self-repair loop on failed executions
* 📊 Streamlit benchmark dashboard (latency, accuracy, cost)
* 🧩 PostgreSQL + SQLite adapters
* 🧠 Powered by `pydantic-ai` and `LangGraph`

---

## 🧰 Tech Stack

| Layer            | Tools                                   |
| ---------------- | --------------------------------------- |
| Backend API      | FastAPI, Uvicorn                        |
| Pipeline Core    | Python 3.12, Pydantic, SQLGlot          |
| LLM Interface    | pydantic-ai (OpenAI, Anthropic, Ollama) |
| Database         | SQLite (default), PostgreSQL            |
| Evaluation       | Spider / Dr.Spider                      |
| UI               | Streamlit + Plotly                      |
| Containerization | Docker / Docker Compose                 |

---

## 📄 License

MIT © 2025 Melika Kheirieh
