from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype


class AggregationError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class AggregationSummary:
    alias: str
    function: str
    column: str | None
    output_dtype: str
    null_results: int


@dataclass(frozen=True)
class AggregationResult:
    dataframe: pd.DataFrame
    group_by: list[str]
    include_null_keys: bool
    sort_groups: bool
    rows_before: int
    rows_after: int
    input_columns: int
    output_columns: int
    group_count: int
    excluded_rows: int
    aggregations: list[AggregationSummary]


SUPPORTED_FUNCTIONS = {"count", "sum", "avg", "median", "min", "max", "distinct"}
NUMERIC_FUNCTIONS = {"sum", "avg", "median"}
_NULL_SENTINEL = object()


def _python_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def _hashable(value: Any) -> Any:
    if value is None or pd.isna(value):
        return _NULL_SENTINEL
    if isinstance(value, pd.Timestamp):
        return ("timestamp", value.isoformat())
    try:
        hash(value)
        return value
    except Exception:
        return ("repr", repr(value))


def _validate_group_by(df: pd.DataFrame, group_by: Sequence[str] | None) -> list[str]:
    columns = list(group_by or [])
    if len(columns) != len(set(columns)):
        raise AggregationError(
            "DUPLICATE_GROUP_COLUMN",
            "Grouping columns may not contain duplicates.",
            {"columns": columns},
        )
    unknown = [c for c in columns if c not in df.columns]
    if unknown:
        raise AggregationError(
            "UNKNOWN_COLUMN",
            "One or more grouping columns do not exist.",
            {"columns": unknown},
        )
    return columns


