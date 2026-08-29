"""RED tests for complete source-space vision coverage and lineage."""

from __future__ import annotations

import importlib
import importlib.util
import random
import sys
from dataclasses import fields, is_dataclass
from pathlib import Path


def _load_fixture_module():
    fixture_path = Path(__file__).resolve().parents[1] / "fixtures" / "vision_coverage.py"
    spec = importlib.util.spec_from_file_location("vision_coverage_fixture", fixture_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_module(module_name: str):
    try:
        return importlib.import_module(module_name)
    except Exception:  # noqa: BLE001 - turn absent future boundaries into body RED
        return None


def _source_inputs(segmentation_module, strips):
    input_type = getattr(segmentation_module, "SourceAssetInput", None)
    assert input_type is not None, (
        "coverage_boundary_missing: app.services.segmentation.SourceAssetInput"
    )
    return tuple(
        input_type(
            source_asset_id=strip.source_asset_id,
            original_checksum=strip.original_checksum,
            original_width=strip.width,
            original_height=strip.height,
            source_bounds=strip.source_bounds,
            strip_order=strip.strip_order,
            region_order=0,
            payload=strip.payload,
        )
        for strip in strips
    )


def test_complete_coverage_maps_every_ordered_source_band():
    """Every strip and every ordered content band must reach the map."""

    fixture_module = _load_fixture_module()
    assert fixture_module is not None, "fixture_missing: tests/fixtures/vision_coverage.py"
    strips = tuple(fixture_module.make_ordered_source_strips())

    assert len(strips) == 3, "fixture_contract: expected three ordered source strips"
    assert tuple(strip.strip_order for strip in strips) == (0, 1, 2)
    assert len({strip.checksum for strip in strips}) == len(strips)
    assert all(len(strip.content_bands) == 3 for strip in strips)

    segmentation_module = _load_module("app.services.segmentation")
    assert segmentation_module is not None, (
        "coverage_boundary_missing: app.services.segmentation"
    )
    planner = getattr(segmentation_module, "build_complete_coverage_map", None)
    assert planner is not None, (
        "coverage_boundary_missing: app.services.segmentation.build_complete_coverage_map"
    )

    coverage = planner(
        _source_inputs(segmentation_module, strips),
        segmentation_version="test-vision-coverage-v1",
    )
    expected_source_ids = tuple(strip.source_asset_id for strip in strips)
    expected_bands = tuple(
        (strip.source_asset_id, band)
        for strip in strips
        for band in strip.content_bands
    )
    assert tuple(coverage.source_asset_ids) == expected_source_ids
    actual_bands = tuple(
        (region.source_asset_id, tuple(region.bounds))
        for region in coverage.regions
        if region.region_class == "canonical_panel"
    )
    assert actual_bands == expected_bands
    assert coverage.panel_count == len(expected_bands)


def test_derived_slices_preserve_original_source_lineage():
    """Independent slice rows must retain parent checksum, bounds, and order."""

    fixture_module = _load_fixture_module()
    assert fixture_module is not None, "fixture_missing: tests/fixtures/vision_coverage.py"
    strips = tuple(fixture_module.make_ordered_source_strips())
    assert tuple(strip.strip_order for strip in strips) == (0, 1, 2)

    source_asset = getattr(_load_module("app.models"), "SourceAsset", None)
    assert source_asset is not None, "lineage_model_missing: app.models.SourceAsset"
    table = getattr(source_asset, "__table__", None)
    model_fields = set(table.columns.keys()) if table is not None else set()
    required_model_fields = {
        "original_checksum",
        "original_width",
        "original_height",
        "source_bounds_json",
        "strip_order",
        "region_order",
        "trim_classification",
        "coverage_map_hash",
    }
    model_missing = sorted(required_model_fields - model_fields)

    ingest_module = _load_module("app.services.ingest")
    ingested_asset = getattr(ingest_module, "IngestedAsset", None)
    assert ingested_asset is not None, "lineage_result_missing: app.services.ingest.IngestedAsset"
    result_fields = {
        field.name for field in fields(ingested_asset)
    } if is_dataclass(ingested_asset) else set()
    required_result_fields = {
        "original_checksum",
        "original_width",
        "original_height",
        "source_bounds",
        "strip_order",
        "region_order",
        "trim_classification",
        "coverage_map_hash",
    }
    result_missing = sorted(required_result_fields - result_fields)

    assert not model_missing and not result_missing, (
        "lineage_gap: independent slice lineage is incomplete; "
        f"SourceAsset missing={model_missing}; IngestedAsset missing={result_missing}"
    )


def test_analysis_planning_never_uses_random_sampling(monkeypatch):
    """The all-source analysis path must be deterministic and never sample."""

    def fail_if_random_called(*_args, **_kwargs):
        raise AssertionError("random_entry_called: vision coverage planner sampled input")

    for name in ("choice", "choices", "sample", "randint", "randrange", "shuffle", "random"):
        monkeypatch.setattr(random, name, fail_if_random_called)

    fixture_module = _load_fixture_module()
    assert fixture_module is not None, "fixture_missing: tests/fixtures/vision_coverage.py"
    strips = tuple(fixture_module.make_ordered_source_strips())
    segmentation_module = _load_module("app.services.segmentation")
    planner = getattr(segmentation_module, "build_complete_coverage_map", None) if segmentation_module else None
    assert planner is not None, (
        "deterministic_planner_missing: app.services.segmentation.build_complete_coverage_map"
    )

    coverage = planner(
        _source_inputs(segmentation_module, strips),
        segmentation_version="test-vision-coverage-v1",
    )
    assert tuple(coverage.source_asset_ids) == tuple(strip.source_asset_id for strip in strips)
