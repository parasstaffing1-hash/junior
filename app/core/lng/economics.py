from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


class LNGEconomicsError(ValueError):
    pass


def _d(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise LNGEconomicsError(f"{field} must be numeric.") from exc
    if not result.is_finite():
        raise LNGEconomicsError(f"{field} must be finite.")
    return result


def calculate_delivered_cost(
    *,
    base_price_per_mmbtu: Any,
    energy_mmbtu: Any | None = None,
    buyer_price_per_mmbtu: Any | None = None,
    costs_per_mmbtu: dict[str, Any | None] | None = None,
) -> dict[str, Any]:
    """Calculate known delivered cost while preserving unknown cost components."""
    base = _d(base_price_per_mmbtu, "base_price_per_mmbtu")
    known_costs: dict[str, float] = {}
    unknown_costs: list[str] = []
    incremental = Decimal("0")

    for name, value in (costs_per_mmbtu or {}).items():
        if value is None:
            unknown_costs.append(name)
            continue
        amount = _d(value, name)
        known_costs[name] = float(amount)
        incremental += amount

    delivered = base + incremental
    gross_spread = None
    total_gross_spread = None
    buyer = None
    if buyer_price_per_mmbtu is not None:
        buyer = _d(buyer_price_per_mmbtu, "buyer_price_per_mmbtu")
        gross_spread = buyer - delivered
    total_cost = None
    if energy_mmbtu is not None:
        energy = _d(energy_mmbtu, "energy_mmbtu")
        if energy < 0:
            raise LNGEconomicsError("energy_mmbtu cannot be negative.")
        total_cost = delivered * energy
        if gross_spread is not None:
            total_gross_spread = gross_spread * energy

    return {
        "base_price_per_mmbtu": float(base),
        "known_costs_per_mmbtu": known_costs,
        "unknown_costs": unknown_costs,
        "known_delivered_cost_per_mmbtu": float(delivered),
        "buyer_price_per_mmbtu": None if buyer is None else float(buyer),
        "potential_gross_spread_per_mmbtu": None if gross_spread is None else float(gross_spread),
        "total_known_delivered_cost": None if total_cost is None else float(total_cost),
        "potential_total_gross_spread": None if total_gross_spread is None else float(total_gross_spread),
        "complete_cost_picture": len(unknown_costs) == 0,
        "profitability_label": "POTENTIAL_GROSS_SPREAD_ONLY",
    }


def calculate_brokerage(
    *,
    basis: str,
    rate: Any,
    energy_mmbtu: Any | None = None,
    volume_mt: Any | None = None,
    transaction_value: Any | None = None,
) -> dict[str, Any]:
    kind = basis.strip().upper()
    commission_rate = _d(rate, "rate")

    if kind in {"$/MMBTU", "USD/MMBTU", "PER_MMBTU"}:
        if energy_mmbtu is None:
            raise LNGEconomicsError("energy_mmbtu is required for $/MMBtu commission.")
        volume_basis = _d(energy_mmbtu, "energy_mmbtu")
        commission = commission_rate * volume_basis
        unit = "USD/MMBtu"
    elif kind in {"$/MT", "USD/MT", "PER_MT"}:
        if volume_mt is None:
            raise LNGEconomicsError("volume_mt is required for $/MT commission.")
        volume_basis = _d(volume_mt, "volume_mt")
        commission = commission_rate * volume_basis
        unit = "USD/MT"
    elif kind in {"PERCENT", "PERCENTAGE", "%"}:
        if transaction_value is None:
            raise LNGEconomicsError("transaction_value is required for percentage commission.")
        volume_basis = _d(transaction_value, "transaction_value")
        commission = volume_basis * commission_rate / Decimal("100")
        unit = "%"
    elif kind in {"FIXED", "FIXED_AMOUNT"}:
        volume_basis = Decimal("1")
        commission = commission_rate
        unit = "USD fixed"
    else:
        raise LNGEconomicsError(f"Unsupported commission basis: {basis}")

    return {
        "commission_basis": kind,
        "commission_rate": float(commission_rate),
        "commission_unit": unit,
        "volume_basis": float(volume_basis),
        "estimated_commission": float(commission),
    }
