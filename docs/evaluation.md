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

* Results are written under:

```text
benchmarks/results/
```

---

## Pro evaluation (Spider)

**Use case**: benchmark NL2SQL performance on a standard dataset.

> Spider is not included in the repository.
> You must download/prepare it locally.

### Run a small smoke subset

```bash
make eval-pro-smoke
```

### Run a bigger batch

```bash
make eval-pro
```

### Output

* Results are written under:

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
  * and overall stability across runs.

If you change prompts, model, or verification logic, you should re-run the same evaluation and compare distributions rather than single-point numbers.

---

## Reproducibility tips

* Keep the model name and key settings stable while comparing changes.
* Run the same command multiple times and compare aggregate behavior.
* Use the dashboard and logs to understand *why* a run failed, not just *that* it failed.
