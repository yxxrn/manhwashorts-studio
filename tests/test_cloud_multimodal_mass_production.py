"""RED contract tests for the pinned cloud multimodal production path.

These tests intentionally import the new boundary inside test bodies so a
missing implementation is a collection-clean, body-level RED result.
"""

from __future__ import annotations

import importlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest


def _module():
    try:
        return importlib.import_module("app.services.cloud_multimodal")
    except Exception as exc:
        pytest.fail(f"cloud multimodal boundary import failed in test body: {exc}")


def _identity(module):
    return module.CloudModelIdentity(
        provider="openai_compatible",
        model="mock-multimodal-v1",
        model_version="pinned",
        endpoint="http://mock.invalid/v1",
        prompt_versions={
            "visual": "balloon-free-visual-evidence-v1",
            "story_map": "cloud-causal-map-v2",
            "narration": "vision-first-story-analyzer-v3",
        },
    )


def _panels(module, prefix: str = "chapter-a"):
    return tuple(
        module.CloudPanelInput(
            panel_id=f"{prefix}-panel-{index}",
            source_asset_id=f"{prefix}-asset-{index}",
            source_order=index + 1,
            mime_type="image/png",
            payload=f"{prefix}-panel-payload-{index}".encode(),
            panel_bounds=(0, 0, 100, 100),
            source_dimensions=(100, 100),
            strip_region_id=f"{prefix}-region-{index}",
            coverage_map_version="coverage-v1",
            coverage_map_hash="b" * 64,
        )
        for index in range(3)
    )


def _boundary_request(module):
    segmentation = importlib.import_module("app.services.strip_segmentation")
    return segmentation.BoundaryRequest(
        source_asset_id="strip-a",
        source_checksum="a" * 64,
        width=400,
        height=2200,
        candidates=(
            segmentation.BoundaryCandidate(
                position=1100,
                confidence=0.8,
                score=0.8,
                run_top=1080,
                run_bottom=1120,
                reason="structural separator",
            ),
        ),
        tiles=(
            {"tile_index": 0, "y0": 0, "y1": 1200, "payload_b64": "cG5n"},
            {"tile_index": 1, "y0": 1000, "y1": 2200, "payload_b64": "cG5n"},
        ),
    )


def test_visual_repair_failure_metadata_is_sanitized_and_counts_feasible_scope():
    module = _module()
    repair = importlib.import_module("app.services.visual_narrative_repair")
    entry = repair.FeasibleVisualRecord(
        panel_region_id="region-safe",
        panel_id="panel-safe",
        source_asset_id="asset-safe",
        source_order=10,
        eligible_sections=("setup",),
        eligible_beats=("beat-safe",),
        resolution_state="UPSCALED",
        feasible_rois=(
            {"kind": "primary", "roi_label": "primary", "crop_box": [0, 0, 100, 100], "telemetry": {}},
            {"kind": "tighter_crop", "roi_label": "tight", "crop_box": [2, 2, 98, 98], "telemetry": {}},
        ),
        visual_strengths={"edge_connected_blank_fraction": 0.0},
        evidence_hash="e" * 64,
        detector_version="detector-v1",
        mask_sha256="m" * 64,
        panel_size=(100, 100),
    )
    ledger = repair.FeasibleVisualLedger(
        entries=(entry,), model_identity_hash="model-hash"
    )

    metadata = module._visual_narrative_repair_failure_metadata(
        ledger=ledger,
        section_to_beats={"hook": ("beat-missing",), "setup": ("beat-safe",)},
        attempt_count=3,
        failure_code="visual.narrative_repair_ungrounded",
    )

    assert metadata == {
        "contract_version": repair.REPAIR_CONTRACT_VERSION,
        "attempt_count": 3,
        "failure_code": "visual.narrative_repair_ungrounded",
        "feasible_panel_count": 1,
        "feasible_roi_count": 2,
        "missing_section_count": 1,
        "ledger_hash": ledger.ledger_hash,
    }


def test_visual_repair_analyzer_failure_keeps_only_field_count_and_guides_retry():
    module = _module()

    metadata = module._visual_narrative_repair_analyzer_metadata(
        "script passage evidence does not cover its claims",
        {"script_passages": [{}, {}, {}]},
    )

    assert metadata == {
        "failed_predicate": "analyzer_contract_invalid",
        "failed_field": "passage_evidence",
        "failed_count": 3,
    }
    assert "message" not in metadata
    assert "prose" not in metadata
    assert "feasible evidence_panel_ids" in module._visual_narrative_repair_retry_feedback(
        "cloud.narrative_not_grounded",
        failed_field="passage_evidence",
    )


def test_invalid_visual_repair_cache_does_not_bypass_bounded_provider_path(monkeypatch):
    module = _module()
    repair = importlib.import_module("app.services.visual_narrative_repair")
    from types import SimpleNamespace

    entry = repair.FeasibleVisualRecord(
        panel_region_id="region-safe",
        panel_id="panel-safe",
        source_asset_id="asset-safe",
        source_order=10,
        eligible_sections=(),
        eligible_beats=("beat-safe",),
        resolution_state="UPSCALED",
        feasible_rois=(
            {
                "kind": "primary",
                "roi_label": "primary",
                "crop_box": [0, 0, 1080, 1920],
                "telemetry": {},
            },
        ),
        visual_strengths={"edge_connected_blank_fraction": 0.0},
        evidence_hash="e" * 64,
        detector_version="detector-v1",
        mask_sha256="m" * 64,
        panel_size=(1080, 1920),
    )
    ledger = repair.FeasibleVisualLedger(
        entries=(entry,), model_identity_hash="model-hash"
    )
    visual = SimpleNamespace(
        panels=(
            {
                "panel_id": "panel-safe",
                "source_asset_id": "asset-safe",
                "source_order": 10,
            },
        ),
        source_hash="v" * 64,
        visual_evidence_hash="visual-hash",
    )
    story_map = SimpleNamespace(
        claims=({"claim_id": "claim-safe"},),
        as_dict=lambda: {
            "beats": [{"beat_id": "beat-safe", "panel_ids": ["panel-safe"]}],
            "claims": [{"claim_id": "claim-safe", "panel_ids": ["panel-safe"]}],
        },
        story_map_hash="s" * 64,
    )

    class Cache:
        def get(self, _key):
            return {"cached": True}

        def put(self, _key, _value):
            raise AssertionError("the failed provider path must not cache a result")

    cached = SimpleNamespace(
        visual_evidence_hash=visual.visual_evidence_hash,
        evidence_graph={"claims": []},
        passages=(),
    )
    monkeypatch.setattr(
        module.NarrationResult,
        "from_dict",
        staticmethod(lambda _value: cached),
    )
    calls = {"count": 0}

    def fail_provider(*_args, **_kwargs):
        calls["count"] += 1
        raise module.CloudStageError("cloud.provider_request_failed")

    runner = module.CloudStageRunner(
        provider=SimpleNamespace(model_id=_identity(module).model),
        model_identity=_identity(module),
        cache=Cache(),
    )
    runner._narration_observations = lambda *_args: (
        [{"panel_id": "panel-safe"}],
        {"continuity_ledger": {}, "coverage_manifest": {}},
    )
    runner._call = fail_provider

    with pytest.raises(module.CloudStageError) as caught:
        runner.run_visual_narrative_repair(
            visual,
            story_map,
            None,
            ledger,
            {"hook": ("beat-missing",)},
        )

    assert caught.value.code == "cloud.provider_request_failed"
    assert calls["count"] == repair.MAX_REPAIR_ATTEMPTS


def test_visual_repair_contract_bump_scopes_stale_provider_cache():
    repair = importlib.import_module("app.services.visual_narrative_repair")
    ledger = type("Ledger", (), {"ledger_hash": "ledger-hash"})()
    common = {
        "ledger": ledger,
        "model_identity_hash": "model-hash",
        "prompt_sha256": "prompt-hash",
        "narration_hash": "narration-hash",
    }

    old_key = repair.repair_cache_key(
        **common,
        contract_version="visual_narrative_repair_v1",
    )
    current_key = repair.repair_cache_key(
        **common,
        contract_version=repair.REPAIR_CONTRACT_VERSION,
    )

    assert repair.REPAIR_CONTRACT_VERSION == "visual_narrative_repair_v2"
    assert repair.REPAIR_PROMPT_VERSION == "visual-narrative-repair-v3"
    assert old_key != current_key


def _visual_row(panel, *, unknown: bool = False, provider_hash: bool = False):
    sidecar = {
        "contract_version": "COLOR_AGNOSTIC_BALLOON_FREE_V1",
        "panel_id": panel["panel_id"],
        "source_asset_id": panel["source_asset_id"],
        "source_order": panel["source_order"],
        "balloon_regions": [],
        "protected_regions": [],
        "balloon_mask_status": "unknown" if unknown else "known_empty",
        "mask_confidence": 0.0 if unknown else 0.95,
        "evidence_source": "vision_geometry_unavailable" if unknown else "vision_geometry_v1",
        "mask_reason": "geometry is unavailable" if unknown else "provider explicitly reports no speech region",
    }
    return {
        "panel_id": panel["panel_id"],
        "visible_facts": [f"visible fact {panel['source_order']}"],
        "dialogue_or_ocr": [],
        "inferences": [],
        "uncertainties": [],
        "entities": [],
        "state_changes": [],
        "causal_links": [],
        "evidence_refs": [panel["panel_id"]],
        "visual_evidence": sidecar | ({"evidence_hash": "a" * 64} if provider_hash else {}),
    }


def _narrative_output(prefix: str, panel_ids: list[str]):
    helper = importlib.import_module("test_narrative_identity")
    passages = helper._passages(prefix, 4, "consequence")
    extensions = (
        " as pressure starts building nearby",
        " while the safer route disappears",
        " without proving who controls it",
        " before the next turn arrives",
    )
    for passage, extension in zip(passages, extensions, strict=True):
        passage["text"] = str(passage["text"]).rstrip(".!?") + extension + "."
    output = helper._v3_chapter(
        chapter_prefix=prefix,
        passages=passages,
        ending_kind="consequence",
    )
    for observation, panel_id in zip(output["observations"], panel_ids, strict=True):
        observation["panel_id"] = panel_id
        observation["evidence_refs"] = [panel_id]
    for passage in output["script_passages"]:
        passage["evidence_panel_ids"] = list(panel_ids)
    for claim in output["evidence_graph"]["claims"]:
        claim["evidence_panel_ids"] = list(panel_ids)
    output["coverage_manifest"]["panel_ids"] = list(panel_ids)
    output["coverage_manifest"]["total_panels"] = len(panel_ids)
    output["coverage_manifest"]["processed_panels"] = len(panel_ids)
    for chunk in output["continuity_ledger"]["chunks"]:
        chunk["panel_ids"] = list(panel_ids)
    for entity in output["continuity_ledger"]["entities"]:
        entity["panel_ids"] = list(panel_ids)
    output["narrative_outline"]["story_spine"]["unresolved_question"] = "What changes next?"
    return output


@dataclass
class _FakeProvider:
    model_id: str = "mock-multimodal-v1"
    unknown_visual: bool = False
    transient_unknown_count: int = 0
    transient_story_map_invalid_count: int = 0
    fail_for_prefix: str = ""
    fail_count: int = 0
    provider_hash: bool = False
    structured_dialogue: bool = False

    def __post_init__(self):
        self.calls: list[tuple[str, str, str]] = []
        self.analysis_run_ids: list[str] = []
        self.boundary_payloads: list[dict] = []
        self.boundary_prompts: list[str] = []
        self.narration_payloads: list[dict] = []

    def observe(self, request):
        self.calls.append(("visual", request.visual_instruction_version, request.visual_instruction_sha256))
        self.analysis_run_ids.append(request.analysis_run_id)
        if self.transient_unknown_count:
            self.transient_unknown_count -= 1
            return [
                _visual_row(panel, unknown=True, provider_hash=self.provider_hash)
                for panel in request.panels
            ]
        if self.fail_count:
            self.fail_count -= 1
            raise RuntimeError("provider secret-bearing failure detail")
        if self.fail_for_prefix and request.panels[0]["panel_id"].startswith(self.fail_for_prefix):
            raise RuntimeError("provider failure for one chapter")
        rows = [
            _visual_row(panel, unknown=self.unknown_visual, provider_hash=self.provider_hash)
            for panel in request.panels
        ]
        if self.structured_dialogue:
            for row in rows:
                row["dialogue_or_ocr"] = [{"text": "visible words", "type": "ocr"}]
                row["visible_facts"] = [{"fact": "a visible fact"}]
                row["inferences"] = [{"inference": "a qualified inference"}]
        return rows

    def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
        self.calls.append((stage, prompt_version, prompt_sha256))
        if stage == "narration":
            self.narration_payloads.append(dict(payload))
        if stage == "strip_segmentation":
            self.boundary_payloads.append(dict(payload))
            self.boundary_prompts.append(prompt_text)
            return {
                "source_asset_id": payload["source_asset_id"],
                "source_checksum": payload["source_checksum"],
                "random_sampling": False,
                "boundaries": [
                    {
                        "y": candidate["position"],
                        "accepted": True,
                        "confidence": 0.96,
                        "reason": "the overlapping tiles support this boundary",
                        "protected_regions": [],
                    }
                    for candidate in payload["candidate_boundaries"]
                ],
            }
        panel_ids = list(payload["panel_ids"])
        if stage == "story_map":
            if self.transient_story_map_invalid_count:
                self.transient_story_map_invalid_count -= 1
                return {
                    "panel_ids": panel_ids,
                    "random_sampling": False,
                    "beats": [{"beat_id": "beat-1", "panel_ids": panel_ids, "summary": "pressure builds"}],
                    "causal_chain": [{"from_beat": "beat-1", "to_beat": "missing", "reason": ""}],
                    "claims": [{"claim_id": "claim-1", "panel_ids": panel_ids}],
                }
            return {
                "contract_version": "cloud-causal-map-v1",
                "panel_ids": panel_ids,
                "random_sampling": False,
                "beats": [
                    {"beat_id": "beat-1", "panel_ids": panel_ids[:2], "summary": "pressure builds"},
                    {"beat_id": "beat-2", "panel_ids": panel_ids[1:] or panel_ids, "summary": "the next choice stays open"},
                ],
                "causal_chain": [
                    {"from_beat": "beat-1", "to_beat": "beat-2", "reason": "the visible choice changes the stakes"}
                ],
                "claims": [
                    {
                        "claim_id": "map-claim-1",
                        "text": "The visible choice changes the stakes.",
                        "panel_ids": panel_ids,
                        "qualification": "The sequence supports this reading.",
                    }
                    ,{
                        "claim_id": "cloud-claim-fact",
                        "text": "The visible route changes the immediate balance.",
                        "panel_ids": panel_ids,
                        "qualification": "The ordered panels support this visible reading.",
                    },
                    {
                        "claim_id": "cloud-claim-interpretation",
                        "text": "The next choice may narrow the available route.",
                        "panel_ids": panel_ids,
                        "qualification": "This remains a qualified interpretation of the sequence.",
                    }
                ],
            }
        return _narrative_output("cloud", panel_ids)


class _BoundaryLineageProvider(_FakeProvider):
    def __init__(self, *, foreign_responses: int | None):
        super().__init__()
        self.foreign_responses = foreign_responses

    def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
        output = super().complete_json(
            stage=stage,
            prompt_version=prompt_version,
            prompt_sha256=prompt_sha256,
            prompt_text=prompt_text,
            payload=payload,
        )
        if stage == "strip_segmentation" and (
            self.foreign_responses is None or self.foreign_responses > 0
        ):
            if self.foreign_responses is not None:
                self.foreign_responses -= 1
            output = dict(output)
            output["source_asset_id"] = "foreign-source"
        return output


class _CompactNarrationProvider(_FakeProvider):
    """Models that return narrative content without the persisted analyzer envelope."""

    def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
        output = super().complete_json(
            stage=stage,
            prompt_version=prompt_version,
            prompt_sha256=prompt_sha256,
            prompt_text=prompt_text,
            payload=payload,
        )
        if stage != "narration":
            return output
        claims = []
        for claim in output["evidence_graph"]["claims"]:
            compact_claim = dict(claim)
            compact_claim.pop("claim_type", None)
            claims.append(compact_claim)
        return {
            "narrative_outline": output["narrative_outline"],
            "script_passages": output["script_passages"],
            "evidence_graph": claims,
        }


class _CausalMapClaimsOnlyProvider(_FakeProvider):
    """Models that reuse causal-map claim IDs but omit the graph envelope."""

    def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
        output = super().complete_json(
            stage=stage,
            prompt_version=prompt_version,
            prompt_sha256=prompt_sha256,
            prompt_text=prompt_text,
            payload=payload,
        )
        if stage != "narration":
            return output
        claim_ids = ["map-claim-1", "cloud-claim-fact", "cloud-claim-interpretation"]
        for index, passage in enumerate(output["script_passages"]):
            passage["claim_ids"] = [claim_ids[index % len(claim_ids)]]
        return {
            "narrative_outline": output["narrative_outline"],
            "script_passages": output["script_passages"],
        }


class _InvalidNarrationProvider(_FakeProvider):
    def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
        if stage == "narration":
            return {"narrative_outline": {}, "script_passages": [], "evidence_graph": []}
        return super().complete_json(
            stage=stage,
            prompt_version=prompt_version,
            prompt_sha256=prompt_sha256,
            prompt_text=prompt_text,
            payload=payload,
        )


def test_narration_reconciles_compact_provider_envelope_from_visual_lineage():
    module = _module()
    provider = _CompactNarrationProvider(structured_dialogue=True)
    panels = tuple(
        replace(
            panel,
            panel_bounds=(0, 0, 100, 100),
            source_dimensions=(100, 100),
            strip_region_id=panel.panel_id,
            coverage_map_version="coverage-v1",
            coverage_map_hash="b" * 64,
        )
        for panel in _panels(module)
    )
    runner = module.CloudStageRunner(provider=provider, model_identity=_identity(module))

    visual = runner.run_visual_evidence(panels)
    story_map = runner.run_story_map(visual)
    result = runner.run_narration(visual, story_map, panels=panels)

    assert len(result.observations) == len(panels)
    assert result.continuity_ledger["reconciled_after_final_chunk"] is True
    assert result.continuity_ledger["chunks"][0]["panel_ids"] == list(visual.panel_ids)
    assert result.evidence_graph["claims"]
    assert all(claim["claim_type"] == "interpretation" for claim in result.evidence_graph["claims"])
    assert provider.narration_payloads[0]["duration_contract"] == {
        **module.script.narration_duration_contract("dramatic"),
        "minimum_s": 50.0,
        "maximum_s": 60.0,
        "target_word_min": 115,
        "target_word_max": 125,
    }


def test_narration_reconciler_accepts_ocr_text_visual_observations():
    module = _module()
    provider = _FakeProvider()
    panels = _panels(module)
    runner = module.CloudStageRunner(provider=provider, model_identity=_identity(module))

    visual = runner.run_visual_evidence(panels)
    for row in visual.panels:
        row["observation"]["dialogue_or_ocr"] = [{"ocr_text": "visible words", "type": "ocr"}]

    observations, _structural = module.CloudStageRunner._narration_observations(visual, panels)

    assert observations[0]["dialogue_or_ocr"] == ["visible words"]


def test_narration_reuses_validated_causal_claims_when_graph_is_omitted():
    module = _module()
    provider = _CausalMapClaimsOnlyProvider()
    panels = tuple(
        replace(
            panel,
            panel_bounds=(0, 0, 100, 100),
            source_dimensions=(100, 100),
            strip_region_id=panel.panel_id,
            coverage_map_version="coverage-v1",
            coverage_map_hash="b" * 64,
        )
        for panel in _panels(module)
    )
    runner = module.CloudStageRunner(provider=provider, model_identity=_identity(module))

    visual = runner.run_visual_evidence(panels)
    story_map = runner.run_story_map(visual)
    result = runner.run_narration(visual, story_map, panels=panels)

    assert {claim["claim_id"] for claim in result.evidence_graph["claims"]} <= {
        claim["claim_id"] for claim in story_map.claims
    }
    assert result.evidence_graph["claims"]


def test_narration_terminal_contract_failure_is_sanitized():
    module = _module()
    panels = _panels(module)
    runner = module.CloudStageRunner(
        provider=_InvalidNarrationProvider(),
        model_identity=_identity(module),
        max_attempts=1,
    )
    visual = runner.run_visual_evidence(panels)
    story_map = runner.run_story_map(visual)

    with pytest.raises(module.CloudStageError) as error:
        runner.run_narration(visual, story_map, panels=panels)

    assert error.value.code == "cloud.narrative_not_grounded"


def test_stage_runner_sends_strip_boundary_tiles_through_pinned_prompt():
    module = _module()
    segmentation = importlib.import_module("app.services.strip_segmentation")
    provider = _FakeProvider()
    runner = module.CloudStageRunner(provider=provider, model_identity=_identity(module))
    request = segmentation.BoundaryRequest(
        source_asset_id="strip-a",
        source_checksum="a" * 64,
        width=400,
        height=2200,
        candidates=(
            segmentation.BoundaryCandidate(
                position=1100,
                confidence=0.8,
                score=0.8,
                run_top=1080,
                run_bottom=1120,
                reason="structural separator",
            ),
        ),
        tiles=(
            {"tile_index": 0, "y0": 0, "y1": 1200, "payload_b64": "cG5n"},
            {"tile_index": 1, "y0": 1000, "y1": 2200, "payload_b64": "cG5n"},
        ),
    )

    result = runner.assess_strip_boundaries(request)

    assert result["random_sampling"] is False
    assert provider.calls[-1][0] == "strip_segmentation"
    assert provider.calls[-1][1] == "strip-boundary-assessment-v1"
    assert provider.calls[-1][2] == "41dce6cbda6c546f96cf8dc270dc7375f777b7eaf123422508c3d31fce2fe2a3"
    assert provider.boundary_payloads[-1]["overlapping_source_tiles"]
    assert provider.boundary_payloads[-1]["candidate_boundaries"]
    assert "Protected_regions objects use keys: region_id, kind, bounds, confidence," in provider.boundary_prompts[-1]
    assert "evidence_source. Bounds are exactly [x0, y0, x1, y1] integer coordinates" in provider.boundary_prompts[-1]
    assert "Boundary objects use keys exactly: y, accepted, confidence, reason," in provider.boundary_prompts[-1]
    assert "protected_regions. Never rename y to position or cut." in provider.boundary_prompts[-1]


def test_stage_runner_retries_foreign_boundary_lineage_then_accepts_valid_response():
    module = _module()
    provider = _BoundaryLineageProvider(foreign_responses=1)
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        max_attempts=2,
    )

    result = runner.assess_strip_boundaries(_boundary_request(module))

    assert result["source_asset_id"] == "strip-a"
    assert len([call for call in provider.calls if call[0] == "strip_segmentation"]) == 2


def test_stage_runner_keeps_foreign_boundary_lineage_blocked_after_bounded_retries():
    module = _module()
    provider = _BoundaryLineageProvider(foreign_responses=None)
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        max_attempts=2,
    )

    with pytest.raises(module.strip_segmentation.StripSegmentationError) as caught:
        runner.assess_strip_boundaries(_boundary_request(module))

    assert caught.value.code == "segmentation.provider_lineage_invalid"
    assert len([call for call in provider.calls if call[0] == "strip_segmentation"]) == 2


def test_causal_map_prompt_declares_exact_reconciled_object_fields():
    module = _module()

    version, digest, prompt = module._load_causal_prompt()

    assert version == "cloud-causal-map-v2"
    assert len(digest) == 64
    assert "beat_id, panel_ids, summary" in prompt
    assert "at least five ordered" in prompt
    assert "from_beat, to_beat, reason" in prompt
    assert "each claim has claim_id," in prompt
    assert "text, panel_ids, qualification" in prompt


def test_prepare_project_panels_preserves_segmentation_review_code(monkeypatch):
    module = _module()
    segmentation = importlib.import_module("app.services.segmentation")
    pipeline = importlib.import_module("app.services.pipeline")
    payload = b"not decoded because lineage is rejected first"
    input_row = segmentation.SourceAssetInput(
        source_asset_id="partial-source",
        original_checksum="f" * 64,
        original_width=100,
        original_height=100,
        source_bounds=(0, 0, 100, 80),
        strip_order=0,
        region_order=0,
        payload=payload,
        decoded_width=100,
        decoded_height=80,
    )
    monkeypatch.setattr(pipeline, "project_assets", lambda _db, _project_id: ())
    monkeypatch.setattr(pipeline, "image_assets", lambda assets: list(assets))
    monkeypatch.setattr(pipeline, "_build_source_inputs", lambda _assets: ((input_row,), {}))

    with pytest.raises(module.CloudStageError) as caught:
        module.prepare_project_panels(object(), "project-a")

    assert caught.value.code == "segmentation.coverage_incomplete"
    assert caught.value.reviewable is True


def test_review_preview_requests_audited_segmentation_auto_override(monkeypatch):
    module = _module()
    from types import SimpleNamespace

    captured = {}

    def fake_prepare(_db, _project_id, **kwargs):
        captured.update(kwargs)
        raise module.CloudStageError("segmentation.ambiguous_boundary", reviewable=True)

    class Store:
        def load(self, _project_id):
            return None

        def save(self, record):
            self.record = record

    service = module.CloudBatchService.__new__(module.CloudBatchService)
    service.runner = SimpleNamespace(
        model_identity=SimpleNamespace(identity_hash="model-hash"),
        assess_strip_boundaries=lambda _request: {},
    )
    service.store = Store()
    service.review_root = None
    monkeypatch.setattr(module, "prepare_project_panels", fake_prepare)

    result = service.run_project(object(), "project-a", review_only_preview=True)

    assert result.state == module.ChapterState.NEEDS_REVIEW
    assert captured["review_only_auto_override"] is True


def test_review_project_restores_prepared_manifest_before_cold_prepare(monkeypatch):
    module = _module()
    from types import SimpleNamespace

    panels = _panels(module)
    failed = module.ChapterJobRecord(
        job_id="project-a",
        state=module.ChapterState.NEEDS_REVIEW,
        error_code="cloud.narrative_not_grounded",
        stage_results={
            "prepared_panel_manifest": {"manifest": "durable"},
            "segmentation": {"status": "RECONCILED"},
        },
    )

    class Store:
        def load(self, _project_id):
            return failed

        def save(self, _record):
            return None

    service = module.CloudBatchService.__new__(module.CloudBatchService)
    service.runner = SimpleNamespace(
        model_identity=SimpleNamespace(identity_hash="m" * 64),
        assess_strip_boundaries=lambda _request: {},
    )
    service.store = Store()
    service.review_root = None
    restored = {}

    def restore(_db, _project_id, manifest):
        restored["manifest"] = manifest
        return panels, {"status": "RECONCILED"}

    monkeypatch.setattr(module, "_restore_project_prepared_manifest", restore)
    monkeypatch.setattr(
        module,
        "prepare_project_panels",
        lambda *_args, **_kwargs: pytest.fail("review resume must not cold-prepare a durable manifest"),
    )
    monkeypatch.setattr(service, "run_job", lambda *_args, **_kwargs: failed)

    result = service.run_project(object(), "project-a", review_only_preview=True)

    assert result is failed
    assert restored["manifest"] == {"manifest": "durable"}


def test_review_project_falls_back_to_current_materialization_on_stale_metadata_cache(
    monkeypatch,
):
    module = _module()
    from types import SimpleNamespace

    panels = _panels(module, "stale-metadata")
    first_failure = module.ChapterJobRecord(
        job_id="project-a",
        state=module.ChapterState.NEEDS_REVIEW,
        error_code="cloud.prepared_manifest_requires_materialization",
        stage_results={
            "prepared_panel_manifest": {"manifest": "durable"},
            "segmentation": {"status": "RECONCILED"},
        },
    )
    second_failure = module.ChapterJobRecord(
        job_id="project-a",
        state=module.ChapterState.NEEDS_REVIEW,
        error_code="cloud.narrative_not_grounded",
        stage_results={},
    )

    class Store:
        def load(self, _project_id):
            return first_failure

        def save(self, _record):
            return None

    service = module.CloudBatchService.__new__(module.CloudBatchService)
    service.runner = SimpleNamespace(
        model_identity=SimpleNamespace(identity_hash="m" * 64),
        assess_strip_boundaries=lambda _request: {},
    )
    service.store = Store()
    service.review_root = None
    prepared = {}
    monkeypatch.setattr(
        module,
        "_restore_project_prepared_manifest",
        lambda _db, _project_id, _manifest: (panels, {"status": "RECONCILED"}),
    )
    monkeypatch.setattr(
        module,
        "_build_project_prepared_manifest",
        lambda *_args, **_kwargs: {"manifest": "rebuilt"},
    )

    def fake_prepare(_db, _project_id, **kwargs):
        prepared.update(kwargs)
        return panels, {"status": "RECONCILED"}

    monkeypatch.setattr(module, "prepare_project_panels", fake_prepare)
    calls = []

    def fake_run_job(_job_id, passed_panels):
        calls.append(tuple(passed_panels))
        return first_failure if len(calls) == 1 else second_failure

    monkeypatch.setattr(service, "run_job", fake_run_job)

    result = service.run_project(object(), "project-a", review_only_preview=True)

    assert result is second_failure
    assert calls == [panels, panels]
    assert prepared["review_only_auto_override"] is True
    assert prepared["cached_segmentation"] == {"status": "RECONCILED"}


def test_review_preview_failure_code_keeps_nested_stable_code():
    module = _module()

    assert module._review_failure_code(
        "reference_planning_failed: visual.visual_unavailable: no feasible panel"
    ) == "visual.visual_unavailable"
    assert module._review_failure_code(
        "reference.subtitle_overflow: review preview failed"
    ) == "reference.subtitle_overflow"
    assert module._review_failure_code("unstructured local failure") == "review.preview_failed"


def test_review_preview_failure_code_keeps_subtitle_stable_code():
    module = _module()

    assert module._review_failure_code(
        "subtitle.timing_out_of_bounds: sentence karaoke contract is invalid"
    ) == "subtitle.timing_out_of_bounds"


