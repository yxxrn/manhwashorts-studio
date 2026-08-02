"""SQLAlchemy ORM models.

Mirrors the entity list in PRD section 11. Every table that holds user
material also carries provenance columns so the rights gate in
``app.services.policy`` can reason about publication safety.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.constants import (
    AssetType,
    ContentType,
    CredentialStatus,
    JobStatus,
    LicenseType,
    NarrationStyle,
    PrivacyStatus,
    ProjectStatus,
    RightsStatus,
    SpoilerLevel,
    UploadStatus,
)
from app.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    workspaces: Mapped[list[Workspace]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="My Workspace")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Jakarta")

    owner: Mapped[User] = relationship(back_populates="workspaces")
    projects: Mapped[list[Project]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    channels: Mapped[list[YouTubeChannel]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    credentials: Mapped[list[ProviderCredential]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    manhwa_title: Mapped[str] = mapped_column(String(200), default="")
    chapter: Mapped[str] = mapped_column(String(60), default="")
    content_type: Mapped[str] = mapped_column(String(40), default=ContentType.CHAPTER_RECAP)
    language: Mapped[str] = mapped_column(String(10), default="id")
    spoiler_level: Mapped[str] = mapped_column(String(20), default=SpoilerLevel.MEDIUM)
    narration_style: Mapped[str] = mapped_column(String(20), default=NarrationStyle.DRAMATIC)
    target_duration: Mapped[int] = mapped_column(Integer, default=60)
    status: Mapped[str] = mapped_column(String(20), default=ProjectStatus.DRAFT, index=True)

    series_name: Mapped[str] = mapped_column(String(200), default="")
    cta_text: Mapped[str] = mapped_column(Text, default="")
    banned_words: Mapped[list[str]] = mapped_column(JSON, default=list)
    pronunciations: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    voice_id: Mapped[str] = mapped_column(String(80), default="id")
    template: Mapped[str] = mapped_column(String(60), default="classic")

    error_message: Mapped[str] = mapped_column(Text, default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)

    workspace: Mapped[Workspace] = relationship(back_populates="projects")
    assets: Mapped[list[SourceAsset]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    analyses: Mapped[list[StoryAnalysis]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    scripts: Mapped[list[ScriptVersion]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    scenes: Mapped[list[TimelineScene]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    render_jobs: Mapped[list[RenderJob]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    publications: Mapped[list[Publication]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    @property
    def approved_script(self) -> ScriptVersion | None:
        approved = [s for s in self.scripts if s.approved_at is not None]
        return max(approved, key=lambda s: s.version) if approved else None

    @property
    def latest_script(self) -> ScriptVersion | None:
        return max(self.scripts, key=lambda s: s.version) if self.scripts else None


class SourceAsset(Base, TimestampMixin):
    """User-supplied material plus its rights provenance (PRD FR-02)."""

    __tablename__ = "source_assets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(20), default=AssetType.TEXT)
    original_filename: Mapped[str] = mapped_column(String(255), default="")
    storage_key: Mapped[str] = mapped_column(String(500), default="")
    mime_type: Mapped[str] = mapped_column(String(120), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str] = mapped_column(String(64), default="", index=True)

    extracted_text: Mapped[str] = mapped_column(Text, default="")
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    duration: Mapped[float] = mapped_column(Float, default=0.0)

    # Provenance
    source_name: Mapped[str] = mapped_column(String(255), default="")
    rights_owner: Mapped[str] = mapped_column(String(255), default="")
    license_type: Mapped[str] = mapped_column(String(40), default=LicenseType.UNKNOWN)
    permission_reference: Mapped[str] = mapped_column(Text, default="")
    permission_date: Mapped[str] = mapped_column(String(40), default="")
    usage_limits: Mapped[str] = mapped_column(Text, default="")
    rights_status: Mapped[str] = mapped_column(
        String(20), default=RightsStatus.UNDECLARED, index=True
    )
    attribution: Mapped[str] = mapped_column(Text, default="")

    order_index: Mapped[int] = mapped_column(Integer, default=0)

    project: Mapped[Project] = relationship(back_populates="assets")

    @property
    def is_publishable(self) -> bool:
        return self.rights_status in (RightsStatus.DECLARED, RightsStatus.VERIFIED)


class StoryAnalysis(Base, TimestampMixin):
    """Extracted story facts with citations back to source assets (FR-03)."""

    __tablename__ = "story_analyses"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    characters: Mapped[list[dict]] = mapped_column(JSON, default=list)
    locations: Mapped[list[str]] = mapped_column(JSON, default=list)
    events: Mapped[list[dict]] = mapped_column(JSON, default=list)
    main_conflict: Mapped[str] = mapped_column(Text, default="")
    twist: Mapped[str] = mapped_column(Text, default="")
    cliffhanger: Mapped[str] = mapped_column(Text, default="")
    pronunciation_candidates: Mapped[list[str]] = mapped_column(JSON, default=list)
    low_confidence_notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    edited_by_user: Mapped[bool] = mapped_column(Boolean, default=False)

    project: Mapped[Project] = relationship(back_populates="analyses")


class ScriptVersion(Base, TimestampMixin):
    """A versioned narration script (FR-04)."""

    __tablename__ = "script_versions"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_script_project_version"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)

    # sections: [{section, text, locked, estimated_duration, citations: [asset_id]}]
    sections: Mapped[list[dict]] = mapped_column(JSON, default=list)
    hook_options: Mapped[list[str]] = mapped_column(JSON, default=list)
    selected_hook: Mapped[int] = mapped_column(Integer, default=0)
    estimated_duration: Mapped[float] = mapped_column(Float, default=0.0)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list[dict]] = mapped_column(JSON, default=list)
    generator: Mapped[str] = mapped_column(String(40), default="rules")

    approved_by: Mapped[str] = mapped_column(String(32), default="")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="scripts")
    audio_segments: Mapped[list[AudioSegment]] = relationship(
        back_populates="script_version", cascade="all, delete-orphan"
    )

    @property
    def plain_text(self) -> str:
        return "\n\n".join(s.get("text", "") for s in self.sections if s.get("text"))


class AudioSegment(Base, TimestampMixin):
    """One TTS clip aligned to the timeline (FR-05)."""

    __tablename__ = "audio_segments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    script_version_id: Mapped[str] = mapped_column(
        ForeignKey("script_versions.id", ondelete="CASCADE"), index=True
    )
    section: Mapped[str] = mapped_column(String(20), default="")
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    voice_id: Mapped[str] = mapped_column(String(80), default="")
    provider: Mapped[str] = mapped_column(String(40), default="")
    storage_key: Mapped[str] = mapped_column(String(500), default="")
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    start_time: Mapped[float] = mapped_column(Float, default=0.0)
    end_time: Mapped[float] = mapped_column(Float, default=0.0)
    # word timings: [{word, start, end}]
    word_timings: Mapped[list[dict]] = mapped_column(JSON, default=list)
    user_uploaded: Mapped[bool] = mapped_column(Boolean, default=False)

    script_version: Mapped[ScriptVersion] = relationship(back_populates="audio_segments")


class TimelineScene(Base, TimestampMixin):
    """A visual scene bound to an asset and a time range (FR-06)."""

    __tablename__ = "timeline_scenes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_assets.id", ondelete="SET NULL"), nullable=True
    )
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    section: Mapped[str] = mapped_column(String(20), default="")
    start_time: Mapped[float] = mapped_column(Float, default=0.0)
    end_time: Mapped[float] = mapped_column(Float, default=0.0)
    # focal point 0..1 used for 9:16 crop
    focus_x: Mapped[float] = mapped_column(Float, default=0.5)
    focus_y: Mapped[float] = mapped_column(Float, default=0.4)
    focus_end_x: Mapped[float] = mapped_column(Float, default=0.5)
    focus_end_y: Mapped[float] = mapped_column(Float, default=0.4)
    roi_label: Mapped[str] = mapped_column(String(40), default="")
    camera_curve: Mapped[str] = mapped_column(String(40), default="")
    effect: Mapped[str] = mapped_column(String(40), default="kenburns_in")
    overlay_text: Mapped[str] = mapped_column(Text, default="")
    transition: Mapped[str] = mapped_column(String(40), default="fade")

    project: Mapped[Project] = relationship(back_populates="scenes")
    asset: Mapped[SourceAsset | None] = relationship()

    @property
    def duration(self) -> float:
        return max(0.0, self.end_time - self.start_time)


class SubtitleCue(Base, TimestampMixin):
    """Subtitle cue with timing (FR-07)."""

    __tablename__ = "subtitle_cues"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    start_time: Mapped[float] = mapped_column(Float, default=0.0)
    end_time: Mapped[float] = mapped_column(Float, default=0.0)
    edited_by_user: Mapped[bool] = mapped_column(Boolean, default=False)


class QualityCheck(Base, TimestampMixin):
    """Result of a single pre-publication check (FR-08)."""

    __tablename__ = "quality_checks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(80))
    severity: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text, default="")
    passed: Mapped[bool] = mapped_column(Boolean, default=True)
    override_reason: Mapped[str] = mapped_column(Text, default="")
    overridden_by: Mapped[str] = mapped_column(String(32), default="")


class RenderJob(Base, TimestampMixin):
    """Async render or preview job (FR-09)."""

    __tablename__ = "render_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20), default="final")  # preview | final
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.QUEUED, index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(80), default="")
    output_key: Mapped[str] = mapped_column(String(500), default="")
    subtitle_key: Mapped[str] = mapped_column(String(500), default="")
    thumbnail_key: Mapped[str] = mapped_column(String(500), default="")
    checksum: Mapped[str] = mapped_column(String(64), default="")
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str] = mapped_column(String(80), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    log_tail: Mapped[str] = mapped_column(Text, default="")
    attempt: Mapped[int] = mapped_column(Integer, default=1)

    # Encoder choice (v1.2). ``requested`` is what the caller asked for
    # (auto | cpu | nvenc | qsv | vaapi | videotoolbox); ``encoder`` is what
    # actually ran, which differs when a GPU was unavailable and we fell back.
    encoder_requested: Mapped[str] = mapped_column(String(20), default="auto")
    encoder: Mapped[str] = mapped_column(String(20), default="")
    encoder_hardware: Mapped[bool] = mapped_column(Boolean, default=False)
    encoder_fell_back: Mapped[bool] = mapped_column(Boolean, default=False)
    encoder_reason: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="render_jobs")


class YouTubeChannel(Base, TimestampMixin):
    """Connected channel with encrypted OAuth credentials (FR-10)."""

    __tablename__ = "youtube_channels"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[str] = mapped_column(String(120), default="")
    channel_title: Mapped[str] = mapped_column(String(200), default="")
    # Fernet-encrypted JSON credential blob. Never logged.
    encrypted_credentials: Mapped[str] = mapped_column(Text, default="")
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)

    workspace: Mapped[Workspace] = relationship(back_populates="channels")


class Publication(Base, TimestampMixin):
    """Upload attempt / published video record (FR-10, FR-11)."""

    __tablename__ = "publications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    render_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("render_jobs.id", ondelete="SET NULL"), nullable=True
    )
    channel_id: Mapped[str | None] = mapped_column(
        ForeignKey("youtube_channels.id", ondelete="SET NULL"), nullable=True
    )
    youtube_video_id: Mapped[str] = mapped_column(String(40), default="")
    video_title: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    privacy_status: Mapped[str] = mapped_column(String(20), default=PrivacyStatus.PRIVATE)
    upload_status: Mapped[str] = mapped_column(String(20), default=UploadStatus.PENDING, index=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, default="")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(64), default="", index=True)

    project: Mapped[Project] = relationship(back_populates="publications")


class VideoStat(Base, TimestampMixin):
    """Analytics snapshot pulled from YouTube (FR-12)."""

    __tablename__ = "video_stats"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    publication_id: Mapped[str] = mapped_column(
        ForeignKey("publications.id", ondelete="CASCADE"), index=True
    )
    views: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    average_view_duration: Mapped[float] = mapped_column(Float, default=0.0)
    average_percentage_viewed: Mapped[float] = mapped_column(Float, default=0.0)
    subscribers_gained: Mapped[int] = mapped_column(Integer, default=0)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    source: Mapped[str] = mapped_column(String(40), default="youtube_api")


class ProviderCredential(Base, TimestampMixin):
    """A user-supplied API key for an AI provider (v1.1 BYOK).

    The key itself is never stored in plaintext: only a Fernet-encrypted blob in
    ``encrypted_secret``, plus a short non-reversible ``key_hint`` (last four
    characters) so the UI can tell two keys apart without decrypting either.

    ``kind`` is the capability (llm / tts) and ``provider`` is the vendor
    adapter. One credential per (workspace, kind, provider) pair; the newest
    ``is_default`` row for a kind is what the pipeline uses.
    """

    __tablename__ = "provider_credentials"
    __table_args__ = (
        UniqueConstraint("workspace_id", "kind", "provider", name="uq_credential_scope"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(120), default="")

    #: Fernet-encrypted JSON: {"api_key": "..."}. Never logged, never returned.
    encrypted_secret: Mapped[str] = mapped_column(Text, default="")
    #: Last 4 chars of the key, for display only (e.g. "...4f2a").
    key_hint: Mapped[str] = mapped_column(String(12), default="")
    #: Override for self-hosted or proxy endpoints; None means vendor default.
    base_url: Mapped[str | None] = mapped_column(String(300), nullable=True)

    #: Model chosen by the user from the fetched list.
    model: Mapped[str] = mapped_column(String(120), default="")
    #: Models returned by the provider at last verification, cached for the UI.
    available_models: Mapped[list] = mapped_column(JSON, default=list)

    status: Mapped[str] = mapped_column(String(20), default=CredentialStatus.UNVERIFIED)
    status_message: Mapped[str] = mapped_column(Text, default="")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    is_default: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    workspace: Mapped[Workspace] = relationship(back_populates="credentials")


class AuditLog(Base):
    """Append-only audit trail for sensitive actions (NFR: security)."""

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    actor_id: Mapped[str] = mapped_column(String(32), default="")
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(60), default="")
    entity_id: Mapped[str] = mapped_column(String(32), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
