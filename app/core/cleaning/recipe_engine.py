from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re

import numpy as np
import pandas as pd
from dateutil import parser as date_parser
from pandas.api.types import is_numeric_dtype


SUPPORTED_STEPS = {
    "missing_values",
    "imputation",
    "string_cleaning",
    "numeric_cleaning",
    "date_cleaning",
    "category_normalization",
    "duplicate_removal",
    "outlier_treatment",
}


class RecipeExecutionError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class StepResult:
    step_index: int
    step_type: str
    rows_before: int
    rows_after: int
    columns_before: int
    columns_after: int
    changed_cells: int
    details: dict[str, Any]


@dataclass(frozen=True)
class RecipeResult:
    dataframe: pd.DataFrame
    rows_before: int
    rows_after: int
    columns_before: int
    columns_after: int
    total_steps: int
    completed_steps: int
    total_changed_cells: int
    step_results: list[StepResult]


def _require_columns(df: pd.DataFrame, columns: list[str] | None) -> list[str]:
    if not columns:
        raise RecipeExecutionError("COLUMNS_REQUIRED", "Step requires selected columns.")
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise RecipeExecutionError("UNKNOWN_COLUMN", "One or more selected columns do not exist.", {"columns": missing})
    return list(dict.fromkeys(columns))


def _changed_cells(before: pd.DataFrame, after: pd.DataFrame) -> int:
    common_rows = min(len(before), len(after))
    common_cols = [c for c in before.columns if c in after.columns]
    if not common_cols or common_rows == 0:
        return 0
    left = before.iloc[:common_rows][common_cols].astype("string")
    right = after.iloc[:common_rows][common_cols].astype("string")
    return int((left.ne(right).fillna(False)).sum().sum())


def _step_missing_values(df: pd.DataFrame, p: dict) -> tuple[pd.DataFrame, dict]:
    op = p.get("operation")
    out = df.copy(deep=True)

    if op == "drop_rows_any":
        before = len(out)
        out = out.dropna().reset_index(drop=True)
        return out, {"removed_rows": before - len(out)}

    if op == "drop_rows_selected":
        cols = _require_columns(out, p.get("columns"))
        before = len(out)
        out = out.dropna(subset=cols).reset_index(drop=True)
        return out, {"removed_rows": before - len(out)}

    if op == "drop_columns_any":
        cols = [c for c in out.columns if out[c].isna().any()]
        out = out.drop(columns=cols)
        return out, {"removed_columns": cols}

    if op == "drop_columns_threshold":
        threshold = p.get("threshold")
        if threshold is None or not 0 <= float(threshold) <= 100:
            raise RecipeExecutionError("INVALID_THRESHOLD", "threshold must be 0..100")
        cols = [c for c in out.columns if out[c].isna().mean() * 100 >= float(threshold)]
        out = out.drop(columns=cols)
        return out, {"removed_columns": cols}

    if op == "fill_constant":
        cols = p.get("columns") or list(out.columns)
        _require_columns(out, cols)
        if "fill_value" not in p:
            raise RecipeExecutionError("FILL_VALUE_REQUIRED", "fill_value is required")
        count = int(out[cols].isna().sum().sum())
        out[cols] = out[cols].fillna(p["fill_value"])
        return out, {"filled_cells": count}

    raise RecipeExecutionError("UNSUPPORTED_OPERATION", f"Unsupported missing_values operation: {op}")


def _step_imputation(df: pd.DataFrame, p: dict) -> tuple[pd.DataFrame, dict]:
    method = p.get("method")
    cols = _require_columns(df, p.get("columns"))
    out = df.copy(deep=True)
    filled = 0

    for c in cols:
        before = int(out[c].isna().sum())
        if before == 0:
            continue
        if method == "mean":
            if not is_numeric_dtype(out[c]):
                raise RecipeExecutionError("INCOMPATIBLE_COLUMN_TYPE", "mean requires numeric column", {"column": c})
            value = out[c].mean()
            if pd.isna(value):
                raise RecipeExecutionError("NO_IMPUTATION_VALUE", "mean unavailable", {"column": c})
            out[c] = out[c].fillna(value)
        elif method == "median":
            if not is_numeric_dtype(out[c]):
                raise RecipeExecutionError("INCOMPATIBLE_COLUMN_TYPE", "median requires numeric column", {"column": c})
            value = out[c].median()
            if pd.isna(value):
                raise RecipeExecutionError("NO_IMPUTATION_VALUE", "median unavailable", {"column": c})
            out[c] = out[c].fillna(value)
        elif method == "mode":
            modes = out[c].dropna().mode()
            if modes.empty:
                raise RecipeExecutionError("NO_IMPUTATION_VALUE", "mode unavailable", {"column": c})
            out[c] = out[c].fillna(modes.iloc[0])
        elif method == "forward_fill":
            out[c] = out[c].ffill(limit=p.get("limit"))
        elif method == "backward_fill":
            out[c] = out[c].bfill(limit=p.get("limit"))
        elif method == "interpolate_linear":
            if not is_numeric_dtype(out[c]):
                raise RecipeExecutionError("INCOMPATIBLE_COLUMN_TYPE", "interpolation requires numeric column", {"column": c})
            out[c] = out[c].interpolate(method="linear", limit_direction="both")
        else:
            raise RecipeExecutionError("UNSUPPORTED_OPERATION", f"Unsupported imputation method: {method}")
        filled += before - int(out[c].isna().sum())

    return out, {"imputed_cells": filled}


