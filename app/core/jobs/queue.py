from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import or_

from app.models.all import AutomationRun, JobSchedule


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class DurableJobQueue:
    """Lease-based queue stored in the application database.

    PostgreSQL deployments can run multiple workers safely; SQLite remains useful
    for development with the same state machine and a single worker.
    """

    def __init__(self, session_factory, *, worker_id: str, lease_seconds: int = 300):
        self.session_factory = session_factory
        self.worker_id = worker_id
        self.lease_seconds = max(30, int(lease_seconds))

    def enqueue(self, *, tenant_id: str, action: str, context: dict[str, Any], payload: dict[str, Any], idempotency_key: str | None = None, max_attempts: int = 3) -> AutomationRun:
        db = self.session_factory()
        try:
            if idempotency_key:
                existing = db.query(AutomationRun).filter(AutomationRun.tenant_id == tenant_id, AutomationRun.idempotency_key == idempotency_key).first()
                if existing:
                    return existing
            run = AutomationRun(
                tenant_id=tenant_id,
                dataset_id=str(context.get("dataset_id") or "GLOBAL"),
                source_version_id=context.get("version_id"),
                action=action,
                parameters={"context": context, "payload": payload},
                idempotency_key=idempotency_key,
                correlation_id=str(payload.get("correlation_id") or uuid4()),
                job_type="durable_automation",
                status="QUEUED",
                progress=0.0,
                max_attempts=max(1, int(max_attempts)),
                attempt_count=0,
                next_run_at=utc_now(),
                retryable=True,
            )
            db.add(run)
            db.commit()
            db.refresh(run)
            return run
        finally:
            db.close()

    def claim_next(self) -> AutomationRun | None:
        db = self.session_factory()
        try:
            now = utc_now()
            stale_before = now - timedelta(seconds=self.lease_seconds)
            query = db.query(AutomationRun).filter(
                or_(
                    AutomationRun.status == "QUEUED",
                    (AutomationRun.status == "RETRY_WAITING") & (AutomationRun.next_run_at <= now),
                    (AutomationRun.status == "RUNNING") & (AutomationRun.heartbeat_at < stale_before),
                ),
                AutomationRun.next_run_at <= now,
            ).order_by(AutomationRun.created_at.asc())
            run = query.with_for_update(skip_locked=True).first()
            if run is None:
                return None
            run.status = "RUNNING"
            run.attempt_count = int(run.attempt_count or 0) + 1
            run.started_at = run.started_at or now
            run.locked_at = now
            run.locked_by = self.worker_id
            run.heartbeat_at = now
            run.progress = max(float(run.progress or 0), 0.01)
            db.commit()
            db.expunge(run)
            return run
        finally:
            db.close()

    def heartbeat(self, run_id: str) -> bool:
        db = self.session_factory()
        try:
            run = db.query(AutomationRun).filter(AutomationRun.id == run_id, AutomationRun.locked_by == self.worker_id, AutomationRun.status == "RUNNING").first()
            if not run:
                return False
            run.heartbeat_at = utc_now()
            db.commit()
            return True
        finally:
            db.close()

    def complete(self, run_id: str, result: dict[str, Any] | None = None) -> None:
        self._finish(run_id, status="COMPLETED", result=result, error=None)

    def fail(self, run_id: str, error: dict[str, Any], *, retry_delay_seconds: int = 30) -> None:
        db = self.session_factory()
        try:
            run = db.query(AutomationRun).filter(AutomationRun.id == run_id).first()
            if not run:
                return
            retry = bool(run.retryable) and int(run.attempt_count or 0) < int(run.max_attempts or 1)
            run.status = "RETRY_WAITING" if retry else "FAILED"
            run.error_details = error
            run.next_run_at = utc_now() + timedelta(seconds=max(1, retry_delay_seconds)) if retry else None
            run.locked_at = None
            run.locked_by = None
            run.heartbeat_at = None
            if not retry:
                run.completed_at = utc_now()
            db.commit()
        finally:
            db.close()

    def _finish(self, run_id: str, *, status: str, result: dict[str, Any] | None, error: dict[str, Any] | None) -> None:
        db = self.session_factory()
        try:
            run = db.query(AutomationRun).filter(AutomationRun.id == run_id).first()
            if not run:
                return
            run.status = status
            run.result = result
            run.error_details = error
            run.progress = 1.0 if status == "COMPLETED" else run.progress
            run.completed_at = utc_now()
            run.locked_at = None
            run.locked_by = None
            run.heartbeat_at = None
            db.commit()
        finally:
            db.close()

    def materialize_due_schedules(self, *, limit: int = 50) -> int:
        db = self.session_factory()
        count = 0
        try:
            now = utc_now()
            schedules = db.query(JobSchedule).filter(JobSchedule.enabled.is_(True), JobSchedule.next_run_at <= now).order_by(JobSchedule.next_run_at.asc()).limit(max(1, min(limit, 500))).all()
            for schedule in schedules:
                run = AutomationRun(
                    tenant_id=schedule.tenant_id,
                    dataset_id=str((schedule.context or {}).get("dataset_id") or "GLOBAL"),
                    source_version_id=(schedule.context or {}).get("version_id"),
                    action=schedule.action,
                    parameters={"context": schedule.context or {}, "payload": schedule.payload or {}, "schedule_id": schedule.id},
                    correlation_id=str(uuid4()),
                    job_type="scheduled_automation",
                    status="QUEUED",
                    progress=0.0,
                    max_attempts=3,
                    attempt_count=0,
                    next_run_at=now,
                    retryable=True,
                )
                db.add(run)
                schedule.last_run_at = now
                schedule.next_run_at = now + timedelta(seconds=max(1, int(schedule.interval_seconds)))
                count += 1
            db.commit()
            return count
        finally:
            db.close()

    def run_once(self, executor: Callable[[str], Any], *, schedule_limit: int = 50) -> dict[str, Any]:
        scheduled = self.materialize_due_schedules(limit=schedule_limit)
        run = self.claim_next()
        if run is None:
            return {"scheduled": scheduled, "processed": False}
        try:
            result = executor(run.id)
            self.complete(run.id, result if isinstance(result, dict) else None)
            return {"scheduled": scheduled, "processed": True, "run_id": run.id, "status": "COMPLETED"}
        except Exception as exc:
            self.fail(run.id, {"error": str(exc), "type": type(exc).__name__})
            return {"scheduled": scheduled, "processed": True, "run_id": run.id, "status": "RETRY_WAITING" if int(run.attempt_count or 0) < int(run.max_attempts or 1) else "FAILED"}
