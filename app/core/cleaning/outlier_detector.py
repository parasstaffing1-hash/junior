from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype


Method = Literal["iqr", "zscore", "percentile"]


class OutlierDetectionError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class OutlierRow:
    row_index: int
    column: str
    value: float
    score: float | None
    direction: str


@dataclass(frozen=True)
class ColumnOutlierSummary:
    column: str
    method: str
    non_null_count: int
    null_count: int
    outlier_count: int
    outlier_percentage: float
    lower_bound: float | None
    upper_bound: float | None
    center: float | None
    scale: float | None
    q1: float | None
    q3: float | None
    iqr: float | None
    lower_percentile_value: float | None
    upper_percentile_value: float | None


@dataclass(frozen=True)
class OutlierDetectionResult:
    method: str
    rows: int
    columns_analyzed: list[str]
    total_outlier_cells: int
    rows_with_any_outlier: int
    row_outlier_percentage: float
    columns: list[ColumnOutlierSummary]
    flagged_rows: list[OutlierRow]
    row_flags: list[dict[str, Any]]


def _validate_columns(df: pd.DataFrame, columns: Sequence[str] | None) -> list[str]:
    if not columns:
        raise OutlierDetectionError(
            "COLUMNS_REQUIRED",
            "At least one numeric column must be selected.",
        )

    selected = list(dict.fromkeys(columns))
    unknown = [c for c in selected if c not in df.columns]
    if unknown:
        raise OutlierDetectionError(
            "UNKNOWN_COLUMN",
            "One or more selected columns do not exist.",
            {"columns": unknown},
        )

    non_numeric = [c for c in selected if not is_numeric_dtype(df[c])]
    if non_numeric:
        raise OutlierDetectionError(
            "INCOMPATIBLE_COLUMN_TYPE",
            "Outlier detection requires numeric columns.",
            {"columns": non_numeric, "dtypes": {c: str(df[c].dtype) for c in non_numeric}},
        )

    return selected


def _empty_summary(column: str, method: str, null_count: int) -> ColumnOutlierSummary:
    return ColumnOutlierSummary(
        column=column,
        method=method,
        non_null_count=0,
        null_count=null_count,
        outlier_count=0,
        outlier_percentage=0.0,
        lower_bound=None,
        upper_bound=None,
        center=None,
        scale=None,
        q1=None,
        q3=None,
        iqr=None,
        lower_percentile_value=None,
        upper_percentile_value=None,
    )


