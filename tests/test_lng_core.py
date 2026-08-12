import pytest

from app.core.lng.economics import LNGEconomicsError, calculate_brokerage, calculate_delivered_cost
from app.core.lng.matching import match_cargo_to_rfq
from app.core.lng.pricing import LNGPricingError, evaluate_price_formula
from app.core.lng.units import LNGConversionError, convert_lng_quantity


def test_exact_energy_conversion_tbtu_to_mmbtu():
    result = convert_lng_quantity(3.4, "TBtu", "MMBtu")
    assert result["output_value"] == pytest.approx(3_400_000)
    assert result["conversion_method"] == "exact_energy_unit"
    assert result["assumptions"] == []


def test_lng_volume_conversion_requires_density():
    with pytest.raises(LNGConversionError):
        convert_lng_quantity(100_000, "m3_lng", "MT")


def test_mass_to_energy_records_heating_value_assumption():
    result = convert_lng_quantity(65_000, "MT", "MMBtu", heating_value_mmbtu_per_mt=52.4, basis="CONTRACT_ASSUMPTION")
    assert result["output_value"] == pytest.approx(3_406_000)
    assert result["assumptions"][0]["name"] == "heating_value_mmbtu_per_mt"
    assert result["conversion_basis"] == "CONTRACT_ASSUMPTION"


def test_index_pricing_requires_supplied_benchmark():
    with pytest.raises(LNGPricingError):
        evaluate_price_formula(formula_type="INDEX_PLUS", premium_discount=0.25, benchmark_name="JKM")


def test_brent_slope_formula_is_deterministic():
    result = evaluate_price_formula(formula_type="BRENT_SLOPE", benchmark_name="Brent", benchmark_value=80, coefficient=0.135, constant=0.50)
    assert result["price"] == pytest.approx(11.30)


def test_delivered_cost_preserves_unknown_costs():
    result = calculate_delivered_cost(
        base_price_per_mmbtu=9.2,
        buyer_price_per_mmbtu=11.1,
        energy_mmbtu=3_400_000,
        costs_per_mmbtu={"shipping": 0.9, "terminal": 0.12, "insurance": None, "finance": 0.18, "other": 0.10},
    )
    assert result["known_delivered_cost_per_mmbtu"] == pytest.approx(10.50)
    assert result["unknown_costs"] == ["insurance"]
    assert result["complete_cost_picture"] is False
    assert result["profitability_label"] == "POTENTIAL_GROSS_SPREAD_ONLY"


@pytest.mark.parametrize(
    ("basis", "kwargs", "expected"),
    [
        ("$/MMBtu", {"energy_mmbtu": 3_400_000}, 34_000),
        ("$/MT", {"volume_mt": 65_000}, 32_500),
        ("percentage", {"transaction_value": 40_000_000}, 400_000),
        ("fixed", {}, 25_000),
    ],
)
def test_brokerage_bases(basis, kwargs, expected):
    rate = {"$/MMBtu": 0.01, "$/MT": 0.5, "percentage": 1.0, "fixed": 25_000}[basis]
    result = calculate_brokerage(basis=basis, rate=rate, **kwargs)
    assert result["estimated_commission"] == pytest.approx(expected)


def test_blocked_compliance_rejects_match():
    result = match_cargo_to_rfq(
        {"delivery_basis": "DES", "delivery_window_start": "2026-11-15", "delivery_window_end": "2026-11-25", "cargo_volume_mt": 65_000},
        {"delivery_basis": "DES", "loading_window_start": "2026-11-15", "loading_window_end": "2026-11-20", "volume_mt": 65_000, "compliance_status": "BLOCKED", "terminal_compatibility": "COMPATIBLE", "quality_compatibility": "PASS"},
    )
    assert result["overall_status"] == "REJECT"
    assert result["actionable"] is False


def test_unknown_quality_is_not_actionable_pass():
    result = match_cargo_to_rfq(
        {"delivery_basis": "DES", "delivery_window_start": "2026-11-15", "delivery_window_end": "2026-11-25", "cargo_volume_mt": 65_000},
        {"delivery_basis": "DES", "loading_window_start": "2026-11-15", "loading_window_end": "2026-11-20", "volume_mt": 65_000, "compliance_status": "CLEAR", "terminal_compatibility": "COMPATIBLE"},
    )
    assert result["overall_status"] == "REVIEW_REQUIRED"
    assert result["actionable"] is False
    assert "quality" in result["critical_unknowns"]
