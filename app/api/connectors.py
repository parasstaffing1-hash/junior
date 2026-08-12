from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.core.connectors.service import ConnectorError, ConnectorService, validate_schema
from app.core.security import SecurityError, authorize, assert_dataset_tenant
import pandas as pd
from app.models.all import DatasetVersion


router = APIRouter(prefix="/api/v1/connectors", tags=["connectors"])


def _actor(request: Request):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise SecurityError("AUTHENTICATION_REQUIRED", "A security principal is required.")
    return actor


@router.get("/catalog")
def connector_catalog(request: Request):
    authorize(_actor(request), "read")
    return {"connectors": ConnectorService.catalog(), "credentials_persisted": False, "secret_ref_supported": True}


@router.post("/test")
def test_connector(payload: dict[str, Any], request: Request):
    authorize(_actor(request), "analyze")
    service = ConnectorService(timeout_seconds=float(payload.get("timeout_seconds", 30)), allowed_hosts=set(payload.get("allowed_hosts") or []))
    kind = str(payload.get("kind", "")).casefold()
    try:
        if kind in {"database", "postgresql", "sqlserver", "snowflake", "bigquery"}:
            result = service.read_database(connection_url=str(payload.get("connection_url", "")), query=str(payload.get("query", "SELECT 1")), parameters=payload.get("parameters"), max_rows=1)
        elif kind in {"rest", "rest_json", "api"}:
            result = service.read_rest(url=str(payload.get("url", "")), data_path=payload.get("data_path"), headers=payload.get("headers"), max_pages=1, max_rows=5)
        elif kind == "parquet":
            result = service.read_parquet(path=str(payload.get("path", "")), max_rows=1)
        else:
            raise ConnectorError("CONNECTOR_KIND_INVALID", "kind must be database, rest_json, or parquet.")
        return {"ok": True, "source": result.source, "columns": [str(item) for item in result.frame.columns], "sample": result.frame.astype(object).where(pd.notna(result.frame), None).to_dict(orient="records")}
    except ConnectorError as exc:
        raise SecurityError(exc.code, exc.message, status_code=422, details=exc.details) from exc


@router.post("/schema/validate")
def schema_validate(payload: dict[str, Any], request: Request):
    actor = _actor(request)
    authorize(actor, "analyze")
    dataset_id = str(payload.get("dataset_id", ""))
    db = request.app.state.SessionLocal()
    try:
        dataset = assert_dataset_tenant(db, dataset_id, actor)
        version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.current_version_id).first()
        if version is None:
            raise SecurityError("VERSION_NOT_FOUND", "Dataset has no current version.", status_code=404)
        frame = pd.read_csv(request.app.state.storage.resolve(version.storage_path))
        contract = payload.get("contract") or {}
        result = validate_schema(frame, contract)
        return {"dataset_id": dataset_id, "source_version_id": version.id, "contract": contract, "validation": result, "promotion_allowed": result["valid"] or not bool(payload.get("reject_breaking", True))}
    finally:
        db.close()
