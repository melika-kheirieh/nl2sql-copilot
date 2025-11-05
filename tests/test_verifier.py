from nl2sql.verifier import Verifier
from nl2sql.types import StageTrace


# --- Tiny fake adapter for preview execution ---------------------------------
class FakeAdapter:
    """Mimics adapter.execute_preview(sql) returning dicts with ok/error."""

    def __init__(self, will_ok=True, error=None):
        self.will_ok = will_ok
        self.error = error

    def execute_preview(self, sql: str):
        if self.will_ok:
            return {"ok": True}
        if self.error:
            return {"ok": False, "error": self.error}
        return {"ok": False}


# -----------------------------------------------------------------------------


def test_verifier_parse_error_is_not_ok():
    v = Verifier()
    fake = FakeAdapter(will_ok=True)
    r = v.verify("SELCT * FRM broken;", adapter=fake)  # intentionally broken
    assert not r.ok
    assert r.error and "parse_error" in r.error


def test_verifier_plain_aggregate_without_groupby_is_flagged():
    v = Verifier()
    fake = FakeAdapter(will_ok=True)
    r = v.verify("SELECT COUNT(*), country FROM customers;", adapter=fake)
    assert not r.ok
    assert r.error and "aggregation_without_group_by" in r.error


def test_verifier_windowed_aggregate_is_ok_without_groupby():
    v = Verifier()
    fake = FakeAdapter(will_ok=True)
    r = v.verify(
        "SELECT customer_id, SUM(amount) OVER (PARTITION BY customer_id) AS s FROM payments;",
        adapter=fake,
    )
    assert r.ok, r.error


def test_verifier_distinct_projection_is_ok_with_aggregate():
    v = Verifier()
    fake = FakeAdapter(will_ok=True)
    r = v.verify(
        "SELECT DISTINCT artist_id, COUNT(*) FROM albums;",
        adapter=fake,
    )
    # DISTINCT + aggregate can be valid; avoid false positives.
    assert r.ok or "aggregation_without_group_by" not in (r.error or [])


def test_verifier_exec_error_is_reported():
    v = Verifier()
    fake = FakeAdapter(will_ok=False, error="no such table: imaginary_table")
    r = v.verify("SELECT name FROM imaginary_table;", adapter=fake)
    assert not r.ok
    assert any(("exec_error" in e) or ("exec_exception" in e) for e in (r.error or []))


def test_verifier_returns_trace_with_int_duration():
    v = Verifier()
    fake = FakeAdapter(will_ok=True)
    r = v.verify("SELECT 1;", adapter=fake)
    assert isinstance(r.trace, StageTrace)
    # Some implementations store duration as int milliseconds:
    assert isinstance(r.trace.duration_ms, int)
