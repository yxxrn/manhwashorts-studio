"""Publish, channel connection, and analytics routes (PRD FR-10 to FR-12)."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.deps import CurrentUser, CurrentWorkspace, DbSession, OwnedProject
from app.models import Publication, VideoStat, YouTubeChannel
from app.schemas import (
    ChannelOut,
    MessageOut,
    MetadataOut,
    PublicationOut,
    PublishRequest,
    StatOut,
)
from app.security import encrypt_json
from app.services import publish as publish_svc
from app.services import youtube as yt
from app.services.pipeline import PipelineError, audit

router = APIRouter(prefix="/api", tags=["publish"])

# Short-lived OAuth state values, keyed by state -> workspace id. In a
# multi-process deployment this belongs in Redis; for the local single-process
# app an in-memory dict is adequate and avoids persisting CSRF tokens.
_oauth_state: dict[str, str] = {}


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PipelineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# --- channels --------------------------------------------------------------


@router.get("/youtube/channels", response_model=list[ChannelOut])
def list_channels(db: DbSession, workspace: CurrentWorkspace) -> list[YouTubeChannel]:
    return publish_svc.workspace_channels(db, workspace.id)


@router.get("/youtube/connect")
def connect_channel(workspace: CurrentWorkspace, user: CurrentUser):
    """Start the OAuth consent flow (FR-10)."""
    if not yt.oauth_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "YouTube OAuth is not configured. Set MS_YOUTUBE_ENABLED=true, "
                "MS_YOUTUBE_CLIENT_ID, and MS_YOUTUBE_CLIENT_SECRET, then restart. "
                "Without it the app runs in dry-run mode and uploads nothing."
            ),
        )
    state = secrets.token_urlsafe(24)
    _oauth_state[state] = workspace.id
    flow = yt.build_flow(state=state)
    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return {"authorization_url": url, "state": state}


@router.get("/youtube/callback")
def oauth_callback(
    db: DbSession,
    request: Request,
    state: str = Query(...),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    """OAuth redirect target. Exchanges the code and stores tokens encrypted."""
    workspace_id = _oauth_state.pop(state, None)
    if workspace_id is None:
        # Unknown state means CSRF or an expired attempt.
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state.")
    if error:
        raise HTTPException(status_code=400, detail=f"Authorisation was denied: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="No authorisation code returned.")

    flow = yt.build_flow(state=state)
    try:
        flow.fetch_token(code=code)
        credentials = yt.credentials_to_dict(flow.credentials)
        info = yt.fetch_channel_info(credentials)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Could not complete OAuth: {type(exc).__name__}"
        ) from exc

    existing = db.scalars(
        select(YouTubeChannel).where(
            YouTubeChannel.workspace_id == workspace_id,
            YouTubeChannel.channel_id == info.channel_id,
        )
    ).first()
    channel = existing or YouTubeChannel(
        workspace_id=workspace_id, channel_id=info.channel_id
    )
    channel.channel_title = info.title
    # Tokens are encrypted at rest and never returned by the API.
    channel.encrypted_credentials = encrypt_json(credentials)
    channel.scopes = info.scopes
    channel.connected_at = datetime.now(UTC)
    channel.revoked = False
    if existing is None:
        db.add(channel)
    audit(db, "youtube.connect", "channel", channel.id, detail_channel=info.channel_id)
    db.flush()
    return RedirectResponse(url="/?connected=1", status_code=303)


@router.delete("/youtube/channels/{channel_id}", response_model=MessageOut)
def disconnect_channel(
    channel_id: str,
    db: DbSession,
    workspace: CurrentWorkspace,
    user: CurrentUser,
) -> dict:
    """Revoke a channel connection and erase its stored tokens (FR-10)."""
    channel = db.get(YouTubeChannel, channel_id)
    if channel is None or channel.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Channel not found.")
    channel.revoked = True
    channel.encrypted_credentials = ""
    audit(db, "youtube.disconnect", "channel", channel.id, user.id)
    db.flush()
    return {"detail": "Channel disconnected and stored credentials erased."}


# --- publish ---------------------------------------------------------------


@router.get("/projects/{project_id}/metadata", response_model=MetadataOut)
def suggest_metadata(project: OwnedProject, db: DbSession) -> dict:
    """Draft title/description/tags from the approved script (FR-10)."""
    return publish_svc.build_metadata_for(db, project.id)


@router.get("/projects/{project_id}/publish/readiness")
def publish_readiness(project: OwnedProject, db: DbSession) -> dict:
    return publish_svc.can_publish(db, project.id)


@router.post("/projects/{project_id}/publish", response_model=PublicationOut)
def publish_project(
    payload: PublishRequest,
    project: OwnedProject,
    db: DbSession,
    user: CurrentUser,
) -> Publication:
    """Upload the rendered Short. Private by default; public is double-gated."""
    return _guard(
        publish_svc.publish,
        db,
        project.id,
        channel_id=payload.channel_id,
        video_title=payload.video_title,
        description=payload.description,
        tags=payload.tags,
        privacy_status=payload.privacy_status,
        scheduled_at=payload.scheduled_at,
        confirm_public=payload.confirm_public,
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
    """Retry a failed upload without re-rendering (FR-10)."""
    publication = db.get(Publication, publication_id)
    if publication is None:
        raise HTTPException(status_code=404, detail="Publication not found.")
    project = publication.project
    if project is None or project.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Publication not found.")
    return _guard(publish_svc.retry_publish, db, publication_id, user.id)


# --- analytics (FR-12) ----------------------------------------------------


@router.post("/publications/{publication_id}/stats/sync")
def sync_stats(
    publication_id: str,
    db: DbSession,
    workspace: CurrentWorkspace,
    user: CurrentUser,
) -> dict:
    publication = db.get(Publication, publication_id)
    if publication is None:
        raise HTTPException(status_code=404, detail="Publication not found.")
    project = publication.project
    if project is None or project.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Publication not found.")

    row = _guard(publish_svc.sync_stats, db, publication_id, user.id)
    if row is None:
        # Honest empty state rather than a row of fabricated zeros.
        return {
            "available": False,
            "detail": (
                "No analytics available yet. In dry-run mode there is no data; "
                "on a live video, retention metrics can take up to 48 hours."
            ),
        }
    return {"available": True, "stats": StatOut.model_validate(row).model_dump()}


@router.get("/publications/{publication_id}/stats", response_model=list[StatOut])
def list_stats(
    publication_id: str,
    db: DbSession,
    workspace: CurrentWorkspace,
) -> list[VideoStat]:
    publication = db.get(Publication, publication_id)
    if publication is None:
        raise HTTPException(status_code=404, detail="Publication not found.")
    project = publication.project
    if project is None or project.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Publication not found.")
    return list(
        db.scalars(
            select(VideoStat)
            .where(VideoStat.publication_id == publication_id)
            .order_by(VideoStat.synced_at.desc())
        )
    )
