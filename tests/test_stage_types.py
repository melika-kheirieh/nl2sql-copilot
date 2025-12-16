from nl2sql.types import StageResult, StageTrace


# ---------------------------------------------------------------------------
# StageResult
# ---------------------------------------------------------------------------


def test_stage_result_ok_defaults():
    r = StageResult(ok=True)

    assert r.ok is True
    assert r.data is None
    assert r.error is None
    assert r.trace is None


def test_stage_result_error_defaults():
    r = StageResult(ok=False, error=["Syntax error"])

    assert r.ok is False
    assert r.data is None
    assert r.error == ["Syntax error"]
    assert r.trace is None


def test_stage_result_ok_should_not_have_error():
    r = StageResult(ok=True, error=None)

    assert r.ok
    assert r.error is None


# ---------------------------------------------------------------------------
# StageTrace
# ---------------------------------------------------------------------------


def test_stage_trace_basic_fields():
    t = StageTrace(stage="planner", duration_ms=12)

    assert t.stage == "planner"
    assert isinstance(t.duration_ms, int)


def test_stage_trace_optional_token_fields():
    t = StageTrace(
        stage="generator",
        duration_ms=3,
        token_in=10,
        token_out=20,
    )

    assert t.token_in == 10
    assert t.token_out == 20


def test_stage_trace_defaults_for_optional_fields():
    t = StageTrace(stage="safety", duration_ms=1)

    assert t.token_in is None
    assert t.token_out is None
    assert t.notes is None
