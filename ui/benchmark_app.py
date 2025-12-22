from __future__ import annotations

import json
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd  # type: ignore
import streamlit as st


RESULTS_ROOT = Path("benchmarks") / "results"


def list_result_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.jsonl"))


def run_label(root: Path, p: Path) -> str:
    """Human-friendly label for a run file."""
    try:
        return str(p.relative_to(root))
    except Exception:
        return str(p)


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Read JSONL into a list of dicts; returns (rows, bad_lines)."""
    rows: list[dict[str, Any]] = []
    bad = 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except JSONDecodeError:
                bad += 1
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows, bad


def metric_or_na(df: pd.DataFrame, col: str, fn) -> Optional[float]:
    if col not in df.columns or df.empty:
        return None
    try:
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        if s.empty:
            return None
        return float(fn(s))
    except Exception:
        return None


def main() -> None:
    st.set_page_config(page_title="Benchmark Dashboard", layout="wide")
    st.title("Benchmark Dashboard")

    if not RESULTS_ROOT.exists():
        st.error(f"Results folder not found: {RESULTS_ROOT}")
        st.stop()

    result_files = list_result_files(RESULTS_ROOT)
    if not result_files:
        st.warning(f"No .jsonl files found under: {RESULTS_ROOT}")
        st.info("Tip: generate results under benchmarks/results/<timestamp>/*.jsonl")
        st.stop()

    file_path = st.selectbox(
        "Select benchmark run",
        result_files,
        format_func=lambda p: run_label(RESULTS_ROOT, p),
    )

    rows, bad_lines = read_jsonl(file_path)
    if bad_lines:
        st.warning(f"Skipped {bad_lines} malformed JSON line(s).")

    if not rows:
        st.warning("Selected file contains no valid JSON objects.")
        st.stop()

    df = pd.DataFrame(rows)

    with st.expander("Schema / columns", expanded=False):
        st.write(sorted(df.columns.astype(str).tolist()))

    required = {"latency_ms"}
    missing = required - set(df.columns)
    if missing:
        st.error(f"Missing required column(s): {sorted(missing)}")
        st.stop()

    exec_acc = metric_or_na(df, "exec_acc", lambda s: s.mean())
    safe_fail = metric_or_na(df, "safe_fail", lambda s: s.mean())
    p50 = metric_or_na(df, "latency_ms", lambda s: s.quantile(0.50))
    p95 = metric_or_na(df, "latency_ms", lambda s: s.quantile(0.95))
    avg_cost = metric_or_na(df, "cost_usd", lambda s: s.mean())

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Rows", f"{len(df):,}")
    c2.metric("Exec acc", "N/A" if exec_acc is None else f"{exec_acc:.3f}")
    c3.metric("Safe fail", "N/A" if safe_fail is None else f"{safe_fail:.3f}")
    c4.metric("Latency p50 (ms)", "N/A" if p50 is None else f"{p50:.1f}")
    c5.metric("Latency p95 (ms)", "N/A" if p95 is None else f"{p95:.1f}")

    if avg_cost is not None:
        st.metric("Avg cost (USD)", f"{avg_cost:.6f}")

    group_cols = [c for c in ["provider", "model"] if c in df.columns]
    if group_cols:
        st.subheader("Breakdown")
        g = df.copy()
        for col in ["latency_ms", "cost_usd", "exec_acc", "safe_fail"]:
            if col in g.columns:
                g[col] = pd.to_numeric(g[col], errors="coerce")

        agg: Dict[str, Any] = {"latency_ms": ["count", "mean"]}
        if "exec_acc" in g.columns:
            agg["exec_acc"] = ["mean"]
        if "safe_fail" in g.columns:
            agg["safe_fail"] = ["mean"]
        if "cost_usd" in g.columns:
            agg["cost_usd"] = ["mean"]

        out = g.groupby(group_cols).agg(agg)
        out.columns = [
            "_".join([a, b]).strip("_") for a, b in out.columns.to_flat_index()
        ]
        out = out.reset_index().sort_values(by="latency_ms_count", ascending=False)
        st.dataframe(out, use_container_width=True)

    st.subheader("Raw rows")
    st.dataframe(df, use_container_width=True)


if __name__ == "__main__":
    main()
