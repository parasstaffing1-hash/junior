from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import math
from time import perf_counter
from typing import Any

import numpy as np
import pandas as pd


class IntelligenceError(ValueError):
    """Stable error contract for all data-intelligence services."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None, *, status_code: int = 422):
        self.code = code
        self.message = message
        self.details = details or {}
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class ExecutionBudget:
    """Hard bounds shared by algorithms exposed through request parameters."""

    max_rows: int = 20_000
    max_features: int = 100
    max_trials: int = 20
    timeout_seconds: int = 120
    random_state: int = 42

    def __post_init__(self) -> None:
        if not 100 <= self.max_rows <= 250_000:
            raise IntelligenceError("INVALID_ROW_LIMIT", "max_rows must be between 100 and 250000.")
        if not 1 <= self.max_features <= 500:
            raise IntelligenceError("INVALID_FEATURE_LIMIT", "max_features must be between 1 and 500.")
        if not 1 <= self.max_trials <= 100:
            raise IntelligenceError("INVALID_TRIAL_LIMIT", "max_trials must be between 1 and 100.")
        if not 1 <= self.timeout_seconds <= 3600:
            raise IntelligenceError("INVALID_TIMEOUT", "timeout_seconds must be between 1 and 3600.")


def json_safe(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, np.ndarray):
        return [json_safe(item) for item in value.tolist()]
    if isinstance(value, pd.Series):
        return [json_safe(item) for item in value.tolist()]
    if isinstance(value, pd.DataFrame):
        safe = value.astype(object).where(pd.notna(value), None)
        return json_safe(safe.to_dict(orient="records"))
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return value


def dataframe_fingerprint(frame: pd.DataFrame) -> str:
    """Stable schema/content summary without serializing the whole dataset."""

    payload = {
        "rows": int(len(frame)),
        "columns": [
            {
                "name": str(column),
                "dtype": str(frame[column].dtype),
                "nulls": int(frame[column].isna().sum()),
                "unique": int(frame[column].nunique(dropna=True)),
            }
            for column in frame.columns
        ],
        "sample_hash": hashlib.sha256(
            pd.util.hash_pandas_object(frame.head(256), index=True).values.tobytes()
        ).hexdigest(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def bounded_frame(
    frame: pd.DataFrame,
    *,
    budget: ExecutionBudget,
    required_columns: list[str] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise IntelligenceError("EMPTY_DATASET", "Dataset must contain at least one row.")
    missing = sorted(set(required_columns or []) - set(frame.columns))
    if missing:
        raise IntelligenceError("COLUMN_NOT_FOUND", "One or more required columns do not exist.", {"columns": missing})
    if len(frame.columns) > budget.max_features and required_columns is None:
        raise IntelligenceError(
            "FEATURE_LIMIT_EXCEEDED",
            "Dataset has more columns than the configured feature limit.",
            {"columns": len(frame.columns), "max_features": budget.max_features},
        )
    sampled = len(frame) > budget.max_rows
    if sampled:
        working = frame.sample(n=budget.max_rows, random_state=budget.random_state).sort_index().copy()
    else:
        working = frame.copy()
    metadata = {
        "rows_scanned": int(len(frame)),
        "rows_used": int(len(working)),
        "feature_count": int(len(working.columns)),
        "sampled": sampled,
        "sample_size": int(len(working)),
        "cache_hit": False,
    }
    return working, metadata


def started_timer() -> float:
    return perf_counter()


def finish_metadata(started: float, metadata: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        **metadata,
        **json_safe(extra),
        "execution_ms": round((perf_counter() - started) * 1000, 3),
    }
