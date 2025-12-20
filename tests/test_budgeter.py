from nl2sql.context_engineering.types import ContextBudget
from nl2sql.context_engineering.schema_pack import build_schema_pack
from nl2sql.context_engineering.budgeter import apply_budget


def test_budget_enforces_limits():
    raw_tables = {
        "t1": ["c3", "c2", "c1"],
        "t2": ["b1", "b2"],
        "t3": ["a1"],
    }
    raw_fks = {"t1": [], "t2": [], "t3": []}
    pack = build_schema_pack(raw_tables, raw_fks)

    budget = ContextBudget(max_tables=2, max_columns_per_table=2, max_total_columns=3)
    new_pack, reason = apply_budget(pack, budget)

    assert list(new_pack.tables.keys()) == ["t1", "t2"]
    assert new_pack.tables["t1"].columns == ["c1", "c2"]  # sorted then trimmed
    # total columns cap 3 => t1 keeps 2, t2 keeps 1
    assert new_pack.tables["t2"].columns == ["b1"]
    assert reason is not None
