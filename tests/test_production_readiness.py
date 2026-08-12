from __future__ import annotations


def test_production_readiness_fails_closed_by_default(client):
    response = client.get("/api/v1/platform/production-readiness")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is False
    assert any(item["id"] == "authentication" for item in body["blocking_checks"])
