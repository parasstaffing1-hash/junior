"""Deterministic, guarded natural-language-to-SQL templates.

This module deliberately emits a small, inspectable SQL subset. It is useful
without an LLM and keeps every generated statement behind the existing
read-only validator before execution.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from app.core.sql.workbench import SQLWorkbenchError, execute_dataset_sql, validate_read_only_sql


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise SQLWorkbenchError("INVALID_COLUMN", "The generated identifier is not safe.", {"column": value})
    return value


def _column_candidates(question: str, columns: list[str]) -> list[str]:
    lowered = question.casefold()
    ranked: list[tuple[int, str]] = []
    for column in columns:
        tokens = [token for token in re.split(r"[^a-zA-Z0-9]+", str(column).casefold()) if token]
        score = 0
        if str(column).casefold() in lowered:
            score += 100
        score += sum(10 for token in tokens if len(token) > 2 and re.search(rf"\b{re.escape(token)}\b", lowered))
        ranked.append((score, str(column)))
    return [column for score, column in sorted(ranked, key=lambda item: (-item[0], item[1])) if score > 0]


def _resolve_column(question: str, columns: list[str], *, exclude: set[str] | None = None, preferred_types: set[str] | None = None, frame: pd.DataFrame | None = None) -> str | None:
    exclude = exclude or set()
    candidates = [column for column in _column_candidates(question, columns) if column not in exclude]
    if preferred_types and frame is not None:
        typed = [column for column in candidates if any(kind in str(frame[column].dtype).casefold() for kind in preferred_types)]
        if typed:
            candidates = typed
    return candidates[0] if candidates else None


def generate_sql(question: str, frame: pd.DataFrame) -> dict[str, Any]:
    if not isinstance(question, str) or not question.strip():
        raise SQLWorkbenchError("QUESTION_REQUIRED", "A natural-language question is required.")
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise SQLWorkbenchError("DATASET_REQUIRED", "A non-empty dataset is required.")
    text = question.strip()
    lowered = text.casefold()
    columns = [str(column) for column in frame.columns]
    numeric = {column for column in columns if pd.api.types.is_numeric_dtype(frame[column])}

    number_match = re.search(r"\btop\s+(\d+)\b", lowered)
    limit = max(1, min(int(number_match.group(1)), 500)) if number_match else 10
    measure = _resolve_column(lowered, columns, preferred_types={"int", "float", "decimal", "double"}, frame=frame)
    dimension = _resolve_column(lowered, columns, exclude={measure} if measure else set(), frame=frame)

    if any(token in lowered for token in ("duplicate", "duplicates", "repeated")):
        column = _resolve_column(lowered, columns, frame=frame) or columns[0]
        safe = _identifier(column)
        sql = f'SELECT "{safe}" AS duplicate_value, COUNT(*) AS duplicate_count FROM dataset GROUP BY "{safe}" HAVING COUNT(*) > 1 ORDER BY duplicate_count DESC'
        intent = "duplicate_detection"
    elif "top" in lowered and measure and dimension:
        safe_measure, safe_dimension = _identifier(measure), _identifier(dimension)
        sql = f'SELECT "{safe_dimension}" AS dimension, SUM("{safe_measure}") AS total_{safe_measure} FROM dataset GROUP BY "{safe_dimension}" ORDER BY total_{safe_measure} DESC LIMIT {limit}'
        intent = "top_n_by_measure"
    elif any(token in lowered for token in ("average", "avg", "mean")) and measure:
        safe = _identifier(measure)
        sql = f'SELECT AVG("{safe}") AS average_{safe}, COUNT("{safe}") AS non_null_count FROM dataset'
        intent = "average_measure"
    elif any(token in lowered for token in ("total", "sum", "revenue", "sales", "amount")) and measure:
        safe = _identifier(measure)
        sql = f'SELECT SUM("{safe}") AS total_{safe}, COUNT("{safe}") AS row_count FROM dataset'
        intent = "total_measure"
    elif any(token in lowered for token in ("by", "per", "grouped", "group")) and measure and dimension:
        safe_measure, safe_dimension = _identifier(measure), _identifier(dimension)
        sql = f'SELECT "{safe_dimension}" AS dimension, SUM("{safe_measure}") AS total_{safe_measure}, COUNT(*) AS row_count FROM dataset GROUP BY "{safe_dimension}" ORDER BY total_{safe_measure} DESC'
        intent = "grouped_measure"
    else:
        raise SQLWorkbenchError(
            "NL_QUERY_NOT_SUPPORTED",
            "No safe deterministic template matched the question. Specify a top-N, grouped, total, average, or duplicate question using column names.",
            {"supported_intents": ["top N <dimension> by <measure>", "total <measure>", "average <measure>", "<measure> by <dimension>", "find duplicate <column>"]},
        )

    validation = validate_read_only_sql(sql)
    return {"question": text, "intent": intent, "sql": sql, "validation": validation, "selected_columns": {"measure": measure, "dimension": dimension}}


def answer_question(question: str, frame: pd.DataFrame, *, max_rows: int = 500, timeout_seconds: float = 5.0) -> dict[str, Any]:
    generated = generate_sql(question, frame)
    execution = execute_dataset_sql(frame, generated["sql"], max_rows=max_rows, timeout_seconds=timeout_seconds)
    return {**generated, "execution": execution, "answer": {"columns": execution["columns"], "rows": execution["rows"], "row_count": execution["row_count"]}}


__all__ = ["answer_question", "generate_sql"]
