"""Bounded, reproducible notebook operations.

The runtime deliberately exposes a data-analysis DSL instead of arbitrary code
execution. This keeps a shared production API safe while still covering the
common Python/R/SQL notebook workflow: inspect, summarize, compare and group.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


ALLOWED_OPERATIONS = {"profile", "head", "describe", "missingness", "groupby_sum", "value_counts"}


class NotebookRuntimeError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


def _safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe(v) for v in value]
    if pd.isna(value) if not isinstance(value, (dict, list, tuple)) else False:
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except (ValueError, TypeError):
            pass
    return value


def _columns(frame: pd.DataFrame, requested: Any = None) -> list[str]:
    columns = [str(item) for item in (requested or frame.columns.tolist())]
    missing = sorted(set(columns) - {str(item) for item in frame.columns})
    if missing:
        raise NotebookRuntimeError("COLUMN_NOT_FOUND", "A notebook cell references an unknown column.", {"columns": missing})
    return columns


def execute_notebook(frame: pd.DataFrame, cells: list[dict[str, Any]], *, max_rows: int = 500) -> dict[str, Any]:
    if not isinstance(cells, list) or not cells or len(cells) > 50:
        raise NotebookRuntimeError("CELLS_REQUIRED", "A notebook must contain between 1 and 50 cells.")
    limit = max(1, min(int(max_rows), 5000))
    results: list[dict[str, Any]] = []
    for position, cell in enumerate(cells, start=1):
        if not isinstance(cell, dict):
            raise NotebookRuntimeError("INVALID_CELL", "Each notebook cell must be a JSON object.", {"position": position})
        operation = str(cell.get("operation", "")).casefold()
        if operation not in ALLOWED_OPERATIONS:
            raise NotebookRuntimeError("UNSUPPORTED_OPERATION", "Notebook operation is not allowlisted.", {"operation": operation, "allowed": sorted(ALLOWED_OPERATIONS)})
        args = cell.get("args") or {}
        if not isinstance(args, dict):
            raise NotebookRuntimeError("INVALID_CELL_ARGS", "Cell args must be a JSON object.", {"position": position})
        if operation == "profile":
            result = {"row_count": int(len(frame)), "column_count": int(len(frame.columns)), "columns": [{"name": str(c), "dtype": str(frame[c].dtype), "null_count": int(frame[c].isna().sum()), "distinct_count": int(frame[c].nunique(dropna=True))} for c in frame.columns]}
        elif operation == "head":
            columns = _columns(frame, args.get("columns"))
            head = frame[columns].head(min(limit, max(1, int(args.get("limit", 20)))))
            result = {"columns": columns, "rows": head.where(pd.notna(head), None).to_dict(orient="records")}
        elif operation == "describe":
            columns = _columns(frame, args.get("columns"))
            numeric = frame[columns].select_dtypes(include="number")
            result = {"columns": list(numeric.columns), "summary": _safe(numeric.describe().round(6).where(pd.notna(numeric.describe()), None).to_dict())}
        elif operation == "missingness":
            columns = _columns(frame, args.get("columns"))
            result = {"columns": [{"name": c, "null_count": int(frame[c].isna().sum()), "null_rate": round(float(frame[c].isna().mean()), 6)} for c in columns]}
        elif operation == "groupby_sum":
            by = str(args.get("by", ""))
            value = str(args.get("value", ""))
            _columns(frame, [by, value])
            grouped = frame.groupby(by, dropna=False, as_index=False)[value].sum().sort_values(value, ascending=False).head(limit)
            result = {"group_by": by, "measure": value, "rows": grouped.where(pd.notna(grouped), None).to_dict(orient="records")}
        else:
            column = str(args.get("column", ""))
            _columns(frame, [column])
            counts = frame[column].value_counts(dropna=False).head(limit).rename_axis(column).reset_index(name="count")
            result = {"column": column, "rows": counts.where(pd.notna(counts), None).to_dict(orient="records")}
        results.append({"cell_id": str(cell.get("id") or f"cell-{position}"), "operation": operation, "result": _safe(result)})
    return {"cell_count": len(results), "max_rows": limit, "operations": sorted({item["operation"] for item in results}), "cells": results}
