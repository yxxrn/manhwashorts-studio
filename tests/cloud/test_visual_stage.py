"""Cloud multimodal regression tests grouped by responsibility."""

from __future__ import annotations

from tests.cloud.mass_support import (
    SimpleNamespace,
    _admission_panel,
    _admission_png,
    _boundary_request,
    _BoundaryLineageProvider,
    _FakeProvider,
    _identity,
    _immutable_slot_fixture,
    _module,
    _narrative_output,
    _panels,
    _position_rewrite_text,
    _provider_position_vector,
    _visual_row,
    importlib,
    json,
    pytest,
    replace,
)


def test_visual_parallel_worker_setting_is_bounded_and_environment_driven(monkeypatch):
    module = _module()

    monkeypatch.setenv("MS_VISUAL_PARALLEL_WORKERS", "4")
    assert module._configured_visual_parallel_workers() == 4
    monkeypatch.setenv("MS_VISUAL_PARALLEL_WORKERS", "0")
    assert module._configured_visual_parallel_workers() == 1
    monkeypatch.setenv("MS_VISUAL_PARALLEL_WORKERS", "99")
    assert module._configured_visual_parallel_workers() == 32
    monkeypatch.setenv("MS_VISUAL_PARALLEL_WORKERS", "invalid")
    assert module._configured_visual_parallel_workers() == 8

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

def test_visual_repair_failure_classifies_safe_predicate_and_targets_retry_feedback():
    module = _module()

    metadata = module._visual_narrative_repair_error_metadata(
        "repaired section still has no feasible visual citation",
        code="visual.narrative_repair_ungrounded",
    )

    assert metadata == {
        "failed_predicate": "visual.repair_missing_section_without_feasible_citation",
    }
    assert "missing section" in module._visual_narrative_repair_retry_feedback(
        "visual.narrative_repair_ungrounded",
        failed_predicate=metadata["failed_predicate"],
    )
    chronology_feedback = module._visual_narrative_repair_retry_feedback(
        "visual.narrative_repair_ungrounded",
        failed_predicate="visual.repair_chronology",
    )
    assert "chronology_contract" in chronology_feedback
    assert "min_source_order" in chronology_feedback
    assert "hook may be later" in chronology_feedback
    capacity_feedback = module._visual_narrative_repair_retry_feedback(
        "visual.narrative_repair_ungrounded",
        failed_predicate="visual.repair_visual_capacity_shortfall",
    )
    assert "capacity_contract" in capacity_feedback
    assert "evidence_panel_slot_capacity" in capacity_feedback
    assert "visual_slot_capacity" in capacity_feedback
    assert "unique cited panels" in capacity_feedback
    assert "repaired section still" not in str(metadata)

def test_visual_repair_runner_limits_claims_to_original_feasible_lineage(monkeypatch):
    module = _module()
    repair = importlib.import_module("app.services.visual_narrative_repair")
    monkeypatch.setattr(repair.reference_profile, "REVIEW_MAX_SHOT_SECONDS", 60.0)
    ledger = repair.FeasibleVisualLedger(
        entries=(
            repair.FeasibleVisualRecord(
                panel_region_id="region-safe",
                panel_id="panel-safe",
                source_asset_id="asset-safe",
                source_order=10,
                eligible_sections=("hook",),
                eligible_beats=("beat-safe",),
                resolution_state="NATIVE",
                feasible_rois=({"kind": "primary", "roi_label": "primary", "crop_box": [0, 0, 100, 100], "telemetry": {}},),
                visual_strengths={"edge_connected_blank_fraction": 0.0, "protected_retained_fraction": 1.0},
                evidence_hash="e" * 64,
                detector_version="detector-v1",
                mask_sha256="m" * 64,
                panel_size=(100, 100),
            ),
        ),
        model_identity_hash=_identity(module).identity_hash,
    )
    visual = SimpleNamespace(
        panels=({"panel_id": "panel-safe"}, {"panel_id": "panel-unsafe"}),
        source_hash="source-hash",
        visual_evidence_hash="visual-hash",
    )
    story_dict = {
        "beats": [{"beat_id": "beat-safe", "panel_ids": ["panel-safe", "panel-unsafe"]}],
        "claims": [
            {"claim_id": "claim-safe", "panel_ids": ["panel-safe", "panel-unsafe"]},
            {"claim_id": "claim-unsafe", "panel_ids": ["panel-unsafe"]},
        ],
    }
    story_map = SimpleNamespace(
        claims=tuple(story_dict["claims"]),
        story_map_hash="story-hash",
        as_dict=lambda: story_dict,
    )

    class Provider:
        model_id = _identity(module).model

        def __init__(self):
            self.payloads = []

        def complete_json(self, **kwargs):
            self.payloads.append(dict(kwargs["payload"]))
            return {
                "claims": [{"claim_id": "claim-safe", "evidence_panel_ids": ["panel-safe"]}],
                "passages": [{
                    "passage_id": "p1",
                    "text": "A grounded visible change lands here.",
                    "claim_ids": ["claim-safe"],
                    "evidence_panel_ids": ["panel-safe"],
                }],
                "narrative_outline": {"story_spine": {}, "ending_kind": "consequence"},
            }

    captured = []

    def stop_after_wiring(value, *, ledger, allowed_claim_ids, allowed_claim_panel_ids=None):
        del ledger, allowed_claim_ids
        captured.append({claim_id: set(panel_ids) for claim_id, panel_ids in allowed_claim_panel_ids.items()})
        assert len(value["claims"]) == 1
        assert value["claims"][0]["claim_id"] == "claim-safe"
        assert value["claims"][0]["claim_type"] == "interpretation"
        assert value["claims"][0]["evidence_panel_ids"] == ["panel-safe"]
        raise repair.VisualNarrativeRepairError(
            "stop after lineage wiring",
            "visual.narrative_repair_ungrounded",
        )

    monkeypatch.setattr(repair, "validate_repaired_panel_references", stop_after_wiring)
    provider = Provider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        max_attempts=1,
    )
    runner._narration_observations = lambda *_args: (
        [{"panel_id": "panel-safe"}],
        {"continuity_ledger": {}, "coverage_manifest": {}},
    )

    with pytest.raises(module.CloudStageError):
        runner.run_visual_narrative_repair(
            visual,
            story_map,
            None,
            ledger,
            {"hook": ("beat-safe",)},
        )

    assert provider.payloads
    assert provider.payloads[0]["feasible_claim_ids"] == ["claim-safe"]
    assert captured
    assert all(item == {"claim-safe": {"panel-safe"}} for item in captured)

