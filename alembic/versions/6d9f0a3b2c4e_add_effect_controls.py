"""persist motion intensity and disabled local effects

Revision ID: 6d9f0a3b2c4e
Revises: 5c8e7f2a1b3d
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "6d9f0a3b2c4e"
down_revision: str | None = "5c8e7f2a1b3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("timeline_scenes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("motion_intensity", sa.String(length=20), nullable=False, server_default="low"))
        batch_op.add_column(sa.Column("disabled_effects", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("timeline_scenes", schema=None) as batch_op:
        batch_op.drop_column("disabled_effects")
        batch_op.drop_column("motion_intensity")