@pytest.mark.parametrize(
    "failure_code",
    ("cloud.narrative_not_grounded", "cloud.narrative_duration_out_of_range"),
)
def test_review_project_repairs_after_initial_narration_failure(monkeypatch, failure_code):
    module = _module()
    from types import SimpleNamespace

    panels = _panels(module)
    dropped_panel = replace(
        panels[0],
        panel_id="dropped-panel",
        source_asset_id="dropped-asset",
        source_order=999,
    )
    all_panels = panels + (dropped_panel,)
    visual = module.VisualStageResult(
        panels=tuple(_visual_row(panel.descriptor()) for panel in panels),
        source_hash="v" * 64,
        model_identity_hash="m" * 64,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="p" * 64,
    )
    story_map = module.StoryMapResult(
        panel_ids=tuple(panel.panel_id for panel in panels),
        beats=({"beat_id": "beat-1", "panel_ids": [panel.panel_id for panel in panels]},),
        causal_chain=({"from_beat": "beat-1", "to_beat": "beat-1", "reason": "the visible sequence continues"},),
        claims=({"claim_id": "claim-1", "panel_ids": [panel.panel_id for panel in panels], "qualification": "the panels support this"},),
        story_map_hash="s" * 64,
        model_identity_hash="m" * 64,
        prompt_version="cloud-causal-map-v2",
        prompt_sha256="c" * 64,
    )
    failed = module.ChapterJobRecord(
        job_id="project-a",
        state=module.ChapterState.NEEDS_REVIEW,
        error_code=failure_code,
        stage_results={"visual": visual.as_dict(), "story_map": story_map.as_dict()},
    )
    partial_narration = SimpleNamespace(visual_evidence_hash=visual.visual_evidence_hash)

    class Store:
        def __init__(self):
            self.saved = []

        def load(self, _job_id):
            return None

        def save(self, record):
            self.saved.append(record)

    service = module.CloudBatchService.__new__(module.CloudBatchService)
    service.runner = SimpleNamespace(
        model_identity=SimpleNamespace(identity_hash="m" * 64),
        assess_strip_boundaries=lambda _request: {},
        prompts={"visual_narrative_repair": ("repair-v1", "r" * 64, "")},
        _last_narration_result=(
            partial_narration
            if failure_code == "cloud.narrative_duration_out_of_range"
            else None
        ),
    )
    service.store = Store()
    service.review_root = None
    monkeypatch.setattr(
        module,
        "prepare_project_panels",
        lambda *_args, **_kwargs: (all_panels, {"status": "RECONCILED"}),
    )
    monkeypatch.setattr(
        module,
        "_build_project_prepared_manifest",
        lambda *_args, **_kwargs: {"manifest": "generated"},
    )
    monkeypatch.setattr(service, "run_job", lambda *_args, **_kwargs: failed)
    observed = {}

    def fake_repair(_db, _project_id, script_row, _panels, _result, **_kwargs):
        observed["script_row"] = script_row
        observed["result"] = _result
        observed["panel_ids"] = tuple(panel.panel_id for panel in _panels)
        return SimpleNamespace(
            narration=SimpleNamespace(
                as_dict=lambda: {"spoken_text": "repaired", "passages": []},
            ),
            visual=visual,
            story_map=story_map,
        ), SimpleNamespace(as_dict=lambda: {"entries": []}), ()

    monkeypatch.setattr(service, "_repair_review_narrative", fake_repair)
    monkeypatch.setattr(
        module,
        "persist_cloud_chapter",
            lambda *_args, **_kwargs: (
            SimpleNamespace(id="analysis-a"),
            SimpleNamespace(id="script-a", version=1, estimated_duration=50.0, sections=[]),
        ),
    )
    pipeline = importlib.import_module("app.services.pipeline")
    monkeypatch.setattr(pipeline, "build_timeline", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        pipeline,
        "render_silent_review_preview",
        lambda *_args, **_kwargs: (None, SimpleNamespace(as_dict=lambda: {"review": True})),
    )

    result = service.run_project(object(), "project-a", review_only_preview=True)

    assert observed["script_row"] is None
    if failure_code == "cloud.narrative_duration_out_of_range":
        assert observed["result"].narration is partial_narration
    else:
        assert observed["result"] is None
    assert observed["panel_ids"] == tuple(panel.panel_id for panel in panels)
    assert result.state == module.ChapterState.REVIEW_PREVIEW_READY, (result.error_code, result.error_message)


def test_review_repair_forwards_persisted_panel_crop_fallback(monkeypatch):
    module = _module()
    from pathlib import Path
    from types import SimpleNamespace

    pipeline = importlib.import_module("app.services.pipeline")
    reference_profile = importlib.import_module("app.services.reference_profile")
    repair = importlib.import_module("app.services.visual_narrative_repair")

    class Database:
        def get(self, *_args):
            return SimpleNamespace(template="reference_matched_shorts_v2")

    monkeypatch.setattr(
        reference_profile,
        "resolve_reference_profile",
        lambda _template: SimpleNamespace(),
    )
    monkeypatch.setattr(pipeline, "project_assets", lambda *_args: ())
    monkeypatch.setattr(pipeline, "image_assets", lambda *_args: ())
    monkeypatch.setattr(
        pipeline,
        "latest_analysis",
        lambda *_args: SimpleNamespace(panel_regions=()),
    )
    observed = {}

    def fake_load(*_args, **kwargs):
        observed.update(kwargs)
        return (SimpleNamespace(panel_id="panel-1"),)

    monkeypatch.setattr(
        pipeline,
        "_load_reference_panel_fallback_candidates",
        fake_load,
    )
    monkeypatch.setattr(
        repair,
        "default_section_to_beats",
        lambda *_args: {"hook": ("beat-1",)},
    )
    monkeypatch.setattr(
        repair,
        "build_feasible_visual_ledger",
        lambda *_args, **_kwargs: SimpleNamespace(entries=("entry",)),
    )
    monkeypatch.setattr(repair, "missing_visual_sections", lambda *_args: ())

    service = module.CloudBatchService.__new__(module.CloudBatchService)
    service.runner = SimpleNamespace(
        model_identity=SimpleNamespace(identity_hash="m" * 64),
    )
    result = SimpleNamespace(
        visual=SimpleNamespace(),
        story_map=SimpleNamespace(
            beats=({"beat_id": "beat-1", "panel_ids": ["panel-1"]},),
        ),
        narration=SimpleNamespace(),
    )

    outcome = service._repair_review_narrative(
        Database(),
        "project-a",
        SimpleNamespace(sections=({"section": "hook"},)),
        (),
        result,
        review_source_upscale_policy="review_silent_source_upscale_v1",
        review_source_root=Path("/review"),
    )

    assert outcome[0] is result
    assert observed["allow_persisted_panel_crop_fallback"] is True


def test_persisted_review_reuses_exact_prepared_panel_payloads(monkeypatch):
    """A durable script must not force review back through segmented DB bytes."""
    module = _module()
    from pathlib import Path
    from types import SimpleNamespace

    pipeline = importlib.import_module("app.services.pipeline")
    reference_profile = importlib.import_module("app.services.reference_profile")
    repair = importlib.import_module("app.services.visual_narrative_repair")
    panels = _panels(module)
    observed = {}

    class Database:
        def get(self, *_args):
            return SimpleNamespace(template="reference_matched_shorts_v2")

    monkeypatch.setattr(
        reference_profile,
        "resolve_reference_profile",
        lambda _template: SimpleNamespace(),
    )
    monkeypatch.setattr(pipeline, "project_assets", lambda *_args: ())
    monkeypatch.setattr(pipeline, "image_assets", lambda *_args: ())
    monkeypatch.setattr(
        pipeline,
        "latest_analysis",
        lambda *_args: SimpleNamespace(panel_regions=()),
    )
    monkeypatch.setattr(
        pipeline,
        "_load_reference_panel_fallback_candidates",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        module,
        "_build_ephemeral_review_candidates",
        lambda received_panels, *_args, **_kwargs: (
            observed.update({"panels": received_panels}) or ("candidate",),
            {"hook": ("beat-1",)},
        ),
    )
    monkeypatch.setattr(
        repair,
        "default_section_to_beats",
        lambda *_args: {"hook": ("beat-1",)},
    )

    def fake_ledger(candidates, *_args, **_kwargs):
        return SimpleNamespace(entries=("entry",) if tuple(candidates) else ())

    monkeypatch.setattr(repair, "build_feasible_visual_ledger", fake_ledger)
    monkeypatch.setattr(
        repair,
        "missing_visual_sections",
        lambda ledger, *_args: () if ledger.entries else ("hook",),
    )

    service = module.CloudBatchService.__new__(module.CloudBatchService)
    service.runner = SimpleNamespace(
        model_identity=SimpleNamespace(identity_hash="m" * 64),
    )
    result = SimpleNamespace(
        visual=SimpleNamespace(),
        story_map=SimpleNamespace(
            beats=({"beat_id": "beat-1", "panel_ids": [panel.panel_id for panel in panels]},),
        ),
        narration=SimpleNamespace(),
    )

    outcome = service._repair_review_narrative(
        Database(),
        "project-a",
        SimpleNamespace(sections=({"section": "hook"},)),
        panels,
        result,
        review_source_upscale_policy="review_silent_source_upscale_v1",
        review_source_root=Path("/review"),
    )

    assert observed["panels"] is panels
    assert outcome[0] is result
    assert outcome[1].entries == ("entry",)
    assert outcome[2] == ()


def test_ephemeral_review_registry_allows_title_visual_row_without_story_candidate():
    module = _module()
    import io

    from PIL import Image

    profile_module = importlib.import_module("app.services.reference_profile")
    payload = io.BytesIO()
    Image.new("RGB", (64, 64), (80, 90, 100)).save(payload, format="PNG")
    panels = tuple(
        module.CloudPanelInput(
            panel_id=f"panel-{index}",
            source_asset_id=f"asset-{index}",
            source_order=index,
            mime_type="image/png",
            payload=payload.getvalue(),
            panel_bounds=(0, 0, 64, 64),
            source_dimensions=(64, 64),
        )
        for index in range(6)
    )
    visual = module.VisualStageResult(
        panels=tuple(_visual_row(panel.descriptor()) for panel in panels),
        source_hash="v" * 64,
        model_identity_hash="m" * 64,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="p" * 64,
    )
    story_map = module.StoryMapResult(
        panel_ids=tuple(panel.panel_id for panel in panels),
        beats=tuple(
            {"beat_id": f"beat-{index}", "panel_ids": [f"panel-{index}"]}
            for index in range(1, 6)
        ),
        causal_chain=(),
        claims=(),
        story_map_hash="s" * 64,
        model_identity_hash="m" * 64,
        prompt_version="cloud-causal-map-v2",
        prompt_sha256="c" * 64,
    )

    candidates, section_to_beats = module._build_ephemeral_review_candidates(
        panels,
        visual,
        story_map,
        profile=profile_module.REFERENCE_MATCHED_SHORTS_V2,
        review_source_upscale_policy=None,
    )

    assert len(candidates) == 5
    assert all(candidate.source_order > 0 for candidate in candidates)
    assert len(section_to_beats) == 5


def test_narration_observations_accept_provider_text_key_variants():
    """Provider text keys (content/description/assertion) must not be rejected."""
    module = _module()
    panels = _panels(module)
    visual = module.VisualStageResult(
        panels=tuple(
            {
                "panel_id": panel.panel_id,
                "source_asset_id": panel.source_asset_id,
                "source_order": panel.source_order,
                "observation": {
                    "panel_id": panel.panel_id,
                    "visible_facts": [{"description": f"fact-{panel.panel_id}"}],
                    "dialogue_or_ocr": [{"content": "say"}, {"detected_text": "sfx"}],
                    "inferences": [{"assertion": "implied"}],
                    "uncertainties": [{"issue": "unknown"}],
                    "evidence_refs": [panel.panel_id],
                },
            }
            for panel in panels
        ),
        source_hash="s" * 64,
        model_identity_hash="m" * 64,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="p" * 64,
    )
    observations, _structural = module.CloudStageRunner._narration_observations(
        visual, panels
    )
    assert len(observations) == len(panels)
    assert observations[0]["visible_facts"] == [f"fact-{panels[0].panel_id}"]
    assert "say" in observations[0]["dialogue_or_ocr"]


def test_prepare_project_panels_reindexes_canonical_story_orders_after_gutters(monkeypatch):
    module = _module()
    segmentation = importlib.import_module("app.services.segmentation")
    pipeline = importlib.import_module("app.services.pipeline")
    from types import SimpleNamespace

    input_row = segmentation.SourceAssetInput(
        source_asset_id="asset-a",
        original_checksum="a" * 64,
        original_width=100,
        original_height=200,
        source_bounds=(0, 0, 100, 200),
        strip_order=0,
        region_order=0,
        payload=b"panel-payload",
        decoded_width=100,
        decoded_height=200,
    )
    regions = tuple(
        segmentation.CoverageRegion(
            region_id=f"region-{index}",
            source_asset_id="asset-a",
            source_order=source_order,
            bounds=(0, source_order * 40, 100, source_order * 40 + 40),
            region_class="canonical_panel",
            area=4_000,
            confidence=0.99,
            evidence="provider-confirmed panel",
        )
        for index, source_order in enumerate((1, 3))
    )
    coverage = segmentation.CoverageMap(
        version="coverage-v1",
        map_sha256="b" * 64,
        source_asset_ids=("asset-a",),
        tiles=(),
        regions=regions,
        source_content_coverage_ratio=1.0,
        canonical_panel_area=8_000,
        verified_gutter_area=0,
        unresolved_material_area=0,
        panel_count=2,
        reconciliation_errors=(),
    )
    monkeypatch.setattr(pipeline, "project_assets", lambda _db, _project_id: ())
    monkeypatch.setattr(pipeline, "image_assets", lambda assets: list(assets))
    monkeypatch.setattr(pipeline, "_build_source_inputs", lambda _assets: ((input_row,), {"asset-a": SimpleNamespace(id="asset-a")}))
    monkeypatch.setattr(module.strip_segmentation, "reconcile_sources", lambda *_args, **_kwargs: SimpleNamespace(status="RECONCILED"))
    monkeypatch.setattr(segmentation, "build_complete_coverage_map", lambda *_args, **_kwargs: coverage)
    monkeypatch.setattr(segmentation, "verify_segmentation_completeness", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(pipeline, "_encode_panel_payload", lambda *_args, **_kwargs: b"panel-payload")

    panels = module.prepare_project_panels(object(), "project-a")

    assert [panel.source_order for panel in panels] == [0, 1]


def test_prepare_project_panels_admission_funnel_precedes_panel_sink(monkeypatch):
    module = _module()
    segmentation = importlib.import_module("app.services.segmentation")
    pipeline = importlib.import_module("app.services.pipeline")
    from types import SimpleNamespace

    input_row = segmentation.SourceAssetInput(
        source_asset_id="asset-funnel",
        original_checksum="a" * 64,
        original_width=100,
        original_height=200,
        source_bounds=(0, 0, 100, 200),
        strip_order=0,
        region_order=0,
        payload=b"funnel-payload",
        decoded_width=100,
        decoded_height=200,
    )
    coverage = segmentation.CoverageMap(
        version="coverage-v1",
        map_sha256="b" * 64,
        source_asset_ids=("asset-funnel",),
        tiles=(),
        regions=(
            segmentation.CoverageRegion(
                region_id="funnel-panel",
                source_asset_id="asset-funnel",
                source_order=0,
                bounds=(0, 0, 100, 100),
                region_class="canonical_panel",
                area=10_000,
                confidence=0.99,
                evidence="provider-confirmed panel",
            ),
            segmentation.CoverageRegion(
                region_id="funnel-gutter",
                source_asset_id="asset-funnel",
                source_order=1,
                bounds=(0, 100, 100, 200),
                region_class="verified_gutter",
                area=10_000,
                confidence=0.99,
                evidence="local-flat-separator",
            ),
        ),
        source_content_coverage_ratio=1.0,
        canonical_panel_area=10_000,
        verified_gutter_area=10_000,
        unresolved_material_area=0,
        panel_count=1,
        reconciliation_errors=(),
    )
    monkeypatch.setattr(
        pipeline,
        "project_assets",
        lambda _db, _project_id: (SimpleNamespace(id="asset-funnel", type="image"),),
    )
    monkeypatch.setattr(pipeline, "image_assets", lambda assets: list(assets))
    monkeypatch.setattr(
        pipeline,
        "_build_source_inputs",
        lambda _assets: ((input_row,), {"asset-funnel": SimpleNamespace(id="asset-funnel")}),
    )
    monkeypatch.setattr(
        module.strip_segmentation,
        "reconcile_sources",
        lambda *_args, **_kwargs: SimpleNamespace(status="RECONCILED"),
    )
    monkeypatch.setattr(segmentation, "build_complete_coverage_map", lambda *_args, **_kwargs: coverage)
    monkeypatch.setattr(segmentation, "verify_segmentation_completeness", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(pipeline, "_encode_panel_payload", lambda *_args, **_kwargs: b"funnel-payload")
    submitted = []

    panels, segmentation_state = module.prepare_project_panels(
        object(),
        "project-funnel",
        panel_sink=submitted.append,
        return_segmentation=True,
    )

    assert [panel.panel_id for panel in panels] == ["funnel-panel"]
    assert [panel.panel_id for panel in submitted] == ["funnel-panel"]
    assert segmentation_state["panel_admission"]["counts"] == {
        "raw_input_images": 1,
        "ingest_assets": 1,
        "candidate_regions": 2,
        "canonical_regions": 1,
        "admitted_vision_panels": 1,
        "rejected_non_panel": 1,
        "deduped": 0,
        "merged": 0,
        "needs_review": 0,
    }


def test_prepare_project_panels_streams_each_admitted_panel_before_next_payload(monkeypatch):
    module = _module()
    segmentation = importlib.import_module("app.services.segmentation")
    pipeline = importlib.import_module("app.services.pipeline")
    from types import SimpleNamespace

    input_row = segmentation.SourceAssetInput(
        source_asset_id="asset-stream-order",
        original_checksum="a" * 64,
        original_width=100,
        original_height=300,
        source_bounds=(0, 0, 100, 300),
        strip_order=0,
        region_order=0,
        payload=b"stream-order-payload",
        decoded_width=100,
        decoded_height=300,
    )
    coverage = segmentation.CoverageMap(
        version="coverage-v1",
        map_sha256="b" * 64,
        source_asset_ids=("asset-stream-order",),
        tiles=(),
        regions=tuple(
            segmentation.CoverageRegion(
                region_id=panel_id,
                source_asset_id="asset-stream-order",
                source_order=index,
                bounds=(0, index * 100, 100, (index + 1) * 100),
                region_class="canonical_panel",
                area=10_000,
                confidence=0.99,
                evidence="provider-confirmed panel",
            )
            for index, panel_id in enumerate(("stream-panel-0", "stream-panel-1"))
        )
        + (
            segmentation.CoverageRegion(
                region_id="stream-gutter",
                source_asset_id="asset-stream-order",
                source_order=2,
                bounds=(0, 200, 100, 300),
                region_class="verified_gutter",
                area=10_000,
                confidence=0.99,
                evidence="local-flat-separator",
            ),
        ),
        source_content_coverage_ratio=1.0,
        canonical_panel_area=20_000,
        verified_gutter_area=10_000,
        unresolved_material_area=0,
        panel_count=2,
        reconciliation_errors=(),
    )
    monkeypatch.setattr(
        pipeline,
        "project_assets",
        lambda _db, _project_id: (SimpleNamespace(id="asset-stream-order", type="image"),),
    )
    monkeypatch.setattr(pipeline, "image_assets", lambda assets: list(assets))
    monkeypatch.setattr(
        pipeline,
        "_build_source_inputs",
        lambda _assets: ((input_row,), {"asset-stream-order": SimpleNamespace(id="asset-stream-order")}),
    )
    monkeypatch.setattr(
        module.strip_segmentation,
        "reconcile_sources",
        lambda *_args, **_kwargs: SimpleNamespace(status="RECONCILED"),
    )
    monkeypatch.setattr(segmentation, "build_complete_coverage_map", lambda *_args, **_kwargs: coverage)
    monkeypatch.setattr(segmentation, "verify_segmentation_completeness", lambda *_args, **_kwargs: ())
    events = []

    def encode(transient, _source_input):
        events.append(f"encode:{transient.panel_id}")
        return f"payload:{transient.panel_id}".encode()

    monkeypatch.setattr(pipeline, "_encode_panel_payload", encode)

    panels = module.prepare_project_panels(
        object(),
        "project-stream-order",
        panel_sink=lambda panel: events.append(f"sink:{panel.panel_id}"),
    )

    assert [panel.panel_id for panel in panels] == ["stream-panel-0", "stream-panel-1"]
    assert events.index("sink:stream-panel-0") < events.index("encode:stream-panel-1")


def test_prepare_project_panels_emits_inside_source_reconciliation_callback(monkeypatch):
    module = _module()
    segmentation = importlib.import_module("app.services.segmentation")
    pipeline = importlib.import_module("app.services.pipeline")
    from types import SimpleNamespace

    input_row = segmentation.SourceAssetInput(
        source_asset_id="asset-stream-callback",
        original_checksum="a" * 64,
        original_width=100,
        original_height=200,
        source_bounds=(0, 0, 100, 200),
        strip_order=0,
        region_order=0,
        payload=b"stream-callback-payload",
        decoded_width=100,
        decoded_height=200,
    )
    coverage = segmentation.CoverageMap(
        version="coverage-v1",
        map_sha256="b" * 64,
        source_asset_ids=("asset-stream-callback",),
        tiles=(),
        regions=tuple(
            segmentation.CoverageRegion(
                region_id=panel_id,
                source_asset_id="asset-stream-callback",
                source_order=index,
                bounds=(0, index * 100, 100, (index + 1) * 100),
                region_class="canonical_panel",
                area=10_000,
                confidence=0.99,
                evidence="provider-confirmed panel",
            )
            for index, panel_id in enumerate(("callback-panel-0", "callback-panel-1"))
        ),
        source_content_coverage_ratio=1.0,
        canonical_panel_area=20_000,
        verified_gutter_area=0,
        unresolved_material_area=0,
        panel_count=2,
        reconciliation_errors=(),
    )
    events = []

    monkeypatch.setattr(
        pipeline,
        "project_assets",
        lambda _db, _project_id: (SimpleNamespace(id="asset-stream-callback", type="image"),),
    )
    monkeypatch.setattr(pipeline, "image_assets", lambda assets: list(assets))
    monkeypatch.setattr(
        pipeline,
        "_build_source_inputs",
        lambda _assets: ((input_row,), {"asset-stream-callback": SimpleNamespace(id="asset-stream-callback")}),
    )

    def reconcile(*_args, **kwargs):
        events.append("reconcile:begin")
        callback = kwargs["on_reconciled"]
        callback(
            (input_row,),
            SimpleNamespace(source_asset_id="asset-stream-callback", status="RECONCILED"),
        )
        events.append("reconcile:return")
        return SimpleNamespace(status="RECONCILED", reports=(), as_dict=lambda: {"reports": []})

    monkeypatch.setattr(module.strip_segmentation, "reconcile_sources", reconcile)
    monkeypatch.setattr(segmentation, "build_complete_coverage_map", lambda *_args, **_kwargs: coverage)
    monkeypatch.setattr(segmentation, "verify_segmentation_completeness", lambda *_args, **_kwargs: ())

    def encode(transient, _source_input):
        events.append(f"encode:{transient.panel_id}")
        return f"payload:{transient.panel_id}".encode()

    monkeypatch.setattr(pipeline, "_encode_panel_payload", encode)

    panels = module.prepare_project_panels(
        object(),
        "project-stream-callback",
        panel_sink=lambda panel: events.append(f"sink:{panel.panel_id}"),
    )

    assert [panel.panel_id for panel in panels] == ["callback-panel-0", "callback-panel-1"]
    assert events.index("sink:callback-panel-0") < events.index("reconcile:return")


def test_stage_runner_reconciles_all_panels_with_local_hashes_and_pinned_identity():
    module = _module()
    provider = _FakeProvider()
    runner = module.CloudStageRunner(provider=provider, model_identity=_identity(module))

    result = runner.run_chapter(_panels(module))

    assert result.state == module.ChapterState.READY_TO_RENDER
    assert result.visual.reconciled is True
    assert [item["source_order"] for item in result.visual.panels] == [1, 2, 3]
    assert all(len(item["evidence_hash"]) == 64 for item in result.visual.panels)
    assert result.story_map.panel_ids == result.visual.panel_ids
    assert result.narration.display_words
    assert all(word == word.upper() and word.isalnum() for word in result.narration.display_words)
    assert result.narration.requires_voice_timing is True
    assert 50.0 <= result.narration.estimated_duration_s <= 60.0
    assert len({call[2] for call in provider.calls}) >= 3


def test_review_preview_state_is_distinct_from_voice_ready_render_state():
    module = _module()

    assert module.ChapterState.REVIEW_PREVIEW_READY.value == "REVIEW_PREVIEW_READY"
    result = type("Result", (), {"state": module.ChapterState.REVIEW_PREVIEW_READY})()
    assert module.regular_render_allowed(result) is False
    with pytest.raises(module.CloudStageError) as caught:
        module.require_final_render_ready(result)
    assert caught.value.code == "cloud.stage_not_ready"


def test_stage_runner_chunks_large_visual_requests_and_reconciles_full_order():
    module = _module()
    provider = _FakeProvider()
    runner = module.CloudStageRunner(provider=provider, model_identity=_identity(module))
    panels = tuple(
        module.CloudPanelInput(
            panel_id=f"large-chapter-panel-{index}",
            source_asset_id=f"large-chapter-asset-{index}",
            source_order=index,
            mime_type="image/png",
            payload=f"large-chapter-panel-payload-{index}".encode(),
        )
        for index in range(26)
    )

    result = runner.run_visual_evidence(panels)

    assert result.panel_ids == tuple(f"large-chapter-panel-{index}" for index in range(26))
    assert len([call for call in provider.calls if call[0] == "visual"]) == 4


def test_live_visual_request_panel_cap_is_bounded_for_response_size():
    module = _module()

    assert module.VISUAL_REQUEST_MAX_PANELS == 8
    assert module.VISUAL_REQUEST_OVERLAP == 0


def test_visual_chunk_budget_uses_provider_bound_payload_size():
    module = _module()
    import io

    from PIL import Image

    image = Image.effect_noise((900, 5334), 100).convert("RGB")
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=False)
    payload = output.getvalue()
    panels = tuple(
        module.CloudPanelInput(
            panel_id=f"payload-budget-panel-{index}",
            source_asset_id=f"payload-budget-asset-{index}",
            source_order=index,
            mime_type="image/png",
            payload=payload,
        )
        for index in range(13)
    )

    chunks = module._visual_panel_chunks(panels, max_panels=4, overlap=1)

    assert 1 < len(chunks) < len(panels)
    assert all(0 < len(chunk) <= 4 for chunk in chunks)
    assert {
        item.panel_id
        for chunk in chunks
        for item in chunk
    } == {item.panel_id for item in panels}


def test_large_visual_provider_payload_is_downsampled_without_mutating_panel():
    module = _module()
    import io

    from PIL import Image

    image = Image.effect_noise((900, 5334), 100).convert("RGB")
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=False)
    panel = module.CloudPanelInput(
        panel_id="large-panel",
        source_asset_id="large-asset",
        source_order=0,
        mime_type="image/png",
        payload=output.getvalue(),
    )

    payload, mime_type = module._visual_provider_payload(panel)

    assert mime_type == "image/jpeg"
    assert len(payload) < len(panel.payload)
    assert panel.mime_type == "image/png"
    assert panel.payload == output.getvalue()


def test_small_non_jpeg_visual_provider_payload_is_normalized_for_endpoint():
    module = _module()
    import io

    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (16, 16), (128, 128, 128)).save(output, format="PNG")
    panel = module.CloudPanelInput(
        panel_id="small-png-panel",
        source_asset_id="small-png-asset",
        source_order=0,
        mime_type="image/png",
        payload=output.getvalue(),
    )

    payload, mime_type = module._visual_provider_payload(panel)

    assert mime_type == "image/jpeg"
    assert payload != panel.payload
    with Image.open(io.BytesIO(payload)) as prepared:
        assert prepared.format == "JPEG"
        assert prepared.size == (16, 16)
    assert panel.mime_type == "image/png"
    assert panel.payload == output.getvalue()


def test_unknown_visual_geometry_blocks_before_story_mapping():
    module = _module()
    provider = _FakeProvider(unknown_visual=True)
    runner = module.CloudStageRunner(provider=provider, model_identity=_identity(module))

    with pytest.raises(module.CloudStageError) as caught:
        runner.run_chapter(_panels(module))

    assert caught.value.code == "visual.balloon_mask_unknown"
    assert caught.value.safe_metadata == {
        "stage": "visual",
        "chunk_index": 0,
        "panel_count": 3,
    }
    assert len([call for call in provider.calls if call[0] == "visual"]) >= 2


def test_unknown_visual_geometry_isolated_to_poison_panel():
    module = _module()

    class _OneUnknownProvider(_FakeProvider):
        def observe(self, request):
            rows = super().observe(request)
            for row, panel in zip(rows, request.panels, strict=True):
                if panel["panel_id"] == "chapter-a-panel-1":
                    visual = dict(row["visual_evidence"])
                    visual.update(
                        {
                            "balloon_mask_status": "unknown",
                            "mask_confidence": 0.0,
                            "evidence_source": "vision_geometry_unavailable",
                            "mask_reason": "geometry is unavailable",
                        }
                    )
                    row["visual_evidence"] = visual
            return rows

    provider = _OneUnknownProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        max_attempts=1,
    )

    result = runner.run_visual_evidence(_panels(module))

    assert result.reconciled is True
    assert result.panel_ids == ("chapter-a-panel-0", "chapter-a-panel-2")
    assert len([call for call in provider.calls if call[0] == "visual"]) == 5


def test_transient_unknown_visual_response_is_retried_atomically():
    module = _module()
    provider = _FakeProvider(transient_unknown_count=1)
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        max_attempts=2,
    )

    result = runner.run_visual_evidence(_panels(module))

    assert result.reconciled is True
    assert len([call for call in provider.calls if call[0] == "visual"]) == 2
    assert provider.analysis_run_ids[0] != provider.analysis_run_ids[1]


def test_call_preserves_known_provider_response_error_category():
    module = _module()
    from app.services import vision_adapter

    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=_identity(module),
        max_attempts=2,
    )

    def invalid_response():
        raise vision_adapter.VisionResponseInvalid()

    with pytest.raises(module.CloudStageError) as caught:
        runner._call(invalid_response, request_stage="other")

    assert caught.value.code == "cloud.provider_response_invalid"
    assert caught.value.safe_metadata == {
        "provider_error_code": "vision_response_invalid",
        "request_stage": "other",
    }


