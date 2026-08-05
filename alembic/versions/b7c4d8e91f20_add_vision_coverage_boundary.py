"""Persist complete vision coverage lineage and analysis evidence state."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7c4d8e91f20"
down_revision: str | None = "a4p0_editorial_voice_visual_contract"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("story_analyses", schema=None) as batch_op:
        batch_op.add_column(sa.Column("analysis_run_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("state", sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column("provider_type", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("provider_name", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("model_name", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("instruction_version", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("instruction_sha256", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("coverage_manifest_json", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("continuity_ledger_json", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("evidence_graph_json", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("story_spine_json", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("blocking_reasons_json", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("reconciliation_json", sa.JSON(), nullable=True))

    with op.batch_alter_table("source_assets", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "original_checksum",
                sa.String(length=64),
                nullable=False,
                server_default="",
            )
        )
        batch_op.add_column(
            sa.Column("original_width", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("original_height", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column(
                "source_bounds_json",
                sa.JSON(),
                nullable=False,
                server_default="{}",
            )
        )
        batch_op.add_column(
            sa.Column("strip_order", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("region_order", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column(
                "trim_classification",
                sa.String(length=40),
                nullable=False,
                server_default="unknown",
            )
        )
        batch_op.add_column(
            sa.Column(
                "coverage_map_hash",
                sa.String(length=64),
                nullable=False,
                server_default="",
            )
        )

    op.create_table(
        "panel_regions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("story_analysis_id", sa.String(length=32), nullable=False),
        sa.Column("source_asset_id", sa.String(length=32), nullable=False),
        sa.Column("source_asset_checksum", sa.String(length=64), nullable=False),
        sa.Column("original_width", sa.Integer(), nullable=False),
        sa.Column("original_height", sa.Integer(), nullable=False),
        sa.Column("strip_region_id", sa.String(length=80), nullable=False),
        sa.Column("panel_id", sa.String(length=80), nullable=False),
        sa.Column("source_order", sa.Integer(), nullable=False),
        sa.Column("bounds_json", sa.JSON(), nullable=False),
        sa.Column("region_class", sa.String(length=40), nullable=False),
        sa.Column("segmentation_confidence", sa.Float(), nullable=False),
        sa.Column("segmentation_version", sa.String(length=40), nullable=False),
        sa.Column("coverage_map_hash", sa.String(length=64), nullable=False),
        sa.Column("observation_json", sa.JSON(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["story_analysis_id"], ["story_analyses.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["source_asset_id"], ["source_assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_panel_regions_analysis_source_order",
        "panel_regions",
        ["story_analysis_id", "source_order"],
        unique=True,
    )
    op.create_index(
        "ix_panel_regions_source_asset_id",
        "panel_regions",
        ["source_asset_id"],
        unique=False,
    )
    op.create_index(
        "ix_panel_regions_region_class",
        "panel_regions",
        ["region_class"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_panel_regions_region_class", table_name="panel_regions")
    op.drop_index("ix_panel_regions_source_asset_id", table_name="panel_regions")
    op.drop_index("uq_panel_regions_analysis_source_order", table_name="panel_regions")
    op.drop_table("panel_regions")

    with op.batch_alter_table("source_assets", schema=None) as batch_op:
        batch_op.drop_column("coverage_map_hash")
        batch_op.drop_column("trim_classification")
        batch_op.drop_column("region_order")
        batch_op.drop_column("strip_order")
        batch_op.drop_column("source_bounds_json")
        batch_op.drop_column("original_height")
        batch_op.drop_column("original_width")
        batch_op.drop_column("original_checksum")

    with op.batch_alter_table("story_analyses", schema=None) as batch_op:
        batch_op.drop_column("reconciliation_json")
        batch_op.drop_column("blocking_reasons_json")
        batch_op.drop_column("story_spine_json")
        batch_op.drop_column("evidence_graph_json")
        batch_op.drop_column("continuity_ledger_json")
        batch_op.drop_column("coverage_manifest_json")
        batch_op.drop_column("instruction_sha256")
        batch_op.drop_column("instruction_version")
        batch_op.drop_column("model_name")
        batch_op.drop_column("provider_name")
        batch_op.drop_column("provider_type")
        batch_op.drop_column("state")
        batch_op.drop_column("analysis_run_id")
