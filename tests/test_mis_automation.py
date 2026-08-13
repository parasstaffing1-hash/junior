from app.core.mis_automation import build_mis_automation_plan, validate_mis_automation_plan


def test_mis_plan_explicitly_separates_local_outputs_and_external_gates():
    plan = build_mis_automation_plan(
        ["OrderDate", "Region", "Sales"],
        dataset_id="dataset-1",
        source_version_id="version-1",
        source_sha256="abc123",
        row_count=10,
        mode="automatic",
        schedule={"cron": "0 8 * * 1-5", "timezone": "Asia/Kolkata"},
        delivery=["sharepoint", "outlook"],
    )
    assert plan["status"] == "REVIEW_REQUIRED"
    assert plan["source"]["source_version_id"] == "version-1"
    assert plan["generated_artifacts"]["xlsx"]["status"] == "implemented"
    assert plan["generated_artifacts"]["xlsm"]["status"] == "external_required"
    assert plan["generated_artifacts"]["native_pivottable_and_slicers"]["status"] == "external_required"
    assert {item["id"] for item in plan["external_gates"]} == {"excel_desktop_refresh", "vba_and_xlsm", "microsoft_365_delivery"}
    assert plan["publication"]["allowed"] is False
    assert validate_mis_automation_plan(plan)["valid"] is True


def test_mis_automatic_mode_requires_a_schedule():
    try:
        build_mis_automation_plan(["id"], mode="automatic")
    except Exception as exc:
        assert getattr(exc, "code", None) == "MIS_SCHEDULE_REQUIRED"
    else:
        raise AssertionError("automatic MIS mode must require a schedule")


def test_mis_api_persists_lineage_aware_plan_and_audit(client):
    catalog = client.get("/api/v1/integrations/catalog")
    assert catalog.status_code == 200
    integration_ids = {item["id"] for item in catalog.json()["integrations"]}
    assert {"excel_desktop", "sharepoint", "onedrive", "outlook"}.issubset(integration_ids)
    gate = client.post("/api/v1/integrations/plan", json={"provider": "excel_desktop", "operation": "refresh_pivot_cache", "approved": False})
    assert gate.status_code == 200
    assert gate.json()["steps"][0]["status"] == "required"

    imported = client.post(
        "/api/v1/datasets/import",
        files={"file": ("mis.csv", b"Date,Region,Sales\n2026-01-01,North,100\n2026-01-02,South,200\n", "text/csv")},
    )
    assert imported.status_code in {200, 201}, imported.text
    dataset_id = imported.json()["dataset_id"]
    response = client.post(
        f"/api/v1/datasets/{dataset_id}/mis/automation-plan",
        json={"mode": "manual", "delivery": ["local_download"], "workspace_id": "default"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["plan"]["source"]["dataset_id"] == dataset_id
    assert body["workspace_asset"]["asset_type"] == "mis_automation_plan"
    assert body["audit_event_id"]
    assert body["publication"]["allowed"] is False
