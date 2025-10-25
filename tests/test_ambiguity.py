from nl2sql.ambiguity_detector import AmbiguityDetector
from nl2sql.types import StageResult
from app.routers import nl2sql

def test_detects_ambiguous_terms():
    det = AmbiguityDetector()
    res = det.detect("Show me recent top singers", "table: singer(id,name,age)")
    assert len(res) >= 1
    assert "recent" in res[0].lower()

def test_not_false_positive():
    det = AmbiguityDetector()
    res = det.detect("List all singers older than 30", "table: singer(id, name, age)")
    assert res == []

def test_ambiguity_response():
    fake_result = StageResult(ok=True, data={"ambiguous": True, "questions": ["Clarify column?"]})
    response = nl2sql._to_dict(fake_result.data)
    assert response["ambiguous"] is True