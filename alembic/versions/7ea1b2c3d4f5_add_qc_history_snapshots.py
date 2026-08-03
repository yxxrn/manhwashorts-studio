"""persist append-only quality report snapshots

Revision ID: 7ea1b2c3d4f5
Revises: 6d9f0a3b2c4e
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7ea1b2c3d4f5"
down_revision: str | None = "6d9f0a3b2c4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "qc_history_snapshots",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("render_job_id", sa.String(length=32), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("report", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["render_job_id"], ["render_jobs.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_qc_history_snapshots_project_id", "qc_history_snapshots", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_qc_history_snapshots_project_id", table_name="qc_history_snapshots")
    op.drop_table("qc_history_snapshots")
