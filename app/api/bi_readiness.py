"""BI-ready data preparation endpoints."""

from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import APIRouter, Request

from app.core.bi.readiness import apply_bi_readiness, build_bi_readiness_profile
from app.core.intelligence.common import IntelligenceError, json_safe
from app.models.all import Dataset, DatasetVersion
from app.orchestration.platform import _create_version


router = APIRouter(prefix="/api/v1", tags=["bi-readiness"])


def _load_dataset(request: Request, dataset_id: str, version_id: str | None = None):
    db = request.app.state.SessionLocal()
    try:
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        if dataset is None:
            raise IntelligenceError("DATASET_NOT_FOUND", "Dataset was not found.", {"dataset_id": dataset_id}, status_code=404)
        selected_id = version_id or dataset.current_version_id
        version = db.query(DatasetVersion).filter(DatasetVersion.id == selected_id).first()
        if version is None:
            raise IntelligenceError("VERSION_NOT_FOUND", "Dataset version was not found.", {"source_version_id": selected_id}, status_code=404)
        if version.dataset_id != dataset.id:
            raise IntelligenceError("LINEAGE_MISMATCH", "source_version_id does not belong to dataset_id.")
        try:
            frame = pd.read_csv(request.app.state.storage.resolve(version.storage_path))
        except pd.errors.EmptyDataError:
            frame = pd.DataFrame()
        return db, dataset, version, frame
    except Exception:
        db.close()
        raise


def _public(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key != "dataframe"}


@router.get("/bi-readiness/catalog")
def bi_readiness_catalog():
    return {
        "capability": "BI-ready data preparation",
        "mode": "approval_first",
        "workflow": ["profile", "preview", "apply_to_new_immutable_version", "validate_model_contract"],
        "outputs": ["clean BI-friendly fields", "FactData grain contract", "date and dimension recommendations", "explicit measure recommendations", "lineage and validation warnings"],
        "safety": {"source_overwrite": False, "silent_duplicate_removal": False, "business_semantics_invented": False, "security_publish_gate": True},
    }


@router.post("/datasets/{dataset_id}/bi-readiness/profile")
def profile_bi_readiness(dataset_id: str, request: Request, payload: dict[str, Any] | None = None):
    db, dataset, version, frame = _load_dataset(request, dataset_id, (payload or {}).get("version_id"))
    try:
        profile = build_bi_readiness_profile(frame, dataset_id=dataset.id, source_version_id=version.id)
        return profile
    finally:
        db.close()


@router.post("/datasets/{dataset_id}/bi-readiness/preview")
def preview_bi_readiness(dataset_id: str, request: Request, payload: dict[str, Any] | None = None):
    db, dataset, version, frame = _load_dataset(request, dataset_id, (payload or {}).get("version_id"))
    try:
        profile = build_bi_readiness_profile(frame, dataset_id=dataset.id, source_version_id=version.id)
        result = apply_bi_readiness(frame, profile, options=(payload or {}).get("options"))
        return {"dataset_id": dataset.id, "input_version_id": version.id, "profile": result["profile"], **_public(result), "version_created": False, "message": "Preview only. No source or dataset version was changed."}
    finally:
        db.close()


@router.post("/datasets/{dataset_id}/bi-readiness/apply")
def apply_bi_readiness_version(dataset_id: str, request: Request, payload: dict[str, Any] | None = None):
    db, dataset, version, frame = _load_dataset(request, dataset_id, (payload or {}).get("version_id"))
    try:
        profile = build_bi_readiness_profile(frame, dataset_id=dataset.id, source_version_id=version.id)
        result = apply_bi_readiness(frame, profile, options=(payload or {}).get("options"))
        output_version, version_result = _create_version(
            db,
            request.app.state.storage,
            dataset,
            version,
            result["dataframe"],
            pipeline_type="BIReadinessPreparation",
            parameters={"selected_actions": result["execution"]["selected_actions"], "model_contract": result["model_contract"]},
            before_stats={"row_count": len(frame), "column_count": len(frame.columns), "readiness_score": profile["readiness_score"]},
        )
        return {"dataset_id": dataset.id, "input_version_id": version.id, "output_version_id": output_version.id, "version_created": True, **version_result, "profile": result["profile"], **_public(result), "lineage": {"parent_version_id": version.id, "output_version_id": output_version.id, "pipeline_type": "BIReadinessPreparation"}, "message": "BI-ready data was saved as a new immutable dataset version. Review the model contract before publishing."}
    finally:
        db.close()


__all__ = ["router"]
