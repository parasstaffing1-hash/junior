from __future__ import annotations

import os
import re
import time
import uuid
from collections import OrderedDict
import json
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

import pandas as pd
from fastapi import Depends, FastAPI, File, Form, Query, Request, UploadFile, BackgroundTasks
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.core.database import Base, SessionLocal, build_database, engine, ensure_additive_local_schema
from app.core.config import load_settings
from app.core.security import Actor, RateLimiter, SecurityError, assert_dataset_tenant, authenticate, audit_request
from app.core.security_policy import apply_row_policies, visible_columns
from app.core.observability import MetricsRegistry
from app.core.lineage import build_column_lineage
from app.core.enterprise.production_readiness import production_readiness
from app.core.automation.gateway import action_catalog, dispatch_plan
from app.core.cleaning.recipe_engine import execute_recipe
from app.core.dashboard.layout import validate_layout
from app.core.dashboard.templates import (
    DEFAULT_DASHBOARD_TEMPLATE_ID,
    DashboardTemplateError,
    get_dashboard_template,
    list_dashboard_templates,
)
from app.core.eda.findings import detect_findings
from app.core.eda.report import generate_eda_report
from app.core.infographics import InfographicError, build_infographic, catalog as infographic_catalog, parse_pasted_table
from app.core.enterprise.readiness import capability_catalog, readiness_summary
from app.core.enterprise.bi_architecture import enterprise_bi_blueprint, senior_bi_capability_matrix
from app.core.enterprise.staff_control import build_staff_plan, staff_control_center
from app.core.quality.health_improvement import build_health_improvement_plan
from app.core.enterprise.workspace import (
    ASSET_STATUSES,
    ASSET_TYPES,
    WorkspaceValidationError,
    validate_asset_definition,
    validate_workspace_assets,
)
from app.core.kpi.calculator import calculate_kpi
from app.orchestration.quality_pipeline import QualityPipeline
from app.core.statistics.report import generate_statistics_report
from app.core.sql.workbench import (
    SQLWorkbenchError,
    execute_dataset_sql,
    execute_sqlite_database_bytes,
    run_sql_proficiency_benchmark,
    validate_read_only_sql,
)
from app.core.transformation.pipeline import execute_pipeline
from app.core.visualization.recommender import recommend_charts
from app.errors import AppError
from app.models.all import AnalysisRun, Artifact, AuditEvent, Dataset, DatasetVersion, AutomationRun, WorkspaceAsset, GeographicBoundary, SecurityPolicy
from app.orchestration.automated_analyst import AutomatedAnalyst
from app.orchestration.platform import (
    PlatformAnalysisError,
    PLATFORM_ANALYSIS_BUDGET,
    _create_version,
    attach_analysis_basis,
    build_analysis_basis,
    jsonable,
    load_current_dataset,
    run_full_platform_analysis,
)
from app.core.intake.importer import DatasetImporter
from app.core.intelligence.common import IntelligenceError, bounded_frame
from app.core.jobs.queue import DurableJobQueue
from app.api.intelligence import router as intelligence_router
from app.api.geographic import router as geographic_router
from app.api.conversation import router as conversation_router
from app.api.bi_readiness import router as bi_readiness_router
from app.api.security import router as security_router
from app.api.jobs import router as jobs_router
from app.api.connectors import router as connectors_router
from app.api.metrics import router as metrics_router
from app.api.integrations import router as integrations_router
from app.api.storage import router as storage_router
from app.core.bi.report_service import build_bi_report
from app.core.bi.export_formats import INTERACTIVE_EXPORT_FORMATS, resolve_export_formats
from app.core.projects.catalog import FLAGSHIP_PROJECT_IDS, get_project_spec, list_flagship_project_specs, list_project_specs
from app.core.projects.fixtures import build_project_fixture
from app.core.projects.workbench import ProjectBuildError, build_project
from app.core.professional.analysis import build_business_analysis, professional_capability_matrix
from app.storage.dataset_storage import DatasetStorage


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = PROJECT_ROOT / "frontend"


_EXPORT_FORMATS = {
    "html": {
        "format": "html",
        "suffix": "BI_Report",
        "extension": "html",
        "media_type": "text/html",
        "open_with": "Web browser",
        "requires_extraction": False,
        "client_ready": True,
    },
    "pdf": {
        "format": "pdf",
        "suffix": "BI_Report",
        "extension": "pdf",
        "media_type": "application/pdf",
        "open_with": "PDF reader",
        "requires_extraction": False,
        "client_ready": True,
    },
    "xlsx": {
        "format": "excel_xlsx",
        "suffix": "BI_Report",
        "extension": "xlsx",
        "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "open_with": "Microsoft Excel",
        "requires_extraction": False,
        "client_ready": True,
    },
    "powerbi": {
        "format": "powerbi_pbip_project",
        "suffix": "PowerBI_PBIP_Project",
        "extension": "zip",
        "client_file_extension": ".pbip",
        "media_type": "application/zip",
        "open_with": "Power BI Desktop",
        "requires_extraction": True,
        "client_ready": True,
        "instructions": "Extract the ZIP and open the root .pbip file in Power BI Desktop; keep all project folders together.",
    },
    "tableau": {
        "format": "tableau_packaged_workbook",
        "suffix": "Tableau_Packaged_Workbook",
        "extension": "twbx",
        "client_file_extension": ".twbx",
        "media_type": "application/zip",
        "open_with": "Tableau Desktop or Tableau Reader",
        "requires_extraction": False,
        "client_ready": True,
        "instructions": "Open the .twbx file directly in Tableau Desktop or Tableau Reader; the data is included.",
    },
    "tableau_twb": {
        "format": "tableau_workbook_source",
        "suffix": "Tableau_Workbook_Source",
        "extension": "twb",
        "client_file_extension": ".twb",
        "media_type": "application/xml",
        "open_with": "Tableau Desktop",
        "requires_extraction": False,
        "requires_companion_data": True,
        "client_ready": False,
        "instructions": "This editable .twb references data.csv; use the .twbx download for a self-contained client handoff.",
    },
}


def _safe_export_stem(display_name: str | None) -> str:
    stem = Path(str(display_name or "dataset")).stem
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_-")
    return (safe[:80] or "dataset")


