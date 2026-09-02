"""Browser-first YouTube publication flow.

The legacy YouTube Data API implementation lives under ``archive/youtube_api``.
Runtime publishing uses YouTube Studio through a persistent Playwright/Chrome
profile. The database still records Publication rows for idempotency, audit, and
agent status, but no OAuth tokens or YouTube API channel objects are required.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import PrivacyStatus, ProjectStatus, UploadStatus
from app.models import Publication, RenderJob, VideoStat
from app.security import new_idempotency_key
from app.services import policy
from app.services import quality as quality_svc
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
from app.services.youtube_browser import BrowserPublishError, YouTubeStudioBrowserPublisher
from app.services.youtube_metadata import build_metadata


def _now() -> datetime:
    return datetime.now(UTC)


def verify_artifact(job: RenderJob) -> Path:
    if not job.output_key:
        raise PipelineError("the render job has no output file")
    path = Path(job.output_key)
    if not path.is_file():
        raise PipelineError("the rendered video is missing from disk. Re-render before publishing.")
    if job.checksum and sha256_file(path) != job.checksum:
        raise PipelineError(
            "the rendered file does not match its checksum from render time. Re-render to be safe."
        )
    return path


def _thumbnail_path(job: RenderJob) -> Path | None:
    if not job.output_key:
        return None
    parent = Path(job.output_key).parent
    for name in ("thumbnail.jpg", "final.jpg"):
        path = parent / name
        if path.is_file():
            return path
    return None


def build_metadata_for(db: Session, project_id: str) -> dict:
    project = get_project(db, project_id)
    from app.services.pipeline import current_script

    script = current_script(db, project_id)
    assets = project_assets(db, project_id)
    attributions = sorted({a.attribution.strip() for a in assets if a.attribution.strip()})
    return build_metadata(
        project_title=project.title,
        manhwa_title=project.manhwa_title,
        chapter=project.chapter,
        script_text=script.plain_text if script else "",
        attribution="; ".join(attributions),
    )


def browser_status() -> dict:
    status = YouTubeStudioBrowserPublisher().session_status()
    return {
        "publisher": "youtube_studio_browser",
        "available": status.available,
        "authenticated": status.authenticated,
        "profile_dir": status.profile_dir,
        "browser": status.browser,
        "action_required": status.action_required,
        "detail": status.detail,
    }


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
    """Publish the latest successful render through YouTube Studio browser UI."""
    del channel_id  # legacy API argument kept temporarily for agent compatibility
    project = get_project(db, project_id)

    findings = policy.check_public_publish(privacy_status)
    if findings:
        raise PipelineError(findings[0].message)
    if privacy_status == PrivacyStatus.PUBLIC and not confirm_public:
        raise PipelineError(
            "Publishing publicly needs explicit confirmation. Set confirm_public=true after review."
        )

    job = successful_render(db, project_id)
    if job is None:
        raise PipelineError("no successful final render found. Render the video before publishing.")
    video_path = verify_artifact(job)

    results = run_quality_checks(db, project_id, job=job, actor_id=actor_id)
    blocking = [result for result in results if result.blocking]
    if blocking:
        raise PipelineError(
            "Quality checks must pass before publishing: "
            + "; ".join(result.message for result in blocking[:3])
        )

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
    needs_defaults = not (video_title or saved_title) or not (description or saved_description) or not (
        tags or saved_tags
    )
    defaults = (
        build_metadata_for(db, project_id)
        if needs_defaults
        else {"title": "", "description": "", "tags": []}
    )
    final_title = (video_title or saved_title or defaults["title"])[:100]
    final_description = (description or saved_description or defaults["description"])[:5000]
    final_tags = (tags if tags else saved_tags or defaults["tags"])[:20]

    if publication is None:
        publication = Publication(
            project_id=project_id,
            render_job_id=job.id,
            channel_id=None,
            idempotency_key=new_idempotency_key(),
        )
        db.add(publication)

    publication.render_job_id = job.id
    publication.channel_id = None
    publication.video_title = final_title
    publication.description = final_description
    publication.tags = final_tags
    publication.privacy_status = privacy_status
    publication.scheduled_at = scheduled_at
    publication.upload_status = UploadStatus.UPLOADING
    publication.attempt = (publication.attempt or 0) + 1
    publication.error_message = ""
    db.flush()
    db.commit()

    publisher = YouTubeStudioBrowserPublisher()
    try:
        result = publisher.publish(
            video_path=video_path,
            title=final_title,
            description=final_description,
            tags=final_tags,
            thumbnail_path=_thumbnail_path(job),
            privacy_status=str(privacy_status),
            scheduled_at=scheduled_at,
        )
    except BrowserPublishError as exc:
        publication.upload_status = UploadStatus.FAILED
        detail = f"{exc.code}: {exc}"
        if exc.action_required:
            detail += f" [action_required={exc.action_required}]"
        publication.error_message = detail[:1000]
        project.status = ProjectStatus.FAILED
        project.error_message = str(exc)[:1000]
        audit(
            db,
            "publish.browser_failed",
            "publication",
            publication.id,
            actor_id,
            code=exc.code,
            retryable=exc.retryable,
            action_required=exc.action_required,
        )
        db.flush()
        db.commit()
        raise PipelineError(str(exc)) from exc

    publication.youtube_video_id = result.video_id
    publication.upload_status = UploadStatus.UPLOADED
    publication.privacy_status = result.privacy_status
    publication.thumbnail_attempt = 1 if _thumbnail_path(job) else 0
    publication.thumbnail_status = result.thumbnail_status
    publication.thumbnail_error = "" if result.thumbnail_status == "uploaded" else "browser_ui"
    publication.published_at = _now()
    project.status = ProjectStatus.PUBLISHED
    project.error_message = ""
    audit(
        db,
        "publish.browser_succeeded",
        "publication",
        publication.id,
        actor_id,
        provider=result.provider,
        video_id=result.video_id,
        privacy=result.privacy_status,
        stages=result.stages,
        thumbnail_status=result.thumbnail_status,
    )
    db.flush()
    db.commit()
    return publication


def retry_publish(db: Session, publication_id: str, actor_id: str = "") -> Publication:
    publication = db.get(Publication, publication_id)
    if publication is None:
        raise PipelineError("publication not found")
    if publication.upload_status == UploadStatus.UPLOADED:
        raise PipelineError("this video was already uploaded")
    return publish(
        db,
        publication.project_id,
        video_title=publication.video_title,
        description=publication.description,
        tags=list(publication.tags or []),
        privacy_status=publication.privacy_status,
        scheduled_at=publication.scheduled_at,
        confirm_public=publication.privacy_status == PrivacyStatus.PUBLIC,
        actor_id=actor_id,
    )


def retry_thumbnail(db: Session, publication_id: str, actor_id: str = "") -> Publication:
    del actor_id
    publication = db.get(Publication, publication_id)
    if publication is None:
        raise PipelineError("publication not found")
    raise PipelineError(
        "browser publishing sets the thumbnail inside the upload wizard; standalone thumbnail retry "
        "is archived with the YouTube API publisher"
    )


def sync_stats(db: Session, publication_id: str, actor_id: str = "") -> VideoStat | None:
    publication = db.get(Publication, publication_id)
    if publication is None:
        raise PipelineError("publication not found")
    audit(
        db,
        "stats.unavailable",
        "publication",
        publication.id,
        actor_id,
        source="youtube_studio_browser",
    )
    db.flush()
    return None


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
    job = successful_render(db, project_id)
    if job is None:
        return {"ready": False, "reason": "no successful final render yet", "checks": None}
    results = evaluate_quality_checks(db, project_id, job=job)
    summary = quality_svc.summarise(results)
    browser = browser_status()
    ready = bool(summary["can_publish"] and browser["available"] and browser["authenticated"])
    reasons: list[str] = []
    if not summary["can_publish"]:
        reasons.extend(summary["error_codes"])
    if not browser["available"]:
        reasons.append("youtube.browser_unavailable")
    elif not browser["authenticated"]:
        reasons.append("youtube.reauthentication_required")
    return {
        "ready": ready,
        "reason": "; ".join(reasons),
        "checks": summary,
        "publisher": browser,
    }
