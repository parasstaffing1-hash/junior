"""Cross-tool BI exports.

The application keeps one report model and emits lightweight, portable desktop
artifacts from it.  Power BI's source-controlled project format (PBIP) is a
folder, so the downloadable artifact is a ZIP containing that folder and the
``.pbip`` shortcut.  Tableau workbooks are XML (``.twb``) and packaged
workbooks (``.twbx``) are ZIP files containing the XML and local data.

These writers intentionally use only the Python standard library and pandas;
they are useful in the API container where Power BI Desktop and Tableau
Desktop are not installed.  The resulting files are real project/workbook
files rather than renamed PDFs or spreadsheets.
"""

from __future__ import annotations

from hashlib import sha1
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
from typing import Any
from uuid import uuid5, NAMESPACE_URL
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

import pandas as pd

from app.core.enterprise.bi_architecture import enterprise_bi_blueprint, enterprise_bi_markdown
from app.core.bi.dax import analyze_dax_measures
from app.core.bi.power_query import analyze_power_query
from app.core.bi.semantic_model import build_semantic_model_contract


PBIP_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json"
PBIR_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json"
PBISM_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json"


class BIExportValidationError(ValueError):
    """Raised when a generated desktop BI package is not client-openable."""


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n").encode("utf-8")


def _zip_bytes(entries: dict[str, bytes | str]) -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value.encode("utf-8") if isinstance(value, str) else value)
    return stream.getvalue()


def _validate_archive_paths(names: list[str]) -> None:
    unsafe = [name for name in names if name.startswith(("/", "\\")) or ".." in PurePosixPath(name).parts]
    if unsafe:
        raise BIExportValidationError(f"BI package contains unsafe archive paths: {unsafe[:3]}")


def validate_powerbi_project_package(package: bytes) -> dict[str, Any]:
    """Validate the PBIP project ZIP contract required by Power BI Desktop."""
    try:
        with ZipFile(BytesIO(package)) as archive:
            names = archive.namelist()
            _validate_archive_paths(names)
            projects = [name for name in names if name.endswith(".pbip") and "/" not in name]
            if len(projects) != 1:
                raise BIExportValidationError("Power BI package must contain one root .pbip project file.")
            project_file = projects[0]
            try:
                project = json.loads(archive.read(project_file))
                report_folder = project["artifacts"][0]["report"]["path"]
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                raise BIExportValidationError("Power BI .pbip project pointer is invalid.") from exc
            if not report_folder or ".." in PurePosixPath(report_folder).parts:
                raise BIExportValidationError("Power BI report folder pointer is unsafe or empty.")
            pbir_path = f"{report_folder}/definition.pbir"
            if pbir_path not in names:
                raise BIExportValidationError("Power BI report definition.pbir is missing.")
            try:
                pbir = json.loads(archive.read(pbir_path))
                model_reference = pbir["datasetReference"]["byPath"]["path"]
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise BIExportValidationError("Power BI report does not reference a semantic model by path.") from exc
            model_folder = posixpath.normpath(posixpath.join(report_folder, model_reference))
            if model_folder.startswith("../") or model_folder in {"", "."}:
                raise BIExportValidationError("Power BI semantic model pointer resolves outside the project.")
            required = {
                f"{model_folder}/definition.pbism",
                f"{model_folder}/model.bim",
                "data/data.csv",
                "MODEL_DESIGN.md",
                "RLS_CONFIGURATION.md",
                "model_contract.json",
                "advanced_semantic_model.json",
                "ENTERPRISE_ARCHITECTURE.md",
                "enterprise_blueprint.json",
                "engineering/postgresql_scale_pattern.sql",
                "engineering/incremental_refresh.pq",
                "engineering/advanced_dax_library.dax",
                "engineering/tabular_editor_calculation_groups.csx",
                "engineering/semantic_model_ci.yml",
            }
            missing = sorted(required.difference(names))
            if missing:
                raise BIExportValidationError(f"Power BI project is missing required files: {missing}")
            if not archive.read("data/data.csv").strip():
                raise BIExportValidationError("Power BI project data/data.csv is empty.")
            json_files = [
                name
                for name in names
                if name.endswith((".json", ".pbip", ".pbir", ".pbism", ".bim"))
            ]
            for name in json_files:
                try:
                    json.loads(archive.read(name))
                except json.JSONDecodeError as exc:
                    raise BIExportValidationError(f"Power BI project JSON is invalid: {name}") from exc
            model = json.loads(archive.read(f"{model_folder}/model.bim")).get("model") or {}
            tables = model.get("tables") or []
            fact = next((item for item in tables if item.get("name") == "FactData"), None)
            if not fact:
                raise BIExportValidationError("Power BI semantic model must contain the FactData fact table.")
            roles = model.get("roles") or []
            if not roles or not any(
                permission.get("filterExpression") == "FALSE()"
                for role in roles
                for permission in role.get("tablePermissions", [])
            ):
                raise BIExportValidationError("Power BI semantic model must include the fail-closed RLS publication template.")
            contract = json.loads(archive.read("model_contract.json"))
            if contract.get("model_type") != "star_schema" or contract.get("fact_table", {}).get("name") != "FactData":
                raise BIExportValidationError("Power BI model contract does not describe the generated star schema.")
            page_files = [name for name in names if name.endswith("/page.json")]
            pages = [json.loads(archive.read(name)) for name in page_files]
            page_types = {str(item.get("type")) for item in pages if item.get("type")}
            if not {"Drillthrough", "Tooltip"}.issubset(page_types):
                raise BIExportValidationError("Power BI project must include drill-through and tooltip pages.")
            visuals = [name for name in names if name.endswith("/visual.json")]
            visual_specs = [json.loads(archive.read(name)) for name in visuals]
            slicer_count = sum((item.get("visual") or {}).get("visualType") == "slicer" for item in visual_specs)
            return {
                "valid": True,
                "format": "powerbi_pbip_project",
                "project_file": project_file,
                "report_folder": report_folder,
                "semantic_model_folder": model_folder,
                "visual_count": len(visuals),
                "page_count": len(pages),
                "page_types": sorted(page_types),
                "table_count": len(tables),
                "dimension_count": max(0, len(tables) - 1),
                "relationship_count": len(model.get("relationships") or []),
                "measure_count": len(fact.get("measures") or []),
                "time_intelligence_measure_count": len(contract.get("time_intelligence_measures") or []),
                "rls_role_count": len(roles),
                "slicer_count": slicer_count,
                "model_contract": contract,
                "requires_extraction": True,
            }
    except BadZipFile as exc:
        raise BIExportValidationError("Power BI project package is not a valid ZIP archive.") from exc


