from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype


class BinningError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class BinSummary:
    operation_index: int
    column: str
    output_column: str
    method: str
    edges: list[float]
    labels: list[str]
    null_input_count: int
    out_of_range_count: int
    produced_null_count: int
    bin_counts: dict[str, int]


@dataclass(frozen=True)
class BinningResult:
    dataframe: pd.DataFrame
    rows_before: int
    rows_after: int
    columns_before: int
    columns_after: int
    operations: list[BinSummary]
    added_columns: list[str]
    replaced_columns: list[str]


def _validate_labels(labels, expected):
    if labels is None:
        return None
    if len(labels) != expected:
        raise BinningError(
            "LABEL_COUNT_MISMATCH",
            "Number of labels must equal number of bins.",
            {"expected": expected, "actual": len(labels)},
        )
    if len(labels) != len(set(labels)):
        raise BinningError("DUPLICATE_LABEL", "Bin labels must be unique.")
    return [str(x) for x in labels]


def _default_labels(edges):
    return [f"bin_{i+1}" for i in range(len(edges)-1)]


def _numeric_series(df, column):
    if column not in df.columns:
        raise BinningError("UNKNOWN_COLUMN", "Binning column does not exist.", {"column": column})
    if not is_numeric_dtype(df[column]):
        raise BinningError(
            "INCOMPATIBLE_COLUMN_TYPE",
            "Binning requires a numeric column.",
            {"column": column, "dtype": str(df[column].dtype)},
        )
    return df[column]


def _prepare_edges(series, spec):
    method = spec["method"]

    if method == "custom":
        edges = spec.get("edges")
        if not isinstance(edges, list) or len(edges) < 2:
            raise BinningError("EDGES_REQUIRED", "custom binning requires at least two edges.")
        try:
            arr = np.array(edges, dtype=float)
        except Exception as exc:
            raise BinningError("INVALID_EDGES", "Custom edges must be numeric.") from exc
        if np.isnan(arr).any():
            raise BinningError("INVALID_EDGES", "Custom edges may not contain NaN.")
        if not np.all(np.diff(arr) > 0):
            raise BinningError("INVALID_EDGES", "Custom edges must be strictly increasing.")
        return arr.tolist()

    bins = spec.get("bins")
    if not isinstance(bins, int) or bins < 1:
        raise BinningError("BINS_REQUIRED", f"{method} binning requires bins >= 1.")

    valid = series.dropna()
    if valid.empty:
        raise BinningError("NO_VALID_VALUES", "Column has no non-null numeric values to bin.")

    if method == "equal_width":
        mn = float(valid.min())
        mx = float(valid.max())
        if mn == mx:
            raise BinningError("CONSTANT_COLUMN", "Equal-width binning requires at least two distinct values.")
        return np.linspace(mn, mx, bins + 1).tolist()

    if method == "quantile":
        duplicates = spec.get("duplicate_edges", "drop")
        if duplicates not in {"drop", "error"}:
            raise BinningError("INVALID_DUPLICATE_EDGE_POLICY", "duplicate_edges must be drop or error.")
        q = np.linspace(0, 1, bins + 1)
        edges = valid.quantile(q).astype(float).tolist()
        unique = []
        for edge in edges:
            if not unique or edge != unique[-1]:
                unique.append(edge)
        if len(unique) != len(edges):
            if duplicates == "error":
                raise BinningError("DUPLICATE_BIN_EDGES", "Quantile calculation produced duplicate bin edges.")
            edges = unique
        if len(edges) < 2:
            raise BinningError("CONSTANT_COLUMN", "Quantile binning requires at least two distinct values.")
        return edges

    raise BinningError("UNSUPPORTED_METHOD", f"Unsupported binning method: {method}")


