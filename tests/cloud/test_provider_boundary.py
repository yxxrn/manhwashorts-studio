"""Cloud multimodal regression tests grouped by responsibility."""

from __future__ import annotations

from tests.cloud.mass_support import (
    SimpleNamespace,
    _CompactNarrationProvider,
    _FakeProvider,
    _identity,
    _immutable_slot_fixture,
    _module,
    _panels,
    _visual_row,
    importlib,
    pytest,
    replace,
)


def test_visual_repair_infeasible_capacity_plan_fails_before_provider_call(monkeypatch):
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
        calls = 0
        def complete_json(self, **_kwargs):
            self.calls += 1
            raise AssertionError("provider must not be called for an infeasible local plan")
    provider = Provider()
    runner = module.CloudStageRunner(provider=provider, model_identity=_identity(module), max_attempts=1)
    runner._narration_observations = lambda *_args: (
        [{"panel_id":"panel-safe"}],
        {"continuity_ledger":{},"coverage_manifest":{}},
    )

    with pytest.raises(module.CloudStageError) as exc_info:
        runner.run_visual_narrative_repair(
            visual, story_map, None, ledger, {"hook": ("beat-safe",)}
        )

    assert exc_info.value.code == "visual.narrative_repair_ungrounded"
    assert exc_info.value.safe_metadata["failed_predicate"] == "visual.repair_capacity_plan_infeasible"
    assert provider.calls == 0

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

def test_call_preserves_typed_transport_metadata_without_retrying_permanent_failure():
    module = _module()
    from app.services import vision_adapter

    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=_identity(module),
        max_attempts=3,
    )
    calls = 0

    def permanent_transport():
        nonlocal calls
        calls += 1
        raise vision_adapter.VisionProviderRequestFailed(
            status_code=429,
            retry_after_s=7.0,
            retryable=False,
            timeout=True,
            transport_subtype="connect",
        )

    with pytest.raises(module.CloudStageError) as caught:
        runner._call(permanent_transport, request_stage="other")

    assert calls == 1
    assert caught.value.code == "cloud.provider_request_failed"
    assert caught.value.safe_metadata == {
        "provider_error_code": "vision_provider_request_failed",
        "request_stage": "other",
        "status_code": 429,
        "retry_after_s": 7.0,
        "timeout": True,
        "retryable": False,
        "transport_subtype": "connect",
        "provider_error_category": "timeout",
    }

def test_call_does_not_retry_capability_errors_as_generic_transport():
    module = _module()
    from app.services import vision_adapter

    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=_identity(module),
        max_attempts=3,
    )
    calls = 0

    def missing_capability():
        nonlocal calls
        calls += 1
        raise vision_adapter.VisionCapabilityError()

    with pytest.raises(module.CloudStageError) as caught:
        runner._call(missing_capability, request_stage="other")

    assert calls == 1
    assert caught.value.code == "cloud.capability_missing"

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

def test_visual_text_only_provider_targets_leave_two_word_ceiling_margin(monkeypatch):
    module = _module()
    runner = module.CloudStageRunner(
        provider=SimpleNamespace(model_id=_identity(module).model),
        model_identity=_identity(module),
        max_attempts=1,
    )
    passage = {
        "passage_id": "p1",
        "editorial_role": "hook",
        "text": "Grounded action stays concise while evidence remains fixed.",
        "claim_ids": ["claim-1"],
        "evidence_panel_ids": ["panel-1"],
    }
    candidate = module.NarrationResult(
        spoken_text=passage["text"],
        display_words=module.derive_display_words(passage["text"]),
        passages=(passage,),
        ending_kind="consequence",
        word_count=9,
        estimated_duration_s=3.91,
        observations=(),
        continuity_ledger={},
        evidence_graph={"claims": [{
            "claim_id": "claim-1", "claim_type": "fact", "text": "Two figures hold swords.",
            "qualification": "Visible evidence supports it.", "evidence_panel_ids": ["panel-1"],
        }]},
        story_spine={}, qc_report={}, model_identity_hash=runner.model_identity.identity_hash,
        prompt_version=runner.prompts["visual_narrative_repair"][0],
        prompt_sha256=runner.prompts["visual_narrative_repair"][1],
        visual_evidence_hash="visual-hash",
    )
    captured = {}
    def fake_targeted(*args, **kwargs):
        captured["targets"] = kwargs["passage_word_targets"]
        captured["ceilings"] = kwargs["passage_word_budgets"]
        return candidate
    monkeypatch.setattr(runner, "_run_targeted_narration_repair", fake_targeted)
    runner._run_visual_repair_text_only_duration_repair(
        visual_repair_prompt=runner.prompts["visual_narrative_repair"],
        source={"ledger_hash": "ledger"},
        observations=({"panel_id": "panel-1", "visible_facts": ["Two figures hold swords."], "uncertainties": []},),
        structural={}, story_map=SimpleNamespace(),
        visual=SimpleNamespace(visual_evidence_hash="visual-hash"), candidate=candidate,
        capacity_plan={"rows": [{"target_lexical_words": 9, "max_lexical_words": 9, "evidence_panel_ids": ["panel-1"]}]},
    )
    assert captured["targets"] == {"p1": 7}
    assert captured["ceilings"] == {"p1": 9}

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

def test_stream_provider_invalid_gets_full_singleton_recovery_budget():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-schema-singleton-budget"))
    )
    target_id = panels[-1].panel_id

    class _TransientSchemaMissProvider(_FakeProvider):
        def __init__(self):
            super().__init__()
            self.request_sizes = []
            self.target_singletons = 0

        def observe(self, request):
            self.request_sizes.append(len(request.panels))
            rows = super().observe(request)
            if len(request.panels) > 1:
                return [row for row in rows if row.get("panel_id") != target_id]
            if request.panels[0]["panel_id"] == target_id:
                self.target_singletons += 1
                if self.target_singletons == 1:
                    return []
            return rows

    provider = _TransientSchemaMissProvider()
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
    assert provider.request_sizes == [len(panels), 1, 1]
    assert provider.target_singletons == 2

