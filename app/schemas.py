"""Pydantic request/response models for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.constants import (
    DEFAULT_ENGLISH_VOICE_ID,
    DEFAULT_PROJECT_LANGUAGE,
    DEFAULT_TARGET_SECONDS,
    PROJECT_DURATION_MAX_SECONDS,
    PROJECT_DURATION_MIN_SECONDS,
    ContentType,
    CredentialKind,
    LicenseType,
    NarrationStyle,
    PrivacyStatus,
    SpoilerLevel,
)

# --- auth ------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(default="", max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    name: str


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    timezone: str


# --- projects --------------------------------------------------------------


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    manhwa_title: str = Field(default="", max_length=200)
    chapter: str = Field(default="", max_length=60)
    content_type: ContentType = ContentType.CHAPTER_RECAP
    language: Literal["en", "id"] = DEFAULT_PROJECT_LANGUAGE
    spoiler_level: SpoilerLevel = SpoilerLevel.MEDIUM
    narration_style: NarrationStyle = NarrationStyle.DRAMATIC
    # New projects target the middle of the standard 50-60 second final window.
    # Stored projects retain their persisted values.
    target_duration: int = Field(
        default=DEFAULT_TARGET_SECONDS,
        ge=PROJECT_DURATION_MIN_SECONDS,
        le=PROJECT_DURATION_MAX_SECONDS,
    )
    voice_id: str = Field(default=DEFAULT_ENGLISH_VOICE_ID, max_length=80)
    series_name: str = Field(default="", max_length=200)
    cta_text: str = Field(default="", max_length=500)
    banned_words: list[str] = Field(default_factory=list)
    pronunciations: dict[str, str] = Field(default_factory=dict)
    template: str = Field(default="reference_matched_shorts_v2", max_length=60)  # New projects use the 60 FPS profile.


class ProjectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    manhwa_title: str | None = Field(default=None, max_length=200)
    chapter: str | None = Field(default=None, max_length=60)
    content_type: ContentType | None = None
    language: Literal["en", "id"] | None = None
    spoiler_level: SpoilerLevel | None = None
    narration_style: NarrationStyle | None = None
    target_duration: int | None = Field(
        default=None,
        ge=PROJECT_DURATION_MIN_SECONDS,
        le=PROJECT_DURATION_MAX_SECONDS,
    )
    voice_id: str | None = Field(default=None, max_length=80)
    series_name: str | None = Field(default=None, max_length=200)
    cta_text: str | None = Field(default=None, max_length=500)
    banned_words: list[str] | None = None
    pronunciations: dict[str, str] | None = None
    template: str | None = Field(default=None, max_length=60)
    archived: bool | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    title: str
    manhwa_title: str
    chapter: str
    content_type: str
    language: str
    spoiler_level: str
    narration_style: str
    target_duration: int
    status: str
    series_name: str
    cta_text: str
    voice_id: str
    template: str
    archived: bool
    error_message: str
    created_at: datetime
    updated_at: datetime


# --- assets ----------------------------------------------------------------


class RightsIn(BaseModel):
    """Rights declaration attached to an upload (PRD section 8)."""

    source_name: str = Field(default="", max_length=255)
    rights_owner: str = Field(default="", max_length=255)
    license_type: LicenseType = LicenseType.UNKNOWN
    permission_reference: str = Field(default="", max_length=2000)
    permission_date: str = Field(default="", max_length=40)
    usage_limits: str = Field(default="", max_length=2000)
    attribution: str = Field(default="", max_length=1000)
    declared: bool = False


class TextAssetCreate(BaseModel):
    text: str = Field(min_length=40)
    title: str = Field(default="notes.txt", max_length=255)
    rights: RightsIn = Field(default_factory=RightsIn)


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    type: str
    original_filename: str
    mime_type: str
    size_bytes: int
    checksum: str
    width: int
    height: int
    duration: float = 0.0
    source_name: str
    rights_owner: str
    license_type: str
    rights_status: str
    attribution: str
    order_index: int
    source_family: str
    source_family_manual: bool
    panel_bbox: dict = Field(default_factory=dict)
    panel_quality: dict = Field(default_factory=dict)
    panel_decision: str = "accept"
    created_at: datetime


class AssetRightsUpdate(RightsIn):
    pass


class AssetFamilyUpdate(BaseModel):
    source_family: str = Field(min_length=1, max_length=255)


# --- analysis / script -----------------------------------------------------


class AnalysisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    characters: list[dict]
    locations: list[str]
    events: list[dict]
    main_conflict: str
    twist: str
    cliffhanger: str
    pronunciation_candidates: list[str]
    low_confidence_notes: list[str]
    edited_by_user: bool
    state: str | None = None
    blocking_reasons: dict = Field(default_factory=dict)
    narrative_profile_id: str | None = None
    narrative_profile_version: str | None = None
    narrative_profile_sha256: str | None = None
    narrative_screening_warning_codes: list[str] = Field(default_factory=list)


class AnalysisStatusOut(BaseModel):
    state: str | None = None
    run_id: str | None = None
    provider_type: str | None = None
    provider_name: str | None = None
    model: str | None = None
    instruction_version: str | None = None
    instruction_sha256: str | None = None
    coverage_map_version: str | None = None
    coverage_map_hash: str | None = None
    total_panels: int = 0
    processed_panels: int = 0
    source_content_coverage_ratio: float = 0.0
    unresolved_material_area: int = 0
    reconciliation_complete: bool = False
    chain_reconciled: bool = False
    claim_count: int = 0
    passage_count: int = 0
    blocking_codes: list[str] = Field(default_factory=list)
    findings: list[dict] = Field(default_factory=list)
    narrative_profile_id: str | None = None
    narrative_profile_version: str | None = None
    narrative_profile_sha256: str | None = None
    narrative_screening_warning_codes: list[str] = Field(default_factory=list)


class AnalysisRequest(BaseModel):
    narrative_profile_id: str | None = None


class AnalysisUpdate(BaseModel):
    characters: list[dict] | None = None
    locations: list[str] | None = None
    events: list[dict] | None = None
    main_conflict: str | None = None
    twist: str | None = None
    cliffhanger: str | None = None
    pronunciation_candidates: list[str] | None = None


class ScriptGenerateRequest(BaseModel):
    keep_locked: bool = True
    hook_count: int = Field(default=3, ge=1, le=8)
    seed: int | None = None
    narrative_profile_id: str | None = None


class SectionIn(BaseModel):
    section: str
    text: str = ""
    locked: bool = False
    citations: list[int] = Field(default_factory=list)
    editorial_role: str = ""
    claim_ids: list[str] = Field(default_factory=list)
    evidence_panel_ids: list[str] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)


class ScriptUpdate(BaseModel):
    sections: list[SectionIn]
    selected_hook: int | None = Field(default=None, ge=0)


class ScriptApproveRequest(BaseModel):
    editorial_review_confirmed: bool


class ScriptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    version: int
    sections: list[dict]
    hook_options: list[str]
    selected_hook: int
    estimated_duration: float
    word_count: int
    warnings: list[dict]
    generator: str
    editorial_metadata: dict = Field(default_factory=dict)
    approved_by: str
    approved_at: datetime | None


# --- voice / timeline ------------------------------------------------------


class VoiceRequest(BaseModel):
    speed: float = Field(default=1.15, ge=0.5, le=2.0)
    provider: str | None = None


class VoiceAuditionRequest(BaseModel):
    voice_ids: list[str] = Field(min_length=4, max_length=4)
    speed: float = Field(default=1.15, ge=0.5, le=2.0)

    @field_validator("voice_ids")
    @classmethod
    def _validate_voice_ids(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned) or len(set(cleaned)) != 4:
            raise ValueError("voice_ids must contain four unique nonempty values")
        return cleaned


class VoiceAuditionItemOut(BaseModel):
    audition_id: str
    checksum_prefix: str
    index: int
    voice_id: str
    provider: str
    provider_key: str
    duration: float
    represented_roles: list[str]
    download_url: str


class VoiceAuditionOut(BaseModel):
    text: str
    speed: float
    represented_roles: list[str]
    items: list[VoiceAuditionItemOut]


class AudioSegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    section: str
    order_index: int
    text: str
    spoken_text: str = ""
    display_text: str = ""
    voice_id: str
    provider: str
    voice_profile_hash: str = ""
    voice_profile: dict = Field(default_factory=dict)
    duration: float
    start_time: float
    end_time: float
    word_timings: list[dict]
    dramatic_events: list[dict]
    user_uploaded: bool


class SceneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    asset_id: str | None
    order_index: int
    section: str
    start_time: float
    end_time: float
    focus_x: float
    focus_y: float
    focus_end_x: float
    focus_end_y: float
    roi_label: str
    source_family: str
    motion_mode: str
    motion_intensity: str
    motion_reason: str
    camera_curve: str
    camera_intent: str
    narration_timing: str
    effect: str
    disabled_effects: list[str]
    overlay_text: str
    transition: str


class SceneUpdate(BaseModel):
    asset_id: str | None = None
    focus_x: float | None = Field(default=None, ge=0.0, le=1.0)
    focus_y: float | None = Field(default=None, ge=0.0, le=1.0)
    focus_end_x: float | None = Field(default=None, ge=0.0, le=1.0)
    focus_end_y: float | None = Field(default=None, ge=0.0, le=1.0)
    roi_label: str | None = Field(default=None, max_length=40)
    camera_curve: str | None = Field(default=None, max_length=40)
    camera_intent: str | None = Field(default=None, max_length=20)
    narration_timing: str | None = Field(default=None, max_length=20)
    effect: str | None = Field(default=None, max_length=40)
    motion_intensity: str | None = Field(default=None, max_length=20)
    disabled_effects: list[str] | None = Field(default=None, max_length=12)
    overlay_text: str | None = Field(default=None, max_length=500)
    transition: str | None = Field(default=None, max_length=40)
    start_time: float | None = Field(default=None, ge=0.0)
    end_time: float | None = Field(default=None, ge=0.0)


class CueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    order_index: int
    text: str
    start_time: float
    end_time: float
    edited_by_user: bool


class CueUpdate(BaseModel):
    text: str | None = Field(default=None, max_length=500)
    start_time: float | None = Field(default=None, ge=0.0)
    end_time: float | None = Field(default=None, ge=0.0)


# --- quality / render ------------------------------------------------------


class QualityCheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    severity: str
    message: str
    passed: bool
    override_reason: str
    overridden_by: str


class QCOverrideEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    quality_code: str
    actor_id: str
    reason: str
    before_passed: bool
    after_passed: bool
    created_at: datetime


class QCHistorySnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    render_job_id: str | None
    passed: bool
    report: dict
    created_at: datetime


class QualitySummaryOut(BaseModel):
    total: int
    errors: int
    warnings: int
    can_publish: bool
    error_codes: list[str]
    warning_codes: list[str]
    checks: list[QualityCheckOut]


class OverrideRequest(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=5, max_length=1000)


class RenderRequestIn(BaseModel):
    kind: str = Field(default="final", pattern="^(preview|final)$")
    #: Which encoder to use. "auto" prefers a working GPU; an unavailable GPU
    #: falls back to CPU rather than failing the render.
    encoder: str = Field(
        default="auto",
        pattern="^(auto|cpu|nvenc|qsv|vaapi|videotoolbox)$",
    )
    profile: str = Field(default="Auto", pattern="^(Auto|Calm|Balanced|Dynamic|No motion)$")


class RenderJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    kind: str
    status: str
    progress: int
    stage: str
    output_key: str
    subtitle_key: str
    thumbnail_key: str
    checksum: str
    duration: float
    width: int
    height: int
    error_code: str
    error_message: str
    attempt: int
    started_at: datetime | None
    completed_at: datetime | None
    #: Encoder requested vs the one that actually ran (they differ on fallback).
    encoder_requested: str = "auto"
    encoder: str = ""
    encoder_hardware: bool = False
    encoder_fell_back: bool = False
    encoder_reason: str = ""
    render_profile: str = ""
    render_wall_seconds: float = 0.0
    peak_rss_bytes: int = 0
    scratch_bytes: int = 0


class DraftOut(BaseModel):
    script_id: str
    script_version: int
    estimated_duration: float
    audio_duration: float
    segments: int
    scenes: int
    cues: int
    warnings: list[dict]


# --- publish ---------------------------------------------------------------


class YouTubeBrowserAccountCreate(BaseModel):
    account_id: str = Field(min_length=1, max_length=32)
    label: str = Field(default="", max_length=120)
    trust_channel_defaults: bool | None = None


class YouTubeBrowserAccountUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    make_default: bool = False
    trust_channel_defaults: bool | None = None


class PublishRequest(BaseModel):
    channel_id: str | None = None
    youtube_account_id: str | None = Field(default=None, max_length=32)
    video_title: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=5000)
    tags: list[str] = Field(default_factory=list)
    privacy_status: PrivacyStatus = PrivacyStatus.PRIVATE
    scheduled_at: datetime | None = None
    confirm_public: bool = False
    trust_channel_defaults: bool | None = None

    @field_validator("tags")
    @classmethod
    def _limit_tags(cls, v: list[str]) -> list[str]:
        return [t.strip()[:60] for t in v if t.strip()][:20]


class PublicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    channel_id: str | None
    youtube_account_id: str = "default"
    youtube_video_id: str
    video_title: str
    description: str
    tags: list[str]
    privacy_status: str
    upload_status: str
    scheduled_at: datetime | None
    published_at: datetime | None
    error_message: str
    attempt: int
    thumbnail_status: str = "pending"
    thumbnail_error: str = ""
    thumbnail_attempt: int = 0
    thumbnail_note: str = ""
    thumbnail_retry_url: str | None = None


class MetadataOut(BaseModel):
    title: str
    description: str
    tags: list[str]


class ChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    channel_id: str
    channel_title: str
    scopes: list[str]
    connected_at: datetime | None
    revoked: bool


class StatOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    publication_id: str
    views: int
    likes: int
    comments: int
    average_view_duration: float
    average_percentage_viewed: float
    subscribers_gained: int
    synced_at: datetime
    source: str


# --- BYOK credentials (v1.1) ----------------------------------------------


class CredentialCreate(BaseModel):
    """Save an API key. The key is verified before it is stored."""

    kind: CredentialKind
    provider: str = Field(min_length=1, max_length=40)
    api_key: str = Field(min_length=8, max_length=500)
    base_url: str | None = Field(default=None, max_length=300)
    label: str = Field(default="", max_length=120)
    model: str = Field(default="", max_length=120)
    #: Only set false for offline/air-gapped setups; the row is then unverified.
    verify: bool = True

    @field_validator("api_key")
    @classmethod
    def _strip_key(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("api_key must not be blank")
        return cleaned


class CredentialTestRequest(BaseModel):
    """Check a key and list its models without saving anything."""

    kind: CredentialKind
    provider: str = Field(min_length=1, max_length=40)
    api_key: str = Field(min_length=8, max_length=500)
    base_url: str | None = Field(default=None, max_length=300)


class ModelSelectRequest(BaseModel):
    model: str = Field(min_length=1, max_length=120)


class ModelOut(BaseModel):
    id: str
    label: str = ""


class CredentialOut(BaseModel):
    """A stored credential. Never contains the key itself."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    provider: str
    label: str
    #: Last four characters only, e.g. "...4f2a".
    key_hint: str
    base_url: str | None
    model: str
    available_models: list[dict] = Field(default_factory=list)
    status: str
    status_message: str
    verified_at: datetime | None
    last_used_at: datetime | None
    is_default: bool
    is_active: bool
    created_at: datetime


