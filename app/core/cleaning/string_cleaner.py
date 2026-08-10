from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence
import re
import unicodedata

import pandas as pd
from pandas.api.types import is_object_dtype, is_string_dtype


OperationType = Literal[
    "trim",
    "collapse_whitespace",
    "lowercase",
    "uppercase",
    "titlecase",
    "normalize_unicode",
    "empty_to_null",
    "remove_special_characters",
    "replace_substring",
]


class StringCleaningError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class ColumnSummary:
    column: str
    changed_cells: int
    nulls_before: int
    nulls_after: int


@dataclass(frozen=True)
class StringCleaningResult:
    dataframe: pd.DataFrame
    rows_before: int
    rows_after: int
    columns_before: int
    columns_after: int
    total_changed_cells: int
    columns: list[ColumnSummary]


def _validate_columns(df: pd.DataFrame, columns: Sequence[str] | None) -> list[str]:
    if not columns:
        raise StringCleaningError(
            "COLUMNS_REQUIRED",
            "At least one column must be selected.",
        )
    unknown = [c for c in columns if c not in df.columns]
    if unknown:
        raise StringCleaningError(
            "UNKNOWN_COLUMN",
            "One or more selected columns do not exist.",
            {"columns": unknown},
        )
    return list(dict.fromkeys(columns))


def _ensure_string_compatible(df: pd.DataFrame, columns: list[str]) -> None:
    bad = [
        c for c in columns
        if not (is_object_dtype(df[c].dtype) or is_string_dtype(df[c].dtype))
    ]
    if bad:
        raise StringCleaningError(
            "INCOMPATIBLE_COLUMN_TYPE",
            "String cleaning requires string-compatible columns.",
            {"columns": bad, "dtypes": {c: str(df[c].dtype) for c in bad}},
        )


def _normalize_unicode(value: str, form: str = "NFKC") -> str:
    return unicodedata.normalize(form, value)


def _remove_special_characters(value: str, pattern: str | None = None) -> str:
    regex = pattern or r"[^\w\s\-.,@/]"
    return re.sub(regex, "", value, flags=re.UNICODE)


def _apply_one(series: pd.Series, op: dict[str, Any]) -> pd.Series:
    op_type = op["type"]

    def transform(value):
        if pd.isna(value):
            return value
        text = str(value)

        if op_type == "trim":
            return text.strip()
        if op_type == "collapse_whitespace":
            return re.sub(r"\s+", " ", text).strip()
        if op_type == "lowercase":
            return text.lower()
        if op_type == "uppercase":
            return text.upper()
        if op_type == "titlecase":
            return text.title()
        if op_type == "normalize_unicode":
            return _normalize_unicode(text, op.get("form", "NFKC"))
        if op_type == "empty_to_null":
            return pd.NA if text.strip() == "" else text
        if op_type == "remove_special_characters":
            return _remove_special_characters(text, op.get("pattern"))
        if op_type == "replace_substring":
            old = op.get("old")
            new = op.get("new", "")
            if old is None:
                raise StringCleaningError(
                    "REPLACE_VALUE_REQUIRED",
                    "replace_substring requires an 'old' value.",
                )
            return text.replace(str(old), str(new))

        raise StringCleaningError(
            "UNSUPPORTED_OPERATION",
            f"Unsupported string operation: {op_type}",
        )

    return series.map(transform)


def apply_string_cleaning(
    df: pd.DataFrame,
    *,
    columns: Sequence[str] | None,
    operations: list[dict[str, Any]],
) -> StringCleaningResult:
    if not isinstance(df, pd.DataFrame):
        raise StringCleaningError(
            "INVALID_DATAFRAME",
            "Input must be a pandas DataFrame.",
        )
    selected = _validate_columns(df, columns)
    _ensure_string_compatible(df, selected)

    if not operations:
        raise StringCleaningError(
            "OPERATIONS_REQUIRED",
            "At least one string cleaning operation is required.",
        )

    cleaned = df.copy(deep=True)
    summaries: list[ColumnSummary] = []
    total_changed = 0

    for column in selected:
        before = cleaned[column].copy(deep=True)
        nulls_before = int(before.isna().sum())

        after = before
        for op in operations:
            after = _apply_one(after, op)

        before_cmp = before.astype("string")
        after_cmp = after.astype("string")
        changed_mask = (before_cmp != after_cmp).fillna(False)
        changed_cells = int(changed_mask.sum())

        cleaned[column] = after
        nulls_after = int(cleaned[column].isna().sum())
        total_changed += changed_cells

        summaries.append(
            ColumnSummary(
                column=column,
                changed_cells=changed_cells,
                nulls_before=nulls_before,
                nulls_after=nulls_after,
            )
        )

    return StringCleaningResult(
        dataframe=cleaned,
        rows_before=len(df),
        rows_after=len(cleaned),
        columns_before=len(df.columns),
        columns_after=len(cleaned.columns),
        total_changed_cells=total_changed,
        columns=summaries,
    )
