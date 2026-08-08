"""RED tests for deterministic panel-to-claim evidence reconciliation."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest


def _load_fixture_module():
    fixture_path = Path(__file__).parent / "fixtures" / "vision_coverage.py"
    spec = importlib.util.spec_from_file_location(
        "vision_coverage_fixture_reconciliation",
        fixture_path,
    )
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


def _chain(segmentation_module):
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
    coverage = segmentation_module.build_complete_coverage_map(
        inputs,
        segmentation_version="task3-v1",
    )
    canonical = [
        region for region in coverage.regions if region.region_class == "canonical_panel"
    ]
    panel_regions = [
        {
            "panel_id": f"panel-{index}",
            "source_asset_id": region.source_asset_id,
            "source_order": index,
            "bounds": tuple(region.bounds),
        }
        for index, region in enumerate(canonical)
    ]
    observations = [
        {"observation_id": f"observation-{index}", "panel_id": panel["panel_id"]}
        for index, panel in enumerate(panel_regions)
    ]
    chunks = [
        {
            "chunk_id": "chunk-0",
            "observation_ids": [observation["observation_id"] for observation in observations],
        }
    ]
    claims = [
        {
            "claim_id": "claim-0",
            "evidence_panel_ids": [panel["panel_id"] for panel in panel_regions],
        }
    ]
    return coverage, panel_regions, observations, chunks, claims


def test_clean_reconciliation_chain_is_accepted():
    segmentation_module = _require_segmentation()
    coverage, panels, observations, chunks, claims = _chain(segmentation_module)
    reconcile = getattr(segmentation_module, "reconcile_coverage_chain", None)
    assert reconcile is not None, "coverage_boundary_missing: reconcile_coverage_chain"

    accepted, errors = reconcile(coverage, panels, observations, chunks, claims)
    assert accepted is True
    assert errors == ()


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    (
        ("missing_asset", "chain.source_asset_missing"),
        ("missing_panel", "chain.observation_panel_missing"),
        ("duplicate_order", "chain.duplicate_source_order"),
        ("out_of_order", "chain.out_of_order_source_order"),
        ("orphan_observation", "chain.orphan_observation"),
        ("chunk_observation", "chain.chunk_observation_missing"),
        ("claim_evidence", "chain.claim_panel_evidence_missing"),
    ),
)
def test_reconciliation_rejects_each_broken_link(failure, expected_code):
    segmentation_module = _require_segmentation()
    coverage, panels, observations, chunks, claims = _chain(segmentation_module)

    if failure == "missing_asset":
        panels[0]["source_asset_id"] = "missing-source"
    elif failure == "missing_panel":
        observations[0]["panel_id"] = "missing-panel"
    elif failure == "duplicate_order":
        panels[-1]["source_order"] = panels[0]["source_order"]
    elif failure == "out_of_order":
        panels.reverse()
    elif failure == "orphan_observation":
        observations[0]["observation_id"] = "orphan-observation"
        observations[0]["panel_id"] = "missing-panel"
    elif failure == "chunk_observation":
        chunks[0]["observation_ids"] = ["missing-observation"]
    elif failure == "claim_evidence":
        claims[0]["evidence_panel_ids"] = []

    reconcile = segmentation_module.reconcile_coverage_chain
    accepted, errors = reconcile(coverage, panels, observations, chunks, claims)
    assert accepted is False
    assert any(error.startswith(expected_code) for error in errors)


def test_reconciliation_returns_stable_sorted_errors():
    segmentation_module = _require_segmentation()
    coverage, panels, observations, chunks, claims = _chain(segmentation_module)
    panels[0]["source_asset_id"] = "missing-source"
    observations[0]["panel_id"] = "missing-panel"
    chunks[0]["observation_ids"] = ["missing-observation"]
    claims[0]["evidence_panel_ids"] = []

    accepted, errors = segmentation_module.reconcile_coverage_chain(
        coverage,
        panels,
        observations,
        chunks,
        claims,
    )
    assert accepted is False
    assert errors == tuple(sorted(errors))
    assert len(errors) >= 4


def test_reconciliation_does_not_mutate_input_records():
    segmentation_module = _require_segmentation()
    coverage, panels, observations, chunks, claims = _chain(segmentation_module)
    original_panels = [dict(panel) for panel in panels]
    original_observations = [dict(observation) for observation in observations]
    segmentation_module.reconcile_coverage_chain(
        coverage,
        panels,
        observations,
        chunks,
        claims,
    )
    assert panels == original_panels
    assert observations == original_observations
