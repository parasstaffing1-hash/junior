from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


class LNGPricingError(ValueError):
    pass


def _d(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise LNGPricingError(f"{field} must be numeric.") from exc
    if not result.is_finite():
        raise LNGPricingError(f"{field} must be finite.")
    return result


def evaluate_price_formula(
    *,
    formula_type: str,
    benchmark_value: Any | None = None,
    coefficient: Any = 1,
    constant: Any = 0,
    premium_discount: Any = 0,
    fixed_price: Any | None = None,
    floor: Any | None = None,
    ceiling: Any | None = None,
    benchmark_name: str | None = None,
    currency: str = "USD",
    unit: str = "MMBtu",
) -> dict[str, Any]:
    """Evaluate a deterministic LNG commercial price expression.

    The function never fetches or invents benchmark prices. A caller must supply
    the benchmark value for benchmark-linked structures.
    """
    kind = formula_type.strip().upper()
    coeff = _d(coefficient, "coefficient")
    const = _d(constant, "constant")
    pd = _d(premium_discount, "premium_discount")

    if kind == "FIXED":
        if fixed_price is None:
            raise LNGPricingError("fixed_price is required for FIXED pricing.")
        raw = _d(fixed_price, "fixed_price")
        expression = "fixed_price"
    elif kind in {"INDEX_PLUS", "INDEX_MINUS", "INDEX_MULTIPLIER", "BRENT_SLOPE", "CUSTOM_LINEAR"}:
        if benchmark_value is None:
            raise LNGPricingError("benchmark_value is required for benchmark-linked pricing.")
        benchmark = _d(benchmark_value, "benchmark_value")
        if kind == "INDEX_PLUS":
            raw = benchmark + pd + const
            expression = "benchmark + premium_discount + constant"
        elif kind == "INDEX_MINUS":
            raw = benchmark - abs(pd) + const
            expression = "benchmark - abs(premium_discount) + constant"
        else:
            raw = (benchmark * coeff) + const + pd
            expression = "benchmark * coefficient + constant + premium_discount"
    else:
        raise LNGPricingError(f"Unsupported formula_type: {formula_type}")

    applied_floor = False
    applied_ceiling = False
    price = raw
    if floor is not None:
        floor_value = _d(floor, "floor")
        if price < floor_value:
            price = floor_value
            applied_floor = True
    if ceiling is not None:
        ceiling_value = _d(ceiling, "ceiling")
        if price > ceiling_value:
            price = ceiling_value
            applied_ceiling = True
    if floor is not None and ceiling is not None and _d(floor, "floor") > _d(ceiling, "ceiling"):
        raise LNGPricingError("floor cannot be greater than ceiling.")

    return {
        "formula_type": kind,
        "benchmark_name": benchmark_name,
        "benchmark_value": None if benchmark_value is None else float(_d(benchmark_value, "benchmark_value")),
        "coefficient": float(coeff),
        "constant": float(const),
        "premium_discount": float(pd),
        "raw_price": float(raw),
        "price": float(price),
        "currency": currency,
        "unit": unit,
        "expression": expression,
        "floor_applied": applied_floor,
        "ceiling_applied": applied_ceiling,
    }
