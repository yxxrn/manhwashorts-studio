"""RED contract tests for the pinned cloud multimodal production path.

These tests intentionally import the new boundary inside test bodies so a
missing implementation is a collection-clean, body-level RED result.
"""

from __future__ import annotations

import importlib
import json
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


def test_review_preview_failure_code_keeps_nested_stable_code():
    module = _module()

    assert module._review_failure_code(
        "reference_planning_failed: visual.visual_unavailable: no feasible panel"
    ) == "visual.visual_unavailable"
    assert module._review_failure_code(
        "reference.subtitle_overflow: review preview failed"
    ) == "reference.subtitle_overflow"
    assert module._review_failure_code("unstructured local failure") == "review.preview_failed"


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
    assert len([call for call in provider.calls if call[0] == "visual"]) == 2
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
    valid = module.NarrationResult(
        spoken_text="One grounded turn changes what follows.",
        display_words=("ONE", "GROUNDED", "TURN"),
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
        estimated_duration_s=53.0,
        observations=tuple(rows),
        continuity_ledger={},
        evidence_graph={"claims": []},
        story_spine={},
        qc_report={},
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


def _position_rewrite_text(word_budget, prefix):
    return " ".join(f"{prefix.rstrip('_')}word{index}" for index in range(word_budget))


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
    assert first["version"] == "narration-repair-position-registry-v1"
    assert 8 <= len(positions) <= 12
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

def test_narration_targeted_repair_reuses_grounding_and_repairs_duration(tmp_path):
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
        == "vision-first-story-analyzer-v3-targeted-position-repair-v1"
    )
    assert len(repair_prompt_sha256) == 64
    assert "TARGETED NARRATION POSITION REPAIR" in repair_prompt_text
    assert repair_prompt_text != runner.prompts["narration"][2]
    assert provider.repair_payloads[0]["targeted_repair"]["failure_codes"] == [
        "cloud.narrative_duration_out_of_range",
        "cloud.narrative_word_count_out_of_range",
    ]
    assert result.estimated_duration_s >= 50.0
    assert 115 <= result.word_count <= 125
    assert result.qc_report["narration_repair"]["scope"] == (
        "position_locked_rewrite_vector"
    )
    assert result.qc_report["narration_repair"]["candidate_hash"]
    assert result.qc_report["narration_repair"]["position_registry_version"] == (
        "narration-repair-position-registry-v1"
    )
    assert result.qc_report["narration_repair"]["slot_order_hash"]
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
