"""Shared FastAPI dependencies: session auth and ownership checks.

Authentication uses a signed session cookie (itsdangerous), which keeps the
local deployment free of an external identity provider while still refusing
tampered tokens.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Project, User, Workspace

_SALT = "manhwashorts-session"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.resolve_secret_key(), salt=_SALT)


def issue_session(user_id: str) -> str:
    """Create a signed session token for a user id."""
    return _serializer().dumps({"uid": user_id})


def read_session(token: str) -> str | None:
    """Return the user id from a session token, or None if invalid/expired."""
    try:
        data = _serializer().loads(token, max_age=settings.session_max_age)
    except (BadSignature, SignatureExpired, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    uid = data.get("uid")
    return uid if isinstance(uid, str) else None


DbSession = Annotated[Session, Depends(get_db)]


def current_user(
    db: DbSession,
    ms_session: Annotated[str | None, Cookie()] = None,
) -> User:
    """Resolve the logged-in user, or raise 401."""
    if not ms_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Log in first.",
        )
    uid = read_session(ms_session)
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session is invalid or expired. Log in again.",
        )
    user = db.get(User, uid)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account not found or disabled.",
        )
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def current_workspace(db: DbSession, user: CurrentUser) -> Workspace:
    """Return the user's workspace, creating a default one on first use."""
    workspace = db.scalars(
        select(Workspace).where(Workspace.owner_id == user.id).order_by(Workspace.created_at)
    ).first()
    if workspace is None:
        workspace = Workspace(owner_id=user.id, name="My Workspace")
        db.add(workspace)
        db.flush()
    return workspace


CurrentWorkspace = Annotated[Workspace, Depends(current_workspace)]


def owned_project(
    project_id: str,
    db: DbSession,
    workspace: CurrentWorkspace,
) -> Project:
    """Fetch a project, enforcing that it belongs to the caller's workspace.

    Returning 404 rather than 403 for a foreign project avoids confirming that
    the id exists at all.
    """
    project = db.get(Project, project_id)
    if project is None or project.workspace_id != workspace.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found.",
        )
    return project


OwnedProject = Annotated[Project, Depends(owned_project)]
