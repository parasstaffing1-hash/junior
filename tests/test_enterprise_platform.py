from __future__ import annotations


def _import_sales_dataset(client) -> dict:
    response = client.post(
        "/api/v1/datasets/import",
        files={
            "file": (
                "sales.csv",
                b"Region,Product,Sales,Profit\nNorth,A,100,20\nNorth,B,200,30\nSouth,A,300,60\n",
                "text/csv",
            )
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_acquisition_readiness_is_evidence_based(client):
    catalog = client.get("/api/v1/platform/capabilities")
    assert catalog.status_code == 200
    assert catalog.json()["count"] == len(catalog.json()["capabilities"])
    assert catalog.json()["count"] >= 30
    assert {item["status"] for item in catalog.json()["capabilities"]} == {"available", "partial", "planned"}

    readiness = client.get("/api/v1/platform/acquisition-readiness")
    assert readiness.status_code == 200
    body = readiness.json()
    assert body["score"] < 90
    assert body["acquisition_ready"] is False
    assert body["grade"] == "INVESTMENT_REQUIRED"
    assert "warehouse_connectors" in {item["id"] for item in body["critical_blockers"]}


def test_sql_workbench_validates_and_executes_read_only_queries(client):
    assert client.post("/api/v1/sql/validate", json={"sql": "SELECT * FROM dataset"}).json()["read_only"] is True
    for unsafe_sql in ("DELETE FROM dataset", "SELECT * FROM dataset; DROP TABLE dataset"):
        response = client.post("/api/v1/sql/validate", json={"sql": unsafe_sql})
        assert response.status_code == 422

    imported = _import_sales_dataset(client)
    result = client.post(
        f"/api/v1/datasets/{imported['dataset_id']}/sql/query",
        json={
            "sql": "SELECT Region, SUM(Sales) AS total_sales FROM dataset GROUP BY Region ORDER BY total_sales DESC, Region DESC",
            "max_rows": 10,
        },
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["source_version_id"] == imported["version_id"]
    assert body["columns"] == ["Region", "total_sales"]
    assert body["rows"] == [
        {"Region": "South", "total_sales": 300},
        {"Region": "North", "total_sales": 300},
    ]
    assert body["truncated"] is False


def test_governed_workspace_assets_version_lineage_and_publish(client):
    imported = _import_sales_dataset(client)
    workspace_id = "investment-committee"
    semantic = client.post(
        f"/api/v1/workspaces/{workspace_id}/assets",
        json={
            "asset_type": "semantic_model",
            "name": "Sales Model",
            "dataset_id": imported["dataset_id"],
            "owner": "analytics@example.com",
            "tags": ["finance", "certified-candidate"],
            "definition": {"dimensions": ["Region", "Product"], "measures": {"sales": "SUM(Sales)"}},
        },
    )
    assert semantic.status_code == 201, semantic.text
    semantic_asset = semantic.json()

    kpi = client.post(
        f"/api/v1/workspaces/{workspace_id}/assets",
        json={
            "asset_type": "kpi",
            "name": "Total Sales",
            "definition": {"expression": "SUM(Sales)", "depends_on": [semantic_asset["id"]]},
        },
    )
    assert kpi.status_code == 201, kpi.text
    kpi_asset = kpi.json()

    listed = client.get(f"/api/v1/workspaces/{workspace_id}/assets")
    assert listed.status_code == 200
    assert listed.json()["count"] == 2

    validation = client.post(f"/api/v1/workspaces/{workspace_id}/validate")
    assert validation.json()["valid"] is True

    lineage = client.get(f"/api/v1/workspaces/{workspace_id}/lineage")
    assert lineage.status_code == 200
    assert {edge["relationship"] for edge in lineage.json()["edges"]} == {"source_dataset", "depends_on"}

    blocked_publish = client.post(f"/api/v1/workspaces/{workspace_id}/assets/{kpi_asset['id']}/publish")
    assert blocked_publish.status_code == 422
    assert blocked_publish.json()["error"]["details"]["unpublished_dependencies"][0]["asset_id"] == semantic_asset["id"]

    semantic_published = client.post(f"/api/v1/workspaces/{workspace_id}/assets/{semantic_asset['id']}/publish")
    assert semantic_published.status_code == 200
    published = client.post(f"/api/v1/workspaces/{workspace_id}/assets/{kpi_asset['id']}/publish")
    assert published.status_code == 200
    assert published.json()["asset"]["status"] == "published"
    assert published.json()["asset"]["version"] == 2

    revised = client.put(
        f"/api/v1/workspaces/{workspace_id}/assets/{kpi_asset['id']}",
        json={"definition": {"expression": "SUM(Sales) * 1.01", "depends_on": [semantic_asset["id"]]}},
    )
    assert revised.status_code == 200
    assert revised.json()["status"] == "draft"
    assert revised.json()["version"] == 3

    duplicate = client.post(
        f"/api/v1/workspaces/{workspace_id}/assets",
        json={"asset_type": "kpi", "name": "Total Sales", "definition": {"expression": "SUM(Sales)"}},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "WORKSPACE_ASSET_CONFLICT"

    invalid = client.post(
        f"/api/v1/workspaces/{workspace_id}/assets",
        json={"asset_type": "dashboard", "name": "Executive Dashboard", "definition": {}},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["details"]["missing"] == ["widgets"]

    direct_publish = client.post(
        f"/api/v1/workspaces/{workspace_id}/assets",
        json={"asset_type": "kpi", "name": "Bypass", "status": "published", "definition": {"expression": "1"}},
    )
    assert direct_publish.status_code == 422
