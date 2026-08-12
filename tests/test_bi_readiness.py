from __future__ import annotations


def _import_bi_dataset(client):
    content = (
        "Order Date,Region,Product,Revenue\n"
        "2026-01-01, North ,Alpha,100\n"
        "2026-02-01,South,Alpha,125\n"
        "2026-03-01,North, Beta,140\n"
        "2026-03-01,North, Beta,140\n"
    ).encode()
    response = client.post("/api/v1/datasets/import", files={"file": ("bi-ready.csv", content, "text/csv")})
    assert response.status_code == 200, response.text
    return response.json()


def test_bi_readiness_profile_and_preview_are_explainable(client):
    imported = _import_bi_dataset(client)
    dataset_id = imported["dataset_id"]

    profile = client.post(f"/api/v1/datasets/{dataset_id}/bi-readiness/profile", json={})
    assert profile.status_code == 200, profile.text
    body = profile.json()
    assert body["readiness_status"] == "REVIEW_REQUIRED"
    assert "revenue" in body["measure_columns"]
    assert "order_date" in body["date_columns"]
    assert body["model_contract"]["model_type"] == "star_schema"
    assert body["model_contract"]["fact_table"]["grain"] == "one row per BI-ready source record"
    assert body["advanced_model_contract"]["validation"]["status"] == "REVIEW_REQUIRED"
    assert body["advanced_model_contract"]["validation"]["checks"]["fact_grain_declared"] is True
    assert any(item["code"] == "DUPLICATE_ROWS_REVIEW" for item in body["blockers"])

    preview = client.post(f"/api/v1/datasets/{dataset_id}/bi-readiness/preview", json={"options": {"remove_exact_duplicates": True}})
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert preview_body["version_created"] is False
    assert preview_body["execution"]["rows_before"] == 4
    assert preview_body["execution"]["rows_after"] == 3
    assert "bi_row_id" in preview_body["preview"]["columns"]
    assert preview_body["model_contract"]["dimensions"]


def test_bi_readiness_apply_creates_immutable_child_version(client):
    imported = _import_bi_dataset(client)
    dataset_id = imported["dataset_id"]
    applied = client.post(
        f"/api/v1/datasets/{dataset_id}/bi-readiness/apply",
        json={"options": {"remove_exact_duplicates": True}},
    )
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["version_created"] is True
    assert body["output_version_id"] != imported["version_id"]
    assert body["lineage"]["parent_version_id"] == imported["version_id"]
    assert body["execution"]["rows_after"] == 3
    assert body["profile"]["model_contract"]["fact_table"]["name"] == "FactData"

    versions = client.get(f"/api/v1/datasets/{dataset_id}/versions")
    assert versions.status_code == 200
    assert len(versions.json()) == 2
    assert versions.json()[-1]["is_current"] is True


def test_bi_readiness_catalog_and_ui_surface(client):
    catalog = client.get("/api/v1/bi-readiness/catalog")
    assert catalog.status_code == 200
    assert catalog.json()["mode"] == "approval_first"
    assert catalog.json()["safety"]["source_overwrite"] is False
    page = client.get("/")
    assert page.status_code == 200
    assert "BI-ready data" in page.text
    assert "GOVERNED BI HANDOFF" in page.text
