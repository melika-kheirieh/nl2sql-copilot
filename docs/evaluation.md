# Evaluation

This project ships with two evaluation tracks:

- **Lite (MVP sanity)**: fast, local, no external datasets.
- **Pro (Spider)**: realistic NL2SQL benchmark using the Spider dataset.

The goal is not to “flex numbers”.
The goal is to make evaluation **repeatable**, **auditable**, and **easy to run**.

---

## Lite evaluation (MVP)

**Use case**: verify the end-to-end pipeline on the demo database and quickly detect regressions.

### What it does

- Runs a small set of queries against the bundled demo DB.
- Produces JSONL results under `benchmarks/results/`.
- Intended for smoke checks and CI-friendly validation.

### Run

```bash
make eval-smoke
````

### Output

Results are written under:

```text
benchmarks/results/
```

---

## Pro evaluation (Spider)

**Use case**: benchmark NL2SQL performance on a standard dataset.

> The Spider dataset is **not included** in this repository.
> You must download and prepare it locally.

### Run a small smoke subset

```bash
make eval-pro-smoke
```

### Run a larger batch

```bash
make eval-pro
```

### Output

Results are written under:

```text
benchmarks/results_pro/
```

---

## Inspect results (UI)

### Streamlit benchmark dashboard

This dashboard reads `benchmarks/results/**/*.jsonl` and visualizes run summaries.

```bash
make bench-ui
```

Then open:

```text
http://localhost:8501
```

---

## Notes on interpretation

* Treat results as **baselines**, not marketing claims.
* Focus on:

  * failure modes (ambiguous questions, safety blocks, verification failures),
  * repair recovery rate,
  * latency distribution,
  * overall stability across repeated runs.
* Some queries may be **intentionally blocked** by safety or cost guardrails
  (e.g. full table scans without `LIMIT`).
  These failures represent **expected behavior**, not regressions.

If you change prompts, models, or verification logic, re-run the same evaluation
and compare **distributions and trends**, not single-point numbers.

---

## Reproducibility tips

* Keep the model name and key settings stable while comparing changes.
* Run the same command multiple times and compare aggregate behavior.
* Use logs, traces, and the dashboard to understand *why* a run failed,
  not just *that* it failed.
