#!/usr/bin/env python3
"""Standalone render worker.

The API renders inline in a background task by default, which is fine for a
single-user local install. Run this instead when you want rendering off the web
process (long queues, or so a server restart does not abandon a render).

``execute_render`` only accepts jobs still in QUEUED state, so running this
alongside the inline task cannot double-render a job.

Usage:
    python scripts/worker.py                 # poll forever
    python scripts/worker.py --once          # drain the queue and exit
    python scripts/worker.py --interval 10   # custom poll interval
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import init_db, session_scope  # noqa: E402
from app.services import pipeline as pl  # noqa: E402
from app.services.render import check_environment  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s worker: %(message)s",
)
logger = logging.getLogger("worker")

_shutdown = False


def _handle_signal(signum, _frame) -> None:
    """Finish the current render, then stop."""
    global _shutdown
    _shutdown = True
    logger.info("signal %s received; will stop after the current job", signum)


def claim_and_run() -> bool:
    """Recover stale jobs, then render one queued job."""
    with session_scope() as db:
        recovered = pl.recover_stale_jobs(db)
        if recovered:
            logger.warning("recovered %d stale render job(s)", recovered)
        job = pl.next_queued_job(db)
        if job is None:
            return False
        job_id = job.id
        project_id = job.project_id
        kind = job.kind

    logger.info("starting %s render %s (project %s)", kind, job_id, project_id)
    started = time.monotonic()
    with session_scope() as db:
        try:
            job = pl.execute_render(db, job_id)
        except pl.PipelineError as exc:
            # execute_render records failures on the row; this catches setup
            # errors raised before it takes over.
            logger.error("job %s failed to start: %s", job_id, exc)
            return True

    elapsed = time.monotonic() - started
    if job.status == "succeeded":
        logger.info(
            "job %s succeeded in %.1fs -> %.2fs %dx%d",
            job_id, elapsed, job.duration, job.width, job.height,
        )
    else:
        logger.error("job %s %s after %.1fs: %s", job_id, job.status, elapsed, job.error_message)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="drain the queue then exit")
    parser.add_argument("--interval", type=float, default=5.0, help="poll interval in seconds")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    init_db()
    problems = check_environment()
    if problems:
        for problem in problems:
            logger.error("environment: %s", problem)
        logger.error("refusing to start: fix the environment first")
        return 1

    logger.info("worker ready (interval %.1fs)", args.interval)
    idle_logged = False
    while not _shutdown:
        try:
            did_work = claim_and_run()
        except Exception:
            logger.exception("unexpected worker error; continuing")
            did_work = False

        if did_work:
            idle_logged = False
            continue

        if args.once:
            logger.info("queue empty; exiting (--once)")
            return 0
        if not idle_logged:
            logger.info("queue empty; waiting for work")
            idle_logged = True
        time.sleep(args.interval)

    logger.info("worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
