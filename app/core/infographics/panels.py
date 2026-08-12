"""The infographic panel types, one per job the data has to do.

The form is chosen before the colour: a single number is a hero figure, not a
one-bar chart; magnitude across nominal categories is one hue with the leading bar
emphasised, never a value-ramp that double-encodes length as colour; part-to-whole
is a stacked bar rather than a pie; change over time is a line with a single
end-label.
"""

from __future__ import annotations

from typing import Any

from .renderer import (
    MAX_BAR_THICKNESS_FRACTION,
    SEMIBOLD,
    bar_geometry,
    compact_number,
    draw_footer,
    draw_header,
    figure_to_png,
    fits_inside,
    ink_on_fill,
    new_canvas,
    rounded_bar,
    strip_chrome,
)
from .themes import FONT_STACK, MAX_BREAKDOWN_SEGMENTS, CanvasFormat, Theme

PANEL_TYPES: tuple[dict[str, str], ...] = (
    {"id": "stat_headline", "name": "Headline stat", "job": "Lead with one number.", "needs": "a single value"},
    {"id": "ranked_bars", "name": "Ranked bars", "job": "Rank categories by size.", "needs": "a category and a measure"},
    {"id": "trend_line", "name": "Trend line", "job": "Show change over time.", "needs": "a date and a measure"},
    {"id": "share_breakdown", "name": "Share breakdown", "job": "Split a total into parts.", "needs": "a category and a measure"},
    {"id": "comparison", "name": "Comparison", "job": "Put two groups side by side.", "needs": "a category and a measure"},
    {"id": "india_map_story", "name": "India map story", "job": "Turn state data into an editorial map.", "needs": "a state field, a measure, and a registered boundary"},
    {"id": "world_map_story", "name": "Global country map story", "job": "Turn country data into an editorial world map.", "needs": "a country or ISO field, a measure, and a registered boundary"},
)

PANEL_TYPE_IDS = frozenset(item["id"] for item in PANEL_TYPES)


def _hero_fontsize(text: str, canvas: CanvasFormat) -> float:
    """Scale the hero figure down only as far as the string forces."""
    base = 150 if canvas.width >= canvas.height else 128
    if len(text) > 8:
        base *= 0.62
    elif len(text) > 6:
        base *= 0.74
    elif len(text) > 4:
        base *= 0.87
    return base


def render_stat_headline(spec: dict[str, Any], theme: Theme, canvas: CanvasFormat) -> bytes:
    figure = new_canvas(theme, canvas)
    draw_header(figure, theme, title=spec["title"], subtitle=spec.get("subtitle"))

    value_text = spec.get("display_value") or compact_number(
        spec["value"], prefix=spec.get("prefix", ""), suffix=spec.get("suffix", "")
    )
    # Exactly one hero figure per panel, in the same sans as everything else.
    figure.text(
        0.06,
        0.52,
        value_text,
        color=theme.accent,
        fontsize=_hero_fontsize(value_text, canvas),
        fontweight="bold",
        fontfamily=FONT_STACK,
        va="center",
        ha="left",
    )
    caption = spec.get("caption")
    if caption:
        figure.text(
            0.06,
            0.30,
            caption,
            color=theme.ink_secondary,
            fontsize=24,
            fontfamily=FONT_STACK,
            va="center",
            ha="left",
            wrap=True,
        )
    delta = spec.get("delta_label")
    if delta:
        figure.text(
            0.06,
            0.21,
            delta,
            color=theme.ink_muted,
            fontsize=20,
            fontfamily=FONT_STACK,
            va="center",
            ha="left",
        )
    draw_footer(figure, theme, source=spec.get("source"), handle=spec.get("handle"))
    return figure_to_png(figure)


