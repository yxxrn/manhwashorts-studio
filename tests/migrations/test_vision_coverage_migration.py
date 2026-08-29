"""RED tests for the vision coverage persistence boundary."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PREDECESSOR = "a4p0_editorial_voice_visual_contract"
TARGET_REVISION = "b7c4d8e91f20"

STORY_ANALYSIS_FIELDS = (
    "analysis_run_id",
    "state",
    "provider_type",
    "provider_name",
    "model_name",
    "instruction_version",
    "instruction_sha256",
    "coverage_manifest_json",
    "continuity_ledger_json",
    "evidence_graph_json",
    "story_spine_json",
    "blocking_reasons_json",
    "reconciliation_json",
)

SOURCE_LINEAGE_FIELDS = (
    "original_checksum",
    "original_width",
    "original_height",
    "source_bounds_json",
    "strip_order",
    "region_order",
    "trim_classification",
    "coverage_map_hash",
)

PANEL_REGION_FIELDS = (
    "id",
    "story_analysis_id",
    "source_asset_id",
    "source_asset_checksum",
    "original_width",
    "original_height",
    "strip_region_id",
    "panel_id",
    "source_order",
    "bounds_json",
    "region_class",
    "segmentation_confidence",
    "segmentation_version",
    "coverage_map_hash",
    "observation_json",
    "chunk_index",
    "evidence_refs_json",
    "created_at",
    "updated_at",
)

TIMELINE_SNAPSHOT_FIELDS = (
    "panel_region_id",
    "panel_id",
    "panel_bounds_json",
    "visual_evidence_json",
    "source_asset_checksum",
)


def _load_module(module_name: str):
    try:
        return importlib.import_module(module_name)
    except Exception:  # noqa: BLE001 - absent RED boundaries fail in test bodies
        return None


def _migration_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database_url = f"sqlite:///{tmp_path / 'vision-migration.db'}"
    data_dir = tmp_path / "app-data"
    monkeypatch.setenv("MS_DATABASE_URL", database_url)
    monkeypatch.setenv("MS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("MS_STORAGE_DIR", str(data_dir / "storage"))
    monkeypatch.setenv("MS_OUTPUT_DIR", str(data_dir / "output"))
    monkeypatch.setenv("MS_TMP_DIR", str(data_dir / "tmp"))
    monkeypatch.setenv("MS_TEST_MODE", "1")

    config_module = importlib.import_module("app.config")
    config_module.get_settings.cache_clear()
    monkeypatch.setattr(config_module, "settings", config_module.get_settings())

    from alembic.config import Config

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return config, database_url


def _upgrade_to_vision_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config, database_url = _migration_config(tmp_path, monkeypatch)
    from alembic import command

    try:
        command.upgrade(config, TARGET_REVISION)
    except Exception as exc:  # noqa: BLE001 - report a machine-readable RED reason
        pytest.fail(
            "vision_migration_missing: upgrade from "
            f"{PREDECESSOR} to {TARGET_REVISION} failed with "
            f"{type(exc).__name__}: {exc}"
        )

    from sqlalchemy import create_engine

    return config, create_engine(database_url)


def test_models_declare_the_complete_vision_boundary():
    models = _load_module("app.models")
    assert models is not None, "vision_model_missing: app.models could not be imported"

    source_asset = getattr(models, "SourceAsset", None)
    assert source_asset is not None, "vision_model_missing: SourceAsset"
    source_missing = sorted(
        name for name in SOURCE_LINEAGE_FIELDS if not hasattr(source_asset, name)
    )

    story_analysis = getattr(models, "StoryAnalysis", None)
    assert story_analysis is not None, "vision_model_missing: StoryAnalysis"
    analysis_missing = sorted(
        name for name in STORY_ANALYSIS_FIELDS if not hasattr(story_analysis, name)
    )

    panel_region = getattr(models, "PanelRegion", None)
    assert panel_region is not None, "vision_model_missing: PanelRegion"
    panel_columns = set(panel_region.__table__.columns.keys())
    panel_missing = sorted(set(PANEL_REGION_FIELDS) - panel_columns)

    assert not source_missing and not analysis_missing and not panel_missing, (
        "vision_model_schema_missing: "
        f"SourceAsset={source_missing}; StoryAnalysis={analysis_missing}; "
        f"PanelRegion={panel_missing}"
    )


def test_upgrade_adds_columns_constraints_and_indexes(tmp_path, monkeypatch):
    _config, engine = _upgrade_to_vision_boundary(tmp_path, monkeypatch)
    try:
        from sqlalchemy import inspect

        inspector = inspect(engine)
        story_columns = {
            column["name"]: column for column in inspector.get_columns("story_analyses")
        }
        assert set(STORY_ANALYSIS_FIELDS) <= set(story_columns), (
            "vision_schema_missing: StoryAnalysis columns "
            f"{sorted(set(STORY_ANALYSIS_FIELDS) - set(story_columns))}"
        )
        assert all(story_columns[name]["nullable"] for name in STORY_ANALYSIS_FIELDS), (
            "historical_analysis_guard_missing: new StoryAnalysis fields must be nullable"
        )

        source_columns = {
            column["name"]: column for column in inspector.get_columns("source_assets")
        }
        assert set(SOURCE_LINEAGE_FIELDS) <= set(source_columns), (
            "vision_schema_missing: SourceAsset lineage columns "
            f"{sorted(set(SOURCE_LINEAGE_FIELDS) - set(source_columns))}"
        )

        assert "panel_regions" in inspector.get_table_names(), (
            "vision_schema_missing: panel_regions table"
        )
        panel_columns = {
            column["name"]: column for column in inspector.get_columns("panel_regions")
        }
        assert set(PANEL_REGION_FIELDS) <= set(panel_columns), (
            "vision_schema_missing: PanelRegion columns "
            f"{sorted(set(PANEL_REGION_FIELDS) - set(panel_columns))}"
        )

        foreign_keys = {
            tuple(foreign_key["constrained_columns"]): foreign_key
            for foreign_key in inspector.get_foreign_keys("panel_regions")
        }
        assert foreign_keys[("story_analysis_id",)]["referred_table"] == "story_analyses"
        assert foreign_keys[("source_asset_id",)]["referred_table"] == "source_assets"
        assert foreign_keys[("story_analysis_id",)]["options"]["ondelete"] == "CASCADE"
        assert foreign_keys[("source_asset_id",)]["options"]["ondelete"] == "CASCADE"

        indexes = inspector.get_indexes("panel_regions")
        assert any(
            index.get("unique")
            and tuple(index["column_names"]) == ("story_analysis_id", "source_order")
            for index in indexes
        ), "vision_index_missing: unique(story_analysis_id, source_order)"
        assert any(
            tuple(index["column_names"]) == ("source_asset_id",) for index in indexes
        ), "vision_index_missing: source_asset_id"
        assert any(
            tuple(index["column_names"]) == ("region_class",) for index in indexes
        ), "vision_index_missing: region_class"
    finally:
        engine.dispose()


def test_linked_story_analysis_asset_and_panel_region_round_trip(tmp_path, monkeypatch):
    _config, engine = _upgrade_to_vision_boundary(tmp_path, monkeypatch)
    try:
        models = _load_module("app.models")
        assert models is not None, "vision_model_missing: app.models"
        required_models = ("User", "Workspace", "Project", "SourceAsset", "StoryAnalysis", "PanelRegion")
        missing_models = [name for name in required_models if not hasattr(models, name)]
        assert not missing_models, f"vision_model_missing: {missing_models}"

        from sqlalchemy.orm import Session

        with Session(engine) as session:
            user = models.User(id="user-vision", email="vision@example.com", name="Vision")
            workspace = models.Workspace(id="workspace-vision", owner_id=user.id, name="Vision")
            project = models.Project(
                id="project-vision",
                workspace_id=workspace.id,
                title="Synthetic vision migration",
            )
            asset = models.SourceAsset(
                id="asset-vision",
                project_id=project.id,
                type="image",
                original_filename="synthetic.png",
                checksum="derived-checksum",
                original_checksum="original-checksum",
                original_width=64,
                original_height=192,
                source_bounds_json={"x": 0, "y": 0, "width": 64, "height": 192},
                strip_order=0,
                region_order=1,
                trim_classification="none",
                coverage_map_hash="coverage-hash",
            )
            analysis = models.StoryAnalysis(
                id="analysis-vision",
                project_id=project.id,
                analysis_run_id="run-vision",
                state=None,
                coverage_manifest_json=None,
                reconciliation_json=None,
            )
            region = models.PanelRegion(
                id="region-vision",
                story_analysis_id=analysis.id,
                source_asset_id=asset.id,
                source_asset_checksum=asset.original_checksum,
                original_width=asset.original_width,
                original_height=asset.original_height,
                strip_region_id="strip-0-region-1",
                panel_id="panel-1",
                source_order=0,
                bounds_json={"x": 0, "y": 64, "width": 64, "height": 48},
                region_class="canonical_panel",
                segmentation_confidence=1.0,
                segmentation_version="test-v1",
                coverage_map_hash=asset.coverage_map_hash,
                observation_json={"visible_facts": ["synthetic"]},
                chunk_index=0,
                evidence_refs_json=["panel-1"],
            )
            session.add_all([user, workspace, project, asset, analysis, region])
            session.commit()

            loaded = session.get(models.PanelRegion, region.id)
            historical = session.get(models.StoryAnalysis, analysis.id)
            assert loaded is not None
            assert loaded.story_analysis.id == analysis.id
            assert loaded.source_asset.id == asset.id
            assert loaded.source_asset_checksum == "original-checksum"
            assert loaded.bounds_json["y"] == 64
            assert historical is not None
            assert historical.state is None
            assert historical.coverage_manifest_json is None
            assert historical.reconciliation_json is None
    finally:
        engine.dispose()


def test_downgrade_removes_only_the_vision_boundary_objects(tmp_path, monkeypatch):
    config, engine = _upgrade_to_vision_boundary(tmp_path, monkeypatch)
    try:
        from sqlalchemy import inspect

        from alembic import command

        try:
            command.downgrade(config, PREDECESSOR)
        except Exception as exc:  # noqa: BLE001 - report a machine-readable RED reason
            pytest.fail(
                "vision_migration_missing: downgrade to "
                f"{PREDECESSOR} failed with {type(exc).__name__}: {exc}"
            )

        inspector = inspect(engine)
        assert "panel_regions" not in inspector.get_table_names(), (
            "vision_downgrade_leak: panel_regions remains"
        )
        story_columns = {column["name"] for column in inspector.get_columns("story_analyses")}
        source_columns = {column["name"] for column in inspector.get_columns("source_assets")}
        assert not set(STORY_ANALYSIS_FIELDS) & story_columns, (
            "vision_downgrade_leak: StoryAnalysis vision columns remain"
        )
        assert not set(SOURCE_LINEAGE_FIELDS) & source_columns, (
            "vision_downgrade_leak: SourceAsset lineage columns remain"
        )
        assert {"story_analyses", "source_assets", "projects"} <= set(inspector.get_table_names())
        assert {"characters", "checksum", "width", "height"} <= story_columns | source_columns
    finally:
        engine.dispose()


def _task4_revision(config):
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(config)
    candidates = [
        revision
        for revision in script.walk_revisions()
        if "panel lineage" in (revision.doc or "").lower()
    ]
    assert len(candidates) == 1, (
        "timeline_lineage_migration_missing: expected one migration documenting panel lineage"
    )
    revision = candidates[0]
    assert revision.down_revision == TARGET_REVISION, (
        "timeline_lineage_migration_parent_invalid: "
        f"expected {TARGET_REVISION}, got {revision.down_revision}"
    )
    return revision


def test_panel_lineage_migration_is_a_child_of_repository_head_and_reversible(
    tmp_path, monkeypatch
):
    config, database_url = _migration_config(tmp_path, monkeypatch)
    revision = _task4_revision(config)
    from sqlalchemy import create_engine, inspect

    from alembic import command

    command.upgrade(config, revision.revision)
    engine = create_engine(database_url)
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("timeline_scenes")}
        assert set(TIMELINE_SNAPSHOT_FIELDS) <= columns, (
            "timeline_lineage_schema_missing: "
            f"{sorted(set(TIMELINE_SNAPSHOT_FIELDS) - columns)}"
        )
        command.downgrade(config, TARGET_REVISION)
        columns = {column["name"] for column in inspect(engine).get_columns("timeline_scenes")}
        assert not set(TIMELINE_SNAPSHOT_FIELDS) & columns, (
            "timeline_lineage_downgrade_leak: "
            f"{sorted(set(TIMELINE_SNAPSHOT_FIELDS) & columns)}"
        )
    finally:
        engine.dispose()
