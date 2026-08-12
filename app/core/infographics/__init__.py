"""Share-ready infographic generation from tabular data."""

from .service import (
    InfographicError,
    build_infographic,
    build_spec,
    catalog,
    parse_pasted_table,
    recommend_panel,
)
from .themes import InfographicThemeError, get_format, get_theme, list_formats, list_themes

__all__ = [
    "InfographicError",
    "InfographicThemeError",
    "build_infographic",
    "build_spec",
    "catalog",
    "get_format",
    "get_theme",
    "list_formats",
    "list_themes",
    "parse_pasted_table",
    "recommend_panel",
]
