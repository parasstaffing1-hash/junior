"""BI-readiness profiling and approval-first preparation.

This module creates a clean, explainable handoff between raw tabular data and
the existing BI report/export builders. It never silently overwrites a source
version: callers preview the proposed output and explicitly apply it to create
an immutable child version.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from app.core.intelligence.common import IntelligenceError, json_safe
from app.core.bi.semantic_model import build_semantic_model_contract


def _safe_identifier(value: Any, fallback: str = "column") -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip()).strip("_").lower()
    if not text:
        text = fallback
    if text[0].isdigit():
        text = f"column_{text}"
    return text


def _column_mapping(columns: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for index, source in enumerate(columns, 1):
        base = _safe_identifier(source, f"column_{index}")
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        mapping[str(source)] = candidate
    return mapping


def _parse_rate(series: pd.Series, parser) -> float:
    values = series.dropna()
    if values.empty:
        return 0.0
    try:
        parsed = parser(values)
        return round(float(parsed.notna().mean()), 4)
    except Exception:
        return 0.0


def _is_identifier(name: str, series: pd.Series) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", name.casefold())
    if bool(re.search(r"date|time|timestamp|month|period", normalized)):
        return False
    return bool(re.search(r"(^|_)(id|key|code|uuid|zip|postal)(_|$)", normalized)) or (not pd.api.types.is_numeric_dtype(series) and series.nunique(dropna=True) == len(series.dropna()) and len(series) > 10)


def _date_rate(series: pd.Series, name: str) -> float:
    if pd.api.types.is_numeric_dtype(series) and not re.search(r"date|time|timestamp|month|period", name, re.IGNORECASE):
        return 0.0
    return _parse_rate(series, lambda values: pd.to_datetime(values, errors="coerce", format="mixed"))


def _numeric_rate(series: pd.Series) -> float:
    if pd.api.types.is_numeric_dtype(series):
        return 1.0
    return _parse_rate(series, lambda values: pd.to_numeric(values, errors="coerce"))


def _changed_cells(before: pd.DataFrame, after: pd.DataFrame) -> int:
    common_columns = [column for column in before.columns if column in after.columns]
    if not common_columns:
        return int(len(before) * len(before.columns))
    common_rows = min(len(before), len(after))
    if common_rows == 0:
        return 0
    left = before.iloc[:common_rows][common_columns].astype("string")
    right = after.iloc[:common_rows][common_columns].astype("string")
    return int(left.ne(right).fillna(False).sum().sum())


def _preview(frame: pd.DataFrame, limit: int = 12) -> dict[str, Any]:
    sample = frame.head(max(0, min(int(limit), 50))).where(pd.notna(frame.head(max(0, min(int(limit), 50)))), None)
    return {"columns": [str(column) for column in frame.columns], "rows": json_safe(sample.to_dict(orient="records")), "total_rows": int(len(frame)), "total_columns": int(len(frame.columns)), "limit": int(limit)}


def _model_contract(profile: dict[str, Any], *, columns: list[str] | None = None, row_count: int | None = None) -> dict[str, Any]:
    mapping = profile["column_mapping"]
    selected_columns = columns or list(mapping.values())
    date = next((item for item in profile["columns"] if item["role"] == "date"), None)
    dimensions = [item for item in profile["columns"] if item["role"] == "dimension" and item["bi_name"] in selected_columns]
    measures = [item for item in profile["columns"] if item["role"] == "measure" and item["bi_name"] in selected_columns]
    dimension_contract: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    if date:
        dimension_contract.append({"table": "DimDate", "key": "Date", "source_column": date["bi_name"], "purpose": "calendar filtering and time intelligence"})
        relationships.append({"from_table": "FactData", "from_column": date["bi_name"], "to_table": "DimDate", "to_column": "Date", "cardinality": "many_to_one", "cross_filter": "single"})
    for item in dimensions[:8]:
        table_name = "Dim" + "".join(part.title() for part in item["bi_name"].split("_"))
        dimension_contract.append({"table": table_name, "key": item["bi_name"], "source_column": item["bi_name"], "purpose": "descriptive filtering and grouping"})
        relationships.append({"from_table": "FactData", "from_column": item["bi_name"], "to_table": table_name, "to_column": item["bi_name"], "cardinality": "many_to_one", "cross_filter": "single"})
    return {
        "model_type": "star_schema",
        "fact_table": {"name": "FactData", "grain": "one row per BI-ready source record", "row_count": int(row_count if row_count is not None else profile["row_count"]), "columns": selected_columns},
        "dimensions": dimension_contract,
        "measures": [{"name": f"Total {item['bi_name']}", "expression": f"SUM ( FactData[{item['bi_name']}] )", "source_column": item["bi_name"]} for item in measures],
        "relationships": relationships,
        "date_table": {"required": bool(date), "table": "DimDate", "source_column": date["bi_name"] if date else None, "time_intelligence": bool(date)},
        "validation": {"one_fact_grain": True, "single_direction_relationships": True, "explicit_measures": bool(measures), "rls": "Configure and approve in the target semantic model before publishing."},
    }


def _advanced_model_contract(profile: dict[str, Any], *, columns: list[str] | None = None) -> dict[str, Any]:
    """Create a conservative advanced model proposal from the profiled roles."""
    selected = list(columns or profile.get("column_mapping", {}).values())
    design: dict[str, Any] = {
        "grain": "one row per BI-ready source record",
        "grain_columns": list(profile.get("identifier_columns", []))[:1] or selected[:1],
        "grain_confirmed": False,
        "fact": {"name": "FactData", "columns": selected, "surrogate_key": "fact_sk"},
        "dimension_columns": [item for item in profile.get("dimension_columns", []) if item in selected],
        "measure_columns": [item for item in profile.get("measure_columns", []) if item in selected],
    }
    date_columns = [item for item in profile.get("date_columns", []) if item in selected]
    if date_columns:
        design["date_column"] = date_columns[0]
    return build_semantic_model_contract(selected, design)


def build_bi_readiness_profile(frame: pd.DataFrame, *, dataset_id: str | None = None, source_version_id: str | None = None) -> dict[str, Any]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise IntelligenceError("EMPTY_DATASET", "A non-empty dataset is required to build BI-ready data.")
    source_columns = [str(column) for column in frame.columns]
    mapping = _column_mapping(source_columns)
    descriptions: list[dict[str, Any]] = []
    date_columns: list[str] = []
    measure_columns: list[str] = []
    dimension_columns: list[str] = []
    identifier_columns: list[str] = []
    total_rows = len(frame)

    for source_name in source_columns:
        series = frame[source_name]
        missing = int(series.isna().sum())
        numeric_rate = _numeric_rate(series)
        date_rate = _date_rate(series, source_name)
        identifier = _is_identifier(source_name, series)
        normalized = re.sub(r"[^a-z0-9]+", " ", source_name.casefold()).strip()
        if date_rate >= 0.9 and not identifier:
            role = "date"
            date_columns.append(mapping[source_name])
        elif numeric_rate >= 0.95 and not identifier:
            role = "measure"
            measure_columns.append(mapping[source_name])
        elif identifier:
            role = "key"
            identifier_columns.append(mapping[source_name])
        else:
            role = "dimension"
            dimension_columns.append(mapping[source_name])
        descriptions.append({
            "source_name": source_name,
            "bi_name": mapping[source_name],
            "role": role,
            "pandas_dtype": str(series.dtype),
            "null_count": missing,
            "null_rate_pct": round(missing / max(1, total_rows) * 100, 2),
            "cardinality": int(series.nunique(dropna=True)),
            "numeric_parse_rate": numeric_rate,
            "date_parse_rate": date_rate,
            "recommended": role in {"date", "measure", "dimension"},
        })

    duplicate_rows = int(frame.duplicated().sum())
    missing_cells = int(frame.isna().sum().sum())
    missing_rate = missing_cells / max(1, len(frame) * len(frame.columns))
    score = 0
    score += 20 if len(set(mapping.values())) == len(mapping) else 0
    score += 20 if measure_columns else 0
    score += 15 if date_columns else 0
    score += 15 if dimension_columns else 0
    score += 15 if duplicate_rows == 0 else 0
    score += 15 if missing_rate <= 0.10 else 8 if missing_rate <= 0.25 else 0
    actions = [
        {"id": "standardize_column_names", "label": "Standardize column names", "risk": "low", "default_selected": True, "reason": "Creates stable BI-friendly snake_case fields without changing row meaning."},
        {"id": "trim_text_values", "label": "Trim and normalize text", "risk": "low", "default_selected": True, "reason": "Removes accidental whitespace that creates duplicate categories."},
        {"id": "coerce_numeric_values", "label": "Coerce high-confidence numeric fields", "risk": "low", "default_selected": True, "reason": "Converts fields that are at least 95% numeric; invalid values become null and remain visible in validation."},
        {"id": "normalize_date_values", "label": "Normalize date fields", "risk": "low", "default_selected": True, "reason": "Writes recognized dates in ISO YYYY-MM-DD form for reliable date relationships."},
        {"id": "add_bi_row_id", "label": "Add a BI row identifier", "risk": "low", "default_selected": not bool(identifier_columns), "reason": "Provides a stable fact-row key when no source identifier is available."},
        {"id": "remove_exact_duplicates", "label": "Remove exact duplicate rows", "risk": "medium", "default_selected": False, "reason": f"Would remove {duplicate_rows:,} exact duplicate row(s); leave off unless the source grain confirms duplicates are invalid."},
    ]
    blockers: list[dict[str, Any]] = []
    if not measure_columns:
        blockers.append({"code": "NO_MEASURE", "message": "No high-confidence numeric measure was detected; define one manually before publishing a BI model."})
    if duplicate_rows and not any(action["id"] == "remove_exact_duplicates" and action["default_selected"] for action in actions):
        blockers.append({"code": "DUPLICATE_ROWS_REVIEW", "message": "Exact duplicate rows require a source-grain decision before publishing."})
    if missing_rate > 0.25:
        blockers.append({"code": "HIGH_MISSINGNESS", "message": "More than 25% of cells are missing; automatic imputation is intentionally not applied."})
    profile = {
        "dataset_id": dataset_id,
        "source_version_id": source_version_id,
        "row_count": int(len(frame)),
        "column_count": int(len(frame.columns)),
        "column_mapping": mapping,
        "columns": descriptions,
        "date_columns": date_columns,
        "measure_columns": measure_columns,
        "dimension_columns": dimension_columns,
        "identifier_columns": identifier_columns,
        "duplicate_rows": duplicate_rows,
        "missing_cells": missing_cells,
        "readiness_score": int(score),
        "readiness_status": "BI_READY" if score >= 80 and not blockers else "REVIEW_REQUIRED",
        "actions": actions,
        "blockers": blockers,
        "warnings": ["BI-ready preparation does not infer business meaning or certify metric definitions.", "Review the fact grain, relationships, measures, and security policy before publishing to Power BI or Tableau."],
    }
    profile["model_contract"] = _model_contract(profile)
    profile["advanced_model_contract"] = _advanced_model_contract(profile)
    return json_safe(profile)


def apply_bi_readiness(frame: pd.DataFrame, profile: dict[str, Any], *, options: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise IntelligenceError("EMPTY_DATASET", "A non-empty dataset is required to create BI-ready data.")
    options = dict(options or {})
    defaults = {item["id"]: bool(item.get("default_selected")) for item in profile.get("actions", [])}
    selected = {key: bool(options.get(key, default)) for key, default in defaults.items()}
    before = frame.copy(deep=True)
    output = frame.copy(deep=True)
    mapping = profile.get("column_mapping", {})
    if selected.get("standardize_column_names", True):
        output = output.rename(columns={source: target for source, target in mapping.items() if source in output.columns})
    applied: list[str] = []
    warnings: list[dict[str, Any]] = []
    if selected.get("standardize_column_names", True):
        applied.append("standardize_column_names")
    for item in profile.get("columns", []):
        column = mapping.get(item["source_name"], item["bi_name"])
        if column not in output.columns:
            continue
        if selected.get("trim_text_values", True) and item["role"] in {"dimension", "key"}:
            if not pd.api.types.is_numeric_dtype(output[column]):
                output[column] = output[column].map(lambda value: value if pd.isna(value) else re.sub(r"\s+", " ", str(value).strip()))
        if selected.get("coerce_numeric_values", True) and item["numeric_parse_rate"] >= 0.95 and item["role"] == "measure" and not pd.api.types.is_numeric_dtype(output[column]):
            parsed = pd.to_numeric(output[column], errors="coerce")
            invalid = int((output[column].notna() & parsed.isna()).sum())
            output[column] = parsed
            if invalid:
                warnings.append({"code": "NUMERIC_VALUES_SET_NULL", "column": column, "count": invalid})
        if selected.get("normalize_date_values", True) and item["role"] == "date":
            parsed = pd.to_datetime(output[column], errors="coerce", format="mixed")
            invalid = int((output[column].notna() & parsed.isna()).sum())
            output[column] = parsed.dt.strftime("%Y-%m-%d")
            if invalid:
                warnings.append({"code": "DATE_VALUES_SET_NULL", "column": column, "count": invalid})
    if selected.get("trim_text_values", True):
        applied.append("trim_text_values")
    if selected.get("coerce_numeric_values", True) and profile.get("measure_columns"):
        applied.append("coerce_numeric_values")
    if selected.get("normalize_date_values", True) and profile.get("date_columns"):
        applied.append("normalize_date_values")
    if selected.get("remove_exact_duplicates", False):
        before_rows = len(output)
        output = output.drop_duplicates(keep="first").reset_index(drop=True)
        applied.append("remove_exact_duplicates")
        if before_rows != len(output):
            warnings.append({"code": "EXACT_DUPLICATES_REMOVED", "count": before_rows - len(output)})
    if selected.get("add_bi_row_id", False):
        if "bi_row_id" not in output.columns:
            output.insert(0, "bi_row_id", range(1, len(output) + 1))
            applied.append("add_bi_row_id")
    final_profile = build_bi_readiness_profile(output, dataset_id=profile.get("dataset_id"), source_version_id=profile.get("source_version_id"))
    final_profile["model_contract"] = _model_contract(final_profile, columns=list(output.columns), row_count=len(output))
    final_profile["advanced_model_contract"] = _advanced_model_contract(final_profile, columns=list(output.columns))
    return {
        "dataframe": output,
        "execution": {
            "rows_before": int(len(before)),
            "rows_after": int(len(output)),
            "columns_before": int(len(before.columns)),
            "columns_after": int(len(output.columns)),
            "changed_cells": _changed_cells(before, output),
            "applied_actions": list(dict.fromkeys(applied)),
            "selected_actions": selected,
            "warnings": warnings,
        },
        "profile": final_profile,
        "preview": _preview(output),
        "model_contract": final_profile["model_contract"],
    }


__all__ = ["apply_bi_readiness", "build_bi_readiness_profile"]
