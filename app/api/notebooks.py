"""Governed notebook execution endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request

from app.core.notebook_runtime import NotebookRuntimeError, execute_notebook
from app.core.security import SecurityError, assert_dataset_tenant, authorize
from app.models.all import AuditLog, Dataset, WorkspaceAsset
from app.orchestration.platform import load_current_dataset


router = APIRouter(prefix="/api/v1/workspaces", tags=["notebooks"])


def _actor(request: Request):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise SecurityError("AUTHENTICATION_REQUIRED", "A security principal is required.")
    return actor


@router.post("/{workspace_id}/notebooks/{asset_id}/run")
def run_notebook(workspace_id: str, asset_id: str, payload: dict[str, Any], request: Request):
    actor = _actor(request)
    authorize(actor, "analyze", workspace_id=workspace_id)
    db = request.app.state.SessionLocal()
    try:
        asset = db.query(WorkspaceAsset).filter(WorkspaceAsset.id == asset_id, WorkspaceAsset.tenant_id == actor.tenant_id, WorkspaceAsset.workspace_id == workspace_id, WorkspaceAsset.asset_type == "notebook").first()
        if asset is None:
            raise SecurityError("NOTEBOOK_NOT_FOUND", "The notebook was not found in this tenant workspace.", status_code=404)
        definition = asset.definition_json or {}
        dataset_id = str(payload.get("dataset_id") or asset.dataset_id or definition.get("dataset_id") or "")
        if not dataset_id:
            raise SecurityError("NOTEBOOK_DATASET_REQUIRED", "A notebook must be bound to a dataset.", status_code=422)
        dataset = assert_dataset_tenant(db, dataset_id, actor)
        _, source_version, frame = load_current_dataset(db, request.app.state.storage, dataset.id)
        cells = payload.get("cells") if "cells" in payload else definition.get("cells")
        try:
            result = execute_notebook(frame, cells or [], max_rows=int(payload.get("max_rows", 500)))
        except NotebookRuntimeError as exc:
            raise SecurityError(exc.code, exc.message, status_code=422, details=exc.details) from exc
        run_id = str(uuid.uuid4())
        manifest = {"run_id": run_id, "workspace_id": workspace_id, "asset_id": asset.id, "dataset_id": dataset.id, "source_version_id": source_version.id, "source_sha256": source_version.sha256, "language": definition.get("language", "python"), "execution_mode": "bounded_allowlisted_notebook_dsl"}
        db.add(AuditLog(tenant_id=actor.tenant_id, actor_id=actor.principal_id, action="notebook.run", resource_type="notebook", resource_id=asset.id, success=True, status_code=200, metadata_json={"manifest": manifest, "operations": result["operations"]}))
        db.commit()
        return {"run_id": run_id, "notebook_id": asset.id, "dataset_id": dataset.id, "source_version_id": source_version.id, "manifest": manifest, "result": result}
    finally:
        db.close()


@router.get("/{workspace_id}/notebooks")
def list_notebooks(workspace_id: str, request: Request):
    actor = _actor(request)
    authorize(actor, "read", workspace_id=workspace_id)
    db = request.app.state.SessionLocal()
    try:
        rows = db.query(WorkspaceAsset).filter(WorkspaceAsset.tenant_id == actor.tenant_id, WorkspaceAsset.workspace_id == workspace_id, WorkspaceAsset.asset_type == "notebook").order_by(WorkspaceAsset.name.asc()).all()
        return {"workspace_id": workspace_id, "notebooks": [{"id": row.id, "name": row.name, "dataset_id": row.dataset_id or (row.definition_json or {}).get("dataset_id"), "language": (row.definition_json or {}).get("language"), "status": row.status, "version": row.version} for row in rows]}
    finally:
        db.close()
