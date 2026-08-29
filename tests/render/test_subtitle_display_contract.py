"""RED tests for the separated spoken narration and display subtitle contract."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        ("Why can't Jin-Woo move?", "WHY CANT JINWOO MOVE"),
        ("Än café 42—猫 /", "ÄN CAFÉ 42猫"),
        ("  mixed\tcase  words ", "MIXED CASE WORDS"),
        ("...?! — •", ""),
    ),
)
def test_normalize_display_text_is_uppercase_unicode_alnum_only(source, expected):
    from app.services.timeline import normalize_display_text

    assert normalize_display_text(source) == expected
    assert normalize_display_text(normalize_display_text(source)) == expected


def test_build_cues_emits_one_display_word_per_timed_spoken_token():
    from app.services.timeline import AudioSpan, build_cues

    span = AudioSpan(
        section="hook",
        text="Why can't Jin-Woo move?",
        start_time=0.0,
        end_time=4.0,
        word_timings=[
            {"word": "Why", "start": 0.0, "end": 1.0},
            {"word": "can't", "start": 1.0, "end": 2.0},
            {"word": "Jin-Woo", "start": 2.0, "end": 3.0},
            {"word": "move?", "start": 3.0, "end": 4.0},
        ],
    )

    cues = build_cues([span], min_cue_seconds=0.1, media_duration=4.0)

    assert [cue.text for cue in cues] == ["WHY", "CANT", "JINWOO", "MOVE"]
    assert [(cue.start_time, cue.end_time) for cue in cues] == [
        (0.0, 1.0),
        (1.0, 2.0),
        (2.0, 3.0),
        (3.0, 4.0),
    ]


def test_build_cues_fallback_is_one_word_and_clamped_without_timings():
    from app.services.timeline import AudioSpan, build_cues

    cues = build_cues(
        [AudioSpan("setup", "One, two... three!", 1.0, 4.0)],
        min_cue_seconds=0.8,
        media_duration=3.5,
    )

    assert [cue.text for cue in cues] == ["ONE", "TWO", "THREE"]
    assert all(cue.end_time <= 3.5 for cue in cues)
    assert all(cue.end_time > cue.start_time for cue in cues)
    assert all(
        right.start_time >= left.end_time
        for left, right in zip(cues, cues[1:], strict=False)
    )


def test_short_timed_cues_extend_without_overlapping_or_exceeding_media():
    from app.services.timeline import AudioSpan, build_cues

    cues = build_cues(
        [
            AudioSpan(
                "setup",
                "One two three",
                0.0,
                1.0,
                word_timings=[
                    {"word": "One", "start": 0.0, "end": 0.1},
                    {"word": "two", "start": 0.1, "end": 0.2},
                    {"word": "three", "start": 0.2, "end": 0.3},
                ],
            )
        ],
        min_cue_seconds=0.25,
        media_duration=1.0,
    )

    assert len(cues) == 3
    assert all(cue.end_time <= 1.0 for cue in cues)
    assert all(cue.end_time > cue.start_time for cue in cues)
    assert all(
        right.start_time >= left.end_time
        for left, right in zip(cues, cues[1:], strict=False)
    )


def test_validate_cues_flags_display_shape_and_timing_errors():
    from app.services.timeline import CueSpec, validate_cues

    cues = [
        CueSpec(0, "TWO WORDS", 0.0, 1.0),
        CueSpec(1, "lower", 0.5, 1.5),
        CueSpec(2, "what?", 1.5, 2.5),
        CueSpec(3, "...", 2.5, 2.8),
        CueSpec(4, "LATE", -0.1, 3.5),
    ]

    codes = {
        warning["code"]
        for warning in validate_cues(cues, 28, 2, media_duration=3.0)
    }

    assert {
        "subtitle.display_multiword",
        "subtitle.display_not_uppercase",
        "subtitle.display_punctuation",
        "subtitle.display_empty",
        "subtitle.overlap",
        "subtitle.before_media",
        "subtitle.after_media",
    } <= codes


def test_srt_serializes_uppercase_punctuation_free_display_words():
    from app.services.timeline import CueSpec, to_srt

    srt = to_srt([CueSpec(0, "can't?", 0.0, 1.0)])

    assert "\nCANT\n" in f"\n{srt}"
    assert "can't?" not in srt


def test_render_receives_display_text_not_spoken_punctuation():
    from app.services.render import build_ass
    from app.services.timeline import CueSpec, normalize_display_text

    display = normalize_display_text("Why can't Jin-Woo move?")
    ass = build_ass([CueSpec(0, display, 0.0, 1.0)], 1080, 1920)

    assert "WHY CANT JINWOO MOVE" not in ass
    assert "WHY" in ass
    assert "CANT" in ass
    assert "JINWOO" in ass
    assert "MOVE" in ass
    assert "?" not in ass
    assert "'" not in ass


class _FakeTTSProvider:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def available(self) -> bool:
        return True

    def synthesize(self, text: str, out_path: Path, voice_id: str, speed: float):
        from app.services.tts import SpeechClip

        self.calls.append(text)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"synthetic audio")
        step = 4.0 / len(text.split())
        timings = [
            {"word": word, "start": index * step, "end": (index + 1) * step}
            for index, word in enumerate(text.split())
        ]
        return SpeechClip(
            path=out_path,
            text=text,
            duration=4.0,
            voice_id=voice_id,
            provider=self.name,
            word_timings=timings,
            voice_profile={"provider": self.name, "voice_id": voice_id},
        )


class _FakeTTSDecision:
    source = "test"
    model = "fake-tts"


def test_generate_voiceover_separates_source_spoken_and_display_text(db, monkeypatch):
    from app.models import Project, ScriptVersion, User, Workspace
    from app.security import hash_password
    from app.services import pipeline as pipeline_service

    user = User(
        email="subtitle-contract@example.com",
        name="Subtitle Contract",
        password_hash=hash_password("test-password"),
    )
    db.add(user)
    db.flush()
    workspace = Workspace(owner_id=user.id, name="Subtitle Contract Workspace")
    db.add(workspace)
    db.flush()
    project = Project(
        workspace_id=workspace.id,
        title="Subtitle Contract Project",
        language="en",
        voice_id="en",
        pronunciations={"Jin-Woo": "Jin Woo"},
    )
    db.add(project)
    db.flush()
    source_text = "Why can't Jin-Woo move?"
    script = ScriptVersion(
        project_id=project.id,
        version=1,
        sections=[{"section": "hook", "text": source_text}],
        estimated_duration=4.0,
        generator="vision_evidence_v2",
        approved_by="human-1",
        approved_at=datetime.now(UTC),
    )
    db.add(script)
    db.commit()

    fake_provider = _FakeTTSProvider()
    monkeypatch.setattr(
        pipeline_service.resolver_svc,
        "resolve_tts",
        lambda _db, _workspace_id, override=None: (fake_provider, _FakeTTSDecision()),
    )

    pipeline_service.generate_voiceover(db, project.id, actor_id="human-1")
    segment = pipeline_service.audio_segments(db, script.id)[0]

    assert segment.text == source_text
    assert segment.spoken_text == "Why can't Jin Woo move?"
    assert segment.display_text == "WHY CANT JINWOO MOVE"
    assert fake_provider.calls == [segment.spoken_text]
    assert "?" not in segment.display_text
    assert "'" not in segment.display_text


def test_subtitle_patch_normalizes_and_rejects_empty_display(auth_client):
    from app.db import SessionLocal
    from app.models import SubtitleCue

    project_response = auth_client.post(
        "/api/projects",
        json={
            "title": "Subtitle API Contract",
            "manhwa_title": "Synthetic",
            "chapter": "1",
            "target_duration": 60,
            "language": "en",
            "voice_id": "en",
        },
    )
    assert project_response.status_code == 201, project_response.text
    project_id = project_response.json()["id"]
    with SessionLocal() as db:
        cue = SubtitleCue(
            project_id=project_id,
            order_index=0,
            text="OLD",
            start_time=0.0,
            end_time=1.0,
        )
        db.add(cue)
        db.commit()
        cue_id = cue.id

    updated = auth_client.patch(
        f"/api/projects/{project_id}/subtitles/{cue_id}",
        json={"text": "Why?"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["text"] == "WHY"
    assert updated.json()["edited_by_user"] is True

    srt = auth_client.get(f"/api/projects/{project_id}/subtitles.srt")
    assert srt.status_code == 200, srt.text
    assert "WHY" in srt.text

    rejected = auth_client.patch(
        f"/api/projects/{project_id}/subtitles/{cue_id}",
        json={"text": "...?!"},
    )
    assert rejected.status_code == 422
