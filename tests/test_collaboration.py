from __future__ import annotations


def test_reviews_and_shared_collections_are_governed_and_tenant_scoped(client):
    asset = client.post(
        "/api/v1/workspaces/finance/assets",
        json={"asset_type": "dashboard", "name": "Revenue dashboard", "definition": {"widgets": [{"type": "kpi", "metric": "revenue"}]}},
    )
    assert asset.status_code == 201, asset.text
    asset_id = asset.json()["id"]

    review = client.post("/api/v1/workspaces/finance/reviews", json={"asset_id": asset_id, "title": "Approve revenue dashboard"})
    assert review.status_code == 201, review.text
    review_id = review.json()["id"]
    comment = client.post(f"/api/v1/workspaces/finance/reviews/{review_id}/comments", json={"body": "Numbers reconcile to the source extract."})
    assert comment.status_code == 201, comment.text
    decision = client.post(f"/api/v1/workspaces/finance/reviews/{review_id}/decision", json={"decision": "APPROVE", "evidence": {"test_run": "243-pass", "reconciliation": "passed"}})
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "RESOLVED"

    collection = client.post("/api/v1/workspaces/finance/collections", json={"name": "Leadership pack", "description": "Certified leadership assets"})
    assert collection.status_code == 201, collection.text
    item = client.post(f"/api/v1/workspaces/finance/collections/{collection.json()['id']}/items", json={"asset_id": asset_id})
    assert item.status_code == 201, item.text
    listed = client.get("/api/v1/workspaces/finance/collections")
    assert listed.status_code == 200
    assert listed.json()["collections"][0]["asset_ids"] == [asset_id]


def test_review_and_collection_routes_require_authentication_in_production(monkeypatch, tmp_path):
    from app.main import create_app
    from fastapi.testclient import TestClient

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "api_key")
    monkeypatch.setenv("ADMIN_API_KEY", "bootstrap-secret")
    app = create_app(database_url="sqlite:///" + str(tmp_path / "collaboration.db"), storage_root=tmp_path / "storage")
    with TestClient(app) as secure_client:
        assert secure_client.get("/api/v1/workspaces/finance/reviews").status_code == 401
        headers_a = {"X-API-Key": "bootstrap-secret", "X-Tenant-ID": "tenant-a"}
        headers_b = {"X-API-Key": "bootstrap-secret", "X-Tenant-ID": "tenant-b"}
        created = secure_client.post("/api/v1/workspaces/finance/collections", headers=headers_a, json={"name": "Tenant A pack"})
        assert created.status_code == 201
        assert secure_client.get("/api/v1/workspaces/finance/collections", headers=headers_b).json()["collections"] == []
    app.state.engine.dispose()
