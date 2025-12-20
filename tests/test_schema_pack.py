from nl2sql.context_engineering.schema_pack import build_schema_pack


def test_schema_pack_is_deterministic():
    raw_tables = {"b": ["y", "x"], "a": ["c", "a"]}
    raw_fks = {"b": [("x", "a.a")], "a": []}

    p1 = build_schema_pack(raw_tables, raw_fks)
    p2 = build_schema_pack(raw_tables, raw_fks)

    assert p1 == p2
    assert list(p1.tables.keys()) == ["a", "b"]
    assert p1.tables["a"].columns == ["a", "c"]
