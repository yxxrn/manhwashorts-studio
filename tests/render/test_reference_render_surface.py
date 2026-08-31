"""RED contracts for the reference-matched render and selected-voice surface."""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image


def _reference_scene(**overrides):
    values = {
        "asset_id": None,
        "start_time": 0.0,
        "end_time": 40.901,
        "focus_x": 0.5,
        "focus_y": 0.56,
        "focus_end_x": 0.5,
        "focus_end_y": 0.56,
        "motion_mode": "hold",
        "motion_intensity": "low",
        "motion_reason": "reference hold",
        "camera_curve": "static",
        "camera_intent": "neutral",
        "effect": "none",
        "disabled_effects": [],
        "transition": "cut",
        "overlay_text": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_reference_profile_exposes_render_surface_values_and_stable_hash():
    from app.services import reference_profile

    profile = reference_profile.REFERENCE_MATCHED_SHORTS_V1
    assert profile.caption_font_height_ratio == pytest.approx(0.028)
    assert profile.caption_italic is True
    assert profile.caption_highlight_current_word is False
    assert profile.caption_anchor == (0.50, 0.56)
    assert profile.caption_alignment == 5
    assert profile.caption_primary_color == "white"
    assert profile.caption_outline_color == "black"
    assert profile.caption_outline_pixels == 6
    assert profile.caption_shadow_color == "black"
    assert profile.caption_shadow_alpha_max <= 0.35
    assert profile.normal_zoom_max == pytest.approx(1.08)
    assert profile.impact_zoom_max == pytest.approx(1.14)
    canonical = reference_profile.canonical_profile_json(profile)
    assert reference_profile.profile_hash(profile) == reference_profile.profile_hash(profile)
    assert canonical.count('"caption_font_height_ratio"') == 1
    assert canonical.count('"caption_italic"') == 1
    assert canonical.count('"caption_highlight_current_word"') == 1


def test_reference_ass_is_one_word_white_centered_and_legacy_ass_stays_karaoke():
    from app.services import reference_profile, render
    from app.services.timeline import CueSpec

    profile = reference_profile.REFERENCE_MATCHED_SHORTS_V1
    ass = render.build_ass(
        [CueSpec(0, "WHY", 0.0, 1.0)], 1080, 1920, profile=profile
    )
    style_line = next(line for line in ass.splitlines() if line.startswith("Style: Caption"))
    fields = style_line.split(",")
    assert fields[2] == "54"
    assert fields[3] == "&H00FFFFFF"
    assert fields[7] == "-1"
    assert fields[8] == "-1"
    assert fields[16] == "6"
    assert fields[18] == "5"
    assert ass.count("Dialogue:") == 1
    assert "\\pos(540,1075)" in ass
    assert "WHY" in ass
    assert "\\c&H0000FFFF&" not in ass
    assert "\\k" not in ass and "\\K" not in ass
    assert "\\N" not in ass

    with pytest.raises(render.RenderError, match="reference subtitle"):
        render.build_ass(
            [CueSpec(0, "WHY?", 0.0, 1.0)], 1080, 1920, profile=profile
        )

    legacy = render.build_ass(
        [CueSpec(0, "This is a test", 0.0, 2.0)], 1080, 1920, "Anton"
    )
    assert "Style: Caption,Anton" in legacy
    assert legacy.count("Dialogue:") == 4
    assert legacy.count("\\c&H0000FFFF&") == 4


def test_reference_motion_uses_profile_caps_and_disables_procedural_effects():
    from app.services import reference_profile, render

    profile = reference_profile.REFERENCE_MATCHED_SHORTS_V1
    normal = render._motion_filter(
        "slow_push_in", 1080, 1920, 1.0, 30, profile=profile
    )
    impact = render._motion_filter(
        "push_in", 1080, 1920, 1.0, 30, profile=profile
    )
    assert "0.08" in normal
    assert "0.14" in impact
    for filter_graph in (normal, impact):
        assert not any(token in filter_graph for token in ("sin(", "cos(", "shake", "orbit"))
    assert render._procedural_effect("impact", "high", profile=profile) == "null"
    assert render._procedural_effect("atmospheric", "low", profile=profile) == "null"
    assert render.local_effects("impact", profile=profile) == ()
    assert render.local_effects("atmospheric", profile=profile) == ()


def test_build_render_request_carries_selected_reference_profile(monkeypatch, tmp_path):
    from app.models import PanelRegion, SourceAsset
    from app.services import pipeline, reference_profile, visual_scoring

    profile = reference_profile.REFERENCE_MATCHED_SHORTS_V1
    project = SimpleNamespace(
        id="project-a",
        template=profile.profile_id,
        title="Do not overlay this title",
    )
    script = SimpleNamespace(
        id="script-a",
        approved_at=object(),
        editorial_metadata={"editorial_review_confirmed": True},
    )
    segment = SimpleNamespace(
        storage_key="clip.wav",
        spoken_text="Review.",
        word_timings=[{"word": "Review.", "start": 0.0, "end": 40.901}],
    )
    audio_path = tmp_path / "clip.wav"
    audio_path.write_bytes(b"audio")
    panel_path = tmp_path / "panel.png"
    Image.new("RGB", (8, 6), (40, 50, 60)).save(panel_path)
    asset = SourceAsset(
        id="asset-reference",
        project_id=project.id,
        type="image",
        storage_key="panel.png",
        checksum="asset-checksum",
        original_checksum="asset-checksum",
        width=8,
        height=6,
        original_width=8,
        original_height=6,
    )
    evidence = visual_scoring.PanelVisualEvidence(
        contract_version=visual_scoring.VISUAL_EVIDENCE_CONTRACT_VERSION,
        panel_id="panel-reference",
        source_asset_id=asset.id,
        source_order=1,
        balloon_regions=(),
        protected_regions=(),
        balloon_mask_status="known_empty",
        mask_confidence=1.0,
        evidence_source="test_fixture",
        mask_reason="render-surface fixture affirmatively contains no speech balloons",
    )
    evidence_json = visual_scoring.panel_visual_evidence_json(evidence)
    region = PanelRegion(
        id="region-reference",
        story_analysis_id="analysis-reference",
        source_asset_id=asset.id,
        source_asset_checksum=asset.original_checksum,
        original_width=8,
        original_height=6,
        panel_id="panel-reference",
        source_order=1,
        bounds_json={"x": 0, "y": 0, "width": 8, "height": 6},
        observation_json={"visual_evidence": evidence_json},
    )
    scene = _reference_scene(
        asset_id=asset.id,
        panel_region_id=region.id,
        panel_id=region.panel_id,
        panel_bounds_json=region.bounds_json,
        visual_evidence_json=evidence_json,
        source_asset_checksum=asset.original_checksum,
    )

    monkeypatch.setattr(pipeline, "get_project", lambda _db, _id: project)
    monkeypatch.setattr(pipeline, "current_script", lambda _db, _id: script)
    monkeypatch.setattr(
        pipeline,
        "_approved_adaptive_reference_policy",
        lambda _script: {"adaptive": True},
    )
    monkeypatch.setattr(pipeline, "audio_segments", lambda _db, _id: [segment])
    monkeypatch.setattr(pipeline, "project_scenes", lambda _db, _id: [scene])
    monkeypatch.setattr(pipeline, "project_cues", lambda _db, _id: [])
    monkeypatch.setattr(pipeline, "cue_specs", lambda _cues: [])
    monkeypatch.setattr(pipeline, "project_assets", lambda _db, _id: [])
    monkeypatch.setattr(pipeline.storage, "workspace_dir", lambda *_args: tmp_path)
    monkeypatch.setattr(
        pipeline.storage,
        "path_for",
        lambda key: panel_path if key == asset.storage_key else audio_path,
    )
    monkeypatch.setattr(
        pipeline.storage,
        "exists",
        lambda key: key in {asset.storage_key, segment.storage_key},
    )
    monkeypatch.setattr(
        pipeline.tts_svc,
        "concat_audio",
        lambda _paths, output, gap=0.18: output.write_bytes(b"master"),
    )
    monkeypatch.setattr(pipeline.tts_svc, "probe_duration", lambda _path: 40.901)

    job = SimpleNamespace(
        project_id="project-a",
        kind="final",
        render_profile="Auto",
        encoder_requested="cpu",
    )
    def get(model, key):
        if model is SourceAsset and key == asset.id:
            return asset
        if model is PanelRegion and key == region.id:
            return region
        return None

    db = SimpleNamespace(flush=lambda: None, get=get)
    request = pipeline.build_render_request(db, job)
    assert request.profile is profile
    assert request.profile.profile_id == "reference_matched_shorts_v1"
    assert request.title_text == ""
    assert request.stabilized_reference_motion is True
    assert request.allow_conservative_full_panel is True
    assert all(not item.overlay_text for item in request.scenes)


def test_reference_final_encoder_and_probe_profile_are_hard_gates():
    from app.services import encoders, reference_profile, render

    profile = reference_profile.REFERENCE_MATCHED_SHORTS_V1
    valid = {
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "codec": "h264",
        "profile": "High",
        "pix_fmt": "yuv420p",
    }
    render.validate_reference_output(valid, profile)
    invalid = {**valid, "profile": "Main"}
    with pytest.raises(render.RenderError, match="reference.output_profile"):
        render.validate_reference_output(invalid, profile)

    selection = encoders.Selection(spec=encoders.VAAPI, requested="vaapi")
    with pytest.raises(render.RenderError, match="reference.encoder_profile"):
        render._validate_reference_encoder(selection, profile)
    for spec in (encoders.CPU, encoders.NVENC, encoders.QSV, encoders.VIDEOTOOLBOX):
        selection = encoders.Selection(spec=spec, requested=spec.key)
        args = encoders.video_args(selection, preview=False, final=True)
        assert args[args.index("-profile:v") + 1].lower() == "high"
        assert args[args.index("-pix_fmt") + 1] == "yuv420p"
        render._validate_reference_encoder(selection, profile)


@pytest.mark.parametrize(
    ("field", "bad_value", "expected_code"),
    [
        ("width", 720, "reference.output_resolution"),
        ("height", 1280, "reference.output_resolution"),
        ("fps", 29.97, "reference.output_fps"),
        ("codec", "hevc", "reference.output_codec"),
        ("profile", "Main", "reference.output_codec_profile"),
        ("pix_fmt", "nv12", "reference.output_pix_fmt"),
    ],
)
def test_reference_quality_reports_stable_output_profile_codes(field, bad_value, expected_code):
    from app.services import quality, reference_profile

    profile = reference_profile.REFERENCE_MATCHED_SHORTS_V1
    info = {
        "width": 1080,
        "height": 1920,
        "fps": 30.0,
        "codec": "h264",
        "profile": "High",
        "pix_fmt": "yuv420p",
    }
    info[field] = bad_value
    failures = quality.check_reference_output_profile(info, profile)
    assert expected_code in {result.code for result in failures}


def test_real_ffmpeg_probe_reports_reference_output_profile(tmp_path):
    from app.config import settings
    from app.services import render

    output = tmp_path / "reference-profile-probe.mp4"
    subprocess.run(
        [
            settings.ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=1080x1920:r=30:d=0.1",
            "-frames:v",
            "3",
            "-an",
            "-c:v",
            "libx264",
            "-profile:v",
            "high",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    info = render.probe(output)
    assert info["codec"] == "h264"
    assert info["profile"].lower() == "high"
    assert info["pix_fmt"] == "yuv420p"
    assert info["fps"] == pytest.approx(30.0)
    assert info["width"] == 1080
    assert info["height"] == 1920


def test_voice_defaults_and_english_selection_use_actual_resolved_voice(monkeypatch, tmp_path):
    from app.schemas import VoiceAuditionRequest, VoiceRequest
    from app.services import pipeline
    from app.services.tts import SpeechClip

    assert VoiceRequest().speed == pytest.approx(1.15)
    assert VoiceAuditionRequest(voice_ids=["a", "b", "c", "d"]).speed == pytest.approx(1.15)
    assert inspect.signature(pipeline.generate_voiceover).parameters["speed"].default == pytest.approx(1.15)

    project = SimpleNamespace(
        id="project-a",
        workspace_id="workspace-a",
        language="en",
        voice_id="selected-project-voice",
        pronunciations={},
    )
    script = SimpleNamespace(
        id="script-a",
        sections=[{"section": "hook", "text": "A punctuated story."}],
        warnings=[],
    )
    calls = []

    class Provider:
        name = "byok:custom_openai"

        def available(self):
            return True

        def synthesize(self, text, out_path, voice_id, speed):
            calls.append((text, voice_id, speed))
            out_path.write_bytes(b"RIFF audio")
            return SpeechClip(
                path=out_path,
                text=text,
                duration=1.0,
                voice_id="resolved-provider-voice",
                provider=self.name,
                word_timings=[],
                voice_profile={"provider": self.name, "voice_id": "resolved-provider-voice"},
            )

    added = []
    db = SimpleNamespace(
        scalars=lambda _query: [],
        add=added.append,
        flush=lambda: None,
    )
    monkeypatch.setattr(pipeline, "get_project", lambda _db, _id: project)
    monkeypatch.setattr(pipeline, "_script_for_media", lambda _db, _id: script)
    monkeypatch.setattr(
        pipeline.resolver_svc,
        "resolve_tts",
        lambda *_args, **_kwargs: (Provider(), SimpleNamespace(source="byok", model="tts-1")),
    )
    monkeypatch.setattr(pipeline.storage, "workspace_dir", lambda *_args: tmp_path)
    monkeypatch.setattr(
        pipeline.storage,
        "put_file",
        lambda *_args: SimpleNamespace(storage_key="projects/project-a/audio/clip.wav"),
    )
    monkeypatch.setattr(pipeline.storage, "delete", lambda *_args: None)
    monkeypatch.setattr(pipeline.script_svc, "apply_pronunciations", lambda text, _mapping: text)
    monkeypatch.setattr(pipeline.timeline_svc, "normalize_display_text", lambda text: text.upper())
    monkeypatch.setattr(pipeline.editorial_timing, "dramatic_events", lambda *_args: [])
    monkeypatch.setattr(pipeline, "audit", lambda *_args, **_kwargs: None)

    segments = pipeline.generate_voiceover(db, project.id)
    assert calls == [("A punctuated story.", "selected-project-voice", 1.15)]
    assert segments[0].voice_id == "resolved-provider-voice"
    assert segments[0].voice_profile_hash


def test_audition_default_speed_and_browser_request_are_reference_cadenced():
    from app.services import voice_auditions

    assert inspect.signature(voice_auditions.generate_auditions).parameters["speed"].default == pytest.approx(1.15)
    browser = Path("app/static/app.js").read_text(encoding="utf-8")
    assert "body: { speed: 1.15 }" in browser