def detect_outliers(
    df: pd.DataFrame,
    *,
    method: Method,
    columns: Sequence[str] | None,
    iqr_multiplier: float = 1.5,
    z_threshold: float = 3.0,
    lower_percentile: float = 1.0,
    upper_percentile: float = 99.0,
    max_flagged_rows: int = 100,
) -> OutlierDetectionResult:
    if not isinstance(df, pd.DataFrame):
        raise OutlierDetectionError(
            "INVALID_DATAFRAME",
            "Input must be a pandas DataFrame.",
        )

    selected = _validate_columns(df, columns)

    if method not in {"iqr", "zscore", "percentile"}:
        raise OutlierDetectionError(
            "INVALID_METHOD",
            f"Unsupported outlier method: {method}",
        )

    if iqr_multiplier <= 0:
        raise OutlierDetectionError(
            "INVALID_IQR_MULTIPLIER",
            "iqr_multiplier must be greater than zero.",
        )

    if z_threshold <= 0:
        raise OutlierDetectionError(
            "INVALID_Z_THRESHOLD",
            "z_threshold must be greater than zero.",
        )

    if not (0 <= lower_percentile < upper_percentile <= 100):
        raise OutlierDetectionError(
            "INVALID_PERCENTILE_RANGE",
            "Percentiles must satisfy 0 <= lower < upper <= 100.",
            {
                "lower_percentile": lower_percentile,
                "upper_percentile": upper_percentile,
            },
        )

    if max_flagged_rows <= 0:
        raise OutlierDetectionError(
            "INVALID_MAX_FLAGGED_ROWS",
            "max_flagged_rows must be greater than zero.",
        )

    summaries: list[ColumnOutlierSummary] = []
    flagged_rows: list[OutlierRow] = []
    row_flag_map: dict[int, list[str]] = {}
    total_outlier_cells = 0

    for column in selected:
        series = df[column]
        non_null = series.dropna().astype(float)
        null_count = int(series.isna().sum())

        if non_null.empty:
            summaries.append(_empty_summary(column, method, null_count))
            continue

        outlier_mask = pd.Series(False, index=df.index)
        score_series = pd.Series(np.nan, index=df.index, dtype="float64")
        lower_bound = upper_bound = center = scale = None
        q1 = q3 = iqr = None
        low_pct_value = high_pct_value = None

        if method == "iqr":
            q1 = float(non_null.quantile(0.25))
            q3 = float(non_null.quantile(0.75))
            iqr = float(q3 - q1)
            lower_bound = float(q1 - iqr_multiplier * iqr)
            upper_bound = float(q3 + iqr_multiplier * iqr)
            outlier_mask = series.notna() & (
                (series.astype(float) < lower_bound) |
                (series.astype(float) > upper_bound)
            )

        elif method == "zscore":
            center = float(non_null.mean())
            scale = float(non_null.std(ddof=0))
            if scale == 0 or np.isnan(scale):
                outlier_mask = pd.Series(False, index=df.index)
            else:
                score_series = (series.astype(float) - center) / scale
                outlier_mask = series.notna() & (score_series.abs() > z_threshold)

        else:
            low_pct_value = float(non_null.quantile(lower_percentile / 100.0))
            high_pct_value = float(non_null.quantile(upper_percentile / 100.0))
            lower_bound = low_pct_value
            upper_bound = high_pct_value
            outlier_mask = series.notna() & (
                (series.astype(float) < lower_bound) |
                (series.astype(float) > upper_bound)
            )

        outlier_count = int(outlier_mask.sum())
        total_outlier_cells += outlier_count
        non_null_count = int(series.notna().sum())

        for idx in df.index[outlier_mask]:
            value = float(series.loc[idx])
            if method == "zscore" and not pd.isna(score_series.loc[idx]):
                score = float(score_series.loc[idx])
                direction = "high" if score > 0 else "low"
            else:
                score = None
                if lower_bound is not None and value < lower_bound:
                    direction = "low"
                else:
                    direction = "high"

            row_flag_map.setdefault(int(idx), []).append(column)

            if len(flagged_rows) < max_flagged_rows:
                flagged_rows.append(
                    OutlierRow(
                        row_index=int(idx),
                        column=column,
                        value=value,
                        score=score,
                        direction=direction,
                    )
                )

        summaries.append(
            ColumnOutlierSummary(
                column=column,
                method=method,
                non_null_count=non_null_count,
                null_count=null_count,
                outlier_count=outlier_count,
                outlier_percentage=round(
                    (outlier_count / non_null_count) * 100, 6
                ) if non_null_count else 0.0,
                lower_bound=lower_bound,
                upper_bound=upper_bound,
                center=center,
                scale=scale,
                q1=q1,
                q3=q3,
                iqr=iqr,
                lower_percentile_value=low_pct_value,
                upper_percentile_value=high_pct_value,
            )
        )

    row_flags = [
        {
            "row_index": row_index,
            "outlier_columns": columns_,
            "outlier_column_count": len(columns_),
        }
        for row_index, columns_ in sorted(row_flag_map.items())
    ]

    rows_with_any = len(row_flag_map)

    return OutlierDetectionResult(
        method=method,
        rows=len(df),
        columns_analyzed=selected,
        total_outlier_cells=total_outlier_cells,
        rows_with_any_outlier=rows_with_any,
        row_outlier_percentage=round(
            (rows_with_any / len(df)) * 100, 6
        ) if len(df) else 0.0,
        columns=summaries,
        flagged_rows=flagged_rows,
        row_flags=row_flags,
    )