def render_ranked_bars(spec: dict[str, Any], theme: Theme, canvas: CanvasFormat) -> bytes:
    items = spec["items"]
    figure = new_canvas(theme, canvas)
    content_top = draw_header(figure, theme, title=spec["title"], subtitle=spec.get("subtitle"))

    labels = [str(item["label"]) for item in items]
    values = [float(item["value"]) for item in items]
    largest = max(values) if values else 1.0
    if largest <= 0:
        largest = 1.0

    # The category gutter is sized before the axes are placed, so the rounding
    # geometry below is measured against the final plot box rather than a stale one.
    longest = max((len(label) for label in labels), default=0)
    left_margin = min(0.42, 0.10 + longest * 0.012)
    axes = figure.add_axes([left_margin, 0.13, 0.94 - left_margin, max(0.20, content_top - 0.16)])
    strip_chrome(axes, theme)

    count = len(items)
    thickness = MAX_BAR_THICKNESS_FRACTION
    axes.set_xlim(0, largest * 1.30)
    axes.set_ylim(-0.7, count - 0.3)
    axes.invert_yaxis()
    corner_radius, mutation_aspect = bar_geometry(axes, thickness, horizontal=True)

    # One hue for the series; the leading bar carries the accent and the rest
    # recede. A ramp across nominal categories would re-encode length as colour.
    emphasize = bool(spec.get("emphasize_leader", True))
    for index, (label, value) in enumerate(zip(labels, values)):
        color = theme.accent if (emphasize and index == 0) else theme.de_emphasis
        rounded_bar(
            axes,
            start=0,
            length=value,
            position=index,
            thickness=thickness,
            color=color,
            corner_radius=corner_radius,
            mutation_aspect=mutation_aspect,
            horizontal=True,
        )
        axes.text(
            -largest * 0.012,
            index,
            label,
            color=theme.ink_primary if (emphasize and index == 0) else theme.ink_secondary,
            fontsize=21,
            fontweight=SEMIBOLD if (emphasize and index == 0) else "normal",
            fontfamily=FONT_STACK,
            va="center",
            ha="right",
        )
        # Bars label at the tip; the axis is dropped entirely at this size.
        axes.text(
            value + largest * 0.018,
            index,
            compact_number(value, prefix=spec.get("prefix", ""), suffix=spec.get("suffix", "")),
            color=theme.ink_primary,
            fontsize=21,
            fontweight=SEMIBOLD,
            fontfamily=FONT_STACK,
            va="center",
            ha="left",
        )

    draw_footer(figure, theme, source=spec.get("source"), handle=spec.get("handle"))
    return figure_to_png(figure)


def render_trend_line(spec: dict[str, Any], theme: Theme, canvas: CanvasFormat) -> bytes:
    points = spec["points"]
    figure = new_canvas(theme, canvas)
    content_top = draw_header(figure, theme, title=spec["title"], subtitle=spec.get("subtitle"))

    axes = figure.add_axes([0.10, 0.16, 0.84, max(0.20, content_top - 0.20)])
    strip_chrome(axes, theme, grid_axis="y")

    labels = [str(point["label"]) for point in points]
    values = [float(point["value"]) for point in points]
    positions = list(range(len(values)))

    axes.plot(positions, values, color=theme.accent, linewidth=2.0, solid_capstyle="round", solid_joinstyle="round", zorder=3)
    if spec.get("fill", True):
        # A wash, never a saturated block.
        axes.fill_between(positions, values, min(values), color=theme.accent, alpha=0.10, linewidth=0, zorder=2)

    # The end marker carries a surface ring so it stays legible over the line.
    axes.plot(
        positions[-1],
        values[-1],
        marker="o",
        markersize=13,
        markerfacecolor=theme.accent,
        markeredgecolor=theme.surface,
        markeredgewidth=2.0,
        zorder=4,
    )
    axes.annotate(
        compact_number(values[-1], prefix=spec.get("prefix", ""), suffix=spec.get("suffix", "")),
        xy=(positions[-1], values[-1]),
        xytext=(-8, 22),
        textcoords="offset points",
        color=theme.ink_primary,
        fontsize=24,
        fontweight="bold",
        fontfamily=FONT_STACK,
        ha="right",
    )

    span = max(values) - min(values)
    padding = span * 0.22 if span else max(abs(values[0]) * 0.1, 1.0)
    axes.set_ylim(min(values) - padding * 0.6, max(values) + padding)
    axes.set_xlim(-0.35, len(values) - 0.65)

    # First and last period only — a label under every point is unreadable here.
    for index in {0, len(labels) - 1}:
        axes.text(
            index,
            min(values) - padding * 0.6,
            labels[index],
            color=theme.ink_muted,
            fontsize=18,
            fontfamily=FONT_STACK,
            va="top",
            ha="left" if index == 0 else "right",
        )

    draw_footer(figure, theme, source=spec.get("source"), handle=spec.get("handle"))
    return figure_to_png(figure)


