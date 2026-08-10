from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path: Path):
    app = create_app(
        database_url="sqlite:///" + str(tmp_path / "analytics.db"),
        storage_root=tmp_path / "storage",
    )
    with TestClient(app) as test_client:
        yield test_client
    app.state.engine.dispose()


@pytest.fixture
def csv_file():
    return {
        "file": (
            "sample.csv",
            b"name,age,region\nAlice,30,North\nBob,,South\nAlice,30,North\n",
            "text/csv",
        )
    }
