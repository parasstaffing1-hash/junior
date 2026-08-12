"""Turn a dataset or pasted table into share-ready infographic panels.

The caller may name the panel and columns explicitly, or ask the service to choose:
auto-selection reads the frame's shape and picks the form by the job the data can
do — a date and a measure become a trend, a category and a measure become a
ranking, and a frame with neither becomes a headline stat.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pandas as pd
from pandas.api.types import is_numeric_dtype

from app.core.geographic import GeographicError, build_geographic_map

from .panels import PANEL_TYPE_IDS, PANEL_TYPES, fold_tail_into_other, render_panel
from .themes import (
    MAX_BREAKDOWN_SEGMENTS,
    InfographicThemeError,
    get_format,
    get_theme,
    list_formats,
    list_themes,
)

MAX_RANKED_ITEMS = 10
MIN_TREND_POINTS = 4


class InfographicError(ValueError):
    """A user-facing infographic build failure."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


def catalog() -> dict[str, Any]:
    return {
        "panel_types": list(PANEL_TYPES),
        "themes": list_themes(),
        "formats": list_formats(),
        "max_breakdown_segments": MAX_BREAKDOWN_SEGMENTS,
        "max_ranked_items": MAX_RANKED_ITEMS,
        "templates": [
            {"id": "india_map_story", "name": "India Map Story", "panel_type": "india_map_story", "recommended_theme": "india_pixels", "recommended_formats": ["square", "landscape", "portrait"]},
            {"id": "world_map_story", "name": "Global Country Map Story", "panel_type": "world_map_story", "recommended_theme": "midnight", "recommended_formats": ["square", "landscape", "portrait"]},
        ],
    }


def parse_pasted_table(text: str) -> pd.DataFrame:
    """Read pasted CSV or tab-separated rows the way a user actually pastes them."""
    content = (text or "").strip()
    if not content:
        raise InfographicError("EMPTY_DATA", "Paste at least a header row and one data row.")
    try:
        dialect = csv.Sniffer().sniff(content[:2048], delimiters=",\t;|")
        separator = dialect.delimiter
    except csv.Error:
        separator = "\t" if "\t" in content.splitlines()[0] else ","
    try:
        frame = pd.read_csv(io.StringIO(content), sep=separator)
    except Exception as exc:
        raise InfographicError("UNREADABLE_DATA", f"The pasted data could not be parsed: {exc}") from exc
    frame.columns = [str(column).strip() for column in frame.columns]
    if frame.empty or not len(frame.columns):
        raise InfographicError("EMPTY_DATA", "The pasted data contained no usable rows.")
    return frame


def _numeric_columns(frame: pd.DataFrame) -> list[str]:
    return [str(column) for column in frame.columns if is_numeric_dtype(frame[column])]


def _categorical_columns(frame: pd.DataFrame) -> list[str]:
    columns = []
    for column in frame.columns:
        if is_numeric_dtype(frame[column]):
            continue
        unique = frame[column].nunique(dropna=True)
        if 2 <= unique <= 50:
            columns.append(str(column))
    return columns


def _date_column(frame: pd.DataFrame) -> str | None:
    for column in frame.columns:
        if is_numeric_dtype(frame[column]):
            continue
        parsed = pd.to_datetime(frame[column], errors="coerce")
        if parsed.notna().mean() >= 0.8 and parsed.nunique() >= MIN_TREND_POINTS:
            return str(column)
    return None


def recommend_panel(frame: pd.DataFrame) -> str:
    """Pick the form by the job the data can do, before any styling is chosen."""
    numeric = _numeric_columns(frame)
    if not numeric:
        return "stat_headline"
    if _date_column(frame) is not None:
        return "trend_line"
    categorical = _categorical_columns(frame)
    if categorical:
        distinct = frame[categorical[0]].nunique(dropna=True)
        if distinct == 2:
            return "comparison"
        if distinct <= MAX_BREAKDOWN_SEGMENTS:
            return "share_breakdown"
        return "ranked_bars"
    return "stat_headline"


