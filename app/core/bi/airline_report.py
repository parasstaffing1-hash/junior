"""U.S. DOT/BTS-aware airline operations BI report generation."""

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
from app.core.dashboard.templates import apply_dashboard_template, get_dashboard_template, list_dashboard_templates
from app.core.reporting.bi_exports import build_bi_desktop_exports
from app.core.reporting.pdf_generator import build_pdf_bytes
from app.core.reporting.report_builder import assemble_report, render_html
from app.core.reporting.xlsx_generator import build_xlsx_bytes
from app.core.visualization.bar_chart import build_bar_chart
from app.core.visualization.line_chart import build_line_chart


def _normalized(value: Any) -> str:
    return "".join(character for character in str(value).casefold() if character.isalnum())


def _find(frame: pd.DataFrame, *candidates: str) -> str | None:
    columns = {_normalized(column): str(column) for column in frame.columns}
    return next((columns[_normalized(item)] for item in candidates if _normalized(item) in columns), None)


def is_airline_operations_dataset(frame: pd.DataFrame) -> bool:
    """Identify BTS-like flight data without relying on the source filename."""
    flight_date = _find(frame, "FlightDate", "Flight Date")
    carrier = _find(frame, "Reporting_Airline", "Reporting Carrier", "Reporting_Airline", "IATA_CODE_Reporting_Airline", "Carrier")
    origin = _find(frame, "Origin", "Origin Airport")
    destination = _find(frame, "Dest", "Destination", "Destination Airport")
    return bool(flight_date and carrier and origin and destination)


def _number(value: Any, *, percent: bool = False, minutes: bool = False) -> str:
    if value is None or pd.isna(value):
        return "—"
    number = float(value)
    if percent:
        return f"{number:,.1f}%"
    if minutes:
        return f"{number:,.1f} min"
    return f"{number:,.0f}" if number.is_integer() else f"{number:,.2f}"


def _kpi(label: str, value: Any, source_ref: str, definition: dict[str, Any], *, percent: bool = False, minutes: bool = False) -> dict[str, Any]:
    return {
        "label": label,
        "value": None if value is None or pd.isna(value) else float(value) if isinstance(value, float) else int(value) if isinstance(value, int) else value,
        "formatted_value": _number(value, percent=percent, minutes=minutes),
        "source_ref": source_ref,
        "definition": definition,
    }


def _safe_rate(numerator: float, denominator: float) -> float | None:
    return (100.0 * float(numerator) / float(denominator)) if denominator else None


def _jsonable_rows(frame: pd.DataFrame, limit: int = 15) -> list[dict[str, Any]]:
    safe = frame.head(limit).copy()
    for column in safe.columns:
        if pd.api.types.is_datetime64_any_dtype(safe[column]):
            safe[column] = safe[column].dt.strftime("%Y-%m-%d")
    return safe.astype(object).where(pd.notna(safe), None).to_dict(orient="records")


