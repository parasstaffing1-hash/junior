"""Approval-first semantic-model design and validation.

The model contract is intentionally independent of a vendor desktop or cloud
service.  It gives an analyst/BI developer a deterministic design artifact,
validates the structural invariants locally, and leaves business meaning and
publication approval explicit.
"""

from __future__ import annotations

import re
from typing import Any


class SemanticModelError(ValueError):
    """A model design cannot be represented safely."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _identifier(value: Any, fallback: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip()).strip("_")
    if not text:
        text = fallback
    if text[0].isdigit():
        text = f"_{text}"
    return text


def _name(value: Any, fallback: str) -> str:
    return _identifier(value, fallback)


def _items(value: Any, label: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SemanticModelError("INVALID_MODEL_LIST", f"{label} must be a list of objects.")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise SemanticModelError("INVALID_MODEL_ITEM", f"{label}[{index}] must be an object.")
        result.append(dict(item))
    return result


def _columns(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SemanticModelError("INVALID_MODEL_COLUMNS", f"{label} must be a list of column names.")
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def _require(value: Any, code: str, message: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise SemanticModelError(code, message)
    return text


def _quoted_table(name: str) -> str:
    return "[" + name.replace("]", "]]" ) + "]"


def _dax_measure(name: str, expression: str, description: str) -> dict[str, str]:
    return {"name": name, "expression": expression, "description": description}


def _sql_ddl(contract: dict[str, Any]) -> str:
    fact = contract["fact_table"]
    lines = [
        "-- Generated reference DDL. Review data types, retention and constraints before execution.",
        f"CREATE TABLE {fact['name']} (",
        f"    {fact['surrogate_key']} bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,",
    ]
    for column in fact["columns"]:
        if column == fact["surrogate_key"]:
            continue
        lines.append(f"    {column} text,")
    lines.extend([
        f"    CONSTRAINT uq_{fact['name']}_grain UNIQUE ({', '.join(fact['grain_columns'])})",
        ");",
    ])
    for dimension in contract["dimensions"]:
        lines.extend([
            "",
            f"CREATE TABLE {dimension['name']} (",
            f"    {dimension['surrogate_key']} bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,",
            f"    {dimension['natural_key']} text NOT NULL,",
        ])
        for column in dimension["attributes"]:
            if column not in {dimension["natural_key"], dimension["surrogate_key"]}:
                lines.append(f"    {column} text,")
        if dimension["scd_strategy"] == "type_2":
            lines.extend([
                "    valid_from timestamptz NOT NULL,",
                "    valid_to timestamptz,",
                "    is_current boolean NOT NULL DEFAULT true,",
            ])
        lines.extend([
            f"    CONSTRAINT uq_{dimension['name']}_natural UNIQUE ({dimension['natural_key']}, {dimension['surrogate_key']})",
            ");",
            f"CREATE INDEX ix_{dimension['name']}_{dimension['natural_key']} ON {dimension['name']} ({dimension['natural_key']});",
        ])
    for bridge in contract["bridges"]:
        lines.extend([
            "",
            f"CREATE TABLE {bridge['name']} (",
            f"    {bridge['left_key']} bigint NOT NULL,",
            f"    {bridge['right_key']} bigint NOT NULL,",
            f"    CONSTRAINT pk_{bridge['name']} PRIMARY KEY ({bridge['left_key']}, {bridge['right_key']})",
            ");",
            f"CREATE INDEX ix_{bridge['name']}_{bridge['right_key']} ON {bridge['name']} ({bridge['right_key']});",
        ])
    return "\n".join(lines) + "\n"


def _validation(
    *,
    source_columns: list[str],
    fact: dict[str, Any],
    dimensions: list[dict[str, Any]],
    dates: list[dict[str, Any]],
    bridges: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    measures: list[dict[str, Any]],
    grain_confirmed: bool,
    storage_mode: str,
    aggregations: list[dict[str, Any]],
    field_parameters: list[dict[str, Any]],
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    source_set = set(source_columns)
    table_names = {fact["name"], *(item["name"] for item in dimensions), *(item["name"] for item in dates), *(item["name"] for item in bridges)}

    if storage_mode not in {"Import", "DirectQuery", "DirectLake", "Composite"}:
        errors.append({"code": "INVALID_STORAGE_MODE", "message": "storage_mode must be Import, DirectQuery, DirectLake or Composite."})
    if storage_mode == "DirectLake":
        warnings.append({"code": "DIRECT_LAKE_EXTERNAL_GATE", "message": "Validate Fabric capacity, OneLake permissions and fallback behavior in the target tenant."})
    if storage_mode == "Composite":
        warnings.append({"code": "COMPOSITE_MODEL_EXTERNAL_GATE", "message": "Validate dual-storage relationships, aggregation precedence and DirectQuery latency in the target engine."})
    for aggregation in aggregations:
        if aggregation.get("detail_table") not in table_names:
            errors.append({"code": "AGGREGATION_DETAIL_TABLE_NOT_FOUND", "aggregation": aggregation})
        missing_group = sorted(set(aggregation.get("group_by", [])) - source_set)
        if missing_group:
            warnings.append({"code": "AGGREGATION_GROUP_COLUMN_REVIEW", "aggregation": aggregation.get("name"), "columns": missing_group})
    parameter_names = [str(item.get("name")) for item in field_parameters]
    if len(set(parameter_names)) != len(parameter_names):
        errors.append({"code": "DUPLICATE_FIELD_PARAMETER_NAME", "message": "Field parameter names must be unique."})

    if not grain_confirmed:
        warnings.append({"code": "GRAIN_REQUIRES_APPROVAL", "message": "Confirm the fact grain with the business owner before publishing."})
    if not fact["grain_columns"]:
        errors.append({"code": "FACT_GRAIN_REQUIRED", "message": "The fact table must declare at least one grain column."})
    missing_fact = sorted(set(fact["columns"]) - source_set - {fact["surrogate_key"]})
    if missing_fact:
        errors.append({"code": "FACT_COLUMNS_NOT_FOUND", "message": "Fact columns are not present in the source contract.", "columns": missing_fact})
    missing_grain = sorted(set(fact["grain_columns"]) - set(fact["columns"]))
    if missing_grain:
        errors.append({"code": "GRAIN_COLUMNS_NOT_IN_FACT", "message": "Every grain column must be a fact column.", "columns": missing_grain})
    if len({item["name"] for item in dimensions}) != len(dimensions):
        errors.append({"code": "DUPLICATE_DIMENSION_NAME", "message": "Dimension table names must be unique."})
    if len(table_names) != 1 + len(dimensions) + len(dates) + len(bridges):
        errors.append({"code": "DUPLICATE_TABLE_NAME", "message": "Fact, dimension, date and bridge table names must be unique."})

    for dimension in dimensions:
        if dimension["natural_key"] not in dimension["attributes"] and dimension["natural_key"] not in source_set:
            warnings.append({"code": "NATURAL_KEY_NOT_IN_SOURCE", "table": dimension["name"], "column": dimension["natural_key"]})
        if dimension["scd_strategy"] not in {"none", "type_1", "type_2"}:
            errors.append({"code": "INVALID_SCD_STRATEGY", "table": dimension["name"], "strategy": dimension["scd_strategy"]})
        if dimension["scd_strategy"] == "type_2":
            required_history = {"valid_from", "valid_to", "is_current"}
            if not required_history.issubset(set(dimension["history_columns"])):
                errors.append({"code": "SCD2_COLUMNS_REQUIRED", "table": dimension["name"], "columns": sorted(required_history)})

    for item in dates:
        if item["source_column"] not in fact["columns"]:
            errors.append({"code": "DATE_ROLE_NOT_IN_FACT", "table": item["name"], "column": item["source_column"]})
    for bridge in bridges:
        if not bridge["left_table"] or not bridge["right_table"] or bridge["left_table"] not in table_names or bridge["right_table"] not in table_names:
            errors.append({"code": "BRIDGE_TARGET_NOT_FOUND", "table": bridge["name"], "left_table": bridge["left_table"], "right_table": bridge["right_table"]})
        if bridge["left_table"] == bridge["right_table"]:
            errors.append({"code": "BRIDGE_TARGETS_MUST_DIFFER", "table": bridge["name"]})
    for relationship in relationships:
        if relationship["from_table"] not in table_names or relationship["to_table"] not in table_names:
            errors.append({"code": "RELATIONSHIP_TARGET_NOT_FOUND", "relationship": relationship})
        if relationship["cardinality"] == "many_to_many" and relationship["via_bridge"] is None:
            errors.append({"code": "MANY_TO_MANY_BRIDGE_REQUIRED", "relationship": relationship})
        if relationship["cross_filter"] not in {"single", "both"}:
            errors.append({"code": "INVALID_CROSS_FILTER", "relationship": relationship})
    if not measures:
        warnings.append({"code": "EXPLICIT_MEASURES_REQUIRED", "message": "Define at least one business-approved measure before publishing."})
    return {
        "status": "VALID" if not errors and grain_confirmed else "REVIEW_REQUIRED",
        "errors": errors,
        "warnings": warnings,
        "checks": {
            "fact_grain_declared": bool(fact["grain_columns"]),
            "grain_confirmed": bool(grain_confirmed),
            "unique_table_names": len(table_names) == 1 + len(dimensions) + len(dates) + len(bridges),
            "relationships_target_known_tables": not any(item["code"] == "RELATIONSHIP_TARGET_NOT_FOUND" for item in errors),
            "scd2_history_complete": not any(item["code"] == "SCD2_COLUMNS_REQUIRED" for item in errors),
            "many_to_many_uses_bridge": not any(item["code"] == "MANY_TO_MANY_BRIDGE_REQUIRED" for item in errors),
            "explicit_measures": bool(measures),
            "storage_mode_valid": storage_mode in {"Import", "DirectQuery", "DirectLake", "Composite"},
            "aggregation_tables_known": not any(item["code"] == "AGGREGATION_DETAIL_TABLE_NOT_FOUND" for item in errors),
            "field_parameter_names_unique": len(set(parameter_names)) == len(parameter_names),
        },
    }


def build_semantic_model_contract(columns: list[str], design: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build and validate a semantic-model contract from source columns and design choices."""
    source_columns = []
    for value in columns or []:
        text = str(value).strip()
        if text and text not in source_columns:
            source_columns.append(text)
    if not source_columns:
        raise SemanticModelError("SOURCE_COLUMNS_REQUIRED", "At least one source column is required.")
    design = dict(design or {})
    fact_design = dict(design.get("fact") or {})
    fact_name = _name(fact_design.get("name") or design.get("fact_table"), "FactData")
    fact_columns = _columns(fact_design.get("columns") or design.get("fact_columns"), "fact.columns") or list(source_columns)
    grain_columns = _columns(fact_design.get("grain_columns") or design.get("grain_columns"), "fact.grain_columns")
    if not grain_columns:
        grain_columns = [fact_columns[0]] if fact_columns else []
    fact_surrogate_key = _identifier(fact_design.get("surrogate_key"), "fact_sk")
    fact = {
        "name": fact_name,
        "grain": _require(fact_design.get("grain") or design.get("grain"), "FACT_GRAIN_REQUIRED", "A business-readable fact grain is required, for example 'one row per order line'."),
        "grain_columns": grain_columns,
        "columns": list(dict.fromkeys([fact_surrogate_key, *fact_columns])),
        "surrogate_key": fact_surrogate_key,
        "degenerate_dimensions": _columns(fact_design.get("degenerate_dimensions") or design.get("degenerate_dimensions"), "degenerate_dimensions"),
    }
    raw_dimensions = _items(design.get("dimensions"), "dimensions")
    if not raw_dimensions:
        raw_dimensions = [{"source_column": item, "name": f"Dim{_name(item, 'Attribute')}", "natural_key": item} for item in _columns(design.get("dimension_columns"), "dimension_columns")]
    dimensions: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_dimensions, 1):
        source_column = str(raw.get("source_column") or raw.get("natural_key") or "").strip()
        table_name = _name(raw.get("name"), f"Dim{index}")
        natural_key = _identifier(raw.get("natural_key") or source_column, f"{table_name.lower()}_nk")
        surrogate_key = _identifier(raw.get("surrogate_key"), f"{table_name.lower()}_sk")
        attributes = _columns(raw.get("attributes"), f"dimensions[{index}].attributes") or ([source_column] if source_column else [])
        history_columns = _columns(raw.get("history_columns"), f"dimensions[{index}].history_columns")
        if str(raw.get("scd_strategy", "none")).casefold() == "type_2":
            history_columns = list(dict.fromkeys([*history_columns, "valid_from", "valid_to", "is_current"]))
        dimensions.append({
            "name": table_name,
            "source_column": source_column,
            "natural_key": natural_key,
            "surrogate_key": surrogate_key,
            "key_strategy": str(raw.get("key_strategy", "surrogate")),
            "scd_strategy": str(raw.get("scd_strategy", "none")).casefold(),
            "attributes": list(dict.fromkeys([*attributes, natural_key, *history_columns])),
            "history_columns": history_columns,
            "snowflake_parent": _name(raw.get("snowflake_parent"), "") if raw.get("snowflake_parent") else None,
            "role_playing": _columns(raw.get("role_playing"), f"dimensions[{index}].role_playing"),
        })
    date_design = _items(design.get("date_dimensions") or design.get("role_playing_dates"), "date_dimensions")
    dates: list[dict[str, Any]] = []
    if date_design:
        for index, raw in enumerate(date_design, 1):
            role = _name(raw.get("role") or raw.get("name"), f"DateRole{index}")
            dates.append({"name": _name(raw.get("name"), f"Dim{role}"), "role": role, "source_column": str(raw.get("source_column") or "").strip(), "base_table": "DimDate", "relationship_mode": str(raw.get("relationship_mode", "active"))})
    elif design.get("date_column"):
        dates.append({"name": "DimDate", "role": "Calendar", "source_column": str(design["date_column"]), "base_table": "DimDate", "relationship_mode": "active"})

    raw_bridges = _items(design.get("bridges"), "bridges")
    bridges: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_bridges, 1):
        bridges.append({
            "name": _name(raw.get("name"), f"Bridge{index}"),
            "left_table": _name(raw.get("left_table"), ""),
            "right_table": _name(raw.get("right_table"), ""),
            "left_key": _identifier(raw.get("left_key"), "left_sk"),
            "right_key": _identifier(raw.get("right_key"), "right_sk"),
            "grain": str(raw.get("grain") or "one row per unique left/right association"),
        })
    measures: list[dict[str, Any]] = []
    raw_measures = _items(design.get("measures"), "measures")
    if raw_measures:
        for index, raw in enumerate(raw_measures, 1):
            measures.append({"name": _require(raw.get("name"), "MEASURE_NAME_REQUIRED", f"measures[{index}] needs a name."), "expression": _require(raw.get("expression"), "MEASURE_EXPRESSION_REQUIRED", f"measures[{index}] needs an explicit expression."), "format": str(raw.get("format", "#,##0.00")), "description": str(raw.get("description") or "Business-approved semantic measure.")})
    else:
        for column in _columns(design.get("measure_columns"), "measure_columns"):
            measures.append(_dax_measure(f"Total {column}", f"SUM ( {_quoted_table(fact_name)}[{column}] )", f"Explicit sum of {column}; confirm the additive behavior at the declared grain."))

    storage_mode = str(design.get("storage_mode", "Import"))
    raw_perspectives = _items(design.get("perspectives"), "perspectives")
    perspectives = [{"name": _require(item.get("name"), "PERSPECTIVE_NAME_REQUIRED", "Every perspective needs a name."), "tables": _columns(item.get("tables"), "perspectives.tables"), "measures": _columns(item.get("measures"), "perspectives.measures")} for item in raw_perspectives]
    raw_parameters = _items(design.get("field_parameters"), "field_parameters")
    field_parameters = [{"name": _require(item.get("name"), "FIELD_PARAMETER_NAME_REQUIRED", "Every field parameter needs a name."), "fields": _columns(item.get("fields"), "field_parameters.fields"), "default": item.get("default")} for item in raw_parameters]
    raw_aggregations = _items(design.get("aggregations"), "aggregations")
    aggregations = [{"name": _name(item.get("name"), f"Agg{index}"), "detail_table": _name(item.get("detail_table"), fact_name), "group_by": _columns(item.get("group_by"), f"aggregations[{index}].group_by"), "measures": _columns(item.get("measures"), f"aggregations[{index}].measures"), "storage_mode": str(item.get("storage_mode", "Import"))} for index, item in enumerate(raw_aggregations, 1)]
    shared_model = {"shared": bool(design.get("shared_model", False)), "name": str(design.get("shared_model_name") or fact_name), "composite_model": storage_mode == "Composite", "source_mode": storage_mode}

    relationships: list[dict[str, Any]] = []
    for dimension in dimensions:
        relationships.append({"from_table": fact_name, "from_column": dimension["surrogate_key"], "to_table": dimension["name"], "to_column": dimension["surrogate_key"], "cardinality": "many_to_one", "cross_filter": "single", "active": True, "via_bridge": None})
        if dimension["snowflake_parent"]:
            relationships.append({"from_table": dimension["name"], "from_column": dimension["snowflake_parent"], "to_table": dimension["snowflake_parent"], "to_column": dimension["snowflake_parent"], "cardinality": "many_to_one", "cross_filter": "single", "active": True, "via_bridge": None})
    for date in dates:
        relationships.append({"from_table": fact_name, "from_column": date["source_column"], "to_table": date["name"], "to_column": "Date", "cardinality": "many_to_one", "cross_filter": "single", "active": date["relationship_mode"] == "active", "via_bridge": None, "role": date["role"]})
    for bridge in bridges:
        relationships.extend([
            {"from_table": bridge["name"], "from_column": bridge["left_key"], "to_table": bridge["left_table"], "to_column": bridge["left_key"], "cardinality": "many_to_one", "cross_filter": "single", "active": True, "via_bridge": bridge["name"]},
            {"from_table": bridge["name"], "from_column": bridge["right_key"], "to_table": bridge["right_table"], "to_column": bridge["right_key"], "cardinality": "many_to_one", "cross_filter": "single", "active": True, "via_bridge": bridge["name"]},
        ])
    validation = _validation(source_columns=source_columns, fact=fact, dimensions=dimensions, dates=dates, bridges=bridges, relationships=relationships, measures=measures, grain_confirmed=bool(design.get("grain_confirmed", False)), storage_mode=storage_mode, aggregations=aggregations, field_parameters=field_parameters)
    contract = {
        "model_type": "snowflake_schema" if str(design.get("model_type", "star_schema")).casefold() in {"snowflake", "snowflake_schema"} else "star_schema",
        "source_columns": source_columns,
        "fact_table": fact,
        "dimensions": dimensions,
        "date_tables": dates,
        "bridges": bridges,
        "degenerate_dimensions": fact["degenerate_dimensions"],
        "relationships": relationships,
        "measures": measures,
        "semantic_model": {"storage_mode": storage_mode, "shared_model": shared_model, "perspectives": perspectives, "field_parameters": field_parameters, "aggregations": aggregations},
        "rationale": [
            "The declared grain prevents double counting and makes every measure explainable.",
            "Surrogate keys isolate the semantic model from changing source natural keys.",
            "Single-direction dimension-to-fact filtering reduces ambiguous propagation and improves performance.",
            "SCD Type 2 dimensions preserve historical context with valid_from, valid_to and is_current fields.",
            "Role-playing date tables keep order, ship and other date roles independently filterable.",
            "Bridge tables make many-to-many membership explicit instead of hiding bidirectional ambiguity.",
            "Degenerate dimensions remain on the fact when the identifier has no descriptive dimension attributes.",
        ],
        "dax_measure_templates": measures,
        "sql_ddl_reference": _sql_ddl({"fact_table": fact, "dimensions": dimensions, "bridges": bridges}),
        "validation": validation,
        "release_gates": [
            "Business owner confirms grain and metric definitions.",
            "Data steward approves natural-key, SCD and bridge semantics.",
            "Reconcile fact totals and relationship cardinalities against the source.",
            "Test RLS/OLS and negative access before publication.",
            "Validate performance in the target semantic engine before production.",
        ],
    }
    return contract


__all__ = ["SemanticModelError", "build_semantic_model_contract"]
