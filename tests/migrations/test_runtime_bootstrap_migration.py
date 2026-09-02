from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

from app import models  # noqa: F401
from app.db_base import Base

ROOT = Path(__file__).resolve().parents[2]
PREVIOUS = "d4a8f2c1b7e9"
HEAD = "e8f1a2b3c4d5"


def _env(database: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("MS_TEST_MODE", None)
    env["MS_DATABASE_URL"] = f"sqlite:///{database}"
    env["MS_DATA_DIR"] = str(database.parent / "data")
    env["MS_STORAGE_DIR"] = str(database.parent / "storage")
    env["MS_OUTPUT_DIR"] = str(database.parent / "output")
    env["MS_TMP_DIR"] = str(database.parent / "tmp")
    env["MS_SUWAYOMI_ENABLED"] = "false"
    env["MS_SUWAYOMI_AUTO_START"] = "false"
    env["MS_YOUTUBE_BROWSER_ENABLED"] = "false"
    return env


def _upgrade(database: Path, revision: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "upgrade", revision],
        cwd=ROOT,
        env=_env(database),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _revision(database: Path) -> str:
    with sqlite3.connect(database) as connection:
        return connection.execute("select version_num from alembic_version").fetchone()[0]


def test_fresh_alembic_head_matches_orm_tables_and_columns(tmp_path):
    database = tmp_path / "fresh.db"
    _upgrade(database, "head")
    assert _revision(database) == HEAD

    engine = create_engine(f"sqlite:///{database}")
    inspector = inspect(engine)
    actual_tables = set(inspector.get_table_names())
    expected_tables = set(Base.metadata.tables)
    assert expected_tables <= actual_tables

    for table_name in sorted(expected_tables):
        expected = set(Base.metadata.tables[table_name].columns.keys())
        actual = {column["name"] for column in inspector.get_columns(table_name)}
        assert actual == expected, table_name


def test_upgrade_adopts_legacy_runtime_columns_without_duplicates(tmp_path):
    database = tmp_path / "legacy.db"
    _upgrade(database, PREVIOUS)
    engine = create_engine(f"sqlite:///{database}")

    Base.metadata.tables["qc_override_events"].create(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE render_jobs ADD COLUMN render_profile VARCHAR(20) NOT NULL DEFAULT 'auto'"
        )
        connection.exec_driver_sql(
            "ALTER TABLE render_jobs ADD COLUMN lease_token VARCHAR(64) NOT NULL DEFAULT ''"
        )
        connection.exec_driver_sql("ALTER TABLE render_jobs ADD COLUMN lease_until DATETIME")
        connection.exec_driver_sql("ALTER TABLE render_jobs ADD COLUMN heartbeat_at DATETIME")
        connection.exec_driver_sql(
            "ALTER TABLE timeline_scenes ADD COLUMN motion_mode VARCHAR(40) NOT NULL DEFAULT 'hold'"
        )
        connection.exec_driver_sql(
            "ALTER TABLE timeline_scenes ADD COLUMN motion_reason TEXT NOT NULL DEFAULT ''"
        )
    engine.dispose()

    _upgrade(database, "head")
    assert _revision(database) == HEAD

    engine = create_engine(f"sqlite:///{database}")
    inspector = inspect(engine)
    render = [column["name"] for column in inspector.get_columns("render_jobs")]
    scenes = [column["name"] for column in inspector.get_columns("timeline_scenes")]
    assert render.count("render_profile") == 1
    assert render.count("lease_token") == 1
    assert scenes.count("motion_mode") == 1
    assert scenes.count("motion_reason") == 1