def test_transient_invalid_story_map_is_retried_atomically():
    module = _module()
    provider = _FakeProvider(transient_story_map_invalid_count=1)
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        max_attempts=2,
    )
    visual = runner.run_visual_evidence(_panels(module))

    result = runner.run_story_map(visual)

    assert result.panel_ids == visual.panel_ids
    assert len([call for call in provider.calls if call[0] == "story_map"]) == 2


def test_provider_hash_is_not_accepted_and_failure_is_sanitized():
    module = _module()
    provider = _FakeProvider(provider_hash=True)
    runner = module.CloudStageRunner(provider=provider, model_identity=_identity(module), max_attempts=2)

    with pytest.raises(module.CloudStageError) as caught:
        runner.run_chapter(_panels(module))

    assert caught.value.code == "cloud.provider_hash_forbidden"


def test_provider_failure_is_bounded_and_sanitized():
    module = _module()
    provider = _FakeProvider(fail_count=3)
    runner = module.CloudStageRunner(provider=provider, model_identity=_identity(module), max_attempts=2)

    with pytest.raises(module.CloudStageError) as caught:
        runner.run_chapter(_panels(module))

    assert caught.value.code == "cloud.provider_request_failed"
    assert "secret-bearing" not in str(caught.value)
    assert len(provider.calls) == 2


def test_cache_key_is_idempotent_and_changes_with_source_or_model():
    module = _module()
    provider = _FakeProvider()
    cache = module.MemoryStageCache()
    runner = module.CloudStageRunner(provider=provider, model_identity=_identity(module), cache=cache)
    panels = _panels(module)

    first = runner.run_chapter(panels)
    call_count = len(provider.calls)
    second = runner.run_chapter(panels)
    assert second == first
    assert len(provider.calls) == call_count

    changed = list(panels)
    changed[0] = replace(changed[0], payload=b"different-content", payload_checksum="")
    runner.run_chapter(tuple(changed))
    assert len(provider.calls) > call_count


def test_batch_isolates_failure_and_resumes_from_durable_stage(tmp_path):
    module = _module()
    store = module.JsonJobStore(tmp_path)
    provider = _FakeProvider(fail_for_prefix="bad")
    runner = module.CloudStageRunner(provider=provider, model_identity=_identity(module))
    batch = module.CloudBatchService(runner=runner, store=store)

    records = batch.run_batch({"good": _panels(module, "good"), "bad": _panels(module, "bad")})

    assert records["good"].state == module.ChapterState.READY_TO_RENDER
    assert records["bad"].state == module.ChapterState.FAILED
    assert records["bad"].error_code == "cloud.provider_request_failed"
    assert (tmp_path / "good.json").exists()
    assert (tmp_path / "bad.json").exists()

    provider.fail_for_prefix = ""
    resumed = batch.run_job("bad", _panels(module, "bad"))
    assert resumed.state == module.ChapterState.READY_TO_RENDER
    assert resumed.stage_results["visual"]["reconciled"] is True


def test_review_only_gate_never_invents_voice_word_timings():
    module = _module()
    provider = _FakeProvider()
    runner = module.CloudStageRunner(provider=provider, model_identity=_identity(module))
    result = runner.run_chapter(_panels(module))

    gate = module.review_only_render_gate(result)
    assert gate.allowed is True
    assert gate.audio_path is None
    assert gate.timing_source == "voice_required"
    with pytest.raises(module.CloudStageError) as caught:
        module.require_final_render_ready(result)
    assert caught.value.code == "cloud.voice_timing_required"


def test_reconciled_evidence_cannot_enter_regular_render_until_state_is_ready():
    module = _module()
    provider = _FakeProvider()
    runner = module.CloudStageRunner(provider=provider, model_identity=_identity(module))
    result = runner.run_chapter(_panels(module))
    assert module.regular_render_allowed(result) is False
    assert module.review_only_render_gate(result).publish_allowed is False


def test_project_persistence_reuses_regular_script_gate_without_approval(monkeypatch):
    module = _module()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db import Base
    from app.models import Project, SourceAsset, StoryAnalysis, User, Workspace

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        user = User(email="cloud-owner@example.com", name="Cloud Owner", password_hash="test")
        workspace = Workspace(owner=user, name="Cloud Workspace")
        project = Project(workspace=workspace, title="Cloud chapter", chapter="1")
        db.add_all([user, workspace, project])
        db.flush()

        stale = StoryAnalysis(
            project_id=project.id,
            analysis_run_id="stale-280-panel-analysis",
            state="RECONCILED",
            created_at=datetime.now(UTC) + timedelta(days=1),
        )
        db.add(stale)
        db.flush()

        panels = tuple(
            module.CloudPanelInput(
                panel_id=f"persist-panel-{index}",
                source_asset_id=f"persist-asset-{index}",
                source_order=index,
                mime_type="image/png",
                payload=f"persist-payload-{index}".encode(),
                panel_bounds=(0, 0, 100, 100),
                source_dimensions=(100, 100),
                strip_region_id=f"persist-region-{index}",
                coverage_map_version="cloud-coverage-v1",
                coverage_map_hash="c" * 64,
            )
            for index in range(3)
        )
        for panel in panels:
            db.add(
                SourceAsset(
                    id=panel.source_asset_id,
                    project_id=project.id,
                    type="image",
                    original_filename=f"{panel.panel_id}.png",
                    storage_key=f"cloud/{panel.panel_id}.png",
                    checksum=panel.source_checksum,
                    original_checksum=panel.source_checksum,
                    original_width=100,
                    original_height=100,
                    width=100,
                    height=100,
                )
            )
        db.flush()

        runner = module.CloudStageRunner(provider=_FakeProvider(), model_identity=_identity(module))
        result = runner.run_chapter(panels)
        validation_flags = []
        analyzer_contract = importlib.import_module("app.services.analyzer_contract")
        original_validate = analyzer_contract.validate_analyzer_output

        def capture_validation(output, **kwargs):
            validation_flags.append(kwargs.get("allow_dialogue_copy", False))
            return original_validate(output, **kwargs)

        monkeypatch.setattr(analyzer_contract, "validate_analyzer_output", capture_validation)
        analysis, script = module.persist_cloud_chapter(
            db,
            project.id,
            panels,
            result,
            model_identity=runner.model_identity,
        )

        assert analysis.state == "SCRIPT_DRAFT"
        assert script.generator == "vision_evidence_v3"
        assert script.editorial_metadata["editorial_review_confirmed"] is False
        assert script.editorial_metadata["narrative_identity"]["profile_id"] == "sharp_friend_v1"
        assert len(analysis.panel_regions) == 3
        assert validation_flags == [False, False]
        assert module.regular_render_allowed(result) is False


def test_persistence_round_trip_retains_701_prepared_panels_and_source_order():
    module = _module()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db import Base
    from app.models import Project, SourceAsset, StoryAnalysis, User, Workspace

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    source_orders = [index if index < 699 else index + 2 for index in range(701)]
    panels = tuple(
        module.CloudPanelInput(
            panel_id=f"roundtrip-panel-{index}",
            source_asset_id=f"roundtrip-asset-{index}",
            source_order=source_order,
            prepared_order=index,
            mime_type="image/png",
            payload=f"roundtrip-payload-{index}".encode(),
            panel_bounds=(0, 0, 100, 100),
            source_dimensions=(100, 100),
            strip_region_id=f"roundtrip-region-{index}",
            coverage_map_version="cloud-coverage-v1",
            coverage_map_hash="c" * 64,
        )
        for index, source_order in enumerate(source_orders)
    )

    with Session(engine) as db:
        user = User(email="roundtrip-owner@example.com", name="Roundtrip Owner", password_hash="test")
        workspace = Workspace(owner=user, name="Roundtrip Workspace")
        project = Project(workspace=workspace, title="701-panel chapter", chapter="1")
        db.add_all([user, workspace, project])
        db.flush()
        db.add_all(
            [
                SourceAsset(
                    id=panel.source_asset_id,
                    project_id=project.id,
                    type="image",
                    original_filename=f"{panel.panel_id}.png",
                    storage_key=f"cloud/{panel.panel_id}.png",
                    checksum=panel.source_checksum,
                    original_checksum=panel.source_checksum,
                    original_width=100,
                    original_height=100,
                    width=100,
                    height=100,
                )
                for panel in panels
            ]
        )
        db.flush()

        runner = module.CloudStageRunner(
            provider=_FakeProvider(),
            model_identity=_identity(module),
        )
        small_result = runner.run_chapter(_panels(module, "roundtrip-fixture"))
        panel_ids = [panel.panel_id for panel in panels]
        visual_rows = []
        for panel in panels:
            visual_row = _visual_row(panel.descriptor())
            visual_row["source_checksum"] = panel.source_checksum
            visual_rows.append(visual_row)
        visual = module.VisualStageResult(
            panels=tuple(visual_rows),
            source_hash="roundtrip-701-source",
            model_identity_hash=small_result.visual.model_identity_hash,
            prompt_version=small_result.visual.prompt_version,
            prompt_sha256=small_result.visual.prompt_sha256,
        )
        continuity = json.loads(json.dumps(small_result.narration.continuity_ledger))
        continuity["chunks"] = [
            {**dict(continuity["chunks"][0]), "panel_ids": panel_ids}
        ]
        for entity in continuity["entities"]:
            entity["panel_ids"] = panel_ids
        for motive in continuity["motives"]:
            motive["evidence_panel_ids"] = panel_ids
        for change in continuity["state_changes"]:
            change["evidence_panel_ids"] = panel_ids
        for link in continuity["causal_links"]:
            link["from_panel_id"] = panel_ids[0]
            link["to_panel_id"] = panel_ids[-1]
            link["evidence_panel_ids"] = panel_ids
        observations = []
        for index, panel in enumerate(panels):
            observation = dict(small_result.narration.observations[index % len(small_result.narration.observations)])
            observation.update(
                {
                    "panel_id": panel.panel_id,
                    "source_asset_id": panel.source_asset_id,
                    "evidence_refs": [panel.panel_id],
                    "source_index": index,
                }
            )
            observations.append(observation)
        evidence_graph = json.loads(json.dumps(small_result.narration.evidence_graph))
        for claim in evidence_graph["claims"]:
            claim["evidence_panel_ids"] = panel_ids
        passages = tuple(
            {**dict(passage), "evidence_panel_ids": panel_ids}
            for passage in small_result.narration.passages
        )
        narration = replace(
            small_result.narration,
            observations=tuple(observations),
            continuity_ledger=continuity,
            evidence_graph=evidence_graph,
            passages=passages,
            visual_evidence_hash=visual.visual_evidence_hash,
        )
        story_map = replace(
            small_result.story_map,
            panel_ids=tuple(panel_ids),
            visual_evidence_hash=visual.visual_evidence_hash,
        )
        result = module.ChapterResult(
            state=module.ChapterState.READY_TO_RENDER,
            visual=visual,
            story_map=story_map,
            narration=narration,
        )
        analysis, _script = module.persist_cloud_chapter(
            db,
            project.id,
            panels,
            result,
            model_identity=runner.model_identity,
        )
        analysis_id = analysis.id
        db.commit()

    with Session(engine) as db:
        persisted = db.get(StoryAnalysis, analysis_id)
        assert persisted is not None
        regions = sorted(
            persisted.panel_regions,
            key=lambda row: row.observation_json["source_index"],
        )
        assert len(regions) == 701
        assert [row.observation_json["source_index"] for row in regions] == list(range(701))
        assert [row.source_order for row in regions] == source_orders
        assert [row.panel_id for row in regions] == [panel.panel_id for panel in panels]
        assert persisted.coverage_manifest_json["panel_ids"] == [panel.panel_id for panel in panels]
        assert persisted.coverage_manifest_json["total_panels"] == 701
        assert persisted.coverage_manifest_json["processed_panels"] == 701
        assert persisted.coverage_manifest_json["processed_canonical_panel_count"] == 701


def test_generate_script_rejects_foreign_analysis_id_before_materialization():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db import Base
    from app.models import Project, StoryAnalysis, User, Workspace
    from app.services import pipeline

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        user = User(email="foreign-analysis@example.com", name="Foreign Analysis", password_hash="test")
        workspace = Workspace(owner=user, name="Foreign Analysis Workspace")
        requested = Project(workspace=workspace, title="Requested", chapter="1")
        owner = Project(workspace=workspace, title="Owner", chapter="2")
        db.add_all([user, workspace, requested, owner])
        db.flush()
        foreign = StoryAnalysis(project_id=owner.id, state="RECONCILED")
        db.add(foreign)
        db.flush()

        with pytest.raises(pipeline.PipelineError, match="analysis_project_mismatch"):
            pipeline.generate_script(
                db,
                requested.id,
                analysis_id=foreign.id,
                narrative_profile_id="sharp_friend_v1",
            )


def test_persistence_failure_rolls_back_uncommitted_analysis_and_regions(monkeypatch):
    module = _module()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db import Base
    from app.models import PanelRegion, Project, SourceAsset, StoryAnalysis, User, Workspace
    from app.services import pipeline

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        user = User(email="rollback-owner@example.com", name="Rollback Owner", password_hash="test")
        workspace = Workspace(owner=user, name="Rollback Workspace")
        project = Project(workspace=workspace, title="Rollback chapter", chapter="1")
        db.add_all([user, workspace, project])
        db.flush()
        panels = _panels(module, "rollback")
        db.add_all(
            [
                SourceAsset(
                    id=panel.source_asset_id,
                    project_id=project.id,
                    type="image",
                    original_filename=f"{panel.panel_id}.png",
                    storage_key=f"cloud/{panel.panel_id}.png",
                    checksum=panel.source_checksum,
                    original_checksum=panel.source_checksum,
                    original_width=100,
                    original_height=100,
                    width=100,
                    height=100,
                )
                for panel in panels
            ]
        )
        db.flush()
        runner = module.CloudStageRunner(
            provider=_FakeProvider(),
            model_identity=_identity(module),
        )
        result = runner.run_chapter(panels)

        def fail_after_flush(*args, **kwargs):
            raise RuntimeError("intentional persistence boundary failure")

        monkeypatch.setattr(pipeline, "generate_script", fail_after_flush)
        with pytest.raises(module.CloudStageError) as caught:
            module.persist_cloud_chapter(
                db,
                project.id,
                panels,
                result,
                model_identity=runner.model_identity,
            )
        assert caught.value.code == "cloud.persistence_failed"
        db.rollback()
        assert db.query(StoryAnalysis).count() == 0
        assert db.query(PanelRegion).count() == 0


def test_batch_operator_entrypoint_exposes_resume_safe_project_options():
    try:
        cli = importlib.import_module("scripts.run_cloud_multimodal_batch")
    except Exception as exc:
        pytest.fail(f"cloud batch CLI import failed in test body: {exc}")

    parser = cli.build_parser()
    options = parser.parse_args(
        [
            "--project-id",
            "project-a",
            "--project-id",
            "project-b",
            "--state-dir",
            "ignored/cloud-jobs",
            "--model",
            "pinned-model",
        ]
    )
    assert options.project_id == ["project-a", "project-b"]
    assert options.state_dir == "ignored/cloud-jobs"
    assert options.model == "pinned-model"
    assert callable(cli.main)



def test_visual_checkpoint_is_scoped_and_durable(tmp_path):
    module = _module()
    checkpoint = tmp_path / "visual-checkpoints.jsonl"
    first_provider = _FakeProvider()
    first = module.CloudStageRunner(
        provider=first_provider,
        model_identity=_identity(module),
        visual_checkpoint_path=checkpoint,
    )
    first_result = first.run_visual_evidence(_panels(module, "checkpoint"))
    assert first_result.reconciled is True
    assert len([call for call in first_provider.calls if call[0] == "visual"]) == 1

    resumed_provider = _FakeProvider()
    resumed = module.CloudStageRunner(
        provider=resumed_provider,
        model_identity=_identity(module),
        visual_checkpoint_path=checkpoint,
    )
    resumed_result = resumed.run_visual_evidence(_panels(module, "checkpoint"))
    assert resumed_result == first_result
    assert not [call for call in resumed_provider.calls if call[0] == "visual"]

    unrelated_provider = _FakeProvider()
    unrelated = module.CloudStageRunner(
        provider=unrelated_provider,
        model_identity=_identity(module),
        visual_checkpoint_path=checkpoint,
    )
    unrelated.run_visual_evidence(_panels(module, "unrelated"))
    assert len([call for call in unrelated_provider.calls if call[0] == "visual"]) == 1


def test_cached_visual_reanalyzes_only_rows_without_visible_facts():
    module = _module()
    panels = _panels(module, "cached-facts")
    identity = _identity(module)
    seed_provider = _FakeProvider()
    seed_runner = module.CloudStageRunner(
        provider=seed_provider,
        model_identity=identity,
        cache=module.MemoryStageCache(),
    )
    valid = seed_runner.run_visual_evidence(panels)
    invalid_rows = [dict(row) for row in valid.panels]
    invalid_observation = dict(invalid_rows[1]["observation"])
    invalid_observation["visible_facts"] = []
    invalid_rows[1]["observation"] = invalid_observation
    invalid = replace(valid, panels=tuple(invalid_rows))
    cache = module.MemoryStageCache()
    cache.put(
        module._cache_key(
            "visual",
            list(module._visual_panel_identities(panels)),
            identity,
            seed_runner.prompts["visual"],
        ),
        invalid.as_dict(),
    )

    class _RepairProvider(_FakeProvider):
        def __post_init__(self):
            super().__post_init__()
            self.request_panel_ids = []

        def observe(self, request):
            self.request_panel_ids.append(
                tuple(panel["panel_id"] for panel in request.panels)
            )
            return super().observe(request)

    provider = _RepairProvider()
    resumed = module.CloudStageRunner(
        provider=provider,
        model_identity=identity,
        cache=cache,
        max_attempts=1,
    )
    result = resumed.run_visual_evidence(panels)

    assert provider.request_panel_ids == [(panels[1].panel_id,)]
    assert result.panels[1]["observation"]["visible_facts"] == [
        f"visible fact {panels[1].source_order}"
    ]


def test_metadata_only_visual_repair_materializes_only_invalid_rows(monkeypatch):
    module = _module()
    pipeline = importlib.import_module("app.services.pipeline")
    panels = _panels(module, "metadata-repair")
    metadata_panels = tuple(
        replace(
            panel,
            payload=b"prepared-panel-manifest-v2:" + ("a" * 64).encode(),
            payload_checksum="",
            identity_payload_checksum="a" * 64,
            identity_descriptor_hash="b" * 64,
            source_identity_hash="c" * 64,
            metadata_only=True,
        )
        for panel in panels
    )
    target = metadata_panels[1]
    asset = SimpleNamespace(id=target.source_asset_id)
    source_input = SimpleNamespace(
        source_asset_id=target.source_asset_id,
        original_checksum=target.source_checksum,
        original_width=100,
        original_height=100,
        source_bounds=(0, 0, 100, 100),
        decoded_width=100,
        decoded_height=100,
        payload=b"source-bytes",
    )
    encoded_panel_ids = []
    monkeypatch.setattr(
        pipeline,
        "project_assets",
        lambda db, project_id: (asset,),
    )
    monkeypatch.setattr(pipeline, "image_assets", lambda assets: tuple(assets))
    monkeypatch.setattr(
        pipeline,
        "_build_source_inputs",
        lambda assets: ((source_input,), {target.source_asset_id: asset}),
    )
    monkeypatch.setattr(
        pipeline,
        "_encode_panel_payload",
        lambda panel, source: encoded_panel_ids.append(panel.panel_id) or b"real-png",
    )

    materialized = module._materialize_metadata_only_panels(
        object(),
        "project-1",
        metadata_panels,
        required_panel_ids=(target.panel_id,),
    )

    assert encoded_panel_ids == [target.panel_id]
    assert materialized[0].metadata_only is True
    assert materialized[1].metadata_only is False
    assert materialized[1].payload == b"real-png"
    assert materialized[1].identity_descriptor_hash == "b" * 64
    assert materialized[2].metadata_only is True


def test_resume_discovers_exact_cached_visual_subset_without_provider_call():
    module = _module()
    panels = _panels(module, "subset-cache")
    identity = _identity(module)
    seed_runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=identity,
        cache=module.MemoryStageCache(),
    )
    valid = seed_runner.run_visual_evidence(panels)
    subset = replace(
        valid,
        panels=tuple(valid.panels[:2]),
        source_hash="subset-source",
    )
    cache = module.MemoryStageCache()
    cache.put("durable-subset", subset.as_dict())
    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=identity,
        cache=cache,
    )

    found = module._find_cached_visual_subset(
        runner,
        panels,
        expected_source_hash="subset-source",
    )

    assert found is not None
    assert found.panel_ids == tuple(panel.panel_id for panel in panels[:2])


def test_resume_discovers_checkpoint_visual_subset_without_scanning_stage_json(tmp_path):
    module = _module()
    panels = _panels(module, "checkpoint-subset")
    identity = _identity(module)
    seed_runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=identity,
        cache=module.MemoryStageCache(),
    )
    valid = seed_runner.run_visual_evidence(panels)
    checkpoint_path = tmp_path / "visual_checkpoints.jsonl"
    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=identity,
        cache=type(
            "NoScanCache",
            (),
            {"iter_records": lambda self: (_ for _ in ()).throw(AssertionError("stage scan"))},
        )(),
        visual_checkpoint_path=checkpoint_path,
    )
    scope = runner._checkpoint_scope([], runner.prompts["visual"])
    rows = [
        {
            **dict(row),
            "checkpoint_scope": scope,
            "checkpoint_version": module.VISUAL_CHECKPOINT_VERSION,
        }
        for row in valid.panels[:2]
    ]
    rows[0]["observation"] = {
        **rows[0]["observation"],
        "visible_facts": [],
    }
    checkpoint_path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )

    found = module._find_cached_visual_subset(
        runner,
        panels,
        expected_source_hash="checkpoint-source",
    )

    assert found is not None
    assert found.panel_ids == tuple(panel.panel_id for panel in panels[:2])
    assert found.source_hash == "checkpoint-source"


def test_materialized_visual_subset_reseeds_post_materialization_cache_key():
    module = _module()
    panels = _panels(module, "materialized-key")
    identity = _identity(module)
    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=identity,
        cache=module.MemoryStageCache(),
    )
    valid = runner.run_visual_evidence(panels)
    metadata_panels = tuple(
        replace(
            panel,
            mime_type="image/jpeg",
            metadata_only=True,
            identity_payload_checksum=f"{index + 1:064x}",
            identity_descriptor_hash=f"{index + 101:064x}",
        )
        for index, panel in enumerate(panels)
    )
    materialized_panels = tuple(
        replace(panel, mime_type="image/png", metadata_only=False)
        for panel in metadata_panels
    )

    module._seed_visual_subset_cache(runner, metadata_panels, valid)
    assert runner.cache.get(
        module._cache_key(
            "visual",
            list(module._visual_panel_identities(metadata_panels)),
            identity,
            runner.prompts["visual"],
        )
    ) is not None
    assert runner.cache.get(
        module._cache_key(
            "visual",
            list(module._visual_panel_identities(materialized_panels)),
            identity,
            runner.prompts["visual"],
        )
    ) is None

    module._seed_visual_subset_cache(runner, materialized_panels, valid)

    cached_materialized = runner.cache.get(
        module._cache_key(
            "visual",
            list(module._visual_panel_identities(materialized_panels)),
            identity,
            runner.prompts["visual"],
        )
    )
    assert cached_materialized is not None
    assert module.VisualStageResult.from_dict(cached_materialized) == valid


def test_incomplete_visual_stage_requires_checkpoint_subset_restore():
    module = _module()
    panels = _panels(module, "partial-visual")
    identity = _identity(module)
    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=identity,
        cache=module.MemoryStageCache(),
    )
    valid = runner.run_visual_evidence(panels)
    partial = replace(valid, panels=tuple(valid.panels[:2]))

    assert module._visual_cache_requires_subset_restore(
        runner,
        partial.as_dict(),
        panels,
    ) is True
    assert module._visual_cache_requires_subset_restore(
        runner,
        valid.as_dict(),
        panels,
    ) is False
    stale_identity = replace(
        valid,
        source_hash="stale-source",
        panel_identity_hashes=tuple("f" * 64 for _ in panels),
    )
    assert module._visual_cache_requires_subset_restore(
        runner,
        stale_identity.as_dict(),
        panels,
    ) is True


def test_file_stage_cache_round_trips_durable_values(tmp_path):
    module = _module()
    cache = module.FileStageCache(tmp_path / "stage-cache")
    cache.put("visual-key", {"panels": [{"panel_id": "panel-a"}]})
    assert cache.get("visual-key") == {"panels": [{"panel_id": "panel-a"}]}

def test_openai_compatible_json_stage_uses_pinned_prompt_without_exposing_key(monkeypatch):
    from app.services import vision_adapter

    captured: dict[str, object] = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    def fake_post(url, *, headers, json, timeout):
        captured.update({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return _Response()

    monkeypatch.setattr(vision_adapter.httpx, "post", fake_post)
    provider = vision_adapter.OpenAICompatibleVisionProvider(
        base_url="https://api.example.test/v1",
        model="mock-large",
        api_key="test-key-not-printed",
    )
    response = provider.complete_json(
        stage="story_map",
        prompt_version="cloud-causal-map-v2",
        prompt_sha256="b" * 64,
        prompt_text="Return a complete ordered causal map.",
        payload={"panel_ids": ["p1"]},
    )
    assert isinstance(response, dict)
    body = captured["json"]
    assert "Return a complete ordered causal map." in body["messages"][0]["content"]
    assert body["model"] == "mock-large"
    assert body["response_format"] == {"type": "json_object"}
    assert captured["headers"]["Authorization"] == "Bearer test-key-not-printed"
    assert provider.endpoint == "https://api.example.test/v1"


def test_story_map_uses_bounded_ordered_chunks_and_resumes_from_durable_chunk_cache(tmp_path):
    module = _module()
    import threading
    import time

    panels = tuple(
        module.CloudPanelInput(
            panel_id=f"long-panel-{index:04d}",
            source_asset_id=f"long-asset-{index:04d}",
            source_order=index + 1,
            mime_type="image/png",
            payload=f"long-payload-{index}".encode(),
        )
        for index in range(721)
    )
    visual = module.VisualStageResult(
        panels=tuple(_visual_row(panel.descriptor()) for panel in panels),
        source_hash="long-visual-source",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )

    class ChunkProvider(_FakeProvider):
        def __post_init__(self):
            super().__post_init__()
            self.story_sizes = []
            self.active = 0
            self.max_active = 0
            self._lock = threading.Lock()

        def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
            if stage != "story_map":
                return super().complete_json(
                    stage=stage,
                    prompt_version=prompt_version,
                    prompt_sha256=prompt_sha256,
                    prompt_text=prompt_text,
                    payload=payload,
                )
            with self._lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                self.story_sizes.append((payload["batch_index"], len(payload["panel_ids"])))
            try:
                time.sleep(0.01)
                return super().complete_json(
                    stage=stage,
                    prompt_version=prompt_version,
                    prompt_sha256=prompt_sha256,
                    prompt_text=prompt_text,
                    payload=payload,
                )
            finally:
                with self._lock:
                    self.active -= 1

    cache_root = tmp_path / "stage-cache"
    first_provider = ChunkProvider()
    first_runner = module.CloudStageRunner(
        provider=first_provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(cache_root),
        max_attempts=1,
    )
    first = first_runner.run_story_map(visual)

    assert first.panel_ids == tuple(panel.panel_id for panel in panels)
    assert sorted(first_provider.story_sizes) == [
        (0, 180), (1, 180), (2, 180), (3, 180), (4, 1)
    ]
    assert first_provider.max_active <= 4
    assert len(first.beats) == 10

    second_provider = ChunkProvider()
    second_runner = module.CloudStageRunner(
        provider=second_provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(cache_root),
        max_attempts=1,
    )
    second = second_runner.run_story_map(visual)
    assert second == first
    assert second_provider.story_sizes == []


def test_story_map_resume_reuses_completed_chunks_after_one_chunk_failure(tmp_path):
    module = _module()
    panels = tuple(
        module.CloudPanelInput(
            panel_id=f"resume-panel-{index:04d}",
            source_asset_id=f"resume-asset-{index:04d}",
            source_order=index + 1,
            mime_type="image/png",
            payload=f"resume-payload-{index}".encode(),
        )
        for index in range(721)
    )
    visual = module.VisualStageResult(
        panels=tuple(_visual_row(panel.descriptor()) for panel in panels),
        source_hash="resume-visual-source",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )

    class PartialProvider(_FakeProvider):
        def __init__(self, failing_batch=None):
            super().__init__()
            self.failing_batch = failing_batch
            self.story_batches = []

        def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
            if stage == "story_map":
                batch = int(payload["batch_index"])
                self.story_batches.append(batch)
                if self.failing_batch == batch:
                    self.failing_batch = None
                    raise RuntimeError("bounded provider failure")
            return super().complete_json(
                stage=stage,
                prompt_version=prompt_version,
                prompt_sha256=prompt_sha256,
                prompt_text=prompt_text,
                payload=payload,
            )

    cache_root = tmp_path / "partial-cache"
    with pytest.raises(module.CloudStageError) as caught:
        module.CloudStageRunner(
            provider=PartialProvider(failing_batch=1),
            model_identity=_identity(module),
            cache=module.FileStageCache(cache_root),
            max_attempts=1,
        ).run_story_map(visual)
    assert caught.value.code == "cloud.provider_request_failed"

    recovering = PartialProvider()
    result = module.CloudStageRunner(
        provider=recovering,
        model_identity=_identity(module),
        cache=module.FileStageCache(cache_root),
        max_attempts=1,
    ).run_story_map(visual)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels)
    assert recovering.story_batches == [1]


def test_narration_uses_the_same_bounded_ordered_chunk_contract(tmp_path):
    module = _module()
    panels = tuple(
        module.CloudPanelInput(
            panel_id=f"narr-panel-{index:04d}",
            source_asset_id=f"narr-asset-{index:04d}",
            source_order=index + 1,
            mime_type="image/png",
            payload=f"narr-payload-{index}".encode(),
            panel_bounds=(0, 0, 100, 100),
            source_dimensions=(100, 100),
            strip_region_id=f"narr-region-{index}",
            coverage_map_version="coverage-v1",
            coverage_map_hash="b" * 64,
        )
        for index in range(361)
    )
    visual_rows = []
    for panel in panels:
        observation = _visual_row(panel.descriptor())
        visual_rows.append(
            {
                "panel_id": panel.panel_id,
                "source_asset_id": panel.source_asset_id,
                "source_order": panel.source_order,
                "source_checksum": panel.source_checksum,
                "observation": observation,
                "visual_evidence": observation["visual_evidence"],
                "evidence_hash": "",
            }
        )
    visual = module.VisualStageResult(
        panels=tuple(visual_rows),
        source_hash="narr-visual-source",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )
    class NarrationProvider(_FakeProvider):
        def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
            if stage == "narration":
                self.calls.append((stage, prompt_version, prompt_sha256))
            if stage != "narration":
                return super().complete_json(
                    stage=stage,
                    prompt_version=prompt_version,
                    prompt_sha256=prompt_sha256,
                    prompt_text=prompt_text,
                    payload=payload,
                )
            panel_ids = list(payload["panel_ids"])
            seed_panel_ids = (
                panel_ids[:3]
                if len(panel_ids) >= 3
                else ["seed-panel-0", "seed-panel-1", "seed-panel-2"]
            )
            output = _narrative_output("cloud", seed_panel_ids)
            base_observation = dict(output["observations"][0])
            output["observations"] = [
                {
                    **base_observation,
                    "panel_id": panel_id,
                    "evidence_refs": [panel_id],
                }
                for panel_id in panel_ids
            ]
            output["coverage_manifest"]["panel_ids"] = panel_ids
            output["coverage_manifest"]["total_panels"] = len(panel_ids)
            output["coverage_manifest"]["processed_panels"] = len(panel_ids)
            for chunk in output["continuity_ledger"]["chunks"]:
                chunk["panel_ids"] = panel_ids
            for entity in output["continuity_ledger"]["entities"]:
                entity["panel_ids"] = panel_ids
            for passage in output["script_passages"]:
                passage["evidence_panel_ids"] = panel_ids
            for claim in output["evidence_graph"]["claims"]:
                claim["evidence_panel_ids"] = panel_ids
            return output

    provider = NarrationProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(tmp_path / "narr-cache"),
        max_attempts=1,
    )
    story_map = runner.run_story_map(visual)
    result = runner.run_narration(visual, story_map, panels=panels)

    assert result.observations[0]["panel_id"] == panels[0].panel_id
    assert result.observations[-1]["panel_id"] == panels[-1].panel_id
    assert len([call for call in provider.calls if call[0] == "narration"]) == 1
    assert result.qc_report["narration_topology"] == "chapter_evidence_reduce_v1"
    assert result.qc_report["editorial_selection"]["selection_hash"]
    assert len(result.observations) == len(panels)

    resumed_provider = NarrationProvider()
    resumed_runner = module.CloudStageRunner(
        provider=resumed_provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(tmp_path / "narr-cache"),
        max_attempts=1,
    )
    resumed_story = resumed_runner.run_story_map(visual)
    resumed_result = resumed_runner.run_narration(visual, resumed_story, panels=panels)
    assert resumed_result == result
    assert resumed_provider.calls == []

