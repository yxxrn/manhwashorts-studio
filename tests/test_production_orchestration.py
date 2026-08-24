"""Focused contracts for the explicit production boundary."""

from __future__ import annotations

from datetime import UTC, datetime


def test_evidence_v3_requires_exact_approval_and_never_falls_back(db):
    from app.services import pipeline as pl
    from tests.test_script_evidence_gate import _project, _seed_analysis, _seed_script

    project = _project(db)
    row = _seed_analysis(db, project)
    script = _seed_script(db, project, row)
    script.generator = "vision_evidence_v3"
    script.approved_by = "reviewer"
    script.approved_at = script.updated_at
    metadata = dict(script.editorial_metadata)
    metadata["approved_script_version"] = script.version
    metadata["approved_script_hash"] = "stale"
    script.editorial_metadata = metadata
    db.flush()

    try:
        pl._script_for_media(db, project.id)
    except pl.PipelineError as exc:
        assert "approved" in str(exc)
    else:  # pragma: no cover - contract guard
        raise AssertionError("stale v3 approval was accepted")

    metadata["approved_script_hash"] = pl._script_content_hash(script)
    script.editorial_metadata = metadata
    db.flush()
    assert pl._script_for_media(db, project.id).id == script.id


def test_review_only_media_may_use_unapproved_evidence_script_without_relaxing_default(db):
    from app.services import pipeline as pl
    from tests.test_script_evidence_gate import _project, _seed_analysis, _seed_script

    project = _project(db)
    row = _seed_analysis(db, project)
    script = _seed_script(db, project, row)
    script.generator = "vision_evidence_v3"
    script.approved_by = ""
    script.approved_at = None
    db.flush()

    try:
        pl._script_for_media(db, project.id)
    except pl.PipelineError as exc:
        assert "approved" in str(exc)
    else:  # pragma: no cover - contract guard
        raise AssertionError("default media path must keep the approval gate")

    assert (
        pl._script_for_media(
            db,
            project.id,
            allow_unapproved_review=True,
        ).id
        == script.id
    )


def test_production_resume_reuses_audio_timeline_and_render(db, monkeypatch, tmp_path):
    from app.constants import JobStatus
    from app.models import RenderJob, ScriptVersion
    from app.services import pipeline as pl
    from tests.test_script_evidence_gate import _project

    project = _project(db)
    script = ScriptVersion(
        project_id=project.id,
        version=1,
        generator="vision_evidence_v3",
        sections=[{"section": "hook", "text": "A grounded hook."}],
        approved_by="reviewer",
        approved_at=datetime.now(UTC),
        editorial_metadata={"approved_script_version": 1},
    )
    db.add(script)
    db.flush()
    script.editorial_metadata["approved_script_hash"] = pl._script_content_hash(script)
    db.flush()

    calls = {"audio": 0, "timeline": 0, "enqueue": 0, "execute": 0}
    monkeypatch.setattr(pl, "_audio_stage_ready", lambda *_args: calls["audio"] > 0)
    monkeypatch.setattr(pl, "_timeline_stage_ready", lambda *_args: calls["timeline"] > 0)
    monkeypatch.setattr(pl, "run_quality_checks", lambda *args, **kwargs: [])

    def fake_audio(*args, **kwargs):
        calls["audio"] += 1
        return []

    def fake_timeline(*args, **kwargs):
        calls["timeline"] += 1
        return []

    def fake_enqueue(db, project_id, **kwargs):
        calls["enqueue"] += 1
        job = RenderJob(project_id=project_id, kind="final", status=JobStatus.QUEUED)
        db.add(job)
        db.flush()
        return job

    output = tmp_path / "final.mp4"
    output.write_bytes(b"fixture")

    def fake_execute(db, job_id):
        calls["execute"] += 1
        job = db.get(RenderJob, job_id)
        job.status = JobStatus.SUCCEEDED
        job.output_key = str(output)
        job.checksum = "fixture"
        return job

    monkeypatch.setattr(pl, "generate_voiceover", fake_audio)
    monkeypatch.setattr(pl, "build_timeline", fake_timeline)
    monkeypatch.setattr(pl, "enqueue_render", fake_enqueue)
    monkeypatch.setattr(pl, "execute_render", fake_execute)

    approved_hash = pl._script_content_hash(script)
    first = pl.run_production(
        db,
        project.id,
        actor_id="operator",
        approved_script_hash=approved_hash,
        approved_script_version=1,
    )
    second = pl.run_production(
        db,
        project.id,
        actor_id="operator",
        approved_script_hash=approved_hash,
        approved_script_version=1,
    )

    assert first.id == second.id
    assert calls == {"audio": 1, "timeline": 1, "enqueue": 1, "execute": 1}
