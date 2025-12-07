from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_healthz_ok():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.text == "ok"


def test_metrics_exposes_prometheus():
    # Hit one endpoint to bump counters
    client.get("/healthz")
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "http_requests_total" in r.text
