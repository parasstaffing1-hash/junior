from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.orchestration.platform import run_full_platform_analysis
from app.storage.dataset_storage import DatasetStorage


class AutomatedAnalyst:
    """Orchestrates the merged Tools 1–100 platform as one user-facing workflow."""

    def __init__(self, db: Session, storage: DatasetStorage):
        self.db = db
        self.storage = storage

    def run_full_pipeline(self, dataset_id: str) -> dict[str, Any]:
        return run_full_platform_analysis(self.db, self.storage, dataset_id)
