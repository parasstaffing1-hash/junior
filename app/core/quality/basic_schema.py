from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Iterable


NULL_TOKENS = {"", "null", "none", "na", "n/a", "nan"}
INTEGER_RE = re.compile(r"^[+-]?\d+$")
FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+|\d+[eE][+-]?\d+)$")
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?$")


def is_null(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().casefold() in NULL_TOKENS)


def classify_value(value: Any) -> str:
    if is_null(value):
        return "string"
    if isinstance(value, bool):
        return "boolean"
    text = str(value).strip()
    if INTEGER_RE.fullmatch(text):
        return "integer"
    if FLOAT_RE.fullmatch(text):
        try:
            if math.isfinite(float(text)):
                return "float"
        except ValueError:
            pass
    if text.casefold() in {"true", "false"}:
        return "boolean"
    if DATETIME_RE.fullmatch(text):
        try:
            datetime.fromisoformat(text.replace("Z", "+00:00"))
            return "datetime"
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%b-%Y", "%d-%B-%Y"):
        try:
            datetime.strptime(text, fmt)
            return "date"
        except ValueError:
            pass
    return "string"


def detect_basic_schema(rows: list[dict[str, Any]], columns: Iterable[str] | None = None) -> dict[str, Any]:
    names = list(columns or (list(rows[0].keys()) if rows else []))
    results: list[dict[str, Any]] = []
    total_rows = len(rows)
    for name in names:
        values = [row.get(name) for row in rows]
        non_null = [value for value in values if not is_null(value)]
        kinds = [classify_value(value) for value in non_null]
        active = set(kinds)
        if not non_null:
            detected = "string"
            confidence = 0.0
        elif active == {"integer"}:
            detected, confidence = "integer", 1.0
        elif active <= {"integer", "float"}:
            detected, confidence = ("float" if "float" in active else "integer"), 1.0
        elif len(active) == 1:
            detected, confidence = next(iter(active)), 1.0
        else:
            detected = "string"
            confidence = round(max(kinds.count(max(active, key=kinds.count)) / len(kinds), 0.5), 4)
        unique_count = len({str(value) for value in non_null})
        results.append({
            "name": name,
            "column": name,
            "detected_type": detected,
            "physical_type": detected,
            "confidence": confidence,
            "nullable": len(non_null) != total_rows,
            "non_null_count": len(non_null),
            "null_count": total_rows - len(non_null),
            "unique_count": unique_count,
            "sample_values": [str(value) for value in non_null[:5]],
        })
    return {
        "total_rows": total_rows,
        "total_columns": len(names),
        "columns": results,
        "by_name": {item["name"]: item for item in results},
    }
