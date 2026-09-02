"""Browser-first YouTube publish routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.deps import CurrentUser, CurrentWorkspace, DbSession, OwnedProject
from app.models import Publication, VideoStat
from app.routing import CommitRoute
from app.schemas import (
    MetadataOut,
    PublicationOut,
    PublishRequest,
    StatOut,
    YouTubeBrowserAccountCreate,
    YouTubeBrowserAccountUpdate,
)
from app.services import publish as publish_svc
from app.services.pipeline import PipelineError

router = APIRouter(prefix="/api", tags=["publish"], route_class=CommitRoute)


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PipelineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/youtube/browser/status")
def youtube_browser_status(workspace: CurrentWorkspace, account_id: str | None = None) -> dict:
    del workspace
    return publish_svc.browser_status(account_id)


@router.get("/youtube/browser/accounts")
def youtube_browser_accounts(workspace: CurrentWorkspace) -> dict:
    del workspace
    return publish_svc.browser_accounts()


@router.post("/youtube/browser/accounts")
def create_youtube_browser_account(
    payload: YouTubeBrowserAccountCreate, workspace: CurrentWorkspace
) -> dict:
    del workspace
    return _guard(
        publish_svc.create_browser_account,
        account_id=payload.account_id,
        label=payload.label,
        trust_channel_defaults=payload.trust_channel_defaults,
    )


@router.patch("/youtube/browser/accounts/{account_id}")
def update_youtube_browser_account(
    account_id: str, payload: YouTubeBrowserAccountUpdate, workspace: CurrentWorkspace
) -> dict:
    del workspace
    return _guard(
        publish_svc.update_browser_account,
        account_id,
        label=payload.label,
        make_default=payload.make_default,
        trust_channel_defaults=payload.trust_channel_defaults,
        change_trust_channel_defaults="trust_channel_defaults" in payload.model_fields_set,
    )


@router.get("/projects/{project_id}/metadata", response_model=MetadataOut)
def suggest_metadata(project: OwnedProject, db: DbSession) -> dict:
    return publish_svc.build_metadata_for(db, project.id)


@router.get("/projects/{project_id}/publish/readiness")
def publish_readiness(
    project: OwnedProject, db: DbSession, youtube_account_id: str | None = None
) -> dict:
    return publish_svc.can_publish(db, project.id, youtube_account_id=youtube_account_id)


@router.post("/projects/{project_id}/publish", response_model=PublicationOut)
def publish_project(
    payload: PublishRequest,
    project: OwnedProject,
    db: DbSession,
    user: CurrentUser,
) -> Publication:
    return _guard(
        publish_svc.publish,
        db,
        project.id,
        channel_id=payload.channel_id,
        youtube_account_id=payload.youtube_account_id,
        video_title=payload.video_title,
        description=payload.description,
        tags=payload.tags,
        privacy_status=payload.privacy_status,
        scheduled_at=payload.scheduled_at,
        confirm_public=payload.confirm_public,
        trust_channel_defaults=payload.trust_channel_defaults,
        actor_id=user.id,
    )


@router.get("/projects/{project_id}/publications", response_model=list[PublicationOut])
def list_publications(project: OwnedProject, db: DbSession) -> list[Publication]:
    return publish_svc.project_publications(db, project.id)


@router.post("/publications/{publication_id}/retry", response_model=PublicationOut)
def retry_publication(
    publication_id: str,
    db: DbSession,
    workspace: CurrentWorkspace,
    user: CurrentUser,
) -> Publication:
    publication = db.get(Publication, publication_id)
    if publication is None or publication.project is None or publication.project.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Publication not found.")
    return _guard(publish_svc.retry_publish, db, publication_id, user.id)


@router.post("/publications/{publication_id}/thumbnail/retry", response_model=PublicationOut)
def retry_publication_thumbnail(
    publication_id: str,
    db: DbSession,
    workspace: CurrentWorkspace,
    user: CurrentUser,
) -> Publication:
    publication = db.get(Publication, publication_id)
    if publication is None or publication.project is None or publication.project.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Publication not found.")
    return _guard(publish_svc.retry_thumbnail, db, publication_id, user.id)


@router.post("/publications/{publication_id}/stats/sync")
def sync_stats(
    publication_id: str,
    db: DbSession,
    workspace: CurrentWorkspace,
    user: CurrentUser,
) -> dict:
    publication = db.get(Publication, publication_id)
    if publication is None or publication.project is None or publication.project.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Publication not found.")
    _guard(publish_svc.sync_stats, db, publication_id, user.id)
    return {
        "available": False,
        "detail": "YouTube analytics API is archived; browser publishing does not fetch analytics.",
    }


@router.get("/publications/{publication_id}/stats", response_model=list[StatOut])
def list_stats(
    publication_id: str,
    db: DbSession,
    workspace: CurrentWorkspace,
) -> list[VideoStat]:
    publication = db.get(Publication, publication_id)
    if publication is None or publication.project is None or publication.project.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Publication not found.")
    return list(
        db.scalars(
            select(VideoStat)
            .where(VideoStat.publication_id == publication_id)
            .order_by(VideoStat.synced_at.desc())
        )
    )
