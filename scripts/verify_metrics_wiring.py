#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]

PROMETHEUS_FILE = ROOT / "adapters" / "metrics" / "prometheus.py"
RULES_FILE = ROOT / "infra" / "prometheus" / "rules.yml"
DASHBOARD_DIR = ROOT / "infra" / "grafana" / "dashboards"

# Extract metric definitions from prometheus_client constructors
# e.g. Counter("pipeline_runs_total", ...), Histogram("stage_duration_ms", ...)
METRIC_DEF_RE = re.compile(r'\b(?:Counter|Histogram|Summary|Gauge)\(\s*"([^"]+)"')

# Tokenize possible PromQL identifiers
PROMQL_TOKEN_RE = re.compile(r"([a-zA-Z_:][a-zA-Z0-9_:]*)")

PROMQL_KEYWORDS_AND_FUNCS = {
    # funcs / aggs
    "sum",
    "rate",
    "increase",
    "irate",
    "avg",
    "min",
    "max",
    "count",
    "histogram_quantile",
    "quantile",
    "topk",
    "bottomk",
    "clamp_min",
    "clamp_max",
    "abs",
    "round",
    "floor",
    "ceil",
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
    "and",
    "or",
    "unless",
    # literals
    "true",
    "false",
    "nan",
    "inf",
}

# Common label keys that may appear as tokens in dashboards / JSON / YAML
PROMQL_LABEL_KEYS = {
    "le",
    "job",
    "instance",
    "stage",
    "status",
    "outcome",
    "hit",
    "ok",
    "reason",
}

GENERATED_SUFFIXES = ("_bucket", "_sum", "_count", "_created")


def extract_defined_metrics() -> set[str]:
    text = PROMETHEUS_FILE.read_text(encoding="utf-8")
    return set(METRIC_DEF_RE.findall(text))


def iter_rule_exprs_from_yaml_text(text: str) -> Iterable[str]:
    """
    Minimal YAML extraction for Prometheus rules.yml:
    - extracts only values under 'expr:' keys
    - supports:
        expr: <single line>
        expr: |
          <multiline>
        expr: >
          <multiline folded>
    We do NOT parse the whole YAML (no dependency on PyYAML).
    """
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^(\s*)expr:\s*(.*)\s*$", line)
        if not m:
            i += 1
            continue

        indent = len(m.group(1))
        rest = m.group(2)

        # Block scalar
        if rest in ("|", ">"):
            i += 1
            block_lines: list[str] = []
            while i < len(lines):
                ln = lines[i]
                # stop when indentation goes back to <= expr indent
                if ln.strip() == "":
                    block_lines.append("")
                    i += 1
                    continue
                if len(ln) - len(ln.lstrip(" ")) <= indent:
                    break
                block_lines.append(ln.strip())
                i += 1
            yield "\n".join(block_lines).strip()
            continue

        # Single-line expr
        yield rest.strip().strip('"').strip("'")
        i += 1


def extract_metrics_from_promql(promql: str) -> set[str]:
    tokens = set(PROMQL_TOKEN_RE.findall(promql))
    out = set()
    for t in tokens:
        if t in PROMQL_KEYWORDS_AND_FUNCS:
            continue
        if t in PROMQL_LABEL_KEYS:
            continue
        if t.isupper():
            continue
        out.add(t)
    return out


def extract_used_metrics() -> set[str]:
    used: set[str] = set()

    # rules.yml: only expr fields
    rules_text = RULES_FILE.read_text(encoding="utf-8")
    for expr in iter_rule_exprs_from_yaml_text(rules_text):
        used |= extract_metrics_from_promql(expr)

    # dashboards: only targets[].expr
    for path in DASHBOARD_DIR.glob("**/*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        used |= extract_metrics_from_dashboard_json(data)

    return used


def extract_metrics_from_dashboard_json(obj: Any) -> set[str]:
    metrics: set[str] = set()

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "expr" and isinstance(v, str):
                    metrics.update(extract_metrics_from_promql(v))
                else:
                    walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(obj)
    return metrics


def is_generated_series(metric: str, defined: set[str]) -> bool:
    # Accept histogram/summary generated series like <base>_bucket/_sum/_count/_created
    for base in defined:
        for suf in GENERATED_SUFFIXES:
            if metric == f"{base}{suf}":
                return True
    return False


def main() -> None:
    defined = extract_defined_metrics()
    if not defined:
        print(f"❌ No metrics detected in {PROMETHEUS_FILE}. Regex may be wrong.")
        sys.exit(1)

    used = extract_used_metrics()

    # Only validate raw metrics (exclude recording rules, which contain ':')
    missing = sorted(
        m
        for m in used
        if ":" not in m and m not in defined and not is_generated_series(m, defined)
    )

    if missing:
        print("❌ Metrics used but not defined (raw):")
        for m in missing:
            print(f"  - {m}")
        sys.exit(1)

    print("✅ Metrics wiring OK — no drift detected.")


if __name__ == "__main__":
    main()
