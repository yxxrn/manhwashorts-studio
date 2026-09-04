"""Shared enums and constants.

These mirror the vocabulary used in the PRD so code, docs, and UI stay aligned.
"""

from __future__ import annotations

from enum import StrEnum

PROJECT_DURATION_MIN_SECONDS = 10
PROJECT_DURATION_MAX_SECONDS = 90
DEFAULT_TARGET_SECONDS = 55
STANDARD_FINAL_DURATION_MIN_SECONDS = 50.0
STANDARD_FINAL_DURATION_MAX_SECONDS = 60.0


class ProjectStatus(StrEnum):
    """Lifecycle of a project, per PRD FR-01."""

    DRAFT = "draft"
    GENERATING = "generating"
    REVIEW = "review"
    RENDERING = "rendering"
    READY = "ready"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    FAILED = "failed"


class ContentType(StrEnum):
    CHAPTER_RECAP = "chapter_recap"
    CHARACTER_PROFILE = "character_profile"
    FUN_FACTS = "fun_facts"
    THEORY = "theory"
    CLIFFHANGER = "cliffhanger"


class SpoilerLevel(StrEnum):
    MINIMAL = "minimal"
    MEDIUM = "medium"
    FULL = "full"


class NarrationStyle(StrEnum):
    DRAMATIC = "dramatic"
    CASUAL = "casual"
    MYSTERIOUS = "mysterious"
    FAST = "fast"
    INFORMATIVE = "informative"


class AssetType(StrEnum):
    TEXT = "text"
    DOCUMENT = "document"
    IMAGE = "image"
    AUDIO = "audio"
    MUSIC = "music"


class RightsStatus(StrEnum):
    """Provenance state for a source asset, per PRD section 8."""

    UNDECLARED = "undeclared"
    DECLARED = "declared"
    VERIFIED = "verified"
    REJECTED = "rejected"


class LicenseType(StrEnum):
    OWNED = "owned"
    LICENSED = "licensed"
    PERMISSION_GRANTED = "permission_granted"
    PUBLIC_DOMAIN = "public_domain"
    CREATIVE_COMMONS = "creative_commons"
    UNKNOWN = "unknown"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PrivacyStatus(StrEnum):
    PRIVATE = "private"
    UNLISTED = "unlisted"
    PUBLIC = "public"


class UploadStatus(StrEnum):
    PENDING = "pending"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    LIVE = "live"
    FAILED = "failed"


class CheckSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class CredentialKind(StrEnum):
    """What a bring-your-own-key credential is used for (v1.1 BYOK).

    Split by capability rather than by vendor, because one vendor can serve
    several roles (OpenAI does both chat and speech) and one role can be served
    by many vendors.
    """

    #: Chapter analysis, highlight picking, script rewriting, agentic steps.
    LLM = "llm"
    #: Text-to-speech narration.
    TTS = "tts"


class CredentialStatus(StrEnum):
    """Result of the last verification attempt against the provider."""

    #: Saved but never checked against the provider.
    UNVERIFIED = "unverified"
    #: Provider accepted the key and returned a model list.
    VERIFIED = "verified"
    #: Provider rejected the key, or the endpoint was unreachable.
    INVALID = "invalid"


class ScriptSection(StrEnum):
    """Default Shorts beat structure, per PRD FR-04."""

    HOOK = "hook"
    SETUP = "setup"
    CONFLICT = "conflict"
    TWIST = "twist"
    CTA = "cta"


# Product defaults: English text, American English narration.
DEFAULT_PROJECT_LANGUAGE = "en"

# Production Grok narrator default. Provider language remains English; voice_id controls timbre/accent.
DEFAULT_ENGLISH_VOICE_ID = "orion"
DEFAULT_ENGLISH_SPEED = 0.90


#: Default timing envelope (seconds) for the 75s editorial target.
SECTION_BUDGET: dict[ScriptSection, tuple[float, float]] = {
    ScriptSection.HOOK: (0.0, 2.0),
    ScriptSection.SETUP: (2.0, 8.0),
    ScriptSection.CONFLICT: (8.0, 45.0),
    ScriptSection.TWIST: (45.0, 65.0),
    ScriptSection.CTA: (65.0, 75.0),
}

#: Fraction of the target duration each section should occupy.
SECTION_WEIGHTS: dict[ScriptSection, float] = {
    ScriptSection.HOOK: 0.027,
    ScriptSection.SETUP: 0.08,
    ScriptSection.CONFLICT: 0.493,
    ScriptSection.TWIST: 0.267,
    ScriptSection.CTA: 0.133,
}

#: Words per second used for duration estimates before TTS runs.
WORDS_PER_SECOND: dict[NarrationStyle, float] = {
    NarrationStyle.DRAMATIC: 2.3,
    NarrationStyle.CASUAL: 2.6,
    NarrationStyle.MYSTERIOUS: 2.1,
    NarrationStyle.FAST: 3.2,
    NarrationStyle.INFORMATIVE: 2.7,
}

#: Subtitle safe-area insets as a fraction of frame height (YouTube Shorts UI).
SUBTITLE_SAFE_TOP = 0.12
SUBTITLE_SAFE_BOTTOM = 0.22

MAX_SUBTITLE_CHARS_PER_LINE = 28
MAX_SUBTITLE_LINES = 2
