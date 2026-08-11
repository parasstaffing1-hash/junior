from __future__ import annotations

import re
import warnings

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.core.automation.gateway import dispatch_plan
from app.core.cleaning.category_normalizer import apply_category_normalization
from app.core.cleaning.date_cleaner import apply_date_cleaning
from app.core.cleaning.duplicate_remover import remove_duplicates
from app.core.cleaning.imputation import apply_imputation
from app.core.cleaning.missing_cleaner import apply_missing_value_operation
from app.core.cleaning.numeric_cleaner import apply_numeric_cleaning
from app.core.cleaning.outlier_detector import detect_outliers
from app.core.cleaning.outlier_treatment import apply_outlier_treatment
from app.core.cleaning.recipe_engine import execute_recipe
from app.core.cleaning.string_cleaner import apply_string_cleaning
from app.core.dashboard.chart_widget import render_manifest as render_chart_widget
from app.core.dashboard.drilldown import resolve_drilldown
from app.core.dashboard.filter_engine import apply_filter
from app.core.dashboard.kpi_card import build_card
from app.core.dashboard.layout import validate_layout
from app.core.dashboard.table_widget import build_table
from app.core.eda.categorical_eda import analyze_categorical_eda
from app.core.eda.explorer import explore_correlations, explore_distributions
from app.core.eda.findings import detect_findings
from app.core.eda.group_comparison import analyze_group_comparison
from app.core.eda.numeric_eda import analyze_numeric_eda
from app.core.eda.numeric_relationships import (
    analyze_numeric_categorical_relationships,
    analyze_numeric_relationships,
)
from app.core.eda.relationships import analyze_categorical_relationships
from app.core.eda.report import generate_eda_report
from app.core.kpi.alerts import evaluate_rule
from app.core.kpi.calculator import calculate_kpi
from app.core.kpi.definition import validate_definition as validate_kpi_definition
from app.core.kpi.formulas import evaluate_formula
from app.core.kpi.month_over_month import analyze_mom
from app.core.kpi.period_comparison import compare_periods
from app.core.kpi.quarter_over_quarter import analyze_qoq
from app.core.kpi.target_variance import analyze_target_variance
from app.core.kpi.week_over_week import analyze_wow
from app.core.kpi.year_over_year import analyze_yoy
from app.core.quality.basic_schema import detect_basic_schema
from app.core.quality.categorical_profiler import profile_categorical
from app.core.quality.duplicate_analyzer import analyze_duplicates
from app.core.quality.health_score import calculate_health
from app.core.quality.missing_analyzer import analyze_missing
from app.core.quality.numeric_profiler import profile_numeric
from app.core.quality.semantic_schema import detect_semantic_schema
from app.core.quality.validation_engine import validate_dataset
from app.core.reporting.pdf_generator import build_pdf_bytes
from app.core.reporting.report_builder import assemble_report, render_html as render_report_html
from app.core.reporting.xlsx_generator import build_xlsx_bytes
from app.core.statistics.ab_test import analyze_ab_test
from app.core.statistics.anova import analyze_anova
from app.core.statistics.confidence_intervals import calculate_confidence_interval
from app.core.statistics.correlation import analyze_correlations
from app.core.statistics.covariance import analyze_covariance
from app.core.statistics.descriptive_stats import analyze_descriptive_statistics
from app.core.statistics.distribution import analyze_distributions
from app.core.statistics.effect_size import calculate_effect_size
from app.core.statistics.engine import chi_square_test, fisher_exact_test, paired_t_test
from app.core.statistics.independent_t import independent_t_test
from app.core.statistics.non_parametric import analyze_non_parametric
from app.core.statistics.normality import analyze_normality
from app.core.statistics.one_sample_t import one_sample_t_test
from app.core.statistics.percentiles import analyze_percentiles
from app.core.statistics.power import calculate_power
from app.core.statistics.report import generate_statistics_report
from app.core.statistics.sampling import sample_dataframe
from app.core.statistics.selector import select_statistical_test
from app.core.transformation.aggregation import aggregate_dataframe
from app.core.transformation.binning import apply_binning
from app.core.transformation.calculated_columns import apply_calculated_columns
from app.core.transformation.column_operations import apply_column_operations
from app.core.transformation.compatibility import analyze_merge_compatibility
from app.core.transformation.concatenation import concatenate_dataframes
from app.core.transformation.fuzzy_matching import fuzzy_match
from app.core.transformation.group_by import analyze_groups
from app.core.transformation.join import apply_join
from app.core.transformation.lag_lead import apply_lag_lead
from app.core.transformation.pipeline import execute_pipeline
from app.core.transformation.pivot import pivot_dataframe
from app.core.transformation.ranking import apply_ranking
from app.core.transformation.record_linkage import link_records
from app.core.transformation.rolling_window import apply_rolling_windows
from app.core.transformation.row_filter import apply_row_filter
from app.core.transformation.running_total import apply_running_totals
from app.core.transformation.sorting import apply_sort
from app.core.transformation.type_converter import apply_type_conversions
from app.core.transformation.unpivot import unpivot_dataframe
from app.core.visualization.area_chart import build_area_chart
from app.core.visualization.bar_chart import build_bar_chart
from app.core.visualization.box_violin import build_box_violin
from app.core.visualization.business_chart import build_business_chart
from app.core.visualization.heatmap import build_heatmap
from app.core.visualization.histogram import build_histogram
from app.core.visualization.line_chart import build_line_chart
from app.core.visualization.pie_donut import build_pie_donut
from app.core.visualization.recommender import recommend_charts
from app.core.visualization.scatter_bubble import build_scatter_bubble
from app.main import create_app


