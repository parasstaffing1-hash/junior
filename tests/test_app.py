from __future__ import annotations


def _import_dataset(client, csv_file):
    response = client.post("/api/v1/datasets/import", files=csv_file)
    assert response.status_code == 200, response.text
    return response.json()


def test_health_and_readiness(client):
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Automated Data Analyst" in dashboard.text
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ready"}


def test_inspect_import_list_preview_and_source(client, csv_file):
    inspection = client.post("/api/v1/datasets/import/inspect", files=csv_file)
    assert inspection.status_code == 200
    assert inspection.json()["columns"] == ["name", "age", "region"]
    assert inspection.json()["row_count"] == 3

    imported = _import_dataset(client, csv_file)
    dataset_id = imported["dataset_id"]

    listed = client.get("/api/v1/datasets")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == dataset_id

    preview = client.get(f"/api/v1/datasets/{dataset_id}/preview?limit=10")
    assert preview.status_code == 200
    assert preview.json()["schema"]["column_count"] == 3
    assert preview.json()["preview"]["rows"][1]["age"] is None
    assert preview.json()["analysis_available"] is False

    source = client.get(f"/api/v1/datasets/{dataset_id}/source")
    assert source.status_code == 200
    assert source.headers["content-type"].startswith("text/csv")
    assert "name,age,region" in source.text


def test_automated_analyst_creates_clean_version_and_updates_preview(client, csv_file):
    imported = _import_dataset(client, csv_file)
    dataset_id = imported["dataset_id"]

    result = client.post(f"/api/v1/datasets/{dataset_id}/automated_analyst")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["status"] == "COMPLETED_AUTOMATED_ANALYST"
    assert body["initial_health_score"] < 95
    assert body["cleaned"] is True
    assert body["final_version_id"] != imported["version_id"]

    preview = client.get(f"/api/v1/datasets/{dataset_id}/preview?limit=10")
    assert preview.status_code == 200
    assert preview.json()["analysis_available"] is True
    assert preview.json()["schema"]["row_count"] == 2
    assert preview.json()["health_score"]["score"] == body["initial_health_score"]
    listed = client.get("/api/v1/datasets")
    assert listed.json()[0]["health_score"]["score"] == body["initial_health_score"]


def test_missing_dataset_is_a_structured_404(client):
    response = client.get("/api/v1/datasets/not-a-real-id/preview")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DATASET_NOT_FOUND"


def test_unsupported_upload_is_a_structured_415(client):
    files = {"file": ("sample.exe", b"not a dataset", "application/octet-stream")}
    response = client.post("/api/v1/datasets/import/inspect", files=files)
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_file_type"


def test_bi_report_dashboard_filters_and_exports(client):
    files = {
        "file": (
            "sales.csv",
            b"Date,Country,Product,Segment,Sales,Profit,Units Sold\n"
            b"2025-01-01,Canada,Alpha,Consumer,100,20,5\n"
            b"2025-02-01,Canada,Beta,Corporate,200,40,8\n"
            b"2025-03-01,United States,Alpha,Consumer,300,60,12\n",
            "text/csv",
        )
    }
    imported = _import_dataset(client, files)
    dataset_id = imported["dataset_id"]

    report_response = client.get(f"/api/v1/datasets/{dataset_id}/bi_report")
    assert report_response.status_code == 200, report_response.text
    report = report_response.json()
    assert report["source"]["row_count"] == 3
    assert {item["label"] for item in report["kpis"]} >= {"Total sales", "Total profit", "Profit margin"}
    assert {item["title"] for item in report["charts"]} == {"Sales by country", "Profit by product", "Sales trend over time"}
    assert report["dashboard"]["layout_validation"]["valid"] is True
    assert all("artifact_path" not in chart for chart in report["charts"])

    for file_format, content_type, signature in (
        ("html", "text/html", b"<!doctype html>"),
        ("pdf", "application/pdf", b"%PDF"),
        ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", b"PK"),
    ):
        exported = client.get(f"/api/v1/datasets/{dataset_id}/bi_report/{file_format}")
        assert exported.status_code == 200, exported.text
        assert exported.headers["content-type"].startswith(content_type)
        assert exported.content.startswith(signature)
        assert len(exported.content) > 100

    filtered = client.get(f"/api/v1/datasets/{dataset_id}/bi_report?country=Canada")
    assert filtered.status_code == 200
    assert filtered.json()["source"]["row_count"] == 2
    assert filtered.json()["applied_filters"] == {"country": "Canada"}


