from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.core.data_engineering import DataEngineeringService
from app.core.eda.findings import detect_findings
from app.core.forecasting import ForecastingService
from app.core.intelligence.common import ExecutionBudget, IntelligenceError, dataframe_fingerprint, finish_metadata, json_safe, started_timer
from app.core.ml import MachineLearningService, UnsupervisedLearningService
from app.orchestration.quality_pipeline import QualityPipeline


ORCHESTRATION_OPERATIONS = {
    "data_science_plan",
    "data_engineering_plan",
    "feature_store",
    "data_product",
    "incident_root_cause",
    "unified_pipeline",
    "autonomous",
}


class IntelligenceOrchestrator:
    """Composes shared engines and enforces human gates for impactful actions."""

    def __init__(self, budget: ExecutionBudget | None = None):
        self.budget = budget or ExecutionBudget()
        self.ml = MachineLearningService(self.budget)
        self.unsupervised = UnsupervisedLearningService(self.budget)
        self.forecasting = ForecastingService(self.budget)
        self.data_engineering = DataEngineeringService(self.budget)
        self.quality = QualityPipeline()

    @staticmethod
    def catalog() -> dict[str, Any]:
        return {"operation_count": len(ORCHESTRATION_OPERATIONS), "operations": sorted(ORCHESTRATION_OPERATIONS), "approval_gates": ["business_objective_approval", "high_impact_model_approval", "production_release_approval", "destructive_replay_approval"]}

    @staticmethod
    def _frame_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
        safe = frame.astype(object).where(pd.notna(frame), None)
        return json_safe(safe.to_dict(orient="records"))

    def _quality(self, frame: pd.DataFrame, dataset_id: str, source_version_id: str) -> dict[str, Any]:
        sampled = frame.head(min(len(frame), self.budget.max_rows))
        return self.quality.analyze(rows=self._frame_rows(sampled), dataset_id=dataset_id, source_version_id=source_version_id)

    @staticmethod
    def _feature_store(frame: pd.DataFrame, params: dict[str, Any], dataset_id: str, source_version_id: str) -> dict[str, Any]:
        definitions = params.get("feature_definitions") or [
            {"name": str(column), "source_columns": [str(column)], "transformation": "identity", "owner": params.get("owner")}
            for column in frame.columns[: min(20, len(frame.columns))]
        ]
        seen = set()
        features = []
        for index, definition in enumerate(definitions):
            name = str(definition.get("name") or f"feature_{index + 1}")
            if name in seen:
                raise IntelligenceError("DUPLICATE_FEATURE_NAME", "Feature names must be unique.", {"feature": name})
            seen.add(name)
            sources = [str(column) for column in definition.get("source_columns", [])]
            missing = [column for column in sources if column not in frame.columns]
            transformation = str(definition.get("transformation", "identity"))
            features.append(
                {
                    "name": name,
                    "version": int(definition.get("version", 1)),
                    "source_columns": sources,
                    "missing_source_columns": missing,
                    "transformation": transformation,
                    "owner": definition.get("owner") or params.get("owner"),
                    "online_ready": bool(definition.get("online_ready", False)),
                    "lineage": {"dataset_id": dataset_id, "source_version_id": source_version_id, "source_columns": sources, "transformation": transformation},
                    "lineage_complete": bool(sources) and not missing,
                }
            )
        incomplete = [item["name"] for item in features if not item["lineage_complete"]]
        return {
            "feature_set_name": str(params.get("feature_set_name", "default_feature_set")),
            "feature_count": len(features),
            "features": features,
            "lineage_complete": not incomplete,
            "incomplete_features": incomplete,
            "training_serving_consistency_policy": "Use the same immutable feature-definition version for training and serving.",
            "online_feature_server_created": False,
        }

    def run(
        self,
        frame: pd.DataFrame,
        operation: str,
        *,
        dataset_id: str,
        source_version_id: str,
        **params: Any,
    ) -> dict[str, Any]:
        operation = str(operation).strip().casefold()
        if operation not in ORCHESTRATION_OPERATIONS:
            raise IntelligenceError("UNKNOWN_ORCHESTRATION_OPERATION", "Unsupported orchestration operation.", {"operation": operation, "allowed": sorted(ORCHESTRATION_OPERATIONS)})
        if not dataset_id or not source_version_id:
            raise IntelligenceError("LINEAGE_REQUIRED", "dataset_id and source_version_id are required.")
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise IntelligenceError("EMPTY_DATASET", "Dataset must contain at least one row.")
        started = started_timer()
        sampled = len(frame) > self.budget.max_rows
        work = frame.sample(n=self.budget.max_rows, random_state=self.budget.random_state).sort_index() if sampled else frame.copy()
        metadata = {"rows_scanned": len(frame), "rows_used": len(work), "feature_count": len(work.columns), "sampled": sampled, "sample_size": len(work), "cache_hit": False}
        objective = str(params.get("objective", "Build trusted, decision-ready data intelligence."))

        if operation == "data_science_plan":
            target = params.get("target_column")
            blockers = []
            readiness = None
            task_type = "unsupervised"
            if target:
                readiness = self.ml.readiness(work, **params)
                task_type = readiness["task_type"]
                if readiness["status"] == "NOT_READY":
                    blockers.append("ml_readiness_not_met")
            steps = [
                {"order": 1, "stage": "problem_framing", "action": "Validate objective, prediction unit, leakage boundary, and business success metric.", "status": "REQUIRES_INPUT" if not target else "READY"},
                {"order": 2, "stage": "data_readiness", "action": "Profile schema, target quality, missingness, duplicates, imbalance, and leakage.", "status": "READY"},
                {"order": 3, "stage": "feature_preparation", "action": "Use the shared reproducible preprocessing and feature-selection pipeline.", "status": "READY"},
                {"order": 4, "stage": "baseline_and_candidates", "action": "Train a naive baseline and compare bounded candidate families.", "status": "READY" if target else "CONDITIONAL"},
                {"order": 5, "stage": "validation", "action": "Run cross-validation, stability, residual/calibration, error-slice, and uncertainty checks.", "status": "READY" if target else "CONDITIONAL"},
                {"order": 6, "stage": "explainability", "action": "Create driver analysis with causal limitations stated.", "status": "READY" if target else "CONDITIONAL"},
                {"order": 7, "stage": "governance", "action": "Create model card, reproducibility manifest, registry version, and approval gate.", "status": "READY"},
                {"order": 8, "stage": "monitoring", "action": "Define drift, performance, schema, and retraining policies.", "status": "READY"},
            ]
            result = {"objective": objective, "task_type": task_type, "workflow_steps": steps, "blockers": blockers, "readiness": readiness, "recommended_metrics": ["balanced_accuracy", "f1_macro", "log_loss", "calibration"] if task_type == "classification" else ["rmse", "mae", "r_squared"] if task_type == "regression" else ["silhouette", "stability", "business_interpretability"], "ready_to_execute": not blockers and bool(target or params.get("allow_unsupervised", True))}
        elif operation == "data_engineering_plan":
            result = self.data_engineering.run(work, "orchestration", **params)
        elif operation == "feature_store":
            result = self._feature_store(work, params, dataset_id, source_version_id)
        elif operation == "data_product":
            contract = self.data_engineering.run(work, "contract_schema_evolution", **params)
            readiness = self.data_engineering.run(work, "source_readiness", **params)
            consumers = [str(item) for item in params.get("consumers", [])]
            checks = {
                "owner_declared": bool(params.get("owner")),
                "primary_key_valid": bool(readiness["primary_key"]["unique"]),
                "sla_declared": int(params.get("sla_minutes", 60)) > 0,
                "contract_declared": bool(contract["contract"]["columns"]),
                "consumers_declared": bool(consumers),
                "lineage_declared": True,
            }
            score = float(sum(checks.values()) / len(checks) * 100)
            result = {"product_name": str(params.get("product_name", objective)), "domain": str(params.get("domain", "general")), "owner": params.get("owner"), "consumers": consumers, "contract": contract["contract"], "quality_policy": params.get("quality_policy") or {"minimum_readiness_score": 80, "breaking_schema_changes": "reject"}, "governance_checks": checks, "data_product_score": score, "status": "GOVERNED" if score >= 80 else "NEEDS_GOVERNANCE", "lineage": {"dataset_id": dataset_id, "source_version_id": source_version_id}}
        elif operation == "incident_root_cause":
            signals = params.get("incident_signals") or {}
            candidates = []
            def add(cause: str, confidence: float, evidence: str) -> None:
                candidates.append({"cause": cause, "confidence": confidence, "evidence": evidence})
            if signals.get("credential_error"):
                add("credential_or_access_failure", 0.98, "A credential or access error was reported.")
            if signals.get("schema_change"):
                add("schema_contract_break", 0.95, "A schema change was reported near incident start.")
            if float(signals.get("freshness_age_minutes", 0)) > float(params.get("sla_minutes", 60)):
                add("upstream_freshness_failure", 0.9, "Freshness exceeded the declared SLA.")
            if float(signals.get("missingness_delta", 0)) >= 0.1:
                add("missingness_shift", 0.85, "Missingness changed materially.")
            if float(signals.get("feature_drift", 0)) >= 0.2:
                add("feature_distribution_drift", 0.82, "Feature drift is elevated.")
            if float(signals.get("performance_degradation", 0)) >= 0.1:
                add("model_performance_degradation", 0.82, "Model performance degraded materially.")
            if signals.get("pipeline_error"):
                diagnostic = self.data_engineering.run(work, "observability_failure_diagnosis", run_metrics={"status": "FAILED", "error_message": str(signals["pipeline_error"]), **signals.get("run_metrics", {})})
                for diagnosis in diagnostic["diagnoses"]:
                    add(diagnosis["cause"], diagnosis["confidence"], str(signals["pipeline_error"]))
            if not candidates:
                add("insufficient_evidence", 0.3, "No strong incident signal was provided.")
            candidates.sort(key=lambda item: (-item["confidence"], item["cause"]))
            dependencies = [str(item) for item in params.get("dependencies", [])]
            result = {"incident_summary": objective, "most_likely_cause": candidates[0], "ranked_alternative_causes": candidates[1:], "evidence": [item["evidence"] for item in candidates], "operational_evidence": signals.get("evidence", []), "blast_radius": dependencies, "affected_assets": params.get("affected_assets", []), "safe_remediation_recommendations": ["Pause unsafe downstream promotion while impact is assessed.", "Validate source contract, freshness, lineage, and the latest pipeline run.", "Replay only after identifying affected partitions and confirming idempotency.", "Re-evaluate model performance if data or feature computation changed."], "replay_recommended": bool(signals.get("pipeline_error")), "retrain_recommended": bool(float(signals.get("performance_degradation", 0)) >= 0.1), "automatic_execution": False}
        else:
            quality = self._quality(work, dataset_id, source_version_id)
            engineering = self.data_engineering.run(work, "source_readiness", **params)
            findings = detect_findings(work)
            target = params.get("target_column")
            ml_readiness = self.ml.readiness(work, **params) if target else None
            forecast_readiness = None
            if params.get("date_column") and params.get("value_column"):
                forecast_readiness = self.forecasting.readiness(work, **params)
            stages = [
                {"phase": 1, "name": "understand", "service": "quality_and_schema", "status": "COMPLETED", "evidence": {"health": quality.get("health"), "findings": len(findings.get("findings", []))}},
                {"phase": 2, "name": "engineer_data", "service": "data_engineering", "status": "READY" if engineering["status"] != "NOT_READY" else "BLOCKED", "evidence": {"readiness_score": engineering["readiness_score"]}},
                {"phase": 3, "name": "analyze", "service": "statistics_eda_bi_reporting", "status": "READY"},
                {"phase": 4, "name": "forecast", "service": "forecasting", "status": "READY" if forecast_readiness and forecast_readiness["status"] != "NOT_READY" else "CONDITIONAL"},
                {"phase": 5, "name": "model", "service": "machine_learning", "status": "READY" if ml_readiness and ml_readiness["status"] != "NOT_READY" else "CONDITIONAL"},
                {"phase": 6, "name": "govern", "service": "mlops_and_data_product_governance", "status": "READY", "approval_gate": "high_impact_model_approval"},
                {"phase": 7, "name": "operate", "service": "monitoring_incident_retraining", "status": "READY", "approval_gate": "production_release_approval"},
            ]
            blockers = []
            if engineering["status"] == "NOT_READY":
                blockers.append("data_engineering_readiness")
            if ml_readiness and ml_readiness["status"] == "NOT_READY":
                blockers.append("ml_readiness")
            result = {"system": "Autonomous Data Intelligence Orchestrator" if operation == "autonomous" else "Unified Data and ML Pipeline Orchestrator", "objective": objective, "requested_outcomes": params.get("requested_outcomes") or ["trusted_data", "business_insights", "predictive_model", "operational_monitoring"], "execution_plan": stages, "safe_read_only_results": {"quality": quality, "data_engineering_readiness": engineering, "ml_readiness": ml_readiness, "forecast_readiness": forecast_readiness, "findings": findings}, "blockers": blockers, "execution_status": "READY_TO_PLAN" if not blockers else "BLOCKED", "human_approval_gates": ["business_objective_approval", "high_impact_model_approval", "production_release_approval"], "autonomy_boundary": "May inspect, plan, analyze, validate, and recommend. Deployment, destructive replay, and other high-impact production changes require explicit authorized execution.", "unsafe_actions_executed": False}
        return {"operation": operation, "dataset_id": dataset_id, "source_version_id": source_version_id, "dataset_fingerprint": dataframe_fingerprint(work), **json_safe(result), "execution": finish_metadata(started, metadata)}
