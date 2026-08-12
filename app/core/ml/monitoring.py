from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import ks_2samp
from sklearn.inspection import permutation_importance
from sklearn.metrics import balanced_accuracy_score, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.pipeline import Pipeline

from app.core.intelligence.common import ExecutionBudget, IntelligenceError, finish_metadata, json_safe, started_timer
from app.core.ml.common import prepare_supervised
from app.core.ml.registry import build_estimator
from app.core.ml.service import MachineLearningService, classification_metrics, regression_metrics


MONITORING_OPERATIONS = {
    "conformal_regression",
    "conformal_classification",
    "out_of_distribution",
    "adversarial_validation",
    "feature_drift",
    "target_drift",
    "prediction_drift",
    "performance_drift",
    "leakage_detection",
    "champion_challenger",
    "concept_drift",
    "calibration_drift",
    "uncertainty_drift",
    "schema_compatibility",
    "missingness_drift",
    "correlation_drift",
    "segment_stability",
    "threshold_robustness",
    "drift_root_cause",
    "production_readiness",
}


def _psi(reference: pd.Series, current: pd.Series, bins: int = 10) -> float:
    reference = pd.to_numeric(reference, errors="coerce").dropna()
    current = pd.to_numeric(current, errors="coerce").dropna()
    if reference.empty or current.empty:
        return 0.0
    edges = np.unique(np.quantile(reference, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    ref_counts = np.histogram(reference, bins=edges)[0] / len(reference)
    cur_counts = np.histogram(current, bins=edges)[0] / len(current)
    ref_counts = np.clip(ref_counts, 1e-6, None)
    cur_counts = np.clip(cur_counts, 1e-6, None)
    return float(np.sum((cur_counts - ref_counts) * np.log(cur_counts / ref_counts)))


def _categorical_distance(reference: pd.Series, current: pd.Series) -> tuple[float, list[str]]:
    left = reference.astype("string").fillna("<NULL>").value_counts(normalize=True)
    right = current.astype("string").fillna("<NULL>").value_counts(normalize=True)
    categories = left.index.union(right.index)
    distance = float(0.5 * np.abs(left.reindex(categories, fill_value=0) - right.reindex(categories, fill_value=0)).sum())
    return distance, sorted(str(category) for category in set(right.index) - set(left.index))[:50]


class MonitoringService:
    """Shared uncertainty, drift, validation, and production-readiness engine."""

    def __init__(self, budget: ExecutionBudget | None = None):
        self.budget = budget or ExecutionBudget(max_rows=20_000)
        self.ml = MachineLearningService(self.budget)

    @staticmethod
    def _feature_columns(reference: pd.DataFrame, current: pd.DataFrame, requested: list[str] | None, target: str | None) -> list[str]:
        common = [str(column) for column in reference.columns if column in current.columns and str(column) != target]
        features = [str(column) for column in (requested or common)]
        missing = [column for column in features if column not in reference.columns or column not in current.columns]
        if missing or not features:
            raise IntelligenceError("FEATURES_REQUIRED", "Monitoring requires common feature columns.", {"missing": missing})
        return features

    def drift_summary(self, reference: pd.DataFrame, current: pd.DataFrame, features: list[str]) -> list[dict[str, Any]]:
        rows = []
        for feature in features:
            left = reference[feature]
            right = current[feature]
            missing_delta = float(right.isna().mean() - left.isna().mean())
            if pd.api.types.is_numeric_dtype(left) and pd.api.types.is_numeric_dtype(right):
                left_numeric = pd.to_numeric(left, errors="coerce").dropna()
                right_numeric = pd.to_numeric(right, errors="coerce").dropna()
                if left_numeric.empty or right_numeric.empty:
                    statistic, p_value = 0.0, 1.0
                else:
                    test = ks_2samp(left_numeric, right_numeric)
                    statistic, p_value = float(test.statistic), float(test.pvalue)
                psi = _psi(left, right)
                score = max(statistic, min(1.0, psi / 0.25), min(1.0, abs(missing_delta) / 0.1))
                rows.append({"feature": feature, "type": "numeric", "ks_statistic": statistic, "ks_p_value": p_value, "psi": psi, "missingness_delta": missing_delta, "drift_score": float(score), "drifted": bool(statistic >= 0.2 or psi >= 0.2 or abs(missing_delta) >= 0.1)})
            else:
                distance, new_categories = _categorical_distance(left, right)
                score = max(distance, min(1.0, abs(missing_delta) / 0.1))
                rows.append({"feature": feature, "type": "categorical", "total_variation_distance": distance, "new_categories": new_categories, "missingness_delta": missing_delta, "drift_score": float(score), "drifted": bool(distance >= 0.2 or abs(missing_delta) >= 0.1)})
        return sorted(rows, key=lambda item: (-item["drift_score"], item["feature"]))

    @staticmethod
    def _probability(model: Pipeline, X: pd.DataFrame) -> tuple[np.ndarray, list[Any]]:
        if not hasattr(model, "predict_proba"):
            raise IntelligenceError("PROBABILITIES_UNAVAILABLE", "The selected model does not expose class probabilities.")
        probability = np.asarray(model.predict_proba(X), dtype=float)
        classes = list(model.named_steps["model"].classes_)
        return probability, classes

    def _prepared_pair(self, reference: pd.DataFrame, current: pd.DataFrame, params: dict[str, Any]):
        target = params.get("target_column")
        if not target:
            raise IntelligenceError("TARGET_REQUIRED", "target_column is required for this monitoring operation.")
        features = self._feature_columns(reference, current, params.get("feature_columns"), str(target))
        reference_data = prepare_supervised(reference, target_column=str(target), feature_columns=features, task_type=str(params.get("task_type", "auto")), budget=self.budget, minimum_rows=20)
        current_data = prepare_supervised(current, target_column=str(target), feature_columns=features, task_type=reference_data.task_type, budget=self.budget, minimum_rows=10)
        return reference_data, current_data, features

    def run(
        self,
        reference: pd.DataFrame,
        current: pd.DataFrame,
        operation: str,
        *,
        model: Pipeline | None = None,
        challenger_model: Pipeline | None = None,
        **params: Any,
    ) -> dict[str, Any]:
        operation = str(operation).strip().casefold()
        if operation not in MONITORING_OPERATIONS:
            raise IntelligenceError("UNKNOWN_MONITORING_OPERATION", "Unsupported monitoring operation.", {"operation": operation, "allowed": sorted(MONITORING_OPERATIONS)})
        if not isinstance(reference, pd.DataFrame) or reference.empty or not isinstance(current, pd.DataFrame) or current.empty:
            raise IntelligenceError("REFERENCE_AND_CURRENT_REQUIRED", "Reference and current datasets must both contain rows.")
        started = started_timer()
        target = str(params.get("target_column")) if params.get("target_column") else None
        features = self._feature_columns(reference, current, params.get("feature_columns"), target)
        metadata = {"rows_scanned": len(reference) + len(current), "rows_used": min(len(reference), self.budget.max_rows) + min(len(current), self.budget.max_rows), "feature_count": len(features), "sampled": len(reference) > self.budget.max_rows or len(current) > self.budget.max_rows, "sample_size": min(len(reference), self.budget.max_rows) + min(len(current), self.budget.max_rows), "cache_hit": False}

        if operation in {"feature_drift", "missingness_drift", "schema_compatibility", "correlation_drift", "adversarial_validation", "out_of_distribution"}:
            drift = self.drift_summary(reference, current, features)
            if operation == "feature_drift":
                result = {"features": drift, "drifted_feature_count": sum(item["drifted"] for item in drift), "maximum_drift_score": max(item["drift_score"] for item in drift)}
            elif operation == "missingness_drift":
                rows = [{"feature": feature, "reference_missing_rate": float(reference[feature].isna().mean()), "current_missing_rate": float(current[feature].isna().mean()), "delta": float(current[feature].isna().mean() - reference[feature].isna().mean()), "drifted": abs(float(current[feature].isna().mean() - reference[feature].isna().mean())) >= float(params.get("threshold", 0.1))} for feature in features]
                result = {"features": sorted(rows, key=lambda item: (-abs(item["delta"]), item["feature"])), "drifted_feature_count": sum(item["drifted"] for item in rows)}
            elif operation == "schema_compatibility":
                issues = []
                for column in reference.columns:
                    if column not in current.columns:
                        issues.append({"column": str(column), "issue": "missing_column", "severity": "breaking"})
                    elif str(reference[column].dtype) != str(current[column].dtype):
                        issues.append({"column": str(column), "issue": "dtype_change", "reference": str(reference[column].dtype), "current": str(current[column].dtype), "severity": "breaking" if pd.api.types.is_numeric_dtype(reference[column]) != pd.api.types.is_numeric_dtype(current[column]) else "compatible"})
                for column in current.columns:
                    if column not in reference.columns:
                        issues.append({"column": str(column), "issue": "new_column", "severity": "non_breaking"})
                result = {"compatible": not any(item["severity"] == "breaking" for item in issues), "issues": issues}
            elif operation == "correlation_drift":
                numeric = [feature for feature in features if pd.api.types.is_numeric_dtype(reference[feature]) and pd.api.types.is_numeric_dtype(current[feature])]
                if len(numeric) < 2:
                    raise IntelligenceError("NUMERIC_FEATURES_REQUIRED", "Correlation drift requires at least two common numeric features.")
                left = reference[numeric].corr().fillna(0)
                right = current[numeric].corr().fillna(0)
                shifts = []
                for first in range(len(numeric)):
                    for second in range(first + 1, len(numeric)):
                        delta = float(right.iloc[first, second] - left.iloc[first, second])
                        shifts.append({"feature_1": numeric[first], "feature_2": numeric[second], "reference_correlation": float(left.iloc[first, second]), "current_correlation": float(right.iloc[first, second]), "delta": delta, "absolute_delta": abs(delta)})
                shifts.sort(key=lambda item: (-item["absolute_delta"], item["feature_1"], item["feature_2"]))
                mean_shift = float(np.mean([item["absolute_delta"] for item in shifts]))
                result = {"mean_absolute_correlation_delta": mean_shift, "structure_drifted": mean_shift >= float(params.get("threshold", 0.15)), "largest_pair_shifts": shifts[:50]}
            else:
                combined = pd.concat([reference[features].assign(__period__="reference"), current[features].assign(__period__="current")], ignore_index=True)
                outcome = self.ml.train(combined, target_column="__period__", feature_columns=features, task_type="classification", algorithm="random_forest_classifier", model_parameters={"n_estimators": 100, "max_depth": 7, "min_samples_leaf": 3, "class_weight": None, "n_jobs": 1, "random_state": self.budget.random_state})
                score = float(outcome.result["metrics"].get("roc_auc") or outcome.result["metrics"]["balanced_accuracy"])
                interpretation = "severe" if score >= 0.9 else "material" if score >= 0.75 else "mild" if score >= 0.6 else "not_detected"
                result = {"adversarial_auc": score, "distribution_shift": interpretation, "out_of_distribution": score >= float(params.get("threshold", 0.75)), "feature_drift": drift}
        elif operation in {"target_drift"}:
            if not target or target not in reference.columns or target not in current.columns:
                raise IntelligenceError("TARGET_REQUIRED", "target_column must exist in both datasets.")
            result = {"target": target, "drift": self.drift_summary(reference[[target]], current[[target]], [target])[0]}
        elif operation in {"prediction_drift", "performance_drift", "concept_drift", "calibration_drift", "uncertainty_drift", "conformal_regression", "conformal_classification", "segment_stability", "threshold_robustness", "drift_root_cause", "champion_challenger"}:
            if model is None:
                raise IntelligenceError("MODEL_REQUIRED", "This monitoring operation requires a persisted application model.")
            reference_data, current_data, features = self._prepared_pair(reference, current, params)
            reference_prediction = model.predict(reference_data.X)
            current_prediction = model.predict(current_data.X)
            if operation == "prediction_drift":
                left = pd.Series(reference_prediction, name="prediction")
                right = pd.Series(current_prediction, name="prediction")
                result = {"prediction_drift": self.drift_summary(pd.DataFrame({"prediction": left}), pd.DataFrame({"prediction": right}), ["prediction"])[0]}
            elif operation in {"performance_drift", "concept_drift"}:
                reference_metrics = classification_metrics(reference_data.y, reference_prediction) if reference_data.task_type == "classification" else regression_metrics(reference_data.y, reference_prediction)
                current_metrics = classification_metrics(current_data.y, current_prediction) if current_data.task_type == "classification" else regression_metrics(current_data.y, current_prediction)
                if reference_data.task_type == "classification":
                    degradation = float((reference_metrics["balanced_accuracy"] - current_metrics["balanced_accuracy"]) / (abs(reference_metrics["balanced_accuracy"]) + 1e-12))
                else:
                    degradation = float((current_metrics["rmse"] - reference_metrics["rmse"]) / (abs(reference_metrics["rmse"]) + 1e-12))
                result = {"task_type": reference_data.task_type, "reference_metrics": reference_metrics, "current_metrics": current_metrics, "relative_degradation": degradation, "drifted": degradation >= float(params.get("degradation_threshold", 0.15))}
            elif operation == "calibration_drift":
                if reference_data.task_type != "classification":
                    raise IntelligenceError("CLASSIFICATION_REQUIRED", "Calibration drift requires classification.")
                left_probability, classes = self._probability(model, reference_data.X)
                right_probability, _ = self._probability(model, current_data.X)
                def ece(y: pd.Series, probability: np.ndarray) -> float:
                    confidence = probability.max(axis=1)
                    prediction = np.asarray(classes)[probability.argmax(axis=1)]
                    correct = prediction == np.asarray(y)
                    edges = np.linspace(0, 1, 11)
                    value = 0.0
                    for index in range(10):
                        mask = (confidence >= edges[index]) & (confidence < edges[index + 1] if index < 9 else confidence <= edges[index + 1])
                        if mask.any():
                            value += float(mask.mean()) * abs(float(correct[mask].mean()) - float(confidence[mask].mean()))
                    return value
                left_ece, right_ece = ece(reference_data.y, left_probability), ece(current_data.y, right_probability)
                result = {"reference_ece": left_ece, "current_ece": right_ece, "ece_increase": right_ece - left_ece, "drifted": right_ece - left_ece >= float(params.get("ece_threshold", 0.05))}
            elif operation == "uncertainty_drift":
                if reference_data.task_type == "classification":
                    left_probability, _ = self._probability(model, reference_data.X)
                    right_probability, _ = self._probability(model, current_data.X)
                    left_uncertainty = 1 - left_probability.max(axis=1)
                    right_uncertainty = 1 - right_probability.max(axis=1)
                else:
                    transformed_left = model.named_steps["preprocessor"].transform(reference_data.X)
                    transformed_right = model.named_steps["preprocessor"].transform(current_data.X)
                    estimator = model.named_steps["model"]
                    if not hasattr(estimator, "estimators_"):
                        raise IntelligenceError("UNCERTAINTY_UNAVAILABLE", "Regression uncertainty currently requires an ensemble model.")
                    left_uncertainty = np.vstack([tree.predict(transformed_left) for tree in estimator.estimators_]).T.std(axis=1)
                    right_uncertainty = np.vstack([tree.predict(transformed_right) for tree in estimator.estimators_]).T.std(axis=1)
                left_mean, right_mean = float(np.mean(left_uncertainty)), float(np.mean(right_uncertainty))
                increase = float((right_mean - left_mean) / (abs(left_mean) + 1e-12))
                result = {"reference_mean_uncertainty": left_mean, "current_mean_uncertainty": right_mean, "relative_increase": increase, "drifted": increase >= float(params.get("threshold", 0.2))}
            elif operation == "conformal_regression":
                if reference_data.task_type != "regression":
                    raise IntelligenceError("REGRESSION_REQUIRED", "Conformal regression requires a regression target.")
                alpha = float(params.get("alpha", 0.1))
                if not 0 < alpha < 0.5:
                    raise IntelligenceError("INVALID_ALPHA", "alpha must be in (0,0.5).")
                residual = np.abs(np.asarray(reference_data.y, dtype=float) - np.asarray(reference_prediction, dtype=float))
                quantile = float(np.quantile(residual, min(1.0, math.ceil((len(residual) + 1) * (1 - alpha)) / len(residual))))
                lower = np.asarray(current_prediction, dtype=float) - quantile
                upper = np.asarray(current_prediction, dtype=float) + quantile
                actual = np.asarray(current_data.y, dtype=float)
                coverage = float(np.mean((actual >= lower) & (actual <= upper)))
                result = {"alpha": alpha, "nominal_coverage": 1 - alpha, "empirical_coverage": coverage, "interval_half_width": quantile, "intervals_preview": [{"prediction": float(prediction), "lower": float(lo), "upper": float(hi)} for prediction, lo, hi in zip(np.asarray(current_prediction).ravel()[:500], lower.ravel()[:500], upper.ravel()[:500])]}
            elif operation == "conformal_classification":
                if reference_data.task_type != "classification":
                    raise IntelligenceError("CLASSIFICATION_REQUIRED", "Conformal classification requires classification.")
                alpha = float(params.get("alpha", 0.1))
                left_probability, classes = self._probability(model, reference_data.X)
                right_probability, _ = self._probability(model, current_data.X)
                class_index = {str(label): index for index, label in enumerate(classes)}
                scores = np.asarray([1 - left_probability[row, class_index[str(label)]] for row, label in enumerate(reference_data.y)])
                threshold = float(np.quantile(scores, min(1.0, math.ceil((len(scores) + 1) * (1 - alpha)) / len(scores))))
                sets = [[str(classes[index]) for index, probability in enumerate(row) if 1 - probability <= threshold] for row in right_probability]
                coverage = float(np.mean([str(label) in prediction_set for label, prediction_set in zip(current_data.y, sets)]))
                result = {"alpha": alpha, "nominal_coverage": 1 - alpha, "empirical_coverage": coverage, "score_threshold": threshold, "mean_set_size": float(np.mean([len(item) for item in sets])), "prediction_sets_preview": sets[:500]}
            elif operation == "segment_stability":
                segment = str(params.get("segment_column", ""))
                if segment not in features:
                    raise IntelligenceError("SEGMENT_COLUMN_REQUIRED", "segment_column must be one of the selected features.")
                rows = []
                for label, group in current_data.X.assign(__actual__=np.asarray(current_data.y), __prediction__=np.asarray(current_prediction)).groupby(segment, sort=True):
                    if len(group) < max(5, int(params.get("minimum_segment_rows", 10))):
                        continue
                    metrics = classification_metrics(group["__actual__"], group["__prediction__"]) if current_data.task_type == "classification" else regression_metrics(group["__actual__"], group["__prediction__"])
                    rows.append({"segment": str(label), "rows": len(group), "metrics": metrics})
                result = {"segment_column": segment, "segments": rows}
            elif operation == "threshold_robustness":
                result = self.ml.threshold_analysis(model, current, **params)
            elif operation == "champion_challenger":
                if challenger_model is None:
                    raise IntelligenceError("CHALLENGER_MODEL_REQUIRED", "Champion/challenger evaluation requires two persisted models.")
                challenger_prediction = challenger_model.predict(current_data.X)
                champion_metrics = classification_metrics(current_data.y, current_prediction) if current_data.task_type == "classification" else regression_metrics(current_data.y, current_prediction)
                challenger_metrics = classification_metrics(current_data.y, challenger_prediction) if current_data.task_type == "classification" else regression_metrics(current_data.y, challenger_prediction)
                winner = "champion" if (champion_metrics["balanced_accuracy"] >= challenger_metrics["balanced_accuracy"] if current_data.task_type == "classification" else champion_metrics["rmse"] <= challenger_metrics["rmse"]) else "challenger"
                result = {"winner": winner, "champion_metrics": champion_metrics, "challenger_metrics": challenger_metrics, "automatic_promotion": False, "approval_required": winner == "challenger"}
            else:
                drift = self.drift_summary(reference, current, features)
                scoring = "balanced_accuracy" if reference_data.task_type == "classification" else "neg_root_mean_squared_error"
                importance = permutation_importance(model, reference_data.X, reference_data.y, n_repeats=3, random_state=self.budget.random_state, scoring=scoring, n_jobs=1)
                importance_map = {feature: max(0.0, float(value)) for feature, value in zip(features, importance.importances_mean)}
                ranked = sorted([{**item, "predictive_importance": importance_map[item["feature"]], "root_cause_score": float(item["drift_score"] * (importance_map[item["feature"]] + 1e-6))} for item in drift], key=lambda item: (-item["root_cause_score"], item["feature"]))
                result = {"root_cause_ranking": ranked, "top_driver": ranked[0] if ranked else None}
        elif operation == "leakage_detection":
            if not target or target not in reference.columns:
                raise IntelligenceError("TARGET_REQUIRED", "target_column is required for leakage detection.")
            risks = []
            target_series = reference[target]
            for feature in features:
                uniqueness = float(reference[feature].nunique(dropna=False) / len(reference))
                if uniqueness >= 0.99:
                    risks.append({"feature": feature, "risk": "identifier", "severity": "high", "evidence": uniqueness})
                lowered = feature.casefold()
                if any(token in lowered for token in ("target", "label", "outcome", "future", "post_")):
                    risks.append({"feature": feature, "risk": "name_signal", "severity": "medium"})
                if pd.api.types.is_numeric_dtype(reference[feature]) and pd.api.types.is_numeric_dtype(target_series):
                    correlation = abs(float(pd.to_numeric(reference[feature], errors="coerce").corr(pd.to_numeric(target_series, errors="coerce"))))
                    if math.isfinite(correlation) and correlation >= 0.98:
                        risks.append({"feature": feature, "risk": "near_perfect_target_correlation", "severity": "high", "evidence": correlation})
            result = {"risk_count": len(risks), "high_risk_count": sum(item["severity"] == "high" for item in risks), "risks": risks, "safe_to_train": not any(item["severity"] == "high" for item in risks)}
        else:
            drift = self.drift_summary(reference, current, features)
            checks = [
                {"check": "schema_compatibility", "score": 1.0 if set(reference.columns) <= set(current.columns) else 0.0},
                {"check": "feature_drift", "score": max(0.0, 1 - np.mean([item["drift_score"] for item in drift]))},
                {"check": "sample_size", "score": min(1.0, len(current) / 500)},
                {"check": "missingness", "score": max(0.0, 1 - float(current[features].isna().mean().mean()) * 2)},
            ]
            score = float(np.mean([item["score"] for item in checks]) * 100)
            result = {"readiness_score": score, "status": "READY" if score >= 80 else "CONDITIONALLY_READY" if score >= 65 else "NOT_READY", "checks": checks, "recommendations": ["Resolve schema incompatibilities before release."] if checks[0]["score"] == 0 else [], "automatic_deployment": False}

        return {"operation": operation, **json_safe(result), "execution": finish_metadata(started, metadata)}
