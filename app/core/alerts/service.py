from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.kpi.alerts import AlertRuleError, evaluate_rule, validate_rule
from app.core.connectors.service import _validate_url
from app.core.intelligence.common import json_safe
from app.models.all import AlertDelivery, AlertRule


class AlertDeliveryError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


def validate_alert_definition(definition: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(definition, dict):
        raise AlertDeliveryError("INVALID_ALERT_DEFINITION", "Alert definition must be an object.")
    try:
        validate_rule(definition)
    except AlertRuleError as exc:
        raise AlertDeliveryError(exc.code, exc.message, details=exc.details) from exc
    channel = str(definition.get("channel", "webhook")).casefold()
    if channel != "webhook":
        raise AlertDeliveryError("UNSUPPORTED_ALERT_CHANNEL", "Only the approval-gated webhook channel is available in this deployment.", details={"channel": channel})
    ref = str(definition.get("webhook_secret_ref", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", ref):
        raise AlertDeliveryError("WEBHOOK_SECRET_REF_REQUIRED", "webhook_secret_ref must name a deployment secret; raw URLs are never stored.")
    return {**json_safe(definition), "channel": "webhook", "webhook_secret_ref": ref}


def _webhook_url(secret_ref: str) -> str:
    env_name = "ALERT_WEBHOOK_" + re.sub(r"[^A-Za-z0-9]", "_", secret_ref).upper()
    value = os.getenv(env_name)
    if not value:
        raise AlertDeliveryError("WEBHOOK_NOT_CONFIGURED", "The alert webhook secret is not configured in the deployment environment.", details={"expected_secret": env_name})
    try:
        return _validate_url(value)
    except Exception as exc:
        raise AlertDeliveryError("WEBHOOK_URL_BLOCKED", "The configured webhook URL is invalid or private.") from exc


def _public_delivery(delivery: AlertDelivery) -> dict[str, Any]:
    return {
        "id": delivery.id,
        "tenant_id": delivery.tenant_id,
        "rule_id": delivery.rule_id,
        "source_type": delivery.source_type,
        "source_id": delivery.source_id,
        "severity": delivery.severity,
        "channel": delivery.channel,
        "status": delivery.status,
        "response": delivery.response,
        "created_at": delivery.created_at,
        "delivered_at": delivery.delivered_at,
    }


def evaluate_and_deliver(
    db,
    *,
    tenant_id: str,
    snapshot: dict[str, Any],
    source_type: str = "metric_snapshot",
    source_id: str | None = None,
    approved: bool = False,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    rules = db.query(AlertRule).filter(AlertRule.tenant_id == tenant_id, AlertRule.enabled.is_(True)).order_by(AlertRule.created_at.asc()).all()
    evaluations: list[dict[str, Any]] = []
    deliveries: list[dict[str, Any]] = []
    for rule in rules:
        definition = dict(rule.definition or {})
        try:
            evaluation = evaluate_rule(definition, snapshot)
        except AlertRuleError as exc:
            evaluation = {"matched": False, "triggered": False, "reason": "invalid_rule", "error": {"code": exc.code, "message": exc.message}}
        evaluation = {"rule_id": rule.id, "rule_name": rule.name, **json_safe(evaluation)}
        evaluations.append(evaluation)
        if not evaluation.get("triggered"):
            continue
        payload = {
            "event": "data_alert_triggered",
            "alert_rule_id": rule.id,
            "alert_rule_name": rule.name,
            "source_type": source_type,
            "source_id": source_id,
            "snapshot": json_safe(snapshot),
            "evaluation": evaluation,
            "emitted_at": datetime.now(timezone.utc).isoformat(),
        }
        delivery = AlertDelivery(
            tenant_id=tenant_id,
            rule_id=rule.id,
            source_type=source_type,
            source_id=source_id,
            severity=str(definition.get("severity", "warning")),
            channel=str(definition.get("channel", "webhook")),
            status="PENDING_APPROVAL" if not approved else "QUEUED",
            payload=payload,
            response={"approval_required": not approved},
        )
        db.add(delivery)
        db.flush()
        if approved:
            try:
                target = _webhook_url(str(definition["webhook_secret_ref"]))
                with httpx.Client(timeout=max(1, min(int(timeout_seconds), 120)), follow_redirects=False) as client:
                    response = client.post(target, json=payload)
                delivery.status = "DELIVERED" if 200 <= response.status_code < 300 else "FAILED"
                delivery.response = {"status_code": response.status_code, "body_sha256": hashlib.sha256(response.content).hexdigest(), "body_bytes": len(response.content)}
                if delivery.status == "DELIVERED":
                    delivery.delivered_at = datetime.now(timezone.utc).replace(tzinfo=None)
            except (httpx.HTTPError, AlertDeliveryError) as exc:
                delivery.status = "FAILED"
                delivery.response = {"error": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
        db.flush()
        deliveries.append(_public_delivery(delivery))
    db.commit()
    return {"tenant_id": tenant_id, "rules_evaluated": len(rules), "triggered_count": len(deliveries), "evaluations": evaluations, "deliveries": deliveries, "approved_for_external_delivery": approved}


__all__ = ["AlertDeliveryError", "evaluate_and_deliver", "validate_alert_definition"]
