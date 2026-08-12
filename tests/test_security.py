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


def test_workspace_assets_are_tenant_scoped(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "api_key")
    monkeypatch.setenv("ADMIN_API_KEY", "bootstrap-secret")
    app = create_app(database_url="sqlite:///" + str(tmp_path / "workspace-security.db"), storage_root=tmp_path / "storage")
    with TestClient(app) as client:
        headers_a = {"X-API-Key": "bootstrap-secret", "X-Tenant-ID": "tenant-a"}
        headers_b = {"X-API-Key": "bootstrap-secret", "X-Tenant-ID": "tenant-b"}
        created = client.post("/api/v1/workspaces/finance/assets", headers=headers_a, json={"asset_type": "kpi", "name": "Revenue", "definition": {"expression": "SUM(Sales)"}})
        assert created.status_code == 201, created.text
        asset_id = created.json()["id"]
        assert client.get("/api/v1/workspaces/finance/assets", headers=headers_b).json()["count"] == 0
        assert client.get(f"/api/v1/workspaces/finance/assets/{asset_id}", headers=headers_b).status_code == 404
        assert client.post(f"/api/v1/workspaces/finance/assets/{asset_id}/publish", headers=headers_b).status_code == 404
    app.state.engine.dispose()


def test_geographic_assets_are_tenant_scoped(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "api_key")
    monkeypatch.setenv("ADMIN_API_KEY", "bootstrap-secret")
    app = create_app(database_url="sqlite:///" + str(tmp_path / "geo-security.db"), storage_root=tmp_path / "storage")
    with TestClient(app) as client:
        headers_a = {"X-API-Key": "bootstrap-secret", "X-Tenant-ID": "tenant-a"}
        headers_b = {"X-API-Key": "bootstrap-secret", "X-Tenant-ID": "tenant-b"}
        payload = {
            "name": "Tenant A boundary",
            "country_code": "IN",
            "admin_level": 1,
            "source": "test",
            "source_version": "1",
            "license": "test",
            "geojson": {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"name": "A"}, "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}}]},
        }
        created = client.post("/api/v1/geographic/boundaries/import", headers=headers_a, json=payload)
        assert created.status_code == 201, created.text
        boundary_id = created.json()["boundary_id"]
        assert client.get("/api/v1/geographic/boundaries", headers=headers_b).json()["boundaries"] == []
        assert client.get(f"/api/v1/geographic/boundaries/{boundary_id}/geometry", headers=headers_b).status_code == 404
    app.state.engine.dispose()
