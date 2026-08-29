"""RED contract tests for the deferred immutable VoiceProfile boundary."""

from __future__ import annotations

import importlib

import pytest


def _module():
    try:
        return importlib.import_module("app.services.voice_profile")
    except Exception as exc:
        pytest.fail(f"voice profile boundary import failed in test body: {exc}")


def test_voice_profile_is_pinned_and_provider_switch_requires_new_approval():
    module = _module()
    profile = module.build_voice_profile(
        provider="openai",
        model="gpt-4o-mini-tts",
        model_version="2026-01",
        voice_id="alloy",
        reference_hash="a" * 64,
        locale="en-US",
        speed=1.15,
        style="grounded",
        stability=0.7,
        approval_state="approved",
    )
    assert profile.profile_sha256 and len(profile.profile_sha256) == 64
    assert module.validate_voice_profile(profile).profile_sha256 == profile.profile_sha256
    changed = module.build_voice_profile(
        provider="openai",
        model="gpt-5-tts",
        model_version="2026-02",
        voice_id="alloy",
        reference_hash="a" * 64,
        locale="en-US",
        speed=1.15,
        style="grounded",
        stability=0.7,
        approval_state="approved",
    )
    with pytest.raises(module.VoiceProfileError) as caught:
        module.require_compatible_approval(profile, changed)
    assert caught.value.code == "voice_profile_reapproval_required"


def test_voice_profile_rejects_noncanonical_reference_hash():
    module = _module()
    with pytest.raises(module.VoiceProfileError) as caught:
        module.build_voice_profile(
            provider="openai",
            model="gpt-4o-mini-tts",
            model_version="2026-01",
            voice_id="alloy",
            reference_hash="not-a-sha256",
            locale="en-US",
            speed=1.15,
            style="grounded",
            stability=0.7,
        )
    assert caught.value.code == "voice_profile_invalid"
