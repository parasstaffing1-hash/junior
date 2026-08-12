"""Evidence-based, approval-first data-health improvement plans."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from app.core.cleaning.recipe_engine import execute_recipe
from app.orchestration.quality_pipeline import QualityPipeline


def _rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return frame.astype(object).where(pd.notna(frame), None).to_dict(orient="records")


def _quality(frame: pd.DataFrame, *, dataset_id: str, source_version_id: str) -> dict[str, Any]:
    return QualityPipeline().analyze(
        _rows(frame),
        dataset_id=dataset_id,
        source_version_id=source_version_id,
    )


def _string_cleanup_columns(frame: pd.DataFrame) -> list[str]:
    columns: list[str] = []
    for name in frame.select_dtypes(include=["object", "string"]).columns:
        changed = False
        for value in frame[name].dropna().tolist():
            text = str(value)
            if text != text.strip() or re.search(r"\s{2,}", text) or text.strip() == "":
                changed = True
                break
        if changed:
            columns.append(str(name))
    return columns


def _numeric_coercion_columns(frame: pd.DataFrame) -> list[str]:
    columns: list[str] = []
    for name in frame.select_dtypes(include=["object", "string"]).columns:
        if "id" in str(name).casefold():
            continue
        values = frame[name].dropna()
        if len(values) < 5:
            continue
        parsed = pd.to_numeric(values, errors="coerce")
        if len(values) and float(parsed.notna().mean()) >= 0.95 and not pd.api.types.is_numeric_dtype(frame[name]):
            columns.append(str(name))
    return columns


def _date_normalization_columns(frame: pd.DataFrame) -> list[str]:
    columns: list[str] = []
    for name in frame.columns:
        if not re.search(r"date|time|timestamp", str(name), re.IGNORECASE):
            continue
        values = frame[name].dropna()
        if values.empty:
            continue
        parsed = pd.to_datetime(values, errors="coerce")
        if float(parsed.notna().mean()) < 0.95:
            continue
        non_iso = any(not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T.*)?", str(value).strip()) for value in values.head(250))
        if non_iso:
            columns.append(str(name))
    return columns


def _missing_imputation_steps(frame: pd.DataFrame) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    steps: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    for name in frame.columns:
        missing = int(frame[name].isna().sum())
        if not missing:
            continue
        rate = missing / max(1, len(frame))
        if rate > 0.30:
            review.append({"column": str(name), "reason": f"{missing:,} missing values ({rate:.1%}); automatic imputation could distort the field."})
            continue
        if pd.api.types.is_numeric_dtype(frame[name]):
            method = "median"
        else:
            method = "mode"
        steps.append({
            "type": "imputation",
            "params": {"columns": [str(name)], "method": method},
        })
    return steps, review


def build_health_improvement_plan(frame: pd.DataFrame, *, dataset_id: str, source_version_id: str) -> dict[str, Any]:
    before = _quality(frame, dataset_id=dataset_id, source_version_id=source_version_id)
    steps: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []

    string_columns = _string_cleanup_columns(frame)
    if string_columns:
        steps.append({
            "type": "string_cleaning",
            "params": {"columns": string_columns, "operations": ["trim", "collapse_whitespace", "empty_to_null"]},
        })
        recommendations.append({"id": "normalize_text", "label": "Normalize text fields", "risk": "low", "reason": "Removes leading/trailing whitespace, repeated spaces, and blank text values.", "columns": string_columns})

    numeric_columns = _numeric_coercion_columns(frame)
    if numeric_columns:
        steps.append({
            "type": "numeric_cleaning",
            "params": {"columns": numeric_columns, "to_numeric": True, "remove_commas": True, "invalid_policy": "keep_original"},
        })
        recommendations.append({"id": "coerce_numeric", "label": "Standardize numeric fields", "risk": "low", "reason": "Converts values that are overwhelmingly numeric while retaining unparseable originals for review.", "columns": numeric_columns})

    date_columns = _date_normalization_columns(frame)
    if date_columns:
        steps.append({
            "type": "date_cleaning",
            "params": {"columns": date_columns, "target_type": "date", "on_invalid": "keep_original"},
        })
        recommendations.append({"id": "normalize_dates", "label": "Normalize date fields", "risk": "low", "reason": "Converts recognizable dates into a consistent ISO date representation and retains invalid values for review.", "columns": date_columns})

    imputation_steps, review = _missing_imputation_steps(frame)
    for step in imputation_steps:
        steps.append(step)
        column = step["params"]["columns"][0]
        recommendations.append({"id": f"impute_{column}", "label": f"Fill missing {column}", "risk": "medium", "reason": f"Uses {step['params']['method']} imputation for a field with a manageable missing-value rate.", "columns": [column]})

    duplicate_count = int(frame.duplicated().sum())
    if duplicate_count:
        steps.append({"type": "duplicate_removal", "params": {"mode": "exact", "keep": "first"}})
        recommendations.append({"id": "remove_exact_duplicates", "label": "Remove exact duplicate rows", "risk": "medium", "reason": f"Keeps the first copy and removes {duplicate_count:,} exact duplicate row(s).", "columns": []})

    if not steps:
        review.append({"column": "dataset", "reason": "No low-risk cleaning change was identified. Remaining health penalties may reflect modeling, semantic confidence, cardinality, or business rules rather than dirty cells."})

    after_recipe = execute_recipe(frame, steps) if steps else None
    after = _quality(after_recipe.dataframe, dataset_id=dataset_id, source_version_id=source_version_id) if after_recipe else before
    return {
        "dataset_id": dataset_id,
        "source_version_id": source_version_id,
        "status": "IMPROVEMENTS_AVAILABLE" if steps else "NO_LOW_RISK_CHANGES",
        "approval_required": True,
        "before": {"score": before["health"]["score"], "grade": before["health"]["grade"], "issues": before["health"]["issues"]},
        "after_preview": {"score": after["health"]["score"], "grade": after["health"]["grade"], "delta": after["health"]["score"] - before["health"]["score"], "rows": len(after_recipe.dataframe) if after_recipe else len(frame), "changed_cells": after_recipe.total_changed_cells if after_recipe else 0},
        "recommendations": recommendations,
        "steps": steps,
        "manual_review": review,
        "message": "Review the proposed recipe, preview the changes, then apply it to create a new immutable dataset version." if steps else "The dataset has no low-risk automatic changes. Review business rules, semantic modeling, or targeted manual transformations next.",
    }