def test_story_map_accepts_provider_ordered_beats_alias(tmp_path):
    module = _module()
    panels = _panels(module)
    visual = module.VisualStageResult(
        panels=tuple(_visual_row(panel.descriptor()) for panel in panels),
        source_hash="ordered-beats-source",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )

    class OrderedBeatsProvider(_FakeProvider):
        def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
            result = super().complete_json(
                stage=stage,
                prompt_version=prompt_version,
                prompt_sha256=prompt_sha256,
                prompt_text=prompt_text,
                payload=payload,
            )
            if stage == "story_map":
                result["ordered_beats"] = result.pop("beats")
            return result

    provider = OrderedBeatsProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(tmp_path / "ordered-beats-cache"),
        max_attempts=1,
    )

    result = runner.run_story_map(visual)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels)
    assert len(result.beats) == 2

def test_story_map_splits_incomplete_large_chunk_without_dropping_coverage(tmp_path):
    module = _module()
    panels = tuple(
        module.CloudPanelInput(
            panel_id=f"fallback-panel-{index:03d}",
            source_asset_id=f"fallback-asset-{index:03d}",
            source_order=index + 1,
            mime_type="image/png",
            payload=f"fallback-payload-{index}".encode(),
        )
        for index in range(61)
    )
    visual = module.VisualStageResult(
        panels=tuple(_visual_row(panel.descriptor()) for panel in panels),
        source_hash="fallback-source",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )

    class IncompleteLargeProvider(_FakeProvider):
        def __post_init__(self):
            super().__post_init__()
            self.story_sizes = []

        def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
            if stage == "story_map":
                self.story_sizes.append(len(payload["panel_ids"]))
            result = super().complete_json(
                stage=stage,
                prompt_version=prompt_version,
                prompt_sha256=prompt_sha256,
                prompt_text=prompt_text,
                payload=payload,
            )
            if stage == "story_map" and len(payload["panel_ids"]) > 60:
                result["ordered_beats"] = result.pop("beats")
                for beat in result["ordered_beats"]:
                    beat["panel_ids"] = [payload["panel_ids"][0]]
                for claim in result["claims"]:
                    claim["panel_ids"] = [payload["panel_ids"][0]]
            return result

    provider = IncompleteLargeProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(tmp_path / "fallback-cache"),
        max_attempts=1,
    )

    result = runner.run_story_map(visual)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels)
    assert sorted(provider.story_sizes) == [1, 60, 61]


def test_story_map_reduces_incomplete_60_panel_chunk_to_30_without_dropping_coverage(tmp_path):
    module = _module()
    panels = tuple(
        module.CloudPanelInput(
            panel_id=f"nested-fallback-panel-{index:03d}",
            source_asset_id=f"nested-fallback-asset-{index:03d}",
            source_order=index + 1,
            mime_type="image/png",
            payload=f"nested-fallback-payload-{index}".encode(),
        )
        for index in range(61)
    )
    visual = module.VisualStageResult(
        panels=tuple(_visual_row(panel.descriptor()) for panel in panels),
        source_hash="nested-fallback-source",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )

    class IncompleteMediumProvider(_FakeProvider):
        def __post_init__(self):
            super().__post_init__()
            self.story_sizes = []

        def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
            if stage == "story_map":
                self.story_sizes.append(len(payload["panel_ids"]))
            result = super().complete_json(
                stage=stage,
                prompt_version=prompt_version,
                prompt_sha256=prompt_sha256,
                prompt_text=prompt_text,
                payload=payload,
            )
            if stage == "story_map" and len(payload["panel_ids"]) > 30:
                result["ordered_beats"] = result.pop("beats")
                for beat in result["ordered_beats"]:
                    beat["panel_ids"] = [payload["panel_ids"][0]]
                for claim in result["claims"]:
                    claim["panel_ids"] = [payload["panel_ids"][0]]
            return result

    provider = IncompleteMediumProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(tmp_path / "nested-fallback-cache"),
        max_attempts=1,
    )

    result = runner.run_story_map(visual)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels)
    assert sorted(provider.story_sizes) == [1, 30, 30, 60, 61]


def test_narration_uses_one_final_reduce_call_after_editorial_selection(tmp_path):
    module = _module()
    panels = tuple(
        module.CloudPanelInput(
            panel_id=f"narration-fallback-panel-{index:03d}",
            source_asset_id=f"narration-fallback-asset-{index:03d}",
            source_order=index + 1,
            mime_type="image/png",
            payload=f"narration-fallback-payload-{index}".encode(),
            panel_bounds=(0, 0, 100, 100),
            source_dimensions=(100, 100),
            strip_region_id=f"narration-fallback-region-{index}",
            coverage_map_version="coverage-v1",
            coverage_map_hash="b" * 64,
        )
        for index in range(181)
    )
    visual_rows = []
    for panel in panels:
        observation = _visual_row(panel.descriptor())
        visual_rows.append(
            {
                "panel_id": panel.panel_id,
                "source_asset_id": panel.source_asset_id,
                "source_order": panel.source_order,
                "source_checksum": panel.source_checksum,
                "observation": observation,
                "visual_evidence": observation["visual_evidence"],
                "evidence_hash": "",
            }
        )
    visual = module.VisualStageResult(
        panels=tuple(visual_rows),
        source_hash="narration-fallback-source",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )
    panel_ids = [panel.panel_id for panel in panels]
    story_map = module.StoryMapResult(
        panel_ids=tuple(panel_ids),
        beats=({"beat_id": "beat-all", "panel_ids": panel_ids, "summary": "the visible sequence develops"},),
        causal_chain=({"from_beat": "beat-all", "to_beat": "beat-all", "reason": "the visible sequence continues"},),
        claims=({
            "claim_id": "claim-all",
            "text": "The visible sequence develops.",
            "panel_ids": panel_ids,
            "qualification": "The ordered panels support this reading.",
        },),
        story_map_hash="s" * 64,
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="cloud-causal-map-v2",
        prompt_sha256="c" * 64,
    )

    class EditorialReduceProvider(_FakeProvider):
        def __post_init__(self):
            super().__post_init__()
            self.narration_sizes = []

        def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
            if stage == "narration":
                panel_ids = list(payload["panel_ids"])
                self.narration_sizes.append(len(panel_ids))
                output = _narrative_output("cloud", panel_ids[:3])
                for passage in output["script_passages"]:
                    passage["evidence_panel_ids"] = panel_ids
                for claim in output["evidence_graph"]["claims"]:
                    claim["evidence_panel_ids"] = panel_ids
                return output
            return super().complete_json(
                stage=stage,
                prompt_version=prompt_version,
                prompt_sha256=prompt_sha256,
                prompt_text=prompt_text,
                payload=payload,
            )

    provider = EditorialReduceProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(tmp_path / "narration-fallback-cache"),
        max_attempts=1,
    )

    result = runner.run_narration(visual, story_map, panels=panels)

    assert len(result.observations) == len(panels)
    assert provider.narration_sizes == [4]
    assert result.qc_report["narration_topology"] == "chapter_evidence_reduce_v1"
    assert result.qc_report["editorial_selection"]["selection_hash"]



def test_narration_retry_sends_sanitized_contract_feedback(tmp_path):
    module = _module()
    panels = _panels(module)
    visual_rows = []
    for panel in panels:
        observation = _visual_row(panel.descriptor())
        visual_rows.append(
            {
                "panel_id": panel.panel_id,
                "source_asset_id": panel.source_asset_id,
                "source_order": panel.source_order,
                "source_checksum": panel.source_checksum,
                "observation": observation,
                "visual_evidence": observation["visual_evidence"],
                "evidence_hash": "",
            }
        )
    visual = module.VisualStageResult(
        panels=tuple(visual_rows),
        source_hash="narration-feedback-source",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )
    panel_ids = [panel.panel_id for panel in panels]
    story_map = module.StoryMapResult(
        panel_ids=tuple(panel_ids),
        beats=({"beat_id": "beat-all", "panel_ids": panel_ids, "summary": "the visible sequence develops"},),
        causal_chain=({"from_beat": "beat-all", "to_beat": "beat-all", "reason": "the visible sequence continues"},),
        claims=({
            "claim_id": "claim-all",
            "text": "The visible sequence develops.",
            "panel_ids": panel_ids,
            "qualification": "The ordered panels support this reading.",
        },),
        story_map_hash="s" * 64,
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="cloud-causal-map-v2",
        prompt_sha256="c" * 64,
    )

    class FeedbackProvider(_FakeProvider):
        def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
            if stage != "narration":
                return super().complete_json(
                    stage=stage,
                    prompt_version=prompt_version,
                    prompt_sha256=prompt_sha256,
                    prompt_text=prompt_text,
                    payload=payload,
                )
            self.narration_payloads.append(dict(payload))
            output = _narrative_output("feedback", list(payload["panel_ids"]))
            if int(payload.get("retry_attempt", 0)) == 0:
                output["evidence_graph"]["claims"][0]["evidence_panel_ids"] = ["foreign-panel"]
            return output

    provider = FeedbackProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(tmp_path / "narration-feedback-cache"),
        max_attempts=2,
    )

    result = runner.run_narration(visual, story_map, panels=panels)

    assert len(result.observations) == len(panels)
    assert len(provider.narration_payloads) == 2
    assert provider.narration_payloads[1]["contract_retry_feedback"] == (
        "repeat only exact current panel IDs and include every claim's evidence IDs "
        "in the referencing passage"
    )


def test_repaired_visual_evidence_hash_invalidates_downstream_stage_identity():
    module = _module()
    panels = _panels(module)
    rows = []
    for panel in panels:
        observation = _visual_row(panel.descriptor())
        rows.append({
            "panel_id": panel.panel_id,
            "source_asset_id": panel.source_asset_id,
            "source_order": panel.source_order,
            "source_checksum": panel.source_checksum,
            "observation": observation,
            "visual_evidence": observation["visual_evidence"],
            "evidence_hash": "",
        })
    visual = module.VisualStageResult(
        panels=tuple(rows),
        source_hash="same-source-bytes",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )
    changed_row = dict(rows[0])
    changed_observation = dict(changed_row["observation"])
    changed_observation["visible_facts"] = ["repaired visible fact"]
    changed_row["observation"] = changed_observation
    changed = module.VisualStageResult(
        panels=(changed_row, *rows[1:]),
        source_hash=visual.source_hash,
        model_identity_hash=visual.model_identity_hash,
        prompt_version=visual.prompt_version,
        prompt_sha256=visual.prompt_sha256,
    )

    assert visual.visual_evidence_hash != changed.visual_evidence_hash
    story = module.StoryMapResult(
        panel_ids=visual.panel_ids,
        beats=(),
        causal_chain=(),
        claims=(),
        story_map_hash="s" * 64,
        model_identity_hash=visual.model_identity_hash,
        prompt_version="cloud-causal-map-v2",
        prompt_sha256="c" * 64,
        visual_evidence_hash=visual.visual_evidence_hash,
    )
    assert story.visual_evidence_hash != changed.visual_evidence_hash


def test_resume_filters_poison_panels_before_cached_visual_source_hash():
    module = _module()
    panels = _panels(module, prefix="resume")
    cached_visual = {
        "panels": [
            {"panel_id": panels[0].panel_id},
            {"panel_id": panels[2].panel_id},
        ],
    }

    filtered = module._panels_for_cached_visual_stage(panels, cached_visual)

    assert [panel.panel_id for panel in filtered] == [
        panels[0].panel_id,
        panels[2].panel_id,
    ]


def test_visual_repair_normalizes_panel_ids_alias_before_grounding_validation():
    repair = importlib.import_module("app.services.visual_narrative_repair")
    record = repair.FeasibleVisualRecord(
        panel_region_id="region-1",
        panel_id="panel-1",
        source_asset_id="asset-1",
        source_order=1,
        eligible_sections=("hook",),
        eligible_beats=("beat-1",),
        resolution_state="NATIVE",
        feasible_rois=(
            {
                "kind": "primary",
                "roi_label": "primary",
                "crop_box": [0, 0, 100, 100],
                "telemetry": {},
            },
        ),
        visual_strengths={
            "edge_connected_blank_fraction": 0.0,
            "protected_retained_fraction": 1.0,
        },
        evidence_hash="e" * 64,
        detector_version="detector-v1",
        mask_sha256="m" * 64,
        panel_size=(100, 100),
    )
    ledger = repair.FeasibleVisualLedger(
        entries=(record,),
        model_identity_hash="model" * 16,
    )
    value = {
        "claims": [
            {
                "claim_id": "claim-1",
                "claim_type": "fact",
                "text": "A visible turn changes the next beat.",
                "qualification": "the panel supports this reading",
                "panel_ids": ["panel-1"],
            },
        ],
        "passages": [
            {
                "passage_id": "passage-1",
                "editorial_role": "hook",
                "text": "A visible turn changes the next beat.",
                "claim_ids": ["claim-1"],
                "panel_ids": ["panel-1"],
            },
        ],
    }

    repaired, remaps = repair.remap_same_beat_panel_citations(
        value,
        ledger=ledger,
        section_to_beats={"hook": ("beat-1",)},
    )

    assert remaps == ()
    assert repaired["claims"][0]["evidence_panel_ids"] == ["panel-1"]
    assert repaired["passages"][0]["evidence_panel_ids"] == ["panel-1"]
    repair.validate_repaired_panel_references(
        repaired,
        ledger=ledger,
        allowed_claim_ids={"claim-1"},
    )


def test_visual_repair_retry_feedback_is_static_and_specific():
    module = _module()

    feedback = module._visual_narrative_repair_retry_feedback(
        "visual.narrative_repair_ungrounded"
    )

    assert "existing claim IDs" in feedback
    assert "feasible panel IDs" in feedback
    assert "118-124 lexical words" in feedback
    assert module._visual_narrative_repair_retry_feedback(
        "cloud.provider_response_invalid"
    ).startswith("return strict JSON")

def test_narration_cache_requires_complete_grounded_result_even_with_matching_visual_hash():
    module = _module()
    panels = _panels(module)
    rows = tuple(_visual_row(panel.descriptor()) for panel in panels)
    visual = module.VisualStageResult(
        panels=rows,
        source_hash="cache-validity-source",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )
    spoken_text = " ".join(
        ["One grounded turn changes what follows."] * 20
    )
    display_words = tuple(re.findall(r"[A-Z0-9]+", spoken_text.upper()))
    valid = module.NarrationResult(
        spoken_text=spoken_text,
        display_words=display_words,
        passages=tuple(
            {
                "passage_id": f"p{index}",
                "editorial_role": "role",
                "text": "A grounded turn changes what follows.",
                "claim_ids": ["claim"],
                "evidence_panel_ids": [panel.panel_id],
            }
            for index in range(4)
            for panel in (panels[index % len(panels)],)
        ),
        ending_kind="consequence",
        word_count=120,
        estimated_duration_s=52.17,
        observations=tuple(rows),
        continuity_ledger={},
        evidence_graph={"claims": []},
        story_spine={},
        qc_report={
            "duration_contract": module.script.narration_duration_metrics(
                spoken_text,
                "dramatic",
            ),
        },
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="vision-first-story-analyzer-v3",
        prompt_sha256="n" * 64,
        visual_evidence_hash=visual.visual_evidence_hash,
    )
    stale = replace(valid, word_count=0, spoken_text="", display_words=())

    assert module._narration_result_is_usable(
        valid,
        visual,
        require_duration=True,
    ) is True
    assert module._narration_result_is_usable(
        stale,
        visual,
        require_duration=True,
    ) is False
    missing_contract = replace(valid, qc_report={})
    assert module._narration_result_is_usable(
        missing_contract,
        visual,
        require_duration=True,
    ) is False
    contract_invalid = replace(
        valid,
        word_count=172,
        estimated_duration_s=69.57,
    )
    assert module._narration_result_is_usable(
        contract_invalid,
        visual,
        require_duration=True,
    ) is False
    assert module._narration_result_is_usable(
        contract_invalid,
        visual,
        require_duration=False,
    ) is True

def test_final_narration_scope_rejects_mixed_observations_and_continuity(tmp_path):
    module = _module()
    panels = _panels(module, "scope-reconcile")
    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=_identity(module),
        max_attempts=1,
    )
    visual = runner.run_visual_evidence(panels)
    full_observations, full_structural = runner._narration_observations(
        visual, panels
    )
    selected_visual = replace(visual, panels=(visual.panels[0],))
    selected_observations, selected_structural = runner._narration_observations(
        selected_visual, panels[:1]
    )
    selected_panel_id = panels[0].panel_id
    passage_texts = []
    for index in range(4):
        words = (
            f"Passage {index} explains why this grounded turn matters while "
            "the evidence keeps the next decision connected to the visible "
            "panel and its changing stakes"
        ).split()
        words.extend(["clearly"] * (30 - len(words)))
        passage_texts.append(" ".join(words) + ".")
    passages = tuple(
        {
            "passage_id": f"scope-passage-{index}",
            "editorial_role": f"scope-role-{index}",
            "text": text,
            "claim_ids": ["scope-claim"],
            "evidence_panel_ids": [selected_panel_id],
        }
        for index, text in enumerate(passage_texts)
    )
    spoken_text = "\n\n".join(item["text"] for item in passages)
    duration_contract = module.script.narration_duration_metrics(
        spoken_text,
        "dramatic",
    )
    candidate = module.NarrationResult(
        spoken_text=spoken_text,
        display_words=module.derive_display_words(spoken_text),
        passages=passages,
        ending_kind="consequence",
        word_count=int(duration_contract["word_count"]),
        estimated_duration_s=float(duration_contract["estimated_duration_s"]),
        observations=tuple(selected_observations),
        continuity_ledger=dict(selected_structural["continuity_ledger"]),
        evidence_graph={
            "claims": [
                {
                    "claim_id": "scope-claim",
                    "claim_type": "interpretation",
                    "text": "A grounded turn changes the next decision.",
                    "qualification": "The visible sequence supports this reading.",
                    "evidence_panel_ids": [selected_panel_id],
                }
            ]
        },
        story_spine={},
        qc_report={"duration_contract": duration_contract},
        model_identity_hash=runner.model_identity.identity_hash,
        prompt_version=runner.prompts["narration"][0],
        prompt_sha256=runner.prompts["narration"][1],
        visual_evidence_hash=visual.visual_evidence_hash,
    )
    mixed_scope = replace(candidate, observations=tuple(full_observations))

    assert module._narration_result_is_usable(
        mixed_scope,
        visual,
        require_duration=True,
        require_grounding=True,
    ) is False

    reconciled = module._reconcile_narration_full_scope(
        mixed_scope,
        observations=full_observations,
        structural=full_structural,
        expected_panel_ids=visual.panel_ids,
        visual_evidence_hash=visual.visual_evidence_hash,
    )

    assert reconciled.continuity_ledger == full_structural["continuity_ledger"]
    assert module._narration_result_is_usable(
        reconciled,
        visual,
        require_duration=True,
        require_grounding=True,
    ) is True

    service = module.CloudBatchService(
        runner=runner,
        store=module.JsonJobStore(tmp_path),
    )
    state_reconciled = service._reconcile_cached_narration(
        mixed_scope,
        visual,
        panels,
    )
    assert state_reconciled.continuity_ledger == full_structural["continuity_ledger"]
    assert state_reconciled.observations == tuple(full_observations)

    story_map = module.StoryMapResult(
        panel_ids=visual.panel_ids,
        beats=(
            {
                "beat_id": "scope-beat",
                "panel_ids": list(visual.panel_ids),
                "summary": "the ordered evidence develops",
            },
        ),
        causal_chain=(),
        claims=(),
        story_map_hash="s" * 64,
        model_identity_hash=runner.model_identity.identity_hash,
        prompt_version=runner.prompts["story_map"][0],
        prompt_sha256=runner.prompts["story_map"][1],
        visual_evidence_hash=visual.visual_evidence_hash,
    )
    store = module.JsonJobStore(tmp_path / "resume")
    record = module.ChapterJobRecord(
        job_id="scope-resume",
        stage_results={
            "visual": visual.as_dict(),
            "story_map": story_map.as_dict(),
            "narration": mixed_scope.as_dict(),
        },
    )
    store.save(record)

    def unexpected_narration(*_args, **_kwargs):
        raise AssertionError("cached narration must not dispatch a provider call")

    runner.run_narration = unexpected_narration
    resumed = module.CloudBatchService(runner=runner, store=store).run_job(
        "scope-resume",
        panels,
    )
    assert resumed.state == module.ChapterState.READY_TO_RENDER
    persisted = store.load("scope-resume")
    assert persisted is not None
    assert persisted.stage_results["narration"]["continuity_ledger"] == full_structural[
        "continuity_ledger"
    ]

    broken = replace(
        reconciled,
        continuity_ledger=dict(selected_structural["continuity_ledger"]),
    )
    assert module._narration_result_is_usable(
        broken,
        visual,
        require_duration=True,
        require_grounding=True,
    ) is False

def test_editorial_selection_is_bounded_ordered_and_panel_keyed():
    module = _module()
    panels = tuple(
        module.CloudPanelInput(
            panel_id=f"selection-panel-{index:03d}",
            source_asset_id=f"selection-asset-{index:03d}",
            source_order=index + 1,
            mime_type="image/png",
            payload=f"selection-payload-{index}".encode(),
        )
        for index in range(240)
    )
    visual = module.VisualStageResult(
        panels=tuple(_visual_row(panel.descriptor()) for panel in panels),
        source_hash="selection-source",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )
    beats = []
    claims = []
    for beat_index in range(24):
        panel_ids = [
            panel.panel_id
            for panel in panels[beat_index * 10:(beat_index + 1) * 10]
        ]
        beat_id = f"selection-beat-{beat_index:02d}"
        claim_id = f"selection-claim-{beat_index:02d}"
        beats.append({
            "beat_id": beat_id,
            "panel_ids": panel_ids,
            "summary": f"beat {beat_index}",
            "state_changes": [f"change {beat_index}"],
        })
        claims.append({
            "claim_id": claim_id,
            "claim_type": "fact",
            "text": f"fact {beat_index}",
            "qualification": "visible evidence supports this",
            "evidence_panel_ids": panel_ids[:2],
        })
    story_map = module.StoryMapResult(
        panel_ids=tuple(panel.panel_id for panel in panels),
        beats=tuple(beats),
        causal_chain=tuple(
            {
                "from_beat": beats[index]["beat_id"],
                "to_beat": beats[index + 1]["beat_id"],
                "reason": "ordered consequence",
            }
            for index in range(len(beats) - 1)
        ),
        claims=tuple(claims),
        story_map_hash="story-selection",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="cloud-causal-map-v2",
        prompt_sha256="c" * 64,
        visual_evidence_hash=visual.visual_evidence_hash,
    )

    selection = module.select_editorial_beats(visual, story_map, target_count=10)

    assert 8 <= len(selection.beat_ids) <= 12
    assert len(selection.panel_ids) == len(set(selection.panel_ids))
    assert selection.beat_ids == tuple(
        beat_id
        for beat_id in selection.beat_ids
    )
    visual_order = {panel.panel_id: index for index, panel in enumerate(panels)}
    assert tuple(sorted(selection.panel_ids, key=visual_order.get)) == selection.panel_ids
    assert set(selection.claim_ids) <= {claim["claim_id"] for claim in claims}
    assert selection.selection_hash

def test_narration_duration_failure_retains_candidate_for_visual_repair(monkeypatch):
    module = _module()
    panels = _panels(module, "duration-retain")
    provider = _FakeProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
    )
    visual = runner.run_visual_evidence(panels)
    story_map = runner.run_story_map(visual)
    baseline = runner.run_narration(visual, story_map, panels=panels)
    story_map = replace(
        story_map,
        claims=tuple(dict(item) for item in baseline.evidence_graph["claims"]),
    )
    invalid = replace(baseline, estimated_duration_s=70.0)
    monkeypatch.setattr(
        runner,
        "_run_narration_batched",
        lambda *args, **kwargs: invalid,
    )

    with pytest.raises(module.CloudStageError) as caught:
        runner.run_narration(visual, story_map, panels=panels)

    assert caught.value.code == "cloud.narrative_repair_position_selection_invalid"
    assert runner._last_narration_result is invalid

def test_repair_harness_uses_compact_candidate_context_without_normal_call():
    module = _module()
    base_panels = _panels(module, "compact-repair")
    extra_panel = replace(
        base_panels[-1],
        panel_id="compact-repair-panel-4",
        source_asset_id="compact-repair-asset-4",
        source_order=4,
        payload=b"compact-repair-panel-payload-4",
        source_checksum="",
        payload_checksum="",
        strip_region_id="compact-repair-region-4",
    )
    panels = base_panels + (extra_panel,)

    class RepairOnlyProvider(_FakeProvider):
        def __post_init__(self):
            super().__post_init__()
            self.repair_panel_ids: list[tuple[str, ...]] = []

        def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
            if stage == "narration":
                raise AssertionError("normal narration must not run for a durable candidate")
            if stage == "narration_repair":
                self.calls.append((stage, prompt_version, prompt_sha256))
                self.repair_panel_ids.append(tuple(str(item) for item in payload["panel_ids"]))
                return _provider_position_vector(payload)
            return super().complete_json(
                stage=stage,
                prompt_version=prompt_version,
                prompt_sha256=prompt_sha256,
                prompt_text=prompt_text,
                payload=payload,
            )

    provider = RepairOnlyProvider()
    cache = module.MemoryStageCache()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=cache,
        max_attempts=1,
    )
    visual = runner.run_visual_evidence(panels)
    visual = replace(
        visual,
        panels=tuple(
            {
                **dict(row),
                "panel_bounds": list(panel.panel_bounds or ()),
                "source_dimensions": list(panel.source_dimensions or ()),
                "coverage_map_version": panel.coverage_map_version,
                "coverage_map_hash": panel.coverage_map_hash,
            }
            for row, panel in zip(visual.panels, panels, strict=True)
        ),
    )
    story_map = runner.run_story_map(visual)
    selected_visual = replace(visual, panels=visual.panels[:3])
    selected_ids = tuple(str(item["panel_id"]) for item in selected_visual.panels)
    candidate_output = _narrative_output("compact-repair-candidate", list(selected_ids))
    trusted_claims = []
    for index in range(8):
        trusted_claim = dict(story_map.claims[index % len(story_map.claims)])
        trusted_claim["claim_id"] = f"compact-trusted-claim-{index}"
        trusted_claim.setdefault("claim_type", "fact")
        trusted_claim["panel_ids"] = list(selected_ids)
        trusted_claim["evidence_panel_ids"] = list(selected_ids)
        trusted_claims.append(trusted_claim)
    story_map = replace(story_map, claims=tuple(trusted_claims))
    for passage_index, passage in enumerate(candidate_output["script_passages"]):
        passage["claim_ids"] = [
            trusted_claims[passage_index * 2]["claim_id"],
            trusted_claims[passage_index * 2 + 1]["claim_id"],
        ]
        passage["evidence_panel_ids"] = list(selected_ids)
    candidate_output["evidence_graph"] = {"claims": trusted_claims}
    observations, structural = runner._narration_observations(selected_visual, None)
    spoken = "\n\n".join(str(item["text"]).strip() for item in candidate_output["script_passages"])
    candidate = module.NarrationResult(
        spoken_text=spoken,
        display_words=module.derive_display_words(spoken),
        passages=tuple(dict(item) for item in candidate_output["script_passages"]),
        ending_kind=str(candidate_output["narrative_outline"]["ending_kind"]),
        word_count=160,
        estimated_duration_s=64.35,
        observations=tuple(observations),
        continuity_ledger=dict(structural["continuity_ledger"]),
        evidence_graph=dict(candidate_output["evidence_graph"]),
        story_spine=dict(candidate_output["narrative_outline"]["story_spine"]),
        qc_report={},
        model_identity_hash=runner.model_identity.identity_hash,
        prompt_version=runner.prompts["narration"][0],
        prompt_sha256=runner.prompts["narration"][1],
        visual_evidence_hash=selected_visual.visual_evidence_hash,
    )

    repaired = runner.run_narration_repair_candidate(
        candidate,
        visual,
        story_map,
        panels=None,
    )

    assert provider.repair_panel_ids == [selected_ids]
    assert [call[0] for call in provider.calls] == ["visual", "story_map", "narration_repair"]
    assert 115 <= repaired.word_count <= 125
    assert 50.0 <= repaired.estimated_duration_s <= 60.0
    assert all(
        value.get("cache_type") != "narration-final-v1"
        for value in cache._values.values()
    )


