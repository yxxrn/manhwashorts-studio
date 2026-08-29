"""Cloud multimodal regression tests grouped by responsibility."""

from __future__ import annotations

from tests.cloud.mass_support import (
    _FakeProvider,
    _identity,
    _immutable_slot_fixture,
    _micro_compaction_rewrite_texts,
    _module,
    _narrative_output,
    _panels,
    _position_rewrite_text,
    _provider_position_vector,
    _repair_identity_metadata,
    _visual_row,
    importlib,
    json,
    pytest,
    replace,
)


def test_locked_story_budget_normalizer_rebalances_real_passage_skew():
    module = _module()
    rewrites = (
        "An unexpected clash of weapons erupts high above with bursts of light and sudden motion effects around them.",
        "A brown-haired boy stands near a blonde girl. She raises her hand. An adult carries a child. Later she appears with a glowing cat by a dark-haired man.",
        "The boy and girl settle back to pick a show. She reacts with wide eyes. The girl sits at a desk. Surprise crosses her face.",
        "A blue symbol glows near one figure. The blonde returns in new poses. Her face shows distress.",
        "Sword bearers move through sparks and light. Hands grip a small object near symbols. Outside the building stands quiet. Inside someone reaches into a bag nearby.",
    )
    positions = [{"passage_id": f"p{index}"} for index in range(5)]
    registry = {
        "provider_context_mode": "locked_story_text_only",
        "passage_word_budgets": {f"p{i}": value for i, value in enumerate((18, 27, 26, 18, 26))},
        "selected_story_context": [
            {"passage_index": 0, "incoming_bridge": {"kind": "hook_teaser"}},
            {"passage_index": 1, "incoming_bridge": {"kind": "teaser_rewind"}},
            {"passage_index": 2, "incoming_bridge": {"kind": "temporal_only"}},
            {"passage_index": 3, "incoming_bridge": {"kind": "temporal_only"}},
            {"passage_index": 4, "incoming_bridge": {"kind": "temporal_only"}},
        ],
    }
    normalized, metadata = module._normalize_locked_story_budget(rewrites, positions, registry)
    counts = [module.script.narration_word_count(text) for text in normalized]
    assert counts == [18, 27, 26, 18, 26]
    assert sum(counts) == 115
    assert metadata["failed_predicate"] is None
    assert metadata["operation_count"] == 4
    assert normalized[2].startswith("Later, the ")
    assert normalized[3].startswith("Later, a ")

def test_locked_story_adaptive_normalizer_never_breaks_hair_possessive_noun_phrase():
    module = _module()
    text = "Brown-haired man's eyes widen as blue-haired woman watches concerned"
    positions = [{"passage_id": "p0"}]
    registry = {
        "provider_context_mode": "locked_story_text_only",
        "passage_word_targets": {"p0": 7},
        "passage_word_budgets": {"p0": 7},
        "selected_story_context": [{"passage_index": 0, "incoming_bridge": {"kind": "temporal_only"}}],
    }
    normalized, metadata = module._normalize_locked_story_budget((text,), positions, registry)
    assert normalized == (text,)
    assert "Brown-haired man's" in normalized[0]
    assert metadata["failed_predicate"] in {"normalization_ceiling_delta_window", "normalization_trim_unavailable"}

def test_targeted_repair_prompt_declares_exact_slot_wire_shape():
    module = _module()
    instruction = module.NARRATION_REPAIR_INSTRUCTION
    assert '{"rewrites": ["text for position 0", "..."]}' in instruction
    assert "never return, create, or rewrite claim ids" in instruction.lower()
    assert "approximately 120 total words" in instruction
    assert "word_budget_min/word_budget_max" in instruction
    assert "third-person narrator language" in instruction
    assert "never quote or preserve a four-word lexical sequence" in instruction
    assert "renaming a speaker are not loopholes" in instruction

