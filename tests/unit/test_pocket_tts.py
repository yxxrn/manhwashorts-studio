from __future__ import annotations


def test_pocket_provider_uses_alba_and_records_stable_profile(monkeypatch, tmp_path):
    from pathlib import Path
    from types import SimpleNamespace

    import httpx

    from app.config import settings
    from app.services import tts as tts_svc

    monkeypatch.setattr(settings, "tts_pocket_voice", "alba")
    monkeypatch.setattr(settings, "tts_pocket_url", "http://pocket.test")
    calls = []
    ffmpeg_calls = []

    def fake_post(url, data=None, timeout=None):
        calls.append((url, dict(data or {}), timeout))
        return httpx.Response(200, content=b"x" * 2048, request=httpx.Request("POST", url))

    def fake_run(cmd, **kwargs):
        ffmpeg_calls.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"normalized")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(tts_svc.subprocess, "run", fake_run)
    monkeypatch.setattr(tts_svc, "probe_duration", lambda _path: 1.25)
    clip = tts_svc.PocketTTSProvider().synthesize("Doctor Doom is dangerous.", tmp_path / "voice.wav", "en", 1.15)
    assert calls[0][1]["voice_url"] == "alba"
    assert clip.provider == "pocket" and clip.voice_id == "alba"
    assert clip.voice_profile["model"] == settings.tts_pocket_model
    assert clip.voice_profile["provider_version"] == tts_svc.POCKET_TTS_PROVIDER_VERSION
    filter_chain = " ".join(ffmpeg_calls[0])
    assert "loudnorm=" not in filter_chain
    assert "equalizer=f=3200" in filter_chain


def test_tts_resolver_local_first_keeps_old_provider_as_lazy_fallback(db, monkeypatch):
    from app.config import settings
    from app.services import resolver as resolver_svc
    from tests.factories.evidence import _project

    project = _project(db)
    monkeypatch.setattr(settings, "tts_local_first", True)
    monkeypatch.setattr(settings, "tts_pocket_voice", "alba")
    primary, decision = resolver_svc.resolve_tts(db, project.workspace_id)
    fallback, fallback_decision = resolver_svc.resolve_tts_fallback(db, project.workspace_id)
    assert primary.name == "pocket"
    assert decision.provider == "pocket" and "alba" in decision.label.lower()
    assert fallback.name == settings.tts_provider
    assert fallback_decision.provider == settings.tts_provider


def test_voiceover_falls_back_once_for_whole_pocket_session(db, monkeypatch):
    from datetime import UTC, datetime

    from app.models import Project, ScriptVersion, User, Workspace
    from app.security import hash_password
    from app.services import pipeline as pl
    from app.services import tts as tts_svc

    class FailingPocket:
        name = "pocket"
        calls = 0
        def available(self): return True
        def synthesize(self, text, out_path, voice_id, speed):
            self.calls += 1
            raise tts_svc.TTSError("local service failed")

    class Fallback:
        name = "fake-cloud"
        calls = 0
        def available(self): return True
        def synthesize(self, text, out_path, voice_id, speed):
            self.calls += 1
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"fallback audio")
            duration = 3.0
            return tts_svc.SpeechClip(
                out_path, text, duration, voice_id, self.name,
                tts_svc.estimate_word_timings(text, duration),
                tts_svc.voice_profile_for(self.name, voice_id, speed=speed),
            )

    class Decision:
        def __init__(self, source, provider):
            self.source, self.provider, self.model = source, provider, "test"

    user = User(email="pocket-fallback@example.com", name="Pocket", password_hash=hash_password("password"))
    db.add(user)
    db.flush()
    workspace = Workspace(owner_id=user.id, name="Pocket Workspace")
    db.add(workspace)
    db.flush()
    project = Project(workspace_id=workspace.id, title="Pocket Test", language="en", voice_id="en")
    db.add(project)
    db.flush()
    script = ScriptVersion(
        project_id=project.id, version=1, generator="vision_evidence_v2",
        sections=[{"section": "hook", "text": "Doctor Doom is dangerous."}],
        approved_by="reviewer", approved_at=datetime.now(UTC),
    )
    db.add(script)
    db.commit()
    primary, fallback = FailingPocket(), Fallback()
    monkeypatch.setattr(pl.resolver_svc, "resolve_tts", lambda *_a, **_k: (primary, Decision("local", "pocket")))
    monkeypatch.setattr(pl.resolver_svc, "resolve_tts_fallback", lambda *_a, **_k: (fallback, Decision("env", "fake-cloud")))
    segments = pl.generate_voiceover(db, project.id, actor_id="test")
    assert primary.calls == 1 and fallback.calls == 1
    assert len(segments) == 1 and segments[0].provider == "fake-cloud"


def test_render_identity_changes_when_locked_pocket_voice_changes(db, monkeypatch):
    from app.config import settings
    from app.services import pipeline as pl
    from tests.factories.evidence import _project

    project = _project(db)
    monkeypatch.setattr(settings, "tts_local_first", True)
    monkeypatch.setattr(settings, "tts_pocket_voice", "alba")
    alba_identity = pl._render_output_identity(project)
    monkeypatch.setattr(settings, "tts_pocket_voice", "azelma")
    azelma_identity = pl._render_output_identity(project)
    assert alba_identity != azelma_identity
    assert alba_identity["tts_selection"]["pocket_voice"] == "alba"
    assert alba_identity["tts_selection"]["pocket_production_speed"] == 1.0
    assert azelma_identity["tts_selection"]["pocket_voice"] == "azelma"


