from __future__ import annotations

from scripts.validate_release_configuration import validate


def test_release_validator_accepts_source_controlled_test_mode():
    result = validate("test")
    assert result["ready"] is True
    assert not result["blocking_checks"]


def test_release_validator_fails_closed_for_unconfigured_production(monkeypatch):
    for name in ("APP_ENV", "AUTH_MODE", "ADMIN_API_KEY", "REQUIRE_TENANT_HEADER", "DATABASE_URL", "JOB_WORKER_ENABLED", "WORKER_ID", "ALLOWED_ORIGINS", "BACKUP_ROOT", "BACKUP_BUCKET"):
        monkeypatch.delenv(name, raising=False)
    result = validate("production")
    assert result["ready"] is False
    assert {item["id"] for item in result["blocking_checks"]} >= {"authentication", "postgresql", "worker", "backup"}
