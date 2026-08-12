from __future__ import annotations

import hashlib
import os
import re
from typing import Any

import pandas as pd
from fastapi import APIRouter, Request

from app.core.connectors.service import ConnectorError, ConnectorService, validate_schema
from app.core.data_engineering.service import DataEngineeringService
from app.core.intelligence.common import dataframe_fingerprint, json_safe
from app.core.security import SecurityError, assert_dataset_tenant, authorize
from app.errors import AppError
from app.models.all import AuditEvent, DatasetVersion, IngestionState, WorkspaceAsset


router = APIRouter(prefix="/api/v1/datasets", tags=["ingestion"])


def _actor(request: Request):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise SecurityError("AUTHENTICATION_REQUIRED", "A security principal is required.")
    authorize(actor, "write")
    return actor


def _connection_url(connection_ref: str) -> str:
    ref = str(connection_ref or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", ref):
        raise AppError("INVALID_CONNECTION_REF", "connection_ref must be a secret reference, not a raw URL.", status_code=422)
    env_name = "DATA_SOURCE_URL_" + re.sub(r"[^A-Za-z0-9]", "_", ref).upper()
    value = os.getenv(env_name)
    if not value:
        raise AppError("CONNECTION_SECRET_NOT_CONFIGURED", "The referenced source connection is not configured in the deployment secret manager.", status_code=424, details={"connection_ref": ref, "expected_secret": env_name})
    return value


def _source_object(payload: dict[str, Any], *, kind: str) -> str:
    explicit = str(payload.get("source_object") or "").strip()
    if explicit:
        return explicit[:255]
    basis = str(payload.get("query") or payload.get("url") or payload.get("data_path") or kind)
    return f"{kind}:{hashlib.sha256(basis.encode('utf-8')).hexdigest()[:24]}"


def _filter_watermark(frame: pd.DataFrame, column: str, last_value: Any) -> pd.DataFrame:
    if column not in frame.columns:
        raise AppError("WATERMARK_COLUMN_NOT_FOUND", "The configured watermark column is not present in the source result.", status_code=422, details={"column": column, "columns": [str(item) for item in frame.columns]})
    if last_value is None:
        return frame
    series = frame[column]
    if pd.api.types.is_numeric_dtype(series):
        return frame[pd.to_numeric(series, errors="coerce") > float(last_value)].copy()
    parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    if parsed.notna().mean() >= 0.9:
        cutoff = pd.to_datetime(last_value, errors="coerce")
        if pd.notna(cutoff):
            return frame[parsed > cutoff].copy()
    return frame[series.astype("string") > str(last_value)].copy()


def _max_watermark(frame: pd.DataFrame, column: str) -> Any:
    if column not in frame.columns or frame.empty:
        return None
    series = frame[column].dropna()
    if series.empty:
        return None
    if pd.api.types.is_numeric_dtype(series):
        return json_safe(series.max())
    parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    if parsed.notna().mean() >= 0.9:
        return json_safe(parsed.max())
    return json_safe(series.astype("string").max())


def _contract_for(db, dataset_id: str | None, tenant_id: str) -> tuple[dict[str, Any] | None, WorkspaceAsset | None]:
    if not dataset_id:
        return None, None
    asset = db.query(WorkspaceAsset).filter(
        WorkspaceAsset.dataset_id == dataset_id,
        WorkspaceAsset.tenant_id == tenant_id,
        WorkspaceAsset.workspace_id == "default",
        WorkspaceAsset.asset_type == "data_contract",
        WorkspaceAsset.status.in_(["draft", "approved", "certified", "published"]),
    ).order_by(WorkspaceAsset.version.desc()).first()
    if asset is None:
        return None, None
    definition = asset.definition_json or {}
    return dict(definition.get("contract") or definition), asset


def _persist_connector_result(
    *,
    db,
    request: Request,
    actor,
    frame: pd.DataFrame,
    kind: str,
    connection_ref: str,
    source_object: str,
    mode: str,
    watermark_column: str | None,
    state: IngestionState | None,
    source_metadata: dict[str, Any],
):
    target_dataset_id = state.dataset_id if state is not None else None
    if mode == "incremental" and state is None:
        raise AppError("INGESTION_STATE_REQUIRED", "Run a full load before the first incremental load for this source.", status_code=409)
    if mode == "incremental":
        if not watermark_column:
            raise AppError("WATERMARK_COLUMN_REQUIRED", "incremental mode requires watermark_column.", status_code=422)
        frame = _filter_watermark(frame, watermark_column, state.last_watermark if state else None)
        if frame.empty:
            return {"status": "NO_NEW_ROWS", "dataset_id": target_dataset_id, "last_watermark": state.last_watermark if state else None, "source": source_metadata}

    contract, contract_asset = _contract_for(db, target_dataset_id, actor.tenant_id)
    contract_result = validate_schema(frame, contract) if contract else {"valid": True, "breaking": False, "missing_columns": [], "unexpected_columns": [], "type_changes": [], "required_columns_with_nulls": []}
    if not contract_result["valid"]:
        raise AppError("SCHEMA_CONTRACT_BREACH", "The source result violates the approved dataset contract; no version was created.", status_code=409, details={"contract_asset_id": contract_asset.id if contract_asset else None, "validation": contract_result})

    parent_version_id = state.last_version_id if state else None
    metadata = {
        "ingestion": {
            "kind": kind,
            "connection_ref": connection_ref,
            "source_object": source_object,
            "mode": mode,
            "watermark_column": watermark_column,
            "last_watermark": _max_watermark(frame, watermark_column) if watermark_column else None,
            "contract_asset_id": contract_asset.id if contract_asset else None,
            "schema_validation": json_safe(contract_result),
        },
        "source": json_safe(source_metadata),
    }
    imported = request.app.state.importer.import_dataframe(
        db,
        frame,
        name=str(source_object),
        source_type=kind,
        tenant_id=actor.tenant_id,
        dataset_id=target_dataset_id,
        parent_version_id=parent_version_id,
        metadata=metadata,
    )
    audit = AuditEvent(
        dataset_id=imported["dataset_id"],
        input_version_id=parent_version_id or imported["version_id"],
        output_version_id=imported["version_id"],
        engine=f"ConnectorImport.{kind}",
        parameters=json_safe({"connection_ref": connection_ref, "source_object": source_object, "mode": mode, "watermark_column": watermark_column, "contract_asset_id": contract_asset.id if contract_asset else None}),
        affected_rows=int(len(frame)),
        affected_columns=int(len(frame.columns)),
        before_stats={"source": source_metadata},
        after_stats={"schema_validation": contract_result, "fingerprint": dataframe_fingerprint(frame)},
    )
    db.add(audit)
    if state is None:
        state = IngestionState(
            tenant_id=actor.tenant_id,
            connection_ref=connection_ref,
            source_object=source_object,
            dataset_id=imported["dataset_id"],
        )
        db.add(state)
    state.dataset_id = imported["dataset_id"]
    state.last_version_id = imported["version_id"]
    state.last_watermark = _max_watermark(frame, watermark_column) if watermark_column else None
    state.last_fingerprint = dataframe_fingerprint(frame)
    state.source_schema = DataEngineeringService().schema(frame, primary_key_columns=[])
    db.commit()
    return {**imported, "mode": mode, "source": source_metadata, "contract_validation": contract_result, "last_watermark": state.last_watermark, "ingestion_state_id": state.id}


def _load_state(db, actor, connection_ref: str, source_object: str) -> IngestionState | None:
    return db.query(IngestionState).filter(
        IngestionState.tenant_id == actor.tenant_id,
        IngestionState.connection_ref == connection_ref,
        IngestionState.source_object == source_object,
    ).first()


@router.post("/import/database", status_code=201)
def import_database(payload: dict[str, Any], request: Request):
    actor = _actor(request)
    connection_ref = str(payload.get("connection_ref") or "").strip()
    query = str(payload.get("query") or "").strip()
    mode = str(payload.get("mode", "full")).casefold()
    if mode not in {"full", "incremental"}:
        raise AppError("INVALID_INGESTION_MODE", "mode must be full or incremental.", status_code=422)
    if not connection_ref or not query:
        raise AppError("INGESTION_FIELDS_REQUIRED", "connection_ref and query are required.", status_code=422)
    url = _connection_url(connection_ref)
    source_object = _source_object(payload, kind="database")
    db = request.app.state.SessionLocal()
    created_storage_key = None
    try:
        state = _load_state(db, actor, connection_ref, source_object)
        result = ConnectorService(timeout_seconds=request.app.state.settings.request_timeout_seconds).read_database(
            connection_url=url,
            query=query,
            parameters=payload.get("parameters") or {},
            max_rows=max(100, min(int(payload.get("max_rows", 100_000)), 250_000)),
        )
        response = _persist_connector_result(
            db=db,
            request=request,
            actor=actor,
            frame=result.frame,
            kind="database",
            connection_ref=connection_ref,
            source_object=source_object,
            mode=mode,
            watermark_column=payload.get("watermark_column"),
            state=state,
            source_metadata=result.source,
        )
        created_storage_key = response.get("dataset_id")
        return response
    except ConnectorError as exc:
        db.rollback()
        raise AppError(exc.code, exc.message, status_code=422, details=exc.details) from exc
    except AppError:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise AppError("DATABASE_INGEST_FAILED", "Database ingestion failed.", status_code=422, details={"error": str(exc)}) from exc
    finally:
        db.close()


@router.post("/import/rest", status_code=201)
def import_rest(payload: dict[str, Any], request: Request):
    actor = _actor(request)
    connection_ref = str(payload.get("connection_ref") or "").strip()
    mode = str(payload.get("mode", "full")).casefold()
    if mode not in {"full", "incremental"}:
        raise AppError("INVALID_INGESTION_MODE", "mode must be full or incremental.", status_code=422)
    if not connection_ref or not payload.get("url"):
        raise AppError("INGESTION_FIELDS_REQUIRED", "connection_ref and url are required.", status_code=422)
    source_object = _source_object(payload, kind="rest_json")
    db = request.app.state.SessionLocal()
    try:
        state = _load_state(db, actor, connection_ref, source_object)
        result = ConnectorService(timeout_seconds=request.app.state.settings.request_timeout_seconds).read_rest(
            url=str(payload["url"]),
            data_path=payload.get("data_path"),
            headers=payload.get("headers") or {},
            page_param=str(payload.get("page_param", "page")),
            page_size_param=str(payload.get("page_size_param", "page_size")),
            page_size=max(1, min(int(payload.get("page_size", 100)), 1000)),
            max_pages=max(1, min(int(payload.get("max_pages", 100)), 1000)),
            max_rows=max(100, min(int(payload.get("max_rows", 100_000)), 250_000)),
        )
        return _persist_connector_result(
            db=db,
            request=request,
            actor=actor,
            frame=result.frame,
            kind="rest_json",
            connection_ref=connection_ref,
            source_object=source_object,
            mode=mode,
            watermark_column=payload.get("watermark_column"),
            state=state,
            source_metadata=result.source,
        )
    except ConnectorError as exc:
        db.rollback()
        raise AppError(exc.code, exc.message, status_code=422, details=exc.details) from exc
    except AppError:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise AppError("REST_INGEST_FAILED", "REST ingestion failed.", status_code=422, details={"error": str(exc)}) from exc
    finally:
        db.close()


@router.get("/{dataset_id}/ingestion/state")
def ingestion_state(dataset_id: str, request: Request):
    actor = _actor(request)
    db = request.app.state.SessionLocal()
    try:
        assert_dataset_tenant(db, dataset_id, actor)
        rows = db.query(IngestionState).filter(IngestionState.dataset_id == dataset_id, IngestionState.tenant_id == actor.tenant_id).all()
        return {"dataset_id": dataset_id, "states": [{"id": row.id, "connection_ref": row.connection_ref, "source_object": row.source_object, "last_watermark": row.last_watermark, "last_version_id": row.last_version_id, "last_fingerprint": row.last_fingerprint, "source_schema": row.source_schema, "updated_at": row.updated_at} for row in rows]}
    finally:
        db.close()