def _prepare_model_frame(source: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str | None]]:
    columns = {
        "date": _find(source, "FlightDate", "Flight Date"),
        "carrier_code": _find(source, "IATA_CODE_Reporting_Airline", "Reporting_Airline", "Reporting Carrier", "Carrier"),
        "carrier_name": _find(source, "Reporting_Airline", "Airline", "CarrierName"),
        "origin": _find(source, "Origin", "Origin Airport"),
        "destination": _find(source, "Dest", "Destination", "Destination Airport"),
        "cancelled": _find(source, "Cancelled", "Canceled"),
        "diverted": _find(source, "Diverted"),
        "arr_delay": _find(source, "ArrDelay", "ArrivalDelay", "Arrival Delay", "ArrivalDelayMinutes"),
        "dep_delay": _find(source, "DepDelay", "DepartureDelay", "Departure Delay", "DepartureDelayMinutes"),
        "cancellation_code": _find(source, "CancellationCode", "Cancellation Code"),
        "distance": _find(source, "Distance", "DistanceMiles"),
        "air_time": _find(source, "AirTime", "Air Time", "AirTimeMinutes"),
        "carrier_delay": _find(source, "CarrierDelay", "Carrier Delay"),
        "weather_delay": _find(source, "WeatherDelay", "Weather Delay"),
        "nas_delay": _find(source, "NASDelay", "NAS Delay"),
        "security_delay": _find(source, "SecurityDelay", "Security Delay"),
        "late_aircraft_delay": _find(source, "LateAircraftDelay", "Late Aircraft Delay"),
        "flight_number": _find(source, "Flight_Number_Reporting_Airline", "FlightNumber", "Flight Number"),
        "tail_number": _find(source, "Tail_Number", "TailNumber", "Tail Number"),
    }
    model = pd.DataFrame(index=source.index)
    model["FlightDate"] = pd.to_datetime(source[columns["date"]], errors="coerce").dt.normalize()
    model["Carrier"] = source[columns["carrier_code"]].astype("string").fillna("Unknown")
    if columns["carrier_name"] and columns["carrier_name"] != columns["carrier_code"]:
        model["CarrierName"] = source[columns["carrier_name"]].astype("string").fillna(model["Carrier"])
    model["Origin"] = source[columns["origin"]].astype("string").fillna("Unknown")
    model["Destination"] = source[columns["destination"]].astype("string").fillna("Unknown")
    model["Route"] = model["Origin"] + " → " + model["Destination"]
    if columns["flight_number"]:
        model["FlightNumber"] = source[columns["flight_number"]].astype("string")
    if columns["tail_number"]:
        model["TailNumber"] = source[columns["tail_number"]].astype("string")

    def numeric(key: str, default: float = 0.0) -> pd.Series:
        column = columns[key]
        if not column:
            return pd.Series(default, index=source.index, dtype="float64")
        return pd.to_numeric(source[column], errors="coerce")

    cancelled = numeric("cancelled").fillna(0).gt(0).astype("int64")
    diverted = numeric("diverted").fillna(0).gt(0).astype("int64")
    arrival_delay = numeric("arr_delay", float("nan"))
    departure_delay = numeric("dep_delay", float("nan"))
    eligible = (cancelled.eq(0) & diverted.eq(0) & arrival_delay.notna()).astype("int64")
    on_time = (eligible.eq(1) & arrival_delay.le(15)).astype("int64")
    model["ScheduledFlights"] = 1
    model["OperatedFlights"] = cancelled.eq(0).astype("int64")
    model["CancelledFlights"] = cancelled
    model["DivertedFlights"] = diverted
    model["EligibleArrivals"] = eligible
    model["OnTimeArrivals"] = on_time
    model["OnTimeArrivalPctValue"] = on_time.where(eligible.eq(1)).astype("float64") * 100.0
    model["ArrivalDelayMinutes"] = arrival_delay
    model["DepartureDelayMinutes"] = departure_delay
    model["PositiveArrivalDelayMinutes"] = arrival_delay.clip(lower=0).fillna(0)
    if columns["distance"]:
        model["DistanceMiles"] = numeric("distance")
    if columns["air_time"]:
        model["AirTimeMinutes"] = numeric("air_time")
    if columns["cancellation_code"]:
        cancellation_map = {"A": "Carrier", "B": "Weather", "C": "National Aviation System", "D": "Security"}
        raw_codes = source[columns["cancellation_code"]].astype("string").str.strip().str.upper()
        model["CancellationReason"] = raw_codes.map(cancellation_map).fillna(raw_codes).fillna("Not cancelled")

    cause_columns = {
        "Carrier": numeric("carrier_delay"),
        "Weather": numeric("weather_delay"),
        "National Aviation System": numeric("nas_delay"),
        "Security": numeric("security_delay"),
        "Late Aircraft": numeric("late_aircraft_delay"),
    }
    causes = pd.DataFrame(cause_columns, index=source.index).fillna(0).clip(lower=0)
    if causes.to_numpy().sum() > 0:
        model["PrimaryDelayCause"] = causes.idxmax(axis=1).where(causes.max(axis=1).gt(0), "No reported cause")
        model["PrimaryDelayMinutes"] = causes.max(axis=1)
        model["ReportedCauseDelayMinutes"] = causes.sum(axis=1)
    else:
        model["PrimaryDelayCause"] = "No reported cause"
        model["PrimaryDelayMinutes"] = 0.0
        model["ReportedCauseDelayMinutes"] = 0.0
    return model.reset_index(drop=True), columns


