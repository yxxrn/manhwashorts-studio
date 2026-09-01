"""Cloud multimodal regression tests grouped by responsibility."""

from __future__ import annotations

from tests.cloud.mass_support import (
    _FakeProvider,
    _identity,
    _immutable_slot_fixture,
    _InvalidNarrationProvider,
    _module,
    _narrative_output,
    _panels,
    _visual_row,
    importlib,
    pytest,
    replace,
)


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


def test_narration_reconstructs_passage_evidence_from_claims(tmp_path):
    module = _module()
    panels = _panels(module, prefix="passage-evidence")

    class MissingPassageEvidenceProvider(_FakeProvider):
        def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
            if stage != "narration":
                return super().complete_json(
                    stage=stage,
                    prompt_version=prompt_version,
                    prompt_sha256=prompt_sha256,
                    prompt_text=prompt_text,
                    payload=payload,
                )
            output = _narrative_output("passage-evidence", list(payload["panel_ids"]))
            for passage in output["script_passages"]:
                passage.pop("evidence_panel_ids", None)
            return output

    provider = MissingPassageEvidenceProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(tmp_path / "passage-evidence-cache"),
        max_attempts=1,
    )
    visual = runner.run_visual_evidence(panels)
    story_map = runner.run_story_map(visual)

    result = runner.run_narration(visual, story_map, panels=panels)

    assert all(passage["evidence_panel_ids"] for passage in result.passages)
    assert all(
        set(passage["evidence_panel_ids"]) >= set(
            {
                panel_id
                for claim in result.evidence_graph["claims"]
                if claim["claim_id"] in passage["claim_ids"]
                for panel_id in claim["evidence_panel_ids"]
            }
        )
        for passage in result.passages
    )

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
    assert result.qc_report["narration_topology"] == "chapter_story_understanding_v1"
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


def test_narration_retry_feedback_requires_supported_ending_contract():
    module = _module()

    feedback = module._narration_retry_feedback(
        "v3 narrative_outline keys do not match the contract"
    )

    assert "exact keys story_spine and ending_kind" in feedback
    assert "cliffhanger, consequence, or open_question" in feedback

def test_narration_retry_feedback_retargets_uncompactable_position_vector():
    module = _module()

    feedback = module._narration_retry_feedback(
        "cloud.narrative_repair_micro_compaction_unavailable",
        observed_word_count=130,
    )

    assert "same locked positions" in feedback
    assert "115-120 lexical words" in feedback
    assert "word_budget_max as a hard ceiling" in feedback
    assert "passage_word_budget_max" in feedback
    assert "130 lexical words" in feedback
    assert "claim" in feedback and "evidence" in feedback

def test_narration_retry_feedback_uses_adaptive_capacity_locked_bounds():
    module = _module()

    feedback = module._narration_retry_feedback(
        "cloud.narrative_repair_position_budget_invalid",
        observed_word_count=91,
        target_word_min=56,
        target_word_max=72,
        target_word_count=64,
        capacity_locked=True,
    )

    assert "aim near 64 lexical words total" in feedback
    assert "supplied 56-72 range" in feedback
    assert "position word_budget is a drafting target" in feedback
    assert "115-120 lexical words" not in feedback
    assert "91 lexical words" in feedback

def test_narration_retry_feedback_raises_adaptive_undershoot_without_filler():
    module = _module()
    feedback = module._narration_retry_feedback(
        "cloud.narrative_repair_position_budget_invalid",
        observed_word_count=53,
        target_word_min=56,
        target_word_max=72,
        target_word_count=64,
        capacity_locked=True,
    )
    assert "at least 56 lexical words" in feedback
    assert "aim near 64" in feedback
    assert "at or below 72" in feedback
    assert "grounded temporal or action detail" in feedback
    assert "never use filler" in feedback
    assert "53 lexical words" in feedback

def test_narration_retry_feedback_surgically_trims_only_over_ceiling_positions():
    module = _module()
    feedback = module._narration_retry_feedback(
        "cloud.narrative_repair_position_budget_invalid",
        observed_word_count=68,
        target_word_min=56,
        target_word_max=72,
        target_word_count=60,
        capacity_locked=True,
        failed_predicate="passage_word_budget",
        per_position_word_counts=[13, 25, 9, 11, 10],
        expected_ranges=[
            {"max": 18}, {"max": 27}, {"max": 9}, {"max": 9}, {"max": 9}
        ],
    )
    assert "Shorten ONLY these over-ceiling positions" in feedback
    assert "position 3: 11>9" in feedback
    assert "position 4: 10>9" in feedback
    assert "position 0" not in feedback
    assert "position 1" not in feedback
    assert "do not shorten compliant positions" in feedback
    assert "at or above 56 words" in feedback

def test_narration_retry_feedback_preserves_exact_position_count():
    module = _module()
    feedback = module._narration_retry_feedback(
        "cloud.narrative_repair_position_contract_invalid"
    )
    assert "exactly one rewrite string for every supplied ordered position" in feedback
    assert "do not merge or omit positions" in feedback
    assert "rewrites array" in feedback
    assert module.NARRATION_REPAIR_POSITION_MAX_ATTEMPTS == 3

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

    assert caught.value.code == "cloud.narrative_duration_out_of_range"
    assert runner._last_narration_result is invalid

