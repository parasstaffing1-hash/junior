from __future__ import annotations

from typing import Any

import pandas as pd

from app.core.quality.basic_schema import detect_basic_schema
from app.core.quality.categorical_profiler import profile_categorical
from app.core.quality.duplicate_analyzer import analyze_duplicates
from app.core.quality.health_score import calculate_health
from app.core.quality.missing_analyzer import analyze_missing
from app.core.quality.numeric_profiler import profile_numeric
from app.core.quality.semantic_schema import detect_semantic_schema
from app.core.quality.validation_engine import validate_dataset
from app.core.privacy import classify_frame


DEFAULT_RULES = [
    {"column": "customer_id", "rule": "required"},
    {"column": "customer_id", "rule": "unique"},
    {"column": "age", "rule": "range", "min": 0, "max": 120},
    {"column": "email", "rule": "email"},
    {"column": "revenue", "rule": "min", "value": 0},
    {"column": "signup_date", "rule": "date"},
    {"column": "status", "rule": "allowed_values", "values": ["Active", "Inactive"]},
]


def generate_profile_rules(rows: list[dict[str, Any]], basic_schema: dict[str, Any], semantic_schema: dict[str, Any], numeric_profile: dict[str, Any], categorical_profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Create conservative validation rules from observed profiling evidence."""
    rules: list[dict[str, Any]] = []
    semantic = semantic_schema.get("by_name", {})
    numeric = numeric_profile.get("columns", {})
    categorical = categorical_profile.get("columns", {})
    total = len(rows)
    for column in basic_schema.get("columns", []):
        name = str(column["name"])
        info = semantic.get(name, {})
        values = [row.get(name) for row in rows]
        non_null = [value for value in values if value is not None and str(value).strip() != ""]
        if total and len(non_null) == total:
            rules.append({"column": name, "rule": "required", "severity": "error", "source": "profile", "reason": "No missing values were observed."})
        if info.get("semantic_type") in {"identifier"} and non_null and len({repr(value) for value in non_null}) == len(non_null):
            rules.append({"column": name, "rule": "unique", "severity": "warning", "source": "profile", "reason": "The column behaves like a unique identifier."})
        if info.get("semantic_type") == "email":
            rules.append({"column": name, "rule": "email", "severity": "error", "source": "profile", "ignore_nulls": True})
        elif info.get("semantic_type") == "date":
            rules.append({"column": name, "rule": "date", "severity": "error", "source": "profile", "ignore_nulls": True})
        if name in numeric:
            stats = numeric[name]
            minimum, maximum = stats.get("min"), stats.get("max")
            if minimum is not None and maximum is not None:
                spread = max(abs(float(maximum) - float(minimum)), 1.0)
                rules.append({"column": name, "rule": "range", "min": float(minimum) - spread * 0.25, "max": float(maximum) + spread * 0.25, "severity": "warning", "source": "profile", "reason": "Observed range with a conservative 25% margin."})
        if name in categorical and categorical[name].get("unique_count", 0) <= 20 and categorical[name].get("unique_count", 0) > 0:
            values = [item["value"] for item in categorical[name].get("frequencies", [])]
            if values:
                rules.append({"column": name, "rule": "allowed_values", "values": values, "severity": "warning", "source": "profile", "reason": "Low-cardinality values observed in the profiling sample."})
    return rules


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
        generated_rules = generate_profile_rules(rows, basic, semantic, numeric, categorical)
        if rules is not None:
            explicit_keys = {(str(rule.get("column")), str(rule.get("rule", rule.get("type", ""))).casefold()) for rule in rules}
            selected_rules = [rule for rule in generated_rules if (str(rule.get("column")), str(rule.get("rule", "")).casefold()) not in explicit_keys] + list(rules)
        else:
            selected_rules = generated_rules + [rule for rule in DEFAULT_RULES if rule["column"] in columns and (str(rule["column"]), str(rule["rule"]).casefold()) not in {(str(item.get("column")), str(item.get("rule", "")).casefold()) for item in generated_rules}]
        validation = validate_dataset(rows, dataset_id, selected_rules)
        pii = classify_frame(pd.DataFrame(rows, columns=columns)) if rows else {"columns": [], "pii_columns": [], "policy": "PII must be masked or access-restricted before external export."}
        health = calculate_health(basic_schema=basic, semantic_schema=semantic, numeric_profile=numeric, categorical_profile=categorical, missing_analysis=missing, duplicate_analysis=duplicates, validation=validation)
        preview = {"rows": rows[:max(0, preview_limit)], "limit": preview_limit, "total_rows": len(rows), "total_columns": len(columns), "columns": columns}
        return {"dataset_id": dataset_id, "source_version_id": source_version_id, "preview": preview, "basic_schema": basic, "semantic_schema": semantic, "numeric_profile": numeric, "categorical_profile": categorical, "generated_rules": generated_rules, "pii_findings": pii, "missing_analysis": missing, "duplicate_analysis": duplicates, "health": health, "validation": validation, "recommended_next_stage": "cleaning" if validation["status"] != "PASS" or health["score"] < 90 else "ready_for_analysis"}
