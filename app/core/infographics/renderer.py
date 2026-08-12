"""Matplotlib rendering of share-ready infographic panels.

The visual rules are fixed here rather than left to each caller: thin marks with a
rounded data-end squared at the baseline, hairline recessive chrome, a 2px surface
gap separating touching fills, a surface ring on overlapping markers, and selective
direct labels. Text never wears the series colour — a coloured mark beside the text
carries identity instead, except for a label set inside a fill, where the ink is
picked by the fill's luminance so it always clears contrast.
"""

from __future__ import annotations

import io
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402

from .themes import FONT_STACK, CanvasFormat, Theme  # noqa: E402

# The canvas is authored in pixels; matplotlib works in inches at a fixed DPI.
DPI = 100
# Bars stay thin — a filled slot reads loud and childish at social sizes.
MAX_BAR_THICKNESS_FRACTION = 0.55
# The data-end rounding is a fixed pixel look, so it is derived from the rendered
# bar thickness rather than from data units, which differ per axis and per panel.
CORNER_RADIUS_FRACTION = 0.18
MAX_CORNER_RADIUS_PX = 14.0
SEMIBOLD = 600


def _hex_to_rgb(value: str) -> tuple[float, float, float]:
    raw = value.lstrip("#")
    return tuple(int(raw[index : index + 2], 16) / 255 for index in (0, 2, 4))  # type: ignore[return-value]


def _relative_luminance(value: str) -> float:
    def channel(part: float) -> float:
        return part / 12.92 if part <= 0.03928 else ((part + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(part) for part in _hex_to_rgb(value))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def ink_on_fill(fill: str, theme: Theme) -> str:
    """Pick label ink inside a coloured fill by the fill's luminance."""
    return theme.on_fill_dark if _relative_luminance(fill) > 0.45 else theme.on_fill_light


def compact_number(value: float, *, prefix: str = "", suffix: str = "") -> str:
    """Format a headline number the way a reader scans it, not the way it is stored."""
    number = float(value)
    magnitude = abs(number)
    sign = "-" if number < 0 else ""
    if magnitude >= 1_000_000_000:
        body = f"{magnitude / 1_000_000_000:,.1f}B"
    elif magnitude >= 1_000_000:
        body = f"{magnitude / 1_000_000:,.1f}M"
    elif magnitude >= 10_000:
        body = f"{magnitude / 1_000:,.1f}K"
    elif magnitude >= 1:
        body = f"{magnitude:,.0f}" if float(magnitude).is_integer() else f"{magnitude:,.1f}"
    else:
        body = f"{magnitude:,.2f}"
    body = body.replace(".0B", "B").replace(".0M", "M").replace(".0K", "K")
    return f"{sign}{prefix}{body}{suffix}"


def new_canvas(theme: Theme, canvas: CanvasFormat):
    figure = plt.figure(figsize=(canvas.width / DPI, canvas.height / DPI), dpi=DPI)
    figure.patch.set_facecolor(theme.surface)
    return figure


def axis_scale(axes) -> tuple[float, float]:
    """Data units per pixel on each axis, for geometry that must look fixed in px."""
    axes.figure.canvas.draw()
    box = axes.get_window_extent()
    x_low, x_high = axes.get_xlim()
    y_low, y_high = axes.get_ylim()
    return abs(x_high - x_low) / max(box.width, 1.0), abs(y_high - y_low) / max(box.height, 1.0)


def bar_geometry(axes, thickness: float, *, horizontal: bool = True) -> tuple[float, float]:
    """Return the data-unit rounding radius and the mutation aspect for a bar.

    ``FancyBboxPatch`` rounds in x-data units and in ``rounding_size * aspect``
    y-data units, so the aspect is set to the ratio of the two scales to make the
    corner render as a circle rather than an ellipse stretched by the axis ranges.
    """
    x_per_px, y_per_px = axis_scale(axes)
    thickness_px = abs(thickness / (y_per_px if horizontal else x_per_px))
    radius_px = min(thickness_px * CORNER_RADIUS_FRACTION, MAX_CORNER_RADIUS_PX)
    if horizontal:
        return radius_px * x_per_px, y_per_px / x_per_px
    return radius_px * y_per_px, y_per_px / x_per_px


def rounded_bar(
    axes,
    *,
    start: float,
    length: float,
    position: float,
    thickness: float,
    color: str,
    corner_radius: float,
    mutation_aspect: float = 1.0,
    horizontal: bool = True,
    zorder: int = 3,
) -> None:
    """Draw one bar with a rounded data-end and a square baseline end.

    Matplotlib rounds every corner at once, so the baseline side is squared off by
    covering it with a plain rectangle in the same colour.
    """
    if length <= 0:
        return
    radius = max(min(corner_radius, abs(length) / 2), 1e-9)
    if horizontal:
        axes.add_patch(
            FancyBboxPatch(
                (start, position - thickness / 2),
                length,
                thickness,
                boxstyle=f"round,pad=0,rounding_size={radius}",
                linewidth=0,
                facecolor=color,
                zorder=zorder,
                mutation_aspect=mutation_aspect,
            )
        )
        axes.add_patch(
            Rectangle(
                (start, position - thickness / 2),
                radius,
                thickness,
                linewidth=0,
                facecolor=color,
                zorder=zorder + 1,
            )
        )
    else:
        axes.add_patch(
            FancyBboxPatch(
                (position - thickness / 2, start),
                thickness,
                length,
                boxstyle=f"round,pad=0,rounding_size={radius}",
                linewidth=0,
                facecolor=color,
                zorder=zorder,
                mutation_aspect=mutation_aspect,
            )
        )
        axes.add_patch(
            Rectangle(
                (position - thickness / 2, start),
                thickness,
                radius,
                linewidth=0,
                facecolor=color,
                zorder=zorder + 1,
            )
        )


def strip_chrome(axes, theme: Theme, *, grid_axis: str | None = None) -> None:
    """Remove default matplotlib furniture and leave only recessive hairlines."""
    axes.set_facecolor("none")
    for side in ("top", "right", "left", "bottom"):
        axes.spines[side].set_visible(False)
    axes.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False, length=0)
    if grid_axis:
        # Solid hairline, one step off the surface — never dashed.
        axes.grid(True, axis=grid_axis, color=theme.grid, linewidth=1.0, linestyle="-", zorder=0)
        axes.set_axisbelow(True)


