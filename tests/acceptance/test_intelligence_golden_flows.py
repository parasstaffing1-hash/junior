from __future__ import annotations

import numpy as np
import pandas as pd


def _import_golden_dataset(client, name: str = "golden.csv"):
    rows = 84
    index = np.arange(rows)
    frame = pd.DataFrame(
        {
            "record_id": [f"R{value:04d}" for value in index],
            "date": pd.date_range("2019-01-31", periods=rows, freq="ME"),
            "region": np.where(index % 3 == 0, "North", np.where(index % 3 == 1, "South", "West")),
            "product": np.where(index % 2 == 0, "Core", "Plus"),
            "customer_age": 21 + index % 48,
            "support_calls": index % 8,
            "sales": 500 + index * 13 + np.sin(index * 2 * np.pi / 12) * 90,
            "profit": 90 + index * 3 + np.cos(index * 2 * np.pi / 12) * 25,
            "churned": np.where((index % 5 == 0) | ((index % 7 == 0) & (index > 20)), "yes", "no"),
        }
    )
    if name == "golden-analyst.csv":
        frame.loc[1] = frame.loc[0]
        frame.loc[2, "region"] = " North "
    response = client.post(
        "/api/v1/datasets/import",
        files={"file": (name, frame.to_csv(index=False).encode(), "text/csv")},
    )
    assert response.status_code == 200, response.text
    return response.json(), frame


def test_golden_flow_a_full_analyst(client):
    imported, _ = _import_golden_dataset(client, "golden-analyst.csv")
    versions_before = client.get(f"/api/v1/datasets/{imported['dataset_id']}/versions").json()
    v1_sha256 = versions_before[0]["sha256"]
    quality = client.post(f"/api/v1/datasets/{imported['dataset_id']}/quality/analyze", json={})
    response = client.post(
        "/api/v1/automated-analyst/analyze",
        json={"dataset_id": imported["dataset_id"], "source_version_id": imported["version_id"]},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "COMPLETED_AUTOMATED_ANALYST"
    assert result["source_version_id"] == imported["version_id"]
    # The interactive run returns its dashboard manifest first; heavyweight
    # documents are deliberately created by their explicit download endpoints.
    assert result["report"]["files"]["html"]
    assert result["findings"]["findings"]
    assert quality.status_code == 200
    assert result["cleaned"] is True
    assert result["final_version_id"] != imported["version_id"]
    eda = client.post(f"/api/v1/datasets/{imported['dataset_id']}/eda/report", json={})
    findings = client.post(f"/api/v1/datasets/{imported['dataset_id']}/findings", json={})
    kpi = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/kpis/calculate",
        json={"definition": {"definition_type": "aggregate", "components": {"value": {"aggregation": "sum", "column": "sales"}}}},
    )
    dashboard = client.get(f"/api/v1/datasets/{imported['dataset_id']}/bi_report")
    pdf = client.get(f"/api/v1/datasets/{imported['dataset_id']}/bi_report/pdf")
    excel = client.get(f"/api/v1/datasets/{imported['dataset_id']}/bi_report/xlsx")
    assert all(item.status_code == 200 for item in (eda, findings, kpi, dashboard, pdf, excel))
    assert dashboard.json()["dashboard"]["layout_validation"]["valid"] is True
    assert pdf.content.startswith(b"%PDF") and excel.content.startswith(b"PK")
    versions_after = client.get(f"/api/v1/datasets/{imported['dataset_id']}/versions").json()
    assert len(versions_after) == 2
    assert versions_after[0]["id"] == imported["version_id"]
    assert versions_after[0]["sha256"] == v1_sha256


