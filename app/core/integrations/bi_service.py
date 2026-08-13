from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx


class IntegrationError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class IntegrationPlan:
    provider: str
    operation: str
    approval_required: bool
    external_side_effect: bool
    steps: list[dict[str, Any]]


class PowerBIServiceClient:
    """Minimal Graph/Power BI REST boundary; credentials never persist in the app."""

    def __init__(self, *, base_url: str, access_token: str, timeout_seconds: float = 30):
        if not base_url or not access_token:
            raise IntegrationError("POWERBI_CREDENTIALS_REQUIRED", "Power BI base_url and access_token are required.")
        self.base_url = base_url.rstrip("/") + "/"
        self.client = httpx.Client(timeout=timeout_seconds, headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"})

    def close(self) -> None:
        self.client.close()

    def request(self, method: str, path: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if method.upper() not in {"GET", "POST", "PATCH"}:
            raise IntegrationError("POWERBI_METHOD_BLOCKED", "Only approved read and controlled write methods are supported.")
        response = self.client.request(method.upper(), urljoin(self.base_url, path.lstrip("/")), json=payload)
        if response.status_code >= 400:
            raise IntegrationError("POWERBI_REQUEST_FAILED", "Power BI request failed.", details={"status_code": response.status_code, "body": response.text[:1000]})
        if not response.content:
            return {"status_code": response.status_code}
        return response.json()


def integration_catalog() -> list[dict[str, Any]]:
    return [
        {"id": "powerbi_service", "provider": "Microsoft Power BI Service", "status": "approval_gated", "credential_profile": "entra_application_or_delegated_oauth", "operations": ["validate_token", "refresh_dataset", "list_refreshes", "publish_model"]},
        {"id": "fabric", "provider": "Microsoft Fabric", "status": "approval_gated", "credential_profile": "entra_application_or_delegated_oauth", "operations": ["validate_token", "run_pipeline", "refresh_lakehouse", "refresh_semantic_model"]},
        {"id": "microsoft_graph", "provider": "Microsoft Graph", "status": "approval_gated", "credential_profile": "microsoft_graph_application_or_delegated_oauth", "operations": ["share_workbook", "send_report", "write_sharepoint", "write_onedrive", "read_permissions"]},
        {"id": "excel_desktop", "provider": "Excel Desktop or approved hosted Excel runner", "status": "approval_gated", "credential_profile": "excel_desktop_automation", "operations": ["refresh_workbook", "refresh_power_query", "refresh_pivot_cache", "recalculate_formulas", "run_approved_macro"]},
        {"id": "sharepoint", "provider": "Microsoft SharePoint", "status": "approval_gated", "credential_profile": "microsoft_graph_application_or_delegated_oauth", "operations": ["upload_workbook", "publish_version", "read_permissions"]},
        {"id": "onedrive", "provider": "Microsoft OneDrive", "status": "approval_gated", "credential_profile": "microsoft_graph_application_or_delegated_oauth", "operations": ["upload_workbook", "publish_version", "read_permissions"]},
        {"id": "outlook", "provider": "Microsoft Outlook via Graph", "status": "approval_gated", "credential_profile": "microsoft_graph_application_or_delegated_oauth", "operations": ["send_report", "send_scheduled_report"]},
        {"id": "tableau_cloud", "provider": "Tableau Cloud", "status": "approval_gated", "credential_profile": "tableau_connected_app_or_pat", "operations": ["publish_workbook", "refresh_datasource"]},
    ]


def plan_integration(*, provider: str, operation: str, approved: bool, artifact_id: str | None = None) -> IntegrationPlan:
    allowed = {item["id"]: set(item["operations"]) for item in integration_catalog()}
    if provider not in allowed or operation not in allowed[provider]:
        raise IntegrationError("INTEGRATION_OPERATION_NOT_ALLOWED", "The requested integration operation is not allowlisted.", details={"provider": provider, "operation": operation})
    if not approved:
        return IntegrationPlan(provider, operation, True, True, [{"stage": "approval", "status": "required", "artifact_id": artifact_id}])
    return IntegrationPlan(provider, operation, True, True, [{"stage": "validate_credentials", "status": "required"}, {"stage": "validate_artifact_lineage", "status": "required", "artifact_id": artifact_id}, {"stage": "execute_external_side_effect", "status": "approved"}])
