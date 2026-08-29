from __future__ import annotations

import os
import time


def _seed_project(db, *, status="draft"):
    from app.models import Project, User, Workspace
    from app.security import hash_password

    user = User(email=f"cleanup-{time.time_ns()}@example.com", password_hash=hash_password("pass12345"))
    db.add(user)
    db.flush()
    db.commit()
    workspace = Workspace(owner_id=user.id, name="cleanup")
    db.add(workspace)
    db.flush()
    db.commit()
    project = Project(workspace_id=workspace.id, title="cleanup", status=status)
    db.add(project)
    db.flush()
    db.commit()
    return project


def test_cleanup_tmp_preserves_active_render_scratch(db, app_settings):
    from app.constants import JobStatus
    from app.models import RenderJob
    from app.services import cleanup

    project = _seed_project(db)
    db.add(RenderJob(project_id=project.id, status=JobStatus.RUNNING))
    db.flush()
    db.commit()

    scratch = app_settings.tmp_dir / project.id
    scratch.mkdir(parents=True, exist_ok=True)
    payload = scratch / "in-flight.bin"
    payload.write_bytes(b"x" * 1024)
    old = time.time() - 10 * 86400
    os.utime(scratch, (old, old))
    os.utime(payload, (old, old))

    assert cleanup.cleanup_tmp(older_than_days=1) == 0
    assert payload.is_file()


def test_cleanup_tmp_removes_old_inactive_project_scratch(db, app_settings):
    from app.services import cleanup

    project = _seed_project(db)
    scratch = app_settings.tmp_dir / project.id
    scratch.mkdir(parents=True, exist_ok=True)
    payload = scratch / "stale.bin"
    payload.write_bytes(b"x" * 1024)
    old = time.time() - 10 * 86400
    os.utime(scratch, (old, old))
    os.utime(payload, (old, old))

    assert cleanup.cleanup_tmp(older_than_days=1) >= 1024
    assert not scratch.exists()


def test_cached_disk_usage_avoids_repeated_recursive_walks(monkeypatch):
    from app.services import cleanup

    calls = []

    def fake_size(path):
        calls.append(path)
        return 7

    monkeypatch.setattr(cleanup, "_dir_size", fake_size)
    monkeypatch.setattr(cleanup, "_DATA_USAGE_CACHE", None)

    first = cleanup.get_data_usage_cached(ttl_seconds=60)
    second = cleanup.get_data_usage_cached(ttl_seconds=60)

    assert first == second
    assert first["total_bytes"] == 21
    assert len(calls) == 3
