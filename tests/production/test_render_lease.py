from datetime import timedelta


def test_render_claim_and_stale_recovery(db):
    from app.constants import JobStatus
    from app.models import Project, RenderJob, User, Workspace
    from app.security import hash_password
    from app.services.pipeline import claim_render_job, recover_stale_jobs

    user = User(email="lease@test.local", name="Lease", password_hash=hash_password("testpass1234"))
    workspace = Workspace(owner=user, name="Lease")
    project = Project(title="lease-test", workspace=workspace)
    db.add(project)
    db.flush()
    job = RenderJob(project_id=project.id, status=JobStatus.QUEUED)
    db.add(job)
    db.flush()
    assert claim_render_job(db, job.id)
    assert job.status == JobStatus.RUNNING
    assert not claim_render_job(db, job.id)
    job.lease_until = job.started_at - timedelta(seconds=1)
    db.flush()
    assert recover_stale_jobs(db) == 1
    assert job.status == JobStatus.QUEUED
    assert job.attempt == 2


def test_quality_override_is_append_only(db):
    from app.constants import CheckSeverity
    from app.models import Project, QualityCheck, User, Workspace
    from app.security import hash_password
    from app.services.pipeline import override_warning, project_qc_overrides

    user = User(email="override@test.local", name="Override", password_hash=hash_password("testpass1234"))
    workspace = Workspace(owner=user, name="Override")
    project = Project(title="override-test", workspace=workspace)
    db.add(project)
    db.flush()
    db.add(QualityCheck(
        project_id=project.id,
        code="warning.test",
        severity=CheckSeverity.WARNING,
        message="test warning",
        passed=False,
    ))
    db.flush()
    check = override_warning(db, project.id, "warning.test", "manual review completed", user.id)
    events = project_qc_overrides(db, project.id)
    assert check.passed is True
    assert len(events) == 1
    assert events[0].reason == "manual review completed"
    assert events[0].actor_id == user.id
    assert events[0].before_passed is False
    assert events[0].after_passed is True
