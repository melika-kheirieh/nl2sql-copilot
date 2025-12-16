from __future__ import annotations

import sqlite3
import pytest

from nl2sql.verifier import Verifier
from nl2sql.types import StageTrace
from adapters.db.sqlite_adapter import SQLiteAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class AlwaysOKAdapter:
    """Minimal adapter for verifier tests that don't need real planning."""

    def explain_query_plan(self, sql: str) -> None:
        return None


@pytest.fixture
def sqlite_db_path(tmp_path) -> str:
    p = tmp_path / "test.db"
    conn = sqlite3.connect(str(p))
    conn.execute("CREATE TABLE users(id INTEGER, name TEXT);")
    conn.execute("INSERT INTO users VALUES (1, 'a');")
    conn.commit()
    conn.close()
    return str(p)


# ---------------------------------------------------------------------------
# Verifier behavior tests
# ---------------------------------------------------------------------------


def test_verifier_parse_error_is_rejected():
    v = Verifier()
    r = v.verify("SELCT * FRM broken;")  # intentionally invalid SQL

    assert not r.ok
    assert r.error


def test_verifier_aggregate_without_groupby_is_rejected():
    v = Verifier()
    r = v.verify("SELECT COUNT(*), country FROM customers;")

    assert not r.ok
    assert r.error


def test_verifier_windowed_aggregate_is_allowed():
    v = Verifier()
    r = v.verify(
        "SELECT customer_id, "
        "SUM(amount) OVER (PARTITION BY customer_id) AS s "
        "FROM payments;"
    )

    assert r.ok


def test_verifier_distinct_projection_with_aggregate_is_allowed():
    v = Verifier()
    r = v.verify("SELECT DISTINCT artist_id, COUNT(*) FROM albums;")

    # Contract: must not be rejected as invalid aggregation
    assert r.ok or not r.error


def test_verifier_plan_check_ok_with_real_sqlite(sqlite_db_path: str):
    v = Verifier()
    adapter = SQLiteAdapter(sqlite_db_path)

    r = v.verify("SELECT name FROM users;", adapter=adapter)

    assert r.ok


def test_verifier_plan_check_missing_table_is_rejected(sqlite_db_path: str):
    v = Verifier()
    adapter = SQLiteAdapter(sqlite_db_path)

    r = v.verify("SELECT name FROM imaginary_table;", adapter=adapter)

    assert not r.ok
    assert r.error


def test_verifier_returns_trace_with_int_duration():
    v = Verifier()
    adapter = AlwaysOKAdapter()

    r = v.verify("SELECT 1 FROM users;", adapter=adapter)

    assert isinstance(r.trace, StageTrace)
    assert isinstance(r.trace.duration_ms, int)