def test_stream_provider_invalid_exception_uses_second_singleton_probe():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-schema-exception-budget"))
    )
    target_id = panels[-1].panel_id

    class _TransientInvalidSingletonProvider(_FakeProvider):
        def __init__(self):
            super().__init__()
            self.target_singletons = 0

        def observe(self, request):
            rows = super().observe(request)
            if len(request.panels) > 1:
                return [row for row in rows if row.get("panel_id") != target_id]
            if request.panels[0]["panel_id"] == target_id:
                self.target_singletons += 1
                if self.target_singletons == 1:
                    raise module.CloudStageError("cloud.provider_response_invalid")
            return rows

    provider = _TransientInvalidSingletonProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.MemoryStageCache(),
        max_attempts=2,
    )
    stream = runner.start_visual_evidence_stream(
        queue_size=1, max_panels=len(panels), max_estimated_bytes=10_000_000
    )
    for panel in panels:
        stream.submit(panel)
    result = stream.finish(panels)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels)
    assert result.rejected_panels == ()
    assert provider.target_singletons == 2
    ledger = {item["panel_id"]: item for item in runner.last_visual_stream_metrics["panel_attempt_ledger"]}
    assert ledger[target_id]["attempt_count"] == 3

def test_stream_exhausted_batch_provider_invalid_splits_to_singletons():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-batch-schema-split"))
    )

    class _InvalidBatchProvider(_FakeProvider):
        def __init__(self):
            super().__init__()
            self.batch_calls = 0
            self.singleton_calls = 0

        def observe(self, request):
            if len(request.panels) > 1:
                self.batch_calls += 1
                raise module.CloudStageError("cloud.provider_response_invalid")
            self.singleton_calls += 1
            return super().observe(request)

    provider = _InvalidBatchProvider()
    runner = module.CloudStageRunner(
        provider=provider, model_identity=_identity(module),
        cache=module.MemoryStageCache(), max_attempts=2,
    )
    stream = runner.start_visual_evidence_stream(
        queue_size=1, max_panels=len(panels), max_estimated_bytes=10_000_000
    )
    for panel in panels:
        stream.submit(panel)
    result = stream.finish(panels)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels)
    assert result.rejected_panels == ()
    assert provider.batch_calls == 2
    assert provider.singleton_calls == len(panels)
    ledger = runner.last_visual_stream_metrics["panel_attempt_ledger"]
    assert all(item["terminal_status"] == "accepted" for item in ledger)

def test_typed_retryable_provider_error_retries_once_and_permanent_does_not():
    module = _module()
    provider = _FakeProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        max_attempts=2,
    )
    calls = []

    def retryable():
        calls.append("retryable")
        if len(calls) == 1:
            raise module.VisionProviderRequestFailed(
                status_code=429,
                retry_after_s=0,
                retryable=True,
            )
        return {"ok": True}

    assert runner._call(retryable) == {"ok": True}
    assert calls == ["retryable", "retryable"]

    permanent_calls = []

    def permanent():
        permanent_calls.append("permanent")
        raise module.VisionProviderRequestFailed(
            status_code=400,
            retryable=False,
        )

    with pytest.raises(module.CloudStageError) as caught:
        runner._call(permanent)
    assert caught.value.code == "cloud.provider_request_failed"
    assert permanent_calls == ["permanent"]

def test_provider_concurrency_gate_bounds_actual_nested_calls():
    module = _module()
    import time
    from concurrent.futures import ThreadPoolExecutor

    gate = module._ProviderConcurrencyGate(3)

    def invoke(value):
        return gate.call(lambda: (time.sleep(0.04), value)[1])

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sorted(executor.map(invoke, range(8))) == list(range(8))

    snapshot = gate.snapshot()
    assert snapshot["provider_concurrency_limit"] == 3
    assert snapshot["provider_peak_in_flight"] == 3
    assert snapshot["provider_in_flight"] == 0

def test_stream_worker_and_window_repairs_share_provider_concurrency_gate():
    module = _module()
    provider = _FakeProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        max_attempts=1,
        visual_parallel_workers=4,
    )
    stream = runner.start_visual_evidence_stream(
        queue_size=4,
        max_panels=4,
        worker_count=4,
    )
    try:
        worker_runner = stream._worker_runner()
        assert worker_runner.visual_parallel_workers == 4
        assert worker_runner._provider_concurrency_gate is stream._provider_gate
        assert stream._provider_gate.snapshot()["provider_concurrency_limit"] == 4
    finally:
        stream.abort()

def test_upstream_stage_identity_rejects_provider_model_change():
    module = _module()
    base = _identity(module)
    current = module.CloudModelIdentity(
        provider=base.provider, model="different-model", model_version=base.model_version,
        endpoint=base.endpoint,
        prompt_versions=dict(base.prompt_versions)
        | {"visual_narrative_repair": module.CURRENT_VISUAL_REPAIR_PROMPT_VERSION},
    )
    legacy = module.CloudModelIdentity(
        provider=base.provider, model=base.model, model_version=base.model_version,
        endpoint=base.endpoint,
        prompt_versions=dict(base.prompt_versions)
        | {"visual_narrative_repair": module.LEGACY_VISUAL_REPAIR_PROMPT_VERSION},
    )
    assert not module._stage_result_identity_is_compatible(legacy.identity_hash, current, stage="story_map")