def test_targeted_repair_prompt_distinguishes_normal_and_capacity_locked_budgets():
    module = _module()
    instruction = module.NARRATION_REPAIR_INSTRUCTION
    assert "normal repair mode" in instruction
    assert "drafting guidance" in instruction
    assert "CAPACITY-LOCKED WORD BUDGET MODE" in instruction
    assert "drafting targets" in instruction
    assert "not exact quotas" in instruction
    assert "hard ceiling" in instruction

def test_targeted_repair_prompt_requires_grounded_locked_scope():
    module = _module()
    instruction = module.NARRATION_REPAIR_INSTRUCTION
    assert "trusted lineage" in instruction
    assert "Recount every position" in instruction
    assert "third-person narrator language" in instruction
    assert "never mix a same-chapter panel from another section" in instruction

def test_targeted_repair_prompt_keeps_global_acceptance_bounds():
    module = _module()
    instruction = module.NARRATION_REPAIR_INSTRUCTION
    assert "total 115-125 words" in instruction
    assert "50-60 seconds" in instruction

def test_visual_text_only_position_registry_preserves_passage_locked_evidence_subset():
    module = _module()
    runner, candidate, visual, story_map = _immutable_slot_fixture(module)
    panel_ids = list(visual.panel_ids[:4])
    claims = []
    passages = []
    budgets = {}
    for index, panel_id in enumerate(panel_ids):
        claim_id = f"immutable-claim-{index}-0"
        base_claim = next(claim for claim in story_map.claims if claim["claim_id"] == claim_id)
        claim = dict(base_claim)
        claim["panel_ids"] = [panel_id, visual.panel_ids[(index + 1) % 4]]
        claim["evidence_panel_ids"] = list(claim["panel_ids"])
        claims.append(claim)
        passage_id = f"locked-passage-{index}"
        passages.append({
            "passage_id": passage_id,
            "editorial_role": "causal_turn",
            "text": f"Grounded passage {index} stays on its selected panel.",
            "claim_ids": [claim_id],
            "evidence_panel_ids": [panel_id],
        })
        budgets[passage_id] = 30
    locked_candidate = replace(candidate, passages=tuple(passages), evidence_graph={"claims": claims})
    locked_story_map = replace(story_map, claims=tuple(claims))
    registry = runner._build_narration_repair_position_registry(
        locked_candidate, locked_story_map, passage_word_budgets=budgets
    )
    assert registry["evidence_closure"]["evidence_scope_mode"] == "candidate_passage_locked"
    assert len(registry["positions"]) == len(passages)
    expected = {row["passage_id"]: row for row in passages}
    for row in registry["positions"]:
        original = expected[row["passage_id"]]
        assert row["claim_ids"] == original["claim_ids"]
        assert row["evidence_panel_ids"] == original["evidence_panel_ids"]
        assert row["word_budget"] == budgets[row["passage_id"]]
        assert row["word_budget_min"] == row["word_budget"]
        assert row["word_budget_max"] == row["word_budget"]

def test_capacity_locked_position_registry_allows_only_first_hook_out_of_order():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    reordered = (candidate.passages[-1], *candidate.passages[:-1])
    teaser = replace(candidate, passages=reordered)
    base, remainder = divmod(120, len(reordered))
    budgets = {
        str(row["passage_id"]): base + (1 if index < remainder else 0)
        for index, row in enumerate(reordered)
    }

    registry = runner._build_narration_repair_position_registry(
        teaser, story_map, passage_word_budgets=budgets
    )
    positions = registry["positions"]
    causal = [row["causal_position"] for row in positions]
    assert causal[0] > causal[1]
    assert causal[1:] == sorted(causal[1:])
    assert registry["allow_hook_teaser"] is True

    with pytest.raises(module.CloudStageError) as exc:
        runner._build_narration_repair_position_registry(teaser, story_map)
    assert exc.value.code == "cloud.narrative_repair_position_order_invalid"

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

