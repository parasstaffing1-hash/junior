from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.lng.enums import ConversionBasis


ENERGY_TO_MMBTU = {
    "MMBTU": Decimal("1"),
    "TBTU": Decimal("1000000"),
    "GJ": Decimal("0.9478171203133172"),
    "MWH": Decimal("3.412141633127942"),
}


class LNGConversionError(ValueError):
    pass


@dataclass(frozen=True)
class ConversionResult:
    input_value: Decimal
    input_unit: str
    output_value: Decimal
    output_unit: str
    conversion_method: str
    conversion_basis: str
    assumptions: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_value": float(self.input_value),
            "input_unit": self.input_unit,
            "output_value": float(self.output_value),
            "output_unit": self.output_unit,
            "conversion_method": self.conversion_method,
            "conversion_basis": self.conversion_basis,
            "assumptions": self.assumptions,
        }


def _d(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise LNGConversionError(f"{field} must be numeric.") from exc
    if not result.is_finite():
        raise LNGConversionError(f"{field} must be finite.")
    return result


def _energy_convert(value: Decimal, input_unit: str, output_unit: str) -> Decimal:
    mmbtu = value * ENERGY_TO_MMBTU[input_unit]
    return mmbtu / ENERGY_TO_MMBTU[output_unit]


def convert_lng_quantity(
    value: Any,
    input_unit: str,
    output_unit: str,
    *,
    basis: str = ConversionBasis.ACTUAL_CARGO_VALUE,
    density_mt_per_m3: Any | None = None,
    heating_value_mmbtu_per_mt: Any | None = None,
) -> dict[str, Any]:
    """Convert commercial LNG quantities while preserving all assumptions.

    Energy-only conversions are exact unit conversions. Conversions involving
    LNG mass or liquid volume require a caller-supplied cargo/contract/estimate
    assumption and never fall back to a hidden universal LNG constant.
    """
    amount = _d(value, "value")
    src = input_unit.strip().upper().replace("³", "3")
    dst = output_unit.strip().upper().replace("³", "3")
    valid_basis = ConversionBasis(basis).value

    if src == dst:
        return ConversionResult(amount, src, amount, dst, "identity", valid_basis, []).as_dict()

    if src in ENERGY_TO_MMBTU and dst in ENERGY_TO_MMBTU:
        return ConversionResult(
            amount,
            src,
            _energy_convert(amount, src, dst),
            dst,
            "exact_energy_unit",
            valid_basis,
            [],
        ).as_dict()

    assumptions: list[dict[str, Any]] = []
    mass_mt: Decimal | None = None

    if src in {"MT", "METRIC_TONNE", "METRIC_TONNES"}:
        mass_mt = amount
    elif src in {"M3", "M3_LNG", "LNG_M3"}:
        if density_mt_per_m3 is None:
            raise LNGConversionError("density_mt_per_m3 is required for LNG liquid-volume conversions.")
        density = _d(density_mt_per_m3, "density_mt_per_m3")
        if density <= 0:
            raise LNGConversionError("density_mt_per_m3 must be greater than zero.")
        mass_mt = amount * density
        assumptions.append({"name": "density_mt_per_m3", "value": float(density), "basis": valid_basis})
    elif src in ENERGY_TO_MMBTU:
        if heating_value_mmbtu_per_mt is None:
            raise LNGConversionError("heating_value_mmbtu_per_mt is required for energy-to-mass/LNG-volume conversions.")
        hv = _d(heating_value_mmbtu_per_mt, "heating_value_mmbtu_per_mt")
        if hv <= 0:
            raise LNGConversionError("heating_value_mmbtu_per_mt must be greater than zero.")
        mass_mt = (amount * ENERGY_TO_MMBTU[src]) / hv
        assumptions.append({"name": "heating_value_mmbtu_per_mt", "value": float(hv), "basis": valid_basis})
    else:
        raise LNGConversionError(f"Unsupported input unit: {input_unit}")

    if dst in {"MT", "METRIC_TONNE", "METRIC_TONNES"}:
        output = mass_mt
        method = "mass_assumption_conversion"
    elif dst in {"M3", "M3_LNG", "LNG_M3"}:
        if density_mt_per_m3 is None:
            raise LNGConversionError("density_mt_per_m3 is required for LNG liquid-volume conversions.")
        density = _d(density_mt_per_m3, "density_mt_per_m3")
        if density <= 0:
            raise LNGConversionError("density_mt_per_m3 must be greater than zero.")
        output = mass_mt / density
        if not any(a["name"] == "density_mt_per_m3" for a in assumptions):
            assumptions.append({"name": "density_mt_per_m3", "value": float(density), "basis": valid_basis})
        method = "liquid_volume_assumption_conversion"
    elif dst in ENERGY_TO_MMBTU:
        if heating_value_mmbtu_per_mt is None:
            raise LNGConversionError("heating_value_mmbtu_per_mt is required for mass/LNG-volume-to-energy conversions.")
        hv = _d(heating_value_mmbtu_per_mt, "heating_value_mmbtu_per_mt")
        if hv <= 0:
            raise LNGConversionError("heating_value_mmbtu_per_mt must be greater than zero.")
        mmbtu = mass_mt * hv
        output = mmbtu / ENERGY_TO_MMBTU[dst]
        if not any(a["name"] == "heating_value_mmbtu_per_mt" for a in assumptions):
            assumptions.append({"name": "heating_value_mmbtu_per_mt", "value": float(hv), "basis": valid_basis})
        method = "energy_assumption_conversion"
    else:
        raise LNGConversionError(f"Unsupported output unit: {output_unit}")

    return ConversionResult(amount, src, output, dst, method, valid_basis, assumptions).as_dict()
