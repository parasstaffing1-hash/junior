from __future__ import annotations

import csv
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402
from app.models.all import Base  # noqa: E402


OUTPUT = ROOT / "docs" / "integration"


def domain_for(path: str) -> str:
    if "/forecast" in path:
        return "forecasting"
    if "/ml/" in path or path.startswith("/api/v1/models"):
        return "machine_learning"
    if "/mlops/" in path or path.startswith("/api/v1/experiments"):
        return "mlops"
    if "/data-engineering/" in path:
        return "data_engineering"
    if "/orchestrat" in path:
        return "orchestration"
    if "/bi_report" in path or "/dashboard" in path or "/report" in path:
        return "bi_reporting"
    if "/datasets" in path:
        return "dataset_analytics"
    if "/automation" in path:
        return "automation"
    return "platform"


def write_endpoints() -> None:
    rows = []
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith("/api/v1"):
            continue
        for method, operation in operations.items():
            if method.upper() in {"HEAD", "OPTIONS", "PARAMETERS"}:
                continue
            rows.append(
                {
                    "method": method.upper(),
                    "path": path,
                    "operation_name": operation.get("operationId", ""),
                    "domain": domain_for(path),
                    "tags": ";".join(operation.get("tags") or []),
                }
            )
    rows.sort(key=lambda item: (item["path"], item["method"]))
    destination = OUTPUT / "API_ENDPOINT_INVENTORY.csv"
    with destination.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=["method", "path", "operation_name", "domain", "tags"])
        writer.writeheader()
        writer.writerows(rows)


def write_entities() -> None:
    rows = []
    for table in sorted(Base.metadata.tables.values(), key=lambda item: item.name):
        foreign_keys = sorted(f"{key.parent.name}->{key.target_fullname}" for key in table.foreign_keys)
        rows.append(
            {
                "entity": table.name,
                "columns": ";".join(column.name for column in table.columns),
                "primary_key": ";".join(column.name for column in table.primary_key.columns),
                "foreign_keys": ";".join(foreign_keys),
                "indexes": ";".join(sorted(index.name or "" for index in table.indexes)),
            }
        )
    destination = OUTPUT / "DATABASE_ENTITY_INVENTORY.csv"
    with destination.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=["entity", "columns", "primary_key", "foreign_keys", "indexes"])
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write_endpoints()
    write_entities()
