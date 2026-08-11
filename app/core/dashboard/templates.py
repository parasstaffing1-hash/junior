"""Selectable, data-driven dashboard templates.

The templates describe presentation only: every metric, chart, and table remains
computed from the selected dataset.  This deliberately keeps the UI useful for
arbitrary business data instead of coupling a dashboard to a demo data model.
"""

from __future__ import annotations

from copy import deepcopy
from math import ceil
from typing import Any


DEFAULT_DASHBOARD_TEMPLATE_ID = "executive"


class DashboardTemplateError(ValueError):
    """Raised when a requested dashboard template is not available."""


_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "id": "executive",
        "name": "Executive overview",
        "category": "Leadership",
        "icon": "fa-chart-line",
        "description": "A focused operating pulse: headline KPIs, one spotlight trend, and supporting drivers.",
        "recommended_for": "Leadership reviews and recurring business updates",
        "layout": "spotlight",
        "chart_order": ("chart:sales_over_time", "chart:sales_by_country", "chart:sales_by_store", "chart:profit_by_product"),
        "theme": {
            "accent": "#2563eb",
            "accent_secondary": "#14b8a6",
            "surface": "#f8fafc",
            "surface_alt": "#eff6ff",
            "card": "#ffffff",
            "ink": "#0f172a",
            "muted": "#64748b",
            "border": "#dbe4ee",
            "positive": "#059669",
            "negative": "#e11d48",
            "palette": ["#2563eb", "#14b8a6", "#8b5cf6", "#f59e0b", "#e11d48", "#0891b2"],
        },
    },
    {
        "id": "sales_performance",
        "name": "Sales performance",
        "category": "Revenue",
        "icon": "fa-arrow-trend-up",
        "description": "A performance-management layout that gives sales trends, market contribution, and profitability equal weight.",
        "recommended_for": "Revenue, territory, store, and product reviews",
        "layout": "performance",
        "chart_order": ("chart:sales_over_time", "chart:sales_by_country", "chart:sales_by_store", "chart:profit_by_product"),
        "theme": {
            "accent": "#0f766e",
            "accent_secondary": "#0ea5e9",
            "surface": "#f8fafc",
            "surface_alt": "#ecfeff",
            "card": "#ffffff",
            "ink": "#102a43",
            "muted": "#627d98",
            "border": "#d9e2ec",
            "positive": "#059669",
            "negative": "#dc2626",
            "palette": ["#0f766e", "#0ea5e9", "#2563eb", "#f97316", "#be123c", "#7c3aed"],
        },
    },
    {
        "id": "ecommerce_conversion",
        "name": "E-commerce conversion",
        "category": "Growth",
        "icon": "fa-cart-shopping",
        "description": "A conversion-oriented composition with a prominent trend, compact supporting comparisons, and detail tables.",
        "recommended_for": "Digital commerce, product funnels, and customer-segment analysis",
        "layout": "commerce",
        "chart_order": ("chart:sales_over_time", "chart:profit_by_product", "chart:sales_by_country", "chart:sales_by_store"),
        "theme": {
            "accent": "#4f46e5",
            "accent_secondary": "#06b6d4",
            "surface": "#f8fafc",
            "surface_alt": "#eef2ff",
            "card": "#ffffff",
            "ink": "#1e1b4b",
            "muted": "#6b7280",
            "border": "#dfe4f0",
            "positive": "#16a34a",
            "negative": "#ef4444",
            "palette": ["#4f46e5", "#06b6d4", "#3b82f6", "#f59e0b", "#ec4899", "#14b8a6"],
        },
    },
    {
        "id": "operations",
        "name": "Operations control",
        "category": "Operations",
        "icon": "fa-boxes-stacked",
        "description": "A dense, comparison-first control room for product, location, inventory, or service operations.",
        "recommended_for": "Operational KPIs, inventory, service delivery, and regional performance",
        "layout": "operations",
        "chart_order": ("chart:profit_by_product", "chart:sales_by_country", "chart:sales_by_store", "chart:sales_over_time"),
        "theme": {
            "accent": "#b45309",
            "accent_secondary": "#0f766e",
            "surface": "#fffbeb",
            "surface_alt": "#fef3c7",
            "card": "#ffffff",
            "ink": "#292524",
            "muted": "#78716c",
            "border": "#e7dfcf",
            "positive": "#15803d",
            "negative": "#dc2626",
            "palette": ["#b45309", "#0f766e", "#2563eb", "#d97706", "#be123c", "#7c3aed"],
        },
    },
    {
        "id": "root_cause",
        "name": "Root-cause explorer",
        "category": "Diagnostic",
        "icon": "fa-diagram-project",
        "description": "A diagnostic layout that puts category drivers and ranked detail beside the headline business outcome.",
        "recommended_for": "Investigations, variance analysis, and category-level drilldowns",
        "layout": "diagnostic",
        "chart_order": ("chart:profit_by_product", "chart:sales_by_country", "chart:sales_by_store", "chart:sales_over_time"),
        "theme": {
            "accent": "#1d4ed8",
            "accent_secondary": "#e11d48",
            "surface": "#f8fafc",
            "surface_alt": "#eff6ff",
            "card": "#ffffff",
            "ink": "#111827",
            "muted": "#6b7280",
            "border": "#d1d5db",
            "positive": "#0284c7",
            "negative": "#e11d48",
            "palette": ["#1d4ed8", "#e11d48", "#0891b2", "#f59e0b", "#7c3aed", "#059669"],
        },
    },
    {
        "id": "powerbi_executive_sales",
        "name": "Power BI executive sales",
        "category": "Power BI reference",
        "icon": "fa-chart-column",
        "description": "A source-backed executive sales composition using the BIBB Power BI palette, compact KPI band, and ranked performance views.",
        "recommended_for": "Superstore sales, revenue leadership, and executive scorecards",
        "layout": "performance",
        "chart_order": ("chart:sales_over_time", "chart:sales_by_country", "chart:sales_by_store", "chart:profit_by_product"),
        "theme": {
            "accent": "#B2182B",
            "accent_secondary": "#4393C3",
            "surface": "#ffffff",
            "surface_alt": "#F4F7FA",
            "card": "#ffffff",
            "ink": "#0A2B43",
            "muted": "#5B7183",
            "border": "#D1E5F0",
            "positive": "#38B64B",
            "negative": "#ee1c25",
            "palette": ["#B2182B", "#D6604D", "#F4A582", "#FDDBC7", "#4393C3", "#125CA5"],
        },
        "theme_asset": "/static/assets/powerbi/bibb_free_default.json",
        "source_reference": {
            "repository": "Dashboard-Design/Power-BI-Design-Files",
            "repository_url": "https://github.com/Dashboard-Design/Power-BI-Design-Files",
            "source_path": "Full Dashboards/Exceutive Sales Report/style.css",
            "source_sha": "09875c7d29a25134292dfee1662673b23a3cd410",
            "theme_path": "Theme .JSON Files/BIBB - Free Default.json",
            "theme_sha": "9f6331034e7d0d1f8242894fa414dc2865d31c08",
            "license": "MIT",
            "implementation": "web-native adaptation",
        },
    },
    {
        "id": "powerbi_ecommerce_conversion",
        "name": "Power BI e-commerce conversion",
        "category": "Power BI reference",
        "icon": "fa-filter-circle-dollar",
        "description": "A source-backed conversion dashboard with the repository's Ecommerce Conversion background, prominent funnel trend, and comparison cards.",
        "recommended_for": "E-commerce funnels, website traffic, and channel conversion",
        "layout": "commerce",
        "chart_order": ("chart:sales_over_time", "chart:profit_by_product", "chart:sales_by_country", "chart:sales_by_store"),
        "theme": {
            "accent": "#0f6385",
            "accent_secondary": "#388fa1",
            "surface": "#ffffff",
            "surface_alt": "#eef7f8",
            "card": "#ffffff",
            "ink": "#173B4A",
            "muted": "#607D86",
            "border": "#D6E7E9",
            "positive": "#2F855A",
            "negative": "#AC3E31",
            "palette": ["#0f6385", "#388fa1", "#88aca2", "#ecd9af", "#d6a379", "#745644"],
        },
        "background_asset": "/static/assets/powerbi/ecommerce_background.svg",
        "theme_asset": "/static/assets/powerbi/teals.json",
        "source_reference": {
            "repository": "Dashboard-Design/Power-BI-Design-Files",
            "repository_url": "https://github.com/Dashboard-Design/Power-BI-Design-Files",
            "source_path": "Full Dashboards/Ecommerce Conversion Dashboard/Images/Background.svg",
            "source_sha": "2aa8cda50c542870ed427264625418c09edf50cd",
            "theme_path": "Theme .JSON Files/kerrykolosko - Teals.json",
            "theme_sha": "97184407089b1d98c93ffea335643cbcc3a0279e",
            "license": "MIT",
            "implementation": "web-native adaptation",
        },
    },
    {
        "id": "powerbi_kpi_slicer",
        "name": "Power BI KPI slicer",
        "category": "Power BI reference",
        "icon": "fa-sliders",
        "description": "A KPI-first layout inspired by the repository's native SVG Button Slicer cards, with metric emphasis and quick comparison context.",
        "recommended_for": "Inventory, operating scorecards, and interactive KPI reviews",
        "layout": "spotlight",
        "chart_order": ("chart:sales_over_time", "chart:sales_by_store", "chart:sales_by_country", "chart:profit_by_product"),
        "theme": {
            "accent": "#0f6385",
            "accent_secondary": "#AC3E31",
            "surface": "#ffffff",
            "surface_alt": "#f2f7f8",
            "card": "#ffffff",
            "ink": "#173B4A",
            "muted": "#607D86",
            "border": "#D6E7E9",
            "positive": "#2F855A",
            "negative": "#AC3E31",
            "palette": ["#0f6385", "#388fa1", "#88aca2", "#ecd9af", "#d6a379", "#745644"],
        },
        "theme_asset": "/static/assets/powerbi/teals.json",
        "source_reference": {
            "repository": "Dashboard-Design/Power-BI-Design-Files",
            "repository_url": "https://github.com/Dashboard-Design/Power-BI-Design-Files",
            "source_path": "Native Visuals/Button Slicer/Button Slicer - 5 Clickable KPI Cards/README.md",
            "source_sha": "569cf96af2959ea9be0f67e9daaf56892fbb5a59",
            "theme_path": "Theme .JSON Files/kerrykolosko - Teals.json",
            "theme_sha": "97184407089b1d98c93ffea335643cbcc3a0279e",
            "license": "MIT",
            "implementation": "web-native adaptation",
        },
    },
)

