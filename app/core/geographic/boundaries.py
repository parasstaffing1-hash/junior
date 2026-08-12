"""GeoJSON validation and metadata helpers for the boundary registry."""

from __future__ import annotations

from typing import Any, Iterable


SUPPORTED_GEOMETRIES = {"Polygon", "MultiPolygon", "Point", "MultiPoint", "LineString", "MultiLineString"}
MAX_BOUNDARY_FEATURES = 50_000


def _coordinate_pairs(value: Any) -> Iterable[tuple[float, float]]:
    if isinstance(value, (list, tuple)):
        if len(value) >= 2 and all(isinstance(part, (int, float)) for part in value[:2]):
            yield float(value[0]), float(value[1])
        else:
            for part in value:
                yield from _coordinate_pairs(part)


def boundary_bbox(collection: dict[str, Any]) -> list[float] | None:
    coordinates = []
    for feature in collection.get("features", []):
        coordinates.extend(_coordinate_pairs((feature.get("geometry") or {}).get("coordinates")))
    if not coordinates:
        return None
    xs = [item[0] for item in coordinates]
    ys = [item[1] for item in coordinates]
    return [min(xs), min(ys), max(xs), max(ys)]


def validate_feature_collection(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("type") != "FeatureCollection":
        raise ValueError("Boundary geometry must be a GeoJSON FeatureCollection.")
    features = value.get("features")
    if not isinstance(features, list) or not features:
        raise ValueError("Boundary GeoJSON must contain at least one feature.")
    if len(features) > MAX_BOUNDARY_FEATURES:
        raise ValueError(f"Boundary GeoJSON exceeds the {MAX_BOUNDARY_FEATURES:,}-feature limit.")
    property_names: set[str] = set()
    for index, feature in enumerate(features):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ValueError(f"Boundary feature {index} is not a GeoJSON Feature.")
        geometry = feature.get("geometry") or {}
        if geometry.get("type") not in SUPPORTED_GEOMETRIES or not geometry.get("coordinates"):
            raise ValueError(f"Boundary feature {index} has an unsupported or empty geometry.")
        properties = feature.get("properties")
        if properties is None:
            feature["properties"] = {}
        elif not isinstance(properties, dict):
            raise ValueError(f"Boundary feature {index} properties must be an object.")
        property_names.update(str(key) for key in (feature.get("properties") or {}))
    return {
        "feature_count": len(features),
        "property_names": sorted(property_names),
        "bbox": boundary_bbox(value),
        "geometry_types": sorted({str((feature.get("geometry") or {}).get("type")) for feature in features}),
    }
