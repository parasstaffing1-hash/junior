from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.core.alerts.service import AlertDeliveryError, evaluate_and_deliver, validate_alert_definition
from app.core.security import SecurityError, authorize
from app.models.all import AlertDelivery, AlertRule


router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


def _actor(request: Request):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise SecurityError("AUTHENTICATION_REQUIRED", "A security principal is required.")
    return actor


@router.post("/rules", status_code=201)
def create_alert_rule(payload: dict[str, Any], request: Request):
    actor = _actor(request)
    authorize(actor, "manage_security", workspace_id=str(payload.get("workspace_id", "default")))
    try:
        definition = validate_alert_definition(payload.get("definition") or {})
    except AlertDeliveryError as exc:
        raise SecurityError(exc.code, exc.message, status_code=422, details=exc.details) from exc
    db = request.app.state.SessionLocal()
    try:
        rule = AlertRule(tenant_id=actor.tenant_id, workspace_id=str(payload.get("workspace_id", "default")), name=str(payload.get("name", "alert")), definition=definition, enabled=bool(payload.get("enabled", True)))
        db.add(rule)
        db.commit()
        return {"id": rule.id, "tenant_id": rule.tenant_id, "workspace_id": rule.workspace_id, "name": rule.name, "definition": rule.definition, "enabled": rule.enabled}
    finally:
        db.close()


@router.get("/rules")
def list_alert_rules(request: Request, workspace_id: str = "default"):
    actor = _actor(request)
    authorize(actor, "read", workspace_id=workspace_id)
    db = request.app.state.SessionLocal()
    try:
        rows = db.query(AlertRule).filter(AlertRule.tenant_id == actor.tenant_id, AlertRule.workspace_id == workspace_id).order_by(AlertRule.created_at.asc()).all()
        return {"tenant_id": actor.tenant_id, "workspace_id": workspace_id, "rules": [{"id": row.id, "name": row.name, "definition": row.definition, "enabled": row.enabled, "created_at": row.created_at, "updated_at": row.updated_at} for row in rows]}
    finally:
        db.close()


@router.post("/evaluate")
def evaluate_alerts(payload: dict[str, Any], request: Request):
    actor = _actor(request)
    authorize(actor, "analyze")
    snapshot = payload.get("snapshot") or {}
    if not isinstance(snapshot, dict) or not snapshot.get("kpi_slug"):
        raise SecurityError("SNAPSHOT_REQUIRED", "snapshot.kpi_slug is required.", status_code=422)
    db = request.app.state.SessionLocal()
    try:
        return evaluate_and_deliver(db, tenant_id=actor.tenant_id, snapshot=snapshot, source_type=str(payload.get("source_type", "metric_snapshot")), source_id=payload.get("source_id"), approved=bool(payload.get("approved", False) and actor.is_admin), timeout_seconds=request.app.state.settings.request_timeout_seconds)
    finally:
        db.close()


@router.get("/deliveries")
def list_deliveries(request: Request, limit: int = 100):
    actor = _actor(request)
    authorize(actor, "read")
    db = request.app.state.SessionLocal()
    try:
        rows = db.query(AlertDelivery).filter(AlertDelivery.tenant_id == actor.tenant_id).order_by(AlertDelivery.created_at.desc()).limit(max(1, min(limit, 500))).all()
        return {"tenant_id": actor.tenant_id, "deliveries": [{"id": row.id, "rule_id": row.rule_id, "source_type": row.source_type, "source_id": row.source_id, "severity": row.severity, "channel": row.channel, "status": row.status, "response": row.response, "created_at": row.created_at, "delivered_at": row.delivered_at} for row in rows]}
    finally:
        db.close()
