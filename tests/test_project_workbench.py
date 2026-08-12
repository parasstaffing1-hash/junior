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
    assert result["validation"]["artifact_sizes"]["powerbi_bytes"] > 100
    assert result["validation"]["artifact_sizes"]["tableau_bytes"] > 100
    assert result["powerbi_bytes"].startswith(b"PK")
    assert result["tableau_twbx_bytes"].startswith(b"PK")
    assert result["tableau_twb_bytes"].startswith(b"<?xml")
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
    assert build.json()["artifact_formats"]["powerbi"]["filename"] == "superstore_sales_dashboard_PowerBI_PBIP_Project.zip"
    assert build.json()["artifact_formats"]["tableau"]["filename"] == "superstore_sales_dashboard_Tableau_Packaged_Workbook.twbx"
    for kind in ("powerbi", "tableau", "tableau_twb"):
        artifact = client.get(f"/api/v1/projects/superstore_sales_dashboard/artifacts/{kind}")
        assert artifact.status_code == 200, artifact.text
        assert len(artifact.content) > 100
    powerbi = client.get("/api/v1/projects/superstore_sales_dashboard/artifacts/powerbi")
    assert "superstore_sales_dashboard_PowerBI_PBIP_Project.zip" in powerbi.headers["content-disposition"]
    assert powerbi.headers["x-bi-requires-extraction"] == "true"
    tableau = client.get("/api/v1/projects/superstore_sales_dashboard/artifacts/tableau")
    assert "superstore_sales_dashboard_Tableau_Packaged_Workbook.twbx" in tableau.headers["content-disposition"]
    assert tableau.headers["x-bi-requires-extraction"] == "false"

    validation = client.post("/api/v1/project-validation/run", json={"rows": 12})
    assert validation.status_code == 200
    assert validation.json()["status"] == "COMPLETED"
    assert validation.json()["passed"] == 20
    assert validation.json()["failed"] == 0