def _clip_to_edges(series, edges, right, include_lowest):
    out = series.copy()
    eps = np.finfo(float).eps
    lo = edges[0]
    hi = edges[-1]
    if right:
        lower = lo if include_lowest else np.nextafter(lo, np.inf)
        upper = hi
    else:
        lower = lo
        upper = np.nextafter(hi, -np.inf)
    out = out.clip(lower=lower, upper=upper)
    return out


def apply_binning(df: pd.DataFrame, *, operations: list[dict[str, Any]]) -> BinningResult:
    if not isinstance(df, pd.DataFrame):
        raise BinningError("INVALID_DATAFRAME", "Input must be a pandas DataFrame.")
    if not operations:
        raise BinningError("OPERATIONS_REQUIRED", "At least one binning operation is required.")

    out = df.copy(deep=True)
    summaries: list[BinSummary] = []
    added, replaced = [], []
    seen_outputs = set()

    for idx, spec in enumerate(operations):
        column = spec.get("column")
        output_column = str(spec.get("output_column") or f"{column}_bin").strip()
        method = spec.get("method", "equal_width")
        right = bool(spec.get("right", True))
        include_lowest = bool(spec.get("include_lowest", True))
        out_of_range = spec.get("out_of_range", "null")
        replace = bool(spec.get("replace", False))

        if not output_column:
            raise BinningError("OUTPUT_COLUMN_REQUIRED", "output_column must be non-empty.", {"operation_index": idx})
        if output_column in seen_outputs:
            raise BinningError("DUPLICATE_OUTPUT_COLUMN", "Output column may only appear once per request.", {"column": output_column})
        seen_outputs.add(output_column)

        series = _numeric_series(out, column)

        exists = output_column in out.columns
        if exists and not replace:
            raise BinningError("OUTPUT_COLUMN_EXISTS", "Binning output column already exists.", {"column": output_column})

        if out_of_range not in {"null", "error", "clip"}:
            raise BinningError("INVALID_OUT_OF_RANGE_POLICY", "out_of_range must be null, error, or clip.")

        edges = _prepare_edges(series, spec)
        labels = _validate_labels(spec.get("labels"), len(edges)-1) or _default_labels(edges)

        working = series.copy()
        non_null = working.notna()
        below = non_null & (working < edges[0] if include_lowest else working <= edges[0])
        above = non_null & (working > edges[-1] if right else working >= edges[-1])
        oor = below | above
        oor_count = int(oor.sum())

        if oor_count and out_of_range == "error":
            raise BinningError(
                "OUT_OF_RANGE_VALUES",
                "One or more values fall outside the configured bin range.",
                {"column": column, "count": oor_count, "operation_index": idx},
            )
        if oor_count and out_of_range == "clip":
            working = _clip_to_edges(working, edges, right, include_lowest)

        try:
            categorized = pd.cut(
                working,
                bins=edges,
                labels=labels,
                right=right,
                include_lowest=include_lowest,
                ordered=True,
            )
        except Exception as exc:
            raise BinningError("BINNING_FAILED", "Could not apply binning operation.", {"column": column}) from exc

        result_series = categorized.astype("string")
        if out_of_range == "null":
            result_series = result_series.mask(oor, pd.NA)

        out[output_column] = result_series

        counts = out[output_column].value_counts(dropna=False)
        bin_counts = {str(k): int(v) for k, v in counts.items() if not pd.isna(k)}

        summary = BinSummary(
            operation_index=idx,
            column=column,
            output_column=output_column,
            method=method,
            edges=[float(x) for x in edges],
            labels=labels,
            null_input_count=int(series.isna().sum()),
            out_of_range_count=oor_count,
            produced_null_count=int(out[output_column].isna().sum()),
            bin_counts=bin_counts,
        )
        summaries.append(summary)
        (replaced if exists else added).append(output_column)

    return BinningResult(
        dataframe=out,
        rows_before=len(df),
        rows_after=len(out),
        columns_before=len(df.columns),
        columns_after=len(out.columns),
        operations=summaries,
        added_columns=added,
        replaced_columns=replaced,
    )
