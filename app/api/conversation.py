"""Conversational analyst API backed by the selected dataset version."""

from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import APIRouter, Request

from app.core.conversational import ConversationalDataIntelligence, conversational_catalog
from app.core.intelligence.common import ExecutionBudget, IntelligenceError
from app.models.all import Dataset, DatasetVersion


router = APIRouter(prefix="/api/v1", tags=["conversational-intelligence"])


def _load_dataset(request: Request, dataset_id: str, source_version_id: str | None = None):
    db = request.app.state.SessionLocal()
    try:
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        if dataset is None:
            raise IntelligenceError("DATASET_NOT_FOUND", "Dataset was not found.", {"dataset_id": dataset_id}, status_code=404)
        version_id = source_version_id or dataset.current_version_id
        version = db.query(DatasetVersion).filter(DatasetVersion.id == version_id).first()
        if version is None:
            raise IntelligenceError("VERSION_NOT_FOUND", "Dataset version was not found.", {"source_version_id": version_id}, status_code=404)
        if version.dataset_id != dataset.id:
            raise IntelligenceError("LINEAGE_MISMATCH", "source_version_id does not belong to dataset_id.")
        try:
            frame = pd.read_csv(request.app.state.storage.resolve(version.storage_path))
        except pd.errors.EmptyDataError:
            frame = pd.DataFrame()
        return dataset, version, frame
    finally:
        db.close()


@router.get("/conversation/catalog")
def get_conversation_catalog():
    return conversational_catalog()


@router.post("/datasets/{dataset_id}/conversation/ask")
def ask_conversational_analyst(dataset_id: str, request: Request, payload: dict[str, Any] | None = None):
    body = dict(payload or {})
    dataset, version, frame = _load_dataset(request, dataset_id, body.get("source_version_id"))
    try:
        budget = ExecutionBudget(
            max_rows=int(body.get("max_rows", 20_000)),
            max_features=int(body.get("max_features", 250)),
            max_trials=20,
            timeout_seconds=int(body.get("timeout_seconds", 120)),
            random_state=int(body.get("random_state", 42)),
        )
    except (TypeError, ValueError) as exc:
        raise IntelligenceError("CONVERSATION_OPTIONS_INVALID", "Conversation execution options must be numeric.") from exc
    history = body.get("history") or []
    if not isinstance(history, list) or len(history) > 12:
        raise IntelligenceError("CONVERSATION_HISTORY_INVALID", "history must be a list of at most 12 prior messages.")
    result = ConversationalDataIntelligence(budget).ask(
        frame,
        str(body.get("message") or ""),
        dataset_id=dataset.id,
        dataset_name=dataset.name,
        source_version_id=version.id,
        conversation_id=body.get("conversation_id"),
        history=history,
    )
    return {"dataset_id": dataset.id, "source_version_id": version.id, **result}
