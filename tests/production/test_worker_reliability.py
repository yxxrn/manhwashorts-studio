from datetime import timedelta


def test_stale_job_can_be_recovered_and_reclaimed_after_process_restart(db):
    from app.constants import JobStatus
    from app.models import Project, RenderJob, User, Workspace
    from app.services.pipeline import claim_render_job, recover_stale_jobs

    user = User(email="worker-restart@example.com", password_hash="x")
    workspace = Workspace(owner=user)
    project = Project(workspace=workspace, title="restart")
    job = RenderJob(project=project, status=JobStatus.QUEUED)
    db.add(job)
    db.flush()
    assert claim_render_job(db, job.id)
    job.lease_until = job.started_at - timedelta(seconds=1)
    db.flush()
    assert recover_stale_jobs(db) == 1
    assert claim_render_job(db, job.id)
    assert job.status == JobStatus.RUNNING
    assert job.attempt == 2