def _validate_aggregations(
    df: pd.DataFrame,
    group_by: list[str],
    aggregations: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not aggregations:
        raise AggregationError(
            "AGGREGATIONS_REQUIRED",
            "At least one aggregation is required.",
        )

    aliases: list[str] = []
    normalized: list[dict[str, Any]] = []

    for index, spec in enumerate(aggregations):
        function = spec.get("function")
        column = spec.get("column")
        alias = spec.get("alias")
        drop_nulls = bool(spec.get("drop_nulls", True))

        if function not in SUPPORTED_FUNCTIONS:
            raise AggregationError(
                "UNSUPPORTED_AGGREGATION",
                f"Unsupported aggregation function: {function}",
                {"aggregation_index": index, "function": function},
            )

        if function != "count" and not column:
            raise AggregationError(
                "AGGREGATION_COLUMN_REQUIRED",
                f"{function} requires a column.",
                {"aggregation_index": index},
            )

        if column is not None and column not in df.columns:
            raise AggregationError(
                "UNKNOWN_COLUMN",
                "Aggregation column does not exist.",
                {"aggregation_index": index, "column": column},
            )

        if function in NUMERIC_FUNCTIONS and not is_numeric_dtype(df[column]):
            raise AggregationError(
                "INCOMPATIBLE_COLUMN_TYPE",
                f"{function} requires a numeric column.",
                {
                    "aggregation_index": index,
                    "column": column,
                    "dtype": str(df[column].dtype),
                },
            )

        if not alias:
            if function == "count" and column is None:
                alias = "row_count"
            else:
                alias = f"{column}_{function}"

        alias = str(alias).strip()
        if not alias:
            raise AggregationError(
                "ALIAS_REQUIRED",
                "Aggregation alias may not be empty.",
                {"aggregation_index": index},
            )

        if alias in group_by:
            raise AggregationError(
                "ALIAS_COLLISION",
                "Aggregation alias collides with a grouping column.",
                {"aggregation_index": index, "alias": alias},
            )

        if alias in aliases:
            raise AggregationError(
                "DUPLICATE_ALIAS",
                "Aggregation aliases must be unique.",
                {"aggregation_index": index, "alias": alias},
            )

        aliases.append(alias)
        normalized.append({
            "function": function,
            "column": column,
            "alias": alias,
            "drop_nulls": drop_nulls,
        })

    return normalized


def _build_groups(
    df: pd.DataFrame,
    group_by: list[str],
    include_null_keys: bool,
    sort_groups: bool,
) -> tuple[list[tuple[tuple[Any, ...], list[int]]], int]:
    if not group_by:
        return [(tuple(), [int(i) for i in df.index.tolist()])], 0

    buckets: dict[tuple[Any, ...], list[int]] = {}
    excluded_rows = 0

    for idx, row in df[group_by].iterrows():
        raw = tuple(row[c] for c in group_by)
        has_null = any(v is None or pd.isna(v) for v in raw)
        if has_null and not include_null_keys:
            excluded_rows += 1
            continue

        key = tuple(_hashable(v) for v in raw)
        buckets.setdefault(key, []).append(int(idx))

    groups = list(buckets.items())

    if sort_groups:
        def sortable(item):
            key, _ = item
            parts = []
            for value in key:
                if value is _NULL_SENTINEL:
                    parts.append((1, ""))
                else:
                    parts.append((0, str(value)))
            return tuple(parts)
        groups.sort(key=sortable)

    return groups, excluded_rows


def _aggregate_series(series: pd.Series, function: str, drop_nulls: bool):
    if function == "count":
        return int(series.count())
    if function == "sum":
        return series.sum(min_count=1)
    if function == "avg":
        return series.mean()
    if function == "median":
        return series.median()
    if function == "min":
        return series.min() if series.notna().any() else pd.NA
    if function == "max":
        return series.max() if series.notna().any() else pd.NA
    if function == "distinct":
        return int(series.nunique(dropna=drop_nulls))
    raise AssertionError("unreachable")


def aggregate_dataframe(
    df: pd.DataFrame,
    *,
    group_by: Sequence[str] | None,
    aggregations: list[dict[str, Any]] | None,
    include_null_keys: bool = True,
    sort_groups: bool = False,
) -> AggregationResult:
    if not isinstance(df, pd.DataFrame):
        raise AggregationError("INVALID_DATAFRAME", "Input must be a pandas DataFrame.")

    group_columns = _validate_group_by(df, group_by)
    specs = _validate_aggregations(df, group_columns, aggregations)

    groups, excluded_rows = _build_groups(
        df,
        group_by=group_columns,
        include_null_keys=include_null_keys,
        sort_groups=sort_groups,
    )

    rows: list[dict[str, Any]] = []

    for _, row_indexes in groups:
        subset = df.loc[row_indexes] if row_indexes else df.iloc[0:0]
        output: dict[str, Any] = {}

        if group_columns and row_indexes:
            first = df.loc[row_indexes[0], group_columns]
            for column in group_columns:
                output[column] = _python_value(first[column])

        for spec in specs:
            function = spec["function"]
            column = spec["column"]
            alias = spec["alias"]
            drop_nulls = spec["drop_nulls"]

            if function == "count" and column is None:
                value = int(len(subset))
            else:
                value = _aggregate_series(subset[column], function, drop_nulls)

            if isinstance(value, np.generic):
                value = value.item()
            output[alias] = value

        rows.append(output)

    output_columns = group_columns + [s["alias"] for s in specs]
    result_df = pd.DataFrame(rows, columns=output_columns)

    summaries: list[AggregationSummary] = []
    for spec in specs:
        alias = spec["alias"]
        summaries.append(
            AggregationSummary(
                alias=alias,
                function=spec["function"],
                column=spec["column"],
                output_dtype=str(result_df[alias].dtype) if alias in result_df.columns else "object",
                null_results=int(result_df[alias].isna().sum()) if alias in result_df.columns else 0,
            )
        )

    return AggregationResult(
        dataframe=result_df,
        group_by=group_columns,
        include_null_keys=include_null_keys,
        sort_groups=sort_groups,
        rows_before=len(df),
        rows_after=len(result_df),
        input_columns=len(df.columns),
        output_columns=len(result_df.columns),
        group_count=len(groups),
        excluded_rows=excluded_rows,
        aggregations=summaries,
    )
