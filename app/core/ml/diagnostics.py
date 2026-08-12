from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.base import clone
from sklearn.inspection import partial_dependence, permutation_importance
from sklearn.metrics import balanced_accuracy_score, mean_squared_error
from sklearn.model_selection import KFold, StratifiedKFold, cross_validate, learning_curve, train_test_split, validation_curve
from sklearn.pipeline import Pipeline

from app.core.intelligence.common import ExecutionBudget, IntelligenceError, finish_metadata, json_safe, started_timer
from app.core.ml.common import PreparedSupervisedData, prepare_supervised
from app.core.ml.service import classification_metrics, regression_metrics


DIAGNOSTIC_OPERATIONS = {
    "cross_validation",
    "learning_curve",
    "validation_curve",
    "bootstrap_stability",
    "permutation_importance_stability",
    "partial_dependence",
    "ice",
    "error_slices",
    "bias_variance",
    "residual_diagnostics",
}


class ModelDiagnosticsService:
    """Diagnostics against an explicitly supplied, application-owned model."""

    def __init__(self, budget: ExecutionBudget | None = None):
        self.budget = budget or ExecutionBudget(max_rows=10_000)

    def _prepare(self, frame: pd.DataFrame, params: dict[str, Any]) -> PreparedSupervisedData:
        target = params.get("target_column") or params.get("target_columns")
        if target is None:
            raise IntelligenceError("TARGET_REQUIRED", "target_column is required for model diagnostics.")
        return prepare_supervised(
            frame,
            target_column=target,
            feature_columns=params.get("feature_columns"),
            task_type=str(params.get("task_type", "auto")),
            budget=self.budget,
            minimum_rows=30,
        )

    @staticmethod
    def _cv(data: PreparedSupervisedData, folds: int, random_state: int):
        folds = min(10, max(2, folds))
        if data.task_type == "classification":
            if isinstance(data.y, pd.DataFrame):
                raise IntelligenceError("SINGLE_TARGET_REQUIRED", "Classification diagnostics require a single target.")
            minimum = int(data.y.value_counts().min())
            folds = min(folds, minimum)
            if folds < 2:
                raise IntelligenceError("INSUFFICIENT_CLASS_ROWS", "Each class needs at least two rows for cross-validation.")
            return StratifiedKFold(folds, shuffle=True, random_state=random_state)
        return KFold(folds, shuffle=True, random_state=random_state)

    @staticmethod
    def _score(task_type: str) -> str:
        return "balanced_accuracy" if task_type == "classification" else "neg_root_mean_squared_error"

    @staticmethod
    def _evaluate(task_type: str, actual: Any, predicted: Any) -> dict[str, Any]:
        return classification_metrics(actual, predicted) if task_type == "classification" else regression_metrics(actual, predicted)

    def run(self, model: Pipeline, frame: pd.DataFrame, operation: str, **params: Any) -> dict[str, Any]:
        operation = str(operation).strip().casefold()
        if operation not in DIAGNOSTIC_OPERATIONS:
            raise IntelligenceError("UNKNOWN_DIAGNOSTIC", "Unsupported model diagnostic.", {"operation": operation, "allowed": sorted(DIAGNOSTIC_OPERATIONS)})
        started = started_timer()
        data = self._prepare(frame, params)
        random_state = int(params.get("random_state", self.budget.random_state))
        folds = int(params.get("folds", 5))
        splitter = self._cv(data, folds, random_state)
        scoring = self._score(data.task_type)

        if operation == "cross_validation":
            scores = cross_validate(clone(model), data.X, data.y, cv=splitter, scoring=scoring, return_train_score=True, n_jobs=1)
            train_scores = np.asarray(scores["train_score"], dtype=float)
            validation_scores = np.asarray(scores["test_score"], dtype=float)
            result = {
                "scoring": scoring,
                "train_scores": json_safe(train_scores),
                "validation_scores": json_safe(validation_scores),
                "mean_validation_score": float(validation_scores.mean()),
                "validation_std": float(validation_scores.std(ddof=1)) if len(validation_scores) > 1 else 0.0,
                "mean_generalization_gap": float(train_scores.mean() - validation_scores.mean()),
            }
        elif operation == "learning_curve":
            sizes = np.asarray(params.get("train_sizes", [0.2, 0.4, 0.6, 0.8, 1.0]), dtype=float)
            if np.any(sizes <= 0) or np.any(sizes > 1):
                raise IntelligenceError("INVALID_TRAIN_SIZES", "Fractional train_sizes must be in (0,1].")
            counts, train, validation = learning_curve(clone(model), data.X, data.y, train_sizes=sizes, cv=splitter, scoring=scoring, n_jobs=1, shuffle=True, random_state=random_state)
            result = {
                "scoring": scoring,
                "curve": [
                    {
                        "training_rows": int(count),
                        "train_mean": float(np.mean(train[index])),
                        "train_std": float(np.std(train[index])),
                        "validation_mean": float(np.mean(validation[index])),
                        "validation_std": float(np.std(validation[index])),
                    }
                    for index, count in enumerate(counts)
                ],
            }
        elif operation == "validation_curve":
            parameter_name = str(params.get("parameter_name", ""))
            parameter_values = params.get("parameter_values")
            if not parameter_name or not isinstance(parameter_values, list) or len(parameter_values) < 2:
                raise IntelligenceError("VALIDATION_PARAMETER_REQUIRED", "parameter_name and at least two parameter_values are required.")
            if not parameter_name.startswith("model__"):
                parameter_name = f"model__{parameter_name}"
            try:
                train, validation = validation_curve(clone(model), data.X, data.y, param_name=parameter_name, param_range=parameter_values[:20], cv=splitter, scoring=scoring, n_jobs=1)
            except (TypeError, ValueError) as exc:
                raise IntelligenceError("INVALID_VALIDATION_PARAMETER", "Validation-curve parameter is not supported by this model.", {"parameter_name": parameter_name, "error": str(exc)}) from exc
            result = {
                "parameter_name": parameter_name,
                "scoring": scoring,
                "curve": [
                    {
                        "parameter_value": json_safe(value),
                        "train_mean": float(np.mean(train[index])),
                        "validation_mean": float(np.mean(validation[index])),
                        "validation_std": float(np.std(validation[index])),
                    }
                    for index, value in enumerate(parameter_values[:20])
                ],
            }
        elif operation == "bootstrap_stability":
            X_train, X_validation, y_train, y_validation = train_test_split(data.X, data.y, test_size=0.25, random_state=random_state, stratify=data.y if data.task_type == "classification" and isinstance(data.y, pd.Series) and data.y.value_counts().min() >= 2 else None)
            repeats = min(20, max(3, int(params.get("repeats", 8))))
            scores = []
            rng = np.random.default_rng(random_state)
            for _ in range(repeats):
                sample = rng.choice(len(X_train), len(X_train), replace=True)
                candidate = clone(model)
                candidate.fit(X_train.iloc[sample], y_train.iloc[sample])
                prediction = candidate.predict(X_validation)
                score = balanced_accuracy_score(y_validation, prediction) if data.task_type == "classification" else -math.sqrt(mean_squared_error(y_validation, prediction))
                scores.append(float(score))
            result = {"scoring": scoring, "bootstrap_scores": scores, "mean_score": float(np.mean(scores)), "std_score": float(np.std(scores, ddof=1)), "coefficient_of_variation": float(np.std(scores, ddof=1) / (abs(np.mean(scores)) + 1e-12))}
        elif operation == "permutation_importance_stability":
            repeats = min(10, max(3, int(params.get("repeats", 5))))
            runs = []
            for repeat in range(repeats):
                importance = permutation_importance(model, data.X, data.y, n_repeats=3, random_state=random_state + repeat, scoring=scoring, n_jobs=1)
                runs.append(np.asarray(importance.importances_mean, dtype=float))
            matrix = np.vstack(runs)
            rows = sorted(
                [
                    {"feature": feature, "importance_mean": float(matrix[:, index].mean()), "importance_std": float(matrix[:, index].std(ddof=1)), "positive_run_rate": float((matrix[:, index] > 0).mean())}
                    for index, feature in enumerate(data.features)
                ],
                key=lambda item: (-item["importance_mean"], item["feature"]),
            )
            result = {"scoring": scoring, "repeats": repeats, "features": rows}
        elif operation in {"partial_dependence", "ice"}:
            feature = str(params.get("feature") or (data.numeric_features[0] if data.numeric_features else data.features[0]))
            if feature not in data.features:
                raise IntelligenceError("FEATURE_NOT_FOUND", "Diagnostic feature is not part of the model input.", {"feature": feature})
            kind = "individual" if operation == "ice" else "average"
            try:
                dependence = partial_dependence(model, data.X, [feature], kind=kind, grid_resolution=min(50, int(params.get("grid_resolution", 20))))
            except (TypeError, ValueError) as exc:
                raise IntelligenceError("DEPENDENCE_FAILED", "Partial-dependence calculation failed for this feature/model.", {"feature": feature, "error": str(exc)}) from exc
            result = {
                "feature": feature,
                "kind": kind,
                "grid_values": json_safe(dependence.get("grid_values", [])),
                "average": json_safe(dependence.get("average")),
                "individual_preview": json_safe(dependence.get("individual")[:, : min(100, len(data.X))] if dependence.get("individual") is not None else None),
            }
        elif operation == "error_slices":
            segment_column = str(params.get("segment_column", ""))
            if segment_column not in data.X.columns:
                raise IntelligenceError("SEGMENT_COLUMN_REQUIRED", "segment_column must be one of the selected features.")
            prediction = model.predict(data.X)
            evaluated = data.X[[segment_column]].copy()
            evaluated["__actual__"] = np.asarray(data.y)
            evaluated["__prediction__"] = np.asarray(prediction)
            if pd.api.types.is_numeric_dtype(evaluated[segment_column]) and evaluated[segment_column].nunique() > 10:
                evaluated["__segment__"] = pd.qcut(evaluated[segment_column], q=4, duplicates="drop").astype(str)
            else:
                evaluated["__segment__"] = evaluated[segment_column].astype(str)
            minimum_rows = max(5, int(params.get("minimum_segment_rows", 10)))
            slices = []
            for segment, group in evaluated.groupby("__segment__", sort=True):
                if len(group) < minimum_rows:
                    continue
                slices.append({"segment": str(segment), "rows": len(group), "metrics": self._evaluate(data.task_type, group["__actual__"], group["__prediction__"])})
            result = {"segment_column": segment_column, "minimum_segment_rows": minimum_rows, "slices": slices}
        elif operation == "bias_variance":
            scores = cross_validate(clone(model), data.X, data.y, cv=splitter, scoring=scoring, return_train_score=True, n_jobs=1)
            train_score = float(np.mean(scores["train_score"]))
            validation_score = float(np.mean(scores["test_score"]))
            gap = train_score - validation_score
            variability = float(np.std(scores["test_score"], ddof=1)) if len(scores["test_score"]) > 1 else 0.0
            diagnosis = "high_variance" if gap > 0.1 else "high_bias" if validation_score < (0.6 if data.task_type == "classification" else -np.std(np.asarray(data.y))) else "balanced"
            result = {"scoring": scoring, "mean_train_score": train_score, "mean_validation_score": validation_score, "generalization_gap": gap, "validation_variability": variability, "diagnosis": diagnosis}
        else:
            prediction = np.asarray(model.predict(data.X), dtype=float)
            actual = np.asarray(data.y, dtype=float)
            if actual.ndim > 1:
                residual = actual - prediction
                residual_flat = residual.ravel()
                prediction_flat = prediction.ravel()
            else:
                residual_flat = actual - prediction
                prediction_flat = prediction
            sample = residual_flat[: min(5000, len(residual_flat))]
            shapiro = stats.shapiro(sample) if len(sample) >= 3 else None
            result = {
                "metrics": regression_metrics(actual, prediction),
                "residual_summary": {"mean": float(np.mean(residual_flat)), "std": float(np.std(residual_flat, ddof=1)), "min": float(np.min(residual_flat)), "max": float(np.max(residual_flat)), "quantiles": {str(q): float(np.quantile(residual_flat, q)) for q in (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99)}},
                "normality": None if shapiro is None else {"statistic": float(shapiro.statistic), "p_value": float(shapiro.pvalue)},
                "prediction_residual_correlation": float(np.corrcoef(prediction_flat, residual_flat)[0, 1]) if np.std(prediction_flat) > 0 and np.std(residual_flat) > 0 else 0.0,
                "residual_preview": json_safe(residual_flat[:500]),
            }
        return {"operation": operation, "task_type": data.task_type, **json_safe(result), "execution": finish_metadata(started, data.execution)}
