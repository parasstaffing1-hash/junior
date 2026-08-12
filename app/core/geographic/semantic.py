"""Deterministic geographic semantic detection with evidence and confidence."""

from __future__ import annotations

import re
from typing import Any

from .resolver import INDIA_STATES, normalize_geo_name, reference_names


NAME_TYPES = {
    "country": "geo_country", "country_code": "geo_country", "countrycode": "geo_country",
    "state": "geo_state", "state_code": "geo_state", "province": "geo_state",
    "district": "geo_district", "county": "geo_district", "municipality": "geo_city",
    "city": "geo_city", "town": "geo_city", "village": "geo_city", "ward": "geo_custom_region",
    "postal_code": "geo_postal_code", "postcode": "geo_postal_code", "pincode": "geo_postal_code",
    "pin_code": "geo_postal_code", "zip": "geo_postal_code", "zip_code": "geo_postal_code",
    "latitude": "geo_latitude", "lat": "geo_latitude",
    "longitude": "geo_longitude", "lng": "geo_longitude", "lon": "geo_longitude", "long": "geo_longitude",
    "address": "geo_address", "street_address": "geo_address",
    "constituency": "geo_constituency", "parliamentary_constituency": "geo_constituency",
    "assembly_constituency": "geo_constituency",
    "territory": "geo_custom_region", "zone": "geo_custom_region", "sales_region": "geo_custom_region",
    "region": "geo_custom_region", "geographic_identifier": "geo_custom_region",
}

INDIA_STATE_NAMES = {normalize_geo_name(value) for value in INDIA_STATES} | reference_names(country="IN", admin_level=1)
INDIA_PIN_RE = re.compile(r"^[1-9][0-9]{5}$")
COUNTRY_CODES = {"in", "ind", "india", "us", "usa", "united states", "gb", "gbr", "united kingdom"}


def _normalized_column(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")


def _values(rows: list[dict[str, Any]], name: str) -> list[Any]:
    return [row.get(name) for row in rows if row.get(name) is not None and str(row.get(name)).strip()]


def _numeric_range_ratio(values: list[Any], lower: float, upper: float) -> float:
    converted = []
    for value in values:
        try:
            converted.append(float(value))
        except (TypeError, ValueError):
            continue
    return sum(lower <= value <= upper for value in converted) / len(values) if values else 0.0


def _match_ratio(values: list[Any], known: set[str]) -> float:
    return sum(normalize_geo_name(value) in known for value in values) / len(values) if values else 0.0


def _country_context(rows: list[dict[str, Any]], basic_schema: dict[str, Any]) -> str | None:
    for column in basic_schema.get("columns", []):
        name = str(column["name"])
        normalized = _normalized_column(name)
        values = _values(rows, name)
        rendered = {normalize_geo_name(value) for value in values}
        if normalized in {"country", "country_code"} and rendered:
            if rendered & {"india", "in", "ind", "bharat"}:
                return "IN"
            if rendered & {"united states", "us", "usa"}:
                return "US"
    for column in basic_schema.get("columns", []):
        values = _values(rows, str(column["name"]))
        if _match_ratio(values, INDIA_STATE_NAMES) >= 0.6:
            return "IN"
    return None


def detect_geographic_semantics(rows: list[dict[str, Any]], basic_schema: dict[str, Any]) -> dict[str, Any]:
    """Return only columns for which geographic evidence is present."""
    context = _country_context(rows, basic_schema)
    detected: list[dict[str, Any]] = []
    for physical in basic_schema.get("columns", []):
        name = str(physical["name"])
        normalized = _normalized_column(name)
        values = _values(rows, name)
        semantic_type = NAME_TYPES.get(normalized)
        evidence: list[str] = []
        confidence = 0.0
        country = context

        if semantic_type:
            evidence.append("column_name_match")
            confidence = 0.82
        state_ratio = _match_ratio(values, INDIA_STATE_NAMES)
        if state_ratio >= 0.6:
            semantic_type = "geo_state"
            country = "IN"
            evidence.append("known_state_values")
            confidence = max(confidence, min(0.99, 0.86 + state_ratio * 0.12))
        if semantic_type == "geo_latitude":
            valid = _numeric_range_ratio(values, -90, 90)
            evidence.append("latitude_range_valid" if valid >= 0.8 else "latitude_range_warning")
            confidence = max(confidence, 0.98 if valid >= 0.95 else 0.72)
        elif semantic_type == "geo_longitude":
            valid = _numeric_range_ratio(values, -180, 180)
            evidence.append("longitude_range_valid" if valid >= 0.8 else "longitude_range_warning")
            confidence = max(confidence, 0.98 if valid >= 0.95 else 0.72)
        elif semantic_type == "geo_postal_code" and values:
            india_pin_ratio = sum(bool(INDIA_PIN_RE.fullmatch(str(value).strip())) for value in values) / len(values)
            if india_pin_ratio >= 0.7:
                country = country or "IN"
                evidence.append("india_pincode_pattern")
                confidence = max(confidence, 0.95)
        elif semantic_type == "geo_country" and values:
            known_ratio = sum(normalize_geo_name(value) in COUNTRY_CODES for value in values) / len(values)
            if known_ratio >= 0.6:
                evidence.append("known_country_values")
                confidence = max(confidence, 0.96)
        if semantic_type and context and semantic_type not in {"geo_country", "geo_latitude", "geo_longitude"}:
            evidence.append("country_context")
            confidence = min(0.99, confidence + 0.04)
        if semantic_type:
            detected.append({
                "column": name,
                "semantic_type": semantic_type,
                "country": country,
                "confidence": round(confidence, 3),
                "evidence": list(dict.fromkeys(evidence)),
                "unique_count": int(physical.get("unique_count") or 0),
            })
    return {
        "country_context": context,
        "columns": detected,
        "by_name": {item["column"]: item for item in detected},
    }
