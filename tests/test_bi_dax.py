from __future__ import annotations

from app.core.bi.dax import analyze_dax_expression, analyze_dax_measures


def test_dax_analyzer_explains_advanced_measure_contexts():
    result = analyze_dax_expression(
        "Revenue YoY := CALCULATE ( [Total Revenue], SAMEPERIODLASTYEAR ( DimDate[Date] ) )",
        context="measure",
    )
    assert result["status"] == "VALID"
    assert result["features"]["filter_context"] is True
    assert result["features"]["time_intelligence"] == ["SAMEPERIODLASTYEAR"]
    assert result["features"]["explicit_measure_references"] == ["Total Revenue", "Date"]

    dynamic = analyze_dax_expression(
        'Selected Metric := SWITCH ( SELECTEDVALUE ( MetricSelector[Metric], "Total Revenue" ), "Total Revenue", [Total Revenue], [Total Revenue] )'
    )
    assert dynamic["features"]["dynamic_measures"] is True
    assert dynamic["features"]["disconnected_tables"] is True


def test_dax_analyzer_flags_formula_engine_and_ratio_risks():
    result = analyze_dax_expression("Top Value := SUMX ( FILTER ( ALL ( DimCustomer ), [Total Revenue] > 0 ), [Total Revenue] ) / [Total Revenue]")
    codes = {item["code"] for item in result["issues"]}
    assert "FILTER_OVER_WIDE_TABLE" in codes
    assert "UNSAFE_RATIO_OPERATOR" in codes
    assert result["features"]["row_context"] is True
    assert result["features"]["virtual_tables"]

    invalid = analyze_dax_expression("Broken := CALCULATE ( [Total Revenue]")
    assert invalid["status"] == "REVIEW_REQUIRED"
    assert any(item["code"] == "UNBALANCED_PARENTHESES" for item in invalid["issues"])


def test_dax_measure_batch_and_api(client):
    measures = [{"name": "Total Revenue", "expression": "Total Revenue := SUM ( FactSales[revenue] )"}, {"name": "Broken", "expression": "Broken := CALCULATE ( [Total Revenue]"}]
    batch = analyze_dax_measures(measures)
    assert batch["measure_count"] == 2
    assert batch["valid_count"] == 1
    assert batch["status"] == "REVIEW_REQUIRED"

    response = client.post("/api/v1/bi-readiness/dax/analyze", json={"expression": "Total Revenue := SUM ( FactSales[revenue] )"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "VALID"

    batch_response = client.post("/api/v1/bi-readiness/dax/analyze", json={"measures": measures})
    assert batch_response.status_code == 200, batch_response.text
    assert batch_response.json()["valid_count"] == 1