def test_run_narration_selected_story_tracks_selected_visual_hash(monkeypatch):
    module = _module()
    panels = _panels(module, "selected-story-visual-hash")
    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=_identity(module),
        max_attempts=1,
    )
    visual = runner.run_visual_evidence(panels)
    story_map = runner.run_story_map(visual)
    selected_panel_ids = tuple(panel.panel_id for panel in panels[:2])
    selected_set = set(selected_panel_ids)
    beat_ids = tuple(
        str(beat["beat_id"])
        for beat in story_map.beats
        if selected_set.intersection(str(value) for value in beat.get("panel_ids", ()))
    )
    claim_ids = tuple(
        str(claim["claim_id"])
        for claim in story_map.claims
        if selected_set.intersection(
            str(value)
            for value in claim.get("evidence_panel_ids", claim.get("panel_ids", ()))
        )
    )
    selection = module.EditorialSelection(
        beat_ids=beat_ids,
        panel_ids=selected_panel_ids,
        claim_ids=claim_ids,
        beat_scores=(),
        selection_hash=module._hash(
            {"beats": beat_ids, "panels": selected_panel_ids, "claims": claim_ids}
        ),
    )
    monkeypatch.setattr(module, "select_editorial_beats", lambda *_args, **_kwargs: selection)

    def stop_after_selection(_prompt, _source, _obs, _struct, selected_story, selected_visual, **_kwargs):
        assert selected_visual.visual_evidence_hash != visual.visual_evidence_hash
        assert selected_story.visual_evidence_hash == selected_visual.visual_evidence_hash
        raise module.CloudStageError("test.stop")

    monkeypatch.setattr(runner, "_run_narration_batched", stop_after_selection)
    with pytest.raises(module.CloudStageError) as caught:
        runner.run_narration(visual, story_map, panels=panels)
    assert caught.value.code == "test.stop"

def test_narration_contract_diagnostic_keeps_only_field_and_count():
    module = _module()
    diagnostic = module._safe_narration_contract_diagnostic(
        "script passage text leaked a private value",
        {"script_passages": [{}, {}], "observations": [{}, {}], "evidence_graph": {"claims": [{}]}},
    )
    assert diagnostic == "field=script_passages;count=2"
    assert "private" not in diagnostic

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

def test_repair_only_identity_bump_normalizes_targeted_narration_candidate(monkeypatch):
    module = _module()
    base = _identity(module)
    current = replace(
        base,
        prompt_versions=dict(base.prompt_versions)
        | {"visual_narrative_repair": module.CURRENT_VISUAL_REPAIR_PROMPT_VERSION},
    )
    legacy = replace(
        current,
        prompt_versions=dict(current.prompt_versions)
        | {"visual_narrative_repair": module.LEGACY_VISUAL_REPAIR_PROMPT_VERSION},
    )
    runner = module.CloudStageRunner(provider=_FakeProvider(), model_identity=current)
    visual_prompt = runner.prompts["visual"]
    story_prompt = runner.prompts["story_map"]
    narration_prompt = runner.prompts["narration"]
    visual = module.VisualStageResult(
        panels=(),
        source_hash="visual-source",
        model_identity_hash=current.identity_hash,
        prompt_version=visual_prompt[0],
        prompt_sha256=visual_prompt[1],
    )
    story_map = module.StoryMapResult(
        panel_ids=(),
        beats=(),
        causal_chain=(),
        claims=(),
        story_map_hash="story-hash",
        model_identity_hash=current.identity_hash,
        prompt_version=story_prompt[0],
        prompt_sha256=story_prompt[1],
        visual_evidence_hash=visual.visual_evidence_hash,
    )
    candidate = module.NarrationResult(
        spoken_text="Grounded words remain unchanged.",
        display_words=("GROUNDED", "WORDS", "REMAIN", "UNCHANGED"),
        passages=(),
        ending_kind="consequence",
        word_count=130,
        estimated_duration_s=56.52,
        qc_report={},
        model_identity_hash=legacy.identity_hash,
        prompt_version=narration_prompt[0],
        prompt_sha256=narration_prompt[1],
        visual_evidence_hash=visual.visual_evidence_hash,
    )
    captured = []
    monkeypatch.setattr(
        runner,
        "_compact_narration_repair_context",
        lambda normalized, _visual, _story: (
            (captured.append(normalized) or _visual),
            _story,
        ),
    )
    monkeypatch.setattr(
        runner,
        "_narration_contract_failures",
        lambda _candidate: ("cloud.narrative_duration_out_of_range",),
    )
    monkeypatch.setattr(
        runner,
        "_run_targeted_narration_repair",
        lambda _prompt, _source, _obs, _struct, _story, _visual, normalized, _failures: normalized,
    )

    result = runner.run_narration_repair_candidate(candidate, visual, story_map)

    assert captured
    assert captured[0].model_identity_hash == current.identity_hash
    assert result.model_identity_hash == current.identity_hash
    assert result.spoken_text == candidate.spoken_text

def test_visual_repair_ending_canonicalization_preserves_passage_text():
    module = _module()
    passages = ({"text": "What could the visible change mean?"},)
    outline = {
        "story_spine": {"unresolved_question": "What could it mean?"},
        "ending_kind": "consequence",
    }
    normalized, provenance = module._canonicalize_visual_repair_ending(outline, passages)
    assert normalized["ending_kind"] == "open_question"
    assert passages[0]["text"] == "What could the visible change mean?"
    assert provenance["to"] == "open_question"

