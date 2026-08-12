from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect


def _alembic(repo: Path, database_url: str, *args: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_alembic_upgrade_downgrade_round_trip(tmp_path: Path):
    repo = Path(__file__).resolve().parents[1]
    database_url = "sqlite:///" + str(tmp_path / "migrated.db")
    _alembic(repo, database_url, "upgrade", "head")

    inspector = inspect(create_engine(database_url))
    assert set(inspector.get_table_names()) == {
        "alembic_version",
        "datasets",
        "dataset_versions",
        "analysis_runs",
        "pipeline_runs",
        "artifacts",
        "audit_events",
        "report_definitions",
        "automation_runs",
        "workspace_assets",
        "registered_models",
        "model_versions",
        "experiments",
        "review_threads",
        "review_comments",
        "review_decisions",
        "workspace_collections",
        "workspace_collection_items",
        "retention_policies",
        "retention_holds",
        "retention_runs",
        "experiment_runs",
        "monitoring_policies",
        "monitoring_runs",
        "approval_records",
        "geographic_boundaries",
        "geographic_mappings",
        "tenants",
        "users",
        "api_keys",
        "workspace_memberships",
        "audit_logs",
        "security_policies",
        "job_schedules",
        "ingestion_states",
        "alert_rules",
        "alert_deliveries",
    }
    assert {column["name"] for column in inspector.get_columns("automation_runs")} >= {
        "job_type",
        "progress",
        "result",
        "artifact_ids",
    }
    assert any(
        set(constraint["column_names"]) == {"dataset_id", "version_number"}
        for constraint in inspector.get_unique_constraints("dataset_versions")
    )
    assert "metadata" in {column["name"] for column in inspector.get_columns("dataset_versions")}
    assert "tenant_id" in {column["name"] for column in inspector.get_columns("workspace_assets")}
    assert "tenant_id" in {column["name"] for column in inspector.get_columns("geographic_boundaries")}
    assert "tenant_id" in {column["name"] for column in inspector.get_columns("geographic_mappings")}
    assert "tenant_id" in {column["name"] for column in inspector.get_columns("registered_models")}
    assert "tenant_id" in {column["name"] for column in inspector.get_columns("experiments")}

    _alembic(repo, database_url, "downgrade", "base")
    assert inspect(create_engine(database_url)).get_table_names() == ["alembic_version"]