def test_visual_repair_reconstructs_referenced_authoritative_claim_before_validation(monkeypatch):
    module = _module()
    repair = importlib.import_module("app.services.visual_narrative_repair")
    ledger = repair.FeasibleVisualLedger(
        entries=(repair.FeasibleVisualRecord(
            panel_region_id="region-safe",
            panel_id="panel-safe",
            source_asset_id="asset-safe",
            source_order=10,
            eligible_sections=("hook",),
            eligible_beats=("beat-safe",),
            resolution_state="NATIVE",
            feasible_rois=({"kind":"primary","roi_label":"primary","crop_box":[0,0,100,100],"telemetry":{}},),
            visual_strengths={"edge_connected_blank_fraction":0.0,"protected_retained_fraction":1.0},
            evidence_hash="e" * 64,
            detector_version="detector-v1",
            mask_sha256="m" * 64,
            panel_size=(100,100),
        ),),
        model_identity_hash=_identity(module).identity_hash,
    )
    visual = SimpleNamespace(
        panels=({"panel_id":"panel-safe"},),
        source_hash="source-hash",
        visual_evidence_hash="visual-hash",
    )
    story_dict = {
        "beats":[{"beat_id":"beat-safe","panel_ids":["panel-safe"]}],
        "claims":[{"claim_id":"claim-safe","text":"Visible fact.","qualification":"Directly visible.","panel_ids":["panel-safe"]}],
    }
    story_map = SimpleNamespace(
        claims=tuple(story_dict["claims"]),
        story_map_hash="story-hash",
        as_dict=lambda: story_dict,
    )
    class Provider:
        model_id = _identity(module).model
        def complete_json(self, **_kwargs):
            return {
                "claims": [{
                    "claim_id": "claim-safe",
                    "evidence_panel_ids": ["panel-wrong-provider-value"],
                }],
                "passages":[{
                    "passage_id":"p1",
                    "text":"A grounded visible change lands here.",
                    "claim_ids":["claim-safe"],
                    "evidence_panel_ids":["panel-safe"],
                }],
                "narrative_outline":{"story_spine":{},"ending_kind":"consequence"},
            }
    original_build_repair_payload = repair.build_repair_payload
    def build_feasible_test_payload(**kwargs):
        payload = original_build_repair_payload(**kwargs)
        payload["capacity_safe_claim_plan"] = {
            "feasible": True,
            "rows": [{
                "passage_index": 0,
                "section": "hook",
                "required_visual_slots": 1,
                "available_visual_slots": 1,
                "claim_ids": ["claim-safe"],
                "evidence_panel_ids": ["panel-safe"],
                "evidence_panel_slot_capacity": {"panel-safe": 1},
                "claim_min_source_orders": [10],
                "max_lexical_words": 125,
                "target_lexical_words": 20,
                "feasible": True,
            }],
        }
        return payload
    monkeypatch.setattr(repair, "build_repair_payload", build_feasible_test_payload)
    captured=[]
    def stop_after_reconstruction(value, **_kwargs):
        captured.extend(value["claims"])
        raise repair.VisualNarrativeRepairError("stop after reconstruction", "visual.narrative_repair_ungrounded")
    monkeypatch.setattr(repair, "validate_repaired_panel_references", stop_after_reconstruction)
    runner = module.CloudStageRunner(provider=Provider(), model_identity=_identity(module), max_attempts=1)
    runner._narration_observations = lambda *_args: (
        [{"panel_id":"panel-safe"}],
        {"continuity_ledger":{},"coverage_manifest":{}},
    )
    with pytest.raises(module.CloudStageError):
        runner.run_visual_narrative_repair(visual, story_map, None, ledger, {"hook": ("beat-safe",)})
    assert len(captured) == repair.MAX_REPAIR_ATTEMPTS
    assert all(
        claim == {
            "claim_id": "claim-safe",
            "claim_type": "interpretation",
            "text": "Visible fact.",
            "qualification": "Directly visible.",
            "evidence_panel_ids": ["panel-safe"],
        }
        for claim in captured
    )

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

