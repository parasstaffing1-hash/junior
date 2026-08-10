from __future__ import annotations

import pandas as pd
import pytest

from app.core.kpi.calculator import KPICalculationError, calculate_kpi
from app.core.kpi.formulas import FormulaError, evaluate_formula


def test_formula_evaluator_is_arithmetic_only():
    assert evaluate_formula("(revenue - cost) / revenue * 100", {"revenue": 200, "cost": 50})["value"] == 75
    with pytest.raises(FormulaError) as blocked:
        evaluate_formula("__import__('os').system('id')", {})
    assert blocked.value.code == "UNSAFE_FORMULA"


def test_kpi_calculator_rejects_code_like_formula():
    frame = pd.DataFrame({"sales": [10, 20]})
    definition = {
        "definition_type": "formula",
        "components": {"sales": {"column": "sales", "aggregation": "sum"}},
        "formula": "__import__('os').system('id')",
    }
    with pytest.raises(KPICalculationError) as blocked:
        calculate_kpi(frame, definition)
    assert blocked.value.code == "UNSAFE_FORMULA"
