"""Deterministic PII discovery and reversible-in-memory masking helpers."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd


PATTERNS = {
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
    "phone": re.compile(r"^\+?[0-9() .-]{7,}$"),
    "ssn": re.compile(r"^\d{3}-\d{2}-\d{4}$"),
    "credit_card": re.compile(r"^(?:\d[ -]?){13,19}$"),
}
NAME_HINTS = {
    "email": ("email", "e_mail", "mail"),
    "phone": ("phone", "mobile", "telephone", "tel"),
    "ssn": ("ssn", "social_security"),
    "credit_card": ("card_number", "credit_card", "cc_number"),
    "name": ("full_name", "customer_name", "person_name"),
    "address": ("address", "street", "postal", "zip", "pincode", "pin_code"),
    "date_of_birth": ("dob", "birth_date", "date_of_birth"),
}


def _name_hint(column: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(column).casefold()).strip("_")
    for kind, hints in NAME_HINTS.items():
        if any(hint in normalized for hint in hints):
            return kind
    return None


def classify_frame(frame: pd.DataFrame, *, sample_size: int = 250) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for column in frame.columns:
        series = frame[column].dropna().astype(str).head(max(1, sample_size))
        hint = _name_hint(str(column))
        detected = hint
        confidence = 0.75 if hint else 0.0
        if not detected and len(series):
            for kind, pattern in PATTERNS.items():
                matches = int(series.map(lambda value: bool(pattern.match(value.strip()))).sum())
                ratio = matches / len(series)
                if ratio >= 0.8:
                    detected, confidence = kind, round(min(0.99, ratio), 3)
                    break
        classification = detected or "none"
        findings.append({
            "column": str(column),
            "classification": classification,
            "confidence": confidence,
            "sampled_values": int(len(series)),
            "action": "mask_or_restrict" if detected else "none",
        })
    return {
        "columns": findings,
        "pii_columns": [item["column"] for item in findings if item["classification"] != "none"],
        "policy": "PII must be masked or access-restricted before external export.",
    }


def mask_frame(frame: pd.DataFrame, classifications: dict[str, Any] | None = None) -> pd.DataFrame:
    result = frame.copy()
    profile = classifications or classify_frame(frame)
    for item in profile.get("columns", []):
        if item.get("classification") == "none":
            continue
        column = item["column"]
        if column not in result.columns:
            continue
        result[column] = result[column].map(lambda value: None if pd.isna(value) else "[REDACTED]")
    return result
