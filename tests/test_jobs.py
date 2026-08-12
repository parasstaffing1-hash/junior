from __future__ import annotations

from datetime import timedelta

from app.core.jobs.queue import DurableJobQueue, utc_now
from app.models.all import AutomationRun, JobSchedule


def test_durable_queue_claim_retry_and_completion(client):
    queue = DurableJobQueue(client.app.state.SessionLocal, worker_id="test-worker")
    run = queue.enqueue(tenant_id="default", action="quality.analyze", context={"dataset_id": "GLOBAL"}, payload={}, max_attempts=2)
    claimed = queue.claim_next()
    assert claimed is not None
    assert claimed.id == run.id
    assert claimed.status == "RUNNING"
    queue.fail(run.id, {"error": "transient"}, retry_delay_seconds=1)
    db = client.app.state.SessionLocal()
    try:
        current = db.query(AutomationRun).filter(AutomationRun.id == run.id).first()
        assert current.status == "RETRY_WAITING"
        current.next_run_at = utc_now() - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    claimed_again = queue.claim_next()
    assert claimed_again is not None
    queue.complete(run.id, {"ok": True})
    db = client.app.state.SessionLocal()
    try:
        current = db.query(AutomationRun).filter(AutomationRun.id == run.id).first()
        assert current.status == "COMPLETED"
        assert current.result == {"ok": True}
    finally:
        db.close()


def test_schedule_materializes_due_job(client):
    queue = DurableJobQueue(client.app.state.SessionLocal, worker_id="test-worker")
    db = client.app.state.SessionLocal()
    try:
        schedule = JobSchedule(tenant_id="default", name="hourly-quality", action="quality.analyze", context={"dataset_id": "GLOBAL"}, payload={}, interval_seconds=3600, enabled=True, next_run_at=utc_now() - timedelta(seconds=1))
        db.add(schedule)
        db.commit()
    finally:
        db.close()
    assert queue.materialize_due_schedules() == 1
    db = client.app.state.SessionLocal()
    try:
        assert db.query(AutomationRun).filter(AutomationRun.job_type == "scheduled_automation").count() == 1
    finally:
        db.close()
