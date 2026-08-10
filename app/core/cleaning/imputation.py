from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence, Any

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype


Strategy = Literal[
    "mean",
    "median",
    "mode",
    "forward_fill",
    "backward_fill",
    "interpolate_linear",
]


class ImputationError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class ColumnImputationSummary:
    column: str
    missing_before: int
    missing_after: int
    filled_count: int
    strategy: str
    statistic_value: Any | None


@dataclass(frozen=True)
class ImputationResult:
    dataframe: pd.DataFrame
    strategy: str
    rows_before: int
    rows_after: int
    columns_before: int
    columns_after: int
    missing_cells_before: int
    missing_cells_after: int
    total_filled: int
    columns: list[ColumnImputationSummary]


def missing_cell_count(df: pd.DataFrame) -> int:
    return int(df.isna().sum().sum())


def _validate_columns(df: pd.DataFrame, columns: Sequence[str] | None) -> list[str]:
    if not columns:
        raise ImputationError(
            "COLUMNS_REQUIRED",
            "At least one column must be selected for imputation.",
        )
    unknown = [c for c in columns if c not in df.columns]
    if unknown:
        raise ImputationError(
            "UNKNOWN_COLUMN",
            "One or more selected columns do not exist.",
            {"columns": unknown},
        )
    return list(dict.fromkeys(columns))


def _safe_mode(series: pd.Series):
    non_null = series.dropna()
    if non_null.empty:
        raise ImputationError(
            "NO_IMPUTATION_VALUE",
            "Mode cannot be calculated for a column containing only missing values.",
            {"column": series.name},
        )
    modes = non_null.mode(dropna=True)
    if modes.empty:
        raise ImputationError(
            "NO_IMPUTATION_VALUE",
            "Mode could not be determined.",
            {"column": series.name},
        )
    return modes.iloc[0]


def _numeric_stat(series: pd.Series, strategy: str):
    if not is_numeric_dtype(series):
        raise ImputationError(
            "INCOMPATIBLE_COLUMN_TYPE",
            f"{strategy} imputation requires a numeric column.",
            {"column": series.name, "dtype": str(series.dtype)},
        )
    non_null = series.dropna()
    if non_null.empty:
        raise ImputationError(
            "NO_IMPUTATION_VALUE",
            f"{strategy} cannot be calculated for an all-null column.",
            {"column": series.name},
        )
    if strategy == "mean":
        return float(non_null.mean())
    if strategy == "median":
        return float(non_null.median())
    raise RuntimeError("unsupported stat")


def apply_imputation(
    df: pd.DataFrame,
    *,
    strategy: Strategy,
    columns: Sequence[str] | None,
    limit: int | None = None,
) -> ImputationResult:
    if not isinstance(df, pd.DataFrame):
        raise ImputationError("INVALID_DATAFRAME", "Input must be a pandas DataFrame.")

    if limit is not None and limit <= 0:
        raise ImputationError(
            "INVALID_LIMIT",
            "limit must be a positive integer when provided.",
            {"limit": limit},
        )

    selected = _validate_columns(df, columns)
    cleaned = df.copy(deep=True)

    rows_before = len(df)
    columns_before = len(df.columns)
    missing_before = missing_cell_count(df)
    summaries: list[ColumnImputationSummary] = []

    for column in selected:
        series = cleaned[column]
        before = int(series.isna().sum())
        statistic_value = None

        if before == 0:
            summaries.append(
                ColumnImputationSummary(
                    column=column,
                    missing_before=0,
                    missing_after=0,
                    filled_count=0,
                    strategy=strategy,
                    statistic_value=None,
                )
            )
            continue

        if strategy in {"mean", "median"}:
            statistic_value = _numeric_stat(series, strategy)
            cleaned[column] = series.fillna(statistic_value)

        elif strategy == "mode":
            statistic_value = _safe_mode(series)
            cleaned[column] = series.fillna(statistic_value)

        elif strategy == "forward_fill":
            cleaned[column] = series.ffill(limit=limit)

        elif strategy == "backward_fill":
            cleaned[column] = series.bfill(limit=limit)

        elif strategy == "interpolate_linear":
            if not is_numeric_dtype(series):
                raise ImputationError(
                    "INCOMPATIBLE_COLUMN_TYPE",
                    "Linear interpolation requires numeric columns.",
                    {"column": column, "dtype": str(series.dtype)},
                )
            cleaned[column] = series.interpolate(
                method="linear",
                limit=limit,
                limit_direction="both",
            )

        else:
            raise ImputationError(
                "UNSUPPORTED_STRATEGY",
                f"Unsupported strategy: {strategy}",
            )

        after = int(cleaned[column].isna().sum())
        summaries.append(
            ColumnImputationSummary(
                column=column,
                missing_before=before,
                missing_after=after,
                filled_count=before - after,
                strategy=strategy,
                statistic_value=statistic_value,
            )
        )

    missing_after = missing_cell_count(cleaned)

    return ImputationResult(
        dataframe=cleaned,
        strategy=strategy,
        rows_before=rows_before,
        rows_after=len(cleaned),
        columns_before=columns_before,
        columns_after=len(cleaned.columns),
        missing_cells_before=missing_before,
        missing_cells_after=missing_after,
        total_filled=missing_before - missing_after,
        columns=summaries,
    )
