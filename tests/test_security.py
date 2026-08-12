from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def test_production_mode_rejects_missing_admin_key(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "api_key")
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ADMIN_API_KEY"):
        create_app(database_url="sqlite:///" + str(tmp_path / "security.db"), storage_root=tmp_path / "storage")


def test_api_key_authentication_tenant_isolation_and_audit(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "api_key")
    monkeypatch.setenv("ADMIN_API_KEY", "bootstrap-secret")
    app = create_app(database_url="sqlite:///" + str(tmp_path / "security.db"), storage_root=tmp_path / "storage")
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/datasets").status_code == 401
        session = client.get("/api/v1/security/session", headers={"X-API-Key": "bootstrap-secret", "X-Tenant-ID": "tenant-a"})
        assert session.status_code == 200
        assert session.json()["tenant_id"] == "tenant-a"

        imported = client.post(
            "/api/v1/datasets/import",
            headers={"X-API-Key": "bootstrap-secret", "X-Tenant-ID": "tenant-a"},
            files={"file": ("sales.csv", b"Region,Sales\nNorth,10\n", "text/csv")},
        )
        assert imported.status_code == 200, imported.text
        dataset_id = imported.json()["dataset_id"]
        policy = client.post("/api/v1/security/policies", headers={"X-API-Key": "bootstrap-secret", "X-Tenant-ID": "tenant-a"}, json={"name": "north-only", "target_type": "row", "definition": {"column": "Region", "allowed_values": ["North"]}})
        assert policy.status_code == 201, policy.text
        filtered = client.get(f"/api/v1/datasets/{dataset_id}/preview", headers={"X-API-Key": "bootstrap-secret", "X-Tenant-ID": "tenant-a"})
        assert filtered.status_code == 200
        assert filtered.json()["preview"]["total_rows"] == 1
        foreign = client.get(f"/api/v1/datasets/{dataset_id}/preview", headers={"X-API-Key": "bootstrap-secret", "X-Tenant-ID": "tenant-b"})
        assert foreign.status_code == 403
        audit = client.get("/api/v1/security/audit", headers={"X-API-Key": "bootstrap-secret", "X-Tenant-ID": "tenant-a"})
        assert audit.status_code == 200
        assert audit.json()["events"]
    app.state.engine.dispose()
