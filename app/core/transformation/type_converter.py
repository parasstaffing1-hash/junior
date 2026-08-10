from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd
from dateutil import parser as date_parser


class TypeConversionError(Exception):
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
class ConversionSummary:
    column: str
    target_type: str
    dtype_before: str
    dtype_after: str
    invalid_count: int
    changed_cells: int
    invalid_examples: list[InvalidValue]


@dataclass(frozen=True)
class TypeConversionResult:
    dataframe: pd.DataFrame
    rows_before: int
    rows_after: int
    columns_before: int
    columns_after: int
    total_invalid_values: int
    total_changed_cells: int
    conversions: list[ConversionSummary]


def _parse_datetime_value(value, formats, dayfirst, yearfirst):
    text = str(value).strip()
    if not text:
        raise ValueError("empty string")
    if formats:
        for fmt in formats:
            try:
                return pd.Timestamp(datetime.strptime(text, fmt))
            except ValueError:
                pass
    return pd.Timestamp(date_parser.parse(text, dayfirst=dayfirst, yearfirst=yearfirst, fuzzy=False))


def _convert_boolean(series, true_values, false_values):
    true_set = {str(x).strip().casefold() for x in true_values}
    false_set = {str(x).strip().casefold() for x in false_values}
    overlap = true_set & false_set
    if overlap:
        raise TypeConversionError(
            "BOOLEAN_TOKEN_COLLISION",
            "Boolean true/false token sets overlap.",
            {"tokens": sorted(overlap)},
        )

    result = []
    invalid = []
    for idx, value in series.items():
        if value is None or pd.isna(value):
            result.append(pd.NA)
            continue
        if isinstance(value, bool):
            result.append(value)
            continue
        token = str(value).strip().casefold()
        if token in true_set:
            result.append(True)
        elif token in false_set:
            result.append(False)
        else:
            result.append(pd.NA)
            invalid.append((idx, value))
    return pd.Series(result, index=series.index, dtype="boolean"), invalid


def _convert_series(series: pd.Series, spec: dict[str, Any]):
    target = spec["target_type"]
    policy = spec.get("policy", "strict")
    invalid = []

    if target == "string":
        out = series.astype("string")
        return out, invalid

    if target in {"integer", "float"}:
        numeric = pd.to_numeric(series, errors="coerce")
        bad = series.notna() & numeric.isna()
        invalid = [(idx, series.loc[idx]) for idx in series.index[bad]]

        if target == "integer":
            fractional = numeric.notna() & ((numeric % 1).abs() > 1e-12)
            invalid.extend((idx, series.loc[idx]) for idx in series.index[fractional])
            numeric.loc[fractional] = pd.NA
            out = numeric.astype("Int64")
        else:
            out = numeric.astype("Float64")

        if invalid and policy == "strict":
            raise TypeConversionError(
                "INVALID_CONVERSION_VALUE",
                "One or more values cannot be converted.",
                {"column": series.name, "target_type": target, "invalid_count": len(invalid)},
            )
        return out, invalid

    if target == "boolean":
        out, invalid = _convert_boolean(
            series,
            spec.get("true_values") or ["true","1","yes","y","t"],
            spec.get("false_values") or ["false","0","no","n","f"],
        )
        if invalid and policy == "strict":
            raise TypeConversionError(
                "INVALID_CONVERSION_VALUE",
                "One or more values cannot be converted to boolean.",
                {"column": series.name, "invalid_count": len(invalid)},
            )
        return out, invalid

    if target in {"date", "datetime"}:
        values = []
        for idx, value in series.items():
            if value is None or pd.isna(value):
                values.append(pd.NaT)
                continue
            try:
                ts = _parse_datetime_value(
                    value,
                    spec.get("input_formats"),
                    bool(spec.get("dayfirst", False)),
                    bool(spec.get("yearfirst", False)),
                )
                values.append(ts.normalize() if target == "date" else ts)
            except Exception:
                values.append(pd.NaT)
                invalid.append((idx, value))

        if invalid and policy == "strict":
            raise TypeConversionError(
                "INVALID_CONVERSION_VALUE",
                "One or more values cannot be converted to date/datetime.",
                {"column": series.name, "target_type": target, "invalid_count": len(invalid)},
            )

        out = pd.Series(values, index=series.index, dtype="datetime64[ns]")
        return out, invalid

    if target == "category":
        out = series.astype("category")
        return out, invalid

    raise TypeConversionError("UNSUPPORTED_TARGET_TYPE", f"Unsupported target type: {target}")


def apply_type_conversions(df: pd.DataFrame, conversions: list[dict[str, Any]], max_examples: int = 10) -> TypeConversionResult:
    if not isinstance(df, pd.DataFrame):
        raise TypeConversionError("INVALID_DATAFRAME", "Input must be a pandas DataFrame.")
    if not conversions:
        raise TypeConversionError("CONVERSIONS_REQUIRED", "At least one conversion is required.")

    seen = set()
    for i, spec in enumerate(conversions):
        column = spec.get("column")
        if not column:
            raise TypeConversionError("COLUMN_REQUIRED", "Each conversion requires a column.", {"conversion_index": i})
        if column in seen:
            raise TypeConversionError("DUPLICATE_CONVERSION_COLUMN", "A column may only be converted once per request.", {"column": column})
        seen.add(column)
        if column not in df.columns:
            raise TypeConversionError("UNKNOWN_COLUMN", "Conversion column does not exist.", {"column": column})
        if spec.get("policy", "strict") not in {"strict", "coerce"}:
            raise TypeConversionError("INVALID_POLICY", "policy must be strict or coerce.", {"column": column})

    out = df.copy(deep=True)
    summaries = []
    total_invalid = 0
    total_changed = 0

    for spec in conversions:
        column = spec["column"]
        before = out[column].copy(deep=True)
        dtype_before = str(before.dtype)

        try:
            converted, invalid = _convert_series(before, spec)
        except TypeConversionError as exc:
            exc.details = {**exc.details, "column": column}
            raise

        out[column] = converted
        dtype_after = str(out[column].dtype)

        before_cmp = before.astype("string")
        after_cmp = out[column].astype("string")
        changed = int((before_cmp.ne(after_cmp).fillna(False)).sum())

        invalid_examples = [
            InvalidValue(row_index=int(idx), original_value=value)
            for idx, value in invalid[:max_examples]
        ]

        summaries.append(
            ConversionSummary(
                column=column,
                target_type=spec["target_type"],
                dtype_before=dtype_before,
                dtype_after=dtype_after,
                invalid_count=len(invalid),
                changed_cells=changed,
                invalid_examples=invalid_examples,
            )
        )
        total_invalid += len(invalid)
        total_changed += changed

    return TypeConversionResult(
        dataframe=out,
        rows_before=len(df),
        rows_after=len(out),
        columns_before=len(df.columns),
        columns_after=len(out.columns),
        total_invalid_values=total_invalid,
        total_changed_cells=total_changed,
        conversions=summaries,
    )
