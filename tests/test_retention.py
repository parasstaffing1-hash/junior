from __future__ import annotations

from datetime import timedelta

import pandas as pd

from app.core.jobs.queue import utc_now
from app.models.all import DatasetVersion


def _make_orphan_old_version(client, dataset_id: str):
    app = client.app
    db = app.state.SessionLocal()
    try:
        dataset = db.query(__import__("app.models.all", fromlist=["Dataset"]).Dataset).filter_by(id=dataset_id).first()
        version = DatasetVersion(dataset_id=dataset.id, version_number=99, parent_version_id=None, storage_path=f"{dataset.id}/v99.csv", sha256="placeholder", row_count=1, column_count=1, metadata_json={}, created_at=utc_now() - timedelta(days=365))
        path = app.state.storage.resolve(version.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("value\n1\n", encoding="utf-8")
        import hashlib
        version.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        db.add(version)
        db.commit()
        return version.id, path
    finally:
        db.close()


def test_retention_dry_run_holds_and_approved_execution(client):
    imported = client.post("/api/v1/datasets/import", files={"file": ("sales.csv", b"Sales\n25\n", "text/csv")})
    assert imported.status_code == 200, imported.text
    dataset_id = imported.json()["dataset_id"]
    old_version_id, old_path = _make_orphan_old_version(client, dataset_id)
    policy = client.post("/api/v1/security/retention/policies", json={"name": "One month", "retention_days": 30, "dataset_ids": [dataset_id]})
    assert policy.status_code == 201, policy.text
    hold = client.post("/api/v1/security/retention/holds", json={"dataset_id": dataset_id, "reason": "Open legal matter"})
    assert hold.status_code == 201, hold.text
    dry_run = client.post("/api/v1/security/retention/evaluate", json={"policy_id": policy.json()["id"]})
    assert dry_run.status_code == 200, dry_run.text
    candidate = next(item for item in dry_run.json()["candidates"] if item["version_id"] == old_version_id)
    assert candidate["legal_hold"] is True
    assert candidate["eligible"] is False
    assert old_path.exists()

    released = client.post(f"/api/v1/security/retention/holds/{hold.json()['id']}/release")
    assert released.status_code == 200
    blocked = client.post("/api/v1/security/retention/evaluate", json={"policy_id": policy.json()["id"], "execute": True})
    assert blocked.status_code == 403
    executed = client.post("/api/v1/security/retention/evaluate", json={"policy_id": policy.json()["id"], "execute": True, "approval_evidence": {"ticket": "RET-1", "reason": "retention window elapsed"}})
    assert executed.status_code == 200, executed.text
    assert old_version_id in executed.json()["deleted_version_ids"]
    assert not old_path.exists()
    db = client.app.state.SessionLocal()
    try:
        assert db.query(DatasetVersion).filter(DatasetVersion.id == old_version_id).first() is None
    finally:
        db.close()


def test_retention_is_tenant_scoped_in_production(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "api_key")
    monkeypatch.setenv("ADMIN_API_KEY", "bootstrap-secret")
    app = create_app(database_url="sqlite:///" + str(tmp_path / "retention.db"), storage_root=tmp_path / "storage")
    with TestClient(app) as secure_client:
        headers_a = {"X-API-Key": "bootstrap-secret", "X-Tenant-ID": "tenant-a"}
        headers_b = {"X-API-Key": "bootstrap-secret", "X-Tenant-ID": "tenant-b"}
        created = secure_client.post("/api/v1/security/retention/policies", headers=headers_a, json={"name": "Tenant A", "retention_days": 30})
        assert created.status_code == 201
        assert secure_client.get("/api/v1/security/retention/policies", headers=headers_b).json()["policies"] == []
    app.state.engine.dispose()
