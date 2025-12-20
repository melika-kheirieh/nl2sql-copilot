from __future__ import annotations

from .types import ContextBudget, ContextPacket, SchemaPack, SchemaTable
from .parse import parse_sqlite_schema_preview
from .budgeter import apply_budget


DEFAULT_CONSTRAINTS = [
    "SELECT_ONLY",
    "NO_DDL_DML",
    "NO_ATTACH_PRAGMA",
    "SINGLE_STATEMENT",
    "LIMIT_REQUIRED_IF_MISSING",
]


class ContextEngineer:
    def __init__(
        self,
        *,
        budget: ContextBudget,
        constraints: list[str] | None = None,
    ) -> None:
        self.budget = budget
        self.constraints = constraints or DEFAULT_CONSTRAINTS

    def build(self, *, schema_preview: str) -> ContextPacket:
        raw_tables = parse_sqlite_schema_preview(schema_preview)

        tables_sorted = sorted(raw_tables.keys())
        tables = {t: SchemaTable(columns=raw_tables[t], fks={}) for t in tables_sorted}
        pack = SchemaPack(tables=tables, version="v1")

        tables_before = len(pack.tables)
        columns_before = sum(len(t.columns) for t in pack.tables.values())

        packed, reason = apply_budget(pack, self.budget)

        tables_after = len(packed.tables)
        columns_after = sum(len(t.columns) for t in packed.tables.values())

        return ContextPacket(
            schema_pack=packed,
            constraints=self.constraints,
            db_hints=None,
            budget=self.budget,
            tables_before=tables_before,
            columns_before=columns_before,
            tables_after=tables_after,
            columns_after=columns_after,
            budget_reason=reason,
        )
