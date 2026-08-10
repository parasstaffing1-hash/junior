import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.models.all import AutomationRun
from app.core.database import SessionLocal

client = TestClient(app)

def test_automation_gateway_actions():
    response = client.get("/api/v1/automation/actions")
    assert response.status_code == 200
    data = response.json()
    assert "actions" in data
    assert any(a["action"] == "report.excel" for a in data["actions"])

def test_automation_execute_idempotency():
    payload = {
        "action": "report.excel",
        "context": {},
        "payload": {"manifest": {"report": {"title": "Test"}, "sections": []}},
        "idempotency_key": "test_idem_123"
    }
    
    # First request
    response1 = client.post("/api/v1/automation/execute", json=payload)
    assert response1.status_code == 200
    run_id_1 = response1.json()["run_id"]
    
    # Second request with same idempotency key
    response2 = client.post("/api/v1/automation/execute", json=payload)
    assert response2.status_code == 200
    run_id_2 = response2.json()["run_id"]
    
    # Should be the exact same run
    assert run_id_1 == run_id_2
    
    # Check status endpoint
    response3 = client.get(f"/api/v1/automation/runs/{run_id_1}")
    assert response3.status_code == 200
    assert response3.json()["action"] == "report.excel"

def test_automation_execute_invalid_action():
    payload = {
        "action": "hacker.execute_code",
        "context": {},
        "payload": {}
    }
    response = client.post("/api/v1/automation/execute", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "AUTOMATION_ACTION_NOT_ALLOWED"
