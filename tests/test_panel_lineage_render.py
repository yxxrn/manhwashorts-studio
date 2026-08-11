"""RED tests for panel lineage persistence into reference rendering."""

from __future__ import annotations

import importlib
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image


def _unknown_evidence(panel_id: str, asset_id: str, source_order: int) -> dict:
    scoring = importlib.import_module("app.services.visual_scoring")
    evidence = scoring.unknown_visual_evidence(
        panel_id=panel_id,
        source_asset_id=asset_id,
        source_order=source_order,
        reason="provider geometry is unavailable for this test panel",
    )
    return scoring.panel_visual_evidence_json(evidence)


def _asset(asset_id: str, *, checksum: str = "asset-checksum") -> SimpleNamespace:
    return SimpleNamespace(
        id=asset_id,
        checksum=checksum,
        original_checksum=checksum,
        storage_key=f"{asset_id}.png",
        width=8,
        height=6,
        original_width=8,
        original_height=6,
    )


def _region(
    region_id: str,
    panel_id: str,
    asset_id: str,
    source_order: int,
    *,
    x: int = 0,
    y: int = 0,
    width: int = 4,
    height: int = 3,
) -> SimpleNamespace:
    evidence = _unknown_evidence(panel_id, asset_id, source_order)
    return SimpleNamespace(
        id=region_id,
        story_analysis_id="analysis-1",
        panel_id=panel_id,
        source_asset_id=asset_id,
        source_asset_checksum="asset-checksum",
        source_order=source_order,
        bounds_json={"x": x, "y": y, "width": width, "height": height},
        observation_json={"visual_evidence": evidence},
    )


class _PanelDb:
    def __init__(self, regions):
        self.regions = regions

    def scalars(self, _statement):
        return self.regions


def _panel_db(monkeypatch: pytest.MonkeyPatch, pipeline, regions):
    monkeypatch.setattr(
        pipeline,
        "latest_analysis",
        lambda _db, _project_id: SimpleNamespace(id="analysis-1"),
    )
    return _PanelDb(regions)


def _script(section: str, *, panel_ids: list[str] | None = None, citations: list[int] | None = None):
    return SimpleNamespace(
        sections=[
            {
                "section": section,
                "evidence_panel_ids": panel_ids or [],
                "citations": citations or [],
            }
        ]
    )


def _shot(section: str, asset_id: str, order_index: int) -> dict:
    return {
        "section": section,
        "asset_id": asset_id,
        "order_index": order_index,
        "start_time": float(order_index),
        "end_time": float(order_index + 1),
    }


def test_timeline_scene_and_transport_records_expose_panel_lineage_fields():
    models = importlib.import_module("app.models")
    timeline = importlib.import_module("app.services.timeline")
    render = importlib.import_module("app.services.render")

    required_model_fields = {
        "panel_region_id",
        "panel_id",
        "panel_bounds_json",
        "visual_evidence_json",
        "source_asset_checksum",
    }
    model_columns = set(models.TimelineScene.__table__.columns.keys())
    assert required_model_fields <= model_columns
    transport_fields = {"panel_region_id", "panel_id", "panel_bounds", "visual_evidence", "source_asset_checksum"}
    assert transport_fields <= {field.name for field in fields(timeline.SceneSpec)}
    assert transport_fields <= {field.name for field in fields(render.SceneInput)}


def test_reference_binding_uses_panel_evidence_and_preserves_unknown_snapshot(monkeypatch):
    pipeline = importlib.import_module("app.services.pipeline")
    asset = _asset("asset-3")
    region = _region("region-3", "panel-3", asset.id, 3)
    script = _script("hook", panel_ids=[region.panel_id])
    db = _panel_db(monkeypatch, pipeline, [region])

    bind = getattr(pipeline, "_bind_reference_panel_regions", None)
    assert callable(bind), "visual.panel_lineage_binding_missing"
    bound = bind(db, "project-1", script, [asset], [_shot("hook", asset.id, 0)])

    assert bound[0]["panel_region_id"] == region.id
    assert bound[0]["panel_id"] == region.panel_id
    assert bound[0]["panel_bounds"] == (0, 0, 4, 3)
    assert bound[0]["visual_evidence"]["balloon_mask_status"] == "unknown"


def test_integer_citation_is_source_order_not_asset_id(monkeypatch):
    pipeline = importlib.import_module("app.services.pipeline")
    asset = _asset("asset-not-3")
    region = _region("region-3", "panel-3", asset.id, 3)
    script = _script("setup", citations=[3])
    db = _panel_db(monkeypatch, pipeline, [region])
    bind = getattr(pipeline, "_bind_reference_panel_regions", None)
    assert callable(bind), "visual.panel_lineage_binding_missing"

    bound = bind(db, "project-1", script, [asset], [_shot("setup", asset.id, 0)])

    assert bound[0]["panel_region_id"] == region.id
    assert bound[0]["panel_id"] == "panel-3"


