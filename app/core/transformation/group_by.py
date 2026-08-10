from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
import pandas as pd


class GroupByError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class GroupExample:
    group_id: int
    key: dict[str, Any]
    size: int
    row_indexes: list[int]


@dataclass(frozen=True)
class GroupByResult:
    dataframe: pd.DataFrame
    group_by: list[str]
    include_null_keys: bool
    sort_groups: bool
    rows_before: int
    rows_after: int
    grouped_rows: int
    excluded_rows: int
    group_count: int
    min_group_size: int
    max_group_size: int
    mean_group_size: float
    median_group_size: float
    singleton_groups: int
    added_columns: list[str]
    groups: list[GroupExample]


_NULL_SENTINEL = object()


def _python_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
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


def _validate_group_columns(df: pd.DataFrame, group_by: Sequence[str] | None) -> list[str]:
    if not group_by:
        raise GroupByError("GROUP_COLUMNS_REQUIRED", "At least one grouping column is required.")
    columns = list(group_by)
    if len(columns) != len(set(columns)):
        raise GroupByError(
            "DUPLICATE_GROUP_COLUMN",
            "Grouping columns may not contain duplicates.",
            {"columns": columns},
        )
    unknown = [c for c in columns if c not in df.columns]
    if unknown:
        raise GroupByError(
            "UNKNOWN_COLUMN",
            "One or more grouping columns do not exist.",
            {"columns": unknown},
        )
    return columns


def build_groups(
    df: pd.DataFrame,
    *,
    group_by: Sequence[str] | None,
    include_null_keys: bool = True,
    sort_groups: bool = False,
) -> tuple[list[tuple[tuple[Any, ...], list[int]]], list[int]]:
    columns = _validate_group_columns(df, group_by)
    buckets: dict[tuple[Any, ...], list[int]] = {}
    excluded: list[int] = []

    for idx, row in df[columns].iterrows():
        raw = tuple(row[c] for c in columns)
        has_null = any(v is None or pd.isna(v) for v in raw)
        if has_null and not include_null_keys:
            excluded.append(int(idx))
            continue
        key = tuple(_hashable(v) for v in raw)
        buckets.setdefault(key, []).append(int(idx))

    groups = list(buckets.items())

    if sort_groups:
        def sortable(item):
            key, _ = item
            converted = []
            for value in key:
                if value is _NULL_SENTINEL:
                    converted.append((1, ""))
                else:
                    converted.append((0, str(value)))
            return tuple(converted)
        groups.sort(key=sortable)

    return groups, excluded


def analyze_groups(
    df: pd.DataFrame,
    *,
    group_by: Sequence[str] | None,
    include_null_keys: bool = True,
    sort_groups: bool = False,
    add_group_id: bool = False,
    group_id_column: str = "__group_id",
    add_group_size: bool = False,
    group_size_column: str = "__group_size",
    max_group_examples: int = 50,
) -> GroupByResult:
    if not isinstance(df, pd.DataFrame):
        raise GroupByError("INVALID_DATAFRAME", "Input must be a pandas DataFrame.")

    columns = _validate_group_columns(df, group_by)

    if max_group_examples <= 0:
        raise GroupByError("INVALID_GROUP_EXAMPLE_LIMIT", "max_group_examples must be greater than zero.")

    if add_group_id:
        if not group_id_column or not str(group_id_column).strip():
            raise GroupByError("GROUP_ID_COLUMN_REQUIRED", "group_id_column must be non-empty.")
        if group_id_column in df.columns:
            raise GroupByError(
                "ANNOTATION_COLUMN_EXISTS",
                "Group ID output column already exists.",
                {"column": group_id_column},
            )

    if add_group_size:
        if not group_size_column or not str(group_size_column).strip():
            raise GroupByError("GROUP_SIZE_COLUMN_REQUIRED", "group_size_column must be non-empty.")
        if group_size_column in df.columns:
            raise GroupByError(
                "ANNOTATION_COLUMN_EXISTS",
                "Group size output column already exists.",
                {"column": group_size_column},
            )

    if add_group_id and add_group_size and group_id_column == group_size_column:
        raise GroupByError(
            "ANNOTATION_COLUMN_COLLISION",
            "Group ID and group size output columns must be different.",
            {"column": group_id_column},
        )

    groups, excluded = build_groups(
        df,
        group_by=columns,
        include_null_keys=include_null_keys,
        sort_groups=sort_groups,
    )

    out = df.copy(deep=True)
    group_id_values = pd.Series(pd.NA, index=out.index, dtype="Int64")
    group_size_values = pd.Series(pd.NA, index=out.index, dtype="Int64")
    examples: list[GroupExample] = []
    sizes: list[int] = []

    for group_id, (hash_key, row_indexes) in enumerate(groups, start=1):
        size = len(row_indexes)
        sizes.append(size)
        group_id_values.loc[row_indexes] = group_id
        group_size_values.loc[row_indexes] = size

        if len(examples) < max_group_examples:
            first_row = df.loc[row_indexes[0], columns]
            key = {c: _python_value(first_row[c]) for c in columns}
            examples.append(
                GroupExample(
                    group_id=group_id,
                    key=key,
                    size=size,
                    row_indexes=row_indexes[:20],
                )
            )

    added_columns: list[str] = []
    if add_group_id:
        out[group_id_column] = group_id_values
        added_columns.append(group_id_column)
    if add_group_size:
        out[group_size_column] = group_size_values
        added_columns.append(group_size_column)

    if sizes:
        min_size = int(min(sizes))
        max_size = int(max(sizes))
        mean_size = round(float(np.mean(sizes)), 6)
        median_size = float(np.median(sizes))
        singleton_groups = int(sum(1 for x in sizes if x == 1))
    else:
        min_size = max_size = 0
        mean_size = median_size = 0.0
        singleton_groups = 0

    return GroupByResult(
        dataframe=out,
        group_by=columns,
        include_null_keys=include_null_keys,
        sort_groups=sort_groups,
        rows_before=len(df),
        rows_after=len(out),
        grouped_rows=sum(sizes),
        excluded_rows=len(excluded),
        group_count=len(groups),
        min_group_size=min_size,
        max_group_size=max_size,
        mean_group_size=mean_size,
        median_group_size=median_size,
        singleton_groups=singleton_groups,
        added_columns=added_columns,
        groups=examples,
    )
