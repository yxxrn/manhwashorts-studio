"""Versioned runtime narrative identities and prompt resources."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


class NarrativeIdentityError(ValueError):
    """Safe failure for an unknown or drifted narrative identity resource."""

    code = "narrative_identity_invalid"


@dataclass(frozen=True)
class NarrativeIdentityProfile:
    profile_id: str
    profile_version: str
    language: str
    identity: str
    target_word_min: int = 90
    target_word_max: int = 125
    passage_min: int = 4
    passage_max: int = 6
    allowed_ending_kinds: tuple[str, ...] = (
        "cliffhanger",
        "consequence",
        "open_question",
    )
    prompt_version: str = "vision-first-story-analyzer-v3"
    prompt_filename: str = "vision_first_story_analyzer_v3.txt"
    contract_sha256: str = "134b544c9e2f74ca0b8c64ff55a27c831e76f77a08f26fc2a463112cb0678b3e"


SHARP_FRIEND_V1 = NarrativeIdentityProfile(
    profile_id="sharp_friend_v1",
    profile_version="1.0.0",
    language="en-US",
    identity="a clever, friendly, perceptive friend under controlled tension",
)

_PROFILE_REGISTRY: dict[str, NarrativeIdentityProfile] = {
    "sharp_friend_v1": SHARP_FRIEND_V1,
}


def _profile(profile_id: str) -> NarrativeIdentityProfile:
    if not isinstance(profile_id, str) or profile_id not in _PROFILE_REGISTRY:
        raise NarrativeIdentityError("unknown narrative identity")
    return _PROFILE_REGISTRY[profile_id]


def get_narrative_identity(profile_id: str) -> NarrativeIdentityProfile:
    """Return the explicitly registered profile or fail without resource details."""

    return _profile(profile_id)


def canonical_profile_contract_json(
    profile: NarrativeIdentityProfile,
    prompt_sha256: str,
) -> str:
    """Serialize the profile contract with its derived hash excluded."""

    payload = {
        "profile_id": profile.profile_id,
        "profile_version": profile.profile_version,
        "language": profile.language,
        "identity": profile.identity,
        "target_word_min": profile.target_word_min,
        "target_word_max": profile.target_word_max,
        "passage_min": profile.passage_min,
        "passage_max": profile.passage_max,
        "allowed_ending_kinds": list(profile.allowed_ending_kinds),
        "prompt_version": profile.prompt_version,
        "prompt_filename": profile.prompt_filename,
        "prompt_sha256": prompt_sha256,
        "contract_sha256": "",
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _load_prompt(profile: NarrativeIdentityProfile) -> tuple[str, str]:
    prompt_path = Path(__file__).resolve().parents[1] / "prompts" / profile.prompt_filename
    try:
        text = prompt_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise NarrativeIdentityError("narrative identity resource is invalid") from None
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    version_line = f"Version: {profile.prompt_version}"
    if version_line not in normalized:
        raise NarrativeIdentityError("narrative identity resource is invalid")
    prompt_sha256 = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return prompt_sha256, normalized


def load_narrative_instruction(profile_id: str) -> tuple[str, str, str]:
    """Return the verified prompt version, SHA-256, and normalized LF text."""

    profile = _profile(profile_id)
    prompt_sha256, normalized = _load_prompt(profile)
    canonical = canonical_profile_contract_json(profile, prompt_sha256)
    contract_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if contract_sha256 != profile.contract_sha256:
        raise NarrativeIdentityError("narrative identity resource is invalid")
    return profile.prompt_version, prompt_sha256, normalized