def draw_header(
    figure,
    theme: Theme,
    *,
    title: str,
    subtitle: str | None,
    top: float = 0.94,
) -> float:
    """Write the title block and return the y position where content may start."""
    figure.text(
        0.06,
        top,
        title,
        color=theme.ink_primary,
        fontsize=34,
        fontweight=SEMIBOLD,
        fontfamily=FONT_STACK,
        va="top",
        ha="left",
        wrap=True,
    )
    cursor = top - 0.075
    if subtitle:
        figure.text(
            0.06,
            cursor,
            subtitle,
            color=theme.ink_secondary,
            fontsize=19,
            fontfamily=FONT_STACK,
            va="top",
            ha="left",
            wrap=True,
        )
        cursor -= 0.055
    return cursor


def draw_footer(figure, theme: Theme, *, source: str | None, handle: str | None) -> None:
    """Attribution line — a stats post without a source is not evidence."""
    if source:
        rendered_source = str(source).strip()
        if rendered_source.casefold().startswith("source:"):
            rendered_source = rendered_source.split(":", 1)[1].strip()
        figure.text(
            0.06,
            0.045,
            f"Source: {rendered_source}",
            color=theme.ink_muted,
            fontsize=15,
            fontfamily=FONT_STACK,
            va="center",
            ha="left",
        )
    if handle:
        figure.text(
            0.94,
            0.045,
            handle,
            color=theme.ink_muted,
            fontsize=15,
            fontweight=SEMIBOLD,
            fontfamily=FONT_STACK,
            va="center",
            ha="right",
        )


def figure_to_png(figure) -> bytes:
    buffer = io.BytesIO()
    figure.savefig(
        buffer,
        format="png",
        dpi=DPI,
        facecolor=figure.get_facecolor(),
        bbox_inches=None,
        pad_inches=0,
    )
    plt.close(figure)
    return buffer.getvalue()


def fits_inside(axes, text: str, *, fontsize: float, available_data_width: float) -> bool:
    """Measure a label before placing it inside a mark; never clip it to fit."""
    renderer = axes.figure.canvas.get_renderer()
    probe = axes.text(0, 0, text, fontsize=fontsize, fontfamily=FONT_STACK, alpha=0)
    extent = probe.get_window_extent(renderer=renderer)
    probe.remove()
    corners = axes.transData.inverted().transform([(0, 0), (extent.width, 0)])
    width_in_data = abs(corners[1][0] - corners[0][0])
    return width_in_data * 1.35 <= available_data_width


def apply_font_defaults() -> None:
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = list(FONT_STACK)
    plt.rcParams["axes.unicode_minus"] = False


apply_font_defaults()