def test_golden_flow_b_forecasting(client):
    imported, _ = _import_golden_dataset(client, "golden-forecast.csv")
    base = {
        "source_version_id": imported["version_id"],
        "date_column": "date",
        "value_column": "sales",
        "frequency": "ME",
        "horizon": 6,
    }
    readiness = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/forecasting/readiness", json=base
    )
    comparison = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/forecasting/compare",
        json={**base, "candidate_models": ["naive", "holt"]},
    )
    decomposition = client.post(f"/api/v1/datasets/{imported['dataset_id']}/forecasting/decomposition", json=base)
    backtest = client.post(f"/api/v1/datasets/{imported['dataset_id']}/forecasting/backtest", json={**base, "model": "holt", "max_folds": 3})
    forecast = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/forecasting/forecast",
        json={**base, "model": "holt"},
    )
    summary = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/forecasting/portfolio_summary",
        json={**base, "group_columns": ["region"], "model": "holt"},
    )
    assert readiness.status_code == comparison.status_code == decomposition.status_code == backtest.status_code == forecast.status_code == summary.status_code == 200
    assert readiness.json()["status"] != "NOT_READY"
    assert decomposition.json()["components"]
    assert {"observed", "trend", "seasonal", "residual"} <= set(decomposition.json()["components"][0])
    assert backtest.json()["folds"]
    assert comparison.json()["ranking"]
    result = forecast.json()
    assert len(result["forecast"]) == 6
    assert all({"lower", "upper"} <= set(point) for point in result["forecast"])
    assert result["source_version_id"] == imported["version_id"]
    assert summary.json()["portfolio"]["ready_series"] == 3


def test_golden_flow_c_supervised_data_science(client):
    imported, _ = _import_golden_dataset(client, "golden-supervised.csv")
    payload = {
        "source_version_id": imported["version_id"],
        "target_column": "churned",
        "feature_columns": ["customer_age", "support_calls", "sales", "region", "product"],
        "task_type": "classification",
        "model_name": "Golden churn model",
        "max_trials": 3,
    }
    readiness = client.post(f"/api/v1/datasets/{imported['dataset_id']}/ml/readiness", json=payload)
    selected = client.post(f"/api/v1/datasets/{imported['dataset_id']}/ml/feature-selection", json={**payload, "top_k": 4})
    compared = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/ml/compare",
        json={**payload, "algorithms": ["logistic_regression", "random_forest_classifier"]},
    )
    tuned = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/ml/tune",
        json={**payload, "algorithm": "logistic_regression"},
    )
    assert readiness.status_code == selected.status_code == compared.status_code == tuned.status_code == 200
    assert readiness.json()["status"] != "NOT_READY"
    assert selected.json()["ranking"]
    assert compared.json()["leaderboard"]
    model_version_id = tuned.json()["registered_best_model"]["model_version_id"]
    explained = client.post(f"/api/v1/models/{model_version_id}/explain", json={"repeats": 2})
    error_slices = client.post(
        f"/api/v1/models/{model_version_id}/diagnostics/error_slices",
        json={"segment_column": "region", "minimum_segment_rows": 5},
    )
    threshold = client.post(f"/api/v1/models/{model_version_id}/thresholds", json={})
    calibration = client.post(f"/api/v1/models/{model_version_id}/calibration", json={})
    conformal = client.post(f"/api/v1/models/{model_version_id}/monitor/conformal_classification", json={})
    readiness_scorecard = client.post(f"/api/v1/models/{model_version_id}/monitor/production_readiness", json={})
    card = client.post(
        "/api/v1/mlops/model_card",
        json={
            "model_name": "Golden churn model",
            "algorithm": "logistic_regression",
            "task_type": "classification",
            "metrics": tuned.json()["trials"][0]["metrics"],
            "training_lineage": {"dataset_id": imported["dataset_id"], "source_version_id": imported["version_id"]},
        },
    )
    assert all(response.status_code == 200 for response in (explained, error_slices, threshold, calibration, conformal, readiness_scorecard, card))
    assert explained.json()["drivers"]
    assert error_slices.json()["slices"]
    assert threshold.json()["best"]
    assert 0 <= calibration.json()["expected_calibration_error"] <= 1
    assert conformal.json()["prediction_sets_preview"]
    assert readiness_scorecard.json()["automatic_deployment"] is False
    assert card.json()["model_card"]["training_lineage"]["source_version_id"] == imported["version_id"]