_TEMPLATE_BY_ID = {template["id"]: template for template in _TEMPLATES}


def list_dashboard_templates() -> list[dict[str, Any]]:
    """Return template metadata safe to expose through the API."""
    return deepcopy(list(_TEMPLATES))


def get_dashboard_template(template_id: str | None = None) -> dict[str, Any]:
    """Resolve a template id or the default template.

    IDs are deliberately strict and stable because they are persisted in URLs and
    browser preferences.  Empty input selects the default.
    """
    resolved_id = DEFAULT_DASHBOARD_TEMPLATE_ID if template_id is None or not str(template_id).strip() else str(template_id).strip().casefold()
    template = _TEMPLATE_BY_ID.get(resolved_id)
    if template is None:
        raise DashboardTemplateError(f"Unknown dashboard template: {template_id}")
    return deepcopy(template)


def _widget(widget: dict[str, Any], *, x: int, y: int, w: int, h: int, rank: int, template_id: str, variant: str) -> dict[str, Any]:
    out = deepcopy(widget)
    out.update({"x": x, "y": y, "w": w, "h": h, "position": rank})
    out["presentation"] = {"template_id": template_id, "variant": variant}
    return out


def _order_charts(charts: list[dict[str, Any]], preferred_refs: tuple[str, ...]) -> list[dict[str, Any]]:
    preference = {source_ref: index for index, source_ref in enumerate(preferred_refs)}
    return sorted(charts, key=lambda chart: (preference.get(chart.get("source_ref"), len(preference)), chart.get("source_ref", "")))


