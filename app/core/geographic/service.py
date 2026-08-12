"""Map recommendations, geographic EDA, and source-backed map specifications."""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Iterable
from uuid import uuid4

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype

from app.errors import AppError

from .boundaries import boundary_bbox, validate_feature_collection
from .resolver import normalize_geo_name, resolve_geography
from .semantic import detect_geographic_semantics


CORE_MAP_TYPES = ("choropleth", "categorical_region", "point", "bubble", "heatmap", "cluster")
ADVANCED_MAP_TYPES = ("density", "proportional_symbol", "dot_density", "hexbin", "bivariate_choropleth", "flow", "route", "time_series")
AGGREGATIONS = ("sum", "average", "mean", "median", "count", "distinct_count", "minimum", "min", "maximum", "max")
CLASSIFICATIONS = ("equal_interval", "quantile", "natural_breaks", "custom")
INDIA_COUNTRY_CODE = "IN"
INDIA_OFFICIAL_BOUNDARY_SOURCE = {
    "provider": "Survey of India",
    "country_code": INDIA_COUNTRY_CODE,
    "scope": "india_only",
    "admin_levels": [1, 2],
    "official": True,
    "status": "requires_import",
    "catalog_url": "https://onlinemaps.surveyofindia.gov.in/Digital_Product_Show.aspx/FAQs.aspx",
    "administrative_boundary_url": "https://surveyofindia.gov.in/pages/administrative-boundary-data-base-abdb-",
    "accepted_geometry_format": "GeoJSON",
}
PALETTES = {
    "blue": ["#eff6ff", "#bfdbfe", "#60a5fa", "#2563eb", "#1e3a8a"],
    "teal": ["#ecfdf5", "#a7f3d0", "#34d399", "#059669", "#064e3b"],
    "sunset": ["#fff7ed", "#fed7aa", "#fb923c", "#ea580c", "#7c2d12"],
    "purple": ["#faf5ff", "#e9d5ff", "#c084fc", "#9333ea", "#581c87"],
}
MAP_TEMPLATES = (
    {"id": "executive_choropleth", "name": "Executive choropleth", "map_type": "choropleth", "palette": "blue", "classification": "quantile", "classes": 5},
    {"id": "location_bubbles", "name": "Location bubbles", "map_type": "bubble", "palette": "teal", "radius": 18},
    {"id": "hotspot_heatmap", "name": "Hotspot heatmap", "map_type": "heatmap", "palette": "sunset", "radius": 24},
    {"id": "dense_points", "name": "Dense point clusters", "map_type": "cluster", "palette": "purple", "radius": 16},
)


class GeographicError(AppError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None, status_code: int = 422):
        super().__init__(code, message, status_code=status_code, details=details or {})


def _physical_schema(frame: pd.DataFrame) -> dict[str, Any]:
    columns = []
    for name in frame.columns:
        series = frame[name]
        if is_numeric_dtype(series):
            detected_type = "float" if pd.api.types.is_float_dtype(series) else "integer"
        elif pd.api.types.is_datetime64_any_dtype(series):
            detected_type = "datetime"
        else:
            detected_type = "string"
        columns.append({"name": str(name), "detected_type": detected_type, "unique_count": int(series.nunique(dropna=True))})
    return {"columns": columns}


def _records(frame: pd.DataFrame, limit: int = 10_000) -> list[dict[str, Any]]:
    clean = frame.head(limit).where(pd.notna(frame.head(limit)), None)
    return clean.to_dict(orient="records")


def geographic_profile(frame: pd.DataFrame) -> dict[str, Any]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise GeographicError("EMPTY_DATA", "A non-empty dataset is required for geographic analysis.")
    semantics = detect_geographic_semantics(_records(frame), _physical_schema(frame))
    numeric = [str(column) for column in frame.columns if is_numeric_dtype(frame[column])]
    categorical = [str(column) for column in frame.columns if not is_numeric_dtype(frame[column]) and frame[column].nunique(dropna=True) <= 500]
    return {
        **semantics,
        "row_count": int(len(frame)),
        "numeric_columns": numeric,
        "categorical_columns": categorical,
        "has_coordinates": bool(
            any(item["semantic_type"] == "geo_latitude" for item in semantics["columns"])
            and any(item["semantic_type"] == "geo_longitude" for item in semantics["columns"])
        ),
    }


