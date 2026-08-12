from __future__ import annotations


def test_production_readiness_fails_closed_by_default(client):
    response = client.get("/api/v1/platform/production-readiness")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is False
    assert any(item["id"] == "authentication" for item in body["blocking_checks"])


def test_observability_reports_bounded_route_slo_and_storage_evidence(client):
    assert client.get("/health").status_code == 200
    response = client.get("/api/v1/platform/observability")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "http" in body and "routes" in body["http"]
    assert body["storage"]["dataset_count"] == 0
    assert body["slo_targets"]["report_interaction_p95_seconds"] == 3.0
