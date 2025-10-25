from nl2sql.types import StageResult, StageTrace

def test_error_response():
    r = StageResult(ok=False, error=["Syntax error"])
    assert not r.ok
    assert r.error == ["Syntax error"]

def test_trace_dataclass_structure():
    t = StageTrace(stage="planner", duration_ms=12.5, token_in=10, token_out=20)
    assert t.stage == "planner"
    assert isinstance(t.duration_ms, float)
    assert t.token_out == 20

def test_stage_result_defaults():
    r = StageResult(ok=True)
    assert r.ok
    assert r.data is None
    assert r.error is None