def test_position_registry_accepts_five_grounded_passages_with_three_claims():
    module = _module()
    runner, candidate, visual, story_map = _immutable_slot_fixture(module)
    claims = [dict(claim) for claim in story_map.claims[::2][:3]]
    panel_ids = [str(panel_id) for panel_id in visual.panel_ids[:3]]
    passages = tuple(
        {
            "passage_id": f"small-passage-{index}",
            "editorial_role": "causal_turn",
            "text": f"The grounded turn {index} changes what follows.",
            "claim_ids": [claims[claim_index]["claim_id"]],
            "evidence_panel_ids": [panel_ids[claim_index]],
        }
        for index, (claim_index, _) in enumerate(
            ((0, 0), (0, 0), (1, 1), (2, 2), (2, 2))
        )
    )
    small_candidate = replace(
        candidate,
        passages=passages,
        evidence_graph={"claims": claims},
        word_count=129,
        estimated_duration_s=56.09,
    )
    small_story_map = replace(
        story_map,
        beats=tuple(story_map.beats[:3]),
        causal_chain=tuple(story_map.causal_chain[:2]),
        claims=tuple(claims),
    )

    registry = runner._build_narration_repair_position_registry(
        small_candidate,
        small_story_map,
    )

    assert len(registry["positions"]) == 5
    assert len({row["passage_id"] for row in registry["positions"]}) == 5
    assert len({claim_id for row in registry["positions"] for claim_id in row["claim_ids"]}) == 3
    assert sum(row["word_budget"] for row in registry["positions"]) == 120

def test_position_registry_uses_candidate_claim_scope_not_all_story_claims():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    passages = []
    retained_claims = []
    for index, original in enumerate(candidate.passages[:5]):
        passage = dict(original)
        claim_ids = list(original["claim_ids"] if index < 2 else original["claim_ids"][:1])
        passage["claim_ids"] = claim_ids
        passages.append(passage)
        retained_claims.extend(claim_ids)
    candidate_claims = [
        dict(claim)
        for claim in candidate.evidence_graph["claims"]
        if claim["claim_id"] in set(retained_claims)
    ]
    candidate_with_seven_claims = replace(
        candidate,
        passages=tuple(passages),
        evidence_graph={"claims": candidate_claims},
    )
    story_claims = tuple(dict(claim) for claim in story_map.claims[:10])
    story_with_ten_claims = replace(story_map, claims=story_claims)

    registry = runner._build_narration_repair_position_registry(
        candidate_with_seven_claims,
        story_with_ten_claims,
    )

    assert len(registry["positions"]) == 7
    assert len({claim_id for row in registry["positions"] for claim_id in row["claim_ids"]}) == 7
    assert len({row["passage_id"] for row in registry["positions"]}) == 5
    assert sum(row["word_budget"] for row in registry["positions"]) == 120

def test_position_registry_maxima_cannot_exceed_final_word_bound():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)

    registry = runner._build_narration_repair_position_registry(candidate, story_map)

    assert sum(row["word_budget_max"] for row in registry["positions"]) <= 125

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
    assert "normal repair mode" in instruction
    assert "drafting guidance" in instruction

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

