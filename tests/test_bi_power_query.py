from __future__ import annotations

from app.core.bi.power_query import analyze_power_query, analyze_power_query_batch


def _m_query() -> str:
    return """(SourceTable as table, RangeStart as datetime, RangeEnd as datetime) as table =>
let
    Typed = Table.TransformColumnTypes(SourceTable, {{"EventDate", type datetime}}),
    IncrementalWindow = Table.SelectRows(Typed, each [EventDate] >= RangeStart and [EventDate] < RangeEnd),
    Clean = Table.TransformColumns(IncrementalWindow, {{"Entity", each Text.Trim(_), type text}}),
    Safe = Table.AddColumn(Clean, "Validation", each try [MetricValue] otherwise null),
    Result = Table.RemoveColumns(Safe, {{"Validation"}})
in
    Result"""


def test_power_query_analyzer_covers_foldable_incremental_function():
    result = analyze_power_query(_m_query(), incremental_refresh=True)
    assert result["status"] == "VALID"
    assert result["features"]["parameterized"] is True
    assert result["features"]["reusable_function"] is True
    assert result["features"]["typed_transform"] is True
    assert result["features"]["query_folding_boundary"] is True
    assert result["features"]["incremental_refresh_window"] is True
    assert result["features"]["error_handling"] is True


def test_power_query_analyzer_flags_missing_incremental_window_and_buffer():
    result = analyze_power_query("let Buffered = Table.Buffer(Source), Result = Table.SelectRows(Buffered, each [Value] > 0) in Result", incremental_refresh=True)
    codes = {item["code"] for item in result["issues"]}
    assert "INCREMENTAL_WINDOW_REQUIRED" in codes
    assert "TABLE_BUFFER_FOLDING_BREAK" in codes
    assert result["status"] == "REVIEW_REQUIRED"


def test_power_query_batch_and_api(client):
    batch = analyze_power_query_batch([{"name": "Sales", "expression": _m_query(), "incremental_refresh": True}])
    assert batch["status"] == "VALID"
    response = client.post("/api/v1/bi-readiness/power-query/analyze", json={"expression": _m_query(), "incremental_refresh": True})
    assert response.status_code == 200, response.text
    assert response.json()["features"]["incremental_refresh_window"] is True
