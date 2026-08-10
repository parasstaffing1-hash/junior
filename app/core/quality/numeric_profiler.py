from __future__ import annotations

import math
import statistics
from typing import Any


def _number(value: Any) -> float | int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "")
    try:
        result = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return int(result) if result.is_integer() and "." not in text else result


def _quantile(values: list[float], q: float) -> float:
    if len(values) == 1:
        return values[0]
    pos = (len(values) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def _stats(values: list[float | int], total_count: int) -> dict[str, Any]:
    if not values:
        return {"count": total_count, "non_null_count": 0, "null_count": total_count, "null_percentage": 100.0, "sum": None, "mean": None, "median": None, "min": None, "max": None, "range": None, "std_dev": None, "variance": None, "q1": None, "q3": None, "iqr": None, "unique_count": 0, "zero_count": 0, "negative_count": 0, "positive_count": 0, "skewness": None, "kurtosis": None}
    ordered = sorted(float(value) for value in values)
    mean = statistics.fmean(ordered)
    variance = statistics.pvariance(ordered)
    std_dev = math.sqrt(variance)
    skewness = None
    kurtosis = None
    if std_dev > 0 and len(ordered) >= 3:
        z = [(value - mean) / std_dev for value in ordered]
        skewness = sum(item ** 3 for item in z) / len(z)
        if len(ordered) >= 4:
            kurtosis = sum(item ** 4 for item in z) / len(z) - 3.0
    q1, median, q3 = _quantile(ordered, .25), _quantile(ordered, .5), _quantile(ordered, .75)
    total = sum(values)
    total = int(total) if all(isinstance(value, int) for value in values) else float(total)
    return {"count": total_count, "non_null_count": len(values), "null_count": total_count - len(values), "null_percentage": round((total_count - len(values)) / total_count * 100, 6) if total_count else 0.0, "sum": total, "mean": mean, "median": median, "min": min(values), "max": max(values), "range": max(values) - min(values), "std_dev": std_dev, "variance": variance, "q1": q1, "q3": q3, "iqr": q3 - q1, "unique_count": len(set(values)), "zero_count": sum(value == 0 for value in values), "negative_count": sum(value < 0 for value in values), "positive_count": sum(value > 0 for value in values), "skewness": skewness, "kurtosis": kurtosis}


def profile_numeric(rows: list[dict[str, Any]], basic_schema: dict[str, Any], semantic_schema: dict[str, Any]) -> dict[str, Any]:
    semantic = semantic_schema.get("by_name", {})
    profiles: dict[str, dict[str, Any]] = {}
    for column in basic_schema.get("columns", []):
        name = column["name"]
        sem = semantic.get(name, {}).get("semantic_type")
        if sem in {"identifier", "postal_code", "phone_number", "boolean"}:
            continue
        values = [_number(row.get(name)) for row in rows]
        numeric_values = [value for value in values if value is not None]
        numeric_ratio = len(numeric_values) / len(rows) if rows else 0.0
        if column["detected_type"] not in {"integer", "float"} and sem not in {"currency", "percentage", "age"} and numeric_ratio < 0.8:
            continue
        profiles[name] = _stats(numeric_values, len(rows))
    return {"columns": profiles, "numeric_columns": list(profiles)}
