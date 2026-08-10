from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype


Method = Literal["iqr", "zscore", "percentile"]
Action = Literal[
    "remove_rows",
    "cap",
    "winsorize",
    "replace_constant",
    "replace_median",
    "replace_mean",
    "retain_and_flag",
]


class OutlierTreatmentError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class ColumnTreatmentSummary:
    column: str
    outlier_count: int
    lower_bound: float | None
    upper_bound: float | None
    replacement_value: float | None
    treated_cells: int
    flag_column: str | None


@dataclass(frozen=True)
class OutlierTreatmentResult:
    dataframe: pd.DataFrame
    method: str
    action: str
    columns_analyzed: list[str]
    rows_before: int
    rows_after: int
    columns_before: int
    columns_after: int
    outlier_cells_before: int
    rows_with_outliers_before: int
    removed_rows: int
    treated_cells: int
    added_flag_columns: list[str]
    columns: list[ColumnTreatmentSummary]


def _validate_columns(df: pd.DataFrame, columns: Sequence[str] | None) -> list[str]:
    if not columns:
        raise OutlierTreatmentError(
            "COLUMNS_REQUIRED",
            "At least one numeric column must be selected.",
        )

    selected = list(dict.fromkeys(columns))
    unknown = [c for c in selected if c not in df.columns]
    if unknown:
        raise OutlierTreatmentError(
            "UNKNOWN_COLUMN",
            "One or more selected columns do not exist.",
            {"columns": unknown},
        )

    non_numeric = [c for c in selected if not is_numeric_dtype(df[c])]
    if non_numeric:
        raise OutlierTreatmentError(
            "INCOMPATIBLE_COLUMN_TYPE",
            "Outlier treatment requires numeric columns.",
            {"columns": non_numeric, "dtypes": {c: str(df[c].dtype) for c in non_numeric}},
        )

    return selected


def _detect_mask(
    series: pd.Series,
    *,
    method: Method,
    iqr_multiplier: float,
    z_threshold: float,
    lower_percentile: float,
    upper_percentile: float,
) -> tuple[pd.Series, float | None, float | None]:
    non_null = series.dropna().astype(float)
    mask = pd.Series(False, index=series.index)

    if non_null.empty:
        return mask, None, None

    if method == "iqr":
        q1 = float(non_null.quantile(0.25))
        q3 = float(non_null.quantile(0.75))
        iqr = q3 - q1
        low = float(q1 - iqr_multiplier * iqr)
        high = float(q3 + iqr_multiplier * iqr)
        mask = series.notna() & (
            (series.astype(float) < low) |
            (series.astype(float) > high)
        )
        return mask, low, high

    if method == "zscore":
        mean = float(non_null.mean())
        std = float(non_null.std(ddof=0))
        if std == 0 or np.isnan(std):
            return mask, None, None
        z = (series.astype(float) - mean) / std
        mask = series.notna() & (z.abs() > z_threshold)
        low = mean - z_threshold * std
        high = mean + z_threshold * std
        return mask, float(low), float(high)

    if method == "percentile":
        low = float(non_null.quantile(lower_percentile / 100.0))
        high = float(non_null.quantile(upper_percentile / 100.0))
        mask = series.notna() & (
            (series.astype(float) < low) |
            (series.astype(float) > high)
        )
        return mask, low, high

    raise OutlierTreatmentError(
        "INVALID_METHOD",
        f"Unsupported outlier method: {method}",
    )


