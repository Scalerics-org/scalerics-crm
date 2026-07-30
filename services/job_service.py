"""SQLite-backed background job queue with a single worker thread."""

import json
import logging
import threading
import time
from typing import Callable, Optional

from database import create_job, get_next_pending_job, update_job, get_job

logger = logging.getLogger(__name__)


class JobWorker:
    def __init__(self, db_path: str, poll_interval: float = 2.0):
        self._db_path = db_path
        self._poll_interval = poll_interval
        self._handlers: dict[str, Callable] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def register(self, job_type: str, handler: Callable) -> None:
        self._handlers[job_type] = handler

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="JobWorker"
        )
        self._thread.start()
        logger.info("JobWorker started")

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                self._process_next()
            except Exception as e:
                logger.error(f"JobWorker loop error: {e}")
            time.sleep(self._poll_interval)

    def _process_next(self) -> None:
        job = get_next_pending_job(self._db_path)
        if not job:
            return

        handler = self._handlers.get(job["type"])
        if not handler:
            update_job(
                self._db_path, job["id"],
                status="failed",
                error_message=f"No handler registered for job type: {job['type']}",
            )
            logger.error(f"Job {job['id']}: no handler for type '{job['type']}'")
            return

        logger.info(f"Job {job['id']} ({job['type']}): starting")
        try:
            payload = json.loads(job["payload"] or "{}")
            result = handler(payload)
            update_job(
                self._db_path, job["id"],
                status="completed",
                result=json.dumps(result or {}),
            )
            logger.info(f"Job {job['id']} ({job['type']}): completed")
        except Exception as e:
            retry_count = (job["retry_count"] or 0) + 1
            max_retries = job["max_retries"] or 3
            if retry_count < max_retries:
                update_job(
                    self._db_path, job["id"],
                    status="pending",
                    retry_count=retry_count,
                    error_message=str(e),
                )
                logger.warning(
                    f"Job {job['id']} ({job['type']}): failed (attempt {retry_count}/{max_retries}): {e}"
                )
            else:
                update_job(
                    self._db_path, job["id"],
                    status="failed",
                    error_message=str(e),
                )
                logger.error(
                    f"Job {job['id']} ({job['type']}): permanently failed after {max_retries} attempts: {e}"
                )


# ─── Module-level singleton ───────────────────────────────────────────────────

_worker: Optional[JobWorker] = None


def init_worker(db_path: str) -> JobWorker:
    global _worker
    _worker = JobWorker(db_path)
    return _worker


def get_worker() -> Optional[JobWorker]:
    return _worker
