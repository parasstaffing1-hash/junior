from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

import pandas as pd
from fastapi import Depends, FastAPI, File, Form, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.core.database import Base, SessionLocal, build_database, engine
from app.core.automation.gateway import action_catalog, dispatch_plan
from app.core.cleaning.recipe_engine import execute_recipe
from app.core.dashboard.layout import validate_layout
from app.core.eda.findings import detect_findings
from app.core.eda.report import generate_eda_report
from app.core.kpi.calculator import calculate_kpi
from app.orchestration.quality_pipeline import QualityPipeline
from app.core.statistics.report import generate_statistics_report
from app.core.transformation.pipeline import execute_pipeline
from app.core.visualization.recommender import recommend_charts
from app.errors import AppError
from app.models.all import AnalysisRun, Artifact, AuditEvent, Dataset, DatasetVersion
from app.orchestration.automated_analyst import AutomatedAnalyst
from app.orchestration.platform import (
    PlatformAnalysisError,
    _create_version,
    jsonable,
    load_current_dataset,
    run_full_platform_analysis,
)
from app.core.intake.importer import DatasetImporter
from app.core.bi.report_service import build_bi_report
from app.storage.dataset_storage import DatasetStorage


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = PROJECT_ROOT / "frontend"


def _error_response(exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
    )


def _remove_artifact_paths(value):
    """Keep local chart file paths out of the browser-facing JSON payload."""
    if isinstance(value, dict):
        return {key: _remove_artifact_paths(item) for key, item in value.items() if key not in {"artifact_path", "image_path"}}
    if isinstance(value, list):
        return [_remove_artifact_paths(item) for item in value]
    return value


