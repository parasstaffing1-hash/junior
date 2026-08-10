from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.dashboard.templates import list_dashboard_templates
from app.main import create_app
from app.storage.dataset_storage import DatasetStorage, StoragePathError


def _import_sales_dataset(client: TestClient) -> str:
    response = client.post(
        "/api/v1/datasets/import",
        files={
            "file": (
                "sales.csv",
                b"Date,Country,Product,Segment,Sales,Profit,Units Sold\n"
                b"2025-01-01,Canada,Alpha,Consumer,100,20,5\n"
                b"2025-02-01,Canada,Beta,Corporate,200,40,8\n"
                b"2025-03-01,United States,Alpha,Consumer,300,60,12\n",
                "text/csv",
            )
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["dataset_id"]


def test_dashboard_template_catalog_and_selection_do_not_create_versions(client: TestClient):
    catalog = client.get("/api/v1/dashboard-templates")
    assert catalog.status_code == 200
    assert catalog.json()["default_template_id"] == "executive"
    assert {item["id"] for item in catalog.json()["templates"]} == {
        "executive",
        "sales_performance",
        "ecommerce_conversion",
        "operations",
        "root_cause",
    }

    dataset_id = _import_sales_dataset(client)
    versions_before = client.get(f"/api/v1/datasets/{dataset_id}/versions").json()
    for template in list_dashboard_templates():
        response = client.get(f"/api/v1/datasets/{dataset_id}/bi_report?template_id={template['id']}")
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["dashboard"]["template"]["id"] == template["id"]
        assert report["report"]["parameters"]["dashboard_template_id"] == template["id"]
        assert report["dashboard"]["layout_validation"]["valid"] is True
        assert any(widget["widget_type"] == "table" for widget in report["dashboard"]["widgets"])
        assert f"template_id={template['id']}" in report["downloads"]["pdf"]

    versions_after = client.get(f"/api/v1/datasets/{dataset_id}/versions").json()
    assert [item["id"] for item in versions_after] == [item["id"] for item in versions_before]


def test_unknown_dashboard_template_has_a_structured_error(client: TestClient):
    dataset_id = _import_sales_dataset(client)
    response = client.get(f"/api/v1/datasets/{dataset_id}/bi_report?template_id=not-real")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "DASHBOARD_TEMPLATE_NOT_FOUND"
    assert "executive" in error["details"]["available_template_ids"]


def test_storage_rejects_path_escape_and_upload_limit(tmp_path: Path):
    storage = DatasetStorage(root=tmp_path / "storage", max_upload_bytes=8)
    with pytest.raises(StoragePathError):
        storage.resolve("../outside.csv")
    with pytest.raises(StoragePathError):
        storage.resolve("/etc/passwd")

    app = create_app(
        database_url="sqlite:///" + str(tmp_path / "analytics.db"),
        storage_root=tmp_path / "limited-storage",
        max_upload_bytes=8,
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/datasets/import",
            files={"file": ("../../too-large.csv", b"name\nthis-row-is-too-large\n", "text/csv")},
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
    app.state.engine.dispose()
