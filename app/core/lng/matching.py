from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.core.lng.enums import CompatibilityStatus


def _as_date(value: Any | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _range_overlap(a_start: date | None, a_end: date | None, b_start: date | None, b_end: date | None) -> bool | None:
    if None in {a_start, a_end, b_start, b_end}:
        return None
    return max(a_start, b_start) <= min(a_end, b_end)


def match_cargo_to_rfq(rfq: dict[str, Any], cargo: dict[str, Any]) -> dict[str, Any]:
    """Run deterministic commercial hard filters and a transparent soft score.

    Critical unknowns are never treated as PASS. Compliance BLOCKED and known
    incompatibilities reject the opportunity regardless of the soft score.
    """
    checks: list[dict[str, Any]] = []

    compliance = str(cargo.get("compliance_status") or "INSUFFICIENT DATA").upper()
    if compliance == "BLOCKED":
        checks.append({"check": "compliance", "status": CompatibilityStatus.FAIL, "reason": "Cargo/counterparty compliance status is BLOCKED."})
    elif compliance == "CLEAR":
        checks.append({"check": "compliance", "status": CompatibilityStatus.PASS, "reason": "Compliance status is CLEAR."})
    else:
        checks.append({"check": "compliance", "status": CompatibilityStatus.UNKNOWN, "reason": "Compliance clearance is not established."})

    overlap = _range_overlap(
        _as_date(rfq.get("delivery_window_start")),
        _as_date(rfq.get("delivery_window_end")),
        _as_date(cargo.get("delivery_window_start") or cargo.get("loading_window_start")),
        _as_date(cargo.get("delivery_window_end") or cargo.get("loading_window_end")),
    )
    if overlap is True:
        checks.append({"check": "window", "status": CompatibilityStatus.PASS, "reason": "Commercial windows overlap."})
    elif overlap is False:
        checks.append({"check": "window", "status": CompatibilityStatus.FAIL, "reason": "Required and available windows do not overlap."})
    else:
        checks.append({"check": "window", "status": CompatibilityStatus.UNKNOWN, "reason": "Insufficient window data."})

    rfq_basis = str(rfq.get("delivery_basis") or "").upper()
    cargo_basis = str(cargo.get("delivery_basis") or "").upper()
    if not rfq_basis or not cargo_basis:
        checks.append({"check": "delivery_basis", "status": CompatibilityStatus.UNKNOWN, "reason": "Delivery basis is incomplete."})
    elif rfq_basis == cargo_basis:
        checks.append({"check": "delivery_basis", "status": CompatibilityStatus.PASS, "reason": "Delivery bases match."})
    elif rfq_basis == "DES" and cargo_basis == "FOB" and cargo.get("viable_shipping_solution") is True:
        checks.append({"check": "delivery_basis", "status": CompatibilityStatus.WARNING, "reason": "FOB cargo can only satisfy DES RFQ through the supplied viable shipping solution."})
    else:
        checks.append({"check": "delivery_basis", "status": CompatibilityStatus.FAIL, "reason": f"RFQ requires {rfq_basis}; cargo is {cargo_basis}."})

    terminal_status = str(cargo.get("terminal_compatibility") or "INSUFFICIENT_DATA").upper().replace(" ", "_")
    if terminal_status == "COMPATIBLE":
        checks.append({"check": "terminal", "status": CompatibilityStatus.PASS, "reason": "Terminal compatibility is confirmed."})
    elif terminal_status == "CONDITIONALLY_COMPATIBLE":
        checks.append({"check": "terminal", "status": CompatibilityStatus.WARNING, "reason": "Terminal compatibility has conditions."})
    elif terminal_status == "INCOMPATIBLE":
        checks.append({"check": "terminal", "status": CompatibilityStatus.FAIL, "reason": "Terminal compatibility is INCOMPATIBLE."})
    else:
        checks.append({"check": "terminal", "status": CompatibilityStatus.UNKNOWN, "reason": "Terminal compatibility is not established."})

    quality_status = str(cargo.get("quality_compatibility") or "UNKNOWN").upper()
    if quality_status in {"PASS", "COMPATIBLE"}:
        checks.append({"check": "quality", "status": CompatibilityStatus.PASS, "reason": "Quality is compatible with the stated requirement."})
    elif quality_status in {"FAIL", "INCOMPATIBLE"}:
        checks.append({"check": "quality", "status": CompatibilityStatus.FAIL, "reason": "Cargo quality fails a mandatory requirement."})
    elif quality_status in {"WARNING", "CONDITIONALLY_COMPATIBLE"}:
        checks.append({"check": "quality", "status": CompatibilityStatus.WARNING, "reason": "Quality compatibility is conditional."})
    else:
        checks.append({"check": "quality", "status": CompatibilityStatus.UNKNOWN, "reason": "Quality compatibility is not established."})

    rfq_volume = rfq.get("cargo_volume_mt")
    cargo_volume = cargo.get("volume_mt")
    if rfq_volume is None or cargo_volume is None:
        checks.append({"check": "volume", "status": CompatibilityStatus.UNKNOWN, "reason": "Cargo volume comparison is incomplete."})
    else:
        tolerance = float(rfq.get("volume_tolerance_pct", 10.0)) / 100.0
        required = float(rfq_volume)
        available = float(cargo_volume)
        lower, upper = required * (1 - tolerance), required * (1 + tolerance)
        status = CompatibilityStatus.PASS if lower <= available <= upper else CompatibilityStatus.FAIL
        checks.append({"check": "volume", "status": status, "reason": f"Available volume {available:g} MT vs required {required:g} MT with {tolerance * 100:g}% tolerance."})

    statuses = [str(check["status"]) for check in checks]
    hard_fail = CompatibilityStatus.FAIL in statuses
    critical_unknowns = [check["check"] for check in checks if str(check["status"]) == CompatibilityStatus.UNKNOWN]
    pass_count = sum(1 for status in statuses if status == CompatibilityStatus.PASS)
    warning_count = sum(1 for status in statuses if status == CompatibilityStatus.WARNING)
    denominator = len(checks) or 1
    score = round(max(0.0, min(100.0, ((pass_count + (warning_count * 0.5)) / denominator) * 100.0)), 1)

    if hard_fail:
        overall = "REJECT"
    elif critical_unknowns:
        overall = "REVIEW_REQUIRED"
    else:
        overall = "PASS_WITH_WARNINGS" if warning_count else "PASS"

    return {
        "overall_status": overall,
        "actionable": overall in {"PASS", "PASS_WITH_WARNINGS"},
        "score": score,
        "checks": checks,
        "critical_unknowns": critical_unknowns,
    }