def validate_tableau_packaged_workbook(package: bytes) -> dict[str, Any]:
    """Validate the TWBX workbook and embedded local data expected by Tableau."""
    try:
        with ZipFile(BytesIO(package)) as archive:
            names = archive.namelist()
            _validate_archive_paths(names)
            workbooks = [name for name in names if name.endswith(".twb")]
            if len(workbooks) != 1:
                raise BIExportValidationError("Tableau package must contain one .twb workbook.")
            workbook_file = workbooks[0]
            try:
                root = ET.fromstring(archive.read(workbook_file))
            except ET.ParseError as exc:
                raise BIExportValidationError("Tableau .twb workbook XML is invalid.") from exc
            if root.tag != "workbook":
                raise BIExportValidationError("Tableau .twb root element must be workbook.")
            connection = root.find(".//connection[@class='textscan']")
            data_file = connection.get("filename") if connection is not None else None
            if not data_file or data_file not in names:
                raise BIExportValidationError("Tableau package does not contain its referenced local data file.")
            if not archive.read(data_file).strip():
                raise BIExportValidationError("Tableau packaged data file is empty.")
            worksheets = root.findall(".//worksheet")
            dashboards = root.findall(".//dashboard")
            if not worksheets or not dashboards:
                raise BIExportValidationError("Tableau package must contain a worksheet and dashboard.")
            return {
                "valid": True,
                "format": "tableau_packaged_workbook",
                "workbook_file": workbook_file,
                "data_file": data_file,
                "worksheet_count": len(worksheets),
                "dashboard_count": len(dashboards),
                "requires_extraction": False,
            }
    except BadZipFile as exc:
        raise BIExportValidationError("Tableau packaged workbook is not a valid TWBX archive.") from exc


def _safe_identifier(value: Any, fallback: str = "GeneratedReport", *, max_length: int = 80) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "")).strip("_-")
    if not text:
        text = fallback
    if text[0].isdigit():
        text = f"Report_{text}"
    return text[:max_length] or fallback


def _safe_xml_name(value: Any, fallback: str = "Worksheet", *, max_length: int = 80) -> str:
    text = re.sub(r"[^A-Za-z0-9 _-]+", "", str(value or "")).strip()
    return (text[:max_length] or fallback).replace("/", "-")


def _looks_like_date_column(value: Any) -> bool:
    name = re.sub(r"[^a-z0-9]+", "_", str(value).casefold()).strip("_")
    compact = name.replace("_", "")
    common = {"date", "time", "datetime", "timestamp", "month", "orderdate", "transactiondate", "eventdate", "reportdate", "snapshotdate", "tradedate", "signupdate", "createdat", "updatedat"}
    return compact in common or name.endswith(("_date", "_datetime", "_timestamp", "_time"))


def _column_type(series: pd.Series) -> tuple[str, str, str]:
    """Return Power BI type, M type and Tableau type for a pandas series."""
    if pd.api.types.is_bool_dtype(series):
        return "boolean", "type logical", "boolean"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "dateTime", "type datetime", "datetime"
    if pd.api.types.is_integer_dtype(series):
        return "int64", "Int64.Type", "integer"
    if pd.api.types.is_float_dtype(series) or pd.api.types.is_numeric_dtype(series):
        return "double", "type number", "real"
    return "string", "type text", "string"


def _column_specs(frame: pd.DataFrame) -> list[dict[str, str]]:
    specs: list[dict[str, str]] = []
    used: set[str] = set()
    for raw_column in frame.columns:
        actual = str(raw_column)
        # Power BI and Tableau both accept spaces, but duplicate/empty headers
        # make the generated model ambiguous. Preserve the source name where
        # possible and add a deterministic suffix only for duplicates.
        model_name = actual or "Column"
        base = model_name
        suffix = 2
        while model_name in used:
            model_name = f"{base}_{suffix}"
            suffix += 1
        used.add(model_name)
        power_type, m_type, tableau_type = _column_type(frame[raw_column])
        specs.append({"source": actual, "name": model_name, "power_type": power_type, "m_type": m_type, "tableau_type": tableau_type})
    return specs


def _m_expression(frame: pd.DataFrame, columns: list[dict[str, str]], *, relative_path: str = "data/data.csv") -> str:
    transforms = ", ".join(
        "{{\"{name}\", {m_type}}}".format(name=item["source"].replace('"', '""'), m_type=item["m_type"])
        for item in columns
        if item["m_type"] != "type text"
    )
    typed = f",\n    Typed = Table.TransformColumnTypes(PromotedHeaders, {{{transforms}}})" if transforms else ""
    typed_step = "Typed" if transforms else "PromotedHeaders"
    text_transforms = ", ".join(
        "{{\"{name}\", each if _ = null then null else Text.Trim(Text.From(_)), type text}}".format(name=item["source"].replace('"', '""'))
        for item in columns
        if item["m_type"] == "type text"
    )
    trimmed = f",\n    TrimmedText = Table.TransformColumns({typed_step}, {{{text_transforms}}})" if text_transforms else ""
    cleaned_step = "TrimmedText" if text_transforms else typed_step
    return (
        "let\n"
        f"    Source = Csv.Document(File.Contents(\"{relative_path}\"), [Delimiter=\",\", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),\n"
        "    PromotedHeaders = Table.PromoteHeaders(Source, [PromoteAllScalars=true])"
        f"{typed}"
        f"{trimmed},\n"
        f"    RemovedBlankRows = Table.SelectRows({cleaned_step}, each List.NonNullCount(Record.FieldValues(_)) > 0),\n"
        "    RemovedDuplicates = Table.Distinct(RemovedBlankRows)\n"
        "in\n    RemovedDuplicates"
    )