TOOL_NAMES = [
    "CSV importer", "dataset preview", "basic schema", "semantic schema", "numeric profiler",
    "categorical profiler", "missing analyzer", "duplicate analyzer", "health score", "validation engine",
    "missing cleaner", "string cleaner", "numeric cleaner", "date cleaner", "category normalizer",
    "imputation", "duplicate remover", "outlier detector", "outlier treatment", "recipe engine",
    "column operations", "type conversion", "row filter", "sorting", "calculated columns",
    "group by", "aggregation", "pivot", "unpivot", "binning", "join", "concatenation",
    "merge compatibility", "fuzzy matching", "record linkage", "ranking", "running total", "lag lead",
    "rolling window", "transformation pipeline", "descriptive statistics", "percentiles", "correlation",
    "covariance", "distribution analysis", "normality", "confidence intervals", "effect size", "sampling",
    "statistics report", "one sample t test", "independent t test", "paired t test", "chi square",
    "fisher exact", "ANOVA", "non parametric tests", "test selector", "power and sample size", "AB test",
    "numeric EDA", "categorical EDA", "numeric vs numeric", "numeric vs categorical",
    "categorical vs categorical", "correlation explorer", "distribution explorer", "group comparison",
    "deterministic findings", "EDA report", "bar chart", "line chart", "area chart", "pie donut chart",
    "histogram", "scatter bubble", "box violin", "heatmap", "business chart", "chart recommender",
    "KPI definition", "KPI calculator", "formula registry", "period comparison", "week over week",
    "month over month", "quarter over quarter", "year over year", "target variance", "alerts",
    "dashboard layout", "KPI card", "chart widget", "table widget", "filter engine", "drilldown",
    "report builder", "PDF generator", "Excel generator", "automation gateway",
]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


