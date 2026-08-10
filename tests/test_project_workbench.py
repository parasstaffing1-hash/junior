from __future__ import annotations

import pytest

from app.core.projects.catalog import list_project_specs
from app.core.projects.fixtures import build_project_fixture
from app.core.projects.workbench import build_project


PROJECT_IDS = [item["id"] for item in list_project_specs()]


def test_project_catalog_matches_reference_scope():
    projects = list_project_specs()
    assert len(projects) == 20
    assert len({item["id"] for item in projects}) == 20
    assert all(item["template_id"] for item in projects)


@pytest.mark.parametrize("project_id", PROJECT_IDS)
def test_every_reference_project_builds_a_valid_bi_package(project_id):
    result = build_project(build_project_fixture(project_id, rows=24), project_id=project_id)
    assert result["status"] == "COMPLETED"
    assert result["validation"]["valid"] is True
    assert result["validation"]["kpi_count"] >= 2
    assert result["validation"]["chart_count"] >= 2
    assert result["validation"]["table_count"] >= 1
    assert result["validation"]["artifact_sizes"]["pdf_bytes"] > 100
    assert result["validation"]["artifact_sizes"]["xlsx_bytes"] > 100
    assert result["dashboard"]["layout_validation"]["valid"] is True


def test_project_fixture_is_reproducible_across_calls():
    first = build_project_fixture("sales_forecasting", rows=24)
    second = build_project_fixture("sales_forecasting", rows=24)
    assert first.equals(second)


def test_project_catalog_and_validation_endpoints(client):
    catalog = client.get("/api/v1/project-catalog")
    assert catalog.status_code == 200
    assert catalog.json()["count"] == 20

    build = client.post("/api/v1/projects/superstore_sales_dashboard/build", json={"rows": 16, "template_id": "root_cause"})
    assert build.status_code == 200
    assert build.json()["status"] == "COMPLETED"
    assert build.json()["dashboard"]["template"]["id"] == "root_cause"

    validation = client.post("/api/v1/project-validation/run", json={"rows": 12})
    assert validation.status_code == 200
    assert validation.json()["status"] == "COMPLETED"
    assert validation.json()["passed"] == 20
    assert validation.json()["failed"] == 0
