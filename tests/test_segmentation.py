"""RED tests for deterministic, complete source-space segmentation."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import io
import random
import sys
from dataclasses import dataclass, fields, replace
from pathlib import Path

import pytest
from PIL import Image

pytestmark = pytest.mark.usefixtures("app_settings")


def _load_fixture_module():
    fixture_path = Path(__file__).parent / "fixtures" / "vision_coverage.py"
    spec = importlib.util.spec_from_file_location("vision_coverage_fixture_task3", fixture_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _require_segmentation():
    try:
        module = importlib.import_module("app.services.segmentation")
    except Exception as exc:  # noqa: BLE001 - missing boundary must fail in the body
        module = None
        reason = (
            "segmentation_boundary_missing: app.services.segmentation "
            f"({type(exc).__name__}: {exc})"
        )
    else:
        reason = "segmentation_boundary_missing: app.services.segmentation"
    assert module is not None, reason
    return module


def _fixture_inputs(segmentation_module):
    fixture_module = _load_fixture_module()
    assert fixture_module is not None, "fixture_missing: tests/fixtures/vision_coverage.py"
    strips = tuple(fixture_module.make_ordered_source_strips())
    source_type = getattr(segmentation_module, "SourceAssetInput", None)
    assert source_type is not None, "coverage_boundary_missing: SourceAssetInput"
    inputs = tuple(
        source_type(
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
    return strips, inputs


def _jpeg(width: int, height: int, *, top: int = 0, bottom: int | None = None) -> bytes:
    bottom = height if bottom is None else bottom
    image = Image.new("RGB", (width, height), (83, 97, 121))
    pixels = image.load()
    for y in range(height):
        if y < top or y >= bottom:
            for x in range(width):
                pixels[x, y] = (255, 255, 255)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=92)
    return output.getvalue()


@dataclass(frozen=True)
class _SliceInput:
    """Test-only fallback while the production lineage fields are RED."""

    source_asset_id: str
    original_checksum: str
    original_width: int
    original_height: int
    source_bounds: tuple[int, int, int, int]
    strip_order: int
    region_order: int
    payload: bytes
    decoded_width: int
    decoded_height: int


def _slice_inputs(segmentation_module, *, heights=(32, 32, 32), missing=()):
    source_type = getattr(segmentation_module, "SourceAssetInput", None)
    assert source_type is not None, "coverage_boundary_missing: SourceAssetInput"
    source_fields = {field.name for field in fields(source_type)}
    input_type = (
        source_type
        if {"decoded_width", "decoded_height"} <= source_fields
        else _SliceInput
    )
    width = 32
    original_height = sum(heights)
    result = []
    offset = 0
    for index, height in enumerate(heights):
        payload = _jpeg(width, height)
        if index not in missing:
            result.append(
                input_type(
                    source_asset_id=f"slice-{index}",
                    original_checksum="original-slice-checksum",
                    original_width=width,
                    original_height=original_height,
                    source_bounds=(0, offset, width, offset + height),
                    strip_order=index,
                    region_order=index,
                    payload=payload,
                    decoded_width=width,
                    decoded_height=height,
                )
            )
        offset += height
    return tuple(result)


def test_source_asset_input_exposes_decoded_payload_dimensions():
    segmentation_module = _require_segmentation()
    source_type = getattr(segmentation_module, "SourceAssetInput", None)
    assert source_type is not None, "coverage_boundary_missing: SourceAssetInput"
    result_fields = {field.name for field in fields(source_type)}
    assert {"decoded_width", "decoded_height"} <= result_fields, (
        "slice_lineage_missing: SourceAssetInput must distinguish decoded "
        "payload dimensions from original dimensions"
    )


def test_adjacent_slices_reconcile_against_one_full_original_denominator():
    segmentation_module = _require_segmentation()
    builder = getattr(segmentation_module, "build_complete_coverage_map", None)
    assert builder is not None, "coverage_boundary_missing: build_complete_coverage_map"
    inputs = _slice_inputs(segmentation_module, heights=(32, 64))

    coverage = builder(inputs, segmentation_version="slice-task3-2-v1")
    assert coverage.source_asset_ids == ("slice-0", "slice-1")
    assert coverage.source_content_coverage_ratio == pytest.approx(1.0)
    assert coverage.unresolved_material_area == 0
    assert coverage.reconciliation_errors == ()
    assert [
        tuple(region.bounds)
        for region in coverage.regions
        if region.region_class == "canonical_panel"
    ] == [(0, 0, 32, 32), (0, 32, 32, 96)]
    assert coverage.map_sha256 == builder(
        inputs,
        segmentation_version="slice-task3-2-v1",
    ).map_sha256


@pytest.mark.parametrize("missing", (1, 2), ids=("middle_slice", "last_slice"))
def test_missing_slice_cannot_disappear_from_source_denominator(missing):
    segmentation_module = _require_segmentation()
    builder = getattr(segmentation_module, "build_complete_coverage_map", None)
    assert builder is not None, "coverage_boundary_missing: build_complete_coverage_map"
    inputs = _slice_inputs(
        segmentation_module,
        heights=(32, 32, 32),
        missing=(missing,),
    )

    coverage = builder(inputs, segmentation_version="slice-task3-2-v1")
    assert coverage.source_content_coverage_ratio < 1.0
    assert any(
        error.startswith("coverage.partition_gap")
        for error in coverage.reconciliation_errors
    )
    assert coverage.reconciliation_errors


@pytest.mark.parametrize("mismatch", ("decoded_dimensions", "bounds_span"))
def test_slice_decode_or_global_bounds_mismatch_blocks(mismatch):
    segmentation_module = _require_segmentation()
    builder = getattr(segmentation_module, "build_complete_coverage_map", None)
    assert builder is not None, "coverage_boundary_missing: build_complete_coverage_map"
    source = _slice_inputs(segmentation_module, heights=(32,))[0]
    if mismatch == "decoded_dimensions":
        source = replace(source, decoded_width=31)
    else:
        source = replace(source, source_bounds=(0, 0, 32, 31))

    coverage = builder((source,), segmentation_version="slice-task3-2-v1")
    assert coverage.unresolved_material_area > 0
    assert coverage.source_content_coverage_ratio < 1.0
    assert any("decoded" in error for error in coverage.reconciliation_errors)


def test_slice_tile_ranges_include_global_y_offset_and_unsliced_defaults_remain():
    segmentation_module = _require_segmentation()
    builder = getattr(segmentation_module, "build_complete_coverage_map", None)
    planner = getattr(segmentation_module, "plan_overlapping_tiles", None)
    assert builder is not None, "coverage_boundary_missing: build_complete_coverage_map"
    assert planner is not None, "coverage_boundary_missing: plan_overlapping_tiles"
    inputs = _slice_inputs(segmentation_module, heights=(32, 64))
    coverage = builder(inputs, segmentation_version="slice-task3-2-v1")

    assert [
        (tile.source_asset_id, tile.y0, tile.y1)
        for tile in coverage.tiles
    ] == [
        ("slice-0", 0, 32),
        ("slice-1", 32, 96),
    ]
    unsliced = planner("unsliced", 32, 96, tile_height=2048, overlap=128)
    assert [(tile.y0, tile.y1) for tile in unsliced] == [(0, 96)]


def test_original_lineage_dimensions_and_checksum_are_in_map_hash_boundary():
    segmentation_module = _require_segmentation()
    builder = getattr(segmentation_module, "build_complete_coverage_map", None)
    assert builder is not None, "coverage_boundary_missing: build_complete_coverage_map"
    inputs = _slice_inputs(segmentation_module, heights=(32, 64))
    baseline = builder(inputs, segmentation_version="slice-task3-2-v1")
    assert baseline.source_content_coverage_ratio == pytest.approx(1.0)

    checksum_changed = replace(inputs[0], original_checksum="different-checksum")
    dimensions_changed = replace(
        inputs[0],
        original_width=33,
        decoded_width=33,
        source_bounds=(0, 0, 33, 32),
    )
    assert builder(
        (checksum_changed, *inputs[1:]),
        segmentation_version="slice-task3-2-v1",
    ).map_sha256 != baseline.map_sha256
    assert builder(
        (dimensions_changed, *inputs[1:]),
        segmentation_version="slice-task3-2-v1",
    ).map_sha256 != baseline.map_sha256


def _overviews(strips, coverage):
    by_id = {
        strip.source_asset_id: {
            "bounds": tuple(strip.source_bounds),
            "bands": [],
        }
        for strip in strips
    }
    for region in coverage.regions:
        if region.source_asset_id in by_id:
            by_id[region.source_asset_id]["bands"].append(
                {
                    "bounds": tuple(region.bounds),
                    "region_class": region.region_class,
                }
            )
    return by_id


def test_tile_validation_and_exact_ranges():
    segmentation_module = _require_segmentation()
    planner = getattr(segmentation_module, "plan_overlapping_tiles", None)
    assert planner is not None, "coverage_boundary_missing: plan_overlapping_tiles"

    tiles = planner("asset-5000", 720, 5000, tile_height=2048, overlap=128)
    assert [
        (tile.y0, tile.y1, tile.overlap_above, tile.overlap_below)
        for tile in tiles
    ] == [
        (0, 2048, 0, 128),
        (1920, 3968, 128, 128),
        (3840, 5000, 128, 0),
    ]
    assert tiles == planner("asset-5000", 720, 5000, tile_height=2048, overlap=128)
    assert len({tile.tile_sha256 for tile in tiles}) == len(tiles)

    invalid = (
        ("asset", 0, 10, 10, 1),
        ("asset", 10, 0, 10, 1),
        ("asset", 10, 10, 0, 1),
        ("asset", 10, 10, 10, 10),
        ("asset", 10, 10, 10, -1),
    )
    for source_asset_id, width, height, tile_height, overlap in invalid:
        with pytest.raises(ValueError):
            planner(
                source_asset_id,
                width,
                height,
                tile_height=tile_height,
                overlap=overlap,
            )


def test_complete_map_contains_every_source_and_band():
    segmentation_module = _require_segmentation()
    strips, inputs = _fixture_inputs(segmentation_module)
    builder = getattr(segmentation_module, "build_complete_coverage_map", None)
    assert builder is not None, "coverage_boundary_missing: build_complete_coverage_map"

    coverage = builder(inputs, segmentation_version="task3-v1")
    expected_source_ids = tuple(strip.source_asset_id for strip in strips)
    expected_bands = tuple(
        (strip.source_asset_id, band)
        for strip in strips
        for band in strip.content_bands
    )
    actual_bands = tuple(
        (region.source_asset_id, tuple(region.bounds))
        for region in coverage.regions
        if region.region_class == "canonical_panel"
    )
    assert tuple(coverage.source_asset_ids) == expected_source_ids
    assert actual_bands == expected_bands
    assert coverage.panel_count == len(expected_bands)
    assert coverage.unresolved_material_area == 0
    assert coverage.source_content_coverage_ratio == pytest.approx(1.0)
    assert coverage.map_sha256 == builder(inputs, segmentation_version="task3-v1").map_sha256


def test_verified_gutters_are_evidenced_and_bad_payload_is_unresolved():
    segmentation_module = _require_segmentation()
    strips, inputs = _fixture_inputs(segmentation_module)
    builder = segmentation_module.build_complete_coverage_map

    coverage = builder(inputs, segmentation_version="task3-v1")
    gutters = [
        region for region in coverage.regions if region.region_class == "verified_gutter"
    ]
    assert gutters
    assert all(region.evidence and 0.0 < region.confidence <= 1.0 for region in gutters)

    bad_input = replace(inputs[0], payload=b"not-a-decodable-image")
    bad = builder((bad_input,), segmentation_version="task3-v1")
    assert bad.unresolved_material_area > 0
    assert bad.source_content_coverage_ratio < 1.0
    assert any(
        error.startswith("coverage.unresolved_material")
        for error in bad.reconciliation_errors
    )


def test_union_area_does_not_double_count_overlapping_derived_bounds():
    segmentation_module = _require_segmentation()
    strips, inputs = _fixture_inputs(segmentation_module)
    builder = segmentation_module.build_complete_coverage_map
    duplicate = replace(
        inputs[0],
        source_asset_id="derived-overlap",
        strip_order=1,
        region_order=1,
    )

    coverage = builder((inputs[0], duplicate), segmentation_version="task3-v1")
    original_area = strips[0].width * strips[0].height
    accounted_area = coverage.canonical_panel_area + coverage.verified_gutter_area
    assert tuple(coverage.source_asset_ids) == (
        inputs[0].source_asset_id,
        duplicate.source_asset_id,
    )
    assert accounted_area == original_area
    assert coverage.source_content_coverage_ratio == pytest.approx(1.0)


def test_completeness_verifier_reports_source_space_gaps_and_conflicts():
    segmentation_module = _require_segmentation()
    strips, inputs = _fixture_inputs(segmentation_module)
    coverage = segmentation_module.build_complete_coverage_map(
        inputs,
        segmentation_version="task3-v1",
    )
    verifier = getattr(segmentation_module, "verify_segmentation_completeness", None)
    assert verifier is not None, "coverage_boundary_missing: completeness verifier"
    base = _overviews(strips, coverage)
    assert verifier(base, coverage) == ()

    first_id = strips[0].source_asset_id
    missing = dict(base)
    missing.pop(first_id)
    assert any(
        error.startswith("coverage.source_asset_missing")
        for error in verifier(missing, coverage)
    )

    seam_gap = {key: dict(value) for key, value in base.items()}
    seam_gap[first_id] = dict(base[first_id])
    seam_gap[first_id]["bands"] = list(base[first_id]["bands"][:-1])
    assert any(
        error.startswith("coverage.seam_gap")
        for error in verifier(seam_gap, coverage)
    )

    unexplained = {key: dict(value) for key, value in base.items()}
    unexplained[first_id] = dict(base[first_id])
    unexplained[first_id]["bands"] = list(base[first_id]["bands"]) + [
        {
            "bounds": (0, 1, strips[0].width, 2),
            "region_class": "canonical_panel",
        }
    ]
    assert any(
        error.startswith("coverage.unexplained_band")
        for error in verifier(unexplained, coverage)
    )

    top_truncated = {key: dict(value) for key, value in base.items()}
    top_truncated[first_id] = dict(base[first_id])
    x0, y0, x1, y1 = base[first_id]["bounds"]
    top_truncated[first_id]["bounds"] = (x0, y0 + 1, x1, y1)
    assert any(
        error.startswith("coverage.top_truncation")
        for error in verifier(top_truncated, coverage)
    )

    bottom_truncated = {key: dict(value) for key, value in base.items()}
    bottom_truncated[first_id] = dict(base[first_id])
    bottom_truncated[first_id]["bounds"] = (x0, y0, x1, y1 - 1)
    assert any(
        error.startswith("coverage.bottom_truncation")
        for error in verifier(bottom_truncated, coverage)
    )

    conflict = {key: dict(value) for key, value in base.items()}
    conflict[first_id] = dict(base[first_id])
    conflict[first_id]["bands"] = list(base[first_id]["bands"]) + [
        {"bounds": (0, 0, strips[0].width, 4), "region_class": "verified_gutter"},
        {"bounds": (0, 2, strips[0].width, 6), "region_class": "canonical_panel"},
    ]
    assert any(
        error.startswith("coverage.overlap_class_conflict")
        for error in verifier(conflict, coverage)
    )


def test_ingested_asset_exposes_unsliced_and_sliced_lineage():
    _require_segmentation()
    ingest = importlib.import_module("app.services.ingest")
    ingested_type = getattr(ingest, "IngestedAsset", None)
    assert ingested_type is not None
    result_fields = {field.name for field in fields(ingested_type)}
    required = {
        "original_checksum",
        "original_width",
        "original_height",
        "source_bounds",
        "strip_order",
        "region_order",
        "trim_classification",
        "coverage_map_hash",
    }
    assert required <= result_fields

    data = _jpeg(400, 600)
    checksum = hashlib.sha256(data).hexdigest()
    unsliced = ingest.ingest_image_parts("project", "panel.jpg", data)
    assert len(unsliced) == 1
    assert unsliced[0].original_checksum == checksum
    assert (unsliced[0].original_width, unsliced[0].original_height) == (400, 600)
    assert tuple(unsliced[0].source_bounds) == (0, 0, 400, 600)
    assert unsliced[0].strip_order == 0
    assert unsliced[0].region_order == 0

    tall = _jpeg(720, 4372, top=100, bottom=4272)
    tall_checksum = hashlib.sha256(tall).hexdigest()
    sliced = ingest.ingest_image_parts("project", "strip.jpg", tall)
    assert len(sliced) > 1
    assert all(asset.original_checksum == tall_checksum for asset in sliced)
    assert all(
        (asset.original_width, asset.original_height) == (720, 4372)
        for asset in sliced
    )
    assert tuple(sliced[0].source_bounds)[1] == 0
    assert tuple(sliced[-1].source_bounds)[3] == 4372
    assert [asset.strip_order for asset in sliced] == [0] * len(sliced)
    assert [asset.region_order for asset in sliced] == list(range(len(sliced)))
    assert all(asset.trim_classification for asset in sliced)


def test_slice_merges_slivers_and_retains_original_top_bottom(monkeypatch):
    _require_segmentation()
    strips = importlib.import_module("app.services.strips")
    data = _jpeg(400, 1000)

    monkeypatch.setattr(
        strips,
        "plan_cuts",
        lambda _image: ([(20, 430), (430, 500), (500, 980)], [False, False, False]),
    )
    pieces = strips.slice_strip(data)
    assert len(pieces) == 2
    assert pieces[0].top == 0
    assert pieces[-1].bottom == 1000
    assert all(piece.bottom > piece.top for piece in pieces)
    assert all(piece.bottom - piece.top >= 200 for piece in pieces)
    assert all(left.bottom == right.top for left, right in zip(pieces, pieces[1:], strict=False))


def test_segmentation_never_calls_random_entry_points(monkeypatch):
    segmentation_module = _require_segmentation()
    strips, inputs = _fixture_inputs(segmentation_module)
    builder = segmentation_module.build_complete_coverage_map

    def fail_if_random_called(*_args, **_kwargs):
        raise AssertionError("random_entry_called: segmentation sampled source")

    for name in (
        "choice",
        "choices",
        "sample",
        "randint",
        "randrange",
        "shuffle",
        "random",
    ):
        monkeypatch.setattr(random, name, fail_if_random_called)

    coverage = builder(inputs, segmentation_version="task3-v1")
    assert coverage.source_asset_ids == tuple(strip.source_asset_id for strip in strips)
