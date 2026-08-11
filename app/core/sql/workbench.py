from __future__ import annotations

import re
import sqlite3
from time import monotonic
from typing import Any

import pandas as pd


DISALLOWED_KEYWORDS = {
    "alter", "attach", "begin", "commit", "create", "delete", "detach",
    "drop", "insert", "load_extension", "pragma", "reindex", "release",
    "replace", "rollback", "savepoint", "truncate", "update", "vacuum",
}


class SQLWorkbenchError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _structural_sql(sql: str) -> str:
    no_block_comments = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    no_line_comments = re.sub(r"--[^\r\n]*", " ", no_block_comments)
    no_strings = re.sub(r"'(?:''|[^'])*'", "''", no_line_comments)
    return re.sub(r'"(?:""|[^"])*"', '""', no_strings)


def validate_read_only_sql(sql: Any) -> dict[str, Any]:
    if not isinstance(sql, str) or not sql.strip():
        raise SQLWorkbenchError("SQL_REQUIRED", "A SQL query is required.")
    if len(sql) > 100_000:
        raise SQLWorkbenchError("SQL_TOO_LARGE", "SQL queries are limited to 100,000 characters.")
    structural = _structural_sql(sql).strip()
    without_trailing_semicolon = structural[:-1].rstrip() if structural.endswith(";") else structural
    if ";" in without_trailing_semicolon:
        raise SQLWorkbenchError("MULTIPLE_STATEMENTS_NOT_ALLOWED", "Only one read-only SQL statement is allowed.")
    first_keyword = re.match(r"^([a-zA-Z_]+)", without_trailing_semicolon)
    if not first_keyword or first_keyword.group(1).casefold() not in {"select", "with", "explain"}:
        raise SQLWorkbenchError("READ_ONLY_SQL_REQUIRED", "Queries must start with SELECT, WITH, or EXPLAIN.")
    used_keywords = {item.casefold() for item in re.findall(r"\b[a-zA-Z_]+\b", without_trailing_semicolon)}
    blocked = sorted(DISALLOWED_KEYWORDS & used_keywords)
    if blocked:
        raise SQLWorkbenchError("UNSAFE_SQL", "The query contains a disallowed write or administrative operation.", {"keywords": blocked})
    return {"valid": True, "statement_type": first_keyword.group(1).casefold(), "read_only": True}


def execute_dataset_sql(
    frame: pd.DataFrame,
    sql: str,
    *,
    max_rows: int = 500,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    validation = validate_read_only_sql(sql)
    if not 1 <= int(max_rows) <= 5_000:
        raise SQLWorkbenchError("INVALID_ROW_LIMIT", "max_rows must be between 1 and 5,000.")
    if not 0.1 <= float(timeout_seconds) <= 30:
        raise SQLWorkbenchError("INVALID_TIMEOUT", "timeout_seconds must be between 0.1 and 30.")

    started = monotonic()
    connection = sqlite3.connect(":memory:")
    connection.set_progress_handler(lambda: 1 if monotonic() - started > timeout_seconds else 0, 1_000)
    try:
        frame.to_sql("dataset", connection, index=False, if_exists="replace")
        cursor = connection.execute(sql)
        columns = [str(item[0]) for item in (cursor.description or [])]
        raw_rows = cursor.fetchmany(int(max_rows) + 1)
        truncated = len(raw_rows) > int(max_rows)
        raw_rows = raw_rows[: int(max_rows)]
        rows = [
            {column: _jsonable(value) for column, value in zip(columns, row, strict=False)}
            for row in raw_rows
        ]
    except sqlite3.OperationalError as exc:
        message = str(exc)
        if "interrupted" in message.casefold():
            raise SQLWorkbenchError("SQL_TIMEOUT", "The SQL query exceeded the execution timeout.") from exc
        raise SQLWorkbenchError("SQL_EXECUTION_FAILED", "The SQL query could not be executed.", {"error": message}) from exc
    finally:
        connection.close()

    return {
        **validation,
        "table": "dataset",
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "max_rows": int(max_rows),
        "execution_ms": round((monotonic() - started) * 1_000, 2),
    }


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and pd.isna(value):
            return None
        return value
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        return _jsonable(value.item())
    return str(value)
