"""Validated runtime configuration for local, test, and production deployments."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_env: str
    database_url: str
    storage_root: Path
    max_upload_bytes: int
    auth_mode: str
    admin_api_key: str | None
    allowed_origins: tuple[str, ...]
    require_tenant_header: bool
    rate_limit_per_minute: int
    request_timeout_seconds: int
    job_poll_seconds: float
    worker_id: str
    job_worker_enabled: bool

    @property
    def production(self) -> bool:
        return self.app_env.casefold() in {"production", "prod"}


def load_settings(*, database_url: str | None = None, storage_root: str | Path | None = None) -> Settings:
    load_dotenv()
    app_env = os.getenv("APP_ENV", "development").strip().casefold()
    configured_auth = os.getenv("AUTH_MODE")
    auth_mode = (configured_auth or ("api_key" if app_env in {"production", "prod"} else "disabled")).strip().casefold()
    if auth_mode not in {"disabled", "api_key"}:
        raise RuntimeError("AUTH_MODE must be 'disabled' or 'api_key'.")

    configured_limit = int(os.getenv("MAX_UPLOAD_BYTES", str(100 * 1024 * 1024)))
    if configured_limit <= 0:
        raise RuntimeError("MAX_UPLOAD_BYTES must be greater than zero.")
    rate_limit = int(os.getenv("RATE_LIMIT_PER_MINUTE", "120"))
    if rate_limit <= 0:
        raise RuntimeError("RATE_LIMIT_PER_MINUTE must be greater than zero.")
    timeout = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "300"))
    if timeout <= 0:
        raise RuntimeError("REQUEST_TIMEOUT_SECONDS must be greater than zero.")

    origins = tuple(item.strip() for item in os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",") if item.strip())
    admin_key = os.getenv("ADMIN_API_KEY") or os.getenv("BOOTSTRAP_ADMIN_API_KEY")
    if auth_mode == "api_key" and not admin_key:
        raise RuntimeError("ADMIN_API_KEY is required when AUTH_MODE=api_key.")

    root = Path(storage_root or os.getenv("STORAGE_ROOT") or Path(__file__).resolve().parents[2] / "storage").expanduser()
    return Settings(
        app_env=app_env,
        database_url=database_url or os.getenv("DATABASE_URL", "sqlite:///./analytics.db"),
        storage_root=root,
        max_upload_bytes=configured_limit,
        auth_mode=auth_mode,
        admin_api_key=admin_key,
        allowed_origins=origins,
        require_tenant_header=_bool(os.getenv("REQUIRE_TENANT_HEADER"), default=app_env in {"production", "prod"}),
        rate_limit_per_minute=rate_limit,
        request_timeout_seconds=timeout,
        job_poll_seconds=max(0.1, float(os.getenv("JOB_POLL_SECONDS", "2"))),
        worker_id=os.getenv("WORKER_ID", f"local-{os.getpid()}"),
        job_worker_enabled=_bool(os.getenv("JOB_WORKER_ENABLED"), default=False),
    )
