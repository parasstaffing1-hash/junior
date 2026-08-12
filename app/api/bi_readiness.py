"""BI-ready data preparation endpoints."""

from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import APIRouter, Request

from app.core.bi.readiness import apply_bi_readiness, build_bi_readiness_profile
from app.core.bi.dax import DAXAnalysisError, analyze_dax_expression, analyze_dax_measures
from app.core.bi.power_query import PowerQueryError, analyze_power_query, analyze_power_query_batch
from app.core.bi.semantic_model import SemanticModelError, build_semantic_model_contract
from app.core.intelligence.common import IntelligenceError, json_safe
from app.models.all import Dataset, DatasetVersion
from app.orchestration.platform import _create_version


router = APIRouter(prefix="/api/v1", tags=["bi-readiness"])


def _load_dataset(request: Request, dataset_id: str, version_id: str | None = None):
    db = request.app.state.SessionLocal()
    try:
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        if dataset is None:
            raise IntelligenceError("DATASET_NOT_FOUND", "Dataset was not found.", {"dataset_id": dataset_id}, status_code=404)
        selected_id = version_id or dataset.current_version_id
        version = db.query(DatasetVersion).filter(DatasetVersion.id == selected_id).first()
        if version is None:
            raise IntelligenceError("VERSION_NOT_FOUND", "Dataset version was not found.", {"source_version_id": selected_id}, status_code=404)
        if version.dataset_id != dataset.id:
            raise IntelligenceError("LINEAGE_MISMATCH", "source_version_id does not belong to dataset_id.")
        try:
            frame = pd.read_csv(request.app.state.storage.resolve(version.storage_path))
        except pd.errors.EmptyDataError:
            frame = pd.DataFrame()
        return db, dataset, version, frame
    except Exception:
        db.close()
        raise


def _public(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key != "dataframe"}


@router.get("/bi-readiness/catalog")
def bi_readiness_catalog():
    return {
        "capability": "BI-ready data preparation",
        "mode": "approval_first",
        "workflow": ["profile", "preview", "apply_to_new_immutable_version", "validate_model_contract"],
        "outputs": ["clean BI-friendly fields", "FactData grain contract", "date and dimension recommendations", "explicit measure recommendations", "advanced semantic-model contract", "lineage and validation warnings"],
        "safety": {"source_overwrite": False, "silent_duplicate_removal": False, "business_semantics_invented": False, "security_publish_gate": True},
    }


@router.get("/bi-readiness/semantic-model/catalog")
def semantic_model_catalog():
    return {
        "capability": "Advanced semantic-model design",
        "patterns": ["star_schema", "snowflake_schema", "explicit_fact_grain", "surrogate_keys", "scd_type_1", "scd_type_2", "role_playing_dates", "bridge_tables", "many_to_many", "degenerate_dimensions", "shared_models", "composite_models", "import", "direct_query", "direct_lake", "aggregations", "perspectives", "field_parameters"],
        "workflow": ["declare_source_columns", "declare_grain", "design_facts_dimensions", "validate_relationships", "review_measures", "approve_and_publish_in_target_engine"],
        "release_policy": "The local validator can prove structural consistency; business meaning, security and target-engine performance still require approval evidence.",
    }


@router.post("/bi-readiness/dax/analyze")
def analyze_dax(payload: dict[str, Any]):
    try:
        if payload.get("measures") is not None:
            return analyze_dax_measures(payload.get("measures") or [])
        return analyze_dax_expression(payload.get("expression"), context=str(payload.get("context", "measure")))
    except DAXAnalysisError as exc:
        raise IntelligenceError(exc.code, exc.message, exc.details, status_code=422) from exc


@router.post("/bi-readiness/power-query/analyze")
def analyze_power_query_route(payload: dict[str, Any]):
    try:
        if payload.get("queries") is not None:
            return analyze_power_query_batch(payload.get("queries") or [])
        return analyze_power_query(payload.get("expression"), incremental_refresh=bool(payload.get("incremental_refresh", False)))
    except PowerQueryError as exc:
        raise IntelligenceError(exc.code, exc.message, exc.details, status_code=422) from exc


def _semantic_model_or_error(columns: list[str], design: dict[str, Any] | None) -> dict[str, Any]:
    try:
        return build_semantic_model_contract(columns, design)
    except SemanticModelError as exc:
        raise IntelligenceError(exc.code, exc.message, exc.details, status_code=422) from exc