class CredentialTestOut(BaseModel):
    ok: bool
    message: str
    models: list[ModelOut] = Field(default_factory=list)


class ProviderSpecOut(BaseModel):
    key: str
    label: str
    kind: str
    default_base_url: str
    console_url: str
    custom_endpoint: bool
    notes: str


class ProviderCatalogOut(BaseModel):
    llm: list[ProviderSpecOut]
    tts: list[ProviderSpecOut]


class ResolutionOut(BaseModel):
    """Which provider a stage will actually use, and why."""

    source: str
    provider: str
    model: str
    label: str
    credential_id: str
    reason: str


class ActiveProvidersOut(BaseModel):
    llm: ResolutionOut
    tts: ResolutionOut


# --- misc ------------------------------------------------------------------


class EncoderOut(BaseModel):
    """One encoder backend and whether it works on this machine."""

    key: str
    label: str
    codec: str
    hardware: bool
    available: bool
    #: Why it is unavailable, when it is not.
    detail: str = ""
    notes: str = ""


class ActiveEncoderOut(BaseModel):
    encoder: str
    label: str
    codec: str
    hardware: bool
    requested: str
    fell_back: bool
    reason: str


class EncoderCapabilityOut(BaseModel):
    """Encoder report for the settings UI: what is configured vs what will run."""

    configured: str
    active: ActiveEncoderOut
    encoders: list[EncoderOut]
    gpu_available: bool


