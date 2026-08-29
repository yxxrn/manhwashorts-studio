from app.models import Project, User, Workspace
from app.services import pipeline as pl


def test_failed_qc_cannot_become_ready_and_history_is_append_only(db):
    user = User(email="qc-history@example.com", password_hash="x")
    db.add(user)
    db.flush()
    workspace = Workspace(owner_id=user.id)
    db.add(workspace)
    db.flush()
    project = Project(workspace_id=workspace.id, title="QC history")
    db.add(project)
    db.flush()

    first = pl.run_quality_checks(db, project.id, actor_id=user.id)
    assert any(result.blocking for result in first)
    assert project.status != "ready"
    snapshots = pl.project_qc_history(db, project.id)
    assert len(snapshots) == 1
    first_id = snapshots[0].id

    second = pl.run_quality_checks(db, project.id, actor_id=user.id)
    assert second
    snapshots = pl.project_qc_history(db, project.id)
    assert len(snapshots) == 2
    assert snapshots[0].id == first_id
    assert snapshots[0].created_at <= snapshots[1].created_at