def _first(profile: dict[str, Any], semantic_type: str) -> str | None:
    return next((item["column"] for item in profile.get("columns", []) if item["semantic_type"] == semantic_type), None)


def _origin_destination(frame: pd.DataFrame) -> bool:
    names = {str(column).casefold() for column in frame.columns}
    return bool(names & {"origin", "origin_city", "origin_airport"}) and bool(names & {"destination", "dest", "destination_city", "destination_airport"})


def recommend_maps(frame: pd.DataFrame) -> dict[str, Any]:
    profile = geographic_profile(frame)
    recommendations: list[dict[str, Any]] = []
    latitude = _first(profile, "geo_latitude")
    longitude = _first(profile, "geo_longitude")
    region = next((item for item in profile["columns"] if item["semantic_type"] in {"geo_country", "geo_state", "geo_district", "geo_city", "geo_postal_code", "geo_constituency", "geo_custom_region"}), None)
    measure = next(iter(profile["numeric_columns"]), None)
    category = next(iter(profile["categorical_columns"]), None)
    date_column = next((str(column) for column in frame.columns if "date" in str(column).casefold() or "time" in str(column).casefold()), None)

    if latitude and longitude:
        if measure:
            recommendations.append({"type": "bubble", "score": 0.98, "reason": f"Latitude/longitude plus continuous {measure}", "implementation_status": "ready"})
        if len(frame) >= 1_000:
            recommendations.append({"type": "cluster", "score": 0.96, "reason": "Dense coordinate data benefits from interactive clustering", "implementation_status": "ready"})
            recommendations.append({"type": "heatmap", "score": 0.92, "reason": "Many coordinate observations can reveal hotspots", "implementation_status": "ready"})
        recommendations.append({"type": "point", "score": 0.9, "reason": "Validated latitude and longitude fields are available", "implementation_status": "ready"})
    if region and measure:
        recommendations.append({"type": "choropleth", "score": 0.97, "reason": f"{region['semantic_type'].replace('geo_', '').title()} geography plus continuous {measure}", "implementation_status": "ready_with_boundary"})
    if region and category:
        recommendations.append({"type": "categorical_region", "score": 0.89, "reason": f"Geographic regions can be colored by {category}", "implementation_status": "ready_with_boundary"})
    if _origin_destination(frame):
        recommendations.append({"type": "flow", "score": 0.84, "reason": "Origin and destination fields support movement analysis", "implementation_status": "extension"})
    if date_column and (latitude and longitude or region):
        recommendations.append({"type": "time_series", "score": 0.82, "reason": f"{date_column} can animate geographic change", "implementation_status": "extension"})
    recommendations.sort(key=lambda item: (-item["score"], item["type"]))
    return {"recommended": recommendations, "profile": profile}


def _clean_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _validated_column(frame: pd.DataFrame, value: str | None, label: str, *, required: bool = False) -> str | None:
    if not value:
        if required:
            raise GeographicError("COLUMN_REQUIRED", f"{label} is required.", {"field": label})
        return None
    if value not in frame.columns:
        raise GeographicError("UNKNOWN_COLUMN", f"Column '{value}' is not in the dataset.", {"column": value, "available": [str(item) for item in frame.columns]})
    return str(value)


def _aggregate(grouped, aggregation: str, measure: str | None):
    operation = aggregation.casefold()
    if operation == "count":
        return grouped.size()
    if measure is None:
        raise GeographicError("MEASURE_REQUIRED", f"Aggregation '{aggregation}' requires a metric column.")
    series = grouped[measure]
    if operation in {"average", "mean"}:
        return series.mean()
    if operation == "median":
        return series.median()
    if operation == "distinct_count":
        return series.nunique()
    if operation in {"minimum", "min"}:
        return series.min()
    if operation in {"maximum", "max"}:
        return series.max()
    return series.sum()