class DiskUsageOut(BaseModel):
    """Data directory usage (v1.3.1). Lets an operator see growth early."""

    tmp_bytes: int = 0
    output_bytes: int = 0
    storage_bytes: int = 0
    total_bytes: int = 0
    total_human: str = ""
    max_bytes: int = 0
    over_limit: bool = False


class HealthOut(BaseModel):
    status: str
    version: str
    environment: str
    ffmpeg: bool
    tts_provider: str
    llm_provider: str
    youtube_enabled: bool
    problems: list[str]
    #: Encoder actually in use, e.g. "cpu" or "nvenc".
    video_encoder: str = "cpu"
    gpu_encoding: bool = False
    #: Empty when the usage scan could not run; health must never fail on it.
    disk_usage: DiskUsageOut | None = None


class LocalCapabilitiesOut(BaseModel):
    api_version: str
    local_only_default: bool
    auth: str
    openapi_url: str
    orchestration: bool
    approval_required: bool
    render_async: bool
    stages: list[str]
    source_connectors: list[str] = Field(default_factory=list)
    publishers: list[str] = Field(default_factory=list)


class ProjectPipelineStatusOut(BaseModel):
    project_id: str
    project_status: str
    current_stage: str
    action_required: str | None = None
    analysis_state: str | None = None
    script_id: str | None = None
    script_version: int | None = None
    script_approved: bool = False
    voice_ready: bool = False
    timeline_ready: bool = False
    quality_ready: bool = False
    quality_blocking_codes: list[str] = Field(default_factory=list)
    render_job_id: str | None = None
    render_status: str | None = None
    render_progress: int = 0
    render_stage: str = ""
    download_url: str | None = None
    publication_id: str | None = None
    publish_status: str | None = None
    youtube_video_id: str | None = None
    youtube_url: str | None = None
    thumbnail_upload_status: str | None = None
    thumbnail_upload_note: str = ""
    thumbnail_retry_url: str | None = None


