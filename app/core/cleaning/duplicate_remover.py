from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence

import pandas as pd


Mode = Literal["exact", "columns"]
KeepPolicy = Literal["first", "last", "none"]


class DuplicateRemovalError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class DuplicateGroup:
    group_id: int
    key: dict[str, Any]
    row_indexes: list[int]
    occurrences: int


@dataclass(frozen=True)
class DuplicateRemovalResult:
    dataframe: pd.DataFrame
    mode: str
    keep: str
    key_columns: list[str]
    rows_before: int
    rows_after: int
    removed_rows: int
    duplicate_rows_before: int
    duplicate_groups: int
    duplicate_percentage_before: float
    groups: list[DuplicateGroup]
    removed_row_indexes: list[int]
    removed_preview: list[dict[str, Any]]


def _validate_columns(df: pd.DataFrame, columns: Sequence[str] | None) -> list[str]:
    if not columns:
        raise DuplicateRemovalError(
            "COLUMNS_REQUIRED",
            "Selected columns are required for key-based duplicate removal.",
        )
    unknown = [c for c in columns if c not in df.columns]
    if unknown:
        raise DuplicateRemovalError(
            "UNKNOWN_COLUMN",
            "One or more selected columns do not exist.",
            {"columns": unknown},
        )
    return list(dict.fromkeys(columns))


def _key_columns(df: pd.DataFrame, mode: Mode, columns: Sequence[str] | None) -> list[str]:
    if mode == "exact":
        return list(df.columns)
    if mode == "columns":
        return _validate_columns(df, columns)
    raise DuplicateRemovalError("INVALID_MODE", f"Unsupported duplicate mode: {mode}")


def _normalize_key_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    try:
        if hasattr(value, "item"):
            return value.item()
    except Exception:
        pass
    return value


def _hashable_key_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return ("__NULL__",)
    if isinstance(value, pd.Timestamp):
        return ("__TIMESTAMP__", value.isoformat())
    try:
        hash(value)
        return value
    except Exception:
        return ("__REPR__", repr(value))


def _build_groups(df: pd.DataFrame, keys: list[str]) -> list[DuplicateGroup]:
    if df.empty:
        return []

    buckets: dict[tuple[Any, ...], list[int]] = {}
    display_values: dict[tuple[Any, ...], tuple[Any, ...]] = {}

    for idx, row in df[keys].iterrows():
        raw_values = tuple(row[col] for col in keys)
        bucket_key = tuple(_hashable_key_value(v) for v in raw_values)
        buckets.setdefault(bucket_key, []).append(int(idx))
        display_values.setdefault(bucket_key, raw_values)

    groups: list[DuplicateGroup] = []
    group_id = 1

    for bucket_key, indexes in buckets.items():
        if len(indexes) < 2:
            continue

        raw_values = display_values[bucket_key]
        key = {
            col: _normalize_key_value(value)
            for col, value in zip(keys, raw_values)
        }

        groups.append(
            DuplicateGroup(
                group_id=group_id,
                key=key,
                row_indexes=indexes,
                occurrences=len(indexes),
            )
        )
        group_id += 1

    return groups


def remove_duplicates(
    df: pd.DataFrame,
    *,
    mode: Mode,
    columns: Sequence[str] | None = None,
    keep: KeepPolicy = "first",
    max_groups: int = 100,
    max_removed_preview: int = 20,
) -> DuplicateRemovalResult:
    if not isinstance(df, pd.DataFrame):
        raise DuplicateRemovalError(
            "INVALID_DATAFRAME",
            "Input must be a pandas DataFrame.",
        )

    if keep not in {"first", "last", "none"}:
        raise DuplicateRemovalError(
            "INVALID_KEEP_POLICY",
            "keep must be first, last, or none.",
            {"keep": keep},
        )

    keys = _key_columns(df, mode, columns)

    duplicate_mask_all = df.duplicated(subset=keys, keep=False)
    duplicate_rows_before = int(duplicate_mask_all.sum())

    groups_all = _build_groups(df, keys)
    duplicate_groups = len(groups_all)

    if keep == "first":
        remove_mask = df.duplicated(subset=keys, keep="first")
    elif keep == "last":
        remove_mask = df.duplicated(subset=keys, keep="last")
    else:
        remove_mask = duplicate_mask_all

    removed_indexes = [int(x) for x in df.index[remove_mask].tolist()]
    cleaned = df.loc[~remove_mask].copy().reset_index(drop=True)

    duplicate_percentage_before = (
        round((duplicate_rows_before / len(df)) * 100, 6)
        if len(df)
        else 0.0
    )

    removed_preview_df = df.loc[remove_mask].head(max_removed_preview)
    removed_preview = removed_preview_df.where(
        removed_preview_df.notna(), None
    ).to_dict(orient="records")

    return DuplicateRemovalResult(
        dataframe=cleaned,
        mode=mode,
        keep=keep,
        key_columns=keys,
        rows_before=len(df),
        rows_after=len(cleaned),
        removed_rows=int(remove_mask.sum()),
        duplicate_rows_before=duplicate_rows_before,
        duplicate_groups=duplicate_groups,
        duplicate_percentage_before=duplicate_percentage_before,
        groups=groups_all[:max_groups],
        removed_row_indexes=removed_indexes,
        removed_preview=removed_preview,
    )