def _measure_name(label: Any, used: set[str]) -> str:
    name = str(label or "Measure")
    base = name
    index = 2
    while name in used:
        name = f"{base} ({index})"
        index += 1
    used.add(name)
    return name


def _dax_column(column: Any, table: str = "FactData") -> str:
    return f"'{table}'[{str(column).replace(']', ']]')}]"


def _powerbi_measures(report: dict[str, Any], frame: pd.DataFrame, date_column: str | None) -> list[dict[str, Any]]:
    measures: list[dict[str, Any]] = []
    used: set[str] = set()
    for kpi in report.get("kpis", []):
        definition = kpi.get("definition") or {}
        kind = definition.get("definition_type")
        components = definition.get("components") or {}
        expression: str | None = None
        format_string = "#,##0.00"
        if kind == "aggregate":
            value = components.get("value") or {}
            column = value.get("column")
            aggregation = str(value.get("aggregation", "sum")).casefold()
            if column:
                expression = {"sum": "SUM", "average": "AVERAGE", "min": "MIN", "max": "MAX"}.get(aggregation, "SUM") + f"({_dax_column(column)})"
        elif kind == "average" and definition.get("column"):
            expression = f"AVERAGE({_dax_column(definition['column'])})"
        elif kind == "distinct_count" and definition.get("column"):
            expression = f"DISTINCTCOUNT({_dax_column(definition['column'])})"
        elif kind == "ratio":
            numerator = components.get("numerator") or {}
            denominator = components.get("denominator") or {}
            if numerator.get("column") and denominator.get("column"):
                expression = f"DIVIDE(SUM({_dax_column(numerator['column'])}), SUM({_dax_column(denominator['column'])}), 0)"
                format_string = "0.0%" if definition.get("ratio_scale", 1) == 100 else "0.00%"
        elif kind == "count":
            expression = "COUNTROWS('FactData')"
        if expression is None:
            # A KPI with no fact-table definition — a model metric, a significance
            # test, a cross-group comparison — has no honest DAX equivalent. Falling
            # back to a row count here would publish a confidently wrong number under
            # a business label, so the measure is omitted instead.
            continue
        name = _measure_name(kpi.get("label"), used)
        if "$" in str(kpi.get("formatted_value", "")) and format_string != "0.0%":
            format_string = "$#,##0.00"
        measures.append({"name": name, "expression": expression, "formatString": format_string, "description": "Source-backed KPI generated from the fact table."})
    # Two measures with identical DAX are a modelling defect: the field list becomes
    # ambiguous and time intelligence can end up chained to the duplicate. Reuse an
    # equivalent measure instead of publishing a second copy of it.
    by_expression = {measure["expression"]: measure["name"] for measure in measures}

    def ensure_measure(label: str, expression: str, format_string: str, description: str) -> str:
        if expression in by_expression:
            return by_expression[expression]
        name = _measure_name(label, used)
        measures.append({"name": name, "expression": expression, "formatString": format_string, "description": description})
        by_expression[expression] = name
        return name

    ensure_measure("Rows", "COUNTROWS('FactData')", "#,##0", "Count of rows at the fact-table grain.")
    numeric_columns = [str(column) for column in frame.columns if pd.api.types.is_numeric_dtype(frame[column])]
    primary = numeric_columns[0] if numeric_columns else None
    if primary:
        total_name = ensure_measure(f"Total {primary}", f"SUM({_dax_column(primary)})", "#,##0.00", f"Additive total of {primary}.")
        ensure_measure(f"Average {primary}", f"AVERAGE({_dax_column(primary)})", "#,##0.00", f"Arithmetic average of {primary}; validate weighting for business use.")
        if date_column:
            prior_name = ensure_measure(f"{primary} Previous Year", f"CALCULATE([{total_name}], SAMEPERIODLASTYEAR('DimDate'[Date]))", "#,##0.00", "Comparable prior-year period using DimDate.")
            ensure_measure(f"{primary} YTD", f"TOTALYTD([{total_name}], 'DimDate'[Date])", "#,##0.00", "Year-to-date total using the governed date dimension.")
            ensure_measure(f"{primary} YoY %", f"DIVIDE([{total_name}] - [{prior_name}], [{prior_name}])", "0.0%", "Year-over-year change with safe divide behavior.")
    return measures


def _theme_asset(dashboard: dict[str, Any]) -> tuple[str, bytes]:
    template = dashboard.get("template") or {}
    asset = str(template.get("theme_asset") or "").split("/")[-1]
    asset_root = Path(__file__).resolve().parents[3] / "frontend" / "assets" / "powerbi"
    if asset:
        candidate = asset_root / asset
        if candidate.is_file():
            return asset, candidate.read_bytes()
    palette = ((template.get("theme") or {}).get("palette") or {})
    colors = list(palette.values()) if isinstance(palette, dict) else list(palette)
    return "generated_theme.json", _json_bytes({"name": template.get("name", "Generated theme"), "dataColors": colors})


def _visual_type(widget: dict[str, Any]) -> str:
    if widget.get("widget_type") == "kpi":
        return "cardVisual"
    if widget.get("widget_type") == "slicer":
        return "slicer"
    if widget.get("widget_type") == "table":
        return "tableEx"
    chart = (widget.get("config") or {}).get("chart") or {}
    return "lineChart" if chart.get("chart_type") == "line" else "barChart"


