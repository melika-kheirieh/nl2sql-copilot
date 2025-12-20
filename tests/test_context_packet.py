from nl2sql.context_engineering.types import ContextBudget
from nl2sql.context_engineering.engineer import ContextEngineer


def _to_schema_preview(raw_tables: dict[str, list[str]]) -> str:
    # Deterministic: stable ordering for test reliability
    lines: list[str] = []
    for t in sorted(raw_tables.keys()):
        cols = ", ".join(raw_tables[t])
        lines.append(f"{t}({cols})")
    return "\n".join(lines)


def test_context_packet_counts():
    raw_tables = {"t1": ["c1", "c2"], "t2": ["b1"]}
    budget = ContextBudget(
        max_tables=10,
        max_columns_per_table=10,
        max_total_columns=999,
    )

    schema_preview = _to_schema_preview(raw_tables)

    packet = ContextEngineer(budget=budget).build(schema_preview=schema_preview)

    assert packet.tables_before == 2
    assert packet.columns_before == 3
    assert packet.tables_after == 2
    assert packet.columns_after == 3
    assert packet.budget_reason is None
