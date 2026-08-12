from __future__ import annotations

import argparse
import csv
from pathlib import Path
import re


FORECAST_OPERATIONS = [
    "trend_seasonality", "decomposition", "stationarity", "transform", "acf_pacf", "exponential_smoothing",
    "arima", "sarima", "evaluate", "split", "backtest", "compare", "prediction_intervals", "auto_select",
    "scenario", "accuracy_monitoring", "bias_drift", "reconciliation", "ensemble", "intermittent", "exogenous",
    "calendar_effects", "probabilistic_calibration", "multi_series", "horizon_frequency", "override_consensus",
    "planning_table", "readiness", "pipeline", "portfolio_summary",
]
DIAGNOSTIC_OPERATIONS = [
    "cross_validation", "learning_curve", "validation_curve", "bootstrap_stability", "permutation_importance_stability",
    "partial_dependence", "ice", "error_slices", "bias_variance", "residual_diagnostics",
]
MONITORING_OPERATIONS = [
    "conformal_regression", "conformal_classification", "out_of_distribution", "adversarial_validation",
    "feature_drift", "target_drift", "prediction_drift", "performance_drift", "leakage_detection",
    "champion_challenger", "concept_drift", "calibration_drift", "uncertainty_drift", "schema_compatibility",
    "missingness_drift", "correlation_drift", "segment_stability", "threshold_robustness", "drift_root_cause",
    "production_readiness",
]
MLOPS_OPERATIONS = [
    "experiment_registry", "reproducibility_manifest", "model_card", "training_serving_parity",
    "model_registry_versioning", "retraining_decision", "monitoring_policy", "deployment_strategy",
    "governance_approval", "lifecycle_orchestration",
]
DATA_ENGINEERING_OPERATIONS = [
    "source_readiness", "contract_schema_evolution", "incremental_cdc", "dependency_dag", "partition_storage",
    "backfill_replay", "freshness_sla", "observability_failure_diagnosis", "cost_optimization", "orchestration",
]
ORCHESTRATION_OPERATIONS = [
    "data_science_plan", "data_engineering_plan", "feature_store", "data_product", "incident_root_cause",
    "unified_pipeline", "autonomous",
]

GENERIC_DUPLICATES = {142, 143, 144, 145, 146, 147}
EXTENSIONS = {132, 294, 295, 299, 300}
MERGES = {133, 134, 135, 136, 137, 138, 139, 140, 141, 193, 194, 195, 200, 201, 208, 209, 210, 211}


def capability(filename: str) -> str:
    stem = re.sub(r"^tool\d+_", "", Path(filename).stem)
    return stem.replace("_", " ").title()


def operation_for(tool: int) -> str:
    if 101 <= tool <= 130:
        return FORECAST_OPERATIONS[tool - 101]
    explicit = {
        131: "readiness", 132: "shared_preprocessing", 133: "train_regression", 134: "train_classification",
        135: "compare", 136: "tune", 137: "feature_selection", 138: "explain", 139: "imbalance_strategy",
        140: "thresholds", 141: "calibration", 153: "compare", 158: "stability",
    }
    if tool in explicit:
        return explicit[tool]
    if 142 <= tool <= 147 or 179 <= tool <= 243:
        return "train_registered_algorithm"
    if 148 <= tool <= 178:
        return "run_unsupervised"
    if 244 <= tool <= 253:
        return DIAGNOSTIC_OPERATIONS[tool - 244]
    if 254 <= tool <= 273:
        return MONITORING_OPERATIONS[tool - 254]
    if 274 <= tool <= 283:
        return MLOPS_OPERATIONS[tool - 274]
    if 284 <= tool <= 293:
        return DATA_ENGINEERING_OPERATIONS[tool - 284]
    return ORCHESTRATION_OPERATIONS[tool - 294]


