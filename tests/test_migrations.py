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
    }
    assert any(
        set(constraint["column_names"]) == {"dataset_id", "version_number"}
        for constraint in inspector.get_unique_constraints("dataset_versions")
    )

    _alembic(repo, database_url, "downgrade", "base")
    assert inspect(create_engine(database_url)).get_table_names() == ["alembic_version"]