def test_golden_flow_d_unsupervised(client):
    imported, _ = _import_golden_dataset(client, "golden-unsupervised.csv")
    base = {
        "source_version_id": imported["version_id"],
        "feature_columns": ["customer_age", "support_calls", "sales", "profit"],
        "n_clusters": 3,
    }
    compared = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/ml/unsupervised/compare",
        json={**base, "algorithms": ["kmeans", "hierarchical_clustering"]},
    )
    clustered = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/ml/unsupervised/run",
        json={**base, "algorithm": "kmeans"},
    )
    embedded = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/ml/unsupervised/run",
        json={**base, "algorithm": "pca", "n_components": 2},
    )
    anomaly = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/ml/unsupervised/run",
        json={**base, "algorithm": "isolation_forest", "contamination": 0.05},
    )
    stability = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/ml/unsupervised/stability",
        json={**base, "algorithm": "kmeans", "repeats": 3},
    )
    assert all(response.status_code == 200 for response in (compared, clustered, embedded, anomaly, stability))
    assert compared.json()["champion_algorithm"] in {"kmeans", "hierarchical_clustering"}
    assert clustered.json()["cluster_count"] == 3
    assert embedded.json()["component_count"] == 2
    assert len(embedded.json()["embedding_preview"][0]) == 2
    assert anomaly.json()["anomaly_count"] > 0
    assert stability.json()["repeats"] == 3
    findings = client.post(f"/api/v1/datasets/{imported['dataset_id']}/findings", json={})
    assert findings.status_code == 200 and findings.json()["findings"]


def test_golden_flow_e_mlops_lifecycle_and_monitoring(client):
    imported, frame = _import_golden_dataset(client, "golden-mlops.csv")
    training = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/ml/train",
        json={
            "source_version_id": imported["version_id"],
            "target_column": "churned",
            "feature_columns": ["customer_age", "support_calls", "sales", "region"],
            "task_type": "classification",
            "algorithm": "logistic_regression",
            "model_name": "Governed churn model",
        },
    )
    assert training.status_code == 200, training.text
    trained = training.json()
    model_version_id = trained["registered_model"]["model_version_id"]
    challenger_training = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/ml/train",
        json={
            "source_version_id": imported["version_id"],
            "target_column": "churned",
            "feature_columns": ["customer_age", "support_calls", "sales", "region"],
            "task_type": "classification",
            "algorithm": "random_forest_classifier",
            "model_name": "Governed churn model",
        },
    )
    assert challenger_training.status_code == 200, challenger_training.text
    challenger_version_id = challenger_training.json()["registered_model"]["model_version_id"]
    experiment = client.post(
        "/api/v1/experiments",
        json={"name": "Golden lifecycle experiment", "dataset_id": imported["dataset_id"], "objective": "Predict churn"},
    )
    assert experiment.status_code == 201, experiment.text
    run = client.post(
        f"/api/v1/experiments/{experiment.json()['id']}/runs",
        json={"model_version_id": model_version_id, "metrics": trained["metrics"], "reproducibility_manifest": {"source_version_id": imported["version_id"]}},
    )
    card = client.post(
        "/api/v1/mlops/model_card",
        json={
            "model_name": "Governed churn model",
            "algorithm": "logistic_regression",
            "task_type": "classification",
            "metrics": trained["metrics"],
            "training_lineage": {"dataset_id": imported["dataset_id"], "source_version_id": imported["version_id"]},
        },
    )
    shifted = frame.copy()
    shifted["sales"] = shifted["sales"] * 2.5 + 1000
    shifted["customer_age"] = shifted["customer_age"] + 15
    current = client.post(
        "/api/v1/datasets/import",
        files={"file": ("golden-mlops-shifted.csv", shifted.to_csv(index=False).encode(), "text/csv")},
    )
    assert current.status_code == 200, current.text
    current_lineage = {"dataset_id": current.json()["dataset_id"], "source_version_id": current.json()["version_id"]}
    policy = client.post("/api/v1/mlops/monitoring_policy", json={"task_type": "classification", "schedule": "daily"})
    champion_challenger = client.post(
        f"/api/v1/models/{model_version_id}/monitor/champion_challenger",
        json={**current_lineage, "challenger_model_version_id": challenger_version_id},
    )
    monitor = client.post(f"/api/v1/models/{model_version_id}/monitor/feature_drift", json=current_lineage)
    prediction_drift = client.post(f"/api/v1/models/{model_version_id}/monitor/prediction_drift", json=current_lineage)
    performance_drift = client.post(f"/api/v1/models/{model_version_id}/monitor/performance_drift", json=current_lineage)
    retraining = client.post(
        "/api/v1/mlops/retraining_decision",
        json={"signals": {"performance_degradation": 0.2, "feature_drift": 0.3, "concept_drift": 0.25}},
    )
    incident = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/orchestration/incident_root_cause",
        json={"source_version_id": imported["version_id"], "objective": "Investigate model alert", "incident_signals": {"feature_drift": 0.3, "performance_degradation": 0.2}},
    )
    assert all(response.status_code in {200, 201} for response in (run, card, policy, champion_challenger, monitor, prediction_drift, performance_drift, retraining, incident))
    assert card.json()["model_card"]["training_lineage"]["source_version_id"] == imported["version_id"]
    assert monitor.json()["monitoring_run_id"]
    assert monitor.json()["drifted_feature_count"] > 0
    assert champion_challenger.json()["automatic_promotion"] is False
    assert policy.json()["external_schedule_created"] is False
    assert retraining.json()["decision"] == "RETRAIN_RECOMMENDED"
    assert retraining.json()["automatic_retraining_started"] is False
    assert incident.json()["automatic_execution"] is False


