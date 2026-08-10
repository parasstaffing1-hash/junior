from __future__ import annotations

from typing import Any


def _grade(score: int) -> str:
    if score >= 90:
        return "EXCELLENT"
    if score >= 75:
        return "GOOD"
    if score >= 60:
        return "FAIR"
    if score >= 40:
        return "POOR"
    return "CRITICAL"


def calculate_health(*, basic_schema: dict[str, Any], semantic_schema: dict[str, Any], numeric_profile: dict[str, Any], categorical_profile: dict[str, Any], missing_analysis: dict[str, Any], duplicate_analysis: dict[str, Any], validation: dict[str, Any] | None = None) -> dict[str, Any]:
    total_columns = max(1, basic_schema.get("total_columns", 0))
    missing_penalty = min(30, missing_analysis.get("missing_percentage", 0.0) * 0.4)
    duplicate_penalty = min(30, duplicate_analysis.get("redundant_duplicate_rows", 0) / max(1, duplicate_analysis.get("total_rows", 0)) * 100 * 0.6)
    schema_penalty = sum(1 for item in basic_schema.get("columns", []) if item.get("confidence", 0) < 0.75) * 3.0
    semantic_penalty = sum(1 for item in semantic_schema.get("columns", []) if item.get("confidence", 0) < 0.7) * 2.0
    cardinality_penalty = sum(2 for item in categorical_profile.get("columns", {}).values() if item.get("high_cardinality"))
    cardinality_penalty += sum(3 for item in categorical_profile.get("columns", {}).values() if item.get("low_variation"))
    suspicious_numeric_penalty = 0.0
    suspicious_numeric: list[dict[str, Any]] = []
    for name, profile in numeric_profile.get("columns", {}).items():
        if profile.get("negative_count", 0) > 0:
            suspicious_numeric_penalty += 3
            suspicious_numeric.append({"column": name, "code": "NEGATIVE_VALUES"})
        if name.casefold() == "age" and (profile.get("max") is not None and profile["max"] > 120):
            suspicious_numeric_penalty += 5
            suspicious_numeric.append({"column": name, "code": "AGE_OUT_OF_RANGE"})
    validation_penalty = 0.0
    if validation:
        validation_penalty = min(20, validation.get("error_count", 0) * 1.5)
    components = {"missing_penalty": round(missing_penalty, 6), "duplicate_penalty": round(duplicate_penalty, 6), "schema_penalty": round(schema_penalty, 6), "semantic_penalty": round(semantic_penalty, 6), "cardinality_penalty": round(cardinality_penalty, 6), "numeric_penalty": round(suspicious_numeric_penalty, 6), "validation_penalty": round(validation_penalty, 6)}
    score = max(0, min(100, round(100 - sum(components.values()))))
    issues = []
    if missing_analysis.get("missing_cells", 0):
        issues.append({"code": "MISSING_VALUES", "severity": "MEDIUM", "value": missing_analysis["missing_cells"]})
    if duplicate_analysis.get("duplicate_groups", 0):
        issues.append({"code": "DUPLICATES", "severity": "HIGH", "value": duplicate_analysis["duplicate_groups"]})
    issues.extend(suspicious_numeric)
    return {"score": score, "health_score": score, "grade": _grade(score), "components": components, **components, "issues": issues, "within_bounds": 0 <= score <= 100}
