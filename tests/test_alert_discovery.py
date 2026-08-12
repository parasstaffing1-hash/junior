from __future__ import annotations


def test_alert_discovery_evaluates_published_metrics_and_preserves_approval_gate(client):
    imported = client.post("/api/v1/datasets/import", files={"file": ("sales.csv", b"Sales\n25\n", "text/csv")})
    assert imported.status_code == 200, imported.text
    dataset_id = imported.json()["dataset_id"]
    metric = client.post(
        "/api/v1/workspaces/finance/assets",
        json={
            "asset_type": "kpi",
            "name": "Revenue",
            "dataset_id": dataset_id,
            "status": "draft",
            "definition": {"expression": "SUM(Sales)", "definition_type": "aggregate", "components": {"value": {"aggregation": "sum", "column": "Sales"}}},
        },
    )
    assert metric.status_code == 201, metric.text
    assert client.post(f"/api/v1/workspaces/finance/assets/{metric.json()['id']}/publish").status_code == 200
    rule = client.post(
        "/api/v1/alerts/rules",
        json={
            "workspace_id": "finance",
            "name": "Revenue breach",
            "definition": {"rule_type": "above", "kpi_slug": "Revenue", "threshold": 10, "channel": "webhook", "webhook_secret_ref": "OPERATIONS"},
        },
    )
    assert rule.status_code == 201, rule.text
    discovered = client.post("/api/v1/alerts/discover", json={"dataset_id": dataset_id, "workspace_id": "finance"})
    assert discovered.status_code == 200, discovered.text
    body = discovered.json()
    assert body["metrics_discovered"] == 1
    assert body["metrics"][0]["status"] == "EVALUATED"
    assert body["metrics"][0]["snapshot"]["value"] == 25.0
    assert body["metrics"][0]["alerts"]["deliveries"][0]["status"] == "PENDING_APPROVAL"

    plan = client.post("/api/v1/automation/plan", json={"action": "alert.discover", "context": {"dataset_id": dataset_id, "workspace_id": "finance"}, "payload": {}})
    assert plan.status_code == 200
    assert plan.json()["path"] == "/api/v1/alerts/discover"
    scheduled = client.post("/api/v1/jobs/schedules", json={"name": "Discover revenue", "action": "alert.discover", "context": {"dataset_id": dataset_id, "workspace_id": "finance"}, "payload": {}, "interval_seconds": 60})
    assert scheduled.status_code == 201, scheduled.text
    assert client.get("/api/v1/jobs/schedules/list").json()["schedules"][0]["action"] == "alert.discover"


def test_alert_discovery_skips_invalid_metric_without_failing_other_metrics(client):
    imported = client.post("/api/v1/datasets/import", files={"file": ("sales.csv", b"Sales\n25\n", "text/csv")})
    dataset_id = imported.json()["dataset_id"]
    for name, definition in (
        ("Good", {"expression": "SUM(Sales)", "definition_type": "aggregate", "components": {"value": {"aggregation": "sum", "column": "Sales"}}}),
        ("Bad", {"expression": "SUM(Missing)", "definition_type": "aggregate", "components": {"value": {"aggregation": "sum", "column": "Missing"}}}),
    ):
        created = client.post("/api/v1/workspaces/default/assets", json={"asset_type": "kpi", "name": name, "dataset_id": dataset_id, "status": "draft", "definition": definition})
        assert created.status_code == 201, created.text
        assert client.post(f"/api/v1/workspaces/default/assets/{created.json()['id']}/publish").status_code == 200
    discovered = client.post("/api/v1/alerts/discover", json={"dataset_id": dataset_id})
    assert discovered.status_code == 200
    assert {item["status"] for item in discovered.json()["metrics"]} == {"EVALUATED", "SKIPPED"}