def _projection(column: Any, table: str = "FactData") -> dict[str, Any]:
    field = {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": str(column)}}
    return {"field": field, "queryRef": f"{table}.{column}", "nativeQueryRef": str(column)}


def _measure_projection(name: Any) -> dict[str, Any]:
    field = {"Measure": {"Expression": {"SourceRef": {"Entity": "FactData"}}, "Property": str(name)}}
    return {"field": field, "queryRef": f"FactData.{name}", "nativeQueryRef": str(name)}


def _visual_query(
    widget: dict[str, Any],
    report: dict[str, Any],
    *,
    available_columns: list[str] | None = None,
) -> dict[str, Any] | None:
    chart = (widget.get("config") or {}).get("chart") or {}
    widget_type = widget.get("widget_type")
    if widget_type == "chart":
        category = chart.get("category_column") or chart.get("x_column")
        value = chart.get("value_column") or chart.get("y_column")
        query_state: dict[str, Any] = {}
        if category:
            query_state["Category"] = {"projections": [_projection(category)]}
        if value:
            query_state["Y"] = {"projections": [_projection(value)]}
        return {"queryState": query_state} if query_state else None
    if widget_type == "kpi":
        source_ref = widget.get("source_ref")
        measure = next((item.get("label") for item in report.get("kpis", []) if item.get("source_ref") == source_ref), None)
        return {"queryState": {"Data": {"projections": [_measure_projection(measure)]}}} if measure else None
    if widget_type == "table":
        table = (widget.get("config") or {}).get("table") or {}
        requested = [str(column) for column in (table.get("columns") or [])]
        valid = [column for column in requested if not available_columns or column in available_columns]
        if not valid and available_columns:
            valid = available_columns[:12]
        projections = [_projection(column) for column in valid[:12]]
        return {"queryState": {"Values": {"projections": projections}}} if projections else None
    if widget_type == "slicer":
        field = (widget.get("config") or {}).get("column")
        return {"queryState": {"Values": {"projections": [_projection(field)]}}} if field else None
    return None


def _field_expression(column: str, table: str = "FactData") -> dict[str, Any]:
    return {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": str(column)}}


def _semantic_model_parts(
    frame: pd.DataFrame,
    report: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, bytes | str], dict[str, Any]]:
    """Build an explainable star-schema model and its local CSV sources."""
    modeled = frame.copy(deep=True)
    date_column = next(
        (str(column) for column in modeled.columns if pd.api.types.is_datetime64_any_dtype(modeled[column])),
        None,
    )
    if date_column is None:
        for column in modeled.columns:
            if _looks_like_date_column(column):
                parsed = pd.to_datetime(modeled[column], errors="coerce")
                if parsed.notna().mean() >= 0.6:
                    date_column = str(column)
                    modeled[column] = parsed
                    break
    if date_column:
        modeled[date_column] = pd.to_datetime(modeled[date_column], errors="coerce").dt.normalize()

    fact_specs = _column_specs(modeled)
    fact_columns = [
        {
            "name": item["name"],
            "dataType": item["power_type"],
            "sourceColumn": item["source"],
            "summarizeBy": "sum" if item["power_type"] in {"int64", "double"} else "none",
            "description": "Source column retained at the fact-table grain.",
        }
        for item in fact_specs
    ]
    fact_table = {
        "name": "FactData",
        "description": "Central fact table at one row per cleaned source record; measures aggregate from this table.",
        "columns": fact_columns,
        "measures": _powerbi_measures(report, modeled, date_column),
        "partitions": [{"name": "FactData", "mode": "import", "source": {"type": "m", "expression": _m_expression(modeled, fact_specs)}}],
    }

    tables: list[dict[str, Any]] = [fact_table]
    relationships: list[dict[str, Any]] = []
    entries: dict[str, bytes | str] = {
        "data/data.csv": modeled.to_csv(index=False, date_format="%Y-%m-%dT%H:%M:%S").encode("utf-8"),
    }
    dimensions: list[dict[str, Any]] = []

    if date_column and modeled[date_column].notna().any():
        start = modeled[date_column].min()
        end = modeled[date_column].max()
        dates = pd.date_range(start=start, end=end, freq="D")
        dim_date = pd.DataFrame({"Date": dates})
        dim_date["DateKey"] = dim_date["Date"].dt.strftime("%Y%m%d").astype("int64")
        dim_date["Year"] = dim_date["Date"].dt.year.astype("int64")
        dim_date["Quarter"] = "Q" + dim_date["Date"].dt.quarter.astype(str)
        dim_date["Month"] = dim_date["Date"].dt.month_name()
        dim_date["MonthNumber"] = dim_date["Date"].dt.month.astype("int64")
        dim_date["YearMonth"] = dim_date["Date"].dt.strftime("%Y-%m")
        dim_date["Week"] = dim_date["Date"].dt.isocalendar().week.astype("int64")
        dim_date["Day"] = dim_date["Date"].dt.day.astype("int64")
        dim_date["IsWeekend"] = dim_date["Date"].dt.dayofweek >= 5
        specs = _column_specs(dim_date)
        tables.append({
            "name": "DimDate",
            "description": "Conformed calendar dimension used for filtering and time-intelligence measures.",
            "columns": [{"name": item["name"], "dataType": item["power_type"], "sourceColumn": item["source"], "summarizeBy": "none"} for item in specs],
            "partitions": [{"name": "DimDate", "mode": "import", "source": {"type": "m", "expression": _m_expression(dim_date, specs, relative_path="data/DimDate.csv")}}],
            "annotations": [{"name": "__PBI_TimeIntelligenceEnabled", "value": "1"}],
        })
        relationships.append({
            "name": f"rel_{sha1(f'{date_column}:DimDate'.encode()).hexdigest()[:16]}",
            "fromTable": "FactData",
            "fromColumn": date_column,
            "fromCardinality": "many",
            "toTable": "DimDate",
            "toColumn": "Date",
            "toCardinality": "one",
            "crossFilteringBehavior": "oneDirection",
        })
        entries["data/DimDate.csv"] = dim_date.to_csv(index=False, date_format="%Y-%m-%dT%H:%M:%S").encode("utf-8")
        dimensions.append({"table": "DimDate", "key": "Date", "source_column": date_column, "purpose": "calendar filtering and time intelligence"})

    candidates = [
        str(column)
        for column in modeled.columns
        if str(column) != date_column
        and not pd.api.types.is_numeric_dtype(modeled[column])
        and 1 < modeled[column].nunique(dropna=True) <= min(500, max(20, len(modeled) // 2))
    ][:4]
    used_names = {"FactData", "DimDate"}
    for column in candidates:
        safe_dimension = _safe_identifier(column, "Category", max_length=45)
        base = "Dim" + "".join(part[:1].upper() + part[1:] for part in safe_dimension.split("_") if part)
        table_name = base
        suffix = 2
        while table_name in used_names:
            table_name = f"{base}{suffix}"
            suffix += 1
        used_names.add(table_name)
        dimension = modeled[[column]].dropna().drop_duplicates().sort_values(column, key=lambda values: values.astype(str)).reset_index(drop=True)
        specs = _column_specs(dimension)
        data_path = f"data/{table_name}.csv"
        tables.append({
            "name": table_name,
            "description": f"Conformed dimension for {column}; isolates descriptive attributes from fact aggregation.",
            "columns": [{"name": item["name"], "dataType": item["power_type"], "sourceColumn": item["source"], "summarizeBy": "none"} for item in specs],
            "partitions": [{"name": table_name, "mode": "import", "source": {"type": "m", "expression": _m_expression(dimension, specs, relative_path=data_path)}}],
        })
        relationships.append({
            "name": f"rel_{sha1(f'{column}:{table_name}'.encode()).hexdigest()[:16]}",
            "fromTable": "FactData",
            "fromColumn": column,
            "fromCardinality": "many",
            "toTable": table_name,
            "toColumn": column,
            "toCardinality": "one",
            "crossFilteringBehavior": "oneDirection",
        })
        entries[data_path] = dimension.to_csv(index=False).encode("utf-8")
        dimensions.append({"table": table_name, "key": column, "source_column": column, "purpose": "descriptive filtering and grouping"})

    role = {
        "name": "RLS - Configure Before Publish",
        "description": "Fail-closed RLS template. Replace FALSE() with an approved identity-to-scope rule before assigning users.",
        "modelPermission": "read",
        "tablePermissions": [{"name": "FactData", "filterExpression": "FALSE()"}],
    }
    model = {
        "name": "Generated Semantic Model",
        "compatibilityLevel": 1600,
        "model": {
            "culture": "en-US",
            "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "discourageImplicitMeasures": True,
            "tables": tables,
            "relationships": relationships,
            "roles": [role],
            "annotations": [{"name": "ModelingApproach", "value": "Explainable star schema generated from the cleaned source."}],
        }
    }
    contract = {
        "model_type": "star_schema",
        "fact_table": {"name": "FactData", "grain": "one row per cleaned source record", "row_count": int(len(modeled))},
        "dimensions": dimensions,
        "relationships": relationships,
        "date_table": "DimDate" if date_column else None,
        "source_date_column": date_column,
        "measure_count": len(fact_table["measures"]),
        "time_intelligence_measures": [item["name"] for item in fact_table["measures"] if any(token in item["name"] for token in ("YTD", "Previous Year", "YoY"))],
        "power_query_steps": ["load local source", "promote headers", "assign types", "trim text", "remove blank rows", "remove exact duplicates"],
        "rls": {"role": role["name"], "mode": "fail_closed_template", "requires_client_identity_mapping": True},
        "performance_decisions": [
            "One-direction many-to-one relationships reduce ambiguous filter paths.",
            "Explicit measures are preferred over implicit aggregation.",
            "Dimension tables deduplicate slicer values and keep the fact table at a stable grain.",
            "Imported CSV partitions are deterministic and portable; replace them with governed production sources before scheduled refresh.",
        ],
    }
    numeric_columns = [item["name"] for item in fact_specs if item["power_type"] in {"int64", "double"}]
    advanced_design = {
        "grain": "one row per cleaned source record",
        "grain_columns": [date_column] if date_column else ([fact_specs[0]["name"]] if fact_specs else []),
        "grain_confirmed": False,
        "fact": {"name": "FactData", "columns": [item["name"] for item in fact_specs], "surrogate_key": "fact_sk"},
        "dimension_columns": [item["source_column"] for item in dimensions if item.get("table") != "DimDate"],
        "measure_columns": numeric_columns,
        "date_column": date_column,
        "storage_mode": "Import",
        "shared_model": False,
    }
    advanced_contract = build_semantic_model_contract([item["name"] for item in fact_specs], advanced_design)
    contract["advanced_semantic_model"] = advanced_contract
    design_lines = [
        "# POWER BI MODEL DESIGN",
        "",
        "## Why this model",
        "FactData keeps the cleaned source grain (one row per source record). Separate dimensions provide stable filter paths, prevent repeated descriptive values from driving ambiguous joins, and make measures easier to explain.",
        "",
        "## Relationships",
        *[f"- FactData[{item['fromColumn']}] → {item['toTable']}[{item['toColumn']}], one-direction many-to-one." for item in relationships],
        "",
        "## Measures and time",
        "Measures are explicit DAX definitions. When a usable source date exists, DimDate and YTD / previous-year / YoY measures are included.",
        "",
        "## Performance",
        *[f"- {item}" for item in contract["performance_decisions"]],
        "",
        "## Publication gate",
        "Confirm grain, currency, timezone, refresh path, and RLS identity mapping with the business owner before publishing.",
    ]
    entries["MODEL_DESIGN.md"] = "\n".join(design_lines) + "\n"
    entries["RLS_CONFIGURATION.md"] = (
        "# ROW-LEVEL SECURITY\n\n"
        "The model contains a fail-closed role named `RLS - Configure Before Publish` with `FALSE()` on FactData.\n"
        "Replace that expression with an approved DAX rule tied to a governed security mapping table, test View As for every role, and only then assign users/groups in the Power BI service.\n"
        "The generator does not invent client identities or silently grant access.\n"
    )
    entries["model_contract.json"] = _json_bytes(contract)
    entries["advanced_semantic_model.json"] = _json_bytes(advanced_contract)
    dax_analysis = analyze_dax_measures([{"name": item["name"], "expression": f"{item['name']} := {item['expression']}", "context": "measure"} for item in fact_table["measures"]])
    power_query_analysis = analyze_power_query(_m_expression(modeled, fact_specs))
    contract["local_engineering_review"] = {
        "dax": {"status": dax_analysis["status"], "measure_count": dax_analysis["measure_count"], "valid_count": dax_analysis["valid_count"], "external_execution_required": dax_analysis["external_execution_required"]},
        "power_query": {"status": power_query_analysis["status"], "features": power_query_analysis["features"], "external_execution_required": power_query_analysis["external_execution_required"]},
    }
    entries["model_contract.json"] = _json_bytes(contract)
    entries["DAX_ANALYSIS.json"] = _json_bytes(dax_analysis)
    entries["POWER_QUERY_ANALYSIS.json"] = _json_bytes(power_query_analysis)
    blueprint = enterprise_bi_blueprint(dataset_name=str(report.get("title") or "Generated Semantic Model"), source_rows=max(500_000_000, int(len(modeled))))
    entries["ENTERPRISE_ARCHITECTURE.md"] = enterprise_bi_markdown(blueprint)
    entries["enterprise_blueprint.json"] = _json_bytes({key: value for key, value in blueprint.items() if key != "files"})
    entries.update(blueprint["files"])
    return modeled, model, entries, contract


def _powerbi_entries(
    frame: pd.DataFrame,
    report: dict[str, Any],
    dashboard: dict[str, Any],
    manifest: dict[str, Any],
    dataset_name: str,
    source_version_id: str | None,
) -> dict[str, bytes | str]:
    project_name = _safe_identifier(dataset_name or report.get("title"), "GeneratedReport")
    report_folder = f"{project_name}.Report"
    model_folder = f"{project_name}.SemanticModel"
    report_id = str(report.get("id") or dashboard.get("id") or project_name)
    page_id = sha1(f"{report_id}:{project_name}".encode("utf-8")).hexdigest()[:20]
    drill_page_id = sha1(f"{report_id}:drillthrough".encode("utf-8")).hexdigest()[:20]
    tooltip_page_id = sha1(f"{report_id}:tooltip".encode("utf-8")).hexdigest()[:20]
    modeled_frame, model, model_entries, model_contract = _semantic_model_parts(frame, report)
    columns = _column_specs(modeled_frame)
    theme_name, theme_bytes = _theme_asset(dashboard)
    pbip = {
        "$schema": PBIP_SCHEMA,
        "version": "1.0",
        "artifacts": [{"report": {"path": report_folder}}],
        "settings": {"enableAutoRecovery": True},
    }
    pbir = {"$schema": PBIR_SCHEMA, "version": "4.0", "datasetReference": {"byPath": {"path": f"../{model_folder}"}}}
    pbism = {"$schema": PBISM_SCHEMA, "version": "4.2", "settings": {"qnaEnabled": True}}
    page = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json",
        "name": page_id,
        "displayName": report.get("title", dataset_name),
        "displayOption": "FitToPage",
        "height": 720,
        "width": 1280,
    }
    drill_dimension = next(
        (item["source_column"] for item in model_contract["dimensions"] if item["table"] != "DimDate"),
        str(modeled_frame.columns[0]) if len(modeled_frame.columns) else None,
    )
    drill_page = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json",
        "name": drill_page_id,
        "displayName": "Drill-through Detail",
        "displayOption": "FitToPage",
        "height": 720,
        "width": 1280,
        "type": "Drillthrough",
        "visibility": "HiddenInViewMode",
        "pageBinding": {
            "name": str(uuid5(NAMESPACE_URL, f"{report_id}:drillthrough")),
            "type": "Drillthrough",
            "parameters": ([{"name": str(drill_dimension), "asAggregation": False, "fieldExpr": _field_expression(str(drill_dimension))}] if drill_dimension else []),
        },
        "annotations": [{"name": "purpose", "value": "Transaction-level detail filtered from the overview."}],
    }
    tooltip_page = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json",
        "name": tooltip_page_id,
        "displayName": "Tooltip Summary",
        "displayOption": "FitToPage",
        "height": 320,
        "width": 480,
        "type": "Tooltip",
        "visibility": "HiddenInViewMode",
        "pageBinding": {"name": str(uuid5(NAMESPACE_URL, f"{report_id}:tooltip")), "type": "Tooltip"},
        "annotations": [{"name": "purpose", "value": "Reusable report-page tooltip for additional metric context."}],
    }
    entries: dict[str, bytes | str] = {
        **model_entries,
        f"{project_name}.pbip": _json_bytes(pbip),
        f"{report_folder}/definition.pbir": _json_bytes(pbir),
        f"{model_folder}/definition.pbism": _json_bytes(pbism),
        f"{model_folder}/model.bim": _json_bytes(model),
        f"{report_folder}/definition/version.json": _json_bytes({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/versionMetadata/1.0.0/schema.json",
            "version": "2.0.0",
        }),
        f"{report_folder}/definition/report.json": _json_bytes({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/3.2.0/schema.json",
            "themeCollection": {
                "baseTheme": {
                    "name": "CY24SU06",
                    "reportVersionAtImport": {"visual": "2.9.0", "page": "2.1.0", "report": "3.2.0"},
                    "type": "SharedResources",
                },
                "customTheme": {
                    "name": theme_name,
                    "reportVersionAtImport": {"visual": "2.9.0", "page": "2.1.0", "report": "3.2.0"},
                    "type": "RegisteredResources",
                },
            },
            "objects": {},
            "resourcePackages": [{
                "name": "RegisteredResources",
                "type": "RegisteredResources",
                "items": [{"name": theme_name, "path": theme_name, "type": "CustomTheme"}],
            }],
        }),
        f"{report_folder}/definition/pages/pages.json": _json_bytes({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.0.0/schema.json",
            "pageOrder": [page_id, drill_page_id, tooltip_page_id],
            "activePageName": page_id,
        }),
        f"{report_folder}/definition/pages/{page_id}/page.json": _json_bytes(page),
        f"{report_folder}/definition/pages/{drill_page_id}/page.json": _json_bytes(drill_page),
        f"{report_folder}/definition/pages/{tooltip_page_id}/page.json": _json_bytes(tooltip_page),
        f"{report_folder}/StaticResources/RegisteredResources/{theme_name}": theme_bytes,
        ".gitignore": "**/.pbi/localSettings.json\n**/.pbi/cache.abf\n",
        "OPEN_IN_POWER_BI.txt": (
            "POWER BI DESKTOP PROJECT (.PBIP)\n\n"
            "1. Extract this entire ZIP file to a local folder.\n"
            f"2. Keep {project_name}.pbip, {report_folder}, {model_folder}, and data in the same folder.\n"
            "3. In Power BI Desktop, enable the Power BI Project (PBIP) and PBIR preview features if prompted.\n"
            f"4. Open {project_name}.pbip in Power BI Desktop.\n\n"
            "The source data is included under data/. Review MODEL_DESIGN.md and RLS_CONFIGURATION.md before publishing.\n"
            "The RLS role is intentionally fail-closed until client identities are mapped. This package is a PBIP project, not a renamed PBIX file.\n"
        ),
    }
    entries["README.txt"] = entries["OPEN_IN_POWER_BI.txt"]
    widgets = list(dashboard.get("widgets") or [])
    filter_columns = [str(item.get("column")) for item in (dashboard.get("filters") or []) if item.get("column")]
    filter_columns.extend(str(item["source_column"]) for item in model_contract["dimensions"] if item.get("source_column"))
    filter_columns = list(dict.fromkeys(column for column in filter_columns if column in [str(item) for item in modeled_frame.columns]))[:3]
    for index, column in enumerate(filter_columns):
        if column:
            widgets.append({
                "id": f"slicer:{column}",
                "widget_type": "slicer",
                "title": f"Filter by {column}",
                "config": {"column": str(column)},
                "x": index * 4,
                "y": 10,
                "w": 4,
                "h": 1,
            })
    for index, widget in enumerate(widgets):
        visual_id = sha1(f"{page_id}:{widget.get('id', index)}".encode("utf-8")).hexdigest()[:20]
        x = max(0, min(1270, round(float(widget.get("x", 0)) / 12 * 1280)))
        y = max(0, min(710, round(float(widget.get("y", 0)) * 64)))
        width = max(80, min(1280 - x, round(float(widget.get("w", 3)) / 12 * 1280)))
        height = max(60, min(720 - y, round(float(widget.get("h", 2)) * 64)))
        visual: dict[str, Any] = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.9.0/schema.json",
            "name": visual_id,
            "position": {"x": x, "y": y, "z": index, "width": width, "height": height, "tabOrder": index},
            "visual": {"visualType": _visual_type(widget)},
        }
        query = _visual_query(widget, report, available_columns=[str(column) for column in modeled_frame.columns])
        if query:
            visual["visual"]["query"] = query
        entries[f"{report_folder}/definition/pages/{page_id}/visuals/{visual_id}/visual.json"] = _json_bytes(visual)

    detail_visual_id = sha1(f"{drill_page_id}:detail-table".encode("utf-8")).hexdigest()[:20]
    detail_columns = [str(column) for column in modeled_frame.columns[:12]]
    entries[f"{report_folder}/definition/pages/{drill_page_id}/visuals/{detail_visual_id}/visual.json"] = _json_bytes({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.9.0/schema.json",
        "name": detail_visual_id,
        "position": {"x": 40, "y": 60, "z": 0, "width": 1200, "height": 620, "tabOrder": 0},
        "visual": {"visualType": "tableEx", "query": {"queryState": {"Values": {"projections": [_projection(column) for column in detail_columns]}}}},
    })
    tooltip_measure = next(iter(model["model"]["tables"][0].get("measures") or []), None)
    if tooltip_measure:
        tooltip_visual_id = sha1(f"{tooltip_page_id}:metric".encode("utf-8")).hexdigest()[:20]
        entries[f"{report_folder}/definition/pages/{tooltip_page_id}/visuals/{tooltip_visual_id}/visual.json"] = _json_bytes({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.9.0/schema.json",
            "name": tooltip_visual_id,
            "position": {"x": 20, "y": 20, "z": 0, "width": 440, "height": 280, "tabOrder": 0},
            "visual": {"visualType": "cardVisual", "query": {"queryState": {"Data": {"projections": [_measure_projection(tooltip_measure["name"])]}}}},
        })
    entries["export_manifest.json"] = _json_bytes({
        "format": "powerbi_pbip",
        "report_id": report_id,
        "dataset_name": dataset_name,
        "source_version_id": source_version_id,
        "template_id": (dashboard.get("template") or {}).get("id"),
        "columns": [item["source"] for item in columns],
        "visual_count": len([name for name in entries if name.endswith("/visual.json")]),
        "pages": {"overview": page_id, "drillthrough": drill_page_id, "tooltip": tooltip_page_id},
        "semantic_model": model_contract,
        "interactions": {"slicers": sum(widget.get("widget_type") == "slicer" for widget in widgets), "drillthrough_page": True, "tooltip_page": True},
        "manifest_revision": manifest.get("revision"),
        "client_handoff": {
            "download_extension": ".zip",
            "project_file": f"{project_name}.pbip",
            "open_with": "Power BI Desktop",
            "requires_extraction": True,
        },
    })
    return entries