def _export_descriptor(display_name: str | None, kind: str) -> dict[str, Any]:
    spec = _EXPORT_FORMATS[kind]
    filename = f"{_safe_export_stem(display_name)}_{spec['suffix']}.{spec['extension']}"
    return {
        key: value
        for key, value in {**spec, "filename": filename}.items()
        if key != "suffix"
    }


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
    settings = load_settings(database_url=database_url, storage_root=storage_root)
    if database_url is None:
        database_engine, session_factory = engine, SessionLocal
    else:
        database_engine, session_factory = build_database(database_url)

    # Isolated tests and local development may bootstrap a disposable SQLite
    # database. Production schema changes are owned by Alembic and must run
    # before the web process starts.
    should_bootstrap_schema = database_url is not None or os.getenv("APP_ENV", "development").casefold() != "production"
    if should_bootstrap_schema:
        ensure_additive_local_schema(database_engine)
    configured_storage_root = storage_root or settings.storage_root
    configured_limit = max_upload_bytes if max_upload_bytes is not None else settings.max_upload_bytes
    if configured_limit <= 0:
        raise RuntimeError("MAX_UPLOAD_BYTES must be greater than zero.")
    storage = DatasetStorage(root=str(configured_storage_root), max_upload_bytes=configured_limit)
    importer = DatasetImporter(storage=storage)

    app = FastAPI(title="Automated Data Analyst API", version="1.0.0")
    app.state.engine = database_engine
    app.state.SessionLocal = session_factory
    app.state.storage = storage
    app.state.importer = importer
    app.state.settings = settings
    app.state.rate_limiter = RateLimiter(settings.rate_limit_per_minute)
    app.state.metrics = MetricsRegistry()
    app.state.bi_report_cache = OrderedDict()
    try:
        app.state.bi_report_cache_size = max(1, min(10, int(os.getenv("BI_REPORT_CACHE_SIZE", "3"))))
    except ValueError as exc:
        raise RuntimeError("BI_REPORT_CACHE_SIZE must be an integer.") from exc

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.auth_mode == "disabled" else list(settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Tenant-ID", "X-Workspace-ID", "X-Request-ID", "X-Correlation-ID"],
    )

    @app.middleware("http")
    async def security_and_observability_middleware(request: Request, call_next):
        started = time.perf_counter()
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id
        request.state.correlation_id = request.headers.get("x-correlation-id") or request_id
        public_path = request.url.path in {"/", "/health", "/health/live", "/health/ready"} or request.url.path.startswith("/static") or request.url.path in {"/docs", "/redoc", "/openapi.json"}
        actor = Actor("anonymous", request.headers.get("x-tenant-id", "default"), frozenset({"read"}), frozenset({"read"}), frozenset(), "anonymous")
        if not public_path:
            db = request.app.state.SessionLocal()
            try:
                try:
                    actor = authenticate(request, request.app.state.settings, db)
                except SecurityError as exc:
                    return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}, "request_id": request_id}, headers={"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None)
                limiter_key = f"{actor.tenant_id}:{actor.principal_id}:{request.client.host if request.client else 'unknown'}"
                if not request.app.state.rate_limiter.allow(limiter_key):
                    return JSONResponse(status_code=429, content={"error": {"code": "RATE_LIMITED", "message": "Rate limit exceeded.", "details": {"retry_after_seconds": 60}}, "request_id": request_id}, headers={"Retry-After": "60"})
                dataset_match = re.search(r"/api/v1/datasets/([^/]+)", request.url.path)
                if dataset_match:
                    candidate = db.query(Dataset).filter(Dataset.id == dataset_match.group(1)).first()
                    if candidate is not None and candidate.tenant_id != actor.tenant_id:
                        return JSONResponse(status_code=403, content={"error": {"code": "TENANT_FORBIDDEN", "message": "The dataset belongs to another tenant.", "details": {"dataset_id": candidate.id}}, "request_id": request_id})
            finally:
                db.close()
        request.state.actor = actor
        response = None
        error_code = None
        try:
            response = await call_next(request)
            request.app.state.metrics.record_request(request.method, request.url.path, response.status_code, time.perf_counter() - started)
            return response
        except SecurityError as exc:
            error_code = exc.code
            return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}, "request_id": request_id})
        finally:
            if response is not None:
                response.headers["X-Request-ID"] = request_id
                response.headers["X-Content-Type-Options"] = "nosniff"
                response.headers["X-Frame-Options"] = "DENY"
                response.headers["Referrer-Policy"] = "same-origin"
                response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else response.headers.get("Cache-Control", "")
                if request.url.path.startswith("/api/"):
                    audit_db = request.app.state.SessionLocal()
                    try:
                        audit_request(audit_db, actor=actor, request=request, status_code=response.status_code, duration_ms=(time.perf_counter() - started) * 1000, success=response.status_code < 400, error_code=error_code)
                    except Exception:
                        audit_db.rollback()
                    finally:
                        audit_db.close()

    if FRONTEND_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError):
        return _error_response(exc)

    @app.exception_handler(PlatformAnalysisError)
    async def platform_analysis_error_handler(_: Request, exc: PlatformAnalysisError):
        return JSONResponse(status_code=404 if exc.code in {"DATASET_NOT_FOUND", "VERSION_NOT_FOUND"} else 422, content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}})

    @app.exception_handler(IntelligenceError)
    async def intelligence_error_handler(_: Request, exc: IntelligenceError):
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}})

    @app.exception_handler(ProjectBuildError)
    async def project_build_error_handler(_: Request, exc: ProjectBuildError):
        return JSONResponse(status_code=422, content={"error": {"code": "PROJECT_BUILD_FAILED", "message": exc.message, "details": exc.details}})

    @app.exception_handler(WorkspaceValidationError)
    async def workspace_validation_error_handler(_: Request, exc: WorkspaceValidationError):
        return JSONResponse(status_code=422, content={"error": {"code": "WORKSPACE_ASSET_INVALID", "message": exc.message, "details": exc.details}})

    @app.exception_handler(SQLWorkbenchError)
    async def sql_workbench_error_handler(_: Request, exc: SQLWorkbenchError):
        status_code = 408 if exc.code == "SQL_TIMEOUT" else 422
        return JSONResponse(status_code=status_code, content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}})

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
        carrier: str | None = None,
        origin: str | None = None,
        destination: str | None = None,
        route: str | None = None,
        template_id: str | None = None,
        exports: tuple[str, ...] | None = None,
    ):
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        if dataset is None:
            raise AppError("DATASET_NOT_FOUND", "Dataset was not found.", status_code=404, details={"dataset_id": dataset_id})
        version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.current_version_id).first()
        if version is None:
            raise AppError("VERSION_NOT_FOUND", "Dataset has no current version.", status_code=404, details={"dataset_id": dataset_id})
        try:
            selected_template = get_dashboard_template(template_id)
        except DashboardTemplateError as exc:
            raise AppError(
                "DASHBOARD_TEMPLATE_NOT_FOUND",
                "The requested dashboard template does not exist.",
                status_code=422,
                details={"template_id": template_id, "available_template_ids": [item["id"] for item in list_dashboard_templates()]},
            ) from exc
        selected_exports = resolve_export_formats(exports)
        cache_key = (
            str(dataset_id), str(version.id), selected_template["id"],
            country, product, segment, store, carrier, origin, destination, route,
            tuple(sorted(selected_exports)),
        )
        cache = request.app.state.bi_report_cache
        if cache_key in cache:
            cached = cache.pop(cache_key)
            cache[cache_key] = cached
            return dataset, cached
        try:
            source_frame = pd.read_csv(request.app.state.storage.resolve(version.storage_path))
            actor = getattr(request.state, "actor", Actor("development", dataset.tenant_id, frozenset({"admin"}), frozenset({"*"}), frozenset({"*"})))
            policies = db.query(SecurityPolicy).filter(SecurityPolicy.tenant_id == actor.tenant_id, SecurityPolicy.workspace_id == "default").all()
            source_frame, _ = apply_row_policies(source_frame, policies, actor)
            source_frame, _ = visible_columns(source_frame, policies)
            frame, execution_metadata = bounded_frame(source_frame, budget=PLATFORM_ANALYSIS_BUDGET)
            analysis_basis = build_analysis_basis(execution_metadata)
            report = build_bi_report(
                frame,
                dataset_name=dataset.name,
                source_version_id=version.id,
                filters={
                    "country": country, "product": product, "segment": segment, "store": store,
                    "carrier": carrier, "origin": origin, "destination": destination, "route": route,
                },
                template_id=selected_template["id"],
                output_dir=request.app.state.storage.root / dataset_id / "reports",
                exports=selected_exports,
            )
            attach_analysis_basis(report, analysis_basis)
        except Exception as exc:
            raise AppError(
                "BI_REPORT_FAILED",
                "The BI report could not be generated from this dataset.",
                status_code=422,
                details={"dataset_id": dataset_id, "error": str(exc)},
            ) from exc
        cache[cache_key] = report
        while len(cache) > request.app.state.bi_report_cache_size:
            cache.popitem(last=False)
        return dataset, report

    def public_bi_report(
        dataset_id: str,
        report: dict,
        *,
        country: str | None = None,
        product: str | None = None,
        segment: str | None = None,
        store: str | None = None,
        carrier: str | None = None,
        origin: str | None = None,
        destination: str | None = None,
        route: str | None = None,
    ):
        template_id = (report.get("dashboard", {}).get("template", {}) or {}).get("id", DEFAULT_DASHBOARD_TEMPLATE_ID)
        query = urlencode({key: value for key, value in {
            "country": country,
            "product": product,
            "segment": segment,
            "store": store,
            "carrier": carrier,
            "origin": origin,
            "destination": destination,
            "route": route,
            "template_id": template_id,
        }.items() if value})
        suffix = f"?{query}" if query else ""
        downloads = {
            "html": f"/api/v1/datasets/{dataset_id}/bi_report/html{suffix}",
            "pdf": f"/api/v1/datasets/{dataset_id}/bi_report/pdf{suffix}",
            "xlsx": f"/api/v1/datasets/{dataset_id}/bi_report/xlsx{suffix}",
            "powerbi": f"/api/v1/datasets/{dataset_id}/bi_report/powerbi{suffix}",
            "tableau": f"/api/v1/datasets/{dataset_id}/bi_report/tableau{suffix}",
            "tableau_twb": f"/api/v1/datasets/{dataset_id}/bi_report/tableau_twb{suffix}",
        }
        source_name = (report.get("source") or {}).get("name") or dataset_id
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
            "bi_model": report.get("desktop_export_validation", {}).get("powerbi", {}).get("model_contract"),
            "desktop_export_validation": report.get("desktop_export_validation", {}),
            "analysis_basis": report.get("analysis_basis"),
            "downloads": downloads,
            "download_formats": {
                kind: {**_export_descriptor(source_name, kind), "url": url}
                for kind, url in downloads.items()
            },
        }

    def _infographic_options(payload: dict[str, Any], *, source: str) -> dict[str, Any]:
        allowed = {
            "panel_type",
            "title",
            "subtitle",
            "category_column",
            "measure_column",
            "date_column",
            "aggregation",
            "handle",
            "prefix",
            "suffix",
            "geography_column",
            "boundary_property",
            "country",
            "admin_level",
            "map_palette",
            "map_classification",
            "map_classes",
            "show_labels",
        }
        options = {
            key: value
            for key, value in payload.items()
            if key in allowed and value not in (None, "")
        }
        options["source"] = str(payload.get("source") or source)
        return options

    def _infographic_boundary_record(request: Request, boundary_id: str | None) -> tuple[GeographicBoundary, dict[str, Any]] | None:
        if not boundary_id:
            return None
        db = request.app.state.SessionLocal()
        try:
            boundary = db.query(GeographicBoundary).filter(GeographicBoundary.id == str(boundary_id)).first()
            if boundary is None:
                raise AppError("BOUNDARY_NOT_FOUND", "The selected map boundary was not found.", status_code=404)
            path = request.app.state.storage.resolve(boundary.geometry_file)
            if not path.is_file():
                raise AppError("BOUNDARY_FILE_MISSING", "The selected map boundary geometry is missing.", status_code=500)
            return boundary, json.loads(path.read_text(encoding="utf-8"))
        finally:
            db.close()

    def _infographic_boundary(request: Request, boundary_id: str | None) -> dict[str, Any] | None:
        record = _infographic_boundary_record(request, boundary_id)
        return record[1] if record else None

    def _save_infographic(request: Request, generated: dict[str, Any]) -> dict[str, Any]:
        infographic_id = generated["infographic_id"]
        storage_key = f"infographics/{infographic_id}.png"
        path = request.app.state.storage.resolve(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(generated["png_bytes"])
        source_text = str(generated["spec"].get("source") or "provided data").strip()
        if source_text.casefold().startswith("source:"):
            source_text = source_text.split(":", 1)[1].strip()
        return {
            "infographic_id": infographic_id,
            "generated_at": generated["generated_at"],
            "panel_type": generated["panel_type"],
            "theme": generated["theme"],
            "format": generated["format"],
            "spec": generated["spec"],
            "byte_size": generated["byte_size"],
            "image_url": f"/api/v1/infographics/{infographic_id}.png",
            "download_url": f"/api/v1/infographics/{infographic_id}.png?download=true",
            "suggested_post": f"{generated['spec'].get('title', 'Data update')}\n\nSource: {source_text}",
        }

    def _build_infographic_response(frame: pd.DataFrame, payload: dict[str, Any], request: Request, *, source: str) -> dict[str, Any]:
        work, execution_metadata = bounded_frame(frame, budget=PLATFORM_ANALYSIS_BUDGET)
        options = _infographic_options(payload, source=source)
        boundary_record = _infographic_boundary_record(request, payload.get("boundary_id"))
        boundary = boundary_record[1] if boundary_record else None
        panel_type = str(payload.get("panel_type") or "").strip().casefold()
        if boundary_record and panel_type == "india_map_story" and boundary_record[0].country_code != "IN":
            raise AppError(
                "INDIA_BOUNDARY_REQUIRED",
                "India Map Story only accepts an IN state/UT boundary. A global/international boundary is not valid for this output.",
                status_code=422,
                details={"selected_boundary_country": boundary_record[0].country_code},
            )
        if boundary_record and panel_type == "world_map_story" and boundary_record[0].country_code != "WLD":
            raise AppError(
                "GLOBAL_BOUNDARY_REQUIRED",
                "Global Country Map Story only accepts the WLD country boundary.",
                status_code=422,
                details={"selected_boundary_country": boundary_record[0].country_code},
            )
        if boundary is not None:
            options["boundary_geojson"] = boundary
        try:
            generated = build_infographic(
                work,
                theme_id=payload.get("theme_id"),
                format_id=payload.get("format_id"),
                **options,
            )
        except InfographicError as exc:
            raise AppError(exc.code, exc.message, status_code=422, details=exc.details) from exc
        return {
            **_save_infographic(request, generated),
            "source": source,
            "analysis_basis": build_analysis_basis(execution_metadata),
            "columns": [str(column) for column in work.columns],
        }

    @app.get("/api/v1/infographics/catalog")
    def get_infographic_catalog():
        return infographic_catalog()

    @app.post("/api/v1/datasets/{dataset_id}/infographics")
    def create_dataset_infographic(dataset_id: str, payload: dict[str, Any], request: Request, db: Session = Depends(get_db)):
        dataset, _, frame = load_current_dataset(db, request.app.state.storage, dataset_id)
        return _build_infographic_response(frame, payload or {}, request, source=dataset.name)

    @app.post("/api/v1/infographics/paste")
    def create_pasted_infographic(payload: dict[str, Any], request: Request):
        try:
            frame = parse_pasted_table(str((payload or {}).get("data") or ""))
        except InfographicError as exc:
            raise AppError(exc.code, exc.message, status_code=422, details=exc.details) from exc
        return _build_infographic_response(frame, payload or {}, request, source="data provided by creator")

    @app.get("/api/v1/infographics/{infographic_id}.png")
    def download_infographic(infographic_id: str, request: Request, download: bool = Query(False)):
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", infographic_id):
            raise AppError("INFOGRAPHIC_NOT_FOUND", "The requested infographic does not exist.", status_code=404)
        path = request.app.state.storage.resolve(f"infographics/{infographic_id}.png")
        if not path.is_file():
            raise AppError("INFOGRAPHIC_NOT_FOUND", "The requested infographic does not exist.", status_code=404)
        disposition = "attachment" if download else "inline"
        return FileResponse(
            path,
            media_type="image/png",
            filename=f"data-story-{infographic_id[:8]}.png",
            headers={"Content-Disposition": f'{disposition}; filename="data-story-{infographic_id[:8]}.png"'},
        )

    @app.get("/api/v1/dashboard-templates")
    def get_dashboard_templates():
        """List the presentation templates available for source-backed dashboards."""
        return {
            "default_template_id": DEFAULT_DASHBOARD_TEMPLATE_ID,
            "templates": list_dashboard_templates(),
        }

    @app.get("/api/v1/project-catalog")
    def get_project_catalog():
        """Return three flagship projects plus supporting skill drills."""
        projects = list_project_specs()
        flagships = list_flagship_project_specs()
        return {
            "count": len(projects),
            "flagship_count": len(flagships),
            "flagship_project_ids": list(FLAGSHIP_PROJECT_IDS),
            "flagship_projects": flagships,
            "projects": projects,
        }

    @app.get("/api/v1/project-catalog/{project_id}")
    def get_project_catalog_item(project_id: str):
        try:
            return {"project": next(item for item in list_project_specs() if item["id"] == str(project_id).casefold())}
        except StopIteration as exc:
            raise AppError("PROJECT_NOT_FOUND", "The requested portfolio project does not exist.", status_code=404, details={"project_id": project_id}) from exc

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
        actor = getattr(request.state, "actor", Actor("development", dataset.tenant_id, frozenset({"admin"}), frozenset({"*"}), frozenset({"*"})))
        policies = db.query(SecurityPolicy).filter(SecurityPolicy.tenant_id == actor.tenant_id, SecurityPolicy.workspace_id == "default").all()
        frame, _ = apply_row_policies(frame, policies, actor)
        frame, _ = visible_columns(frame, policies)
        return dataset, version, frame

    def public_workspace_asset(asset: WorkspaceAsset) -> dict[str, Any]:
        return {
            "id": asset.id,
            "workspace_id": asset.workspace_id,
            "asset_type": asset.asset_type,
            "name": asset.name,
            "description": asset.description,
            "dataset_id": asset.dataset_id,
            "definition": asset.definition_json,
            "status": asset.status,
            "owner": asset.owner,
            "tags": asset.tags or [],
            "version": asset.version,
            "created_at": asset.created_at,
            "updated_at": asset.updated_at,
        }

    def get_workspace_asset_or_404(workspace_id: str, asset_id: str, db: Session) -> WorkspaceAsset:
        asset = db.query(WorkspaceAsset).filter(
            WorkspaceAsset.workspace_id == workspace_id,
            WorkspaceAsset.id == asset_id,
        ).first()
        if asset is None:
            raise AppError(
                "WORKSPACE_ASSET_NOT_FOUND",
                "The requested workspace asset was not found.",
                status_code=404,
                details={"workspace_id": workspace_id, "asset_id": asset_id},
            )
        return asset

    def validate_workspace_asset_payload(payload: dict[str, Any], *, existing: WorkspaceAsset | None = None) -> dict[str, Any]:
        asset_type = str(payload.get("asset_type", existing.asset_type if existing else "")).strip().casefold()
        name = str(payload.get("name", existing.name if existing else "")).strip()
        status = str(payload.get("status", existing.status if existing else "draft")).strip().casefold()
        definition = payload.get("definition", existing.definition_json if existing else None)
        if not name:
            raise WorkspaceValidationError("Asset name is required.")
        if asset_type not in ASSET_TYPES:
            raise WorkspaceValidationError("Unsupported workspace asset type.", {"asset_type": asset_type, "allowed": sorted(ASSET_TYPES)})
        if status not in ASSET_STATUSES:
            raise WorkspaceValidationError("Unsupported workspace asset status.", {"status": status, "allowed": sorted(ASSET_STATUSES)})
        validate_asset_definition(asset_type, definition)
        tags = payload.get("tags", existing.tags if existing else [])
        if tags is None:
            tags = []
        if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
            raise WorkspaceValidationError("tags must be a list of non-empty strings.")
        return {
            "asset_type": asset_type,
            "name": name,
            "status": status,
            "definition": definition,
            "tags": list(dict.fromkeys(tag.strip() for tag in tags)),
        }

    @app.get("/api/v1/platform/capabilities")
    def platform_capabilities():
        capabilities = capability_catalog()
        return {"count": len(capabilities), "capabilities": capabilities}

    @app.get("/api/v1/platform/acquisition-readiness")
    def platform_acquisition_readiness():
        return readiness_summary()

    @app.get("/api/v1/platform/production-readiness")
    def platform_production_readiness(request: Request):
        return production_readiness(request.app.state.settings)

    @app.get("/api/v1/platform/professional-capabilities")
    def platform_professional_capabilities():
        """Return the executable analyst/BI acceptance matrix."""
        return professional_capability_matrix()

    @app.get("/api/v1/platform/enterprise-bi-capabilities")
    def platform_enterprise_bi_capabilities():
        """Return the prioritized Senior BI engineering matrix and evidence boundary."""
        return senior_bi_capability_matrix()

    @app.get("/api/v1/platform/enterprise-bi-blueprint")
    def platform_enterprise_bi_blueprint(
        dataset_name: str = Query("Enterprise Analytics", min_length=1, max_length=120),
        source_rows: int = Query(500_000_000, ge=0, le=5_000_000_000),
    ):
        """Generate a reviewable 500M–5B-row BI architecture and deployment contract."""
        return enterprise_bi_blueprint(dataset_name=dataset_name, source_rows=source_rows)

    @app.get("/api/v1/platform/staff-control-center")
    def get_staff_control_center(dataset_id: str | None = Query(None, max_length=120)):
        """Return the unified staff-level automatic/manual operating contract."""
        return staff_control_center(dataset_id=dataset_id)

    @app.post("/api/v1/platform/staff-control-center/plan")
    def create_staff_control_plan(payload: dict[str, Any] | None = None):
        request_payload = payload or {}
        try:
            return build_staff_plan(
                mode=request_payload.get("mode", "automatic"),
                objective=request_payload.get("objective", "Build a trusted, decision-ready data product"),
                dataset_id=request_payload.get("dataset_id"),
                requested_stages=request_payload.get("requested_stages"),
            )
        except ValueError as exc:
            raise AppError("STAFF_CONTROL_PLAN_INVALID", str(exc), status_code=422) from exc

    @app.post("/api/v1/datasets/{dataset_id}/staff-control-center/plan")
    def create_dataset_staff_control_plan(dataset_id: str, payload: dict[str, Any] | None = None):
        request_payload = dict(payload or {})
        request_payload["dataset_id"] = dataset_id
        try:
            return build_staff_plan(
                mode=request_payload.get("mode", "automatic"),
                objective=request_payload.get("objective", "Build a trusted, decision-ready data product"),
                dataset_id=dataset_id,
                requested_stages=request_payload.get("requested_stages"),
            )
        except ValueError as exc:
            raise AppError("STAFF_CONTROL_PLAN_INVALID", str(exc), status_code=422) from exc

    @app.post("/api/v1/sql/validate")
    def validate_sql(payload: dict[str, Any] | None = None):
        return validate_read_only_sql((payload or {}).get("sql"))

    @app.post("/api/v1/sql/proficiency-benchmark")
    def sql_proficiency_benchmark():
        """Execute the 20-question, 90-minute-equivalent SQL acceptance suite."""
        return run_sql_proficiency_benchmark()

    @app.post("/api/v1/datasets/{dataset_id}/sql/query")
    def query_dataset_with_sql(
        dataset_id: str,
        payload: dict[str, Any] | None = None,
        request: Request = None,
        db: Session = Depends(get_db),
    ):
        body = payload or {}
        dataset, version, frame = load_dataset_frame(dataset_id, request, db, body.get("version_id"))
        try:
            max_rows = int(body.get("max_rows", 500))
            timeout_seconds = float(body.get("timeout_seconds", 5.0))
        except (TypeError, ValueError) as exc:
            raise SQLWorkbenchError("SQL_OPTIONS_INVALID", "max_rows and timeout_seconds must be numeric.") from exc
        result = execute_dataset_sql(
            frame,
            body.get("sql"),
            max_rows=max_rows,
            timeout_seconds=timeout_seconds,
        )
        return {
            "dataset_id": dataset.id,
            "dataset_name": dataset.name,
            "source_version_id": version.id,
            **result,
        }

    @app.post("/api/v1/datasets/{dataset_id}/business-analysis")
    def analyze_business_question(
        dataset_id: str,
        payload: dict[str, Any] | None = None,
        request: Request = None,
        db: Session = Depends(get_db),
    ):
        body = payload or {}
        dataset, version, frame = load_dataset_frame(dataset_id, request, db, body.get("version_id"))
        return {
            "dataset_id": dataset.id,
            "source_version_id": version.id,
            **build_business_analysis(
                frame,
                dataset_name=dataset.name,
                problem=str(body.get("problem") or "").strip() or None,
            ),
        }

    @app.post("/api/v1/sql/database-query")
    async def query_uploaded_sqlite_database(
        file: UploadFile = File(...),
        sql: str = Form(...),
        max_rows: int = Form(500),
        timeout_seconds: float = Form(5.0),
    ):
        """Run one bounded read-only query against a new multi-table SQLite database."""
        database = await file.read(configured_limit + 1)
        if len(database) > configured_limit:
            raise AppError(
                "UPLOAD_TOO_LARGE",
                f"Database exceeds the {configured_limit:,}-byte upload limit.",
                status_code=413,
            )
        result = execute_sqlite_database_bytes(
            database,
            sql,
            max_rows=max_rows,
            timeout_seconds=timeout_seconds,
        )
        return {"database_name": file.filename or "uploaded.sqlite", **result}

    @app.get("/api/v1/workspaces/{workspace_id}/assets")
    def list_workspace_assets(
        workspace_id: str,
        asset_type: Optional[str] = Query(None),
        status: Optional[str] = Query(None),
        db: Session = Depends(get_db),
    ):
        query = db.query(WorkspaceAsset).filter(WorkspaceAsset.workspace_id == workspace_id)
        if asset_type:
            query = query.filter(WorkspaceAsset.asset_type == asset_type.casefold())
        if status:
            query = query.filter(WorkspaceAsset.status == status.casefold())
        assets = query.order_by(WorkspaceAsset.asset_type.asc(), WorkspaceAsset.name.asc()).all()
        return {"workspace_id": workspace_id, "count": len(assets), "assets": [public_workspace_asset(asset) for asset in assets]}

    @app.post("/api/v1/workspaces/{workspace_id}/assets", status_code=201)
    def create_workspace_asset(workspace_id: str, payload: dict[str, Any], db: Session = Depends(get_db)):
        normalized = validate_workspace_asset_payload(payload)
        if normalized["status"] == "published":
            raise WorkspaceValidationError("Use the publish endpoint to publish a governed asset.")
        dataset_id = payload.get("dataset_id")
        if dataset_id and db.query(Dataset).filter(Dataset.id == str(dataset_id)).first() is None:
            raise AppError("DATASET_NOT_FOUND", "Dataset was not found.", status_code=404, details={"dataset_id": dataset_id})
        asset = WorkspaceAsset(
            workspace_id=workspace_id,
            asset_type=normalized["asset_type"],
            name=normalized["name"],
            description=payload.get("description"),
            dataset_id=str(dataset_id) if dataset_id else None,
            definition_json=normalized["definition"],
            status=normalized["status"],
            owner=payload.get("owner"),
            tags=normalized["tags"],
        )
        db.add(asset)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise AppError(
                "WORKSPACE_ASSET_CONFLICT",
                "An asset with this name and type already exists in the workspace.",
                status_code=409,
                details={"workspace_id": workspace_id, "asset_type": normalized["asset_type"], "name": normalized["name"]},
            ) from exc
        db.refresh(asset)
        return public_workspace_asset(asset)

    @app.get("/api/v1/workspaces/{workspace_id}/assets/{asset_id}")
    def get_workspace_asset(workspace_id: str, asset_id: str, db: Session = Depends(get_db)):
        return public_workspace_asset(get_workspace_asset_or_404(workspace_id, asset_id, db))

    @app.put("/api/v1/workspaces/{workspace_id}/assets/{asset_id}")
    def update_workspace_asset(workspace_id: str, asset_id: str, payload: dict[str, Any], db: Session = Depends(get_db)):
        asset = get_workspace_asset_or_404(workspace_id, asset_id, db)
        normalized = validate_workspace_asset_payload(payload, existing=asset)
        if normalized["status"] == "published" and asset.status != "published":
            raise WorkspaceValidationError("Use the publish endpoint to publish a governed asset.")
        dataset_id = payload.get("dataset_id", asset.dataset_id)
        if dataset_id and db.query(Dataset).filter(Dataset.id == str(dataset_id)).first() is None:
            raise AppError("DATASET_NOT_FOUND", "Dataset was not found.", status_code=404, details={"dataset_id": dataset_id})
        governed_content_changed = any((
            normalized["asset_type"] != asset.asset_type,
            normalized["name"] != asset.name,
            (str(dataset_id) if dataset_id else None) != asset.dataset_id,
            normalized["definition"] != asset.definition_json,
        ))
        asset.asset_type = normalized["asset_type"]
        asset.name = normalized["name"]
        asset.description = payload.get("description", asset.description)
        asset.dataset_id = str(dataset_id) if dataset_id else None
        asset.definition_json = normalized["definition"]
        asset.status = "draft" if asset.status == "published" and governed_content_changed else normalized["status"]
        asset.owner = payload.get("owner", asset.owner)
        asset.tags = normalized["tags"]
        asset.version += 1
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise AppError("WORKSPACE_ASSET_CONFLICT", "An asset with this name and type already exists in the workspace.", status_code=409) from exc
        db.refresh(asset)
        return public_workspace_asset(asset)

    @app.post("/api/v1/workspaces/{workspace_id}/assets/{asset_id}/publish")
    def publish_workspace_asset(workspace_id: str, asset_id: str, db: Session = Depends(get_db)):
        asset = get_workspace_asset_or_404(workspace_id, asset_id, db)
        validate_asset_definition(asset.asset_type, asset.definition_json)
        workspace_assets = db.query(WorkspaceAsset).filter(WorkspaceAsset.workspace_id == workspace_id).all()
        validation = validate_workspace_assets(workspace_assets)
        target_errors = [item for item in validation["unresolved_dependencies"] if item["asset_id"] == asset.id]
        if target_errors:
            raise WorkspaceValidationError("The asset has unresolved dependencies and cannot be published.", {"unresolved_dependencies": target_errors})
        asset_by_id = {item.id: item for item in workspace_assets}
        unpublished_dependencies = [
            {"asset_id": dependency.id, "name": dependency.name, "status": dependency.status}
            for dependency_id in (asset.definition_json or {}).get("depends_on", [])
            if (dependency := asset_by_id.get(dependency_id)) is not None and dependency.status != "published"
        ]
        if unpublished_dependencies:
            raise WorkspaceValidationError(
                "All dependencies must be published before this asset can be published.",
                {"unpublished_dependencies": unpublished_dependencies},
            )
        asset.status = "published"
        asset.version += 1
        db.commit()
        db.refresh(asset)
        return {"asset": public_workspace_asset(asset), "validation": validation}

    @app.get("/api/v1/workspaces/{workspace_id}/lineage")
    def get_workspace_lineage(workspace_id: str, db: Session = Depends(get_db)):
        assets = db.query(WorkspaceAsset).filter(WorkspaceAsset.workspace_id == workspace_id).order_by(WorkspaceAsset.created_at.asc()).all()
        nodes = [
            {"id": asset.id, "kind": "asset", "asset_type": asset.asset_type, "name": asset.name, "status": asset.status}
            for asset in assets
        ]
        dataset_ids = sorted({asset.dataset_id for asset in assets if asset.dataset_id})
        datasets = db.query(Dataset).filter(Dataset.id.in_(dataset_ids)).all() if dataset_ids else []
        nodes.extend({"id": item.id, "kind": "dataset", "name": item.name} for item in datasets)
        edges = []
        for asset in assets:
            if asset.dataset_id:
                edges.append({"source": asset.dataset_id, "target": asset.id, "relationship": "source_dataset"})
            for dependency in (asset.definition_json or {}).get("depends_on", []):
                edges.append({"source": dependency, "target": asset.id, "relationship": "depends_on"})
        return {"workspace_id": workspace_id, "nodes": nodes, "edges": edges, "validation": validate_workspace_assets(assets)}

    @app.post("/api/v1/workspaces/{workspace_id}/validate")
    def validate_workspace(workspace_id: str, db: Session = Depends(get_db)):
        assets = db.query(WorkspaceAsset).filter(WorkspaceAsset.workspace_id == workspace_id).all()
        return {"workspace_id": workspace_id, **validate_workspace_assets(assets)}

    def public_project_result(result: dict[str, Any], *, project_id: str) -> dict[str, Any]:
        """Remove binary/HTML payloads while keeping an API-sized build result."""
        artifacts = {
            kind: f"/api/v1/projects/{project_id}/artifacts/{kind}"
            for kind in ("html", "pdf", "xlsx", "powerbi", "tableau", "tableau_twb")
            if result.get("files", {}).get(kind)
        }
        return {
            "project": result["project"],
            "status": result["status"],
            "analytics": result["analytics"],
            "kpis": result["kpis"],
            "charts": _remove_artifact_paths(result["charts"]),
            "tables": result["tables"],
            "findings": result.get("findings", []),
            "dashboard": _remove_artifact_paths(result["dashboard"]),
            "validation": result["validation"],
            "bi_model": result.get("desktop_export_validation", {}).get("powerbi", {}).get("model_contract"),
            "desktop_export_validation": result.get("desktop_export_validation", {}),
            "artifacts": artifacts,
            "artifact_formats": {
                kind: {**_export_descriptor(project_id, kind), "url": url}
                for kind, url in artifacts.items()
            },
        }

    @app.post("/api/v1/projects/{project_id}/build")
    def build_portfolio_project(
        project_id: str,
        payload: dict[str, Any] | None = None,
        request: Request = None,
        db: Session = Depends(get_db),
    ):
        body = payload or {}
        try:
            spec = get_project_spec(project_id)
        except KeyError as exc:
            raise AppError("PROJECT_NOT_FOUND", "The requested portfolio project does not exist.", status_code=404, details={"project_id": project_id}) from exc
        selected_template = body.get("template_id") or spec.template_id
        dataset_id = body.get("dataset_id")
        if dataset_id:
            dataset, version, frame = load_dataset_frame(str(dataset_id), request, db, body.get("version_id"))
            source_metadata = {"dataset_id": dataset.id, "source_version_id": version.id, "source_name": dataset.name}
        else:
            try:
                rows = max(12, min(int(body.get("rows", 48)), 5000))
            except (TypeError, ValueError) as exc:
                raise AppError("PROJECT_ROWS_INVALID", "rows must be an integer between 12 and 5000.", status_code=422) from exc
            frame = build_project_fixture(spec.id, rows=rows)
            source_metadata = {"source_name": "synthetic_validation_fixture", "source_version_id": None}
        result = build_project(
            frame,
            project_id=spec.id,
            template_id=selected_template,
            output_dir=request.app.state.storage.root / "project_builds" / spec.id,
        )
        response = public_project_result(result, project_id=spec.id)
        response["source"] = source_metadata
        return response

    @app.post("/api/v1/project-validation/run")
    def run_project_validation(payload: dict[str, Any] | None = None, request: Request = None):
        body = payload or {}
        requested = body.get("project_ids") or [item["id"] for item in list_project_specs()]
        try:
            rows = max(12, min(int(body.get("rows", 48)), 5000))
        except (TypeError, ValueError) as exc:
            raise AppError("PROJECT_ROWS_INVALID", "rows must be an integer between 12 and 5000.", status_code=422) from exc
        results, failures = [], []
        for project_id in requested:
            try:
                spec = get_project_spec(str(project_id))
                result = build_project(
                    build_project_fixture(spec.id, rows=rows),
                    project_id=spec.id,
                    template_id=body.get("template_id") or spec.template_id,
                    output_dir=request.app.state.storage.root / "project_builds" / spec.id,
                )
                results.append(public_project_result(result, project_id=spec.id))
            except Exception as exc:
                failures.append({"project_id": str(project_id), "error": str(exc)})
        return {
            "status": "COMPLETED" if not failures else "PARTIAL",
            "count": len(results) + len(failures),
            "passed": len(results),
            "failed": len(failures),
            "projects": results,
            "failures": failures,
        }

    @app.get("/api/v1/projects/{project_id}/artifacts/{kind}")
    def download_project_artifact(project_id: str, kind: str, request: Request):
        try:
            spec = get_project_spec(project_id)
        except KeyError as exc:
            raise AppError("PROJECT_NOT_FOUND", "The requested portfolio project does not exist.", status_code=404, details={"project_id": project_id}) from exc
        artifact_names = {
            "html": "report.html",
            "pdf": "report.pdf",
            "xlsx": "report.xlsx",
            "powerbi": "report.pbip.zip",
            "tableau": "report.twbx",
            "tableau_twb": "report.twb",
        }
        if kind not in artifact_names:
            raise AppError("ARTIFACT_NOT_FOUND", "Supported artifacts are html, pdf, xlsx, powerbi, tableau, and tableau_twb.", status_code=404, details={"kind": kind})
        path = request.app.state.storage.root / "project_builds" / spec.id / artifact_names[kind]
        if not path.is_file():
            raise AppError("ARTIFACT_NOT_FOUND", "Build the project before downloading its artifact.", status_code=404, details={"project_id": spec.id, "kind": kind})
        descriptor = _export_descriptor(spec.id, kind)
        return FileResponse(
            path,
            media_type=descriptor["media_type"],
            filename=descriptor["filename"],
            headers={
                "X-BI-Format": descriptor["format"],
                "X-BI-Requires-Extraction": str(descriptor["requires_extraction"]).lower(),
            },
        )

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

    @app.get("/metrics")
    def metrics_endpoint(request: Request):
        if request.app.state.settings.auth_mode != "disabled":
            actor = getattr(request.state, "actor", None)
            if actor is None or not actor.is_admin:
                raise SecurityError("FORBIDDEN", "Metrics require an administrator.", status_code=403)
        return Response(content=request.app.state.metrics.render_prometheus(), media_type="text/plain; version=0.0.4")

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
        actor = getattr(request.state, "actor", None)
        return await request.app.state.importer.import_file(db=db, upload=file, tenant_id=getattr(actor, "tenant_id", "default"), **kwargs)

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
        carrier: Optional[str] = Query(None),
        origin: Optional[str] = Query(None),
        destination: Optional[str] = Query(None),
        route: Optional[str] = Query(None),
        template_id: Optional[str] = Query(None),
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
            carrier=carrier,
            origin=origin,
            destination=destination,
            route=route,
            template_id=template_id,
            exports=INTERACTIVE_EXPORT_FORMATS,
        )
        return public_bi_report(
            dataset_id, report,
            country=country, product=product, segment=segment, store=store,
            carrier=carrier, origin=origin, destination=destination, route=route,
        )

    def download_bi_report(
        dataset_id: str,
        request: Request,
        db: Session,
        kind: str,
        country: str | None,
        product: str | None,
        segment: str | None,
        store: str | None,
        carrier: str | None,
        origin: str | None,
        destination: str | None,
        route: str | None,
        template_id: str | None,
    ):
        dataset, report = build_dataset_bi_report(
            dataset_id,
            request,
            db,
            country=country,
            product=product,
            segment=segment,
            store=store,
            carrier=carrier,
            origin=origin,
            destination=destination,
            route=route,
            template_id=template_id,
            exports=(kind,),
        )
        path = report["files"].get(kind)
        if not path:
            raise AppError("BI_REPORT_FILE_MISSING", "The requested BI report file was not created.", status_code=500, details={"format": kind})
        descriptor = _export_descriptor(dataset.name, kind)
        return FileResponse(
            path,
            media_type=descriptor["media_type"],
            filename=descriptor["filename"],
            headers={
                "X-BI-Format": descriptor["format"],
                "X-BI-Requires-Extraction": str(descriptor["requires_extraction"]).lower(),
            },
        )

    @app.get("/api/v1/datasets/{dataset_id}/bi_report/html")
    def download_bi_report_html(
        dataset_id: str,
        request: Request,
        country: Optional[str] = Query(None),
        product: Optional[str] = Query(None),
        segment: Optional[str] = Query(None),
        store: Optional[str] = Query(None),
        carrier: Optional[str] = Query(None),
        origin: Optional[str] = Query(None),
        destination: Optional[str] = Query(None),
        route: Optional[str] = Query(None),
        template_id: Optional[str] = Query(None),
        db: Session = Depends(get_db),
    ):
        return download_bi_report(dataset_id, request, db, "html", country, product, segment, store, carrier, origin, destination, route, template_id)

    @app.get("/api/v1/datasets/{dataset_id}/bi_report/pdf")
    def download_bi_report_pdf(
        dataset_id: str,
        request: Request,
        country: Optional[str] = Query(None),
        product: Optional[str] = Query(None),
        segment: Optional[str] = Query(None),
        store: Optional[str] = Query(None),
        carrier: Optional[str] = Query(None),
        origin: Optional[str] = Query(None),
        destination: Optional[str] = Query(None),
        route: Optional[str] = Query(None),
        template_id: Optional[str] = Query(None),
        db: Session = Depends(get_db),
    ):
        return download_bi_report(dataset_id, request, db, "pdf", country, product, segment, store, carrier, origin, destination, route, template_id)

    @app.get("/api/v1/datasets/{dataset_id}/bi_report/xlsx")
    def download_bi_report_xlsx(
        dataset_id: str,
        request: Request,
        country: Optional[str] = Query(None),
        product: Optional[str] = Query(None),
        segment: Optional[str] = Query(None),
        store: Optional[str] = Query(None),
        carrier: Optional[str] = Query(None),
        origin: Optional[str] = Query(None),
        destination: Optional[str] = Query(None),
        route: Optional[str] = Query(None),
        template_id: Optional[str] = Query(None),
        db: Session = Depends(get_db),
    ):
        return download_bi_report(dataset_id, request, db, "xlsx", country, product, segment, store, carrier, origin, destination, route, template_id)

    @app.get("/api/v1/datasets/{dataset_id}/bi_report/powerbi")
    def download_bi_report_powerbi(
        dataset_id: str,
        request: Request,
        country: Optional[str] = Query(None),
        product: Optional[str] = Query(None),
        segment: Optional[str] = Query(None),
        store: Optional[str] = Query(None),
        carrier: Optional[str] = Query(None),
        origin: Optional[str] = Query(None),
        destination: Optional[str] = Query(None),
        route: Optional[str] = Query(None),
        template_id: Optional[str] = Query(None),
        db: Session = Depends(get_db),
    ):
        return download_bi_report(dataset_id, request, db, "powerbi", country, product, segment, store, carrier, origin, destination, route, template_id)

    @app.get("/api/v1/datasets/{dataset_id}/bi_report/tableau")
    def download_bi_report_tableau(
        dataset_id: str,
        request: Request,
        country: Optional[str] = Query(None),
        product: Optional[str] = Query(None),
        segment: Optional[str] = Query(None),
        store: Optional[str] = Query(None),
        carrier: Optional[str] = Query(None),
        origin: Optional[str] = Query(None),
        destination: Optional[str] = Query(None),
        route: Optional[str] = Query(None),
        template_id: Optional[str] = Query(None),
        db: Session = Depends(get_db),
    ):
        return download_bi_report(dataset_id, request, db, "tableau", country, product, segment, store, carrier, origin, destination, route, template_id)

    @app.get("/api/v1/datasets/{dataset_id}/bi_report/tableau_twb")
    def download_bi_report_tableau_twb(
        dataset_id: str,
        request: Request,
        country: Optional[str] = Query(None),
        product: Optional[str] = Query(None),
        segment: Optional[str] = Query(None),
        store: Optional[str] = Query(None),
        carrier: Optional[str] = Query(None),
        origin: Optional[str] = Query(None),
        destination: Optional[str] = Query(None),
        route: Optional[str] = Query(None),
        template_id: Optional[str] = Query(None),
        db: Session = Depends(get_db),
    ):
        return download_bi_report(dataset_id, request, db, "tableau_twb", country, product, segment, store, carrier, origin, destination, route, template_id)

    @app.post("/api/v1/automated-analyst/analyze")
    def analyze_dataset(payload: dict[str, Any] | None = None, request: Request = None, db: Session = Depends(get_db)):
        dataset_id = (payload or {}).get("dataset_id")
        if not dataset_id:
            raise AppError("DATASET_ID_REQUIRED", "dataset_id is required.", status_code=422)
        analyst = AutomatedAnalyst(db=db, storage=request.app.state.storage)
        return analyst.run_full_pipeline(dataset_id=dataset_id)

    @app.get("/api/v1/reports/{dataset_id}")
    def get_standard_report(dataset_id: str, request: Request, country: Optional[str] = Query(None), product: Optional[str] = Query(None), segment: Optional[str] = Query(None), store: Optional[str] = Query(None), template_id: Optional[str] = Query(None), db: Session = Depends(get_db)):
        _, report = build_dataset_bi_report(
            dataset_id,
            request,
            db,
            country=country,
            product=product,
            segment=segment,
            store=store,
            template_id=template_id,
            exports=INTERACTIVE_EXPORT_FORMATS,
        )
        return public_bi_report(dataset_id, report, country=country, product=product, segment=segment, store=store)

    @app.get("/api/v1/dashboards/{dataset_id}")
    def get_standard_dashboard(dataset_id: str, request: Request, country: Optional[str] = Query(None), product: Optional[str] = Query(None), segment: Optional[str] = Query(None), store: Optional[str] = Query(None), template_id: Optional[str] = Query(None), db: Session = Depends(get_db)):
        _, report = build_dataset_bi_report(dataset_id, request, db, country=country, product=product, segment=segment, store=store, template_id=template_id)
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
    def get_dataset_lineage(dataset_id: str, request: Request, db: Session = Depends(get_db)):
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        if dataset is None:
            raise AppError("DATASET_NOT_FOUND", "Dataset was not found.", status_code=404, details={"dataset_id": dataset_id})
        audits = db.query(AuditEvent).filter(AuditEvent.dataset_id == dataset_id).order_by(AuditEvent.timestamp.asc()).all()
        artifacts = db.query(Artifact).filter(Artifact.dataset_id == dataset_id).order_by(Artifact.created_at.asc()).all()
        versions = db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset_id).order_by(DatasetVersion.version_number.asc()).all()
        return {
            "dataset_id": dataset_id,
            "dataset_name": dataset.name,
            "current_version_id": dataset.current_version_id,
            "events": [{"id": event.id, "engine": event.engine, "input_version_id": event.input_version_id, "output_version_id": event.output_version_id, "parameters": event.parameters, "affected_rows": event.affected_rows, "affected_columns": event.affected_columns, "before_stats": event.before_stats, "after_stats": event.after_stats, "timestamp": event.timestamp} for event in audits],
            "artifacts": [{"id": artifact.id, "type": artifact.artifact_type, "source_version_id": artifact.source_version_id, "storage_path": artifact.storage_path, "sha256": artifact.sha256, "size": artifact.size, "metadata": artifact.metadata_json, "created_at": artifact.created_at} for artifact in artifacts],
            "column_lineage": build_column_lineage(versions, audits, artifacts, request.app.state.storage),
        }

    @app.post("/api/v1/datasets/{dataset_id}/quality/analyze")
    def analyze_quality(dataset_id: str, payload: dict[str, Any] | None = None, request: Request = None, db: Session = Depends(get_db)):
        _, version, frame = load_dataset_frame(dataset_id, request, db, (payload or {}).get("version_id"))
        rows = frame.astype(object).where(pd.notna(frame), None).to_dict(orient="records")
        result = QualityPipeline().analyze(rows, dataset_id=dataset_id, source_version_id=version.id, rules=(payload or {}).get("rules"), columns=(payload or {}).get("columns"))
        return result

    @app.post("/api/v1/datasets/{dataset_id}/quality/improvement-plan")
    def quality_improvement_plan(dataset_id: str, payload: dict[str, Any] | None = None, request: Request = None, db: Session = Depends(get_db)):
        _, version, frame = load_dataset_frame(dataset_id, request, db, (payload or {}).get("version_id"))
        try:
            return build_health_improvement_plan(frame, dataset_id=dataset_id, source_version_id=version.id)
        except Exception as exc:
            raise AppError("QUALITY_IMPROVEMENT_FAILED", str(exc), status_code=422) from exc

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

    class DummyRequest:
        def __init__(self, app_instance):
            self.app = app_instance

    def _execute_automation_run(run_id: str):
        db = app.state.SessionLocal()
        try:
            run = db.query(AutomationRun).filter(AutomationRun.id == run_id).first()
            if not run: return

            # Intelligence jobs use the same durable worker entrypoint as
            # core automation jobs when the production worker is enabled.
            if str(run.action or "").startswith("intelligence."):
                db.close()
                from app.api.intelligence import _execute_background_job
                _execute_background_job(app, run.id, raise_on_error=True)
                return
            
            run.status = "RUNNING"
            run.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
            
            req = DummyRequest(app)
            req.state = type("WorkerState", (), {})()
            req.state.actor = Actor(
                "durable-worker",
                run.tenant_id or "default",
                frozenset({"owner", "admin"}),
                frozenset({"*"}),
                frozenset({"*"}),
                "worker",
            )
            action = run.action
            action_payload = run.parameters.get("payload") or {}
            context = run.parameters.get("context") or {}
            dataset_id = context.get("dataset_id")
            
            if action == "analyst.analyze":
                AutomatedAnalyst(db=db, storage=req.app.state.storage).run_full_pipeline(dataset_id)
            elif action == "bi.report":
                build_dataset_bi_report(dataset_id, req, db, country=action_payload.get("country"), product=action_payload.get("product"), segment=action_payload.get("segment"))
            elif action == "quality.analyze":
                analyze_quality(dataset_id, action_payload, req, db)
            elif action == "cleaning.preview":
                cleaning_operation(dataset_id, action_payload, req, db, False)
            elif action == "cleaning.apply":
                cleaning_operation(dataset_id, action_payload, req, db, True)
            elif action == "transformation.preview":
                transformation_operation(dataset_id, action_payload, req, db, False)
            elif action == "transformation.apply":
                transformation_operation(dataset_id, action_payload, req, db, True)
            elif action == "statistics.summary":
                statistics_summary(dataset_id, action_payload, req, db)
            elif action == "eda.report":
                eda_report(dataset_id, action_payload, req, db)
            elif action == "charts.recommend":
                recommend_visualizations(dataset_id, action_payload, req, db)
            elif action == "kpi.calculate":
                calculate_dataset_kpi(dataset_id, action_payload, req, db)
            elif action == "dashboard.validate":
                validate_dashboard(context.get("dashboard_id"), action_payload)
            elif action == "report.pdf":
                generate_report_file(action_payload, "pdf")
            elif action == "report.excel":
                generate_report_file(action_payload, "xlsx")
            else:
                raise Exception(f"Action '{action}' is not supported in background execution.")
            
            run.status = "COMPLETED"
            run.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
        except Exception as exc:
            db.rollback()
            run.status = "FAILED"
            run.error_details = {"error": str(exc)}
            run.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
            raise
        finally:
            db.close()

    app.state.execute_automation_run = _execute_automation_run

    @app.post("/api/v1/automation/actions")
    @app.post("/api/v1/automation/execute")
    def automation_execute(payload: dict[str, Any], request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
        body = payload or {}
        action = body.get("action")
        context = body.get("context") or {}
        action_payload = body.get("payload") or {}
        idempotency_key = body.get("idempotency_key")
        correlation_id = body.get("correlation_id")
        actor = getattr(request.state, "actor", None)
        tenant_id = actor.tenant_id if actor is not None else "default"
        
        if idempotency_key:
            existing = db.query(AutomationRun).filter(AutomationRun.tenant_id == tenant_id, AutomationRun.idempotency_key == idempotency_key).first()
            if existing:
                return {"run_id": existing.id, "status": existing.status, "message": "Returned existing run due to idempotency_key."}

        try:
            dispatch_plan(action, context, action_payload)
        except Exception as exc:
            raise AppError("AUTOMATION_ACTION_NOT_ALLOWED", getattr(exc, "message", str(exc)), status_code=422, details=getattr(exc, "details", {})) from exc

        if request.app.state.settings.job_worker_enabled:
            durable_payload = dict(action_payload)
            if correlation_id:
                durable_payload.setdefault("correlation_id", correlation_id)
            queued = DurableJobQueue(request.app.state.SessionLocal, worker_id=request.app.state.settings.worker_id).enqueue(
                tenant_id=tenant_id,
                action=action,
                context=context,
                payload=durable_payload,
                idempotency_key=idempotency_key,
                max_attempts=int(body.get("max_attempts", 3)),
            )
            return {"run_id": queued.id, "status": queued.status, "durable": True}

        run = AutomationRun(
            tenant_id=tenant_id,
            dataset_id=context.get("dataset_id", "GLOBAL"),
            source_version_id=context.get("version_id"),
            action=action,
            parameters=payload,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            status="QUEUED"
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        background_tasks.add_task(_execute_automation_run, run.id)
        
        return {"run_id": run.id, "status": run.status}

    @app.get("/api/v1/automation/runs/{run_id}")
    def get_automation_run_status(run_id: str, request: Request, db: Session = Depends(get_db)):
        actor = getattr(request.state, "actor", None)
        run_query = db.query(AutomationRun).filter(AutomationRun.id == run_id)
        if actor is not None:
            run_query = run_query.filter(AutomationRun.tenant_id == actor.tenant_id)
        run = run_query.first()
        if not run:
            raise AppError("RUN_NOT_FOUND", "Automation run not found", status_code=404)
        return {
            "run_id": run.id,
            "action": run.action,
            "status": run.status,
            "created_at": run.created_at,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "error_details": run.error_details,
            "correlation_id": run.correlation_id
        }

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

    app.include_router(intelligence_router)
    app.include_router(geographic_router)
    app.include_router(conversation_router)
    app.include_router(bi_readiness_router)
    app.include_router(security_router)
    app.include_router(jobs_router)
    app.include_router(metrics_router)
    app.include_router(integrations_router)
    app.include_router(storage_router)
    app.include_router(connectors_router)

    return app


app = create_app()
