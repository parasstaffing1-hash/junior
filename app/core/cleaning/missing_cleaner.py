from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence

import pandas as pd
from pandas.api.types import is_numeric_dtype, is_bool_dtype, is_datetime64_any_dtype

from app.errors import AppError


Operation = Literal[
    "drop_rows_any",
    "drop_rows_selected",
    "drop_columns_any",
    "drop_columns_threshold",
    "fill_constant",
]


@dataclass(frozen=True)
class CleaningResult:
    dataframe: pd.DataFrame
    operation: str
    rows_before: int
    rows_after: int
    columns_before: int
    columns_after: int
    missing_cells_before: int
    missing_cells_after: int
    affected_rows: int
    affected_columns: int
    dropped_columns: list[str]
    changed_columns: list[str]


def missing_cell_count(df: pd.DataFrame) -> int:
    return int(df.isna().sum().sum())


def _validate_columns(df: pd.DataFrame, columns: Sequence[str] | None) -> list[str]:
    if not columns:
        return []
    unknown = [c for c in columns if c not in df.columns]
    if unknown:
        raise AppError(
            code="UNKNOWN_COLUMN",
            message="One or more selected columns do not exist.",
            details={"columns": unknown},
        )
    return list(dict.fromkeys(columns))


def _fill_value_is_compatible(series: pd.Series, fill_value: Any) -> bool:
    if is_numeric_dtype(series.dtype) and not is_bool_dtype(series.dtype):
        return isinstance(fill_value, (int, float)) and not isinstance(fill_value, bool)
    if is_bool_dtype(series.dtype):
        return isinstance(fill_value, bool)
    if is_datetime64_any_dtype(series.dtype):
        try:
            pd.to_datetime(fill_value)
            return True
        except Exception:
            return False
    return True


def apply_missing_value_operation(
    df: pd.DataFrame,
    *,
    operation: Operation,
    columns: Sequence[str] | None = None,
    fill_value: Any | None = None,
    threshold: float | None = None,
) -> CleaningResult:
    if not isinstance(df, pd.DataFrame):
        raise AppError("INVALID_DATAFRAME", "Input must be a pandas DataFrame.")

    selected = _validate_columns(df, columns)

    rows_before = len(df)
    columns_before = len(df.columns)
    missing_before = missing_cell_count(df)
    dropped_columns: list[str] = []
    changed_columns: list[str] = []
    cleaned = df.copy(deep=True)

    if operation == "drop_rows_any":
        cleaned = df.dropna(axis=0, how="any").copy()

    elif operation == "drop_rows_selected":
        if not selected:
            raise AppError(
                "COLUMNS_REQUIRED",
                "Selected columns are required for drop_rows_selected.",
            )
        cleaned = df.dropna(axis=0, subset=selected).copy()

    elif operation == "drop_columns_any":
        dropped_columns = [c for c in df.columns if df[c].isna().any()]
        cleaned = df.drop(columns=dropped_columns).copy()

    elif operation == "drop_columns_threshold":
        if threshold is None:
            raise AppError(
                "THRESHOLD_REQUIRED",
                "A missing-percentage threshold is required.",
            )
        if not 0 <= threshold <= 100:
            raise AppError(
                "INVALID_THRESHOLD",
                "Threshold must be between 0 and 100.",
                details={"threshold": threshold},
            )
        if len(df) == 0:
            dropped_columns = []
        else:
            dropped_columns = [
                c for c in df.columns if (float(df[c].isna().mean()) * 100) >= threshold
            ]
        cleaned = df.drop(columns=dropped_columns).copy()

    elif operation == "fill_constant":
        if fill_value is None:
            raise AppError(
                "FILL_VALUE_REQUIRED",
                "fill_value is required for fill_constant.",
            )
        target_columns = selected or list(df.columns)
        incompatible = [
            c for c in target_columns
            if df[c].isna().any() and not _fill_value_is_compatible(df[c], fill_value)
        ]
        if incompatible:
            raise AppError(
                "INCOMPATIBLE_FILL_VALUE",
                "The fill value is incompatible with one or more selected columns.",
                details={"columns": incompatible},
            )
        changed_columns = [c for c in target_columns if df[c].isna().any()]
        cleaned = df.copy(deep=True)
        for c in target_columns:
            if cleaned[c].isna().any():
                cleaned[c] = cleaned[c].fillna(fill_value)

    else:
        raise AppError(
            "UNSUPPORTED_OPERATION",
            f"Unsupported operation: {operation}",
        )

    cleaned = cleaned.reset_index(drop=True)
    missing_after = missing_cell_count(cleaned)

    return CleaningResult(
        dataframe=cleaned,
        operation=operation,
        rows_before=rows_before,
        rows_after=len(cleaned),
        columns_before=columns_before,
        columns_after=len(cleaned.columns),
        missing_cells_before=missing_before,
        missing_cells_after=missing_after,
        affected_rows=max(0, rows_before - len(cleaned)),
        affected_columns=max(len(dropped_columns), len(changed_columns)),
        dropped_columns=dropped_columns,
        changed_columns=changed_columns,
    )
