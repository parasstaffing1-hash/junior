from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Sequence
import re

import pandas as pd
from dateutil import parser as date_parser


TargetType = Literal["date", "datetime"]
InvalidPolicy = Literal["keep_original", "set_null", "error"]


class DateCleaningError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class InvalidValue:
    row_index: int
    original_value: Any


@dataclass(frozen=True)
class AmbiguousValue:
    row_index: int
    original_value: Any
    reason: str


@dataclass(frozen=True)
class ColumnSummary:
    column: str
    changed_cells: int
    invalid_count: int
    parsed_count: int
    ambiguous_count: int
    invalid_examples: list[InvalidValue]
    ambiguous_examples: list[AmbiguousValue]


@dataclass(frozen=True)
class DateCleaningResult:
    dataframe: pd.DataFrame
    rows_before: int
    rows_after: int
    columns_before: int
    columns_after: int
    total_changed_cells: int
    total_invalid_values: int
    total_ambiguous_values: int
    columns: list[ColumnSummary]


AMBIGUOUS_NUMERIC_DATE = re.compile(
    r"^\s*(\d{1,2})[/-](\d{1,2})[/-](\d{2}|\d{4})\s*$"
)


def _validate_columns(df: pd.DataFrame, columns: Sequence[str] | None) -> list[str]:
    if not columns:
        raise DateCleaningError(
            "COLUMNS_REQUIRED",
            "At least one date column must be selected.",
        )
    unknown = [c for c in columns if c not in df.columns]
    if unknown:
        raise DateCleaningError(
            "UNKNOWN_COLUMN",
            "One or more selected columns do not exist.",
            {"columns": unknown},
        )
    return list(dict.fromkeys(columns))


def _is_ambiguous_numeric_date(value: Any) -> bool:
    if value is None or pd.isna(value):
        return False
    match = AMBIGUOUS_NUMERIC_DATE.match(str(value))
    if not match:
        return False
    first, second = int(match.group(1)), int(match.group(2))
    return 1 <= first <= 12 and 1 <= second <= 12 and first != second


def _parse_with_formats(
    text: str,
    input_formats: list[str],
) -> datetime | None:
    for fmt in input_formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _parse_value(
    value: Any,
    *,
    input_formats: list[str] | None,
    dayfirst: bool,
    yearfirst: bool,
    convert_to_utc: bool,
) -> pd.Timestamp:
    if isinstance(value, pd.Timestamp):
        ts = value
    elif isinstance(value, datetime):
        ts = pd.Timestamp(value)
    else:
        text = str(value).strip()
        if not text:
            raise ValueError("empty string")

        parsed = None
        if input_formats:
            parsed = _parse_with_formats(text, input_formats)

        if parsed is not None:
            ts = pd.Timestamp(parsed)
        else:
            try:
                parsed = date_parser.parse(
                    text,
                    dayfirst=dayfirst,
                    yearfirst=yearfirst,
                    fuzzy=False,
                )
                ts = pd.Timestamp(parsed)
            except Exception as exc:
                raise ValueError(str(exc)) from exc

    if convert_to_utc:
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
    return ts


def _format_timestamp(ts: pd.Timestamp, target_type: TargetType) -> str:
    if target_type == "date":
        return ts.strftime("%Y-%m-%d")

    if ts.tzinfo is not None:
        # ISO 8601 with timezone; use Z for UTC.
        text = ts.isoformat(timespec="seconds")
        return text.replace("+00:00", "Z")

    return ts.strftime("%Y-%m-%dT%H:%M:%S")


def apply_date_cleaning(
    df: pd.DataFrame,
    *,
    columns: Sequence[str] | None,
    target_type: TargetType,
    input_formats: list[str] | None = None,
    dayfirst: bool = False,
    yearfirst: bool = False,
    convert_to_utc: bool = False,
    on_invalid: InvalidPolicy = "keep_original",
    trim: bool = True,
    max_examples: int = 10,
) -> DateCleaningResult:
    if not isinstance(df, pd.DataFrame):
        raise DateCleaningError(
            "INVALID_DATAFRAME",
            "Input must be a pandas DataFrame.",
        )

    selected = _validate_columns(df, columns)

    if target_type not in {"date", "datetime"}:
        raise DateCleaningError(
            "INVALID_TARGET_TYPE",
            "target_type must be date or datetime.",
        )

    if on_invalid not in {"keep_original", "set_null", "error"}:
        raise DateCleaningError(
            "INVALID_POLICY",
            "Unsupported invalid-value policy.",
            {"on_invalid": on_invalid},
        )

    if input_formats is not None and any(not x for x in input_formats):
        raise DateCleaningError(
            "INVALID_INPUT_FORMAT",
            "Input formats may not contain empty values.",
        )

    cleaned = df.copy(deep=True)
    summaries: list[ColumnSummary] = []
    total_changed = 0
    total_invalid = 0
    total_ambiguous = 0

    for column in selected:
        original = cleaned[column].copy(deep=True)
        output: list[Any] = []
        invalid_examples: list[InvalidValue] = []
        ambiguous_examples: list[AmbiguousValue] = []
        invalid_count = 0
        parsed_count = 0
        ambiguous_count = 0

        for idx, value in original.items():
            if value is None or pd.isna(value):
                output.append(value)
                continue

            source_value = value
            if isinstance(value, str) and trim:
                source_value = value.strip()

            if (
                not input_formats
                and isinstance(source_value, str)
                and _is_ambiguous_numeric_date(source_value)
            ):
                ambiguous_count += 1
                if len(ambiguous_examples) < max_examples:
                    ambiguous_examples.append(
                        AmbiguousValue(
                            row_index=int(idx),
                            original_value=value,
                            reason="Both day/month and month/day interpretations are possible.",
                        )
                    )

            try:
                ts = _parse_value(
                    source_value,
                    input_formats=input_formats,
                    dayfirst=dayfirst,
                    yearfirst=yearfirst,
                    convert_to_utc=convert_to_utc,
                )
                output.append(_format_timestamp(ts, target_type))
                parsed_count += 1
            except Exception:
                invalid_count += 1
                if len(invalid_examples) < max_examples:
                    invalid_examples.append(
                        InvalidValue(row_index=int(idx), original_value=value)
                    )

                if on_invalid == "error":
                    raise DateCleaningError(
                        "INVALID_DATE_VALUE",
                        "One or more values could not be parsed as dates.",
                        {
                            "column": column,
                            "row_index": int(idx),
                            "value": value,
                        },
                    )
                if on_invalid == "set_null":
                    output.append(pd.NA)
                else:
                    output.append(value)

        final = pd.Series(output, index=original.index, dtype="object")

        original_cmp = original.astype("string")
        final_cmp = final.astype("string")
        changed_mask = (original_cmp != final_cmp).fillna(False)
        changed_cells = int(changed_mask.sum())

        cleaned[column] = final
        total_changed += changed_cells
        total_invalid += invalid_count
        total_ambiguous += ambiguous_count

        summaries.append(
            ColumnSummary(
                column=column,
                changed_cells=changed_cells,
                invalid_count=invalid_count,
                parsed_count=parsed_count,
                ambiguous_count=ambiguous_count,
                invalid_examples=invalid_examples,
                ambiguous_examples=ambiguous_examples,
            )
        )

    return DateCleaningResult(
        dataframe=cleaned,
        rows_before=len(df),
        rows_after=len(cleaned),
        columns_before=len(df.columns),
        columns_after=len(cleaned.columns),
        total_changed_cells=total_changed,
        total_invalid_values=total_invalid,
        total_ambiguous_values=total_ambiguous,
        columns=summaries,
    )
