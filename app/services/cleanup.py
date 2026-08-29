"""Lightweight cleanup for data/tmp and data/output.

Goal (Fase 0.1): Keep the project ringan. Automatically remove old scratch
files and unreferenced renders without adding heavy dependencies.

Design principles:
- Prefer age-based deletion for tmp (safe, scratch data).
- For output/, only delete files that are not referenced by any successful render.
- Respect config: tmp_retention_days, output_retention_days, max_data_gb.
- Run on startup (cheap) + manual CLI.
- Never touch storage/ (source assets) or the database itself.
"""

from __future__ import annotations

import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import settings

# Note: app.db and app.models are imported lazily inside the functions that need
# them. Importing them here would pull SQLAlchemy in on every startup, and this
# module is meant to stay cheap enough to call from the lifespan hook.

# Health checks can be frequent while a project directory contains thousands of
# files. Keep that endpoint from recursively walking all data roots on every
# request; cleanup/CLI paths still call the exact uncached function.
_DATA_USAGE_CACHE: tuple[float, dict[str, Any]] | None = None
_DATA_USAGE_CACHE_TTL_SECONDS = 30.0


def _now() -> datetime:
    return datetime.now(UTC)


def _age_days(path: Path) -> int:
    """Return age in whole days. Returns a very large number on error."""
    try:
        mtime = path.stat().st_mtime
        return int((time.time() - mtime) / 86400.0)
    except OSError:
        return 99999


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
    except OSError:
        pass
    return total