def _place_kpis(kpis: list[dict[str, Any]], *, template_id: str, rank: int = 0) -> tuple[list[dict[str, Any]], int, int]:
    placed: list[dict[str, Any]] = []
    for index, item in enumerate(kpis):
        placed.append(_widget(item, x=(index % 4) * 3, y=(index // 4) * 2, w=3, h=2, rank=rank + index, template_id=template_id, variant="kpi"))
    next_y = max(3, ceil(len(kpis) / 4) * 2) if kpis else 0
    return placed, next_y, rank + len(placed)


def _place_spotlight(charts: list[dict[str, Any]], *, y: int, template_id: str, rank: int) -> tuple[list[dict[str, Any]], int, int]:
    placed: list[dict[str, Any]] = []
    if not charts:
        return placed, y, rank
    placed.append(_widget(charts[0], x=0, y=y, w=8, h=5, rank=rank, template_id=template_id, variant="chart-primary"))
    rank += 1
    if len(charts) > 1:
        placed.append(_widget(charts[1], x=8, y=y, w=4, h=5, rank=rank, template_id=template_id, variant="chart-side"))
        rank += 1
    y += 5
    for index in range(2, len(charts), 2):
        chunk = charts[index:index + 2]
        width = 12 if len(chunk) == 1 else 6
        for column, item in enumerate(chunk):
            placed.append(_widget(item, x=column * width, y=y, w=width, h=4, rank=rank, template_id=template_id, variant="chart-support"))
            rank += 1
        y += 4
    return placed, y, rank


def _place_wide_first(charts: list[dict[str, Any]], *, y: int, template_id: str, rank: int) -> tuple[list[dict[str, Any]], int, int]:
    placed: list[dict[str, Any]] = []
    if not charts:
        return placed, y, rank
    placed.append(_widget(charts[0], x=0, y=y, w=12, h=4, rank=rank, template_id=template_id, variant="chart-primary"))
    rank += 1
    y += 4
    for index in range(1, len(charts), 2):
        chunk = charts[index:index + 2]
        width = 12 if len(chunk) == 1 else 6
        for column, item in enumerate(chunk):
            placed.append(_widget(item, x=column * width, y=y, w=width, h=4, rank=rank, template_id=template_id, variant="chart-support"))
            rank += 1
        y += 4
    return placed, y, rank


def _place_three_column(charts: list[dict[str, Any]], *, y: int, template_id: str, rank: int) -> tuple[list[dict[str, Any]], int, int]:
    placed: list[dict[str, Any]] = []
    for index in range(0, len(charts), 3):
        chunk = charts[index:index + 3]
        width = 12 // len(chunk)
        for column, item in enumerate(chunk):
            placed.append(_widget(item, x=column * width, y=y, w=width, h=4, rank=rank, template_id=template_id, variant="chart-driver"))
            rank += 1
        y += 4
    return placed, y, rank


def _place_tables(tables: list[dict[str, Any]], *, y: int, template_id: str, rank: int) -> list[dict[str, Any]]:
    placed: list[dict[str, Any]] = []
    for item in tables:
        rows = ((item.get("config") or {}).get("table") or {}).get("rows") or []
        height = max(4, min(6, 2 + ceil(len(rows) / 3)))
        placed.append(_widget(item, x=0, y=y, w=12, h=height, rank=rank, template_id=template_id, variant="table"))
        y += height
        rank += 1
    return placed


def apply_dashboard_template(template: dict[str, Any], widgets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply an intentional, collision-free grid layout to dashboard widgets."""
    template_id = template["id"]
    kpis = [item for item in widgets if item.get("widget_type") == "kpi"]
    charts = _order_charts([item for item in widgets if item.get("widget_type") == "chart"], tuple(template.get("chart_order") or ()))
    tables = [item for item in widgets if item.get("widget_type") == "table"]

    placed, next_y, rank = _place_kpis(kpis, template_id=template_id)
    layout = template.get("layout")
    if layout in {"spotlight", "commerce"}:
        chart_widgets, next_y, rank = _place_spotlight(charts, y=next_y, template_id=template_id, rank=rank)
    elif layout == "diagnostic":
        chart_widgets, next_y, rank = _place_three_column(charts, y=next_y, template_id=template_id, rank=rank)
    else:
        chart_widgets, next_y, rank = _place_wide_first(charts, y=next_y, template_id=template_id, rank=rank)
    placed.extend(chart_widgets)
    placed.extend(_place_tables(tables, y=next_y, template_id=template_id, rank=rank))
    return placed
