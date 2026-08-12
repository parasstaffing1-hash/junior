from __future__ import annotations


def test_staff_control_center_exposes_automatic_and_manual_workflows(client):
    center = client.get("/api/v1/platform/staff-control-center")
    assert center.status_code == 200
    payload = center.json()

    assert {item["id"] for item in payload["operating_modes"]} == {"automatic", "manual"}
    assert len(payload["stages"]) == 8
    assert payload["release_boundary"]["approval_required"]
    assert payload["readiness"]["capability_count"] >= 25

    automatic = client.post(
        "/api/v1/platform/staff-control-center/plan",
        json={
            "mode": "automatic",
            "objective": "Build a trusted revenue dashboard",
            "requested_stages": ["intake_quality", "modeling", "bi_delivery"],
        },
    )
    assert automatic.status_code == 200
    automatic_payload = automatic.json()
    assert automatic_payload["status"] == "READY_TO_PLAN"
    assert automatic_payload["execution_mode"] == "automatic"
    assert len(automatic_payload["stages"]) == 3
    assert all(item["automatic"]["available"] for item in automatic_payload["stages"])

    manual = client.post(
        "/api/v1/datasets/demo-dataset/staff-control-center/plan",
        json={"mode": "manual", "requested_stages": ["sql_analysis"]},
    )
    assert manual.status_code == 200
    manual_payload = manual.json()
    assert manual_payload["status"] == "READY_FOR_MANUAL_REVIEW"
    assert manual_payload["dataset_id"] == "demo-dataset"
    assert manual_payload["stages"][0]["manual"]["available"] is True
    assert "write/read-only SQL" in manual_payload["stages"][0]["manual"]["controls"]

    invalid = client.post(
        "/api/v1/platform/staff-control-center/plan",
        json={"mode": "guided", "requested_stages": ["sql_analysis"]},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "STAFF_CONTROL_PLAN_INVALID"


def test_staff_control_center_is_present_in_workspace(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "STAFF BI CONTROL CENTER" in page.text
    assert 'id="staff-mode"' in page.text
    assert 'id="staff-stage-checklist"' in page.text
    assert "Build staff plan" in page.text


def test_settings_view_is_wired_to_the_workspace_shell(client):
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="nav-settings"' in page.text
    assert 'onclick="App.openSettings()"' in page.text
    assert 'id="view-settings"' in page.text
    assert 'id="settings-dashboard-template"' in page.text
    assert 'id="settings-api-status"' in page.text
    assert 'id="settings-tenant-id"' in page.text
    assert 'id="settings-api-key"' in page.text
    assert "Save session access" in page.text
