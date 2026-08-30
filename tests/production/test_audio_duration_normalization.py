from __future__ import annotations

from dataclasses import replace

import pytest


def test_duration_window_tempo_corrects_fast_provider_into_reference_window():
    from app.services import tts

    correction = tts._duration_window_tempo(
        42.096,
        5,
        duration_min_s=50.0,
        duration_max_s=60.0,
        gap_s=0.18,
    )
    assert correction is not None
    tempo, target = correction
    assert target == pytest.approx(50.75)
    assert 0.80 <= tempo < 1.0
    corrected = 42.096 / tempo + 4 * 0.18
    assert corrected == pytest.approx(50.75, abs=0.001)


def test_duration_window_tempo_rejects_extreme_correction():
    from app.services import tts

    with pytest.raises(tts.TTSError, match="safe production range"):
        tts._duration_window_tempo(
            20.0,
            5,
            duration_min_s=50.0,
            duration_max_s=60.0,
            gap_s=0.18,
        )


def test_normalize_speech_clips_uses_one_shared_tempo(monkeypatch, tmp_path):
    from app.services import tts

    durations = [6.288, 11.88, 12.216, 6.456, 5.256]
    clips = [
        tts.SpeechClip(
            path=tmp_path / f"{index}.wav",
            text=f"section {index}",
            duration=duration,
            voice_id="voice",
            provider="http",
            voice_profile={"provider": "http", "voice_id": "voice", "speed": 1.15},
        )
        for index, duration in enumerate(durations)
    ]
    observed = []

    def fake_retime(clip, tempo):
        observed.append(tempo)
        return replace(
            clip,
            duration=clip.duration / tempo,
            voice_profile={
                **clip.voice_profile,
                "timing_policy": tts.PRODUCTION_AUDIO_TIMING_POLICY_VERSION,
                "tempo_correction": round(float(tempo), 6),
            },
        )

    monkeypatch.setattr(tts, "_retime_speech_clip", fake_retime)
    adjusted, metadata = tts.normalize_speech_clips_to_duration_window(
        clips,
        duration_min_s=50.0,
        duration_max_s=60.0,
    )
    assert metadata["applied"] is True
    assert metadata["duration_before_s"] == pytest.approx(42.816)
    assert 50.0 <= metadata["duration_after_s"] <= 60.0
    assert len({round(value, 8) for value in observed}) == 1
    assert all(
        clip.voice_profile["timing_policy"] == tts.PRODUCTION_AUDIO_TIMING_POLICY_VERSION
        for clip in adjusted
    )
