from __future__ import annotations

from datetime import UTC, datetime

from app.constants import JobStatus
from app.models import RenderJob, ScriptVersion
from app.services import pipeline as pl
from tests.factories.evidence import _project


def test_render_output_identity_normalizes_visible_watermark():
    class Project:
        watermark_enabled = True
        watermark_text = "  @rurushortss  "
    assert pl._render_output_identity(Project()) == {
        "version": "render-watermark-v1",
        "watermark_enabled": True,
        "watermark_text": "@rurushortss",
    }
    Project.watermark_enabled = False
    assert pl._render_output_identity(Project())["watermark_text"] == ""


def test_render_reuse_invalidates_when_watermark_changes(db, tmp_path):
    project = _project(db)
    script = ScriptVersion(project_id=project.id, version=1, generator="test", sections=[{"section": "hook", "text": "Grounded."}], approved_by="reviewer", approved_at=datetime.now(UTC), editorial_metadata={})
    db.add(script)
    db.flush()
    script_hash = pl._script_content_hash(script)
    output = tmp_path / "final.mp4"
    output.write_bytes(b"video")
    job = RenderJob(project_id=project.id, kind="final", status=JobStatus.SUCCEEDED, output_key=str(output))
    db.add(job)
    db.flush()
    script.editorial_metadata = {"production": {"script_hash": script_hash, "render_job_id": job.id}}
    project.watermark_enabled = False
    project.watermark_text = ""
    assert pl._render_stage_ready(db, project.id, script_hash) is job
    project.watermark_enabled = True
    project.watermark_text = "@rurushortss"
    assert pl._render_stage_ready(db, project.id, script_hash) is None
    script.editorial_metadata = {"production": {"script_hash": script_hash, "render_job_id": job.id, "render_output_identity": pl._render_output_identity(project)}}
    assert pl._render_stage_ready(db, project.id, script_hash) is job
    project.watermark_text = "@anotherchannel"
    assert pl._render_stage_ready(db, project.id, script_hash) is None
