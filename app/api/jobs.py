from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Request

from app.core.automation.gateway import dispatch_plan
from app.core.jobs.queue import DurableJobQueue, utc_now
from app.core.security import SecurityError, authorize
from app.models.all import AutomationRun, JobSchedule


router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def _actor(request: Request):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise SecurityError("AUTHENTICATION_REQUIRED", "A security principal is required.")
    return actor


def _queue(request: Request) -> DurableJobQueue:
    return DurableJobQueue(request.app.state.SessionLocal, worker_id=request.app.state.settings.worker_id)


@router.post("", status_code=202)
def enqueue_job(payload: dict[str, Any], request: Request):
    actor = _actor(request)
    authorize(actor, "analyze")
    action = str(payload.get("action", ""))
    context = {**(payload.get("context") or {})}
    context.setdefault("tenant_id", actor.tenant_id)
    job_payload = payload.get("payload") or {}
    try:
        dispatch_plan(action, context, job_payload)
    except Exception as exc:
        raise SecurityError("AUTOMATION_ACTION_NOT_ALLOWED", str(exc), status_code=422) from exc
    run = _queue(request).enqueue(tenant_id=actor.tenant_id, action=action, context=context, payload=job_payload, idempotency_key=payload.get("idempotency_key"), max_attempts=int(payload.get("max_attempts", 3)))
    return {"run_id": run.id, "status": run.status, "job_type": run.job_type, "tenant_id": run.tenant_id, "next_run_at": run.next_run_at, "durable": True}


@router.get("")
def list_jobs(request: Request, status: str | None = None, limit: int = 100):
    actor = _actor(request)
    authorize(actor, "read")
    db = request.app.state.SessionLocal()
    try:
        query = db.query(AutomationRun).filter(AutomationRun.tenant_id == actor.tenant_id)
        if status:
            query = query.filter(AutomationRun.status == status.upper())
        rows = query.order_by(AutomationRun.created_at.desc()).limit(max(1, min(limit, 500))).all()
        return {"tenant_id": actor.tenant_id, "jobs": [_public_job(row) for row in rows]}
    finally:
        db.close()


@router.get("/{run_id}")
def get_job(run_id: str, request: Request):
    actor = _actor(request)
    authorize(actor, "read")
    db = request.app.state.SessionLocal()
    try:
        row = db.query(AutomationRun).filter(AutomationRun.id == run_id, AutomationRun.tenant_id == actor.tenant_id).first()
        if row is None:
            raise SecurityError("RUN_NOT_FOUND", "Job was not found.", status_code=404)
        return _public_job(row)
    finally:
        db.close()


@router.post("/schedules", status_code=201)
def create_schedule(payload: dict[str, Any], request: Request):
    actor = _actor(request)
    authorize(actor, "write")
    action = str(payload.get("action", ""))
    context = {**(payload.get("context") or {})}
    context.setdefault("tenant_id", actor.tenant_id)
    try:
        dispatch_plan(action, context, payload.get("payload") or {})
    except Exception as exc:
        raise SecurityError("AUTOMATION_ACTION_NOT_ALLOWED", str(exc), status_code=422) from exc
    interval_seconds = int(payload.get("interval_seconds", 3600))
    if interval_seconds < 60:
        raise SecurityError("SCHEDULE_INTERVAL_INVALID", "interval_seconds must be at least 60.", status_code=422)
    db = request.app.state.SessionLocal()
    try:
        schedule = JobSchedule(tenant_id=actor.tenant_id, name=str(payload.get("name", action)), action=action, context=context, payload=payload.get("payload") or {}, interval_seconds=interval_seconds, enabled=bool(payload.get("enabled", True)), next_run_at=utc_now() + timedelta(seconds=int(payload.get("start_in_seconds", interval_seconds))))
        db.add(schedule)
        db.commit()
        return _public_schedule(schedule)
    finally:
        db.close()


@router.get("/schedules/list")
def list_schedules(request: Request):
    actor = _actor(request)
    authorize(actor, "read")
    db = request.app.state.SessionLocal()
    try:
        rows = db.query(JobSchedule).filter(JobSchedule.tenant_id == actor.tenant_id).order_by(JobSchedule.next_run_at.asc()).all()
        return {"tenant_id": actor.tenant_id, "schedules": [_public_schedule(row) for row in rows]}
    finally:
        db.close()


@router.post("/schedules/{schedule_id}/toggle")
def toggle_schedule(schedule_id: str, request: Request, payload: dict[str, Any] | None = None):
    actor = _actor(request)
    authorize(actor, "write")
    db = request.app.state.SessionLocal()
    try:
        row = db.query(JobSchedule).filter(JobSchedule.id == schedule_id, JobSchedule.tenant_id == actor.tenant_id).first()
        if row is None:
            raise SecurityError("SCHEDULE_NOT_FOUND", "Schedule was not found.", status_code=404)
        row.enabled = bool((payload or {}).get("enabled", not row.enabled))
        db.commit()
        return _public_schedule(row)
    finally:
        db.close()


@router.get("/worker/status")
def worker_status(request: Request):
    actor = _actor(request)
    authorize(actor, "read")
    db = request.app.state.SessionLocal()
    try:
        rows = db.query(AutomationRun).filter(AutomationRun.tenant_id == actor.tenant_id).all()
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.status] = counts.get(row.status, 0) + 1
        return {"worker_id": request.app.state.settings.worker_id, "worker_enabled": request.app.state.settings.job_worker_enabled, "queue_counts": counts, "durable_queue": True}
    finally:
        db.close()


def _public_job(row: AutomationRun) -> dict[str, Any]:
    return {"run_id": row.id, "tenant_id": row.tenant_id, "action": row.action, "status": row.status, "job_type": row.job_type, "progress": row.progress, "attempt_count": row.attempt_count, "max_attempts": row.max_attempts, "retryable": row.retryable, "next_run_at": row.next_run_at, "created_at": row.created_at, "started_at": row.started_at, "completed_at": row.completed_at, "locked_by": row.locked_by, "error_details": row.error_details, "result": row.result, "correlation_id": row.correlation_id}


def _public_schedule(row: JobSchedule) -> dict[str, Any]:
    return {"id": row.id, "tenant_id": row.tenant_id, "name": row.name, "action": row.action, "context": row.context, "payload": row.payload, "interval_seconds": row.interval_seconds, "enabled": row.enabled, "next_run_at": row.next_run_at, "last_run_at": row.last_run_at, "created_at": row.created_at, "updated_at": row.updated_at}
