"""Tenant-scoped retention policy and legal-hold controls."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Request

from app.core.security import SecurityError, assert_dataset_tenant, authorize
from app.models.all import AnalysisRun, Artifact, AutomationRun, AuditEvent, Dataset, DatasetVersion, ModelVersion, PipelineRun, RetentionHold, RetentionPolicy, RetentionRun


router = APIRouter(prefix="/api/v1/security/retention", tags=["retention"])


def _actor(request: Request):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise SecurityError("AUTHENTICATION_REQUIRED", "A security principal is required.")
    return actor


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _policy_definition(payload: dict[str, Any]) -> dict[str, Any]:
    definition = dict(payload.get("definition") or {})
    try:
        days = int(definition.get("retention_days", payload.get("retention_days", 0)))
    except (TypeError, ValueError) as exc:
        raise SecurityError("RETENTION_DAYS_INVALID", "retention_days must be a positive integer.", status_code=422) from exc
    if days < 1 or days > 36500:
        raise SecurityError("RETENTION_DAYS_INVALID", "retention_days must be between 1 and 36,500.", status_code=422)
    dataset_ids = definition.get("dataset_ids", payload.get("dataset_ids", [])) or []
    if not isinstance(dataset_ids, list) or any(not isinstance(item, str) or not item.strip() for item in dataset_ids):
        raise SecurityError("RETENTION_DATASETS_INVALID", "dataset_ids must be a list of non-empty identifiers.", status_code=422)
    return {"retention_days": days, "dataset_ids": list(dict.fromkeys(str(item) for item in dataset_ids)), "include_artifacts": bool(definition.get("include_artifacts", True))}


def _public_policy(row: RetentionPolicy) -> dict[str, Any]:
    return {"id": row.id, "tenant_id": row.tenant_id, "workspace_id": row.workspace_id, "name": row.name, "definition": row.definition, "enabled": row.enabled, "created_by": row.created_by, "created_at": row.created_at, "updated_at": row.updated_at}


@router.post("/policies", status_code=201)
def create_policy(payload: dict[str, Any], request: Request):
    actor = _actor(request)
    workspace_id = str(payload.get("workspace_id", "default"))
    authorize(actor, "manage_security", workspace_id=workspace_id)
    name = str(payload.get("name", "")).strip()
    if not name:
        raise SecurityError("RETENTION_NAME_REQUIRED", "A retention policy name is required.", status_code=422)
    definition = _policy_definition(payload)
    db = request.app.state.SessionLocal()
    try:
        for dataset_id in definition["dataset_ids"]:
            assert_dataset_tenant(db, dataset_id, actor)
        row = RetentionPolicy(tenant_id=actor.tenant_id, workspace_id=workspace_id, name=name, definition=definition, enabled=bool(payload.get("enabled", True)), created_by=actor.principal_id)
        db.add(row)
        db.commit()
        db.refresh(row)
        return _public_policy(row)
    finally:
        db.close()


@router.get("/policies")
def list_policies(request: Request, workspace_id: str = "default"):
    actor = _actor(request)
    authorize(actor, "read", workspace_id=workspace_id)
    db = request.app.state.SessionLocal()
    try:
        rows = db.query(RetentionPolicy).filter(RetentionPolicy.tenant_id == actor.tenant_id, RetentionPolicy.workspace_id == workspace_id).order_by(RetentionPolicy.name.asc()).all()
        return {"tenant_id": actor.tenant_id, "workspace_id": workspace_id, "policies": [_public_policy(row) for row in rows]}
    finally:
        db.close()


@router.post("/holds", status_code=201)
def create_hold(payload: dict[str, Any], request: Request):
    actor = _actor(request)
    authorize(actor, "manage_security", workspace_id=str(payload.get("workspace_id", "default")))
    dataset_id = str(payload.get("dataset_id", "")).strip()
    reason = str(payload.get("reason", "")).strip()
    if not dataset_id or not reason:
        raise SecurityError("RETENTION_HOLD_FIELDS_REQUIRED", "dataset_id and reason are required.", status_code=422)
    db = request.app.state.SessionLocal()
    try:
        assert_dataset_tenant(db, dataset_id, actor)
        hold = RetentionHold(tenant_id=actor.tenant_id, dataset_id=dataset_id, reason=reason, active=True, created_by=actor.principal_id)
        db.add(hold)
        db.commit()
        db.refresh(hold)
        return {"id": hold.id, "tenant_id": hold.tenant_id, "dataset_id": hold.dataset_id, "reason": hold.reason, "active": hold.active, "created_by": hold.created_by, "created_at": hold.created_at}
    finally:
        db.close()


@router.get("/holds")
def list_holds(request: Request, active: bool = True):
    actor = _actor(request)
    authorize(actor, "read")
    db = request.app.state.SessionLocal()
    try:
        query = db.query(RetentionHold).filter(RetentionHold.tenant_id == actor.tenant_id)
        if active:
            query = query.filter(RetentionHold.active.is_(True))
        rows = query.order_by(RetentionHold.created_at.desc()).limit(500).all()
        return {"tenant_id": actor.tenant_id, "holds": [{"id": row.id, "dataset_id": row.dataset_id, "reason": row.reason, "active": row.active, "created_by": row.created_by, "released_by": row.released_by, "created_at": row.created_at, "released_at": row.released_at} for row in rows]}
    finally:
        db.close()


@router.post("/holds/{hold_id}/release")
def release_hold(hold_id: str, request: Request):
    actor = _actor(request)
    authorize(actor, "manage_security")
    db = request.app.state.SessionLocal()
    try:
        hold = db.query(RetentionHold).filter(RetentionHold.id == hold_id, RetentionHold.tenant_id == actor.tenant_id).first()
        if hold is None:
            raise SecurityError("RETENTION_HOLD_NOT_FOUND", "The legal hold was not found.", status_code=404)
        hold.active = False
        hold.released_by = actor.principal_id
        hold.released_at = _now()
        db.commit()
        return {"id": hold.id, "dataset_id": hold.dataset_id, "active": hold.active, "released_by": hold.released_by, "released_at": hold.released_at}
    finally:
        db.close()


def _references(db, version_id: str) -> list[str]:
    references: list[str] = []
    if db.query(DatasetVersion.id).filter(DatasetVersion.parent_version_id == version_id).first(): references.append("dataset_versions")
    if db.query(AnalysisRun.id).filter(AnalysisRun.source_version_id == version_id).first(): references.append("analysis_runs")
    if db.query(PipelineRun.id).filter((PipelineRun.source_version_id == version_id) | (PipelineRun.output_version_id == version_id)).first(): references.append("pipeline_runs")
    if db.query(AutomationRun.id).filter(AutomationRun.source_version_id == version_id).first(): references.append("automation_runs")
    if db.query(ModelVersion.id).filter(ModelVersion.training_source_version_id == version_id).first(): references.append("model_versions")
    if db.query(AuditEvent.id).filter((AuditEvent.input_version_id == version_id) | (AuditEvent.output_version_id == version_id)).first(): references.append("audit_events")
    return references


@router.post("/evaluate")
def evaluate_retention(payload: dict[str, Any] | None, request: Request):
    body = payload or {}
    actor = _actor(request)
    workspace_id = str(body.get("workspace_id", "default"))
    authorize(actor, "manage_security", workspace_id=workspace_id)
    db = request.app.state.SessionLocal()
    try:
        policy_id = str(body.get("policy_id", "")).strip()
        query = db.query(RetentionPolicy).filter(RetentionPolicy.tenant_id == actor.tenant_id, RetentionPolicy.workspace_id == workspace_id)
        if policy_id:
            query = query.filter(RetentionPolicy.id == policy_id)
        else:
            query = query.filter(RetentionPolicy.enabled.is_(True))
        policy = query.order_by(RetentionPolicy.created_at.asc()).first()
        if policy is None:
            raise SecurityError("RETENTION_POLICY_NOT_FOUND", "No enabled retention policy was found.", status_code=404)
        definition = dict(policy.definition or {})
        cutoff = _now() - timedelta(days=int(definition["retention_days"]))
        dataset_query = db.query(Dataset).filter(Dataset.tenant_id == actor.tenant_id)
        scoped_ids = list(definition.get("dataset_ids") or [])
        if scoped_ids:
            dataset_query = dataset_query.filter(Dataset.id.in_(scoped_ids))
        datasets = dataset_query.all()
        hold_ids = {row.dataset_id for row in db.query(RetentionHold).filter(RetentionHold.tenant_id == actor.tenant_id, RetentionHold.active.is_(True)).all()}
        candidates: list[dict[str, Any]] = []
        for dataset in datasets:
            for version in db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset.id, DatasetVersion.created_at < cutoff, DatasetVersion.id != dataset.current_version_id).order_by(DatasetVersion.created_at.asc()).all():
                references = _references(db, version.id)
                path = request.app.state.storage.resolve(version.storage_path)
                candidates.append({"dataset_id": dataset.id, "dataset_name": dataset.name, "version_id": version.id, "version_number": version.version_number, "created_at": version.created_at.isoformat() if version.created_at else None, "storage_path": version.storage_path, "bytes": path.stat().st_size if path.is_file() else 0, "legal_hold": dataset.id in hold_ids, "references": references, "eligible": not references and dataset.id not in hold_ids})
        execute = bool(body.get("execute", False))
        evidence = body.get("approval_evidence")
        approved = execute and actor.is_admin and isinstance(evidence, dict) and bool(evidence)
        if execute and not approved:
            raise SecurityError("RETENTION_APPROVAL_REQUIRED", "Execution requires administrator approval and a non-empty approval_evidence object.", status_code=403)
        deleted: list[str] = []
        if execute:
            for candidate in candidates:
                if not candidate["eligible"]:
                    continue
                version = db.query(DatasetVersion).filter(DatasetVersion.id == candidate["version_id"], DatasetVersion.dataset_id == candidate["dataset_id"]).first()
                if version is None:
                    continue
                for artifact in db.query(Artifact).filter(Artifact.source_version_id == version.id).all():
                    request.app.state.storage.resolve(artifact.storage_path).unlink(missing_ok=True)
                    db.delete(artifact)
                request.app.state.storage.resolve(version.storage_path).unlink(missing_ok=True)
                db.delete(version)
                deleted.append(candidate["version_id"])
            db.flush()
        status = "EXECUTED" if execute else "DRY_RUN"
        run = RetentionRun(tenant_id=actor.tenant_id, policy_id=policy.id, mode="execute" if execute else "dry_run", status=status, candidates=candidates, evidence={"cutoff": cutoff.isoformat(), "candidate_count": len(candidates), "eligible_count": sum(1 for item in candidates if item["eligible"]), "deleted_count": len(deleted), "approval_evidence": evidence if execute else None}, requested_by=actor.principal_id, approved_by=actor.principal_id if execute else None, executed_at=_now() if execute else None)
        db.add(run)
        db.commit()
        return {"run_id": run.id, "policy_id": policy.id, "mode": run.mode, "status": run.status, "cutoff": cutoff, "candidates": candidates, "deleted_version_ids": deleted, "approval_required_for_execute": True}
    finally:
        db.close()


@router.get("/runs")
def list_runs(request: Request, limit: int = 100):
    actor = _actor(request)
    authorize(actor, "read")
    db = request.app.state.SessionLocal()
    try:
        rows = db.query(RetentionRun).filter(RetentionRun.tenant_id == actor.tenant_id).order_by(RetentionRun.created_at.desc()).limit(max(1, min(limit, 500))).all()
        return {"tenant_id": actor.tenant_id, "runs": [{"id": row.id, "policy_id": row.policy_id, "mode": row.mode, "status": row.status, "candidates": row.candidates, "evidence": row.evidence, "requested_by": row.requested_by, "approved_by": row.approved_by, "created_at": row.created_at, "executed_at": row.executed_at} for row in rows]}
    finally:
        db.close()