def test_visual_repair_text_only_duration_fallback_locks_passage_scope(monkeypatch):
    module = _module()
    from types import SimpleNamespace

    runner = module.CloudStageRunner(
        provider=SimpleNamespace(model_id=_identity(module).model),
        model_identity=_identity(module),
        max_attempts=1,
    )
    passage = {
        "passage_id": "visual-repair-p1",
        "editorial_role": "hook",
        "text": "Grounded words stay tied to the same visible evidence.",
        "claim_ids": ["claim-1"],
        "evidence_panel_ids": ["panel-1"],
    }
    spoken = passage["text"]
    candidate = module.NarrationResult(
        spoken_text=spoken,
        display_words=module.derive_display_words(spoken),
        passages=(passage,),
        ending_kind="consequence",
        word_count=137,
        estimated_duration_s=59.57,
        observations=(),
        continuity_ledger={},
        evidence_graph={"claims": [{
            "claim_id": "claim-1",
            "claim_type": "fact",
            "text": "Grounded visible fact.",
            "qualification": "Visible evidence supports it.",
            "evidence_panel_ids": ["panel-1"],
        }]},
        story_spine={},
        qc_report={},
        model_identity_hash=runner.model_identity.identity_hash,
        prompt_version=runner.prompts["visual_narrative_repair"][0],
        prompt_sha256=runner.prompts["visual_narrative_repair"][1],
        visual_evidence_hash="visual-hash",
    )
    repaired = replace(
        candidate,
        word_count=120,
        estimated_duration_s=52.17,
        qc_report={"narration_repair": {"scope": "position_locked_rewrite_vector"}},
    )
    captured = {}

    def fake_targeted(*args, allow_passage_removal=True, **kwargs):
        captured["source"] = args[1]
        captured["failure_codes"] = args[7]
        captured["allow_passage_removal"] = allow_passage_removal
        captured["passage_word_budgets"] = kwargs.get("passage_word_budgets")
        captured["passage_word_targets"] = kwargs.get("passage_word_targets")
        return repaired

    monkeypatch.setattr(runner, "_run_targeted_narration_repair", fake_targeted)
    result = runner._run_visual_repair_text_only_duration_repair(
        visual_repair_prompt=runner.prompts["visual_narrative_repair"],
        source={"ledger_hash": "ledger"},
        observations=({
            "panel_id": "panel-1",
            "visible_facts": ["Two grounded figures hold swords."],
            "uncertainties": ["Their intent is not visually confirmed."],
        },),
        structural={},
        story_map=SimpleNamespace(),
        visual=SimpleNamespace(visual_evidence_hash="visual-hash"),
        candidate=candidate,
        capacity_plan={"rows": [{"target_lexical_words": 120, "max_lexical_words": 125, "evidence_panel_ids": ["panel-1"]}]},
    )

    assert captured["failure_codes"] == ("cloud.narrative_duration_out_of_range",)
    assert captured["passage_word_targets"] == {"visual-repair-p1": 120}
    assert captured["passage_word_budgets"] == {"visual-repair-p1": 125}
    assert captured["allow_passage_removal"] is False
    evidence_context = captured["source"]["selected_evidence_context"]
    assert evidence_context == [{
        "passage_index": 0,
        "passage_id": "visual-repair-p1",
        "evidence_panel_ids": ["panel-1"],
        "panels": [{
            "panel_id": "panel-1",
            "visible_facts": ["Two grounded figures hold swords."],
            "uncertainties": ["Their intent is not visually confirmed."],
        }],
    }]
    assert result.prompt_version == runner.prompts["visual_narrative_repair"][0]
    assert result.prompt_sha256 == runner.prompts["visual_narrative_repair"][1]
    marker = result.qc_report["visual_repair_text_only_duration_repair_v1"]
    assert marker["scope"] == "text_only_locked_claim_evidence"
    assert marker["passage_removal_allowed"] is False
    assert marker["candidate_word_count"] == 137
    assert marker["result_word_count"] == 120

