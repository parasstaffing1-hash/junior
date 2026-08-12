"""Automatic metric discovery for scheduled alert evaluation."""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.core.alerts.service import evaluate_and_deliver
from app.core.kpi.calculator import KPICalculationError, calculate_kpi
from app.core.security import SecurityError
from app.models.all import Dataset, DatasetVersion, WorkspaceAsset


def discover_and_evaluate_metrics(db, storage, *, tenant_id: str, dataset_id: str, workspace_id: str = "default", approved: bool = False, timeout_seconds: int = 30) -> dict[str, Any]:
    """Evaluate every published metric bound to a dataset and feed alert rules.

    The discovery is deterministic and source-versioned. External delivery still
    remains approval-gated inside ``evaluate_and_deliver``.
    """
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id, Dataset.tenant_id == tenant_id).first()
    if dataset is None:
        raise SecurityError("DATASET_NOT_FOUND", "Dataset was not found.", status_code=404, details={"dataset_id": dataset_id})
    version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.current_version_id, DatasetVersion.dataset_id == dataset.id).first()
    if version is None:
        raise SecurityError("VERSION_NOT_FOUND", "Dataset has no current version.", status_code=404)
    frame = pd.read_csv(storage.resolve(version.storage_path))
    assets = db.query(WorkspaceAsset).filter(
        WorkspaceAsset.tenant_id == tenant_id,
        WorkspaceAsset.workspace_id == workspace_id,
        WorkspaceAsset.dataset_id == dataset.id,
        WorkspaceAsset.asset_type.in_(["metric", "kpi"]),
        WorkspaceAsset.status.in_(["published", "certified", "approved"]),
    ).order_by(WorkspaceAsset.name.asc()).all()
    metric_results: list[dict[str, Any]] = []
    for asset in assets:
        definition = dict(asset.definition_json or {})
        try:
            calculation = calculate_kpi(frame, definition)
            total = calculation.get("total") or {}
            snapshot = {
                "kpi_slug": asset.name,
                "metric_id": asset.id,
                "metric_version": asset.version,
                "dataset_id": dataset.id,
                "source_version_id": version.id,
                "value": total.get("value"),
                "components": total.get("components"),
                "target_value": total.get("target_value"),
                "target_status": total.get("status"),
            }
            evaluation = evaluate_and_deliver(
                db,
                tenant_id=tenant_id,
                snapshot=snapshot,
                source_type="scheduled_metric_discovery",
                source_id=asset.id,
                approved=approved,
                timeout_seconds=timeout_seconds,
            )
            metric_results.append({"metric_id": asset.id, "metric": asset.name, "status": "EVALUATED", "snapshot": snapshot, "alerts": evaluation})
        except KPICalculationError as exc:
            metric_results.append({"metric_id": asset.id, "metric": asset.name, "status": "SKIPPED", "error": {"code": exc.code, "message": exc.message, "details": exc.details}})
    return {"tenant_id": tenant_id, "dataset_id": dataset.id, "source_version_id": version.id, "workspace_id": workspace_id, "metrics_discovered": len(assets), "metrics": metric_results, "external_delivery_approval": bool(approved)}
