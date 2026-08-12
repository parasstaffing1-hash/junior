from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator
from sklearn.calibration import calibration_curve
from sklearn.inspection import permutation_importance
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    f1_score,
    log_loss,
    mean_absolute_error,
    mean_absolute_percentage_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import KFold, ParameterGrid, StratifiedKFold, cross_val_score, train_test_split
from sklearn.pipeline import Pipeline

from app.core.intelligence.common import ExecutionBudget, IntelligenceError, finish_metadata, json_safe, started_timer
from app.core.ml.common import PreparedSupervisedData, build_preprocessor, prepare_supervised
from app.core.ml.registry import DEFAULT_CANDIDATES, MODEL_REGISTRY, build_estimator, model_catalog


@dataclass
class TrainingOutcome:
    model: Pipeline
    result: dict[str, Any]


def classification_metrics(y_true: Any, prediction: Any, probability: np.ndarray | None = None, classes: list[Any] | None = None) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "accuracy": float(accuracy_score(y_true, prediction)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, prediction)),
        "f1_macro": float(f1_score(y_true, prediction, average="macro", zero_division=0)),
        "precision_macro": float(precision_score(y_true, prediction, average="macro", zero_division=0)),
        "recall_macro": float(recall_score(y_true, prediction, average="macro", zero_division=0)),
    }
    if probability is not None:
        try:
            metrics["log_loss"] = float(log_loss(y_true, probability, labels=classes))
        except ValueError:
            metrics["log_loss"] = None
        if probability.shape[1] == 2 and classes:
            try:
                binary = (np.asarray(y_true) == classes[-1]).astype(int)
                metrics["roc_auc"] = float(roc_auc_score(binary, probability[:, -1]))
            except ValueError:
                metrics["roc_auc"] = None
    return metrics


def regression_metrics(y_true: Any, prediction: Any) -> dict[str, Any]:
    actual = np.asarray(y_true, dtype=float)
    predicted = np.asarray(prediction, dtype=float)
    nonzero = np.abs(actual) > 1e-12
    return {
        "mae": float(mean_absolute_error(actual, predicted)),
        "rmse": float(math.sqrt(mean_squared_error(actual, predicted))),
        "r_squared": float(r2_score(actual, predicted)),
        "mape_pct": float(np.mean(np.abs((actual[nonzero] - predicted[nonzero]) / actual[nonzero])) * 100) if nonzero.any() else None,
    }