def test_unified_api_groups_and_full_automated_workflow(client):
    files = {
        "file": (
            "business.csv",
            b"Date,Country,Product,Sales,Profit\n"
            b"2025-01-01,Canada,Alpha,100,20\n"
            b"2025-02-01,Canada,Beta,200,40\n"
            b"2025-03-01,United States,Alpha,300,60\n",
            "text/csv",
        )
    }
    imported = _import_dataset(client, files)
    dataset_id = imported["dataset_id"]

    quality = client.post(f"/api/v1/datasets/{dataset_id}/quality/analyze", json={})
    assert quality.status_code == 200
    assert quality.json()["source_version_id"] == imported["version_id"]
    assert "basic_schema" in quality.json()

    cleaning = client.post(
        f"/api/v1/datasets/{dataset_id}/cleaning/preview",
        json={"steps": [{"type": "string_cleaning", "params": {"columns": ["Country"], "operations": ["trim"]}}]},
    )
    assert cleaning.status_code == 200
    assert cleaning.json()["version_created"] is False

    transformation = client.post(
        f"/api/v1/datasets/{dataset_id}/transformations/preview",
        json={"steps": [{"tool": "calculated_columns", "parameters": {"calculations": [{"name": "Margin", "expression": {"op": "divide", "left": {"op": "column", "name": "Profit"}, "right": {"op": "column", "name": "Sales"}}}]}}]},
    )
    assert transformation.status_code == 200, transformation.text
    assert "Margin" in transformation.json()["preview"]["columns"]

    statistics = client.post(f"/api/v1/datasets/{dataset_id}/statistics/summary", json={})
    assert statistics.status_code == 200
    assert "descriptive" in statistics.json()["sections"]
    assert client.post(f"/api/v1/datasets/{dataset_id}/eda/report", json={}).status_code == 200
    assert client.post(f"/api/v1/datasets/{dataset_id}/findings", json={}).status_code == 200
    assert client.post(f"/api/v1/datasets/{dataset_id}/visualization/recommend", json={}).status_code == 200

    kpi = client.post(
        f"/api/v1/datasets/{dataset_id}/kpis/calculate",
        json={"definition": {"definition_type": "aggregate", "components": {"value": {"aggregation": "sum", "column": "Sales"}}}},
    )
    assert kpi.status_code == 200
    assert kpi.json()["total"]["value"] == 600.0

    actions = client.get("/api/v1/automation/actions")
    assert actions.status_code == 200
    assert any(action["action"] == "quality.analyze" for action in actions.json()["actions"])
    plan = client.post("/api/v1/automation/plan", json={"action": "quality.analyze", "context": {"dataset_id": dataset_id}, "payload": {}})
    assert plan.status_code == 200
    assert plan.json()["path"].endswith(f"/datasets/{dataset_id}/quality/analyze")

    analysis = client.post("/api/v1/automated-analyst/analyze", json={"dataset_id": dataset_id})
    assert analysis.status_code == 200, analysis.text
    body = analysis.json()
    assert body["status"] == "COMPLETED_AUTOMATED_ANALYST"
    assert "statistics" in body and "eda" in body and "findings" in body
    assert body["report"]["files"]["pdf"]

    versions = client.get(f"/api/v1/datasets/{dataset_id}/versions")
    lineage = client.get(f"/api/v1/datasets/{dataset_id}/lineage")
    latest = client.get(f"/api/v1/datasets/{dataset_id}/analysis")
    assert versions.status_code == lineage.status_code == latest.status_code == 200
    assert len(versions.json()) >= 1
    assert lineage.json()["events"]
    assert lineage.json()["artifacts"]
    assert latest.json()["result"]["status"] == "COMPLETED_AUTOMATED_ANALYST"