@pytest.fixture(scope="module")
def tool_context(tmp_path_factory):
    root = tmp_path_factory.mktemp("tools_1_100")
    app = create_app(
        database_url="sqlite:///" + str(root / "analytics.db"),
        storage_root=root / "storage",
    )

    n = 60
    index = np.arange(n)
    base = pd.DataFrame(
        {
            "id": index + 1,
            "date": pd.date_range("2025-01-01", periods=n, freq="D"),
            "category": np.array(["A", "B", "C"])[index % 3],
            "channel": np.array(["Online", "Store", "Partner", "Store"])[index % 4],
            "group": np.where(index % 2 == 0, "control", "treatment"),
            "status": np.where(index % 3 == 0, "failure", "success"),
            "value": 10 + index * 0.8 + (index % 5) * 0.7,
            "value2": 8 + index * 0.72 + ((index % 7) - 3) * 0.9,
        }
    )
    time_index = np.arange(800)
    time_frame = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=len(time_index), freq="D"),
            "category": np.array(["A", "B", "C"])[time_index % 3],
            "value": 100 + time_index * 0.05 + (time_index % 30),
        }
    )
    clean = pd.DataFrame(
        {
            "text": [" Alpha ", "Beta", " Gamma  Value ", "Delta", None, " Alpha "],
            "numeric_text": ["1,200", "2500", "3,750", "4000", None, "1,200"],
            "date_text": ["2025-01-01", "2025-02-02", "2025-03-03", "2025-04-04", None, "2025-01-01"],
            "country": ["U.S.A", "USA", "United States", "Canada", None, "U.S.A"],
            "value": [1.0, np.nan, 3.0, 1000.0, 5.0, 1.0],
        }
    )
    quality_rows = [
        {"id": 1, "name": "Alice", "email": "alice@example.com", "date": "2025-01-01", "value": 10, "category": "A"},
        {"id": 2, "name": "Bob", "email": "bob@example.com", "date": "2025-01-02", "value": 20, "category": "B"},
        {"id": 3, "name": None, "email": "invalid", "date": "2025-01-03", "value": None, "category": "A"},
        {"id": 2, "name": "Bob", "email": "bob@example.com", "date": "2025-01-02", "value": 20, "category": "B"},
    ]
    right = pd.DataFrame({"id": [1, 2, 3, 61], "right_value": [100, 200, 300, 610]})
    names_left = pd.DataFrame({"name": ["Acme Corp", "Globex Ltd", "Soylent"]})
    names_right = pd.DataFrame({"name": ["ACME Corporation", "Globex Limited", "Soylent Inc"], "account_id": [10, 20, 30]})
    kpi_definition = {
        "id": "revenue", "name": "Revenue", "slug": "revenue", "definition_type": "aggregate",
        "components": {"value": {"column": "value", "aggregation": "sum"}},
        "target_value": 2200, "target_direction": "higher_is_better", "format_type": "currency",
    }

    with TestClient(app) as client:
        uploaded = client.post(
            "/api/v1/datasets/import",
            files={"file": ("certification.csv", base.to_csv(index=False).encode(), "text/csv")},
        )
        assert uploaded.status_code == 200, uploaded.text
        imported = uploaded.json()
        yield {
            "client": client, "imported": imported, "base": base, "time": time_frame, "clean": clean,
            "quality_rows": quality_rows, "right": right, "names_left": names_left, "names_right": names_right,
            "kpi_definition": kpi_definition,
        }
    app.state.engine.dispose()


def _quality(context):
    if "quality" not in context:
        rows = context["quality_rows"]
        basic = detect_basic_schema(rows)
        semantic = detect_semantic_schema(rows, basic)
        numeric = profile_numeric(rows, basic, semantic)
        categorical = profile_categorical(rows, basic, semantic)
        missing = analyze_missing(rows)
        duplicates = analyze_duplicates(rows)
        validation = validate_dataset(rows, "certification", [{"column": "id", "rule": "required"}])
        context["quality"] = {
            "basic": basic, "semantic": semantic, "numeric": numeric, "categorical": categorical,
            "missing": missing, "duplicates": duplicates, "validation": validation,
        }
    return context["quality"]


def _health(context):
    quality = _quality(context)
    return calculate_health(
        basic_schema=quality["basic"], semantic_schema=quality["semantic"],
        numeric_profile=quality["numeric"], categorical_profile=quality["categorical"],
        missing_analysis=quality["missing"], duplicate_analysis=quality["duplicates"],
        validation=quality["validation"],
    )


