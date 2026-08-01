"""Database engine, session management, and declarative base."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _make_engine() -> Engine:
    url = settings.database_url
    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        # FastAPI may touch a session from a worker thread.
        connect_args["check_same_thread"] = False
    return create_engine(url, connect_args=connect_args, future=True, pool_pre_ping=True)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record) -> None:  # pragma: no cover - driver hook
    """Enable foreign keys and WAL so concurrent worker reads behave."""
    if engine.url.get_backend_name() != "sqlite":
        return
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def get_db(request: Request) -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session.

    The session is published on ``request.state.db`` so ``app.routing.CommitRoute``
    can commit it *before* the response reaches the client. Code after the
    ``yield`` in a dependency runs after the response has already been sent, so
    committing only here let a fast caller issue its next request against a
    transaction that had not landed yet — a register/login would return 201 and
    the very next call 401. See ``app/routing.py`` for the measurements.

    The commit below is kept as a fallback for anything not served through
    ``CommitRoute`` (and is a no-op when the route already committed), so no
    caller can silently lose a write.
    """
    db = SessionLocal()
    request.state.db = db
    try:
        yield db
        if db.in_transaction():
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context manager for background workers and scripts."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """Create all tables. Alembic owns migrations; this is for local bootstrap."""
    from app import models  # noqa: F401  (register mappers)

    settings.ensure_dirs()
    Base.metadata.create_all(bind=engine)
