#!/usr/bin/env python3

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROMETHEUS_FILE = ROOT / "adapters" / "metrics" / "prometheus.py"
RULES_FILE = ROOT / "infra" / "prometheus" / "rules.yml"
DASHBOARD_DIR = ROOT / "infra" / "grafana" / "dashboards"

METRIC_DEF_RE = re.compile(r'name\s*=\s*"([^"]+)"')
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


def extract_defined_metrics() -> set[str]:
    text = PROMETHEUS_FILE.read_text(encoding="utf-8")
    return set(METRIC_DEF_RE.findall(text))


def extract_metrics_from_text(text: str) -> set[str]:
    tokens = set(PROMQL_TOKEN_RE.findall(text))
    return {
        t
        for t in tokens
        if t not in PROMQL_KEYWORDS_AND_FUNCS
        and not t.isupper()
        # quick drop of label keys that might show up as tokens in JSON
        and t
        not in {"le", "job", "instance", "stage", "status", "outcome", "hit", "ok"}
    }


def extract_used_metrics() -> set[str]:
    used = set()

    used |= extract_metrics_from_text(RULES_FILE.read_text(encoding="utf-8"))

    for path in DASHBOARD_DIR.glob("**/*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        used |= extract_metrics_from_text(json.dumps(data))

    return used


def is_generated_from_defined(metric: str, defined: set[str]) -> bool:
    """
    Prometheus client libraries generate extra series for certain metric types:
      - Histogram: <base>_bucket, <base>_sum, <base>_count, <base>_created
      - Summary:   <base>_sum, <base>_count, <base>_created   (quantiles are separate)
    We accept these as valid if <base> exists in 'defined'.
    """
    generated_suffixes = ("_bucket", "_sum", "_count", "_created")
    for base in defined:
        for suf in generated_suffixes:
            if metric == f"{base}{suf}":
                return True
    return False


def main() -> None:
    defined = extract_defined_metrics()
    used = extract_used_metrics()

    # Ignore recorded series (contain ':') — they are derived metrics, not raw ones.
    # Only validate raw metrics, allowing generated histogram/summary series.
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
