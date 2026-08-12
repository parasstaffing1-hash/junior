"""Run the durable database-backed automation worker.

Production example:
    JOB_WORKER_ENABLED=true python scripts/run_worker.py
"""

from __future__ import annotations

import time

from app.core.jobs.queue import DurableJobQueue
from app.main import app


def main() -> None:
    settings = app.state.settings
    queue = DurableJobQueue(app.state.SessionLocal, worker_id=settings.worker_id)
    while True:
        queue.run_once(app.state.execute_automation_run)
        time.sleep(settings.job_poll_seconds)


if __name__ == "__main__":
    main()
