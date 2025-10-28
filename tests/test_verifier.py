from nl2sql.verifier import Verifier
from nl2sql.types import StageResult, StageTrace

def make_exec_result(ok=True, error=None):
    return StageResult(ok=ok, data={"dummy": True} if ok else None, trace=None, error=error)

def test_verifier_handles_execution_error():
    v = Verifier()
    r = v.run(sql="SELECT 1", exec_result=make_exec_result(ok=False, error=["db error"]))
    assert not r.ok
    assert "execution_error" in r.trace.notes["reason"]
    assert r.error == ["db error"]

def test_verifier_detects_agg_without_group():
    v = Verifier()
    sql = "SELECT COUNT(*) FROM users"
    r = v.run(sql=sql, exec_result=make_exec_result(ok=True))
    assert not r.ok
    assert any("Aggregation without GROUP BY" in e for e in r.error)

def test_verifier_parses_valid_sql_ok():
    v = Verifier()
    sql = "SELECT COUNT(*), city FROM users GROUP BY city"
    r = v.run(sql=sql, exec_result=make_exec_result(ok=True))
    assert r.ok
    assert r.data == {"verified": True}
    assert isinstance(r.trace, StageTrace)
