from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import platform
from typing import Any

import pandas as pd

from app.core.intelligence.common import IntelligenceError, dataframe_fingerprint, json_safe


MLOPS_OPERATIONS = {
    "experiment_registry",
    "reproducibility_manifest",
    "model_card",
    "training_serving_parity",
    "model_registry_versioning",
    "retraining_decision",
    "monitoring_policy",
    "deployment_strategy",
    "governance_approval",
    "lifecycle_orchestration",
}


class MLOpsService:
    """Deterministic MLOps metadata, decisions, and approval-gate logic."""

    PACKAGE_NAMES = ("python", "pandas", "numpy", "scikit-learn", "scipy", "statsmodels", "sqlalchemy", "fastapi")

    @staticmethod
    def catalog() -> dict[str, Any]:
        return {"operation_count": len(MLOPS_OPERATIONS), "operations": sorted(MLOPS_OPERATIONS)}

    @classmethod
    def environment_fingerprint(cls) -> dict[str, Any]:
        versions = {"python": platform.python_version()}
        for package in cls.PACKAGE_NAMES[1:]:
            try:
                versions[package] = metadata.version(package)
            except metadata.PackageNotFoundError:
                versions[package] = None
        payload = {"platform": platform.platform(), "implementation": platform.python_implementation(), "packages": versions}
        payload["sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return payload

    @staticmethod
    def _manifest(frame: pd.DataFrame, params: dict[str, Any]) -> dict[str, Any]:
        required = ("dataset_id", "source_version_id", "algorithm")
        missing = [key for key in required if not params.get(key)]
        if missing:
            raise IntelligenceError("LINEAGE_REQUIRED", "Reproducibility manifest requires dataset, source version, and algorithm.", {"missing": missing})
        manifest = {
            "dataset_id": str(params["dataset_id"]),
            "source_version_id": str(params["source_version_id"]),
            "dataset_fingerprint": dataframe_fingerprint(frame),
            "algorithm": str(params["algorithm"]),
            "parameters": json_safe(params.get("parameters") or {}),
            "features": [str(column) for column in params.get("features", [])],
            "target_columns": [str(column) for column in params.get("target_columns", [])],
            "preprocessing": json_safe(params.get("preprocessing") or {}),
            "random_state": int(params.get("random_state", 42)),
            "code_revision": params.get("code_revision"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "environment": MLOpsService.environment_fingerprint(),
        }
        manifest["sha256"] = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return manifest

    def run(self, operation: str, *, frame: pd.DataFrame | None = None, **params: Any) -> dict[str, Any]:
        operation = str(operation).strip().casefold()
        if operation not in MLOPS_OPERATIONS:
            raise IntelligenceError("UNKNOWN_MLOPS_OPERATION", "Unsupported MLOps operation.", {"operation": operation, "allowed": sorted(MLOPS_OPERATIONS)})
        if operation == "experiment_registry":
            runs = params.get("runs") or []
            if not isinstance(runs, list):
                raise IntelligenceError("INVALID_EXPERIMENT_RUNS", "runs must be a list.")
            primary_metric = str(params.get("primary_metric", "balanced_accuracy"))
            direction = str(params.get("direction", "maximize"))
            if direction not in {"maximize", "minimize"}:
                raise IntelligenceError("INVALID_DIRECTION", "direction must be maximize or minimize.")
            eligible = [run for run in runs if isinstance(run, dict) and (run.get("metrics") or {}).get(primary_metric) is not None]
            ranked = sorted(eligible, key=lambda run: ((-1 if direction == "maximize" else 1) * float(run["metrics"][primary_metric]), str(run.get("id", ""))))
            return {"operation": operation, "experiment_name": str(params.get("experiment_name", "experiment")), "primary_metric": primary_metric, "direction": direction, "run_count": len(runs), "eligible_runs": len(ranked), "ranking": [{"rank": index + 1, **json_safe(run)} for index, run in enumerate(ranked)], "best_run": json_safe(ranked[0]) if ranked else None}
        if operation == "reproducibility_manifest":
            if frame is None or frame.empty:
                raise IntelligenceError("DATASET_REQUIRED", "A dataset is required for a reproducibility manifest.")
            return {"operation": operation, "manifest": self._manifest(frame, params)}
        if operation == "model_card":
            required = ("model_name", "algorithm", "task_type", "metrics", "training_lineage")
            missing = [key for key in required if not params.get(key)]
            if missing:
                raise IntelligenceError("MODEL_CARD_FIELDS_REQUIRED", "Model card is missing required fields.", {"missing": missing})
            return {
                "operation": operation,
                "model_card": {
                    "model_name": str(params["model_name"]),
                    "version": params.get("version"),
                    "algorithm": str(params["algorithm"]),
                    "task_type": str(params["task_type"]),
                    "intended_use": str(params.get("intended_use", "Decision support within the documented dataset domain.")),
                    "out_of_scope_use": str(params.get("out_of_scope_use", "Undocumented populations, high-impact automated decisions, and incompatible schemas.")),
                    "metrics": json_safe(params["metrics"]),
                    "training_lineage": json_safe(params["training_lineage"]),
                    "features": json_safe(params.get("features", [])),
                    "limitations": json_safe(params.get("limitations") or ["Predictive associations are not causal evidence.", "Performance must be monitored on the production population.", "Schema and feature parity are required before scoring."]),
                    "ethical_considerations": json_safe(params.get("ethical_considerations") or ["Evaluate subgroup performance and material impact before approval."]),
                    "approval_status": str(params.get("approval_status", "CANDIDATE")),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            }
        if operation == "training_serving_parity":
            training = params.get("training_specification") or {}
            serving = params.get("serving_specification") or {}
            checks = []
            for key in ("features", "dtypes", "preprocessing_version", "feature_definition_version", "model_artifact_sha256"):
                expected = training.get(key)
                observed = serving.get(key)
                checks.append({"check": key, "expected": json_safe(expected), "observed": json_safe(observed), "passed": expected == observed})
            return {"operation": operation, "parity": all(item["passed"] for item in checks), "checks": checks, "blocking_mismatches": [item["check"] for item in checks if not item["passed"]]}
        if operation == "model_registry_versioning":
            return {"operation": operation, "required_metadata": ["model_id", "version", "algorithm", "parameters", "metrics", "training_dataset_id", "training_source_version_id", "feature_specification", "preprocessing_specification", "artifact_location", "artifact_sha256", "created_at", "status"], "allowed_statuses": ["CANDIDATE", "VALIDATED", "APPROVED", "CHAMPION", "ARCHIVED", "REJECTED"], "immutability_policy": "Model-version metadata and artifact checksum are immutable after creation.", "persistence_owned_by": "registered_models/model_versions repositories"}
        if operation == "retraining_decision":
            signals = params.get("signals") or {}
            reasons = []
            score = 0.0
            weighted = (("performance_degradation", 0.35, 0.1), ("feature_drift", 0.2, 0.2), ("concept_drift", 0.2, 0.2), ("calibration_drift", 0.1, 0.05), ("schema_incompatibility", 0.15, 0.5))
            for key, weight, threshold in weighted:
                value = float(signals.get(key, 0))
                normalized = min(1.0, value / threshold) if threshold else 0.0
                score += weight * normalized
                if value >= threshold:
                    reasons.append({"signal": key, "value": value, "threshold": threshold})
            decision = "RETRAIN_RECOMMENDED" if score >= 0.6 else "INVESTIGATE" if score >= 0.35 else "NO_RETRAIN"
            return {"operation": operation, "decision": decision, "decision_score": score, "reasons": reasons, "automatic_retraining_started": False, "approval_required": decision == "RETRAIN_RECOMMENDED"}
        if operation == "monitoring_policy":
            task_type = str(params.get("task_type", "classification"))
            policy = {
                "schedule": str(params.get("schedule", "daily")),
                "schema": {"enabled": True, "breaking_change_action": "block_and_alert"},
                "missingness": {"absolute_delta_threshold": 0.1},
                "feature_drift": {"ks_threshold": 0.2, "psi_threshold": 0.2},
                "prediction_drift": {"threshold": 0.2},
                "performance": {"metric": "balanced_accuracy" if task_type == "classification" else "rmse", "relative_degradation_threshold": 0.15},
                "calibration": {"enabled": task_type == "classification", "ece_increase_threshold": 0.05},
                "uncertainty": {"relative_increase_threshold": 0.2},
                "minimum_current_rows": int(params.get("minimum_current_rows", 100)),
                "alert_channels": json_safe(params.get("alert_channels", [])),
            }
            return {"operation": operation, "policy": policy, "external_schedule_created": False}
        if operation == "deployment_strategy":
            risk = str(params.get("risk_level", "medium")).casefold()
            if risk not in {"low", "medium", "high"}:
                raise IntelligenceError("INVALID_RISK_LEVEL", "risk_level must be low, medium, or high.")
            strategy = "shadow" if risk == "high" else "canary" if risk == "medium" else "blue_green"
            return {"operation": operation, "recommended_strategy": strategy, "risk_level": risk, "stages": [{"stage": "offline_validation", "traffic_pct": 0, "approval_required": True}, {"stage": "shadow", "traffic_pct": 0, "approval_required": risk == "high"}, {"stage": "canary", "traffic_pct": 5, "approval_required": True}, {"stage": "expanded_canary", "traffic_pct": 25, "approval_required": True}, {"stage": "production", "traffic_pct": 100, "approval_required": True}], "rollback_triggers": ["performance threshold breach", "schema incompatibility", "latency or error budget breach", "material subgroup regression"], "deployment_executed": False}
        if operation == "governance_approval":
            evidence = params.get("evidence") or {}
            required = ["data_lineage", "reproducibility_manifest", "model_card", "validation", "security_review", "monitoring_policy", "rollback_plan"]
            checks = [{"evidence": key, "present": bool(evidence.get(key))} for key in required]
            blockers = [item["evidence"] for item in checks if not item["present"]]
            requested_status = str(params.get("requested_status", "APPROVED")).upper()
            if requested_status not in {"VALIDATED", "APPROVED", "CHAMPION", "REJECTED"}:
                raise IntelligenceError("INVALID_REQUESTED_STATUS", "Unsupported governance transition.")
            return {"operation": operation, "gate_passed": not blockers, "requested_status": requested_status, "checks": checks, "blockers": blockers, "approval_record_recommended": not blockers, "status_changed": False, "human_approval_required": requested_status in {"APPROVED", "CHAMPION"}}
        policy = self.run("monitoring_policy", **params)
        deployment = self.run("deployment_strategy", **params)
        return {
            "operation": operation,
            "lifecycle": [
                {"stage": "experiment", "status": "READY"},
                {"stage": "reproducibility", "status": "READY" if params.get("reproducibility_manifest") else "REQUIRED"},
                {"stage": "model_card", "status": "READY" if params.get("model_card") else "REQUIRED"},
                {"stage": "registry", "status": "READY" if params.get("model_version_id") else "REQUIRED"},
                {"stage": "validation", "status": "READY" if params.get("validation") else "REQUIRED"},
                {"stage": "governance", "status": "HUMAN_APPROVAL_REQUIRED"},
                {"stage": "deployment", "status": "NOT_EXECUTED", "strategy": deployment["recommended_strategy"]},
                {"stage": "monitoring", "status": "POLICY_READY", "policy": policy["policy"]},
                {"stage": "retraining", "status": "DECISION_GATED"},
            ],
            "unsafe_actions_executed": False,
        }
