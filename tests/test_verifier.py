from __future__ import annotations

import sqlite3
import pytest

from nl2sql.verifier import Verifier
from nl2sql.types import StageTrace
from adapters.db.sqlite_adapter import SQLiteAdapter


class AlwaysOKAdapter:
    """Minimal adapter for lint-only tests."""

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


def test_verifier_parse_error_is_not_ok():
    v = Verifier()
    r = v.verify("SELCT * FRM broken;")  # intentionally broken
    assert not r.ok
    assert r.error and "parse_error" in r.error


def test_verifier_plain_aggregate_without_groupby_is_flagged():
    v = Verifier()
    r = v.verify("SELECT COUNT(*), country FROM customers;")
    assert not r.ok
    assert r.error and "aggregation_without_group_by" in r.error


def test_verifier_windowed_aggregate_is_ok_without_groupby():
    v = Verifier()
    r = v.verify(
        "SELECT customer_id, SUM(amount) OVER (PARTITION BY customer_id) AS s FROM payments;",
    )
    assert r.ok, r.error


def test_verifier_distinct_projection_is_ok_with_aggregate():
    v = Verifier()
    r = v.verify("SELECT DISTINCT artist_id, COUNT(*) FROM albums;")
    assert r.ok or "aggregation_without_group_by" not in (r.error or [])


def test_verifier_plan_check_ok(sqlite_db_path: str):
    v = Verifier()
    adapter = SQLiteAdapter(sqlite_db_path)
    r = v.verify("SELECT name FROM users;", adapter=adapter)
    assert r.ok, r.error


def test_verifier_plan_check_missing_table(sqlite_db_path: str):
    v = Verifier()
    adapter = SQLiteAdapter(sqlite_db_path)
    r = v.verify("SELECT name FROM imaginary_table;", adapter=adapter)
    assert not r.ok
    assert any("no such table" in e.lower() for e in (r.error or []))


def test_verifier_returns_trace_with_int_duration():
    v = Verifier()
    adapter = AlwaysOKAdapter()
    r = v.verify("SELECT 1 FROM users;", adapter=adapter)
    assert isinstance(r.trace, StageTrace)
    assert isinstance(r.trace.duration_ms, int)
