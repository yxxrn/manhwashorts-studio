"""Adopt runtime bootstrap schema into Alembic lineage.

Revision ID: e8f1a2b3c4d5
Revises: d4a8f2c1b7e9
"""

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "e8f1a2b3c4d5"
down_revision = "d4a8f2c1b7e9"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "qc_override_events" not in tables:
        op.create_table(
            "qc_override_events",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("project_id", sa.String(length=32), nullable=False),
            sa.Column("quality_code", sa.String(length=80), nullable=False),
            sa.Column("actor_id", sa.String(length=32), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("before_passed", sa.Boolean(), nullable=False),
            sa.Column("after_passed", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_qc_override_events_project_id",
            "qc_override_events",
            ["project_id"],
            unique=False,
        )

    render_columns = _columns("render_jobs")
    with op.batch_alter_table("render_jobs") as batch:
        if "render_profile" not in render_columns:
            batch.add_column(
                sa.Column(
                    "render_profile",
                    sa.String(length=20),
                    nullable=False,
                    server_default="auto",
                )
            )
        if "lease_token" not in render_columns:
            batch.add_column(
                sa.Column(
                    "lease_token",
                    sa.String(length=64),
                    nullable=False,
                    server_default="",
                )
            )
        if "lease_until" not in render_columns:
            batch.add_column(sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
        if "heartbeat_at" not in render_columns:
            batch.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))

    scene_columns = _columns("timeline_scenes")
    with op.batch_alter_table("timeline_scenes") as batch:
        if "motion_mode" not in scene_columns:
            batch.add_column(
                sa.Column(
                    "motion_mode",
                    sa.String(length=40),
                    nullable=False,
                    server_default="hold",
                )
            )
        if "motion_reason" not in scene_columns:
            batch.add_column(
                sa.Column(
                    "motion_reason",
                    sa.Text(),
                    nullable=False,
                    server_default="",
                )
            )


def downgrade() -> None:
    scene_columns = _columns("timeline_scenes")
    with op.batch_alter_table("timeline_scenes") as batch:
        if "motion_reason" in scene_columns:
            batch.drop_column("motion_reason")
        if "motion_mode" in scene_columns:
            batch.drop_column("motion_mode")

    render_columns = _columns("render_jobs")
    with op.batch_alter_table("render_jobs") as batch:
        for name in ("heartbeat_at", "lease_until", "lease_token", "render_profile"):
            if name in render_columns:
                batch.drop_column(name)

    if "qc_override_events" in set(inspect(op.get_bind()).get_table_names()):
        op.drop_index("ix_qc_override_events_project_id", table_name="qc_override_events")
        op.drop_table("qc_override_events")