def _tableau_root(frame: pd.DataFrame, report: dict[str, Any], dashboard: dict[str, Any]) -> bytes:
    columns = _column_specs(frame)
    root = ET.Element("workbook", {"source-platform": "win", "version": "2024.1", "xmlns:user": "http://www.tableausoftware.com/xml/user"})
    ET.SubElement(root, "preferences", {"default-format": "png"})
    ET.SubElement(root, "style-theme", {"name": "clean"})
    datasources_root = ET.SubElement(root, "datasources")
    datasource = ET.SubElement(datasources_root, "datasource", {"caption": "Data", "inline": "true", "name": "Data", "version": "2024.1"})
    connection = ET.SubElement(datasource, "connection", {"class": "textscan", "directory": "", "filename": "data.csv", "password": "", "server": ""})
    relation = ET.SubElement(connection, "relation", {"name": "data#csv", "table": "[data#csv]", "type": "table"})
    csv_columns = ET.SubElement(relation, "columns", {"character-set": "UTF-8", "header": "yes", "locale": "en_US", "separator": ","})
    for index, item in enumerate(columns):
        ET.SubElement(csv_columns, "column", {"datatype": item["tableau_type"], "name": item["source"], "ordinal": str(index)})
    metadata_records = ET.SubElement(datasource, "metadata-records")
    for index, item in enumerate(columns):
        record = ET.SubElement(metadata_records, "metadata-record", {"class": "column"})
        ET.SubElement(record, "remote-name").text = item["source"]
        ET.SubElement(record, "remote-type").text = item["tableau_type"]
        ET.SubElement(record, "local-name").text = f"[{item['source']}]"
        ET.SubElement(record, "parent-name").text = "[data#csv]"
        ET.SubElement(record, "remote-alias").text = item["source"]
        ET.SubElement(record, "ordinal").text = str(index)
        ET.SubElement(record, "family").text = "data#csv"
        ET.SubElement(record, "local-type").text = item["tableau_type"]
        ET.SubElement(record, "aggregation").text = "Sum" if item["tableau_type"] in {"integer", "real"} else "Count"
        ET.SubElement(record, "contains-null").text = "true"
    worksheets = ET.SubElement(root, "worksheets")
    widgets = dashboard.get("widgets") or []
    worksheet_names: list[str] = []
    for index, widget in enumerate(widgets):
        base_name = _safe_xml_name(widget.get("title"), f"Visual {index + 1}")
        name = base_name
        if name in worksheet_names:
            name = f"{base_name} {index + 1}"
        worksheet_names.append(name)
        worksheet = ET.SubElement(worksheets, "worksheet", {"name": name})
        table = ET.SubElement(worksheet, "table")
        view = ET.SubElement(table, "view")
        datasources = ET.SubElement(view, "datasources")
        ET.SubElement(datasources, "datasource", {"caption": "Data"}).text = "Data"
        dependencies = ET.SubElement(view, "datasource-dependencies", {"datasource": "Data"})
        chart = (widget.get("config") or {}).get("chart") or {}
        fields = [chart.get("category_column") or chart.get("x_column"), chart.get("value_column") or chart.get("y_column")]
        if widget.get("widget_type") == "table":
            fields = [*frame.columns[:8]]
        if not fields:
            fields = [columns[0]["source"]] if columns else []
        by_source = {item["source"]: item for item in columns}
        for field in fields:
            if field is not None and str(field) in by_source:
                item = by_source[str(field)]
                ET.SubElement(dependencies, "column", {"datatype": item["tableau_type"], "name": f"[{item['source']}]"})
        ET.SubElement(table, "style")
        panes = ET.SubElement(table, "panes")
        pane = ET.SubElement(panes, "pane")
        ET.SubElement(pane, "view").append(ET.Element("breakdown", {"value": "auto"}))
        ET.SubElement(table, "rows")
        ET.SubElement(table, "cols")
    dashboards = ET.SubElement(root, "dashboards")
    dashboard_node = ET.SubElement(dashboards, "dashboard", {"enable-sort-zone-taborder": "true", "name": "Dashboard"})
    layout = ET.SubElement(dashboard_node, "layout", {"dim-ordering": "alphabetic"})
    for index, name in enumerate(worksheet_names):
        widget = widgets[index]
        ET.SubElement(layout, "zone", {
            "h": str(max(60, int(widget.get("h", 2)) * 60)),
            "name": name,
            "show-title": "true",
            "type-v2": "worksheet",
            "w": str(max(100, int(widget.get("w", 3)) * 80)),
            "x": str(int(widget.get("x", 0)) * 80),
            "y": str(int(widget.get("y", 0)) * 60),
        })
    ET.SubElement(root, "windows").append(ET.Element("window", {"class": "dashboard", "name": "Dashboard"}))
    root.append(ET.Element("repository-location", {"derived-from": "/workbooks/generated", "id": str(uuid5(NAMESPACE_URL, str(report.get("id", "generated")))), "path": "/workbooks"}))
    tree = ET.ElementTree(root)
    output = BytesIO()
    tree.write(output, encoding="utf-8", xml_declaration=True)
    return output.getvalue()


