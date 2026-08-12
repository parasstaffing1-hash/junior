from __future__ import annotations

import pandas as pd
import pytest

from app.core.bi.export_formats import (
    ALL_EXPORT_FORMATS,
    INTERACTIVE_EXPORT_FORMATS,
    resolve_export_formats,
)
from app.core.bi.report_service import build_bi_report


def _sales_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "Date": ["2026-01-05", "2026-01-12", "2026-02-03", "2026-02-18"],
        "Country": ["India", "India", "Germany", "Germany"],
        "Product": ["Alpha", "Beta", "Alpha", "Beta"],
        "Sales": [1200.0, 900.0, 1500.0, 400.0],
        "Profit": [300.0, 150.0, 450.0, 40.0],
    })


def _flight_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "FlightDate": ["2026-05-01", "2026-05-02", "2026-05-03"],
        "IATA_CODE_Reporting_Airline": ["AA", "DL", "UA"],
        "Origin": ["JFK", "ATL", "ORD"],
        "Dest": ["LAX", "JFK", "SFO"],
        "Cancelled": [0, 0, 0],
        "Diverted": [0, 0, 0],
        "ArrDelay": [10, -5, 20],
        "DepDelay": [5, -10, 15],
        "Distance": [2475, 760, 1846],
    })


def _sales_records_style_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "Region": ["Africa", "Asia", "Africa", "Europe"],
        "Country": ["Chad", "India", "Ghana", "Germany"],
        "Item Type": ["Office Supplies", "Cosmetics", "Office Supplies", "Fruits"],
        "Sales Channel": ["Online", "Offline", "Online", "Online"],
        "Order Date": ["1/27/2011", "2/10/2011", "3/15/2011", "4/20/2011"],
        "Units Sold": [4484, 3000, 2100, 1800],
        "Total Revenue": [2920025.64, 1200000.0, 1500000.0, 700000.0],
        "Total Profit": [566105.0, 250000.0, 300000.0, 120000.0],
    })


def test_resolve_export_formats_defaults_to_every_format():
    assert resolve_export_formats(None) == frozenset(ALL_EXPORT_FORMATS)


def test_resolve_export_formats_pairs_tableau_workbook_source_with_its_package():
    assert resolve_export_formats(["tableau"]) == frozenset({"tableau", "tableau_twb"})


def test_sales_records_style_fields_generate_kpis_and_dashboard_charts(tmp_path):
    result = build_bi_report(
        _sales_records_style_frame(),
        dataset_name="10000 Sales Records",
        output_dir=tmp_path,
        template_id="powerbi_executive_sales",
        exports=INTERACTIVE_EXPORT_FORMATS,
    )
    assert result["dashboard"]["template"]["id"] == "powerbi_executive_sales"
    assert len(result["kpis"]) >= 5
    chart_refs = {chart["source_ref"] for chart in result["charts"]}
    assert {"chart:sales_by_country", "chart:profit_by_product", "chart:sales_over_time", "chart:sales_by_channel", "chart:units_by_product", "chart:profit_over_time"} <= chart_refs
    assert result["dashboard"]["layout_validation"]["valid"] is True


def test_resolve_export_formats_rejects_unknown_formats():
    with pytest.raises(ValueError, match="Unsupported BI export format"):
        resolve_export_formats(["pdf", "powerpoint"])


@pytest.mark.parametrize("frame_factory", [_sales_frame, _flight_frame], ids=["sales", "airline"])
def test_interactive_exports_write_only_the_immediate_dashboard_manifest(tmp_path, frame_factory):
    """The automated-analyst path should stay responsive; downloads build on demand."""
    result = build_bi_report(
        frame_factory(),
        dataset_name="Selective Exports",
        output_dir=tmp_path,
        exports=INTERACTIVE_EXPORT_FORMATS,
    )

    assert set(result["files"]) == set(INTERACTIVE_EXPORT_FORMATS)
    for path in result["files"].values():
        assert pd.io.common.file_exists(path)
    assert result["html"]
    assert result["pdf_bytes"] == b"" and result["xlsx_bytes"] == b""
    assert result["powerbi_bytes"] == b""
    assert result["tableau_twbx_bytes"] == b""


@pytest.mark.parametrize("frame_factory", [_sales_frame, _flight_frame], ids=["sales", "airline"])
def test_default_build_writes_every_export(tmp_path, frame_factory):
    result = build_bi_report(frame_factory(), dataset_name="All Exports", output_dir=tmp_path)

    assert set(result["files"]) == set(ALL_EXPORT_FORMATS)
    assert result["export_formats"] == sorted(ALL_EXPORT_FORMATS)
    assert result["powerbi_bytes"] and result["tableau_twbx_bytes"]
