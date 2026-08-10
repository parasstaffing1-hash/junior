from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any


def _freeze(value: Any) -> Any:
    if value is None:
        return ("null", None)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, (int, float, str)):
        return (type(value).__name__, value)
    if isinstance(value, (date, datetime)):
        return (type(value).__name__, value.isoformat())
    return (type(value).__name__, repr(value))


def analyze_duplicates(rows: list[dict[str, Any]], *, key_columns: list[str] | None = None) -> dict[str, Any]:
    columns = list(key_columns or (list(rows[0].keys()) if rows else []))
    buckets: dict[tuple[Any, ...], list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        buckets[tuple(_freeze(row.get(column)) for column in columns)].append(index)
    groups = []
    for group_id, indexes in enumerate(sorted((indexes for indexes in buckets.values() if len(indexes) > 1), key=lambda item: item[0]), start=1):
        groups.append({"group_id": group_id, "occurrences": len(indexes), "row_indexes": indexes, "first_occurrence": indexes[0], "subsequent_occurrences": indexes[1:], "key": {column: rows[indexes[0]].get(column) for column in columns}})
    participants = sum(group["occurrences"] for group in groups)
    redundant = sum(group["occurrences"] - 1 for group in groups)
    return {"mode": "columns" if key_columns else "exact", "columns": columns if key_columns else [], "total_rows": len(rows), "duplicate_rows": participants, "subsequent_duplicate_rows": redundant, "redundant_duplicate_rows": redundant, "exact_duplicate_count": redundant, "duplicate_groups": len(groups), "duplicate_rate": participants / len(rows) if rows else 0.0, "groups": groups, "summary": f"{participants} rows participate in {len(groups)} duplicate group(s); {redundant} row(s) are redundant if keeping the first occurrence."}
