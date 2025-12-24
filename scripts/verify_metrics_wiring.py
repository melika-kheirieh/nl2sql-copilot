#!/usr/bin/env python3

import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]

PROMETHEUS_FILE = ROOT / "adapters" / "metrics" / "prometheus.py"
RULES_FILE = ROOT / "infra" / "prometheus" / "rules.yml"
DASHBOARD_DIR = ROOT / "infra" / "grafana" / "dashboards"

# Extract metric names from prometheus client constructors:
# Counter("x", ...), Gauge("x", ...), Histogram("x", ...), Summary("x", ...)
METRIC_CTOR_RE = re.compile(r'\b(?:Counter|Gauge|Histogram|Summary)\(\s*"([^"]+)"')

# Fallback in case a metric is defined via keyword arg name="..."
METRIC_NAME_KW_RE = re.compile(r'\bname\s*=\s*"([^"]+)"')

# PromQL token pattern
PROMQL_TOKEN_RE = re.compile(r"([a-zA-Z_:][a-zA-Z0-9_:]*)")

PROMQL_KEYWORDS_AND_FUNCS = {
    # aggregations / funcs
    "sum",
    "rate",
    "increase",
    "irate",
    "avg",
    "min",
    "max",
    "count",
    "count_values",
    "stddev",
    "stdvar",
    "bottomk",
    "topk",
    "quantile",
    "histogram_quantile",
    "clamp_min",
    "clamp_max",
    "abs",
    "round",
    "floor",
    "ceil",
    "scalar",
    "vector",
    "sort",
    "sort_desc",
    "label_replace",
    "label_join",
    "time",
    # modifiers / keywords
    "by",
    "without",
    "offset",
    "bool",
    "on",
    "ignoring",
    "group_left",
    "group_right",
    # literals / common
    "true",
    "false",
    "nan",
    "inf",
}

PROMQL_LABEL_KEYS = {
    "le",
    "job",
    "instance",
    "stage",
    "status",
    "outcome",
    "hit",
    "ok",
}

# label values that appear in your rules/dashboards
PROMQL_COMMON_LABEL_VALUES = {
    "attempt",
    "success",
    "failed",
    "ok",
    "error",
    "ambiguous",
    "true",
    "false",
}

# time units that can show up e.g. [5m], [10s]
PROMQL_TIME_UNITS = {"ms", "s", "m", "h", "d", "w", "y"}


def extract_defined_metrics() -> set[str]:
    text = PROMETHEUS_FILE.read_text(encoding="utf-8")
    defined = set(METRIC_CTOR_RE.findall(text))
    defined |= set(METRIC_NAME_KW_RE.findall(text))
    return defined


def _collect_promql_from_rules_yml(text: str) -> list[str]:
    """
    Extract only PromQL expressions from rules.yml:
    - expr: <single line>
    - expr: |  (multiline indented block)
    - expr: >  (multiline indented block)
    """
    lines = text.splitlines()
    exprs: list[str] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        if not stripped.startswith("expr:"):
            i += 1
            continue

        indent = len(line) - len(stripped)
        rest = stripped[len("expr:") :].strip()

        # Case 1: expr: <single-line>
        if rest and rest not in {"|", ">"}:
            exprs.append(rest)
            i += 1
            continue

        # Case 2: expr: | or expr: > or expr: (empty) with following indented block
        i += 1
        block_lines: list[str] = []
        while i < len(lines):
            nxt = lines[i]
            nxt_stripped = nxt.lstrip()
            nxt_indent = len(nxt) - len(nxt_stripped)

            # Stop when indentation returns to expr level (or less)
            if nxt_stripped and nxt_indent <= indent:
                break

            # Keep blank lines inside block as separators
            block_lines.append(nxt_stripped)
            i += 1

        expr = "\n".join(block_lines).strip()
        if expr:
            exprs.append(expr)

    return exprs


def _collect_promql_from_dashboard_json(obj: Any) -> Iterable[str]:
    """
    Recursively collect PromQL strings from Grafana dashboard JSON.
    Common keys are: "expr" (Prometheus target), sometimes "query".
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in {"expr", "query"} and isinstance(v, str):
                yield v
            else:
                yield from _collect_promql_from_dashboard_json(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _collect_promql_from_dashboard_json(item)


def extract_promql_sources() -> list[str]:
    sources: list[str] = []

    # rules.yml
    rules_text = RULES_FILE.read_text(encoding="utf-8")
    sources.extend(_collect_promql_from_rules_yml(rules_text))

    # dashboards
    for path in DASHBOARD_DIR.glob("**/*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        sources.extend(list(_collect_promql_from_dashboard_json(data)))

    return sources


def extract_metrics_from_promql(promql: str) -> set[str]:
    tokens = set(PROMQL_TOKEN_RE.findall(promql))
    out: set[str] = set()
    for t in tokens:
        if t in PROMQL_KEYWORDS_AND_FUNCS:
            continue
        if t in PROMQL_LABEL_KEYS:
            continue
        if t in PROMQL_COMMON_LABEL_VALUES:
            continue
        if t in PROMQL_TIME_UNITS:
            continue
        if t.isupper():
            continue
        out.add(t)
    return out


def is_generated_from_defined(metric: str, defined: set[str]) -> bool:
    """
    Accept generated series from client libraries:
      - Histogram: <base>_bucket, <base>_sum, <base>_count, <base>_created
      - Summary:   <base>_sum, <base>_count, <base>_created
    """
    generated_suffixes = ("_bucket", "_sum", "_count", "_created")
    for base in defined:
        for suf in generated_suffixes:
            if metric == f"{base}{suf}":
                return True
    return False


def main() -> None:
    defined = extract_defined_metrics()
    promql_sources = extract_promql_sources()

    used: set[str] = set()
    for q in promql_sources:
        used |= extract_metrics_from_promql(q)

    # Ignore recorded series (contain ':') — derived metrics are allowed.
    missing = sorted(
        m
        for m in used
        if ":" not in m
        and m not in defined
        and not is_generated_from_defined(m, defined)
    )

    if missing:
        print("❌ Metrics used but not defined (raw):")
        for m in missing:
            print(f"  - {m}")
        sys.exit(1)

    print("✅ Metrics wiring OK — no drift detected.")


if __name__ == "__main__":
    main()
