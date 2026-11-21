"""Background worker: durable SQLite queue, heartbeat for liveness, orderly restart recovery."""

from __future__ import annotations

import logging
import os
import signal
import socket
import time
from pathlib import Path

from .. import repo
from ..config import get_settings
from ..db import connection, migrate, utcnow
from ..logging_utils import configure_logging, safe_error
from ..models_cache import verify_model_cache
from ..pipeline import run_job
from ..platform_check import warn_sme_without_sve
from ..translate.service import JobCancelled

log = logging.getLogger(__name__)
_stop = False


def _handle_signal(signum: int, _frame: object) -> None:
    global _stop
    log.info("received signal %s, finishing current job", signum)
    _stop = True


def heartbeat(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(utcnow(), encoding="utf-8")


def heartbeat_is_fresh(path: Path, max_age: float = 60.0) -> bool:
    try:
        return (time.time() - path.stat().st_mtime) < max_age
    except OSError:
        return False


def main() -> None:
    configure_logging(os.environ.get("MT_LOG_LEVEL", "INFO"))
    s = get_settings()
    warn_sme_without_sve()
    migrate()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    try:
        verify_model_cache(s.models_dir)
    except Exception as exc:  # noqa: BLE001
        log.error("model cache verification failed: %s", safe_error(exc))
        raise SystemExit(2) from exc
    with connection() as conn:
        n = repo.requeue_stale_running(conn, s.job_max_attempts)
        if n:
            log.info("recovered %s interrupted job(s)", n)
        log.info("worker %s started (ocr_engine=%s)", worker_id, s.ocr_engine)
        while not _stop:
            heartbeat(s.worker_heartbeat_file)
            job = repo.claim_next_job(conn, worker_id, s.job_max_attempts)
            if job is None:
                time.sleep(s.worker_poll_seconds)
                continue
            log.info("job %s started (%s/%s)", job["id"], job["scope"], job["step"])
            try:
                # done / partial / failed depending on how many pages failed (never "done" with failures)
                status, error = run_job(conn, job)
                if status != "done":
                    log.warning("job %s finished with failed pages: %s", job["id"], error)
            except JobCancelled:
                status, error = "cancelled", None
            except Exception as exc:  # noqa: BLE001
                status, error = "failed", safe_error(exc)
                log.warning("job %s failed: %s", job["id"], error)
            repo.update_job(
                conn,
                job["id"],
                {"status": status, "error": error, "finished_at": utcnow(), "locked_by": None},
            )
            log.info("job %s %s", job["id"], status)
            heartbeat(s.worker_heartbeat_file)
    log.info("worker stopped")
