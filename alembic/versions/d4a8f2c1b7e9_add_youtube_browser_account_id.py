"""add YouTube browser account id to publications

Revision ID: d4a8f2c1b7e9
Revises: 9c13d7a4e2b1
"""

import sqlalchemy as sa

from alembic import op

revision = "d4a8f2c1b7e9"
down_revision = "9c13d7a4e2b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "publications",
        sa.Column("youtube_account_id", sa.String(length=32), nullable=False, server_default="default"),
    )


def downgrade() -> None:
    op.drop_column("publications", "youtube_account_id")
