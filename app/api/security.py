from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Request
import pandas as pd

from app.core.privacy import classify_frame, mask_frame
from app.core.security import SecurityError, authorize, issue_api_key
from app.models.all import ApiKey, AuditLog, SecurityPolicy, Tenant, User, WorkspaceMembership


router = APIRouter(prefix="/api/v1/security", tags=["security"])


def _actor(request: Request):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise SecurityError("AUTHENTICATION_REQUIRED", "A security principal is required.")
    return actor


@router.get("/session")
def session_info(request: Request):
    actor = _actor(request)
    return {
        "principal_id": actor.principal_id,
        "tenant_id": actor.tenant_id,
        "roles": sorted(actor.roles),
        "scopes": sorted(actor.scopes),
        "workspace_ids": sorted(actor.workspace_ids),
        "auth_type": actor.auth_type,
        "production_auth_required": request.app.state.settings.auth_mode != "disabled",
    }


@router.post("/tenants", status_code=201)
def create_tenant(payload: dict[str, Any], request: Request):
    actor = _actor(request)
    authorize(actor, "manage_security")
    db = request.app.state.SessionLocal()
    try:
        slug = str(payload.get("slug", "")).strip().casefold()
        name = str(payload.get("name", "")).strip()
        if not slug or not name:
            raise SecurityError("TENANT_FIELDS_REQUIRED", "slug and name are required.", status_code=422)
        if db.query(Tenant).filter(Tenant.slug == slug).first():
            raise SecurityError("TENANT_EXISTS", "The tenant slug already exists.", status_code=409)
        tenant = Tenant(slug=slug, name=name, status="active", plan=str(payload.get("plan", "standard")))
        db.add(tenant)
        db.commit()
        return {"tenant_id": tenant.id, "slug": tenant.slug, "name": tenant.name, "status": tenant.status}
    finally:
        db.close()


@router.post("/users", status_code=201)
def create_user(payload: dict[str, Any], request: Request):
    actor = _actor(request)
    authorize(actor, "manage_security")
    db = request.app.state.SessionLocal()
    try:
        tenant_id = str(payload.get("tenant_id") or actor.tenant_id)
        email = str(payload.get("email", "")).strip().casefold()
        if not email:
            raise SecurityError("USER_EMAIL_REQUIRED", "email is required.", status_code=422)
        if db.query(User).filter(User.tenant_id == tenant_id, User.email == email).first():
            raise SecurityError("USER_EXISTS", "The user already exists in this tenant.", status_code=409)
        user = User(tenant_id=tenant_id, email=email, display_name=str(payload.get("display_name") or email), is_admin=bool(payload.get("is_admin", False)))
        db.add(user)
        db.flush()
        for workspace_id in payload.get("workspace_ids") or ["default"]:
            db.add(WorkspaceMembership(tenant_id=tenant_id, user_id=user.id, workspace_id=str(workspace_id), role=str(payload.get("role", "analyst"))))
        db.commit()
        return {"user_id": user.id, "tenant_id": user.tenant_id, "email": user.email, "workspace_ids": payload.get("workspace_ids") or ["default"]}
    finally:
        db.close()


@router.post("/api-keys", status_code=201)
def create_api_key(payload: dict[str, Any], request: Request):
    actor = _actor(request)
    authorize(actor, "manage_security")
    db = request.app.state.SessionLocal()
    try:
        tenant_id = str(payload.get("tenant_id") or actor.tenant_id)
        value, prefix, digest = issue_api_key(name=str(payload.get("name", "client")))
        record = ApiKey(
            tenant_id=tenant_id,
            user_id=payload.get("user_id"),
            name=str(payload.get("name", "client")),
            key_prefix=prefix,
            key_hash=digest,
            roles=list(payload.get("roles") or ["analyst"]),
            scopes=list(payload.get("scopes") or ["read", "analyze", "export"]),
            workspace_ids=list(payload.get("workspace_ids") or ["default"]),
            active=True,
            expires_at=(datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=int(payload.get("expires_in_days", 365)))) if payload.get("expires_in_days") else None,
        )
        db.add(record)
        db.commit()
        return {"api_key_id": record.id, "name": record.name, "key_prefix": record.key_prefix, "api_key": value, "warning": "Store this key now; it will not be shown again."}
    finally:
        db.close()


