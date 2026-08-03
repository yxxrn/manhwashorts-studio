"""End-to-end pipeline tests, including real FFmpeg rendering.

Marked ``slow`` because rendering runs FFmpeg for real. Run the fast suite with
``pytest -m "not slow"``; run everything with plain ``pytest``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow


def _ffmpeg_missing() -> bool:
    return shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None


requires_ffmpeg = pytest.mark.skipif(_ffmpeg_missing(), reason="ffmpeg/ffprobe not installed")


def _probe(path: Path) -> dict:
    """Independent probe: verify the artifact with ffprobe, not app code."""
    import json

    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    data = json.loads(out)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), {})
    audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), {})
    return {
        "duration": float(data["format"]["duration"]),
        "width": int(video.get("width", 0)),
        "height": int(video.get("height", 0)),
        "video_codec": video.get("codec_name", ""),
        "audio_codec": audio.get("codec_name", ""),
        "has_audio": bool(audio),
    }


def _seed_project(db, recap_text: str, panel_count: int = 4) -> str:
    """Create a workspace, project, and rights-declared assets directly in the DB."""
    import io

    from PIL import Image

    from app.constants import AssetType, LicenseType, RightsStatus
    from app.models import Project, SourceAsset, User, Workspace
    from app.security import hash_password
    from app.services import ingest, storage

    user = User(email="pipeline@example.com", name="Pipe", password_hash=hash_password("pass12345"))
    db.add(user)
    db.flush()
    workspace = Workspace(owner_id=user.id, name="WS")
    db.add(workspace)
    db.flush()

    project = Project(
        workspace_id=workspace.id,
        title="E2E Project",
        manhwa_title="Judul Uji",
        chapter="7",
        target_duration=60,
        voice_id="id",
        cta_text="Komentar di bawah kalau kamu punya teori.",
    )
    db.add(project)
    db.flush()

    text_asset = ingest.ingest_text(project.id, recap_text, "recap.txt")
    db.add(
        SourceAsset(
            project_id=project.id,
            type=text_asset.type,
            original_filename=text_asset.original_filename,
            storage_key=text_asset.storage_key,
            mime_type=text_asset.mime_type,
            size_bytes=text_asset.size_bytes,
            checksum=text_asset.checksum,
            extracted_text=text_asset.extracted_text,
            rights_owner="Tester",
            license_type=LicenseType.OWNED,
            rights_status=RightsStatus.DECLARED,
            order_index=0,
        )
    )

    for i in range(panel_count):
        # Distinct sizes so cropping is exercised on portrait, landscape, square.
        size = [(1200, 1600), (1600, 900), (1000, 1000), (900, 1600)][i % 4]
        img = Image.new("RGB", size, (30 + i * 40, 40, 90))
        buffer = io.BytesIO()
        img.save(buffer, "JPEG", quality=85)
        data = buffer.getvalue()
        stored = storage.put_bytes(f"projects/{project.id}/images", f"p{i}.jpg", data)
        db.add(
            SourceAsset(
                project_id=project.id,
                type=AssetType.IMAGE,
                original_filename=f"p{i}.jpg",
                storage_key=stored.storage_key,
                mime_type="image/jpeg",
                size_bytes=stored.size_bytes,
                checksum=stored.checksum,
                width=size[0],
                height=size[1],
                rights_owner="Tester",
                license_type=LicenseType.OWNED,
                rights_status=RightsStatus.DECLARED,
                order_index=i + 1,
            )
        )
    db.flush()
    return project.id


def test_draft_pipeline_produces_consistent_timeline(db, recap_text):
    """Audio, scenes, and cues must all agree on the timeline length."""
    from app.services import pipeline as pl

    project_id = _seed_project(db, recap_text)
    summary = pl.generate_draft(db, project_id, seed=42)

    assert summary["segments"] == 5
    assert summary["scenes"] > 0
    assert summary["cues"] > 0

    script = pl.current_script(db, project_id)
    segments = pl.audio_segments(db, script.id)
    scenes = pl.project_scenes(db, project_id)
    cues = pl.project_cues(db, project_id)

    audio_end = max(s.end_time for s in segments)
    scene_end = max(s.end_time for s in scenes)
    cue_end = max(c.end_time for c in cues)

    # Regression: scenes used to fall short by the sum of inter-beat gaps.
    assert scene_end == pytest.approx(audio_end, abs=0.05)
    assert cue_end <= audio_end + 0.05

    # Regression: cues used to restart at zero for every segment.
    for a, b in zip(cues, cues[1:], strict=False):
        assert b.start_time >= a.end_time - 0.01
    assert all(c.end_time > c.start_time for c in cues)


def test_short_audio_padding_extends_last_scene_to_sixty_seconds(db, recap_text):
    from app.services import pipeline as pl

    project_id = _seed_project(db, recap_text)
    pl.generate_draft(db, project_id, seed=42)
    script = pl.current_script(db, project_id)
    pl.approve_script(db, script.id, actor_id="test")
    job = pl.enqueue_render(db, project_id, "preview", actor_id="test")
    request = pl.build_render_request(db, job)
    assert request.audio_path is not None
    assert request.scenes[-1].end_time >= 60.0


def test_scenes_reference_only_declared_assets(db, recap_text):
    from app.services import pipeline as pl

    project_id = _seed_project(db, recap_text)
    pl.generate_draft(db, project_id, seed=42)

    asset_ids = {a.id for a in pl.project_assets(db, project_id)}
    for scene in pl.project_scenes(db, project_id):
        assert scene.asset_id in asset_ids


def test_quality_passes_for_well_formed_project(db, recap_text):
    from app.services import pipeline as pl
    from app.services.quality import summarise

    project_id = _seed_project(db, recap_text)
    pl.generate_draft(db, project_id, seed=42)
    script = pl.current_script(db, project_id)
    pl.approve_script(db, script.id, actor_id="test")

    results = pl.run_quality_checks(db, project_id)
    summary = summarise(results)
    assert summary["errors"] == 0, f"unexpected blocking errors: {summary['error_codes']}"


def test_render_requires_approved_script(db, recap_text):
    from app.services import pipeline as pl

    project_id = _seed_project(db, recap_text)
    pl.generate_draft(db, project_id, seed=42)
    # Script deliberately not approved.
    with pytest.raises(pl.PipelineError, match="[Qq]uality"):
        pl.enqueue_render(db, project_id, "final", actor_id="test")


@requires_ffmpeg
def test_full_render_produces_playable_short(db, recap_text):
    """The headline requirement: a real, playable 9:16 MP4 with audio."""
    from app.constants import JobStatus
    from app.services import pipeline as pl

    project_id = _seed_project(db, recap_text)
    pl.generate_draft(db, project_id, seed=42)
    script = pl.current_script(db, project_id)
    pl.approve_script(db, script.id, actor_id="test")

    job = pl.enqueue_render(db, project_id, "final", actor_id="test")
    job = pl.execute_render(db, job.id)

    assert job.status == JobStatus.SUCCEEDED, f"{job.error_code}: {job.error_message}"
    assert job.progress == 100
    assert job.checksum

    output = Path(job.output_key)
    assert output.is_file()
    assert output.stat().st_size > 100_000

    info = _probe(output)
    assert info["width"] == 1080 and info["height"] == 1920
    assert info["video_codec"] == "h264"
    assert info["has_audio"] and info["audio_codec"] == "aac"

    # Video length must track the narration, not drift from it.
    segments = pl.audio_segments(db, script.id)
    audio_end = max(s.end_time for s in segments)
    assert info["duration"] == pytest.approx(audio_end, abs=0.6)

    # Sidecar artifacts.
    assert Path(job.subtitle_key).is_file()
    assert " --> " in Path(job.subtitle_key).read_text(encoding="utf-8")


@requires_ffmpeg
def test_burned_subtitles_appear_in_pixels(db, recap_text, tmp_path):
    """Captions must be rendered into the frame, not just exported as SRT."""
    from PIL import Image

    from app.services import pipeline as pl

    project_id = _seed_project(db, recap_text)
    pl.generate_draft(db, project_id, seed=42)
    script = pl.current_script(db, project_id)
    pl.approve_script(db, script.id, actor_id="test")
    job = pl.execute_render(db, pl.enqueue_render(db, project_id, "final", actor_id="test").id)
    assert job.status == "succeeded", job.error_message

    # Sample a frame inside the first cue, where narration is definitely playing.
    cues = pl.project_cues(db, project_id)
    timestamp = (cues[0].start_time + cues[0].end_time) / 2
    frame = tmp_path / "frame.png"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{timestamp:.2f}", "-i", job.output_key,
            "-vframes", "1", str(frame),
        ],
        check=True,
    )

    with Image.open(frame) as img:
        width, height = img.size
        band = img.crop((0, int(height * 0.60), width, int(height * 0.88))).convert("RGB")
        pixels = list(band.getdata())
        near_white = sum(r > 235 and g > 235 and b > 235 for r, g, b in pixels)
        active_yellow = sum(r > 180 and g > 180 and b < 120 for r, g, b in pixels)
    assert near_white + active_yellow > 1000, "no caption pixels found in the subtitle safe area"


@requires_ffmpeg
def test_render_failure_is_retryable(db, recap_text):
    """A failed render must be diagnosable and retryable without losing history."""
    from app.constants import JobStatus
    from app.services import pipeline as pl

    project_id = _seed_project(db, recap_text)
    pl.generate_draft(db, project_id, seed=42)
    script = pl.current_script(db, project_id)
    pl.approve_script(db, script.id, actor_id="test")

    job = pl.enqueue_render(db, project_id, "final", actor_id="test")

    # Break the render by deleting the audio files it depends on.
    from app.services import storage

    for segment in pl.audio_segments(db, script.id):
        storage.delete(segment.storage_key)

    failed = pl.execute_render(db, job.id)
    assert failed.status == JobStatus.FAILED
    assert failed.error_message  # actionable message, not a bare traceback

    retry = pl.retry_render(db, failed.id, actor_id="test")
    assert retry.id != failed.id
    assert retry.attempt == failed.attempt + 1
    assert retry.status == JobStatus.QUEUED
    # The failed job is preserved for the audit trail.
    assert db.get(type(failed), failed.id).status == JobStatus.FAILED


@requires_ffmpeg
def test_publish_dry_run_writes_receipt_and_no_fabricated_stats(db, recap_text):
    from app.constants import UploadStatus
    from app.services import pipeline as pl
    from app.services import publish as publish_svc

    project_id = _seed_project(db, recap_text)
    pl.generate_draft(db, project_id, seed=42)
    script = pl.current_script(db, project_id)
    pl.approve_script(db, script.id, actor_id="test")
    job = pl.execute_render(db, pl.enqueue_render(db, project_id, "final", actor_id="test").id)
    assert job.status == "succeeded", job.error_message

    publication = publish_svc.publish(db, project_id, privacy_status="private", actor_id="test")
    assert publication.upload_status == UploadStatus.UPLOADED
    assert publication.youtube_video_id.startswith("dryrun_")
    assert publication.attempt == 1

    # Dry run must not invent analytics.
    assert publish_svc.sync_stats(db, publication.id, actor_id="test") is None


@requires_ffmpeg
def test_public_publish_double_gated(db, recap_text):
    from app.services import pipeline as pl
    from app.services import publish as publish_svc

    project_id = _seed_project(db, recap_text)
    pl.generate_draft(db, project_id, seed=42)
    script = pl.current_script(db, project_id)
    pl.approve_script(db, script.id, actor_id="test")
    pl.execute_render(db, pl.enqueue_render(db, project_id, "final", actor_id="test").id)

    with pytest.raises(pl.PipelineError, match="[Pp]ublic"):
        publish_svc.publish(db, project_id, privacy_status="public", confirm_public=True)


@requires_ffmpeg
def test_publish_detects_tampered_artifact(db, recap_text):
    """A file changed after render must not be uploaded silently."""
    from app.services import pipeline as pl
    from app.services import publish as publish_svc

    project_id = _seed_project(db, recap_text)
    pl.generate_draft(db, project_id, seed=42)
    script = pl.current_script(db, project_id)
    pl.approve_script(db, script.id, actor_id="test")
    job = pl.execute_render(db, pl.enqueue_render(db, project_id, "final", actor_id="test").id)

    Path(job.output_key).write_bytes(b"not the rendered video")
    with pytest.raises(pl.PipelineError, match="checksum"):
        publish_svc.publish(db, project_id, privacy_status="private", actor_id="test")


def test_audit_log_records_pipeline_actions(db, recap_text):
    from sqlalchemy import select

    from app.models import AuditLog
    from app.services import pipeline as pl

    project_id = _seed_project(db, recap_text)
    pl.generate_draft(db, project_id, actor_id="tester", seed=42)
    script = pl.current_script(db, project_id)
    pl.approve_script(db, script.id, actor_id="tester")

    actions = {row.action for row in db.scalars(select(AuditLog))}
    assert {"analysis.run", "script.generate", "voice.generate", "timeline.build",
            "script.approve"} <= actions


def test_regenerating_voice_replaces_old_segments(db, recap_text):
    """Re-running a stage must replace its output, not accumulate duplicates."""
    from app.services import pipeline as pl

    project_id = _seed_project(db, recap_text)
    pl.generate_draft(db, project_id, seed=42)
    script = pl.current_script(db, project_id)

    first = pl.audio_segments(db, script.id)
    pl.generate_voiceover(db, project_id, actor_id="test")
    second = pl.audio_segments(db, script.id)

    assert len(second) == len(first)
    assert {s.id for s in second} != {s.id for s in first}
