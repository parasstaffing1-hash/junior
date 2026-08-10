from __future__ import annotations

from typing import Any

from app.core.quality.basic_schema import detect_basic_schema
from app.core.quality.categorical_profiler import profile_categorical
from app.core.quality.duplicate_analyzer import analyze_duplicates
from app.core.quality.health_score import calculate_health
from app.core.quality.missing_analyzer import analyze_missing
from app.core.quality.numeric_profiler import profile_numeric
from app.core.quality.semantic_schema import detect_semantic_schema
from app.core.quality.validation_engine import validate_dataset


DEFAULT_RULES = [
    {"column": "customer_id", "rule": "required"},
    {"column": "customer_id", "rule": "unique"},
    {"column": "age", "rule": "range", "min": 0, "max": 120},
    {"column": "email", "rule": "email"},
    {"column": "revenue", "rule": "min", "value": 0},
    {"column": "signup_date", "rule": "date"},
    {"column": "status", "rule": "allowed_values", "values": ["Active", "Inactive"]},
]


class QualityPipeline:
    """Runs Tools 2-10 as pure functions over one immutable V1 row snapshot."""

    def analyze(self, rows: list[dict[str, Any]], *, dataset_id: str, source_version_id: str, preview_limit: int = 5, rules: list[dict[str, Any]] | None = None, columns: list[str] | None = None) -> dict[str, Any]:
        columns = list(columns or (list(rows[0].keys()) if rows else []))
        basic = detect_basic_schema(rows, columns)
        semantic = detect_semantic_schema(rows, basic)
        numeric = profile_numeric(rows, basic, semantic)
        categorical = profile_categorical(rows, basic, semantic)
        missing = analyze_missing(rows, columns)
        duplicates = analyze_duplicates(rows)
        validation = validate_dataset(rows, dataset_id, rules if rules is not None else [rule for rule in DEFAULT_RULES if rule["column"] in columns])
        health = calculate_health(basic_schema=basic, semantic_schema=semantic, numeric_profile=numeric, categorical_profile=categorical, missing_analysis=missing, duplicate_analysis=duplicates, validation=validation)
        preview = {"rows": rows[:max(0, preview_limit)], "limit": preview_limit, "total_rows": len(rows), "total_columns": len(columns), "columns": columns}
        return {"dataset_id": dataset_id, "source_version_id": source_version_id, "preview": preview, "basic_schema": basic, "semantic_schema": semantic, "numeric_profile": numeric, "categorical_profile": categorical, "missing_analysis": missing, "duplicate_analysis": duplicates, "health": health, "validation": validation, "recommended_next_stage": "cleaning" if validation["status"] != "PASS" or health["score"] < 90 else "ready_for_analysis"}
