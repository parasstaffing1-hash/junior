from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Request
from sqlalchemy.orm import Session

from app.core.data_engineering import DataEngineeringService
from app.core.forecasting import ForecastingService
from app.core.intelligence.common import ExecutionBudget, IntelligenceError, json_safe
from app.core.ml.diagnostics import DIAGNOSTIC_OPERATIONS, ModelDiagnosticsService
from app.core.ml.monitoring import MONITORING_OPERATIONS, MonitoringService
from app.core.ml.service import MachineLearningService, TrainingOutcome
from app.core.ml.unsupervised import UnsupervisedLearningService, UnsupervisedOutcome
from app.core.mlops import MLOpsService
from app.core.jobs.queue import DurableJobQueue
from app.core.security import Actor
from app.core.security import assert_dataset_tenant, authorize
from app.core.security_policy import apply_row_policies, visible_columns
from app.core.feature_store import materialize_features
from app.core.incident import derive_incident_signals
from app.models.all import (
    AnalysisRun,
    ApprovalRecord,
    Artifact,
    AutomationRun,
    Dataset,
    DatasetVersion,
    Experiment,
    ExperimentRun,
    ModelVersion,
    MonitoringPolicy,
    MonitoringRun,
    RegisteredModel,
    SecurityPolicy,
    WorkspaceAsset,
)
from app.orchestration.intelligence import IntelligenceOrchestrator
from app.services.model_registry import load_trusted_model, public_model, register_training_outcome, transition_model_version
from app.storage.dataset_storage import DatasetStorage


router = APIRouter(prefix="/api/v1", tags=["data-intelligence"])

