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


def _seed_project(db, recap_text: str, panel_count: int = 12) -> str:
    """Create a workspace, project, and rights-declared assets directly in the DB."""
    import io

    from PIL import Image, ImageDraw

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
        language="id",
        voice_id="id",
        template="classic_test",
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
        base = (
            35 + (i * 37) % 170,
            35 + (i * 53) % 170,
            35 + (i * 71) % 170,
        )
        img = Image.new("RGB", size, base)
        draw = ImageDraw.Draw(img)
        width, height = size
        for band in range(1, 6):
            y = band * height // 6
            accent = tuple((channel + band * 29 + i * 11) % 220 + 20 for channel in base)
            draw.rectangle((0, max(0, y - height // 40), width, min(height, y + height // 40)), fill=accent)
        inset = max(12, min(width, height) // 10)
        draw.rectangle((inset, inset, width - inset, height - inset), outline=(245, 245, 245), width=max(4, inset // 12))
        radius = max(20, min(width, height) // 7)
        cx = width * (2 + (i % 3)) // 5
        cy = height * (2 + ((i // 3) % 3)) // 5
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=(15, 15, 15), width=max(5, radius // 12))
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
    _seed_reconciled_analysis(db, project.id)
    return project.id


def _seed_reconciled_analysis(db, project_id: str) -> None:
    """Persist a provider-free vision fixture over the real image assets."""
    import hashlib

    from sqlalchemy import select

    from app.constants import AssetType
    from app.models import AuditLog, PanelRegion, SourceAsset, StoryAnalysis
    from app.services.analyzer_contract import load_analyzer_instruction

    assets = list(
        db.scalars(
            select(SourceAsset)
            .where(SourceAsset.project_id == project_id, SourceAsset.type == AssetType.IMAGE)
            .order_by(SourceAsset.order_index, SourceAsset.id)
        )
    )
    assert assets
    version, digest, _ = load_analyzer_instruction()
    coverage_hash = hashlib.sha256(f"pipeline-fixture:{project_id}".encode()).hexdigest()
    panel_ids = tuple(f"pipeline-panel-{index + 1:03d}" for index in range(len(assets)))

    observations = []
    for index, (panel_id, asset) in enumerate(zip(panel_ids, assets, strict=True)):
        asset.original_checksum = asset.checksum
        asset.original_width = asset.width
        asset.original_height = asset.height
        asset.source_bounds_json = {"x": 0, "y": 0, "width": asset.width, "height": asset.height}
        asset.strip_order = index
        asset.region_order = 0
        asset.trim_classification = "unsliced"
        asset.coverage_map_hash = coverage_hash
        observations.append({
            "panel_id": panel_id,
            "source_asset_id": asset.id,
            "strip_region_id": f"pipeline-region-{index + 1:03d}",
            "source_index": index,
            "region_bounds": {"x": 0, "y": 0, "width": asset.width, "height": asset.height},
            "coverage_map_version": "vision-coverage-v2",
            "coverage_map_hash": coverage_hash,
            "visible_facts": [f"Synthetic panel {index + 1} shows the chapter progressing."],
            "dialogue_or_ocr": [],
            "inferences": [],
            "uncertainties": [],
            "evidence_refs": [panel_id],
        })

    evidence_groups = [list(panel_ids[offset::3]) for offset in range(3)]
    claims = [
        {
            "claim_id": f"claim-{index + 1}",
            "claim_type": "fact",
            "text": f"The visual sequence establishes chapter beat {index + 1}.",
            "qualification": "This is synthetic fixture evidence for the media pipeline test.",
            "evidence_panel_ids": group,
        }
        for index, group in enumerate(evidence_groups) if group
    ]
    claim_by_id = {claim["claim_id"]: claim for claim in claims}

    def evidence(*claim_ids: str) -> list[str]:
        return list(dict.fromkeys(
            panel_id
            for claim_id in claim_ids
            for panel_id in claim_by_id[claim_id]["evidence_panel_ids"]
        ))

    c1 = claims[0]["claim_id"]
    c2 = claims[min(1, len(claims) - 1)]["claim_id"]
    c3 = claims[min(2, len(claims) - 1)]["claim_id"]
    passages = [
        {"passage_id": "pipeline-hook", "editorial_role": "hook", "text": "A routine mission turns dangerous when Rian discovers evidence that changes what the team expects.", "claim_ids": [c1], "evidence_panel_ids": evidence(c1)},
        {"passage_id": "pipeline-setup", "editorial_role": "setup", "text": "Across the chapter, each panel shows Rian moving from uncertainty toward a choice while the surrounding threat becomes harder to ignore.", "claim_ids": [c2], "evidence_panel_ids": evidence(c2)},
        {"passage_id": "pipeline-escalation", "editorial_role": "escalation", "text": "The pressure builds because every new detail narrows his options, forcing him to act before the situation closes around him and leaves the rest of the team unable to respond.", "claim_ids": [c2, c3], "evidence_panel_ids": evidence(c2, c3)},
        {"passage_id": "pipeline-insight", "editorial_role": "editorial_insight", "text": "What matters is not raw strength but the decision pattern: Rian keeps converting setbacks into information that gives him one more move.", "claim_ids": [c1, c3], "evidence_panel_ids": evidence(c1, c3)},
        {"passage_id": "pipeline-payoff", "editorial_role": "payoff_open_loop", "text": "By the final beat, the evidence points forward, but what will Rian risk when the next opening appears?", "claim_ids": [c3], "evidence_panel_ids": evidence(c3)},
    ]
    manifest = {
        "total_panels": len(panel_ids), "processed_panels": len(panel_ids), "panel_ids": list(panel_ids),
        "source_content_coverage_ratio": 1.0, "unresolved_material_area": 0, "material_unresolved_regions": [],
        "reconciliation_complete": True, "coverage_map_version": "vision-coverage-v2", "coverage_map_hash": coverage_hash,
        "total_canonical_panels": len(panel_ids), "persisted_canonical_panels": len(panel_ids),
        "processed_canonical_panel_count": len(panel_ids),
    }
    row = StoryAnalysis(
        project_id=project_id, analysis_run_id=f"pipeline-fixture-{project_id[:8]}", state="RECONCILED",
        provider_type="fixture", provider_name="synthetic-vision", model_name="offline-fixture",
        instruction_version=version, instruction_sha256=digest, coverage_manifest_json=manifest,
        continuity_ledger_json={
            "chunks": [{"chunk_id": "pipeline-chunk", "panel_ids": list(panel_ids)}],
            "entities": [{"entity_id": "entity-rian", "canonical_name": "Rian", "aliases": [], "panel_ids": list(panel_ids)}],
            "motives": [], "state_changes": [], "causal_links": [], "reconciled_after_final_chunk": True,
        },
        evidence_graph_json={"claims": claims, "script_passages": passages},
        story_spine_json={
            "who_wants_what": "Rian wants to understand the changing situation.",
            "obstacle": "The threat keeps narrowing his options.",
            "decision": "He keeps moving while using each setback as information.",
            "consequence": "His choices create one more opening.",
            "changed_stakes": "The team now depends on what he discovers next.",
            "unresolved_question": "What will Rian risk when the next opening appears?",
        },
        reconciliation_json={
            "coverage_map_hash": coverage_hash, "coverage_map_version": "vision-coverage-v2",
            "canonical_panel_count": len(panel_ids), "processed_panel_count": len(panel_ids),
            "chain_reconciled": True, "chain_errors": [],
        },
    )
    db.add(row)
    db.flush()
    for index, (panel_id, asset, observation) in enumerate(zip(panel_ids, assets, observations, strict=True)):
        db.add(PanelRegion(
            story_analysis_id=row.id, source_asset_id=asset.id, source_asset_checksum=asset.checksum,
            original_width=asset.width, original_height=asset.height, strip_region_id=observation["strip_region_id"],
            panel_id=panel_id, source_order=index, bounds_json=observation["region_bounds"],
            region_class="canonical_panel", segmentation_confidence=1.0, segmentation_version="vision-coverage-v2",
            coverage_map_hash=coverage_hash, observation_json=observation, chunk_index=0,
            evidence_refs_json=observation["evidence_refs"],
        ))
    db.add(AuditLog(actor_id="fixture", action="analysis.fixture", entity_type="project", entity_id=project_id, detail={"panel_count": len(panel_ids)}))
    db.flush()


def _prepare_media(db, project_id: str, *, actor_id: str = "test", seed: int = 42):
    from app.services import pipeline as pl
    draft = pl.generate_draft(db, project_id, actor_id=actor_id, seed=seed)
    script = pl.current_script(db, project_id)
    pl.approve_script(db, script.id, actor_id=actor_id, editorial_review_confirmed=True)
    segments = pl.generate_voiceover(
        db, project_id, actor_id=actor_id, provider_name="espeak", speed=0.75
    )
    scenes = pl.build_timeline(db, project_id, actor_id=actor_id)
    return draft, script, segments, scenes, pl.project_cues(db, project_id)


def test_draft_pipeline_produces_consistent_timeline(db, recap_text):
    """Audio, scenes, and cues must all agree on the timeline length."""
    from app.services import pipeline as pl

    project_id = _seed_project(db, recap_text)
    summary = pl.generate_draft(db, project_id, seed=42)
    assert summary["segments"] == 0
    assert summary["scenes"] == 0
    assert summary["cues"] == 0

    script = pl.current_script(db, project_id)
    pl.approve_script(db, script.id, actor_id="test", editorial_review_confirmed=True)
    segments = pl.generate_voiceover(db, project_id, actor_id="test", provider_name="espeak")
    scenes = pl.build_timeline(db, project_id, actor_id="test")
    cues = pl.project_cues(db, project_id)
    assert len(segments) == 5
    assert scenes and cues

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


def test_short_audio_does_not_extend_beyond_narration(db, recap_text):
    from app.services import pipeline as pl

    project_id = _seed_project(db, recap_text)
    _draft, script, _segments, _scenes, _cues = _prepare_media(db, project_id)
    job = pl.enqueue_render(db, project_id, "preview", actor_id="test")
    request = pl.build_render_request(db, job)
    assert request.audio_path is not None
    from app.services.tts import probe_duration
    assert request.scenes[-1].end_time == pytest.approx(probe_duration(request.audio_path), abs=0.6)


def test_scenes_reference_only_declared_assets(db, recap_text):
    from app.services import pipeline as pl

    project_id = _seed_project(db, recap_text)
    _prepare_media(db, project_id)

    asset_ids = {a.id for a in pl.project_assets(db, project_id)}
    for scene in pl.project_scenes(db, project_id):
        assert scene.asset_id in asset_ids


def test_quality_passes_for_well_formed_project(db, recap_text):
    from app.services import pipeline as pl
    from app.services.quality import summarise

    project_id = _seed_project(db, recap_text)
    _draft, script, _segments, _scenes, _cues = _prepare_media(db, project_id)

    results = pl.run_quality_checks(db, project_id)
    summary = summarise(results)
    assert summary["errors"] == 0, f"unexpected blocking errors: {[(r.code, r.detail) for r in results if not r.passed]}"


def test_render_requires_built_timeline(db, recap_text):
    from app.services import pipeline as pl

    project_id = _seed_project(db, recap_text)
    pl.generate_draft(db, project_id, seed=42)
    # Draft generation deliberately stops before audio/timeline construction.
    with pytest.raises(pl.PipelineError, match="[Tt]imeline"):
        pl.enqueue_render(db, project_id, "final", actor_id="test")


@requires_ffmpeg
def test_full_render_produces_playable_short(db, recap_text):
    """The headline requirement: a real, playable 9:16 MP4 with audio."""
    from app.constants import JobStatus
    from app.services import pipeline as pl

    project_id = _seed_project(db, recap_text)
    _draft, script, _segments, _scenes, _cues = _prepare_media(db, project_id)

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
    _draft, script, _segments, _scenes, _cues = _prepare_media(db, project_id)
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
        pixels = list(band.get_flattened_data())
        near_white = sum(r > 235 and g > 235 and b > 235 for r, g, b in pixels)
        active_yellow = sum(r > 180 and g > 180 and b < 120 for r, g, b in pixels)
    assert near_white + active_yellow > 1000, "no caption pixels found in the subtitle safe area"


@requires_ffmpeg
def test_render_failure_is_retryable(db, recap_text):
    """A failed render must be diagnosable and retryable without losing history."""
    from app.constants import JobStatus
    from app.services import pipeline as pl

    project_id = _seed_project(db, recap_text)
    _draft, script, _segments, _scenes, _cues = _prepare_media(db, project_id)

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
def test_publish_dry_run_writes_receipt_and_no_fabricated_stats(db, recap_text, monkeypatch):
    from app.constants import UploadStatus
    from app.services import pipeline as pl
    from app.services import publish as publish_svc
    from app.services.youtube_browser import BrowserPublishResult

    class DryRunBrowserPublisher:
        def __init__(self, account_id=None):
            self.account_id = account_id or "default"

        def publish(self, **kwargs):
            return BrowserPublishResult(
                video_id="dryrun_browser_test",
                privacy_status=str(kwargs.get("privacy_status") or "private"),
                upload_status="uploaded",
                stages=["published"],
                thumbnail_status="uploaded",
            )

    monkeypatch.setattr(publish_svc, "YouTubeStudioBrowserPublisher", DryRunBrowserPublisher)

    # Use a larger visual fixture so the new same-panel hard gate is exercised
    # by a production-shaped timeline rather than a four-panel compatibility set.
    project_id = _seed_project(db, recap_text, panel_count=12)
    _prepare_media(db, project_id)
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
    _draft, script, _segments, _scenes, _cues = _prepare_media(db, project_id)
    pl.execute_render(db, pl.enqueue_render(db, project_id, "final", actor_id="test").id)

    with pytest.raises(pl.PipelineError, match="[Pp]ublic"):
        publish_svc.publish(db, project_id, privacy_status="public", confirm_public=True)


@requires_ffmpeg
def test_publish_detects_tampered_artifact(db, recap_text):
    """A file changed after render must not be uploaded silently."""
    from app.services import pipeline as pl
    from app.services import publish as publish_svc

    project_id = _seed_project(db, recap_text)
    _draft, script, _segments, _scenes, _cues = _prepare_media(db, project_id)
    job = pl.execute_render(db, pl.enqueue_render(db, project_id, "final", actor_id="test").id)

    Path(job.output_key).write_bytes(b"not the rendered video")
    with pytest.raises(pl.PipelineError, match="checksum"):
        publish_svc.publish(db, project_id, privacy_status="private", actor_id="test")


def test_audit_log_records_pipeline_actions(db, recap_text):
    from sqlalchemy import select

    from app.models import AuditLog

    project_id = _seed_project(db, recap_text)
    _prepare_media(db, project_id, actor_id="tester")

    actions = {row.action for row in db.scalars(select(AuditLog))}
    assert {"script.generate", "voice.generate", "timeline.build", "script.approve"} <= actions


def test_regenerating_voice_replaces_old_segments(db, recap_text):
    """Re-running a stage must replace its output, not accumulate duplicates."""
    from app.services import pipeline as pl

    project_id = _seed_project(db, recap_text)
    _draft, script, _segments, _scenes, _cues = _prepare_media(db, project_id)

    first = pl.audio_segments(db, script.id)
    pl.generate_voiceover(db, project_id, actor_id="test", provider_name="espeak")
    second = pl.audio_segments(db, script.id)

    assert len(second) == len(first)
    assert {s.id for s in second} != {s.id for s in first}
