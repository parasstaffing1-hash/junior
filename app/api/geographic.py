"""Geographic Intelligence API integrated with dataset versions and workspaces."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from urllib.request import Request as UrlRequest, urlopen
from uuid import uuid4

import pandas as pd
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.core.geographic import (
    GeographicError,
    INDIA_OFFICIAL_BOUNDARY_SOURCE,
    build_geographic_map,
    geographic_catalog,
    geographic_eda,
    location_analytics,
    normalize_geo_name,
    recommend_maps,
    resolve_geography,
    validate_feature_collection,
)
from app.core.intelligence.common import ExecutionBudget, bounded_frame
from app.core.security import SecurityError, assert_dataset_tenant, authorize
from app.models.all import Dataset, DatasetVersion, GeographicBoundary, GeographicMapping, WorkspaceAsset


router = APIRouter(prefix="/api/v1", tags=["geographic-intelligence"])
GEO_BUDGET = ExecutionBudget(max_rows=20_000, max_features=250, timeout_seconds=120, random_state=42)
INDIA_BOUNDARY_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_1_states_provinces.geojson"
WORLD_BOUNDARY_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_countries.geojson"
WORLD_BOUNDARY_CODE = "WLD"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _load_dataset(request: Request, dataset_id: str):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise SecurityError("AUTHENTICATION_REQUIRED", "A security principal is required.")
    db = request.app.state.SessionLocal()
    try:
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id, Dataset.tenant_id == actor.tenant_id).first()
        if dataset is None:
            raise GeographicError("DATASET_NOT_FOUND", "Dataset was not found.", {"dataset_id": dataset_id}, status_code=404)
        version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.current_version_id).first()
        if version is None:
            raise GeographicError("VERSION_NOT_FOUND", "Dataset has no current version.", {"dataset_id": dataset_id}, status_code=404)
        frame = pd.read_csv(request.app.state.storage.resolve(version.storage_path))
        bounded, execution = bounded_frame(frame, budget=GEO_BUDGET)
        return dataset, version, bounded, execution
    finally:
        db.close()


def _execution_basis(execution: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_row_count": int(execution["rows_scanned"]),
        "analysis_row_count": int(execution["rows_used"]),
        "sampled": bool(execution["sampled"]),
        "sampling_method": "deterministic_random_sample" if execution["sampled"] else "full_dataset",
        "random_state": GEO_BUDGET.random_state,
    }


def _india_scope_error(boundary: dict[str, Any], requested_country: str) -> GeographicError:
    return GeographicError(
        "INDIA_BOUNDARY_REQUIRED",
        "This dataset is identified as India. Select or import an India-only IN boundary; a global/international boundary cannot be used for an India map.",
        {
            "requested_country": requested_country,
            "selected_boundary_country": boundary.get("country_code"),
            "source": INDIA_OFFICIAL_BOUNDARY_SOURCE,
        },
    )


def _validate_map_boundary_scope(payload: dict[str, Any], boundary: dict[str, Any] | None, frame: pd.DataFrame) -> None:
    if boundary is None:
        return
    profile = geographic_eda(frame)
    requested_country = str(payload.get("country") or profile.get("country_context") or "").strip().upper()
    boundary_country = str(boundary.get("country_code") or "").strip().upper()
    if requested_country == "IN" and boundary_country != "IN":
        raise _india_scope_error(boundary, requested_country)
    if requested_country in {"WLD", "WORLD", "GLOBAL"} and boundary_country != "WLD":
        raise GeographicError(
            "GLOBAL_BOUNDARY_REQUIRED",
            "A global country map requires the registered WLD boundary, not an India-only boundary.",
            {"requested_country": requested_country, "selected_boundary_country": boundary_country},
        )


def _public_boundary(boundary: GeographicBoundary) -> dict[str, Any]:
    return {
        "boundary_id": boundary.id,
        "name": boundary.name,
        "workspace_id": boundary.workspace_id,
        "country_code": boundary.country_code,
        "admin_level": boundary.admin_level,
        "parent_id": boundary.parent_id,
        "source": boundary.source,
        "source_version": boundary.source_version,
        "license": boundary.license,
        "geometry_format": boundary.geometry_format,
        "feature_count": boundary.feature_count,
        "metadata": boundary.metadata_json,
        "updated_at": boundary.updated_at.isoformat() if boundary.updated_at else None,
    }


def _boundary_and_geometry(request: Request, boundary_id: str, *, tenant_id: str | None = None):
    db = request.app.state.SessionLocal()
    try:
        query = db.query(GeographicBoundary).filter(GeographicBoundary.id == boundary_id)
        if tenant_id is not None:
            query = query.filter(GeographicBoundary.tenant_id == tenant_id)
        boundary = query.first()
        if boundary is None:
            raise GeographicError("BOUNDARY_NOT_FOUND", "Boundary was not found.", {"boundary_id": boundary_id}, status_code=404)
        path = request.app.state.storage.resolve(boundary.geometry_file)
        if not path.is_file():
            raise GeographicError("BOUNDARY_FILE_MISSING", "Registered boundary geometry is missing.", {"boundary_id": boundary_id}, status_code=500)
        return _public_boundary(boundary), json.loads(path.read_text(encoding="utf-8"))
    finally:
        db.close()


def _manual_mappings(request: Request, workspace_id: str, country: str | None = None, admin_level: int | None = None, *, tenant_id: str = "default") -> dict[str, dict[str, Any]]:
    db = request.app.state.SessionLocal()
    try:
        query = db.query(GeographicMapping).filter(GeographicMapping.tenant_id == tenant_id, GeographicMapping.workspace_id == workspace_id)
        if country:
            query = query.filter(GeographicMapping.country_code == country.upper())
        if admin_level is not None:
            query = query.filter(GeographicMapping.admin_level == int(admin_level))
        return {
            item.normalized_input: {
                "canonical_name": item.canonical_name,
                "canonical_id": item.canonical_id,
                "country": item.country_code,
                "admin_level": item.admin_level,
            }
            for item in query.all()
        }
    finally:
        db.close()


@router.get("/geographic/catalog")
def get_geographic_catalog():
    return geographic_catalog()


@router.get("/geographic/india/source")
def get_india_boundary_source():
    """Describe the approved India-only boundary source without relabeling fallbacks as official."""
    return INDIA_OFFICIAL_BOUNDARY_SOURCE


@router.post("/datasets/{dataset_id}/geographic/profile")
def profile_geography(dataset_id: str, request: Request):
    dataset, version, frame, execution = _load_dataset(request, dataset_id)
    result = geographic_eda(frame)
    return {"dataset_id": dataset.id, "source_version_id": version.id, "analysis_basis": _execution_basis(execution), **result}


@router.post("/datasets/{dataset_id}/geographic/recommendations")
def map_recommendations(dataset_id: str, request: Request):
    dataset, version, frame, execution = _load_dataset(request, dataset_id)
    return {
        "dataset_id": dataset.id,
        "source_version_id": version.id,
        "analysis_basis": _execution_basis(execution),
        **recommend_maps(frame),
    }


@router.post("/datasets/{dataset_id}/geographic/location-analytics")
def analyze_locations(dataset_id: str, request: Request, payload: dict[str, Any] | None = None):
    dataset, version, frame, execution = _load_dataset(request, dataset_id)
    return {
        "dataset_id": dataset.id,
        "source_version_id": version.id,
        "analysis_basis": _execution_basis(execution),
        **location_analytics(frame, payload),
    }


@router.post("/datasets/{dataset_id}/geographic/maps/preview")
def preview_map(dataset_id: str, request: Request, payload: dict[str, Any] | None = None):
    payload = dict(payload or {})
    actor = getattr(request.state, "actor", None)
    dataset, version, frame, execution = _load_dataset(request, dataset_id)
    boundary_geojson = None
    boundary_metadata = None
    if payload.get("boundary_id"):
        boundary_metadata, boundary_geojson = _boundary_and_geometry(request, str(payload["boundary_id"]), tenant_id=actor.tenant_id)
        _validate_map_boundary_scope(payload, boundary_metadata, frame)
    workspace_id = str(payload.get("workspace_id") or "default")
    mappings = _manual_mappings(request, workspace_id, payload.get("country"), payload.get("admin_level"), tenant_id=actor.tenant_id)
    result = build_geographic_map(frame, payload, boundary_geojson=boundary_geojson, manual_mappings=mappings)
    return {
        "dataset_id": dataset.id,
        "dataset_name": dataset.name,
        "source_version_id": version.id,
        "analysis_basis": _execution_basis(execution),
        "boundary": boundary_metadata,
        **result,
    }


@router.post("/geographic/resolve")
def resolve_names(request: Request, payload: dict[str, Any]):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise SecurityError("AUTHENTICATION_REQUIRED", "A security principal is required.")
    values = payload.get("values")
    if not isinstance(values, list) or not values or len(values) > 1_000:
        raise GeographicError("INVALID_VALUES", "values must contain 1–1,000 geographic names.")
    workspace_id = str(payload.get("workspace_id") or "default")
    authorize(actor, "read", workspace_id=workspace_id)
    country = str(payload.get("country") or "").upper() or None
    admin_level = int(payload["admin_level"]) if payload.get("admin_level") is not None else None
    mappings = _manual_mappings(request, workspace_id, country, admin_level, tenant_id=actor.tenant_id)
    resolved = [resolve_geography(value, country=country, admin_level=admin_level, manual_mappings=mappings) for value in values]
    statuses: dict[str, int] = {}
    for item in resolved:
        statuses[item["status"]] = statuses.get(item["status"], 0) + 1
    return {"workspace_id": workspace_id, "results": resolved, "status_counts": statuses}


@router.post("/geographic/mappings", status_code=201)
def save_mapping(request: Request, payload: dict[str, Any]):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise SecurityError("AUTHENTICATION_REQUIRED", "A security principal is required.")
    required = ("input", "canonical_name", "canonical_id")
    missing = [key for key in required if not str(payload.get(key) or "").strip()]
    if missing:
        raise GeographicError("MAPPING_FIELDS_REQUIRED", "Manual mapping is incomplete.", {"missing": missing})
    workspace_id = str(payload.get("workspace_id") or "default")
    authorize(actor, "write", workspace_id=workspace_id)
    country = str(payload.get("country") or "").upper()
    admin_level = int(payload.get("admin_level") or 0)
    normalized = normalize_geo_name(payload["input"])
    db = request.app.state.SessionLocal()
    try:
        mapping = db.query(GeographicMapping).filter(
            GeographicMapping.tenant_id == actor.tenant_id,
            GeographicMapping.workspace_id == workspace_id,
            GeographicMapping.normalized_input == normalized,
            GeographicMapping.country_code == country,
            GeographicMapping.admin_level == admin_level,
        ).first()
        if mapping is None:
            mapping = GeographicMapping(tenant_id=actor.tenant_id, workspace_id=workspace_id, input_value=str(payload["input"]), normalized_input=normalized, country_code=country, admin_level=admin_level)
            db.add(mapping)
        mapping.canonical_name = str(payload["canonical_name"])
        mapping.canonical_id = str(payload["canonical_id"])
        mapping.method = "manual"
        mapping.confidence = 1.0
        mapping.updated_at = utc_now()
        db.commit()
        return {"mapping_id": mapping.id, "normalized_input": mapping.normalized_input, "canonical_name": mapping.canonical_name, "canonical_id": mapping.canonical_id, "reused_on_refresh": True}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("/geographic/boundaries")
def list_boundaries(request: Request, workspace_id: str = "default", country_code: str | None = None, admin_level: int | None = None):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise SecurityError("AUTHENTICATION_REQUIRED", "A security principal is required.")
    authorize(actor, "read", workspace_id=workspace_id)
    db = request.app.state.SessionLocal()
    try:
        query = db.query(GeographicBoundary).filter(GeographicBoundary.tenant_id == actor.tenant_id, GeographicBoundary.workspace_id == workspace_id)
        if country_code:
            query = query.filter(GeographicBoundary.country_code == country_code.upper())
        if admin_level is not None:
            query = query.filter(GeographicBoundary.admin_level == int(admin_level))
        items = query.order_by(GeographicBoundary.country_code, GeographicBoundary.admin_level, GeographicBoundary.name).all()
        return {"workspace_id": workspace_id, "boundaries": [_public_boundary(item) for item in items]}
    finally:
        db.close()


@router.post("/geographic/boundaries/import", status_code=201)
def import_boundary(request: Request, payload: dict[str, Any]):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise SecurityError("AUTHENTICATION_REQUIRED", "A security principal is required.")
    required = ("name", "country_code", "admin_level", "source", "source_version", "license", "geojson")
    missing = [key for key in required if payload.get(key) in (None, "")]
    if missing:
        raise GeographicError("BOUNDARY_FIELDS_REQUIRED", "Boundary metadata is incomplete.", {"missing": missing})
    try:
        metadata = validate_feature_collection(payload["geojson"])
    except ValueError as exc:
        raise GeographicError("INVALID_BOUNDARY", str(exc)) from exc
    country = str(payload["country_code"]).strip().upper()
    if len(country) not in {2, 3} and country not in {"WLD", "WORLD", "GLOBAL"}:
        raise GeographicError("INVALID_COUNTRY_CODE", "country_code must be a 2- or 3-letter ISO-style code, or WLD for a global boundary.")
    boundary_id = str(uuid4())
    workspace_id = str(payload.get("workspace_id") or "default")
    authorize(actor, "write", workspace_id=workspace_id)
    storage_key = f"geographic/boundaries/{boundary_id}.geojson"
    path = request.app.state.storage.resolve(storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload["geojson"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_bytes(serialized)
    temporary.replace(path)
    db = request.app.state.SessionLocal()
    try:
        boundary = GeographicBoundary(
            id=boundary_id,
            tenant_id=actor.tenant_id,
            workspace_id=workspace_id,
            name=str(payload["name"]).strip(),
            country_code=country,
            admin_level=int(payload["admin_level"]),
            parent_id=payload.get("parent_id"),
            source=str(payload["source"]).strip(),
            source_version=str(payload["source_version"]).strip(),
            license=str(payload["license"]).strip(),
            geometry_file=storage_key,
            geometry_format="GeoJSON",
            feature_count=metadata["feature_count"],
            metadata_json={
                **metadata,
                **{
                    key: payload[key]
                    for key in ("source_type", "source_url", "scope", "jurisdiction", "official")
                    if payload.get(key) not in (None, "")
                },
                "sha256": hashlib.sha256(serialized).hexdigest(),
            },
        )
        db.add(boundary)
        db.commit()
        return _public_boundary(boundary)
    except IntegrityError as exc:
        db.rollback()
        path.unlink(missing_ok=True)
        raise GeographicError("BOUNDARY_VERSION_EXISTS", "This boundary name and source version are already registered.", status_code=409) from exc
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    finally:
        db.close()


@router.post("/geographic/boundaries/import/official-india", status_code=201)
def import_official_india_boundary(request: Request, payload: dict[str, Any]):
    """Import a Survey of India GeoJSON and mark it as the official India source."""
    source = str(payload.get("source") or "").strip()
    source_url = str(payload.get("source_url") or "").strip()
    country = str(payload.get("country_code") or "").strip().upper()
    try:
        admin_level = int(payload.get("admin_level"))
    except (TypeError, ValueError):
        admin_level = -1
    allowed_hosts = ("surveyofindia.gov.in", "onlinemaps.surveyofindia.gov.in")
    if country != "IN":
        raise GeographicError("INDIA_COUNTRY_REQUIRED", "Official India boundary imports must use country_code IN.")
    if admin_level not in {1, 2}:
        raise GeographicError("INDIA_ADMIN_LEVEL_REQUIRED", "Official India boundaries must be state/UT (1) or district/subdistrict (2) level.")
    if source.casefold() != "survey of india":
        raise GeographicError("OFFICIAL_SOURCE_REQUIRED", "The official India import path accepts Survey of India data only.")
    if not source_url or not any(host in source_url.casefold() for host in allowed_hosts):
        raise GeographicError("OFFICIAL_SOURCE_URL_REQUIRED", "Provide the Survey of India catalog or administrative-boundary URL used for this geometry.")
    enriched = {
        **payload,
        "source": "Survey of India",
        "source_type": "official",
        "scope": "india_only",
        "jurisdiction": "Republic of India",
        "official": True,
    }
    return import_boundary(request, enriched)


@router.post("/geographic/boundaries/bootstrap/india", status_code=201)
@router.post("/geographic/boundaries/bootstrap/india-fallback", status_code=201)
def bootstrap_india_boundary(request: Request):
    """Register a clearly labelled India-only demo fallback; it is not official Survey of India data."""
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise SecurityError("AUTHENTICATION_REQUIRED", "A security principal is required.")
    authorize(actor, "write", workspace_id="default")
    try:
        http_request = UrlRequest(INDIA_BOUNDARY_URL, headers={"User-Agent": "AutomatedDataAnalyst/1.0"})
        with urlopen(http_request, timeout=30) as response:  # noqa: S310 - fixed public-domain URL above
            content = response.read(25 * 1024 * 1024 + 1)
        if len(content) > 25 * 1024 * 1024:
            raise ValueError("The boundary source exceeded the 25 MB safety limit.")
        source_geometry = json.loads(content.decode("utf-8"))
        features = [
            feature for feature in source_geometry.get("features", [])
            if str((feature.get("properties") or {}).get("admin") or "").casefold() == "india"
        ]
        if len(features) < 20:
            raise ValueError("The public source did not contain the expected India state features.")
        geometry = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"name": (feature.get("properties") or {}).get("name"), "name_alt": (feature.get("properties") or {}).get("name_alt")}, "geometry": feature.get("geometry")}
            for feature in features
        ]}
    except Exception as exc:
        raise GeographicError("BOUNDARY_BOOTSTRAP_FAILED", f"India boundary download failed: {exc}", status_code=502) from exc
    metadata = validate_feature_collection(geometry)
    payload = {
        "name": "India States - Natural Earth 50m (fallback)",
        "country_code": "IN",
        "admin_level": 1,
        "source": "Natural Earth",
        "source_version": "5.1.2",
        "license": "Public domain - Natural Earth Terms of Use (fallback only; not official India boundary)",
        "geojson": geometry,
    }
    boundary_id = str(uuid4())
    storage_key = f"geographic/boundaries/{boundary_id}.geojson"
    path = request.app.state.storage.resolve(storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(geometry, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_bytes(serialized)
    temporary.replace(path)
    db = request.app.state.SessionLocal()
    try:
        boundary = GeographicBoundary(
            id=boundary_id,
            tenant_id=actor.tenant_id,
            workspace_id="default",
            name=payload["name"],
            country_code="IN",
            admin_level=1,
            source=payload["source"],
            source_version=payload["source_version"],
            license=payload["license"],
            geometry_file=storage_key,
            geometry_format="GeoJSON",
            feature_count=metadata["feature_count"],
            metadata_json={
                **metadata,
                "sha256": hashlib.sha256(serialized).hexdigest(),
                "source_url": INDIA_BOUNDARY_URL,
                "scope": "india_only",
                "source_type": "fallback",
                "official": False,
                "fallback_for": "Survey of India official boundary import",
            },
        )
        db.add(boundary)
        db.commit()
        return _public_boundary(boundary)
    except IntegrityError:
        db.rollback()
        existing = db.query(GeographicBoundary).filter(GeographicBoundary.tenant_id == actor.tenant_id, GeographicBoundary.workspace_id == "default", GeographicBoundary.name == payload["name"], GeographicBoundary.source_version == payload["source_version"]).first()
        path.unlink(missing_ok=True)
        if existing is not None:
            return _public_boundary(existing)
        raise GeographicError("BOUNDARY_VERSION_EXISTS", "The India boundary is already registered.", status_code=409)
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    finally:
        db.close()


@router.post("/geographic/boundaries/bootstrap/world", status_code=201)
@router.post("/geographic/boundaries/bootstrap/global", status_code=201)
def bootstrap_world_boundary(request: Request):
    """Register a public-domain Natural Earth country boundary for global map stories."""
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise SecurityError("AUTHENTICATION_REQUIRED", "A security principal is required.")
    authorize(actor, "write", workspace_id="default")
    try:
        http_request = UrlRequest(WORLD_BOUNDARY_URL, headers={"User-Agent": "AutomatedDataAnalyst/1.0"})
        with urlopen(http_request, timeout=30) as response:  # noqa: S310 - fixed public-domain URL above
            content = response.read(25 * 1024 * 1024 + 1)
        if len(content) > 25 * 1024 * 1024:
            raise ValueError("The boundary source exceeded the 25 MB safety limit.")
        source_geometry = json.loads(content.decode("utf-8"))
        source_features = source_geometry.get("features") or []
        if len(source_features) < 150:
            raise ValueError("The public source did not contain the expected global country features.")
        features = []
        for feature in source_features:
            source_properties = feature.get("properties") or {}
            clean = {
                "name": source_properties.get("NAME") or source_properties.get("ADMIN"),
                "name_long": source_properties.get("NAME_LONG") or source_properties.get("NAME") or source_properties.get("ADMIN"),
                "name_en": source_properties.get("NAME_EN") or source_properties.get("NAME") or source_properties.get("ADMIN"),
                "name_alt": source_properties.get("NAME_ALT"),
                "iso_a2": source_properties.get("ISO_A2") if source_properties.get("ISO_A2") not in (None, "-99", -99) else None,
                "iso_a3": source_properties.get("ISO_A3") if source_properties.get("ISO_A3") not in (None, "-99", -99) else source_properties.get("ADM0_A3"),
                "iso_n3": source_properties.get("ISO_N3") if source_properties.get("ISO_N3") not in (None, "-99", -99) else None,
                "abbrev": source_properties.get("ABBREV"),
                "postal": source_properties.get("POSTAL"),
                "formal_en": source_properties.get("FORMAL_EN"),
                "continent": source_properties.get("CONTINENT"),
                "region": source_properties.get("REGION_UN"),
                "subregion": source_properties.get("SUBREGION"),
            }
            if not clean["name"] or not feature.get("geometry"):
                continue
            features.append({"type": "Feature", "properties": clean, "geometry": feature.get("geometry")})
        if len(features) < 150:
            raise ValueError("The public source did not contain enough usable country geometries.")
        geometry = {"type": "FeatureCollection", "features": features}
    except Exception as exc:
        raise GeographicError("BOUNDARY_BOOTSTRAP_FAILED", f"Global country boundary download failed: {exc}", status_code=502) from exc

    metadata = validate_feature_collection(geometry)
    payload = {
        "name": "Global Countries - Natural Earth 50m",
        "country_code": WORLD_BOUNDARY_CODE,
        "admin_level": 0,
        "source": "Natural Earth",
        "source_version": "5.1.2",
        "license": "Public domain - Natural Earth Terms of Use",
        "geojson": geometry,
    }
    boundary_id = str(uuid4())
    storage_key = f"geographic/boundaries/{boundary_id}.geojson"
    path = request.app.state.storage.resolve(storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(geometry, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_bytes(serialized)
    temporary.replace(path)
    db = request.app.state.SessionLocal()
    try:
        boundary = GeographicBoundary(
            id=boundary_id,
            tenant_id=actor.tenant_id,
            workspace_id="default",
            name=payload["name"],
            country_code=WORLD_BOUNDARY_CODE,
            admin_level=0,
            source=payload["source"],
            source_version=payload["source_version"],
            license=payload["license"],
            geometry_file=storage_key,
            geometry_format="GeoJSON",
            feature_count=metadata["feature_count"],
            metadata_json={
                **metadata,
                "sha256": hashlib.sha256(serialized).hexdigest(),
                "source_url": WORLD_BOUNDARY_URL,
                "scope": "global",
                "join_keys": ["name", "name_long", "name_en", "iso_a2", "iso_a3", "iso_n3", "postal"],
            },
        )
        db.add(boundary)
        db.commit()
        return _public_boundary(boundary)
    except IntegrityError:
        db.rollback()
        existing = db.query(GeographicBoundary).filter(
            GeographicBoundary.tenant_id == actor.tenant_id,
            GeographicBoundary.workspace_id == "default",
            GeographicBoundary.name == payload["name"],
            GeographicBoundary.source_version == payload["source_version"],
        ).first()
        path.unlink(missing_ok=True)
        if existing is not None:
            return _public_boundary(existing)
        raise GeographicError("BOUNDARY_VERSION_EXISTS", "The global country boundary is already registered.", status_code=409)
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    finally:
        db.close()


@router.get("/geographic/boundaries/{boundary_id}/geometry")
def get_boundary_geometry(boundary_id: str, request: Request):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise SecurityError("AUTHENTICATION_REQUIRED", "A security principal is required.")
    authorize(actor, "read")
    _, geometry = _boundary_and_geometry(request, boundary_id, tenant_id=actor.tenant_id)
    return JSONResponse(content=geometry, media_type="application/geo+json")


@router.post("/geographic/territories/build")
def build_territory(request: Request, payload: dict[str, Any]):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise SecurityError("AUTHENTICATION_REQUIRED", "A security principal is required.")
    boundary_id = str(payload.get("boundary_id") or "")
    property_name = str(payload.get("property") or "")
    values = payload.get("values") or []
    if not boundary_id or not property_name or not isinstance(values, list) or not values:
        raise GeographicError("TERRITORY_FIELDS_REQUIRED", "boundary_id, property, and values are required.")
    authorize(actor, "analyze")
    boundary, geometry = _boundary_and_geometry(request, boundary_id, tenant_id=actor.tenant_id)
    selected = {normalize_geo_name(value) for value in values}
    features = [feature for feature in geometry["features"] if normalize_geo_name((feature.get("properties") or {}).get(property_name)) in selected]
    if not features:
        raise GeographicError("TERRITORY_EMPTY", "No boundary features matched the requested territory values.")
    result = {"type": "FeatureCollection", "features": features}
    return {"territory_id": str(uuid4()), "name": str(payload.get("name") or "Custom territory"), "boundary": boundary, "feature_count": len(features), "geojson": result}


def _public_saved_map(asset: WorkspaceAsset) -> dict[str, Any]:
    return {"id": asset.id, "tenant_id": asset.tenant_id, "workspace_id": asset.workspace_id, "name": asset.name, "description": asset.description, "dataset_id": asset.dataset_id, "configuration": asset.definition_json, "status": asset.status, "version": asset.version, "updated_at": asset.updated_at.isoformat() if asset.updated_at else None}


@router.get("/geographic/saved-maps")
def list_saved_maps(request: Request, workspace_id: str = "default"):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise SecurityError("AUTHENTICATION_REQUIRED", "A security principal is required.")
    authorize(actor, "read", workspace_id=workspace_id)
    db = request.app.state.SessionLocal()
    try:
        items = db.query(WorkspaceAsset).filter(WorkspaceAsset.tenant_id == actor.tenant_id, WorkspaceAsset.workspace_id == workspace_id, WorkspaceAsset.asset_type == "geographic_map").order_by(WorkspaceAsset.updated_at.desc()).all()
        return {"workspace_id": workspace_id, "maps": [_public_saved_map(item) for item in items]}
    finally:
        db.close()


@router.post("/geographic/saved-maps", status_code=201)
def save_map(request: Request, payload: dict[str, Any]):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise SecurityError("AUTHENTICATION_REQUIRED", "A security principal is required.")
    name = str(payload.get("name") or "").strip()
    dataset_id = str(payload.get("dataset_id") or "").strip()
    configuration = payload.get("configuration")
    if not name or not dataset_id or not isinstance(configuration, dict) or not configuration.get("map_type"):
        raise GeographicError("MAP_FIELDS_REQUIRED", "name, dataset_id, and a map configuration are required.")
    db = request.app.state.SessionLocal()
    try:
        if db.query(Dataset).filter(Dataset.id == dataset_id, Dataset.tenant_id == actor.tenant_id).first() is None:
            raise GeographicError("DATASET_NOT_FOUND", "Dataset was not found.", status_code=404)
        workspace_id = str(payload.get("workspace_id") or "default")
        authorize(actor, "write", workspace_id=workspace_id)
        asset = db.query(WorkspaceAsset).filter(WorkspaceAsset.tenant_id == actor.tenant_id, WorkspaceAsset.workspace_id == workspace_id, WorkspaceAsset.asset_type == "geographic_map", WorkspaceAsset.name == name).first()
        if asset is None:
            asset = WorkspaceAsset(tenant_id=actor.tenant_id, workspace_id=workspace_id, asset_type="geographic_map", name=name, dataset_id=dataset_id, definition_json={}, status="draft")
            db.add(asset)
        else:
            asset.version += 1
        asset.description = str(payload.get("description") or "") or None
        asset.definition_json = {"map_type": configuration["map_type"], "dataset_id": dataset_id, "configuration": configuration}
        asset.updated_at = utc_now()
        db.commit()
        return _public_saved_map(asset)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