def _human_size(num_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if num_bytes < 1024:
            return f"{num_bytes:.1f}{unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f}TB"


def get_data_usage() -> dict[str, Any]:
    """Return current usage of data directories."""
    tmp_size = _dir_size(settings.tmp_dir)
    output_size = _dir_size(settings.output_dir)
    storage_size = _dir_size(settings.storage_dir)
    total = tmp_size + output_size + storage_size

    return {
        "tmp_bytes": tmp_size,
        "output_bytes": output_size,
        "storage_bytes": storage_size,
        "total_bytes": total,
        "total_human": _human_size(total),
        "max_bytes": settings.max_data_gb * 1024 * 1024 * 1024,
        "over_limit": total > (settings.max_data_gb * 1024 * 1024 * 1024),
    }


def get_data_usage_cached(
    ttl_seconds: float = _DATA_USAGE_CACHE_TTL_SECONDS,
) -> dict[str, Any]:
    """Return disk usage with a short process-local TTL for health polling."""
    global _DATA_USAGE_CACHE

    now = time.monotonic()
    cached = _DATA_USAGE_CACHE
    if cached is not None and ttl_seconds > 0 and now - cached[0] < ttl_seconds:
        return dict(cached[1])
    usage = get_data_usage()
    _DATA_USAGE_CACHE = (now, dict(usage))
    return usage


def _cache_data_usage(usage: dict[str, Any]) -> None:
    global _DATA_USAGE_CACHE
    _DATA_USAGE_CACHE = (time.monotonic(), dict(usage))


def _delete_tree(path: Path) -> int:
    """Delete a file or directory tree. Returns bytes freed."""
    try:
        if path.is_file():
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            return size
        if path.is_dir():
            size = _dir_size(path)
            shutil.rmtree(path, ignore_errors=True)
            return size
    except OSError:
        pass
    return 0


def _tmp_project_state() -> tuple[set[str], set[str]] | None:
    """Return (live project ids, projects whose scratch must be preserved).

    A directory belonging to a running/queued render is never age-cleaned.
    Project ``RENDERING`` state is also treated as active so a brief job-state
    transition cannot race cleanup.  Database failure remains fail-safe: callers
    skip orphan assumptions rather than delete blindly.
    """
    try:
        from app.constants import JobStatus, ProjectStatus
        from app.db import session_scope
        from app.models import Project, RenderJob

        with session_scope() as db:
            project_rows = db.query(Project.id, Project.status).all()
            live = {row.id for row in project_rows}
            active = {
                row.id for row in project_rows if row.status == ProjectStatus.RENDERING
            }
            active.update(
                row.project_id
                for row in db.query(RenderJob.project_id)
                .filter(RenderJob.status.in_((JobStatus.QUEUED, JobStatus.RUNNING)))
                .all()
            )
            return live, active
    except Exception:
        return None


def cleanup_tmp(older_than_days: int | None = None) -> int:
    """Delete scratch directories inside data/tmp.

    Two rules, because age alone leaves real garbage behind:

    1. **Orphans go immediately.** ``data/tmp/<project_id>/`` belonging to a
       project that no longer exists can never be used again, so waiting out a
       retention window only wastes disk. This is the common case: deleting a
       project (or a test run) removed the row but left the scratch tree.
    2. **Everything else waits out the retention window**, since a directory for
       a live project may belong to a render currently in flight.

    Returns bytes freed.
    """
    if older_than_days is None:
        older_than_days = settings.tmp_retention_days

    freed = 0
    tmp_root = settings.tmp_dir
    if not tmp_root.exists():
        return 0

    state = _tmp_project_state()
    live = state[0] if state is not None else None
    active = state[1] if state is not None else set()

    for child in list(tmp_root.iterdir()):
        if not child.is_dir():
            # Stray loose files: age-based only.
            if _age_days(child) > older_than_days:
                freed += _delete_tree(child)
            continue

        # Active render scratch is protected regardless of age.
        if child.name in active:
            continue

        # Orphaned project scratch: delete regardless of age.
        if live is not None and child.name not in live:
            freed += _delete_tree(child)
            continue

        if _age_days(child) > older_than_days:
            freed += _delete_tree(child)

    return freed


def cleanup_output(older_than_days: int | None = None, aggressive: bool = False) -> int:
    """Delete old render outputs that are no longer referenced.

    We only delete files inside data/output/<project>/ that are not pointed to
    by any successful RenderJob.

    Returns bytes freed.
    """
    if older_than_days is None:
        older_than_days = settings.output_retention_days

    freed = 0
    output_root = settings.output_dir

    if not output_root.exists():
        return 0

    # Collect all output paths that are still referenced
    referenced: set[str] = set()
    try:
        # Lazy import to keep the module light when not needed
        from app.db import session_scope
        from app.models import RenderJob

        with session_scope() as db:
            jobs = db.query(RenderJob).filter(
                RenderJob.status == "succeeded",
                RenderJob.output_key != "",
            ).all()
            for job in jobs:
                if job.output_key:
                    output_path = Path(job.output_key).resolve()
                    referenced.add(str(output_path))
                    for thumbnail_name in (
                        "thumbnail.jpg", "thumbnail_clean.jpg",
                        "thumbnail_v1.jpg", "thumbnail_v2.jpg", "thumbnail_v3.jpg",
                    ):
                        referenced.add(str((output_path.parent / thumbnail_name).resolve()))
    except Exception:
        # If DB is unavailable or models not ready, be conservative.
        return 0

    cutoff_days = older_than_days
    if aggressive:
        cutoff_days = max(1, cutoff_days // 2)  # be more aggressive when over limit

    for project_dir in list(output_root.iterdir()):
        if not project_dir.is_dir():
            continue

        for f in list(project_dir.iterdir()):
            if not f.is_file():
                continue

            if f.suffix.lower() not in {".mp4", ".srt", ".jpg", ".jpeg"}:
                continue

            if str(f.resolve()) in referenced:
                continue

            if _age_days(f) > cutoff_days:
                freed += _delete_tree(f)

        # remove empty project dir
        try:
            if not any(project_dir.iterdir()):
                project_dir.rmdir()
        except OSError:
            pass

    return freed


def run_cleanup(force: bool = False) -> dict[str, Any]:
    """Run full cleanup pass.

    Returns summary dict.
    """
    usage_before = get_data_usage()
    over_limit = usage_before["over_limit"] or force

    freed_tmp = cleanup_tmp()
    freed_output = cleanup_output(aggressive=over_limit)

    total_freed = freed_tmp + freed_output
    usage_after = get_data_usage()
    _cache_data_usage(usage_after)

    return {
        "before": usage_before,
        "after": usage_after,
        "freed_bytes": total_freed,
        "freed_human": _human_size(total_freed),
        "tmp_freed": freed_tmp,
        "output_freed": freed_output,
        "over_limit": usage_after["over_limit"],
        "aggressive": over_limit,
    }


def cleanup_on_startup() -> None:
    """Lightweight cleanup on app start.

    Only does age-based tmp cleanup + checks if we are over limit.
    Expensive output cleanup is only done when over limit.
    """
    try:
        usage = get_data_usage()
        freed = cleanup_tmp()

        if usage["over_limit"]:
            extra = cleanup_output(aggressive=True)
            freed += extra

        if freed > 0:
            try:
                import logging
                logging.getLogger("manhwashorts").info(
                    "cleanup: freed %s on startup (tmp+output)",
                    _human_size(freed),
                )
            except Exception:
                pass
    except Exception as exc:
        # Never break startup because of cleanup
        try:
            from app.config import logger  # type: ignore
            logger.warning("cleanup on startup failed: %s", exc)
        except Exception:
            pass
