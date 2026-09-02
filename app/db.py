"""Database engine, session management, and declarative base."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db_base import Base


def _make_engine() -> Engine:
    url = settings.database_url
    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        # FastAPI may touch a session from a worker thread.
        connect_args["check_same_thread"] = False
    return create_engine(url, connect_args=connect_args, future=True, pool_pre_ping=True)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@event.listens_for(engine, "connect")
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


def _upgrade_runtime_schema() -> None:
    """Bring a normal runtime database to the checked-in Alembic head.

    Fresh installations and upgrades use the same path, so a database can
    never be created without an ``alembic_version`` lineage row. Tests keep
    using ``create_all`` for speed and isolation.
    """
    from alembic.config import Config

    from alembic import command

    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "head")


def init_db() -> None:
    """Initialize the database safely for tests or a real runtime."""
    from app import models  # noqa: F401  (register mappers)

    settings.ensure_dirs()
    if os.environ.get("MS_TEST_MODE") == "1":
        Base.metadata.create_all(bind=engine)
        return
    _upgrade_runtime_schema()


def safe_drop_all(metadata, bind: Engine) -> None:
    """Allow destructive schema resets only inside an explicit test DB."""
    if os.environ.get("MS_TEST_MODE") != "1":
        raise RuntimeError("drop_all is disabled outside MS_TEST_MODE=1")
    url = str(bind.url)
    # SQLAlchemy preserves Windows drive separators in ``sqlite:///`` URLs;
    # normalize only for this guard so the same test-only protection works on
    # Windows and POSIX without broadening which database may be dropped.
    normalized_url = url.replace("\\", "/")
    if bind.url.get_backend_name() != "sqlite" or "/data/test_runs/" not in normalized_url:
        raise RuntimeError(f"refusing destructive reset of non-test database: {url}")
    metadata.drop_all(bind=bind)
