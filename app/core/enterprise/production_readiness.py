from __future__ import annotations

import os
from typing import Any


def production_readiness(settings, *, database_url: str | None = None) -> dict[str, Any]:
    url = database_url or settings.database_url
    checks = [
        {"id": "authentication", "required": True, "passed": settings.auth_mode == "api_key" and bool(settings.admin_api_key), "evidence": "AUTH_MODE=api_key with ADMIN_API_KEY"},
        {"id": "tenant_isolation", "required": True, "passed": settings.require_tenant_header, "evidence": "REQUIRE_TENANT_HEADER=true"},
        {"id": "production_database", "required": True, "passed": url.casefold().startswith("postgresql"), "evidence": "PostgreSQL is required for production"},
        {"id": "durable_worker", "required": True, "passed": settings.job_worker_enabled, "evidence": "JOB_WORKER_ENABLED=true and worker service deployed"},
        {"id": "allowed_origins", "required": True, "passed": bool(settings.allowed_origins) and "*" not in settings.allowed_origins, "evidence": "Explicit ALLOWED_ORIGINS"},
        {"id": "upload_limit", "required": True, "passed": settings.max_upload_bytes > 0, "evidence": "Positive MAX_UPLOAD_BYTES"},
        {"id": "external_identity", "required": False, "passed": bool(os.getenv("ENTRA_TENANT_ID") and os.getenv("ENTRA_CLIENT_ID")), "evidence": "Optional Entra/OIDC integration credentials"},
        {"id": "powerbi_service", "required": False, "passed": bool(os.getenv("POWERBI_BASE_URL") and os.getenv("POWERBI_ACCESS_TOKEN")), "evidence": "Optional Power BI Service credentials"},
        {"id": "backup_destination", "required": True, "passed": bool(os.getenv("BACKUP_ROOT") or os.getenv("BACKUP_BUCKET")), "evidence": "BACKUP_ROOT or BACKUP_BUCKET must be configured"},
    ]
    blockers = [item for item in checks if item["required"] and not item["passed"]]
    optional = [item for item in checks if not item["required"] and not item["passed"]]
    return {"ready": not blockers, "score": round(100 * sum(bool(item["passed"]) for item in checks) / len(checks)), "required_checks": checks, "blocking_checks": blockers, "optional_external_checks": optional, "note": "External identity and BI tenant checks cannot be proven by local code alone; configure credentials and run their approval-gated validation before public deployment."}
