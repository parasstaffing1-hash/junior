from __future__ import annotations


def test_bounded_notebook_runtime_is_reproducible_and_read_only(client):
    imported = client.post("/api/v1/datasets/import", files={"file": ("sales.csv", b"Region,Sales\nNorth,10\nNorth,12\nSouth,7\n", "text/csv")})
    assert imported.status_code == 200, imported.text
    dataset_id = imported.json()["dataset_id"]
    notebook = client.post(
        "/api/v1/workspaces/finance/assets",
        json={
            "asset_type": "notebook",
            "name": "Sales QA notebook",
            "dataset_id": dataset_id,
            "definition": {
                "language": "python",
                "dataset_id": dataset_id,
                "cells": [
                    {"id": "profile", "operation": "profile"},
                    {"id": "totals", "operation": "groupby_sum", "args": {"by": "Region", "value": "Sales"}},
                ],
            },
        },
    )
    assert notebook.status_code == 201, notebook.text
    run = client.post(f"/api/v1/workspaces/finance/notebooks/{notebook.json()['id']}/run", json={})
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["manifest"]["execution_mode"] == "bounded_allowlisted_notebook_dsl"
    assert body["result"]["operations"] == ["groupby_sum", "profile"]
    assert body["result"]["cells"][1]["result"]["rows"][0] == {"Region": "North", "Sales": 22}


def test_notebook_rejects_arbitrary_operations(client):
    imported = client.post("/api/v1/datasets/import", files={"file": ("sales.csv", b"Region,Sales\nNorth,10\n", "text/csv")})
    dataset_id = imported.json()["dataset_id"]
    notebook = client.post(
        "/api/v1/workspaces/finance/assets",
        json={"asset_type": "notebook", "name": "Unsafe", "dataset_id": dataset_id, "definition": {"language": "python", "dataset_id": dataset_id, "cells": [{"operation": "exec"}]}},
    )
    assert notebook.status_code == 201
    run = client.post(f"/api/v1/workspaces/finance/notebooks/{notebook.json()['id']}/run", json={})
    assert run.status_code == 422
    assert run.json()["error"]["code"] == "UNSUPPORTED_OPERATION"
