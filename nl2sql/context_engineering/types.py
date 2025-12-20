from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class SchemaTable:
    columns: List[str]
    fks: Dict[str, str]  # kept for future; sqlite preview has none


@dataclass(frozen=True)
class SchemaPack:
    tables: Dict[str, SchemaTable]
    version: str = "v1"


@dataclass(frozen=True)
class ContextBudget:
    max_tables: int
    max_columns_per_table: int
    max_total_columns: int


@dataclass(frozen=True)
class ContextPacket:
    schema_pack: SchemaPack
    constraints: List[str]
    db_hints: Optional[dict]
    budget: ContextBudget

    tables_before: int
    columns_before: int
    tables_after: int
    columns_after: int
    budget_reason: Optional[str]
