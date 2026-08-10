from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


class CalculationError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class CalculationSummary:
    calculation_index: int
    name: str
    dtype: str
    null_count: int
    overwritten: bool
    changed_cells: int


@dataclass(frozen=True)
class CalculatedColumnResult:
    dataframe: pd.DataFrame
    rows_before: int
    rows_after: int
    columns_before: int
    columns_after: int
    calculations: list[CalculationSummary]
    added_columns: list[str]
    overwritten_columns: list[str]


BINARY_ARITH = {"add", "subtract", "multiply", "divide", "mod", "power"}
COMPARISON = {"eq", "ne", "gt", "gte", "lt", "lte"}
BOOLEAN_BINARY = {"and", "or"}
UNARY = {"abs", "round", "negate", "not", "is_null", "not_null", "lower", "upper", "trim"}
SPECIAL = {"column", "literal", "coalesce", "concat", "if_else"}
SUPPORTED = BINARY_ARITH | COMPARISON | BOOLEAN_BINARY | UNARY | SPECIAL


def _as_series(value: Any, index: pd.Index) -> pd.Series:
    if isinstance(value, pd.Series):
        return value.reindex(index)
    return pd.Series([value] * len(index), index=index)


def _column(df: pd.DataFrame, name: str | None) -> pd.Series:
    if not name:
        raise CalculationError("COLUMN_NAME_REQUIRED", "column expression requires name.")
    if name not in df.columns:
        raise CalculationError("UNKNOWN_COLUMN", "Referenced column does not exist.", {"column": name})
    return df[name]


def _eval(df: pd.DataFrame, node: dict[str, Any], divide_by_zero: str):
    if not isinstance(node, dict):
        raise CalculationError("INVALID_EXPRESSION", "Expression node must be an object.")
    op = node.get("op")
    if op not in SUPPORTED:
        raise CalculationError("UNSUPPORTED_EXPRESSION", f"Unsupported expression op: {op}", {"op": op})

    if op == "column":
        return _column(df, node.get("name"))
    if op == "literal":
        return node.get("value")

    if op in BINARY_ARITH | COMPARISON | BOOLEAN_BINARY:
        if "left" not in node or "right" not in node:
            raise CalculationError("OPERANDS_REQUIRED", f"{op} requires left and right operands.")
        left = _as_series(_eval(df, node["left"], divide_by_zero), df.index)
        right = _as_series(_eval(df, node["right"], divide_by_zero), df.index)
        try:
            if op == "add": return left + right
            if op == "subtract": return left - right
            if op == "multiply": return left * right
            if op == "divide":
                zero = right.notna() & (right == 0)
                if zero.any() and divide_by_zero == "error":
                    raise CalculationError("DIVIDE_BY_ZERO", "Division by zero encountered.", {"rows":[int(i) for i in right.index[zero][:20].tolist()]})
                return left / right.mask(zero)
            if op == "mod":
                zero = right.notna() & (right == 0)
                if zero.any() and divide_by_zero == "error":
                    raise CalculationError("DIVIDE_BY_ZERO", "Modulo by zero encountered.")
                return left % right.mask(zero)
            if op == "power": return left ** right
            if op == "eq": return (left == right).fillna(False)
            if op == "ne": return (left != right).fillna(False)
            if op == "gt": return (left > right).fillna(False)
            if op == "gte": return (left >= right).fillna(False)
            if op == "lt": return (left < right).fillna(False)
            if op == "lte": return (left <= right).fillna(False)
            if op == "and": return left.fillna(False).astype(bool) & right.fillna(False).astype(bool)
            if op == "or": return left.fillna(False).astype(bool) | right.fillna(False).astype(bool)
        except CalculationError:
            raise
        except Exception as exc:
            raise CalculationError("INCOMPATIBLE_EXPRESSION_TYPES", "Expression operands have incompatible types.", {"op": op}) from exc

    if op in UNARY:
        if "value" not in node:
            raise CalculationError("VALUE_REQUIRED", f"{op} requires value.")
        s = _as_series(_eval(df, node["value"], divide_by_zero), df.index)
        try:
            if op == "abs": return s.abs()
            if op == "round": return s.round(int(node.get("digits", 0)))
            if op == "negate": return -s
            if op == "not": return ~s.fillna(False).astype(bool)
            if op == "is_null": return s.isna()
            if op == "not_null": return s.notna()
            if op == "lower": return s.astype("string").str.lower()
            if op == "upper": return s.astype("string").str.upper()
            if op == "trim": return s.astype("string").str.strip()
        except Exception as exc:
            raise CalculationError("INCOMPATIBLE_EXPRESSION_TYPES", "Unary expression value has an incompatible type.", {"op": op}) from exc

    if op == "coalesce":
        args = node.get("args")
        if not isinstance(args, list) or not args:
            raise CalculationError("ARGS_REQUIRED", "coalesce requires a non-empty args array.")
        result = _as_series(_eval(df, args[0], divide_by_zero), df.index).copy()
        for arg in args[1:]:
            candidate = _as_series(_eval(df, arg, divide_by_zero), df.index)
            result = result.where(result.notna(), candidate)
        return result

    if op == "concat":
        args = node.get("args")
        if not isinstance(args, list) or not args:
            raise CalculationError("ARGS_REQUIRED", "concat requires a non-empty args array.")
        sep = str(node.get("separator", ""))
        null_as = str(node.get("null_as", ""))
        parts = []
        for arg in args:
            s = _as_series(_eval(df, arg, divide_by_zero), df.index)
            parts.append(s.map(lambda x: null_as if pd.isna(x) else str(x)))
        result = parts[0].astype("string")
        for part in parts[1:]:
            result = result + sep + part.astype("string")
        return result

    if op == "if_else":
        for required in ("condition", "then", "else"):
            if required not in node:
                raise CalculationError("IF_ELSE_FIELD_REQUIRED", f"if_else requires {required}.")
        condition = _as_series(_eval(df, node["condition"], divide_by_zero), df.index)
        then = _as_series(_eval(df, node["then"], divide_by_zero), df.index)
        other = _as_series(_eval(df, node["else"], divide_by_zero), df.index)
        return pd.Series(np.where(condition.fillna(False).astype(bool), then, other), index=df.index)

    raise AssertionError("unreachable")


