from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


class SortingError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class SortKeySummary:
    priority: int
    column: str
    direction: str
    null_count: int


@dataclass(frozen=True)
class SortingResult:
    dataframe: pd.DataFrame
    rows_before: int
    rows_after: int
    columns_before: int
    columns_after: int
    sort_keys: list[SortKeySummary]
    nulls: str
    moved_rows: int
    original_row_order: list[int]
    sorted_original_row_indexes: list[int]


def apply_sort(
    df: pd.DataFrame,
    *,
    sort_by: list[dict[str, Any]],
    nulls: str = "last",
) -> SortingResult:
    if not isinstance(df, pd.DataFrame):
        raise SortingError("INVALID_DATAFRAME", "Input must be a pandas DataFrame.")

    if not sort_by:
        raise SortingError("SORT_KEYS_REQUIRED", "At least one sort key is required.")

    if nulls not in {"first", "last"}:
        raise SortingError(
            "INVALID_NULL_POSITION",
            "nulls must be first or last.",
            {"nulls": nulls},
        )

    columns = []
    ascending = []
    summaries = []

    for priority, spec in enumerate(sort_by):
        column = spec.get("column")
        direction = spec.get("direction", "asc")

        if not column:
            raise SortingError(
                "COLUMN_REQUIRED",
                "Each sort key requires a column.",
                {"priority": priority},
            )

        if column not in df.columns:
            raise SortingError(
                "UNKNOWN_COLUMN",
                "Sort column does not exist.",
                {"priority": priority, "column": column},
            )

        if column in columns:
            raise SortingError(
                "DUPLICATE_SORT_COLUMN",
                "A sort column may only be specified once.",
                {"column": column},
            )

        if direction not in {"asc", "desc"}:
            raise SortingError(
                "INVALID_DIRECTION",
                "direction must be asc or desc.",
                {"priority": priority, "direction": direction},
            )

        columns.append(column)
        ascending.append(direction == "asc")
        summaries.append(
            SortKeySummary(
                priority=priority,
                column=column,
                direction=direction,
                null_count=int(df[column].isna().sum()),
            )
        )

    working = df.copy(deep=True)
    marker = "__tool24_original_position__"
    if marker in working.columns:
        raise SortingError(
            "RESERVED_COLUMN_COLLISION",
            "Dataset contains a reserved internal column name.",
            {"column": marker},
        )

    working[marker] = range(len(working))

    try:
        sorted_df = working.sort_values(
            by=columns,
            ascending=ascending,
            na_position=nulls,
            kind="mergesort",
        )
    except TypeError as exc:
        raise SortingError(
            "INCOMPATIBLE_SORT_VALUES",
            "Column values could not be compared for sorting.",
            {"columns": columns},
        ) from exc

    sorted_original_indexes = [int(x) for x in sorted_df[marker].tolist()]
    moved_rows = sum(1 for position, original_position in enumerate(sorted_original_indexes) if position != original_position)

    final = sorted_df.drop(columns=[marker]).reset_index(drop=True)

    return SortingResult(
        dataframe=final,
        rows_before=len(df),
        rows_after=len(final),
        columns_before=len(df.columns),
        columns_after=len(final.columns),
        sort_keys=summaries,
        nulls=nulls,
        moved_rows=moved_rows,
        original_row_order=list(range(len(df))),
        sorted_original_row_indexes=sorted_original_indexes,
    )