@router.get("/audit")
def list_audit(request: Request, limit: int = 100):
    actor = _actor(request)
    authorize(actor, "manage_security")
    db = request.app.state.SessionLocal()
    try:
        rows = db.query(AuditLog).filter(AuditLog.tenant_id == actor.tenant_id).order_by(AuditLog.created_at.desc()).limit(max(1, min(limit, 500))).all()
        return {"tenant_id": actor.tenant_id, "events": [{"id": row.id, "actor_id": row.actor_id, "action": row.action, "resource_type": row.resource_type, "resource_id": row.resource_id, "success": row.success, "status_code": row.status_code, "request_id": row.request_id, "correlation_id": row.correlation_id, "metadata": row.metadata_json, "duration_ms": row.duration_ms, "created_at": row.created_at} for row in rows]}
    finally:
        db.close()


@router.get("/policies")
def list_policies(request: Request, workspace_id: str = "default"):
    actor = _actor(request)
    authorize(actor, "read", workspace_id=workspace_id)
    db = request.app.state.SessionLocal()
    try:
        rows = db.query(SecurityPolicy).filter(SecurityPolicy.tenant_id == actor.tenant_id, SecurityPolicy.workspace_id == workspace_id).order_by(SecurityPolicy.created_at.asc()).all()
        return {"workspace_id": workspace_id, "policies": [{"id": row.id, "name": row.name, "target_type": row.target_type, "definition": row.definition, "enabled": row.enabled, "created_at": row.created_at, "updated_at": row.updated_at} for row in rows]}
    finally:
        db.close()


@router.post("/policies", status_code=201)
def create_policy(payload: dict[str, Any], request: Request):
    actor = _actor(request)
    authorize(actor, "manage_security", workspace_id=str(payload.get("workspace_id", "default")))
    db = request.app.state.SessionLocal()
    try:
        workspace_id = str(payload.get("workspace_id", "default"))
        policy = SecurityPolicy(tenant_id=actor.tenant_id, workspace_id=workspace_id, name=str(payload.get("name", "policy")), target_type=str(payload.get("target_type", "row")), definition=payload.get("definition") or {}, enabled=bool(payload.get("enabled", True)))
        db.add(policy)
        db.commit()
        return {"id": policy.id, "workspace_id": policy.workspace_id, "name": policy.name, "target_type": policy.target_type, "definition": policy.definition, "enabled": policy.enabled}
    finally:
        db.close()


@router.post("/pii/profile")
def profile_pii(payload: dict[str, Any], request: Request):
    actor = _actor(request)
    authorize(actor, "analyze")
    dataset_id = str(payload.get("dataset_id", ""))
    if not dataset_id:
        raise SecurityError("DATASET_ID_REQUIRED", "dataset_id is required.", status_code=422)
    db = request.app.state.SessionLocal()
    try:
        from app.core.security import assert_dataset_tenant
        dataset = assert_dataset_tenant(db, dataset_id, actor)
        version = next((item for item in dataset.versions if item.id == dataset.current_version_id), None)
        if version is None:
            raise SecurityError("VERSION_NOT_FOUND", "Dataset has no current version.", status_code=404)
        frame = pd.read_csv(request.app.state.storage.resolve(version.storage_path))
        profile = classify_frame(frame)
        if bool(payload.get("mask")):
            return {"dataset_id": dataset_id, "source_version_id": version.id, "profile": profile, "preview": mask_frame(frame, profile).head(20).to_dict(orient="records")}
        return {"dataset_id": dataset_id, "source_version_id": version.id, "profile": profile}
    finally:
        db.close()