def _aggregate(frame: pd.DataFrame, category: str, measure: str, how: str) -> pd.Series:
    work = frame[[category, measure]].dropna()
    work[measure] = pd.to_numeric(work[measure], errors="coerce")
    work = work.dropna()
    if work.empty:
        raise InfographicError(
            "NO_USABLE_ROWS",
            f"No rows had both a '{category}' value and a numeric '{measure}' value.",
        )
    grouped = getattr(work.groupby(category, dropna=False)[measure], how)()
    return grouped.sort_values(ascending=False)


def build_spec(
    frame: pd.DataFrame,
    *,
    panel_type: str | None = None,
    title: str | None = None,
    subtitle: str | None = None,
    category_column: str | None = None,
    measure_column: str | None = None,
    date_column: str | None = None,
    aggregation: str = "sum",
    source: str | None = None,
    handle: str | None = None,
    prefix: str = "",
    suffix: str = "",
    boundary_geojson: dict[str, Any] | None = None,
    geography_column: str | None = None,
    boundary_property: str | None = None,
    country: str | None = None,
    admin_level: int | None = None,
    map_palette: str | None = None,
    map_classification: str | None = None,
    map_classes: int = 5,
    show_labels: bool = True,
) -> dict[str, Any]:
    """Assemble the panel payload the renderer draws, resolving columns as needed."""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise InfographicError("EMPTY_DATA", "A non-empty table is required.")
    if aggregation not in {"sum", "mean", "median", "max", "min"}:
        raise InfographicError("INVALID_AGGREGATION", "aggregation must be sum, mean, median, max, or min.")

    resolved_panel = (panel_type or recommend_panel(frame)).strip().casefold()
    if resolved_panel not in PANEL_TYPE_IDS:
        raise InfographicError(
            "UNKNOWN_PANEL_TYPE",
            f"Unknown panel type '{panel_type}'.",
            {"available": sorted(PANEL_TYPE_IDS)},
        )

    numeric = _numeric_columns(frame)
    measure = measure_column or (numeric[0] if numeric else None)
    if resolved_panel != "stat_headline" and measure is None:
        raise InfographicError("NO_NUMERIC_COLUMN", "This panel needs at least one numeric column.")
    if measure is not None and measure not in frame.columns:
        raise InfographicError("UNKNOWN_COLUMN", f"Column '{measure}' is not in the data.")

    base: dict[str, Any] = {
        "panel_type": resolved_panel,
        "title": title or _default_title(resolved_panel, measure),
        "subtitle": subtitle,
        "source": source,
        "handle": handle,
        "prefix": prefix,
        "suffix": suffix,
    }

    if resolved_panel in {"india_map_story", "world_map_story"}:
        if boundary_geojson is None:
            scope = "global country" if resolved_panel == "world_map_story" else "India state"
            raise InfographicError("BOUNDARY_REQUIRED", f"{scope} map story needs a registered boundary.")
        geography = geography_column or category_column
        if geography and geography not in frame.columns:
            raise InfographicError("UNKNOWN_COLUMN", f"Column '{geography}' is not in the data.")
        global_story = resolved_panel == "world_map_story"
        try:
            result = build_geographic_map(
                frame,
                {
                    "map_type": "choropleth",
                    "geography_column": geography,
                    "measure_column": measure or measure_column,
                    "aggregation": aggregation,
                    "boundary_property": boundary_property,
                    "country": country or ("WLD" if global_story else "IN"),
                    "admin_level": admin_level if admin_level is not None else (0 if global_story else 1),
                    "palette": map_palette or "blue",
                    "classification": map_classification or "quantile",
                    "classes": map_classes,
                },
                boundary_geojson=boundary_geojson,
            )
        except GeographicError as exc:
            raise InfographicError(exc.code, exc.message, exc.details) from exc
        metric = result["field_mapping"].get("measure_column")
        scope_label = "the world" if global_story else "India"
        level_label = "Country-level" if global_story else "State-level"
        base.update({
            "title": title or f"{metric or 'Data'} across {scope_label}",
            "subtitle": subtitle or f"{level_label} comparison",
            "map_geojson": result["geojson"],
            "map_colors": result["style"]["colors"],
            "classification": result["classification"],
            "matching": result["matching"],
            "field_mapping": result["field_mapping"],
            "show_labels": bool(show_labels),
        })
        return base

    if resolved_panel == "stat_headline":
        if measure is None:
            base.update({"value": float(len(frame)), "caption": "rows in this dataset"})
        else:
            values = pd.to_numeric(frame[measure], errors="coerce").dropna()
            if values.empty:
                raise InfographicError("NO_USABLE_ROWS", f"Column '{measure}' had no numeric values.")
            total = float(getattr(values, aggregation)())
            base.update({
                "value": total,
                "caption": subtitle or f"{aggregation} of {measure} across {len(values):,} rows",
                "subtitle": None if subtitle else base["subtitle"],
            })
        return base

    if resolved_panel == "trend_line":
        resolved_date = date_column or _date_column(frame)
        if resolved_date is None:
            raise InfographicError("NO_DATE_COLUMN", "A trend needs a column that parses as dates.")
        work = frame[[resolved_date, measure]].copy()
        work[resolved_date] = pd.to_datetime(work[resolved_date], errors="coerce")
        work[measure] = pd.to_numeric(work[measure], errors="coerce")
        work = work.dropna().sort_values(resolved_date)
        if len(work) < 2:
            raise InfographicError("INSUFFICIENT_POINTS", "A trend needs at least two dated values.")
        grouped = getattr(work.groupby(work[resolved_date].dt.to_period("M"))[measure], aggregation)()
        points = [
            {"label": str(period), "value": float(value)}
            for period, value in grouped.items()
        ]
        if len(points) < 2:
            points = [
                {"label": row[resolved_date].strftime("%Y-%m-%d"), "value": float(row[measure])}
                for _, row in work.iterrows()
            ]
        base.update({"points": points})
        return base

    category = category_column or next(iter(_categorical_columns(frame)), None)
    if category is None:
        raise InfographicError("NO_CATEGORY_COLUMN", "This panel needs a categorical column with 2–50 values.")
    if category not in frame.columns:
        raise InfographicError("UNKNOWN_COLUMN", f"Column '{category}' is not in the data.")

    grouped = _aggregate(frame, category, measure, aggregation)
    items = [{"label": str(index), "value": float(value)} for index, value in grouped.items()]

    if resolved_panel == "ranked_bars":
        base.update({"items": items[:MAX_RANKED_ITEMS]})
    elif resolved_panel == "share_breakdown":
        positive = [item for item in items if item["value"] > 0]
        if not positive:
            raise InfographicError("NO_POSITIVE_VALUES", "A share breakdown needs positive values to divide a total.")
        base.update({"items": fold_tail_into_other(positive)})
    elif resolved_panel == "comparison":
        if len(items) < 2:
            raise InfographicError("INSUFFICIENT_GROUPS", "A comparison needs two groups.")
        base.update({"items": items[:2]})
    return base


