from __future__ import annotations

from typing import Optional, Tuple
from .types import SchemaPack, SchemaTable, ContextBudget


def apply_budget(
    pack: SchemaPack, budget: ContextBudget
) -> Tuple[SchemaPack, Optional[str]]:
    reason: Optional[str] = None

    table_names = sorted(pack.tables.keys())
    if len(table_names) > budget.max_tables:
        reason = f"tables_pruned_to_{budget.max_tables}"
        table_names = table_names[: budget.max_tables]

    new_tables = {}
    for t in table_names:
        tab = pack.tables[t]
        cols = tab.columns[: budget.max_columns_per_table]
        if len(tab.columns) > budget.max_columns_per_table:
            reason = reason or "columns_trimmed_per_table"
        new_tables[t] = SchemaTable(columns=cols, fks=tab.fks)

    new_pack = SchemaPack(tables=new_tables, version=pack.version)

    total_cols = sum(len(t.columns) for t in new_pack.tables.values())
    if total_cols > budget.max_total_columns:
        reason = reason or "columns_trimmed_total_cap"
        remaining = budget.max_total_columns
        capped = {}
        for t in sorted(new_pack.tables.keys()):
            tab = new_pack.tables[t]
            if remaining <= 0:
                capped[t] = SchemaTable(columns=[], fks=tab.fks)
                continue
            keep_n = min(len(tab.columns), remaining)
            keep = tab.columns[:keep_n]
            remaining -= len(keep)
            capped[t] = SchemaTable(columns=keep, fks=tab.fks)
        new_pack = SchemaPack(tables=capped, version=new_pack.version)

    return new_pack, reason
