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


def test_http_native_speed_recovery_retries_short_audio_once_without_relaxing_tempo_gate(tmp_path):
    from types import SimpleNamespace

    from app.services.pipeline_stages import media

    class FakeTTSError(RuntimeError):
        pass

    calls = []
    normalize_durations = []

    def clip(duration, speed):
        return SimpleNamespace(duration=duration, voice_profile={"speed": speed})

    initial = [clip(value, 1.15) for value in (5.928, 8.592, 10.392, 7.944, 6.360)]
    recovered = [clip(value, 1.0) for value in (6.82, 9.88, 11.95, 9.14, 7.31)]

    def normalize(clips, *, duration_min_s, duration_max_s, gap_s):
        total = sum(item.duration for item in clips) + (len(clips) - 1) * gap_s
        normalize_durations.append(total)
        if clips is initial:
            raise FakeTTSError("audio tempo correction exceeds safe production range (0.80-1.25)")
        assert duration_min_s == 50.0
        assert duration_max_s == 60.0
        return clips, {"version": "production-audio-timing-v1", "tempo": 0.9}

    class Provider:
        def synthesize_sections(self, texts, work, voice_id, speed):
            calls.append((texts, work, voice_id, speed))
            return recovered

    svc = SimpleNamespace(TTSError=FakeTTSError, normalize_speech_clips_to_duration_window=normalize)
    adjusted, policy = media._normalize_http_duration_with_native_speed_recovery(
        svc, Provider(), ["a", "b", "c", "d", "e"], tmp_path, "orion", 1.15, initial,
        duration_min_s=50.0, duration_max_s=60.0, gap_s=0.18,
    )
    assert adjusted is recovered
    assert calls == [(["a", "b", "c", "d", "e"], tmp_path, "orion", 1.0)]
    assert normalize_durations[0] == pytest.approx(39.936)
    assert policy["native_speed_recovery"] is True
    assert policy["requested_speed"] == pytest.approx(1.15)
    assert policy["effective_speed"] == pytest.approx(1.0)
    assert policy["pre_recovery_duration_s"] == pytest.approx(39.936)


def test_http_native_speed_recovery_does_not_retry_already_native_failures(tmp_path):
    from types import SimpleNamespace

    from app.services.pipeline_stages import media

    class FakeTTSError(RuntimeError):
        pass

    clips = [SimpleNamespace(duration=6.0) for _ in range(5)]
    calls = []

    class Provider:
        def synthesize_sections(self, *args):
            calls.append(args)
            return clips

    def normalize(*args, **kwargs):
        raise FakeTTSError("audio tempo correction exceeds safe production range (0.80-1.25)")

    svc = SimpleNamespace(TTSError=FakeTTSError, normalize_speech_clips_to_duration_window=normalize)
    with pytest.raises(FakeTTSError, match="safe production range"):
        media._normalize_http_duration_with_native_speed_recovery(
            svc, Provider(), ["a"] * 5, tmp_path, "orion", 1.0, clips,
            duration_min_s=50.0, duration_max_s=60.0, gap_s=0.18,
        )
    assert calls == []
