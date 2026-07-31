"""Authentication routes: register, login, logout, whoami."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from app.config import settings
from app.deps import CurrentUser, CurrentWorkspace, DbSession, issue_session
from app.models import User, Workspace
from app.schemas import (
    LoginRequest,
    MessageOut,
    RegisterRequest,
    UserOut,
    WorkspaceOut,
)
from app.security import hash_password, verify_password
from app.services.pipeline import audit

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_cookie(response: Response, user_id: str) -> None:
    response.set_cookie(
        key=settings.session_cookie,
        value=issue_session(user_id),
        max_age=settings.session_max_age,
        httponly=True,
        samesite="lax",
        # Secure requires HTTPS; on a local HTTP install it would break login.
        secure=settings.environment == "production",
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: DbSession, response: Response) -> User:
    """Create an account plus its default workspace."""
    email = payload.email.lower().strip()
    existing = db.scalars(select(User).where(User.email == email)).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        )

    user = User(
        email=email,
        name=payload.name.strip() or email.split("@")[0],
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.flush()
    db.add(Workspace(owner_id=user.id, name=f"{user.name}'s Workspace"))
    audit(db, "auth.register", "user", user.id, user.id)
    db.flush()

    _set_cookie(response, user.id)
    return user


@router.post("/login", response_model=UserOut)
def login(payload: LoginRequest, db: DbSession, response: Response) -> User:
    email = payload.email.lower().strip()
    user = db.scalars(select(User).where(User.email == email)).first()
    # Same message for unknown email and wrong password: do not leak which
    # accounts exist.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is disabled.",
        )
    audit(db, "auth.login", "user", user.id, user.id)
    _set_cookie(response, user.id)
    return user


@router.post("/logout", response_model=MessageOut)
def logout(response: Response) -> dict:
    response.delete_cookie(settings.session_cookie)
    return {"detail": "Logged out."}


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user


@router.get("/workspace", response_model=WorkspaceOut)
def workspace(workspace: CurrentWorkspace) -> Workspace:
    return workspace
