import json
import pandas as pd
import streamlit as st
import plotly.express as px
from pathlib import Path

st.set_page_config(page_title="NL2SQL Benchmark Dashboard", layout="wide")

st.title("📊 NL2SQL Copilot – Benchmark Dashboard")

# 1. Load results
result_files = list(Path("benchmarks/results").rglob("*.jsonl"))
if not result_files:
    st.warning("No benchmark result files found in benchmarks/results/")
    st.stop()

file = st.selectbox("Select benchmark file", result_files)
rows = [json.loads(line) for line in open(file)]
df = pd.DataFrame(rows)

# 2. Summary metrics
st.subheader("Aggregate Metrics")
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Queries", len(df))
col2.metric("Execution Accuracy", f"{df['exec_acc'].mean() * 100:.1f}%")
col3.metric("Safety Violations", f"{df['safe_fail'].mean() * 100:.1f}%")
col4.metric("Average Latency (ms)", f"{df['latency_ms'].mean():.0f}")

# 3. Latency Distribution
st.subheader("Latency Distribution")
fig1 = px.histogram(df, x="latency_ms", nbins=30, title="Latency Histogram")
st.plotly_chart(fig1, use_container_width=True)

# 4. Cost vs Accuracy
st.subheader("Cost vs Execution Accuracy")
fig2 = px.scatter(
    df,
    x="cost_usd",
    y="exec_acc",
    color="provider",
    title="Trade-off: Cost vs Accuracy",
    hover_data=["query"],
)
st.plotly_chart(fig2, use_container_width=True)

# 5. Repair Stats
if "repair_attempts" in df.columns:
    st.subheader("Repair Attempts")
    fig3 = px.bar(
        df.groupby("repair_attempts").size().reset_index(name="count"),
        x="repair_attempts",
        y="count",
        title="Number of Repair Attempts per Query",
    )
    st.plotly_chart(fig3, use_container_width=True)
