from __future__ import annotations

import re
from typing import Any

from app.core.geographic.semantic import detect_geographic_semantics

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_RE = re.compile(r"^[+()\-\.\s0-9]{7,30}$")
POSTAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 -]{2,11}$")
CURRENCY_RE = re.compile(r"^\s*(?:[$€£₹]|USD|INR|GBP|EUR)\s*[-+]?\s*[\d,]+(?:\.\d+)?\s*$|^\s*[-+]?\s*[\d,]+(?:\.\d+)?\s*(?:USD|INR|GBP|EUR)\s*$", re.I)
PERCENT_RE = re.compile(r"^\s*[-+]?\d+(?:\.\d+)?\s*%\s*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

IDENTIFIER_TOKENS = {"id", "uuid", "guid", "code", "key", "ref", "number", "no"}
IDENTIFIER_NAMES = {"customer_id", "employee_id", "order_id", "user_id", "account_id", "product_id", "record_id"}
COUNTRY_NAMES = {"country", "region", "state", "province", "territory", "continent", "geo", "geography"}
CURRENCY_NAMES = {"revenue", "price", "amount", "cost", "profit", "sales", "salary", "income", "spend", "fee", "balance", "budget"}
PERCENT_NAMES = {"percentage", "percent", "pct", "rate", "ratio", "conversion_rate", "margin_pct"}
PHONE_NAMES = {"phone", "phone_number", "mobile", "mobile_number", "telephone", "tel", "contact_number"}
POSTAL_NAMES = {"postal", "postal_code", "postcode", "post_code", "zip", "zip_code", "zipcode", "pin", "pincode"}
CATEGORY_NAMES = {"category", "status", "department", "gender", "segment", "type", "class", "tier", "group", "channel", "brand", "team"}


def _name_tokens(name: str) -> set[str]:
    return set(re.split(r"[^a-z0-9]+", name.casefold())) - {""}


def _non_null(rows: list[dict[str, Any]], name: str) -> list[Any]:
    return [row.get(name) for row in rows if row.get(name) is not None and str(row.get(name)).strip() != ""]


def _ratio(values: list[Any], predicate) -> float:
    if not values:
        return 0.0
    # Evaluate each distinct rendered value once, then weight by frequency.
    counts: dict[str, int] = {}
    for value in values:
        key = str(value).strip()
        counts[key] = counts.get(key, 0) + 1
    matched = sum(count for key, count in counts.items() if predicate(key))
    return matched / len(values)


def detect_semantic_schema(rows: list[dict[str, Any]], basic_schema: dict[str, Any]) -> dict[str, Any]:
    geographic = detect_geographic_semantics(rows, basic_schema)
    columns: list[dict[str, Any]] = []
    for physical in basic_schema.get("columns", []):
        name = physical["name"]
        values = _non_null(rows, name)
        lower_name = name.casefold()
        tokens = _name_tokens(name)
        semantic = None
        role = None
        confidence = 0.0
        warnings: list[str] = []
        geo = geographic["by_name"].get(name)

        if geo:
            semantic, confidence, role = geo["semantic_type"], geo["confidence"], "geography"
        elif lower_name in PHONE_NAMES:
            semantic, confidence = "phone_number", 0.95
        elif lower_name in POSTAL_NAMES:
            semantic, confidence = "postal_code", 0.94
        elif lower_name in {"email", "email_address", "e_mail", "mail"} or _ratio(values, EMAIL_RE.fullmatch) >= 0.7:
            semantic, confidence = "email", 0.98
        elif lower_name in PERCENT_NAMES or _ratio(values, PERCENT_RE.fullmatch) >= 0.8:
            semantic, confidence = "percentage", 0.94
        elif lower_name in CURRENCY_NAMES or _ratio(values, CURRENCY_RE.fullmatch) >= 0.6:
            semantic, confidence = "currency", 0.92
        elif lower_name in {"is_active", "active", "enabled", "deleted"} or ({str(v).strip().casefold() for v in values} <= {"true", "false"} if values else False):
            semantic, confidence = "boolean", 0.92
        elif lower_name == "age" or "age" in tokens:
            semantic, confidence = "age", 0.96
        elif "date" in tokens or "time" in tokens:
            date_ratio = _ratio(values, DATE_RE.fullmatch)
            if date_ratio >= 0.6:
                semantic, confidence = "date", round(max(date_ratio, 0.8), 3)
                if date_ratio < 1.0:
                    warnings.append("INCONSISTENT_DATE_STRINGS")
        elif lower_name in IDENTIFIER_NAMES or (tokens & IDENTIFIER_TOKENS and physical["unique_count"] >= max(1, len(values) * 0.8)):
            semantic, confidence = "identifier", 0.98
        elif _ratio(values, PHONE_RE.fullmatch) >= 0.8:
            semantic, confidence = "phone_number", 0.85
        elif lower_name in COUNTRY_NAMES or tokens & COUNTRY_NAMES:
            semantic, confidence, role = "category", 0.87, "country_region"
        elif lower_name in CATEGORY_NAMES or tokens & CATEGORY_NAMES:
            semantic, confidence = "category", 0.88
        elif lower_name in {"name", "full_name", "person_name", "first_name", "last_name"}:
            semantic, confidence = "person_name", 0.84
        elif physical["detected_type"] in {"integer", "float", "number", "numeric"}:
            semantic, confidence, role = "numeric_measure", 0.82, "measure"
        else:
            semantic, confidence = "free_text", 0.65 if physical["detected_type"] == "string" else 0.55

        columns.append({
            "name": name,
            "physical_type": physical["detected_type"],
            "semantic_type": semantic,
            "semantic_role": role,
            "confidence": confidence,
            "country": geo.get("country") if geo else None,
            "evidence": geo.get("evidence", []) if geo else [],
            "warnings": warnings,
        })
    return {
        "columns": columns,
        "by_name": {item["name"]: item for item in columns},
        "country_context": geographic.get("country_context"),
    }