def test_golden_flow_f_data_engineering(client):
    imported, _ = _import_golden_dataset(client, "golden-engineering.csv")
    endpoint = f"/api/v1/datasets/{imported['dataset_id']}/data-engineering"
    lineage = {"source_version_id": imported["version_id"], "primary_key_columns": ["record_id"], "owner": "data-platform", "sla_minutes": 1440}
    readiness = client.post(f"{endpoint}/source_readiness", json=lineage)
    contract = client.post(f"{endpoint}/contract_schema_evolution", json=lineage)
    evolved = client.post(
        f"{endpoint}/contract_schema_evolution",
        json={**lineage, "previous_schema": {"columns": contract.json()["contract"]["columns"] + [{"name": "retired_column", "physical_type": "object", "nullable": True}]}},
    )
    cdc = client.post(f"{endpoint}/incremental_cdc", json={**lineage, "supports_log_based_cdc": True, "source_type": "database"})
    dag = client.post(
        f"{endpoint}/dependency_dag",
        json={**lineage, "dependencies": [{"upstream": "source", "downstream": "curated"}, {"upstream": "curated", "downstream": "semantic_model"}]},
    )
    partition = client.post(f"{endpoint}/partition_storage", json={**lineage, "timestamp_column": "date", "estimated_total_rows": 500_000_000})
    freshness = client.post(f"{endpoint}/freshness_sla", json={**lineage, "timestamp_column": "date", "observed_at": "2026-01-01T00:00:00Z"})
    failure = client.post(
        f"{endpoint}/observability_failure_diagnosis",
        json={**lineage, "run_metrics": {"status": "FAILED", "error_message": "schema column missing", "expected_rows": 84, "rows_written": 10}},
    )
    backfill = client.post(f"{endpoint}/backfill_replay", json={**lineage, "start_date": "2025-01-01", "end_date": "2025-01-07"})
    assert all(response.status_code == 200 for response in (readiness, contract, evolved, cdc, dag, partition, freshness, failure, backfill))
    assert readiness.json()["primary_key"]["unique"] is True
    assert contract.json()["contract"]["columns"]
    assert evolved.json()["evolution"]["classification"] == "breaking"
    assert cdc.json()["recommended_strategy"] == "log_based_cdc"
    assert dag.json()["execution_order"] == ["source", "curated", "semantic_model"]
    assert partition.json()["partition_granularity"] == "week"
    assert failure.json()["most_likely_cause"]["cause"] == "schema_contract_break"
    assert backfill.json()["automatic_execution"] is False


def test_golden_flow_g_autonomous_orchestration(client):
    imported, _ = _import_golden_dataset(client, "golden-autonomous.csv")
    response = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/orchestrate",
        json={
            "source_version_id": imported["version_id"],
            "objective": "Build governed revenue, forecast, churn, and monitoring intelligence",
            "target_column": "churned",
            "feature_columns": ["customer_age", "support_calls", "sales", "region"],
            "date_column": "date",
            "value_column": "sales",
            "primary_key_columns": ["record_id"],
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["system"] == "Autonomous Data Intelligence Orchestrator"
    assert len(result["execution_plan"]) == 7
    assert result["source_version_id"] == imported["version_id"]
    assert result["unsafe_actions_executed"] is False
    assert {"business_objective_approval", "high_impact_model_approval", "production_release_approval"} <= set(result["human_approval_gates"])
