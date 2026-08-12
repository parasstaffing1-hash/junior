"""Visual themes and canvas formats for shareable infographics.

Every palette here was checked with the data-visualization validator against the
theme's own surface — not against a generic one — for the OKLCH lightness band,
the chroma floor, adjacent-pair separation under simulated colour-vision
deficiency, the normal-vision floor, and WCAG contrast. The two light themes sit
below 3:1 on some series fills, which obligates visible direct labels; the
renderer always labels segments, so that relief is built in rather than optional.

Series hues are ordered, never cycled. A breakdown past ``MAX_BREAKDOWN_SEGMENTS``
folds its tail into a single "Other" segment instead of inventing a new hue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Past this, adjacent classes blur and the palette runs out of validated slots.
MAX_BREAKDOWN_SEGMENTS = 6

# Sans throughout, including the hero figure — a display or serif face there reads
# as decoration. Segoe UI ships with Windows; the rest are fallbacks.
FONT_STACK: tuple[str, ...] = ("Segoe UI", "Tahoma", "Verdana", "Arial", "DejaVu Sans")


@dataclass(frozen=True)
class Theme:
    id: str
    name: str
    description: str
    mode: str
    surface: str
    panel: str
    ink_primary: str
    ink_secondary: str
    ink_muted: str
    grid: str
    accent: str
    de_emphasis: str
    series: tuple[str, ...]
    on_fill_light: str = "#ffffff"
    on_fill_dark: str = "#0b0b0b"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "mode": self.mode,
            "surface": self.surface,
            "accent": self.accent,
            "series": list(self.series),
        }


# The eight-hue reference order, trimmed to the six slots a breakdown can seat.
_LIGHT_SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300")
_DARK_SERIES = ("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300")


THEMES: tuple[Theme, ...] = (
    Theme(
        id="midnight",
        name="Midnight",
        description="Deep navy canvas with a bright accent. Reads well in a dark timeline.",
        mode="dark",
        surface="#12151c",
        panel="#1a1e27",
        ink_primary="#ffffff",
        ink_secondary="#c3c2b7",
        ink_muted="#898781",
        grid="#252a35",
        accent="#3987e5",
        de_emphasis="#3a4150",
        series=_DARK_SERIES,
    ),
    Theme(
        id="slate",
        name="Slate",
        description="Cool graphite canvas. Neutral and editorial without going pure black.",
        mode="dark",
        surface="#1e2229",
        panel="#262b34",
        ink_primary="#ffffff",
        ink_secondary="#c3c2b7",
        ink_muted="#8f8e88",
        grid="#31373f",
        accent="#199e70",
        de_emphasis="#454c57",
        series=("#199e70", "#3987e5", "#d95926", "#c98500", "#d55181", "#008300"),
    ),
    Theme(
        id="paper",
        name="Paper",
        description="Warm cream canvas with ink type. Calm, print-like, high legibility.",
        mode="light",
        surface="#f7f3ea",
        panel="#fffdf7",
        ink_primary="#0b0b0b",
        ink_secondary="#52514e",
        ink_muted="#898781",
        grid="#e4ded1",
        accent="#2a78d6",
        de_emphasis="#d8d2c6",
        series=_LIGHT_SERIES,
    ),
    Theme(
        id="mono",
        name="Mono",
        description="White canvas, one accent, maximum contrast. The safe default.",
        mode="light",
        surface="#ffffff",
        panel="#f7f7f5",
        ink_primary="#0b0b0b",
        ink_secondary="#52514e",
        ink_muted="#898781",
        grid="#e8e8e4",
        accent="#2a78d6",
        de_emphasis="#dcdcd9",
        series=_LIGHT_SERIES,
    ),
    Theme(
        id="india_pixels",
        name="India Pixels",
        description="Warm editorial paper with vivid map classes and strong black typography.",
        mode="light",
        surface="#f7f3e7",
        panel="#fffdf7",
        ink_primary="#111111",
        ink_secondary="#4f4d47",
        ink_muted="#77736a",
        grid="#e3ddcf",
        accent="#4267d5",
        de_emphasis="#d6d0c2",
        series=("#4267d5", "#ef8b64", "#44ae9b", "#b868d4", "#e9b84d", "#d95c7c"),
    ),
)

THEMES_BY_ID: dict[str, Theme] = {theme.id: theme for theme in THEMES}
DEFAULT_THEME_ID = "midnight"


@dataclass(frozen=True)
class CanvasFormat:
    id: str
    name: str
    description: str
    width: int
    height: int

    @property
    def aspect(self) -> str:
        return f"{self.width}x{self.height}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "width": self.width,
            "height": self.height,
        }


FORMATS: tuple[CanvasFormat, ...] = (
    CanvasFormat("square", "Square", "1:1 — the safest in-feed crop on X and Instagram.", 1080, 1080),
    CanvasFormat("landscape", "Landscape", "1200×675 — X landscape post and link preview.", 1200, 675),
    CanvasFormat("portrait", "Portrait", "4:5 — the tallest in-feed crop, best for long rankings.", 1080, 1350),
    CanvasFormat("story", "Story", "9:16 — full-screen vertical for Stories and Reels.", 1080, 1920),
)

FORMATS_BY_ID: dict[str, CanvasFormat] = {item.id: item for item in FORMATS}
DEFAULT_FORMAT_ID = "square"


class InfographicThemeError(ValueError):
    """An unknown theme or canvas format was requested."""


def get_theme(theme_id: str | None) -> Theme:
    resolved = (theme_id or DEFAULT_THEME_ID).strip().casefold()
    if resolved not in THEMES_BY_ID:
        raise InfographicThemeError(
            f"Unknown infographic theme '{theme_id}'. Available: {', '.join(sorted(THEMES_BY_ID))}."
        )
    return THEMES_BY_ID[resolved]


def get_format(format_id: str | None) -> CanvasFormat:
    resolved = (format_id or DEFAULT_FORMAT_ID).strip().casefold()
    if resolved not in FORMATS_BY_ID:
        raise InfographicThemeError(
            f"Unknown canvas format '{format_id}'. Available: {', '.join(sorted(FORMATS_BY_ID))}."
        )
    return FORMATS_BY_ID[resolved]


def list_themes() -> list[dict[str, Any]]:
    return [theme.as_dict() for theme in THEMES]


def list_formats() -> list[dict[str, Any]]:
    return [item.as_dict() for item in FORMATS]