def create_app(*, database_url: str | None = None, storage_root: str | Path | None = None, max_upload_bytes: int | None = None) -> FastAPI:
    """Create an isolated app instance for production or tests."""
    if database_url is None:
        database_engine, session_factory = engine, SessionLocal
    else:
        database_engine, session_factory = build_database(database_url)

    Base.metadata.create_all(bind=database_engine)
    configured_storage_root = storage_root or os.getenv("STORAGE_ROOT") or PROJECT_ROOT / "storage"
    configured_limit = max_upload_bytes
    if configured_limit is None:
        try:
            configured_limit = int(os.getenv("MAX_UPLOAD_BYTES", str(100 * 1024 * 1024)))
        except ValueError as exc:
            raise RuntimeError("MAX_UPLOAD_BYTES must be an integer.") from exc
    if configured_limit <= 0:
        raise RuntimeError("MAX_UPLOAD_BYTES must be greater than zero.")
    storage = DatasetStorage(root=str(configured_storage_root), max_upload_bytes=configured_limit)
    importer = DatasetImporter(storage=storage)

    app = FastAPI(title="Automated Data Analyst API", version="1.0.0")
    app.state.engine = database_engine
    app.state.SessionLocal = session_factory
    app.state.storage = storage
    app.state.importer = importer

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if FRONTEND_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError):
        return _error_response(exc)

    @app.exception_handler(PlatformAnalysisError)
    async def platform_analysis_error_handler(_: Request, exc: PlatformAnalysisError):
        return JSONResponse(status_code=404 if exc.code in {"DATASET_NOT_FOUND", "VERSION_NOT_FOUND"} else 422, content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}})

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(_: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": {"code": "REQUEST_INVALID", "message": "Request validation failed.", "details": {"errors": exc.errors()}}})

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_: Request, exc: Exception):
        if os.getenv("APP_ENV", "development").casefold() == "development":
            return JSONResponse(status_code=500, content={"error": {"code": "INTERNAL_ERROR", "message": str(exc), "details": {}}})
        return JSONResponse(status_code=500, content={"error": {"code": "INTERNAL_ERROR", "message": "The request could not be completed.", "details": {}}})

    def get_db(request: Request):
        db = request.app.state.SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def build_dataset_bi_report(
        dataset_id: str,
        request: Request,
        db: Session,
        *,
        country: str | None = None,
        product: str | None = None,
        segment: str | None = None,
        store: str | None = None,
    ):
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        if dataset is None:
            raise AppError("DATASET_NOT_FOUND", "Dataset was not found.", status_code=404, details={"dataset_id": dataset_id})
        version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.current_version_id).first()
        if version is None:
            raise AppError("VERSION_NOT_FOUND", "Dataset has no current version.", status_code=404, details={"dataset_id": dataset_id})
        try:
            frame = pd.read_csv(request.app.state.storage.resolve(version.storage_path))
            report = build_bi_report(
                frame,
                dataset_name=dataset.name,
                source_version_id=version.id,
                filters={"country": country, "product": product, "segment": segment, "store": store},
                output_dir=request.app.state.storage.root / dataset_id / "reports",
            )
        except Exception as exc:
            raise AppError(
                "BI_REPORT_FAILED",
                "The BI report could not be generated from this dataset.",
                status_code=422,
                details={"dataset_id": dataset_id, "error": str(exc)},
            ) from exc
        return dataset, report

    def public_bi_report(dataset_id: str, report: dict, *, country: str | None, product: str | None, segment: str | None, store: str | None = None):
        query = urlencode({key: value for key, value in {"country": country, "product": product, "segment": segment, "store": store}.items() if value})
        suffix = f"?{query}" if query else ""
        return {
            "report_id": report["report_id"],
            "report": report["report"],
            "source": report["source"],
            "field_mapping": report["field_mapping"],
            "filters": report["filters"],
            "applied_filters": report["applied_filters"],
            "kpis": report["kpis"],
            "findings": report.get("findings", []),
            "charts": _remove_artifact_paths(report["charts"]),
            "tables": report["tables"],
            "dashboard": _remove_artifact_paths(report["dashboard"]),
            "downloads": {
                "html": f"/api/v1/datasets/{dataset_id}/bi_report/html{suffix}",
                "pdf": f"/api/v1/datasets/{dataset_id}/bi_report/pdf{suffix}",
                "xlsx": f"/api/v1/datasets/{dataset_id}/bi_report/xlsx{suffix}",
            },
        }

    def load_dataset_frame(dataset_id: str, request: Request, db: Session, version_id: str | None = None):
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        if dataset is None:
            raise PlatformAnalysisError("DATASET_NOT_FOUND", "Dataset was not found.", {"dataset_id": dataset_id})
        selected_version_id = version_id or dataset.current_version_id
        version = db.query(DatasetVersion).filter(DatasetVersion.id == selected_version_id, DatasetVersion.dataset_id == dataset_id).first()
        if version is None:
            raise PlatformAnalysisError("VERSION_NOT_FOUND", "The requested dataset version was not found.", {"dataset_id": dataset_id, "version_id": selected_version_id})
        try:
            frame = pd.read_csv(request.app.state.storage.resolve(version.storage_path))
        except pd.errors.EmptyDataError:
            frame = pd.DataFrame()
        return dataset, version, frame

    def frame_preview(frame: pd.DataFrame, limit: int = 20):
        safe = frame.head(max(0, min(limit, 500))).astype(object).where(pd.notna(frame.head(max(0, min(limit, 500)))), None)
        return {"columns": [str(column) for column in frame.columns], "rows": jsonable(safe.to_dict(orient="records")), "row_count": len(frame), "column_count": len(frame.columns)}

    def create_output_version(dataset, parent, frame, request, db, *, pipeline_type: str, parameters: dict[str, Any]):
        if frame.equals(pd.read_csv(request.app.state.storage.resolve(parent.storage_path))):
            return parent, {"version_created": False, "version_id": parent.id, "version_number": parent.version_number}
        version, details = _create_version(
            db,
            request.app.state.storage,
            dataset,
            parent,
            frame,
            pipeline_type=pipeline_type,
            parameters=parameters,
            before_stats={"row_count": parent.row_count, "column_count": parent.column_count},
        )
        return version, {"version_created": True, **details}

    @app.get("/", response_class=HTMLResponse)
    async def serve_frontend():
        index_path = FRONTEND_DIR / "index.html"
        if index_path.is_file():
            return index_path.read_text(encoding="utf-8")
        return "<h1>Automated Data Analyst API is running.</h1>"

    @app.get("/health")
    @app.get("/health/live")
    def health_check():
        return {"status": "ok"}

    @app.get("/health/ready")
    def readiness_check(request: Request):
        with request.app.state.SessionLocal() as db:
            db.query(Dataset).limit(1).all()
        return {"status": "ready"}

    @app.get("/api/v1/datasets")
    def list_datasets(db: Session = Depends(get_db)):
        datasets = db.query(Dataset).order_by(Dataset.created_at.desc()).all()
        results = []
        for dataset in datasets:
            version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.current_version_id).first()
            latest_run = db.query(AnalysisRun).filter(AnalysisRun.dataset_id == dataset.id).order_by(AnalysisRun.created_at.desc()).first()
            latest_result = latest_run.result if latest_run else None
            health = (latest_result or {}).get("health") or (latest_result or {}).get("health_score")
            results.append({
                "id": dataset.id,
                "name": dataset.name,
                "created_at": dataset.created_at,
                "row_count": version.row_count if version else 0,
                "column_count": version.column_count if version else 0,
                "source_type": dataset.source_type,
                "current_version_id": version.id if version else None,
                "health_score": health,
                "analysis_available": latest_run is not None,
            })
        return results

    @app.post("/api/v1/datasets/import/inspect")
    async def inspect_dataset(request: Request, file: UploadFile = File(...)):
        return await request.app.state.importer.inspect(file)

    @app.post("/api/v1/datasets/import")
    async def import_dataset(
        request: Request,
        file: UploadFile = File(...),
        sheet_name: Optional[str] = Form(None),
        db: Session = Depends(get_db),
    ):
        kwargs = {"sheet_name": sheet_name} if sheet_name else {}
        return await request.app.state.importer.import_file(db=db, upload=file, **kwargs)

    @app.get("/api/v1/datasets/{dataset_id}/preview")
    def get_dataset_preview(
        dataset_id: str,
        request: Request,
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=0, le=500),
        db: Session = Depends(get_db),
    ):
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        if dataset is None:
            raise AppError("DATASET_NOT_FOUND", "Dataset was not found.", status_code=404, details={"dataset_id": dataset_id})
        version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.current_version_id).first()
        if version is None:
            raise AppError("VERSION_NOT_FOUND", "Dataset has no current version.", status_code=404, details={"dataset_id": dataset_id})
        storage = request.app.state.storage
        path = storage.resolve(version.storage_path)
        try:
            frame = pd.read_csv(path, skiprows=range(1, offset + 1), nrows=limit if limit else 0)
            # Cast to object first; otherwise pandas keeps float columns as NaN
            # and Starlette correctly rejects NaN as invalid JSON.
            frame = frame.astype(object).where(pd.notna(frame), None)
            rows = frame.to_dict(orient="records")
            columns = [str(column) for column in frame.columns]
        except pd.errors.EmptyDataError:
            rows, columns = [], []

        latest_run = db.query(AnalysisRun).filter(AnalysisRun.dataset_id == dataset_id).order_by(AnalysisRun.created_at.desc()).first()
        result = latest_run.result if latest_run else None
        health = (result or {}).get("health") or (result or {}).get("health_score")
        return {
            "dataset_id": dataset_id,
            "source_version_id": version.id,
            "schema": {"row_count": version.row_count, "column_count": version.column_count, "columns": columns},
            "preview": {"offset": offset, "limit": limit, "total_rows": version.row_count, "total_columns": version.column_count, "columns": columns, "rows": rows},
            "health_score": health,
            "analysis_available": latest_run is not None,
        }

    @app.get("/api/v1/datasets/{dataset_id}/source")
    def download_dataset_source(dataset_id: str, request: Request, db: Session = Depends(get_db)):
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        if dataset is None:
            raise AppError("DATASET_NOT_FOUND", "Dataset was not found.", status_code=404, details={"dataset_id": dataset_id})
        version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.current_version_id).first()
        if version is None:
            raise AppError("VERSION_NOT_FOUND", "Dataset has no current version.", status_code=404, details={"dataset_id": dataset_id})
        return FileResponse(request.app.state.storage.resolve(version.storage_path), media_type="text/csv", filename=dataset.original_filename)

    @app.post("/api/v1/datasets/{dataset_id}/automated_analyst")
    def run_automated_analyst(dataset_id: str, request: Request, db: Session = Depends(get_db)):
        analyst = AutomatedAnalyst(db=db, storage=request.app.state.storage)
        return analyst.run_full_pipeline(dataset_id=dataset_id)

    @app.get("/api/v1/datasets/{dataset_id}/bi_report")
    def get_bi_report(
        dataset_id: str,
        request: Request,
        country: Optional[str] = Query(None),
        product: Optional[str] = Query(None),
        segment: Optional[str] = Query(None),
        store: Optional[str] = Query(None),
        db: Session = Depends(get_db),
    ):
        _, report = build_dataset_bi_report(
            dataset_id,
            request,
            db,
            country=country,
            product=product,
            segment=segment,
            store=store,
        )
        return public_bi_report(dataset_id, report, country=country, product=product, segment=segment, store=store)

    def download_bi_report(
        dataset_id: str,
        request: Request,
        db: Session,
        kind: str,
        country: str | None,
        product: str | None,
        segment: str | None,
        store: str | None,
    ):
        dataset, report = build_dataset_bi_report(
            dataset_id,
            request,
            db,
            country=country,
            product=product,
            segment=segment,
            store=store,
        )
        path = report["files"].get(kind)
        if not path:
            raise AppError("BI_REPORT_FILE_MISSING", "The requested BI report file was not created.", status_code=500, details={"format": kind})
        extension, media_type = {
            "html": ("html", "text/html"),
            "pdf": ("pdf", "application/pdf"),
            "xlsx": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        }[kind]
        safe_name = f"bi_report_{dataset_id}.{extension}"
        return FileResponse(path, media_type=media_type, filename=safe_name)

    @app.get("/api/v1/datasets/{dataset_id}/bi_report/html")
    def download_bi_report_html(
        dataset_id: str,
        request: Request,
        country: Optional[str] = Query(None),
        product: Optional[str] = Query(None),
        segment: Optional[str] = Query(None),
        store: Optional[str] = Query(None),
        db: Session = Depends(get_db),
    ):
        return download_bi_report(dataset_id, request, db, "html", country, product, segment, store)

    @app.get("/api/v1/datasets/{dataset_id}/bi_report/pdf")
    def download_bi_report_pdf(
        dataset_id: str,
        request: Request,
        country: Optional[str] = Query(None),
        product: Optional[str] = Query(None),
        segment: Optional[str] = Query(None),
        store: Optional[str] = Query(None),
        db: Session = Depends(get_db),
    ):
        return download_bi_report(dataset_id, request, db, "pdf", country, product, segment, store)

    @app.get("/api/v1/datasets/{dataset_id}/bi_report/xlsx")
    def download_bi_report_xlsx(
        dataset_id: str,
        request: Request,
        country: Optional[str] = Query(None),
        product: Optional[str] = Query(None),
        segment: Optional[str] = Query(None),
        store: Optional[str] = Query(None),
        db: Session = Depends(get_db),
    ):
        return download_bi_report(dataset_id, request, db, "xlsx", country, product, segment, store)

    @app.post("/api/v1/automated-analyst/analyze")
    def analyze_dataset(payload: dict[str, Any] | None = None, request: Request = None, db: Session = Depends(get_db)):
        dataset_id = (payload or {}).get("dataset_id")
        if not dataset_id:
            raise AppError("DATASET_ID_REQUIRED", "dataset_id is required.", status_code=422)
        analyst = AutomatedAnalyst(db=db, storage=request.app.state.storage)
        return analyst.run_full_pipeline(dataset_id=dataset_id)

    @app.get("/api/v1/reports/{dataset_id}")
    def get_standard_report(dataset_id: str, request: Request, country: Optional[str] = Query(None), product: Optional[str] = Query(None), segment: Optional[str] = Query(None), store: Optional[str] = Query(None), db: Session = Depends(get_db)):
        _, report = build_dataset_bi_report(dataset_id, request, db, country=country, product=product, segment=segment, store=store)
        return public_bi_report(dataset_id, report, country=country, product=product, segment=segment, store=store)

    @app.get("/api/v1/dashboards/{dataset_id}")
    def get_standard_dashboard(dataset_id: str, request: Request, country: Optional[str] = Query(None), product: Optional[str] = Query(None), segment: Optional[str] = Query(None), store: Optional[str] = Query(None), db: Session = Depends(get_db)):
        _, report = build_dataset_bi_report(dataset_id, request, db, country=country, product=product, segment=segment, store=store)
        return {"dashboard": _remove_artifact_paths(report["dashboard"]), "kpis": report["kpis"], "charts": _remove_artifact_paths(report["charts"]), "tables": report["tables"], "findings": report.get("findings", []), "source": report["source"]}

    @app.get("/api/v1/datasets/{dataset_id}/analysis")
    def get_latest_analysis(dataset_id: str, db: Session = Depends(get_db)):
        run = db.query(AnalysisRun).filter(AnalysisRun.dataset_id == dataset_id).order_by(AnalysisRun.created_at.desc()).first()
        if run is None:
            raise AppError("ANALYSIS_NOT_FOUND", "No analysis has been run for this dataset yet.", status_code=404, details={"dataset_id": dataset_id})
        return {"run_id": run.id, "dataset_id": dataset_id, "source_version_id": run.source_version_id, "status": run.status, "created_at": run.created_at, "result": run.result, "warnings": run.warnings}

    @app.get("/api/v1/datasets/{dataset_id}/versions")
    def list_dataset_versions(dataset_id: str, db: Session = Depends(get_db)):
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        if dataset is None:
            raise AppError("DATASET_NOT_FOUND", "Dataset was not found.", status_code=404, details={"dataset_id": dataset_id})
        versions = db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset_id).order_by(DatasetVersion.version_number.asc()).all()
        return [{"id": version.id, "version_number": version.version_number, "parent_version_id": version.parent_version_id, "storage_path": version.storage_path, "sha256": version.sha256, "row_count": version.row_count, "column_count": version.column_count, "created_at": version.created_at, "is_current": version.id == dataset.current_version_id} for version in versions]

    @app.get("/api/v1/datasets/{dataset_id}/lineage")
    def get_dataset_lineage(dataset_id: str, db: Session = Depends(get_db)):
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        if dataset is None:
            raise AppError("DATASET_NOT_FOUND", "Dataset was not found.", status_code=404, details={"dataset_id": dataset_id})
        audits = db.query(AuditEvent).filter(AuditEvent.dataset_id == dataset_id).order_by(AuditEvent.timestamp.asc()).all()
        artifacts = db.query(Artifact).filter(Artifact.dataset_id == dataset_id).order_by(Artifact.created_at.asc()).all()
        return {
            "dataset_id": dataset_id,
            "dataset_name": dataset.name,
            "current_version_id": dataset.current_version_id,
            "events": [{"id": event.id, "engine": event.engine, "input_version_id": event.input_version_id, "output_version_id": event.output_version_id, "parameters": event.parameters, "affected_rows": event.affected_rows, "affected_columns": event.affected_columns, "before_stats": event.before_stats, "after_stats": event.after_stats, "timestamp": event.timestamp} for event in audits],
            "artifacts": [{"id": artifact.id, "type": artifact.artifact_type, "source_version_id": artifact.source_version_id, "storage_path": artifact.storage_path, "sha256": artifact.sha256, "size": artifact.size, "metadata": artifact.metadata_json, "created_at": artifact.created_at} for artifact in artifacts],
        }

    @app.post("/api/v1/datasets/{dataset_id}/quality/analyze")
    def analyze_quality(dataset_id: str, payload: dict[str, Any] | None = None, request: Request = None, db: Session = Depends(get_db)):
        _, version, frame = load_dataset_frame(dataset_id, request, db, (payload or {}).get("version_id"))
        rows = frame.astype(object).where(pd.notna(frame), None).to_dict(orient="records")
        result = QualityPipeline().analyze(rows, dataset_id=dataset_id, source_version_id=version.id, rules=(payload or {}).get("rules"), columns=(payload or {}).get("columns"))
        return result

    def cleaning_operation(dataset_id: str, payload: dict[str, Any] | None, request: Request, db: Session, apply: bool):
        _, version, frame = load_dataset_frame(dataset_id, request, db, (payload or {}).get("version_id"))
        steps = (payload or {}).get("steps") or []
        try:
            result = execute_recipe(frame, steps)
        except Exception as exc:
            raise AppError("CLEANING_FAILED", getattr(exc, "message", str(exc)), status_code=422, details=getattr(exc, "details", {})) from exc
        response = {"dataset_id": dataset_id, "input_version_id": version.id, "version_created": False, "recipe": jsonable(result), "preview": frame_preview(result.dataframe, (payload or {}).get("preview_limit", 20))}
        if apply:
            dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
            output_version, version_result = create_output_version(dataset, version, result.dataframe, request, db, pipeline_type="CleaningRecipe", parameters={"steps": steps})
            response.update({"output_version_id": output_version.id, **version_result})
        return response

    @app.post("/api/v1/datasets/{dataset_id}/cleaning/preview")
    def preview_cleaning(dataset_id: str, payload: dict[str, Any] | None = None, request: Request = None, db: Session = Depends(get_db)):
        return cleaning_operation(dataset_id, payload, request, db, False)

    @app.post("/api/v1/datasets/{dataset_id}/cleaning/apply")
    def apply_cleaning(dataset_id: str, payload: dict[str, Any] | None = None, request: Request = None, db: Session = Depends(get_db)):
        return cleaning_operation(dataset_id, payload, request, db, True)

    def external_frames(payload: dict[str, Any] | None, request: Request, db: Session):
        frames = {}
        for reference, external_dataset_id in ((payload or {}).get("external_datasets") or {}).items():
            _, _, external_frame = load_dataset_frame(str(external_dataset_id), request, db)
            frames[str(reference)] = external_frame
        return frames

    def transformation_operation(dataset_id: str, payload: dict[str, Any] | None, request: Request, db: Session, apply: bool):
        dataset, version, frame = load_dataset_frame(dataset_id, request, db, (payload or {}).get("version_id"))
        try:
            result = execute_pipeline(frame, steps=(payload or {}).get("steps") or [], external_frames=external_frames(payload, request, db))
        except Exception as exc:
            raise AppError("TRANSFORMATION_FAILED", getattr(exc, "message", str(exc)), status_code=422, details=getattr(exc, "details", {})) from exc
        response = {"dataset_id": dataset_id, "input_version_id": version.id, "pipeline": jsonable(result), "analysis_outputs": result.analysis_outputs, "preview": frame_preview(result.dataframe, (payload or {}).get("preview_limit", 20)), "version_created": False}
        if apply:
            output_version, version_result = create_output_version(dataset, version, result.dataframe, request, db, pipeline_type="TransformationPipeline", parameters={"steps": (payload or {}).get("steps") or [], "external_datasets": (payload or {}).get("external_datasets") or {}})
            response.update({"output_version_id": output_version.id, **version_result})
        return response

    @app.post("/api/v1/datasets/{dataset_id}/transformations/preview")
    def preview_transformations(dataset_id: str, payload: dict[str, Any] | None = None, request: Request = None, db: Session = Depends(get_db)):
        return transformation_operation(dataset_id, payload, request, db, False)

    @app.post("/api/v1/datasets/{dataset_id}/transformations/apply")
    def apply_transformations(dataset_id: str, payload: dict[str, Any] | None = None, request: Request = None, db: Session = Depends(get_db)):
        return transformation_operation(dataset_id, payload, request, db, True)

    @app.post("/api/v1/datasets/{dataset_id}/statistics/summary")
    def statistics_summary(dataset_id: str, payload: dict[str, Any] | None = None, request: Request = None, db: Session = Depends(get_db)):
        _, version, frame = load_dataset_frame(dataset_id, request, db, (payload or {}).get("version_id"))
        numeric = [column for column in frame.columns if pd.api.types.is_numeric_dtype(frame[column])]
        selected = (payload or {}).get("columns") or numeric
        if not selected:
            return {"source_version_id": version.id, "warnings": [{"code": "NO_NUMERIC_COLUMNS", "message": "No numeric columns were available."}], "sections": {}}
        result = generate_statistics_report(frame, title=(payload or {}).get("title", "Statistics Summary Report"), columns=selected, sections=(payload or {}).get("sections"), confidence_intervals=(payload or {}).get("confidence_intervals"), effect_sizes=(payload or {}).get("effect_sizes"))
        return {"source_version_id": version.id, **result}

    @app.post("/api/v1/datasets/{dataset_id}/eda/report")
    def eda_report(dataset_id: str, payload: dict[str, Any] | None = None, request: Request = None, db: Session = Depends(get_db)):
        _, version, frame = load_dataset_frame(dataset_id, request, db, (payload or {}).get("version_id"))
        result = generate_eda_report(frame, title=(payload or {}).get("title", "Automated EDA Report"), sections=(payload or {}).get("sections"), numeric_columns=(payload or {}).get("numeric_columns"), categorical_columns=(payload or {}).get("categorical_columns"))
        return {"source_version_id": version.id, **result}

    @app.post("/api/v1/datasets/{dataset_id}/findings")
    def dataset_findings(dataset_id: str, payload: dict[str, Any] | None = None, request: Request = None, db: Session = Depends(get_db)):
        _, version, frame = load_dataset_frame(dataset_id, request, db, (payload or {}).get("version_id"))
        options = {key: value for key, value in (payload or {}).items() if key in {"high_missing_threshold", "strong_correlation_threshold", "strong_categorical_threshold", "outlier_rate_threshold", "imbalance_threshold", "id_like_threshold", "max_findings"}}
        return {"source_version_id": version.id, **detect_findings(frame, **options)}

    @app.post("/api/v1/datasets/{dataset_id}/visualization/recommend")
    def recommend_visualizations(dataset_id: str, payload: dict[str, Any] | None = None, request: Request = None, db: Session = Depends(get_db)):
        _, version, frame = load_dataset_frame(dataset_id, request, db, (payload or {}).get("version_id"))
        options = {key: value for key, value in (payload or {}).items() if key in {"intent", "x_column", "y_column", "category_column", "time_column", "group_column", "size_column", "numeric_columns", "business_chart_type", "comparison_column", "line_column", "max_recommendations"}}
        return {"source_version_id": version.id, **recommend_charts(frame, **options)}

    @app.post("/api/v1/datasets/{dataset_id}/kpis/calculate")
    def calculate_dataset_kpi(dataset_id: str, payload: dict[str, Any] | None = None, request: Request = None, db: Session = Depends(get_db)):
        _, version, frame = load_dataset_frame(dataset_id, request, db, (payload or {}).get("version_id"))
        definition = (payload or {}).get("definition")
        if not definition:
            raise AppError("KPI_DEFINITION_REQUIRED", "definition is required.", status_code=422)
        result = calculate_kpi(frame, definition, filters=(payload or {}).get("filters"), dimensions=(payload or {}).get("dimensions"), time_column=(payload or {}).get("time_column"), time_grain=(payload or {}).get("time_grain"))
        return {"source_version_id": version.id, **jsonable(result)}

    @app.post("/api/v1/dashboards/{dashboard_id}/validate")
    def validate_dashboard(dashboard_id: str, payload: dict[str, Any] | None = None):
        widgets = (payload or {}).get("widgets") or []
        try:
            validation = validate_layout(widgets, grid_columns=int((payload or {}).get("grid_columns", 12)), allow_overlap=bool((payload or {}).get("allow_overlap", False)))
        except Exception as exc:
            raise AppError("DASHBOARD_INVALID", getattr(exc, "message", str(exc)), status_code=422, details=getattr(exc, "details", {})) from exc
        return {"dashboard_id": dashboard_id, "validation": validation, "widgets": widgets}

    @app.get("/api/v1/automation/actions")
    def automation_actions():
        return {"actions": action_catalog()}

    @app.post("/api/v1/automation/plan")
    def automation_plan(payload: dict[str, Any] | None = None):
        request_payload = payload or {}
        try:
            return dispatch_plan(request_payload.get("action"), request_payload.get("context") or {}, request_payload.get("payload") or {})
        except Exception as exc:
            raise AppError("AUTOMATION_ACTION_NOT_ALLOWED", getattr(exc, "message", str(exc)), status_code=422, details=getattr(exc, "details", {})) from exc

    @app.post("/api/v1/automation/execute")
    def automation_execute(payload: dict[str, Any] | None = None, request: Request = None, db: Session = Depends(get_db)):
        body = payload or {}
        action = body.get("action")
        context = body.get("context") or {}
        action_payload = body.get("payload") or {}
        try:
            dispatch_plan(action, context, action_payload)
        except Exception as exc:
            raise AppError("AUTOMATION_ACTION_NOT_ALLOWED", getattr(exc, "message", str(exc)), status_code=422, details=getattr(exc, "details", {})) from exc
        dataset_id = context.get("dataset_id")
        if action == "analyst.analyze":
            return AutomatedAnalyst(db=db, storage=request.app.state.storage).run_full_pipeline(dataset_id)
        if action == "bi.report":
            _, report = build_dataset_bi_report(dataset_id, request, db, country=action_payload.get("country"), product=action_payload.get("product"), segment=action_payload.get("segment"))
            return public_bi_report(dataset_id, report, country=action_payload.get("country"), product=action_payload.get("product"), segment=action_payload.get("segment"))
        if action == "quality.analyze":
            return analyze_quality(dataset_id, action_payload, request, db)
        if action == "cleaning.preview":
            return cleaning_operation(dataset_id, action_payload, request, db, False)
        if action == "cleaning.apply":
            return cleaning_operation(dataset_id, action_payload, request, db, True)
        if action == "transformation.preview":
            return transformation_operation(dataset_id, action_payload, request, db, False)
        if action == "transformation.apply":
            return transformation_operation(dataset_id, action_payload, request, db, True)
        if action == "statistics.summary":
            return statistics_summary(dataset_id, action_payload, request, db)
        if action == "eda.report":
            return eda_report(dataset_id, action_payload, request, db)
        if action == "charts.recommend":
            return recommend_visualizations(dataset_id, action_payload, request, db)
        if action == "kpi.calculate":
            return calculate_dataset_kpi(dataset_id, action_payload, request, db)
        if action == "dashboard.validate":
            return validate_dashboard(context.get("dashboard_id"), action_payload)
        if action == "report.pdf":
            return generate_report_file(action_payload, "pdf")
        if action == "report.excel":
            return generate_report_file(action_payload, "xlsx")
        raise AppError("AUTOMATION_EXECUTION_UNSUPPORTED", "The action is allowlisted but has no execution adapter.", status_code=422, details={"action": action})

    def generate_report_file(payload: dict[str, Any] | None, kind: str):
        manifest = (payload or {}).get("manifest") or payload or {}
        try:
            if kind == "pdf":
                from app.core.reporting.pdf_generator import build_pdf_bytes
                data = build_pdf_bytes(manifest)
                return Response(content=data, media_type="application/pdf", headers={"Content-Disposition": "attachment; filename=analytics_report.pdf"})
            from app.core.reporting.xlsx_generator import build_xlsx_bytes
            data = build_xlsx_bytes(manifest)
            return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=analytics_report.xlsx"})
        except Exception as exc:
            raise AppError("REPORT_EXPORT_FAILED", getattr(exc, "message", str(exc)), status_code=422, details=getattr(exc, "details", {})) from exc

    @app.post("/api/v1/pdf-reports/generate")
    def generate_pdf_report(payload: dict[str, Any] | None = None):
        return generate_report_file(payload, "pdf")

    @app.post("/api/v1/excel-reports/generate")
    def generate_excel_report(payload: dict[str, Any] | None = None):
        return generate_report_file(payload, "xlsx")

    return app


app = create_app()
