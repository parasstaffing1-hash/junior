from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import MinMaxScaler, OneHotEncoder, StandardScaler

from app.core.intelligence.common import ExecutionBudget, IntelligenceError


@dataclass
class PreparedSupervisedData:
    frame: pd.DataFrame
    X: pd.DataFrame
    y: pd.Series | pd.DataFrame
    features: list[str]
    target_columns: list[str]
    task_type: str
    numeric_features: list[str]
    categorical_features: list[str]
    warnings: list[dict[str, Any]]
    execution: dict[str, Any]


def detect_task(target: pd.Series, requested: str = "auto") -> str:
    requested = str(requested).casefold()
    if requested not in {"auto", "classification", "regression"}:
        raise IntelligenceError("INVALID_TASK_TYPE", "task_type must be auto, classification, or regression.")
    if requested != "auto":
        return requested
    observed = target.dropna()
    if not pd.api.types.is_numeric_dtype(observed):
        return "classification"
    ratio = observed.nunique() / max(1, len(observed))
    return "classification" if observed.nunique() <= 20 and ratio <= 0.2 else "regression"


def prepare_supervised(
    frame: pd.DataFrame,
    *,
    target_column: str | list[str],
    feature_columns: list[str] | None,
    task_type: str,
    budget: ExecutionBudget,
    minimum_rows: int = 20,
) -> PreparedSupervisedData:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise IntelligenceError("EMPTY_DATASET", "Dataset must contain at least one row.")
    targets = [str(target_column)] if isinstance(target_column, str) else [str(column) for column in target_column]
    if not targets:
        raise IntelligenceError("TARGET_REQUIRED", "At least one target column is required.")
    missing_targets = [column for column in targets if column not in frame.columns]
    if missing_targets:
        raise IntelligenceError("TARGET_NOT_FOUND", "Target column does not exist.", {"columns": missing_targets})
    features = [str(column) for column in (feature_columns or [column for column in frame.columns if column not in targets])]
    if any(target in features for target in targets):
        raise IntelligenceError("TARGET_LEAKAGE", "Target columns cannot be included as features.")
    missing_features = [column for column in features if column not in frame.columns]
    if missing_features:
        raise IntelligenceError("FEATURE_NOT_FOUND", "One or more feature columns do not exist.", {"columns": missing_features})
    if not features:
        raise IntelligenceError("FEATURES_REQUIRED", "At least one feature column is required.")
    if len(features) > budget.max_features:
        raise IntelligenceError("FEATURE_LIMIT_EXCEEDED", "Selected features exceed the configured limit.", {"selected": len(features), "max_features": budget.max_features})
    work = frame[features + targets].copy()
    work = work.dropna(subset=targets).reset_index(drop=True)
    sampled = len(work) > budget.max_rows
    if sampled:
        work = work.sample(n=budget.max_rows, random_state=budget.random_state).reset_index(drop=True)
    unusable = [column for column in features if work[column].notna().sum() == 0]
    features = [column for column in features if column not in unusable]
    if not features:
        raise IntelligenceError("NO_USABLE_FEATURES", "All selected features are empty.")
    warnings: list[dict[str, Any]] = []
    if unusable:
        warnings.append({"code": "EMPTY_FEATURES_REMOVED", "columns": unusable})
    X = work[features].copy()
    for column in X.columns:
        if pd.api.types.is_datetime64_any_dtype(X[column]):
            X[column] = X[column].astype("string")
    high_cardinality = [column for column in X.columns if not pd.api.types.is_numeric_dtype(X[column]) and X[column].nunique(dropna=True) > 100]
    if high_cardinality:
        warnings.append({"code": "HIGH_CARDINALITY_CAPPED", "columns": high_cardinality, "max_categories": 50})
    if len(targets) == 1:
        y: pd.Series | pd.DataFrame = work[targets[0]].copy()
        detected = detect_task(y, task_type)
        if detected == "classification":
            y = y.astype(str)
            if y.nunique() < 2:
                raise IntelligenceError("CONSTANT_TARGET", "Classification target requires at least two classes.")
        else:
            y = pd.to_numeric(y, errors="coerce")
            valid = y.notna()
            X = X.loc[valid].reset_index(drop=True)
            y = y.loc[valid].astype(float).reset_index(drop=True)
    else:
        if task_type not in {"auto", "regression"}:
            raise IntelligenceError("MULTIOUTPUT_REGRESSION_REQUIRED", "Multiple targets are supported for regression only.")
        detected = "regression"
        converted = work[targets].apply(pd.to_numeric, errors="coerce")
        valid = converted.notna().all(axis=1)
        X = X.loc[valid].reset_index(drop=True)
        y = converted.loc[valid].astype(float).reset_index(drop=True)
    if len(X) < minimum_rows:
        raise IntelligenceError("INSUFFICIENT_TRAINING_DATA", f"At least {minimum_rows} usable rows are required.", {"rows": len(X)})
    numeric = [column for column in features if pd.api.types.is_numeric_dtype(X[column])]
    categorical = [column for column in features if column not in numeric]
    identifiers = [column for column in features if X[column].nunique(dropna=False) / len(X) >= 0.99]
    if identifiers:
        warnings.append({"code": "IDENTIFIER_LEAKAGE_RISK", "columns": identifiers})
    return PreparedSupervisedData(
        frame=work,
        X=X,
        y=y,
        features=features,
        target_columns=targets,
        task_type=detected,
        numeric_features=numeric,
        categorical_features=categorical,
        warnings=warnings,
        execution={"rows_scanned": len(frame), "rows_used": len(X), "feature_count": len(features), "sampled": sampled, "sample_size": len(X), "cache_hit": False},
    )


def build_preprocessor(data: PreparedSupervisedData, *, non_negative: bool = False) -> ColumnTransformer:
    transforms = []
    scaler = MinMaxScaler() if non_negative else StandardScaler()
    if data.numeric_features:
        transforms.append(("numeric", Pipeline([("imputer", SimpleImputer(strategy="median")), ("scaler", scaler)]), data.numeric_features))
    if data.categorical_features:
        transforms.append(
            (
                "categorical",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("encoder", OneHotEncoder(handle_unknown="ignore", max_categories=50, sparse_output=False)),
                    ]
                ),
                data.categorical_features,
            )
        )
    return ColumnTransformer(transforms, remainder="drop", verbose_feature_names_out=False)
