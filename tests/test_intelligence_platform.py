from __future__ import annotations

import numpy as np
import pandas as pd

from app.models.all import Artifact, ModelVersion


def _intelligence_dataset(client):
    rows = 96
    index = np.arange(rows)
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2018-01-31", periods=rows, freq="ME"),
            "customer_age": 20 + (index % 45),
            "monthly_spend": 80 + index * 1.5 + np.sin(index / 2) * 8,
            "support_calls": index % 7,
            "segment": np.where(index % 3 == 0, "Enterprise", np.where(index % 3 == 1, "SMB", "Consumer")),
            "revenue": 1000 + index * 25 + np.sin(index * 2 * np.pi / 12) * 200,
            "churned": np.where((index % 5 == 0) | ((index % 7 == 0) & (index > 20)), "yes", "no"),
        }
    )
    response = client.post(
        "/api/v1/datasets/import",
        files={"file": ("intelligence.csv", frame.to_csv(index=False).encode(), "text/csv")},
    )
    assert response.status_code == 200, response.text
    return response.json(), frame


def test_capability_catalog_hides_internal_tool_ids(client):
    response = client.get("/api/v1/intelligence/capabilities")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["integrated_reference_capabilities"] == 200
    assert body["production_ui_uses_logical_capability_names"] is True
    assert body["supervised_ml"]["model_count"] >= 70
    assert body["unsupervised_ml"]["algorithm_count"] >= 29
    assert "tool_number" not in response.text


def test_forecasting_flow_preserves_exact_source_version(client):
    imported, _ = _intelligence_dataset(client)
    dataset_id = imported["dataset_id"]
    source_version_id = imported["version_id"]
    before = client.get(f"/api/v1/datasets/{dataset_id}/versions").json()

    readiness = client.post(
        f"/api/v1/datasets/{dataset_id}/forecasting/readiness",
        json={"source_version_id": source_version_id, "date_column": "date", "value_column": "revenue", "frequency": "ME"},
    )
    assert readiness.status_code == 200, readiness.text
    assert readiness.json()["source_version_id"] == source_version_id
    assert readiness.json()["readiness_score"] >= 75

    forecast = client.post(
        f"/api/v1/datasets/{dataset_id}/forecasting/forecast",
        json={"source_version_id": source_version_id, "date_column": "date", "value_column": "revenue", "frequency": "ME", "model": "holt", "horizon": 6},
    )
    assert forecast.status_code == 200, forecast.text
    body = forecast.json()
    assert body["model"] == "holt"
    assert len(body["forecast"]) == 6
    assert body["source_version_id"] == source_version_id
    assert body["execution"]["rows_scanned"] == 96

    after = client.get(f"/api/v1/datasets/{dataset_id}/versions").json()
    assert after == before