FORECAST_TOOL_IDS = {
    "trend_seasonality": 101, "decomposition": 102, "stationarity": 103, "transform": 104, "acf_pacf": 105,
    "exponential_smoothing": 106, "arima": 107, "sarima": 108, "evaluate": 109, "split": 110,
    "backtest": 111, "compare": 112, "prediction_intervals": 113, "auto_select": 114, "scenario": 115,
    "accuracy_monitoring": 116, "bias_drift": 117, "reconciliation": 118, "ensemble": 119, "intermittent": 120,
    "exogenous": 121, "calendar_effects": 122, "probabilistic_calibration": 123, "multi_series": 124,
    "horizon_frequency": 125, "override_consensus": 126, "planning_table": 127, "readiness": 128,
    "pipeline": 129, "forecast": 129, "portfolio_summary": 130,
}
MONITOR_TOOL_IDS = {name: 254 + index for index, name in enumerate(sorted(MONITORING_OPERATIONS))}
DIAGNOSTIC_TOOL_IDS = {name: 244 + index for index, name in enumerate(sorted(DIAGNOSTIC_OPERATIONS))}
DATA_ENGINEERING_TOOL_IDS = {
    "source_readiness": 284, "contract_schema_evolution": 285, "incremental_cdc": 286, "dependency_dag": 287,
    "partition_storage": 288, "backfill_replay": 289, "freshness_sla": 290,
    "observability_failure_diagnosis": 291, "cost_optimization": 292, "orchestration": 293,
}
ORCHESTRATION_TOOL_IDS = {
    "data_science_plan": 294, "data_engineering_plan": 295, "feature_store": 296, "data_product": 297,
    "incident_root_cause": 298, "unified_pipeline": 299, "autonomous": 300,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _services(payload: dict[str, Any] | None = None):
    payload = payload or {}
    budget = ExecutionBudget(
        max_rows=int(payload.get("max_rows", 20_000)),
        max_features=int(payload.get("max_features", 100)),
        max_trials=int(payload.get("max_trials", 20)),
        timeout_seconds=int(payload.get("timeout_seconds", 120)),
        random_state=int(payload.get("random_state", 42)),
    )
    return {
        "forecasting": ForecastingService(budget),
        "ml": MachineLearningService(budget),
        "unsupervised": UnsupervisedLearningService(budget),
        "diagnostics": ModelDiagnosticsService(budget),
        "monitoring": MonitoringService(budget),
        "data_engineering": DataEngineeringService(budget),
        "orchestration": IntelligenceOrchestrator(budget),
        "mlops": MLOpsService(),
    }


def _load_dataset(
    db: Session,
    storage: DatasetStorage,
    dataset_id: str,
    source_version_id: str | None = None,
    expected_tenant_id: str | None = None,
) -> tuple[Dataset, DatasetVersion, pd.DataFrame]:
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if dataset is None:
        raise IntelligenceError("DATASET_NOT_FOUND", "Dataset was not found.", {"dataset_id": dataset_id}, status_code=404)
    if expected_tenant_id and dataset.tenant_id != expected_tenant_id:
        raise IntelligenceError("TENANT_FORBIDDEN", "The dataset belongs to another tenant.", {"dataset_id": dataset_id}, status_code=403)
    version_id = source_version_id or dataset.current_version_id
    version = db.query(DatasetVersion).filter(DatasetVersion.id == version_id).first()
    if version is None:
        raise IntelligenceError("VERSION_NOT_FOUND", "Dataset version was not found.", {"source_version_id": version_id}, status_code=404)
    if version.dataset_id != dataset.id:
        raise IntelligenceError("LINEAGE_MISMATCH", "source_version_id does not belong to dataset_id.")
    path = storage.resolve(version.storage_path)
    try:
        frame = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        frame = pd.DataFrame()
    policy_tenant = expected_tenant_id or dataset.tenant_id
    policy_actor = Actor("dataset-policy-engine", policy_tenant, frozenset({"owner", "admin"}), frozenset({"*"}), frozenset({"*"}), "policy_engine")
    policies = db.query(SecurityPolicy).filter(SecurityPolicy.tenant_id == policy_tenant, SecurityPolicy.workspace_id == "default").all()
    frame, _ = apply_row_policies(frame, policies, policy_actor)
    frame, _ = visible_columns(frame, policies)
    return dataset, version, frame


def _analysis(
    db: Session,
    *,
    dataset: Dataset,
    version: DatasetVersion,
    engine: str,
    internal_tool_id: int,
    parameters: dict[str, Any],
    result: dict[str, Any],
    warnings: list[Any] | None = None,
) -> AnalysisRun:
    run = AnalysisRun(
        dataset_id=dataset.id,
        source_version_id=version.id,
        engine=engine,
        tool_number=internal_tool_id,
        parameters=json_safe(parameters),
        result=json_safe(result),
        warnings=json_safe(warnings or []),
        status="COMPLETED",
        created_at=utc_now(),
    )
    db.add(run)
    db.flush()
    return run


def _store_frame_artifact(
    db: Session,
    storage: DatasetStorage,
    *,
    dataset: Dataset,
    version: DatasetVersion,
    frame: pd.DataFrame,
    artifact_type: str,
    metadata: dict[str, Any],
) -> Artifact:
    artifact_id = str(uuid4())
    storage_key = f"{dataset.id}/intelligence/{artifact_id}.csv"
    destination = storage.resolve(storage_key)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        frame.to_csv(temporary, index=False)
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    artifact = Artifact(
        id=artifact_id,
        dataset_id=dataset.id,
        source_version_id=version.id,
        artifact_type=artifact_type,
        storage_path=storage_key,
        sha256=digest,
        size=destination.stat().st_size,
        metadata_json=json_safe(metadata),
        created_at=utc_now(),
    )
    db.add(artifact)
    db.flush()
    return artifact


def _model_response(model: RegisteredModel, version: ModelVersion, artifact: Artifact) -> dict[str, Any]:
    return {
        "model_id": model.id,
        "model_version_id": version.id,
        "version": version.version,
        "status": version.status,
        "artifact_id": artifact.id,
        "artifact_sha256": artifact.sha256,
        "training_dataset_id": version.training_dataset_id,
        "training_source_version_id": version.training_source_version_id,
    }


def _persist_training(
    db: Session,
    storage: DatasetStorage,
    dataset: Dataset,
    version: DatasetVersion,
    outcome: TrainingOutcome,
    payload: dict[str, Any],
) -> dict[str, Any]:
    model, model_version, artifact = register_training_outcome(
        db,
        storage,
        dataset=dataset,
        source_version=version,
        outcome=outcome,
        model_name=str(payload.get("model_name") or f"{dataset.name} {outcome.result['target_columns'][0]} model"),
        workspace_id=str(payload.get("workspace_id", "default")),
        owner=payload.get("owner"),
        description=payload.get("description"),
    )
    return _model_response(model, model_version, artifact)


def _compatible_frame(version: ModelVersion, frame: pd.DataFrame) -> dict[str, Any]:
    expected = [str(column) for column in (version.feature_specification or {}).get("features", [])]
    missing = [column for column in expected if column not in frame.columns]
    if missing:
        raise IntelligenceError("MODEL_INPUT_INCOMPATIBLE", "Dataset is missing trained model features.", {"missing_features": missing})
    return {"compatible": True, "expected_features": expected, "extra_columns": [str(column) for column in frame.columns if str(column) not in expected]}


def _public_artifact(artifact: Artifact) -> dict[str, Any]:
    return {"id": artifact.id, "type": artifact.artifact_type, "sha256": artifact.sha256, "size": artifact.size, "source_version_id": artifact.source_version_id}


@router.get("/intelligence/capabilities")
def intelligence_capabilities():
    services = _services()
    return {
        "platform": "Unified Data Intelligence OS",
        "integrated_reference_capabilities": 200,
        "production_ui_uses_logical_capability_names": True,
        "forecasting": {"operation_count": len(FORECAST_TOOL_IDS), "operations": sorted(FORECAST_TOOL_IDS)},
        "supervised_ml": services["ml"].catalog(),
        "unsupervised_ml": services["unsupervised"].catalog(),
        "diagnostics": sorted(DIAGNOSTIC_OPERATIONS),
        "monitoring": sorted(MONITORING_OPERATIONS),
        "mlops": services["mlops"].catalog(),
        "data_engineering": services["data_engineering"].catalog(),
        "orchestration": services["orchestration"].catalog(),
        "safety": {"bounded_execution": True, "dataset_version_lineage": True, "verified_model_artifacts_only": True, "automatic_production_deployment": False},
    }


@router.post("/datasets/{dataset_id}/forecasting/{operation}")
def run_forecasting(dataset_id: str, operation: str, request: Request, payload: dict[str, Any] | None = None):
    payload = payload or {}
    db = request.app.state.SessionLocal()
    try:
        dataset, version, frame = _load_dataset(db, request.app.state.storage, dataset_id, payload.get("source_version_id"))
        result = _services(payload)["forecasting"].run(frame, operation, **payload)
        run = _analysis(db, dataset=dataset, version=version, engine=f"ForecastingService.{operation}", internal_tool_id=FORECAST_TOOL_IDS.get(operation, 129), parameters=payload, result=result)
        db.commit()
        return {**result, "analysis_run_id": run.id, "dataset_id": dataset.id, "source_version_id": version.id}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/datasets/{dataset_id}/forecast")
def forecast_dataset(dataset_id: str, request: Request, payload: dict[str, Any] | None = None):
    return run_forecasting(dataset_id, "pipeline", request, payload)


@router.post("/datasets/{dataset_id}/ml/readiness")
def ml_readiness(dataset_id: str, request: Request, payload: dict[str, Any]):
    db = request.app.state.SessionLocal()
    try:
        dataset, version, frame = _load_dataset(db, request.app.state.storage, dataset_id, payload.get("source_version_id"))
        result = _services(payload)["ml"].readiness(frame, **payload)
        run = _analysis(db, dataset=dataset, version=version, engine="MLReadinessService", internal_tool_id=131, parameters=payload, result=result, warnings=result.get("warnings"))
        db.commit()
        return {**result, "analysis_run_id": run.id, "dataset_id": dataset.id, "source_version_id": version.id}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/datasets/{dataset_id}/ml/train")
def ml_train(dataset_id: str, request: Request, payload: dict[str, Any]):
    db = request.app.state.SessionLocal()
    try:
        dataset, version, frame = _load_dataset(db, request.app.state.storage, dataset_id, payload.get("source_version_id"))
        outcome = _services(payload)["ml"].train(frame, **payload)
        model_record = _persist_training(db, request.app.state.storage, dataset, version, outcome, payload)
        result = {**outcome.result, "registered_model": model_record}
        run = _analysis(db, dataset=dataset, version=version, engine="ModelTrainingService", internal_tool_id=134 if outcome.result["task_type"] == "classification" else 133, parameters=payload, result=result, warnings=outcome.result.get("warnings"))
        db.commit()
        return {**result, "analysis_run_id": run.id, "dataset_id": dataset.id, "source_version_id": version.id}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/datasets/{dataset_id}/ml/compare")
def ml_compare(dataset_id: str, request: Request, payload: dict[str, Any]):
    db = request.app.state.SessionLocal()
    try:
        dataset, version, frame = _load_dataset(db, request.app.state.storage, dataset_id, payload.get("source_version_id"))
        result, champion = _services(payload)["ml"].compare(frame, **payload)
        model_record = _persist_training(db, request.app.state.storage, dataset, version, champion, payload)
        result["registered_champion"] = model_record
        run = _analysis(db, dataset=dataset, version=version, engine="ModelSelectionService", internal_tool_id=135, parameters=payload, result=result)
        db.commit()
        return {**result, "analysis_run_id": run.id, "dataset_id": dataset.id, "source_version_id": version.id}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/datasets/{dataset_id}/ml/tune")
def ml_tune(dataset_id: str, request: Request, payload: dict[str, Any]):
    db = request.app.state.SessionLocal()
    try:
        dataset, version, frame = _load_dataset(db, request.app.state.storage, dataset_id, payload.get("source_version_id"))
        result, champion = _services(payload)["ml"].tune(frame, **payload)
        model_record = _persist_training(db, request.app.state.storage, dataset, version, champion, payload)
        result["registered_best_model"] = model_record
        run = _analysis(db, dataset=dataset, version=version, engine="HyperparameterTuningService", internal_tool_id=136, parameters=payload, result=result)
        db.commit()
        return {**result, "analysis_run_id": run.id, "dataset_id": dataset.id, "source_version_id": version.id}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/datasets/{dataset_id}/ml/feature-selection")
def ml_feature_selection(dataset_id: str, request: Request, payload: dict[str, Any]):
    db = request.app.state.SessionLocal()
    try:
        dataset, version, frame = _load_dataset(db, request.app.state.storage, dataset_id, payload.get("source_version_id"))
        result = _services(payload)["ml"].feature_selection(frame, **payload)
        run = _analysis(db, dataset=dataset, version=version, engine="FeatureSelectionService", internal_tool_id=137, parameters=payload, result=result, warnings=result.get("warnings"))
        db.commit()
        return {**result, "analysis_run_id": run.id, "dataset_id": dataset.id, "source_version_id": version.id}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/datasets/{dataset_id}/ml/unsupervised/{operation}")
def ml_unsupervised(dataset_id: str, operation: str, request: Request, payload: dict[str, Any]):
    db = request.app.state.SessionLocal()
    try:
        dataset, version, frame = _load_dataset(db, request.app.state.storage, dataset_id, payload.get("source_version_id"))
        service = _services(payload)["unsupervised"]
        artifact = None
        if operation == "compare":
            result = service.compare(frame, **payload)
            internal_tool = 153
        elif operation == "stability":
            result = service.stability(frame, **payload)
            internal_tool = 158
        elif operation == "run":
            outcome: UnsupervisedOutcome = service.run(frame, **payload)
            artifact = _store_frame_artifact(db, request.app.state.storage, dataset=dataset, version=version, frame=outcome.artifact_frame, artifact_type="unsupervised_output", metadata={"algorithm": outcome.result["algorithm"]})
            result = {**outcome.result, "artifact": _public_artifact(artifact)}
            internal_tool = 148
        else:
            raise IntelligenceError("UNKNOWN_UNSUPERVISED_OPERATION", "operation must be run, compare, or stability.")
        run = _analysis(db, dataset=dataset, version=version, engine=f"UnsupervisedLearningService.{operation}", internal_tool_id=internal_tool, parameters=payload, result=result)
        db.commit()
        return {**result, "analysis_run_id": run.id, "dataset_id": dataset.id, "source_version_id": version.id}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("/models")
def list_models(request: Request, workspace_id: str = "default"):
    actor = _model_actor(request, "read")
    db = request.app.state.SessionLocal()
    try:
        models = db.query(RegisteredModel).filter(RegisteredModel.tenant_id == actor.tenant_id, RegisteredModel.workspace_id == workspace_id).order_by(RegisteredModel.updated_at.desc()).all()
        return {"workspace_id": workspace_id, "models": [public_model(model) for model in models]}
    finally:
        db.close()


@router.get("/models/{model_id}")
def get_model(model_id: str, request: Request):
    actor = _model_actor(request, "read")
    db = request.app.state.SessionLocal()
    try:
        model = db.query(RegisteredModel).filter(RegisteredModel.id == model_id).first()
        if model is None:
            raise IntelligenceError("MODEL_NOT_FOUND", "Registered model was not found.", status_code=404)
        _model_visible_to_tenant(db, model, actor)
        return public_model(model)
    finally:
        db.close()


def _model_actor(request: Request, permission: str = "analyze") -> Actor:
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise IntelligenceError("AUTHENTICATION_REQUIRED", "A security principal is required.", status_code=401)
    authorize(actor, permission)
    return actor


def _model_visible_to_tenant(db: Session, model: RegisteredModel, actor: Actor) -> None:
    dataset_ids = {version.training_dataset_id for version in model.versions if version.training_dataset_id}
    if model.tenant_id != actor.tenant_id or not dataset_ids or not db.query(Dataset.id).filter(Dataset.id.in_(dataset_ids), Dataset.tenant_id == actor.tenant_id).first():
        raise IntelligenceError("TENANT_FORBIDDEN", "The model is not available to this tenant.", status_code=403)


def _model_dataset(db: Session, storage: DatasetStorage, model_version_id: str, payload: dict[str, Any], actor: Actor | None = None):
    model, version, artifact = load_trusted_model(db, storage, model_version_id)
    registered_model = db.query(RegisteredModel).filter(RegisteredModel.id == version.model_id).first()
    if registered_model is None:
        raise IntelligenceError("MODEL_NOT_FOUND", "The registered model was not found.", status_code=404)
    if actor is not None:
        _model_visible_to_tenant(db, registered_model, actor)
    dataset_id = str(payload.get("dataset_id") or version.training_dataset_id)
    source_version_id = payload.get("source_version_id") or (version.training_source_version_id if dataset_id == version.training_dataset_id else None)
    dataset, source_version, frame = _load_dataset(db, storage, dataset_id, source_version_id, expected_tenant_id=actor.tenant_id if actor else None)
    compatibility = _compatible_frame(version, frame)
    return model, version, artifact, dataset, source_version, frame, compatibility


@router.post("/models/{model_version_id}/predict")
def predict_model(model_version_id: str, request: Request, payload: dict[str, Any]):
    actor = _model_actor(request)
    db = request.app.state.SessionLocal()
    try:
        model, model_version, artifact = load_trusted_model(db, request.app.state.storage, model_version_id)
        if model_version.status not in {"APPROVED", "CHAMPION"}:
            raise IntelligenceError("MODEL_NOT_APPROVED", "Only APPROVED or CHAMPION models can serve predictions.", {"status": model_version.status}, status_code=403)
        training_dataset = assert_dataset_tenant(db, model_version.training_dataset_id, actor)
        batch = False
        source_version = None
        if payload.get("dataset_id"):
            dataset_id = str(payload["dataset_id"])
            assert_dataset_tenant(db, dataset_id, actor)
            model, model_version, artifact, dataset, source_version, frame, compatibility = _model_dataset(db, request.app.state.storage, model_version_id, {"dataset_id": dataset_id, "source_version_id": payload.get("source_version_id")}, actor)
            batch = True
        else:
            records = payload.get("records")
            if isinstance(records, dict):
                records = [records]
            if not isinstance(records, list) or not records:
                raise IntelligenceError("PREDICTION_INPUT_REQUIRED", "Provide records or dataset_id.", status_code=422)
            frame = pd.DataFrame(records)
            dataset = training_dataset
            source_version = db.query(DatasetVersion).filter(DatasetVersion.id == model_version.training_source_version_id).first()
            compatibility = _compatible_frame(model_version, frame)
        features = [str(column) for column in (model_version.feature_specification or {}).get("features", [])]
        predicted = model.predict(frame[features])
        output = frame.copy()
        output["prediction"] = predicted
        if bool(payload.get("include_probabilities", False)) and hasattr(model, "predict_proba"):
            probabilities = model.predict_proba(frame[features])
            classes = list(model.named_steps["model"].classes_) if hasattr(model, "named_steps") and hasattr(model.named_steps.get("model"), "classes_") else list(range(probabilities.shape[1]))
            for index, label in enumerate(classes):
                output[f"probability_{label}"] = probabilities[:, index]
        result = {"model_version_id": model_version.id, "model_status": model_version.status, "model_artifact_sha256": artifact.sha256, "training_dataset_id": model_version.training_dataset_id, "rows_scored": int(len(output)), "predictions": json_safe(output.astype(object).where(pd.notna(output), None).to_dict(orient="records")[:5000]), "truncated": len(output) > 5000, "compatibility": compatibility, "batch": batch}
        if batch and source_version is not None:
            prediction_artifact = _store_frame_artifact(db, request.app.state.storage, dataset=dataset, version=source_version, frame=output, artifact_type="model_predictions", metadata={"model_version_id": model_version.id, "source_version_id": source_version.id, "rows_scored": len(output)})
            result["artifact"] = _public_artifact(prediction_artifact)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/models/{model_version_id}/diagnostics/{operation}")
def model_diagnostics(model_version_id: str, operation: str, request: Request, payload: dict[str, Any]):
    actor = _model_actor(request)
    db = request.app.state.SessionLocal()
    try:
        model, model_version, _, dataset, version, frame, compatibility = _model_dataset(db, request.app.state.storage, model_version_id, payload, actor)
        target = payload.get("target_column") or (model_version.feature_specification or {}).get("targets", [None])[0]
        parameters = {**payload, "target_column": target, "feature_columns": (model_version.feature_specification or {}).get("features"), "task_type": db.query(RegisteredModel).filter(RegisteredModel.id == model_version.model_id).first().task_type}
        result = _services(payload)["diagnostics"].run(model, frame, operation, **parameters)
        result["compatibility"] = compatibility
        run = _analysis(db, dataset=dataset, version=version, engine=f"ModelDiagnosticsService.{operation}", internal_tool_id=DIAGNOSTIC_TOOL_IDS.get(operation, 244), parameters=parameters, result=result)
        db.commit()
        return {**result, "analysis_run_id": run.id, "model_version_id": model_version.id, "dataset_id": dataset.id, "source_version_id": version.id}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/models/{model_version_id}/explain")
def explain_model(model_version_id: str, request: Request, payload: dict[str, Any]):
    actor = _model_actor(request)
    db = request.app.state.SessionLocal()
    try:
        model, model_version, _, dataset, version, frame, compatibility = _model_dataset(db, request.app.state.storage, model_version_id, payload, actor)
        parameters = {**payload, "target_column": payload.get("target_column") or model_version.feature_specification["targets"][0], "feature_columns": model_version.feature_specification["features"]}
        result = _services(payload)["ml"].explain(model, frame, **parameters)
        result["compatibility"] = compatibility
        run = _analysis(db, dataset=dataset, version=version, engine="ExplainabilityService", internal_tool_id=138, parameters=parameters, result=result)
        db.commit()
        return {**result, "analysis_run_id": run.id, "model_version_id": model_version.id}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/models/{model_version_id}/thresholds")
def model_thresholds(model_version_id: str, request: Request, payload: dict[str, Any]):
    actor = _model_actor(request)
    db = request.app.state.SessionLocal()
    try:
        model, model_version, _, dataset, version, frame, _ = _model_dataset(db, request.app.state.storage, model_version_id, payload, actor)
        parameters = {**payload, "target_column": payload.get("target_column") or model_version.feature_specification["targets"][0], "feature_columns": model_version.feature_specification["features"], "task_type": "classification"}
        result = _services(payload)["ml"].threshold_analysis(model, frame, **parameters)
        run = _analysis(db, dataset=dataset, version=version, engine="ThresholdOptimizationService", internal_tool_id=140, parameters=parameters, result=result)
        db.commit()
        return {**result, "analysis_run_id": run.id, "model_version_id": model_version.id}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/models/{model_version_id}/calibration")
def model_calibration(model_version_id: str, request: Request, payload: dict[str, Any]):
    actor = _model_actor(request)
    db = request.app.state.SessionLocal()
    try:
        model, model_version, _, dataset, version, frame, _ = _model_dataset(db, request.app.state.storage, model_version_id, payload, actor)
        parameters = {**payload, "target_column": payload.get("target_column") or model_version.feature_specification["targets"][0], "feature_columns": model_version.feature_specification["features"], "task_type": "classification"}
        result = _services(payload)["ml"].calibration_analysis(model, frame, **parameters)
        run = _analysis(db, dataset=dataset, version=version, engine="CalibrationService", internal_tool_id=141, parameters=parameters, result=result)
        db.commit()
        return {**result, "analysis_run_id": run.id, "model_version_id": model_version.id}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/models/{model_version_id}/monitor/{operation}")
def monitor_model(model_version_id: str, operation: str, request: Request, payload: dict[str, Any]):
    actor = _model_actor(request)
    db = request.app.state.SessionLocal()
    try:
        model, model_version, _, current_dataset, current_version, current, compatibility = _model_dataset(db, request.app.state.storage, model_version_id, payload, actor)
        _, _, reference = _load_dataset(db, request.app.state.storage, model_version.training_dataset_id, payload.get("reference_version_id") or model_version.training_source_version_id)
        challenger = None
        if payload.get("challenger_model_version_id"):
            challenger, _, _ = load_trusted_model(db, request.app.state.storage, str(payload["challenger_model_version_id"]))
        parameters = {**payload, "target_column": payload.get("target_column") or model_version.feature_specification["targets"][0], "feature_columns": model_version.feature_specification["features"], "task_type": db.query(RegisteredModel).filter(RegisteredModel.id == model_version.model_id).first().task_type}
        result = _services(payload)["monitoring"].run(reference, current, operation, model=model, challenger_model=challenger, **parameters)
        result["compatibility"] = compatibility
        policy = db.query(MonitoringPolicy).filter(MonitoringPolicy.model_id == model_version.model_id, MonitoringPolicy.name == "default").first()
        if policy is None:
            policy = MonitoringPolicy(
                model_id=model_version.model_id,
                name="default",
                definition={"operations": sorted(MONITORING_OPERATIONS), "schedule": "on_demand", "automatic_retraining": False},
                enabled=True,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            db.add(policy)
            db.flush()
        drifted = bool(result.get("drifted") or result.get("out_of_distribution") or result.get("drifted_feature_count", 0))
        monitoring_run = MonitoringRun(
            policy_id=policy.id,
            model_version_id=model_version.id,
            dataset_id=current_dataset.id,
            source_version_id=current_version.id,
            metrics=json_safe(result),
            events=[{"operation": operation, "status": "ALERT" if drifted else "HEALTHY"}],
            status="ALERT" if drifted else "COMPLETED",
            started_at=utc_now(),
            completed_at=utc_now(),
        )
        db.add(monitoring_run)
        db.flush()
        result["monitoring_policy_id"] = policy.id
        result["monitoring_run_id"] = monitoring_run.id
        run = _analysis(db, dataset=current_dataset, version=current_version, engine=f"MonitoringService.{operation}", internal_tool_id=MONITOR_TOOL_IDS.get(operation, 273), parameters=parameters, result=result)
        db.commit()
        return {**result, "analysis_run_id": run.id, "model_version_id": model_version.id, "dataset_id": current_dataset.id, "source_version_id": current_version.id}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/models/{model_version_id}/status")
def update_model_status(model_version_id: str, request: Request, payload: dict[str, Any]):
    actor = _model_actor(request, "deploy")
    db = request.app.state.SessionLocal()
    try:
        version = db.query(ModelVersion).filter(ModelVersion.id == model_version_id).first()
        if version is None:
            raise IntelligenceError("MODEL_VERSION_NOT_FOUND", "Model version was not found.", status_code=404)
        model = db.query(RegisteredModel).filter(RegisteredModel.id == version.model_id).first()
        if model is None:
            raise IntelligenceError("MODEL_NOT_FOUND", "Registered model was not found.", status_code=404)
        _model_visible_to_tenant(db, model, actor)
        requested = str(payload.get("status", "")).upper()
        evidence = payload.get("evidence") or {}
        approved_by = payload.get("approved_by")
        if requested in {"VALIDATED", "APPROVED", "CHAMPION"} and not evidence:
            raise IntelligenceError("APPROVAL_EVIDENCE_REQUIRED", "Validation or approval evidence is required for this transition.")
        if requested in {"APPROVED", "CHAMPION"} and not approved_by:
            raise IntelligenceError("HUMAN_APPROVER_REQUIRED", "approved_by is required for approval or champion promotion.")
        transition_model_version(version, requested)
        record = ApprovalRecord(model_version_id=version.id, requested_status=requested, decision="APPROVED", approved_by=approved_by, evidence=json_safe(evidence), created_at=utc_now())
        db.add(record)
        if requested == "CHAMPION":
            for sibling in model.versions:
                if sibling.id != version.id and sibling.status == "CHAMPION":
                    sibling.status = "ARCHIVED"
            model.status = "CHAMPION"
        db.commit()
        return {"model_version_id": version.id, "status": version.status, "approval_record_id": record.id, "approved_by": approved_by}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/models/{model_id}/rollback")
def rollback_model(model_id: str, request: Request, payload: dict[str, Any]):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise IntelligenceError("AUTHENTICATION_REQUIRED", "A security principal is required.", status_code=401)
    authorize(actor, "deploy")
    db = request.app.state.SessionLocal()
    try:
        model = db.query(RegisteredModel).filter(RegisteredModel.id == model_id).first()
        if model is None:
            raise IntelligenceError("MODEL_NOT_FOUND", "The registered model was not found.", status_code=404)
        _model_visible_to_tenant(db, model, actor)
        if not model.versions:
            raise IntelligenceError("MODEL_VERSION_NOT_FOUND", "The registered model has no versions.", status_code=404)
        dataset = db.query(Dataset).filter(Dataset.id == model.versions[0].training_dataset_id).first()
        if dataset is None or dataset.tenant_id != actor.tenant_id:
            raise IntelligenceError("TENANT_FORBIDDEN", "The model is not available to this tenant.", status_code=403)
        target_id = str(payload.get("target_model_version_id") or "")
        target_version_number = payload.get("target_version")
        target = next((version for version in model.versions if (target_id and version.id == target_id) or (target_version_number is not None and int(version.version) == int(target_version_number))), None)
        if target is None:
            raise IntelligenceError("ROLLBACK_TARGET_NOT_FOUND", "A valid target model version is required.", status_code=422)
        if target.status not in {"APPROVED", "ARCHIVED", "CHAMPION"}:
            raise IntelligenceError("ROLLBACK_TARGET_NOT_APPROVED", "Rollback target must be APPROVED, ARCHIVED, or CHAMPION.", {"status": target.status}, status_code=422)
        current = next((version for version in model.versions if version.status == "CHAMPION"), None)
        if current is not None and current.id != target.id:
            current.status = "ARCHIVED"
        target.status = "CHAMPION"
        model.status = "CHAMPION"
        record = ApprovalRecord(model_version_id=target.id, requested_status="CHAMPION", decision="ROLLBACK", approved_by=actor.principal_id, evidence=json_safe({"reason": payload.get("reason"), "source_model_id": model.id, "previous_champion_id": current.id if current else None, "target_model_version_id": target.id}), created_at=utc_now())
        db.add(record)
        db.commit()
        return {"model_id": model.id, "previous_champion_id": current.id if current else None, "target_model_version_id": target.id, "status": target.status, "approval_record_id": record.id, "decision": "ROLLBACK"}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/datasets/{dataset_id}/data-engineering/{operation}")
def run_data_engineering(dataset_id: str, operation: str, request: Request, payload: dict[str, Any] | None = None):
    payload = payload or {}
    db = request.app.state.SessionLocal()
    try:
        dataset, version, frame = _load_dataset(db, request.app.state.storage, dataset_id, payload.get("source_version_id"))
        result = _services(payload)["data_engineering"].run(frame, operation, **payload)
        run = _analysis(db, dataset=dataset, version=version, engine=f"DataEngineeringService.{operation}", internal_tool_id=DATA_ENGINEERING_TOOL_IDS.get(operation, 293), parameters=payload, result=result)
        db.commit()
        return {**result, "analysis_run_id": run.id, "dataset_id": dataset.id, "source_version_id": version.id}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/datasets/{dataset_id}/orchestration/{operation}")
def run_orchestration(dataset_id: str, operation: str, request: Request, payload: dict[str, Any] | None = None):
    payload = payload or {}
    db = request.app.state.SessionLocal()
    try:
        dataset, version, frame = _load_dataset(db, request.app.state.storage, dataset_id, payload.get("source_version_id"))
        orchestration_parameters = {key: value for key, value in payload.items() if key not in {"dataset_id", "source_version_id"}}
        if operation == "incident_root_cause" and not orchestration_parameters.get("incident_signals"):
            orchestration_parameters["incident_signals"] = derive_incident_signals(db, dataset_id=dataset.id)
        result = _services(payload)["orchestration"].run(frame, operation, dataset_id=dataset.id, source_version_id=version.id, **orchestration_parameters)
        materialized_version_id = None
        materialization_metadata = None
        if operation == "feature_store" and bool(payload.get("materialize", False)):
            key_columns = [str(column) for column in (payload.get("entity_columns") or [])]
            missing_keys = [column for column in key_columns if column not in frame.columns]
            if missing_keys:
                raise IntelligenceError("FEATURE_ENTITY_COLUMNS_MISSING", "entity_columns must exist in the source dataset.", {"missing": missing_keys})
            feature_frame, materialization_metadata = materialize_features(frame, result.get("features") or [])
            output_frame = pd.concat([frame[key_columns].reset_index(drop=True), feature_frame], axis=1) if key_columns else feature_frame
            imported = request.app.state.importer.import_dataframe(
                db,
                output_frame,
                name=f"{dataset.name}_features",
                source_type="feature_set",
                tenant_id=getattr(getattr(request.state, "actor", None), "tenant_id", dataset.tenant_id),
                dataset_id=dataset.id,
                parent_version_id=version.id,
                metadata={"feature_set": {"entity_columns": key_columns, **materialization_metadata}, "source_version_id": version.id},
            )
            materialized_version_id = imported["version_id"]
            result["materialized_dataset_id"] = imported["dataset_id"]
            result["materialized_version_id"] = materialized_version_id
            result["materialization"] = {"entity_columns": key_columns, **materialization_metadata}
        if operation in {"feature_store", "data_product"}:
            actor = getattr(request.state, "actor", None)
            if actor is not None:
                authorize(actor, "write", workspace_id=str(payload.get("workspace_id", "default")))
            asset_type = "feature_set" if operation == "feature_store" else "data_product"
            name = str(payload.get("feature_set_name") if operation == "feature_store" else payload.get("product_name") or result.get("product_name") or f"{dataset.name} {asset_type}")
            definition = {"features": result.get("features", []), "entity_columns": payload.get("entity_columns") or [], "materialized_version_id": materialized_version_id, "lineage": {"dataset_id": dataset.id, "source_version_id": version.id}} if asset_type == "feature_set" else {"contract": result.get("contract"), "quality_policy": result.get("quality_policy"), "lineage": result.get("lineage")}
            tenant_id = getattr(getattr(request.state, "actor", None), "tenant_id", dataset.tenant_id)
            existing = db.query(WorkspaceAsset).filter(WorkspaceAsset.tenant_id == tenant_id, WorkspaceAsset.workspace_id == str(payload.get("workspace_id", "default")), WorkspaceAsset.asset_type == asset_type, WorkspaceAsset.name == name).first()
            if existing:
                existing.definition_json = definition
                existing.version += 1
                existing.updated_at = utc_now()
                asset = existing
            else:
                asset = WorkspaceAsset(tenant_id=tenant_id, workspace_id=str(payload.get("workspace_id", "default")), asset_type=asset_type, name=name, description=payload.get("description"), dataset_id=dataset.id, definition_json=definition, status="validated", owner=payload.get("owner"), tags=payload.get("tags") or [], version=1, created_at=utc_now(), updated_at=utc_now())
                db.add(asset)
            db.flush()
            result["workspace_asset_id"] = asset.id
            result["workspace_asset_version"] = asset.version
        run = _analysis(db, dataset=dataset, version=version, engine=f"IntelligenceOrchestrator.{operation}", internal_tool_id=ORCHESTRATION_TOOL_IDS.get(operation, 300), parameters=payload, result=result)
        db.commit()
        return {**result, "analysis_run_id": run.id}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/datasets/{dataset_id}/orchestrate")
def orchestrate_dataset(dataset_id: str, request: Request, payload: dict[str, Any] | None = None):
    payload = payload or {}
    return run_orchestration(dataset_id, str(payload.pop("operation", "autonomous")), request, payload)


@router.get("/feature-sets/{asset_id}/lookup")
def feature_set_lookup(asset_id: str, request: Request, entity: str, key: str):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise IntelligenceError("AUTHENTICATION_REQUIRED", "A security principal is required.", status_code=401)
    db = request.app.state.SessionLocal()
    try:
        asset = db.query(WorkspaceAsset).filter(WorkspaceAsset.id == asset_id, WorkspaceAsset.tenant_id == actor.tenant_id, WorkspaceAsset.asset_type == "feature_set", WorkspaceAsset.workspace_id == "default").first()
        if asset is None:
            raise IntelligenceError("FEATURE_SET_NOT_FOUND", "The feature set was not found.", status_code=404)
        dataset = db.query(Dataset).filter(Dataset.id == asset.dataset_id, Dataset.tenant_id == actor.tenant_id).first()
        if dataset is None:
            raise IntelligenceError("TENANT_FORBIDDEN", "The feature set is not available to this tenant.", status_code=403)
        definition = asset.definition_json or {}
        entity_columns = [str(column) for column in definition.get("entity_columns") or []]
        if entity not in entity_columns:
            raise IntelligenceError("FEATURE_ENTITY_NOT_ALLOWED", "The requested entity column is not part of the feature-set contract.", {"entity": entity, "allowed": entity_columns})
        version_id = definition.get("materialized_version_id")
        if not version_id:
            raise IntelligenceError("FEATURE_SET_NOT_MATERIALIZED", "Materialize the feature set before online lookup.", status_code=409)
        version = db.query(DatasetVersion).filter(DatasetVersion.id == version_id, DatasetVersion.dataset_id == dataset.id).first()
        if version is None:
            raise IntelligenceError("FEATURE_VERSION_NOT_FOUND", "The materialized feature version was not found.", status_code=404)
        frame = pd.read_csv(request.app.state.storage.resolve(version.storage_path))
        if entity not in frame.columns:
            raise IntelligenceError("FEATURE_ENTITY_NOT_FOUND", "The materialized feature data does not contain the entity column.", status_code=500)
        matches = frame[frame[entity].astype("string") == str(key)].head(100)
        return {"asset_id": asset.id, "asset_version": asset.version, "dataset_id": dataset.id, "source_version_id": version.id, "entity": entity, "key": key, "rows": json_safe(matches.astype(object).where(pd.notna(matches), None).to_dict(orient="records")), "row_count": int(len(matches)), "training_serving_parity": True}
    finally:
        db.close()


@router.post("/mlops/{operation}")
def run_mlops(operation: str, request: Request, payload: dict[str, Any] | None = None):
    payload = payload or {}
    actor = _model_actor(request)
    db = request.app.state.SessionLocal()
    try:
        frame = None
        dataset = version = None
        if payload.get("dataset_id"):
            dataset, version, frame = _load_dataset(db, request.app.state.storage, str(payload["dataset_id"]), payload.get("source_version_id"), expected_tenant_id=actor.tenant_id)
            payload = {**payload, "dataset_id": dataset.id, "source_version_id": version.id}
        result = _services(payload)["mlops"].run(operation, frame=frame, **payload)
        if dataset and version:
            _analysis(db, dataset=dataset, version=version, engine=f"MLOpsService.{operation}", internal_tool_id=274 + min(9, sorted(_services(payload)["mlops"].catalog()["operations"]).index(operation)) if operation in _services(payload)["mlops"].catalog()["operations"] else 283, parameters=payload, result=result)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/experiments", status_code=201)
def create_experiment(request: Request, payload: dict[str, Any]):
    actor = _model_actor(request, "write")
    db = request.app.state.SessionLocal()
    try:
        dataset, _, _ = _load_dataset(db, request.app.state.storage, str(payload.get("dataset_id")), payload.get("source_version_id"), expected_tenant_id=actor.tenant_id)
        workspace_id = str(payload.get("workspace_id", "default"))
        authorize(actor, "write", workspace_id=workspace_id)
        experiment = Experiment(tenant_id=dataset.tenant_id, workspace_id=workspace_id, name=str(payload.get("name", "")).strip(), objective=payload.get("objective"), dataset_id=dataset.id, status="ACTIVE", created_at=utc_now(), updated_at=utc_now())
        if not experiment.name:
            raise IntelligenceError("EXPERIMENT_NAME_REQUIRED", "Experiment name is required.")
        db.add(experiment)
        db.commit()
        return {"id": experiment.id, "workspace_id": experiment.workspace_id, "name": experiment.name, "objective": experiment.objective, "dataset_id": experiment.dataset_id, "status": experiment.status}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("/experiments")
def list_experiments(request: Request, workspace_id: str = "default"):
    actor = _model_actor(request, "read")
    db = request.app.state.SessionLocal()
    try:
        experiments = db.query(Experiment).filter(Experiment.tenant_id == actor.tenant_id, Experiment.workspace_id == workspace_id).order_by(Experiment.updated_at.desc()).all()
        return {"workspace_id": workspace_id, "experiments": [{"id": item.id, "name": item.name, "objective": item.objective, "dataset_id": item.dataset_id, "status": item.status, "run_count": len(item.runs)} for item in experiments]}
    finally:
        db.close()


@router.post("/experiments/{experiment_id}/runs", status_code=201)
def create_experiment_run(experiment_id: str, request: Request, payload: dict[str, Any]):
    actor = _model_actor(request, "write")
    db = request.app.state.SessionLocal()
    try:
        experiment = db.query(Experiment).filter(Experiment.id == experiment_id, Experiment.tenant_id == actor.tenant_id).first()
        if experiment is None:
            raise IntelligenceError("EXPERIMENT_NOT_FOUND", "Experiment was not found.", status_code=404)
        model_version_id = payload.get("model_version_id")
        model_version = db.query(ModelVersion).filter(ModelVersion.id == model_version_id).first() if model_version_id else None
        if model_version:
            model = db.query(RegisteredModel).filter(RegisteredModel.id == model_version.model_id, RegisteredModel.tenant_id == actor.tenant_id).first()
            if model is None:
                raise IntelligenceError("TENANT_FORBIDDEN", "The model is not available to this tenant.", status_code=403)
        if model_version and model_version.training_dataset_id != experiment.dataset_id:
            raise IntelligenceError("EXPERIMENT_LINEAGE_MISMATCH", "Model version was trained on a different dataset.")
        run = ExperimentRun(experiment_id=experiment.id, analysis_run_id=payload.get("analysis_run_id"), model_version_id=model_version_id, parameters=json_safe(payload.get("parameters") or {}), metrics=json_safe(payload.get("metrics") or (model_version.metrics if model_version else {})), reproducibility_manifest=json_safe(payload.get("reproducibility_manifest") or {}), status=str(payload.get("status", "COMPLETED")), started_at=utc_now(), completed_at=utc_now())
        db.add(run)
        experiment.updated_at = utc_now()
        db.commit()
        return {"id": run.id, "experiment_id": experiment.id, "model_version_id": run.model_version_id, "metrics": run.metrics, "status": run.status}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _execute_background_job(app: Any, job_id: str, *, raise_on_error: bool = False) -> None:
    db = app.state.SessionLocal()
    try:
        job = db.query(AutomationRun).filter(AutomationRun.id == job_id).first()
        if job is None:
            return
        job.status = "RUNNING"
        job.progress = 0.05
        job.started_at = utc_now()
        db.commit()
        parameters = dict(job.parameters or {})
        if "payload" in parameters and "context" in parameters:
            # DurableJobQueue wraps action payloads with execution context.
            queued_payload = dict(parameters.get("payload") or {})
            queued_payload.setdefault("source_version_id", job.source_version_id)
            parameters = queued_payload
        domain = str(parameters.pop("domain"))
        operation = str(parameters.pop("operation"))
        dataset, version, frame = _load_dataset(db, app.state.storage, job.dataset_id, job.source_version_id, expected_tenant_id=job.tenant_id)
        services = _services(parameters)
        artifact_ids = []
        if domain == "forecasting":
            result = services["forecasting"].run(frame, operation, **parameters)
        elif domain == "ml":
            if operation == "train":
                outcome = services["ml"].train(frame, **parameters)
                result = {**outcome.result, "registered_model": _persist_training(db, app.state.storage, dataset, version, outcome, parameters)}
                artifact_ids.append(result["registered_model"]["artifact_id"])
            elif operation == "compare":
                result, outcome = services["ml"].compare(frame, **parameters)
                result["registered_champion"] = _persist_training(db, app.state.storage, dataset, version, outcome, parameters)
                artifact_ids.append(result["registered_champion"]["artifact_id"])
            elif operation == "tune":
                result, outcome = services["ml"].tune(frame, **parameters)
                result["registered_best_model"] = _persist_training(db, app.state.storage, dataset, version, outcome, parameters)
                artifact_ids.append(result["registered_best_model"]["artifact_id"])
            else:
                result = services["ml"].readiness(frame, **parameters)
        elif domain == "unsupervised":
            if operation == "run":
                outcome = services["unsupervised"].run(frame, **parameters)
                artifact = _store_frame_artifact(db, app.state.storage, dataset=dataset, version=version, frame=outcome.artifact_frame, artifact_type="unsupervised_output", metadata={"algorithm": outcome.result["algorithm"]})
                result = {**outcome.result, "artifact": _public_artifact(artifact)}
                artifact_ids.append(artifact.id)
            elif operation == "compare":
                result = services["unsupervised"].compare(frame, **parameters)
            else:
                result = services["unsupervised"].stability(frame, **parameters)
        elif domain == "data_engineering":
            result = services["data_engineering"].run(frame, operation, **parameters)
        elif domain == "orchestration":
            orchestration_parameters = {key: value for key, value in parameters.items() if key not in {"dataset_id", "source_version_id"}}
            result = services["orchestration"].run(frame, operation, dataset_id=dataset.id, source_version_id=version.id, **orchestration_parameters)
        else:
            raise IntelligenceError("UNKNOWN_JOB_DOMAIN", "Unsupported background job domain.")
        job.result = json_safe(result)
        job.artifact_ids = artifact_ids
        job.progress = 1.0
        job.status = "COMPLETED"
        job.completed_at = utc_now()
        _analysis(db, dataset=dataset, version=version, engine=f"BackgroundJob.{domain}.{operation}", internal_tool_id=300, parameters=parameters, result=result)
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.query(AutomationRun).filter(AutomationRun.id == job_id).first()
        if job:
            job.status = "FAILED"
            job.progress = 1.0
            job.error_details = {"code": exc.code if isinstance(exc, IntelligenceError) else "JOB_FAILED", "message": exc.message if isinstance(exc, IntelligenceError) else str(exc), "details": exc.details if isinstance(exc, IntelligenceError) else {}}
            job.completed_at = utc_now()
            db.commit()
        if raise_on_error:
            raise
    finally:
        db.close()


@router.post("/datasets/{dataset_id}/intelligence/jobs", status_code=202)
def create_intelligence_job(dataset_id: str, request: Request, background_tasks: BackgroundTasks, payload: dict[str, Any]):
    db = request.app.state.SessionLocal()
    try:
        domain = str(payload.get("domain", ""))
        operation = str(payload.get("operation", ""))
        if domain not in {"forecasting", "ml", "unsupervised", "data_engineering", "orchestration"} or not operation:
            raise IntelligenceError("INVALID_JOB", "A supported domain and operation are required.")
        dataset, version, _ = _load_dataset(db, request.app.state.storage, dataset_id, payload.get("source_version_id"))
        actor = getattr(request.state, "actor", None)
        tenant_id = actor.tenant_id if actor is not None else (dataset.tenant_id or "default")
        if request.app.state.settings.job_worker_enabled:
            queued = DurableJobQueue(request.app.state.SessionLocal, worker_id=request.app.state.settings.worker_id).enqueue(
                tenant_id=tenant_id,
                action=f"intelligence.{domain}.{operation}",
                context={"dataset_id": dataset.id, "version_id": version.id},
                payload=json_safe(payload),
                idempotency_key=payload.get("idempotency_key"),
            )
            return {"job_id": queued.id, "status": queued.status, "progress": queued.progress, "dataset_id": dataset.id, "source_version_id": version.id, "correlation_id": queued.correlation_id, "durable": True}
        job = AutomationRun(tenant_id=tenant_id, dataset_id=dataset.id, source_version_id=version.id, action=f"intelligence.{domain}.{operation}", job_type=f"{domain}.{operation}", parameters=json_safe(payload), idempotency_key=payload.get("idempotency_key"), correlation_id=payload.get("correlation_id") or str(uuid4()), status="QUEUED", progress=0.0, result=None, artifact_ids=[], error_details=None, created_at=utc_now())
        db.add(job)
        db.commit()
        background_tasks.add_task(_execute_background_job, request.app, job.id)
        return {"job_id": job.id, "status": job.status, "progress": job.progress, "dataset_id": dataset.id, "source_version_id": version.id, "correlation_id": job.correlation_id}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("/intelligence/jobs/{job_id}")
def get_intelligence_job(job_id: str, request: Request):
    db = request.app.state.SessionLocal()
    try:
        actor = getattr(request.state, "actor", None)
        query = db.query(AutomationRun).filter(AutomationRun.id == job_id)
        if actor is not None:
            query = query.filter(AutomationRun.tenant_id == actor.tenant_id)
        job = query.first()
        if job is None:
            raise IntelligenceError("JOB_NOT_FOUND", "Intelligence job was not found.", status_code=404)
        return {"job_id": job.id, "job_type": job.job_type, "dataset_id": job.dataset_id, "source_version_id": job.source_version_id, "status": job.status, "progress": job.progress, "result": job.result, "artifact_ids": job.artifact_ids, "error": job.error_details, "correlation_id": job.correlation_id, "created_at": job.created_at.isoformat() if job.created_at else None, "started_at": job.started_at.isoformat() if job.started_at else None, "completed_at": job.completed_at.isoformat() if job.completed_at else None}
    finally:
        db.close()