def build_airline_bi_report(
    frame: pd.DataFrame,
    *,
    dataset_name: str,
    source_version_id: str | None = None,
    filters: dict[str, str | None] | None = None,
    template_id: str | None = None,
    output_dir: str | Path | None = None,
    findings: list[dict[str, Any]] | None = None,
    exports: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build a source-backed airline operations dashboard from BTS-like flight rows.

    ``exports`` selects which artifacts are built and written to ``output_dir``;
    it defaults to every supported format.
    """
    export_formats = resolve_export_formats(exports)
    model, field_mapping = _prepare_model_frame(frame)
    filter_source = model.copy(deep=False)
    applied_filters: dict[str, str] = {}
    for name, column in (("carrier", "Carrier"), ("origin", "Origin"), ("destination", "Destination"), ("route", "Route")):
        requested = (filters or {}).get(name)
        if requested:
            model = model[model[column].astype(str) == str(requested)]
            applied_filters[name] = str(requested)

    report_id = str(uuid4())
    dashboard_template = get_dashboard_template(template_id)
    output_path = Path(output_dir).expanduser().resolve() / report_id if output_dir else None
    if output_path:
        output_path.mkdir(parents=True, exist_ok=True)

    scheduled = int(model["ScheduledFlights"].sum())
    operated = int(model["OperatedFlights"].sum())
    cancelled = int(model["CancelledFlights"].sum())
    diverted = int(model["DivertedFlights"].sum())
    eligible = int(model["EligibleArrivals"].sum())
    on_time = int(model["OnTimeArrivals"].sum())
    avg_arrival_delay = model.loc[model["EligibleArrivals"].eq(1), "ArrivalDelayMinutes"].mean()
    total_delay = float(model["PositiveArrivalDelayMinutes"].sum())

    kpis = [
        _kpi("Scheduled Flights", scheduled, "kpi:scheduled_flights", {"definition_type": "aggregate", "components": {"value": {"aggregation": "sum", "column": "ScheduledFlights"}}}),
        _kpi("On-Time Arrival %", _safe_rate(on_time, eligible), "kpi:on_time_rate", {"definition_type": "ratio", "ratio_scale": 100, "components": {"numerator": {"aggregation": "sum", "column": "OnTimeArrivals"}, "denominator": {"aggregation": "sum", "column": "EligibleArrivals"}}}, percent=True),
        _kpi("Cancellation Rate", _safe_rate(cancelled, scheduled), "kpi:cancellation_rate", {"definition_type": "ratio", "ratio_scale": 100, "components": {"numerator": {"aggregation": "sum", "column": "CancelledFlights"}, "denominator": {"aggregation": "sum", "column": "ScheduledFlights"}}}, percent=True),
        _kpi("Average Arrival Delay", avg_arrival_delay, "kpi:avg_arrival_delay", {"definition_type": "average", "column": "ArrivalDelayMinutes"}, minutes=True),
        _kpi("Total Positive Delay", total_delay, "kpi:total_delay", {"definition_type": "aggregate", "components": {"value": {"aggregation": "sum", "column": "PositiveArrivalDelayMinutes"}}}, minutes=True),
        _kpi("Diverted Flights", diverted, "kpi:diverted", {"definition_type": "aggregate", "components": {"value": {"aggregation": "sum", "column": "DivertedFlights"}}}),
    ]
    kpi_by_ref = {item["source_ref"]: item for item in kpis}

    charts: list[dict[str, Any]] = []
    chart_by_ref: dict[str, dict[str, Any]] = {}
    notes: list[str] = []
    from app.core.visualization.echarts import generate_echarts_option

    def add_chart(source_ref: str, chart: dict[str, Any]) -> None:
        content = {**chart, "source_ref": source_ref}
        try:
            content["echarts_option"] = generate_echarts_option(content)
        except Exception as exc:
            # The frontend falls back to basic rendering, but the report must say so.
            notes.append(f"Interactive rendering unavailable for {content.get('title', source_ref)}: {exc}")
        charts.append(content)
        chart_by_ref[source_ref] = content

    add_chart("chart:daily_flights", build_line_chart(model, x_column="FlightDate", y_column="ScheduledFlights", aggregation="sum", parse_datetime=True, frequency="D", fill_missing_intervals=True, title="Daily Scheduled Flights", y_label="Flights", output_path=str(output_path / "daily_flights.png") if output_path else None))
    add_chart("chart:carrier_ontime", build_bar_chart(model, category_column="Carrier", value_column="OnTimeArrivalPctValue", aggregation="mean", top_n=15, title="On-Time Arrival Rate by Carrier", y_label="On-time %", output_path=str(output_path / "carrier_ontime.png") if output_path else None))
    add_chart("chart:origin_delay", build_bar_chart(model, category_column="Origin", value_column="ArrivalDelayMinutes", aggregation="mean", top_n=15, title="Average Arrival Delay by Origin", y_label="Minutes", output_path=str(output_path / "origin_delay.png") if output_path else None))
    add_chart("chart:delay_causes", build_bar_chart(model, category_column="PrimaryDelayCause", value_column="PrimaryDelayMinutes", aggregation="sum", top_n=6, title="Reported Delay Minutes by Primary Cause", y_label="Minutes", output_path=str(output_path / "delay_causes.png") if output_path else None))

    carrier_summary = model.groupby("Carrier", dropna=False).agg(
        scheduled_flights=("ScheduledFlights", "sum"),
        eligible_arrivals=("EligibleArrivals", "sum"),
        on_time_arrivals=("OnTimeArrivals", "sum"),
        cancelled_flights=("CancelledFlights", "sum"),
        average_arrival_delay=("ArrivalDelayMinutes", "mean"),
    ).reset_index()
    carrier_summary["on_time_arrival_pct"] = 100 * carrier_summary["on_time_arrivals"] / carrier_summary["eligible_arrivals"].replace(0, pd.NA)
    carrier_summary["cancellation_pct"] = 100 * carrier_summary["cancelled_flights"] / carrier_summary["scheduled_flights"].replace(0, pd.NA)
    carrier_summary = carrier_summary.sort_values(["scheduled_flights", "Carrier"], ascending=[False, True])

    route_summary = model.groupby("Route", dropna=False).agg(
        scheduled_flights=("ScheduledFlights", "sum"),
        on_time_arrival_pct=("OnTimeArrivalPctValue", "mean"),
        average_arrival_delay=("ArrivalDelayMinutes", "mean"),
        cancellation_pct=("CancelledFlights", "mean"),
    ).reset_index()
    route_summary["cancellation_pct"] *= 100
    route_summary = route_summary.sort_values(["scheduled_flights", "Route"], ascending=[False, True])
    tables = [
        {"title": "Carrier Performance", "source_ref": "table:carrier_performance", "columns": list(carrier_summary.columns), "rows": _jsonable_rows(carrier_summary)},
        {"title": "Busiest Routes", "source_ref": "table:routes", "columns": list(route_summary.columns), "rows": _jsonable_rows(route_summary)},
    ]
    table_by_ref = {item["source_ref"]: item for item in tables}

    insight_items: list[dict[str, Any]] = []
    reliable_carriers = carrier_summary[carrier_summary["eligible_arrivals"].ge(max(10, eligible * 0.01))]
    if not reliable_carriers.empty:
        worst = reliable_carriers.sort_values("on_time_arrival_pct").iloc[0]
        insight_items.append({"code": "CARRIER_ON_TIME_GAP", "message": f"{worst['Carrier']} has the lowest on-time arrival rate among material carriers at {worst['on_time_arrival_pct']:.1f}%.", "evidence": {"carrier": str(worst["Carrier"]), "on_time_arrival_pct": float(worst["on_time_arrival_pct"]), "eligible_arrivals": int(worst["eligible_arrivals"])}})
    origin_summary = model.groupby("Origin").agg(flights=("ScheduledFlights", "sum"), average_delay=("ArrivalDelayMinutes", "mean")).reset_index()
    material_origins = origin_summary[origin_summary["flights"].ge(max(10, scheduled * 0.005))]
    if not material_origins.empty:
        worst_origin = material_origins.sort_values("average_delay", ascending=False).iloc[0]
        insight_items.append({"code": "ORIGIN_DELAY_HOTSPOT", "message": f"{worst_origin['Origin']} is the highest-delay material origin at {worst_origin['average_delay']:.1f} average arrival-delay minutes.", "evidence": {"origin": str(worst_origin["Origin"]), "average_delay_minutes": float(worst_origin["average_delay"]), "flights": int(worst_origin["flights"])}})
    cause_totals = model.groupby("PrimaryDelayCause")["PrimaryDelayMinutes"].sum().sort_values(ascending=False)
    if not cause_totals.empty and cause_totals.iloc[0] > 0:
        insight_items.append({"code": "PRIMARY_DELAY_DRIVER", "message": f"{cause_totals.index[0]} is the largest reported primary delay cause with {cause_totals.iloc[0]:,.0f} minutes.", "evidence": {"cause": str(cause_totals.index[0]), "minutes": float(cause_totals.iloc[0])}})
    if findings:
        insight_items.extend(findings[:5])

    start_date = model["FlightDate"].min()
    end_date = model["FlightDate"].max()
    period = f"{start_date:%Y-%m-%d} to {end_date:%Y-%m-%d}" if pd.notna(start_date) and pd.notna(end_date) else "available source period"
    summary = f"{dataset_name} covers {scheduled:,} scheduled flights from {period}. {operated:,} operated; {_number(_safe_rate(on_time, eligible), percent=True)} arrived on time among eligible completed arrivals; {_number(_safe_rate(cancelled, scheduled), percent=True)} were cancelled."
    recommendation = "Prioritize the largest material carrier/airport delay gap, then test whether the leading reported cause and time-of-day pattern explain it. Monitor on-time arrival %, cancellation %, positive delay minutes per operated flight, and affected-passenger exposure next."

    sections: list[dict[str, Any]] = [
        {"id": "executive_summary", "section_type": "text", "title": "Executive Summary", "content": summary, "position": 10},
        {"id": "metrics", "section_type": "heading", "title": "Operational KPIs", "content": "Operational KPIs", "position": 20},
    ]
    sections.extend({"id": f"kpi_{index}", "section_type": "kpi", "title": item["label"], "source_ref": item["source_ref"], "position": 20 + index} for index, item in enumerate(kpis, 1))
    sections.append({"id": "drivers", "section_type": "heading", "title": "Trends and Drivers", "content": "Trends and Drivers", "position": 40})
    sections.extend({"id": f"chart_{index}", "section_type": "chart", "title": item["title"], "source_ref": item["source_ref"], "position": 40 + index} for index, item in enumerate(charts, 1))
    sections.extend({"id": f"table_{index}", "section_type": "table", "title": item["title"], "source_ref": item["source_ref"], "position": 60 + index} for index, item in enumerate(tables, 1))
    sections.extend([
        {"id": "insights", "section_type": "text", "title": "Evidence-Backed Insights", "content": "\n".join(f"- {item.get('message', item.get('code'))}" for item in insight_items) or "No material driver passed the minimum-volume checks.", "position": 80},
        {"id": "recommendation", "section_type": "text", "title": "Recommendation", "content": recommendation, "position": 90},
        {"id": "caveats", "section_type": "text", "title": "Metric Definitions and Caveats", "content": "Flight grain is one scheduled flight record. On-time arrival means ArrDelay ≤ 15 minutes and excludes cancelled, diverted, or missing-arrival records. Negative delays are retained for averages; total positive delay clips early arrivals to zero. Delay-cause fields are reported for qualifying delayed flights and may not reconcile to total arrival delay. This report does not contain passenger counts or causal proof." + (" " + " ".join(notes) if notes else ""), "position": 100},
    ])
    report = {
        "id": report_id,
        "title": f"{dataset_name} Airline Operations Dashboard",
        "subtitle": "U.S. DOT/BTS on-time performance",
        "description": "Source-backed flight reliability, delay and cancellation analysis.",
        "revision": 1,
        "parameters": {"source_version_id": source_version_id or "", "generated_at": datetime.now(timezone.utc).isoformat(), "dashboard_template_id": dashboard_template["id"], **applied_filters},
        "kpis": kpis,
    }
    content_by_ref = {**kpi_by_ref, **chart_by_ref, **table_by_ref}
    manifest = assemble_report(report, sections, content_by_ref=content_by_ref, parameters=report["parameters"], missing_content_policy="error")

    widgets: list[dict[str, Any]] = []
    for index, item in enumerate(kpis):
        widgets.append({"id": item["source_ref"], "widget_type": "kpi", "title": item["label"], "source_ref": item["source_ref"], "config": {"value": item["formatted_value"], "label": item["label"]}, "x": (index % 3) * 4, "y": (index // 3) * 2, "w": 4, "h": 2})
    for index, item in enumerate(charts):
        widgets.append({"id": item["source_ref"], "widget_type": "chart", "title": item["title"], "source_ref": item["source_ref"], "config": {"chart": item}, "x": (index % 2) * 6, "y": 4 + (index // 2) * 5, "w": 6, "h": 4})
    for index, item in enumerate(tables):
        widgets.append({"id": item["source_ref"], "widget_type": "table", "title": item["title"], "source_ref": item["source_ref"], "config": {"table": item}, "x": 0, "y": 14 + index * 5, "w": 12, "h": 5})
    widgets = apply_dashboard_template(dashboard_template, widgets)
    layout_validation = validate_layout(widgets)
    if not layout_validation["valid"]:
        raise ValueError(f"Generated airline dashboard layout is invalid: {layout_validation['collisions']}")
    dashboard_filters = [{"name": name.casefold(), "column": name, "values": sorted(filter_source[name].dropna().astype(str).unique().tolist())} for name in ("Carrier", "Origin", "Destination", "Route")]
    source = {"type": "dataset_version", "domain": "airline_operations", "name": dataset_name, "version_id": source_version_id, "grain": "one scheduled flight record", "base_row_count": int(len(frame)), "row_count": int(len(model)), "column_count": int(len(model.columns)), "date_range": {"start": start_date.isoformat() if pd.notna(start_date) else None, "end": end_date.isoformat() if pd.notna(end_date) else None}, "filters": applied_filters}
    dashboard = {"id": f"dashboard:{report_id}", "title": report["title"], "description": report["description"], "template": dashboard_template, "available_templates": list_dashboard_templates(), "source": source, "filters": dashboard_filters, "widgets": widgets, "layout_validation": layout_validation}

    html = render_html(manifest)
    pdf_bytes = build_pdf_bytes(manifest, document_title=report["title"], subject=report["description"]) if "pdf" in export_formats else b""
    xlsx_bytes = build_xlsx_bytes(manifest, source_frame=model) if "xlsx" in export_formats else b""
    desktop = {"powerbi": b"", "tableau": b"", "tableau_twb": b"", "validation": {}}
    if export_formats.intersection(DESKTOP_EXPORT_FORMATS):
        desktop = build_bi_desktop_exports(model, report=report, dashboard=dashboard, manifest=manifest, dataset_name=dataset_name, source_version_id=source_version_id)
    files: dict[str, str] = {}
    if output_path:
        files = write_export_files(
            output_path,
            export_formats,
            {"html": html.encode("utf-8"), "pdf": pdf_bytes, "xlsx": xlsx_bytes, "powerbi": desktop["powerbi"], "tableau": desktop["tableau"], "tableau_twb": desktop["tableau_twb"]},
        )
    return {
        "report_id": report_id,
        "report": report,
        "source": source,
        "field_mapping": field_mapping,
        "filters": dashboard_filters,
        "applied_filters": applied_filters,
        "kpis": kpis,
        "charts": charts,
        "tables": tables,
        "findings": insight_items,
        "recommendation": recommendation,
        "dashboard": dashboard,
        "manifest": manifest,
        "html": html,
        "pdf_bytes": pdf_bytes,
        "xlsx_bytes": xlsx_bytes,
        "powerbi_bytes": desktop["powerbi"],
        "tableau_twbx_bytes": desktop["tableau"],
        "tableau_twb_bytes": desktop["tableau_twb"],
        "desktop_export_validation": desktop["validation"],
        "export_formats": sorted(export_formats),
        "files": files,
    }