def test_visual_repair_text_only_retries_stiff_style_once_with_locked_scope(monkeypatch):
    module = _module()
    from types import SimpleNamespace

    runner = module.CloudStageRunner(
        provider=SimpleNamespace(model_id=_identity(module).model),
        model_identity=_identity(module),
        max_attempts=1,
    )
    passage = {
        "passage_id": "visual-repair-p1",
        "editorial_role": "hook",
        "text": "Grounded words stay tied to the same visible evidence.",
        "claim_ids": ["claim-1"],
        "evidence_panel_ids": ["panel-1"],
    }
    spoken = passage["text"]
    candidate = module.NarrationResult(
        spoken_text=spoken,
        display_words=module.derive_display_words(spoken),
        passages=(passage,),
        ending_kind="consequence",
        word_count=120,
        estimated_duration_s=52.17,
        observations=(),
        continuity_ledger={},
        evidence_graph={"claims": [{
            "claim_id": "claim-1",
            "claim_type": "fact",
            "text": "Grounded visible fact.",
            "qualification": "Visible evidence supports it.",
            "evidence_panel_ids": ["panel-1"],
        }]},
        story_spine={},
        qc_report={},
        model_identity_hash=runner.model_identity.identity_hash,
        prompt_version=runner.prompts["visual_narrative_repair"][0],
        prompt_sha256=runner.prompts["visual_narrative_repair"][1],
        visual_evidence_hash="visual-hash",
    )
    calls = []

    def fake_targeted(*args, allow_passage_removal=True, **kwargs):
        calls.append({
            "candidate": args[6],
            "failure_codes": args[7],
            "allow_passage_removal": allow_passage_removal,
            "budgets": kwargs.get("passage_word_budgets"),
            "targets": kwargs.get("passage_word_targets"),
        })
        return replace(
            args[6],
            qc_report={"narration_repair": {"scope": "position_locked_rewrite_vector"}},
        )

    style_checks = {"count": 0}

    def fake_style_check(*_args, **_kwargs):
        style_checks["count"] += 1
        if style_checks["count"] == 1:
            raise module.visual_narrative_repair.VisualNarrativeRepairError(
                "narration uses stiff bureaucratic spoken prose",
                "cloud.narrative_style_stiff",
            )
        return {"status": "pass"}

    monkeypatch.setattr(runner, "_run_targeted_narration_repair", fake_targeted)
    monkeypatch.setattr(
        module.visual_narrative_repair,
        "validate_repaired_hook_quality",
        fake_style_check,
    )
    result = runner._run_visual_repair_text_only_duration_repair(
        visual_repair_prompt=runner.prompts["visual_narrative_repair"],
        source={"ledger_hash": "ledger"},
        observations=({
            "panel_id": "panel-1",
            "visible_facts": ["Grounded visible fact."],
            "uncertainties": [],
        },),
        structural={},
        story_map=SimpleNamespace(),
        visual=SimpleNamespace(visual_evidence_hash="visual-hash"),
        candidate=candidate,
        capacity_plan={"rows": [{
            "target_lexical_words": 120,
            "max_lexical_words": 125,
            "evidence_panel_ids": ["panel-1"],
        }]},
    )

    assert len(calls) == 2
    assert calls[0]["failure_codes"] == ("cloud.narrative_duration_out_of_range",)
    assert calls[1]["failure_codes"] == ("cloud.narrative_style_stiff",)
    assert all(call["allow_passage_removal"] is False for call in calls)
    assert all(call["budgets"] == {"visual-repair-p1": 125} for call in calls)
    assert all(call["targets"] == {"visual-repair-p1": 120} for call in calls)
    assert result.qc_report["visual_repair_text_only_duration_repair_v1"]["style_retry_count"] == 1

def test_micro_expansion_rescues_narrow_undershoot_without_new_content():
    module = _module()
    source = (
        "They'll move when they're ready, but they won't rush. "
        "We haven't seen why they couldn't wait, and I'm still watching. "
        "You've got the same evidence, so don't change what it means. "
        "She'll stay close while we're following the visible turn. "
        "They've already chosen, and we won't invent anything else. "
        "You're seeing the same grounded event while they aren't moving back. "
        "We couldn't add facts, so we'll only expand safe contractions. "
        "I haven't changed any claim or panel evidence here. "
        "They're still following what we've already seen, and they won't add new motives. "
        "We're keeping the same visible sequence, so you won't get invented context. "
        "They'll describe what's grounded while we don't alter the evidence."
    )
    rewrites = (source,)
    before = module.script.narration_word_count(source)
    assert 109 <= before <= 114

    expanded, metadata = module._micro_expand_rewrites(
        rewrites, total_words=before
    )

    assert metadata["version"] == module.NARRATION_MICRO_EXPANSION_VERSION
    assert metadata["applied"] is True
    assert metadata["failed_predicate"] is None
    assert metadata["after_word_count"] >= 115
    assert metadata["after_word_count"] <= 125
    assert "She will" in expanded[0]
    assert metadata["operation_count"] >= 1

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

