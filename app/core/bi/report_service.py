from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from app.core.bi.export_formats import (
    DESKTOP_EXPORT_FORMATS,
    resolve_export_formats,
    write_export_files,
)
from app.core.dashboard.layout import validate_layout
from app.core.dashboard.templates import (
    apply_dashboard_template,
    get_dashboard_template,
    list_dashboard_templates,
)
from app.core.kpi.calculator import calculate_kpi
from app.core.reporting.pdf_generator import build_pdf_bytes
from app.core.reporting.report_builder import assemble_report, render_html
from app.core.reporting.bi_exports import build_bi_desktop_exports
from app.core.reporting.xlsx_generator import build_xlsx_bytes
from app.core.visualization.bar_chart import build_bar_chart
from app.core.visualization.line_chart import build_line_chart


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _column_map(df: pd.DataFrame) -> dict[str, str]:
    return {_normalize_field_name(column): column for column in df.columns}


def _normalize_field_name(value: Any) -> str:
    """Normalize common snake_case, spaced, and hyphenated field names."""
    return "".join(character for character in str(value).casefold() if character.isalnum())


def _find_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    columns = _column_map(df)
    for candidate in candidates:
        found = columns.get(_normalize_field_name(candidate))
        if found is not None:
            return found
    return None


def _jsonable(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        return _jsonable(value.item())
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _format_number(value: float | int | None, *, currency: bool = False, percent: bool = False) -> str:
    if value is None or pd.isna(value):
        return "—"
    number = float(value)
    if percent:
        return f"{number:,.1f}%"
    sign = "-$" if currency and number < 0 else "$" if currency else ""
    magnitude = abs(number)
    if currency and magnitude >= 1_000_000:
        return f"{sign}{magnitude / 1_000_000:,.1f}M"
    if currency and magnitude >= 1_000:
        return f"{sign}{magnitude / 1_000:,.1f}k"
    if number.is_integer():
        return f"{sign}{magnitude:,.0f}"
    return f"{sign}{magnitude:,.2f}"


def _kpi_content(
    df: pd.DataFrame,
    *,
    source_ref: str,
    label: str,
    definition: dict[str, Any],
    currency: bool = False,
    percent: bool = False,
) -> dict[str, Any]:
    result = calculate_kpi(df, definition)
    total = result["total"]
    value = total.get("value")
    return {
        "label": label,
        "value": _jsonable(value),
        "formatted_value": _format_number(value, currency=currency, percent=percent),
        "source_ref": source_ref,
        "definition": definition,
        "components": _jsonable(total.get("components")),
        "filtered_source_rows": result.get("filtered_source_rows"),
    }


def _table(rows: list[dict[str, Any]], columns: list[str] | None = None) -> dict[str, Any]:
    return {"columns": columns or (list(rows[0].keys()) if rows else []), "rows": _jsonable(rows)}


def _chart_path(output_dir: Path, name: str) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    return str(output_dir / f"{name}.png")


def build_bi_report(
    df: pd.DataFrame,
    *,
    dataset_name: str,
    source_version_id: str | None = None,
    filters: dict[str, str | None] | None = None,
    template_id: str | None = None,
    output_dir: str | Path | None = None,
    findings: list[dict[str, Any]] | None = None,
    exports: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build a source-backed BI dashboard and exportable report from one dataset version.

    ``exports`` selects which artifacts are built and written to ``output_dir``;
    it defaults to every supported format.
    """
    if not isinstance(df, pd.DataFrame):
        raise ValueError("BI report input must be a pandas DataFrame.")

    export_formats = resolve_export_formats(exports)

    from app.core.bi.airline_report import build_airline_bi_report, is_airline_operations_dataset
    if is_airline_operations_dataset(df):
        return build_airline_bi_report(
            df,
            dataset_name=dataset_name,
            source_version_id=source_version_id,
            filters=filters,
            template_id=template_id,
            output_dir=output_dir,
            findings=findings,
            exports=export_formats,
        )

    dashboard_template = get_dashboard_template(template_id)
    # Keep the default executive template focused; richer templates deliberately
    # opt into the additional operational driver visuals.
    extended_visuals = dashboard_template["id"] in {"powerbi_executive_sales", "powerbi_kpi_slicer", "operations"}
    report_id = str(uuid4())
    output_path = Path(output_dir).expanduser().resolve() / report_id if output_dir else None
    work = df.copy(deep=True)
    filter_source = df.copy(deep=False)
    sales_column = _find_column(work, ["sales", "weekly sales", "weekly_sales", "net sales", "total revenue", "total_revenue", "revenue", "amount", "sales amount", "total sales", "gross sales"])
    profit_column = _find_column(work, ["profit", "total profit", "total_profit", "gross profit", "net profit"])
    units_column = _find_column(work, ["units sold", "units_sold", "units", "quantity", "volume"])
    date_column = _find_column(work, ["date", "order date", "transaction date", "week", "month"])
    country_column = _find_column(work, ["country", "geography", "region"])
    product_column = _find_column(work, ["product", "product name", "item type", "item_type", "item", "category", "product category"])
    segment_column = _find_column(work, ["segment", "customer segment", "sales channel", "channel", "order priority", "payment mode", "paymentmode", "category"])
    channel_column = _find_column(work, ["sales channel", "sales_channel", "channel", "payment mode", "paymentmode", "order channel"])
    customer_column = _find_column(work, ["customer", "customer name", "customername", "client", "account"])
    store_column = _find_column(work, ["store", "store id", "store_id", "location", "location id"])
    holiday_column = _find_column(work, ["holiday flag", "holiday_flag", "holiday", "is holiday"])
    location_column = country_column or store_column
    location_label = "country" if country_column else "store" if store_column else None

    numeric_columns = []
    for column in [sales_column, profit_column, units_column]:
        if column and column not in numeric_columns:
            work[column] = pd.to_numeric(work[column], errors="coerce")
            numeric_columns.append(column)

    applied_filters = {}
    filter_columns = {
        "country": country_column,
        "product": product_column,
        "segment": segment_column,
    }
    if store_column:
        filter_columns["store"] = store_column
    for filter_name, column in filter_columns.items():
        requested = (filters or {}).get(filter_name)
        if filter_name == "store" and not requested and not country_column:
            requested = (filters or {}).get("country")
        if requested and column:
            work = work[work[column].astype(str) == str(requested)]
            applied_filters[filter_name if filter_name != "store" or "store" in (filters or {}) else "country"] = str(requested)

    source = {
        "type": "dataset_version",
        "name": dataset_name,
        "version_id": source_version_id,
        "base_row_count": int(len(df)),
        "row_count": int(len(work)),
        "column_count": int(len(work.columns)),
        "filters": applied_filters,
    }

    kpis: list[dict[str, Any]] = []
    kpi_by_ref: dict[str, dict[str, Any]] = {}

    row_content = {
        "label": "Rows analyzed",
        "value": int(len(work)),
        "formatted_value": _format_number(len(work)),
        "source_ref": "kpi:rows",
    }
    kpis.append(row_content)
    kpi_by_ref["kpi:rows"] = row_content

    if sales_column:
        content = _kpi_content(
            work,
            source_ref="kpi:total_sales",
            label="Total sales",
            definition={"definition_type": "aggregate", "components": {"value": {"aggregation": "sum", "column": sales_column}}},
            currency=True,
        )
        kpis.append(content)
        kpi_by_ref["kpi:total_sales"] = content
    if profit_column:
        content = _kpi_content(
            work,
            source_ref="kpi:total_profit",
            label="Total profit",
            definition={"definition_type": "aggregate", "components": {"value": {"aggregation": "sum", "column": profit_column}}},
            currency=True,
        )
        kpis.append(content)
        kpi_by_ref["kpi:total_profit"] = content
    if units_column:
        content = _kpi_content(
            work,
            source_ref="kpi:units",
            label="Units sold",
            definition={"definition_type": "aggregate", "components": {"value": {"aggregation": "sum", "column": units_column}}},
        )
        kpis.append(content)
        kpi_by_ref["kpi:units"] = content
    if sales_column and profit_column:
        content = _kpi_content(
            work,
            source_ref="kpi:profit_margin",
            label="Profit margin",
            definition={
                "definition_type": "ratio",
                "ratio_scale": 100,
                "components": {
                    "numerator": {"aggregation": "sum", "column": profit_column},
                    "denominator": {"aggregation": "sum", "column": sales_column},
                },
            },
            percent=True,
        )
        kpis.append(content)
        kpi_by_ref["kpi:profit_margin"] = content
    if sales_column:
        sales_values = pd.to_numeric(work[sales_column], errors="coerce").dropna()
        average_sales = float(sales_values.mean()) if not sales_values.empty else None
        content = {
            "label": "Average sales per record",
            "value": _jsonable(average_sales),
            "formatted_value": _format_number(average_sales, currency=True),
            "source_ref": "kpi:average_sales",
            "definition": {"definition_type": "average", "column": sales_column},
        }
        kpis.append(content)
        kpi_by_ref["kpi:average_sales"] = content
    if store_column:
        stores = work[store_column].dropna().astype(str).nunique()
        content = {
            "label": "Stores covered",
            "value": int(stores),
            "formatted_value": _format_number(stores),
            "source_ref": "kpi:stores",
            "definition": {"definition_type": "distinct_count", "column": store_column},
        }
        kpis.append(content)
        kpi_by_ref["kpi:stores"] = content

    charts: list[dict[str, Any]] = []
    chart_by_ref: dict[str, dict[str, Any]] = {}
    notes: list[str] = []

    from app.core.visualization.echarts import generate_echarts_option

    def add_chart(source_ref: str, spec: dict[str, Any]) -> None:
        content = {**_jsonable(spec), "source_ref": source_ref}
        try:
            content["echarts_option"] = generate_echarts_option(content)
        except Exception as exc:
            # The frontend falls back to basic rendering, but the report must say so.
            notes.append(f"Interactive rendering unavailable for {content.get('title', source_ref)}: {exc}")
        charts.append(content)
        chart_by_ref[source_ref] = content

    def add_pie_chart(source_ref: str, *, category_column: str, value_column: str, title: str) -> None:
        """Add a compact share-of-total visual for channel/category mixes."""
        grouped = (
            work.dropna(subset=[category_column, value_column])
            .groupby(category_column, dropna=False)[value_column]
            .sum()
            .sort_values(ascending=False)
            .head(8)
        )
        if grouped.empty:
            return
        add_chart(source_ref, {
            "chart_type": "pie",
            "category_column": category_column,
            "value_column": value_column,
            "aggregation": "sum",
            "title": title,
            "data": [{category_column: str(index), "value": float(value)} for index, value in grouped.items()],
        })

    if sales_column and location_column:
        location_chart_name = "sales_by_country" if country_column else "sales_by_store"
        location_source_ref = "chart:sales_by_country" if country_column else "chart:sales_by_store"
        chart_output = _chart_path(output_path, location_chart_name) if output_path else None
        try:
            spec = build_bar_chart(
                    work,
                    category_column=location_column,
                    value_column=sales_column,
                    aggregation="sum",
                    top_n=10,
                    title=f"Sales by {location_label}",
                    y_label="Sales",
                    output_path=chart_output,
                )
            if chart_output:
                spec["artifact_path"] = chart_output
            add_chart(location_source_ref, spec)
        except Exception as exc:
            notes.append(f"Sales by {location_label} chart unavailable: {exc}")

    if profit_column and product_column:
        chart_output = _chart_path(output_path, "profit_by_product") if output_path else None
        try:
            spec = build_bar_chart(
                    work,
                    category_column=product_column,
                    value_column=profit_column,
                    aggregation="sum",
                    top_n=10,
                    title="Profit by product",
                    y_label="Profit",
                    output_path=chart_output,
                )
            if chart_output:
                spec["artifact_path"] = chart_output
            add_chart("chart:profit_by_product", spec)
        except Exception as exc:
            notes.append(f"Profit by product chart unavailable: {exc}")

    if sales_column and date_column:
        chart_output = _chart_path(output_path, "sales_over_time") if output_path else None
        try:
            spec = build_line_chart(
                    work,
                    x_column=date_column,
                    y_column=sales_column,
                    aggregation="sum",
                    parse_datetime=True,
                    frequency="MS",
                    fill_missing_intervals=True,
                    title="Sales trend over time",
                    y_label="Sales",
                    output_path=chart_output,
                )
            if chart_output:
                spec["artifact_path"] = chart_output
            add_chart("chart:sales_over_time", spec)
        except Exception as exc:
            notes.append(f"Sales trend unavailable: {exc}")

    if sales_column and channel_column:
        try:
            add_pie_chart("chart:sales_by_channel", category_column=channel_column, value_column=sales_column, title="Revenue mix by sales channel")
        except Exception as exc:
            notes.append(f"Sales-channel mix unavailable: {exc}")

    if extended_visuals and units_column and product_column:
        chart_output = _chart_path(output_path, "units_by_product") if output_path else None
        try:
            spec = build_bar_chart(
                work,
                category_column=product_column,
                value_column=units_column,
                aggregation="sum",
                top_n=10,
                orientation="horizontal",
                title=f"Units sold by {product_column}",
                x_label="Units sold",
                y_label=product_column,
                output_path=chart_output,
            )
            if chart_output:
                spec["artifact_path"] = chart_output
            add_chart("chart:units_by_product", spec)
        except Exception as exc:
            notes.append(f"Units by product unavailable: {exc}")

    if extended_visuals and profit_column and date_column:
        chart_output = _chart_path(output_path, "profit_over_time") if output_path else None
        try:
            spec = build_line_chart(
                work,
                x_column=date_column,
                y_column=profit_column,
                aggregation="sum",
                parse_datetime=True,
                frequency="MS",
                fill_missing_intervals=True,
                title="Profit trend over time",
                y_label="Profit",
                output_path=chart_output,
            )
            if chart_output:
                spec["artifact_path"] = chart_output
            add_chart("chart:profit_over_time", spec)
        except Exception as exc:
            notes.append(f"Profit trend unavailable: {exc}")

    if not charts:
        notes.append("No compatible categorical or time fields were available for charts.")

    tables: list[dict[str, Any]] = []
    table_by_ref: dict[str, dict[str, Any]] = {}
    if sales_column and location_column:
        grouped = work.dropna(subset=[location_column, sales_column]).groupby(location_column, dropna=False)[sales_column].sum().sort_values(ascending=False).head(10)
        location_key = "country" if country_column else "store"
        content = _table(
            [{location_key: str(index), "sales": float(value)} for index, value in grouped.items()],
            [location_key, "sales"],
        )
        content["source_ref"] = "table:top_countries" if country_column else "table:top_stores"
        content["title"] = "Top countries by sales" if country_column else "Top stores by sales"
        tables.append(content)
        table_by_ref[content["source_ref"]] = content
    if profit_column and product_column:
        grouped = work.dropna(subset=[product_column, profit_column]).groupby(product_column, dropna=False)[profit_column].sum().sort_values(ascending=False).head(10)
        content = _table(
            [{"product": str(index), "profit": float(value)} for index, value in grouped.items()],
            ["product", "profit"],
        )
        content["source_ref"] = "table:top_products"
        content["title"] = "Top products by profit"
        tables.append(content)
        table_by_ref["table:top_products"] = content

    if findings is None:
        from app.core.eda.findings import detect_findings
        finding_items = detect_findings(work).get("findings", [])
    else:
        finding_items = findings
    finding_lines = [
        f"- {item.get('message', item.get('code', 'Finding'))}"
        for item in finding_items[:10]
        if item.get("message") or item.get("code")
    ]
    findings_text = "Deterministic findings were not detected." if not finding_lines else "\n".join(finding_lines)

    total_sales = kpi_by_ref.get("kpi:total_sales", {}).get("value")
    total_profit = kpi_by_ref.get("kpi:total_profit", {}).get("value")
    margin = kpi_by_ref.get("kpi:profit_margin", {}).get("formatted_value")
    sales_summary = f"Total sales are {_format_number(total_sales, currency=True)}" if total_sales is not None else "Total sales are not available"
    profit_summary = f"Total profit is {_format_number(total_profit, currency=True)}" if total_profit is not None else "Profit is not available in this source"
    summary = (
        f"This BI report covers {len(work):,} rows from {dataset_name}. "
        f"{sales_summary} and {profit_summary}. "
        f"The calculated profit margin is {margin or 'not available'}."
    )
    if applied_filters:
        summary += " Filters applied: " + ", ".join(f"{key}={value}" for key, value in applied_filters.items()) + "."

    report_sections: list[dict[str, Any]] = [
        {"id": "executive_summary", "section_type": "text", "title": "Executive Summary", "content": summary, "position": 10},
        {"id": "metrics_heading", "section_type": "heading", "title": "Headline metrics", "content": "Headline metrics", "position": 20},
    ]
    for index, content in enumerate(kpis, start=1):
        report_sections.append({
            "id": f"kpi_{index}",
            "section_type": "kpi",
            "title": content["label"],
            "source_ref": content["source_ref"],
            "position": 30 + index,
        })
    report_sections.append({"id": "drivers_heading", "section_type": "heading", "title": "Drivers and trends", "content": "Drivers and trends", "position": 50})
    for index, chart in enumerate(charts, start=1):
        report_sections.append({
            "id": f"chart_{index}",
            "section_type": "chart",
            "title": chart.get("title", "Chart"),
            "source_ref": chart["source_ref"],
            "position": 60 + index,
        })
    for index, table in enumerate(tables, start=1):
        report_sections.append({
            "id": f"table_{index}",
            "section_type": "table",
            "title": "Top " + (
                "countries by sales"
                if table["source_ref"] == "table:top_countries"
                else "stores by sales"
                if table["source_ref"] == "table:top_stores"
                else "products by profit"
            ),
            "source_ref": table["source_ref"],
            "position": 80 + index,
        })
    report_sections.extend([
        {
            "id": "findings",
            "section_type": "text",
            "title": "Key findings",
            "content": findings_text,
            "position": 95,
        },
        {
            "id": "next_steps",
            "section_type": "text",
            "title": "Recommended next steps",
            "content": "Use the filters to compare markets, segments, or products. Review the highest-impact drivers before making pricing, inventory, or sales decisions.",
            "position": 100,
        },
        {
            "id": "caveats",
            "section_type": "text",
            "title": "Caveats and assumptions",
            "content": "Metrics are calculated from the selected dataset version. Missing values and business-valid negative values are retained in the source context; chart availability depends on recognizable date, category, and numeric columns." + (" " + " ".join(notes) if notes else ""),
            "position": 110,
        },
    ])

    report = {
        "id": report_id,
        "title": f"{dataset_name} BI Report",
        "subtitle": "Business performance overview",
        "description": f"Source-backed BI report generated from {dataset_name}.",
        "revision": 1,
        "parameters": {
            "source_version_id": source_version_id or "",
            "generated_at": _utc_now().isoformat(),
            "dashboard_template_id": dashboard_template["id"],
            **applied_filters,
        },
    }
    content_by_ref = {**kpi_by_ref, **chart_by_ref, **table_by_ref}
    manifest = assemble_report(report, report_sections, content_by_ref=content_by_ref, parameters=report["parameters"], missing_content_policy="error")

    widgets = []
    for index, content in enumerate(kpis):
        widgets.append({
            "id": content["source_ref"],
            "widget_type": "kpi",
            "title": content["label"],
            "source_ref": content["source_ref"],
            "config": {"value": content["formatted_value"], "label": content["label"]},
            "x": (index % 4) * 3,
            "y": (index // 4) * 2,
            "w": 3,
            "h": 2,
        })
    for index, chart in enumerate(charts):
        widgets.append({
            "id": chart["source_ref"],
            "widget_type": "chart",
            "title": chart.get("title", "Chart"),
            "source_ref": chart["source_ref"],
            "config": {"chart": chart},
            "x": (index % 2) * 6,
            "y": 4 + (index // 2) * 5,
            "w": 6,
            "h": 4,
        })
    for index, table in enumerate(tables):
        widgets.append({
            "id": table["source_ref"],
            "widget_type": "table",
            "title": table.get("title") or "Detail table",
            "source_ref": table["source_ref"],
            "config": {"table": table},
            "x": 0,
            "y": 4 + len(charts) * 5 + index * 5,
            "w": 12,
            "h": 5,
        })
    widgets = apply_dashboard_template(dashboard_template, widgets)
    layout_validation = validate_layout(widgets)
    if not layout_validation["valid"]:
        raise ValueError(f"Generated BI dashboard layout is invalid: {layout_validation['collisions']}")

    dashboard_filters = [
        {"name": "country", "column": country_column, "values": sorted(filter_source[country_column].dropna().astype(str).unique().tolist()) if country_column else []},
        {"name": "product", "column": product_column, "values": sorted(filter_source[product_column].dropna().astype(str).unique().tolist()) if product_column else []},
        {"name": "segment", "column": segment_column, "values": sorted(filter_source[segment_column].dropna().astype(str).unique().tolist()) if segment_column else []},
    ]
    if store_column:
        dashboard_filters.append({"name": "store", "column": store_column, "values": sorted(filter_source[store_column].dropna().astype(str).unique().tolist())})

    dashboard = {
        "id": f"dashboard:{report_id}",
        "title": report["title"],
        "description": report["description"],
        "template": dashboard_template,
        "available_templates": list_dashboard_templates(),
        "source": source,
        "filters": dashboard_filters,
        "widgets": widgets,
        "layout_validation": layout_validation,
    }

    html_report = render_html(manifest)
    pdf_bytes = build_pdf_bytes(manifest, document_title=report["title"], subject=report["description"]) if "pdf" in export_formats else b""
    xlsx_bytes = build_xlsx_bytes(manifest, source_frame=work) if "xlsx" in export_formats else b""
    desktop_exports = {"powerbi": b"", "tableau": b"", "tableau_twb": b"", "validation": {}}
    if export_formats.intersection(DESKTOP_EXPORT_FORMATS):
        desktop_exports = build_bi_desktop_exports(
            work,
            report={**report, "kpis": kpis},
            dashboard=dashboard,
            manifest=manifest,
            dataset_name=dataset_name,
            source_version_id=source_version_id,
        )
    powerbi_bytes = desktop_exports["powerbi"]
    tableau_twbx_bytes = desktop_exports["tableau"]
    tableau_twb_bytes = desktop_exports["tableau_twb"]
    files = {}
    if output_path:
        files = write_export_files(
            output_path,
            export_formats,
            {
                "html": html_report.encode("utf-8"),
                "pdf": pdf_bytes,
                "xlsx": xlsx_bytes,
                "powerbi": powerbi_bytes,
                "tableau": tableau_twbx_bytes,
                "tableau_twb": tableau_twb_bytes,
            },
        )

    return {
        "report_id": report_id,
        "report": report,
        "source": source,
        "field_mapping": {
            "sales": sales_column,
            "profit": profit_column,
            "units": units_column,
            "date": date_column,
            "country": country_column,
            "product": product_column,
            "segment": segment_column,
            "store": store_column,
            "holiday": holiday_column,
        },
        "filters": dashboard["filters"],
        "applied_filters": applied_filters,
        "kpis": kpis,
        "charts": charts,
        "tables": tables,
        "findings": finding_items,
        "dashboard": dashboard,
        "manifest": manifest,
        "html": html_report,
        "pdf_bytes": pdf_bytes,
        "xlsx_bytes": xlsx_bytes,
        "powerbi_bytes": powerbi_bytes,
        "tableau_twbx_bytes": tableau_twbx_bytes,
        "tableau_twb_bytes": tableau_twb_bytes,
        "desktop_export_validation": desktop_exports["validation"],
        "export_formats": sorted(export_formats),
        "files": files,
    }
