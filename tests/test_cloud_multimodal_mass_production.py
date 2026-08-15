"""RED contract tests for the pinned cloud multimodal production path.

These tests intentionally import the new boundary inside test bodies so a
missing implementation is a collection-clean, body-level RED result.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, replace

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
            "story_map": "cloud-causal-map-v1",
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
                    {"beat_id": "beat-2", "panel_ids": panel_ids[1:], "summary": "the next choice stays open"},
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

    assert version == "cloud-causal-map-v1"
    assert len(digest) == 64
    assert "beat_id, panel_ids, summary" in prompt
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


def test_review_preview_failure_code_keeps_nested_stable_code():
    module = _module()

    assert module._review_failure_code(
        "reference_planning_failed: visual.visual_unavailable: no feasible panel"
    ) == "visual.visual_unavailable"
    assert module._review_failure_code(
        "reference.subtitle_overflow: review preview failed"
    ) == "reference.subtitle_overflow"
    assert module._review_failure_code("unstructured local failure") == "review.preview_failed"


def test_review_project_repairs_after_initial_narration_grounding_failure(monkeypatch):
    module = _module()
    from types import SimpleNamespace

    panels = _panels(module)
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
        prompt_version="cloud-causal-map-v1",
        prompt_sha256="c" * 64,
    )
    failed = module.ChapterJobRecord(
        job_id="project-a",
        state=module.ChapterState.NEEDS_REVIEW,
        error_code="cloud.narrative_not_grounded",
        stage_results={"visual": visual.as_dict(), "story_map": story_map.as_dict()},
    )

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
    )
    service.store = Store()
    service.review_root = None
    monkeypatch.setattr(
        module,
        "prepare_project_panels",
        lambda *_args, **_kwargs: (panels, {"status": "RECONCILED"}),
    )
    monkeypatch.setattr(service, "run_job", lambda *_args, **_kwargs: failed)
    observed = {}

    def fake_repair(_db, _project_id, script_row, _panels, _result, **_kwargs):
        observed["script_row"] = script_row
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
    assert result.state == module.ChapterState.REVIEW_PREVIEW_READY, (result.error_code, result.error_message)


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
        prompt_version="cloud-causal-map-v1",
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
    assert len([call for call in provider.calls if call[0] == "visual"]) == 26


def test_live_visual_request_panel_cap_is_bounded_for_response_size():
    module = _module()

    assert module.VISUAL_REQUEST_MAX_PANELS == 1
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


def test_unknown_visual_geometry_blocks_before_story_mapping():
    module = _module()
    provider = _FakeProvider(unknown_visual=True)
    runner = module.CloudStageRunner(provider=provider, model_identity=_identity(module))

    with pytest.raises(module.CloudStageError) as caught:
        runner.run_chapter(_panels(module))

    assert caught.value.code == "visual.balloon_mask_unknown"
    assert [call[0] for call in provider.calls] == ["visual", "visual"]


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
    assert len([call for call in provider.calls if call[0] == "visual"]) == 4
    assert provider.analysis_run_ids[0] != provider.analysis_run_ids[1]


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


def test_project_persistence_reuses_regular_script_gate_without_approval():
    module = _module()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db import Base
    from app.models import Project, SourceAsset, User, Workspace

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        user = User(email="cloud-owner@example.com", name="Cloud Owner", password_hash="test")
        workspace = Workspace(owner=user, name="Cloud Workspace")
        project = Project(workspace=workspace, title="Cloud chapter", chapter="1")
        db.add_all([user, workspace, project])
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
        assert module.regular_render_allowed(result) is False


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
        prompt_version="cloud-causal-map-v1",
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
