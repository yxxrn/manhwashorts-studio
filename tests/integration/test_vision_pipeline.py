"""RED contract tests for the vision-first analysis pipeline.

These tests intentionally describe the Task 7 boundary before the pipeline
implementation exists.  They use synthetic, rights-safe image data only.
"""

from __future__ import annotations

import hashlib
import importlib
import io
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image


def _pipeline_module():
    module = importlib.import_module("app.services.pipeline")
    assert module is not None
    return module


def _chunk_builder(module):
    builder = getattr(module, "build_observation_chunks", None)
    assert callable(builder), "pipeline_boundary_missing: build_observation_chunks"
    return builder


def _panel_regions(count: int):
    from app.models import PanelRegion

    return tuple(
        PanelRegion(
            story_analysis_id="analysis-fixture",
            source_asset_id=f"asset-{index // 8}",
            source_asset_checksum=f"checksum-{index // 8}",
            original_width=64,
            original_height=64,
            strip_region_id=f"region-{index}",
            panel_id=f"panel-{index}",
            source_order=index,
            bounds_json={"x": 0, "y": index * 2, "width": 64, "height": 2},
            region_class="canonical_panel",
            segmentation_confidence=0.99,
            segmentation_version="vision-coverage-v2",
            coverage_map_hash="map-fixture",
        )
        for index in range(count)
    )


def _chunk_panel_ids(chunks):
    return [[region.panel_id for region in chunk] for chunk in chunks]


def test_build_observation_chunks_has_ordered_two_panel_adjacent_overlap():
    module = _pipeline_module()
    builder = _chunk_builder(module)
    chunks = builder(_panel_regions(25))

    assert _chunk_panel_ids(chunks) == [
        [f"panel-{index}" for index in range(0, 12)],
        [f"panel-{index}" for index in range(10, 22)],
        [f"panel-{index}" for index in range(20, 25)],
    ]
    positions: dict[str, list[int]] = {}
    for chunk_index, chunk in enumerate(chunks):
        for region in chunk:
            positions.setdefault(region.panel_id, []).append(chunk_index)
    assert set(positions) == {f"panel-{index}" for index in range(25)}
    assert all(len(indexes) in {1, 2} for indexes in positions.values())
    assert positions["panel-10"] == [0, 1]
    assert positions["panel-11"] == [0, 1]
    assert positions["panel-20"] == [1, 2]
    assert positions["panel-21"] == [1, 2]
    assert all(
        indexes[1] == indexes[0] + 1
        for indexes in positions.values()
        if len(indexes) == 2
    )


@pytest.mark.parametrize(
    ("chunk_size", "overlap"),
    ((0, 0), (12, -1), (12, 12), (3, 3), (3, 4)),
)
def test_build_observation_chunks_rejects_invalid_size_and_overlap(chunk_size, overlap):
    module = _pipeline_module()
    builder = _chunk_builder(module)
    with pytest.raises((TypeError, ValueError)):
        builder(_panel_regions(4), chunk_size=chunk_size, overlap=overlap)


def test_build_observation_chunks_rejects_duplicate_source_order():
    module = _pipeline_module()
    builder = _chunk_builder(module)
    regions = list(_panel_regions(4))
    regions[1].source_order = regions[0].source_order

    with pytest.raises(ValueError, match="source_order"):
        builder(regions)


def test_build_observation_chunks_rejects_duplicate_panel_id():
    module = _pipeline_module()
    builder = _chunk_builder(module)
    regions = list(_panel_regions(4))
    regions[1].panel_id = regions[0].panel_id

    with pytest.raises(ValueError, match="panel_id"):
        builder(regions)


def test_build_observation_chunks_keeps_small_chapter_in_one_chunk():
    module = _pipeline_module()
    builder = _chunk_builder(module)
    chunks = builder(_panel_regions(12))

    assert len(chunks) == 1
    assert _chunk_panel_ids(chunks) == [[f"panel-{index}" for index in range(12)]]


def test_build_observation_chunks_respects_byte_budget_and_two_panel_overlap():
    module = _pipeline_module()
    builder = _chunk_builder(module)
    panels = _panel_regions(12)
    estimate = 2 * 1024 * 1024
    estimates = {panel.panel_id: estimate for panel in panels}
    limit = 9 * 1024 * 1024

    chunks = builder(
        panels,
        chunk_size=12,
        overlap=2,
        estimated_bytes_by_panel_id=estimates,
        max_estimated_request_bytes=limit,
    )
    assert len(chunks) > 1
    assert all(len(chunk) >= 4 for chunk in chunks[:-1])
    assert all(
        128 * 1024 + sum(estimates[panel.panel_id] for panel in chunk) <= limit
        for chunk in chunks
    )
    assert all(
        [panel.panel_id for panel in left[-2:]] == [panel.panel_id for panel in right[:2]]
        for left, right in zip(chunks, chunks[1:], strict=False)
    )
    positions: dict[str, int] = {}
    for chunk in chunks:
        for panel in chunk:
            positions[panel.panel_id] = positions.get(panel.panel_id, 0) + 1
    assert max(positions.values()) <= 2


def test_build_observation_chunks_rejects_incomplete_byte_estimates():
    module = _pipeline_module()
    builder = _chunk_builder(module)
    panels = _panel_regions(4)
    with pytest.raises(ValueError, match="estimated bytes"):
        builder(panels, estimated_bytes_by_panel_id={"panel-0": 1024})


