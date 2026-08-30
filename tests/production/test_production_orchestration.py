"""Focused contracts for the explicit production boundary."""

from __future__ import annotations

from datetime import UTC, datetime


def test_evidence_v3_requires_exact_approval_and_never_falls_back(db):
    from app.services import pipeline as pl
    from tests.factories.evidence import _project, _seed_analysis, _seed_script

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
    from tests.factories.evidence import _project, _seed_analysis, _seed_script

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
    from tests.factories.evidence import _project

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
    monkeypatch.setattr(
        pl,
        "_ensure_final_thumbnail",
        lambda *_args, **_kwargs: {
            "thumbnail_path": str(tmp_path / "thumbnail.jpg"),
            "headline": "THIS CHANGED EVERYTHING",
            "variants": [],
        },
    )

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


def test_production_invalidates_legacy_audio_checkpoint_without_timing_identity(db, monkeypatch, tmp_path):
    from app.constants import JobStatus
    from app.models import RenderJob, ScriptVersion
    from app.services import pipeline as pl
    from tests.factories.evidence import _project

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
    approved_hash = pl._script_content_hash(script)
    script.editorial_metadata = {
        **script.editorial_metadata,
        "approved_script_hash": approved_hash,
        "production": {
            "script_hash": approved_hash,
            "script_version": 1,
            "audio_script_hash": approved_hash,
        },
    }
    db.flush()

    calls = {"audio": 0}
    captured = {}
    monkeypatch.setattr(pl, "_audio_stage_ready", lambda *_args: True)
    monkeypatch.setattr(pl, "_timeline_stage_ready", lambda *_args: True)
    monkeypatch.setattr(pl, "run_quality_checks", lambda *args, **kwargs: [])
    monkeypatch.setattr(pl, "_ensure_final_thumbnail", lambda *_a, **_k: {"thumbnail_path": str(tmp_path / "thumb.jpg"), "headline": "H", "variants": []})

    def fake_audio(*args, **kwargs):
        calls["audio"] += 1
        captured.update(kwargs)
        return []

    monkeypatch.setattr(pl, "generate_voiceover", fake_audio)
    monkeypatch.setattr(pl, "build_timeline", lambda *_a, **_k: [])
    output = tmp_path / "final.mp4"
    output.write_bytes(b"fixture")

    def fake_enqueue(db, project_id, **kwargs):
        job = RenderJob(project_id=project_id, kind="final", status=JobStatus.QUEUED)
        db.add(job)
        db.flush()
        return job

    def fake_execute(db, job_id):
        job = db.get(RenderJob, job_id)
        job.status = JobStatus.SUCCEEDED
        job.output_key = str(output)
        return job

    monkeypatch.setattr(pl, "enqueue_render", fake_enqueue)
    monkeypatch.setattr(pl, "execute_render", fake_execute)

    pl.run_production(
        db,
        project.id,
        actor_id="operator",
        approved_script_hash=approved_hash,
        approved_script_version=1,
    )
    assert calls["audio"] == 1
    assert captured["duration_bounds_s"] == (50.0, 60.0)
    identity = script.editorial_metadata["production"]["audio_timing_identity"]
    assert identity["version"] == pl.tts_svc.PRODUCTION_AUDIO_TIMING_POLICY_VERSION
    assert identity["duration_bounds_s"] == [50.0, 60.0]


def test_production_rejects_sub_50_second_adaptive_policy_before_media(db, monkeypatch):
    from app.models import ScriptVersion
    from app.services import pipeline as pl
    from tests.factories.evidence import _project

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
    metadata = dict(script.editorial_metadata or {})
    metadata["approved_script_hash"] = pl._script_content_hash(script)
    metadata["duration_policy_contract"] = {
        "version": "coherent_capacity_adaptive_v1",
        "adaptive": True,
        "target_duration_min_s": 24.35,
        "target_duration_max_s": 31.30,
    }
    script.editorial_metadata = metadata
    db.flush()

    media_calls = {"audio": 0, "timeline": 0, "render": 0}
    monkeypatch.setattr(pl, "generate_voiceover", lambda *_a, **_k: media_calls.__setitem__("audio", 1))
    monkeypatch.setattr(pl, "build_timeline", lambda *_a, **_k: media_calls.__setitem__("timeline", 1))
    monkeypatch.setattr(pl, "enqueue_render", lambda *_a, **_k: media_calls.__setitem__("render", 1))

    approved_hash = pl._script_content_hash(script)
    try:
        pl.run_production(
            db,
            project.id,
            actor_id="operator",
            approved_script_hash=approved_hash,
            approved_script_version=1,
        )
    except pl.PipelineError as exc:
        assert "standard 50-60 second window" in str(exc)
    else:  # pragma: no cover - contract guard
        raise AssertionError("sub-50 adaptive review policy reached final production")

    assert media_calls == {"audio": 0, "timeline": 0, "render": 0}


