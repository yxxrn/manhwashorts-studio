"""Immutable, provider-agnostic VoiceProfile identity for the deferred TTS gate.

This module does not synthesize audio.  It gives future auditions and final
rendering one canonical identity so changing a provider, model, or voice can
never silently reuse an approval from another voice.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from math import isfinite
from typing import Literal


class VoiceProfileError(ValueError):
    """Safe, stable failure for an invalid or stale voice identity."""

    def __init__(self, code: str, message: str = "voice profile is invalid") -> None:
        self.code = code
        super().__init__(message)


VoiceApprovalState = Literal["pending", "approved", "invalidated"]


@dataclass(frozen=True)
class VoiceProfile:
    provider: str
    model: str
    model_version: str
    voice_id: str
    reference_hash: str
    locale: str
    speed: float
    style: str
    stability: float
    approval_state: VoiceApprovalState
    profile_sha256: str = ""


def _canonical(profile: VoiceProfile) -> str:
    payload = asdict(profile)
    payload["profile_sha256"] = ""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_shape(profile: VoiceProfile) -> None:
    if not isinstance(profile, VoiceProfile):
        raise VoiceProfileError("voice_profile_invalid")
    for value in (
        profile.provider,
        profile.model,
        profile.model_version,
        profile.voice_id,
        profile.reference_hash,
        profile.locale,
        profile.style,
    ):
        if not isinstance(value, str) or not value.strip():
            raise VoiceProfileError("voice_profile_invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", profile.reference_hash):
        raise VoiceProfileError("voice_profile_invalid")
    if profile.approval_state not in {"pending", "approved", "invalidated"}:
        raise VoiceProfileError("voice_profile_invalid")
    if not isfinite(float(profile.speed)) or float(profile.speed) <= 0:
        raise VoiceProfileError("voice_profile_invalid")
    if not isfinite(float(profile.stability)) or not 0.0 <= float(profile.stability) <= 1.0:
        raise VoiceProfileError("voice_profile_invalid")


def build_voice_profile(
    *,
    provider: str,
    model: str,
    model_version: str,
    voice_id: str,
    reference_hash: str,
    locale: str,
    speed: float,
    style: str,
    stability: float,
    approval_state: VoiceApprovalState = "pending",
) -> VoiceProfile:
    profile = VoiceProfile(
        provider=provider,
        model=model,
        model_version=model_version,
        voice_id=voice_id,
        reference_hash=reference_hash,
        locale=locale,
        speed=float(speed),
        style=style,
        stability=float(stability),
        approval_state=approval_state,
    )
    _validate_shape(profile)
    digest = hashlib.sha256(_canonical(profile).encode("utf-8")).hexdigest()
    return replace(profile, profile_sha256=digest)


def validate_voice_profile(profile: VoiceProfile) -> VoiceProfile:
    _validate_shape(profile)
    if (
        not isinstance(profile.profile_sha256, str)
        or len(profile.profile_sha256) != 64
        or profile.profile_sha256 != hashlib.sha256(_canonical(profile).encode("utf-8")).hexdigest()
    ):
        raise VoiceProfileError("voice_profile_hash_invalid")
    return profile


def require_compatible_approval(previous: VoiceProfile, current: VoiceProfile) -> None:
    """Require a new audition whenever an approved identity changes."""

    validate_voice_profile(previous)
    validate_voice_profile(current)
    identity_fields = (
        "provider",
        "model",
        "model_version",
        "voice_id",
        "reference_hash",
        "locale",
        "speed",
        "style",
        "stability",
    )
    changed = any(getattr(previous, field) != getattr(current, field) for field in identity_fields)
    if previous.approval_state == "approved" and changed:
        raise VoiceProfileError(
            "voice_profile_reapproval_required",
            "voice provider, model, voice, or settings changed after approval",
        )
