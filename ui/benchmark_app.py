from __future__ import annotations

import json
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Optional

import pandas as pd  # type: ignore
import streamlit as st


RESULTS_LITE_ROOT = Path("benchmarks") / "results"
RESULTS_PRO_ROOT = Path("benchmarks") / "results_pro"


def list_result_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.jsonl"))


def run_label(root: Path, p: Path) -> str:
    """Human-friendly label for a run file."""
    try:
        return str(p.relative_to(root))
    except Exception:
        return str(p)


@st.cache_data(show_spinner=False)
def read_jsonl(path_str: str) -> tuple[list[dict[str, Any]], int]:
    """Read JSONL into a list of dicts; returns (rows, bad_lines)."""
    path = Path(path_str)
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


def _to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def metric_or_none(df: pd.DataFrame, col: str, fn) -> Optional[float]:
    if col not in df.columns or df.empty:
        return None
    try:
        s = _to_num(df[col]).dropna()
        if s.empty:
            return None
        return float(fn(s))
    except Exception:
        return None


def fmt_metric_float(x: Optional[float], fmt: str) -> str:
    return "—" if x is None else format(x, fmt)


def fmt_metric_int(x: Optional[float]) -> str:
    return "—" if x is None else f"{x:,.0f}"


def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce key columns into consistent, display-friendly types."""
    out = df.copy()

    if "latency_ms" in out.columns:
        out["latency_ms"] = _to_num(out["latency_ms"]).round(0)

    if "ok" in out.columns:
        # ok might be bool, 0/1, or string; normalize to boolean where possible.
        ok_raw = out["ok"]
        if ok_raw.dtype == bool:
            out["ok"] = ok_raw
        else:
            out["ok"] = ok_raw.map(
                lambda v: bool(v)
                if isinstance(v, (int, float))
                else str(v).lower() in {"true", "1", "yes", "ok"}
            )

    if "error" in out.columns:
        out["error"] = out["error"].astype(str).replace({"None": "", "nan": ""})

    return out


def build_display_df(df: pd.DataFrame) -> pd.DataFrame:
    """Build a compact table suitable for docs screenshots."""
    preferred_cols = [
        "source",
        "db_id",
        "query",
        "ok",
        "latency_ms",
        "error",
    ]
    cols = [c for c in preferred_cols if c in df.columns]
    display = df[cols].copy() if cols else df.copy()

    # Keep the table compact and readable
    if "query" in display.columns:
        display["query"] = display["query"].astype(str)

    return display


def main() -> None:
    st.set_page_config(page_title="Benchmark Dashboard", layout="wide")
    st.title("Benchmark Dashboard")

    # Choose results root (lite vs pro)
    col_a, col_b = st.columns([2, 3])
    with col_a:
        mode = st.radio(
            "Mode",
            options=["Lite (benchmarks/results)", "Pro (benchmarks/results_pro)"],
            horizontal=True,
        )
    results_root = RESULTS_LITE_ROOT if mode.startswith("Lite") else RESULTS_PRO_ROOT

    if not results_root.exists():
        st.error(f"Results folder not found: {results_root}")
        st.stop()

    result_files = list_result_files(results_root)
    if not result_files:
        st.warning(f"No .jsonl files found under: {results_root}")
        st.info("Tip: generate results under benchmarks/results*/<timestamp>/*.jsonl")
        st.stop()

    with col_b:
        file_path = st.selectbox(
            "Select benchmark run",
            result_files,
            format_func=lambda p: run_label(results_root, p),
        )

    rows, bad_lines = read_jsonl(str(file_path))
    if bad_lines:
        st.warning(f"Skipped {bad_lines} malformed JSON line(s).")

    if not rows:
        st.warning("Selected file contains no valid JSON objects.")
        st.stop()

    df_raw = pd.DataFrame(rows)
    df = normalize_df(df_raw)

    with st.expander("Schema / columns", expanded=False):
        st.write(sorted(df.columns.astype(str).tolist()))

    if "latency_ms" not in df.columns:
        st.error("Missing required column: latency_ms")
        st.stop()

    # KPIs
    exec_acc = metric_or_none(df, "exec_acc", lambda s: s.mean())
    safe_fail = metric_or_none(df, "safe_fail", lambda s: s.mean())
    p50 = metric_or_none(df, "latency_ms", lambda s: s.quantile(0.50))
    p95 = metric_or_none(df, "latency_ms", lambda s: s.quantile(0.95))
    avg_cost = metric_or_none(df, "cost_usd", lambda s: s.mean())

    ok_rate = None
    if "ok" in df.columns and not df.empty:
        try:
            ok_rate = float(df["ok"].astype(bool).mean())
        except Exception:
            ok_rate = None

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Rows", f"{len(df):,}")
    k2.metric("OK rate", fmt_metric_float(ok_rate, ".3f"))
    k3.metric("Latency p50 (ms)", fmt_metric_int(p50))
    k4.metric("Latency p95 (ms)", fmt_metric_int(p95))
    k5.metric("Avg cost (USD)", "—" if avg_cost is None else f"{avg_cost:.6f}")

    # Optional: show exec_acc / safe_fail only when present (avoid loud N/A)
    extras = []
    if exec_acc is not None:
        extras.append(("Exec acc", f"{exec_acc:.3f}"))
    if safe_fail is not None:
        extras.append(("Safe fail", f"{safe_fail:.3f}"))
    if extras:
        cols = st.columns(len(extras))
        for c, (name, val) in zip(cols, extras):
            c.metric(name, val)

    # Breakdown by provider/model if present
    group_cols = [c for c in ["provider", "model"] if c in df.columns]
    if group_cols:
        st.subheader("Breakdown")
        g = df.copy()
        for col in ["latency_ms", "cost_usd", "exec_acc", "safe_fail"]:
            if col in g.columns:
                g[col] = _to_num(g[col])

        agg: dict[str, Any] = {"latency_ms": ["count", "mean"]}
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

    # Compact table for docs screenshots
    st.subheader("Rows (compact view)")
    display_df = build_display_df(df)

    # Sort: failures first, then slowest first (nice for inspection)
    sort_cols = []
    if "ok" in display_df.columns:
        sort_cols.append("ok")
    if "latency_ms" in display_df.columns:
        sort_cols.append("latency_ms")
    if sort_cols:
        ascending = [True, False] if sort_cols == ["ok", "latency_ms"] else None
        try:
            display_df = display_df.sort_values(by=sort_cols, ascending=ascending)
        except Exception:
            pass

    show_table = st.checkbox("Show table", value=False)
    if show_table:
        st.dataframe(display_df, use_container_width=True, height=280)

    # Row inspector (trace as JSON, not in the table)
    st.subheader("Row inspector")
    idx_options = list(range(len(df)))

    def label_for_idx(i: int) -> str:
        q = str(df.iloc[i].get("query", "")).strip()
        q_short = (q[:60] + "…") if len(q) > 60 else q
        ok = df.iloc[i].get("ok", "")
        lat = df.iloc[i].get("latency_ms", "")
        return f"#{i} | ok={ok} | latency_ms={lat} | {q_short}"

    selected_idx = st.selectbox(
        "Select a row",
        options=idx_options,
        format_func=label_for_idx,
        index=0,
    )

    row = df_raw.iloc[int(selected_idx)].to_dict()  # preserve original types for JSON
    col1, col2 = st.columns(2)
    with col1:
        with st.expander("Trace (JSON)", expanded=True):
            st.json(row.get("trace", {}))
    with col2:
        with st.expander("Full row (JSON)", expanded=False):
            st.json(row)

    # Optional: full raw DataFrame (debug only)
    with st.expander("Raw rows (debug)", expanded=False):
        st.dataframe(df, use_container_width=True)


if __name__ == "__main__":
    main()
