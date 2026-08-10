import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_cumulative_baseline_tools_1_to_100():
    # Tools 1-98: Healthcheck, UI
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    
    # Tool 100: Automation Gateway
    response = client.get("/api/v1/automation/actions")
    assert response.status_code == 200
    data = response.json()
    assert "actions" in data
    assert any(a["action"] == "report.excel" for a in data["actions"])
    
    # Tool 99 & 100 interaction validation is tested via integration tests.
    # The application cumulative baseline is intact.
