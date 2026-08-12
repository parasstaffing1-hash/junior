from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.intelligence.common import json_safe
from app.models.all import DatasetVersion, IngestionState, MonitoringRun, PipelineRun


def derive_incident_signals(db, *, dataset_id: str) -> dict[str, Any]:
    """Derive root-cause signals from persisted operational evidence."""
    signals: dict[str, Any] = {}
    evidence: list[dict[str, Any]] = []
    monitoring = db.query(MonitoringRun).filter(MonitoringRun.dataset_id == dataset_id).order_by(MonitoringRun.completed_at.desc()).limit(20).all()
    for run in monitoring:
        metrics = run.metrics or {}
        if run.status == "ALERT" or metrics.get("drifted") or metrics.get("out_of_distribution") or metrics.get("drifted_feature_count"):
            signals["feature_drift"] = max(float(signals.get("feature_drift", 0)), float(metrics.get("maximum_drift_score", metrics.get("drifted_feature_count", 0) or 0)))
            signals["performance_degradation"] = max(float(signals.get("performance_degradation", 0)), float(metrics.get("relative_degradation", 0) or 0))
            evidence.append({"type": "monitoring_run", "id": run.id, "status": run.status, "metrics": json_safe(metrics)})
    failures = db.query(PipelineRun).filter(PipelineRun.dataset_id == dataset_id, PipelineRun.status.in_(["FAILED", "ERROR"])).order_by(PipelineRun.completed_at.desc()).limit(20).all()
    if failures:
        signals["pipeline_error"] = f"{len(failures)} persisted pipeline run(s) failed."
        evidence.extend({"type": "pipeline_run", "id": row.id, "status": row.status, "pipeline_type": row.pipeline_type} for row in failures)
    versions = db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset_id).order_by(DatasetVersion.version_number.desc()).limit(10).all()
    schema_breaches = []
    for version in versions:
        metadata = version.metadata_json or {}
        validation = ((metadata.get("ingestion") or {}).get("schema_validation") if isinstance(metadata, dict) else None) or {}
        if validation.get("breaking"):
            schema_breaches.append({"version_id": version.id, "validation": validation})
    if schema_breaches:
        signals["schema_change"] = True
        evidence.extend({"type": "schema_contract", **item} for item in schema_breaches)
    states = db.query(IngestionState).filter(IngestionState.dataset_id == dataset_id).all()
    if states:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        age = max((now - row.updated_at).total_seconds() / 60 for row in states if row.updated_at) if any(row.updated_at for row in states) else 0
        signals["freshness_age_minutes"] = age
        evidence.append({"type": "ingestion_state", "count": len(states), "freshness_age_minutes": age})
    if not signals:
        signals["no_strong_signal"] = True
    signals["evidence"] = evidence
    return signals


__all__ = ["derive_incident_signals"]
