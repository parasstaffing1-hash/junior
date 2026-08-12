from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import APIRouter, Request

from app.core.security import SecurityError, authorize
from app.models.all import Dataset, DatasetVersion, WorkspaceAsset


router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])


def _actor(request: Request):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise SecurityError("AUTHENTICATION_REQUIRED", "A security principal is required.")
    authorize(actor, "read")
    return actor


@router.get("/search")
def search_catalog(request: Request, q: str = "", asset_type: str | None = None, limit: int = 100):
    actor = _actor(request)
    query_text = str(q or "").strip().casefold()
    db = request.app.state.SessionLocal()
    try:
        datasets = db.query(Dataset).filter(Dataset.tenant_id == actor.tenant_id).order_by(Dataset.updated_at.desc()).limit(500).all()
        asset_query = db.query(WorkspaceAsset)
        if actor.workspace_ids and "*" not in actor.workspace_ids:
            asset_query = asset_query.filter(WorkspaceAsset.workspace_id.in_(list(actor.workspace_ids)))
        assets = asset_query.order_by(WorkspaceAsset.updated_at.desc()).limit(500).all()
        allowed_dataset_ids = {dataset.id for dataset in datasets}
        # WorkspaceAsset predates tenant_id. Only dataset-bound assets can be
        # safely returned across tenants; unbound assets are shared definitions
        # and are intentionally excluded from this tenant-scoped catalog.
        assets = [asset for asset in assets if asset.dataset_id and asset.dataset_id in allowed_dataset_ids]
        results: list[dict[str, Any]] = []
        for dataset in datasets:
            version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.current_version_id).first()
            columns: list[str] = []
            if version:
                try:
                    columns = [str(column) for column in pd.read_csv(request.app.state.storage.resolve(version.storage_path), nrows=0).columns]
                except Exception:
                    columns = []
            haystack = " ".join([dataset.name, dataset.original_filename, dataset.source_type, *columns]).casefold()
            if query_text and query_text not in haystack:
                continue
            results.append({"id": dataset.id, "kind": "dataset", "name": dataset.name, "source_type": dataset.source_type, "current_version_id": dataset.current_version_id, "columns": columns, "updated_at": dataset.updated_at})
        for asset in assets:
            if asset_type and asset.asset_type.casefold() != asset_type.casefold():
                continue
            haystack = " ".join([asset.name, asset.asset_type, asset.description or "", " ".join(str(tag) for tag in (asset.tags or []))]).casefold()
            if query_text and query_text not in haystack:
                continue
            results.append({"id": asset.id, "kind": "workspace_asset", "asset_type": asset.asset_type, "name": asset.name, "dataset_id": asset.dataset_id, "status": asset.status, "owner": asset.owner, "version": asset.version, "updated_at": asset.updated_at})
        results.sort(key=lambda item: (str(item.get("updated_at") or ""), str(item.get("name") or "")), reverse=True)
        return {"tenant_id": actor.tenant_id, "query": q, "count": min(len(results), max(1, min(limit, 500))), "results": results[: max(1, min(limit, 500))], "search_scope": "tenant_datasets_workspace_assets_and_current_schema"}
    finally:
        db.close()