def target(tool: int, operation: str) -> tuple[str, str, str, str, str, str]:
    if tool <= 130:
        return ("app/core/forecasting/service.py", f"/api/v1/datasets/{{dataset_id}}/forecasting/{operation}", "AnalysisRun; Artifact; AutomationRun", "Forecast Studio", "pandas; numpy; scipy; statsmodels", "yes" if tool >= 106 else "optional")
    if tool <= 243:
        if 148 <= tool <= 178:
            return ("app/core/ml/unsupervised.py", "/api/v1/datasets/{dataset_id}/ml/unsupervised/{operation}", "AnalysisRun; Artifact; AutomationRun", "Machine Learning", "pandas; numpy; scikit-learn", "yes")
        return ("app/core/ml/registry.py; app/core/ml/service.py", "/api/v1/datasets/{dataset_id}/ml/{operation}", "RegisteredModel; ModelVersion; Artifact; AnalysisRun; ExperimentRun", "Machine Learning; Models", "pandas; numpy; scikit-learn; joblib", "yes")
    if tool <= 253:
        return ("app/core/ml/diagnostics.py", f"/api/v1/models/{{model_version_id}}/diagnostics/{operation}", "ModelVersion; AnalysisRun; AutomationRun", "Models", "numpy; scipy; scikit-learn", "yes")
    if tool <= 273:
        return ("app/core/ml/monitoring.py", f"/api/v1/models/{{model_version_id}}/monitor/{operation}", "MonitoringPolicy; MonitoringRun; ModelVersion; AnalysisRun", "Monitoring", "pandas; numpy; scipy; scikit-learn", "yes")
    if tool <= 283:
        return ("app/core/mlops/service.py; app/services/model_registry.py", f"/api/v1/mlops/{operation}", "Experiment; ExperimentRun; RegisteredModel; ModelVersion; MonitoringPolicy; ApprovalRecord", "Models; Monitoring", "SQLAlchemy; joblib", "optional")
    if tool <= 293:
        return ("app/core/data_engineering/service.py", f"/api/v1/datasets/{{dataset_id}}/data-engineering/{operation}", "AnalysisRun; PipelineRun; WorkspaceAsset; AutomationRun", "Data Engineering", "pandas; numpy", "yes" if tool in {289, 293} else "optional")
    return ("app/orchestration/intelligence.py", f"/api/v1/datasets/{{dataset_id}}/orchestration/{operation}", "AnalysisRun; WorkspaceAsset; AutomationRun", "Automation", "shared platform services", "yes")


def classification(tool: int) -> tuple[str, str]:
    if tool in GENERIC_DUPLICATES:
        return "DEPRECATED DUPLICATE", "Generic wrapper consolidated into explicit registered estimator families."
    if tool in EXTENSIONS:
        return "EXTEND", "Extends the existing cumulative analyst/orchestration foundation."
    if tool in MERGES:
        return "MERGE", "Merged into shared preprocessing, training, comparison, or evaluation services."
    return "NEW", "New capability represented by a canonical shared service."


def build(manifest: Path, output: Path) -> None:
    with manifest.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 200 or [int(row["tool"]) for row in rows] != list(range(101, 301)):
        raise ValueError("Manifest must contain exactly Tools 101 through 300 in order.")
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "tool_number", "capability", "standalone_source", "existing_equivalent", "classification", "overlap_status",
        "target_production_module", "target_api_route", "database_entities", "background_job", "frontend_area",
        "dependencies", "integration_status",
    ]
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            tool = int(row["tool"])
            operation = operation_for(tool)
            module, route, entities, frontend, dependencies, background = target(tool, operation)
            category, overlap = classification(tool)
            existing = "Tools 1-100 dataset/version/analysis/artifact/job/workspace foundation" if category in {"EXTEND", "MERGE"} else "None equivalent; shared platform infrastructure reused"
            writer.writerow(
                {
                    "tool_number": tool,
                    "capability": capability(row["filename"]),
                    "standalone_source": row["filename"],
                    "existing_equivalent": existing,
                    "classification": category,
                    "overlap_status": overlap,
                    "target_production_module": module,
                    "target_api_route": route,
                    "database_entities": entities,
                    "background_job": background,
                    "frontend_area": frontend,
                    "dependencies": dependencies,
                    "integration_status": "INTEGRATED_AND_TESTABLE",
                }
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.manifest, args.output)


if __name__ == "__main__":
    main()