def test_stage_runner_rejects_foreign_boundary_lineage_without_duplicate_request():
    module = _module()
    provider = _BoundaryLineageProvider(foreign_responses=1)
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        max_attempts=2,
    )

    with pytest.raises(module.strip_segmentation.StripSegmentationError) as caught:
        runner.assess_strip_boundaries(_boundary_request(module))

    assert caught.value.code == "segmentation.provider_lineage_invalid"
    assert len([call for call in provider.calls if call[0] == "strip_segmentation"]) == 1

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
    assert len([call for call in provider.calls if call[0] == "strip_segmentation"]) == 1

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
    expected_calls = (len(panels) + module.VISUAL_REQUEST_MAX_PANELS - 1) // module.VISUAL_REQUEST_MAX_PANELS
    assert len([call for call in provider.calls if call[0] == "visual"]) == expected_calls

def test_visual_schema_rejection_retries_only_poison_panel_and_keeps_valid_rows():
    module = _module()

    class _PartialSchemaProvider(_FakeProvider):
        def __post_init__(self):
            super().__post_init__()
            self.request_panel_ids = []
            self.poisoned_once = False

        def observe(self, request):
            panel_ids = tuple(panel["panel_id"] for panel in request.panels)
            self.request_panel_ids.append(panel_ids)
            rows = super().observe(request)
            if "chapter-a-panel-1" in panel_ids and not self.poisoned_once and len(panel_ids) > 1:
                self.poisoned_once = True
                for row in rows:
                    if row["panel_id"] == "chapter-a-panel-1":
                        row["visual_evidence"] = {"balloon_regions": "malformed"}
            return rows

    provider = _PartialSchemaProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        max_attempts=2,
    )

    result = runner.run_visual_evidence(_panels(module))

    assert result.panel_ids == tuple(panel.panel_id for panel in _panels(module))
    assert provider.request_panel_ids == [
        ("chapter-a-panel-0", "chapter-a-panel-1", "chapter-a-panel-2"),
        ("chapter-a-panel-1",),
    ]

def test_empty_semantic_row_uses_versioned_single_panel_repair():
    module = _module()

    class _EmptyThenSemanticRepair(_FakeProvider):
        def __post_init__(self):
            super().__post_init__()
            self.request_panel_ids = []
            self.request_prompt_versions = []

        def observe(self, request):
            self.request_panel_ids.append(tuple(panel["panel_id"] for panel in request.panels))
            self.request_prompt_versions.append(request.visual_instruction_version)
            rows = super().observe(request)
            if request.visual_instruction_version == "balloon-free-visual-evidence-v2":
                for row in rows:
                    if row["panel_id"] == "chapter-a-panel-1":
                        row["visible_facts"] = []
            return rows

    provider = _EmptyThenSemanticRepair()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        max_attempts=2,
    )

    result = runner.run_visual_evidence(_panels(module))

    assert result.panel_ids == tuple(panel.panel_id for panel in _panels(module))
    assert provider.request_panel_ids == [
        ("chapter-a-panel-0", "chapter-a-panel-1", "chapter-a-panel-2"),
        ("chapter-a-panel-1",),
    ]
    assert provider.request_prompt_versions == [
        "balloon-free-visual-evidence-v2",
        "balloon-free-visual-evidence-repair-v2",
    ]

