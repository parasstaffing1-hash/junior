from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def profile_categorical(rows: list[dict[str, Any]], basic_schema: dict[str, Any], semantic_schema: dict[str, Any]) -> dict[str, Any]:
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
            "frequencies": [{"value": key, "count": count} for key, count in counts.most_common()],
            "case_variants": case_groups,
            "whitespace_variants": whitespace_groups,
            "is_single_value": unique_count == 1 and bool(values),
            "low_variation": unique_count <= 1 or (len(values) >= 4 and unique_count / len(values) <= 0.05),
            "high_cardinality": len(values) >= 50 and unique_count / len(values) >= 0.5,
            "warnings": (["CASE_VARIANTS"] if case_groups else []) + (["WHITESPACE_VARIANTS"] if whitespace_groups else []) + (["SINGLE_VALUE_COLUMN"] if unique_count == 1 and values else []),
        }
    return {"columns": profiles, "categorical_columns": list(profiles)}