def apply_calculated_columns(df: pd.DataFrame, *, calculations: list[dict[str, Any]], divide_by_zero: str = "null") -> CalculatedColumnResult:
    if not isinstance(df, pd.DataFrame):
        raise CalculationError("INVALID_DATAFRAME", "Input must be a pandas DataFrame.")
    if not calculations:
        raise CalculationError("CALCULATIONS_REQUIRED", "At least one calculated column is required.")
    if divide_by_zero not in {"null", "error"}:
        raise CalculationError("INVALID_DIVIDE_BY_ZERO_POLICY", "divide_by_zero must be null or error.")

    out = df.copy(deep=True)
    summaries = []
    added = []
    overwritten = []
    names_seen = set()

    for i, calc in enumerate(calculations):
        name = calc.get("name")
        overwrite = bool(calc.get("overwrite", False))
        expression = calc.get("expression")

        if not name or not str(name).strip():
            raise CalculationError("OUTPUT_COLUMN_REQUIRED", "Calculated column requires a non-empty name.", {"calculation_index": i})
        name = str(name)
        if name in names_seen:
            raise CalculationError("DUPLICATE_OUTPUT_COLUMN", "A calculated output name may only appear once per request.", {"name": name})
        names_seen.add(name)

        exists = name in out.columns
        if exists and not overwrite:
            raise CalculationError("OUTPUT_COLUMN_EXISTS", "Calculated output column already exists.", {"name": name, "calculation_index": i})
        if expression is None:
            raise CalculationError("EXPRESSION_REQUIRED", "Calculated column requires expression.", {"calculation_index": i})

        before = out[name].copy(deep=True) if exists else None
        try:
            value = _eval(out, expression, divide_by_zero)
        except CalculationError as exc:
            exc.details = {**exc.details, "calculation_index": i, "output_column": name}
            raise

        out[name] = _as_series(value, out.index)
        if exists:
            changed = int((before.astype("string").ne(out[name].astype("string")).fillna(False)).sum())
            overwritten.append(name)
        else:
            changed = int(out[name].notna().sum())
            added.append(name)

        summaries.append(CalculationSummary(i, name, str(out[name].dtype), int(out[name].isna().sum()), exists, changed))

    return CalculatedColumnResult(
        dataframe=out,
        rows_before=len(df), rows_after=len(out),
        columns_before=len(df.columns), columns_after=len(out.columns),
        calculations=summaries, added_columns=added, overwritten_columns=overwritten,
    )