def apply_outlier_treatment(
    df: pd.DataFrame,
    *,
    method: Method,
    columns: Sequence[str] | None,
    action: Action,
    iqr_multiplier: float = 1.5,
    z_threshold: float = 3.0,
    lower_percentile: float = 1.0,
    upper_percentile: float = 99.0,
    replacement_constant: float | None = None,
    flag_suffix: str = "__outlier",
) -> OutlierTreatmentResult:
    if not isinstance(df, pd.DataFrame):
        raise OutlierTreatmentError(
            "INVALID_DATAFRAME",
            "Input must be a pandas DataFrame.",
        )

    selected = _validate_columns(df, columns)

    if method not in {"iqr", "zscore", "percentile"}:
        raise OutlierTreatmentError("INVALID_METHOD", f"Unsupported method: {method}")

    if action not in {
        "remove_rows",
        "cap",
        "winsorize",
        "replace_constant",
        "replace_median",
        "replace_mean",
        "retain_and_flag",
    }:
        raise OutlierTreatmentError("INVALID_ACTION", f"Unsupported action: {action}")

    if iqr_multiplier <= 0:
        raise OutlierTreatmentError(
            "INVALID_IQR_MULTIPLIER",
            "iqr_multiplier must be greater than zero.",
        )

    if z_threshold <= 0:
        raise OutlierTreatmentError(
            "INVALID_Z_THRESHOLD",
            "z_threshold must be greater than zero.",
        )

    if not (0 <= lower_percentile < upper_percentile <= 100):
        raise OutlierTreatmentError(
            "INVALID_PERCENTILE_RANGE",
            "Percentiles must satisfy 0 <= lower < upper <= 100.",
        )

    if action == "replace_constant" and replacement_constant is None:
        raise OutlierTreatmentError(
            "REPLACEMENT_CONSTANT_REQUIRED",
            "replacement_constant is required for replace_constant.",
        )

    if action == "retain_and_flag" and not flag_suffix:
        raise OutlierTreatmentError(
            "FLAG_SUFFIX_REQUIRED",
            "flag_suffix may not be empty.",
        )

    cleaned = df.copy(deep=True)
    masks: dict[str, pd.Series] = {}
    bounds: dict[str, tuple[float | None, float | None]] = {}
    row_any = pd.Series(False, index=df.index)

    for column in selected:
        mask, low, high = _detect_mask(
            df[column],
            method=method,
            iqr_multiplier=iqr_multiplier,
            z_threshold=z_threshold,
            lower_percentile=lower_percentile,
            upper_percentile=upper_percentile,
        )
        masks[column] = mask
        bounds[column] = (low, high)
        row_any = row_any | mask

    outlier_cells_before = int(sum(int(mask.sum()) for mask in masks.values()))
    rows_with_outliers_before = int(row_any.sum())
    removed_rows = 0
    treated_cells = 0
    added_flag_columns: list[str] = []
    summaries: list[ColumnTreatmentSummary] = []

    if action == "remove_rows":
        removed_rows = rows_with_outliers_before
        cleaned = df.loc[~row_any].copy().reset_index(drop=True)
        for column in selected:
            low, high = bounds[column]
            summaries.append(
                ColumnTreatmentSummary(
                    column=column,
                    outlier_count=int(masks[column].sum()),
                    lower_bound=low,
                    upper_bound=high,
                    replacement_value=None,
                    treated_cells=int(masks[column].sum()),
                    flag_column=None,
                )
            )
        treated_cells = outlier_cells_before

    elif action in {"cap", "winsorize"}:
        for column in selected:
            mask = masks[column]
            low, high = bounds[column]
            count = int(mask.sum())
            if low is not None and high is not None and count:
                series = cleaned[column].astype(float)
                cleaned[column] = series.clip(lower=low, upper=high)
            treated_cells += count
            summaries.append(
                ColumnTreatmentSummary(
                    column=column,
                    outlier_count=count,
                    lower_bound=low,
                    upper_bound=high,
                    replacement_value=None,
                    treated_cells=count,
                    flag_column=None,
                )
            )

    elif action in {"replace_constant", "replace_median", "replace_mean"}:
        for column in selected:
            mask = masks[column]
            count = int(mask.sum())
            non_outliers = df.loc[~mask, column].dropna().astype(float)

            if action == "replace_constant":
                replacement = float(replacement_constant)
            elif action == "replace_median":
                if non_outliers.empty:
                    raise OutlierTreatmentError(
                        "NO_REPLACEMENT_VALUE",
                        "Median replacement could not be calculated.",
                        {"column": column},
                    )
                replacement = float(non_outliers.median())
            else:
                if non_outliers.empty:
                    raise OutlierTreatmentError(
                        "NO_REPLACEMENT_VALUE",
                        "Mean replacement could not be calculated.",
                        {"column": column},
                    )
                replacement = float(non_outliers.mean())

            if count:
                cleaned.loc[mask, column] = replacement

            treated_cells += count
            low, high = bounds[column]
            summaries.append(
                ColumnTreatmentSummary(
                    column=column,
                    outlier_count=count,
                    lower_bound=low,
                    upper_bound=high,
                    replacement_value=replacement,
                    treated_cells=count,
                    flag_column=None,
                )
            )

    else:  # retain_and_flag
        for column in selected:
            flag_column = f"{column}{flag_suffix}"
            if flag_column in cleaned.columns:
                raise OutlierTreatmentError(
                    "FLAG_COLUMN_EXISTS",
                    "A generated flag column already exists.",
                    {"column": flag_column},
                )
            cleaned[flag_column] = masks[column].astype(bool)
            added_flag_columns.append(flag_column)
            count = int(masks[column].sum())
            low, high = bounds[column]
            summaries.append(
                ColumnTreatmentSummary(
                    column=column,
                    outlier_count=count,
                    lower_bound=low,
                    upper_bound=high,
                    replacement_value=None,
                    treated_cells=0,
                    flag_column=flag_column,
                )
            )

    return OutlierTreatmentResult(
        dataframe=cleaned,
        method=method,
        action=action,
        columns_analyzed=selected,
        rows_before=len(df),
        rows_after=len(cleaned),
        columns_before=len(df.columns),
        columns_after=len(cleaned.columns),
        outlier_cells_before=outlier_cells_before,
        rows_with_outliers_before=rows_with_outliers_before,
        removed_rows=removed_rows,
        treated_cells=treated_cells,
        added_flag_columns=added_flag_columns,
        columns=summaries,
    )
