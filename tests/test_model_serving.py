from __future__ import annotations

import numpy as np
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_model_prediction_is_approval_gated_and_audited(client):
    rows = [{"x": index, "segment": "A" if index % 2 else "B", "target": "yes" if index % 3 == 0 else "no"} for index in range(36)]
    imported = client.post("/api/v1/datasets/import", files={"file": ("model.csv", ("x,segment,target\n" + "\n".join(f"{row['x']},{row['segment']},{row['target']}" for row in rows)).encode(), "text/csv")})
    assert imported.status_code == 200, imported.text
    dataset_id = imported.json()["dataset_id"]
    trained = client.post(f"/api/v1/datasets/{dataset_id}/ml/train", json={"target_column": "target", "feature_columns": ["x", "segment"], "task_type": "classification", "algorithm": "logistic_regression", "model_name": "Serving smoke"})
    assert trained.status_code == 200, trained.text
    version_id = trained.json()["registered_model"]["model_version_id"]
    denied = client.post(f"/api/v1/models/{version_id}/predict", json={"records": [{"x": 1, "segment": "A"}]})
    assert denied.status_code == 403
    assert client.post(f"/api/v1/models/{version_id}/status", json={"status": "VALIDATED", "evidence": {"validation": True}}).status_code == 200
    assert client.post(f"/api/v1/models/{version_id}/status", json={"status": "APPROVED", "approved_by": "admin", "evidence": {"validation": True}}).status_code == 200
    assert client.post(f"/api/v1/models/{version_id}/status", json={"status": "CHAMPION", "approved_by": "admin", "evidence": {"validation": True}}).status_code == 200
    predicted = client.post(f"/api/v1/models/{version_id}/predict", json={"records": [{"x": 1, "segment": "A"}, {"x": 2, "segment": "B"}], "include_probabilities": True})
    assert predicted.status_code == 200, predicted.text
    assert len(predicted.json()["predictions"]) == 2


def test_incident_diagnosis_uses_persisted_operational_evidence(client):
    imported = client.post("/api/v1/datasets/import", files={"file": ("ops.csv", b"id,value\n1,10\n2,20\n", "text/csv")})
    assert imported.status_code == 200, imported.text
    dataset_id = imported.json()["dataset_id"]
    response = client.post(f"/api/v1/datasets/{dataset_id}/orchestration/incident_root_cause", json={"objective": "Diagnose the refresh incident"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["most_likely_cause"]["cause"] == "insufficient_evidence"
    assert body["operational_evidence"] == []


def test_registered_models_are_tenant_scoped(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "api_key")
    monkeypatch.setenv("ADMIN_API_KEY", "bootstrap-secret")
    app = create_app(database_url="sqlite:///" + str(tmp_path / "model-tenants.db"), storage_root=tmp_path / "storage")
    rows = "x,target\n" + "\n".join(f"{index},{'yes' if index % 2 else 'no'}" for index in range(24))
    try:
        with TestClient(app) as client:
            for tenant in ("tenant-a", "tenant-b"):
                headers = {"X-API-Key": "bootstrap-secret", "X-Tenant-ID": tenant}
                imported = client.post("/api/v1/datasets/import", headers=headers, files={"file": (f"{tenant}.csv", rows.encode(), "text/csv")})
                assert imported.status_code == 200, imported.text
                trained = client.post(f"/api/v1/datasets/{imported.json()['dataset_id']}/ml/train", headers=headers, json={"target_column": "target", "feature_columns": ["x"], "task_type": "classification", "algorithm": "logistic_regression", "model_name": "Shared name"})
                assert trained.status_code == 200, trained.text
            for tenant in ("tenant-a", "tenant-b"):
                response = client.get("/api/v1/models", headers={"X-API-Key": "bootstrap-secret", "X-Tenant-ID": tenant})
                assert response.status_code == 200
                assert len(response.json()["models"]) == 1
                assert response.json()["models"][0]["tenant_id"] == tenant
    finally:
        app.state.engine.dispose()
