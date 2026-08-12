from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def profile_categorical(rows: list[dict[str, Any]], basic_schema: dict[str, Any], semantic_schema: dict[str, Any], *, max_frequency_values: int = 100) -> dict[str, Any]:
    if max_frequency_values < 1:
        raise ValueError("max_frequency_values must be at least 1")
    semantic = semantic_schema.get("by_name", {})
    profiles: dict[str, dict[str, Any]] = {}
    for column in basic_schema.get("columns", []):
        name = column["name"]
        sem = semantic.get(name, {}).get("semantic_type")
        if sem not in {"category", "person_name", "free_text"} and column["detected_type"] not in {"string", "boolean"}:
            continue
        values = [row.get(name) for row in rows if row.get(name) is not None and str(row.get(name)) != ""]
        counts = Counter(str(value) for value in values)
        variants: dict[str, list[str]] = defaultdict(list)
        whitespace_variants: dict[str, list[str]] = defaultdict(list)
        for value in counts:
            variants[value.casefold()].append(value)
            whitespace_variants[value.strip().casefold()].append(value)
        case_groups = [sorted(group) for group in variants.values() if len(group) > 1]
        whitespace_groups = [sorted(group) for group in whitespace_variants.values() if len(group) > 1]
        unique_count = len(counts)
        profiles[name] = {
            "name": name,
            "count": len(rows),
            "non_null_count": len(values),
            "null_count": len(rows) - len(values),
            "unique_count": unique_count,
            "cardinality_ratio": unique_count / len(values) if values else 0.0,
            "frequencies": [{"value": key, "count": count} for key, count in counts.most_common(max_frequency_values)],
            "frequency_values_total": unique_count,
            "frequency_values_returned": min(unique_count, max_frequency_values),
            "frequency_values_truncated": unique_count > max_frequency_values,
            "case_variants": case_groups,
            "whitespace_variants": whitespace_groups,
            "is_single_value": unique_count == 1 and bool(values),
            "low_variation": unique_count <= 1 or (len(values) >= 4 and unique_count / len(values) <= 0.05),
            "high_cardinality": len(values) >= 50 and unique_count / len(values) >= 0.5,
            "warnings": (["CASE_VARIANTS"] if case_groups else []) + (["WHITESPACE_VARIANTS"] if whitespace_groups else []) + (["SINGLE_VALUE_COLUMN"] if unique_count == 1 and values else []),
        }
    return {"columns": profiles, "categorical_columns": list(profiles)}
