from __future__ import annotations


def test_health_improvement_plan_previews_and_applies_a_new_version(client):
    imported = client.post(
        "/api/v1/datasets/import",
        files={
            "file": (
                "messy_sales.csv",
                b"Customer,Amount,Order Date\n Alice ,\"1,000\",1/2/2024\nBob,2000,2024-03-04\nBob,2000,2024-03-04\n",
                "text/csv",
            )
        },
    )
    assert imported.status_code == 200, imported.text
    dataset_id = imported.json()["dataset_id"]

    plan = client.post(f"/api/v1/datasets/{dataset_id}/quality/improvement-plan")
    assert plan.status_code == 200, plan.text
    body = plan.json()
    assert body["status"] == "IMPROVEMENTS_AVAILABLE"
    assert body["approval_required"] is True
    assert body["steps"]
    assert body["after_preview"]["score"] >= body["before"]["score"]

    preview = client.post(
        f"/api/v1/datasets/{dataset_id}/cleaning/preview",
        json={"steps": body["steps"], "preview_limit": 5},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["version_created"] is False

    applied = client.post(
        f"/api/v1/datasets/{dataset_id}/cleaning/apply",
        json={"steps": body["steps"], "preview_limit": 5},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["version_created"] is True
    assert applied.json()["output_version_id"] != imported.json()["version_id"]


def test_quality_response_exposes_nested_health_contract(client, csv_file):
    imported = client.post("/api/v1/datasets/import", files=csv_file)
    dataset_id = imported.json()["dataset_id"]
    quality = client.post(f"/api/v1/datasets/{dataset_id}/quality/analyze")
    assert quality.status_code == 200
    body = quality.json()
    assert body["health"]["score"] == body["health"]["health_score"]
    assert "missing_analysis" in body
    assert "duplicate_analysis" in body