def _immutable_slot_fixture(module):
    base_panels = _panels(module, "immutable-slot")
    panels = base_panels + tuple(
        replace(
            base_panels[-1],
            panel_id=f"immutable-slot-panel-{index}",
            source_asset_id=f"immutable-slot-asset-{index}",
            source_order=index,
            payload=f"immutable-slot-payload-{index}".encode(),
            payload_checksum="",
            source_checksum="",
            strip_region_id=f"immutable-slot-region-{index}",
        )
        for index in (4, 5)
    )
    rows = []
    for panel in panels:
        observation = _visual_row(panel.descriptor())
        rows.append(
            {
                "panel_id": panel.panel_id,
                "source_asset_id": panel.source_asset_id,
                "source_order": panel.source_order,
                "source_checksum": panel.source_checksum,
                "panel_bounds": list(panel.panel_bounds),
                "source_dimensions": list(panel.source_dimensions),
                "observation": observation,
                "visual_evidence": observation["visual_evidence"],
                "evidence_hash": "",
            }
        )
    identity = _identity(module)
    visual = module.VisualStageResult(
        panels=tuple(rows),
        source_hash="immutable-slot-source",
        model_identity_hash=identity.identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )
    panel_ids = [panel.panel_id for panel in panels]
    beats = tuple(
        {
            "beat_id": f"immutable-beat-{index}",
            "panel_ids": [panel_id],
            "summary": f"the sequence reaches beat {index}",
        }
        for index, panel_id in enumerate(panel_ids)
    )
    claims = tuple(
        {
            "claim_id": f"immutable-claim-{index}-{claim_index}",
            "claim_type": "fact",
            "text": f"The visible beat {index} claim {claim_index} changes the situation.",
            "panel_ids": [panel_id],
            "evidence_panel_ids": [panel_id],
            "qualification": "The ordered panel supports this reading.",
        }
        for index, panel_id in enumerate(panel_ids)
        for claim_index in range(2)
    )
    passages = tuple(
        {
            "passage_id": f"immutable-passage-{index}",
            "editorial_role": "causal_turn",
            "text": f"The sequence reaches beat {index} before the next turn.",
            "claim_ids": [
                f"immutable-claim-{index}-0",
                f"immutable-claim-{index}-1",
            ],
            "evidence_panel_ids": [panel_id],
        }
        for index, panel_id in enumerate(panel_ids)
    )
    story_map = module.StoryMapResult(
        panel_ids=tuple(panel_ids),
        beats=beats,
        causal_chain=tuple(
            {
                "from_beat": beats[index]["beat_id"],
                "to_beat": beats[index + 1]["beat_id"],
                "reason": "the next visible beat follows",
            }
            for index in range(len(beats) - 1)
        ),
        claims=claims,
        story_map_hash="immutable-story-map",
        model_identity_hash=identity.identity_hash,
        prompt_version="cloud-causal-map-v2",
        prompt_sha256="c" * 64,
        visual_evidence_hash=visual.visual_evidence_hash,
    )
    prompt = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=identity,
    ).prompts["narration"]
    candidate = module.NarrationResult(
        spoken_text=" ".join(item["text"] for item in passages),
        display_words=("THE", "SEQUENCE"),
        passages=passages,
        ending_kind="consequence",
        word_count=160,
        estimated_duration_s=64.35,
        qc_report={},
        model_identity_hash=identity.identity_hash,
        prompt_version=prompt[0],
        prompt_sha256=prompt[1],
        observations=tuple(row["observation"] for row in rows),
        continuity_ledger={},
        evidence_graph={"claims": [dict(claim) for claim in claims]},
        story_spine={
            "wants": "understand the visible turn",
            "obstacle": "the route changes",
            "decision": "respond to the change",
            "consequence": "the stakes move",
            "changed_stakes": "the next beat matters",
            "unresolved_direction": "what follows",
        },
        visual_evidence_hash=visual.visual_evidence_hash,
    )
    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=identity,
    )
    return runner, candidate, visual, story_map


def test_targeted_repair_prompt_declares_exact_slot_wire_shape():
    module = _module()
    instruction = module.NARRATION_REPAIR_INSTRUCTION
    assert '{"rewrites": ["text for position 0", "..."]}' in instruction
    assert "never return, create, or rewrite claim IDs" in instruction
    assert "approximately 120 total words" in instruction
    assert "word_budget_min/word_budget_max" in instruction
    assert "third-person narrator language" in instruction
    assert "never quote or preserve a four-word lexical sequence" in instruction
    assert "renaming a speaker are not loopholes" in instruction


def test_targeted_repair_prompt_requires_concise_position_drafting():
    module = _module()
    instruction = module.NARRATION_REPAIR_INSTRUCTION
    assert "not hard admission bounds" in instruction
    assert "word_budget_min/word_budget_max" in instruction
    assert "pathological single-position share" in instruction
    assert "exactly 120 is guidance" in instruction


def test_targeted_repair_prompt_targets_compact_eight_position_vector():
    module = _module()
    instruction = module.NARRATION_REPAIR_INSTRUCTION
    assert "exactly 14 or 15 words per position, aiming for 15" in instruction
    assert "never exceed 15 words in any single rewrite" in instruction
    assert "trim redundant words rather than padding any position" in instruction


def test_targeted_repair_prompt_targets_safe_in_range_total():
    module = _module()
    instruction = module.NARRATION_REPAIR_INSTRUCTION
    assert "Aim for 118 total words" in instruction
    assert "exactly 120 is guidance only" in instruction


def _position_rewrite_text(word_budget, prefix):
    return " ".join(f"{prefix.rstrip('_')}word{index}" for index in range(word_budget))


def _micro_compaction_rewrite_texts(counts):
    rewrites = []
    for index, count in enumerate(counts):
        if index == 0:
            prefix = "it is"
            filler_count = count - 2
        elif index == 1:
            prefix = "does not"
            filler_count = count - 2
        else:
            prefix = ""
            filler_count = count
        fillers = [f"compact{index}word{word_index}" for word_index in range(filler_count)]
        rewrites.append(" ".join(part for part in (prefix, *fillers) if part))
    return rewrites


def _provider_position_vector(payload):
    rows = payload["targeted_repair"]["position_context"]
    vocabulary = [
        "Now",
        "the",
        "visible",
        "turn",
        "changes",
        "what",
        "comes",
        "next",
        "because",
        "the",
        "stakes",
        "shift",
        "while",
        "the",
        "next",
        "choice",
        "keeps",
        "pressure",
        "moving",
        "forward",
    ]
    return {
        "rewrites": [
            " ".join((vocabulary * ((row["word_budget"] // len(vocabulary)) + 1))[: row["word_budget"]])
            for row in rows
        ]
    }


def test_position_repair_preselection_is_deterministic_and_budgeted():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)

    first = runner._build_narration_repair_position_registry(candidate, story_map)
    second = runner._build_narration_repair_position_registry(candidate, story_map)

    positions = first["positions"]
    assert first["version"] == "narration-repair-position-registry-v5"
    assert len(positions) == 8
    assert 8 <= len({claim_id for row in positions for claim_id in row["claim_ids"]}) <= 12
    assert 4 <= len({row["passage_id"] for row in positions}) <= 6
    assert [row["causal_position"] for row in positions] == sorted(
        row["causal_position"] for row in positions
    )
    assert sum(row["word_budget"] for row in positions) == 120
    assert first["target_duration_s"] == pytest.approx(120 / 2.3, abs=0.01)
    assert first["slot_order_hash"] == second["slot_order_hash"]

    with pytest.raises(module.CloudStageError) as caught:
        runner._narration_repair_position_registry(
            list(reversed(positions)),
            candidate,
            story_map,
        )
    assert caught.value.code == "cloud.narrative_repair_position_order_invalid"


def test_position_registry_maxima_cannot_exceed_final_word_bound():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)

    registry = runner._build_narration_repair_position_registry(candidate, story_map)

    assert sum(row["word_budget_max"] for row in registry["positions"]) <= 125


def test_position_preselection_drops_low_priority_claims_before_provider_call():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    first_passage = dict(candidate.passages[0])
    panel_id = first_passage["evidence_panel_ids"][0]
    extra_claims = tuple(
        {
            "claim_id": f"extra-claim-{index}",
            "claim_type": "fact",
            "text": f"The first panel supports extra claim {index}.",
            "panel_ids": [panel_id],
            "evidence_panel_ids": [panel_id],
            "qualification": "The ordered panel supports this reading.",
        }
        for index in range(2)
    )
    first_passage["claim_ids"] = [
        *first_passage["claim_ids"],
        *(claim["claim_id"] for claim in extra_claims),
    ]
    enriched_candidate = replace(
        candidate,
        passages=(first_passage, *candidate.passages[1:]),
        evidence_graph={
            "claims": (*candidate.evidence_graph["claims"], *extra_claims)
        },
    )
    enriched_story_map = replace(
        story_map,
        claims=(*story_map.claims, *extra_claims),
    )

    registry = runner._build_narration_repair_position_registry(
        enriched_candidate,
        enriched_story_map,
    )

    assert len(registry["positions"]) == 8


def test_position_repair_reconciles_vector_by_index_and_copies_trusted_lineage():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    registry = runner._build_narration_repair_position_registry(candidate, story_map)
    raw = {
        "rewrites": [
            _position_rewrite_text(row["word_budget"], f"position{index}_")
            for index, row in enumerate(registry["positions"])
        ]
    }

    reconciled = runner._reconcile_narration_repair_vector(
        raw,
        registry,
        candidate,
    )

    original_by_id = {
        str(passage["passage_id"]): passage for passage in candidate.passages
    }
    assert sum(len(str(passage["text"]).split()) for passage in reconciled["script_passages"]) == 120
    for passage in reconciled["script_passages"]:
        original = original_by_id[passage["passage_id"]]
        assert set(passage["claim_ids"]).issubset(set(original["claim_ids"]))
        assert set(passage["evidence_panel_ids"]).issubset(
            set(original["evidence_panel_ids"])
        )
    assert all(
        not any(identifier in str(passage["text"]) for identifier in passage["claim_ids"])
        for passage in reconciled["script_passages"]
    )


def test_position_repair_scope_accepts_trusted_claim_subset_without_new_lineage():
    module = _module()
    runner, candidate, _visual, _story_map = _immutable_slot_fixture(module)
    first_passage = dict(candidate.passages[0])
    retained_claim_id = first_passage["claim_ids"][0]
    first_passage["claim_ids"] = [retained_claim_id]
    repaired_passages = (first_passage, *candidate.passages[1:])
    repaired_claims = [
        dict(claim)
        for claim in candidate.evidence_graph["claims"]
        if claim["claim_id"] == retained_claim_id
        or claim["claim_id"]
        not in set(candidate.passages[0]["claim_ids"])
    ]
    reduced = replace(
        candidate,
        passages=tuple(repaired_passages),
        evidence_graph={"claims": repaired_claims},
    )

    reconciled = runner._narration_repair_scope_reconciled(
        candidate,
        reduced,
        (),
    )

    assert reconciled is not None
    assert reconciled.passages[0]["claim_ids"] == [retained_claim_id]
    assert [claim["claim_id"] for claim in reconciled.evidence_graph["claims"]] == [
        claim["claim_id"] for claim in repaired_claims
    ]


def test_position_repair_accepts_uneven_bounded_slot_budgets():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    registry = runner._build_narration_repair_position_registry(candidate, story_map)
    counts = [row["word_budget"] for row in registry["positions"]]
    counts[0] += 1
    counts[1] -= 1
    raw = {
        "rewrites": [
            _position_rewrite_text(count, f"uneven{index}_")
            for index, count in enumerate(counts)
        ]
    }

    reconciled = runner._reconcile_narration_repair_vector(
        raw,
        registry,
        candidate,
    )

    assert sum(len(str(passage["text"]).split()) for passage in reconciled["script_passages"]) == 120


def test_position_repair_accepts_deterministic_uneven_distribution_within_ranges():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    registry = runner._build_narration_repair_position_registry(candidate, story_map)
    counts = [row["word_budget"] for row in registry["positions"]]
    counts[0] += 1
    counts[1] -= 1
    assert sum(counts) == 120
    raw = {
        "rewrites": [
            _position_rewrite_text(count, f"wide{index}_")
            for index, count in enumerate(counts)
        ]
    }

    reconciled = runner._reconcile_narration_repair_vector(
        raw,
        registry,
        candidate,
    )

    assert sum(len(str(passage["text"]).split()) for passage in reconciled["script_passages"]) == 120


def test_position_repair_budget_failure_exposes_sanitized_shape_metrics():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    registry = runner._build_narration_repair_position_registry(candidate, story_map)
    counts = [row["word_budget"] for row in registry["positions"]]
    counts = [1, 1, 1, 1, 1, 1, 1, 113]
    assert sum(counts) == 120
    raw = {
        "rewrites": [
            _position_rewrite_text(count, f"metrics{index}_")
            for index, count in enumerate(counts)
        ]
    }

    with pytest.raises(module.CloudStageError) as caught:
        runner._reconcile_narration_repair_vector(raw, registry, candidate)

    assert caught.value.code == "cloud.narrative_repair_position_budget_invalid"
    metrics = caught.value.safe_metadata
    assert metrics["container_type"] == "dict"
    assert metrics["top_level_keys"] == ["rewrites"]
    assert metrics["array_count"] == len(counts)
    assert metrics["per_position_word_counts"] == counts
    assert metrics["total_word_count"] == 120
    assert metrics["estimated_duration_s"] == pytest.approx(120 / 2.3, abs=0.01)
    assert metrics["failed_predicate"] == "position_word_dominance"
    assert len(metrics["expected_ranges"]) == len(counts)
    assert all(set(item) == {"position", "target", "min", "max"} for item in metrics["expected_ranges"])
    assert "metrics0_word" not in json.dumps(metrics)


def test_position_repair_success_exposes_sanitized_shape_metrics():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    registry = runner._build_narration_repair_position_registry(candidate, story_map)
    counts = [row["word_budget"] for row in registry["positions"]]
    raw = {
        "rewrites": [
            _position_rewrite_text(count, f"accepted{index}_")
            for index, count in enumerate(counts)
        ]
    }

    reconciled = runner._reconcile_narration_repair_vector(raw, registry, candidate)

    metrics = reconciled["_response_shape_metrics"]
    assert metrics["container_type"] == "dict"
    assert metrics["top_level_keys"] == ["rewrites"]
    assert metrics["array_count"] == len(counts)
    assert metrics["per_position_word_counts"] == counts
    assert metrics["total_word_count"] == 120
    assert metrics["failed_predicate"] is None
    assert "accepted0_word" not in json.dumps(metrics)


def test_position_repair_accepts_total_below_guidance_inside_final_bounds():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    registry = runner._build_narration_repair_position_registry(candidate, story_map)
    counts = [row["word_budget"] for row in registry["positions"]]
    counts[0] -= 1
    counts[1] -= 1
    assert sum(counts) == 118
    raw = {
        "rewrites": [
            _position_rewrite_text(count, f"bounded{index}_")
            for index, count in enumerate(counts)
        ]
    }

    reconciled = runner._reconcile_narration_repair_vector(raw, registry, candidate)

    assert sum(len(str(passage["text"]).split()) for passage in reconciled["script_passages"]) == 118


def test_position_repair_admits_in_range_total_above_position_guidance():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    registry = runner._build_narration_repair_position_registry(candidate, story_map)
    counts = [row["word_budget"] for row in registry["positions"]]
    counts[0] = registry["positions"][0]["word_budget_max"] + 1
    counts[1] -= 3
    assert counts[0] > registry["positions"][0]["word_budget_max"]
    assert sum(counts) == 119
    raw = {
        "rewrites": [
            _position_rewrite_text(count, f"inrange{index}_")
            for index, count in enumerate(counts)
        ]
    }

    reconciled = runner._reconcile_narration_repair_vector(raw, registry, candidate)

    assert sum(len(str(passage["text"]).split()) for passage in reconciled["script_passages"]) == 119


def test_position_repair_accepts_observed_in_range_distribution_as_guidance():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    registry = runner._build_narration_repair_position_registry(candidate, story_map)
    counts = [18, 16, 16, 17, 15, 14, 14, 14]
    assert len(counts) == len(registry["positions"])
    assert sum(counts) == 124
    raw = {
        "rewrites": [
            _position_rewrite_text(count, f"observed{index}_")
            for index, count in enumerate(counts)
        ]
    }

    reconciled = runner._reconcile_narration_repair_vector(raw, registry, candidate)

    assert sum(len(str(passage["text"]).split()) for passage in reconciled["script_passages"]) == 124
    instruction = module.NARRATION_REPAIR_INSTRUCTION
    assert "not hard admission bounds" in instruction


def test_position_repair_micro_compacts_exact_127_words_without_losing_negation():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    registry = runner._build_narration_repair_position_registry(candidate, story_map)
    counts = [16, 16, 16, 16, 16, 16, 15, 16]
    assert sum(counts) == 127
    raw = {"rewrites": _micro_compaction_rewrite_texts(counts)}

    reconciled = runner._reconcile_narration_repair_vector(raw, registry, candidate)

    metrics = reconciled["_response_shape_metrics"]
    compact = metrics["micro_compaction"]
    assert compact["version"] == "narration-micro-compaction-v3"
    assert compact["applied"] is True
    assert compact["before_word_count"] == 127
    assert compact["after_word_count"] == 125
    assert compact["operation_count"] == 2
    assert compact["operation_types"] == ["it_is_to_its", "does_not_to_doesnt"]
    assert len(compact["result_hash"]) == 64
    assert metrics["total_word_count"] == 125
    assert metrics["estimated_duration_s"] == pytest.approx(125 / 2.3, abs=0.01)
    text = " ".join(str(passage["text"]) for passage in reconciled["script_passages"])
    assert "it's" in text
    assert "doesn't" in text
    assert "does not" not in text


def test_micro_compaction_supports_standard_future_and_modal_contractions():
    module = _module()
    counts = [17, 16, 16, 16, 16, 16, 15, 16]
    prefixes = ("it will", "should not", "they will")
    rewrites = []
    for index, count in enumerate(counts):
        prefix = prefixes[index] if index < len(prefixes) else ""
        filler_count = count - len(prefix.split())
        fillers = [f"newcompact{index}word{word_index}" for word_index in range(filler_count)]
        rewrites.append(" ".join(part for part in (prefix, *fillers) if part))

    compacted, metadata = module._micro_compact_rewrites(
        tuple(rewrites),
        total_words=sum(counts),
    )

    assert metadata["version"] == "narration-micro-compaction-v3"
    assert metadata["after_word_count"] == 125
    assert metadata["operation_types"][:3] == [
        "it_will_to_itll",
        "should_not_to_shouldnt",
        "they_will_to_theyll",
    ]
    assert "it'll" in compacted[0]
    assert "shouldn't" in compacted[1]
    assert "they'll" in compacted[2]


def test_position_repair_micro_compacts_126_words_with_auxiliary_contraction():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    registry = runner._build_narration_repair_position_registry(candidate, story_map)
    counts = [16, 16, 16, 16, 16, 16, 15, 15]
    assert sum(counts) == 126
    rewrites = []
    for index, count in enumerate(counts):
        prefix = "it would" if index == 0 else ""
        filler_count = count - len(prefix.split())
        fillers = [f"auxcompact{index}word{word_index}" for word_index in range(filler_count)]
        rewrites.append(" ".join(part for part in (prefix, *fillers) if part))

    reconciled = runner._reconcile_narration_repair_vector(
        {"rewrites": rewrites},
        registry,
        candidate,
    )

    metrics = reconciled["_response_shape_metrics"]
    compact = metrics["micro_compaction"]
    assert compact["version"] == "narration-micro-compaction-v3"
    assert compact["before_word_count"] == 126
    assert compact["after_word_count"] == 125
    assert compact["operation_types"] == ["it_would_to_itd"]
    assert metrics["total_word_count"] == 125
    assert "it'd" in " ".join(str(passage["text"]) for passage in reconciled["script_passages"])


def test_position_repair_micro_compaction_without_safe_operation_fails_closed():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    registry = runner._build_narration_repair_position_registry(candidate, story_map)
    counts = [16, 16, 16, 16, 16, 16, 15, 16]
    raw = {"rewrites": [_position_rewrite_text(count, f"nocompact{index}_") for index, count in enumerate(counts)]}

    with pytest.raises(module.CloudStageError) as caught:
        runner._reconcile_narration_repair_vector(raw, registry, candidate)

    assert caught.value.code == "cloud.narrative_repair_micro_compaction_unavailable"
    metrics = caught.value.safe_metadata
    assert metrics["failed_predicate"] == "micro_compaction_no_safe_operation"
    assert metrics["total_word_count"] == 127
    assert metrics["micro_compaction"]["operation_count"] == 0
    assert "no_compact0_word" not in json.dumps(metrics)


def test_position_repair_micro_compaction_window_rejects_large_overshoot():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    registry = runner._build_narration_repair_position_registry(candidate, story_map)
    counts = [17] * 8
    raw = {"rewrites": [_position_rewrite_text(count, f"toowide{index}_") for index, count in enumerate(counts)]}

    with pytest.raises(module.CloudStageError) as caught:
        runner._reconcile_narration_repair_vector(raw, registry, candidate)

    assert caught.value.code == "cloud.narrative_repair_micro_compaction_unavailable"
    assert caught.value.safe_metadata["failed_predicate"] == "micro_compaction_window"
    assert caught.value.safe_metadata["total_word_count"] == 136


def test_position_repair_in_range_vector_remains_unchanged_by_micro_compaction():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    registry = runner._build_narration_repair_position_registry(candidate, story_map)
    counts = [row["word_budget"] for row in registry["positions"]]
    raw = {"rewrites": [_position_rewrite_text(count, f"unchanged{index}_") for index, count in enumerate(counts)]}

    reconciled = runner._reconcile_narration_repair_vector(raw, registry, candidate)

    compact = reconciled["_response_shape_metrics"]["micro_compaction"]
    assert compact["version"] == "narration-micro-compaction-v3"
    assert compact["applied"] is False
    assert compact["before_word_count"] == 120
    assert compact["after_word_count"] == 120
    assert compact["operation_count"] == 0
    assert compact["result_hash"] == module._hash({"rewrites": raw["rewrites"]})
    combined = " ".join(str(passage["text"]) for passage in reconciled["script_passages"])
    assert all(_position_rewrite_text(count, f"unchanged{index}_") in combined for index, count in enumerate(counts))


def _repair_identity_metadata(module):
    return {
        "policy_version": "narration-repair-identity-v1",
        "panel_lineage": {
            "ordered_panel_ids": ["panel-1", "panel-2", "panel-3"],
            "panel_identity_hashes": ["a" * 64, "b" * 64, "c" * 64],
            "visual_evidence_hash": "v" * 64,
            "panels": [
                {
                    "panel_id": "panel-1",
                    "source_order": 10,
                    "prepared_order": 0,
                    "evidence_hash": "a" * 64,
                },
                {
                    "panel_id": "panel-2",
                    "source_order": 11,
                    "prepared_order": 1,
                    "evidence_hash": "b" * 64,
                },
                {
                    "panel_id": "panel-3",
                    "source_order": 12,
                    "prepared_order": 2,
                    "evidence_hash": "c" * 64,
                },
            ],
        },
        "model": {"identity_hash": "m" * 64},
        "prompt": {"version": "narration-v1", "sha256": "p" * 64},
        "story": {
            "panel_ids": ["panel-1", "panel-2", "panel-3"],
            "beats_hash": "b" * 64,
            "claims_hash": "c" * 64,
            "causal_chain_hash": "h" * 64,
            "story_map_hash": "s" * 64,
            "beat_count": 2,
            "claim_count": 3,
            "causal_link_count": 1,
        },
        "selection": {
            "beat_ids": ["beat-1", "beat-2"],
            "panel_ids": ["panel-1", "panel-2", "panel-3"],
            "claim_ids": ["claim-1", "claim-2"],
            "selection_hash": "e" * 64,
        },
        "slot_registry": {
            "slot_ids": ["slot-1", "slot-2"],
            "claim_ids": ["claim-1", "claim-2"],
            "evidence_panel_ids": ["panel-1", "panel-2", "panel-3"],
            "slot_order_hash": "o" * 64,
        },
        "candidate": {
            "candidate_hash": "q" * 64,
            "visual_evidence_hash": "v" * 64,
            "model_identity_hash": "m" * 64,
            "prompt_version": "narration-v1",
            "prompt_sha256": "p" * 64,
            "story_map_hash": "s" * 64,
        },
    }


def test_repair_identity_migration_accepts_reordered_serialization_and_prepared_order_only_change():
    module = _module()
    old = _repair_identity_metadata(module)
    new = json.loads(json.dumps(old))
    new["panel_lineage"]["panels"] = list(reversed(new["panel_lineage"]["panels"]))
    for panel in new["panel_lineage"]["panels"]:
        panel["prepared_order"] = 700 - panel["source_order"]
    new["story"] = dict(reversed(list(new["story"].items())))

    record = module.reconcile_narration_repair_identity(
        old,
        new,
        old_identity_hash="old-identity",
        new_identity_hash="new-identity",
        reason="prepared-order-only migration",
    )

    assert record["status"] == "migrated"
    assert record["old_identity_hash"] == "old-identity"
    assert record["new_identity_hash"] == "new-identity"
    assert len(record["canonical_comparison_hash"]) == 64
    assert record["counts"] == {
        "old_panel_count": 3,
        "new_panel_count": 3,
        "old_beat_count": 2,
        "new_beat_count": 2,
        "old_claim_count": 3,
        "new_claim_count": 3,
        "old_slot_count": 2,
        "new_slot_count": 2,
    }


def test_repair_identity_migration_event_is_idempotent_for_warm_resume():
    module = _module()
    old = _repair_identity_metadata(module)
    new = json.loads(json.dumps(old))
    cache = module.MemoryStageCache()

    first = module.persist_narration_repair_identity_migration(
        cache,
        old,
        new,
        old_identity_hash="old-identity",
        new_identity_hash="new-identity",
        model_identity_hash="m" * 64,
        prompt_version="narration-v1",
        prompt_sha256="p" * 64,
        reason="equivalent cache migration",
    )
    second = module.persist_narration_repair_identity_migration(
        cache,
        old,
        new,
        old_identity_hash="old-identity",
        new_identity_hash="new-identity",
        model_identity_hash="m" * 64,
        prompt_version="narration-v1",
        prompt_sha256="p" * 64,
        reason="equivalent cache migration",
    )

    assert first == second
    assert len(list(cache.iter_records(cache_type=module.NARRATION_REPAIR_IDENTITY_MIGRATION_VERSION))) == 1


def test_candidate_load_uses_equivalent_identity_migration_record(monkeypatch):
    module = _module()
    identity = _identity(module)
    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=identity,
        cache=module.MemoryStageCache(),
        max_attempts=1,
    )
    prompt = runner.prompts["narration"]
    source = {"source_identity": "current"}
    candidate_payload = {"spoken_text": "candidate"}
    candidate = __import__("types").SimpleNamespace(
        as_dict=lambda: candidate_payload,
        visual_evidence_hash="v" * 64,
    )
    visual = __import__("types").SimpleNamespace(
        visual_evidence_hash="v" * 64,
        panels=(),
    )
    record = {
        "cache_type": module.NARRATION_REPAIR_CANDIDATE_VERSION,
        "candidate": candidate_payload,
        "candidate_hash": module._hash(candidate_payload),
        "source_identity_hash": "old-identity",
        "model_identity_hash": identity.identity_hash,
        "prompt_version": prompt[0],
        "prompt_sha256": prompt[1],
        "failure_codes": ["cloud.narrative_duration_out_of_range"],
    }
    runner.cache.put(runner._narration_repair_candidate_key(source, prompt), record)
    migrated = {
        **record,
        "source_identity_hash": module._hash(source),
        "identity_migration": {"status": "migrated"},
    }
    monkeypatch.setattr(
        module.NarrationResult,
        "from_dict",
        classmethod(lambda _cls, _payload: candidate),
    )
    monkeypatch.setattr(
        runner,
        "_migrate_narration_repair_candidate_record",
        lambda **_kwargs: migrated,
    )
    monkeypatch.setattr(
        runner,
        "_narration_contract_failures",
        lambda _candidate: ("cloud.narrative_duration_out_of_range",),
    )
    monkeypatch.setattr(module, "_narration_result_is_usable", lambda *_args, **_kwargs: True)

    loaded = runner._load_narration_repair_candidate(
        source=source,
        prompt=prompt,
        visual=visual,
    )

    assert loaded == (candidate, ("cloud.narrative_duration_out_of_range",))


@pytest.mark.parametrize(
    ("section", "field"),
    (
        ("panel_lineage", "visual_evidence_hash"),
        ("model", "identity_hash"),
        ("prompt", "sha256"),
        ("story", "beats_hash"),
        ("story", "claims_hash"),
        ("story", "causal_chain_hash"),
        ("selection", "claim_ids"),
        ("slot_registry", "evidence_panel_ids"),
        ("candidate", "story_map_hash"),
    ),
)
def test_repair_identity_migration_rejects_semantic_dependency_changes(section, field):
    module = _module()
    old = _repair_identity_metadata(module)
    new = json.loads(json.dumps(old))
    value = new[section][field]
    if isinstance(value, list):
        new[section][field] = [*value, "changed"]
    else:
        new[section][field] = "changed"

    with pytest.raises(module.CloudStageError) as caught:
        module.reconcile_narration_repair_identity(
            old,
            new,
            old_identity_hash="old-identity",
            new_identity_hash="new-identity",
            reason="semantic mismatch",
        )

    assert caught.value.code == "cloud.narrative_repair_identity_mismatch"
    assert caught.value.safe_metadata["mismatch_field"] == f"{section}.{field}"
    assert caught.value.safe_metadata["status"] == "rejected"
    assert "changed" not in json.dumps(caught.value.safe_metadata)


def test_narration_and_targeted_repair_request_budgets_are_independent():
    module = _module()
    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=_identity(module),
        max_attempts=1,
        max_narration_requests=1,
        max_repair_requests=1,
    )

    assert runner._call(lambda: "narration", request_stage="narration") == "narration"
    assert runner._call(lambda: "repair", request_stage="narration_repair") == "repair"
    with pytest.raises(module.CloudStageError) as caught:
        runner._call(lambda: "must-not-run", request_stage="narration")

    assert caught.value.code == "cloud.request_budget_exceeded"
    assert runner.request_count == 2
    assert runner.request_counts == {
        "narration": 1,
        "narration_repair": 1,
        "other": 0,
    }