@router.post("/bi-readiness/semantic-model/validate")
def validate_semantic_model(payload: dict[str, Any]):
    columns = payload.get("columns") or []
    return _semantic_model_or_error(columns, payload.get("design") or payload)


@router.post("/datasets/{dataset_id}/bi-readiness/semantic-model")
def design_dataset_semantic_model(dataset_id: str, request: Request, payload: dict[str, Any] | None = None):
    db, dataset, version, frame = _load_dataset(request, dataset_id, (payload or {}).get("version_id"))
    try:
        profile = build_bi_readiness_profile(frame, dataset_id=dataset.id, source_version_id=version.id)
        incoming = dict((payload or {}).get("design") or {})
        incoming.setdefault("grain", "one row per BI-ready source record")
        incoming.setdefault("grain_columns", profile.get("identifier_columns")[:1] or profile.get("measure_columns")[:1] or list(frame.columns)[:1])
        incoming.setdefault("fact_columns", list(profile.get("column_mapping", {}).values()))
        incoming.setdefault("dimension_columns", list(profile.get("dimension_columns", [])))
        incoming.setdefault("measure_columns", list(profile.get("measure_columns", [])))
        if profile.get("date_columns") and not incoming.get("date_column") and not incoming.get("date_dimensions"):
            incoming["date_column"] = profile["date_columns"][0]
        contract = _semantic_model_or_error(list(profile.get("column_mapping", {}).values()), incoming)
        return {"dataset_id": dataset.id, "source_version_id": version.id, "profile": profile, "model_contract": contract, "publication": {"approved": False, "external_target": None, "message": "Review and approve the semantic model before exporting or publishing."}}
    finally:
        db.close()


@router.post("/datasets/{dataset_id}/bi-readiness/profile")
def profile_bi_readiness(dataset_id: str, request: Request, payload: dict[str, Any] | None = None):
    db, dataset, version, frame = _load_dataset(request, dataset_id, (payload or {}).get("version_id"))
    try:
        profile = build_bi_readiness_profile(frame, dataset_id=dataset.id, source_version_id=version.id)
        return profile
    finally:
        db.close()


@router.post("/datasets/{dataset_id}/bi-readiness/preview")
def preview_bi_readiness(dataset_id: str, request: Request, payload: dict[str, Any] | None = None):
    db, dataset, version, frame = _load_dataset(request, dataset_id, (payload or {}).get("version_id"))
    try:
        profile = build_bi_readiness_profile(frame, dataset_id=dataset.id, source_version_id=version.id)
        result = apply_bi_readiness(frame, profile, options=(payload or {}).get("options"))
        return {"dataset_id": dataset.id, "input_version_id": version.id, "profile": result["profile"], **_public(result), "version_created": False, "message": "Preview only. No source or dataset version was changed."}
    finally:
        db.close()


@router.post("/datasets/{dataset_id}/bi-readiness/apply")
def apply_bi_readiness_version(dataset_id: str, request: Request, payload: dict[str, Any] | None = None):
    db, dataset, version, frame = _load_dataset(request, dataset_id, (payload or {}).get("version_id"))
    try:
        profile = build_bi_readiness_profile(frame, dataset_id=dataset.id, source_version_id=version.id)
        result = apply_bi_readiness(frame, profile, options=(payload or {}).get("options"))
        output_version, version_result = _create_version(
            db,
            request.app.state.storage,
            dataset,
            version,
            result["dataframe"],
            pipeline_type="BIReadinessPreparation",
            parameters={"selected_actions": result["execution"]["selected_actions"], "model_contract": result["model_contract"]},
            before_stats={"row_count": len(frame), "column_count": len(frame.columns), "readiness_score": profile["readiness_score"]},
        )
        return {"dataset_id": dataset.id, "input_version_id": version.id, "output_version_id": output_version.id, "version_created": True, **version_result, "profile": result["profile"], **_public(result), "lineage": {"parent_version_id": version.id, "output_version_id": output_version.id, "pipeline_type": "BIReadinessPreparation"}, "message": "BI-ready data was saved as a new immutable dataset version. Review the model contract before publishing."}
    finally:
        db.close()


__all__ = ["router"]
