from __future__ import annotations

import sqlite3


from nl2sql.executor import Executor
from adapters.db.sqlite_adapter import SQLiteAdapter


def _make_db(db_path) -> None:
    """Create a minimal SQLite DB for executor tests."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE users(id INTEGER, name TEXT);")
        conn.execute("INSERT INTO users VALUES (1, 'Alice');")
        conn.commit()
    finally:
        conn.close()


def test_executor_runs_select(tmp_path):
    """Executor should successfully run a simple SELECT query."""
    db_path = tmp_path / "test.db"
    _make_db(db_path)

    ex = Executor(SQLiteAdapter(str(db_path)))
    res = ex.run("SELECT id, name FROM users ORDER BY id;")

    assert res.ok, f"Expected ok=True, got: {res!r}"

    # Be explicit and less brittle about the expected shape.
    assert "rows" in res.data and isinstance(res.data["rows"], list)
    assert res.data["rows"] == [(1, "Alice")]


def test_executor_returns_error_on_bad_sql(tmp_path):
    """Executor should return a non-ok result for invalid SQL (syntax error)."""
    db_path = tmp_path / "test.db"
    _make_db(db_path)

    ex = Executor(SQLiteAdapter(str(db_path)))
    res = ex.run("SELEC * FROM users;")  # intentional typo

    assert not res.ok
    # Keep this loose to avoid coupling to exact driver error strings.
    assert res.error is not None


def test_executor_returns_error_on_missing_table(tmp_path):
    """Executor should return a non-ok result when the referenced table does not exist."""
    db_path = tmp_path / "test.db"
    _make_db(db_path)

    ex = Executor(SQLiteAdapter(str(db_path)))
    res = ex.run("SELECT * FROM missing_table;")

    assert not res.ok
    assert res.error is not None
