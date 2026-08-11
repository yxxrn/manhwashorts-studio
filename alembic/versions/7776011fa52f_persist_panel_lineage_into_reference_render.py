"""Persist panel lineage snapshots for reference rendering."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7776011fa52f"
down_revision: str | None = "b7c4d8e91f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("timeline_scenes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("panel_region_id", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column("panel_id", sa.String(length=80), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("panel_bounds_json", sa.JSON(), nullable=False, server_default="{}")
        )
        batch_op.add_column(
            sa.Column("visual_evidence_json", sa.JSON(), nullable=False, server_default="{}")
        )
        batch_op.add_column(
            sa.Column(
                "source_asset_checksum",
                sa.String(length=64),
                nullable=False,
                server_default="",
            )
        )
        batch_op.create_index(
            "ix_timeline_scenes_panel_region_id",
            ["panel_region_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("timeline_scenes", schema=None) as batch_op:
        batch_op.drop_index("ix_timeline_scenes_panel_region_id")
        batch_op.drop_column("source_asset_checksum")
        batch_op.drop_column("visual_evidence_json")
        batch_op.drop_column("panel_bounds_json")
        batch_op.drop_column("panel_id")
        batch_op.drop_column("panel_region_id")
