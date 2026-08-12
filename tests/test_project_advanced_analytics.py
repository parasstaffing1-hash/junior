from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.core.projects.advanced_analytics import ADVANCED_ANALYSIS_PROJECTS, run_advanced_analysis
from app.core.projects.catalog import get_project_spec
from app.core.projects.fixtures import build_project_fixture
from app.core.projects.workbench import _prepare_frame, _resolve_fields, build_project


def _analysis(project_id: str, rows: int = 400) -> dict:
    spec = get_project_spec(project_id)
    frame = build_project_fixture(project_id, rows=rows)
    fields = _resolve_fields(frame, spec)
    prepared, fields = _prepare_frame(spec, frame, fields)
    return run_advanced_analysis(project_id, prepared, fields)


@pytest.mark.parametrize("project_id", sorted(ADVANCED_ANALYSIS_PROJECTS))
def test_every_advanced_project_runs_its_promised_analysis(project_id):
    result = _analysis(project_id)
    assert result["status"] == "completed", result.get("reason")
    assert result["kpis"], "advanced analysis must contribute headline metrics"
    assert result["narrative"]


def test_ab_test_reports_a_real_significance_decision():
    result = _analysis("ab_test_analysis")
    detail = result["detail"]

    assert result["method"] == "two_proportion_z_test"
    assert detail["control_arm"] != detail["treatment_arm"]
    # A significance decision needs the test statistic, not just a difference of means.
    assert 0.0 <= detail["p_value"] <= 1.0
    assert detail["confidence_interval"]["lower"] <= detail["confidence_interval"]["upper"]
    assert detail["reject_null"] is (detail["p_value"] < detail["alpha"])
    assert "srm" in detail

    labels = {kpi["label"] for kpi in result["kpis"]}
    assert {"p-value", "Significance", "Absolute uplift"} <= labels


def test_forecast_projects_future_periods_with_widening_intervals():
    result = _analysis("sales_forecasting")
    rows = result["detail"]["forecast"]

    assert len(rows) == 6, "the forecast must produce future periods, not a trailing average"
    assert [row["horizon"] for row in rows] == [1, 2, 3, 4, 5, 6]
    for row in rows:
        assert row["lower"] <= row["forecast"] <= row["upper"]
    first_width = rows[0]["upper"] - rows[0]["lower"]
    last_width = rows[-1]["upper"] - rows[-1]["lower"]
    assert last_width >= first_width, "uncertainty must compound across the horizon"


@pytest.mark.parametrize(
    "project_id",
    ["customer_churn_prediction", "fraud_detection", "loan_default_risk"],
)
def test_classification_projects_train_and_score_out_of_sample(project_id):
    result = _analysis(project_id)
    detail = result["detail"]
    training = detail["training"]

    assert training["task_type"] == "classification"
    assert training["validation_rows"] > 0
    assert 0.0 <= training["metrics"]["balanced_accuracy"] <= 1.0
    # Threshold tuning and importance must never be measured on the fitted rows.
    assert detail["evaluation_basis"]["in_sample_scores_reported"] is False
    if detail["operating_threshold"]:
        assert detail["evaluation_basis"]["holdout_rows"] > 0


def test_advanced_analysis_degrades_with_a_reason_instead_of_raising():
    spec = get_project_spec("customer_churn_prediction")
    frame = build_project_fixture("customer_churn_prediction", rows=400)
    fields = _resolve_fields(frame, spec)
    prepared, fields = _prepare_frame(spec, frame, fields)

    result = run_advanced_analysis("customer_churn_prediction", prepared.head(10), fields)

    assert result["status"] == "skipped"
    assert result["reason"]
    assert result["kpis"] == []


def _measures(package: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        name = next(item for item in archive.namelist() if item.endswith("model.bim"))
        model = json.loads(archive.read(name).decode("utf-8"))
    return {
        measure["name"]: " ".join(measure["expression"].split())
        for table in model["model"]["tables"]
        for measure in table.get("measures") or []
    }


def test_power_bi_totals_aggregate_their_column_instead_of_counting_rows():
    """A KPI labelled 'Total sales' must not publish a row count in Power BI."""
    result = build_project(build_project_fixture("superstore_sales_dashboard", rows=300), project_id="superstore_sales_dashboard")
    measures = _measures(result["powerbi_bytes"])

    assert measures["Total sales"] == "SUM('FactData'[sales])"
    assert measures["Average sales"] == "AVERAGE('FactData'[sales])"
    assert measures["Rows analyzed"] == "COUNTROWS('FactData')"


def test_power_bi_model_has_no_duplicate_measure_definitions():
    result = build_project(build_project_fixture("superstore_sales_dashboard", rows=300), project_id="superstore_sales_dashboard")
    expressions = list(_measures(result["powerbi_bytes"]).values())

    assert len(expressions) == len(set(expressions)), "identical DAX published under two names"


def test_time_intelligence_chains_to_the_canonical_total():
    result = build_project(build_project_fixture("superstore_sales_dashboard", rows=300), project_id="superstore_sales_dashboard")
    measures = _measures(result["powerbi_bytes"])

    assert "[Total sales]" in measures["sales YTD"]
    assert "[Total sales]" in measures["sales Previous Year"]


def test_model_metrics_are_not_published_as_dax_measures():
    """Scores from a trained model cannot be recomputed in DAX and must be omitted."""
    result = build_project(build_project_fixture("customer_churn_prediction", rows=300), project_id="customer_churn_prediction")
    measures = _measures(result["powerbi_bytes"])

    for banned in ("ROC AUC", "Balanced accuracy", "Churn recall", "Churn precision"):
        assert banned not in measures