def build_bi_desktop_exports(
    frame: pd.DataFrame,
    *,
    report: dict[str, Any],
    dashboard: dict[str, Any],
    manifest: dict[str, Any],
    dataset_name: str,
    source_version_id: str | None = None,
) -> dict[str, Any]:
    """Return Power BI PBIP and Tableau TWB/TWBX bytes for a report."""
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("BI export input must be a pandas DataFrame.")
    powerbi_zip = _zip_bytes(_powerbi_entries(frame, report, dashboard, manifest, dataset_name, source_version_id))
    twb = _tableau_root(frame, report, dashboard)
    tableau_instructions = (
        "TABLEAU PACKAGED WORKBOOK (.TWBX)\n\n"
        "Open the downloaded .twbx file directly in Tableau Desktop or Tableau Reader.\n"
        "The package contains report.twb and the local data.csv source, so no separate data download is required.\n"
    )
    twbx = _zip_bytes({
        "report.twb": twb,
        "data.csv": frame.to_csv(index=False, date_format="%Y-%m-%dT%H:%M:%S").encode("utf-8"),
        "OPEN_IN_TABLEAU.txt": tableau_instructions,
        "README.txt": tableau_instructions,
        "export_manifest.json": _json_bytes({
            "format": "tableau_twbx",
            "dataset_name": dataset_name,
            "source_version_id": source_version_id,
            "workbook_file": "report.twb",
            "data_file": "data.csv",
            "open_with": ["Tableau Desktop", "Tableau Reader"],
            "requires_extraction": False,
        }),
    })
    validation = {
        "powerbi": validate_powerbi_project_package(powerbi_zip),
        "tableau": validate_tableau_packaged_workbook(twbx),
    }
    return {"powerbi": powerbi_zip, "tableau": twbx, "tableau_twb": twb, "validation": validation}


# A short alias keeps call sites readable while preserving a descriptive public
# function for integrations that import this module directly.
build_bi_exports = build_bi_desktop_exports