def test_unknown_geometry_is_admitted_only_as_audited_conservative_full_panel():
    module = _module()

    class _AlwaysUnknownOnePanel(_FakeProvider):
        def observe(self, request):
            rows = super().observe(request)
            for row in rows:
                if row["panel_id"] == "chapter-a-panel-1":
                    row["visual_evidence"].update(
                        {
                            "balloon_mask_status": "unknown",
                            "mask_confidence": 0.0,
                            "evidence_source": "vision_geometry_unavailable",
                            "mask_reason": "geometry is unavailable",
                        }
                    )
            return rows

    provider = _AlwaysUnknownOnePanel()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        max_attempts=1,
    )

    result = runner.run_visual_evidence(_panels(module))
    unknown = next(
        row for row in result.panels if row["panel_id"] == "chapter-a-panel-1"
    )

    assert result.panel_ids == tuple(panel.panel_id for panel in _panels(module))
    assert unknown["visual_evidence"]["balloon_mask_status"] == "unknown"
    assert unknown["visual_evidence"]["evidence_source"] == "conservative_full_panel_v1"

def test_live_visual_request_panel_cap_is_bounded_for_response_size():
    module = _module()

    assert module.VISUAL_REQUEST_MAX_PANELS == 4
    assert module.VISUAL_REQUEST_OVERLAP == 0

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
    assert result.panel_ids == (
        "chapter-a-panel-0",
        "chapter-a-panel-1",
        "chapter-a-panel-2",
    )
    assert len([call for call in provider.calls if call[0] == "visual"]) == 2
    fallback = result.panels[1]
    assert fallback["fallback_mode"] == "conservative_full_panel_v1"
    assert fallback["visual_evidence"]["evidence_source"] == "conservative_full_panel_v1"

def test_unknown_visual_geometry_gets_targeted_singleton_repair_before_fallback():
    module = _module()

    class _ChunkUnknownSingletonKnown(_FakeProvider):
        def __post_init__(self):
            super().__post_init__()
            self.request_panel_ids = []

        def observe(self, request):
            self.request_panel_ids.append(
                tuple(panel["panel_id"] for panel in request.panels)
            )
            rows = super().observe(request)
            if len(request.panels) > 1:
                for row in rows:
                    if row["panel_id"] == "chapter-a-panel-1":
                        row["visual_evidence"].update(
                            {
                                "balloon_mask_status": "unknown",
                                "mask_confidence": 0.0,
                                "evidence_source": "vision_geometry_unavailable",
                                "mask_reason": "geometry is unavailable in a multi-panel response",
                            }
                        )
            return rows

    provider = _ChunkUnknownSingletonKnown()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        max_attempts=1,
    )

    result = runner.run_visual_evidence(_panels(module))

    assert provider.request_panel_ids == [
        ("chapter-a-panel-0", "chapter-a-panel-1", "chapter-a-panel-2"),
        ("chapter-a-panel-1",),
    ]
    repaired = result.panels[1]
    assert repaired.get("fallback_mode") is None
    assert repaired["visual_evidence"]["balloon_mask_status"] != "unknown"

def test_transient_unknown_visual_response_is_repaired_per_panel():
    module = _module()
    provider = _FakeProvider(transient_unknown_count=1)
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        max_attempts=2,
    )

    result = runner.run_visual_evidence(_panels(module))

    assert result.reconciled is True
    assert len([call for call in provider.calls if call[0] == "visual"]) == (
        1 + len(_panels(module))
    )
    assert provider.analysis_run_ids[0] != provider.analysis_run_ids[1]

def test_reconciled_evidence_cannot_enter_regular_render_until_state_is_ready():
    module = _module()
    provider = _FakeProvider()
    runner = module.CloudStageRunner(provider=provider, model_identity=_identity(module))
    result = runner.run_chapter(_panels(module))
    assert module.regular_render_allowed(result) is False
    assert module.review_only_render_gate(result).publish_allowed is False

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

def test_capacity_locked_adaptive_registry_accepts_56_without_relaxing_standard():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    passage_ids = [str(row["passage_id"]) for row in candidate.passages[:5]]
    passage_word_budgets = dict(zip(passage_ids, (12, 11, 11, 11, 11), strict=True))
    adaptive = {
        "version": "coherent_capacity_adaptive_v1",
        "adaptive": True,
        "target_word_min": 49,
        "target_word_goal": 56,
        "target_word_max": 63,
        "target_duration_min_s": 21.3,
        "target_duration_max_s": 27.39,
    }

    with pytest.raises(module.CloudStageError) as standard_exc:
        runner._build_narration_repair_position_registry(
            candidate, story_map, passage_word_budgets=passage_word_budgets
        )
    assert standard_exc.value.code == "cloud.narrative_repair_position_budget_invalid"

    registry = runner._build_narration_repair_position_registry(
        candidate,
        story_map,
        passage_word_budgets=passage_word_budgets,
        duration_policy_contract=adaptive,
    )
    raw = {
        "rewrites": [
            _position_rewrite_text(row["word_budget"], f"adaptive{index}_")
            for index, row in enumerate(registry["positions"])
        ]
    }
    reconciled = runner._reconcile_narration_repair_vector(raw, registry, candidate)

    assert registry["target_word_count"] == 56
    assert registry["duration_policy_contract"] == adaptive
    assert sum(
        module.script.narration_word_count(row["text"])
        for row in reconciled["script_passages"]
    ) == 56

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

