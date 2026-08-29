"""Database engine, session management, and declarative base."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

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


def init_db() -> None:
    """Create all tables. Alembic owns migrations; this is for local bootstrap."""
    from app import models  # noqa: F401  (register mappers)

    settings.ensure_dirs()
    Base.metadata.create_all(bind=engine)
    if engine.url.get_backend_name() == "sqlite":
        with engine.begin() as connection:
            columns = {
                row[1] for row in connection.exec_driver_sql("PRAGMA table_info(timeline_scenes)")
            }
            if "motion_mode" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE timeline_scenes ADD COLUMN motion_mode VARCHAR(40) NOT NULL DEFAULT 'hold'"
                )
            if "motion_reason" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE timeline_scenes ADD COLUMN motion_reason TEXT NOT NULL DEFAULT ''"
                )
            if "source_family" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE timeline_scenes ADD COLUMN source_family VARCHAR(255) NOT NULL DEFAULT ''"
                )
            asset_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(source_assets)")}
            for name, definition in {
                "source_family": "VARCHAR(255) NOT NULL DEFAULT ''",
                "source_family_manual": "BOOLEAN NOT NULL DEFAULT 0",
            }.items():
                if name not in asset_columns:
                    connection.exec_driver_sql(f"ALTER TABLE source_assets ADD COLUMN {name} {definition}")
            scene_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(timeline_scenes)")}
            for name, definition in {
                "alignment_score": "FLOAT NOT NULL DEFAULT 0",
                "alignment_reasons": "JSON NOT NULL DEFAULT '[]'",
                "rejected_candidates": "JSON NOT NULL DEFAULT '[]'",
                "visual_signature": "VARCHAR(128) NOT NULL DEFAULT ''",
                "panel_region_id": "VARCHAR(32)",
                "panel_id": "VARCHAR(80) NOT NULL DEFAULT ''",
                "panel_bounds_json": "JSON NOT NULL DEFAULT '{}'",
                "visual_evidence_json": "JSON NOT NULL DEFAULT '{}'",
                "source_asset_checksum": "VARCHAR(64) NOT NULL DEFAULT ''",
            }.items():
                if name not in scene_columns:
                    connection.exec_driver_sql(f"ALTER TABLE timeline_scenes ADD COLUMN {name} {definition}")

            asset_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(source_assets)")}
            for name, definition in {
                "panel_bbox": "JSON NOT NULL DEFAULT '{}'",
                "panel_quality": "JSON NOT NULL DEFAULT '{}'",
                "panel_decision": "VARCHAR(20) NOT NULL DEFAULT 'accept'",
            }.items():
                if name not in asset_columns:
                    connection.exec_driver_sql(f"ALTER TABLE source_assets ADD COLUMN {name} {definition}")

            script_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(script_versions)")}
            if "editorial_metadata" not in script_columns:
                connection.exec_driver_sql("ALTER TABLE script_versions ADD COLUMN editorial_metadata JSON NOT NULL DEFAULT '{}'")

            audio_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(audio_segments)")}
            for name, definition in {
                "spoken_text": "TEXT NOT NULL DEFAULT ''",
                "display_text": "TEXT NOT NULL DEFAULT ''",
                "voice_profile_hash": "VARCHAR(64) NOT NULL DEFAULT ''",
                "voice_profile": "JSON NOT NULL DEFAULT '{}'",
            }.items():
                if name not in audio_columns:
                    connection.exec_driver_sql(f"ALTER TABLE audio_segments ADD COLUMN {name} {definition}")

            job_columns = {
                row[1] for row in connection.exec_driver_sql("PRAGMA table_info(render_jobs)")
            }
            for name, definition in {
                "lease_token": "VARCHAR(64) NOT NULL DEFAULT ''",
                "lease_until": "DATETIME",
                "heartbeat_at": "DATETIME",
                "render_profile": "VARCHAR(20) NOT NULL DEFAULT 'auto'",
            }.items():
                if name not in job_columns:
                    connection.exec_driver_sql(
                        f"ALTER TABLE render_jobs ADD COLUMN {name} {definition}"
                    )


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