def test_legacy_global_request_budget_remains_compatible_with_stage_labels():
    module = _module()
    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=_identity(module),
        max_attempts=1,
        max_requests=1,
    )

    assert runner._call(lambda: "first", request_stage="narration") == "first"
    with pytest.raises(module.CloudStageError) as caught:
        runner._call(lambda: "must-not-run", request_stage="narration_repair")

    assert caught.value.code == "cloud.request_budget_exceeded"
    assert runner.request_count == 1


def test_batch_cli_summary_does_not_serialize_stage_payloads():
    batch_cli = importlib.import_module("scripts.run_cloud_multimodal_batch")
    simple_namespace = __import__("types").SimpleNamespace
    record = simple_namespace(
        job_id="job-a",
        state=simple_namespace(value="NEEDS_REVIEW"),
        error_code="cloud.narrative_not_grounded",
        review_queue=[{"code": "cloud.narrative_not_grounded"}],
        stage_results={
            "narration": {"spoken_text": "provider text must not print"},
            "usage": {
                "request_count": 2,
                "request_counts": {"narration": 1, "narration_repair": 1},
            },
        },
    )

    summary = batch_cli._safe_job_summary(record)

    encoded = json.dumps(summary, sort_keys=True)
    assert summary["job_id"] == "job-a"
    assert summary["state"] == "NEEDS_REVIEW"
    assert summary["error_code"] == "cloud.narrative_not_grounded"
    assert summary["usage"] == {
        "request_count": 2,
        "request_counts": {"narration": 1, "narration_repair": 1},
    }
    assert summary["review_codes"] == ["cloud.narrative_not_grounded"]
    assert "stage_results" not in summary
    assert "provider text must not print" not in encoded


def test_position_repair_duration_gate_remains_hard_after_compaction_boundary(monkeypatch):
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    registry = runner._build_narration_repair_position_registry(candidate, story_map)
    counts = [row["word_budget"] for row in registry["positions"]]
    counts[0] += 5
    assert sum(counts) == 125
    raw = {"rewrites": [_position_rewrite_text(count, f"duration{index}_") for index, count in enumerate(counts)]}
    monkeypatch.setattr(module.script, "estimate_narration_duration", lambda *_args: 61.0)

    with pytest.raises(module.CloudStageError) as caught:
        runner._reconcile_narration_repair_vector(raw, registry, candidate)

    assert caught.value.code == "cloud.narrative_repair_position_budget_invalid"
    assert caught.value.safe_metadata["failed_predicate"] == "aggregate_duration"
    assert caught.value.safe_metadata["total_word_count"] == 125
    assert caught.value.safe_metadata["estimated_duration_s"] == 61.0


@pytest.mark.parametrize("mutation", ("old_id_wrapper", "wrong_count", "wrong_type"))
def test_position_repair_rejects_non_positional_provider_shapes(mutation):
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    registry = runner._build_narration_repair_position_registry(candidate, story_map)
    valid = [
        _position_rewrite_text(row["word_budget"], f"position{index}_")
        for index, row in enumerate(registry["positions"])
    ]
    if mutation == "old_id_wrapper":
        raw = {"repair_slots": {"retained_slot_ids": [], "slots": []}}
    elif mutation == "wrong_count":
        raw = {"rewrites": valid[:-1]}
    else:
        raw = {"rewrites": "not-an-array"}

    with pytest.raises(module.CloudStageError) as caught:
        runner._reconcile_narration_repair_vector(raw, registry, candidate)
    assert caught.value.code == "cloud.narrative_repair_position_contract_invalid"


def test_position_repair_shape_metrics_survive_early_contract_rejection():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    registry = runner._build_narration_repair_position_registry(candidate, story_map)
    valid = [
        _position_rewrite_text(row["word_budget"], f"position{index}_")
        for index, row in enumerate(registry["positions"])
    ]
    raw = {"rewrites": valid[:-1]}

    with pytest.raises(module.CloudStageError) as caught:
        runner._reconcile_narration_repair_vector(raw, registry, candidate)

    metadata = caught.value.safe_metadata
    assert caught.value.code == "cloud.narrative_repair_position_contract_invalid"
    assert metadata["failed_predicate"] == "rewrite_count"
    assert metadata["array_count"] == len(valid) - 1
    assert metadata["array_item_types"] == ["str"] * (len(valid) - 1)
    assert metadata["per_position_word_counts"] == [
        module.script.narration_word_count(text) for text in valid[:-1]
    ]
    assert metadata["total_word_count"] == sum(metadata["per_position_word_counts"])
    assert isinstance(metadata["estimated_duration_s"], float)
    assert len(metadata["expected_ranges"]) == len(registry["positions"])
    assert "position" in json.dumps(metadata)
    assert "position0_" not in json.dumps(metadata)


def test_immutable_repair_slots_copy_trusted_lineage_and_reject_provider_ids():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    slots = runner._build_narration_repair_slots(candidate, story_map)
    assert len(slots) == len(candidate.passages)
    assert all(slot.slot_id.startswith("narration_slot_v1_") for slot in slots)
    assert all(slot.claim_ids and slot.evidence_panel_ids for slot in slots)
    assert [slot.causal_position for slot in slots] == list(range(len(slots)))
    assert tuple(slots) == runner._build_narration_repair_slots(candidate, story_map)
    registry = runner._narration_repair_slot_registry(slots)
    assert registry["version"] == "narration-repair-slot-registry-v1"
    assert registry["registry_hash"] == runner._narration_repair_slot_registry(slots)["registry_hash"]

    valid_raw = {
        "repair_slots": {
            "retained_slot_ids": [slot.slot_id for slot in slots],
            "dropped_slot_ids": [],
            "slots": [
                {"slot_id": slot.slot_id, "text": "A locally grounded repair sentence."}
                for slot in slots
            ],
        }
    }
    reconciled = runner._reconcile_narration_repair_slots(
        valid_raw,
        slots,
        candidate,
    )
    for original, repaired in zip(
        candidate.passages,
        reconciled["script_passages"],
        strict=True,
    ):
        assert repaired["passage_id"] == original["passage_id"]
        assert repaired["claim_ids"] == original["claim_ids"]
        assert repaired["evidence_panel_ids"] == original["evidence_panel_ids"]

    retained = [slot.slot_id for slot in slots]
    dropped = []
    raw = {
        "repair_slots": {
            "retained_slot_ids": retained,
            "dropped_slot_ids": dropped,
            "slots": [
                {
                    "slot_id": slot_id,
                    "text": "A locally grounded repair sentence.",
                    "claim_ids": ["provider-invented-claim"],
                }
                for slot_id in retained
            ],
        }
    }
    with pytest.raises(module.CloudStageError) as caught:
        runner._reconcile_narration_repair_slots(raw, slots, candidate)
    assert caught.value.code == "cloud.narrative_repair_slot_contract_invalid"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ("unknown", "cloud.narrative_repair_slot_unknown"),
        ("duplicate", "cloud.narrative_repair_slot_duplicate"),
        ("missing", "cloud.narrative_repair_slot_missing"),
    ),
)
def test_immutable_repair_slots_reject_unknown_duplicate_or_missing_ids(mutation, expected_code):
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    slots = runner._build_narration_repair_slots(candidate, story_map)
    slot_ids = [slot.slot_id for slot in slots]
    retained = slot_ids[:4]
    dropped = slot_ids[4:]
    if mutation == "unknown":
        retained = ["narration_slot_v1_unknown", *retained[1:]]
    elif mutation == "duplicate":
        retained = [retained[0], retained[0], *retained[2:]]
    else:
        dropped = []
    raw = {
        "repair_slots": {
            "retained_slot_ids": retained,
            "dropped_slot_ids": dropped,
            "slots": [
                {"slot_id": slot_id, "text": "A grounded repair sentence."}
                for slot_id in retained
                if slot_id in slot_ids
            ],
        }
    }
    with pytest.raises(module.CloudStageError) as caught:
        runner._reconcile_narration_repair_slots(raw, slots, candidate)
    assert caught.value.code == expected_code


def test_narration_contract_diagnostic_keeps_only_field_and_count():
    module = _module()
    diagnostic = module._safe_narration_contract_diagnostic(
        "script passage text leaked a private value",
        {"script_passages": [{}, {}], "observations": [{}, {}], "evidence_graph": {"claims": [{}]}},
    )
    assert diagnostic == "field=script_passages;count=2"
    assert "private" not in diagnostic


def test_unmapped_repair_claims_report_only_safe_field_and_count():
    module = _module()
    story_map = module.StoryMapResult(
        panel_ids=("panel-1",),
        beats=(),
        causal_chain=(),
        claims=(
            {
                "claim_id": "claim-1",
                "claim_type": "fact",
                "text": "A grounded fact.",
                "qualification": "The panel supports it.",
                "panel_ids": ["panel-1"],
            },
        ),
        story_map_hash="s" * 64,
        model_identity_hash="m" * 64,
        prompt_version="cloud-causal-map-v2",
        prompt_sha256="p" * 64,
        visual_evidence_hash="v" * 64,
    )
    with pytest.raises(module.CloudStageError) as caught:
        module.CloudStageRunner._claims_from_causal_map(
            [{"claim_ids": ["foreign-claim"]}],
            story_map,
        )
    assert caught.value.code == "cloud.narrative_not_grounded"
    assert str(caught.value) == "field=claim_ids;count=1"


def test_visual_cache_identity_ignores_transient_preparation_fields():
    module = _module()
    panels = _panels(module, "identity")
    equivalent = tuple(
        replace(
            panel,
            source_order=panel.source_order + 700,
            source_family="temporary-preparation-family",
            strip_region_id=f"temporary-{panel.panel_id}",
            coverage_map_version="new-review-metadata",
            coverage_map_hash="c" * 64,
            segmentation_version="new-segmentation-metadata",
        )
        for panel in panels
    )
    ordered = module.CloudStageRunner._ordered_panels(panels)
    equivalent_ordered = module.CloudStageRunner._ordered_panels(equivalent)

    assert module._visual_panel_identity_hashes(ordered) == module._visual_panel_identity_hashes(
        equivalent_ordered
    )
    assert module._visual_source_hash(ordered) == module._visual_source_hash(equivalent_ordered)

    changed_crop = list(panels)
    changed_crop[0] = replace(changed_crop[0], panel_bounds=(0, 0, 90, 100))
    assert module._visual_panel_identity_hashes(ordered)[0] != module._visual_panel_identity_hashes(
        module.CloudStageRunner._ordered_panels(tuple(changed_crop))
    )[0]


def test_visual_chunk_identity_invalidates_only_changed_chunk_and_tracks_model_prompt():
    module = _module()
    panels = _panels(module, "chunk-identity")
    identity = _identity(module)
    prompt = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=identity,
    ).prompts["visual"]
    ordered = module.CloudStageRunner._ordered_panels(panels)
    chunks = module._visual_panel_chunks(ordered, max_panels=1, overlap=0)
    keys = [
        module._visual_chunk_cache_key(
            chunk,
            chunk_index=index,
            batch_count=len(chunks),
            model_identity=identity,
            prompt=prompt,
        )
        for index, chunk in enumerate(chunks)
    ]

    changed = list(panels)
    changed[0] = replace(changed[0], payload=b"changed-chunk-payload", payload_checksum="")
    changed_chunks = module._visual_panel_chunks(
        module.CloudStageRunner._ordered_panels(tuple(changed)),
        max_panels=1,
        overlap=0,
    )
    changed_keys = [
        module._visual_chunk_cache_key(
            chunk,
            chunk_index=index,
            batch_count=len(changed_chunks),
            model_identity=identity,
            prompt=prompt,
        )
        for index, chunk in enumerate(changed_chunks)
    ]
    assert keys[0] != changed_keys[0]
    assert keys[1:] == changed_keys[1:]

    changed_model = replace(identity, model="different-pinned-model")
    model_keys = [
        module._visual_chunk_cache_key(
            chunk,
            chunk_index=index,
            batch_count=len(chunks),
            model_identity=changed_model,
            prompt=prompt,
        )
        for index, chunk in enumerate(chunks)
    ]
    changed_prompt = ("visual-contract-next", "d" * 64, prompt[2])
    prompt_keys = [
        module._visual_chunk_cache_key(
            chunk,
            chunk_index=index,
            batch_count=len(chunks),
            model_identity=identity,
            prompt=changed_prompt,
        )
        for index, chunk in enumerate(chunks)
    ]
    assert model_keys != keys
    assert prompt_keys != keys


def test_equivalent_preparation_migrates_legacy_visual_cache_without_provider_call():
    module = _module()
    panels = _panels(module, "migration")
    identity = _identity(module)
    runner = module.CloudStageRunner(provider=_FakeProvider(), model_identity=identity)
    prompt = runner.prompts["visual"]
    legacy = module.VisualStageResult(
        panels=tuple(
            _visual_row(panel.descriptor())
            | {
                "source_asset_id": panel.source_asset_id,
                "source_order": panel.source_order,
                "source_checksum": panel.source_checksum,
            }
            for panel in panels
        ),
        source_hash=module._hash([panel.descriptor() for panel in panels]),
        model_identity_hash=identity.identity_hash,
        prompt_version=prompt[0],
        prompt_sha256=prompt[1],
    ).as_dict()

    migrated = module._migrate_visual_cache_identity(
        legacy,
        panels,
        model_identity=identity,
        prompt=prompt,
    )

    assert migrated is not None
    assert migrated["source_hash"] == module._visual_source_hash(
        module.CloudStageRunner._ordered_panels(panels)
    )
    assert migrated["cache_identity_version"] == module.VISUAL_CACHE_IDENTITY_VERSION
    assert len(migrated["panel_identity_hashes"]) == len(panels)

    lineage = {
        "observations": [
            {
                "panel_id": panel.panel_id,
                "source_asset_id": panel.source_asset_id,
                "source_index": index,
                "region_bounds": {
                    "x": panel.panel_bounds[0],
                    "y": panel.panel_bounds[1],
                    "width": panel.panel_bounds[2] - panel.panel_bounds[0],
                    "height": panel.panel_bounds[3] - panel.panel_bounds[1],
                },
            }
            for index, panel in enumerate(panels)
        ]
    }
    legacy_with_lineage = dict(legacy)
    legacy_with_lineage["source_hash"] = "f" * 64
    migrated_with_lineage = module._migrate_visual_cache_identity(
        legacy_with_lineage,
        panels,
        model_identity=identity,
        prompt=prompt,
        persisted_lineage=lineage,
    )
    assert migrated_with_lineage is not None
    assert (
        migrated_with_lineage["cache_identity_migration_proof"]
        == "persisted_lineage_and_payload_derivation"
    )
    bad_lineage = {
        "observations": [dict(item) for item in lineage["observations"]]
    }
    bad_lineage["observations"][0] = dict(bad_lineage["observations"][0])
    bad_lineage["observations"][0]["region_bounds"] = {
        **bad_lineage["observations"][0]["region_bounds"],
        "width": 99,
    }
    assert (
        module._migrate_visual_cache_identity(
            legacy_with_lineage,
            panels,
            model_identity=identity,
            prompt=prompt,
            persisted_lineage=bad_lineage,
        )
        is None
    )

    changed = list(panels)
    changed[-1] = replace(changed[-1], payload=b"tampered-payload", payload_checksum="")
    assert (
        module._migrate_visual_cache_identity(
            legacy,
            tuple(changed),
            model_identity=identity,
            prompt=prompt,
        )
        is None
    )


def test_metadata_only_manifest_migrates_exact_legacy_descriptor_without_provider_call():
    module = _module()
    base_panels = _panels(module, "metadata-migration")
    panels = tuple(
        replace(
            panel,
            metadata_only=True,
            prepared_order=index,
            identity_payload_checksum="a" * 64,
            identity_descriptor_hash="b" * 64,
            source_identity_hash="c" * 64,
        )
        for index, panel in enumerate(base_panels)
    )
    identity = _identity(module)
    prompt = module.CloudStageRunner(
        provider=_FakeProvider(), model_identity=identity
    ).prompts["visual"]

    def legacy_descriptor(panel):
        descriptor = {
            "panel_id": panel.panel_id,
            "source_asset_id": panel.source_asset_id,
            "source_order": panel.source_order,
            "mime_type": panel.mime_type,
            "source_checksum": panel.source_checksum,
            "payload_checksum": panel.payload_checksum,
        }
        if panel.panel_bounds is not None:
            descriptor["panel_bounds"] = list(panel.panel_bounds)
        if panel.source_dimensions is not None:
            descriptor["source_dimensions"] = list(panel.source_dimensions)
        if panel.strip_region_id:
            descriptor["strip_region_id"] = panel.strip_region_id
        if panel.coverage_map_version:
            descriptor["coverage_map_version"] = panel.coverage_map_version
        if panel.coverage_map_hash:
            descriptor["coverage_map_hash"] = panel.coverage_map_hash
        if panel.segmentation_version:
            descriptor["segmentation_version"] = panel.segmentation_version
        return descriptor

    legacy_descriptors = [legacy_descriptor(panel) for panel in panels]
    legacy = module.VisualStageResult(
        panels=tuple(
            _visual_row(legacy_descriptor(panel))
            | {
                "source_asset_id": panel.source_asset_id,
                "source_order": panel.source_order,
                "source_checksum": panel.source_checksum,
            }
            for panel in panels
        ),
        source_hash=module._hash(legacy_descriptors),
        model_identity_hash=identity.identity_hash,
        prompt_version=prompt[0],
        prompt_sha256=prompt[1],
    ).as_dict()

    migrated = module._migrate_visual_cache_identity(
        legacy,
        panels,
        model_identity=identity,
        prompt=prompt,
    )

    assert migrated is not None
    assert migrated["cache_identity_migration_proof"] == "legacy_descriptor_hash"


def test_visual_runner_reuses_metadata_only_legacy_cache_without_provider_call():
    module = _module()
    base_panels = _panels(module, "runner-migration")
    panels = tuple(
        replace(
            panel,
            metadata_only=True,
            prepared_order=index,
            identity_payload_checksum="a" * 64,
            identity_descriptor_hash="b" * 64,
            source_identity_hash="c" * 64,
        )
        for index, panel in enumerate(base_panels)
    )
    base_identity = _identity(module)
    current_identity = replace(
        base_identity,
        prompt_versions=dict(base_identity.prompt_versions)
        | {"visual_narrative_repair": module.CURRENT_VISUAL_REPAIR_PROMPT_VERSION},
    )
    legacy_identity = replace(
        current_identity,
        prompt_versions=dict(current_identity.prompt_versions)
        | {"visual_narrative_repair": module.LEGACY_VISUAL_REPAIR_PROMPT_VERSION},
    )
    provider = _FakeProvider()

    def fail_observe(_request):
        raise AssertionError("legacy visual cache must be reused before provider call")

    provider.observe = fail_observe
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=current_identity,
    )
    prompt = runner.prompts["visual"]
    legacy_descriptors = [module._legacy_visual_descriptor(panel) for panel in panels]
    legacy = module.VisualStageResult(
        panels=tuple(
            _visual_row(descriptor)
            | {
                "source_asset_id": panel.source_asset_id,
                "source_order": panel.source_order,
                "source_checksum": panel.source_checksum,
            }
            for panel, descriptor in zip(panels, legacy_descriptors, strict=True)
        ),
        source_hash=module._hash(legacy_descriptors),
        model_identity_hash=legacy_identity.identity_hash,
        prompt_version=prompt[0],
        prompt_sha256=prompt[1],
    ).as_dict()

    class Cache:
        def __init__(self):
            self.put_values = {}

        def get(self, _key):
            return None

        def put(self, key, value):
            self.put_values[key] = dict(value)

        def iter_records(self):
            yield legacy

    cache = Cache()
    runner.cache = cache
    result = runner.run_visual_evidence(panels)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels)
    assert provider.calls == []
    assert len(cache.put_values) == 1
    migrated = next(iter(cache.put_values.values()))
    assert migrated["model_identity_hash"] == current_identity.identity_hash
    assert migrated["cache_identity_migration_proof"] == (
        "legacy_model_identity_and_descriptor_hash"
    )

def test_batch_resume_migrates_legacy_visual_without_visual_provider_call(tmp_path):
    module = _module()
    panels = _panels(module, "service-migration")
    identity = _identity(module)
    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=identity,
    )
    prompt = runner.prompts["visual"]
    legacy = module.VisualStageResult(
        panels=tuple(
            _visual_row(panel.descriptor())
            | {
                "source_asset_id": panel.source_asset_id,
                "source_order": panel.source_order,
                "source_checksum": panel.source_checksum,
            }
            for panel in panels
        ),
        source_hash=module._hash([panel.descriptor() for panel in panels]),
        model_identity_hash=identity.identity_hash,
        prompt_version=prompt[0],
        prompt_sha256=prompt[1],
    ).as_dict()
    store = module.JsonJobStore(tmp_path)
    record = module.ChapterJobRecord(job_id="service-migration")
    record.stage_results["visual"] = legacy
    store.save(record)

    provider = _FakeProvider()
    service = module.CloudBatchService(
        runner=module.CloudStageRunner(provider=provider, model_identity=identity),
        store=store,
    )
    result = service.run_job("service-migration", panels)

    # The compact fixture intentionally stops at the existing story-map
    # grounding gate; migration has already happened before that boundary.
    assert result.state == module.ChapterState.NEEDS_REVIEW
    assert result.error_code == "cloud.narrative_not_grounded"
    assert not [call for call in provider.calls if call[0] == "visual"]
    persisted = store.load("service-migration")
    assert persisted is not None
    assert (
        persisted.stage_results["visual"]["cache_identity_version"]
        == module.VISUAL_CACHE_IDENTITY_VERSION
    )

def test_narration_targeted_repair_reuses_grounding_and_repairs_duration(
    tmp_path, monkeypatch
):
    module = _module()
    panels = _panels(module)
    panel_ids = [panel.panel_id for panel in panels]
    visual_rows = []
    for panel in panels:
        observation = _visual_row(panel.descriptor())
        visual_rows.append(
            {
                "panel_id": panel.panel_id,
                "source_asset_id": panel.source_asset_id,
                "source_order": panel.source_order,
                "source_checksum": panel.source_checksum,
                "observation": observation,
                "visual_evidence": observation["visual_evidence"],
                "evidence_hash": "",
            }
        )
    visual = module.VisualStageResult(
        panels=tuple(visual_rows),
        source_hash="targeted-repair-source",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )
    story_map = module.StoryMapResult(
        panel_ids=tuple(panel_ids),
        beats=(
            {
                "beat_id": "beat-all",
                "panel_ids": panel_ids,
                "summary": "the visible sequence develops",
            },
        ),
        causal_chain=(
            {
                "from_beat": "beat-all",
                "to_beat": "beat-all",
                "reason": "the visible sequence continues",
            },
        ),
        claims=tuple(
            {
                "claim_id": f"claim-{index}",
                "claim_type": "fact",
                "text": f"The visible sequence develops claim {index}.",
                "panel_ids": panel_ids,
                "evidence_panel_ids": panel_ids,
                "qualification": "The ordered panels support this reading.",
            }
            for index in range(8)
        ),
        story_map_hash="s" * 64,
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="cloud-causal-map-v2",
        prompt_sha256="c" * 64,
        visual_evidence_hash=visual.visual_evidence_hash,
    )

    def as_candidate():
        output = _narrative_output("repair", panel_ids)
        for passage_index, passage in enumerate(output["script_passages"]):
            passage["claim_ids"] = [
                f"claim-{passage_index * 2}",
                f"claim-{passage_index * 2 + 1}",
            ]
            passage["evidence_panel_ids"] = list(panel_ids)
        output["evidence_graph"] = {
            "claims": [dict(claim) for claim in story_map.claims]
        }
        spoken = "\n\n".join(
            str(passage["text"]).strip() for passage in output["script_passages"]
        )
        return module.NarrationResult(
            spoken_text=spoken,
            display_words=module.derive_display_words(spoken),
            passages=tuple(dict(item) for item in output["script_passages"]),
            ending_kind=str(output["narrative_outline"]["ending_kind"]),
            word_count=172,
            estimated_duration_s=69.57,
            qc_report={"signals": {}, "warnings": []},
            model_identity_hash=_identity(module).identity_hash,
            prompt_version="vision-first-story-analyzer-v3",
            prompt_sha256="n" * 64,
            observations=tuple(dict(item) for item in output["observations"]),
            continuity_ledger=dict(output["continuity_ledger"]),
            evidence_graph=dict(output["evidence_graph"]),
            story_spine=dict(output["narrative_outline"]["story_spine"]),
            visual_evidence_hash=visual.visual_evidence_hash,
        )

    class TargetedRepairProvider(_FakeProvider):
        def __post_init__(self):
            super().__post_init__()
            self.repair_payloads = []
            self.repair_prompts = []

        def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
            if stage == "narration_repair":
                self.calls.append((stage, prompt_version, prompt_sha256))
                self.repair_payloads.append(dict(payload))
                self.repair_prompts.append(
                    (prompt_version, prompt_sha256, prompt_text)
                )
                return _provider_position_vector(payload)
            return super().complete_json(
                stage=stage,
                prompt_version=prompt_version,
                prompt_sha256=prompt_sha256,
                prompt_text=prompt_text,
                payload=payload,
            )

    provider = TargetedRepairProvider()
    analyzer_contract = importlib.import_module("app.services.analyzer_contract")
    dialogue_copy_flags = []
    original_validate = analyzer_contract.validate_analyzer_output

    def capture_validation(output, **kwargs):
        dialogue_copy_flags.append(kwargs.get("allow_dialogue_copy", False))
        return original_validate(output, **kwargs)

    monkeypatch.setattr(
        analyzer_contract,
        "validate_analyzer_output",
        capture_validation,
    )
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(tmp_path / "targeted-repair-cache"),
        max_attempts=1,
    )
    candidate = as_candidate()
    local_observations, local_structural = runner._narration_observations(
        visual,
        panels,
    )
    candidate = replace(
        candidate,
        observations=tuple(local_observations),
        continuity_ledger=dict(local_structural["continuity_ledger"]),
        prompt_sha256=runner.prompts["narration"][1],
    )
    result = runner.run_narration_repair_candidate(
        candidate,
        visual,
        story_map,
        panels=panels,
    )

    assert len(provider.repair_payloads) == 1
    assert len(provider.repair_prompts) == 1
    repair_prompt_version, repair_prompt_sha256, repair_prompt_text = (
        provider.repair_prompts[0]
    )
    assert (
        repair_prompt_version
        == "vision-first-story-analyzer-v3-targeted-position-repair-v8"
    )
    assert len(repair_prompt_sha256) == 64
    assert "TARGETED NARRATION POSITION REPAIR" in repair_prompt_text
    assert "revise it until the total is 115-125 words" in repair_prompt_text
    assert "never return a vector above 125 words" in repair_prompt_text
    assert "cannot repair larger responses" in repair_prompt_text
    assert "never exceed 15 words in any single rewrite" in repair_prompt_text
    assert "delete whole words whenever a position exceeds its 15-word cap" in (
        repair_prompt_text
    )
    assert "exactly 14 or 15 words per position, aiming for 15" in repair_prompt_text
    assert repair_prompt_text != runner.prompts["narration"][2]
    assert provider.repair_payloads[0]["targeted_repair"]["failure_codes"] == [
        "cloud.narrative_duration_out_of_range",
        "cloud.narrative_word_count_out_of_range",
    ]
    assert dialogue_copy_flags == [False]
    assert result.estimated_duration_s >= 50.0
    assert 115 <= result.word_count <= 125
    assert result.qc_report["narration_repair"]["scope"] == (
        "position_locked_rewrite_vector"
    )
    assert result.qc_report["narration_repair"]["candidate_hash"]
    assert result.qc_report["narration_repair"]["position_registry_version"] == (
        "narration-repair-position-registry-v5"
    )
    assert result.qc_report["narration_repair"]["slot_order_hash"]
    assert result.qc_report["narration_repair"]["passage_lineage_version"] == (
        "narration-repair-passage-lineage-v1"
    )
    assert len(result.qc_report["narration_repair"]["passage_lineage_hash"]) == 64
    assert [call[0] for call in provider.calls] == ["narration_repair"]
    assert runner.request_count == 1

    cache_root = tmp_path / "targeted-repair-cache"
    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in cache_root.glob("*.json")
    ]
    assert any(
        record.get("cache_type") == module.NARRATION_REPAIR_CANDIDATE_VERSION
        for record in records
    )
    assert any(
        record.get("cache_type") == module.NARRATION_REPAIR_RESULT_VERSION
        and record.get("slot_order_hash")
        and record.get("passage_lineage_hash")
        == result.qc_report["narration_repair"]["passage_lineage_hash"]
        for record in records
    )

    for path in cache_root.glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        if "cache_type" not in record:
            path.unlink()

    provider.calls.clear()
    resumed = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(cache_root),
        max_attempts=1,
    ).run_narration_repair_candidate(candidate, visual, story_map, panels=panels)
    assert provider.calls == []
    assert resumed.qc_report["narration_repair"]["cache_reused"] is True
    assert resumed.qc_report["narration_repair"]["passage_lineage_hash"] == (
        result.qc_report["narration_repair"]["passage_lineage_hash"]
    )


