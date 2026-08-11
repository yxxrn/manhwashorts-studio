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


def _png_bytes(width: int, height: int, seed: int) -> bytes:
    image = Image.new("RGB", (width, height))
    for y in range(height):
        color = ((40 + seed + y) % 180, (70 + seed * 2 + y) % 180, 110)
        for x in range(width):
            image.putpixel((x, y), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


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
            with Image.open(io.BytesIO(panel["payload"])) as decoded:
                assert decoded.size == (bounds["width"], bounds["height"])
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
