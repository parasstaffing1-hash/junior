from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
import pandas as pd

from app.core.security import SecurityError, authorize, assert_dataset_tenant
from app.core.storage.partitioned_store import PartitionedParquetStore, PartitionedStoreError
from app.models.all import DatasetVersion


router = APIRouter(prefix="/api/v1/storage", tags=["storage"])


@router.post("/datasets/{dataset_id}/partition", status_code=201)
def partition_dataset(dataset_id: str, payload: dict[str, Any], request: Request):
    actor = getattr(request.state, "actor", None)
    authorize(actor, "write")
    db = request.app.state.SessionLocal()
    try:
        dataset = assert_dataset_tenant(db, dataset_id, actor)
        version_id = payload.get("version_id") or dataset.current_version_id
        version = db.query(DatasetVersion).filter(DatasetVersion.id == version_id, DatasetVersion.dataset_id == dataset_id).first()
        if version is None:
            raise SecurityError("VERSION_NOT_FOUND", "Dataset version was not found.", status_code=404)
        frame = pd.read_csv(request.app.state.storage.resolve(version.storage_path))
        store = PartitionedParquetStore(request.app.state.storage.root / "partitioned")
        manifest = store.write(frame, dataset_id=dataset_id, partition_column=payload.get("partition_column"), compression=str(payload.get("compression", "zstd")))
        return {"dataset_id": dataset_id, "source_version_id": version.id, "manifest": manifest, "promotion": "approval_required_before_repointing_dataset_version"}
    except PartitionedStoreError as exc:
        raise SecurityError("PARTITION_WRITE_FAILED", str(exc), status_code=422) from exc
    finally:
        db.close()
