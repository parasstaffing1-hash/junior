from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import APIRouter, Request

from app.core.intelligence.common import IntelligenceError
from app.core.security import SecurityError, assert_dataset_tenant, authorize
from app.core.sql.nl_sql import answer_question
from app.models.all import DatasetVersion


router = APIRouter(prefix="/api/v1", tags=["natural-language-sql"])


@router.post("/datasets/{dataset_id}/sql/natural-language")
def natural_language_sql(dataset_id: str, payload: dict[str, Any], request: Request):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise SecurityError("AUTHENTICATION_REQUIRED", "A security principal is required.")
    authorize(actor, "analyze")
    question = str(payload.get("question") or "").strip()
    db = request.app.state.SessionLocal()
    try:
        dataset = assert_dataset_tenant(db, dataset_id, actor)
        version_id = payload.get("source_version_id") or dataset.current_version_id
        version = db.query(DatasetVersion).filter(DatasetVersion.id == version_id, DatasetVersion.dataset_id == dataset_id).first()
        if version is None:
            raise SecurityError("VERSION_NOT_FOUND", "Dataset version was not found.", status_code=404)
        try:
            frame = pd.read_csv(request.app.state.storage.resolve(version.storage_path))
            result = answer_question(question, frame, max_rows=max(1, min(int(payload.get("max_rows", 500)), 5000)), timeout_seconds=max(0.1, min(float(payload.get("timeout_seconds", 5)), 30)))
        except Exception as exc:
            if hasattr(exc, "code"):
                raise SecurityError(exc.code, getattr(exc, "message", str(exc)), status_code=422, details=getattr(exc, "details", {})) from exc
            raise
        return {"dataset_id": dataset_id, "source_version_id": version.id, **result, "guardrails": {"read_only": True, "bounded_rows": True, "generated_sql_is_returned": True, "llm_required": False}}
    finally:
        db.close()
