from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Sequence
import re
import pandas as pd
from pandas.api.types import is_object_dtype, is_string_dtype

class CategoryNormalizationError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code, self.message, self.details = code, message, details or {}
        super().__init__(message)

@dataclass(frozen=True)
class MappingChange:
    original: Any
    normalized: Any
    count: int

@dataclass(frozen=True)
class ColumnSummary:
    column: str
    unique_before: int
    unique_after: int
    changed_cells: int
    mapped_cells: int
    unmapped_count: int
    unmapped_values: list[Any]
    mapping_changes: list[MappingChange]

@dataclass(frozen=True)
class CategoryNormalizationResult:
    dataframe: pd.DataFrame
    rows_before: int
    rows_after: int
    columns_before: int
    columns_after: int
    total_changed_cells: int
    total_mapped_cells: int
    total_unmapped_values: int
    columns: list[ColumnSummary]

def _validate_columns(df: pd.DataFrame, columns: Sequence[str] | None) -> list[str]:
    if not columns:
        raise CategoryNormalizationError("COLUMNS_REQUIRED", "At least one categorical column must be selected.")
    unknown = [c for c in columns if c not in df.columns]
    if unknown:
        raise CategoryNormalizationError("UNKNOWN_COLUMN", "One or more selected columns do not exist.", {"columns": unknown})
    return list(dict.fromkeys(columns))

def _ensure_string_columns(df: pd.DataFrame, columns: list[str]) -> None:
    bad = [c for c in columns if not (is_object_dtype(df[c].dtype) or is_string_dtype(df[c].dtype))]
    if bad:
        raise CategoryNormalizationError(
            "INCOMPATIBLE_COLUMN_TYPE",
            "Category normalization requires string-compatible columns.",
            {"columns": bad, "dtypes": {c: str(df[c].dtype) for c in bad}},
        )

def _text_op(value: Any, op: str) -> Any:
    if value is None or pd.isna(value):
        return value
    text = str(value)
    if op == "trim":
        return text.strip()
    if op == "collapse_whitespace":
        return re.sub(r"\s+", " ", text).strip()
    if op == "lowercase":
        return text.lower()
    if op == "uppercase":
        return text.upper()
    if op == "titlecase":
        return text.title()
    raise CategoryNormalizationError("UNSUPPORTED_OPERATION", f"Unsupported operation: {op}")

def apply_category_normalization(
    df: pd.DataFrame,
    *,
    columns: Sequence[str] | None,
    operations: list[dict[str, Any]] | None = None,
    mappings: dict[str, str] | None = None,
    case_insensitive_mapping: bool = False,
    report_unmapped: bool = True,
    max_unmapped_values: int = 20,
) -> CategoryNormalizationResult:
    if not isinstance(df, pd.DataFrame):
        raise CategoryNormalizationError("INVALID_DATAFRAME", "Input must be a pandas DataFrame.")

    selected = _validate_columns(df, columns)
    _ensure_string_columns(df, selected)
    operations = operations or []
    mappings = mappings or {}
    if not operations and not mappings:
        raise CategoryNormalizationError("NORMALIZATION_RULE_REQUIRED", "At least one text operation or mapping is required.")

    lookup_map = {
        (str(k).casefold() if case_insensitive_mapping else str(k)): str(v)
        for k, v in mappings.items()
    }

    cleaned = df.copy(deep=True)
    summaries = []
    total_changed = total_mapped = total_unmapped = 0

    for column in selected:
        original = cleaned[column].copy(deep=True)
        working = original.copy(deep=True)
        for op in operations:
            working = working.map(lambda v, t=op["type"]: _text_op(v, t))

        mapping_counts: dict[tuple[str, str], int] = {}
        unmapped_seen: set[str] = set()
        unmapped_values: list[str] = []
        mapped_cells = 0
        final_values = []

        for value in working.tolist():
            if value is None or pd.isna(value):
                final_values.append(value)
                continue
            text = str(value)
            key = text.casefold() if case_insensitive_mapping else text
            if key in lookup_map:
                canonical = lookup_map[key]
                final_values.append(canonical)
                if canonical != text:
                    mapped_cells += 1
                    pair = (text, canonical)
                    mapping_counts[pair] = mapping_counts.get(pair, 0) + 1
            else:
                final_values.append(value)
                if report_unmapped and text not in unmapped_seen:
                    unmapped_seen.add(text)
                    if len(unmapped_values) < max_unmapped_values:
                        unmapped_values.append(text)

        final = pd.Series(final_values, index=original.index, dtype="object")
        changed_cells = int(((original.astype("string") != final.astype("string")).fillna(False)).sum())
        unique_before = int(original.dropna().astype("string").nunique())
        unique_after = int(final.dropna().astype("string").nunique())
        unmapped_count = len(unmapped_seen) if report_unmapped else 0

        changes = [
            MappingChange(src, dst, count)
            for (src, dst), count in sorted(mapping_counts.items(), key=lambda x: (-x[1], x[0][0]))
        ]

        cleaned[column] = final
        total_changed += changed_cells
        total_mapped += mapped_cells
        total_unmapped += unmapped_count
        summaries.append(ColumnSummary(
            column=column,
            unique_before=unique_before,
            unique_after=unique_after,
            changed_cells=changed_cells,
            mapped_cells=mapped_cells,
            unmapped_count=unmapped_count,
            unmapped_values=unmapped_values,
            mapping_changes=changes,
        ))

    return CategoryNormalizationResult(
        dataframe=cleaned,
        rows_before=len(df),
        rows_after=len(cleaned),
        columns_before=len(df.columns),
        columns_after=len(cleaned.columns),
        total_changed_cells=total_changed,
        total_mapped_cells=total_mapped,
        total_unmapped_values=total_unmapped,
        columns=summaries,
    )