def test_visual_repair_retry_feedback_targets_subtitle_overflow():
    module = _module()

    feedback = module._visual_narrative_repair_retry_feedback(
        "visual.narrative_repair_ungrounded",
        failed_field="script_passages",
        failed_predicate="visual.repair_subtitle_overflow",
    )

    assert "shorter subtitle-safe wording" in feedback
    assert "22 characters" in feedback
    assert "same grounded claim IDs and evidence" in feedback

def test_visual_repair_retry_feedback_targets_stiff_spoken_prose():
    module = _module()

    feedback = module._visual_narrative_repair_retry_feedback(
        "cloud.narrative_style_stiff",
        failed_predicate="narrative.stiff_spoken_prose",
    )

    assert "conversational narrator English" in feedback
    assert "bureaucratic filler" in feedback
    assert "exact claim and panel bundle" in feedback

def test_visual_repair_retry_feedback_targets_visual_capacity_shortfall():
    module = _module()

    feedback = module._visual_narrative_repair_retry_feedback(
        "visual.narrative_repair_ungrounded",
        failed_field="script_passages",
        failed_predicate="visual.repair_visual_capacity_shortfall",
    )

    assert "required_visual_slots" in feedback
    assert "unique panels" in feedback
    assert "unrelated panels" in feedback
    assert "4.0 seconds" in feedback

def test_visual_repair_retry_feedback_enforces_mandatory_capacity_plan():
    module = _module()

    feedback = module._visual_narrative_repair_retry_feedback(
        "visual.narrative_repair_ungrounded",
        failed_predicate="visual.repair_capacity_plan_mismatch",
    )

    assert "capacity_safe_claim_plan exactly" in feedback
    assert "claim_ids and evidence_panel_ids" in feedback
    assert "max_lexical_words" in feedback

def test_visual_repair_retry_feedback_targets_ending_and_compaction_contracts():
    module = _module()

    metadata = module._visual_narrative_repair_analyzer_metadata(
        "open_question ending must be evidence-grounded and end with ?",
        {"script_passages": [{}]},
    )

    assert metadata["failed_field"] == "ending_kind"
    ending_feedback = module._visual_narrative_repair_retry_feedback(
        "cloud.narrative_not_grounded",
        failed_field=metadata["failed_field"],
    )
    assert "consequence" in ending_feedback
    compaction_feedback = module._visual_narrative_repair_retry_feedback(
        "cloud.narrative_repair_micro_compaction_unavailable",
    )
    assert "118-122" in compaction_feedback
    assert "do not rely on contraction" in compaction_feedback

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
    assert runner.last_visual_stream_metrics["worker_levels"] == [8]
    assert runner.last_visual_stream_metrics["worker_count"] == 8
    assert runner.last_visual_stream_metrics["request_count"] == len(provider.calls)

def test_stream_finish_accepts_out_of_order_source_group_submission():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-source-groups"))
    )
    provider = _FakeProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.MemoryStageCache(),
        max_attempts=1,
    )
    stream = runner.start_visual_evidence_stream(
        queue_size=1,
        max_panels=1,
        max_estimated_bytes=10_000_000,
    )
    for panel in (panels[2], panels[0], panels[1]):
        stream.submit(panel)

    result = stream.finish(panels)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels)

def test_visual_panel_failure_scope_quarantines_local_errors_but_keeps_integrity_hard():
    module = _module()

    assert module._classify_visual_failure(
        "cloud.provider_response_invalid", singleton=True
    ) == "panel_local_reject"
    assert module._classify_visual_failure(
        "cloud.provider_response_invalid"
    ) == "project_hard_stop"
    assert module._classify_visual_failure(
        "cloud.panel_coverage_incomplete", singleton=True
    ) == "project_hard_stop"
    assert module._classify_visual_failure(
        "visual.balloon_mask_unknown", singleton=True
    ) == "panel_local_reject"
    assert module._classify_visual_failure(
        "visual.blank_infeasible", singleton=True
    ) == "panel_local_reject"
    assert module._classify_visual_failure(
        "cloud.panel_lineage_invalid", singleton=True
    ) == "project_hard_stop"
    assert module._classify_visual_failure(
        "cloud.provider_request_failed", singleton=True
    ) == "project_hard_stop"

