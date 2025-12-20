from __future__ import annotations

from typing import Dict, List, Tuple
from .types import SchemaPack, SchemaTable


def build_schema_pack(
    raw_tables: Dict[str, List[str]],
    raw_fks: Dict[str, List[Tuple[str, str]]],
    version: str = "v1",
) -> SchemaPack:
    """
    raw_tables: {"orders": ["id", "user_id", ...], ...}
    raw_fks: {"orders": [("user_id", "users.id"), ...], ...}
    """
    tables_sorted = sorted(raw_tables.keys())

    tables: Dict[str, SchemaTable] = {}
    for t in tables_sorted:
        cols = sorted(set(raw_tables.get(t, [])))
        fks_list = raw_fks.get(t, [])
        fks = {src: dst for (src, dst) in sorted(fks_list, key=lambda x: (x[0], x[1]))}
        tables[t] = SchemaTable(columns=cols, fks=fks)

    return SchemaPack(tables=tables, version=version)


def count_columns(pack: SchemaPack) -> int:
    return sum(len(t.columns) for t in pack.tables.values())