def test_supervised_model_registry_diagnostics_and_governance(client):
    imported, _ = _intelligence_dataset(client)
    dataset_id = imported["dataset_id"]
    source_version_id = imported["version_id"]
    payload = {
        "source_version_id": source_version_id,
        "target_column": "churned",
        "feature_columns": ["customer_age", "monthly_spend", "support_calls", "segment"],
        "task_type": "classification",
        "algorithm": "logistic_regression",
        "model_name": "Customer churn",
    }
    response = client.post(f"/api/v1/datasets/{dataset_id}/ml/train", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    registered = body["registered_model"]
    assert registered["training_source_version_id"] == source_version_id
    assert len(registered["artifact_sha256"]) == 64
    model_version_id = registered["model_version_id"]

    diagnostics = client.post(
        f"/api/v1/models/{model_version_id}/diagnostics/cross_validation",
        json={"folds": 3},
    )
    assert diagnostics.status_code == 200, diagnostics.text
    assert diagnostics.json()["compatibility"]["compatible"] is True
    assert len(diagnostics.json()["validation_scores"]) == 3

    explanation = client.post(f"/api/v1/models/{model_version_id}/explain", json={"repeats": 2})
    assert explanation.status_code == 200, explanation.text
    assert explanation.json()["drivers"]

    rejected_gate = client.post(f"/api/v1/models/{model_version_id}/status", json={"status": "VALIDATED"})
    assert rejected_gate.status_code == 422
    assert rejected_gate.json()["error"]["code"] == "APPROVAL_EVIDENCE_REQUIRED"
    validated = client.post(f"/api/v1/models/{model_version_id}/status", json={"status": "VALIDATED", "evidence": {"cross_validation": diagnostics.json()["mean_validation_score"]}})
    assert validated.status_code == 200, validated.text
    assert validated.json()["status"] == "VALIDATED"

    models = client.get("/api/v1/models").json()["models"]
    assert models[0]["versions"][0]["training_source_version_id"] == source_version_id


def test_unsupervised_data_engineering_and_orchestration_flows(client):
    imported, _ = _intelligence_dataset(client)
    dataset_id = imported["dataset_id"]
    source_version_id = imported["version_id"]

    clustering = client.post(
        f"/api/v1/datasets/{dataset_id}/ml/unsupervised/run",
        json={"source_version_id": source_version_id, "algorithm": "kmeans", "feature_columns": ["customer_age", "monthly_spend", "support_calls"], "n_clusters": 3},
    )
    assert clustering.status_code == 200, clustering.text
    assert clustering.json()["cluster_count"] == 3
    assert clustering.json()["artifact"]["sha256"]

    readiness = client.post(
        f"/api/v1/datasets/{dataset_id}/data-engineering/source_readiness",
        json={"source_version_id": source_version_id, "primary_key_columns": ["date"], "owner": "analytics", "sla_minutes": 1440},
    )
    assert readiness.status_code == 200, readiness.text
    assert readiness.json()["primary_key"]["unique"] is True

    cdc = client.post(
        f"/api/v1/datasets/{dataset_id}/data-engineering/incremental_cdc",
        json={"source_version_id": source_version_id, "primary_key_columns": ["date"], "supports_log_based_cdc": True, "source_type": "database"},
    )
    assert cdc.status_code == 200, cdc.text
    assert cdc.json()["recommended_strategy"] == "log_based_cdc"
    assert cdc.json()["configured_external_source"] is False

    feature_store = client.post(
        f"/api/v1/datasets/{dataset_id}/orchestration/feature_store",
        json={"source_version_id": source_version_id, "feature_set_name": "Customer behavior", "owner": "analytics", "feature_definitions": [{"name": "spend", "source_columns": ["monthly_spend"], "transformation": "identity"}]},
    )
    assert feature_store.status_code == 200, feature_store.text
    assert feature_store.json()["lineage_complete"] is True
    assert feature_store.json()["workspace_asset_id"]

    autonomous = client.post(
        f"/api/v1/datasets/{dataset_id}/orchestrate",
        json={"source_version_id": source_version_id, "objective": "Build a trusted predictive customer data product", "target_column": "churned", "feature_columns": ["customer_age", "monthly_spend", "support_calls", "segment"]},
    )
    assert autonomous.status_code == 200, autonomous.text
    body = autonomous.json()
    assert body["unsafe_actions_executed"] is False
    assert "production_release_approval" in body["human_approval_gates"]
    assert body["source_version_id"] == source_version_id


def test_common_background_job_persists_result(client):
    imported, _ = _intelligence_dataset(client)
    response = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/intelligence/jobs",
        json={"domain": "data_engineering", "operation": "source_readiness", "source_version_id": imported["version_id"], "primary_key_columns": ["date"]},
    )
    assert response.status_code == 202, response.text
    status = client.get(f"/api/v1/intelligence/jobs/{response.json()['job_id']}")
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "COMPLETED"
    assert status.json()["progress"] == 1.0
    assert status.json()["result"]["readiness_score"] > 0


def test_model_cannot_run_on_incompatible_dataset(client):
    imported, _ = _intelligence_dataset(client)
    trained = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/ml/train",
        json={"target_column": "churned", "feature_columns": ["customer_age", "monthly_spend"], "task_type": "classification", "algorithm": "logistic_regression", "model_name": "Compatibility model"},
    )
    assert trained.status_code == 200, trained.text
    model_version_id = trained.json()["registered_model"]["model_version_id"]
    other = client.post(
        "/api/v1/datasets/import",
        files={"file": ("other.csv", b"different,target\n1,a\n2,b\n3,a\n", "text/csv")},
    ).json()
    response = client.post(
        f"/api/v1/models/{model_version_id}/explain",
        json={"dataset_id": other["dataset_id"], "source_version_id": other["version_id"], "target_column": "target"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MODEL_INPUT_INCOMPATIBLE"


def test_tampered_model_artifact_is_never_deserialized(client):
    imported, _ = _intelligence_dataset(client)
    trained = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/ml/train",
        json={
            "target_column": "churned",
            "feature_columns": ["customer_age", "monthly_spend"],
            "task_type": "classification",
            "algorithm": "logistic_regression",
            "model_name": "Tamper-evident model",
        },
    )
    assert trained.status_code == 200, trained.text
    model_version_id = trained.json()["registered_model"]["model_version_id"]
    db = client.app.state.SessionLocal()
    try:
        version = db.query(ModelVersion).filter(ModelVersion.id == model_version_id).one()
        artifact = db.query(Artifact).filter(Artifact.id == version.artifact_id).one()
        artifact_path = client.app.state.storage.resolve(artifact.storage_path)
    finally:
        db.close()
    with artifact_path.open("ab") as stream:
        stream.write(b"tampered")

    response = client.post(f"/api/v1/models/{model_version_id}/explain", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MODEL_ARTIFACT_CHECKSUM_FAILED"