def test_run_job_repairs_structurally_usable_dialogue_copy_narration(tmp_path):
    module = _module()
    panels = _panels(module)
    panel_ids = [panel.panel_id for panel in panels]
    copied_dialogue = "the crown must not leave this hall tonight"
    setup_runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=_identity(module),
        max_attempts=1,
    )
    visual = setup_runner.run_visual_evidence(panels)
    mutated_rows = [dict(row) for row in visual.panels]
    first_row = dict(mutated_rows[0])
    first_observation = dict(first_row["observation"])
    first_observation["dialogue_or_ocr"] = [copied_dialogue]
    first_row["observation"] = first_observation
    mutated_rows[0] = first_row
    visual = replace(visual, panels=tuple(mutated_rows))

    class DialogueCopyResumeProvider(_FakeProvider):
        def __post_init__(self):
            super().__post_init__()
            self.repair_payloads = []

        def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
            if stage == "narration_repair":
                self.calls.append((stage, prompt_version, prompt_sha256))
                self.repair_payloads.append(dict(payload))
                return _provider_position_vector(payload)
            return super().complete_json(
                stage=stage,
                prompt_version=prompt_version,
                prompt_sha256=prompt_sha256,
                prompt_text=prompt_text,
                payload=payload,
            )

    provider = DialogueCopyResumeProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(tmp_path / "dialogue-copy-resume-cache"),
        max_attempts=1,
    )
    runner_prompts = runner.prompts
    story_map = module.StoryMapResult(
        panel_ids=tuple(panel_ids),
        beats=(
            {
                "beat_id": "beat-all",
                "panel_ids": panel_ids,
                "summary": "the visible sequence develops",
            },
        ),
        causal_chain=(),
        claims=tuple(
            {
                "claim_id": f"claim-{index}",
                "claim_type": "fact",
                "text": f"The visible sequence develops claim {index}.",
                "panel_ids": panel_ids,
                "evidence_panel_ids": panel_ids,
                "qualification": "The ordered panels support this reading.",
            }
            for index in range(8)
        ),
        story_map_hash="s" * 64,
        model_identity_hash=_identity(module).identity_hash,
        prompt_version=runner_prompts["story_map"][0],
        prompt_sha256=runner_prompts["story_map"][1],
        visual_evidence_hash=visual.visual_evidence_hash,
    )

    output = _narrative_output("dialogue-copy-resume", panel_ids)
    output["evidence_graph"] = {"claims": [dict(claim) for claim in story_map.claims]}
    output["observations"][0]["dialogue_or_ocr"] = [copied_dialogue]
    filler = "the pressure keeps building here while the sequence turns"

    def sized_text(base: str, target: int) -> str:
        words = base.split()
        while len(words) < target:
            words.extend(filler.split())
        return " ".join(words[:target]) + "."

    passage_texts = [
        sized_text(
            "The opening beat keeps the pressure rising without stalling "
            "while the first visible choice narrows the route",
            30,
        ),
        sized_text(
            "The middle beats widen the stakes as the witness weighs the "
            "safer path against the cost of waiting",
            30,
        ),
        sized_text(
            "Each turn narrows the field so the claim stays tied to what "
            "the sequence shows",
            30,
        ),
        sized_text(
            "The closing beat shows that the crown must not leave this hall "
            "tonight so the guarded choice shifts the outcome",
            30,
        ),
    ]
    for passage, text, passage_index in zip(
        output["script_passages"], passage_texts, range(len(passage_texts)), strict=True
    ):
        passage["text"] = text
        passage["claim_ids"] = [
            f"claim-{passage_index * 2}",
            f"claim-{passage_index * 2 + 1}",
        ]
        passage["evidence_panel_ids"] = list(panel_ids)
    spoken = "\n\n".join(passage_texts)
    duration_metrics = module.script.narration_duration_metrics(spoken, "dramatic")
    candidate = module.NarrationResult(
        spoken_text=spoken,
        display_words=module.derive_display_words(spoken),
        passages=tuple(dict(item) for item in output["script_passages"]),
        ending_kind=str(output["narrative_outline"]["ending_kind"]),
        word_count=int(duration_metrics["word_count"]),
        estimated_duration_s=float(duration_metrics["estimated_duration_s"]),
        qc_report={
            "duration_contract": module.script.narration_duration_contract("dramatic"),
        },
        model_identity_hash=_identity(module).identity_hash,
        prompt_version=runner_prompts["narration"][0],
        prompt_sha256=runner_prompts["narration"][1],
        observations=tuple(dict(item) for item in output["observations"]),
        continuity_ledger=dict(output["continuity_ledger"]),
        evidence_graph=dict(output["evidence_graph"]),
        story_spine=dict(output["narrative_outline"]["story_spine"]),
        visual_evidence_hash=visual.visual_evidence_hash,
    )

    store = module.JsonJobStore(tmp_path / "dialogue-copy-resume-jobs")
    record = module.ChapterJobRecord(
        job_id="dialogue-copy-resume",
        stage_results={
            "visual": visual.as_dict(),
            "story_map": story_map.as_dict(),
            "narration": candidate.as_dict(),
        },
    )
    store.save(record)

    analyzer_contract = importlib.import_module("app.services.analyzer_contract")
    assert module.CloudStageRunner._narration_contract_failures(candidate) == (
        "cloud.narrative_source_dialogue_copy",
    )
    assert 115 <= int(duration_metrics["word_count"]) <= 125
    assert 50.0 <= float(duration_metrics["estimated_duration_s"]) <= 60.0

    resumed = module.CloudBatchService(runner=runner, store=store).run_job(
        "dialogue-copy-resume",
        panels,
    )

    assert resumed.state == module.ChapterState.READY_TO_RENDER
    assert [call[0] for call in provider.calls] == ["narration_repair"]
    assert len(provider.repair_payloads) == 1
    persisted = store.load("dialogue-copy-resume")
    assert persisted is not None
    repaired = module.NarrationResult.from_dict(
        persisted.stage_results["narration"]
    )
    assert 115 <= repaired.word_count <= 125
    assert not analyzer_contract.contains_source_dialogue_copy(
        repaired.observations,
        repaired.passages,
    )


def test_targeted_position_repair_validates_full_scope_in_one_request(tmp_path):
    module = _module()
    panel_count = 601
    panels = tuple(
        module.CloudPanelInput(
            panel_id=f"scope-chunk-panel-{index:04d}",
            source_asset_id=f"scope-chunk-asset-{index:04d}",
            source_order=index + 1,
            mime_type="image/png",
            payload=f"scope-chunk-payload-{index}".encode(),
            panel_bounds=(0, 0, 100, 100),
            source_dimensions=(100, 100),
            strip_region_id=f"scope-chunk-region-{index}",
            coverage_map_version="coverage-v1",
            coverage_map_hash="b" * 64,
        )
        for index in range(panel_count)
    )
    panel_ids = [panel.panel_id for panel in panels]
    visual_rows = []
    for panel in panels:
        observation = _visual_row(panel.descriptor())
        visual_rows.append(
            {
                "panel_id": panel.panel_id,
                "source_asset_id": panel.source_asset_id,
                "source_order": panel.source_order,
                "source_checksum": panel.source_checksum,
                "observation": observation,
                "visual_evidence": observation["visual_evidence"],
                "evidence_hash": "",
            }
        )
    visual = module.VisualStageResult(
        panels=tuple(visual_rows),
        source_hash="scope-chunk-source",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )
    claims = tuple(
        {
            "claim_id": f"claim-{index}",
            "claim_type": "fact",
            "text": f"The ordered scope develops claim {index}.",
            "panel_ids": panel_ids,
            "evidence_panel_ids": panel_ids,
            "qualification": "The ordered panels support this reading.",
        }
        for index in range(8)
    )
    story_map = module.StoryMapResult(
        panel_ids=tuple(panel_ids),
        beats=(
            {
                "beat_id": "beat-all",
                "panel_ids": panel_ids,
                "summary": "the visible sequence develops",
            },
        ),
        causal_chain=(),
        claims=claims,
        story_map_hash="s" * 64,
        model_identity_hash=_identity(module).identity_hash,
        prompt_version=module._prompt_specs()["story_map"][0],
        prompt_sha256=module._prompt_specs()["story_map"][1],
        visual_evidence_hash=visual.visual_evidence_hash,
    )
    passages = []
    for passage_index in range(4):
        passages.append(
            {
                "passage_id": f"p{passage_index}",
                "editorial_role": "role",
                "text": (
                    f"Scoped passage {passage_index} keeps the ordered evidence "
                    "tied to the full chapter registry."
                ),
                "claim_ids": [
                    f"claim-{passage_index * 2}",
                    f"claim-{passage_index * 2 + 1}",
                ],
                "evidence_panel_ids": list(panel_ids),
            }
        )
    candidate = module.NarrationResult(
        spoken_text=" ".join(str(item["text"]) for item in passages),
        display_words=("SCOPE",),
        passages=tuple(passages),
        ending_kind="consequence",
        word_count=172,
        estimated_duration_s=69.57,
        qc_report={},
        model_identity_hash=_identity(module).identity_hash,
        prompt_version=module._prompt_specs()["narration"][0],
        prompt_sha256=module._prompt_specs()["narration"][1],
        observations=(),
        continuity_ledger={},
        evidence_graph={"claims": [dict(claim) for claim in claims]},
        story_spine={
            "who_wants_what": "the witness wants the guarded route",
            "obstacle": "the closing path blocks the witness",
            "decision": "the witness chooses the visible opening",
            "consequence": "the guarded choice shifts the outcome",
            "changed_stakes": "the sequence raises the visible cost",
            "unresolved_question": "What changes next?",
        },
        visual_evidence_hash=visual.visual_evidence_hash,
    )

    class ScopeChunkRepairProvider(_FakeProvider):
        def __post_init__(self):
            super().__post_init__()
            self.repair_payloads = []

        def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
            if stage == "narration_repair":
                self.calls.append((stage, prompt_version, prompt_sha256))
                self.repair_payloads.append(dict(payload))
                return _provider_position_vector(payload)
            return super().complete_json(
                stage=stage,
                prompt_version=prompt_version,
                prompt_sha256=prompt_sha256,
                prompt_text=prompt_text,
                payload=payload,
            )

    provider = ScopeChunkRepairProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(tmp_path / "scope-chunk-repair-cache"),
        max_attempts=1,
    )
    local_observations, local_structural = runner._narration_observations(
        visual,
        panels,
    )
    candidate = replace(
        candidate,
        observations=tuple(local_observations),
        continuity_ledger=dict(local_structural["continuity_ledger"]),
    )

    result = runner.run_narration_repair_candidate(
        candidate,
        visual,
        story_map,
        panels=panels,
    )

    assert len(provider.repair_payloads) == 1
    assert provider.repair_payloads[0]["batch_count"] == 1
    assert 115 <= result.word_count <= 125
    assert result.estimated_duration_s >= 50.0


def test_position_repair_admits_trusted_evidence_closure_scope(tmp_path):
    module = _module()
    panels = _panels(module)
    panel_ids = [panel.panel_id for panel in panels]
    visual_rows = []
    for panel in panels:
        observation = _visual_row(panel.descriptor())
        visual_rows.append(
            {
                "panel_id": panel.panel_id,
                "source_asset_id": panel.source_asset_id,
                "source_order": panel.source_order,
                "source_checksum": panel.source_checksum,
                "observation": observation,
                "visual_evidence": observation["visual_evidence"],
                "evidence_hash": "",
            }
        )
    visual = module.VisualStageResult(
        panels=tuple(visual_rows),
        source_hash="closure-scope-source",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )
    claims = tuple(
        {
            "claim_id": f"claim-{index}",
            "claim_type": "fact",
            "text": f"The ordered closure develops claim {index}.",
            "panel_ids": panel_ids,
            "evidence_panel_ids": panel_ids,
            "qualification": "The ordered panels support this reading.",
        }
        for index in range(8)
    )
    story_map = module.StoryMapResult(
        panel_ids=tuple(panel_ids),
        beats=(
            {
                "beat_id": "beat-all",
                "panel_ids": panel_ids,
                "summary": "the visible sequence develops",
            },
        ),
        causal_chain=(),
        claims=claims,
        story_map_hash="s" * 64,
        model_identity_hash=_identity(module).identity_hash,
        prompt_version=module._prompt_specs()["story_map"][0],
        prompt_sha256=module._prompt_specs()["story_map"][1],
        visual_evidence_hash=visual.visual_evidence_hash,
    )
    passages = []
    for passage_index in range(4):
        passages.append(
            {
                "passage_id": f"p{passage_index}",
                "editorial_role": "role",
                "text": (
                    f"Closure passage {passage_index} keeps the ordered "
                    "evidence tied to the trusted claim union."
                ),
                "claim_ids": [
                    f"claim-{passage_index * 2}",
                    f"claim-{passage_index * 2 + 1}",
                ],
                # Narrower than the trusted claim closure: the registry must
                # rebuild this union from the story map during repair.
                "evidence_panel_ids": [panel_ids[0]],
            }
        )
    candidate = module.NarrationResult(
        spoken_text=" ".join(str(item["text"]) for item in passages),
        display_words=("CLOSURE",),
        passages=tuple(passages),
        ending_kind="consequence",
        word_count=172,
        estimated_duration_s=69.57,
        qc_report={},
        model_identity_hash=_identity(module).identity_hash,
        prompt_version=module._prompt_specs()["narration"][0],
        prompt_sha256=module._prompt_specs()["narration"][1],
        observations=(),
        continuity_ledger={},
        evidence_graph={"claims": [dict(claim) for claim in claims]},
        story_spine={
            "who_wants_what": "the witness wants the guarded route",
            "obstacle": "the closing path blocks the witness",
            "decision": "the witness chooses the visible opening",
            "consequence": "the guarded choice shifts the outcome",
            "changed_stakes": "the sequence raises the visible cost",
            "unresolved_question": "What changes next?",
        },
        visual_evidence_hash=visual.visual_evidence_hash,
    )

    class ClosureScopeRepairProvider(_FakeProvider):
        def __post_init__(self):
            super().__post_init__()
            self.repair_payloads = []

        def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
            if stage == "narration_repair":
                self.calls.append((stage, prompt_version, prompt_sha256))
                self.repair_payloads.append(dict(payload))
                return _provider_position_vector(payload)
            return super().complete_json(
                stage=stage,
                prompt_version=prompt_version,
                prompt_sha256=prompt_sha256,
                prompt_text=prompt_text,
                payload=payload,
            )

    provider = ClosureScopeRepairProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(tmp_path / "closure-scope-repair-cache"),
        max_attempts=1,
    )
    local_observations, local_structural = runner._narration_observations(
        visual,
        panels,
    )
    candidate = replace(
        candidate,
        observations=tuple(local_observations),
        continuity_ledger=dict(local_structural["continuity_ledger"]),
    )

    result = runner.run_narration_repair_candidate(
        candidate,
        visual,
        story_map,
        panels=panels,
    )

    assert len(provider.repair_payloads) == 1
    assert 115 <= result.word_count <= 125
    for passage in result.passages:
        assert {str(value) for value in passage["evidence_panel_ids"]} == set(
            panel_ids
        )


def test_narration_contract_failures_trigger_repair_for_source_dialogue_copy():
    module = _module()
    spoken = (
        "The bridge is already falling, and the scout hesitates before moving on."
    )
    result = module.NarrationResult(
        spoken_text=spoken,
        display_words=module.derive_display_words(spoken),
        passages=(
            {
                "passage_id": "p1",
                "editorial_role": "hook",
                "text": spoken,
                "claim_ids": ["claim-1"],
                "evidence_panel_ids": ["panel-1"],
            },
        ),
        ending_kind="consequence",
        word_count=118,
        estimated_duration_s=51.3,
        qc_report={},
        model_identity_hash="m" * 64,
        prompt_version="vision-first-story-analyzer-v3",
        prompt_sha256="p" * 64,
        observations=(
            {
                "panel_id": "panel-1",
                "dialogue_or_ocr": ["The bridge is already falling"],
            },
        ),
        continuity_ledger={},
        evidence_graph={"claims": []},
        story_spine={},
        visual_evidence_hash="v" * 64,
    )

    failures = module.CloudStageRunner._narration_contract_failures(result)

    assert "cloud.narrative_source_dialogue_copy" in failures


def test_repair_slots_reconstruct_trusted_evidence_when_candidate_omits_ref():
    module = _module()
    panel_ids = tuple(f"panel-{index}" for index in range(8))
    claims = []
    passages = []
    beats = []
    for passage_index in range(4):
        refs = [panel_ids[passage_index * 2], panel_ids[passage_index * 2 + 1]]
        passage_claim_ids = []
        for claim_index in range(2):
            claim_id = f"claim-{passage_index}-{claim_index}"
            passage_claim_ids.append(claim_id)
            claims.append(
                {
                    "claim_id": claim_id,
                    "claim_type": "fact",
                    "text": f"Grounded claim {claim_id}.",
                        "evidence_panel_ids": [refs[claim_index]],
                    "qualification": "The ordered evidence supports this claim.",
                }
            )
        passages.append(
            {
                "passage_id": f"p{passage_index}",
                "editorial_role": "role",
                "text": f"Grounded passage {passage_index}.",
                "claim_ids": passage_claim_ids,
                "evidence_panel_ids": (
                    [*refs, "panel-8"] if passage_index == 0 else list(refs)
                ),
            }
        )
        beats.append(
            {
                "beat_id": f"b{passage_index}__sub0__beat",
                "panel_ids": [*refs, "panel-8"] if passage_index == 0 else list(refs),
                "summary": "The ordered beat remains grounded.",
            }
        )
    candidate = module.NarrationResult(
        spoken_text="Grounded passage text.",
        display_words=("GROUNDED", "PASSAGE", "TEXT"),
        passages=tuple(passages),
        ending_kind="consequence",
        word_count=118,
        estimated_duration_s=51.3,
        qc_report={},
        model_identity_hash="m" * 64,
        prompt_version="vision-first-story-analyzer-v3",
        prompt_sha256="p" * 64,
        observations=tuple({"panel_id": panel_id} for panel_id in panel_ids),
        continuity_ledger={},
        evidence_graph={"claims": [dict(claim) for claim in claims]},
        story_spine={},
        visual_evidence_hash="v" * 64,
    )
    story_map = module.StoryMapResult(
        panel_ids=(*panel_ids, "panel-8"),
        beats=tuple(beats),
        causal_chain=(),
        claims=tuple(claims),
        story_map_hash="s" * 64,
        model_identity_hash="m" * 64,
        prompt_version="story-map-v1",
        prompt_sha256="c" * 64,
        visual_evidence_hash="v" * 64,
    )

    slots = module.CloudStageRunner._build_narration_repair_slots(
        candidate,
        story_map,
    )

    assert len(slots) == 4
    assert slots[0].evidence_panel_ids == ("panel-0", "panel-1")

    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=_identity(module),
    )
    registry = runner._build_narration_repair_position_registry(candidate, story_map)
    p2_rows = [
        row
        for row in registry["positions"]
        if row["passage_id"] == "p2"
    ]
    assert len(p2_rows) == 2
    assert {tuple(row["evidence_panel_ids"]) for row in p2_rows} == {
        ("panel-4",),
        ("panel-5",),
    }


def test_repair_evidence_closure_admits_exact_p2_story_ancestry():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    candidate = replace(candidate, passages=tuple(candidate.passages[:4]))
    registry = runner._build_narration_repair_position_registry(candidate, story_map)

    closure = runner._validate_narration_repair_evidence_closure(
        registry,
        candidate,
        story_map,
    )
    p2_rows = [
        row
        for row in closure["positions"]
        if isinstance(row, dict) and row.get("passage_id") == "immutable-passage-2"
    ]

    assert p2_rows
    assert closure["closure_hash"] == registry["evidence_closure_hash"]
    for row in p2_rows:
        assert row["beat_id"]
        assert row["section_keys"]
        assert set(row["evidence_panel_ids"]).issubset(
            set(row["permitted_panel_ids"])
        )


def test_repair_evidence_closure_uses_all_claim_ancestry_for_one_passage():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    first = dict(candidate.passages[0])
    first["claim_ids"] = [
        str(candidate.passages[0]["claim_ids"][0]),
        str(candidate.passages[1]["claim_ids"][0]),
    ]
    first["evidence_panel_ids"] = [
        str(candidate.passages[0]["evidence_panel_ids"][0]),
        str(candidate.passages[1]["evidence_panel_ids"][0]),
    ]
    second = dict(candidate.passages[1])
    second["claim_ids"] = [str(candidate.passages[1]["claim_ids"][1])]
    second["evidence_panel_ids"] = [
        str(candidate.passages[1]["evidence_panel_ids"][0])
    ]
    mixed = replace(candidate, passages=(first, second, *candidate.passages[2:]))
    registry = runner._build_narration_repair_position_registry(mixed, story_map)

    closure = runner._validate_narration_repair_evidence_closure(
        registry,
        mixed,
        story_map,
    )
    rows = [
        row
        for row in closure["positions"]
        if row["passage_id"] == first["passage_id"]
    ]

    assert len(rows) == 2
    assert {tuple(row["evidence_panel_ids"]) for row in rows} == {
        (first["evidence_panel_ids"][0],),
        (first["evidence_panel_ids"][1],),
    }
    assert all(
        set(first["evidence_panel_ids"]).issubset(set(row["permitted_panel_ids"]))
        for row in rows
    )


def test_repair_evidence_closure_rejects_unrelated_same_chapter_panel():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    first = dict(candidate.passages[0])
    first["evidence_panel_ids"] = [
        str(first["evidence_panel_ids"][0]),
        str(candidate.passages[1]["evidence_panel_ids"][0]),
    ]
    mixed = replace(candidate, passages=(first, *candidate.passages[1:]))

    with pytest.raises(module.CloudStageError) as caught:
        runner._build_narration_repair_position_registry(mixed, story_map)

    assert caught.value.code == "cloud.narrative_repair_evidence_closure_invalid"


def test_repair_evidence_closure_rejects_missing_story_panel_ancestry():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    first_panel = str(candidate.passages[0]["evidence_panel_ids"][0])
    beats = tuple(
        {
            **dict(beat),
            "panel_ids": [
                panel_id
                for panel_id in beat.get("panel_ids", ())
                if str(panel_id) != first_panel
            ],
        }
        for beat in story_map.beats
    )
    missing = replace(story_map, beats=beats)

    with pytest.raises(module.CloudStageError) as caught:
        runner._build_narration_repair_position_registry(candidate, missing)

    assert caught.value.code == "cloud.narrative_repair_evidence_closure_invalid"


def test_repair_evidence_closure_rejects_stale_story_identity_at_vector_boundary():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    registry = runner._build_narration_repair_position_registry(candidate, story_map)
    raw = {
        "rewrites": [
            _position_rewrite_text(row["word_budget"], f"closure{index}_")
            for index, row in enumerate(registry["positions"])
        ]
    }
    stale_story = replace(story_map, story_map_hash="z" * 64)

    with pytest.raises(module.CloudStageError) as caught:
        runner._reconcile_narration_repair_vector(
            raw,
            registry,
            candidate,
            story_map=stale_story,
        )

    assert caught.value.code == "cloud.narrative_repair_evidence_closure_invalid"


def test_out_of_range_candidate_stays_out_of_final_narration_cache():
    module = _module()
    identity = _identity(module)
    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=identity,
        cache=module.MemoryStageCache(),
    )
    prompt = runner.prompts["narration"]
    source = {
        "visual_evidence_hash": "v" * 64,
        "story_map_hash": "s" * 64,
        "selection_hash": "e" * 64,
    }
    candidate = module.NarrationResult(
        spoken_text="A candidate that needs repair.",
        display_words=("A", "CANDIDATE"),
        passages=(),
        ending_kind="consequence",
        word_count=172,
        estimated_duration_s=69.57,
        observations=(),
        continuity_ledger={},
        evidence_graph={},
        story_spine={},
        qc_report={},
        model_identity_hash=identity.identity_hash,
        prompt_version=prompt[0],
        prompt_sha256=prompt[1],
        visual_evidence_hash=source["visual_evidence_hash"],
    )
    runner._store_narration_repair_candidate(
        source=source,
        prompt=prompt,
        result=candidate,
        failure_codes=(
            "cloud.narrative_duration_out_of_range",
            "cloud.narrative_word_count_out_of_range",
        ),
    )
    candidate_record = runner.cache.get(
        runner._narration_repair_candidate_key(source, prompt)
    )
    assert candidate_record["cache_type"] == module.NARRATION_REPAIR_CANDIDATE_VERSION
    assert runner.cache.get(
        module._cache_key("narration", source, identity, prompt)
    ) is None


def test_narration_targeted_repair_rejects_insufficient_position_registry(monkeypatch, tmp_path):
    module = _module()
    panels = _panels(module)
    panel_ids = [panel.panel_id for panel in panels]
    visual_rows = []
    for panel in panels:
        observation = _visual_row(panel.descriptor())
        visual_rows.append(
            {
                "panel_id": panel.panel_id,
                "source_asset_id": panel.source_asset_id,
                "source_order": panel.source_order,
                "source_checksum": panel.source_checksum,
                "observation": observation,
                "visual_evidence": observation["visual_evidence"],
                "evidence_hash": "",
            }
        )
    visual = module.VisualStageResult(
        panels=tuple(visual_rows),
        source_hash="targeted-scope-source",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )
    story_map = module.StoryMapResult(
        panel_ids=tuple(panel_ids),
        beats=(
            {
                "beat_id": "beat-all",
                "panel_ids": panel_ids,
                "summary": "the visible sequence develops",
            },
        ),
        causal_chain=(
            {
                "from_beat": "beat-all",
                "to_beat": "beat-all",
                "reason": "the visible sequence continues",
            },
        ),
        claims=(
            {
                "claim_id": "claim-all",
                "claim_type": "fact",
                "text": "The visible sequence develops.",
                "panel_ids": panel_ids,
                "qualification": "The ordered panels support this reading.",
            },
        ),
        story_map_hash="s" * 64,
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="cloud-causal-map-v2",
        prompt_sha256="c" * 64,
        visual_evidence_hash=visual.visual_evidence_hash,
    )
    output = _narrative_output("repair-scope", panel_ids)
    trusted_claim = dict(story_map.claims[0])
    for passage in output["script_passages"]:
        passage["claim_ids"] = [trusted_claim["claim_id"]]
        passage["evidence_panel_ids"] = list(panel_ids)
    output["evidence_graph"] = {"claims": [trusted_claim]}
    spoken = "\n\n".join(str(item["text"]).strip() for item in output["script_passages"])
    candidate = module.NarrationResult(
        spoken_text=spoken,
        display_words=module.derive_display_words(spoken),
        passages=tuple(dict(item) for item in output["script_passages"]),
        ending_kind=str(output["narrative_outline"]["ending_kind"]),
        word_count=172,
        estimated_duration_s=69.57,
        qc_report={"signals": {}, "warnings": []},
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="vision-first-story-analyzer-v3",
        prompt_sha256="n" * 64,
        observations=tuple(dict(item) for item in output["observations"]),
        continuity_ledger=dict(output["continuity_ledger"]),
        evidence_graph=dict(output["evidence_graph"]),
        story_spine=dict(output["narrative_outline"]["story_spine"]),
        visual_evidence_hash=visual.visual_evidence_hash,
    )

    class ScopeChangingProvider(_FakeProvider):
        def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
            if stage == "narration_repair":
                slot_ids = payload["targeted_repair"]["slot_registry"]["slot_ids"]
                return {
                    "repair_slots": {
                        "retained_slot_ids": [*slot_ids[:-1], "foreign-slot"],
                        "dropped_slot_ids": [],
                        "slots": [],
                    }
                }
            return super().complete_json(
                stage=stage,
                prompt_version=prompt_version,
                prompt_sha256=prompt_sha256,
                prompt_text=prompt_text,
                payload=payload,
            )

    provider = ScopeChangingProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(tmp_path / "targeted-scope-cache"),
        max_attempts=1,
    )
    original_batched = runner._run_narration_batched

    def first_candidate(*args, **kwargs):
        if kwargs.get("stage") == "narration_repair":
            return original_batched(*args, **kwargs)
        return candidate

    monkeypatch.setattr(runner, "_run_narration_batched", first_candidate)

    with pytest.raises(module.CloudStageError) as caught:
        runner.run_narration(visual, story_map, panels=panels)

    assert caught.value.code == "cloud.narrative_repair_position_selection_invalid"


def test_narration_targeted_repair_canonicalizes_non_lineage_provider_drift():
    module = _module()
    identity = _identity(module)
    passages = tuple(
        {
            "passage_id": f"passage-{index}",
            "editorial_role": f"role-{index}",
            "text": f"Grounded passage {index}.",
            "claim_ids": ["claim-1"],
            "evidence_panel_ids": ["panel-1"],
        }
        for index in range(4)
    )
    claim = {
        "claim_id": "claim-1",
        "claim_type": "fact",
        "text": "The candidate claim is grounded.",
        "qualification": "The ordered evidence supports this claim.",
        "evidence_panel_ids": ["panel-1"],
    }
    candidate = module.NarrationResult(
        spoken_text="Old candidate prose.",
        display_words=("OLD", "CANDIDATE", "PROSE"),
        passages=passages,
        ending_kind="consequence",
        word_count=160,
        estimated_duration_s=64.35,
        qc_report={},
        model_identity_hash=identity.identity_hash,
        prompt_version="vision-first-story-analyzer-v3",
        prompt_sha256="p" * 64,
        observations=({"panel_id": "panel-1"},),
        continuity_ledger={"ordered": True},
        evidence_graph={"claims": [claim]},
        story_spine={"decision": "the decision changes the stakes"},
        visual_evidence_hash="v" * 64,
    )
    repaired = replace(
        candidate,
        spoken_text="New repaired prose.",
        display_words=("NEW", "REPAIRED", "PROSE"),
        passages=tuple(
            {
                **passage,
                "editorial_role": "provider-rephrased-role",
                "text": f"New repaired passage {index}.",
            }
            for index, passage in enumerate(passages)
        ),
        word_count=120,
        estimated_duration_s=54.0,
        evidence_graph={
            "claims": [
                {
                    **claim,
                    "text": "Provider changed claim prose but kept its evidence.",
                    "qualification": "Provider qualification drift is not trusted.",
                }
            ]
        },
    )

    reconciled = module.CloudStageRunner._narration_repair_scope_reconciled(
        candidate,
        repaired,
        (),
    )

    assert reconciled is not None
    assert reconciled.passages[0]["text"] == "New repaired passage 0."
    assert reconciled.passages[0]["editorial_role"] == "role-0"
    assert reconciled.evidence_graph == candidate.evidence_graph
    assert reconciled.observations == candidate.observations
    assert reconciled.story_spine == candidate.story_spine
def test_later_gate_failure_persists_success_shape_metrics(tmp_path):
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    registry = runner._build_narration_repair_position_registry(candidate, story_map)
    counts = [row["word_budget"] for row in registry["positions"]]
    raw = {
        "rewrites": [
            _position_rewrite_text(count, f"durable{index}_")
            for index, count in enumerate(counts)
        ]
    }
    reconciled = runner._reconcile_narration_repair_vector(raw, registry, candidate)
    runner.last_response_shape_metrics = dict(
        reconciled.pop("_response_shape_metrics")
    )

    store = module.JsonJobStore(tmp_path)
    service = module.CloudBatchService(runner=runner, store=store)
    record = module.ChapterJobRecord(job_id="later-gate-shape")
    failure = module.CloudStageError(
        "cloud.narrative_word_count_out_of_range",
        reviewable=True,
    )
    failure_metrics = runner._response_shape_metrics_for_failure(failure.code)
    assert failure_metrics["failed_code"] == failure.code
    assert runner.last_response_shape_metrics["failed_code"] == failure.code
    service._record_failure(record, failure)

    persisted = store.load("later-gate-shape")
    assert persisted is not None
    metrics = persisted.review_queue[-1]["safe_metadata"]
    assert metrics["slot_order_hash"] == registry["slot_order_hash"]
    assert metrics["array_count"] == len(counts)
    assert metrics["per_position_word_counts"] == counts
    assert metrics["failed_code"] == failure.code
    assert metrics["failed_predicate"] == failure.code
    assert "durable0_word" not in json.dumps(metrics)


