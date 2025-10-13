# NL2SQL Copilot — Prototype

A minimal **Text-to-SQL Copilot** built with **LangChain + Gradio**, designed to translate natural language questions into **safe SQL** and run them on a **read-only SQLite** database.

> **Status:** Prototype (v0.1). This demonstrates structure and UX; advanced safety/verification pipelines are planned.

---

## ✨ Features (v0.1)
- Gradio UI for quick interactions
- Config-driven environment (dotenv)
- Pluggable LLM endpoint (proxy or direct OpenAI)
- SQLite **read-only** connection (no data mutation)

**Planned next:**
- Query planning and verification
- Safer SQL guardrails (AST / blocklist / dialect checks)
- Self-repair on failed queries
- Semantic cache and telemetry

---

## 📂 Project Structure
```
nl2sql-copilot-prototype/
├─ app.py
├─ config.py
├─ requirements.txt
├─ .env.example
├─ .gitignore
└─ README.md
└─ db/
   ├─ Chinook_Sqlite.sqlite
   └─ WMSales.sqlite
```
## 🧩 Database Samples

Two example SQLite databases are included in the `db/` folder for quick testing:

| File | Description | Download |
|------|--------------|-----------|
| **Chinook_Sqlite.sqlite** | Classic sample DB with artists, albums, and tracks (music store example). | [⬇️ Download](https://github.com/melika-kheirieh/nl2sql-copilot-prototype/raw/main/db/Chinook_Sqlite.sqlite) |
| **WMSales.sqlite** | Simple sales database (for demoing aggregate and filter queries). | [⬇️ Download](https://github.com/melika-kheirieh/nl2sql-copilot-prototype/raw/main/db/WMSales.sqlite) |

You can use them directly in the Gradio UI by uploading one of these files, or reference them in code for local runs.

---

### 🧠 Sample Questions for *Chinook_Sqlite.sqlite*
Try asking your copilot questions like:

1. “List the top 5 artists by total track count.”  
2. “Which album has the most tracks?”  
3. “Show all tracks longer than 6 minutes.”  
4. “Find the average track length by genre.”
5. “Show total invoice amount by billing country.”
6. “Top 10 most popular genres by number of tracks.”
7. “How many customers have purchased Jazz albums?”
8. “Show the total revenue by employee (sales support).”
9. “List customers who spent more than $100.” 
10. “Which customers are from Canada?”  


---

### 📊 Sample Questions for *WMSales.sqlite*
You can try:

1. “Show total sales per month in 2024.”  
2. “List the top 10 customers by revenue.”  
3. “Which product category had the highest sales this year?”  
4. “Find the average unit price per product.”  
5. “Show all orders placed in the last 30 days.”  
6. “List total sales by region and salesperson.”  
7. “What is the best-selling product overall?”  
8. “Show total discount given per month.”  
9. “Find customers who made more than 5 purchases.”  
10. “What’s the total revenue by payment method?”
---

## ⚙️ Requirements
- Python 3.10+
- A proxy/provider API key (OpenAI / custom proxy)
- SQLite DB file (uploaded via UI)

---

## 🔐 Environment Variables

Copy the example and fill your own values:

```bash
cp .env.example .env
```

`.env.example` (proxy-agnostic):
```bash
# ---- LLM provider or proxy (preferred) ----
PROXY_API_KEY="your-proxy-or-provider-api-key"
PROXY_BASE_URL="https://your-proxy-or-provider-base-url/v1"

# ---- Optional direct OpenAI fallback ----
#OPENAI_API_KEY="your-openai-api-key"
#OPENAI_BASE_URL="https://api.openai.com/v1"
```

`config.py` should select `PROXY_*` first; if empty, it falls back to `OPENAI_*`.

---

## 🧪 Local Quickstart

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env           # then edit .env and add your keys
python app.py                 # open the Gradio link in browser
```

Upload a SQLite file and try a prompt like:
> “Top 5 customers by total orders in 2024.”

---

## 🧰 Safety Notes (Prototype)
- DB is opened in **read-only** mode, but you should still block multi-statement payloads and dangerous tokens (e.g., `ATTACH`, `PRAGMA`, `sqlite_master`, DDL/INSERT/UPDATE/DELETE).
- Consider an AST approach (e.g., `sqlglot`) for a stricter parse/allow-list.

---

## ☁️ Deploy to Hugging Face Spaces (Gradio)

### 1) Create a new Space
- Go to Hugging Face → Spaces → **New Space**
- **Name:** `nl2sql-copilot-prototype`
- **Space SDK:** Gradio
- **Hardware:** CPU Basic
- **Visibility:** Public (or Private)

### 2) Add project files
Commit/push these files to the Space repo:
- `app.py`, `config.py`, `requirements.txt`, `.env.example`, `README.md`, `.gitignore`

### 3) Set Secrets (Variables and secrets)
In Space → **Settings → Variables and secrets**:
- `PROXY_API_KEY`: your real key
- `PROXY_BASE_URL`: e.g., `https://.../v1`
- (Optional) `OPENAI_API_KEY` and `OPENAI_BASE_URL`

> Do **not** commit a real `.env`. Use Space **Secrets**.

### 4) Build & Run
- Spaces auto-install from `requirements.txt`.
- If not auto-started, set **App file: main.py**, SDK: **Gradio**, Python: **3.10+**.

### 5) Test
- Open Space URL
- Upload a small sample SQLite DB
- Check **Logs** tab for errors

**Persistence note:** Uploads are ephemeral; include a tiny demo DB in the repo if needed.

---

## 🧭 Usage Tips
- Prefer concise prompts (e.g., “Show avg price by category for 2023”).
- If a query fails, rephrase or reduce columns.
- For bigger DBs, add a schema introspection step or a “Describe tables” helper.

---

## 🛡️ Security & Privacy
- Never log raw API keys.
- Keep `.env` out of Git; commit only `.env.example`.
- Enforce read-only and block multi-statement SQL.

---

## 🗺️ Roadmap
- [ ] Planner → Generator → Safety → Executor → Verifier loop
- [ ] AST-based guardrails (sqlglot)
- [ ] Self-repair on DB/SQL errors
- [ ] Semantic cache + telemetry
- [ ] Streamlit / FastAPI variants


