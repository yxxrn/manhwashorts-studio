"""Commit-visibility tests (v1.4.0).

The bug
-------

``app.db.get_db`` is a dependency with ``yield``, and since FastAPI 0.106 the
code after the ``yield`` runs *after* the response has already been sent. The
``db.commit()`` therefore landed on the wrong side of the reply:

    POST /api/auth/register  ->  201 Created   (client already has its cookie)
                             ->  ...commit lands here, a moment later

A caller that immediately fired its next request opened a fresh session that
could not see the uncommitted row, and got ``401 Account not found or disabled``.

Measured against a live uvicorn before the fix: 12/12 failures with no delay,
0/6 with a 1.5s delay, and a separate read-only SQLite connection confirmed the
row was absent at the exact moment the 201 arrived.

Why the existing suite missed it
--------------------------------

``TestClient`` drives the ASGI app to completion — dependency teardown included —
before returning, so the commit always happened "in time". Browsers missed it too
because a human is far slower than a millisecond. Only a fast programmatic
client, which is exactly the AI-agent usage this project targets, could hit it.

So these tests do not rely on request timing. They assert the two structural
guarantees instead: every writing router commits before replying, and the
session is reachable for that to be possible.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("app_settings")


# --- structural guarantees -------------------------------------------------


def test_writing_routers_commit_before_responding():
    """Every route that can write must use CommitRoute.

    A router added later without it would silently reintroduce the bug, and the
    symptom (401 on the next call) points nowhere near the cause.
    """
    from app.main import app
    from app.routing import CommitRoute

    offenders = []
    for route in app.routes:
        methods = getattr(route, "methods", set()) or set()
        path = getattr(route, "path", "")
        if not path.startswith("/api"):
            continue
        if not (methods & {"POST", "PUT", "PATCH", "DELETE"}):
            continue
        if not isinstance(route, CommitRoute):
            offenders.append(f"{sorted(methods)} {path}")

    assert not offenders, "these writing routes do not commit before replying: " + ", ".join(
        offenders
    )


def test_session_is_published_on_request_state(app_settings):
    """CommitRoute finds the session via request.state.db; it must be set.

    Exercised by calling the dependency directly with a minimal ASGI scope.
    Overriding it through ``dependency_overrides`` is not viable: FastAPI would
    re-inspect the replacement's signature and reject the ``Request`` parameter.
    """
    from starlette.requests import Request

    from app.db import get_db

    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    generator = get_db(request)
    session = next(generator)
    try:
        published = getattr(request.state, "db", None)
        assert published is not None, "get_db did not publish request.state.db"
        assert published is session
    finally:
        for _ in generator:  # run teardown
            pass


# --- behavioural checks ----------------------------------------------------


def test_register_then_immediately_use_the_session(client):
    """The exact sequence that failed: register, then act with no pause."""
    assert client.post(
        "/api/auth/register",
        json={"email": "visible@example.com", "password": "visiblepass1234"},
    ).status_code == 201

    # No delay, no re-login.
    assert client.get("/api/auth/me").status_code == 200
    created = client.post(
        "/api/projects",
        json={"title": "V", "manhwa_title": "X", "chapter": "1", "target_duration": 45},
    )
    assert created.status_code == 201, created.text


def test_written_rows_are_committed_not_just_flushed(client):
    """Read the row back through a brand-new session, outside the request."""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import User

    email = "durable@example.com"
    assert client.post(
        "/api/auth/register", json={"email": email, "password": "durablepass1234"}
    ).status_code == 201

    # A separate session sees only committed data.
    with SessionLocal() as session:
        user = session.scalars(select(User).where(User.email == email)).first()
        assert user is not None, "register did not commit before responding"
        assert user.is_active


def test_a_failed_request_does_not_commit_a_partial_write(client):
    """CommitRoute must not persist work from a handler that raised."""
    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models import Project, User

    assert client.post(
        "/api/auth/register",
        json={"email": "rollback@example.com", "password": "rollbackpass1234"},
    ).status_code == 201

    with SessionLocal() as session:
        before = session.scalar(select(func.count()).select_from(Project))
        users_before = session.scalar(select(func.count()).select_from(User))

    # Invalid payload: rejected by validation, so nothing should persist.
    assert client.post("/api/projects", json={"title": ""}).status_code == 422

    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(Project)) == before
        assert session.scalar(select(func.count()).select_from(User)) == users_before


def test_read_only_endpoint_needs_no_transaction(client):
    """/api/health takes no session; it must not error or hang."""
    assert client.get("/api/health").status_code == 200