def _default_title(panel_type: str, measure: str | None) -> str:
    subject = measure or "the data"
    return {
        "stat_headline": f"{subject}",
        "ranked_bars": f"{subject} by category",
        "trend_line": f"{subject} over time",
        "share_breakdown": f"Share of {subject}",
        "comparison": f"{subject} compared",
        "india_map_story": f"{subject} across India",
        "world_map_story": f"{subject} across the world",
    }.get(panel_type, str(subject))


def build_infographic(
    frame: pd.DataFrame,
    *,
    theme_id: str | None = None,
    format_id: str | None = None,
    **spec_options: Any,
) -> dict[str, Any]:
    """Build one infographic and return its PNG bytes alongside the resolved spec."""
    try:
        theme = get_theme(theme_id)
        canvas = get_format(format_id)
    except InfographicThemeError as exc:
        raise InfographicError("UNKNOWN_STYLE", str(exc)) from exc

    spec = build_spec(frame, **spec_options)
    try:
        image = render_panel(spec, theme, canvas)
    except InfographicError:
        raise
    except Exception as exc:
        raise InfographicError("RENDER_FAILED", f"The infographic could not be rendered: {exc}") from exc

    public_spec = {
        key: value for key, value in spec.items() if key not in {"panel_type", "map_geojson"}
    }
    return {
        "infographic_id": str(uuid4()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "panel_type": spec["panel_type"],
        "theme": theme.as_dict(),
        "format": canvas.as_dict(),
        "spec": public_spec,
        "png_bytes": image,
        "byte_size": len(image),
    }