def test_stream_merge_accepts_only_explicitly_quarantined_subset():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-quarantine-merge"))
    )
    rows = []
    for index, panel in enumerate(panels[:-1]):
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
        ({"rows": rows},),
        panels,
        rejected_panel_ids=(panels[-1].panel_id,),
    )

    assert tuple(row["panel_id"] for row in merged) == tuple(
        panel.panel_id for panel in panels[:-1]
    )

def test_stream_metrics_preserve_sanitized_visual_failure_predicate():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-failure-predicate"))
    )

    class _NoVisibleFactsProvider(_FakeProvider):
        def observe(self, request):
            rows = super().observe(request)
            for row in rows:
                row["visible_facts"] = []
            return rows

    runner = module.CloudStageRunner(
        provider=_NoVisibleFactsProvider(),
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

    assert caught.value.code == "visual.capacity_insufficient"
    assert runner.last_visual_stream_metrics["visual_failure_predicates"] == {
        "visible_facts_nonempty": len(panels)
    }

def test_stream_final_singleton_probe_recovers_transient_semantic_repair_miss():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-final-singleton"))
    )
    target_id = panels[-1].panel_id

    class _TransientSemanticRepairMissProvider(_FakeProvider):
        def __post_init__(self):
            super().__post_init__()
            self.target_singleton_calls = 0

        def observe(self, request):
            rows = super().observe(request)
            if len(request.panels) > 1:
                for row in rows:
                    if row.get("panel_id") == target_id:
                        row["visible_facts"] = []
                return rows
            if request.panels[0]["panel_id"] == target_id:
                self.target_singleton_calls += 1
                if self.target_singleton_calls == 1:
                    rows[0]["visible_facts"] = []
            return rows

    provider = _TransientSemanticRepairMissProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.MemoryStageCache(),
        max_attempts=2,
    )
    stream = runner.start_visual_evidence_stream(
        queue_size=1,
        max_panels=len(panels),
        max_estimated_bytes=10_000_000,
    )
    for panel in panels:
        stream.submit(panel)

    result = stream.finish(panels)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels)
    assert result.rejected_panels == ()
    assert provider.target_singleton_calls == 2
    ledger = {item["panel_id"]: item for item in runner.last_visual_stream_metrics["panel_attempt_ledger"]}
    assert ledger[target_id]["terminal_status"] == "accepted"
    assert ledger[target_id]["attempt_count"] == 2

def test_stream_quarantines_multiple_terminal_panel_local_failures_after_singleton_repair():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-multi-quarantine"))
    )
    poison_ids = {panels[-2].panel_id, panels[-1].panel_id}

    class _PoisonSiblingsProvider(_FakeProvider):
        def observe(self, request):
            rows = super().observe(request)
            for row in rows:
                if row.get("panel_id") in poison_ids:
                    row["visible_facts"] = []
            return rows

    provider = _PoisonSiblingsProvider()
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

    result = stream.finish(panels)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels[:-2])
    assert [item["panel_id"] for item in result.rejected_panels] == [
        panel.panel_id for panel in panels[-2:]
    ]
    assert runner.last_visual_stream_metrics["missing_panel_count"] == 0
    assert runner.last_visual_stream_metrics["rejected_panel_count"] == 2
    expected_calls_per_poison = 3 + 2 * module.VISUAL_FINAL_FRESH_SINGLETON_ATTEMPTS
    assert len(provider.calls) == 1 + expected_calls_per_poison * len(poison_ids)

def test_stream_retry_budget_honors_configured_attempts_for_missing_panel():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-retry-budget"))
    )

    class _EventuallyCompleteProvider(_FakeProvider):
        def observe(self, request):
            rows = super().observe(request)
            if len(self.calls) <= 2:
                return [
                    row
                    for row in rows
                    if row.get("panel_id") != "stream-retry-budget-panel-2"
                ]
            return rows

    provider = _EventuallyCompleteProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.MemoryStageCache(),
        max_attempts=3,
    )
    stream = runner.start_visual_evidence_stream(
        queue_size=1,
        max_panels=len(panels),
        max_estimated_bytes=10_000_000,
    )
    for panel in panels:
        stream.submit(panel)

    result = stream.finish(panels)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels)
    assert len(provider.calls) == 3
    assert runner.last_visual_stream_metrics["retry_count"] == 2

