from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, text

from app.core.alerts.service import evaluate_and_deliver, validate_alert_definition
from app.core.connectors.service import ConnectorService
from app.models.all import AlertRule


def test_alert_evaluation_is_approval_gated(client):
    response = client.post(
        "/api/v1/alerts/rules",
        json={
            "name": "revenue-floor",
            "definition": {
                "kpi_slug": "revenue",
                "rule_type": "below",
                "threshold": 100,
                "webhook_secret_ref": "finance_ops",
            },
        },
    )
    assert response.status_code == 201, response.text
    evaluation = client.post(
        "/api/v1/alerts/evaluate",
        json={"snapshot": {"kpi_slug": "revenue", "value": 80}, "approved": True},
    )
    assert evaluation.status_code == 200, evaluation.text
    body = evaluation.json()
    assert body["triggered_count"] == 1
    assert body["deliveries"][0]["status"] == "FAILED"
    assert body["deliveries"][0]["response"]["error"] == "WEBHOOK_NOT_CONFIGURED"


def test_alert_rules_reject_raw_webhook_urls(client):
    response = client.post(
        "/api/v1/alerts/rules",
        json={
            "name": "unsafe",
            "definition": {"kpi_slug": "revenue", "rule_type": "above", "threshold": 1, "webhook_url": "https://example.com/hook"},
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "WEBHOOK_SECRET_REF_REQUIRED"


def test_database_connector_catalog_reports_optional_drivers_explicitly():
    catalog = {item["id"]: item for item in ConnectorService.catalog()}
    assert catalog["postgresql"]["status"] == "available"
    assert catalog["sqlserver"]["status"] in {"available", "optional_driver"}
    assert "installation_required" in catalog["snowflake"]


def test_natural_language_sql_is_read_only_and_returns_evidence(client):
    imported = client.post(
        "/api/v1/datasets/import",
        files={"file": ("sales.csv", b"region,revenue\nNorth,10\nSouth,20\nNorth,30\n", "text/csv")},
    )
    assert imported.status_code == 200, imported.text
    dataset_id = imported.json()["dataset_id"]
    response = client.post(f"/api/v1/datasets/{dataset_id}/sql/natural-language", json={"question": "top 2 region by revenue"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intent"] == "top_n_by_measure"
    assert body["validation"]["read_only"] is True
    assert body["execution"]["rows"][0]["total_revenue"] == 40


def test_feature_set_materialization_and_online_lookup(client):
    imported = client.post(
        "/api/v1/datasets/import",
        files={"file": ("customers.csv", b"customer_id,revenue,cost\na,10,4\nb,20,8\n", "text/csv")},
    )
    assert imported.status_code == 200, imported.text
    dataset_id = imported.json()["dataset_id"]
    response = client.post(
        f"/api/v1/datasets/{dataset_id}/orchestration/feature_store",
        json={
            "feature_set_name": "customer-features",
            "entity_columns": ["customer_id"],
            "materialize": True,
            "feature_definitions": [{"name": "margin", "source_columns": ["revenue", "cost"], "transformation": "difference"}],
        },
    )
    assert response.status_code == 200, response.text
    lookup = client.get(f"/api/v1/feature-sets/{response.json()['workspace_asset_id']}/lookup", params={"entity": "customer_id", "key": "a"})
    assert lookup.status_code == 200, lookup.text
    assert lookup.json()["rows"] == [{"customer_id": "a", "margin": 6}]


def test_quality_generates_profile_rules_and_pii_findings(client):
    imported = client.post(
        "/api/v1/datasets/import",
        files={"file": ("quality.csv", b"customer_id,email,age,status\n1,a@example.com,30,Active\n2,b@example.com,40,Inactive\n", "text/csv")},
    )
    assert imported.status_code == 200, imported.text
    result = client.post(f"/api/v1/datasets/{imported.json()['dataset_id']}/quality/analyze", json={})
    assert result.status_code == 200, result.text
    body = result.json()
    generated = {(rule["column"], rule["rule"]) for rule in body["generated_rules"]}
    assert ("email", "email") in generated
    assert ("age", "range") in generated
    assert "email" in body["pii_findings"]["pii_columns"]
