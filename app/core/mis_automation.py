"""Governed Excel/MIS automation plans.

The server can generate and validate source-backed workbooks, but it cannot
truthfully claim to drive Excel Desktop, VBA, SharePoint, OneDrive or Outlook
without an explicitly configured external identity.  This module makes that
boundary part of the artifact contract instead of hiding it in a note.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable


class MISAutomationError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


_DESTINATIONS = {"local_download", "sharepoint", "onedrive", "outlook"}


def _clean_columns(columns: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in columns:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result


def build_mis_automation_plan(
    columns: Iterable[Any],
    *,
    dataset_id: str | None = None,
    source_version_id: str | None = None,
    source_sha256: str | None = None,
    row_count: int | None = None,
    report_name: str | None = None,
    mode: str = "manual",
    schedule: dict[str, Any] | None = None,
    delivery: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Return a reviewable, lineage-aware MIS workflow contract.

    ``manual`` means an operator approves each handoff.  ``automatic`` still
    requires an approved schedule and external credentials before delivery;
    it never silently runs macros or sends email.
    """
    normalized_columns = _clean_columns(columns)
    if not normalized_columns:
        raise MISAutomationError("MIS_COLUMNS_REQUIRED", "At least one source column is required.")
    normalized_mode = str(mode or "manual").strip().casefold()
    if normalized_mode not in {"manual", "automatic"}:
        raise MISAutomationError("MIS_MODE_INVALID", "mode must be manual or automatic.", {"mode": mode})
    if normalized_mode == "automatic" and not isinstance(schedule, dict):
        raise MISAutomationError("MIS_SCHEDULE_REQUIRED", "Automatic MIS mode requires an explicit schedule definition.")

    destinations = list(dict.fromkeys(str(item).strip().casefold() for item in (delivery or ["local_download"])))
    invalid_destinations = sorted(set(destinations) - _DESTINATIONS)
    if invalid_destinations:
        raise MISAutomationError("MIS_DESTINATION_INVALID", "One or more delivery destinations are not supported.", {"invalid": invalid_destinations, "allowed": sorted(_DESTINATIONS)})

    generated_at = datetime.now(timezone.utc).isoformat()
    external_gates = [
        {
            "id": "excel_desktop_refresh",
            "status": "EXTERNAL_REQUIRED",
            "reason": "Excel Desktop or an approved hosted Excel runner is required to refresh native workbook connections and pivot caches.",
            "credential_profile": "excel_desktop_or_graph_oauth",
        },
        {
            "id": "vba_and_xlsm",
            "status": "EXTERNAL_REQUIRED",
            "reason": "Macros are not executed by the server. An approved signed .xlsm package and isolated Excel runner are required.",
            "credential_profile": "excel_desktop_automation",
        },
        {
            "id": "microsoft_365_delivery",
            "status": "EXTERNAL_REQUIRED" if any(item != "local_download" for item in destinations) else "NOT_REQUESTED",
            "reason": "SharePoint, OneDrive and Outlook delivery require Microsoft Graph permissions, tenant consent and recipient policy approval.",
            "credential_profile": "microsoft_graph_application_or_delegated_oauth",
        },
    ]
    workflow = [
        {"step": "source_lineage", "owner": "platform", "status": "implemented", "control": "dataset/version/hash recorded"},
        {"step": "generate_workbook", "owner": "platform", "status": "implemented", "control": "XLSX, formulas, controls and reviewable M plan"},
        {"step": "reconcile_and_review_exceptions", "owner": "operator", "status": "approval_required", "control": "row counts, numeric totals, duplicates and missing values"},
        {"step": "refresh_native_excel_features", "owner": "external_excel_runner", "status": "external_required", "control": "Power Query, PivotTables, slicers and formulas"},
        {"step": "approve_distribution", "owner": "business_owner", "status": "approval_required", "control": "recipient, period, permissions and retention"},
        {"step": "deliver", "owner": "external_m365_or_operator", "status": "external_required" if any(item != "local_download" for item in destinations) else "operator_download", "control": ", ".join(destinations)},
    ]
    return {
        "contract": "mis_automation_v1",
        "status": "REVIEW_REQUIRED",
        "mode": normalized_mode,
        "report_name": str(report_name or "MIS report"),
        "generated_at": generated_at,
        "source": {
            "dataset_id": dataset_id,
            "source_version_id": source_version_id,
            "source_sha256": source_sha256,
            "row_count": int(row_count) if row_count is not None else None,
            "columns": normalized_columns,
        },
        "schedule": schedule if normalized_mode == "automatic" else None,
        "delivery": {"destinations": destinations, "approval_required": True},
        "generated_artifacts": {
            "xlsx": {"status": "implemented", "extension": ".xlsx", "includes": ["dashboard", "raw_data", "formula_lab", "power_query_plan", "exceptions", "reconciliation", "mis_control"]},
            "xlsm": {"status": "external_required", "extension": ".xlsm", "reason": "Signed macro policy and Excel Desktop runner are required."},
            "native_pivottable_and_slicers": {"status": "external_required", "reason": "Native cache refresh and slicer synchronization are target-engine operations."},
            "powerbi_pbip": {"status": "implemented", "extension": ".pbip.zip"},
            "tableau_twbx": {"status": "implemented", "extension": ".twbx"},
        },
        "controls": [
            "source version and hash are retained",
            "row-count and numeric-total reconciliation is generated",
            "duplicate and missing-value exceptions are visible",
            "formula recalculation is requested on workbook open",
            "server-side macro execution is disabled by default",
            "distribution is approval-gated and auditable",
        ],
        "workflow": workflow,
        "external_gates": external_gates,
        "publication": {"allowed": False, "reason": "Business approval and external Excel/Microsoft 365 gates are incomplete."},
    }


def validate_mis_automation_plan(plan: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise MISAutomationError("MIS_PLAN_INVALID", "The MIS automation plan must be a JSON object.")
    required = {"contract", "mode", "source", "workflow", "external_gates", "publication"}
    missing = sorted(required - set(plan))
    if missing:
        raise MISAutomationError("MIS_PLAN_INVALID", "The MIS automation plan is missing required fields.", {"missing": missing})
    source = plan.get("source") if isinstance(plan.get("source"), dict) else {}
    if not source.get("columns"):
        raise MISAutomationError("MIS_PLAN_INVALID", "The MIS automation plan must retain source columns.")
    return {
        "valid": True,
        "contract": plan.get("contract"),
        "mode": plan.get("mode"),
        "external_gate_count": len(plan.get("external_gates") or []),
        "publication_allowed": bool((plan.get("publication") or {}).get("allowed")),
    }


__all__ = ["MISAutomationError", "build_mis_automation_plan", "validate_mis_automation_plan"]