class ProjectRunRequest(BaseModel):
    until: Literal["analysis", "draft", "voice", "timeline", "quality", "render", "publish"] = "draft"
    seed: int | None = None
    narrative_profile_id: str | None = None
    approval_mode: Literal["manual", "trusted_agent"] = "manual"
    confirm_publish_intent: bool = False
    channel_id: str | None = None
    youtube_account_id: str | None = Field(default=None, max_length=32)
    privacy_status: PrivacyStatus = PrivacyStatus.PRIVATE
    scheduled_at: datetime | None = None
    confirm_public: bool = False


class SuwayomiStatusOut(BaseModel):
    enabled: bool = True
    available: bool = False
    url: str = ""
    sources: int = 0
    searchable_sources: int = 0
    needs_extension_setup: bool = False
    managed: bool = False
    installed: bool = False
    error: str = ""


class SuwayomiSearchRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    language: str | None = Field(default=None, max_length=20)
    source_id: str | None = None


class SuwayomiSearchItemOut(BaseModel):
    manga_id: int
    title: str
    source_id: str
    source: str
    language: str
    thumbnail_url: str = ""


class SuwayomiImportRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    chapter_from: float = Field(ge=0)
    chapter_to: float = Field(ge=0)
    language: str | None = Field(default=None, max_length=20)
    source_id: str | None = None
    rights: RightsIn = Field(default_factory=RightsIn)


class SuwayomiImportOut(BaseModel):
    status: str
    project_id: str
    manga_id: int
    title: str
    source_id: str
    source: str
    language: str
    chapters: list[str]
    pages_downloaded: int
    assets_created: int
    duplicates_skipped: int
    asset_ids: list[str]
    rights_status: str


class MessageOut(BaseModel):
    detail: str
