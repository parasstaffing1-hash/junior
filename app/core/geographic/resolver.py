"""Safe, deterministic geographic name normalization and resolution."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Iterable

from rapidfuzz import fuzz, process


@dataclass(frozen=True)
class GeoReference:
    canonical_id: str
    name: str
    country: str
    admin_level: int
    aliases: tuple[str, ...] = ()


INDIA_STATES = (
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat",
    "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh",
    "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab",
    "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh",
    "Uttarakhand", "West Bengal", "Andaman and Nicobar Islands", "Chandigarh",
    "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Jammu and Kashmir", "Ladakh",
    "Lakshadweep", "Puducherry",
)

STATE_ALIASES = {
    "Odisha": ("Orissa",),
    "Uttarakhand": ("Uttaranchal",),
    "Puducherry": ("Pondicherry",),
    "Delhi": ("NCT of Delhi", "National Capital Territory of Delhi"),
    "Andaman and Nicobar Islands": ("A and N Islands", "Andaman Nicobar"),
    "Dadra and Nagar Haveli and Daman and Diu": ("DNHDD", "Dadra Nagar Haveli Daman Diu"),
}

REFERENCES: tuple[GeoReference, ...] = (
    GeoReference("IN", "India", "IN", 0, ("Republic of India", "Bharat")),
    *(GeoReference(f"IN-ADM1-{re.sub(r'[^A-Z0-9]+', '-', name.upper()).strip('-')}", name, "IN", 1, STATE_ALIASES.get(name, ())) for name in INDIA_STATES),
    GeoReference("IN-KA-BENGALURU-URBAN", "Bengaluru Urban", "IN", 2, ("Bangalore", "Bangalore Urban", "Bengaluru")),
    GeoReference("IN-MH-MUMBAI", "Mumbai", "IN", 2, ("Bombay", "Mumbai City")),
    GeoReference("IN-WB-KOLKATA", "Kolkata", "IN", 2, ("Calcutta",)),
    GeoReference("IN-TN-CHENNAI", "Chennai", "IN", 2, ("Madras",)),
    GeoReference("IN-MH-PUNE", "Pune", "IN", 2, ("Poona",)),
    GeoReference("IN-UP-PRAYAGRAJ", "Prayagraj", "IN", 2, ("Allahabad",)),
    GeoReference("IN-MP-BHOPAL", "Bhopal", "IN", 2),
    GeoReference("IN-TG-HYDERABAD", "Hyderabad", "IN", 2),
)


def normalize_geo_name(value: Any) -> str:
    """Normalize spelling presentation without guessing a geographic identity."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _candidate_references(country: str | None, admin_level: int | None) -> list[GeoReference]:
    country_code = str(country or "").strip().upper()
    return [
        item for item in REFERENCES
        if (not country_code or item.country == country_code)
        and (admin_level is None or item.admin_level == int(admin_level))
    ]


def _public_result(
    input_value: Any,
    reference: GeoReference | None,
    *,
    status: str,
    confidence: float,
    method: str,
    candidates: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    return {
        "input": str(input_value),
        "canonical_name": reference.name if reference else None,
        "canonical_id": reference.canonical_id if reference else None,
        "country": reference.country if reference else None,
        "admin_level": reference.admin_level if reference else None,
        "confidence": round(float(confidence), 3),
        "status": status,
        "method": method,
        "candidates": list(candidates),
    }


def resolve_geography(
    value: Any,
    *,
    country: str | None = None,
    admin_level: int | None = None,
    manual_mappings: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve a value conservatively; unsafe fuzzy guesses remain ambiguous."""
    normalized = normalize_geo_name(value)
    if not normalized:
        return _public_result(value, None, status="UNMATCHED", confidence=0.0, method="empty")

    manual = (manual_mappings or {}).get(normalized)
    if manual:
        reference = GeoReference(
            str(manual.get("canonical_id") or f"MANUAL-{normalized}"),
            str(manual["canonical_name"]),
            str(manual.get("country") or country or "").upper(),
            int(manual.get("admin_level") if manual.get("admin_level") is not None else (admin_level or 0)),
        )
        return _public_result(value, reference, status="EXACT", confidence=1.0, method="manual_mapping")

    candidates = _candidate_references(country, admin_level)
    canonical = {normalize_geo_name(item.name): item for item in candidates}
    aliases = {
        normalize_geo_name(alias): item
        for item in candidates
        for alias in item.aliases
    }
    if normalized in canonical:
        return _public_result(value, canonical[normalized], status="EXACT", confidence=1.0, method="exact")
    if normalized in aliases:
        return _public_result(value, aliases[normalized], status="ALIAS", confidence=0.96, method="alias")

    lookup: dict[str, GeoReference] = {**canonical, **aliases}
    if not lookup:
        return _public_result(value, None, status="UNMATCHED", confidence=0.0, method="no_reference")
    ranked = process.extract(normalized, list(lookup), scorer=fuzz.WRatio, limit=3)
    suggestions = []
    for name, score, _ in ranked:
        reference = lookup[name]
        suggestions.append({
            "canonical_name": reference.name,
            "canonical_id": reference.canonical_id,
            "score": round(float(score) / 100, 3),
        })
    best_name, best_score, _ = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0
    best = lookup[best_name]
    if best_score >= 92 and best_score - second_score >= 6:
        return _public_result(value, best, status="FUZZY_HIGH_CONFIDENCE", confidence=best_score / 100, method="fuzzy", candidates=suggestions)
    if best_score >= 75:
        return _public_result(value, None, status="AMBIGUOUS", confidence=best_score / 100, method="fuzzy_review_required", candidates=suggestions)
    return _public_result(value, None, status="UNMATCHED", confidence=best_score / 100, method="fuzzy_below_threshold", candidates=suggestions)


def reference_names(*, country: str | None = None, admin_level: int | None = None) -> set[str]:
    values: set[str] = set()
    for item in _candidate_references(country, admin_level):
        values.add(normalize_geo_name(item.name))
        values.update(normalize_geo_name(alias) for alias in item.aliases)
    return values