def _jenks_breaks(values: list[float], classes: int) -> list[float]:
    """Small deterministic Jenks implementation; values are bounded by the map budget."""
    data = sorted(values)
    classes = max(1, min(classes, len(set(data))))
    if classes <= 1:
        return [data[0], data[-1]]
    lower = [[0] * (classes + 1) for _ in range(len(data) + 1)]
    variance = [[float("inf")] * (classes + 1) for _ in range(len(data) + 1)]
    for index in range(1, classes + 1):
        lower[1][index] = 1
        variance[1][index] = 0.0
    for length in range(2, len(data) + 1):
        total = total_sq = weight = 0.0
        for offset in range(1, length + 1):
            position = length - offset + 1
            value = data[position - 1]
            weight += 1
            total += value
            total_sq += value * value
            current_variance = total_sq - total * total / weight
            previous = position - 1
            if previous:
                for group in range(2, classes + 1):
                    candidate = current_variance + variance[previous][group - 1]
                    if candidate < variance[length][group]:
                        lower[length][group] = position
                        variance[length][group] = candidate
        lower[length][1] = 1
        variance[length][1] = current_variance
    breaks = [data[0]] + [0.0] * (classes - 1) + [data[-1]]
    index = len(data)
    for group in range(classes, 1, -1):
        position = int(lower[index][group]) - 2
        breaks[group - 1] = data[max(position, 0)]
        index = int(lower[index][group] - 1)
    return breaks


def _classification(values: list[float], options: dict[str, Any]) -> dict[str, Any]:
    if not values:
        return {"method": options.get("classification", "quantile"), "breaks": [], "classes": 0}
    method = str(options.get("classification") or "quantile").casefold()
    if method not in CLASSIFICATIONS:
        raise GeographicError("INVALID_CLASSIFICATION", "Unsupported classification method.", {"available": list(CLASSIFICATIONS)})
    classes = max(2, min(7, int(options.get("classes") or 5), len(set(values)))) if len(set(values)) > 1 else 1
    low = _clean_number(options.get("minimum"))
    high = _clean_number(options.get("maximum"))
    work = [value for value in values if (low is None or value >= low) and (high is None or value <= high)] or values
    if method == "custom":
        requested = options.get("custom_breaks") or []
        try:
            breaks = sorted(set(float(value) for value in requested))
        except (TypeError, ValueError) as exc:
            raise GeographicError("INVALID_BREAKS", "Custom breaks must be numeric.") from exc
        if len(breaks) < 2:
            raise GeographicError("INVALID_BREAKS", "Custom classification needs at least two break values.")
    elif classes == 1:
        breaks = [min(work), max(work)]
    elif method == "equal_interval":
        breaks = np.linspace(min(work), max(work), classes + 1).tolist()
    elif method == "natural_breaks":
        breaks = _jenks_breaks(work, classes)
    else:
        breaks = np.quantile(work, np.linspace(0, 1, classes + 1)).tolist()
    breaks = [float(value) for value in dict.fromkeys(breaks)]
    return {"method": method, "breaks": breaks, "classes": max(1, len(breaks) - 1)}


def _class_index(value: float | None, breaks: list[float]) -> int | None:
    if value is None or not breaks:
        return None
    for index, upper in enumerate(breaks[1:]):
        if value <= upper:
            return index
    return max(0, len(breaks) - 2)


def _palette(options: dict[str, Any], class_count: int) -> list[str]:
    name = str(options.get("palette") or "blue").casefold()
    colors = list(PALETTES.get(name, PALETTES["blue"]))
    if class_count > len(colors):
        positions = np.linspace(0, len(colors) - 1, class_count)
        colors = [colors[round(position)] for position in positions]
    else:
        colors = colors[:max(1, class_count)]
    if bool(options.get("reverse_palette")):
        colors.reverse()
    return colors


