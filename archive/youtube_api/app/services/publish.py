"""Publication flow (PRD FR-10, FR-11, FR-12).

Publishing is the last gate in the pipeline and the most consequential, so it
re-checks everything rather than trusting earlier state:

* quality checks must pass (no blocking errors),
* the rendered file must exist and match its recorded checksum,
* public visibility needs both config opt-in and explicit confirmation.

Uploads are idempotent: a retry reuses the existing Publication row and never
re-renders, which is what makes the "retry without re-render" requirement in
FR-10 hold.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import (
    PrivacyStatus,
    ProjectStatus,
    UploadStatus,
)
from app.models import (
    Project,
    Publication,
    RenderJob,
    VideoStat,
    YouTubeChannel,
)
from app.security import decrypt_json, new_idempotency_key
from app.services import policy
from app.services import quality as quality_svc
from app.services import youtube as yt
from app.services.file_integrity import sha256_file
from app.services.pipeline import (
    PipelineError,
    audit,
    evaluate_quality_checks,
    get_project,
    project_assets,
    run_quality_checks,
    successful_render,
)


def _now() -> datetime:
    return datetime.now(UTC)


def workspace_channels(db: Session, workspace_id: str) -> list[YouTubeChannel]:
    return list(
        db.scalars(
            select(YouTubeChannel)
            .where(
                YouTubeChannel.workspace_id == workspace_id,
                YouTubeChannel.revoked == False,  # noqa: E712
            )
            .order_by(YouTubeChannel.created_at)
        )
    )


def resolve_channel(db: Session, project: Project, channel_id: str | None) -> YouTubeChannel | None:
    """Pick the requested channel, or the workspace default."""
    channels = workspace_channels(db, project.workspace_id)
    if channel_id:
        for channel in channels:
            if channel.id == channel_id:
                return channel
        raise PipelineError("that channel is not connected to this workspace")
    return channels[0] if channels else None


def verify_artifact(job: RenderJob) -> Path:
    """Confirm the rendered file is present and unmodified since render."""
    if not job.output_key:
        raise PipelineError("the render job has no output file")
    path = Path(job.output_key)
    if not path.is_file():
        raise PipelineError(
            "the rendered video is missing from disk. Re-render before publishing."
        )
    if job.checksum:
        actual = sha256_file(path)
        if actual != job.checksum:
            raise PipelineError(
                "the rendered file does not match its checksum from render time. "
                "Re-render to be safe."
            )
    return path


def _thumbnail_path(job: RenderJob) -> Path | None:
    if not job.output_key:
        return None
    path = Path(job.output_key).parent / "thumbnail.jpg"
    return path if path.is_file() else None


def _attempt_thumbnail_upload(
    db: Session,
    publication: Publication,
    job: RenderJob,
    provider: yt.YouTubeProvider,
    credentials: dict,
    actor_id: str = "",
) -> bool:
    """Best-effort thumbnail upload. Video publication must never fail here."""
    publication.thumbnail_attempt = (publication.thumbnail_attempt or 0) + 1
    path = _thumbnail_path(job)
    if path is None:
        publication.thumbnail_status = "not_available"
        publication.thumbnail_error = "publishable thumbnail.jpg was not found"
        audit(db, "publish.thumbnail_missing", "publication", publication.id, actor_id)
        return False
    try:
        provider.set_thumbnail(publication.youtube_video_id, path, credentials)
    except Exception as exc:  # thumbnail failure is explicitly non-blocking
        if isinstance(exc, yt.YouTubeError):
            detail = f"{exc.code}: {exc}"
            code = exc.code
        else:
            detail = f"thumbnail_upload_failed: {type(exc).__name__}: {exc}"
            code = "thumbnail_upload_failed"
        publication.thumbnail_status = "failed"
        publication.thumbnail_error = detail[:1000]
        audit(db, "publish.thumbnail_failed", "publication", publication.id, actor_id, code=code)
        return False
    publication.thumbnail_status = "uploaded"
    publication.thumbnail_error = ""
    audit(db, "publish.thumbnail_succeeded", "publication", publication.id, actor_id)
    return True


def build_metadata_for(db: Session, project_id: str) -> dict:
    """Draft publish metadata from the approved script and asset attributions."""
    project = get_project(db, project_id)
    from app.services.pipeline import current_script

    script = current_script(db, project_id)
    assets = project_assets(db, project_id)
    attributions = sorted({a.attribution.strip() for a in assets if a.attribution.strip()})
    return yt.build_metadata(
        project_title=project.title,
        manhwa_title=project.manhwa_title,
        chapter=project.chapter,
        script_text=script.plain_text if script else "",
        attribution="; ".join(attributions),
    )


def publish(
    db: Session,
    project_id: str,
    *,
    channel_id: str | None = None,
    video_title: str = "",
    description: str = "",
    tags: list[str] | None = None,
    privacy_status: str = PrivacyStatus.PRIVATE,
    scheduled_at: datetime | None = None,
    confirm_public: bool = False,
    actor_id: str = "",
) -> Publication:
    """Upload the latest successful render. Requires passing quality checks."""
    project = get_project(db, project_id)

    # 1. Public visibility is double-gated.
    findings = policy.check_public_publish(privacy_status)
    if findings:
        raise PipelineError(findings[0].message)
    if privacy_status == PrivacyStatus.PUBLIC and not confirm_public:
        raise PipelineError(
            "Publishing publicly needs explicit confirmation. "
            "Set confirm_public=true after reviewing the video."
        )

    # 2. There must be a successful final render.
    job = successful_render(db, project_id)
    if job is None:
        raise PipelineError(
            "no successful final render found. Render the video before publishing."
        )
    video_path = verify_artifact(job)

    # 3. Re-run the gates; never trust a stale pass.
    results = run_quality_checks(db, project_id, job=job, actor_id=actor_id)
    blocking = [r for r in results if r.blocking]
    if blocking:
        raise PipelineError(
            "Quality checks must pass before publishing: "
            + "; ".join(r.message for r in blocking[:3])
        )

    channel = resolve_channel(db, project, channel_id)
    provider = yt.get_provider()

    credentials: dict = {}
    if channel is not None and channel.encrypted_credentials:
        try:
            credentials = decrypt_json(channel.encrypted_credentials)
        except ValueError as exc:
            raise PipelineError(
                "stored channel credentials could not be decrypted. Reconnect the channel."
            ) from exc
    if isinstance(provider, yt.GoogleYouTubeProvider) and not credentials:
        raise PipelineError("connect a YouTube channel before publishing")

    # 4. Reuse a pending row so retries preserve the exact metadata/title.
    publication = db.scalars(
        select(Publication)
        .where(
            Publication.project_id == project_id,
            Publication.upload_status.in_(
                [UploadStatus.PENDING, UploadStatus.FAILED, UploadStatus.UPLOADING]
            ),
        )
        .order_by(Publication.created_at.desc())
    ).first()

    saved_title = publication.video_title if publication is not None else ""
    saved_description = publication.description if publication is not None else ""
    saved_tags = list(publication.tags or []) if publication is not None else []
    needs_defaults = not (video_title or saved_title) or not (description or saved_description) or not (tags or saved_tags)
    defaults = build_metadata_for(db, project_id) if needs_defaults else {"title": "", "description": "", "tags": []}
    final_title = (video_title or saved_title or defaults["title"])[:100]
    final_description = (description or saved_description or defaults["description"])[:5000]
    final_tags = (tags if tags else saved_tags or defaults["tags"])[:20]

    if publication is None:
        publication = Publication(
            project_id=project_id,
            render_job_id=job.id,
            channel_id=channel.id if channel else None,
            idempotency_key=new_idempotency_key(),
        )
        db.add(publication)

    publication.render_job_id = job.id
    publication.channel_id = channel.id if channel else None
    publication.video_title = final_title
    publication.description = final_description
    publication.tags = final_tags
    publication.privacy_status = privacy_status
    publication.scheduled_at = scheduled_at
    publication.upload_status = UploadStatus.UPLOADING
    # Column defaults are applied at INSERT, so a freshly constructed row still
    # has attempt=None here. Coalesce before incrementing.
    publication.attempt = (publication.attempt or 0) + 1
    publication.error_message = ""
    db.flush()
    db.commit()

    # 6. Upload.
    try:
        result = provider.upload(
            video_path=video_path,
            title=final_title,
            description=final_description,
            tags=final_tags,
            privacy_status=privacy_status,
            scheduled_at=scheduled_at,
            credentials=credentials,
        )
    except yt.YouTubeError as exc:
        publication.upload_status = UploadStatus.FAILED
        publication.error_message = f"{exc.code}: {exc}"[:1000]
        project.status = ProjectStatus.FAILED
        project.error_message = str(exc)[:1000]
        audit(
            db, "publish.failed", "publication", publication.id, actor_id,
            code=exc.code, retryable=exc.retryable,
        )
        db.flush()
        db.commit()
        raise PipelineError(str(exc)) from exc

    # Persist the successful video first. A later thumbnail failure must never
    # cause a retry to upload the video twice.
    publication.youtube_video_id = result.video_id
    publication.upload_status = UploadStatus.UPLOADED
    publication.privacy_status = result.privacy_status
    if scheduled_at is not None:
        project.status = ProjectStatus.SCHEDULED
    else:
        publication.published_at = _now()
        project.status = ProjectStatus.PUBLISHED
    project.error_message = ""
    audit(
        db, "publish.video_succeeded", "publication", publication.id, actor_id,
        provider=result.provider, video_id=result.video_id, privacy=result.privacy_status,
    )
    db.flush()
    db.commit()

    _attempt_thumbnail_upload(db, publication, job, provider, credentials, actor_id)
    audit(
        db, "publish.succeeded", "publication", publication.id, actor_id,
        provider=result.provider,
        video_id=result.video_id,
        privacy=result.privacy_status,
        scheduled=scheduled_at.isoformat() if scheduled_at else None,
        thumbnail_status=publication.thumbnail_status,
    )
    db.flush()
    db.commit()
    return publication


def retry_publish(db: Session, publication_id: str, actor_id: str = "") -> Publication:
    """Retry a failed upload without re-rendering."""
    publication = db.get(Publication, publication_id)
    if publication is None:
        raise PipelineError("publication not found")
    if publication.upload_status == UploadStatus.UPLOADED:
        raise PipelineError("this video was already uploaded")

    return publish(
        db,
        publication.project_id,
        channel_id=publication.channel_id,
        video_title=publication.video_title,
        description=publication.description,
        tags=list(publication.tags or []),
        privacy_status=publication.privacy_status,
        scheduled_at=publication.scheduled_at,
        confirm_public=publication.privacy_status == PrivacyStatus.PUBLIC,
        actor_id=actor_id,
    )


def retry_thumbnail(db: Session, publication_id: str, actor_id: str = "") -> Publication:
    """Retry only the custom thumbnail; never upload the video again."""
    publication = db.get(Publication, publication_id)
    if publication is None:
        raise PipelineError("publication not found")
    if publication.upload_status != UploadStatus.UPLOADED or not publication.youtube_video_id:
        raise PipelineError("the video must be uploaded before retrying its thumbnail")
    job = db.get(RenderJob, publication.render_job_id) if publication.render_job_id else None
    if job is None:
        raise PipelineError("the publication no longer has its render job")
    project = get_project(db, publication.project_id)
    channel = resolve_channel(db, project, publication.channel_id)
    provider = yt.get_provider()
    credentials: dict = {}
    if channel is not None and channel.encrypted_credentials:
        try:
            credentials = decrypt_json(channel.encrypted_credentials)
        except ValueError as exc:
            raise PipelineError("stored channel credentials could not be decrypted. Reconnect the channel.") from exc
    if not publication.youtube_video_id.startswith("dryrun_") and not isinstance(provider, yt.GoogleYouTubeProvider):
        raise PipelineError("YouTube OAuth is not configured; reconnect the channel before retrying the thumbnail")
    if isinstance(provider, yt.GoogleYouTubeProvider) and not credentials:
        raise PipelineError("connect a YouTube channel before retrying the thumbnail")
    _attempt_thumbnail_upload(db, publication, job, provider, credentials, actor_id)
    db.flush()
    db.commit()
    return publication


def sync_stats(db: Session, publication_id: str, actor_id: str = "") -> VideoStat | None:
    """Pull an analytics snapshot. Returns None when no data is available."""
    publication = db.get(Publication, publication_id)
    if publication is None:
        raise PipelineError("publication not found")
    if not publication.youtube_video_id:
        raise PipelineError("this project has not been uploaded yet")

    credentials: dict = {}
    if publication.channel_id:
        channel = db.get(YouTubeChannel, publication.channel_id)
        if channel and channel.encrypted_credentials:
            try:
                credentials = decrypt_json(channel.encrypted_credentials)
            except ValueError:
                credentials = {}

    provider = yt.get_provider()
    try:
        stats = provider.fetch_stats(publication.youtube_video_id, credentials)
    except yt.YouTubeError as exc:
        raise PipelineError(str(exc)) from exc

    if not stats.available:
        # Do not persist a row of zeros that looks like real data.
        audit(db, "stats.unavailable", "publication", publication.id, actor_id, source=stats.source)
        db.flush()
        return None

    row = VideoStat(
        publication_id=publication.id,
        views=stats.views,
        likes=stats.likes,
        comments=stats.comments,
        average_view_duration=stats.average_view_duration,
        average_percentage_viewed=stats.average_percentage_viewed,
        subscribers_gained=stats.subscribers_gained,
        source=stats.source,
    )
    db.add(row)
    audit(db, "stats.sync", "publication", publication.id, actor_id, views=stats.views)
    db.flush()
    return row


def project_publications(db: Session, project_id: str) -> list[Publication]:
    return list(
        db.scalars(
            select(Publication)
            .where(Publication.project_id == project_id)
            .order_by(Publication.created_at.desc())
        )
    )


def latest_stats(db: Session, publication_id: str) -> VideoStat | None:
    return db.scalars(
        select(VideoStat)
        .where(VideoStat.publication_id == publication_id)
        .order_by(VideoStat.synced_at.desc())
    ).first()


def can_publish(db: Session, project_id: str) -> dict:
    """Report publish readiness using the exact same evaluator as publish."""
    job = successful_render(db, project_id)
    if job is None:
        return {
            "ready": False,
            "reason": "no successful final render yet",
            "checks": None,
        }

    results = evaluate_quality_checks(db, project_id, job=job)
    summary = quality_svc.summarise(results)
    return {
        "ready": summary["can_publish"],
        "reason": "" if summary["can_publish"] else "; ".join(summary["error_codes"]),
        "checks": summary,
    }
