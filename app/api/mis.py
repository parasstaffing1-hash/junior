"""Excel/MIS workflow planning and governed delivery boundaries."""

from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import APIRouter, Request

from app.core.mis_automation import MISAutomationError, build_mis_automation_plan, validate_mis_automation_plan
from app.core.security import SecurityError, assert_dataset_tenant, authorize
from app.models.all import AuditEvent, DatasetVersion, WorkspaceAsset, utc_now


router = APIRouter(prefix="/api/v1", tags=["mis-automation"])


def _actor(request: Request):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise SecurityError("AUTHENTICATION_REQUIRED", "A security principal is required.")
    return actor


def _asset_public(asset: WorkspaceAsset) -> dict[str, Any]:
    return {
        "id": asset.id,
        "tenant_id": asset.tenant_id,
        "workspace_id": asset.workspace_id,
        "asset_type": asset.asset_type,
        "name": asset.name,
        "dataset_id": asset.dataset_id,
        "definition": asset.definition_json,
        "status": asset.status,
        "version": asset.version,
        "created_at": asset.created_at,
        "updated_at": asset.updated_at,
    }


@router.get("/mis/catalog")
def mis_catalog():
    return {
        "capability": "Governed Excel/MIS automation",
        "modes": {
            "manual": "Operator controls each refresh, reconciliation, approval and handoff.",
            "automatic": "Scheduled generation is supported, but publication and external Excel/M365 actions remain approval-gated.",
        },
        "local_outputs": ["xlsx", "powerbi_pbip_zip", "tableau_twbx", "mis_automation_plan"],
        "external_gates": ["excel_desktop_refresh", "vba_and_xlsm", "native_pivot_tables_and_slicers", "sharepoint", "onedrive", "outlook"],
        "safety": {"server_side_macros": False, "silent_distribution": False, "source_overwrite": False},
    }


@router.post("/mis/automation-plan/validate")
def validate_mis_plan(payload: dict[str, Any]):
    try:
        return validate_mis_automation_plan(payload.get("plan") or payload)
    except MISAutomationError as exc:
        raise SecurityError(exc.code, exc.message, status_code=422, details=exc.details) from exc


@router.post("/datasets/{dataset_id}/mis/automation-plan", status_code=201)
def create_mis_automation_plan(dataset_id: str, payload: dict[str, Any] | None, request: Request):
    actor = _actor(request)
    payload = payload or {}
    workspace_id = str(payload.get("workspace_id") or "default")
    authorize(actor, "write", workspace_id=workspace_id)
    db = request.app.state.SessionLocal()
    try:
        dataset = assert_dataset_tenant(db, dataset_id, actor)
        version_id = str(payload.get("version_id") or dataset.current_version_id or "")
        version = db.query(DatasetVersion).filter(DatasetVersion.id == version_id, DatasetVersion.dataset_id == dataset_id).first()
        if version is None:
            raise SecurityError("VERSION_NOT_FOUND", "The requested dataset version was not found.", status_code=404, details={"dataset_id": dataset_id, "version_id": version_id})
        try:
            frame = pd.read_csv(request.app.state.storage.resolve(version.storage_path))
        except pd.errors.EmptyDataError:
            frame = pd.DataFrame()
        plan = build_mis_automation_plan(
            frame.columns,
            dataset_id=dataset.id,
            source_version_id=version.id,
            source_sha256=version.sha256,
            row_count=len(frame),
            report_name=str(payload.get("report_name") or dataset.name),
            mode=str(payload.get("mode") or "manual"),
            schedule=payload.get("schedule"),
            delivery=payload.get("delivery") or ["local_download"],
        )
        asset_name = str(payload.get("name") or f"{dataset.name} MIS Automation")[:200]
        asset = db.query(WorkspaceAsset).filter(
            WorkspaceAsset.tenant_id == actor.tenant_id,
            WorkspaceAsset.workspace_id == workspace_id,
            WorkspaceAsset.asset_type == "mis_automation_plan",
            WorkspaceAsset.name == asset_name,
        ).first()
        if asset is None:
            asset = WorkspaceAsset(
                tenant_id=actor.tenant_id,
                workspace_id=workspace_id,
                asset_type="mis_automation_plan",
                name=asset_name,
                description="Lineage-aware Excel/MIS refresh and distribution contract.",
                dataset_id=dataset.id,
                definition_json=plan,
                status="draft",
                owner=getattr(actor, "principal_id", None),
                tags=["mis", "excel", plan["mode"]],
                version=1,
            )
            db.add(asset)
        else:
            asset.definition_json = plan
            asset.dataset_id = dataset.id
            asset.status = "draft" if asset.status == "published" else asset.status
            asset.version += 1
            asset.updated_at = utc_now()
        db.flush()
        audit = AuditEvent(
            dataset_id=dataset.id,
            input_version_id=version.id,
            output_version_id=None,
            engine="MISAutomationPlan",
            parameters={"workspace_id": workspace_id, "asset_id": asset.id, "mode": plan["mode"], "destinations": (plan.get("delivery") or {}).get("destinations", [])},
            affected_rows=len(frame),
            affected_columns=len(frame.columns),
            before_stats={"source_sha256": version.sha256, "source_version_id": version.id},
            after_stats={"publication_allowed": False, "external_gate_count": len(plan.get("external_gates") or [])},
            timestamp=utc_now(),
        )
        db.add(audit)
        db.commit()
        db.refresh(asset)
        return {
            "dataset_id": dataset.id,
            "source_version_id": version.id,
            "plan": plan,
            "workspace_asset": _asset_public(asset),
            "audit_event_id": audit.id,
            "publication": plan["publication"],
        }
    except MISAutomationError as exc:
        db.rollback()
        raise SecurityError(exc.code, exc.message, status_code=422, details=exc.details) from exc
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


__all__ = ["router"]
