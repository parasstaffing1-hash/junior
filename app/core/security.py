"""Authentication, tenant isolation, authorization, and audit primitives."""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from secrets import token_urlsafe
from threading import Lock
from typing import Any

from fastapi import Request

from app.core.config import Settings
from app.models.all import ApiKey, AuditLog, Dataset, Tenant, User, WorkspaceMembership


class SecurityError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 401, details: dict[str, Any] | None = None):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class Actor:
    principal_id: str
    tenant_id: str
    roles: frozenset[str] = field(default_factory=frozenset)
    scopes: frozenset[str] = field(default_factory=frozenset)
    workspace_ids: frozenset[str] = field(default_factory=frozenset)
    auth_type: str = "development"
    api_key_id: str | None = None

    @property
    def is_admin(self) -> bool:
        return bool(self.roles & {"admin", "owner"})


ROLE_PERMISSIONS = {
    "viewer": {"read"},
    "analyst": {"read", "analyze", "export"},
    "developer": {"read", "analyze", "export", "write", "deploy"},
    "admin": {"read", "analyze", "export", "write", "deploy", "manage_security"},
    "owner": {"read", "analyze", "export", "write", "deploy", "manage_security"},
}


def hash_api_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def issue_api_key(*, name: str = "client", prefix: str = "jda") -> tuple[str, str, str]:
    secret = token_urlsafe(32)
    value = f"{prefix}_{secret}"
    return value, value[: min(12, len(value))], hash_api_key(value)


def _header_key(request: Request) -> str | None:
    candidate = request.headers.get("x-api-key")
    if candidate:
        return candidate.strip()
    authorization = request.headers.get("authorization", "")
    if authorization.casefold().startswith("bearer "):
        return authorization[7:].strip()
    return None


def _tenant_id(request: Request, settings: Settings) -> str:
    value = request.headers.get("x-tenant-id") or request.headers.get("x-organization-id")
    if not value and settings.require_tenant_header:
        raise SecurityError("TENANT_REQUIRED", "X-Tenant-ID is required for this deployment.", status_code=400)
    return value.strip() if value else "default"


def authenticate(request: Request, settings: Settings, db) -> Actor:
    candidate = _header_key(request)
    if settings.auth_mode == "disabled":
        tenant_id = _tenant_id(request, settings)
        return Actor("development", tenant_id, frozenset({"owner", "admin"}), frozenset({"*"}), frozenset({"*"}))
    if not candidate:
        raise SecurityError("AUTHENTICATION_REQUIRED", "Provide an API key using X-API-Key or Authorization: Bearer.")

    tenant_id = _tenant_id(request, settings)

    if settings.admin_api_key and hmac.compare_digest(candidate, settings.admin_api_key):
        return Actor("bootstrap-admin", tenant_id, frozenset({"owner", "admin"}), frozenset({"*"}), frozenset({"*"}), "bootstrap")

    record = db.query(ApiKey).filter(ApiKey.key_hash == hash_api_key(candidate), ApiKey.active.is_(True)).first()
    if record is None or (record.expires_at and record.expires_at <= datetime.now(timezone.utc).replace(tzinfo=None)):
        raise SecurityError("INVALID_API_KEY", "The API key is invalid or expired.")
    if record.tenant_id != tenant_id:
        raise SecurityError("TENANT_FORBIDDEN", "The API key does not belong to this tenant.", status_code=403)
    if record.user_id:
        user = db.query(User).filter(User.id == record.user_id, User.active.is_(True)).first()
        if user is None:
            raise SecurityError("USER_INACTIVE", "The API key owner is inactive.", status_code=403)
    record.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return Actor(record.user_id or record.id, record.tenant_id, frozenset(record.roles or ["viewer"]), frozenset(record.scopes or ["read"]), frozenset(record.workspace_ids or []), "api_key", record.id)


def authorize(actor: Actor, permission: str, *, workspace_id: str | None = None) -> None:
    if actor.is_admin or permission in actor.scopes:
        return
    granted = set().union(*(ROLE_PERMISSIONS.get(role, set()) for role in actor.roles))
    if permission not in granted:
        raise SecurityError("FORBIDDEN", f"Permission '{permission}' is required.", status_code=403, details={"permission": permission})
    if workspace_id and actor.workspace_ids and "*" not in actor.workspace_ids and workspace_id not in actor.workspace_ids:
        raise SecurityError("WORKSPACE_FORBIDDEN", "The actor is not a member of this workspace.", status_code=403, details={"workspace_id": workspace_id})


def assert_dataset_tenant(db, dataset_id: str, actor: Actor) -> Dataset:
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if dataset is None:
        raise SecurityError("DATASET_NOT_FOUND", "Dataset was not found.", status_code=404, details={"dataset_id": dataset_id})
    if dataset.tenant_id != actor.tenant_id:
        raise SecurityError("TENANT_FORBIDDEN", "The dataset belongs to another tenant.", status_code=403, details={"dataset_id": dataset_id})
    return dataset


class RateLimiter:
    """Bounded in-process limiter; deployments with replicas should front this with a shared gateway."""

    def __init__(self, limit: int, window_seconds: int = 60):
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            events = self._events[key]
            while events and now - events[0] >= self.window_seconds:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            if len(self._events) > 10_000:
                for stale_key, stale_events in list(self._events.items())[:1000]:
                    if not stale_events or now - stale_events[-1] >= self.window_seconds:
                        self._events.pop(stale_key, None)
            return True


def audit_request(db, *, actor: Actor, request: Request, status_code: int, duration_ms: float, success: bool, error_code: str | None = None) -> None:
    db.add(AuditLog(
        tenant_id=actor.tenant_id,
        actor_id=actor.principal_id,
        action=f"{request.method} {request.url.path}",
        resource_type="http_request",
        resource_id=request.headers.get("x-request-id"),
        success=success,
        status_code=status_code,
        request_id=request.headers.get("x-request-id"),
        correlation_id=request.headers.get("x-correlation-id"),
        metadata_json={"query": dict(request.query_params), "error_code": error_code},
        duration_ms=duration_ms,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    ))
    db.commit()
