from __future__ import annotations

from dataclasses import dataclass


def test_display_subtitles_remove_terminal_period_but_keep_question():
    from app.services.timeline import normalize_display_text, spoken_tokens

    assert normalize_display_text("The gate opens.") == "The gate opens"
    assert normalize_display_text("Who opened it?") == "Who opened it?"
    assert normalize_display_text("...") == ""
    assert spoken_tokens("Wait...") == ["Wait..."]


def test_editorial_validator_requires_insight_and_evidence():
    from app.services.script import ScriptDraft, Section, validate_editorial

    draft = ScriptDraft(sections=[
        Section("hook", "Why did the gate choose him?"),
        Section("setup", "Rian enters the abandoned dungeon.", evidence=[{"evidence_refs": ["panel_01"]}]),
        Section("conflict", "The floor collapses beneath his feet.", evidence=[{"evidence_refs": ["panel_02"]}]),
        Section("twist", "The system appears."),
        Section("cta", "What will Rian do next?"),
    ])
    codes = {finding["code"] for finding in validate_editorial(draft, "en")}
    assert "editorial.insight_missing" in codes


def test_voice_profile_hash_changes_when_identity_changes():
    from pathlib import Path

    from app.services.tts import SpeechClip

    first = SpeechClip(Path("a.wav"), "one", 1.0, "voice-a", "http", voice_profile={"voice_id": "voice-a", "model": "m"})
    second = SpeechClip(Path("b.wav"), "two", 1.0, "voice-b", "http", voice_profile={"voice_id": "voice-b", "model": "m"})
    assert first.voice_profile_hash != second.voice_profile_hash


def test_blank_panel_quality_is_rejected():
    import io

    from PIL import Image

    from app.services.ingest import _panel_quality

    buffer=io.BytesIO()
    Image.new("RGB", (400, 1000), "white").save(buffer, "JPEG")
    quality=_panel_quality(buffer.getvalue())
    assert quality["decision"] == "reject"


def test_repetition_gate_blocks_ab_pattern():
    from app.services.quality import check_repetition_and_motion

    @dataclass
    class Shot:
        asset_id: str
        start_time: float
        end_time: float
        motion_mode: str = "slow_push"

    results=check_repetition_and_motion([
        Shot("a", 0, 2), Shot("b", 2, 4), Shot("a", 4, 6), Shot("b", 6, 8),
    ])
    assert any(result.code == "visual.alternating_pattern" and result.blocking for result in results)


def test_voice_gate_blocks_mixed_profile():
    from app.services.quality import check_voice_profile

    @dataclass
    class Segment:
        id: str
        voice_profile_hash: str
        voice_profile: dict
        start_time: float
        end_time: float

    results=check_voice_profile([
        Segment("a", "hash-a", {"provider": "http", "voice_id": "a", "language": "en-US", "speed": 1}, 0, 1),
        Segment("b", "hash-b", {"provider": "http", "voice_id": "b", "language": "en-US", "speed": 1}, 1.1, 2),
    ])
    assert any(result.code == "voice.profile_changed" and result.blocking for result in results)