def test_reference_binding_cycles_sorted_regions_deterministically(monkeypatch):
    pipeline = importlib.import_module("app.services.pipeline")
    asset = _asset("asset-shared")
    first = _region("region-a", "panel-a", asset.id, 2)
    second = _region("region-b", "panel-b", asset.id, 4)
    script = _script("escalation", panel_ids=[first.panel_id, second.panel_id])
    db = _panel_db(monkeypatch, pipeline, [first, second])
    bind = getattr(pipeline, "_bind_reference_panel_regions", None)
    assert callable(bind), "visual.panel_lineage_binding_missing"

    bound = bind(
        db,
        "project-1",
        script,
        [asset],
        [_shot("escalation", asset.id, index) for index in range(3)],
    )

    assert [item["panel_id"] for item in bound] == ["panel-a", "panel-b", "panel-a"]


def test_reference_binding_rejects_foreign_cited_asset(monkeypatch):
    pipeline = importlib.import_module("app.services.pipeline")
    asset = _asset("asset-local")
    foreign = _region("region-foreign", "panel-foreign", "asset-foreign", 7)
    script = _script("setup", panel_ids=[foreign.panel_id])
    db = _panel_db(monkeypatch, pipeline, [foreign])
    bind = pipeline._bind_reference_panel_regions

    with pytest.raises(Exception, match="visual\\.panel_lineage_unavailable"):
        bind(db, "project-1", script, [asset], [_shot("setup", asset.id, 0)])


def test_reference_crop_materializes_exact_global_bounds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pipeline = importlib.import_module("app.services.pipeline")
    source = tmp_path / "source.png"
    image = Image.new("RGB", (8, 6))
    for y in range(6):
        for x in range(8):
            image.putpixel((x, y), (x * 20, y * 30, 100))
    image.save(source)

    asset = _asset("asset-crop")
    region = _region("region-crop", "panel-crop", asset.id, 1, x=2, y=1, width=3, height=2)
    scene = SimpleNamespace(
        panel_region_id=region.id,
        panel_id=region.panel_id,
        panel_bounds_json=region.bounds_json,
        visual_evidence_json=region.observation_json["visual_evidence"],
        source_asset_checksum=asset.original_checksum,
    )
    db = SimpleNamespace(get=lambda _model, key: region if key == region.id else None)
    monkeypatch.setattr(pipeline.storage, "path_for", lambda _key: source)
    materialize = getattr(pipeline, "_materialize_reference_panel_crop", None)
    assert callable(materialize), "visual.panel_crop_materializer_missing"

    destination = tmp_path / "scene-0000.png"
    result = materialize(db, asset, scene, destination)

    assert result == destination
    with Image.open(result) as cropped:
        assert cropped.mode == "RGB"
        assert cropped.size == (3, 2)
        assert cropped.getpixel((0, 0)) == image.getpixel((2, 1))
        assert cropped.getpixel((2, 1)) == image.getpixel((4, 2))


def test_reference_crop_rejects_stale_lineage_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pipeline = importlib.import_module("app.services.pipeline")
    source = tmp_path / "source.png"
    Image.new("RGB", (8, 6), (40, 50, 60)).save(source)
    asset = _asset("asset-stale")
    region = _region("region-stale", "panel-stale", asset.id, 1)
    scene = SimpleNamespace(
        panel_region_id=region.id,
        panel_id=region.panel_id,
        panel_bounds_json=region.bounds_json,
        visual_evidence_json=region.observation_json["visual_evidence"],
        source_asset_checksum="stale-checksum",
    )
    db = SimpleNamespace(get=lambda _model, key: region if key == region.id else None)
    monkeypatch.setattr(pipeline.storage, "path_for", lambda _key: source)
    materialize = getattr(pipeline, "_materialize_reference_panel_crop", None)
    assert callable(materialize), "visual.panel_crop_materializer_missing"

    with pytest.raises(Exception, match="visual\\.panel_lineage_unavailable"):
        materialize(db, asset, scene, tmp_path / "scene-0001.png")


@pytest.mark.parametrize(
    "snapshot",
    [None, {"contract_version": "broken"}],
    ids=["missing", "malformed"],
)
def test_reference_crop_rejects_missing_or_malformed_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    snapshot,
):
    pipeline = importlib.import_module("app.services.pipeline")
    source = tmp_path / "source.png"
    Image.new("RGB", (8, 6), (40, 50, 60)).save(source)
    asset = _asset("asset-invalid-snapshot")
    region = _region("region-invalid-snapshot", "panel-invalid-snapshot", asset.id, 1)
    scene = SimpleNamespace(
        panel_region_id=region.id,
        panel_id=region.panel_id,
        panel_bounds_json=region.bounds_json,
        visual_evidence_json=snapshot,
        source_asset_checksum=asset.original_checksum,
    )
    db = SimpleNamespace(get=lambda _model, key: region if key == region.id else None)
    monkeypatch.setattr(pipeline.storage, "path_for", lambda _key: source)
    materialize = pipeline._materialize_reference_panel_crop

    with pytest.raises(Exception, match="visual\\.panel_lineage_unavailable"):
        materialize(db, asset, scene, tmp_path / "scene-invalid.png")


def test_legacy_scene_transport_has_no_reference_lineage():
    render = importlib.import_module("app.services.render")
    scene = render.SceneInput(image_path=None, start_time=0.0, end_time=1.0)
    assert scene.panel_region_id is None
    assert scene.panel_id == ""
    assert scene.panel_bounds is None
    assert scene.visual_evidence is None
    assert scene.source_asset_checksum == ""
