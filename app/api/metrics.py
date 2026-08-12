from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.core.kpi.calculator import calculate_kpi
from app.core.security import SecurityError, authorize, assert_dataset_tenant
from app.models.all import DatasetVersion, WorkspaceAsset
import pandas as pd


router = APIRouter(prefix="/api/v1/metrics", tags=["semantic-metrics"])


def _actor(request: Request):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise SecurityError("AUTHENTICATION_REQUIRED", "A security principal is required.")
    return actor


@router.get("/catalog")
def metric_catalog(request: Request, workspace_id: str = "default"):
    actor = _actor(request)
    authorize(actor, "read", workspace_id=workspace_id)
    db = request.app.state.SessionLocal()
    try:
        rows = db.query(WorkspaceAsset).filter(WorkspaceAsset.workspace_id == workspace_id, WorkspaceAsset.asset_type.in_(["metric", "kpi"]), WorkspaceAsset.status.in_(["certified", "published", "approved", "draft"])).order_by(WorkspaceAsset.name.asc()).all()
        return {"workspace_id": workspace_id, "metrics": [{"id": row.id, "name": row.name, "description": row.description, "status": row.status, "owner": row.owner, "version": row.version, "definition": row.definition_json} for row in rows]}
    finally:
        db.close()


@router.post("/query")
def query_metric(payload: dict[str, Any], request: Request):
    actor = _actor(request)
    authorize(actor, "analyze", workspace_id=str(payload.get("workspace_id", "default")))
    dataset_id = str(payload.get("dataset_id", ""))
    metric_name = str(payload.get("metric", "")).strip()
    if not dataset_id or not metric_name:
        raise SecurityError("METRIC_QUERY_FIELDS_REQUIRED", "dataset_id and metric are required.", status_code=422)
    db = request.app.state.SessionLocal()
    try:
        dataset = assert_dataset_tenant(db, dataset_id, actor)
        asset = db.query(WorkspaceAsset).filter(WorkspaceAsset.workspace_id == str(payload.get("workspace_id", "default")), WorkspaceAsset.asset_type.in_(["metric", "kpi"]), WorkspaceAsset.name == metric_name).first()
        if asset is None:
            raise SecurityError("METRIC_NOT_FOUND", "The governed metric was not found.", status_code=404, details={"metric": metric_name})
        if asset.status not in {"certified", "published", "approved", "draft"}:
            raise SecurityError("METRIC_NOT_PUBLISHED", "The metric is not approved for execution.", status_code=403)
        version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.current_version_id).first()
        if version is None:
            raise SecurityError("VERSION_NOT_FOUND", "Dataset has no current version.", status_code=404)
        frame = pd.read_csv(request.app.state.storage.resolve(version.storage_path))
        definition = asset.definition_json or {}
        result = calculate_kpi(frame, definition, filters=payload.get("filters"), dimensions=payload.get("dimensions"), time_column=payload.get("time_column"), time_grain=payload.get("time_grain"))
        return {"metric": metric_name, "metric_id": asset.id, "metric_version": asset.version, "dataset_id": dataset_id, "source_version_id": version.id, "definition": definition, "result": result}
    finally:
        db.close()