def test_alba_credit_is_present_only_for_pocket_audio():
    from app.services import tts as tts_svc

    credit = tts_svc.voice_attribution("pocket", "alba")
    assert "Alba MacKenna" in credit and "CC BY 4.0" in credit
    assert tts_svc.voice_attribution("http", "alba") == ""


def test_pocket_clarity_gate_keeps_proven_080_retime_local(db, monkeypatch):
    from datetime import UTC, datetime

    from app.models import Project, ScriptVersion, User, Workspace
    from app.security import hash_password
    from app.services import pipeline as pl
    from app.services import tts as tts_svc

    class FastPocket:
        name = "pocket"
        def available(self): return True
        def synthesize_sections(self, texts, work, voice_id, speed):
            path = work / "fast-pocket.wav"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"pocket")
            return [tts_svc.SpeechClip(path, texts[0], 41.0, "alba", "pocket",
                tts_svc.estimate_word_timings(texts[0], 41.0),
                tts_svc.voice_profile_for("pocket", "alba", speed=speed))]


    class Fallback:
        name = "fake-cloud"
        def available(self): return True
        def synthesize_sections(self, texts, work, voice_id, speed):
            path = work / "fallback.wav"
            path.write_bytes(b"fallback")
            return [tts_svc.SpeechClip(path, texts[0], 51.0, voice_id, self.name,
                tts_svc.estimate_word_timings(texts[0], 51.0),
                tts_svc.voice_profile_for(self.name, voice_id, speed=speed))]

    class Decision:
        def __init__(self, source, provider):
            self.source, self.provider, self.model = source, provider, "test"

    user = User(email="clarity-fallback@example.com", name="Clarity", password_hash=hash_password("password"))
    db.add(user)
    db.flush()
    workspace = Workspace(owner_id=user.id, name="Clarity Workspace")
    db.add(workspace)
    db.flush()
    project = Project(workspace_id=workspace.id, title="Clarity Test", language="en", voice_id="en")
    db.add(project)
    db.flush()

    script = ScriptVersion(
        project_id=project.id, version=1, generator="vision_evidence_v2",
        sections=[{"section":"hook","text":"Lu Sheng breaks the balance before anyone can react."}],
        approved_by="reviewer", approved_at=datetime.now(UTC),
    )
    db.add(script)
    db.commit()
    primary, fallback = FastPocket(), Fallback()
    monkeypatch.setattr(pl.resolver_svc, "resolve_tts", lambda *_a, **_k: (primary, Decision("local", "pocket")))
    monkeypatch.setattr(pl.resolver_svc, "resolve_tts_fallback", lambda *_a, **_k: (fallback, Decision("env", "fake-cloud")))
    monkeypatch.setattr(tts_svc, "normalize_speech_clips_to_duration_window",
        lambda clips, **_k: (clips, {"applied": False}))

    segments = pl.generate_voiceover(
        db, project.id, actor_id="test", duration_bounds_s=(50.0, 60.0)
    )
    assert len(segments) == 1
    assert segments[0].provider == "pocket"
    assert tts_svc.POCKET_TTS_CLARITY_TEMPO_MIN == 0.80
    assert tts_svc.PRODUCTION_AUDIO_TIMING_POLICY_VERSION == "production-audio-timing-v5"


def test_pocket_borderline_cadence_uses_bounded_section_gap_instead_of_fallback():
    from types import SimpleNamespace

    from app.services import tts as tts_svc
    from app.services.pipeline_stages import media

    clips = [SimpleNamespace(duration=value) for value in (6.0, 9.8, 9.7, 7.2, 7.1)]
    gap = media._pocket_safe_inter_section_gap(
        tts_svc, clips, duration_min_s=50.0, duration_max_s=60.0, base_gap_s=0.18,
    )
    assert 0.18 < gap <= 0.65
    correction = tts_svc._duration_window_tempo(
        sum(c.duration for c in clips), len(clips),
        duration_min_s=50.0, duration_max_s=60.0, gap_s=gap,
    )
    assert correction is not None
    tempo, _target = correction
    assert tempo > tts_svc.POCKET_TTS_CLARITY_TEMPO_MIN


def test_pocket_gap_recovery_does_not_hide_truly_short_audio():
    from types import SimpleNamespace

    import pytest

    from app.services import tts as tts_svc
    from app.services.pipeline_stages import media

    clips = [SimpleNamespace(duration=value) for value in (5.6, 9.1, 9.1, 7.5, 6.9)]
    gap = media._pocket_safe_inter_section_gap(
        tts_svc, clips, duration_min_s=50.0, duration_max_s=60.0, base_gap_s=0.18,
    )
    assert gap == pytest.approx(media._POCKET_MAX_INTER_SECTION_GAP_S)
    with pytest.raises(tts_svc.TTSError, match='safe production range'):
        tts_svc._duration_window_tempo(
            sum(c.duration for c in clips), len(clips),
            duration_min_s=50.0, duration_max_s=60.0, gap_s=gap,
        )
