from __future__ import annotations

import pytest

from app.config import settings
from app.services import resolver, tts


def _no_byok(monkeypatch):
    monkeypatch.setattr(resolver.cred_svc, "active_credential", lambda *_args, **_kwargs: None)


def test_production_tts_fails_closed_without_ai_endpoint(monkeypatch):
    _no_byok(monkeypatch)
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "tts_provider", "espeak")
    monkeypatch.setattr(settings, "tts_http_url", None)
    with pytest.raises(tts.TTSError, match="local fallback is disabled"):
        resolver.resolve_tts(None, "workspace")


def test_production_rejects_explicit_local_tts_override(monkeypatch):
    _no_byok(monkeypatch)
    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(tts.TTSError, match="local override is disabled"):
        resolver.resolve_tts(None, "workspace", override="espeak")


def test_configured_ai_tts_is_default_and_ara_is_global_voice(monkeypatch):
    _no_byok(monkeypatch)
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "tts_provider", "http")
    monkeypatch.setattr(settings, "tts_http_url", "http://tts.test")
    provider, decision = resolver.resolve_tts(None, "workspace")
    assert isinstance(provider, tts.HttpProvider)
    assert decision.provider == "http"
    assert decision.source == "env"
    from app.constants import DEFAULT_ENGLISH_VOICE_ID
    assert DEFAULT_ENGLISH_VOICE_ID == "ara"
    assert tts.resolve_grok_voice_id("ara") == "ara"
