"""Track comic medium independently from production content type.

Revision ID: b1e4f7a2c9d0
Revises: f3c9a2d4e6b8
"""

import sqlalchemy as sa

from alembic import op

revision = "b1e4f7a2c9d0"
down_revision = "f3c9a2d4e6b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(
            sa.Column("comic_type", sa.String(length=20), nullable=False, server_default="comic")
        )


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("comic_type")
