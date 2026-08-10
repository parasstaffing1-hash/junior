from __future__ import annotations

import math
from collections import Counter
from typing import Any


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def analyze_missing(rows: list[dict[str, Any]], columns: list[str] | None = None) -> dict[str, Any]:
    names = list(columns or (list(rows[0].keys()) if rows else []))
    total_rows, total_columns = len(rows), len(names)
    counts = Counter(name for row in rows for name in names if _missing(row.get(name)))
    column_results = []
    for name in names:
        count = counts[name]
        rate = count / total_rows if total_rows else 0.0
        column_results.append({"name": name, "missing_count": count, "missing_percentage": round(rate * 100, 6), "missing_rate": rate})
    missing_cells = sum(counts.values())
    total_cells = total_rows * total_columns
    patterns = Counter(tuple(name for name in names if _missing(row.get(name))) for row in rows)
    return {"total_rows": total_rows, "total_columns": total_columns, "total_cells": total_cells, "missing_cells": missing_cells, "total_missing_cells": missing_cells, "missing_percentage": round(missing_cells / total_cells * 100, 6) if total_cells else 0.0, "overall_missing_rate": missing_cells / total_cells if total_cells else 0.0, "columns": column_results, "by_name": {item["name"]: item for item in column_results}, "patterns": [{"columns": list(key), "row_count": count} for key, count in patterns.items() if key]}
