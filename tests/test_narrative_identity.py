"""Contract tests for the versioned Sharp Friend narrative identity."""

import hashlib
import importlib
from dataclasses import replace
from pathlib import Path

import pytest


PROMPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "prompts"
    / "vision_first_story_analyzer_v3.txt"
)


def _read_v3_prompt_directly() -> str:
    if not PROMPT_PATH.exists():
        pytest.fail(f"missing v3 prompt resource: {PROMPT_PATH.name}")
    raw = PROMPT_PATH.read_bytes()
    assert b"\r" not in raw
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        pytest.fail(f"v3 prompt is not UTF-8: {exc}")


def test_v3_prompt_resource_is_lf_utf8_and_normative():
    prompt = _read_v3_prompt_directly()
    lowered = prompt.lower()
    required = (
        "contract id: vision-first_editorial_story_engine.analyzer",
        "version: vision-first-story-analyzer-v3",
        "narrative profile: sharp_friend_v1",
        "observe every ordered panel",
        "reconcile all panel, observation, chunk, coverage, continuity, synthesis, and claim gates before prose",
        "story_spine",
        "ending_kind",
        "wants",
        "obstacle",
        "decision",
        "consequence",
        "changed stakes",
        "unresolved direction",
        "contractions",
        "varied sentence lengths",
        "causal connectors",
        "selective evidence-grounded commentary",
        "four to six",
        "cliffhanger",
        "open_question",
        "spoken text",
        "do not generate display_text",
        "no fixed intro",
        "no channel cta",
        "copied speech-balloon dialogue",
        "do not invent an identity, motive, relationship, event, or causal link",
    )
    for fragment in required:
        assert fragment in lowered, fragment
    assert lowered.index("observe every ordered panel") < lowered.index("ending_kind")
    assert lowered.index("ending_kind") < lowered.index("four to six")


def _identity_module():
    try:
        return importlib.import_module("app.services.narrative_identity")
    except Exception as exc:
        pytest.fail(f"narrative identity import failed in the test body: {exc}")


def _identity_error(module):
    error_type = getattr(module, "NarrativeIdentityError", None)
    assert isinstance(error_type, type)
    assert issubclass(error_type, Exception)
    return error_type


def test_profile_is_frozen_and_has_exact_sharp_friend_identity_fields():
    module = _identity_module()
    profile = getattr(module, "SHARP_FRIEND_V1", None)
    assert profile.profile_id == "sharp_friend_v1"
    assert profile.profile_version == "1.0.0"
    assert profile.language == "en-US"
    assert profile.identity == (
        "a clever, friendly, perceptive friend under controlled tension"
    )
    assert (profile.target_word_min, profile.target_word_max) == (90, 125)
    assert (profile.passage_min, profile.passage_max) == (4, 6)
    assert profile.allowed_ending_kinds == (
        "cliffhanger",
        "consequence",
        "open_question",
    )
    assert profile.prompt_version == "vision-first-story-analyzer-v3"
    assert profile.prompt_filename == "vision_first_story_analyzer_v3.txt"
    assert len(profile.contract_sha256) == 64
    with pytest.raises((AttributeError, TypeError)):
        profile.profile_id = "other"


def test_unknown_profile_fails_without_leaking_resource_details():
    module = _identity_module()
    error_type = _identity_error(module)
    with pytest.raises(error_type, match="unknown narrative identity") as caught:
        module.get_narrative_identity("not_a_real_profile")
    assert "vision_first_story_analyzer_v3" not in str(caught.value)
    assert "/" not in str(caught.value)


def test_loader_returns_lf_prompt_and_matches_profile_contract():
    module = _identity_module()
    version, digest, text = module.load_narrative_instruction("sharp_friend_v1")
    assert version == "vision-first-story-analyzer-v3"
    assert digest == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert "\r" not in text
    assert "observe every ordered panel" in text.lower()
    assert module.get_narrative_identity("sharp_friend_v1").contract_sha256


def test_profile_loader_rejects_profile_hash_tampering(monkeypatch):
    module = _identity_module()
    original = module.SHARP_FRIEND_V1
    registry = dict(module._PROFILE_REGISTRY)
    registry["sharp_friend_v1"] = replace(original, contract_sha256="0" * 64)
    monkeypatch.setattr(module, "_PROFILE_REGISTRY", registry)
    with pytest.raises(module.NarrativeIdentityError):
        module.load_narrative_instruction("sharp_friend_v1")


def test_profile_loader_rejects_prompt_filename_tampering(monkeypatch):
    module = _identity_module()
    original = module.SHARP_FRIEND_V1
    registry = dict(module._PROFILE_REGISTRY)
    registry["sharp_friend_v1"] = replace(
        original,
        prompt_filename="vision_first_story_analyzer_v1.txt",
    )
    monkeypatch.setattr(module, "_PROFILE_REGISTRY", registry)
    with pytest.raises(module.NarrativeIdentityError):
        module.load_narrative_instruction("sharp_friend_v1")