def _preview(context):
    response = context["client"].get(
        f"/api/v1/datasets/{context['imported']['dataset_id']}/preview?limit=10"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["preview"]["total_rows"] == len(context["base"])
    return body


def _report_manifest(context):
    if "manifest" not in context:
        context["manifest"] = assemble_report(
            {"id": "certification-report", "title": "Tools 1-100 Certification", "description": "Executable acceptance evidence."},
            [
                {"id": "heading", "section_type": "heading", "position": 1, "content": "Executive Summary"},
                {"id": "text", "section_type": "text", "position": 2, "title": "Result", "content": "All registered analytics layers were exercised."},
                {"id": "kpi", "section_type": "kpi", "position": 3, "title": "Revenue", "source_ref": "kpi:revenue"},
                {"id": "table", "section_type": "table", "position": 4, "title": "Evidence", "source_ref": "table:evidence"},
            ],
            content_by_ref={
                "kpi:revenue": {"value": 2500, "formatted_value": "$2,500.00", "percent_change": 4.2, "target_status": "met"},
                "table:evidence": {"rows": context["base"].head(8).to_dict(orient="records")},
            },
        )
    return context["manifest"]


CALCULATION = {
    "name": "double_value",
    "expression": {
        "op": "multiply", "left": {"op": "column", "name": "value"},
        "right": {"op": "literal", "value": 2},
    },
}
ORDER = [{"column": "date", "ascending": True}]


TOOL_RUNNERS = [
    lambda c: c["imported"],
    _preview,
    lambda c: _quality(c)["basic"],
    lambda c: _quality(c)["semantic"],
    lambda c: _quality(c)["numeric"],
    lambda c: _quality(c)["categorical"],
    lambda c: _quality(c)["missing"],
    lambda c: _quality(c)["duplicates"],
    _health,
    lambda c: validate_dataset(c["quality_rows"], "certification", [{"column": "email", "rule": "email", "severity": "warning"}]),
    lambda c: apply_missing_value_operation(c["clean"], operation="fill_constant", columns=["value"], fill_value=0),
    lambda c: apply_string_cleaning(c["clean"], columns=["text"], operations=[{"type": "trim"}]),
    lambda c: apply_numeric_cleaning(c["clean"], columns=["numeric_text"], operations=[{"type": "remove_thousands_separators"}, {"type": "to_numeric"}]),
    lambda c: apply_date_cleaning(c["clean"], columns=["date_text"], target_type="date"),
    lambda c: apply_category_normalization(c["clean"], columns=["country"], mappings={"U.S.A": "USA", "United States": "USA"}),
    lambda c: apply_imputation(c["clean"], strategy="mean", columns=["value"]),
    lambda c: remove_duplicates(c["clean"], mode="exact", keep="first"),
    lambda c: detect_outliers(c["clean"], method="iqr", columns=["value"]),
    lambda c: apply_outlier_treatment(c["clean"], method="iqr", columns=["value"], action="cap"),
    lambda c: execute_recipe(c["clean"], [{"type": "string_cleaning", "params": {"columns": ["text"], "operations": ["trim"]}}]),
    lambda c: apply_column_operations(c["base"], operations=[{"type": "rename", "mapping": {"value": "amount"}}]),
    lambda c: apply_type_conversions(c["clean"], [{"column": "numeric_text", "target_type": "float", "policy": "coerce"}]),
    lambda c: apply_row_filter(c["base"], conditions=[{"column": "value", "operator": "gt", "value": 25}]),
    lambda c: apply_sort(c["base"], sort_by=[{"column": "value", "direction": "desc"}]),
    lambda c: apply_calculated_columns(c["base"], calculations=[CALCULATION]),
    lambda c: analyze_groups(c["base"], group_by=["category"], add_group_size=True),
    lambda c: aggregate_dataframe(c["base"], group_by=["category"], aggregations=[{"function": "sum", "column": "value", "alias": "total"}]),
    lambda c: pivot_dataframe(c["base"], rows=["category"], columns=["group"], values=[{"function": "sum", "column": "value", "alias": "total"}]),
    lambda c: unpivot_dataframe(c["base"], id_vars=["id"], value_vars=["value", "value2"]),
    lambda c: apply_binning(c["base"], operations=[{"column": "value", "output_column": "value_band", "method": "equal_width", "bins": 4}]),
    lambda c: apply_join(c["base"], c["right"], join_type="left", keys=[{"left": "id", "right": "id"}], add_match_status=True),
    lambda c: concatenate_dataframes([("first", c["base"].head(5)), ("second", c["base"].iloc[5:10])], schema_mode="strict"),
    lambda c: analyze_merge_compatibility(c["base"], c["right"], keys=[{"left": "id", "right": "id"}]),
    lambda c: fuzzy_match(c["names_left"], c["names_right"], fields=[{"left": "name", "right": "name"}], threshold=60, right_include_columns=["account_id"]),
    lambda c: link_records(c["names_left"], c["names_right"], fields=[{"left": "name", "right": "name"}], threshold=60, right_include_columns=["account_id"]),
    lambda c: apply_ranking(c["base"], operations=[{"method": "dense_rank", "output_column": "rank", "partition_by": ["category"], "order_by": [{"column": "value", "ascending": False}]}]),
    lambda c: apply_running_totals(c["base"], operations=[{"source_column": "value", "output_column": "running", "partition_by": ["category"], "order_by": ORDER}]),
    lambda c: apply_lag_lead(c["base"], operations=[{"source_column": "value", "output_column": "prior", "direction": "lag", "offset": 1, "partition_by": ["category"], "order_by": ORDER}]),
    lambda c: apply_rolling_windows(c["base"], operations=[{"source_column": "value", "output_column": "rolling", "function": "avg", "window": 3, "min_periods": 1, "partition_by": ["category"], "order_by": ORDER}]),
    lambda c: execute_pipeline(c["base"], steps=[{"tool": "calculated_columns", "parameters": {"calculations": [CALCULATION]}}]),
    lambda c: analyze_descriptive_statistics(c["base"], columns=["value", "value2"]),
    lambda c: analyze_percentiles(c["base"], columns=["value", "value2"]),
    lambda c: analyze_correlations(c["base"], columns=["value", "value2"]),
    lambda c: analyze_covariance(c["base"], columns=["value", "value2"]),
    lambda c: analyze_distributions(c["base"], columns=["value", "value2"]),
    lambda c: analyze_normality(c["base"], columns=["value", "value2"]),
    lambda c: calculate_confidence_interval(c["base"], interval_type="mean_t", column="value"),
    lambda c: calculate_effect_size(c["base"], effect_type="cohens_d", column="value", column_b="value2"),
    lambda c: sample_dataframe(c["base"], method="simple_random", sample_size=10, seed=100),
    lambda c: generate_statistics_report(c["base"], columns=["value", "value2"]),
    lambda c: one_sample_t_test(c["base"], column="value", population_mean=30),
    lambda c: independent_t_test(c["base"], sample_mode="columns", column_a="value", column_b="value2"),
    lambda c: paired_t_test(c["base"], "value", "value2"),
    lambda c: chi_square_test(c["base"], "category", "channel"),
    lambda c: fisher_exact_test(c["base"], "group", "status"),
    lambda c: analyze_anova(c["base"], value_column="value", group_column="category"),
    lambda c: analyze_non_parametric(c["base"], test_type="mann_whitney_u", column_a="value", column_b="value2"),
    lambda c: select_statistical_test(question_type="compare_two_groups", paired=True, normality="supported", sample_size=60),
    lambda c: calculate_power(design="two_independent_means", effect_size=0.5),
    lambda c: analyze_ab_test(c["base"], variant_column="group", control_value="control", treatment_value="treatment", metric_type="continuous", metric_column="value"),
    lambda c: analyze_numeric_eda(c["base"], columns=["value", "value2"]),
    lambda c: analyze_categorical_eda(c["base"], columns=["category", "channel", "group"]),
    lambda c: analyze_numeric_relationships(c["base"], columns=["value", "value2"]),
    lambda c: analyze_numeric_categorical_relationships(c["base"], numeric_column="value", categorical_column="category"),
    lambda c: analyze_categorical_relationships(c["base"], left="category", right="channel"),
    lambda c: explore_correlations(c["base"], columns=["value", "value2"]),
    lambda c: explore_distributions(c["base"], columns=["value", "value2"]),
    lambda c: analyze_group_comparison(c["base"], value_column="value", group_column="category"),
    lambda c: detect_findings(c["base"]),
    lambda c: generate_eda_report(c["base"]),
    lambda c: build_bar_chart(c["base"], category_column="category", value_column="value"),
    lambda c: build_line_chart(c["base"], x_column="date", y_column="value", parse_datetime=True),
    lambda c: build_area_chart(c["base"], x_column="date", y_column="value", parse_datetime=True),
    lambda c: build_pie_donut(c["base"], category_column="category", value_column="value", aggregation="sum", mode="donut"),
    lambda c: build_histogram(c["base"], value_column="value"),
    lambda c: build_scatter_bubble(c["base"], x_column="value", y_column="value2", group_column="category", regression_line=True),
    lambda c: build_box_violin(c["base"], value_column="value", group_column="category", plot_type="box"),
    lambda c: build_heatmap(c["base"], mode="correlation", columns=["value", "value2"]),
    lambda c: build_business_chart(c["base"], chart_type="variance", category_column="category", value_column="value", comparison_column="value2"),
    lambda c: recommend_charts(c["base"], intent="auto", time_column="date", y_column="value"),
    lambda c: validate_kpi_definition(c["kpi_definition"]),
    lambda c: calculate_kpi(c["base"], c["kpi_definition"], dimensions=["category"]),
    lambda c: evaluate_formula("revenue - cost", {"revenue": 1250, "cost": 800}),
    lambda c: compare_periods(c["time"], date_column="date", value_column="value", current_start="2026-01-01", current_end="2026-02-28", prior_start="2025-11-03", prior_end="2025-12-31"),
    lambda c: analyze_wow(c["time"], date_column="date", value_column="value", reference_date="2026-02-28"),
    lambda c: analyze_mom(c["time"], date_column="date", value_column="value", reference_date="2026-02-28"),
    lambda c: analyze_qoq(c["time"], date_column="date", value_column="value", reference_date="2026-02-28"),
    lambda c: analyze_yoy(c["time"], date_column="date", value_column="value", reference_date="2026-02-28", history_years=2, year_to_date=True),
    lambda c: analyze_target_variance(c["base"], value_column="value", target_value=2200, direction="higher_is_better"),
    lambda c: evaluate_rule({"rule_type": "above", "kpi_slug": "revenue", "threshold": 2000}, {"kpi_slug": "revenue", "value": 2500}),
    lambda c: validate_layout([{"id": "kpi", "x": 0, "y": 0, "w": 3, "h": 2}, {"id": "chart", "x": 3, "y": 0, "w": 9, "h": 4}]),
    lambda c: build_card(label="Revenue", current_value=2500, prior_value=2300, target_value=2400, direction="higher_is_better", format_type="currency"),
    lambda c: render_chart_widget({"id": "revenue-chart", "title": "Revenue", "source_type": "json_spec", "chart_spec": {"type": "bar"}, "show_legend": True, "show_x_axis": True, "show_y_axis": True, "responsive": True, "presentation": "standard"}),
    lambda c: build_table(rows=c["base"].head(10).to_dict(orient="records"), columns=["id", "category", "value"], totals=["value"]),
    lambda c: apply_filter(c["base"].head(10).to_dict(orient="records"), {"id": "category", "field": "category", "operator": "eq", "value_type": "string", "scope": "dashboard"}, "A"),
    lambda c: resolve_drilldown({"source_widget_id": "revenue-chart", "source_dashboard_id": "overview", "target_dashboard_id": "detail", "mapping": {"category": "category"}, "max_depth": 2}, selected={"category": "A"}),
    lambda c: {"manifest": _report_manifest(c), "html": render_report_html(_report_manifest(c))},
    lambda c: build_pdf_bytes(_report_manifest(c)),
    lambda c: build_xlsx_bytes(_report_manifest(c)),
    lambda c: dispatch_plan("bi.report", {"dataset_id": c["imported"]["dataset_id"]}, {}),
]


assert len(TOOL_NAMES) == 100
assert len(TOOL_RUNNERS) == 100


def _assert_executed(result):
    assert result is not None
    if isinstance(result, dict):
        assert result
    elif isinstance(result, (bytes, bytearray)):
        assert len(result) > 100
    elif isinstance(result, str):
        assert result.strip()
    elif isinstance(result, list):
        assert result
    elif hasattr(result, "dataframe"):
        assert isinstance(result.dataframe, pd.DataFrame)
    else:
        assert result


@pytest.mark.parametrize(
    ("tool_number", "name", "runner"),
    [(number, TOOL_NAMES[number - 1], TOOL_RUNNERS[number - 1]) for number in range(1, 101)],
    ids=[f"tool_{number:03d}_{_slug(TOOL_NAMES[number - 1])}" for number in range(1, 101)],
)
def test_tools_1_to_100_execute(tool_number, name, runner, tool_context):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = runner(tool_context)
    _assert_executed(result)