def _step_string_cleaning(df: pd.DataFrame, p: dict) -> tuple[pd.DataFrame, dict]:
    cols = _require_columns(df, p.get("columns"))
    operations = p.get("operations") or []
    if not operations:
        raise RecipeExecutionError("OPERATIONS_REQUIRED", "string_cleaning requires operations")
    out = df.copy(deep=True)

    for c in cols:
        if is_numeric_dtype(out[c]):
            raise RecipeExecutionError("INCOMPATIBLE_COLUMN_TYPE", "string cleaning requires string-compatible columns", {"column": c})
        s = out[c]
        for op in operations:
            if isinstance(op, dict):
                kind = op.get("type")
                old = op.get("old")
                new = op.get("new", "")
            else:
                kind = op
                old = new = None

            def tx(v):
                if pd.isna(v): return v
                t = str(v)
                if kind == "trim": return t.strip()
                if kind == "collapse_whitespace": return re.sub(r"\s+", " ", t).strip()
                if kind == "lowercase": return t.lower()
                if kind == "uppercase": return t.upper()
                if kind == "titlecase": return t.title()
                if kind == "empty_to_null": return pd.NA if t.strip() == "" else t
                if kind == "replace_substring":
                    if old is None: raise RecipeExecutionError("REPLACE_VALUE_REQUIRED", "replace_substring requires old")
                    return t.replace(str(old), str(new))
                raise RecipeExecutionError("UNSUPPORTED_OPERATION", f"Unsupported string operation: {kind}")
            s = s.map(tx)
        out[c] = s
    return out, {}


def _step_numeric_cleaning(df: pd.DataFrame, p: dict) -> tuple[pd.DataFrame, dict]:
    cols = _require_columns(df, p.get("columns"))
    out = df.copy(deep=True)
    invalid_policy = p.get("invalid_policy", "keep_original")
    symbols = p.get("currency_symbols") or ["$", "€", "£", "₹", "¥"]
    invalid = 0

    for c in cols:
        original = out[c].copy()
        s = out[c].map(lambda v: v if pd.isna(v) else str(v).strip())
        if p.get("parse_parentheses_negative", True):
            s = s.map(lambda v: v if pd.isna(v) else ("-" + v[1:-1].strip() if v.startswith("(") and v.endswith(")") else v))
        if p.get("remove_commas", True):
            s = s.map(lambda v: v if pd.isna(v) else v.replace(",", ""))
        for sym in symbols:
            s = s.map(lambda v, sym=sym: v if pd.isna(v) else v.replace(sym, "").strip())
        if p.get("parse_percentage"):
            def pct(v):
                if pd.isna(v): return v
                if str(v).endswith("%"):
                    try: return str(float(str(v)[:-1].strip()) / 100)
                    except Exception: return v
                return v
            s = s.map(pct)
        if p.get("to_numeric", True):
            numeric = pd.to_numeric(s, errors="coerce")
            bad = s.notna() & numeric.isna()
            invalid += int(bad.sum())
            if invalid_policy == "error" and bad.any():
                raise RecipeExecutionError("INVALID_NUMERIC_VALUE", "Unparseable numeric value", {"column": c})
            if invalid_policy == "keep_original":
                final = numeric.astype("object")
                final.loc[bad] = original.loc[bad]
            else:
                final = numeric
            out[c] = final
        else:
            out[c] = s
    return out, {"invalid_values": invalid}