def _png_bytes(width: int, height: int, seed: int) -> bytes:
    image = Image.new("RGB", (width, height))
    for y in range(height):
        color = ((40 + seed + y) % 180, (70 + seed * 2 + y) % 180, 110)
        for x in range(width):
            image.putpixel((x, y), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_provider_transport_bounds_overview_and_preserves_tall_detail_windows():
    module = _pipeline_module()
    image = Image.new("RGB", (800, 5000), (32, 64, 96))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    canonical = buffer.getvalue()

    overview, mime_type = module._vision_provider_payload(canonical, "image/png")
    assert mime_type == "image/jpeg"
    with Image.open(io.BytesIO(overview)) as decoded:
        assert decoded.width <= 384
        assert decoded.height <= 576

    windows = module._vision_analysis_windows(canonical)
    assert 1 < len(windows) <= module.ANALYSIS_WINDOW_MAX_COUNT
    assert windows[0]["y0"] == 0
    assert windows[0]["overlap_above"] == 0
    assert windows[-1]["y1"] == 5000
    assert windows[-1]["overlap_below"] == 0
    for left, right in zip(windows, windows[1:], strict=False):
        expected_overlap = left["y1"] - right["y0"]
        assert expected_overlap > 0
        assert left["overlap_below"] == expected_overlap
        assert right["overlap_above"] == expected_overlap
    transport = {
        "payload": overview,
        "analysis_window_version": module.ANALYSIS_WINDOW_CONTRACT_VERSION,
        "analysis_window_source_size": [800, 5000],
        "analysis_windows": windows,
    }
    assert module._vision_transport_estimated_bytes(transport) < 4 * 1024 * 1024
    identity = module._vision_observation_panel_identity(transport)
    assert identity is not None
    assert len(identity["payload_sha256"]) == 64
    assert len(identity["analysis_windows"]) == len(windows)
    assert all(len(item["payload_sha256"]) == 64 for item in identity["analysis_windows"])


def test_provider_transport_upscales_tiny_overview_to_provider_safe_minimum():
    module = _pipeline_module()
    image = Image.new("RGB", (800, 9), (220, 220, 220))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    overview, mime_type = module._vision_provider_payload(buffer.getvalue(), "image/png")

    assert mime_type == "image/jpeg"
    with Image.open(io.BytesIO(overview)) as decoded:
        assert decoded.width <= module._VISION_PROVIDER_OVERVIEW_MAX_SIZE[0]
        assert decoded.height <= module._VISION_PROVIDER_OVERVIEW_MAX_SIZE[1]
        assert decoded.width >= module._VISION_PROVIDER_OVERVIEW_MIN_SIZE[0]
        assert decoded.height >= module._VISION_PROVIDER_OVERVIEW_MIN_SIZE[1]


def _seed_vision_project(
    db,
    *,
    standalone_count: int = 3,
    missing_slice: bool = False,
    storage_failure: bool = False,
    text_only: bool = False,
):
    from app.constants import AssetType, LicenseType, RightsStatus
    from app.models import Project, SourceAsset, User, Workspace
    from app.security import hash_password
    from app.services import storage

    user = User(
        email="vision-pipeline@example.com",
        name="Vision Fixture",
        password_hash=hash_password("pass12345"),
    )
    db.add(user)
    db.flush()
    workspace = Workspace(owner_id=user.id, name="Vision Fixture Workspace")
    db.add(workspace)
    db.flush()
    project = Project(
        workspace_id=workspace.id,
        title="Vision Pipeline Fixture",
        manhwa_title="Synthetic Chapter",
        chapter="1",
        language="en",
    )
    db.add(project)
    db.flush()

    if text_only:
        db.add(
            SourceAsset(
                project_id=project.id,
                type=AssetType.TEXT,
                original_filename="recap.txt",
                extracted_text="A text-only recap must not enter the vision-first path.",
                mime_type="text/plain",
                rights_owner="fixture",
                license_type=LicenseType.OWNED,
                rights_status=RightsStatus.DECLARED,
                order_index=0,
            )
        )
        db.flush()
        return project.id, []

    specifications = []
    for index in range(standalone_count):
        width, height = 8, 4
        specifications.append(
            {
                "filename": f"asset-{index}.png",
                "payload": _png_bytes(width, height, index),
                "width": width,
                "height": height,
                "original_checksum": f"original-asset-{index}",
                "original_width": width,
                "original_height": height,
                "bounds": (0, 0, width, height),
                "strip_order": index,
                "region_order": 0,
            }
        )
    slice_specs = [
        {
            "filename": "long-strip-0.png",
            "payload": _png_bytes(8, 3, 90),
            "width": 8,
            "height": 3,
            "original_checksum": "original-long-strip",
            "original_width": 8,
            "original_height": 6,
            "bounds": (0, 0, 8, 3),
            "strip_order": standalone_count,
            "region_order": 0,
        },
        {
            "filename": "long-strip-1.png",
            "payload": _png_bytes(8, 3, 100),
            "width": 8,
            "height": 3,
            "original_checksum": "original-long-strip",
            "original_width": 8,
            "original_height": 6,
            "bounds": (0, 3, 8, 6),
            "strip_order": standalone_count,
            "region_order": 1,
        },
    ]
    if missing_slice:
        slice_specs.pop()
    specifications.extend(slice_specs)

    assets = []
    for order_index, specification in enumerate(specifications):
        payload = specification["payload"]
        if storage_failure and order_index == 0:
            stored_key = "projects/missing/vision-fixture.png"
            size_bytes = 0
            checksum = hashlib.sha256(b"missing").hexdigest()
        else:
            stored = storage.put_bytes(
                f"projects/{project.id}/images",
                specification["filename"],
                payload,
            )
            stored_key = stored.storage_key
            size_bytes = stored.size_bytes
            checksum = stored.checksum
        x0, y0, x1, y1 = (
            specification["bounds"][0],
            specification["bounds"][1],
            specification["bounds"][2],
            specification["bounds"][3],
        )
        asset = SourceAsset(
            project_id=project.id,
            type=AssetType.IMAGE,
            original_filename=specification["filename"],
            storage_key=stored_key,
            mime_type="image/png",
            size_bytes=size_bytes,
            checksum=checksum,
            width=specification["width"],
            height=specification["height"],
            original_checksum=specification["original_checksum"],
            original_width=specification["original_width"],
            original_height=specification["original_height"],
            source_bounds_json={
                "x": x0,
                "y": y0,
                "width": x1 - x0,
                "height": y1 - y0,
            },
            strip_order=specification["strip_order"],
            region_order=specification["region_order"],
            trim_classification="preserved",
            rights_owner="fixture",
            license_type=LicenseType.OWNED,
            rights_status=RightsStatus.DECLARED,
            order_index=order_index,
        )
        db.add(asset)
        assets.append(asset)
    db.flush()
    return project.id, assets


def _source_asset_inputs(assets):
    from app.services import storage
    from app.services.segmentation import SourceAssetInput

    inputs = []
    for asset in assets:
        bounds = asset.source_bounds_json
        rectangle = (
            bounds["x"],
            bounds["y"],
            bounds["x"] + bounds["width"],
            bounds["y"] + bounds["height"],
        )
        inputs.append(
            SourceAssetInput(
                source_asset_id=asset.id,
                original_checksum=asset.original_checksum or asset.checksum,
                original_width=asset.original_width,
                original_height=asset.original_height,
                source_bounds=rectangle,
                strip_order=asset.strip_order,
                region_order=asset.region_order,
                payload=storage.read_bytes(asset.storage_key),
                decoded_width=asset.width,
                decoded_height=asset.height,
            )
        )
    return tuple(inputs)


def test_real_v2_fixture_reconciles_assets_and_global_slice_bounds(db):
    from app.services.segmentation import SEGMENTATION_VERSION, build_complete_coverage_map

    project_id, assets = _seed_vision_project(db, standalone_count=3)
    assert project_id
    coverage = build_complete_coverage_map(
        _source_asset_inputs(assets), segmentation_version=SEGMENTATION_VERSION
    )

    assert coverage.version == "vision-coverage-v2"
    assert coverage.source_content_coverage_ratio == 1.0
    assert coverage.unresolved_material_area == 0
    assert coverage.reconciliation_errors == ()
    assert coverage.source_asset_ids == tuple(asset.id for asset in assets)
    assert coverage.map_sha256 == build_complete_coverage_map(
        _source_asset_inputs(assets), segmentation_version=SEGMENTATION_VERSION
    ).map_sha256
    long_strip_assets = assets[-2:]
    assert [asset.source_bounds_json["y"] for asset in long_strip_assets] == [0, 3]
    assert [tile.y0 for tile in coverage.tiles if tile.source_asset_id in {a.id for a in long_strip_assets}] == [0, 3]


def _panel_mapping(panel: Any) -> dict[str, Any]:
    if isinstance(panel, Mapping):
        return dict(panel)
    bounds = getattr(panel, "bounds_json", {}) or {}
    return {
        "panel_id": getattr(panel, "panel_id", ""),
        "source_asset_id": getattr(panel, "source_asset_id", ""),
        "strip_region_id": getattr(panel, "strip_region_id", ""),
        "source_order": getattr(panel, "source_order", 0),
        "region_bounds": bounds,
        "coverage_map_version": getattr(panel, "segmentation_version", ""),
        "coverage_map_hash": getattr(panel, "coverage_map_hash", ""),
    }


def _semantic_observation(panel: Any) -> dict[str, Any]:
    values = _panel_mapping(panel)
    panel_id = values["panel_id"]
    return {
        "panel_id": panel_id,
        "visible_facts": [f"Synthetic visible fact for {panel_id}"],
        "dialogue_or_ocr": [],
        "inferences": [],
        "uncertainties": [],
        "entities": [],
        "state_changes": [],
        "causal_links": [],
        "evidence_refs": [panel_id],
    }


def _canonical_observation(panel: Any, source_index: int) -> dict[str, Any]:
    values = _panel_mapping(panel)
    bounds = values.get("region_bounds") or values.get("bounds_json") or {
        "x": 0,
        "y": 0,
        "width": 1,
        "height": 1,
    }
    return {
        "panel_id": values["panel_id"],
        "source_asset_id": values.get("source_asset_id", "asset-fixture"),
        "strip_region_id": values.get("strip_region_id", values["panel_id"]),
        "source_index": source_index,
        "region_bounds": dict(bounds),
        "coverage_map_version": values.get("coverage_map_version", "vision-coverage-v2"),
        "coverage_map_hash": values.get("coverage_map_hash", "map-fixture"),
        "visible_facts": list(values.get("visible_facts", ["A visible synthetic fact"])),
        "dialogue_or_ocr": list(values.get("dialogue_or_ocr", [])),
        "inferences": list(values.get("inferences", [])),
        "uncertainties": list(values.get("uncertainties", [])),
        "evidence_refs": [values["panel_id"]],
    }


def _request_chunk_mapping(chunk: Any, index: int) -> dict[str, Any]:
    if isinstance(chunk, Mapping):
        return {
            "chunk_id": chunk.get("chunk_id", f"chunk-{index}"),
            "panel_ids": list(chunk.get("panel_ids", [])),
        }
    return {
        "chunk_id": f"chunk-{index}",
        "panel_ids": [getattr(panel, "panel_id", "") for panel in chunk],
    }


def _valid_synthesis_output(request) -> dict[str, Any]:
    expected = tuple(request.expected_panel_ids)
    observations = [
        _canonical_observation(panel, index)
        for index, panel in enumerate(request.ordered_observations)
    ]
    manifest = dict(request.coverage_manifest)
    manifest.update(
        {
            "total_panels": len(expected),
            "processed_panels": len(expected),
            "panel_ids": list(expected),
            "source_content_coverage_ratio": 1.0,
            "unresolved_material_area": 0,
            "material_unresolved_regions": [],
            "reconciliation_complete": True,
        }
    )
    chunks = [
        _request_chunk_mapping(chunk, index)
        for index, chunk in enumerate(request.chunks)
    ]
    claim_id = "claim-fixture"
    return {
        "observations": observations,
        "continuity_ledger": {
            "chunks": chunks,
            "entities": [
                {
                    "entity_id": "entity-fixture",
                    "canonical_name": "Synthetic witness",
                    "aliases": [],
                    "panel_ids": list(expected),
                }
            ],
            "motives": [],
            "state_changes": [],
            "causal_links": [],
            "reconciled_after_final_chunk": True,
        },
        "evidence_graph": {
            "claims": [
                {
                    "claim_id": claim_id,
                    "claim_type": "fact",
                    "text": "The synthetic witness appears in the chapter.",
                    "qualification": "Shown directly in the supplied panels.",
                    "evidence_panel_ids": list(expected),
                }
            ]
        },
        "coverage_manifest": manifest,
        "narrative_outline": {
            "story_spine": {
                "who_wants_what": "The witness wants the truth.",
                "obstacle": "The evidence is incomplete at first.",
                "decision": "The witness follows the visible clue.",
                "consequence": "The clue changes the situation.",
                "changed_stakes": "The next choice now carries risk.",
                "unresolved_question": "What will the final clue reveal?",
            }
        },
        "script_passages": [
            {
                "passage_id": "passage-fixture-hook",
                "editorial_role": "hook",
                "text": "A visible clue appears before the witness can decide whether to follow it in the dark.",
                "claim_ids": [claim_id],
                "evidence_panel_ids": list(expected),
            },
            {
                "passage_id": "passage-fixture-setup",
                "editorial_role": "setup",
                "text": "The witness studies the clue while the surrounding panels show a path toward an uncertain destination and leave the witness with one direction.",
                "claim_ids": [claim_id],
                "evidence_panel_ids": list(expected),
            },
            {
                "passage_id": "passage-fixture-escalation",
                "editorial_role": "escalation",
                "text": "That movement raises the stakes because the clue points forward, yet the witness still cannot see who arranged it or what waits beyond the next panel, before the trail can disappear entirely.",
                "claim_ids": [claim_id],
                "evidence_panel_ids": list(expected),
            },
            {
                "passage_id": "passage-fixture-insight",
                "editorial_role": "editorial_insight",
                "text": "The detail matters because a quiet image can change the witness's safest choice without warning while the clue remains visible.",
                "claim_ids": [claim_id],
                "evidence_panel_ids": list(expected),
            },
            {
                "passage_id": "passage-fixture-payoff",
                "editorial_role": "payoff_open_loop",
                "text": "Who placed the clue there, and what will the next panel reveal?",
                "claim_ids": [claim_id],
                "evidence_panel_ids": list(expected),
            },
        ],
    }


class _ProviderSpy:
    def __init__(self, mode: str = "valid") -> None:
        self.mode = mode
        self.observe_requests = []
        self.observed_panel_ids = []
        self.synthesis_requests = []

    def capability(self):
        from app.services.vision_adapter import VisionCapabilityReport

        return VisionCapabilityReport(
            provider_type="openai_compatible",
            provider_name="fixture-vision",
            model="fixture-model",
            image_input=True,
            structured_json=True,
            available=True,
            blocking_reason=None,
        )

    def observe(self, request):
        self.observe_requests.append(request)
        if request.visual_instruction_version is None:
            rows = [_semantic_observation(panel) for panel in request.panels]
        else:
            rows = [_visual_row(panel) for panel in request.panels]
        self.observed_panel_ids.extend(row["panel_id"] for row in rows)
        if self.mode == "omit_observation" and rows:
            return rows[:-1]
        return rows

    def synthesize(self, request):
        self.synthesis_requests.append(request)
        return _valid_synthesis_output(request)


def _install_provider(monkeypatch, provider):
    module = _pipeline_module()
    report = provider.capability()
    monkeypatch.setattr(
        module.resolver_svc,
        "resolve_vision",
        lambda db, workspace_id: (provider, report),
        raising=True,
    )
    monkeypatch.setattr(
        module.resolver_svc,
        "resolve_analyzer",
        lambda *args, **kwargs: pytest.fail("legacy text analyzer fallback was called"),
        raising=True,
    )
    return module


def test_provider_spy_observes_all_ordered_panels_then_synthesizes_once(db, monkeypatch):
    from sqlalchemy import select

    from app.models import PanelRegion, StoryAnalysis
    from app.services.analyzer_contract import load_analyzer_instruction

    project_id, assets = _seed_vision_project(db, standalone_count=13)
    provider = _ProviderSpy()
    module = _install_provider(monkeypatch, provider)

    row = module.run_analysis(db, project_id)

    assert row.state == "RECONCILED"
    expected = [
        region.panel_id
        for region in db.scalars(
            select(PanelRegion)
            .where(PanelRegion.story_analysis_id == row.id)
            .order_by(PanelRegion.source_order)
        )
    ]
    assert expected
    version, digest, prompt = load_analyzer_instruction()
    assert row.instruction_version == version
    assert row.instruction_sha256 == digest
    assert provider.synthesis_requests
    synthesis_count_ok = provider.synthesis_requests == [provider.synthesis_requests[0]]
    assert synthesis_count_ok
    synthesis_request = provider.synthesis_requests[0]
    assert synthesis_request.instruction_version == version
    assert synthesis_request.instruction_sha256 == digest
    assert synthesis_request.instruction_text == prompt
    assert synthesis_request.expected_panel_ids == tuple(expected)
    assert synthesis_request.coverage_manifest["source_content_coverage_ratio"] == 1.0
    assert synthesis_request.coverage_manifest["unresolved_material_area"] == 0
    assert [
        panel["panel_id"] for panel in synthesis_request.ordered_observations
    ] == expected
    assert all(
        {
            "panel_id",
            "source_asset_id",
            "strip_region_id",
            "source_index",
            "region_bounds",
            "coverage_map_version",
            "coverage_map_hash",
            "visible_facts",
            "dialogue_or_ocr",
            "inferences",
            "uncertainties",
            "evidence_refs",
        }
        == set(panel)
        for panel in synthesis_request.ordered_observations
    )
    assert all("data:image/" not in repr(panel) for panel in synthesis_request.ordered_observations)
    assert provider.observe_requests
    request_contexts = {
        (
            request.analysis_run_id,
            request.instruction_version,
            request.instruction_sha256,
        )
        for request in provider.observe_requests
    }
    assert len(request_contexts) == 1
    assert next(iter(request_contexts))[1:] == (version, digest)
    assert [request.chunk_index for request in provider.observe_requests] == list(
        range(len(provider.observe_requests))
    )
    for request in provider.observe_requests:
        for panel in request.panels:
            assert isinstance(panel["payload"], bytes)
            assert panel["payload"]
            assert [
                key for key, value in panel.items() if isinstance(value, bytes)
            ] == ["payload"]
            assert isinstance(panel["mime_type"], str)
            assert panel["mime_type"].lower().startswith("image/")
            bounds = panel["region_bounds"]
            assert set(bounds) == {"x", "y", "width", "height"}
            assert bounds["width"] > 0
            assert bounds["height"] > 0
            with Image.open(io.BytesIO(panel["payload"])) as decoded:
                assert decoded.width <= module._VISION_PROVIDER_OVERVIEW_MAX_SIZE[0]
                assert decoded.height <= module._VISION_PROVIDER_OVERVIEW_MAX_SIZE[1]
                assert decoded.width >= module._VISION_PROVIDER_OVERVIEW_MIN_SIZE[0]
                assert decoded.height >= module._VISION_PROVIDER_OVERVIEW_MIN_SIZE[1]
            if bounds["y"] > 0:
                assert panel["source_asset_id"] == assets[-1].id
                assert bounds["y"] == 3
                assert bounds["height"] == assets[-1].height
                assert bounds["y"] + bounds["height"] == assets[-1].original_height
    observed_chunks = [
        [panel["panel_id"] for panel in request.panels]
        for request in provider.observe_requests
    ]
    assert provider.observed_panel_ids.count(expected[0]) == 1
    assert set(provider.observed_panel_ids) == set(expected)
    assert all(
        set(left[-2:]) == set(right[:2])
        for left, right in zip(observed_chunks, observed_chunks[1:], strict=False)
    )
    assert all(
        panel_id not in sum(observed_chunks[index + 2 :], [])
        for index, chunk in enumerate(observed_chunks[:-2])
        for panel_id in set(chunk[-2:])
    )
    manifest = row.coverage_manifest_json
    assert manifest["total_assets"] == len(assets)
    assert manifest["original_source_space_area"] > 0
    assert manifest["accounted_source_space_area"] == manifest["original_source_space_area"]
    assert manifest["total_canonical_panels"] == len(expected)
    assert manifest["persisted_canonical_panels"] == len(expected)
    assert manifest["processed_panels"] == len(expected)
    assert manifest["duplicate_overlap_observations"] == len(provider.observed_panel_ids) - len(expected)
    assert manifest["unreadable_low_confidence_panels"] == []
    assert manifest["ordering_uncertainties"] == []
    assert manifest["character_ambiguities"] == []
    assert manifest["tile_ranges"]
    assert manifest["tile_overlap"]
    assert isinstance(manifest["coverage_map_hash"], str)
    assert manifest["coverage_map_hash"]
    assert row.reconciliation_json["coverage_map_hash"] == manifest["coverage_map_hash"]
    regions = db.scalars(
        select(PanelRegion)
        .where(PanelRegion.story_analysis_id == row.id)
        .order_by(PanelRegion.source_order)
    ).all()
    assert [region.source_asset_id for region in regions] == [asset.id for asset in assets]
    assert all(region.coverage_map_hash == manifest["coverage_map_hash"] for region in regions)
    assert all(region.bounds_json["width"] > 0 and region.bounds_json["height"] > 0 for region in regions)
    assert manifest["claim_to_panel_refs"]
    assert row.evidence_graph_json["script_passages"]
    assert all(
        claim["evidence_panel_ids"]
        for claim in row.evidence_graph_json["claims"]
    )
    assert row.story_spine_json["who_wants_what"]
    assert db.scalars(select(StoryAnalysis).where(StoryAnalysis.id == row.id)).one().state == "RECONCILED"


def test_observe_stage_omission_blocks_before_synthesis(db, monkeypatch):
    from sqlalchemy import select

    from app.models import ScriptVersion

    project_id, _ = _seed_vision_project(db, standalone_count=3)
    provider = _ProviderSpy(mode="omit_observation")
    module = _install_provider(monkeypatch, provider)

    row = module.run_analysis(db, project_id)

    assert row.state == "BLOCKED"
    assert row.blocking_reasons_json["codes"] == ["analysis_observation_missing"]
    assert provider.observe_requests
    assert provider.synthesis_requests == []
    assert db.scalars(
        select(ScriptVersion).where(ScriptVersion.project_id == project_id)
    ).all() == []


def _visual_loader():
    scoring = importlib.import_module("app.services.visual_scoring")
    loader = getattr(scoring, "load_visual_evidence_instruction", None)
    assert callable(loader), "visual_instruction_loader_missing"
    return scoring, loader()


def _visual_sidecar(panel_id: str, asset_id: str, source_order: int) -> dict[str, Any]:
    if source_order % 3 == 0:
        return {
            "balloon_mask_status": "known_nonempty",
            "balloon_regions": [
                {
                    "region_id": f"balloon-{panel_id}",
                    "kind": "speech_balloon",
                    "normalized_bbox": [0.1, 0.1, 0.4, 0.3],
                    "normalized_polygon": [],
                    "confidence": 0.9,
                    "evidence_source": "vision_geometry_v1",
                    "mask_status": "known_nonempty",
                }
            ],
            "protected_regions": [],
            "mask_confidence": 0.9,
            "evidence_source": "vision_geometry_v1",
            "mask_reason": "provider supplied normalized geometry",
        }
    if source_order % 3 == 1:
        return {
            "balloon_mask_status": "known_empty",
            "balloon_regions": [],
            "protected_regions": [],
            "mask_confidence": 0.95,
            "evidence_source": "vision_geometry_v1",
            "mask_reason": "provider affirmatively found no speech region",
        }
    return {
        "balloon_mask_status": "unknown",
        "balloon_regions": [],
        "protected_regions": [],
        "mask_confidence": 0.0,
        "evidence_source": "vision_geometry_unavailable",
        "mask_reason": "geometry could not be determined reliably",
    }


def _visual_row(panel: Mapping[str, Any], *, mode: str = "valid") -> dict[str, Any]:
    row = _semantic_observation(panel)
    sidecar = _visual_sidecar(
        panel["panel_id"], panel["source_asset_id"], panel["source_order"]
    )
    if mode == "missing":
        return row
    if mode == "foreign":
        sidecar["panel_id"] = "panel-foreign"
    elif mode == "malformed":
        if sidecar["balloon_regions"]:
            sidecar["balloon_regions"][0]["normalized_bbox"] = [0.1, 0.2]
    elif mode == "provider_hash":
        sidecar["evidence_hash"] = "provider-supplied"
    row["visual_evidence"] = {
        **sidecar,
        "panel_id": sidecar.get("panel_id", panel["panel_id"]),
        "source_asset_id": panel["source_asset_id"],
        "source_order": panel["source_order"],
    }
    return row


class _VisualObservationSpy:
    def __init__(self, mode: str = "valid") -> None:
        self.mode = mode
        self.requests = []

    def observe(self, request):
        self.requests.append(request)
        return [
            _visual_row(panel, mode=self.mode)
            for panel in request.panels
        ]


def _run_visual_observation(module, provider, panels, transports, version, digest):
    chunks = module.build_observation_chunks(panels)
    return module._observe_chunks(
        provider,
        chunks,
        transports,
        analysis_run_id="run-task2-visual",
        instruction_version="vision-first-story-analyzer-v1",
        instruction_sha256="a" * 64,
        visual_instruction_version=version,
        visual_instruction_sha256=digest,
    )


class _EmptyFactsPersistentObservationSpy:
    model_id = "fixture-grounded-facts-model"
    endpoint = "https://fixture.invalid/v1"

    def __init__(self) -> None:
        self.requests = []

    def observe(self, request):
        self.requests.append(request)
        rows = [_visual_row(panel) for panel in request.panels]
        for row in rows:
            row["visible_facts"] = []
        return rows


def test_visual_observation_repairs_empty_facts_and_reuses_repaired_cache(tmp_path, monkeypatch):
    module = _pipeline_module()
    monkeypatch.setattr(module.settings, "data_dir", tmp_path)
    _, (version, digest, _) = _visual_loader()
    panels = _panel_regions(3)
    transports = {
        panel.panel_id: {
            "panel_id": panel.panel_id,
            "source_asset_id": panel.source_asset_id,
            "source_order": panel.source_order,
            "mime_type": "image/png",
            "payload": b"grounded-facts-" + panel.panel_id.encode(),
        }
        for panel in panels
    }
    first = _EmptyFactsPersistentObservationSpy()
    semantic, _, _ = _run_visual_observation(
        module, first, panels, transports, version, digest
    )
    assert first.requests
    assert all(row["visible_facts"] for row in semantic.values())

    second = _EmptyFactsPersistentObservationSpy()
    reused, _, _ = _run_visual_observation(
        module, second, panels, transports, version, digest
    )
    assert second.requests == []
    assert reused == semantic


class _NondeterministicOverlapSpy:
    def __init__(self) -> None:
        self.requests = []

    def observe(self, request):
        self.requests.append(request)
        rows = []
        for panel in request.panels:
            row = _visual_row(panel)
            row["visible_facts"] = [
                f"wording-{request.chunk_index}-{panel['panel_id']}"
            ]
            rows.append(row)
        return rows


def test_observe_chunks_keeps_first_overlap_observation_when_wording_drifts():
    module = _pipeline_module()
    _, (version, digest, _) = _visual_loader()
    panels = _panel_regions(14)
    transports = {
        panel.panel_id: {
            "panel_id": panel.panel_id,
            "source_asset_id": panel.source_asset_id,
            "source_order": panel.source_order,
            "mime_type": "image/png",
            "payload": b"synthetic-image-payload",
        }
        for panel in panels
    }
    provider = _NondeterministicOverlapSpy()
    semantic, ledger, first_chunk = _run_visual_observation(
        module, provider, panels, transports, version, digest
    )

    assert len(provider.requests) == 2
    assert ledger[0]["overlap_with_next"] == ["panel-10", "panel-11"]
    assert ledger[1]["overlap_with_previous"] == ["panel-10", "panel-11"]
    assert semantic["panel-10"]["visible_facts"] == ["wording-0-panel-10"]
    assert semantic["panel-11"]["visible_facts"] == ["wording-0-panel-11"]
    assert first_chunk["panel-10"] == 0
    assert first_chunk["panel-12"] == 1


def test_observe_chunks_still_rejects_nonadjacent_duplicate_panel():
    module = _pipeline_module()
    _, (version, digest, _) = _visual_loader()
    panels = _panel_regions(5)
    transports = {
        panel.panel_id: {
            "panel_id": panel.panel_id,
            "source_asset_id": panel.source_asset_id,
            "source_order": panel.source_order,
            "mime_type": "image/png",
            "payload": b"synthetic-image-payload",
        }
        for panel in panels
    }
    chunks = ((panels[0], panels[1]), (panels[2], panels[3]), (panels[0], panels[4]))
    provider = _VisualObservationSpy()
    with pytest.raises(Exception) as caught:
        module._observe_chunks(
            provider, chunks, transports, analysis_run_id="run-nonadjacent-overlap",
            instruction_version="vision-first-story-analyzer-v1",
            instruction_sha256="a" * 64, visual_instruction_version=version,
            visual_instruction_sha256=digest,
        )
    assert getattr(caught.value, "code", None) == "analysis_observation_missing"
    assert getattr(caught.value, "finding", {}).get("stage") == "observation_overlap"


class _RetryablePersistentObservationSpy:
    model_id = "fixture-cache-model"
    endpoint = "https://fixture.invalid/v1"

    def __init__(self, *, fail_once_chunk: int | None = None) -> None:
        self.fail_once_chunk = fail_once_chunk
        self.attempted_chunks: list[int] = []
        self._failed = False

    def observe(self, request):
        from app.services.vision_adapter import VisionProviderRequestFailed

        self.attempted_chunks.append(request.chunk_index)
        if request.chunk_index == self.fail_once_chunk and not self._failed:
            self._failed = True
            raise VisionProviderRequestFailed(retryable=True, transport_subtype="connect")
        return [_visual_row(panel) for panel in request.panels]


def test_observe_chunks_retries_failed_chunk_and_reuses_validated_cache(tmp_path, monkeypatch):
    module = _pipeline_module()
    monkeypatch.setattr(module.settings, "data_dir", tmp_path)
    _, (version, digest, _) = _visual_loader()
    panels = _panel_regions(14)
    transports = {
        panel.panel_id: {
            "panel_id": panel.panel_id,
            "source_asset_id": panel.source_asset_id,
            "source_order": panel.source_order,
            "mime_type": "image/png",
            "payload": b"cache-test-image-" + panel.panel_id.encode(),
        }
        for panel in panels
    }
    first = _RetryablePersistentObservationSpy(fail_once_chunk=1)
    semantic, _, _ = _run_visual_observation(
        module, first, panels, transports, version, digest
    )
    assert len(semantic) == 14
    assert first.attempted_chunks == [0, 1, 1]

    cache_files = sorted((tmp_path / "vision-observation-cache").glob("*.json"))
    assert len(cache_files) == 2
    assert all('"payload"' not in path.read_text(encoding="utf-8") for path in cache_files)

    second = _RetryablePersistentObservationSpy()
    reused, _, _ = _run_visual_observation(
        module, second, panels, transports, version, digest
    )
    assert second.attempted_chunks == []
    assert reused == semantic


class _TransientInvalidPersistentObservationSpy(_RetryablePersistentObservationSpy):
    def __init__(self, *, invalid_once_chunk: int) -> None:
        super().__init__()
        self.invalid_once_chunk = invalid_once_chunk

    def observe(self, request):
        from app.services.vision_adapter import VisionResponseInvalid

        self.attempted_chunks.append(request.chunk_index)
        if request.chunk_index == self.invalid_once_chunk and not self._failed:
            self._failed = True
            raise VisionResponseInvalid(validation_subtype="structured_json")
        return [_visual_row(panel) for panel in request.panels]


def test_observe_chunks_retries_transient_invalid_response_and_caches_valid_result(tmp_path, monkeypatch):
    module = _pipeline_module()
    monkeypatch.setattr(module.settings, "data_dir", tmp_path)
    _, (version, digest, _) = _visual_loader()
    panels = _panel_regions(14)
    transports = {
        panel.panel_id: {
            "panel_id": panel.panel_id,
            "source_asset_id": panel.source_asset_id,
            "source_order": panel.source_order,
            "mime_type": "image/png",
            "payload": b"invalid-retry-image-" + panel.panel_id.encode(),
        }
        for panel in panels
    }
    first = _TransientInvalidPersistentObservationSpy(invalid_once_chunk=1)
    semantic, _, _ = _run_visual_observation(
        module, first, panels, transports, version, digest
    )
    assert len(semantic) == 14
    assert first.attempted_chunks == [0, 1, 1]

    second = _TransientInvalidPersistentObservationSpy(invalid_once_chunk=-1)
    reused, _, _ = _run_visual_observation(
        module, second, panels, transports, version, digest
    )
    assert second.attempted_chunks == []
    assert reused == semantic


class _AlwaysInvalidObservationSpy(_RetryablePersistentObservationSpy):
    def observe(self, request):
        from app.services.vision_adapter import VisionResponseInvalid

        self.attempted_chunks.append(request.chunk_index)
        raise VisionResponseInvalid(validation_subtype="structured_json")


def test_observe_chunks_persistent_invalid_response_still_fails_closed(tmp_path, monkeypatch):
    module = _pipeline_module()
    monkeypatch.setattr(module.settings, "data_dir", tmp_path)
    _, (version, digest, _) = _visual_loader()
    panels = _panel_regions(3)
    transports = {
        panel.panel_id: {
            "panel_id": panel.panel_id,
            "source_asset_id": panel.source_asset_id,
            "source_order": panel.source_order,
            "mime_type": "image/png",
            "payload": b"always-invalid-image-" + panel.panel_id.encode(),
        }
        for panel in panels
    }
    provider = _AlwaysInvalidObservationSpy()
    with pytest.raises(Exception) as caught:
        _run_visual_observation(module, provider, panels, transports, version, digest)
    assert getattr(caught.value, "code", None) == "vision_response_invalid"
    assert getattr(caught.value, "finding", {}).get("attempts") == module._VISION_OBSERVATION_MAX_ATTEMPTS
    assert provider.attempted_chunks == [0] * module._VISION_OBSERVATION_MAX_ATTEMPTS


def test_observe_chunks_activates_visual_prompt_and_locally_hashes_sidecars():
    module = _pipeline_module()
    scoring, (version, digest, prompt) = _visual_loader()
    panels = _panel_regions(3)
    transports = {
        panel.panel_id: {
            "panel_id": panel.panel_id,
            "source_asset_id": panel.source_asset_id,
            "source_order": panel.source_order,
            "mime_type": "image/png",
            "payload": b"synthetic-image-payload",
        }
        for panel in panels
    }
    provider = _VisualObservationSpy()
    semantic, _, first_chunk = _run_visual_observation(
        module, provider, panels, transports, version, digest
    )

    assert provider.requests
    assert all(request.visual_instruction_version == version for request in provider.requests)
    assert all(request.visual_instruction_sha256 == digest for request in provider.requests)
    assert [row["panel_id"] for row in semantic.values()] == [panel.panel_id for panel in panels]
    assert all("visual_evidence" in row for row in semantic.values())
    assert all("evidence_hash" not in row["visual_evidence"] for row in semantic.values())
    assert prompt.endswith("\n")
    assert first_chunk == {panel.panel_id: 0 for panel in panels}

    coverage = SimpleNamespace(version="vision-coverage-v2", map_sha256="map-task2")
    module._enrich_observations(panels, semantic, first_chunk, coverage)
    for panel in panels:
        persisted = panel.observation_json["visual_evidence"]
        assert persisted["evidence_hash"]
        parsed = scoring.parse_panel_visual_evidence(persisted)
        assert scoring.visual_evidence_hash(parsed) == persisted["evidence_hash"]


def test_observe_chunks_rejects_partial_visual_instruction_pair_before_provider_call():
    module = _pipeline_module()
    _, (version, _, _) = _visual_loader()
    panels = _panel_regions(3)
    transports = {
        panel.panel_id: {
            "panel_id": panel.panel_id,
            "source_asset_id": panel.source_asset_id,
            "source_order": panel.source_order,
            "mime_type": "image/png",
            "payload": b"synthetic-image-payload",
        }
        for panel in panels
    }
    provider = _VisualObservationSpy()
    with pytest.raises(Exception) as caught:
        module._observe_chunks(
            provider,
            module.build_observation_chunks(panels),
            transports,
            analysis_run_id="run-partial-visual-instruction",
            instruction_version="vision-first-story-analyzer-v1",
            instruction_sha256="a" * 64,
            visual_instruction_version=version,
            visual_instruction_sha256=None,
        )
    assert getattr(caught.value, "code", None) == "analyzer_contract_invalid"
    assert provider.requests == []


@pytest.mark.parametrize("mode", ("missing", "foreign", "malformed", "provider_hash"))
def test_observe_chunks_rejects_invalid_visual_sidecars(mode):
    module = _pipeline_module()
    _, (version, digest, _) = _visual_loader()
    panels = _panel_regions(3)
    transports = {
        panel.panel_id: {
            "panel_id": panel.panel_id,
            "source_asset_id": panel.source_asset_id,
            "source_order": panel.source_order,
            "mime_type": "image/png",
            "payload": b"synthetic-image-payload",
        }
        for panel in panels
    }
    provider = _VisualObservationSpy(mode=mode)
    with pytest.raises(Exception) as caught:
        _run_visual_observation(module, provider, panels, transports, version, digest)
    assert getattr(caught.value, "code", None) in {
        "vision_response_invalid",
        "analysis_observation_missing",
    }


def test_legacy_observation_spy_request_without_visual_fields_stays_compatible():
    module = _pipeline_module()
    panels = _panel_regions(3)
    transports = {
        panel.panel_id: {
            "panel_id": panel.panel_id,
            "source_asset_id": panel.source_asset_id,
            "source_order": panel.source_order,
            "mime_type": "image/png",
            "payload": b"synthetic-image-payload",
        }
        for panel in panels
    }
    provider = _ProviderSpy()
    semantic, _, _ = module._observe_chunks(
        provider,
        module.build_observation_chunks(panels),
        transports,
        analysis_run_id="run-legacy-compatible",
        instruction_version="vision-first-story-analyzer-v1",
        instruction_sha256="a" * 64,
    )
    assert semantic
    assert all("visual_evidence" not in row for row in semantic.values())


class _RetryablePersistentSynthesisSpy(_ProviderSpy):
    model_id = "fixture-synthesis-cache-model"
    endpoint = "https://fixture.invalid/v1"

    def __init__(self, *, fail_once: bool = False) -> None:
        super().__init__()
        self.fail_once = fail_once
        self.synthesis_attempts = 0

    def synthesize(self, request):
        from app.services.vision_adapter import VisionProviderRequestFailed

        self.synthesis_attempts += 1
        self.synthesis_requests.append(request)
        if self.fail_once and self.synthesis_attempts == 1:
            raise VisionProviderRequestFailed(retryable=True, transport_subtype="timeout")
        return _valid_synthesis_output(request)



def test_synthesis_retries_transport_failure_and_reuses_validated_cache(db, tmp_path, monkeypatch):
    module = _pipeline_module()
    monkeypatch.setattr(module.settings, "data_dir", tmp_path)
    project_id, _ = _seed_vision_project(db, standalone_count=13)

    first = _RetryablePersistentSynthesisSpy(fail_once=True)
    _install_provider(monkeypatch, first)
    row = module.run_analysis(db, project_id)
    assert row.state == "RECONCILED"
    assert first.synthesis_attempts == 2

    cache_files = sorted((tmp_path / "vision-synthesis-cache").glob("*.json"))
    assert len(cache_files) == 1

    second = _RetryablePersistentSynthesisSpy()
    _install_provider(monkeypatch, second)
    resumed = module.run_analysis(db, project_id)
    assert resumed.state == "RECONCILED"
    assert second.observe_requests == []
    assert second.synthesis_attempts == 0

def test_observation_reconcile_normalizes_structured_dialogue_for_cache_reuse():
    module = _pipeline_module()
    panel = {
        "panel_id": "panel-dialogue",
        "source_asset_id": "asset-dialogue",
        "source_order": 0,
        "mime_type": "image/png",
        "payload": b"synthetic",
    }
    row = {
        "panel_id": "panel-dialogue",
        "visible_facts": ["A speaker is visible."],
        "dialogue_or_ocr": [{"text": "TEN SECONDS", "type": "speech"}],
        "inferences": [],
        "uncertainties": [],
        "entities": [],
        "state_changes": [],
        "causal_links": [],
        "evidence_refs": ["panel-dialogue"],
    }
    normalized = module._validate_observation_rows(
        [row], ["panel-dialogue"], expected_panels={"panel-dialogue": panel}
    )
    assert normalized[0]["dialogue_or_ocr"] == ["TEN SECONDS"]

class _UnknownPanelCorrectiveSynthesisSpy(_RetryablePersistentSynthesisSpy):
    def synthesize(self, request):
        from app.services.vision_adapter import VisionResponseInvalid

        self.synthesis_attempts += 1
        self.synthesis_requests.append(request)
        if self.synthesis_attempts == 1:
            raise VisionResponseInvalid(
                validation_subtype="state_change_evidence_contains_an_unknown_panel"
            )
        return _valid_synthesis_output(request)


def test_synthesis_unknown_panel_reference_retries_with_locked_evidence_lineage(db, tmp_path, monkeypatch):
    module = _pipeline_module()
    monkeypatch.setattr(module.settings, "data_dir", tmp_path)
    project_id, _ = _seed_vision_project(db, standalone_count=13)
    provider = _UnknownPanelCorrectiveSynthesisSpy()
    _install_provider(monkeypatch, provider)

    row = module.run_analysis(db, project_id)

    assert row.state == "RECONCILED"
    assert provider.synthesis_attempts == 2
    first, second = provider.synthesis_requests
    assert first.retry_evidence_lineage is False
    assert second.retry_evidence_lineage is True
    assert second.retry_word_counts is None
    assert second.retry_passages is None
    assert second.expected_panel_ids == first.expected_panel_ids
    assert second.ordered_observations == first.ordered_observations
    assert second.coverage_manifest == first.coverage_manifest
    assert second.chunks == first.chunks


class _PersistentUnknownPanelSynthesisSpy(_RetryablePersistentSynthesisSpy):
    def synthesize(self, request):
        from app.services.vision_adapter import VisionResponseInvalid

        self.synthesis_attempts += 1
        self.synthesis_requests.append(request)
        raise VisionResponseInvalid(
            validation_subtype="state_change_evidence_contains_an_unknown_panel"
        )


def test_synthesis_persistent_unknown_panel_reference_still_fails_closed(db, tmp_path, monkeypatch):
    module = _pipeline_module()
    monkeypatch.setattr(module.settings, "data_dir", tmp_path)
    project_id, _ = _seed_vision_project(db, standalone_count=13)
    provider = _PersistentUnknownPanelSynthesisSpy()
    _install_provider(monkeypatch, provider)

    row = module.run_analysis(db, project_id)

    assert row.state == "BLOCKED"
    assert provider.synthesis_attempts == module._VISION_SYNTHESIS_MAX_ATTEMPTS
    assert provider.synthesis_requests[0].retry_evidence_lineage is False
    assert all(request.retry_evidence_lineage is True for request in provider.synthesis_requests[1:])
    blocking = row.blocking_reasons_json or {}
    assert blocking.get("codes") == ["vision_response_invalid"]
    findings = blocking.get("findings") or []
    assert findings and findings[0].get("stage") == "synthesis_response"
    assert findings[0].get("validation_subtype") == "state_change_evidence_contains_an_unknown_panel"


class _WordCountCorrectiveSynthesisSpy(_RetryablePersistentSynthesisSpy):
    def synthesize(self, request):
        from app.services.vision_adapter import VisionResponseInvalid
        self.synthesis_attempts += 1
        self.synthesis_requests.append(request)
        if self.synthesis_attempts == 1:
            candidate = _valid_synthesis_output(request)
            raise VisionResponseInvalid(
                validation_subtype="production_narration_word_count_out_of_range",
                passage_word_counts=(17, 18, 25, 17, 15),
                retry_passages=tuple(dict(item) for item in candidate["script_passages"]),
            )
        return _valid_synthesis_output(request)


def test_synthesis_word_count_retry_carries_only_safe_count_feedback(db, tmp_path, monkeypatch):
    module = _pipeline_module()
    monkeypatch.setattr(module.settings, "data_dir", tmp_path)
    project_id, _ = _seed_vision_project(db, standalone_count=13)
    provider = _WordCountCorrectiveSynthesisSpy()
    _install_provider(monkeypatch, provider)
    row = module.run_analysis(db, project_id)
    assert row.state == "RECONCILED"
    assert provider.synthesis_attempts == 2
    assert provider.synthesis_requests[0].retry_word_counts is None
    assert provider.synthesis_requests[1].retry_word_counts == (17, 18, 25, 17, 15)
    assert provider.synthesis_requests[1].retry_passages is not None
    assert len(provider.synthesis_requests[1].retry_passages) == 5
    assert provider.synthesis_requests[1].retry_passages == tuple(
        dict(item) for item in _valid_synthesis_output(provider.synthesis_requests[0])["script_passages"]
    )
    assert provider.synthesis_requests[1].ordered_observations == provider.synthesis_requests[0].ordered_observations
    assert provider.synthesis_requests[1].coverage_manifest == provider.synthesis_requests[0].coverage_manifest


class _AnalyzerWordCountCorrectiveSynthesisSpy(_WordCountCorrectiveSynthesisSpy):
    def synthesize(self, request):
        from app.services.vision_adapter import VisionResponseInvalid
        self.synthesis_attempts += 1
        self.synthesis_requests.append(request)
        if self.synthesis_attempts == 1:
            candidate = _valid_synthesis_output(request)
            raise VisionResponseInvalid(
                validation_subtype="script_passage_narration_must_contain_90-125_words",
                passage_word_counts=(18, 27, 35, 28, 22),
                retry_passages=tuple(dict(item) for item in candidate["script_passages"]),
            )
        return _valid_synthesis_output(request)


def test_analyzer_word_count_subtype_uses_locked_retry(db, tmp_path, monkeypatch):
    module = _pipeline_module()
    monkeypatch.setattr(module.settings, "data_dir", tmp_path)
    project_id, _ = _seed_vision_project(db, standalone_count=13)
    provider = _AnalyzerWordCountCorrectiveSynthesisSpy()
    _install_provider(monkeypatch, provider)
    row = module.run_analysis(db, project_id)
    assert row.state == "RECONCILED"
    assert provider.synthesis_attempts == 2
    assert provider.synthesis_requests[1].retry_word_counts == (18, 27, 35, 28, 22)
    assert provider.synthesis_requests[1].retry_passages is not None


class _VisualSelectionCorrectiveSynthesisSpy(_RetryablePersistentSynthesisSpy):
    def synthesize(self, request):
        from app.services.vision_adapter import VisionResponseInvalid
        self.synthesis_attempts += 1
        self.synthesis_requests.append(request)
        if self.synthesis_attempts == 1:
            raise VisionResponseInvalid(
                validation_subtype="production_visual_selection_insufficient"
            )
        return _valid_synthesis_output(request)


def test_synthesis_visual_selection_retry_preserves_evidence_input(db, tmp_path, monkeypatch):
    module = _pipeline_module()
    monkeypatch.setattr(module.settings, "data_dir", tmp_path)
    project_id, _ = _seed_vision_project(db, standalone_count=13)
    provider = _VisualSelectionCorrectiveSynthesisSpy()
    _install_provider(monkeypatch, provider)
    row = module.run_analysis(db, project_id)
    assert row.state == "RECONCILED"
    assert provider.synthesis_attempts == 2
    assert provider.synthesis_requests[0].retry_visual_selection is False
    assert provider.synthesis_requests[1].retry_visual_selection is True
    assert provider.synthesis_requests[1].ordered_observations == provider.synthesis_requests[0].ordered_observations
    assert provider.synthesis_requests[1].coverage_manifest == provider.synthesis_requests[0].coverage_manifest


class _SubtitleOverflowCorrectiveSynthesisSpy(_RetryablePersistentSynthesisSpy):
    def synthesize(self, request):
        self.synthesis_attempts += 1
        self.synthesis_requests.append(request)
        candidate = _valid_synthesis_output(request)
        if self.synthesis_attempts == 1:
            candidate["script_passages"][0]["text"] = (
                "Canceling giantification at the precise right instant, the barbarian dodges death "
                "and reveals the immortal's hidden flaw."
            )
        return candidate


def test_synthesis_subtitle_overflow_uses_locked_corrective_retry(db, tmp_path, monkeypatch):
    module = _pipeline_module()
    monkeypatch.setattr(module.settings, "data_dir", tmp_path)
    project_id, _ = _seed_vision_project(db, standalone_count=13)
    provider = _SubtitleOverflowCorrectiveSynthesisSpy()
    _install_provider(monkeypatch, provider)
    row = module.run_analysis(db, project_id)
    assert row.state == "RECONCILED"
    assert provider.synthesis_attempts == 2
    retry = provider.synthesis_requests[1]
    assert retry.retry_word_counts is not None
    assert retry.retry_passages is not None
    assert retry.retry_passages[0]["text"].startswith("Canceling giantification")
    assert retry.ordered_observations == provider.synthesis_requests[0].ordered_observations
    assert retry.coverage_manifest == provider.synthesis_requests[0].coverage_manifest


def test_preferred_visual_panel_ids_exclude_front_matter_and_invalid_bounds():
    module = _pipeline_module()
    def panel(panel_id, source_order, bounds):
        return SimpleNamespace(
            panel_id=panel_id,
            source_order=source_order,
            bounds_json=bounds,
            observation_json={
                "visual_evidence": {
                    "balloon_mask_status": "known_empty",
                    "protected_regions": [{"kind": "subject"}],
                }
            },
        )
    preferred = module._preferred_visual_panel_ids([
        panel("front", 0, {"x": 0, "y": 0, "width": 800, "height": 1200}),
        panel("invalid", 2, {"x": 0, "y": 0, "width": 0, "height": 1200}),
        panel("story", 3, {"x": 0, "y": 0, "width": 800, "height": 1200}),
    ])
    assert preferred == ("story",)


def test_frameable_preferred_visual_ids_translate_segmented_source_bounds(monkeypatch):
    module = _pipeline_module()
    raw = io.BytesIO()
    Image.new("RGB", (800, 400), (80, 120, 160)).save(raw, format="PNG")
    panel = SimpleNamespace(
        panel_id="story", source_asset_id="asset-a", source_order=3,
        bounds_json={"x": 0, "y": 150, "width": 800, "height": 300},
        observation_json={"visual_evidence": {"balloon_mask_status": "known_nonempty", "protected_regions": [{"kind": "subject"}]}},
    )
    source = SimpleNamespace(payload=raw.getvalue(), source_bounds=(0, 100, 800, 500), source_family="family-a")
    seen = {}
    def feasible(_panel, crop, _candidate, _profile, **_kwargs):
        seen["size"] = crop.size
        return True, tuple(_kwargs.get("editorial_sections", ()))
    monkeypatch.setattr(module.reference_visual_review, "panel_reference_roi_safety", feasible)
    result = module._frameable_preferred_visual_panel_ids((panel,), {"asset-a": source}, SimpleNamespace())
    assert result == ("story",)
    assert seen["size"] == (800, 300)


def test_frameable_preferred_visual_selection_tracks_section_safety(monkeypatch):
    module = _pipeline_module()
    raw = io.BytesIO()
    Image.new("RGB", (800, 400), (80, 120, 160)).save(raw, format="PNG")
    panel = SimpleNamespace(
        panel_id="story", source_asset_id="asset-a", source_order=3,
        bounds_json={"x": 0, "y": 150, "width": 800, "height": 300},
        observation_json={"visual_evidence": {"balloon_mask_status": "known_nonempty", "protected_regions": [{"kind": "subject"}]}},
    )
    source = SimpleNamespace(payload=raw.getvalue(), source_bounds=(0, 100, 800, 500), source_family="family-a")
    def feasible(_panel, _crop, _candidate, _profile, **_kwargs):
        assert tuple(_kwargs["editorial_sections"]) == ("hook", "setup", "conflict", "twist", "cta")
        return True, ("hook", "conflict")
    monkeypatch.setattr(module.reference_visual_review, "panel_reference_roi_safety", feasible)
    generic, by_section = module._frameable_preferred_visual_panel_selection(
        (panel,), {"asset-a": source}, SimpleNamespace()
    )
    assert generic == ("story",)
    assert by_section["hook"] == ("story",)
    assert by_section["conflict"] == ("story",)
    assert by_section["setup"] == ()
    assert by_section["twist"] == ()
    assert by_section["cta"] == ()