def render_share_breakdown(spec: dict[str, Any], theme: Theme, canvas: CanvasFormat) -> bytes:
    items = spec["items"]
    figure = new_canvas(theme, canvas)
    content_top = draw_header(figure, theme, title=spec["title"], subtitle=spec.get("subtitle"))

    total = sum(float(item["value"]) for item in items) or 1.0
    # The stack sits just under the title block; the legend fills the space below it.
    bar_height = 0.13
    bar_bottom = max(0.46, content_top - 0.16)
    axes = figure.add_axes([0.06, bar_bottom, 0.88, bar_height])
    strip_chrome(axes, theme)
    axes.set_xlim(0, total)
    axes.set_ylim(0, 1)

    # A 2px surface gap does the separating; a stroke around each segment would
    # add ink that is not data.
    gap = total * 0.004
    cursor = 0.0
    figure.canvas.draw()
    for index, item in enumerate(items):
        value = float(item["value"])
        width = max(value - gap, total * 0.002)
        axes.add_artist(
            _segment(axes, cursor, width, theme.series[index % len(theme.series)])
        )
        share = value / total * 100
        label = f"{share:.0f}%"
        if fits_inside(axes, label, fontsize=22, available_data_width=width):
            fill = theme.series[index % len(theme.series)]
            axes.text(
                cursor + width / 2,
                0.5,
                label,
                color=ink_on_fill(fill, theme),
                fontsize=22,
                fontweight="bold",
                fontfamily=FONT_STACK,
                va="center",
                ha="center",
                zorder=5,
            )
        cursor += value

    # A legend is always present for two or more series, so identity never rests
    # on colour matching alone.
    legend_top = bar_bottom - 0.10
    for index, item in enumerate(items):
        row, column = divmod(index, 2)
        x = 0.06 + column * 0.46
        y = legend_top - row * 0.075
        figure.patches.append(
            _legend_swatch(figure, x, y, theme.series[index % len(theme.series)])
        )
        share = float(item["value"]) / total * 100
        figure.text(
            x + 0.035,
            y + 0.012,
            f"{item['label']}",
            color=theme.ink_primary,
            fontsize=20,
            fontfamily=FONT_STACK,
            va="center",
            ha="left",
        )
        figure.text(
            x + 0.42,
            y + 0.012,
            f"{share:.1f}%",
            color=theme.ink_secondary,
            fontsize=20,
            fontweight=SEMIBOLD,
            fontfamily=FONT_STACK,
            va="center",
            ha="right",
        )

    draw_footer(figure, theme, source=spec.get("source"), handle=spec.get("handle"))
    return figure_to_png(figure)


def _segment(axes, start: float, width: float, color: str):
    from matplotlib.patches import Rectangle

    return Rectangle((start, 0.1), width, 0.8, linewidth=0, facecolor=color, zorder=3, transform=axes.transData)


def _legend_swatch(figure, x: float, y: float, color: str):
    from matplotlib.patches import Circle

    return Circle((x + 0.010, y + 0.012), 0.009, facecolor=color, linewidth=0, transform=figure.transFigure, zorder=4)