def test_micro_compaction_accepts_131_when_safe_contractions_can_reach_limit():
    module = _module()
    phrases = ("it is", "does not", "will not", "have not", "there is", "that is")
    counts = (17, 17, 17, 16, 16, 16, 16, 16)
    rewrites = []
    for index, count in enumerate(counts):
        prefix = phrases[index] if index < len(phrases) else ""
        used = 2 if prefix else 0
        fillers = [f"safe{index}word{word_index}" for word_index in range(count - used)]
        rewrites.append(" ".join(part for part in (prefix, *fillers) if part))
    assert sum(module.script.narration_word_count(text) for text in rewrites) == 131

    compacted, metadata = module._micro_compact_rewrites(rewrites, total_words=131)

    assert metadata["applied"] is True
    assert metadata["failed_predicate"] is None
    assert metadata["after_word_count"] == 125
    assert len(metadata["operation_types"]) == 6
    assert sum(module.script.narration_word_count(text) for text in compacted) == 125

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

def test_capacity_locked_position_registry_separates_target_from_visual_ceiling():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    passage_ids = [str(item["passage_id"]) for item in candidate.passages]
    targets = dict.fromkeys(passage_ids, 24)
    ceilings = dict.fromkeys(passage_ids, 25)
    registry = runner._build_narration_repair_position_registry(
        candidate,
        story_map,
        passage_word_budgets=ceilings,
        passage_word_targets=targets,
    )
    assert registry["target_word_count"] == 120
    assert registry["passage_word_targets"] == targets
    assert registry["passage_word_budgets"] == ceilings
    assert all(int(row["word_budget"]) == 24 for row in registry["positions"])
    assert all(int(row["word_budget_max"]) == 25 for row in registry["positions"])

    counts = [25, 23, 24, 24, 24]
    raw = {
        "rewrites": [
            _position_rewrite_text(count, f"flex{index}_")
            for index, count in enumerate(counts)
        ]
    }
    output = runner._reconcile_narration_repair_vector(raw, registry, candidate)
    assert sum(module.script.narration_word_count(row["text"]) for row in output["script_passages"]) == 120

def test_locked_story_separate_targets_do_not_pad_text_to_exact_target():
    module = _module()
    rewrites = (
        "Clean direct wording stays comfortably below the visual ceiling today",
        "Natural narration remains concise without padding extra filler words here",
    )
    positions = [{"passage_id": "p0"}, {"passage_id": "p1"}]
    registry = {
        "provider_context_mode": "locked_story_text_only",
        "passage_word_targets": {"p0": 12, "p1": 12},
        "passage_word_budgets": {"p0": 14, "p1": 14},
        "selected_story_context": [
            {"passage_index": 0, "incoming_bridge": {"kind": "hook_teaser"}},
            {"passage_index": 1, "incoming_bridge": {"kind": "temporal_only"}},
        ],
    }
    normalized, metadata = module._normalize_locked_story_budget(rewrites, positions, registry)
    assert normalized == rewrites
    assert metadata["failed_predicate"] is None
    assert metadata["applied"] is False
    assert metadata["target_counts"] == {"p0": 12, "p1": 12}
    assert metadata["ceiling_counts"] == {"p0": 14, "p1": 14}

