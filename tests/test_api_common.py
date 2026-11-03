"""
tests/test_api_common.py
Tests FastAPI common endpoints: /health, /endpoint-usage, and error handling envelope.
"""

from fastapi.testclient import TestClient
from tinycore.api_common import create_app


def test_api_common_health_and_endpoint_usage():
    app = create_app(title="Test Service", version="0.1.0")
    client = TestClient(app, raise_server_exceptions=False)

    # 1. Health check
    r_health = client.get("/health")
    assert r_health.status_code == 200
    data = r_health.json()
    assert data["status"] == "healthy"
    assert "ledger" in data
    assert "monitors" in data
    assert data["monitors"]["global_cap"] == 50

    # 2. Endpoint usage
    r_usage = client.get("/endpoint-usage")
    assert r_usage.status_code == 200
    usage_data = r_usage.json()
    assert "total_calls" in usage_data
    assert "endpoints" in usage_data


def test_api_common_error_envelope():
    app = create_app(title="Error Service")

    @app.get("/trigger-error")
    def trigger_error():
        raise ValueError("Simulated failure")

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/trigger-error")
    assert resp.status_code == 500
    err_json = resp.json()
    assert "error" in err_json
    assert err_json["error"]["message"] == "Simulated failure"
    assert err_json["error"]["path"] == "/trigger-error"
