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
from pathlib import Path
import re
from typing import Any
from uuid import uuid5, NAMESPACE_URL
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd


PBIP_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json"
PBIR_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json"
PBISM_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json"


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n").encode("utf-8")


def _zip_bytes(entries: dict[str, bytes | str]) -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value.encode("utf-8") if isinstance(value, str) else value)
    return stream.getvalue()


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


def _m_expression(frame: pd.DataFrame, columns: list[dict[str, str]]) -> str:
    transforms = ", ".join(
        "{{\"{name}\", {m_type}}}".format(name=item["source"].replace('"', '""'), m_type=item["m_type"])
        for item in columns
        if item["m_type"] != "type text"
    )
    typed = f",\n    Typed = Table.TransformColumnTypes(PromotedHeaders, {{{transforms}}})" if transforms else ""
    final_step = "Typed" if transforms else "PromotedHeaders"
    return (
        "let\n"
        "    Source = Csv.Document(File.Contents(\"data/data.csv\"), [Delimiter=\",\", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),\n"
        "    PromotedHeaders = Table.PromoteHeaders(Source, [PromoteAllScalars=true])"
        f"{typed}\n"
        f"in\n    {final_step}"
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


def _dax_column(column: Any) -> str:
    return f"'Data'[{str(column).replace(']', ']]')}]"


def _powerbi_measures(report: dict[str, Any]) -> list[dict[str, Any]]:
    measures: list[dict[str, Any]] = []
    used: set[str] = set()
    for kpi in report.get("kpis", []):
        definition = kpi.get("definition") or {}
        kind = definition.get("definition_type")
        components = definition.get("components") or {}
        name = _measure_name(kpi.get("label"), used)
        expression = "COUNTROWS('Data')"
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
            expression = "COUNTROWS('Data')"
        if "$" in str(kpi.get("formatted_value", "")) and format_string != "0.0%":
            format_string = "$#,##0.00"
        measures.append({"name": name, "expression": expression, "formatString": format_string})
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
    if widget.get("widget_type") == "table":
        return "tableEx"
    chart = (widget.get("config") or {}).get("chart") or {}
    return "lineChart" if chart.get("chart_type") == "line" else "barChart"


def _projection(column: Any) -> dict[str, Any]:
    field = {"Column": {"Expression": {"SourceRef": {"Entity": "Data"}}, "Property": str(column)}}
    return {"field": field, "queryRef": f"Data.{column}", "nativeQueryRef": str(column)}


def _measure_projection(name: Any) -> dict[str, Any]:
    field = {"Measure": {"Expression": {"SourceRef": {"Entity": "Data"}}, "Property": str(name)}}
    return {"field": field, "queryRef": f"Data.{name}", "nativeQueryRef": str(name)}


def _visual_query(widget: dict[str, Any], report: dict[str, Any]) -> dict[str, Any] | None:
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
        projections = [_projection(column) for column in (table.get("columns") or [])[:12]]
        return {"queryState": {"Values": {"projections": projections}}} if projections else None
    return None


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
    columns = _column_specs(frame)
    model_columns = [
        {
            "name": item["name"],
            "dataType": item["power_type"],
            "sourceColumn": item["source"],
            "summarizeBy": "sum" if item["power_type"] in {"int64", "double"} else "none",
        }
        for item in columns
    ]
    table = {
        "name": "Data",
        "columns": model_columns,
        "measures": _powerbi_measures(report),
        "partitions": [{"name": "Data", "mode": "import", "source": {"type": "m", "expression": _m_expression(frame, columns)}}],
    }
    model = {
        "model": {
            "culture": "en-US",
            "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "tables": [table],
        }
    }
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
    entries: dict[str, bytes | str] = {
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
            "pageOrder": [page_id],
            "activePageName": page_id,
        }),
        f"{report_folder}/definition/pages/{page_id}/page.json": _json_bytes(page),
        f"{report_folder}/StaticResources/RegisteredResources/{theme_name}": theme_bytes,
        "data/data.csv": frame.to_csv(index=False, date_format="%Y-%m-%dT%H:%M:%S").encode("utf-8"),
        ".gitignore": "**/.pbi/localSettings.json\n**/.pbi/cache.abf\n",
        "README.txt": (
            "Generated Power BI project. Extract this ZIP, enable Power BI Desktop's "
            "Power BI Project (PBIP) preview feature, then open the .pbip file.\n"
        ),
    }
    widgets = dashboard.get("widgets") or []
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
        query = _visual_query(widget, report)
        if query:
            visual["visual"]["query"] = query
        entries[f"{report_folder}/definition/pages/{page_id}/visuals/{visual_id}/visual.json"] = _json_bytes(visual)
    entries["export_manifest.json"] = _json_bytes({
        "format": "powerbi_pbip",
        "report_id": report_id,
        "dataset_name": dataset_name,
        "source_version_id": source_version_id,
        "template_id": (dashboard.get("template") or {}).get("id"),
        "columns": [item["source"] for item in columns],
        "visual_count": len(widgets),
        "manifest_revision": manifest.get("revision"),
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
) -> dict[str, bytes]:
    """Return Power BI PBIP and Tableau TWB/TWBX bytes for a report."""
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("BI export input must be a pandas DataFrame.")
    powerbi_zip = _zip_bytes(_powerbi_entries(frame, report, dashboard, manifest, dataset_name, source_version_id))
    twb = _tableau_root(frame, report, dashboard)
    twbx = _zip_bytes({"report.twb": twb, "data.csv": frame.to_csv(index=False, date_format="%Y-%m-%dT%H:%M:%S").encode("utf-8"), "README.txt": "Generated Tableau packaged workbook. Open report.twbx in Tableau Desktop or Tableau Reader.\n"})
    return {"powerbi": powerbi_zip, "tableau": twbx, "tableau_twb": twb}


# A short alias keeps call sites readable while preserving a descriptive public
# function for integrations that import this module directly.
build_bi_exports = build_bi_desktop_exports
