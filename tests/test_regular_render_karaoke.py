"""Focused RED contracts for the regular production karaoke/render boundary."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image


def _timings(*words: tuple[str, float, float]) -> list[dict[str, object]]:
    return [
        {
            "word": word,
            "start": start,
            "end": end,
        }
        for word, start, end in words
    ]


def test_shared_production_chunker_is_deterministic_and_keeps_spoken_text_immutable():
    from app.services import subtitle_karaoke

    spoken = "First turn, then the wounded pair escapes."
    timings = _timings(
        ("First", 0.0, 0.5),
        ("turn,", 0.5, 1.0),
        ("then", 1.0, 1.5),
        ("the", 1.5, 2.0),
        ("wounded", 2.0, 2.5),
        ("pair", 2.5, 3.0),
        ("escapes.", 3.0, 3.5),
    )

    first = subtitle_karaoke.build_sentence_caption_groups(spoken, timings)
    second = subtitle_karaoke.build_sentence_caption_groups(spoken, timings)

    assert spoken == "First turn, then the wounded pair escapes."
    assert first == second
    assert [word.text for word in first[0].words] == [
        "FIRST",
        "TURN",
        "THEN",
        "THE",
        "WOUNDED",
        "PAIR",
        "ESCAPES",
    ]
    assert all(word.text.isalnum() and word.text == word.text.upper() for group in first for word in group.words)
    assert all(len(group.words) >= 2 for group in first)


def test_regular_profile_request_carries_sentence_groups_and_timing_contract(monkeypatch, tmp_path: Path):
    from app.services import pipeline, reference_profile

    project = SimpleNamespace(
        id="project-karaoke",
        template=reference_profile.REFERENCE_MATCHED_SHORTS_V2.profile_id,
        title="No regular title overlay",
    )
    script = SimpleNamespace(id="script-karaoke")
    audio = SimpleNamespace(
        storage_key="voice.wav",
        section="hook",
        start_time=0.0,
        end_time=3.5,
        duration=3.5,
        spoken_text="First turn, then the wounded pair escapes.",
        text="First turn, then the wounded pair escapes.",
        word_timings=_timings(
            ("First", 0.0, 0.5),
            ("turn,", 0.5, 1.0),
            ("then", 1.0, 1.5),
            ("the", 1.5, 2.0),
            ("wounded", 2.0, 2.5),
            ("pair", 2.5, 3.0),
            ("escapes.", 3.0, 3.5),
        ),
    )
    source = tmp_path / "source.png"
    Image.new("RGB", (1080, 1920), (30, 40, 50)).save(source)
    asset = SimpleNamespace(
        id="asset-karaoke",
        storage_key="panel.png",
        original_checksum="checksum-karaoke",
        checksum="checksum-karaoke",
    )
    scene = SimpleNamespace(
        asset_id=asset.id,
        start_time=0.0,
        end_time=3.5,
        focus_x=0.5,
        focus_y=0.5,
        focus_end_x=0.55,
        focus_end_y=0.5,
        motion_mode="slow_push_in",
        motion_intensity="low",
        motion_reason="stable reference motion",
        camera_curve="slow_push_in",
        camera_intent="neutral",
        effect="none",
        disabled_effects=[],
        transition="cut",
        overlay_text="",
        panel_region_id="region-karaoke",
        panel_id="panel-karaoke",
        panel_bounds_json={"x": 0, "y": 0, "width": 1080, "height": 1920},
        visual_evidence_json={},
        source_asset_checksum=asset.original_checksum,
        rejected_candidates=[],
    )

    monkeypatch.setattr(pipeline, "get_project", lambda _db, _id: project)
    monkeypatch.setattr(pipeline, "current_script", lambda _db, _id: script)
    monkeypatch.setattr(pipeline, "audio_segments", lambda _db, _id: [audio])
    monkeypatch.setattr(pipeline, "project_scenes", lambda _db, _id: [scene])
    monkeypatch.setattr(pipeline, "project_cues", lambda _db, _id: [])
    monkeypatch.setattr(pipeline, "project_assets", lambda _db, _id: [])
    monkeypatch.setattr(pipeline.storage, "workspace_dir", lambda *_args: tmp_path)
    monkeypatch.setattr(pipeline.storage, "path_for", lambda key: source)
    monkeypatch.setattr(pipeline.storage, "exists", lambda _key: True)
    monkeypatch.setattr(
        pipeline.tts_svc,
        "concat_audio",
        lambda _paths, output, gap=0.18: output.write_bytes(b"master"),
    )
    monkeypatch.setattr(pipeline.tts_svc, "probe_duration", lambda _path: 3.5)
    monkeypatch.setattr(pipeline, "_materialize_reference_panel_crop", lambda *_args: source)

    def get(model, key):
        return asset if key == asset.id else None

    db = SimpleNamespace(get=get, flush=lambda: None)
    job = SimpleNamespace(
        project_id=project.id,
        kind="final",
        render_profile="Auto",
        encoder_requested="cpu",
    )

    request = pipeline.build_render_request(db, job)

    assert request.profile is reference_profile.REFERENCE_MATCHED_SHORTS_V2
    assert request.subtitle_contract_version == "sentence_chunked_word_karaoke_v2"
    assert request.sentence_groups
    assert [word.text for word in request.sentence_groups[0].words] == [
        "FIRST",
        "TURN",
        "THEN",
        "THE",
        "WOUNDED",
        "PAIR",
        "ESCAPES",
    ]
    assert request.subtitle_timing_source == "audio_segment.word_timings"


def test_regular_profile_requires_authoritative_word_timing():
    from app.services import subtitle_karaoke

    segment = SimpleNamespace(
        section="hook",
        start_time=0.0,
        end_time=3.0,
        spoken_text="A spoken sentence.",
        word_timings=[],
    )
    with pytest.raises(ValueError, match="subtitle.word_timing_missing"):
        subtitle_karaoke.build_sentence_groups_from_segments([segment])


def test_regular_profile_without_audio_segments_fails_with_timing_code(monkeypatch):
    from app.services import pipeline, reference_profile

    project = SimpleNamespace(
        id="missing-timing-project",
        template=reference_profile.REFERENCE_MATCHED_SHORTS_V2.profile_id,
    )
    script = SimpleNamespace(id="missing-timing-script")
    monkeypatch.setattr(pipeline, "get_project", lambda _db, _id: project)
    monkeypatch.setattr(pipeline, "current_script", lambda _db, _id: script)
    monkeypatch.setattr(pipeline, "audio_segments", lambda _db, _id: [])

    with pytest.raises(pipeline.PipelineError, match="subtitle\\.word_timing_missing"):
        pipeline.build_render_request(
            SimpleNamespace(),
            SimpleNamespace(project_id=project.id, kind="final", render_profile="Auto"),
        )


def test_regular_ass_holds_complete_chunk_with_two_lines_and_active_word_style():
    from app.services import render, subtitle_karaoke

    groups = subtitle_karaoke.build_sentence_caption_groups(
        "First turn, then the wounded pair escapes.",
        _timings(
            ("First", 0.0, 0.5),
            ("turn,", 0.5, 1.0),
            ("then", 1.0, 1.5),
            ("the", 1.5, 2.0),
            ("wounded", 2.0, 2.5),
            ("pair", 2.5, 3.0),
            ("escapes.", 3.0, 3.5),
        ),
    )
    ass = render.build_sentence_karaoke_ass(
        groups,
        1080,
        1920,
        font_name="BarberChop",
        max_chars=subtitle_karaoke.CAPTION_MAX_CHARS,
        max_lines=subtitle_karaoke.CAPTION_MAX_LINES,
        active_scale=subtitle_karaoke.CAPTION_ACTIVE_SCALE,
        font_height_ratio=subtitle_karaoke.CAPTION_FONT_HEIGHT_RATIO,
        safe_margin_px=subtitle_karaoke.CAPTION_SAFE_MARGIN_PX,
    )

    style = next(line for line in ass.splitlines() if line.startswith("Style: Caption"))
    assert ",BarberChop,77," in style
    assert ",120,120," in style
    events = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert events
    assert all(event.count("\\N") <= 1 for event in events)
    assert all("\\fscx108\\fscy108" in event for event in events)
    assert all("\\c&H0000FFFF&" in event for event in events)
    assert all(word in ass for word in ("FIRST", "TURN", "THEN", "THE", "WOUNDED", "PAIR", "ESCAPES"))
    event_payloads = [event.rsplit(",,", 1)[1] for event in events]
    display_words = [
        token
        for payload in event_payloads
        for token in re.sub(r"\{[^}]*\}", " ", payload).replace("\\N", " ").split()
    ]
    assert all(re.fullmatch(r"[A-Z0-9]+", word) for word in display_words)


def test_profile_active_regular_render_rejects_missing_persisted_visual_lineage_before_ffmpeg(tmp_path: Path):
    from app.services import reference_profile, render

    source = tmp_path / "source.png"
    Image.new("RGB", (1080, 1920), (30, 40, 50)).save(source)
    scene = render.SceneInput(
        image_path=source,
        start_time=0.0,
        end_time=1.0,
        publish_allowed=False,
    )
    request = render.RenderRequest(
        project_id="regular-lineage",
        scenes=[scene],
        audio_path=None,
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V2,
        persisted_reference_framing=True,
    )

    with pytest.raises(render.RenderError, match="visual\\.panel_lineage_unavailable"):
        render.render_video(request)


def test_profile_none_keeps_legacy_ass_surface():
    from app.services import render
    from app.services.timeline import CueSpec

    ass = render.build_ass([CueSpec(0, "This is legacy", 0.0, 2.0)], 1080, 1920)

    assert "Style: Caption,DejaVu Sans" in ass
    assert ass.count("Dialogue:") == 3
    assert "\\c&H0000FFFF&" in ass


def test_regular_reference_final_subtitle_filter_declares_tv_yuv420p():
    from app.services import reference_profile, render

    filter_chain = render._reference_final_video_filter(
        "subtitles=captions.ass",
        reference_profile.REFERENCE_MATCHED_SHORTS_V2,
        preview=False,
    )

    assert "scale=in_range=full:out_range=tv" in filter_chain
    assert filter_chain.endswith(",format=yuv420p")


def test_regular_manifest_records_measured_subtitle_contract_evidence():
    from app.services import reference_profile, render, subtitle_karaoke

    groups = subtitle_karaoke.build_sentence_caption_groups(
        "First turn.",
        _timings(("First", 0.0, 0.5), ("turn.", 0.5, 1.0)),
    )

    evidence = render._subtitle_manifest_evidence(
        groups,
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V2,
    )

    assert evidence == {
        "max_lines_measured": 1,
        "active_word_events": 2,
        "display_word_count": 2,
        "timing_source": "audio_segment.word_timings",
        "spoken_text_immutable": True,
        "contract_version": subtitle_karaoke.SUBTITLE_CONTRACT_VERSION,
        "profile_id": reference_profile.REFERENCE_MATCHED_SHORTS_V2.profile_id,
    }
