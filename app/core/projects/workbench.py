from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.core.dashboard.layout import validate_layout
from app.core.dashboard.templates import (
    DashboardTemplateError,
    apply_dashboard_template,
    get_dashboard_template,
    list_dashboard_templates,
)
from app.core.eda.findings import detect_findings
from app.core.reporting.bi_exports import build_bi_desktop_exports
from app.core.reporting.pdf_generator import build_pdf_bytes
from app.core.reporting.report_builder import assemble_report, render_html
from app.core.reporting.xlsx_generator import build_xlsx_bytes
from app.core.visualization.bar_chart import build_bar_chart
from app.core.visualization.line_chart import build_line_chart

from .catalog import ProjectSpec, get_project_spec


class ProjectBuildError(ValueError):
    """A user-facing, structured project build failure."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        self.message = message
        self.details = details or {}
        super().__init__(message)


def _normalize(value: Any) -> str:
    return "".join(char for char in str(value).casefold() if char.isalnum())


def _jsonable(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        return _jsonable(value.item())
    if isinstance(value, float) and np.isnan(value):
        return None
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _format(value: Any, *, percent: bool = False, currency: bool = False) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "—"
    number = float(value)
    if percent:
        return f"{number:,.1f}%"
    prefix = "$" if currency and number >= 0 else "-$" if currency else ""
    magnitude = abs(number)
    if currency and magnitude >= 1_000_000:
        return f"{prefix}{magnitude / 1_000_000:,.1f}M"
    if currency and magnitude >= 1_000:
        return f"{prefix}{magnitude / 1_000:,.1f}k"
    return f"{prefix}{magnitude:,.2f}" if not number.is_integer() else f"{prefix}{magnitude:,.0f}"


def _resolve_fields(frame: pd.DataFrame, spec: ProjectSpec) -> dict[str, str]:
    available = {_normalize(column): str(column) for column in frame.columns}
    resolved: dict[str, str] = {}
    for logical, candidates in spec.fields.items():
        for candidate in candidates:
            actual = available.get(_normalize(candidate))
            if actual is not None:
                resolved[logical] = actual
                break
    return resolved


def _numeric(frame: pd.DataFrame, column: str | None) -> pd.Series:
    if not column or column not in frame.columns:
        return pd.Series(dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _ensure_numeric(frame: pd.DataFrame, column: str) -> None:
    frame[column] = pd.to_numeric(frame[column], errors="coerce")


def _prepare_frame(spec: ProjectSpec, source: pd.DataFrame, fields: dict[str, str]) -> tuple[pd.DataFrame, dict[str, str]]:
    frame = source.copy(deep=True)
    fields = dict(fields)
    key = spec.id

    if key == "customer_rfm_segmentation":
        customer, date, amount = fields.get("customer_id"), fields.get("date"), fields.get("amount")
        if not customer or not date or not amount:
            raise ProjectBuildError("RFM needs customer, date, and amount fields.")
        dates = pd.to_datetime(frame[date], errors="coerce")
        frame = frame.assign(_event_date=dates)
        as_of = dates.max()
        if pd.isna(as_of):
            as_of = pd.Timestamp.utcnow().tz_localize(None)
        grouped = frame.groupby(customer, dropna=False).agg(
            recency_days=("_event_date", lambda values: int((as_of - values.max()).days) if values.notna().any() else 0),
            frequency=(fields.get("order_id") or amount, "nunique" if fields.get("order_id") else "count"),
            monetary_value=(amount, "sum"),
        ).reset_index()
        grouped = grouped.rename(columns={customer: "customer_id"})
        grouped["customer_count"] = 1
        ranks = grouped["monetary_value"].rank(method="first")
        grouped["rfm_segment"] = pd.qcut(ranks, q=min(4, len(grouped)), labels=["Bronze", "Silver", "Gold", "Platinum"][: min(4, len(grouped))], duplicates="drop").astype(str)
        frame = grouped
        fields = {"customer_id": "customer_id", "rfm_segment": "rfm_segment", "monetary_value": "monetary_value", "customer_count": "customer_count", "recency_days": "recency_days", "frequency": "frequency"}

    elif key in {"ecommerce_funnel", "website_traffic_dashboard"}:
        if key == "ecommerce_funnel":
            numerator, denominator = fields.get("purchases"), fields.get("visitors")
        else:
            numerator, denominator = fields.get("conversions"), fields.get("sessions")
        if numerator and denominator:
            frame["conversion_rate"] = (_numeric(frame, numerator) / _numeric(frame, denominator).replace(0, np.nan) * 100).fillna(0)
            fields["conversion_rate"] = "conversion_rate"

    elif key == "ab_test_analysis":
        converted = fields.get("converted")
        if converted:
            _ensure_numeric(frame, converted)

    elif key == "loan_default_risk":
        score = fields.get("credit_score")
        if score:
            values = _numeric(frame, score)
            frame["credit_band"] = pd.cut(values, [-np.inf, 579, 669, 739, 799, np.inf], labels=["Poor", "Fair", "Good", "Very good", "Excellent"])
            fields["credit_band"] = "credit_band"

    elif key == "sales_forecasting":
        date, sales = fields.get("date"), fields.get("sales")
        if date and sales:
            frame[date] = pd.to_datetime(frame[date], errors="coerce")
            _ensure_numeric(frame, sales)
            frame["forecast_baseline"] = _numeric(frame, sales).rolling(3, min_periods=1).mean()
            fields["forecast_baseline"] = "forecast_baseline"

    elif key == "customer_churn_prediction":
        tenure = fields.get("tenure")
        if tenure:
            frame["tenure_band"] = pd.cut(_numeric(frame, tenure), [-np.inf, 6, 12, 24, 48, np.inf], labels=["0–6m", "7–12m", "13–24m", "25–48m", "49m+"])
            fields["tenure_band"] = "tenure_band"

    elif key == "supply_chain_inventory":
        for logical in ("opening_stock", "purchases", "units_sold", "closing_stock", "unit_cost", "reorder_level"):
            if fields.get(logical):
                _ensure_numeric(frame, fields[logical])
        closing, cost, sold, opening, reorder = (fields.get(name) for name in ("closing_stock", "unit_cost", "units_sold", "opening_stock", "reorder_level"))
        if closing and cost:
            frame["inventory_value"] = _numeric(frame, closing) * _numeric(frame, cost)
            fields["inventory_value"] = "inventory_value"
            ranked = frame["inventory_value"].fillna(0).sort_values(ascending=False)
            cumulative_share = ranked.cumsum() / max(float(ranked.sum()), 1.0)
            classes = pd.Series("C", index=frame.index)
            classes.loc[cumulative_share.index[cumulative_share <= 0.80]] = "A"
            classes.loc[cumulative_share.index[(cumulative_share > 0.80) & (cumulative_share <= 0.95)]] = "B"
            frame["abc_class"] = classes
            fields["abc_class"] = "abc_class"
        if closing and reorder:
            frame["stockout_risk"] = (_numeric(frame, closing) <= _numeric(frame, reorder)).astype(int) * 100
            fields["stockout_risk"] = "stockout_risk"
        if sold and opening:
            frame["sell_through_rate"] = (_numeric(frame, sold) / (_numeric(frame, opening) + _numeric(frame, sold)).replace(0, np.nan) * 100).fillna(0)
            fields["sell_through_rate"] = "sell_through_rate"

    elif key == "stock_market_volatility":
        date, ticker, close = fields.get("date"), fields.get("ticker"), fields.get("close")
        if date and ticker and close:
            frame[date] = pd.to_datetime(frame[date], errors="coerce")
            _ensure_numeric(frame, close)
            frame["_return"] = frame.groupby(ticker, dropna=False)[close].pct_change().fillna(0)
            frame["volatility"] = frame.groupby(ticker, dropna=False)["_return"].transform(lambda values: values.std(ddof=0) * np.sqrt(252) * 100).fillna(0)
            fields["volatility"] = "volatility"

    elif key == "healthcare_outcomes":
        age = fields.get("age")
        if age:
            frame["age_band"] = pd.cut(_numeric(frame, age), [-np.inf, 29, 44, 59, 74, np.inf], labels=["18–29", "30–44", "45–59", "60–74", "75+"])
            fields["age_band"] = "age_band"

    elif key == "end_to_end_capstone":
        revenue, spend = fields.get("revenue"), fields.get("marketing_spend")
        if revenue and spend:
            frame["marketing_roi"] = (_numeric(frame, revenue) - _numeric(frame, spend)) / _numeric(frame, spend).replace(0, np.nan)
            fields["marketing_roi"] = "marketing_roi"

    for column in frame.columns:
        if pd.api.types.is_numeric_dtype(frame[column]):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame, fields


def _fallback_chart(frame: pd.DataFrame, dimension: str, measure: str, title: str, kind: str) -> dict[str, Any]:
    work = frame[[dimension, measure]].copy().dropna(subset=[dimension, measure])
    if work.empty:
        raise ValueError("No data remains for this chart.")
    if kind == "line":
        work[dimension] = pd.to_datetime(work[dimension], errors="coerce").fillna(work[dimension].astype(str))
    grouped = work.groupby(dimension, dropna=False)[measure].sum().reset_index(name="value").head(24)
    return {
        "chart_type": kind,
        "category_column": dimension if kind == "bar" else None,
        "x_column": dimension if kind == "line" else None,
        "y_column": measure,
        "aggregation": "sum",
        "title": title,
        "data": _jsonable(grouped.to_dict(orient="records")),
    }


def _build_charts(spec: ProjectSpec, frame: pd.DataFrame, fields: dict[str, str], output_path: Path | None) -> list[dict[str, Any]]:
    charts: list[dict[str, Any]] = []
    for index, chart_spec in enumerate(spec.charts, start=1):
        dimension = fields.get(chart_spec["dimension"])
        measure = fields.get(chart_spec["measure"])
        if not dimension or not measure or dimension not in frame.columns or measure not in frame.columns:
            continue
        _ensure_numeric(frame, measure)
        title = chart_spec.get("title", f"{chart_spec['measure']} by {chart_spec['dimension']}")
        chart_path = str(output_path / f"chart_{index}.png") if output_path else None
        try:
            if chart_spec["kind"] == "line":
                chart = build_line_chart(frame, x_column=dimension, y_column=measure, aggregation="sum", parse_datetime=True, frequency="MS", fill_missing_intervals=True, title=title, output_path=chart_path)
            else:
                chart = build_bar_chart(frame, category_column=dimension, value_column=measure, aggregation="mean" if chart_spec["measure"] in {"rating", "temperature", "humidity", "bounce_rate", "nps", "volatility"} else "sum", top_n=12, title=title, output_path=chart_path)
            if chart_path:
                chart["artifact_path"] = chart_path
        except Exception:
            try:
                chart = _fallback_chart(frame, dimension, measure, title, chart_spec["kind"])
            except Exception:
                continue
        chart["source_ref"] = f"chart:project:{index}"
        charts.append(_jsonable(chart))
    if len(charts) < 2:
        numeric_columns = [str(column) for column in frame.columns if pd.api.types.is_numeric_dtype(frame[column])]
        dimension = next((value for value in fields.values() if value in frame.columns and not pd.api.types.is_numeric_dtype(frame[value])), None)
        if dimension and numeric_columns:
            try:
                fallback = _fallback_chart(frame, dimension, numeric_columns[0], "Primary driver", "bar")
                fallback["source_ref"] = "chart:project:fallback"
                charts.append(_jsonable(fallback))
            except Exception:
                pass
    return charts


def _measure_column(fields: dict[str, str], frame: pd.DataFrame) -> tuple[str, str]:
    preferences = (
        ("revenue", "Revenue"), ("sales", "Sales"), ("profit", "Profit"), ("amount", "Amount"),
        ("monetary_value", "Monetary value"), ("cases", "Cases"), ("purchases", "Purchases"),
        ("conversions", "Conversions"), ("sessions", "Sessions"), ("visitors", "Visitors"),
        ("loan_amount", "Loan amount"), ("monthly_income", "Monthly income"), ("close", "Close"),
        ("rating", "Rating"), ("temperature", "Temperature"), ("glucose", "Glucose"),
        ("bmi", "BMI"), ("inventory_value", "Inventory value"), ("nps", "NPS"),
    )
    for logical, label in preferences:
        actual = fields.get(logical)
        if actual and actual in frame.columns and _numeric(frame, actual).notna().any():
            return actual, label
    numeric = [str(column) for column in frame.columns if pd.api.types.is_numeric_dtype(frame[column])]
    if numeric:
        return numeric[0], numeric[0].replace("_", " ").title()
    raise ProjectBuildError("The project requires at least one numeric measure.")


def _kpis(spec: ProjectSpec, frame: pd.DataFrame, fields: dict[str, str]) -> list[dict[str, Any]]:
    measure, label = _measure_column(fields, frame)
    values = _numeric(frame, measure).dropna()
    kpis: list[dict[str, Any]] = [
        {"label": "Rows analyzed", "value": int(len(frame)), "formatted_value": _format(len(frame)), "source_ref": "kpi:project:rows"},
        {"label": f"Total {label.lower()}", "value": float(values.sum()), "formatted_value": _format(values.sum(), currency=label in {"Sales", "Revenue", "Profit", "Amount", "Monetary value", "Loan amount", "Monthly income"}), "source_ref": "kpi:project:total"},
        {"label": f"Average {label.lower()}", "value": float(values.mean()), "formatted_value": _format(values.mean(), currency=label in {"Sales", "Revenue", "Profit", "Amount", "Monetary value", "Loan amount", "Monthly income"}), "source_ref": "kpi:project:average"},
    ]
    if spec.id in {"ecommerce_funnel", "website_traffic_dashboard"} and fields.get("conversion_rate"):
        rate = _numeric(frame, fields["conversion_rate"]).mean()
        kpis.append({"label": "Conversion rate", "value": float(rate), "formatted_value": _format(rate, percent=True), "source_ref": "kpi:project:conversion"})
    if spec.id == "ab_test_analysis" and fields.get("experiment_group") and fields.get("converted"):
        groups = frame.groupby(fields["experiment_group"], dropna=False)[fields["converted"]].mean() * 100
        if len(groups) >= 2:
            control, treatment = float(groups.iloc[0]), float(groups.iloc[1])
            kpis.append({"label": "A/B lift", "value": treatment - control, "formatted_value": _format(treatment - control, percent=True), "source_ref": "kpi:project:lift"})
    if spec.id == "customer_rfm_segmentation" and fields.get("rfm_segment"):
        kpis.append({"label": "Customer segments", "value": int(frame[fields["rfm_segment"]].nunique()), "formatted_value": _format(frame[fields["rfm_segment"]].nunique()), "source_ref": "kpi:project:segments"})
    return [_jsonable(kpi) for kpi in kpis]


def _tables(spec: ProjectSpec, frame: pd.DataFrame, fields: dict[str, str], charts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for index, chart in enumerate(charts[:2], start=1):
        dimension = chart.get("category_column") or chart.get("x_column")
        measure = chart.get("value_column") or chart.get("y_column")
        if not dimension or not measure or dimension not in frame.columns or measure not in frame.columns:
            continue
        work = frame[[dimension, measure]].copy()
        work[measure] = pd.to_numeric(work[measure], errors="coerce")
        grouped = work.dropna(subset=[dimension, measure]).groupby(dimension, dropna=False)[measure].agg(["count", "sum", "mean"]).reset_index()
        grouped = grouped.sort_values("sum", ascending=False).head(12)
        rows = [{str(dimension): _jsonable(row[dimension]), "count": int(row["count"]), "sum": float(row["sum"]), "mean": float(row["mean"])} for _, row in grouped.iterrows()]
        tables.append({"source_ref": f"table:project:{index}", "title": f"Detail by {dimension}", "columns": [str(dimension), "count", "sum", "mean"], "rows": rows})
    if not tables:
        measure, _ = _measure_column(fields, frame)
        rows = [{str(measure): _jsonable(value)} for value in _numeric(frame, measure).dropna().head(20)]
        tables.append({"source_ref": "table:project:sample", "title": "Sample measure values", "columns": [str(measure)], "rows": rows})
    return _jsonable(tables)


def _summary(spec: ProjectSpec, frame: pd.DataFrame, kpis: list[dict[str, Any]]) -> str:
    return (
        f"{spec.name} analyzed {len(frame):,} rows across {len(frame.columns):,} fields. "
        f"The reusable {spec.template_id} BI template presents source-backed KPIs, drivers, and detail tables. "
        f"{kpis[1]['label']} is {kpis[1]['formatted_value']} with a transparent synthetic validation fixture or uploaded source."
    )


def build_project(
    source: pd.DataFrame,
    *,
    project_id: str,
    template_id: str | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Build one reference analytics project with a source-backed BI report.

    The same function is used by the API, validation suite, and portfolio build
    script. It intentionally keeps model outputs interpretable and records the
    chosen dashboard template in the report manifest.
    """
    try:
        spec = get_project_spec(project_id)
    except KeyError as exc:
        raise ProjectBuildError(str(exc)) from exc
    if not isinstance(source, pd.DataFrame) or source.empty:
        raise ProjectBuildError("A non-empty pandas DataFrame is required.")
    try:
        template = get_dashboard_template(template_id or spec.template_id)
    except DashboardTemplateError as exc:
        raise ProjectBuildError(f"Unknown dashboard template: {template_id or spec.template_id}") from exc

    resolved = _resolve_fields(source, spec)
    prepared, resolved = _prepare_frame(spec, source, resolved)
    output_path = None
    if output_dir:
        output_path = Path(output_dir).expanduser().resolve()
        output_path.mkdir(parents=True, exist_ok=True)

    kpis = _kpis(spec, prepared, resolved)
    charts = _build_charts(spec, prepared, resolved, output_path)
    tables = _tables(spec, prepared, resolved, charts)
    try:
        findings = _jsonable(detect_findings(prepared).get("findings", []))
    except Exception:
        findings = []
    report_id = f"project:{spec.id}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    report = {
        "id": report_id,
        "title": spec.name,
        "subtitle": f"{spec.category} portfolio project",
        "description": spec.description,
        "revision": 1,
        "parameters": {
            "project_id": spec.id,
            "template_id": template["id"],
            "source_rows": int(len(source)),
            "prepared_rows": int(len(prepared)),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    content_by_ref: dict[str, Any] = {kpi["source_ref"]: kpi for kpi in kpis}
    content_by_ref.update({chart["source_ref"]: chart for chart in charts})
    content_by_ref.update({table["source_ref"]: table for table in tables})
    sections: list[dict[str, Any]] = [
        {"id": "summary", "section_type": "text", "title": "Executive summary", "content": _summary(spec, prepared, kpis), "position": 10},
        {"id": "metrics", "section_type": "heading", "title": "Headline metrics", "content": "Headline metrics", "position": 20},
    ]
    for index, kpi in enumerate(kpis, start=1):
        sections.append({"id": f"kpi_{index}", "section_type": "kpi", "title": kpi["label"], "source_ref": kpi["source_ref"], "position": 30 + index})
    sections.append({"id": "drivers", "section_type": "heading", "title": "Drivers and trends", "content": "Drivers and trends", "position": 50})
    for index, chart in enumerate(charts, start=1):
        sections.append({"id": f"chart_{index}", "section_type": "chart", "title": chart.get("title", "Chart"), "source_ref": chart["source_ref"], "position": 60 + index})
    for index, table in enumerate(tables, start=1):
        sections.append({"id": f"table_{index}", "section_type": "table", "title": table.get("title", f"Detail {index}"), "source_ref": table["source_ref"], "position": 80 + index})
    sections.extend([
        {"id": "findings", "section_type": "text", "title": "Key findings", "content": "; ".join(str(item.get("message", item.get("code", ""))) for item in findings[:8]) or "No deterministic findings were detected.", "position": 95},
        {"id": "caveats", "section_type": "text", "title": "Caveats and assumptions", "content": "Fixtures are synthetic validation data. Uploaded datasets remain the source of truth; field aliases are recorded in the build metadata.", "position": 110},
    ])
    manifest = assemble_report(report, sections, content_by_ref=content_by_ref, parameters=report["parameters"], missing_content_policy="error")

    widgets: list[dict[str, Any]] = []
    for index, kpi in enumerate(kpis):
        widgets.append({"id": kpi["source_ref"], "widget_type": "kpi", "title": kpi["label"], "source_ref": kpi["source_ref"], "config": {"value": kpi["formatted_value"], "label": kpi["label"]}, "x": (index % 4) * 3, "y": (index // 4) * 2, "w": 3, "h": 2})
    for index, chart in enumerate(charts):
        widgets.append({"id": chart["source_ref"], "widget_type": "chart", "title": chart.get("title", "Chart"), "source_ref": chart["source_ref"], "config": {"chart": chart}, "x": 0, "y": 0, "w": 6, "h": 4})
    for index, table in enumerate(tables):
        widgets.append({"id": table["source_ref"], "widget_type": "table", "title": table["title"], "source_ref": table["source_ref"], "config": {"table": table}, "x": 0, "y": 0, "w": 12, "h": 5})
    widgets = apply_dashboard_template(template, widgets)
    layout_validation = validate_layout(widgets)
    if not layout_validation["valid"]:
        raise ProjectBuildError("Dashboard template produced an invalid layout.", {"collisions": layout_validation["collisions"]})

    dashboard = {
        "id": f"dashboard:{report_id}",
        "title": spec.name,
        "template": template,
        "available_templates": list_dashboard_templates(),
        "widgets": widgets,
        "layout_validation": layout_validation,
        "source": {"type": "project_fixture_or_dataset", "project_id": spec.id, "row_count": int(len(prepared)), "column_count": int(len(prepared.columns))},
        "field_mapping": resolved,
    }
    html_report = render_html(manifest)
    pdf_bytes = build_pdf_bytes(manifest, document_title=spec.name, subject=spec.description)
    xlsx_bytes = build_xlsx_bytes(manifest)
    desktop_exports = build_bi_desktop_exports(
        prepared,
        report={**report, "kpis": kpis},
        dashboard=dashboard,
        manifest=manifest,
        dataset_name=spec.name,
        source_version_id=None,
    )
    powerbi_bytes = desktop_exports["powerbi"]
    tableau_twbx_bytes = desktop_exports["tableau"]
    tableau_twb_bytes = desktop_exports["tableau_twb"]
    files: dict[str, str] = {}
    if output_path:
        html_file = output_path / "report.html"
        pdf_file = output_path / "report.pdf"
        xlsx_file = output_path / "report.xlsx"
        powerbi_file = output_path / "report.pbip.zip"
        tableau_file = output_path / "report.twbx"
        tableau_twb_file = output_path / "report.twb"
        html_file.write_text(html_report, encoding="utf-8")
        pdf_file.write_bytes(pdf_bytes)
        xlsx_file.write_bytes(xlsx_bytes)
        powerbi_file.write_bytes(powerbi_bytes)
        tableau_file.write_bytes(tableau_twbx_bytes)
        tableau_twb_file.write_bytes(tableau_twb_bytes)
        files = {
            "html": str(html_file),
            "pdf": str(pdf_file),
            "xlsx": str(xlsx_file),
            "powerbi": str(powerbi_file),
            "tableau": str(tableau_file),
            "tableau_twb": str(tableau_twb_file),
        }

    validation = {
        "valid": bool(layout_validation["valid"] and len(kpis) >= 2 and len(charts) >= 2 and len(tables) >= 1 and len(pdf_bytes) > 100 and len(xlsx_bytes) > 100 and len(powerbi_bytes) > 100 and len(tableau_twbx_bytes) > 100 and len(tableau_twb_bytes) > 100),
        "layout_valid": bool(layout_validation["valid"]),
        "kpi_count": len(kpis),
        "chart_count": len(charts),
        "table_count": len(tables),
        "artifact_sizes": {
            "html_bytes": len(html_report.encode("utf-8")),
            "pdf_bytes": len(pdf_bytes),
            "xlsx_bytes": len(xlsx_bytes),
            "powerbi_bytes": len(powerbi_bytes),
            "tableau_bytes": len(tableau_twbx_bytes),
            "tableau_twb_bytes": len(tableau_twb_bytes),
        },
    }
    if not validation["valid"]:
        raise ProjectBuildError("Project build validation failed.", validation)
    return {
        "project": {"id": spec.id, "name": spec.name, "category": spec.category, "difficulty": spec.difficulty, "template_id": template["id"]},
        "status": "COMPLETED",
        "report": report,
        "analytics": {"source_rows": int(len(source)), "prepared_rows": int(len(prepared)), "columns": [str(column) for column in prepared.columns], "field_mapping": resolved},
        "kpis": kpis,
        "charts": charts,
        "tables": tables,
        "findings": findings,
        "dashboard": dashboard,
        "manifest": manifest,
        "html": html_report,
        "pdf_bytes": pdf_bytes,
        "xlsx_bytes": xlsx_bytes,
        "powerbi_bytes": powerbi_bytes,
        "tableau_twbx_bytes": tableau_twbx_bytes,
        "tableau_twb_bytes": tableau_twb_bytes,
        "files": files,
        "validation": validation,
    }