def render_comparison(spec: dict[str, Any], theme: Theme, canvas: CanvasFormat) -> bytes:
    items = spec["items"][:2]
    figure = new_canvas(theme, canvas)
    draw_header(figure, theme, title=spec["title"], subtitle=spec.get("subtitle"))

    values = [float(item["value"]) for item in items]
    largest = max(values) if values else 1.0
    if largest <= 0:
        largest = 1.0
    # One hue in two shades — the groups are the same measure, not two identities.
    colors = [theme.accent, theme.de_emphasis]

    for index, (item, value) in enumerate(zip(items, values)):
        x = 0.06 + index * 0.48
        figure.text(
            x,
            0.62,
            compact_number(value, prefix=spec.get("prefix", ""), suffix=spec.get("suffix", "")),
            color=colors[index] if index == 0 else theme.ink_primary,
            fontsize=88,
            fontweight="bold",
            fontfamily=FONT_STACK,
            va="center",
            ha="left",
        )
        figure.text(
            x,
            0.48,
            str(item["label"]),
            color=theme.ink_secondary,
            fontsize=24,
            fontfamily=FONT_STACK,
            va="center",
            ha="left",
        )

    axes = figure.add_axes([0.06, 0.20, 0.88, 0.20])
    strip_chrome(axes, theme)
    axes.set_xlim(0, largest * 1.05)
    axes.set_ylim(-0.6, 1.6)
    axes.invert_yaxis()
    corner_radius, mutation_aspect = bar_geometry(axes, 0.5, horizontal=True)
    for index, value in enumerate(values):
        rounded_bar(
            axes,
            start=0,
            length=value,
            position=index,
            thickness=0.5,
            color=colors[index],
            corner_radius=corner_radius,
            mutation_aspect=mutation_aspect,
            horizontal=True,
        )

    if len(values) == 2 and values[1]:
        difference = (values[0] - values[1]) / abs(values[1]) * 100
        figure.text(
            0.06,
            0.12,
            f"{items[0]['label']} is {abs(difference):,.1f}% {'higher' if difference >= 0 else 'lower'} than {items[1]['label']}",
            color=theme.ink_muted,
            fontsize=20,
            fontfamily=FONT_STACK,
            va="center",
            ha="left",
        )

    draw_footer(figure, theme, source=spec.get("source"), handle=spec.get("handle"))
    return figure_to_png(figure)


def _map_coordinate_pairs(value: Any):
    if isinstance(value, (list, tuple)):
        if len(value) >= 2 and all(isinstance(part, (int, float)) for part in value[:2]):
            yield float(value[0]), float(value[1])
        else:
            for part in value:
                yield from _map_coordinate_pairs(part)


def _map_geometry_rings(geometry: dict[str, Any]) -> list[list[tuple[float, float]]]:
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if kind == "Polygon":
        return [[(float(point[0]), float(point[1])) for point in ring if len(point) >= 2] for ring in coordinates]
    if kind == "MultiPolygon":
        return [
            [(float(point[0]), float(point[1])) for point in ring if len(point) >= 2]
            for polygon in coordinates
            for ring in polygon
        ]
    return []