class MachineLearningService:
    """Shared preparation, training, tuning, comparison, and explanation service."""

    DEFAULT_GRIDS = {
        "logistic_regression": {"C": [0.1, 1.0, 10.0]},
        "ridge_regression": {"alpha": [0.1, 1.0, 10.0]},
        "lasso_regression": {"alpha": [0.001, 0.01, 0.1]},
        "elastic_net_regression": {"alpha": [0.001, 0.01, 0.1], "l1_ratio": [0.2, 0.5, 0.8]},
        "decision_tree_classifier": {"max_depth": [4, 8, 12], "min_samples_leaf": [1, 3, 5]},
        "decision_tree_regressor": {"max_depth": [4, 8, 12], "min_samples_leaf": [1, 3, 5]},
        "random_forest_classifier": {"n_estimators": [80, 150], "max_depth": [6, 10, None], "min_samples_leaf": [1, 3]},
        "random_forest_regressor": {"n_estimators": [80, 150], "max_depth": [6, 10, None], "min_samples_leaf": [1, 3]},
        "gradient_boosting_classifier": {"n_estimators": [60, 100], "learning_rate": [0.03, 0.08], "max_depth": [2, 3]},
        "gradient_boosting_regressor": {"n_estimators": [60, 100], "learning_rate": [0.03, 0.08], "max_depth": [2, 3]},
    }

    def __init__(self, budget: ExecutionBudget | None = None):
        self.budget = budget or ExecutionBudget()

    @staticmethod
    def catalog() -> dict[str, Any]:
        catalog = model_catalog()
        return {
            "model_count": len(catalog),
            "classification_count": sum(item["task_type"] == "classification" for item in catalog),
            "regression_count": sum(item["task_type"] == "regression" for item in catalog),
            "models": catalog,
        }

    def _prepare(self, frame: pd.DataFrame, params: dict[str, Any], *, minimum_rows: int = 20) -> PreparedSupervisedData:
        target = params.get("target_column")
        if target is None:
            target = params.get("target_columns")
        if target is None:
            raise IntelligenceError("TARGET_REQUIRED", "target_column is required.")
        return prepare_supervised(
            frame,
            target_column=target,
            feature_columns=params.get("feature_columns"),
            task_type=str(params.get("task_type", "auto")),
            budget=self.budget,
            minimum_rows=minimum_rows,
        )

    @staticmethod
    def _split(data: PreparedSupervisedData, *, validation_size: float, random_state: int):
        if not 0.1 <= validation_size <= 0.4:
            raise IntelligenceError("INVALID_VALIDATION_SIZE", "validation_size must be between 0.1 and 0.4.")
        stratify = None
        if data.task_type == "classification" and isinstance(data.y, pd.Series):
            counts = data.y.value_counts()
            if int(counts.min()) >= 2 and round(len(data.y) * validation_size) >= len(counts):
                stratify = data.y
        return train_test_split(data.X, data.y, test_size=validation_size, random_state=random_state, stratify=stratify)

    @staticmethod
    def _oversample(X: pd.DataFrame, y: pd.Series, *, random_state: int) -> tuple[pd.DataFrame, pd.Series]:
        combined = X.copy()
        combined["__target__"] = y.to_numpy()
        maximum = int(y.value_counts().max())
        groups = []
        for _, group in combined.groupby("__target__", sort=True):
            groups.append(group.sample(n=maximum, replace=True, random_state=random_state))
        balanced = pd.concat(groups, ignore_index=True).sample(frac=1, random_state=random_state).reset_index(drop=True)
        return balanced.drop(columns="__target__"), balanced["__target__"]

    @staticmethod
    def _prediction_probability(model: Pipeline, X: pd.DataFrame) -> tuple[np.ndarray | None, list[Any] | None]:
        if not hasattr(model, "predict_proba"):
            return None, None
        try:
            probability = np.asarray(model.predict_proba(X), dtype=float)
            classes = list(model.named_steps["model"].classes_)
            return probability, classes
        except (AttributeError, ValueError):
            return None, None

    def readiness(self, frame: pd.DataFrame, **params: Any) -> dict[str, Any]:
        started = started_timer()
        data = self._prepare(frame, params, minimum_rows=10)
        missing_rate = float(data.X.isna().mean().mean())
        duplicate_rate = float(pd.concat([data.X, pd.DataFrame(data.y)], axis=1).duplicated().mean())
        sample_score = min(1.0, len(data.X) / 500)
        identifier_risk = max(float(data.X[column].nunique(dropna=False) / len(data.X)) for column in data.features)
        checks = [
            {"check": "sample_size", "score": sample_score, "value": len(data.X)},
            {"check": "missingness", "score": max(0.0, 1 - 2 * missing_rate), "value": missing_rate},
            {"check": "duplicates", "score": max(0.0, 1 - 2 * duplicate_rate), "value": duplicate_rate},
            {"check": "identifier_risk", "score": 0.5 if identifier_risk >= 0.99 else 1.0, "value": identifier_risk},
        ]
        if data.task_type == "classification" and isinstance(data.y, pd.Series):
            balance = float(data.y.value_counts(normalize=True).min())
            checks.append({"check": "class_balance", "score": min(1.0, balance / 0.2), "value": balance})
        score = float(np.mean([item["score"] for item in checks]) * 100)
        status = "READY" if score >= 80 else "CONDITIONALLY_READY" if score >= 65 else "NOT_READY"
        return {
            "task_type": data.task_type,
            "target_columns": data.target_columns,
            "feature_count": len(data.features),
            "numeric_features": data.numeric_features,
            "categorical_features": data.categorical_features,
            "readiness_score": score,
            "status": status,
            "checks": checks,
            "warnings": data.warnings,
            "recommended_models": DEFAULT_CANDIDATES[data.task_type],
            "execution": finish_metadata(started, data.execution),
        }

    def train(self, frame: pd.DataFrame, **params: Any) -> TrainingOutcome:
        started = started_timer()
        data = self._prepare(frame, params)
        algorithm = str(params.get("algorithm") or DEFAULT_CANDIDATES[data.task_type][1])
        random_state = int(params.get("random_state", self.budget.random_state))
        spec, estimator = build_estimator(
            algorithm,
            task_type=data.task_type,
            random_state=random_state,
            parameters=params.get("model_parameters"),
        )
        if spec.multioutput_target and not isinstance(data.y, pd.DataFrame):
            raise IntelligenceError("MULTIOUTPUT_TARGET_REQUIRED", "This estimator requires target_columns with at least two targets.", {"algorithm": algorithm})
        if isinstance(data.y, pd.DataFrame) and not spec.multioutput_target and algorithm not in {"pls_regression"}:
            raise IntelligenceError("SINGLE_TARGET_REQUIRED", "This estimator supports one target in the current platform.", {"algorithm": algorithm})
        if algorithm == "isotonic_regression" and len(data.features) != 1:
            raise IntelligenceError("SINGLE_FEATURE_REQUIRED", "Isotonic regression requires exactly one selected feature.")
        if spec.maximum_rows and len(data.X) > spec.maximum_rows:
            selection = np.random.default_rng(random_state).choice(len(data.X), spec.maximum_rows, replace=False)
            data.X = data.X.iloc[selection].reset_index(drop=True)
            data.y = data.y.iloc[selection].reset_index(drop=True)
            data.execution["rows_used"] = spec.maximum_rows
            data.execution["sampled"] = True
            data.execution["sample_size"] = spec.maximum_rows
            data.warnings.append({"code": "MODEL_SPECIFIC_ROW_LIMIT", "algorithm": algorithm, "rows_used": spec.maximum_rows})
        if algorithm in {"poisson_regression", "gamma_regression", "tweedie_regression"}:
            target_values = np.asarray(data.y, dtype=float)
            minimum = 0 if algorithm == "poisson_regression" else 1e-12
            if (target_values < minimum).any():
                raise IntelligenceError("TARGET_DOMAIN_ERROR", f"{algorithm} requires a non-negative target." if minimum == 0 else f"{algorithm} requires a positive target.")
        X_train, X_validation, y_train, y_validation = self._split(data, validation_size=float(params.get("validation_size", 0.2)), random_state=random_state)
        imbalance_strategy = str(params.get("imbalance_strategy", "none"))
        if imbalance_strategy not in {"none", "random_oversample"}:
            raise IntelligenceError("INVALID_IMBALANCE_STRATEGY", "imbalance_strategy must be none or random_oversample.")
        if imbalance_strategy == "random_oversample":
            if data.task_type != "classification" or not isinstance(y_train, pd.Series):
                raise IntelligenceError("CLASSIFICATION_REQUIRED", "Random oversampling is available for classification only.")
            X_train, y_train = self._oversample(X_train, y_train, random_state=random_state)
        pipeline = Pipeline([("preprocessor", build_preprocessor(data, non_negative=spec.non_negative_features)), ("model", estimator)])
        try:
            pipeline.fit(X_train, y_train)
            prediction = pipeline.predict(X_validation)
        except Exception as exc:
            raise IntelligenceError("MODEL_TRAINING_FAILED", "Model training failed.", {"algorithm": algorithm, "error": str(exc)}) from exc
        if data.task_type == "classification":
            probability, classes = self._prediction_probability(pipeline, X_validation)
            metrics = classification_metrics(y_validation, prediction, probability, classes)
            primary_metric = "balanced_accuracy"
            baseline = float(y_validation.value_counts(normalize=True).max()) if isinstance(y_validation, pd.Series) else None
        else:
            metrics = regression_metrics(y_validation, prediction)
            primary_metric = "rmse"
            baseline = float(math.sqrt(mean_squared_error(y_validation, np.repeat(np.asarray(y_train).mean(axis=0, keepdims=True), len(y_validation), axis=0)))) if np.asarray(y_validation).ndim > 1 else float(math.sqrt(mean_squared_error(y_validation, np.repeat(float(np.asarray(y_train).mean()), len(y_validation)))))
        try:
            transformed_feature_count = int(len(pipeline.named_steps["preprocessor"].get_feature_names_out()))
        except (AttributeError, ValueError):
            transformed_feature_count = len(data.features)
        result = {
            "algorithm": algorithm,
            "task_type": data.task_type,
            "parameters": json_safe(params.get("model_parameters") or {}),
            "metrics": metrics,
            "primary_metric": primary_metric,
            "baseline": baseline,
            "training_rows": int(len(X_train)),
            "validation_rows": int(len(X_validation)),
            "features": data.features,
            "target_columns": data.target_columns,
            "preprocessing": {
                "numeric": {"columns": data.numeric_features, "imputation": "median", "scaling": "minmax" if spec.non_negative_features else "standard"},
                "categorical": {"columns": data.categorical_features, "imputation": "most_frequent", "encoding": "one_hot", "max_categories": 50},
                "transformed_feature_count": transformed_feature_count,
            },
            "imbalance_strategy": imbalance_strategy,
            "random_state": random_state,
            "warnings": data.warnings,
            "execution": finish_metadata(started, data.execution),
        }
        return TrainingOutcome(model=pipeline, result=json_safe(result))

    def compare(self, frame: pd.DataFrame, **params: Any) -> tuple[dict[str, Any], TrainingOutcome]:
        started = started_timer()
        readiness = self.readiness(frame, **params)
        task_type = readiness["task_type"]
        algorithms = list(params.get("algorithms") or DEFAULT_CANDIDATES[task_type])[: min(12, self.budget.max_trials)]
        successes: list[TrainingOutcome] = []
        failures = []
        for algorithm in algorithms:
            try:
                successes.append(self.train(frame, **{**params, "task_type": task_type, "algorithm": algorithm}))
            except IntelligenceError as exc:
                failures.append({"algorithm": algorithm, "code": exc.code, "message": exc.message, "details": exc.details})
        if not successes:
            raise IntelligenceError("NO_SUCCESSFUL_MODELS", "All candidate models failed.", {"failures": failures})
        if task_type == "classification":
            ranked = sorted(successes, key=lambda item: (-float(item.result["metrics"]["balanced_accuracy"]), -float(item.result["metrics"]["f1_macro"]), item.result["algorithm"]))
        else:
            ranked = sorted(successes, key=lambda item: (float(item.result["metrics"]["rmse"]), -float(item.result["metrics"]["r_squared"]), item.result["algorithm"]))
        leaderboard = []
        for rank, outcome in enumerate(ranked, 1):
            leaderboard.append({"rank": rank, "algorithm": outcome.result["algorithm"], "metrics": outcome.result["metrics"], "execution": outcome.result["execution"]})
        return (
            {
                "task_type": task_type,
                "champion_algorithm": ranked[0].result["algorithm"],
                "candidate_count": len(algorithms),
                "successful_candidates": len(ranked),
                "leaderboard": leaderboard,
                "failures": failures,
                "readiness": readiness,
                "execution": {"execution_ms": round((started_timer() - started) * 1000, 3), "rows_scanned": len(frame), "rows_used": max(item.result["execution"]["rows_used"] for item in ranked), "feature_count": len(ranked[0].result["features"]), "sampled": any(item.result["execution"]["sampled"] for item in ranked), "sample_size": max(item.result["execution"]["sample_size"] for item in ranked), "cache_hit": False},
            },
            ranked[0],
        )

    def tune(self, frame: pd.DataFrame, **params: Any) -> tuple[dict[str, Any], TrainingOutcome]:
        started = started_timer()
        algorithm = str(params.get("algorithm", ""))
        if algorithm not in MODEL_REGISTRY:
            raise IntelligenceError("UNKNOWN_ALGORITHM", "A registered algorithm is required for tuning.")
        grid = params.get("parameter_grid") or self.DEFAULT_GRIDS.get(algorithm)
        if not isinstance(grid, dict) or not grid:
            raise IntelligenceError("PARAMETER_GRID_REQUIRED", "No governed tuning grid is available for this algorithm.")
        candidates = list(ParameterGrid(grid))[: self.budget.max_trials]
        outcomes = []
        failures = []
        for candidate in candidates:
            try:
                outcomes.append(self.train(frame, **{**params, "model_parameters": candidate}))
            except IntelligenceError as exc:
                failures.append({"parameters": candidate, "code": exc.code, "message": exc.message})
        if not outcomes:
            raise IntelligenceError("NO_SUCCESSFUL_TRIALS", "All tuning trials failed.", {"failures": failures})
        task_type = outcomes[0].result["task_type"]
        ranked = sorted(outcomes, key=lambda item: -float(item.result["metrics"]["balanced_accuracy"]) if task_type == "classification" else float(item.result["metrics"]["rmse"]))
        trials = [{"rank": rank, "parameters": outcome.result["parameters"], "metrics": outcome.result["metrics"]} for rank, outcome in enumerate(ranked, 1)]
        return ({"algorithm": algorithm, "task_type": task_type, "trial_count": len(candidates), "successful_trials": len(outcomes), "best_parameters": ranked[0].result["parameters"], "trials": trials, "failures": failures, "execution_ms": round((started_timer() - started) * 1000, 3)}, ranked[0])

    def feature_selection(self, frame: pd.DataFrame, **params: Any) -> dict[str, Any]:
        started = started_timer()
        data = self._prepare(frame, params)
        baseline_algorithm = "random_forest_classifier" if data.task_type == "classification" else "random_forest_regressor"
        outcome = self.train(frame, **{**params, "algorithm": baseline_algorithm})
        _, X_validation, _, y_validation = self._split(data, validation_size=float(params.get("validation_size", 0.2)), random_state=int(params.get("random_state", self.budget.random_state)))
        scoring = "balanced_accuracy" if data.task_type == "classification" else "neg_root_mean_squared_error"
        importance = permutation_importance(outcome.model, X_validation, y_validation, n_repeats=min(5, int(params.get("repeats", 3))), random_state=self.budget.random_state, scoring=scoring, n_jobs=1)
        rows = sorted(
            [
                {"feature": feature, "importance_mean": float(mean), "importance_std": float(std)}
                for feature, mean, std in zip(data.features, importance.importances_mean, importance.importances_std)
            ],
            key=lambda item: (-item["importance_mean"], item["feature"]),
        )
        maximum = min(int(params.get("top_k", 20)), len(rows))
        selected = [item["feature"] for item in rows[:maximum] if item["importance_mean"] > 0]
        return {"task_type": data.task_type, "method": "holdout_permutation_importance", "selected_features": selected, "ranking": rows[:maximum], "warnings": data.warnings, "execution": finish_metadata(started, data.execution)}

    def explain(self, model: Pipeline, frame: pd.DataFrame, **params: Any) -> dict[str, Any]:
        started = started_timer()
        data = self._prepare(frame, params)
        scoring = "balanced_accuracy" if data.task_type == "classification" else "neg_root_mean_squared_error"
        importance = permutation_importance(model, data.X, data.y, n_repeats=min(10, int(params.get("repeats", 5))), random_state=self.budget.random_state, scoring=scoring, n_jobs=1)
        drivers = sorted(
            [{"feature": feature, "importance_mean": float(mean), "importance_std": float(std)} for feature, mean, std in zip(data.features, importance.importances_mean, importance.importances_std)],
            key=lambda item: (-item["importance_mean"], item["feature"]),
        )
        return {"method": "permutation_importance", "task_type": data.task_type, "drivers": drivers[: min(30, len(drivers))], "limitations": ["Importance is predictive association, not proof of causality.", "Correlated features may divide or mask importance."], "execution": finish_metadata(started, data.execution)}

    def threshold_analysis(self, model: Pipeline, frame: pd.DataFrame, **params: Any) -> dict[str, Any]:
        data = self._prepare(frame, params)
        if data.task_type != "classification" or not isinstance(data.y, pd.Series) or data.y.nunique() != 2:
            raise IntelligenceError("BINARY_CLASSIFICATION_REQUIRED", "Threshold analysis requires a binary classification target.")
        probability, classes = self._prediction_probability(model, data.X)
        if probability is None or classes is None:
            raise IntelligenceError("PROBABILITIES_UNAVAILABLE", "The trained model does not expose class probabilities.")
        positive = classes[-1]
        thresholds = [float(value) for value in params.get("thresholds", [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8])]
        rows = []
        for threshold in thresholds:
            if not 0 < threshold < 1:
                raise IntelligenceError("INVALID_THRESHOLD", "Thresholds must be between 0 and 1.")
            prediction = np.where(probability[:, -1] >= threshold, positive, classes[0])
            rows.append({"threshold": threshold, "balanced_accuracy": float(balanced_accuracy_score(data.y, prediction)), "precision": float(precision_score(data.y, prediction, pos_label=positive, zero_division=0)), "recall": float(recall_score(data.y, prediction, pos_label=positive, zero_division=0)), "f1": float(f1_score(data.y, prediction, pos_label=positive, zero_division=0))})
        best = sorted(rows, key=lambda item: (-item["f1"], abs(item["threshold"] - 0.5)))[0]
        return {"positive_class": str(positive), "best": best, "thresholds": rows}

    def calibration_analysis(self, model: Pipeline, frame: pd.DataFrame, **params: Any) -> dict[str, Any]:
        data = self._prepare(frame, params)
        if data.task_type != "classification" or not isinstance(data.y, pd.Series) or data.y.nunique() != 2:
            raise IntelligenceError("BINARY_CLASSIFICATION_REQUIRED", "Calibration analysis requires binary classification.")
        probability, classes = self._prediction_probability(model, data.X)
        if probability is None or classes is None:
            raise IntelligenceError("PROBABILITIES_UNAVAILABLE", "The trained model does not expose class probabilities.")
        binary = (data.y.to_numpy() == classes[-1]).astype(int)
        observed, predicted = calibration_curve(binary, probability[:, -1], n_bins=min(10, int(params.get("bins", 10))), strategy="quantile")
        ece = float(np.mean(np.abs(observed - predicted)))
        return {"positive_class": str(classes[-1]), "expected_calibration_error": ece, "curve": [{"mean_predicted_probability": float(x), "observed_positive_rate": float(y)} for x, y in zip(predicted, observed)]}

    def cross_validate(self, frame: pd.DataFrame, **params: Any) -> dict[str, Any]:
        started = started_timer()
        data = self._prepare(frame, params)
        algorithm = str(params.get("algorithm") or DEFAULT_CANDIDATES[data.task_type][1])
        spec, estimator = build_estimator(algorithm, task_type=data.task_type, random_state=self.budget.random_state, parameters=params.get("model_parameters"))
        pipeline = Pipeline([("preprocessor", build_preprocessor(data, non_negative=spec.non_negative_features)), ("model", estimator)])
        folds = min(10, max(2, int(params.get("folds", 5))))
        if data.task_type == "classification":
            splitter = StratifiedKFold(folds, shuffle=True, random_state=self.budget.random_state)
            scoring = str(params.get("scoring", "balanced_accuracy"))
        else:
            splitter = KFold(folds, shuffle=True, random_state=self.budget.random_state)
            scoring = str(params.get("scoring", "neg_root_mean_squared_error"))
        scores = cross_val_score(pipeline, data.X, data.y, cv=splitter, scoring=scoring, n_jobs=1)
        return {"algorithm": algorithm, "task_type": data.task_type, "scoring": scoring, "fold_scores": json_safe(scores), "mean_score": float(np.mean(scores)), "std_score": float(np.std(scores, ddof=1)) if len(scores) > 1 else 0.0, "execution": finish_metadata(started, data.execution)}