def test_stream_fresh_singleton_confirmation_recovers_after_budget_exhausted():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-fresh-confirmation"))
    )
    target_id = panels[-1].panel_id

    class _FreshConfirmationProvider(_FakeProvider):
        def __init__(self):
            super().__init__()
            self.target_singletons = 0

        def observe(self, request):
            rows = super().observe(request)
            if len(request.panels) > 1:
                return [row for row in rows if row.get("panel_id") != target_id]
            if request.panels[0]["panel_id"] == target_id:
                self.target_singletons += 1
                if self.target_singletons <= 2:
                    raise module.CloudStageError("cloud.provider_response_invalid")
            return rows

    provider = _FreshConfirmationProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.MemoryStageCache(),
        max_attempts=2,
    )
    stream = runner.start_visual_evidence_stream(
        queue_size=1,
        max_panels=len(panels),
        max_estimated_bytes=10_000_000,
    )
    for panel in panels:
        stream.submit(panel)

    result = stream.finish(panels)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels)
    assert result.rejected_panels == ()
    assert provider.target_singletons == 3
    ledger = {
        item["panel_id"]: item
        for item in runner.last_visual_stream_metrics["panel_attempt_ledger"]
    }
    assert ledger[target_id]["terminal_status"] == "accepted"
    assert ledger[target_id]["attempt_count"] == 4

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

def test_fixed_stream_worker_count_is_configurable_and_only_selected_workers_start():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-worker-count"))
    )
    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=_identity(module),
        cache=module.MemoryStageCache(),
        max_attempts=1,
        visual_parallel_workers=3,
    )
    stream = runner.start_visual_evidence_stream(
        queue_size=1,
        max_panels=1,
        max_estimated_bytes=10_000_000,
    )
    assert len(stream._workers) == 3
    for panel in panels:
        stream.submit(panel)
    stream.finish(panels)
    assert runner.last_visual_stream_metrics["worker_count"] == 3

def test_fixed_stream_concurrency_keeps_configured_width_and_metrics():
    module = _module()
    controller = module._FixedVisualConcurrency(8, wave_panel_target=2)

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
    assert snapshot["selected_worker_level"] == 8
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
    assert ledger["reduction_percentages"]["admitted_vision_panels"] == 100.0

@pytest.mark.parametrize("failure_mode", ("raise", "status"))
def test_prepare_project_panels_attaches_funnel_to_segmentation_failure(
    monkeypatch, failure_mode
):
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
        if failure_mode == "raise":
            raise module.strip_segmentation.StripSegmentationError(
                "segmentation.ambiguous_boundary",
                reviewable=True,
            )
        return SimpleNamespace(
            status="NEEDS_REVIEW",
            reports=(SimpleNamespace(review_code="segmentation.ambiguous_boundary"),),
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

def test_semantic_singleton_progresses_to_geometry_singleton_when_geometry_unknown():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-semantic-to-geometry"))
    )

    class _SemanticThenGeometryProvider(_FakeProvider):
        def observe(self, request):
            self.calls.append(("visual", request.visual_instruction_version, request.visual_instruction_sha256))
            self.analysis_run_ids.append(request.analysis_run_id)
            rows = [_visual_row(panel) for panel in request.panels]
            if len(request.panels) > 1:
                for row in rows:
                    row["visible_facts"] = []
                return rows
            if "-semantic-" in request.analysis_run_id:
                return [_visual_row(request.panels[0], unknown=True)]
            return rows

    provider = _SemanticThenGeometryProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.MemoryStageCache(),
        max_attempts=2,
    )
    result = runner.run_visual_evidence(panels)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels)
    assert len(provider.calls) == 1 + 2 * len(panels)
    assert sum("-semantic-" in value for value in provider.analysis_run_ids) == len(panels)
    assert sum("-geometry-" in value for value in provider.analysis_run_ids) == len(panels)

def test_upstream_stage_identity_accepts_repair_prompt_only_legacy_hash():
    module = _module()
    base = _identity(module)
    current = module.CloudModelIdentity(
        provider=base.provider,
        model=base.model,
        model_version=base.model_version,
        endpoint=base.endpoint,
        prompt_versions=dict(base.prompt_versions)
        | {"visual_narrative_repair": module.CURRENT_VISUAL_REPAIR_PROMPT_VERSION},
    )
    legacy = module.CloudModelIdentity(
        provider=base.provider,
        model=base.model,
        model_version=base.model_version,
        endpoint=base.endpoint,
        prompt_versions=dict(current.prompt_versions)
        | {"visual_narrative_repair": module.LEGACY_VISUAL_REPAIR_PROMPT_VERSION},
    )
    assert module._stage_result_identity_is_compatible(legacy.identity_hash, current, stage="story_map")
    assert module._stage_result_identity_is_compatible(legacy.identity_hash, current, stage="narration")
    assert not module._stage_result_identity_is_compatible(legacy.identity_hash, current, stage="visual_narrative_repair")