def test_nonpublishable_final_enqueue_allows_only_rights_blocker(db, monkeypatch):
    from types import SimpleNamespace

    from app.constants import CheckSeverity, JobStatus
    from app.services import pipeline as pl
    from app.services.quality import CheckResult
    from tests.factories.evidence import _project

    project = _project(db)
    monkeypatch.setattr(pl, "project_scenes", lambda *_args: [SimpleNamespace()])
    rights = CheckResult(
        code="rights.undeclared_assets",
        severity=CheckSeverity.ERROR,
        message="rights missing",
        passed=False,
    )
    monkeypatch.setattr(pl, "run_quality_checks", lambda *_args, **_kwargs: [rights])
    job = pl.enqueue_render(
        db, project.id, "final", actor_id="operator", allow_nonpublishable_artifact=True
    )
    assert job.status == JobStatus.QUEUED


def test_nonpublishable_final_enqueue_still_blocks_technical_failure(db, monkeypatch):
    from types import SimpleNamespace

    from app.constants import CheckSeverity
    from app.services import pipeline as pl
    from app.services.quality import CheckResult
    from tests.factories.evidence import _project

    project = _project(db)
    monkeypatch.setattr(pl, "project_scenes", lambda *_args: [SimpleNamespace()])
    failures = [
        CheckResult("rights.undeclared_assets", CheckSeverity.ERROR, "rights missing", False),
        CheckResult("subtitle.overflow", CheckSeverity.ERROR, "subtitle invalid", False),
    ]
    monkeypatch.setattr(pl, "run_quality_checks", lambda *_args, **_kwargs: failures)
    try:
        pl.enqueue_render(
            db, project.id, "final", actor_id="operator", allow_nonpublishable_artifact=True
        )
    except pl.PipelineError as exc:
        assert "Quality checks must pass" in str(exc)
    else:
        raise AssertionError("technical blocker was bypassed")


def test_standard_reference_production_uses_cadence_identity(db, monkeypatch, tmp_path):
    from app.constants import JobStatus
    from app.models import RenderJob, ScriptVersion
    from app.services import pipeline as pl
    from tests.factories.evidence import _project

    project = _project(db)
    project.template = "reference_matched_shorts_v2"
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
    approved_hash = pl._script_content_hash(script)
    script.editorial_metadata = {
        **script.editorial_metadata,
        "approved_script_hash": approved_hash,
    }
    db.flush()

    captured = {}
    monkeypatch.setattr(pl, "_audio_stage_ready", lambda *_a: True)
    monkeypatch.setattr(pl, "_timeline_stage_ready", lambda *_a: False)
    monkeypatch.setattr(pl, "generate_voiceover", lambda *_a, **_k: [])
    monkeypatch.setattr(pl, "run_quality_checks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        pl,
        "build_timeline",
        lambda *_a, **kwargs: captured.update(kwargs) or [],
    )
    output = tmp_path / "final.mp4"
    output.write_bytes(b"fixture")

    def fake_enqueue(db, project_id, **kwargs):
        job = RenderJob(project_id=project_id, kind="final", status=JobStatus.QUEUED)
        db.add(job)
        db.flush()
        return job

    def fake_execute(db, job_id):
        job = db.get(RenderJob, job_id)
        job.status = JobStatus.SUCCEEDED
        job.output_key = str(output)
        return job

    monkeypatch.setattr(pl, "enqueue_render", fake_enqueue)
    monkeypatch.setattr(pl, "execute_render", fake_execute)
    monkeypatch.setattr(
        pl,
        "_ensure_final_thumbnail",
        lambda *_a, **_k: {
            "thumbnail_path": str(tmp_path / "thumb.jpg"),
            "headline": "H",
            "variants": [],
        },
    )

    pl.run_production(
        db,
        project.id,
        actor_id="operator",
        approved_script_hash=approved_hash,
        approved_script_version=1,
    )

    assert captured["standard_reference_production"] is True
    assert captured["adaptive_reference_production"] is False
    identity = script.editorial_metadata["production"]["timeline_planning_identity"]
    assert identity["version"] == pl.reference_profile.PRODUCTION_REFERENCE_CADENCE_POLICY_VERSION
    assert identity["standard_reference_production"] is True
    assert identity["adaptive_reference_production"] is False