def test_locked_story_provider_context_omits_rejected_prior_prose():
    module = _module()
    _runner, candidate, _visual, _story_map = _immutable_slot_fixture(module)
    prior = module._narration_repair_provider_prior_context(
        candidate,
        locked_story_text_only=True,
    )
    assert prior["spoken_text_omitted"] is True
    assert "spoken_text" not in prior
    assert "display_words" not in prior
    assert "story_spine" not in prior
    assert all("text" not in passage for passage in prior["passages"])
    assert [passage["passage_id"] for passage in prior["passages"]] == [
        str(item["passage_id"]) for item in candidate.passages
    ]
    normal = module._narration_repair_provider_prior_context(
        candidate,
        locked_story_text_only=False,
    )
    assert normal["spoken_text"] == candidate.spoken_text
    assert normal["passages"][0]["text"] == candidate.passages[0]["text"]

def test_position_repair_enforces_explicit_passage_word_budgets():
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    passage_ids = [str(item["passage_id"]) for item in candidate.passages]
    base, remainder = divmod(120, len(passage_ids))
    budgets = {
        passage_id: base + (1 if index < remainder else 0)
        for index, passage_id in enumerate(passage_ids)
    }
    registry = runner._build_narration_repair_position_registry(
        candidate, story_map, passage_word_budgets=budgets
    )
    assert registry["passage_word_budgets"] == budgets
    counts = [int(row["word_budget"]) for row in registry["positions"]]
    first_passage = registry["positions"][0]["passage_id"]
    second_passage = next(
        row["passage_id"] for row in registry["positions"] if row["passage_id"] != first_passage
    )
    source_index = next(i for i, row in enumerate(registry["positions"]) if row["passage_id"] == first_passage)
    sink_index = next(i for i, row in enumerate(registry["positions"]) if row["passage_id"] == second_passage)
    counts[source_index] += 1
    counts[sink_index] -= 1
    raw = {"rewrites": [_position_rewrite_text(count, f"budget{index}_") for index, count in enumerate(counts)]}
    with pytest.raises(module.CloudStageError) as caught:
        runner._reconcile_narration_repair_vector(raw, registry, candidate)
    assert caught.value.code == "cloud.narrative_repair_position_budget_invalid"
    assert caught.value.safe_metadata["failed_predicate"] == "passage_word_budget"

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

def test_narration_retry_feedback_surgically_trims_micro_compaction_overshoot():
    module = _module()
    feedback = module._narration_retry_feedback(
        "cloud.narrative_repair_micro_compaction_unavailable",
        observed_word_count=127,
        target_word_min=115,
        target_word_max=125,
        target_word_count=120,
        per_position_word_counts=[14, 16, 15, 17, 15, 16, 17, 17],
        expected_ranges=[
            {"position": i, "target": 15, "min": 13, "max": 18}
            for i in range(8)
        ],
    )
    assert "Remove at least 7 lexical words" in feedback
    assert "position 1: 16>15 target" in feedback
    assert "do not compensate by expanding other positions" in feedback
    assert "115-125" in feedback

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
        == "vision-first-story-analyzer-v3-targeted-position-repair-v21"
    )
    assert len(repair_prompt_sha256) == 64
    assert "TARGETED NARRATION POSITION REPAIR" in repair_prompt_text
    assert "total 115-125 words" in repair_prompt_text
    assert "CAPACITY-LOCKED WORD BUDGET MODE" in repair_prompt_text
    assert "exactly one complete retained passage" in repair_prompt_text
    assert "passage_word_budget_max is a hard ceiling" in repair_prompt_text
    assert "supplied position word_budget values are drafting targets" in repair_prompt_text
    assert "Recount every position" in repair_prompt_text
    assert "never mix a same-chapter panel from another section" in repair_prompt_text
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
    candidate = replace(candidate, passages=tuple(candidate.passages[:3]))

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

    assert caught.value.code == "cloud.narrative_repair_slot_lineage_invalid"

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