def _point_features(frame: pd.DataFrame, options: dict[str, Any], map_type: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    profile = geographic_profile(frame)
    latitude = _validated_column(frame, options.get("latitude_column") or _first(profile, "geo_latitude"), "latitude_column", required=True)
    longitude = _validated_column(frame, options.get("longitude_column") or _first(profile, "geo_longitude"), "longitude_column", required=True)
    measure = _validated_column(frame, options.get("measure_column"), "measure_column")
    category = _validated_column(frame, options.get("category_column"), "category_column")
    tooltip_fields = [str(item) for item in (options.get("tooltip_fields") or [])][:8]
    for field in tooltip_fields:
        _validated_column(frame, field, "tooltip_fields")
    features = []
    invalid = 0
    for _, row in frame.head(20_000).iterrows():
        lat = _clean_number(row[latitude])
        lon = _clean_number(row[longitude])
        if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            invalid += 1
            continue
        value = _clean_number(row[measure]) if measure else 1.0
        properties: dict[str, Any] = {"value": value, "category": str(row[category]) if category and pd.notna(row[category]) else None}
        for field in tooltip_fields:
            raw = row[field]
            properties[field] = None if pd.isna(raw) else (raw.item() if hasattr(raw, "item") else raw)
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": properties})
    if not features:
        raise GeographicError("NO_VALID_COORDINATES", "No rows contain valid latitude/longitude pairs.")
    return features, {"latitude_column": latitude, "longitude_column": longitude, "measure_column": measure, "category_column": category, "invalid_coordinate_rows": invalid}


def _best_boundary_property(collection: dict[str, Any], dataset_values: Iterable[Any], requested: str | None) -> str:
    metadata = validate_feature_collection(collection)
    properties = metadata["property_names"]
    if requested:
        if requested not in properties:
            raise GeographicError("BOUNDARY_PROPERTY_NOT_FOUND", f"Boundary property '{requested}' was not found.", {"available": properties})
        return requested
    normalized_values = {normalize_geo_name(value) for value in dataset_values if normalize_geo_name(value)}
    scored = []
    for property_name in properties:
        boundary_values = {
            normalize_geo_name((feature.get("properties") or {}).get(property_name))
            for feature in collection["features"]
        }
        score = len(normalized_values & boundary_values) / max(1, len(normalized_values))
        scored.append((score, property_name))
    score, selected = max(scored, default=(0.0, ""))
    if not selected or score == 0:
        raise GeographicError("BOUNDARY_PROPERTY_REQUIRED", "Could not infer the boundary name property; select it explicitly.", {"available": properties})
    return selected


BOUNDARY_IDENTITY_PROPERTIES = (
    "name", "name_long", "name_en", "name_alt", "iso_a2", "iso_a3", "iso_n3",
    "abbrev", "postal", "formal_en", "country_code",
)


def _boundary_identity_mappings(collection: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Create collision-safe aliases from country boundary properties.

    A global country layer normally carries both names and ISO keys. Keeping the
    aliases with the registered boundary means a dataset containing ``India``,
    ``IN`` or ``IND`` can join the same layer without shipping a second, stale
    country-code table in the application.
    """
    if not any((feature.get("properties") or {}).get("iso_a3") not in (None, "", "-99", -99) for feature in collection.get("features", [])):
        return {}
    candidates: dict[str, list[dict[str, Any]]] = {}
    for feature in collection.get("features", []):
        properties = feature.get("properties") or {}
        name = next((str(properties.get(key)).strip() for key in ("name", "name_long", "name_en") if properties.get(key) not in (None, "", "-99", -99)), None)
        if not name:
            continue
        iso_a3 = str(properties.get("iso_a3") or "").strip().upper()
        canonical_id = f"WLD-{iso_a3}" if iso_a3 and iso_a3 != "-99" else f"WLD-{normalize_geo_name(name).replace(' ', '-').upper()}"
        reference = {"canonical_name": name, "canonical_id": canonical_id, "country": "WLD", "admin_level": 0}
        for key in BOUNDARY_IDENTITY_PROPERTIES:
            raw = properties.get(key)
            if raw in (None, "", "-99", -99):
                continue
            normalized = normalize_geo_name(raw)
            if normalized:
                candidates.setdefault(normalized, []).append(reference)
    return {
        normalized: items[0]
        for normalized, items in candidates.items()
        if len({(item["canonical_id"], item["canonical_name"]) for item in items}) == 1
    }


def _region_features(frame: pd.DataFrame, options: dict[str, Any], map_type: str, collection: dict[str, Any], manual_mappings: dict[str, dict[str, Any]] | None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    profile = geographic_profile(frame)
    default_geo = next((item["column"] for item in profile["columns"] if item["semantic_type"] not in {"geo_latitude", "geo_longitude", "geo_address"}), None)
    geography = _validated_column(frame, options.get("geography_column") or default_geo, "geography_column", required=True)
    measure = _validated_column(frame, options.get("measure_column"), "measure_column")
    category = _validated_column(frame, options.get("category_column"), "category_column")
    aggregation = str(options.get("aggregation") or "sum").casefold()
    if aggregation not in AGGREGATIONS:
        raise GeographicError("INVALID_AGGREGATION", "Unsupported geographic aggregation.", {"available": list(AGGREGATIONS)})
    if measure is None:
        aggregation = "count"
    work = frame[[item for item in {geography, measure, category} if item]].copy()
    if measure:
        work[measure] = pd.to_numeric(work[measure], errors="coerce")
    grouped = work.dropna(subset=[geography]).groupby(geography, dropna=False)
    values = _aggregate(grouped, aggregation, measure)
    dominant = grouped[category].agg(lambda item: item.dropna().astype(str).mode().iloc[0] if not item.dropna().empty else None) if category else None
    country = str(options.get("country") or profile.get("country_context") or "") or None
    admin_level = options.get("admin_level")
    boundary_mappings = _boundary_identity_mappings(collection)
    scoped_mappings = {**boundary_mappings, **(manual_mappings or {})}
    dataset_lookup: dict[str, dict[str, Any]] = {}
    resolution_statuses: dict[str, int] = {}
    for raw_name, raw_value in values.items():
        resolution = resolve_geography(raw_name, country=country, admin_level=admin_level, manual_mappings=scoped_mappings)
        key = resolution.get("canonical_id") or normalize_geo_name(raw_name)
        resolution_statuses[resolution["status"]] = resolution_statuses.get(resolution["status"], 0) + 1
        dataset_lookup[str(key)] = {
            "name": str(raw_name), "value": _clean_number(raw_value), "category": dominant.get(raw_name) if dominant is not None else None,
        }
    boundary_property = _best_boundary_property(collection, values.index, options.get("boundary_property"))
    output = deepcopy(collection)
    matched_values = []
    matched = 0
    for feature in output["features"]:
        raw_boundary_name = (feature.get("properties") or {}).get(boundary_property)
        resolution = resolve_geography(raw_boundary_name, country=country, admin_level=admin_level, manual_mappings=scoped_mappings)
        key = str(resolution.get("canonical_id") or normalize_geo_name(raw_boundary_name))
        item = dataset_lookup.get(key) or dataset_lookup.get(normalize_geo_name(raw_boundary_name))
        properties = feature.setdefault("properties", {})
        properties["region"] = raw_boundary_name
        properties["metric"] = item["value"] if item else None
        properties["category"] = item["category"] if item else None
        if item and item["value"] is not None:
            matched += 1
            matched_values.append(float(item["value"]))
    classification = _classification(matched_values, options)
    total = sum(matched_values)
    ordered = sorted(set(matched_values), reverse=True)
    for feature in output["features"]:
        value = _clean_number((feature.get("properties") or {}).get("metric"))
        properties = feature["properties"]
        properties["class_index"] = _class_index(value, classification["breaks"])
        properties["rank"] = ordered.index(value) + 1 if value in ordered else None
        properties["percentage"] = value / total * 100 if value is not None and total else None
    matching = {
        "dataset_regions": len(dataset_lookup),
        "boundary_features": len(output["features"]),
        "matched_features": matched,
        "unmatched_features": len(output["features"]) - matched,
        "resolution_statuses": resolution_statuses,
        "boundary_property": boundary_property,
    }
    mapping = {"geography_column": geography, "measure_column": measure, "category_column": category, "aggregation": aggregation}
    return output, mapping, {"classification": classification, "matching": matching}


def build_geographic_map(
    frame: pd.DataFrame,
    options: dict[str, Any] | None = None,
    *,
    boundary_geojson: dict[str, Any] | None = None,
    manual_mappings: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    options = dict(options or {})
    recommendations = recommend_maps(frame)
    requested = str(options.get("map_type") or "auto").casefold()
    if requested == "auto":
        ready = next((item for item in recommendations["recommended"] if item["implementation_status"] in {"ready", "ready_with_boundary"} and (item["implementation_status"] == "ready" or boundary_geojson)), None)
        if ready is None:
            raise GeographicError("NO_MAP_RECOMMENDATION", "No usable geographic field combination was detected.", {"profile": recommendations["profile"]})
        map_type = ready["type"]
    else:
        map_type = requested
    if map_type in ADVANCED_MAP_TYPES:
        raise GeographicError("MAP_TYPE_EXTENSION", f"'{map_type}' is an advanced extension point; choose a core map type for this build.", {"core_map_types": list(CORE_MAP_TYPES)})
    if map_type not in CORE_MAP_TYPES:
        raise GeographicError("UNKNOWN_MAP_TYPE", "Unsupported map type.", {"available": list(CORE_MAP_TYPES)})

    classification = {"method": None, "breaks": [], "classes": 0}
    matching = None
    if map_type in {"point", "bubble", "heatmap", "cluster"}:
        features, mapping = _point_features(frame, options, map_type)
        geojson = {"type": "FeatureCollection", "features": features}
    else:
        if boundary_geojson is None:
            raise GeographicError("BOUNDARY_REQUIRED", "A registered GeoJSON boundary is required for region maps.")
        geojson, mapping, region_details = _region_features(frame, options, map_type, boundary_geojson, manual_mappings)
        classification = region_details["classification"]
        matching = region_details["matching"]
    colors = _palette(options, classification["classes"] or 5)
    result = {
        "map_id": str(uuid4()),
        "map_type": map_type,
        "title": str(options.get("title") or map_type.replace("_", " ").title()),
        "source": {"row_count": int(len(frame)), "column_count": int(len(frame.columns))},
        "field_mapping": mapping,
        "style": {
            "palette": str(options.get("palette") or "blue"), "colors": colors,
            "reverse_palette": bool(options.get("reverse_palette")),
            "opacity": max(0.1, min(1.0, float(options.get("opacity") or 0.82))),
            "radius": max(4, min(60, int(options.get("radius") or 18))),
            "border_color": str(options.get("border_color") or "#ffffff"),
            "border_width": max(0, min(5, float(options.get("border_width") or 1))),
            "missing_color": str(options.get("missing_color") or "#d1d5db"),
            "zero_color": str(options.get("zero_color") or "#f3f4f6"),
            "prefix": str(options.get("prefix") or ""), "suffix": str(options.get("suffix") or ""),
        },
        "classification": classification,
        "matching": matching,
        "geography_scope": {
            "country_code": str(options.get("country") or geographic_profile(frame).get("country_context") or "").upper() or None,
            "india_only": str(options.get("country") or geographic_profile(frame).get("country_context") or "").upper() == INDIA_COUNTRY_CODE,
        },
        "geojson": geojson,
        "bbox": boundary_bbox(geojson),
        "recommendations": recommendations["recommended"],
    }
    return result


def geographic_eda(frame: pd.DataFrame) -> dict[str, Any]:
    profile = geographic_profile(frame)
    recommendations = recommend_maps(frame)["recommended"]
    columns = []
    for item in profile["columns"]:
        series = frame[item["column"]]
        details = {**item, "non_null_count": int(series.notna().sum()), "missing_count": int(series.isna().sum())}
        if item["semantic_type"] == "geo_latitude":
            numeric = pd.to_numeric(series, errors="coerce")
            details["valid_range_ratio"] = round(float(numeric.between(-90, 90).mean()), 4)
        elif item["semantic_type"] == "geo_longitude":
            numeric = pd.to_numeric(series, errors="coerce")
            details["valid_range_ratio"] = round(float(numeric.between(-180, 180).mean()), 4)
        columns.append(details)
    return {
        "status": "ready" if columns else "no_geography_detected",
        "row_count": int(len(frame)),
        "country_context": profile["country_context"],
        "geographic_columns": columns,
        "recommendations": recommendations,
    }


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    first, second = math.radians(lat1), math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    value = math.sin(delta_lat / 2) ** 2 + math.cos(first) * math.cos(second) * math.sin(delta_lon / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def location_analytics(frame: pd.DataFrame, options: dict[str, Any] | None = None) -> dict[str, Any]:
    options = dict(options or {})
    profile = geographic_profile(frame)
    latitude = _validated_column(frame, options.get("latitude_column") or _first(profile, "geo_latitude"), "latitude_column", required=True)
    longitude = _validated_column(frame, options.get("longitude_column") or _first(profile, "geo_longitude"), "longitude_column", required=True)
    points = frame[[latitude, longitude]].apply(pd.to_numeric, errors="coerce").dropna()
    points = points[points[latitude].between(-90, 90) & points[longitude].between(-180, 180)]
    if points.empty:
        raise GeographicError("NO_VALID_COORDINATES", "No valid locations are available for location analytics.")
    centroid = {"latitude": float(points[latitude].mean()), "longitude": float(points[longitude].mean())}
    distances = [_haversine_km(centroid["latitude"], centroid["longitude"], float(row[latitude]), float(row[longitude])) for _, row in points.iterrows()]
    return {
        "location_count": int(len(points)),
        "invalid_location_count": int(len(frame) - len(points)),
        "centroid": centroid,
        "bounds": {"south": float(points[latitude].min()), "west": float(points[longitude].min()), "north": float(points[latitude].max()), "east": float(points[longitude].max())},
        "distance_from_centroid_km": {"median": round(float(np.median(distances)), 3), "p90": round(float(np.quantile(distances, 0.9)), 3), "maximum": round(float(max(distances)), 3)},
    }


def geographic_catalog() -> dict[str, Any]:
    return {
        "product": "Geographic Intelligence",
        "sections": ["Quick Map", "Map Studio", "Geographic EDA", "Territory Builder", "Location Analytics", "Map Templates", "Saved Maps"],
        "core_map_types": list(CORE_MAP_TYPES),
        "advanced_extension_types": list(ADVANCED_MAP_TYPES),
        "aggregations": list(AGGREGATIONS),
        "classifications": list(CLASSIFICATIONS),
        "palettes": PALETTES,
        "templates": list(MAP_TEMPLATES),
        "interactive_renderer": "MapLibre GL JS",
        "boundary_formats": ["GeoJSON"],
        "boundary_extension_formats": ["TopoJSON", "Shapefile", "GeoPackage"],
        "global_country_maps": {
            "boundary_scope": "country-level worldwide",
            "country_code": "WLD",
            "admin_level": 0,
            "join_keys": ["country name", "ISO 3166-1 alpha-2", "ISO 3166-1 alpha-3", "numeric ISO code"],
            "bootstrap_endpoints": [
                "/api/v1/geographic/boundaries/bootstrap/world",
                "/api/v1/geographic/boundaries/bootstrap/global",
            ],
            "source": "Natural Earth Admin 0 Countries 50m",
            "license": "Public domain - Natural Earth Terms of Use",
        },
        "india_boundary": {
            **INDIA_OFFICIAL_BOUNDARY_SOURCE,
            "import_endpoint": "/api/v1/geographic/boundaries/import/official-india",
            "selection_rule": "India datasets must use an IN admin-1/admin-2 boundary; WLD/global boundaries are rejected.",
        },
    }
