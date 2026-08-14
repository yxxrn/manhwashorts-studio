"""RED contract tests for the pinned cloud multimodal production path.

These tests intentionally import the new boundary inside test bodies so a
missing implementation is a collection-clean, body-level RED result.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass

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
        )
        for index in range(3)
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
    fail_for_prefix: str = ""
    fail_count: int = 0
    provider_hash: bool = False

    def __post_init__(self):
        self.calls: list[tuple[str, str, str]] = []
        self.boundary_payloads: list[dict] = []

    def observe(self, request):
        self.calls.append(("visual", request.visual_instruction_version, request.visual_instruction_sha256))
        if self.fail_count:
            self.fail_count -= 1
            raise RuntimeError("provider secret-bearing failure detail")
        if self.fail_for_prefix and request.panels[0]["panel_id"].startswith(self.fail_for_prefix):
            raise RuntimeError("provider failure for one chapter")
        return [
            _visual_row(panel, unknown=self.unknown_visual, provider_hash=self.provider_hash)
            for panel in request.panels
        ]

    def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
        self.calls.append((stage, prompt_version, prompt_sha256))
        if stage == "strip_segmentation":
            self.boundary_payloads.append(dict(payload))
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
    assert provider.calls[-1][2] == "b01302bc92536a9ded8581687b094ef88e5688fb184fd750b2496a10ef93d073"
    assert provider.boundary_payloads[-1]["overlapping_source_tiles"]
    assert provider.boundary_payloads[-1]["candidate_boundaries"]


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


def test_unknown_visual_geometry_blocks_before_story_mapping():
    module = _module()
    provider = _FakeProvider(unknown_visual=True)
    runner = module.CloudStageRunner(provider=provider, model_identity=_identity(module))

    with pytest.raises(module.CloudStageError) as caught:
        runner.run_chapter(_panels(module))

    assert caught.value.code == "visual.balloon_mask_unknown"
    assert [call[0] for call in provider.calls] == ["visual"]


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
    changed[0] = module.CloudPanelInput(
        panel_id=changed[0].panel_id,
        source_asset_id=changed[0].source_asset_id,
        source_order=changed[0].source_order,
        mime_type=changed[0].mime_type,
        payload=b"different-content",
    )
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
