from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.core.integrations.bi_service import IntegrationError, PowerBIServiceClient, integration_catalog, plan_integration
from app.core.security import SecurityError, authorize


router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])


def _actor(request: Request):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise SecurityError("AUTHENTICATION_REQUIRED", "A security principal is required.")
    return actor


@router.get("/catalog")
def catalog(request: Request):
    authorize(_actor(request), "read")
    return {"integrations": integration_catalog(), "credentials_persisted": False, "external_side_effects_default": "approval_required"}


@router.post("/plan")
def integration_plan(payload: dict[str, Any], request: Request):
    authorize(_actor(request), "deploy")
    try:
        plan = plan_integration(provider=str(payload.get("provider", "")), operation=str(payload.get("operation", "")), approved=bool(payload.get("approved", False)), artifact_id=payload.get("artifact_id"))
        return {"provider": plan.provider, "operation": plan.operation, "approval_required": plan.approval_required, "external_side_effect": plan.external_side_effect, "steps": plan.steps}
    except IntegrationError as exc:
        raise SecurityError(exc.code, exc.message, status_code=422, details=exc.details) from exc


@router.post("/powerbi/validate")
def validate_powerbi(payload: dict[str, Any], request: Request):
    authorize(_actor(request), "deploy")
    client = None
    try:
        client = PowerBIServiceClient(base_url=str(payload.get("base_url", "")), access_token=str(payload.get("access_token", "")), timeout_seconds=float(payload.get("timeout_seconds", 30)))
        if not bool(payload.get("approved", False)):
            return {"valid": True, "dry_run": True, "message": "Credentials were structurally accepted; no external request was made until approval is supplied."}
        result = client.request("GET", str(payload.get("validation_path", "v1.0/myorg/groups")))
        return {"valid": True, "dry_run": False, "response": result}
    except IntegrationError as exc:
        raise SecurityError(exc.code, exc.message, status_code=422, details=exc.details) from exc
    finally:
        if client:
            client.close()
