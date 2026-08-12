from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any
from uuid import uuid4

import joblib
from sqlalchemy.orm import Session

from app.core.intelligence.common import IntelligenceError, json_safe
from app.core.ml.service import TrainingOutcome
from app.models.all import Artifact, Dataset, DatasetVersion, ModelVersion, RegisteredModel
from app.storage.dataset_storage import DatasetStorage, StoragePathError


ALLOWED_STATUS_TRANSITIONS = {
    "CANDIDATE": {"VALIDATED", "REJECTED", "ARCHIVED"},
    "VALIDATED": {"APPROVED", "REJECTED", "ARCHIVED"},
    "APPROVED": {"CHAMPION", "ARCHIVED"},
    "CHAMPION": {"ARCHIVED"},
    "REJECTED": {"ARCHIVED"},
    "ARCHIVED": set(),
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def register_training_outcome(
    db: Session,
    storage: DatasetStorage,
    *,
    dataset: Dataset,
    source_version: DatasetVersion,
    outcome: TrainingOutcome,
    model_name: str,
    workspace_id: str = "default",
    owner: str | None = None,
    description: str | None = None,
) -> tuple[RegisteredModel, ModelVersion, Artifact]:
    if source_version.dataset_id != dataset.id:
        raise IntelligenceError("LINEAGE_MISMATCH", "Training source version does not belong to the dataset.")
    normalized_name = str(model_name).strip()
    if not normalized_name:
        raise IntelligenceError("MODEL_NAME_REQUIRED", "A model name is required.")
    model = db.query(RegisteredModel).filter(RegisteredModel.tenant_id == dataset.tenant_id, RegisteredModel.workspace_id == workspace_id, RegisteredModel.name == normalized_name).first()
    if model is None:
        model = RegisteredModel(
            tenant_id=dataset.tenant_id,
            workspace_id=workspace_id,
            name=normalized_name,
            task_type=str(outcome.result["task_type"]),
            description=description,
            owner=owner,
            status="CANDIDATE",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(model)
        db.flush()
    elif model.task_type != outcome.result["task_type"]:
        raise IntelligenceError("MODEL_TASK_MISMATCH", "A registered model cannot mix task types.", {"registered": model.task_type, "training": outcome.result["task_type"]})
    next_version = max((version.version for version in model.versions), default=0) + 1
    storage_key = f"{dataset.id}/models/{model.id}/v{next_version}.joblib"
    try:
        destination = storage.resolve(storage_key)
    except StoragePathError as exc:
        raise IntelligenceError("MODEL_ARTIFACT_PATH_INVALID", "Model artifact path is invalid.") from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        joblib.dump(outcome.model, temporary, compress=3)
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)
    digest = _sha256(destination)
    artifact = Artifact(
        dataset_id=dataset.id,
        source_version_id=source_version.id,
        artifact_type="trained_model",
        storage_path=storage_key,
        sha256=digest,
        size=destination.stat().st_size,
        metadata_json={"created_by": "ModelTrainingService", "trusted_application_artifact": True, "model_id": model.id, "version": next_version},
        created_at=utc_now(),
    )
    db.add(artifact)
    db.flush()
    version = ModelVersion(
        model_id=model.id,
        version=next_version,
        algorithm=str(outcome.result["algorithm"]),
        parameters=json_safe(outcome.result.get("parameters") or {}),
        metrics=json_safe(outcome.result["metrics"]),
        training_dataset_id=dataset.id,
        training_source_version_id=source_version.id,
        feature_specification={"features": outcome.result["features"], "targets": outcome.result["target_columns"]},
        preprocessing_specification=json_safe(outcome.result["preprocessing"]),
        artifact_id=artifact.id,
        artifact_sha256=digest,
        status="CANDIDATE",
        created_at=utc_now(),
    )
    db.add(version)
    db.flush()
    return model, version, artifact


def load_trusted_model(db: Session, storage: DatasetStorage, model_version_id: str):
    version = db.query(ModelVersion).filter(ModelVersion.id == model_version_id).first()
    if version is None:
        raise IntelligenceError("MODEL_VERSION_NOT_FOUND", "Model version was not found.", {"model_version_id": model_version_id}, status_code=404)
    artifact = db.query(Artifact).filter(Artifact.id == version.artifact_id).first()
    if artifact is None or artifact.artifact_type != "trained_model":
        raise IntelligenceError("MODEL_ARTIFACT_NOT_FOUND", "Trusted model artifact metadata is missing.")
    if artifact.dataset_id != version.training_dataset_id or artifact.source_version_id != version.training_source_version_id:
        raise IntelligenceError("MODEL_ARTIFACT_LINEAGE_MISMATCH", "Model artifact lineage does not match model-version lineage.")
    if not (artifact.metadata_json or {}).get("trusted_application_artifact"):
        raise IntelligenceError("UNTRUSTED_MODEL_ARTIFACT", "Only model artifacts created by this application may be loaded.")
    try:
        path = storage.resolve(artifact.storage_path)
    except StoragePathError as exc:
        raise IntelligenceError("MODEL_ARTIFACT_PATH_INVALID", "Model artifact path is outside application storage.") from exc
    if not path.is_file():
        raise IntelligenceError("MODEL_ARTIFACT_NOT_FOUND", "Model artifact file is missing.")
    digest = _sha256(path)
    if digest != artifact.sha256 or digest != version.artifact_sha256:
        raise IntelligenceError("MODEL_ARTIFACT_CHECKSUM_FAILED", "Model artifact checksum validation failed.")
    try:
        model = joblib.load(path)
    except Exception as exc:
        raise IntelligenceError("MODEL_ARTIFACT_LOAD_FAILED", "Verified model artifact could not be loaded.", {"error": str(exc)}) from exc
    return model, version, artifact


def transition_model_version(version: ModelVersion, requested_status: str) -> None:
    requested = str(requested_status).upper()
    current = str(version.status).upper()
    if requested not in ALLOWED_STATUS_TRANSITIONS.get(current, set()):
        raise IntelligenceError("INVALID_MODEL_STATUS_TRANSITION", "Requested model-version status transition is not allowed.", {"current": current, "requested": requested, "allowed": sorted(ALLOWED_STATUS_TRANSITIONS.get(current, set()))})
    version.status = requested


def public_model(model: RegisteredModel) -> dict[str, Any]:
    return {
        "id": model.id,
        "tenant_id": model.tenant_id,
        "workspace_id": model.workspace_id,
        "name": model.name,
        "task_type": model.task_type,
        "description": model.description,
        "owner": model.owner,
        "status": model.status,
        "created_at": model.created_at.isoformat() if model.created_at else None,
        "updated_at": model.updated_at.isoformat() if model.updated_at else None,
        "versions": [
            {
                "id": version.id,
                "version": version.version,
                "algorithm": version.algorithm,
                "metrics": version.metrics,
                "training_dataset_id": version.training_dataset_id,
                "training_source_version_id": version.training_source_version_id,
                "feature_specification": version.feature_specification,
                "preprocessing_specification": version.preprocessing_specification,
                "artifact_sha256": version.artifact_sha256,
                "status": version.status,
                "created_at": version.created_at.isoformat() if version.created_at else None,
            }
            for version in sorted(model.versions, key=lambda item: item.version, reverse=True)
        ],
    }
