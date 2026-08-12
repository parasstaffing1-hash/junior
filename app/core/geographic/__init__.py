"""Deterministic geographic intelligence for tabular datasets."""

from .boundaries import boundary_bbox, validate_feature_collection
from .resolver import normalize_geo_name, resolve_geography
from .semantic import detect_geographic_semantics
from .service import (
    GeographicError,
    INDIA_OFFICIAL_BOUNDARY_SOURCE,
    build_geographic_map,
    geographic_catalog,
    geographic_eda,
    location_analytics,
    recommend_maps,
)

__all__ = [
    "GeographicError",
    "INDIA_OFFICIAL_BOUNDARY_SOURCE",
    "boundary_bbox",
    "build_geographic_map",
    "detect_geographic_semantics",
    "geographic_catalog",
    "geographic_eda",
    "location_analytics",
    "normalize_geo_name",
    "recommend_maps",
    "resolve_geography",
    "validate_feature_collection",
]
