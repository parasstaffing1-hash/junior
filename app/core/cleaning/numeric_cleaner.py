from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Sequence
import re

import pandas as pd
from pandas.api.types import is_numeric_dtype


OperationType = Literal[
    "trim",
    "remove_thousands_separators",
    "remove_currency_symbols",
    "parse_percentage",
    "parse_parentheses_negative",
    "remove_prefix",
    "remove_suffix",
    "to_numeric",
]

InvalidPolicy = Literal["keep_original", "set_null", "error"]


class NumericCleaningError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class InvalidValue:
    row_index: int
    original_value: Any
    cleaned_value: Any


@dataclass(frozen=True)
class ColumnSummary:
    column: str
    changed_cells: int
    invalid_count: int
    numeric_count_before: int
    numeric_count_after: int
    invalid_examples: list[InvalidValue]


@dataclass(frozen=True)
class NumericCleaningResult:
    dataframe: pd.DataFrame
    rows_before: int
    rows_after: int
    columns_before: int
    columns_after: int
    total_changed_cells: int
    total_invalid_values: int
    columns: list[ColumnSummary]


DEFAULT_CURRENCY_SYMBOLS = ["$", "€", "£", "₹", "¥", "₩", "₽", "₺", "R$", "A$", "C$"]


def _validate_columns(df: pd.DataFrame, columns: Sequence[str] | None) -> list[str]:
    if not columns:
        raise NumericCleaningError(
            "COLUMNS_REQUIRED",
            "At least one column must be selected.",
        )
    unknown = [c for c in columns if c not in df.columns]
    if unknown:
        raise NumericCleaningError(
            "UNKNOWN_COLUMN",
            "One or more selected columns do not exist.",
            {"columns": unknown},
        )
    return list(dict.fromkeys(columns))


def _is_numeric_like(value: Any) -> bool:
    if value is None or pd.isna(value):
        return False
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float, Decimal)):
        return True
    try:
        Decimal(str(value))
        return True
    except (InvalidOperation, ValueError):
        return False


def _transform_value(value: Any, operation: dict[str, Any]) -> Any:
    if value is None or pd.isna(value):
        return value

    op = operation["type"]

    if op == "to_numeric":
        return value

    text = str(value)

    if op == "trim":
        return text.strip()

    if op == "remove_thousands_separators":
        separators = operation.get("separators") or [",", " "]
        for sep in separators:
            text = text.replace(str(sep), "")
        return text

    if op == "remove_currency_symbols":
        symbols = operation.get("symbols") or DEFAULT_CURRENCY_SYMBOLS
        for symbol in sorted(symbols, key=len, reverse=True):
            text = text.replace(str(symbol), "")
        return text.strip()

    if op == "parse_parentheses_negative":
        stripped = text.strip()
        if stripped.startswith("(") and stripped.endswith(")"):
            inner = stripped[1:-1].strip()
            return "-" + inner
        return text

    if op == "parse_percentage":
        stripped = text.strip()
        if stripped.endswith("%"):
            numeric_part = stripped[:-1].strip()
            try:
                return str(Decimal(numeric_part) / Decimal("100"))
            except InvalidOperation:
                return text
        return text

    if op == "remove_prefix":
        prefix = operation.get("value")
        if prefix is None:
            raise NumericCleaningError(
                "PREFIX_REQUIRED",
                "remove_prefix requires a value.",
            )
        return text[len(str(prefix)):] if text.startswith(str(prefix)) else text

    if op == "remove_suffix":
        suffix = operation.get("value")
        if suffix is None:
            raise NumericCleaningError(
                "SUFFIX_REQUIRED",
                "remove_suffix requires a value.",
            )
        suffix = str(suffix)
        return text[:-len(suffix)] if suffix and text.endswith(suffix) else text

    raise NumericCleaningError(
        "UNSUPPORTED_OPERATION",
        f"Unsupported numeric cleaning operation: {op}",
    )


def _coerce_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def apply_numeric_cleaning(
    df: pd.DataFrame,
    *,
    columns: Sequence[str] | None,
    operations: list[dict[str, Any]],
    on_invalid: InvalidPolicy = "keep_original",
    max_invalid_examples: int = 10,
) -> NumericCleaningResult:
    if not isinstance(df, pd.DataFrame):
        raise NumericCleaningError(
            "INVALID_DATAFRAME",
            "Input must be a pandas DataFrame.",
        )

    selected = _validate_columns(df, columns)

    if not operations:
        raise NumericCleaningError(
            "OPERATIONS_REQUIRED",
            "At least one numeric cleaning operation is required.",
        )

    if on_invalid not in {"keep_original", "set_null", "error"}:
        raise NumericCleaningError(
            "INVALID_POLICY",
            "Unsupported on_invalid policy.",
            {"on_invalid": on_invalid},
        )

    cleaned = df.copy(deep=True)
    summaries: list[ColumnSummary] = []
    total_changed = 0
    total_invalid = 0

    wants_numeric = any(op["type"] == "to_numeric" for op in operations)

    for column in selected:
        original = cleaned[column].copy(deep=True)
        working = original.copy(deep=True)

        for operation in operations:
            if operation["type"] == "to_numeric":
                continue
            working = working.map(lambda v: _transform_value(v, operation))

        invalid_examples: list[InvalidValue] = []
        invalid_mask = pd.Series(False, index=working.index)

        if wants_numeric:
            coerced = _coerce_numeric(working)
            invalid_mask = working.notna() & coerced.isna()
            invalid_indices = list(working[invalid_mask].index)

            for idx in invalid_indices[:max_invalid_examples]:
                invalid_examples.append(
                    InvalidValue(
                        row_index=int(idx),
                        original_value=original.loc[idx],
                        cleaned_value=working.loc[idx],
                    )
                )

            invalid_count = int(invalid_mask.sum())

            if invalid_count and on_invalid == "error":
                raise NumericCleaningError(
                    "INVALID_NUMERIC_VALUE",
                    "One or more values could not be converted to numeric.",
                    {
                        "column": column,
                        "invalid_count": invalid_count,
                        "examples": [
                            {
                                "row_index": x.row_index,
                                "original_value": x.original_value,
                                "cleaned_value": x.cleaned_value,
                            }
                            for x in invalid_examples
                        ],
                    },
                )

            if on_invalid == "keep_original" and invalid_count:
                final = coerced.astype("object")
                final.loc[invalid_mask] = original.loc[invalid_mask]
            else:
                final = coerced
        else:
            invalid_count = 0
            final = working

        original_cmp = original.astype("string")
        final_cmp = final.astype("string")
        changed_mask = (original_cmp != final_cmp).fillna(False)
        changed_cells = int(changed_mask.sum())

        numeric_before = int(sum(_is_numeric_like(v) for v in original.tolist()))
        numeric_after = int(sum(_is_numeric_like(v) for v in final.tolist()))

        cleaned[column] = final
        total_changed += changed_cells
        total_invalid += invalid_count

        summaries.append(
            ColumnSummary(
                column=column,
                changed_cells=changed_cells,
                invalid_count=invalid_count,
                numeric_count_before=numeric_before,
                numeric_count_after=numeric_after,
                invalid_examples=invalid_examples,
            )
        )

    return NumericCleaningResult(
        dataframe=cleaned,
        rows_before=len(df),
        rows_after=len(cleaned),
        columns_before=len(df.columns),
        columns_after=len(cleaned.columns),
        total_changed_cells=total_changed,
        total_invalid_values=total_invalid,
        columns=summaries,
    )