def test_upstream_visual_and_story_accept_two_audited_repair_generations():
    module = _module()
    base = _identity(module)
    current = module.CloudModelIdentity(
        provider=base.provider,
        model=base.model,
        model_version=base.model_version,
        endpoint=base.endpoint,
        prompt_versions=dict(base.prompt_versions)
        | {"visual_narrative_repair": module.CURRENT_VISUAL_REPAIR_PROMPT_VERSION},
    )
    earlier = module.CloudModelIdentity(
        provider=base.provider,
        model=base.model,
        model_version=base.model_version,
        endpoint=base.endpoint,
        prompt_versions=dict(current.prompt_versions)
        | {"visual_narrative_repair": module.EARLIER_UPSTREAM_VISUAL_REPAIR_PROMPT_VERSION},
    )
    assert module._stage_result_identity_is_compatible(earlier.identity_hash, current, stage="visual")
    assert module._stage_result_identity_is_compatible(earlier.identity_hash, current, stage="story_map")
    assert not module._stage_result_identity_is_compatible(earlier.identity_hash, current, stage="narration")

def test_upstream_visual_and_story_accept_oldest_audited_repair_generation():
    module = _module()
    base = _identity(module)
    current = module.CloudModelIdentity(
        provider=base.provider,
        model=base.model,
        model_version=base.model_version,
        endpoint=base.endpoint,
        prompt_versions=dict(base.prompt_versions)
        | {"visual_narrative_repair": module.CURRENT_VISUAL_REPAIR_PROMPT_VERSION},
    )
    oldest = module.CloudModelIdentity(
        provider=base.provider,
        model=base.model,
        model_version=base.model_version,
        endpoint=base.endpoint,
        prompt_versions=dict(current.prompt_versions)
        | {"visual_narrative_repair": module.OLDEST_UPSTREAM_VISUAL_REPAIR_PROMPT_VERSION},
    )
    assert module._stage_result_identity_is_compatible(
        oldest.identity_hash, current, stage="visual"
    )
    assert module._stage_result_identity_is_compatible(
        oldest.identity_hash, current, stage="story_map"
    )
    assert not module._stage_result_identity_is_compatible(
        oldest.identity_hash, current, stage="narration"
    )

def test_visual_repair_ending_canonicalization_is_content_preserving():
    module = _module()
    passages = [{"text": "The visible consequence lands here."}]
    outline = {"story_spine": {"unresolved_question": "What follows?"}, "ending_kind": "open_question"}
    normalized, provenance = module._canonicalize_visual_repair_ending(outline, passages)
    assert passages == [{"text": "The visible consequence lands here."}]
    assert outline["ending_kind"] == "open_question"
    assert normalized["ending_kind"] == "consequence"
    assert provenance == {"from": "open_question", "to": "consequence", "version": "visual-repair-ending-v1"}

def test_visual_repair_ending_canonicalization_promotes_grounded_question():
    module = _module()
    passages = [{"text": "What happens after this visible change?"}]
    outline = {"story_spine": {"unresolved_question": "What happens next?"}, "ending_kind": "consequence"}
    normalized, provenance = module._canonicalize_visual_repair_ending(outline, passages)
    assert normalized["ending_kind"] == "open_question"
    assert provenance["to"] == "open_question"

def test_visual_repair_ending_canonicalization_follows_grounded_final_punctuation():
    module = _module()
    outline = {
        "story_spine": {"unresolved_question": "What follows?"},
        "ending_kind": "open_question",
    }
    normalized, provenance = module._canonicalize_visual_repair_ending(
        outline,
        ({"text": "The grounded consequence lands here."},),
    )
    assert normalized["ending_kind"] == "consequence"
    assert outline["ending_kind"] == "open_question"
    assert provenance == {
        "from": "open_question",
        "to": "consequence",
        "version": "visual-repair-ending-v1",
    }

def test_visual_repair_ending_canonicalization_repairs_empty_unresolved_question():
    module = _module()
    passages = ({"text": "The grounded consequence lands here."},)
    outline = {"story_spine": {"unresolved_question": ""}, "ending_kind": "consequence"}
    normalized, provenance = module._canonicalize_visual_repair_ending(outline, passages)
    assert normalized["story_spine"]["unresolved_question"] == "What follows?"
    assert outline["story_spine"]["unresolved_question"] == ""
    assert provenance["unresolved_question_repaired"] == "true"