def _step_date_cleaning(df: pd.DataFrame, p: dict) -> tuple[pd.DataFrame, dict]:
    cols = _require_columns(df, p.get("columns"))
    target = p.get("target_type", "date")
    dayfirst = bool(p.get("dayfirst", False))
    yearfirst = bool(p.get("yearfirst", False))
    policy = p.get("on_invalid", "keep_original")
    out = df.copy(deep=True)
    invalid = 0

    for c in cols:
        vals = []
        for v in out[c].tolist():
            if pd.isna(v):
                vals.append(v); continue
            try:
                dt = date_parser.parse(str(v).strip(), dayfirst=dayfirst, yearfirst=yearfirst)
                vals.append(dt.strftime("%Y-%m-%d") if target == "date" else dt.strftime("%Y-%m-%dT%H:%M:%S"))
            except Exception:
                invalid += 1
                if policy == "error":
                    raise RecipeExecutionError("INVALID_DATE_VALUE", "Unparseable date", {"column": c, "value": v})
                vals.append(pd.NA if policy == "set_null" else v)
        out[c] = pd.Series(vals, index=out.index, dtype="object")
    return out, {"invalid_values": invalid}


def _step_category_normalization(df: pd.DataFrame, p: dict) -> tuple[pd.DataFrame, dict]:
    cols = _require_columns(df, p.get("columns"))
    ops = p.get("operations") or []
    mappings = p.get("mappings") or {}
    ci = bool(p.get("case_insensitive_mapping", False))
    lookup = {(str(k).casefold() if ci else str(k)): str(v) for k, v in mappings.items()}
    out = df.copy(deep=True)
    mapped = 0

    for c in cols:
        if is_numeric_dtype(out[c]):
            raise RecipeExecutionError("INCOMPATIBLE_COLUMN_TYPE", "category normalization requires string-compatible column", {"column": c})
        s = out[c]
        for kind in ops:
            kind = kind.get("type") if isinstance(kind, dict) else kind
            def tx(v):
                if pd.isna(v): return v
                t = str(v)
                if kind == "trim": return t.strip()
                if kind == "collapse_whitespace": return re.sub(r"\s+", " ", t).strip()
                if kind == "lowercase": return t.lower()
                if kind == "uppercase": return t.upper()
                if kind == "titlecase": return t.title()
                raise RecipeExecutionError("UNSUPPORTED_OPERATION", f"Unsupported category operation: {kind}")
            s = s.map(tx)

        vals = []
        for v in s.tolist():
            if pd.isna(v):
                vals.append(v); continue
            text = str(v)
            key = text.casefold() if ci else text
            if key in lookup:
                canonical = lookup[key]
                mapped += int(canonical != text)
                vals.append(canonical)
            else:
                vals.append(v)
        out[c] = vals
    return out, {"mapped_cells": mapped}


def _step_duplicate_removal(df: pd.DataFrame, p: dict) -> tuple[pd.DataFrame, dict]:
    mode = p.get("mode", "exact")
    keep = p.get("keep", "first")
    if keep == "none": pandas_keep = False
    elif keep in {"first", "last"}: pandas_keep = keep
    else: raise RecipeExecutionError("INVALID_KEEP_POLICY", "keep must be first, last, or none")

    subset = None
    if mode == "columns":
        subset = _require_columns(df, p.get("columns"))
    elif mode != "exact":
        raise RecipeExecutionError("INVALID_MODE", "duplicate mode must be exact or columns")

    mask = df.duplicated(subset=subset, keep=pandas_keep)
    out = df.loc[~mask].copy().reset_index(drop=True)
    return out, {"removed_rows": int(mask.sum())}


def _outlier_mask(series: pd.Series, p: dict) -> tuple[pd.Series, float | None, float | None]:
    method = p.get("method", "iqr")
    non_null = series.dropna().astype(float)
    mask = pd.Series(False, index=series.index)
    if non_null.empty:
        return mask, None, None

    if method == "iqr":
        m = float(p.get("iqr_multiplier", 1.5))
        q1, q3 = float(non_null.quantile(.25)), float(non_null.quantile(.75))
        iqr = q3 - q1
        low, high = q1 - m * iqr, q3 + m * iqr
    elif method == "zscore":
        z = float(p.get("z_threshold", 3.0))
        mean, std = float(non_null.mean()), float(non_null.std(ddof=0))
        if std == 0 or np.isnan(std): return mask, None, None
        low, high = mean - z * std, mean + z * std
    elif method == "percentile":
        low = float(non_null.quantile(float(p.get("lower_percentile", 1))/100))
        high = float(non_null.quantile(float(p.get("upper_percentile", 99))/100))
    else:
        raise RecipeExecutionError("INVALID_METHOD", f"Unsupported outlier method: {method}")

    mask = series.notna() & ((series.astype(float) < low) | (series.astype(float) > high))
    return mask, float(low), float(high)


