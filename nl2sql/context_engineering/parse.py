from __future__ import annotations

from typing import Dict, List
import re


_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*$")


def parse_sqlite_schema_preview(schema_preview: str) -> Dict[str, List[str]]:
    raw_tables: Dict[str, List[str]] = {}

    for line in (schema_preview or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = _LINE_RE.match(line)
        if not m:
            # ignore unknown line formats (future-proof)
            continue
        table = m.group(1)
        cols_blob = m.group(2).strip()
        cols = [c.strip() for c in cols_blob.split(",") if c.strip()]
        # stable order: keep what service produced but also de-dup deterministically
        cols = sorted(set(cols))
        raw_tables[table] = cols

    # stable order: sort keys by caller later
    return raw_tables
