"""Shared test factories extracted from regression modules."""
# ruff: noqa: F401

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