def _step_outlier_treatment(df: pd.DataFrame, p: dict) -> tuple[pd.DataFrame, dict]:
    cols = _require_columns(df, p.get("columns"))
    for c in cols:
        if not is_numeric_dtype(df[c]):
            raise RecipeExecutionError("INCOMPATIBLE_COLUMN_TYPE", "outlier treatment requires numeric columns", {"column": c})

    action = p.get("action")
    out = df.copy(deep=True)
    masks = {}
    bounds = {}
    row_any = pd.Series(False, index=df.index)

    for c in cols:
        mask, low, high = _outlier_mask(df[c], p)
        masks[c], bounds[c] = mask, (low, high)
        row_any = row_any | mask

    if action == "remove_rows":
        n = int(row_any.sum())
        return out.loc[~row_any].copy().reset_index(drop=True), {"removed_rows": n}

    treated = 0
    flags = []
    for c in cols:
        mask = masks[c]
        low, high = bounds[c]
        count = int(mask.sum())

        if action in {"cap", "winsorize"}:
            if low is not None and high is not None:
                out[c] = out[c].astype(float).clip(low, high)
            treated += count
        elif action == "replace_constant":
            if "replacement_constant" not in p:
                raise RecipeExecutionError("REPLACEMENT_CONSTANT_REQUIRED", "replacement_constant required")
            out.loc[mask, c] = float(p["replacement_constant"])
            treated += count
        elif action == "replace_median":
            value = df.loc[~mask, c].dropna().median()
            out.loc[mask, c] = value
            treated += count
        elif action == "replace_mean":
            value = df.loc[~mask, c].dropna().mean()
            out.loc[mask, c] = value
            treated += count
        elif action == "retain_and_flag":
            suffix = p.get("flag_suffix", "__outlier")
            fc = f"{c}{suffix}"
            if fc in out.columns:
                raise RecipeExecutionError("FLAG_COLUMN_EXISTS", "Generated flag column exists", {"column": fc})
            out[fc] = mask.astype(bool)
            flags.append(fc)
        else:
            raise RecipeExecutionError("INVALID_ACTION", f"Unsupported outlier action: {action}")
    return out, {"treated_cells": treated, "added_flag_columns": flags}


STEP_HANDLERS = {
    "missing_values": _step_missing_values,
    "imputation": _step_imputation,
    "string_cleaning": _step_string_cleaning,
    "numeric_cleaning": _step_numeric_cleaning,
    "date_cleaning": _step_date_cleaning,
    "category_normalization": _step_category_normalization,
    "duplicate_removal": _step_duplicate_removal,
    "outlier_treatment": _step_outlier_treatment,
}


def validate_recipe_steps(steps: list[dict[str, Any]]) -> None:
    if not steps:
        raise RecipeExecutionError("STEPS_REQUIRED", "Recipe requires at least one step.")
    for i, step in enumerate(steps):
        step_type = step.get("type")
        if step_type not in SUPPORTED_STEPS:
            raise RecipeExecutionError(
                "UNSUPPORTED_STEP",
                f"Unsupported recipe step: {step_type}",
                {"step_index": i},
            )
        if not isinstance(step.get("params", {}), dict):
            raise RecipeExecutionError(
                "INVALID_STEP_PARAMS",
                "Step params must be an object.",
                {"step_index": i},
            )


def execute_recipe(df: pd.DataFrame, steps: list[dict[str, Any]]) -> RecipeResult:
    if not isinstance(df, pd.DataFrame):
        raise RecipeExecutionError("INVALID_DATAFRAME", "Input must be a pandas DataFrame.")
    validate_recipe_steps(steps)

    current = df.copy(deep=True)
    step_results: list[StepResult] = []
    total_changed = 0

    for i, step in enumerate(steps):
        before = current.copy(deep=True)
        handler = STEP_HANDLERS[step["type"]]
        try:
            current, details = handler(current, step.get("params", {}))
        except RecipeExecutionError as exc:
            exc.details = {**exc.details, "step_index": i, "step_type": step["type"]}
            raise
        changed = _changed_cells(before, current)
        total_changed += changed
        step_results.append(
            StepResult(
                step_index=i,
                step_type=step["type"],
                rows_before=len(before),
                rows_after=len(current),
                columns_before=len(before.columns),
                columns_after=len(current.columns),
                changed_cells=changed,
                details=details,
            )
        )

    return RecipeResult(
        dataframe=current,
        rows_before=len(df),
        rows_after=len(current),
        columns_before=len(df.columns),
        columns_after=len(current.columns),
        total_steps=len(steps),
        completed_steps=len(step_results),
        total_changed_cells=total_changed,
        step_results=step_results,
    )
