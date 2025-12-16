from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_healthz_ok():
    """Health endpoint should be up and return a stable 'ok' body."""
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.text == "ok"


def test_metrics_exposes_prometheus_format():
    """
    Metrics endpoint should expose Prometheus text format.

    We avoid asserting on a specific metric name (brittle),
    and instead assert on the exposition format markers.
    """
    # Hit one endpoint to ensure the app has processed at least one request
    client.get("/healthz")

    r = client.get("/metrics")
    assert r.status_code == 200

    body = r.text

    # Prometheus text exposition typically includes HELP/TYPE lines
    assert "# HELP" in body or "# TYPE" in body

    # Ensure we have at least one sample line (a metric name + value)
    # This is a loose heuristic: any line that starts with a letter likely is a metric sample.
    assert any(line and line[0].isalpha() for line in body.splitlines())
