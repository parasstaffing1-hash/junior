from __future__ import annotations

from io import BytesIO
import json
from zipfile import ZipFile

from app.core.enterprise.bi_architecture import enterprise_bi_blueprint, senior_bi_capability_matrix
from app.core.projects.fixtures import build_project_fixture
from app.core.projects.workbench import build_project
from app.core.reporting.bi_exports import validate_powerbi_project_package


def test_senior_bi_matrix_has_exact_priority_and_honest_delivery_status():
    matrix = senior_bi_capability_matrix()
    assert matrix["domain_count"] == 10
    assert matrix["priority_order"] == [
        "sql", "data_modeling", "dax", "power_query", "power_bi_performance",
        "microsoft_fabric", "python", "data_warehousing", "power_bi_service_admin", "git_ci_cd",
    ]
    assert {item["status"] for item in matrix["domains"]}.issubset({"implemented", "generated_template", "integration_required"})
    fabric = next(item for item in matrix["domains"] if item["id"] == "microsoft_fabric")
    assert fabric["status"] == "integration_required"
    assert matrix["counts"]["integration_required"] >= 1
    assert "never reported as executed" in matrix["claim_policy"]


def test_enterprise_blueprint_covers_scale_security_performance_and_tooling():
    blueprint = enterprise_bi_blueprint(dataset_name="BTS Flights", source_rows=5_000_000_000)
    assert blueprint["source_rows_design_target"] == 5_000_000_000
    layers = [item["layer"] for item in blueprint["architecture"]]
    assert layers == [
        "Source", "Data Lake / Warehouse", "Partitioned fact tables", "Aggregation layer",
        "Enterprise semantic model", "Incremental refresh / Direct Lake", "DAX", "Security", "Reports",
    ]
    assert blueprint["performance"]["interaction_slo"]["p95_seconds"] == 3.0
    assert {"DAX Studio", "VertiPaq Analyzer", "Tabular Editor", "ALM Toolkit"}.issubset(blueprint["monitoring"]["tooling"])
    files = blueprint["files"]
    assert {"PARTITION BY", "CREATE INDEX", "MATERIALIZED VIEW"}.issubset({token for token in ["PARTITION BY", "CREATE INDEX", "MATERIALIZED VIEW"] if token in files["engineering/postgresql_scale_pattern.sql"]})
    m_query = files["engineering/incremental_refresh.pq"]
    assert all(token in m_query for token in ("RangeStart", "RangeEnd", "Table.SelectRows", "try"))
    dax = files["engineering/advanced_dax_library.dax"]
    assert all(token in dax for token in ("CALCULATE", "RANKX", "SUMX", "TREATAS", "SWITCH"))
    pipeline = files["engineering/semantic_model_ci.yml"]
    assert all(token in pipeline for token in ("pull_request", "test", "production", "rollback"))


def test_pbip_contains_enterprise_engineering_assets_and_api(client):
    frame = build_project_fixture("superstore_sales_dashboard", rows=48)
    package = build_project(frame, project_id="superstore_sales_dashboard")["powerbi_bytes"]
    validation = validate_powerbi_project_package(package)
    assert validation["valid"] is True
    with ZipFile(BytesIO(package)) as archive:
        names = set(archive.namelist())
        required = {
            "ENTERPRISE_ARCHITECTURE.md",
            "enterprise_blueprint.json",
            "engineering/postgresql_scale_pattern.sql",
            "engineering/incremental_refresh.pq",
            "engineering/advanced_dax_library.dax",
            "engineering/tabular_editor_calculation_groups.csx",
            "engineering/semantic_model_ci.yml",
            "DAX_ANALYSIS.json",
            "POWER_QUERY_ANALYSIS.json",
        }
        assert required.issubset(names)
        contract = json.loads(archive.read("enterprise_blueprint.json"))
        assert contract["capability_matrix"]["domain_count"] == 10
        model_contract = json.loads(archive.read("model_contract.json"))
        assert model_contract["local_engineering_review"]["dax"]["measure_count"] >= 1
        assert model_contract["local_engineering_review"]["power_query"]["external_execution_required"] is True

    matrix_response = client.get("/api/v1/platform/enterprise-bi-capabilities")
    assert matrix_response.status_code == 200
    assert matrix_response.json()["domain_count"] == 10
    blueprint_response = client.get("/api/v1/platform/enterprise-bi-blueprint", params={"dataset_name": "BTS Flights", "source_rows": 5_000_000_000})
    assert blueprint_response.status_code == 200
    assert blueprint_response.json()["source_rows_design_target"] == 5_000_000_000
