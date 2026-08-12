"""Fail-closed source and deployment configuration validator.

The test environment validates the release shape without requiring customer
secrets. Production mode additionally requires the minimum controls that make
the runtime safe to expose publicly. Values are never printed.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SOURCE_FILES = (
    ".env.example",
    "Dockerfile",
    "docker-compose.yml",
    "alembic.ini",
    "requirements.txt",
    "README.md",
    "docs/PRODUCTION_RUNBOOK.md",
    "app/main.py",
    "frontend/index.html",
    "scripts/run_worker.py",
    "scripts/backup_database.py",
    "scripts/validate_release_configuration.py",
)
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*['\"][^<\"']{12,}['\"]", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{24,}"),
)


def _env(name: str) -> str:
    return str(os.getenv(name, "")).strip()


def _check(name: str, passed: bool, evidence: str, *, required: bool = True) -> dict[str, object]:
    return {"id": name, "passed": bool(passed), "required": bool(required), "evidence": evidence}


def _tracked_source_paths() -> list[Path]:
    paths: list[Path] = []
    for base in (ROOT / "app", ROOT / "scripts", ROOT / "tests"):
        if not base.is_dir():
            continue
        paths.extend(path for path in base.rglob("*") if path.is_file() and path.suffix.casefold() in {".py", ".yml", ".yaml", ".json", ".toml"})
    paths.extend(ROOT / item for item in ("Dockerfile", "docker-compose.yml", "requirements.txt"))
    return paths


def validate(environment: str) -> dict[str, object]:
    environment = str(environment).casefold().strip()
    if environment not in {"development", "test", "production"}:
        raise ValueError("environment must be development, test or production")
    checks: list[dict[str, object]] = []
    missing = [item for item in REQUIRED_SOURCE_FILES if not (ROOT / item).is_file()]
    checks.append(_check("release_source_shape", not missing, "All required release files are present." if not missing else f"Missing: {', '.join(missing)}"))
    env_example = ROOT / ".env.example"
    example_text = env_example.read_text(encoding="utf-8") if env_example.is_file() else ""
    checks.append(_check("env_contract", "DATABASE_URL=" in example_text and "AUTH_MODE=" in example_text and "ADMIN_API_KEY=" in example_text, "The example declares the database, auth and origin contract."))
    leaks: list[str] = []
    for path in _tracked_source_paths():
        if path.resolve() == Path(__file__).resolve():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            leaks.append(path.relative_to(ROOT).as_posix())
    checks.append(_check("secret_scan", not leaks, "No obvious literal credentials were found in source configuration." if not leaks else f"Potential credential material found in: {', '.join(leaks)}"))
    if environment == "production":
        origins = _env("ALLOWED_ORIGINS")
        checks.extend([
            _check("app_env", _env("APP_ENV").casefold() == "production", "APP_ENV=production is required."),
            _check("authentication", _env("AUTH_MODE").casefold() == "api_key" and len(_env("ADMIN_API_KEY")) >= 32, "API-key authentication and a strong bootstrap key are required."),
            _check("tenant_header", _env("REQUIRE_TENANT_HEADER").casefold() == "true", "Tenant headers are required."),
            _check("postgresql", _env("DATABASE_URL").casefold().startswith("postgresql"), "A managed PostgreSQL URL is required."),
            _check("worker", _env("JOB_WORKER_ENABLED").casefold() == "true" and bool(_env("WORKER_ID")), "A durable worker and unique worker ID are required."),
            _check("cors", bool(origins) and "*" not in origins, "Explicit non-wildcard ALLOWED_ORIGINS are required."),
            _check("backup", bool(_env("BACKUP_ROOT") or _env("BACKUP_BUCKET")), "A durable backup destination is required."),
        ])
    else:
        checks.append(_check("local_mode_explicit", environment in {"development", "test"}, "Local/test validation does not require customer credentials."))
    blockers = [item for item in checks if item["required"] and not item["passed"]]
    return {"environment": environment, "ready": not blockers, "checks": checks, "blocking_checks": blockers, "note": "Production values are evaluated but never echoed."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", default="test", choices=("development", "test", "production"))
    args = parser.parse_args(argv)
    result = validate(args.environment)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