def test_later_gate_metrics_include_reconciled_result_shape():
    module = _module()
    runner, candidate, visual, _story_map = _immutable_slot_fixture(module)

    metrics = runner._narration_repair_result_shape_metrics(
        candidate,
        visual,
        scope_ok=True,
    )

    assert metrics["reconciled_word_count"] == candidate.word_count
    assert metrics["reconciled_duration_s"] == candidate.estimated_duration_s
    assert metrics["reconciled_passage_count"] == len(candidate.passages)
    assert metrics["reconciled_observation_count"] == len(candidate.observations)
    assert metrics["reconciled_visual_panel_count"] == len(visual.panels)
    assert metrics["reconciled_scope_ok"] is True
    assert "duration_bounds" in metrics["reconciled_failed_predicates"]
    assert "word_bounds" in metrics["reconciled_failed_predicates"]
    assert "reconciled_spoken_text" not in json.dumps(metrics)

    runner.last_response_shape_metrics = dict(metrics)
    failure_metrics = runner._response_shape_metrics_for_failure(
        "cloud.narrative_not_grounded"
    )
    assert failure_metrics["failed_code"] == "cloud.narrative_not_grounded"
    assert failure_metrics["failed_predicate"] == "duration_bounds"


def test_observed_vector_uses_one_canonical_duration_across_repair_result_path():
    module = _module()
    script_module = importlib.import_module("app.services.script")
    template, candidate, visual, story_map = _immutable_slot_fixture(module)
    base_panels = _panels(module, "immutable-slot")
    panels = base_panels + tuple(
        replace(
            base_panels[-1],
            panel_id=f"immutable-slot-panel-{index}",
            source_asset_id=f"immutable-slot-asset-{index}",
            source_order=index,
            payload=f"immutable-slot-payload-{index}".encode(),
            payload_checksum="",
            source_checksum="",
            strip_region_id=f"immutable-slot-region-{index}",
        )
        for index in (4, 5)
    )
    local_observations, local_structural = template._narration_observations(
        visual,
        panels,
    )
    candidate = replace(
        candidate,
        observations=tuple(local_observations),
        continuity_ledger=dict(local_structural["continuity_ledger"]),
        story_spine={
            **{
                key: value
                for key, value in candidate.story_spine.items()
                if key not in {"wants", "unresolved_direction"}
            },
            "who_wants_what": candidate.story_spine["wants"],
            "unresolved_question": "What changes next?",
        },
    )
    observed_counts = (18, 17, 16, 16, 16, 13, 13, 13)

    class ObservedVectorProvider(_FakeProvider):
        def __post_init__(self):
            super().__post_init__()
            self.repair_count = 0

        def complete_json(
            self,
            *,
            stage,
            prompt_version,
            prompt_sha256,
            prompt_text="",
            payload,
        ):
            if stage == "narration_repair":
                self.repair_count += 1
                rows = payload["targeted_repair"]["position_context"]
                assert len(rows) == len(observed_counts)
                vocabulary = [
                    "Now",
                    "the",
                    "visible",
                    "turn",
                    "changes",
                    "what",
                    "comes",
                    "next",
                    "because",
                    "the",
                    "stakes",
                    "shift",
                    "while",
                    "the",
                    "next",
                    "choice",
                    "keeps",
                    "pressure",
                    "moving",
                    "forward",
                ]
                return {
                    "rewrites": [
                        " ".join(
                            (vocabulary * ((count // len(vocabulary)) + 1))[:count]
                        )
                        for count in observed_counts
                    ]
                }
            return super().complete_json(
                stage=stage,
                prompt_version=prompt_version,
                prompt_sha256=prompt_sha256,
                prompt_text=prompt_text,
                payload=payload,
            )

    provider = ObservedVectorProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.MemoryStageCache(),
        max_attempts=1,
    )

    repaired = runner.run_narration_repair_candidate(
        candidate,
        visual,
        story_map,
        panels=panels,
    )

    canonical = script_module.narration_duration_metrics(
        repaired.spoken_text,
        "dramatic",
    )
    assert provider.repair_count == 1
    assert runner.request_count == 1
    assert repaired.word_count == sum(observed_counts) == canonical["word_count"]
    assert repaired.estimated_duration_s == canonical["estimated_duration_s"]
    assert 50.0 <= canonical["estimated_duration_s"] <= 60.0
    assert repaired.qc_report["duration_contract"] == canonical


def test_position_repair_reconstructs_five_passage_evidence_from_trusted_registry():
    """The provider supplies prose only; local slots own passage evidence."""

    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    registry = runner._build_narration_repair_position_registry(candidate, story_map)
    incomplete_passages = tuple(
        {**dict(passage), "evidence_panel_ids": []}
        for passage in candidate.passages
    )
    incomplete_candidate = replace(candidate, passages=incomplete_passages)
    raw = {
        "rewrites": [
            _position_rewrite_text(row["word_budget"], f"local{index}_")
            for index, row in enumerate(registry["positions"])
        ]
    }

    reconciled = runner._reconcile_narration_repair_vector(
        raw,
        registry,
        incomplete_candidate,
    )

    passages = reconciled["script_passages"]
    assert len(passages) == 5
    claims = {
        str(claim["claim_id"]): claim
        for claim in candidate.evidence_graph["claims"]
    }
    for passage in passages:
        evidence = set(passage["evidence_panel_ids"])
        assert evidence
        required = {
            panel_id
            for claim_id in passage["claim_ids"]
            for panel_id in claims[claim_id]["evidence_panel_ids"]
        }
        assert required <= evidence
    lineage = reconciled["_passage_lineage"]
    assert lineage["version"] == "narration-repair-passage-lineage-v1"
    assert len(lineage["passages"]) == 5
    assert len(lineage["lineage_hash"]) == 64


def test_position_repair_rejects_missing_trusted_slot_evidence():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    registry = runner._build_narration_repair_position_registry(candidate, story_map)
    broken = json.loads(json.dumps(registry))
    broken["positions"][0]["evidence_panel_ids"] = []
    raw = {
        "rewrites": [
            _position_rewrite_text(row["word_budget"], f"missing{index}_")
            for index, row in enumerate(broken["positions"])
        ]
    }

    with pytest.raises(module.CloudStageError) as caught:
        runner._reconcile_narration_repair_vector(raw, broken, candidate)

    assert caught.value.code == "cloud.narrative_repair_position_lineage_invalid"


def test_position_repair_rejects_claim_evidence_mismatch_before_analyzer():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    first = dict(candidate.passages[0])
    first_panel, foreign_panel = first["evidence_panel_ids"][0], candidate.passages[1]["evidence_panel_ids"][0]
    first["evidence_panel_ids"] = [first_panel, foreign_panel]
    expanded = replace(candidate, passages=(first, *candidate.passages[1:]))
    with pytest.raises(module.CloudStageError) as caught:
        runner._build_narration_repair_position_registry(expanded, story_map)

    assert caught.value.code == "cloud.narrative_repair_evidence_closure_invalid"


def test_position_repair_lineage_merge_is_ordered_and_cache_identity_changes_with_refs():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    registry = runner._build_narration_repair_position_registry(candidate, story_map)
    first = runner._reconstruct_narration_repair_passage_lineage(candidate, registry)
    second = runner._reconstruct_narration_repair_passage_lineage(candidate, registry)

    assert first == second
    assert any(len(row["position_ids"]) > 1 for row in first["passages"])
    assert [row["passage_id"] for row in first["passages"]] == [
        str(passage["passage_id"]) for passage in candidate.passages
    ]

    changed = json.loads(json.dumps(registry))
    first_panel = changed["positions"][0]["evidence_panel_ids"][0]
    second_panel = str(candidate.observations[1]["panel_id"])
    changed["positions"][0]["evidence_panel_ids"] = [first_panel, second_panel]
    changed.pop("passage_lineage_hash", None)
    changed_lineage = runner._reconstruct_narration_repair_passage_lineage(
        candidate,
        changed,
    )
    assert changed_lineage["lineage_hash"] != first["lineage_hash"]


def test_stream_visual_batches_are_disjoint_and_restore_prepared_order():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-batch"))
    )

    batches = module._stream_visual_batches(
        panels,
        max_panels=2,
        max_estimated_bytes=10_000_000,
    )

    assert [[panel.panel_id for panel in batch] for batch in batches] == [
        ["stream-batch-panel-0", "stream-batch-panel-1"],
        ["stream-batch-panel-2"],
    ]
    assert len({panel.panel_id for batch in batches for panel in batch}) == len(panels)
    assert not set(batches[0]).intersection(batches[1])


def test_stream_writer_merges_out_of_order_events_by_stable_panel_order():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-merge"))
    )
    rows = []
    for index, panel in enumerate(panels):
        row = _visual_row(
            {
                "panel_id": panel.panel_id,
                "source_asset_id": panel.source_asset_id,
                "source_order": panel.source_order,
            }
        )
        row.update(
            {
                "source_checksum": panel.source_checksum,
                "source_asset_id": panel.source_asset_id,
                "source_order": panel.source_order,
                "cache_identity_hash": module._visual_panel_identity_hash(panel, index),
                "cache_identity_version": module.VISUAL_CACHE_IDENTITY_VERSION,
            }
        )
        rows.append(row)
    merged = module._merge_stream_visual_rows(
        (
            {"rows": [rows[2]], "seeded_ids": (), "missing_ids": ()},
            {"rows": [rows[0], rows[1]], "seeded_ids": (), "missing_ids": ()},
        ),
        panels,
    )

    assert tuple(row["panel_id"] for row in merged) == tuple(
        panel.panel_id for panel in panels
    )


def test_stream_session_uses_bounded_backpressure_and_one_writer():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-session"))
    )
    identity = _identity(module)
    provider = _FakeProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=identity,
        cache=module.MemoryStageCache(),
        max_attempts=1,
    )
    stream = runner.start_visual_evidence_stream(
        queue_size=1,
        max_panels=1,
        max_estimated_bytes=10_000_000,
    )
    for panel in panels:
        stream.submit(panel)
    result = stream.finish(panels)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels)
    assert stream.writer_thread_count == 1
    assert runner.last_visual_stream_metrics["writer_count"] == 1
    assert runner.last_visual_stream_metrics["max_queue_depth"] <= 1
    assert runner.last_visual_stream_metrics["worker_levels"] == [4, 8, 16, 32]
    assert runner.last_visual_stream_metrics["request_count"] == len(provider.calls)


def test_stream_checkpoint_reuses_panel_identity_when_batch_position_changes(tmp_path):
    module = _module()
    base = _panels(module, "stream-reuse")
    first_panel = replace(base[1], prepared_order=1)
    earlier_panel = replace(base[0], prepared_order=0)
    checkpoint = tmp_path / "visual_checkpoints.jsonl"
    provider = _FakeProvider()

    first_runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.MemoryStageCache(),
        max_attempts=1,
        visual_checkpoint_path=checkpoint,
    )
    first_stream = first_runner.start_visual_evidence_stream(
        queue_size=1,
        max_panels=1,
        max_estimated_bytes=10_000_000,
    )
    first_stream.submit(first_panel)
    first_stream.finish((first_panel,))
    calls_after_first = len(provider.calls)

    resumed_runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.MemoryStageCache(),
        max_attempts=1,
        visual_checkpoint_path=checkpoint,
    )
    resumed_stream = resumed_runner.start_visual_evidence_stream(
        queue_size=1,
        max_panels=1,
        max_estimated_bytes=10_000_000,
    )
    resumed_stream.submit(earlier_panel)
    resumed_stream.submit(first_panel)
    result = resumed_stream.finish((earlier_panel, first_panel))

    assert result.panel_ids == (earlier_panel.panel_id, first_panel.panel_id)
    assert len(provider.calls) - calls_after_first == 1


def test_stream_finish_rejects_partial_final_batch_and_persists_metrics():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-partial"))
    )

    class _DropOneProvider(_FakeProvider):
        def observe(self, request):
            rows = super().observe(request)
            return [
                row
                for row in rows
                if row.get("panel_id") != "stream-partial-panel-2"
            ]

    provider = _DropOneProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.MemoryStageCache(),
        max_attempts=1,
    )
    stream = runner.start_visual_evidence_stream(
        queue_size=1,
        max_panels=len(panels),
        max_estimated_bytes=10_000_000,
    )
    for panel in panels:
        stream.submit(panel)

    with pytest.raises(module.CloudStageError) as caught:
        stream.finish(panels)

    assert caught.value.code == "cloud.panel_coverage_incomplete"
    assert runner.last_visual_stream_metrics["submitted_panel_count"] == len(panels)
    assert runner.last_visual_stream_metrics["accepted_panel_count"] == len(panels) - 1
    assert runner.last_visual_stream_metrics["missing_panel_count"] == 1
    assert runner.last_visual_stream_metrics["missing_panel_ids"] == [
        "stream-partial-panel-2"
    ]


def test_stream_abort_drains_workers_and_rejects_late_finish():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-cancel"))
    )
    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=_identity(module),
        cache=module.MemoryStageCache(),
        max_attempts=1,
    )
    stream = runner.start_visual_evidence_stream(
        queue_size=1,
        max_panels=1,
        max_estimated_bytes=10_000_000,
    )
    for panel in panels:
        stream.submit(panel)
    stream.abort()

    assert stream._writer_thread.is_alive() is False
    assert all(worker.is_alive() is False for worker in stream._workers)
    with pytest.raises(module.CloudStageError) as caught:
        stream.finish(panels)
    assert caught.value.code == "cloud.visual_stream_closed"


def test_stream_retry_tracks_only_missing_panel_ids():
    module = _module()

    assert module._stream_retry_pending_ids(
        ("panel-a", "panel-b", "panel-c"),
        {"panel-a"},
    ) == ("panel-b", "panel-c")


def test_adaptive_stream_concurrency_rolls_back_at_first_instability_knee():
    module = _module()
    controller = module._AdaptiveVisualConcurrency((4, 8, 16, 32), wave_panel_target=2)

    controller.acquire()
    controller.release(panel_count=1, request_count=1, latency_s=1.0, categories={})
    controller.acquire()
    controller.release(panel_count=1, request_count=1, latency_s=1.0, categories={})
    assert controller.snapshot()["selected_worker_level"] == 8

    controller.acquire()
    controller.release(
        panel_count=1,
        request_count=1,
        latency_s=1.0,
        categories={"rate_limited": 1},
    )
    controller.acquire()
    controller.release(
        panel_count=1,
        request_count=1,
        latency_s=1.0,
        categories={"rate_limited": 1},
    )

    snapshot = controller.snapshot()
    assert snapshot["selected_worker_level"] == 4
    assert snapshot["waves"][-1]["stable"] is False


def test_stream_merge_rejects_invalid_or_duplicate_rows_fail_closed():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-invalid"))
    )
    row = _visual_row(
        {
            "panel_id": panels[0].panel_id,
            "source_asset_id": panels[0].source_asset_id,
            "source_order": panels[0].source_order,
        }
    )
    row.update(
        {
            "source_checksum": panels[0].source_checksum,
            "source_asset_id": panels[0].source_asset_id,
            "source_order": panels[0].source_order,
            "cache_identity_hash": module._visual_panel_identity_hash(panels[0], 0),
            "cache_identity_version": module.VISUAL_CACHE_IDENTITY_VERSION,
        }
    )
    with pytest.raises(module.CloudStageError) as caught:
        module._merge_stream_visual_rows(
            (
                {"rows": [row, dict(row)], "seeded_ids": (), "missing_ids": ()},
            ),
            panels,
        )
    assert caught.value.code == "cloud.visual_stream_row_invalid"


def test_stream_merge_rejects_writer_gap_fail_closed():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-gap"))
    )
    row = _visual_row(
        {
            "panel_id": panels[0].panel_id,
            "source_asset_id": panels[0].source_asset_id,
            "source_order": panels[0].source_order,
        }
    )
    row.update(
        {
            "source_checksum": panels[0].source_checksum,
            "source_asset_id": panels[0].source_asset_id,
            "source_order": panels[0].source_order,
            "cache_identity_hash": module._visual_panel_identity_hash(panels[0], 0),
            "cache_identity_version": module.VISUAL_CACHE_IDENTITY_VERSION,
        }
    )

    with pytest.raises(module.CloudStageError) as caught:
        module._merge_stream_visual_rows(
            ({"rows": [row], "seeded_ids": (), "missing_ids": ()},),
            panels,
        )

    assert caught.value.code == "cloud.panel_coverage_incomplete"


def test_run_project_streams_preparation_and_passes_one_precomputed_visual_result(monkeypatch):
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-entrypoint"))
    )
    provider = _FakeProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.MemoryStageCache(),
        max_attempts=1,
    )

    class Store:
        def __init__(self):
            self.saved = []

        def load(self, _project_id):
            return None

        def save(self, record):
            self.saved.append(record)

    service = module.CloudBatchService.__new__(module.CloudBatchService)
    service.runner = runner
    service.store = Store()
    service.review_root = None
    prepared_kwargs = {}
    captured = {}

    def fake_prepare(_db, _project_id, **kwargs):
        prepared_kwargs.update(kwargs)
        sink = kwargs["panel_sink"]
        for panel in panels:
            sink(panel)
        return panels, {"status": "RECONCILED"}

    monkeypatch.setattr(module, "prepare_project_panels", fake_prepare)
    monkeypatch.setattr(
        module,
        "_build_project_prepared_manifest",
        lambda *_args, **_kwargs: {"manifest": "streamed"},
    )

    def fake_run_job(_job_id, passed_panels, *, precomputed_visual=None):
        captured["panels"] = tuple(passed_panels)
        captured["visual"] = precomputed_visual
        return module.ChapterJobRecord(
            job_id="stream-entrypoint",
            state=module.ChapterState.NEEDS_REVIEW,
            error_code="test.stop_after_visual",
            stage_results={
                "visual": precomputed_visual.as_dict()
                if precomputed_visual is not None
                else {},
            },
        )

    monkeypatch.setattr(service, "run_job", fake_run_job)
    result = service.run_project(object(), "stream-entrypoint")

    assert callable(prepared_kwargs["panel_sink"])
    assert captured["visual"] is not None
    assert captured["visual"].panel_ids == tuple(panel.panel_id for panel in panels)
    assert captured["panels"] == panels
    assert result.error_code == "test.stop_after_visual"
    assert runner.last_visual_stream_metrics["accepted_panel_count"] == len(panels)
    assert runner.last_visual_stream_metrics["request_count"] == len(provider.calls)


def _admission_png(color: tuple[int, int, int], *, size: tuple[int, int] = (32, 32)) -> bytes:
    import io

    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


def _admission_panel(module, panel_id: str, *, order: int, bounds=(0, 0, 32, 32), payload=None):
    return module.CloudPanelInput(
        panel_id=panel_id,
        source_asset_id="admission-asset",
        source_order=order,
        mime_type="image/png",
        payload=payload or _admission_png((order + 1, 80, 120)),
        source_checksum="a" * 64,
        panel_bounds=bounds,
        source_dimensions=(32, 128),
        strip_region_id=panel_id,
        coverage_map_hash="c" * 64,
    )


def test_panel_admission_funnel_records_counts_transitions_and_non_panel_reason_codes():
    module = _module()
    panels = (_admission_panel(module, "story-1", order=0),)
    regions = (
        {
            "region_id": "gutter-1",
            "source_asset_id": "admission-asset",
            "source_order": 0,
            "bounds": [0, 32, 32, 48],
            "region_class": "verified_gutter",
            "confidence": 0.99,
            "evidence": "local-flat-separator",
        },
        {
            "region_id": "story-1",
            "source_asset_id": "admission-asset",
            "source_order": 1,
            "bounds": [0, 0, 32, 32],
            "region_class": "canonical_panel",
            "confidence": 0.99,
            "evidence": "local-panel",
        },
    )

    result = module.admit_panel_inputs(
        panels,
        raw_image_count=1,
        ingest_asset_count=1,
        candidate_regions=regions,
        detector_version="panel-admission-test-v1",
    )

    ledger = result.ledger
    assert [step["from"] for step in ledger["transitions"]] == [
        "raw_input_images",
        "ingest_outputs",
        "candidate_regions",
        "canonical_regions",
    ]
    assert [step["to"] for step in ledger["transitions"]] == [
        "ingest_outputs",
        "candidate_regions",
        "canonical_regions",
        "admitted_vision_panels",
    ]
    assert ledger["counts"] == {
        "raw_input_images": 1,
        "ingest_assets": 1,
        "candidate_regions": 2,
        "canonical_regions": 1,
        "admitted_vision_panels": 1,
        "rejected_non_panel": 1,
        "deduped": 0,
        "merged": 0,
        "needs_review": 0,
    }
    assert ledger["decisions"][0]["reason_code"] == "admission.non_panel_transition"
    assert result.admitted == panels


def test_panel_admission_failure_preserves_funnel_before_vision(monkeypatch):
    module = _module()
    panels = (_admission_panel(module, "story-1", order=0),)
    regions = (
        {
            "region_id": "story-1",
            "source_asset_id": "admission-asset",
            "source_order": 0,
            "bounds": [0, 0, 32, 32],
            "region_class": "canonical_panel",
            "confidence": 0.99,
            "evidence": "local-panel",
        },
        {
            "region_id": "ambiguous-1",
            "source_asset_id": "admission-asset",
            "source_order": 1,
            "bounds": [0, 32, 32, 64],
            "region_class": "unresolved_material",
            "confidence": 0.0,
            "evidence": "artwork-connected-boundary",
        },
    )

    ledger = module.panel_admission_failure_ledger(
        panels,
        raw_image_count=2,
        ingest_asset_count=1,
        candidate_regions=regions,
        reason_code="segmentation.ambiguous_boundary",
    )

    assert ledger["status"] == "BLOCKED"
    assert ledger["terminal_reason_code"] == "segmentation.ambiguous_boundary"
    assert ledger["counts"] == {
        "raw_input_images": 2,
        "ingest_assets": 1,
        "candidate_regions": 2,
        "canonical_regions": 1,
        "admitted_vision_panels": 0,
        "rejected_non_panel": 0,
        "deduped": 0,
        "merged": 0,
        "needs_review": 1,
    }
    assert ledger["transitions"][-1]["to"] == "admitted_vision_panels"
    assert ledger["transitions"][-1]["output_count"] == 0
    assert ledger["transitions"][-1]["reason_code"] == "segmentation.ambiguous_boundary"


def test_prepare_project_panels_attaches_funnel_to_segmentation_failure(monkeypatch):
    module = _module()
    segmentation = importlib.import_module("app.services.segmentation")
    pipeline = importlib.import_module("app.services.pipeline")
    from types import SimpleNamespace

    input_row = segmentation.SourceAssetInput(
        source_asset_id="asset-funnel-error",
        original_checksum="a" * 64,
        original_width=100,
        original_height=200,
        source_bounds=(0, 0, 100, 200),
        strip_order=0,
        region_order=0,
        payload=b"funnel-error-payload",
        decoded_width=100,
        decoded_height=200,
    )
    coverage = segmentation.CoverageMap(
        version="coverage-v1",
        map_sha256="b" * 64,
        source_asset_ids=("asset-funnel-error",),
        tiles=(),
        regions=(
            segmentation.CoverageRegion(
                region_id="error-panel",
                source_asset_id="asset-funnel-error",
                source_order=0,
                bounds=(0, 0, 100, 100),
                region_class="canonical_panel",
                area=10_000,
                confidence=0.99,
                evidence="local-panel",
            ),
            segmentation.CoverageRegion(
                region_id="error-gutter",
                source_asset_id="asset-funnel-error",
                source_order=1,
                bounds=(0, 100, 100, 200),
                region_class="verified_gutter",
                area=10_000,
                confidence=0.99,
                evidence="local-separator",
            ),
        ),
        source_content_coverage_ratio=1.0,
        canonical_panel_area=10_000,
        verified_gutter_area=10_000,
        unresolved_material_area=0,
        panel_count=1,
        reconciliation_errors=(),
    )
    monkeypatch.setattr(
        pipeline,
        "project_assets",
        lambda _db, _project_id: (SimpleNamespace(id="asset-funnel-error", type="image"),),
    )
    monkeypatch.setattr(pipeline, "image_assets", lambda assets: list(assets))
    monkeypatch.setattr(
        pipeline,
        "_build_source_inputs",
        lambda _assets: (
            (input_row,),
            {"asset-funnel-error": SimpleNamespace(id="asset-funnel-error")},
        ),
    )
    monkeypatch.setattr(segmentation, "build_complete_coverage_map", lambda *_args, **_kwargs: coverage)
    monkeypatch.setattr(segmentation, "verify_segmentation_completeness", lambda *_args, **_kwargs: ())

    def fail_reconciliation(*_args, **_kwargs):
        raise module.strip_segmentation.StripSegmentationError(
            "segmentation.ambiguous_boundary",
            reviewable=True,
        )

    monkeypatch.setattr(module.strip_segmentation, "reconcile_sources", fail_reconciliation)

    with pytest.raises(module.CloudStageError) as caught:
        module.prepare_project_panels(
            object(),
            "project-funnel-error",
            panel_sink=lambda _panel: None,
        )

    assert caught.value.code == "segmentation.ambiguous_boundary"
    ledger = caught.value.safe_metadata["panel_admission"]
    assert ledger["status"] == "BLOCKED"
    assert ledger["counts"]["candidate_regions"] == 2
    assert ledger["counts"]["canonical_regions"] == 1
    assert ledger["counts"]["admitted_vision_panels"] == 0
    assert ledger["counts"]["rejected_non_panel"] == 1
    assert ledger["terminal_reason_code"] == "segmentation.ambiguous_boundary"


def test_panel_admission_rejects_explicit_blank_title_without_story_evidence():
    module = _module()
    blank = _admission_panel(module, "title-0", order=0, payload=_admission_png((255, 255, 255)))

    result = module.admit_panel_inputs(
        (blank,),
        panel_hints={
            "title-0": {
                "classification": "title",
                "story_evidence": False,
                "metrics": {"uniform_fraction": 1.0},
            }
        },
    )

    assert result.admitted == ()
    assert result.ledger["counts"]["rejected_non_panel"] == 1
    assert result.ledger["decisions"][0]["reason_code"] == "admission.title_no_story_evidence"


def test_panel_admission_never_drops_protected_or_dialogue_ambiguous_region():
    module = _module()
    panel = _admission_panel(module, "ambiguous-1", order=0, payload=_admission_png((255, 255, 255)))

    result = module.admit_panel_inputs(
        (panel,),
        panel_hints={
            "ambiguous-1": {
                "classification": "near_blank",
                "story_evidence": False,
                "protected_regions": True,
                "dialogue_or_ocr": True,
            }
        },
    )

    assert result.admitted == ()
    assert result.ledger["counts"]["rejected_non_panel"] == 0
    assert result.ledger["counts"]["needs_review"] == 1
    assert result.ledger["decisions"][0]["reason_code"] == "admission.protected_or_dialogue_ambiguous"


def test_panel_admission_exact_and_overlapping_near_duplicate_are_deduped():
    module = _module()
    first = _admission_panel(module, "first", order=0, payload=_admission_png((20, 30, 40)))
    exact = module.CloudPanelInput(
        panel_id="exact-copy",
        source_asset_id="admission-asset-copy",
        source_order=1,
        mime_type="image/png",
        payload=first.payload,
        source_checksum=first.source_checksum,
        panel_bounds=first.panel_bounds,
        source_dimensions=first.source_dimensions,
        strip_region_id="exact-copy",
    )
    near = module.CloudPanelInput(
        panel_id="near-copy",
        source_asset_id="admission-asset-near",
        source_order=2,
        mime_type="image/png",
        payload=_admission_png((21, 31, 41)),
        source_checksum=first.source_checksum,
        panel_bounds=first.panel_bounds,
        source_dimensions=first.source_dimensions,
        strip_region_id="near-copy",
    )

    result = module.admit_panel_inputs((first, exact, near))

    assert result.admitted == (first,)
    assert result.ledger["counts"]["deduped"] == 2
    assert [decision["reason_code"] for decision in result.ledger["decisions"]] == [
        "admission.admitted",
        "admission.exact_duplicate",
        "admission.near_duplicate_crop",
    ]


def test_panel_admission_keeps_adjacent_true_panels_distinct():
    module = _module()
    upper = _admission_panel(module, "upper", order=0, bounds=(0, 0, 32, 32))
    lower = _admission_panel(module, "lower", order=1, bounds=(0, 32, 32, 64))

    result = module.admit_panel_inputs((upper, lower))

    assert [panel.panel_id for panel in result.admitted] == ["upper", "lower"]
    assert result.ledger["counts"]["deduped"] == 0


def test_panel_admission_merges_only_geometry_proven_adjacent_oversegmentation():
    module = _module()
    upper = _admission_panel(module, "split-upper", order=0, bounds=(0, 0, 32, 32))
    lower = _admission_panel(module, "split-lower", order=1, bounds=(0, 32, 32, 64))
    merged = _admission_panel(module, "merged", order=0, bounds=(0, 0, 32, 64))

    result = module.admit_panel_inputs(
        (upper, lower),
        merge_candidates=(
            {
                "panel_ids": ["split-upper", "split-lower"],
                "merged_panel": merged,
                "geometry_verified": True,
                "protected_regions_preserved": True,
            },
        ),
    )

    assert [panel.panel_id for panel in result.admitted] == ["merged"]
    assert result.ledger["counts"]["merged"] == 1
    assert result.ledger["decisions"][0]["reason_code"] == "admission.oversegmentation_merged"


def test_panel_admission_rejects_duplicate_ids_and_preserves_deterministic_ledger():
    module = _module()
    first = _admission_panel(module, "duplicate", order=0)
    second = replace(
        first,
        source_order=1,
        payload=_admission_png((50, 60, 70)),
        payload_checksum="",
    )

    with pytest.raises(module.CloudStageError) as caught:
        module.admit_panel_inputs((first, second))

    assert caught.value.code == "cloud.panel_admission_invalid"
    assert caught.value.safe_metadata["reason_code"] == "admission.duplicate_panel_id"