def _map_bbox(features: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    coordinates = [
        pair
        for feature in features
        for pair in _map_coordinate_pairs((feature.get("geometry") or {}).get("coordinates"))
    ]
    if not coordinates:
        return 68.0, 6.0, 98.0, 36.0
    longitudes = [pair[0] for pair in coordinates]
    latitudes = [pair[1] for pair in coordinates]
    west, east = min(longitudes), max(longitudes)
    south, north = min(latitudes), max(latitudes)
    margin_x = max((east - west) * 0.04, 0.2)
    margin_y = max((north - south) * 0.04, 0.2)
    return west - margin_x, south - margin_y, east + margin_x, north + margin_y


def _map_label_point(rings: list[list[tuple[float, float]]]) -> tuple[float, float] | None:
    ring = max(rings, key=len, default=[])
    if not ring:
        return None
    return sum(point[0] for point in ring) / len(ring), sum(point[1] for point in ring) / len(ring)


def _map_color(spec: dict[str, Any], properties: dict[str, Any], theme: Theme) -> str:
    colors = spec.get("map_colors") or list(theme.series)
    value = properties.get("metric")
    if value is None:
        return theme.de_emphasis
    index = properties.get("class_index")
    try:
        index = int(index)
    except (TypeError, ValueError):
        index = len(colors) - 1
    return str(colors[max(0, min(index, len(colors) - 1))])


def render_india_map_story(spec: dict[str, Any], theme: Theme, canvas: CanvasFormat) -> bytes:
    """Render a publication-ready country or state choropleth with evidence metadata."""
    features = list((spec.get("map_geojson") or {}).get("features") or [])
    if not features:
        raise ValueError("Map story requires a non-empty registered boundary.")
    figure = new_canvas(theme, canvas)
    draw_header(figure, theme, title=spec["title"], subtitle=spec.get("subtitle"))
    west, south, east, north = _map_bbox(features)
    axes = figure.add_axes([0.07, 0.18, 0.86, 0.67])
    axes.set_xlim(west, east)
    axes.set_ylim(south, north)
    axes.set_aspect("equal", adjustable="box")
    axes.axis("off")

    from matplotlib.patches import Polygon as MapPolygon, Rectangle as MapRectangle

    for feature in features:
        properties = feature.get("properties") or {}
        rings = _map_geometry_rings(feature.get("geometry") or {})
        fill = _map_color(spec, properties, theme)
        for ring in rings:
            if len(ring) >= 3:
                axes.add_patch(MapPolygon(ring, closed=True, facecolor=fill, edgecolor=theme.surface, linewidth=0.9, joinstyle="round", zorder=2))
        if not spec.get("show_labels", True):
            continue
        point = _map_label_point(rings)
        region = str(properties.get("region") or properties.get("name") or "").strip()
        if point and region:
            metric = properties.get("metric")
            label = region if metric is None else f"{region}\n{compact_number(float(metric), prefix=spec.get('prefix', ''), suffix=spec.get('suffix', ''))}"
            axes.text(point[0], point[1], label, color=ink_on_fill(fill, theme), fontsize=8.5 if len(region) < 13 else 7.2, fontweight=SEMIBOLD, fontfamily=FONT_STACK, ha="center", va="center", linespacing=0.9, zorder=4)

    matching = spec.get("matching") or {}
    axes.text(0.0, 1.01, f"{matching.get('matched_features', 0)}/{matching.get('boundary_features', len(features))} regions matched", transform=axes.transAxes, color=theme.ink_muted, fontsize=11, fontfamily=FONT_STACK, ha="left", va="bottom")
    classification = spec.get("classification") or {}
    breaks = classification.get("breaks") or []
    colors = spec.get("map_colors") or list(theme.series)
    if len(breaks) >= 2:
        legend_ax = figure.add_axes([0.07, 0.09, 0.86, 0.045])
        legend_ax.axis("off")
        slots = min(len(colors), max(1, len(breaks) - 1))
        for index in range(slots):
            left = index / slots
            legend_ax.add_patch(MapRectangle((left, 0.18), 1 / slots - 0.006, 0.28, facecolor=colors[index], transform=legend_ax.transAxes, linewidth=0))
            lower = compact_number(breaks[index], prefix=spec.get("prefix", ""), suffix=spec.get("suffix", ""))
            upper = compact_number(breaks[index + 1], prefix=spec.get("prefix", ""), suffix=spec.get("suffix", ""))
            legend_ax.text(left, 0.02, lower, transform=legend_ax.transAxes, color=theme.ink_muted, fontsize=8.5, fontfamily=FONT_STACK, ha="left", va="top")
            if index == slots - 1:
                legend_ax.text(left + 1 / slots - 0.006, 0.02, upper, transform=legend_ax.transAxes, color=theme.ink_muted, fontsize=8.5, fontfamily=FONT_STACK, ha="right", va="top")
    draw_footer(figure, theme, source=spec.get("source"), handle=spec.get("handle"))
    return figure_to_png(figure)


RENDERERS = {
    "stat_headline": render_stat_headline,
    "ranked_bars": render_ranked_bars,
    "trend_line": render_trend_line,
    "share_breakdown": render_share_breakdown,
    "comparison": render_comparison,
    "india_map_story": render_india_map_story,
    "world_map_story": render_india_map_story,
}


def render_panel(spec: dict[str, Any], theme: Theme, canvas: CanvasFormat) -> bytes:
    panel_type = str(spec.get("panel_type", "")).strip().casefold()
    if panel_type not in RENDERERS:
        raise ValueError(
            f"Unknown infographic panel type '{spec.get('panel_type')}'. Available: {', '.join(sorted(RENDERERS))}."
        )
    return RENDERERS[panel_type](spec, theme, canvas)


def fold_tail_into_other(items: list[dict[str, Any]], limit: int = MAX_BREAKDOWN_SEGMENTS) -> list[dict[str, Any]]:
    """Past the validated slot count, the tail folds into one 'Other' segment."""
    if len(items) <= limit:
        return items
    head = items[: limit - 1]
    tail_total = sum(float(item["value"]) for item in items[limit - 1 :])
    return [*head, {"label": "Other", "value": tail_total}]
