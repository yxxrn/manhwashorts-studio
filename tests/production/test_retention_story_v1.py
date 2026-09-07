from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_retention_profile_is_verified_and_non_question_ending():
    from app.services import narrative_identity as identity

    profile = identity.get_narrative_identity("retention_story_v1")
    assert profile.profile_version == "1.5.3"
    assert profile.allowed_ending_kinds == ("cliffhanger", "consequence")
    assert profile.prompt_version == "vision-first-retention-story-v2.6"
    version, digest, text = identity.load_narrative_instruction(profile.profile_id)
    assert version == profile.prompt_version
    assert len(digest) == 64
    lowered = text.lower()
    assert "one dominant story arc" in lowered
    assert "8-14" in lowered
    assert "must not be a rhetorical question" in lowered
    assert "arc selection itself must account for visual support" in lowered
    assert "factual delta" in lowered
    assert "fresh factual payoff" in lowered
    assert "do not invent a new claim" in lowered


def test_retention_contract_requires_short_single_sentence_hook():
    from app.services import analyzer_contract as contract
    from app.services import narrative_identity as identity

    profile = identity.get_narrative_identity("retention_story_v1")
    passages = [
        {
            "passage_id": "p1",
            "editorial_role": "hook",
            "text": "Jin wakes in the past with a bracelet that rewinds time.",
            "claim_ids": ["c1"],
            "evidence_panel_ids": ["panel-1"],
        },
        {
            "passage_id": "p2",
            "editorial_role": "pressure",
            "text": "He sees the same bully threatening his sect again.",
            "claim_ids": ["c2"],
            "evidence_panel_ids": ["panel-1"],
        },
        {
            "passage_id": "p3",
            "editorial_role": "decision",
            "text": "This time he steps forward instead of staying quiet.",
            "claim_ids": ["c3"],
            "evidence_panel_ids": ["panel-1"],
        },
        {
            "passage_id": "p4",
            "editorial_role": "cliffhanger",
            "text": "Then the bracelet reveals one more thing it can rewind.",
            "claim_ids": ["c4"],
            "evidence_panel_ids": ["panel-1"],
        },
    ]
    outline = {
        "ending_kind": "cliffhanger",
        "story_spine": dict.fromkeys(contract._STORY_SPINE_FIELDS, "grounded"),
    }
    contract._validate_script_passages_v3(
        passages,
        ("panel-1",),
        {"c1": {"panel-1"}, "c2": {"panel-1"}, "c3": {"panel-1"}, "c4": {"panel-1"}},
        [{"dialogue_or_ocr": []}],
        outline,
        profile,
    )

    passages[0] = {
        **passages[0],
        "text": "Jin wakes in the distant past carrying every memory and a mysterious bracelet that can somehow rewind time itself.",
    }
    with pytest.raises(contract.AnalyzerContractError, match="8-14 words"):
        contract._validate_script_passages_v3(
            passages,
        ("panel-1",),
        {"c1": {"panel-1"}, "c2": {"panel-1"}, "c3": {"panel-1"}, "c4": {"panel-1"}},
        [{"dialogue_or_ocr": []}],
        outline,
        profile,
        )




def test_retention_section_maps_merge_repeated_conflict_and_keep_story_text():
    from app.services import reference_visual_review as review

    script = SimpleNamespace(
        editorial_metadata={"narrative_identity": {"profile_id": "retention_story_v1"}},
        sections=[
            {
                "section": "hook",
                "text": "hook beat",
                "evidence_panel_ids": ["p1"],
                "citations": [1],
            },
            {
                "section": "conflict",
                "text": "first conflict beat",
                "evidence_panel_ids": ["p2"],
                "citations": [2],
            },
            {
                "section": "conflict",
                "text": "second conflict beat",
                "evidence_panel_ids": ["p3"],
                "citations": [3],
            },
        ],
    )
    evidence, citations, _beats = review.section_evidence_maps(script)
    story = review.section_story_text_map(script)
    assert evidence["conflict"] == ("p2", "p3")
    assert citations["conflict"] == (2, 3)
    assert story["conflict"] == ("first conflict beat", "second conflict beat")


def test_retention_story_relevance_precedes_visual_fit_for_unused_candidates():
    from app.services import editorial_visual_planner as planner

    weak_features = SimpleNamespace(
        face_visibility=0.1,
        facial_expression=0.1,
        action_pose=0.1,
        impact_frame=0.1,
        dramatic_composition=0.1,
        weapons=0.0,
        visual_effects=0.0,
        close_up=0.0,
        scenery_only=0.0,
    )
    strong_features = SimpleNamespace(
        face_visibility=1.0,
        facial_expression=1.0,
        action_pose=1.0,
        impact_frame=1.0,
        dramatic_composition=1.0,
        weapons=1.0,
        visual_effects=1.0,
        close_up=0.0,
        scenery_only=0.0,
    )
    story_match = SimpleNamespace(
        panel_id="story",
        panel_region_id="r1",
        source_order=20,
        story_relevance_by_section={"conflict": 2.0},
        panel_candidate=SimpleNamespace(features=weak_features, source_family="ch1"),
    )
    pretty_only = SimpleNamespace(
        panel_id="pretty",
        panel_region_id="r2",
        source_order=10,
        story_relevance_by_section={"conflict": 0.0},
        panel_candidate=SimpleNamespace(features=strong_features, source_family="ch1"),
    )
    ranked = sorted(
        [pretty_only, story_match],
        key=lambda item: planner._review_candidate_priority_key(item, {}, "conflict", "action"),
    )
    assert ranked[0] is story_match


def test_unattended_runner_exposes_opt_in_retention_profile():
    from pathlib import Path

    source = Path("scripts/production_run.py").read_text(encoding="utf-8")
    assert 'parser.add_argument("--narrative-profile-id", default="")' in source
    assert 'narrative_profile_id=(getattr(args, "narrative_profile_id", "") or None)' in source
    assert "run state identity does not match requested narrative profile" in source


def test_retention_synthesis_wire_does_not_reintroduce_legacy_fixed_roles():
    from app.services import narrative_identity as identity
    from app.services import vision_adapter

    profile = identity.get_narrative_identity("retention_story_v1")
    version, digest, instruction = identity.load_narrative_instruction(profile.profile_id)
    request = vision_adapter.VisionChapterSynthesisRequest(
        analysis_run_id="retention-wire-test",
        instruction_version=version,
        instruction_sha256=digest,
        instruction_text=instruction,
        expected_panel_ids=("panel-1",),
        coverage_manifest={"processed_panels": 1},
        ordered_observations=(
            {
                "panel_id": "panel-1",
                "visible_facts": ["A fighter blocks a strike."],
                "dialogue_or_ocr": [],
                "inferences": [],
                "uncertainties": [],
                "evidence_refs": ["panel-1"],
            },
        ),
        chunks=({"chunk_id": "chunk-1", "panel_ids": ["panel-1"]},),
        narrative_profile_id=profile.profile_id,
        narrative_profile_version=profile.profile_version,
        narrative_profile_sha256=profile.contract_sha256,
        target_word_count_min=profile.target_word_min,
        target_word_count_max=profile.target_word_max,
        preferred_visual_panel_ids=("panel-1",),
    )

    payload = vision_adapter._build_synthesis_payload(
        request, request.expected_panel_ids, "mock-model", profile
    )
    user_instruction = payload["messages"][1]["content"]

    assert "script_passages must contain four to six passages" in user_instruction
    assert "first passage is the hook" in user_instruction
    assert "ending_kind must be cliffhanger or consequence" in user_instruction
    assert "without ending in a question mark" in user_instruction
    assert "script_passages must contain exactly five passages" not in user_instruction
    assert "payoff_open_loop must end with an evidence-grounded question" not in user_instruction


def test_dialogue_copy_synthesis_retries_as_locked_paraphrase(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import (
        VisionChapterSynthesisRequest,
        VisionResponseInvalid,
    )

    locked = (
        {
            "passage_id": "p1",
            "editorial_role": "hook",
            "text": "Copied source wording appears in this rejected passage.",
            "claim_ids": ["c1"],
            "evidence_panel_ids": ["panel-1"],
        },
    )
    request = VisionChapterSynthesisRequest(
        analysis_run_id="dialogue-retry-test",
        instruction_version="test-v1",
        instruction_sha256="a" * 64,
        instruction_text="test",
        expected_panel_ids=("panel-1",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            if len(self.requests) == 1:
                raise VisionResponseInvalid(
                    validation_subtype="script_passage_copies_source_dialogue",
                    passage_word_counts=(8,),
                    retry_passages=locked,
                )
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(
        pipeline, "_validate_synthesis_subtitle_admission", lambda output, active_request: None
    )
    monkeypatch.setattr(
        pipeline,
        "_validated_synthesis_cache_output",
        lambda output, active_request: output,
    )

    result = pipeline._synthesize_with_cache(provider, request)

    assert result == {"accepted": True}
    assert len(provider.requests) == 2
    retry_request = provider.requests[1]
    assert retry_request.retry_dialogue_paraphrase is True
    assert retry_request.retry_word_counts is None
    assert retry_request.retry_passages == locked


def test_anti_copy_retry_clears_causal_rebuild_mode(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    locked = (
        {
            "passage_id": "p1",
            "editorial_role": "hook",
            "text": "Grounded hook text stays fixed.",
            "claim_ids": ["c1"],
            "evidence_panel_ids": ["a"],
        },
        {
            "passage_id": "p2",
            "editorial_role": "setup",
            "text": "Grounded setup text stays fixed.",
            "claim_ids": ["c2"],
            "evidence_panel_ids": ["a"],
        },
        {
            "passage_id": "p3",
            "editorial_role": "payoff",
            "text": "Copied source phrase must be paraphrased.",
            "claim_ids": ["c3"],
            "evidence_panel_ids": ["a"],
        },
    )
    request = VisionChapterSynthesisRequest(
        analysis_run_id="anti-copy-chain",
        instruction_version="v",
        instruction_sha256="a" * 64,
        instruction_text="x",
        expected_panel_ids=("a",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, r):
            self.requests.append(r)
            if len(self.requests) == 1:
                raise VisionResponseInvalid(
                    validation_subtype="retention_passage_introduces_disconnected_claim",
                    retry_passages=locked,
                )
            if len(self.requests) == 2:
                raise VisionResponseInvalid(
                    validation_subtype="script_passage_copies_source_dialogue",
                    retry_passages=locked,
                )
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_: None)
    monkeypatch.setattr(
        pipeline, "_validated_synthesis_cache_output", lambda output, _request: output
    )
    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}
    retry = provider.requests[2]
    assert retry.retry_dialogue_paraphrase is True
    assert retry.retry_causal_arc is False
    assert retry.retry_claim_semantic_grounding is False
    assert retry.retry_visual_story_alignment is False
    assert retry.retry_projection_contract is False
    assert retry.retry_passages == locked


def test_retention_hook_contract_failure_retries_as_locked_text_repair(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    locked = tuple(
        {
            "passage_id": f"p{index}",
            "editorial_role": "hook" if index == 1 else f"beat_{index}",
            "text": "This rejected narration keeps its grounded meaning while the hook is repaired.",
            "claim_ids": ["c1"],
            "evidence_panel_ids": ["panel-1"],
        }
        for index in range(1, 6)
    )
    counts = (19, 24, 24, 24, 24)
    request = VisionChapterSynthesisRequest(
        analysis_run_id="retention-hook-retry-test",
        instruction_version="test-v1",
        instruction_sha256="d" * 64,
        instruction_text="test",
        expected_panel_ids=("panel-1",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
        narrative_profile_id="retention_story_v1",
        target_word_count_min=115,
        target_word_count_max=125,
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            if len(self.requests) == 1:
                raise VisionResponseInvalid(
                    validation_subtype="retention_hook_must_contain_8-14_words",
                    passage_word_counts=counts,
                    retry_passages=locked,
                )
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_args: None)
    monkeypatch.setattr(
        pipeline,
        "_validated_synthesis_cache_output",
        lambda output, _request: output,
    )

    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}
    assert len(provider.requests) == 2
    retry = provider.requests[1]
    assert retry.retry_word_counts == counts
    assert retry.retry_passages == locked
    assert retry.retry_visual_selection is False
    assert retry.retry_local_claim_grounding is False


def test_claim_qualification_failure_retries_synthesis_without_reusing_bad_passages(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import (
        VisionChapterSynthesisRequest,
        VisionResponseInvalid,
    )

    request = VisionChapterSynthesisRequest(
        analysis_run_id="qualification-retry-test",
        instruction_version="test-v1",
        instruction_sha256="b" * 64,
        instruction_text="test",
        expected_panel_ids=("panel-1",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            if len(self.requests) == 1:
                raise VisionResponseInvalid(
                    validation_subtype="claim_qualification_must_be_a_non-empty_string",
                    passage_word_counts=(13, 19, 23, 22),
                )
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(
        pipeline, "_validate_synthesis_subtitle_admission", lambda output, active_request: None
    )
    monkeypatch.setattr(
        pipeline,
        "_validated_synthesis_cache_output",
        lambda output, active_request: output,
    )

    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}
    retry_request = provider.requests[1]
    assert retry_request.retry_claim_qualification is True
    assert retry_request.retry_word_counts is None
    assert retry_request.retry_passages is None


def test_visual_retry_then_dialogue_retry_switches_to_paraphrase_mode(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    locked = (
        {
            "passage_id": "p1",
            "editorial_role": "hook",
            "text": "Source wording remains too close in this passage.",
            "claim_ids": ["c1"],
            "evidence_panel_ids": ["panel-1"],
        },
    )
    request = VisionChapterSynthesisRequest(
        analysis_run_id="retry-mode-transition-test",
        instruction_version="test-v1",
        instruction_sha256="c" * 64,
        instruction_text="test",
        expected_panel_ids=("panel-1",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            if len(self.requests) == 1:
                raise VisionResponseInvalid(
                    validation_subtype="production_visual_selection_insufficient",
                    retry_passages=locked,
                )
            if len(self.requests) == 2:
                assert active_request.retry_visual_selection is True
                assert active_request.retry_dialogue_paraphrase is False
                raise VisionResponseInvalid(
                    validation_subtype="script_passage_copies_source_dialogue",
                    passage_word_counts=(8,),
                    retry_passages=locked,
                )
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(
        pipeline, "_validate_synthesis_subtitle_admission", lambda output, active_request: None
    )
    monkeypatch.setattr(
        pipeline, "_validated_synthesis_cache_output", lambda output, active_request: output
    )

    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}
    assert len(provider.requests) == 3
    dialogue_retry = provider.requests[2]
    assert dialogue_retry.retry_dialogue_paraphrase is True
    assert dialogue_retry.retry_visual_selection is False
    assert dialogue_retry.retry_word_counts is None
    assert dialogue_retry.retry_passages == locked


def test_retention_contract_rejects_claim_without_local_evidence():
    from app.services import analyzer_contract as contract
    from app.services import narrative_identity as identity

    profile = identity.get_narrative_identity("retention_story_v1")
    passages = [
        {
            "passage_id": "p1",
            "editorial_role": "hook",
            "text": "Jin wakes holding every memory from the future intact.",
            "claim_ids": ["c1"],
            "evidence_panel_ids": ["panel-1"],
        },
        {
            "passage_id": "p2",
            "editorial_role": "pressure",
            "text": "A rival closes in while the sect loses its protection.",
            "claim_ids": ["c1", "c2"],
            "evidence_panel_ids": ["panel-2"],
        },
        {
            "passage_id": "p3",
            "editorial_role": "decision",
            "text": "He chooses the dangerous path and moves before anyone else.",
            "claim_ids": ["c2"],
            "evidence_panel_ids": ["panel-2"],
        },
        {
            "passage_id": "p4",
            "editorial_role": "cliffhanger",
            "text": "Then the same enemy appears at the training ground again.",
            "claim_ids": ["c2"],
            "evidence_panel_ids": ["panel-2"],
        },
    ]
    outline = {
        "ending_kind": "cliffhanger",
        "story_spine": dict.fromkeys(contract._STORY_SPINE_FIELDS, "grounded"),
    }
    with pytest.raises(contract.AnalyzerContractError, match="claim lacks local evidence"):
        contract._validate_script_passages_v3(
            passages,
            ("panel-1", "panel-2"),
            {"c1": {"panel-1"}, "c2": {"panel-2"}},
            [{"dialogue_or_ocr": []}],
            outline,
            profile,
        )


def test_profile_script_coverage_allows_aggregate_claim_evidence_but_requires_local_overlap():
    from app.services.pipeline_stages.script import _profile_claim_coverage_error

    claims = {"c1": {"evidence_panel_ids": ["p1", "p2"]}}
    sections = [
        {"claim_ids": ["c1"], "evidence_panel_ids": ["p1"]},
        {"claim_ids": ["c1"], "evidence_panel_ids": ["p2"]},
    ]
    assert _profile_claim_coverage_error(sections, claims, {"p1", "p2"}) is None
    bad = [
        {"claim_ids": ["c1"], "evidence_panel_ids": ["p1"]},
        {"claim_ids": ["c1"], "evidence_panel_ids": ["p3"]},
    ]
    assert (
        _profile_claim_coverage_error(bad, claims, {"p1", "p2", "p3"})
        == "script section evidence does not ground its claim"
    )


@pytest.mark.parametrize(
    "subtype",
    (
        "script_passage_claim_lacks_local_evidence",
        "script_passage_evidence_does_not_cover_its_claims",
    ),
)
def test_local_claim_failure_retries_with_locked_narration(monkeypatch, subtype):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    locked = (
        {
            "passage_id": "p1",
            "editorial_role": "hook",
            "text": "Grounded narration stays exactly the same here.",
            "claim_ids": ["c1"],
            "evidence_panel_ids": ["panel-1"],
        },
    )
    request = VisionChapterSynthesisRequest(
        analysis_run_id="local-claim-retry-test",
        instruction_version="test-v1",
        instruction_sha256="d" * 64,
        instruction_text="test",
        expected_panel_ids=("panel-1",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            if len(self.requests) == 1:
                raise VisionResponseInvalid(validation_subtype=subtype, retry_passages=locked)
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(
        pipeline, "_validate_synthesis_subtitle_admission", lambda output, active_request: None
    )
    monkeypatch.setattr(
        pipeline, "_validated_synthesis_cache_output", lambda output, active_request: output
    )
    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}
    retry = provider.requests[1]
    assert retry.retry_local_claim_grounding is True
    assert retry.retry_visual_selection is False
    assert retry.retry_dialogue_paraphrase is False
    assert retry.retry_passages == locked


def test_retention_wire_requires_local_claim_grounding():
    from app.services import narrative_identity as identity
    from app.services import vision_adapter

    profile = identity.get_narrative_identity("retention_story_v1")
    version, digest, instruction = identity.load_narrative_instruction(profile.profile_id)
    request = vision_adapter.VisionChapterSynthesisRequest(
        analysis_run_id="local-wire",
        instruction_version=version,
        instruction_sha256=digest,
        instruction_text=instruction,
        expected_panel_ids=("panel-1",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
        narrative_profile_id=profile.profile_id,
        narrative_profile_version=profile.profile_version,
        narrative_profile_sha256=profile.contract_sha256,
    )
    payload = vision_adapter._build_synthesis_payload(
        request, request.expected_panel_ids, "mock", profile
    )
    assert "must be locally grounded" in payload["messages"][1]["content"]


def test_retention_full_ledger_semantic_expansion_adds_direct_and_beat_matches():
    from types import SimpleNamespace

    from app.services import reference_visual_review as review

    script = SimpleNamespace(
        editorial_metadata={"narrative_identity": {"profile_id": "retention_story_v1"}},
        sections=[
            {
                "section": "twist",
                "text": "Jin reaches the Human-Faced Spider and uses his sword technique for the elixir.",
                "evidence_panel_ids": ["broad"],
                "evidence": [{"claim_id": "c1", "panel_ids": ["direct"]}],
            }
        ],
    )
    regions = [
        SimpleNamespace(
            panel_id="direct",
            source_order=10,
            observation_json={"visible_facts": ["Jin stands in a cave."]},
        ),
        SimpleNamespace(
            panel_id="semantic",
            source_order=20,
            observation_json={"visible_facts": ["Human faced spider beside a sword and elixir."]},
        ),
        SimpleNamespace(
            panel_id="noise",
            source_order=30,
            observation_json={"visible_facts": ["Children cry in a courtyard."]},
        ),
    ]
    expanded = review.expand_retention_section_evidence(script, regions, {"twist": ("broad",)})
    assert expanded["twist"][0] == "broad"
    assert "direct" in expanded["twist"]
    assert "semantic" in expanded["twist"]
    assert "noise" not in expanded["twist"]


def test_retention_direct_claim_evidence_gets_strong_story_relevance_boost():
    from types import SimpleNamespace

    from app.services import reference_visual_review as review

    story = {"hook": ("Jin awakens with memories that rewrite his death.",)}
    direct = {"hook": ("panel-direct",)}
    direct_region = SimpleNamespace(
        panel_id="panel-direct",
        source_order=10,
        observation_json={
            "visible_facts": ["Black-haired Jin lies awake in bed holding an object."]
        },
    )
    broad_region = SimpleNamespace(
        panel_id="panel-broad",
        source_order=20,
        observation_json={"visible_facts": ["Memories of death surround Jin as he awakens."]},
    )
    direct_score = review._retention_story_relevance(direct_region, ("hook",), story, direct)[
        "hook"
    ]
    broad_score = review._retention_story_relevance(broad_region, ("hook",), story, direct)["hook"]
    assert direct_score >= 4.0
    assert direct_score > broad_score


def test_retention_claim_text_affinity_bridges_equivalent_story_wording():
    from types import SimpleNamespace

    from app.services import reference_visual_review as review

    region = SimpleNamespace(
        panel_id="snow-pill",
        source_order=10,
        observation_json={"visible_facts": ["The Snow Plum Pill rests on a red cushion."]},
    )
    story = {"conflict": ("He decides to claim every spiritual elixir before the war.",)}
    claim_text = {"conflict": ("Jin plans to claim the Snow Plum Pill and Fire Ginseng.",)}
    without = review._retention_story_relevance(region, ("conflict",), story)["conflict"]
    with_claim = review._retention_story_relevance(
        region, ("conflict",), story, claim_text_by_section=claim_text
    )["conflict"]
    assert without == 0.0
    assert with_claim > 0.0



def test_retention_story_relevance_rejects_weak_title_modal_overlap():
    from types import SimpleNamespace

    from app.services import reference_visual_review as review

    story = {
        "twist": (
            "Once the announcement spreads, the legal catch appears: royal law links "
            "sharing the queen's bedchamber with marriage.",
        )
    }
    claims = {
        "twist": (
            "Royal law says anyone sharing the royal bedchamber with the queen must marry her.",
        )
    }
    unrelated = SimpleNamespace(
        panel_id="old-action",
        source_order=10,
        observation_json={
            "visible_facts": ["A fighter clenches his fists."],
            "dialogue_or_ocr": ["WE MUST CATCH HIM, BUT FIRST SAVE THE QUEEN!"],
            "inferences": [],
        },
    )
    score = review._retention_story_relevance(
        unrelated, ("twist",), story, claim_text_by_section=claims
    )["twist"]
    assert score == 0.0


def test_retention_story_relevance_keeps_substantive_claim_overlap():
    from types import SimpleNamespace

    from app.services import reference_visual_review as review

    story = {"cta": ("The month-long treatment arrangement changes everything.",)}
    claims = {"cta": ("Lloyd stays for one month of treatment.",)}
    region = SimpleNamespace(
        panel_id="recovery",
        source_order=20,
        observation_json={
            "visible_facts": ["A recovery discussion in a hall."],
            "dialogue_or_ocr": ["FULL RECOVERY WILL TAKE ONE MONTH."],
            "inferences": [],
        },
    )
    score = review._retention_story_relevance(
        region, ("cta",), story, claim_text_by_section=claims
    )["cta"]
    assert score > 0.0


def test_retention_tokens_normalize_basic_story_plurals():
    from app.services import reference_visual_review as review

    tokens = review._retention_tokens("memories spiritual elixirs bracelets")
    assert {"memory", "elixir", "bracelet"}.issubset(tokens)


def test_retention_claim_semantic_grounding_rejects_decorative_evidence():
    from app.services import analyzer_contract

    graph = {
        "claims": [
            {
                "claim_id": "c1",
                "claim_type": "fact",
                "text": "The King of Hell is hunting Lloyd's soul.",
                "qualification": "A direct supernatural threat.",
                "evidence_panel_ids": ["panel-1"],
            }
        ]
    }
    observations = [
        {
            "panel_id": "panel-1",
            "visible_facts": ["Red-haired woman holding a sword."],
            "dialogue_or_ocr": [],
            "inferences": [],
            "uncertainties": [],
        }
    ]
    with pytest.raises(analyzer_contract.AnalyzerContractError, match="semantic anchor"):
        analyzer_contract._validate_retention_claim_semantic_grounding(graph, observations)


def test_retention_semantic_failure_ranks_better_evidence_candidates():
    from app.services import analyzer_contract

    graph = {
        "claims": [
            {
                "claim_id": "c1",
                "claim_type": "fact",
                "text": "The curse requires nonstop mana from a trusted source.",
                "qualification": "Direct treatment requirement.",
                "evidence_panel_ids": ["wrong"],
            }
        ]
    }
    observations = [
        {
            "panel_id": "wrong",
            "source_order": 10,
            "visible_facts": ["Green poison energy grows stronger."],
            "dialogue_or_ocr": [],
            "inferences": [],
            "uncertainties": [],
        },
        {
            "panel_id": "continuous",
            "source_order": 20,
            "visible_facts": [],
            "dialogue_or_ocr": [
                "I must continuously use my mana to eradicate the source of the illness."
            ],
            "inferences": [],
            "uncertainties": [],
        },
        {
            "panel_id": "trusted",
            "source_order": 30,
            "visible_facts": [],
            "dialogue_or_ocr": [
                "It must be someone she has absolute trust in. Only that kind of person's mana can do it."
            ],
            "inferences": [],
            "uncertainties": [],
        },
    ]
    with pytest.raises(analyzer_contract.AnalyzerContractError) as exc_info:
        analyzer_contract._validate_retention_claim_semantic_grounding(graph, observations)
    candidates = exc_info.value.diagnostics["candidate_panels"]
    ids = [row["panel_id"] for row in candidates]
    assert ids[:2] == ["continuous", "trusted"]
    assert "mana" in candidates[0]["overlap_anchors"]
    assert any("continuously use my mana" in text for text in candidates[0]["evidence_excerpt"])
    assert any("absolute trust" in text for text in candidates[1]["evidence_excerpt"])


def test_retention_semantic_grounding_accepts_affirmative_dialogue_act():
    from app.services import analyzer_contract

    graph = {
        "claims": [
            {
                "claim_id": "c1",
                "claim_type": "fact",
                "text": "Lloyd responds affirmatively to the queen terms.",
                "qualification": "His affirmative reply confirms acceptance.",
                "evidence_panel_ids": ["panel-1"],
            }
        ]
    }
    observations = [
        {
            "panel_id": "panel-1",
            "visible_facts": [],
            "dialogue_or_ocr": ["YES, YOUR MAJESTY."],
            "inferences": [],
            "uncertainties": [],
        }
    ]
    analyzer_contract._validate_retention_claim_semantic_grounding(graph, observations)


def test_retention_claim_semantic_grounding_accepts_observed_anchor():
    from app.services import analyzer_contract

    graph = {
        "claims": [
            {
                "claim_id": "c1",
                "claim_type": "fact",
                "text": "Maleficarum turns weakness into madness.",
                "qualification": "The curse is explained directly.",
                "evidence_panel_ids": ["panel-1"],
            }
        ]
    }
    observations = [
        {
            "panel_id": "panel-1",
            "visible_facts": [],
            "dialogue_or_ocr": ["Maleficarum turns weakness into madness."],
            "inferences": [],
            "uncertainties": [],
        }
    ]
    analyzer_contract._validate_retention_claim_semantic_grounding(graph, observations)


def test_retention_claim_semantic_grounding_accepts_regular_verb_inflection():
    from app.services import analyzer_contract

    graph = {
        "claims": [
            {
                "claim_id": "c1",
                "claim_type": "fact",
                "text": "The two people embraced with light effects.",
                "qualification": "Visible in the panel.",
                "evidence_panel_ids": ["panel-1"],
            }
        ]
    }
    observations = [
        {
            "panel_id": "panel-1",
            "visible_facts": ["Two people are embracing with light effects."],
            "dialogue_or_ocr": [],
            "inferences": [],
            "uncertainties": [],
        }
    ]
    analyzer_contract._validate_retention_claim_semantic_grounding(graph, observations)

    graph["claims"][0]["text"] = "She saved him."
    observations[0]["visible_facts"] = []
    observations[0]["dialogue_or_ocr"] = ["WHY DID YOU SAVE ME?"]
    analyzer_contract._validate_retention_claim_semantic_grounding(graph, observations)


def test_retention_semantic_grounding_rejects_qualification_only_support():
    from app.services import analyzer_contract

    graph = {
        "claims": [
            {
                "claim_id": "c1",
                "claim_type": "fact",
                "text": "The queen requires trusted mana to suppress the curse.",
                "qualification": "Visible energy effects show the treatment.",
                "evidence_panel_ids": ["p1"],
            }
        ]
    }
    observations = [
        {
            "panel_id": "p1",
            "visible_facts": ["Bright energy effects surround a red-haired woman."],
            "dialogue_or_ocr": [],
            "inferences": [],
            "uncertainties": [],
        }
    ]
    with pytest.raises(analyzer_contract.AnalyzerContractError, match="semantic anchor"):
        analyzer_contract._validate_retention_claim_semantic_grounding(graph, observations)


def test_retention_semantic_grounding_rejects_single_anchor_for_compound_claim():
    from app.services import analyzer_contract

    graph = {
        "claims": [
            {
                "claim_id": "c1",
                "claim_type": "fact",
                "text": "Ancient royal law requires marriage after sharing the queen's bedchamber for treatment.",
                "qualification": "Direct rule.",
                "evidence_panel_ids": ["p1"],
            }
        ]
    }
    observations = [
        {
            "panel_id": "p1",
            "visible_facts": ["A man and woman hold hands."],
            "dialogue_or_ocr": ["MUST MARRY HER!"],
            "inferences": [],
            "uncertainties": [],
        }
    ]
    with pytest.raises(analyzer_contract.AnalyzerContractError) as exc_info:
        analyzer_contract._validate_retention_claim_semantic_grounding(graph, observations)
    diag = exc_info.value.diagnostics
    assert diag["matched_claim_anchors"] == ["marry", "require"]
    assert diag["required_anchor_matches"] >= 2


def test_retention_semantic_reporting_declare_accepts_direct_dialogue_with_content_match():
    from app.services import analyzer_contract

    graph = {
        "claims": [
            {
                "claim_id": "duel",
                "claim_type": "fact",
                "text": "One fighter declares the other barely good enough to fight him.",
                "qualification": "Direct dialogue.",
                "evidence_panel_ids": ["p"],
            }
        ]
    }
    observations = [
        {
            "panel_id": "p",
            "source_index": 93,
            "visible_facts": [],
            "dialogue_or_ocr": ["YOU'RE ONLY BARELY GOOD ENOUGH TO FIGHT ME."],
            "inferences": [],
            "uncertainties": [],
        }
    ]
    analyzer_contract._validate_retention_claim_semantic_grounding(graph, observations)


def test_retention_semantic_reporting_declare_rejects_unrelated_dialogue():
    import pytest

    from app.services import analyzer_contract

    graph = {
        "claims": [
            {
                "claim_id": "war",
                "claim_type": "fact",
                "text": "The leader declares war on the rival clan.",
                "qualification": "Direct dialogue.",
                "evidence_panel_ids": ["p"],
            }
        ]
    }
    observations = [
        {
            "panel_id": "p",
            "source_index": 10,
            "visible_facts": [],
            "dialogue_or_ocr": ["I WILL LEAVE NOW."],
            "inferences": [],
            "uncertainties": [],
        }
    ]
    with pytest.raises(analyzer_contract.AnalyzerContractError, match="semantic anchor"):
        analyzer_contract._validate_retention_claim_semantic_grounding(graph, observations)


def test_retention_semantic_need_matches_required_choice_in_local_window():
    from app.services import analyzer_contract

    graph = {
        "claims": [
            {
                "claim_id": "choice",
                "claim_type": "fact",
                "text": "They must choose a succession candidate.",
                "qualification": "The local dialogue states both the need and the choice.",
                "evidence_panel_ids": ["need", "choose"],
            }
        ]
    }
    observations = [
        {
            "panel_id": "need",
            "source_index": 220,
            "visible_facts": [],
            "dialogue_or_ocr": [
                "WE NEED ONE OF THE TWO OTHER SUCCESSOR CANDIDATES TO BECOME THE ASSOCIATION LEADER."
            ],
            "inferences": [],
            "uncertainties": [],
        },
        {
            "panel_id": "choose",
            "source_index": 229,
            "visible_facts": [],
            "dialogue_or_ocr": [
                "THE PERSON YOU CHOOSE TO SIDE WITH WILL HAVE THE HIGHER CHANCE."
            ],
            "inferences": [],
            "uncertainties": [],
        },
    ]
    analyzer_contract._validate_retention_claim_semantic_grounding(graph, observations)


def test_retention_semantic_need_does_not_bridge_distant_choice():
    import pytest

    from app.services import analyzer_contract

    graph = {
        "claims": [
            {
                "claim_id": "choice",
                "claim_type": "fact",
                "text": "They must choose a succession candidate.",
                "qualification": "The claim must remain local.",
                "evidence_panel_ids": ["need", "choose"],
            }
        ]
    }
    observations = [
        {
            "panel_id": "need",
            "source_index": 10,
            "visible_facts": [],
            "dialogue_or_ocr": ["WE NEED ONE OF THE TWO OTHER SUCCESSOR CANDIDATES."],
            "inferences": [],
            "uncertainties": [],
        },
        {
            "panel_id": "choose",
            "source_index": 40,
            "visible_facts": [],
            "dialogue_or_ocr": ["WHO WILL YOU CHOOSE THEN?"],
            "inferences": [],
            "uncertainties": [],
        },
    ]
    with pytest.raises(analyzer_contract.AnalyzerContractError, match="semantic anchor"):
        analyzer_contract._validate_retention_claim_semantic_grounding(graph, observations)


def test_retention_semantic_grounding_accepts_compound_claim_from_combined_evidence():
    from app.services import analyzer_contract

    graph = {
        "claims": [
            {
                "claim_id": "c1",
                "claim_type": "fact",
                "text": "Royal law requires marriage.",
                "qualification": "Direct rule.",
                "evidence_panel_ids": ["p1", "p2"],
            }
        ]
    }
    observations = [
        {
            "panel_id": "p1",
            "visible_facts": [],
            "dialogue_or_ocr": ["ACCORDING TO THE ROYAL LAW"],
            "inferences": [],
            "uncertainties": [],
        },
        {
            "panel_id": "p2",
            "visible_facts": [],
            "dialogue_or_ocr": ["YOU ARE REQUIRED TO MARRY HER."],
            "inferences": [],
            "uncertainties": [],
        },
    ]
    analyzer_contract._validate_retention_claim_semantic_grounding(graph, observations)


def test_semantic_claim_failure_retries_full_synthesis(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    request = VisionChapterSynthesisRequest(
        analysis_run_id="semantic-retry-test",
        instruction_version="test-v1",
        instruction_sha256="e" * 64,
        instruction_text="test",
        expected_panel_ids=("panel-1",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            if len(self.requests) == 1:
                raise VisionResponseInvalid(
                    validation_subtype="claim_evidence_lacks_semantic_anchor",
                    selection_diagnostics={
                        "claim_id": "claim3",
                        "claim_text": "marriage consequence",
                        "evidence_panel_ids": ["panel-1"],
                    },
                )
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_args: None)
    monkeypatch.setattr(
        pipeline, "_validated_synthesis_cache_output", lambda output, _request: output
    )
    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}
    retry = provider.requests[1]
    assert retry.retry_claim_semantic_grounding is True
    assert retry.retry_claim_semantic_diagnostics == {
        "claim_id": "claim3",
        "claim_text": "marriage consequence",
        "evidence_panel_ids": ["panel-1"],
    }
    assert retry.retry_passages is None
    assert retry.retry_word_counts is None


def test_semantic_claim_retry_preserves_passage_correction_base(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    locked = tuple(
        {
            "passage_id": f"p{i}",
            "editorial_role": "hook" if i == 1 else f"beat{i}",
            "text": f"Grounded passage {i} stays stable.",
            "claim_ids": ["bad" if i == 3 else f"c{i}"],
            "evidence_panel_ids": ["a"],
        }
        for i in range(1, 5)
    )
    request = VisionChapterSynthesisRequest(
        analysis_run_id="semantic-targeted-retry",
        instruction_version="v",
        instruction_sha256="e" * 64,
        instruction_text="x",
        expected_panel_ids=("a",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            if len(self.requests) == 1:
                raise VisionResponseInvalid(
                    validation_subtype="claim_evidence_lacks_semantic_anchor",
                    retry_passages=locked,
                    selection_diagnostics={"claim_id": "bad"},
                )
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_args: None)
    monkeypatch.setattr(
        pipeline, "_validated_synthesis_cache_output", lambda output, _request: output
    )
    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}
    retry = provider.requests[1]
    assert retry.retry_claim_semantic_grounding is True
    assert retry.retry_claim_semantic_diagnostics == {"claim_id": "bad"}
    assert retry.retry_passages == locked


def test_repeated_semantic_claim_gets_one_bounded_forbidden_claim_escalation(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    request = VisionChapterSynthesisRequest(
        analysis_run_id="semantic-bounded-escalation",
        instruction_version="v",
        instruction_sha256="e" * 64,
        instruction_text="x",
        expected_panel_ids=("a", "b"),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )
    failures = [
        {"claim_id": "bad", "claim_text": "She saved him.", "evidence_panel_ids": ["a"]},
        {"claim_id": "bad", "claim_text": "She was the one who saved him.", "evidence_panel_ids": ["a"]},
    ]

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            if len(self.requests) <= len(failures):
                raise VisionResponseInvalid(
                    validation_subtype="claim_evidence_lacks_semantic_anchor",
                    selection_diagnostics=failures[len(self.requests) - 1],
                )
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_args: None)
    monkeypatch.setattr(
        pipeline, "_validated_synthesis_cache_output", lambda output, _request: output
    )

    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}
    assert len(provider.requests) == 3
    assert provider.requests[2].retry_claim_semantic_diagnostics["forbidden_claim_ids"] == [
        "bad"
    ]


def test_forbidden_semantic_claim_fails_fast_after_escalation(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    request = VisionChapterSynthesisRequest(
        analysis_run_id="semantic-no-loop",
        instruction_version="v",
        instruction_sha256="e" * 64,
        instruction_text="x",
        expected_panel_ids=("a",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            raise VisionResponseInvalid(
                validation_subtype="claim_evidence_lacks_semantic_anchor",
                selection_diagnostics={
                    "claim_id": "repeat-me",
                    "claim_text": f"attempt {len(self.requests)} wording",
                    "evidence_panel_ids": ["a"],
                },
            )

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_args: None)

    with pytest.raises(VisionResponseInvalid):
        pipeline._synthesize_with_cache(provider, request)

    assert len(provider.requests) == 3
    assert provider.requests[2].retry_claim_semantic_diagnostics["forbidden_claim_ids"] == [
        "repeat-me"
    ]


def test_semantic_forbidden_claim_survives_different_failure_before_return(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    request = VisionChapterSynthesisRequest(
        analysis_run_id="semantic-no-oscillation",
        instruction_version="v",
        instruction_sha256="e" * 64,
        instruction_text="x",
        expected_panel_ids=("a", "b"),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )
    failures = [
        ("claim-a", "a"),
        ("claim-a", "a"),
        ("claim-b", "b"),
        ("claim-a", "a"),
    ]

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            claim_id, panel_id = failures[len(self.requests) - 1]
            raise VisionResponseInvalid(
                validation_subtype="claim_evidence_lacks_semantic_anchor",
                selection_diagnostics={
                    "claim_id": claim_id,
                    "claim_text": f"wording {len(self.requests)}",
                    "evidence_panel_ids": [panel_id],
                },
            )

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_args: None)

    with pytest.raises(VisionResponseInvalid):
        pipeline._synthesize_with_cache(provider, request)

    assert len(provider.requests) == 4
    assert provider.requests[2].retry_claim_semantic_diagnostics["forbidden_claim_ids"] == [
        "claim-a"
    ]
    assert provider.requests[3].retry_claim_semantic_diagnostics["forbidden_claim_ids"] == [
        "claim-a"
    ]


def test_semantic_retry_payload_targets_only_rejected_claim_passages():
    from app.services import narrative_identity as identity
    from app.services import vision_adapter as va

    profile = identity.get_narrative_identity("retention_story_v1")
    version, digest, instruction = identity.load_narrative_instruction(profile.profile_id)
    locked = tuple(
        {
            "passage_id": f"p{i}",
            "editorial_role": "hook" if i == 1 else f"beat{i}",
            "text": f"Grounded passage {i} stays stable.",
            "claim_ids": ["bad" if i == 3 else f"c{i}"],
            "evidence_panel_ids": ["a"],
        }
        for i in range(1, 5)
    )
    request = va.VisionChapterSynthesisRequest(
        analysis_run_id="semantic-target-wire",
        instruction_version=version,
        instruction_sha256=digest,
        instruction_text=instruction,
        expected_panel_ids=("a",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
        narrative_profile_id=profile.profile_id,
        narrative_profile_version=profile.profile_version,
        narrative_profile_sha256=profile.contract_sha256,
        retry_claim_semantic_grounding=True,
        retry_claim_semantic_diagnostics={"claim_id": "bad"},
        retry_passages=locked,
    )
    payload = va._build_synthesis_payload(
        request, request.expected_panel_ids, "mock", profile
    )
    content = payload["messages"][1]["content"]
    assert "Semantic-grounding retry" in content
    assert "Preserve every passage that does not reference the rejected claim_id exactly" in content
    assert "Rejected claim_id: bad" in content
    assert "rewrite only the minimum downstream passage needed" in content


def test_semantic_retry_prompt_retires_forbidden_claim_ids():
    from app.services import narrative_identity as identity
    from app.services import vision_adapter as va

    profile = identity.get_narrative_identity("retention_story_v1")
    version, digest, instruction = identity.load_narrative_instruction(profile.profile_id)
    request = va.VisionChapterSynthesisRequest(
        analysis_run_id="semantic-forbidden-wire",
        instruction_version=version,
        instruction_sha256=digest,
        instruction_text=instruction,
        expected_panel_ids=("a",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
        narrative_profile_id="retention_story_v1",
        retry_claim_semantic_grounding=True,
        retry_claim_semantic_diagnostics={
            "claim_id": "bad",
            "forbidden_claim_ids": ["bad"],
            "candidate_panels": [{"panel_id": "a", "overlap_anchors": ["save"]}],
        },
    )

    payload = va._build_synthesis_payload(request, request.expected_panel_ids, "mock", profile)
    content = payload["messages"][1]["content"]
    assert "forbidden_claim_ids" in content
    assert "those claim IDs are retired" in content
    assert "mint a new claim_id" in content


def test_deterministic_semantic_evidence_repair_adds_high_gain_candidate():
    from app.services import analyzer_contract, vision_adapter

    output = {
        "evidence_graph": {
            "claims": [
                {
                    "claim_id": "law",
                    "claim_type": "fact",
                    "text": "Royal law requires anyone sharing the bedchamber to marry.",
                    "qualification": "Direct rule.",
                    "evidence_panel_ids": ["payoff"],
                }
            ]
        },
        "script_passages": [
            {
                "passage_id": "p",
                "editorial_role": "twist",
                "text": "Royal law forces marriage.",
                "claim_ids": ["law"],
                "evidence_panel_ids": ["payoff"],
            }
        ],
    }
    diagnostics = {
        "claim_id": "law",
        "claim_anchors": ["royal", "law", "require", "anyone", "sharing", "bedchamber", "marry"],
        "matched_claim_anchors": ["marry"],
        "required_anchor_matches": 3,
        "candidate_panels": [
            {"panel_id": "law-panel", "overlap_anchors": ["royal", "law", "anyone", "bedchamber"]},
            {"panel_id": "payoff", "overlap_anchors": ["marry"]},
        ],
    }
    repaired = vision_adapter._repair_semantic_claim_evidence_from_diagnostics(
        output, diagnostics, ("payoff", "law-panel")
    )
    assert repaired is not None
    assert repaired["evidence_graph"]["claims"][0]["evidence_panel_ids"] == ["payoff", "law-panel"]
    assert repaired["script_passages"][0]["evidence_panel_ids"] == ["payoff", "law-panel"]
    observations = [
        {
            "panel_id": "payoff",
            "visible_facts": [],
            "dialogue_or_ocr": ["MUST MARRY HER!"],
            "inferences": [],
            "uncertainties": [],
        },
        {
            "panel_id": "law-panel",
            "visible_facts": [],
            "dialogue_or_ocr": ["ACCORDING TO ROYAL LAW, ANYONE SHARING THE BEDCHAMBER..."],
            "inferences": [],
            "uncertainties": [],
        },
    ]
    analyzer_contract._validate_retention_claim_semantic_grounding(
        repaired["evidence_graph"], observations
    )



def test_deterministic_semantic_claim_text_repair_shrinks_noncritical_claim():
    from app.services import analyzer_contract, vision_adapter

    output = {
        "evidence_graph": {
            "claims": [
                {
                    "claim_id": "c",
                    "claim_type": "fact",
                    "text": "The master says he is shaken by him.",
                    "qualification": "Direct dialogue.",
                    "evidence_panel_ids": ["p"],
                }
            ]
        },
        "script_passages": [
            {
                "passage_id": "p1",
                "editorial_role": "setup",
                "text": "His reaction changes the mood.",
                "claim_ids": ["c"],
                "evidence_panel_ids": ["p"],
            }
        ],
    }
    diagnostics = {
        "claim_id": "c",
        "claim_anchors": ["master", "says", "shaken"],
        "matched_claim_anchors": ["shaken"],
        "missing_critical_anchors": [],
    }
    repaired = vision_adapter._repair_semantic_claim_text_from_diagnostics(
        output, diagnostics, ("p",)
    )
    assert repaired is not None
    claim = repaired["evidence_graph"]["claims"][0]
    assert claim["text"] == "shaken"
    observations = [
        {
            "panel_id": "p",
            "visible_facts": [],
            "dialogue_or_ocr": ["I am shaken by him."],
            "inferences": [],
            "uncertainties": [],
        }
    ]
    analyzer_contract._validate_retention_claim_semantic_grounding(
        repaired["evidence_graph"], observations
    )


def test_deterministic_semantic_claim_text_repair_refuses_missing_critical_anchor():
    from app.services import vision_adapter

    output = {
        "evidence_graph": {
            "claims": [
                {
                    "claim_id": "c",
                    "claim_type": "fact",
                    "text": "He declares they are enemies.",
                    "qualification": "Direct dialogue.",
                    "evidence_panel_ids": ["p"],
                }
            ]
        },
        "script_passages": [],
    }
    diagnostics = {
        "claim_id": "c",
        "claim_anchors": ["declare", "enemy"],
        "matched_claim_anchors": ["enemy"],
        "missing_critical_anchors": ["declare"],
    }
    assert (
        vision_adapter._repair_semantic_claim_text_from_diagnostics(
            output, diagnostics, ("p",)
        )
        is None
    )


def test_deterministic_semantic_claim_candidate_excerpt_retires_run24_compound_claim():
    from app.services import vision_adapter

    output = {
        "evidence_graph": {
            "claims": [
                {
                    "claim_id": "claim-8",
                    "claim_type": "fact",
                    "text": "Vinter gave her the information to end the cycle.",
                    "qualification": "Directly supported.",
                    "evidence_panel_ids": ["wrong-panel"],
                }
            ]
        },
        "script_passages": [
            {
                "passage_id": "pressure",
                "editorial_role": "pressure",
                "text": "Vinter gave her the information to end the cycle.",
                "claim_ids": ["claim-8"],
                "evidence_panel_ids": ["wrong-panel"],
            }
        ],
    }
    diagnostics = {
        "claim_id": "claim-8",
        "claim_anchors": ["cycle", "gave", "information", "vinter"],
        "matched_claim_anchors": [],
        "required_anchor_matches": 3,
        "semantic_window_max_span": 12,
        "candidate_panels": [
            {
                "panel_id": "plan-panel",
                "source_order": 73,
                "overlap_anchors": ["vinter"],
                "evidence_excerpt": [
                    "I ONLY HAVE ONE SHOT TO EXECUTE THE PLAN THAT VINTER TOLD ME ABOUT."
                ],
            },
            {
                "panel_id": "unrelated-panel",
                "source_order": 74,
                "overlap_anchors": [],
                "evidence_excerpt": ["A door closes behind her."],
            },
        ],
    }

    repaired = vision_adapter._repair_semantic_claim_from_candidate_excerpt(
        output,
        diagnostics,
        ("wrong-panel", "plan-panel", "unrelated-panel"),
    )

    assert repaired is not None
    claim = repaired["evidence_graph"]["claims"][0]
    passage = repaired["script_passages"][0]
    expected_text = "I ONLY HAVE ONE SHOT TO EXECUTE THE PLAN THAT VINTER TOLD ME ABOUT."
    assert claim["text"] == expected_text
    assert passage["text"] == expected_text
    assert claim["evidence_panel_ids"] == ["plan-panel"]
    assert passage["evidence_panel_ids"] == ["plan-panel"]
    assert "end the cycle" not in claim["text"].casefold()
    assert "gave her the information" not in claim["text"].casefold()


def test_deterministic_semantic_claim_candidate_excerpt_refuses_multi_claim_passage():
    from app.services import vision_adapter

    output = {
        "evidence_graph": {
            "claims": [
                {
                    "claim_id": "claim-8",
                    "text": "Vinter gave her the information to end the cycle.",
                    "evidence_panel_ids": ["wrong-panel"],
                },
                {
                    "claim_id": "claim-9",
                    "text": "She has one attempt left.",
                    "evidence_panel_ids": ["plan-panel"],
                },
            ]
        },
        "script_passages": [
            {
                "passage_id": "pressure",
                "text": "Two claims are intentionally coupled here.",
                "claim_ids": ["claim-8", "claim-9"],
                "evidence_panel_ids": ["wrong-panel", "plan-panel"],
            }
        ],
    }
    diagnostics = {
        "claim_id": "claim-8",
        "claim_anchors": ["cycle", "gave", "information", "vinter"],
        "candidate_panels": [
            {
                "panel_id": "plan-panel",
                "source_order": 73,
                "overlap_anchors": ["vinter"],
                "evidence_excerpt": ["VINTER TOLD ME ABOUT THE PLAN."],
            }
        ],
    }

    assert (
        vision_adapter._repair_semantic_claim_from_candidate_excerpt(
            output, diagnostics, ("wrong-panel", "plan-panel")
        )
        is None
    )


def test_deterministic_semantic_evidence_repair_refuses_insufficient_union():
    from app.services import vision_adapter

    output = {
        "evidence_graph": {"claims": [{"claim_id": "c", "evidence_panel_ids": ["p1"]}]},
        "script_passages": [],
    }
    diagnostics = {
        "claim_id": "c",
        "claim_anchors": ["alpha", "beta", "gamma"],
        "matched_claim_anchors": ["alpha"],
        "required_anchor_matches": 3,
        "candidate_panels": [{"panel_id": "p2", "overlap_anchors": ["beta"]}],
    }
    assert (
        vision_adapter._repair_semantic_claim_evidence_from_diagnostics(
            output, diagnostics, ("p1", "p2")
        )
        is None
    )


def test_semantic_retry_instruction_requires_union_anchor_coverage():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    adapter = (root / "app/services/vision_adapter.py").read_text(encoding="utf-8")
    assert (
        "compute the UNION of overlap_anchors only across cited candidates inside one permitted local window"
        in adapter
    )
    assert (
        "MUST contain at least required_anchor_matches distinct claim anchors and every critical claim anchor"
        in adapter
    )
    assert "Never strengthen descriptive/future wording into obligation" in adapter


def test_projection_retry_has_targeted_corrective_instruction():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    adapter = (root / "app/services/vision_adapter.py").read_text(encoding="utf-8")
    pipeline = (root / "app/services/pipeline.py").read_text(encoding="utf-8")
    assert "retry_projection_contract: bool = False" in adapter
    assert "the previous response failed the synthesis projection contract" in adapter
    assert "continuity_ledger MUST preserve the supplied chunk continuity" in adapter
    assert (
        "if projection_retryable or story_spine_retryable or continuity_entity_retryable:"
        in pipeline
    )
    assert "state_change_references_an_unknown_entity" in pipeline
    assert (
        "Every motives[*].entity_id and state_changes[*].entity_id MUST exactly equal an entity_id"
        in adapter
    )
    assert "retry_projection_contract=True" in pipeline


def test_retention_v25_is_self_contained_and_causally_exclusive():
    from app.services import narrative_identity as identity

    version, _digest, prompt = identity.load_narrative_instruction("retention_story_v1")
    assert version == "vision-first-retention-story-v2.6"
    assert "continuity_ledger" in prompt
    assert "entities MUST be nonempty" in prompt
    assert "Never drop or swap entity identity" in prompt
    assert "Every claim must be semantically supported" in prompt
    assert "genre-native retention" in prompt
    assert "CAUSALLY ATTACHED" in prompt
    assert "Two facts being simultaneously true is not causality" in prompt
    assert "CAUSAL-ARC EXCLUSIVITY" in prompt
    assert "The body causal chain starts at passage two" in prompt
    assert "CLAIM-LEVEL CAUSAL CONNECTIVITY" in prompt
    assert "every newly introduced claim MUST have a directed" in prompt


def test_visual_retry_can_complete_with_same_source_scene_beyond_local_distance():
    from app.services import vision_adapter as va

    obs = []
    ids = ["a", "c", "d", "x1", "x2", "x3", "x4", "x5", "b"]
    for panel_id in ids:
        asset = "scene-1" if panel_id in {"a", "b"} else f"asset-{panel_id}"
        obs.append(
            {
                "panel_id": panel_id,
                "source_asset_id": asset,
                "visible_facts": (
                    ["Queen treatment begins."] if panel_id == "a" else ["quiet scene"]
                ),
                "dialogue_or_ocr": [],
            }
        )
    request = va.VisionChapterSynthesisRequest(
        analysis_run_id="scene-retry",
        instruction_version="v",
        instruction_sha256="a" * 64,
        instruction_text="x",
        expected_panel_ids=tuple(ids),
        coverage_manifest={},
        ordered_observations=tuple(obs),
        chunks=(),
        retry_visual_selection=True,
        preferred_visual_panel_ids=("a", "b", "c", "d"),
        preferred_visual_panel_ids_by_section={"hook": ("a", "b", "c", "d")},
    )
    output = {
        "evidence_graph": {
            "claims": [
                {
                    "claim_id": "c1",
                    "text": "queen treatment",
                    "qualification": "grounded",
                    "evidence_panel_ids": ["a"],
                }
            ]
        },
        "script_passages": [
            {
                "passage_id": "p1",
                "editorial_role": "hook",
                "text": "queen treatment begins",
                "claim_ids": ["c1"],
                "evidence_panel_ids": ["a", "c", "d"],
            }
        ],
    }
    repaired = va._complete_retry_visual_selection(output, request)
    evidence = repaired["script_passages"][0]["evidence_panel_ids"]
    assert {"a", "b", "c", "d"} <= set(evidence)


def test_retention_retry_chain_can_apply_multiple_distinct_corrections(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    locked = (
        {
            "passage_id": "p1",
            "editorial_role": "hook",
            "text": "Short grounded hook text.",
            "claim_ids": ["c1"],
            "evidence_panel_ids": ["panel-1"],
        },
    )
    request = VisionChapterSynthesisRequest(
        analysis_run_id="multi-repair",
        instruction_version="v",
        instruction_sha256="d" * 64,
        instruction_text="x",
        expected_panel_ids=("panel-1",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
        narrative_profile_id="retention_story_v1",
        target_word_count_min=115,
        target_word_count_max=125,
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.calls = 0

        def synthesize(self, active_request):
            self.calls += 1
            if self.calls == 1:
                raise VisionResponseInvalid(
                    validation_subtype="synthesis_projection_continuity_identity_invalid"
                )
            if self.calls == 2:
                raise VisionResponseInvalid(
                    validation_subtype="script_passage_copies_source_dialogue",
                    passage_word_counts=(10,),
                    retry_passages=locked,
                )
            if self.calls == 3:
                raise VisionResponseInvalid(
                    validation_subtype="production_narration_word_count_out_of_range",
                    passage_word_counts=(10,),
                    retry_passages=locked,
                )
            if self.calls == 4:
                raise VisionResponseInvalid(
                    validation_subtype="production_visual_selection_insufficient",
                    retry_passages=locked,
                )
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(
        pipeline, "_validate_synthesis_subtitle_admission", lambda output, active_request: None
    )
    monkeypatch.setattr(
        pipeline, "_validated_synthesis_cache_output", lambda output, active_request: output
    )
    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}
    assert provider.calls == 5


def test_visual_retry_accepts_close_chronological_neighbor_without_lexical_overlap():
    from app.services import vision_adapter as va

    ids = ("a", "x1", "x2", "x3", "b")
    observations = tuple(
        {
            "panel_id": panel_id,
            "source_asset_id": f"asset-{panel_id}",
            "visible_facts": (
                ["Queen treatment begins."] if panel_id == "a" else ["quiet unrelated frame"]
            ),
            "dialogue_or_ocr": [],
        }
        for panel_id in ids
    )
    request = va.VisionChapterSynthesisRequest(
        analysis_run_id="near-neighbor-retry",
        instruction_version="v",
        instruction_sha256="a" * 64,
        instruction_text="x",
        expected_panel_ids=ids,
        coverage_manifest={},
        ordered_observations=observations,
        chunks=(),
        retry_visual_selection=True,
        preferred_visual_panel_ids=("a", "b", "x1", "x2"),
        preferred_visual_panel_ids_by_section={"hook": ("a", "b", "x1", "x2")},
    )
    output = {
        "evidence_graph": {
            "claims": [
                {
                    "claim_id": "c1",
                    "text": "queen treatment",
                    "qualification": "grounded",
                    "evidence_panel_ids": ["a"],
                }
            ]
        },
        "script_passages": [
            {
                "passage_id": "p1",
                "editorial_role": "hook",
                "text": "queen treatment begins",
                "claim_ids": ["c1"],
                "evidence_panel_ids": ["a", "x1", "x2"],
            }
        ],
    }
    repaired = va._complete_retry_visual_selection(output, request)
    assert "b" in repaired["script_passages"][0]["evidence_panel_ids"]


def test_retention_causal_chain_rejects_disconnected_late_claim():
    from app.services import analyzer_contract as contract

    continuity = {
        "causal_links": [
            {
                "from_panel_id": "a",
                "to_panel_id": "b",
                "reason": "first beat",
                "evidence_panel_ids": ["a", "b"],
            },
        ]
    }
    graph = {
        "claims": [
            {"claim_id": "c1", "evidence_panel_ids": ["a"]},
            {"claim_id": "c2", "evidence_panel_ids": ["b"]},
            {"claim_id": "c3", "evidence_panel_ids": ["x"]},
        ]
    }
    passages = [
        {"claim_ids": ["c1"]},
        {"claim_ids": ["c2"]},
        {"claim_ids": ["c3"]},
        {"claim_ids": ["c3"]},
    ]
    with pytest.raises(contract.AnalyzerContractError, match="disconnected claim"):
        contract._validate_retention_causal_chain(continuity, graph, passages)


def test_retention_causal_chain_accepts_directed_connected_claims():
    from app.services import analyzer_contract as contract

    continuity = {
        "causal_links": [
            {
                "from_panel_id": "a",
                "to_panel_id": "b",
                "reason": "setup",
                "evidence_panel_ids": ["a", "b"],
            },
            {
                "from_panel_id": "b",
                "to_panel_id": "c",
                "reason": "consequence",
                "evidence_panel_ids": ["b", "c"],
            },
            {
                "from_panel_id": "c",
                "to_panel_id": "d",
                "reason": "payoff",
                "evidence_panel_ids": ["c", "d"],
            },
        ]
    }

    graph = {
        "claims": [
            {"claim_id": "c1", "evidence_panel_ids": ["a"]},
            {"claim_id": "c2", "evidence_panel_ids": ["b"]},
            {"claim_id": "c3", "evidence_panel_ids": ["c"]},
            {"claim_id": "c4", "evidence_panel_ids": ["d"]},
        ]
    }
    passages = [
        {"claim_ids": ["c1"]},
        {"claim_ids": ["c2"]},
        {"claim_ids": ["c3"]},
        {"claim_ids": ["c4"]},
    ]
    contract._validate_retention_causal_chain(continuity, graph, passages)


def test_disconnected_claim_retries_full_causal_arc(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    request = VisionChapterSynthesisRequest(
        analysis_run_id="causal-retry",
        instruction_version="v",
        instruction_sha256="f" * 64,
        instruction_text="x",
        expected_panel_ids=("a",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )
    locked = tuple(
        {
            "passage_id": f"p{i}",
            "editorial_role": "hook" if i == 1 else f"beat{i}",
            "text": f"Grounded passage {i} stays on the same causal chain.",
            "claim_ids": [f"c{i}"],
            "evidence_panel_ids": ["a"],
        }
        for i in range(1, 5)
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            if len(self.requests) == 1:
                raise VisionResponseInvalid(
                    validation_subtype="retention_passage_introduces_disconnected_claim",
                    retry_passages=locked,
                )
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_args: None)
    monkeypatch.setattr(
        pipeline, "_validated_synthesis_cache_output", lambda output, _request: output
    )
    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}
    retry = provider.requests[1]
    assert retry.retry_causal_arc is True
    assert retry.retry_passages == locked
    assert retry.retry_word_counts is None
    assert retry.retry_visual_selection is False
    assert retry.retry_claim_semantic_grounding is False


def test_zero_candidate_disconnected_claim_is_retired_on_first_causal_retry(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    request = VisionChapterSynthesisRequest(
        analysis_run_id="causal-zero-candidate-release",
        instruction_version="v",
        instruction_sha256="f" * 64,
        instruction_text="x",
        expected_panel_ids=("a",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )
    locked = tuple(
        {
            "passage_id": f"p{i}",
            "editorial_role": "hook" if i == 1 else f"beat{i}",
            "text": f"Grounded passage {i} stays on the same causal chain.",
            "claim_ids": [f"c{i}"],
            "evidence_panel_ids": ["a"],
        }
        for i in range(1, 7)
    )
    diagnostics = {
        "passage_number": 6,
        "passage_id": "p6",
        "claim_id": "claim_loyalty",
        "reachable_panel_ids": ["a"],
        "reachable_candidate_claims": [],
        "forward_candidate_claims": [],
    }

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            if len(self.requests) == 1:
                raise VisionResponseInvalid(
                    validation_subtype="retention_passage_introduces_disconnected_claim",
                    retry_passages=locked,
                    selection_diagnostics=diagnostics,
                )
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_args: None)
    monkeypatch.setattr(
        pipeline, "_validated_synthesis_cache_output", lambda output, _request: output
    )

    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}
    retry = provider.requests[1]
    assert retry.retry_causal_arc is True
    assert retry.retry_passages == locked
    assert retry.retry_causal_diagnostics["zero_candidate_release"] is True
    assert retry.retry_causal_diagnostics["forbidden_claim_ids"] == ["claim_loyalty"]


def test_disconnected_claim_diagnostics_identify_reachable_replacement():
    from app.services import analyzer_contract as contract

    continuity = {
        "chunks": [{"panel_ids": ["a", "b", "c"]}],
        "causal_links": [{"from_panel_id": "a", "to_panel_id": "b"}],
    }
    graph = {
        "claims": [
            {"claim_id": "setup", "text": "setup", "evidence_panel_ids": ["a"]},
            {"claim_id": "connected", "text": "connected", "evidence_panel_ids": ["b"]},
            {"claim_id": "disconnected", "text": "remote threat", "evidence_panel_ids": ["c"]},
            {"claim_id": "reachable-alt", "text": "near consequence", "evidence_panel_ids": ["b"]},
        ]
    }
    passages = [
        {"passage_id": "hook", "claim_ids": ["reachable-alt"]},
        {"passage_id": "setup", "claim_ids": ["setup"]},
        {"passage_id": "conflict", "claim_ids": ["connected"]},
        {"passage_id": "twist", "claim_ids": ["disconnected"]},
    ]

    with pytest.raises(contract.AnalyzerContractError) as caught:
        contract._validate_retention_causal_chain(continuity, graph, passages)

    diagnostics = caught.value.diagnostics
    assert diagnostics["passage_id"] == "twist"
    assert diagnostics["claim_id"] == "disconnected"
    assert diagnostics["claim_evidence_panel_ids"] == ["c"]
    assert diagnostics["reachable_panel_ids"] == ["a", "b"]
    assert [row["claim_id"] for row in diagnostics["reachable_candidate_claims"]] == [
        "reachable-alt"
    ]


def test_disconnected_claim_diagnostics_offer_grounded_forward_alternatives():
    from app.services import analyzer_contract as contract

    continuity = {
        "chunks": [{"panel_ids": ["a", "b", "c", "d"]}],
        "causal_links": [{"from_panel_id": "a", "to_panel_id": "b"}],
    }
    graph = {
        "claims": [
            {"claim_id": "setup", "text": "setup", "evidence_panel_ids": ["a"]},
            {"claim_id": "connected", "text": "connected", "evidence_panel_ids": ["b"]},
            {
                "claim_id": "forward-alt",
                "text": "nearer grounded consequence",
                "evidence_panel_ids": ["c"],
            },
            {
                "claim_id": "disconnected",
                "text": "later disconnected consequence",
                "evidence_panel_ids": ["d"],
            },
        ]
    }
    passages = [
        {"passage_id": "hook", "claim_ids": ["forward-alt"]},
        {"passage_id": "setup", "claim_ids": ["setup"]},
        {"passage_id": "conflict", "claim_ids": ["connected"]},
        {"passage_id": "twist", "claim_ids": ["disconnected"]},
    ]

    with pytest.raises(contract.AnalyzerContractError) as caught:
        contract._validate_retention_causal_chain(continuity, graph, passages)

    diagnostics = caught.value.diagnostics
    assert diagnostics["reachable_candidate_claims"] == []
    assert [row["claim_id"] for row in diagnostics["forward_candidate_claims"]] == [
        "forward-alt"
    ]
    assert diagnostics["forward_candidate_claims"][0]["evidence_panel_ids"] == ["c"]
    assert diagnostics["forward_candidate_claims"][0]["first_source_order"] == 2


def test_deterministic_disconnected_passage_reuses_existing_reachable_claim():
    from app.services import vision_adapter

    output = {
        "evidence_graph": {
            "claims": [
                {
                    "claim_id": "setup",
                    "text": "The armored man holds a withered flower.",
                    "evidence_panel_ids": ["flower-panel"],
                },
                {
                    "claim_id": "disconnected",
                    "text": "A pink-haired woman suddenly attacks.",
                    "evidence_panel_ids": ["remote-panel"],
                },
            ]
        },
        "script_passages": [
            {
                "passage_id": "late-beat",
                "editorial_role": "twist",
                "text": "A pink-haired woman suddenly attacks.",
                "claim_ids": ["disconnected"],
                "evidence_panel_ids": ["remote-panel"],
            }
        ],
    }
    diagnostics = {
        "passage_id": "late-beat",
        "claim_id": "disconnected",
        "reachable_candidate_claims": [
            {
                "claim_id": "setup",
                "claim_text": "The armored man holds a withered flower.",
                "evidence_panel_ids": ["flower-panel"],
            }
        ],
    }

    repaired = vision_adapter._repair_disconnected_passage_from_diagnostics(
        output,
        diagnostics,
        ("flower-panel", "remote-panel"),
    )

    assert repaired is not None
    passage = repaired["script_passages"][0]
    assert passage["claim_ids"] == ["setup"]
    assert passage["evidence_panel_ids"] == ["flower-panel"]
    assert passage["text"] == "The armored man holds a withered flower."
    assert "pink-haired" not in passage["text"].casefold()


def test_repeated_disconnected_claim_gets_one_bounded_forbidden_claim_escalation(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    request = VisionChapterSynthesisRequest(
        analysis_run_id="causal-bounded-escalation",
        instruction_version="v",
        instruction_sha256="f" * 64,
        instruction_text="x",
        expected_panel_ids=("a", "b"),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )
    locked = tuple(
        {
            "passage_id": f"p{i}",
            "editorial_role": "hook" if i == 1 else f"beat{i}",
            "text": f"Grounded passage {i} stays on the same causal chain.",
            "claim_ids": [f"c{i}"],
            "evidence_panel_ids": ["a"],
        }
        for i in range(1, 5)
    )
    diagnostics = {
        "passage_id": "p3",
        "claim_id": "remote-threat",
        "reachable_candidate_claims": [
            {"claim_id": "near-consequence", "evidence_panel_ids": ["b"]}
        ],
    }

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            if len(self.requests) <= 2:
                raise VisionResponseInvalid(
                    validation_subtype="retention_passage_introduces_disconnected_claim",
                    retry_passages=locked,
                    selection_diagnostics=diagnostics,
                )
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_args: None)
    monkeypatch.setattr(
        pipeline, "_validated_synthesis_cache_output", lambda output, _request: output
    )

    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}
    assert len(provider.requests) == 3
    assert provider.requests[1].retry_causal_diagnostics["claim_id"] == "remote-threat"
    assert provider.requests[2].retry_causal_diagnostics["forbidden_claim_ids"] == [
        "remote-threat"
    ]


def test_repeated_forbidden_disconnected_claim_fails_fast_after_escalation(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    request = VisionChapterSynthesisRequest(
        analysis_run_id="causal-no-loop",
        instruction_version="v",
        instruction_sha256="f" * 64,
        instruction_text="x",
        expected_panel_ids=("a",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )
    diagnostics = {"passage_id": "p3", "claim_id": "repeat-me"}

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            raise VisionResponseInvalid(
                validation_subtype="retention_passage_introduces_disconnected_claim",
                selection_diagnostics=diagnostics,
            )

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_args: None)

    with pytest.raises(VisionResponseInvalid):
        pipeline._synthesize_with_cache(provider, request)

    assert len(provider.requests) == 3
    assert provider.requests[2].retry_causal_diagnostics["forbidden_claim_ids"] == ["repeat-me"]


def test_forbidden_disconnected_claim_stays_forbidden_across_different_failure(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    request = VisionChapterSynthesisRequest(
        analysis_run_id="causal-no-oscillation",
        instruction_version="v",
        instruction_sha256="f" * 64,
        instruction_text="x",
        expected_panel_ids=("a", "b"),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )
    failures = ["claim-a", "claim-a", "claim-b", "claim-a"]

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            claim_id = failures[len(self.requests) - 1]
            raise VisionResponseInvalid(
                validation_subtype="retention_passage_introduces_disconnected_claim",
                selection_diagnostics={"passage_id": "p3", "claim_id": claim_id},
            )

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_args: None)

    with pytest.raises(VisionResponseInvalid):
        pipeline._synthesize_with_cache(provider, request)

    assert len(provider.requests) == 4
    assert provider.requests[2].retry_causal_diagnostics["forbidden_claim_ids"] == ["claim-a"]
    assert provider.requests[3].retry_causal_diagnostics["forbidden_claim_ids"] == ["claim-a"]


def test_backward_causal_link_retries_full_causal_arc(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    request = VisionChapterSynthesisRequest(
        analysis_run_id="backward-causal-retry",
        instruction_version="v",
        instruction_sha256="f" * 64,
        instruction_text="x",
        expected_panel_ids=("a", "b"),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            if len(self.requests) == 1:
                raise VisionResponseInvalid(
                    validation_subtype="retention_causal_link_moves_backward_in_chronology"
                )
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_args: None)
    monkeypatch.setattr(
        pipeline, "_validated_synthesis_cache_output", lambda output, _request: output
    )
    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}
    retry = provider.requests[1]
    assert retry.retry_causal_arc is True
    assert retry.retry_visual_selection is False
    assert retry.retry_claim_semantic_grounding is False
    assert retry.retry_word_counts is None


def test_backward_causal_link_retry_forbids_exact_invalid_edge(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    request = VisionChapterSynthesisRequest(
        analysis_run_id="backward-causal-edge-ban",
        instruction_version="v",
        instruction_sha256="f" * 64,
        instruction_text="x",
        expected_panel_ids=("earlier", "later"),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            if len(self.requests) == 1:
                raise VisionResponseInvalid(
                    validation_subtype="retention_causal_link_moves_backward_in_chronology",
                    selection_diagnostics={
                        "from_panel_id": "later",
                        "to_panel_id": "earlier",
                        "from_source_order": 88,
                        "to_source_order": 81,
                    },
                )
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_args: None)
    monkeypatch.setattr(
        pipeline, "_validated_synthesis_cache_output", lambda output, _request: output
    )

    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}
    retry = provider.requests[1]
    assert retry.retry_causal_diagnostics["chronology_repair"] is True
    assert retry.retry_causal_diagnostics["forbidden_causal_links"] == [
        {"from_panel_id": "later", "to_panel_id": "earlier"}
    ]


def test_causal_retry_payload_anchors_only_first_two_passages():
    from app.services import narrative_identity as identity
    from app.services import vision_adapter as va

    profile = identity.get_narrative_identity("retention_story_v1")
    version, digest, instruction = identity.load_narrative_instruction(profile.profile_id)
    locked = tuple(
        {
            "passage_id": f"p{i}",
            "editorial_role": "hook" if i == 1 else f"beat{i}",
            "text": f"Grounded passage {i} stays on the same causal chain.",
            "claim_ids": [f"c{i}"],
            "evidence_panel_ids": ["a"],
        }
        for i in range(1, 5)
    )
    request = va.VisionChapterSynthesisRequest(
        analysis_run_id="causal-wire",
        instruction_version=version,
        instruction_sha256=digest,
        instruction_text=instruction,
        expected_panel_ids=("a",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
        narrative_profile_id=profile.profile_id,
        narrative_profile_version=profile.profile_version,
        narrative_profile_sha256=profile.contract_sha256,
        retry_causal_arc=True,
        retry_passages=locked,
    )
    payload = va._build_synthesis_payload(request, request.expected_panel_ids, "mock", profile)
    content = payload["messages"][1]["content"]
    assert "Previous first-two anchor passages" in content
    assert "Do not lock passages three onward" in content


def test_hook_reachability_retry_releases_hook_and_anchors_setup():
    from app.services import narrative_identity as identity
    from app.services import vision_adapter as va

    profile = identity.get_narrative_identity("retention_story_v1")
    version, digest, instruction = identity.load_narrative_instruction(profile.profile_id)
    locked = tuple(
        {
            "passage_id": f"p{i}",
            "editorial_role": "hook" if i == 1 else f"beat{i}",
            "text": f"Grounded passage {i} stays on the same causal chain.",
            "claim_ids": [f"c{i}"],
            "evidence_panel_ids": ["a"],
        }
        for i in range(1, 5)
    )
    request = va.VisionChapterSynthesisRequest(
        analysis_run_id="hook-release-wire",
        instruction_version=version,
        instruction_sha256=digest,
        instruction_text=instruction,
        expected_panel_ids=("a",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
        narrative_profile_id=profile.profile_id,
        narrative_profile_version=profile.profile_version,
        narrative_profile_sha256=profile.contract_sha256,
        retry_causal_arc=True,
        retry_causal_diagnostics={
            "release_hook_teaser": True,
            "forbidden_causal_links": [
                {"from_panel_id": "later", "to_panel_id": "earlier"}
            ],
        },
        retry_passages=locked,
    )
    payload = va._build_synthesis_payload(request, request.expected_panel_ids, "mock", profile)
    content = payload["messages"][1]["content"]

    assert "Previous setup anchor passage" in content
    assert "previous hook is not an anchor" in content
    assert "If the previous hook claim cannot be reached, replace it" in content
    assert "forbidden_causal_links" in content
    assert "never repair it by merely swapping endpoints" in content


def test_zero_candidate_causal_retry_payload_releases_terminal_concept():
    from app.services import narrative_identity as identity
    from app.services import vision_adapter as va

    profile = identity.get_narrative_identity("retention_story_v1")
    version, digest, instruction = identity.load_narrative_instruction(profile.profile_id)
    locked = tuple(
        {
            "passage_id": f"p{i}",
            "editorial_role": "hook" if i == 1 else f"beat{i}",
            "text": f"Grounded passage {i} stays on the same causal chain.",
            "claim_ids": [f"c{i}"],
            "evidence_panel_ids": ["a"],
        }
        for i in range(1, 7)
    )
    request = va.VisionChapterSynthesisRequest(
        analysis_run_id="causal-zero-candidate-prompt",
        instruction_version=version,
        instruction_sha256=digest,
        instruction_text=instruction,
        expected_panel_ids=("a",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
        narrative_profile_id=profile.profile_id,
        narrative_profile_version=profile.profile_version,
        narrative_profile_sha256=profile.contract_sha256,
        retry_causal_arc=True,
        retry_causal_diagnostics={
            "passage_number": 6,
            "passage_id": "p6",
            "claim_id": "claim_loyalty",
            "reachable_panel_ids": ["a"],
            "reachable_candidate_claims": [],
            "forward_candidate_claims": [],
            "zero_candidate_release": True,
            "forbidden_claim_ids": ["claim_loyalty"],
        },
        retry_passages=locked,
    )

    payload = va._build_synthesis_payload(request, request.expected_panel_ids, "mock", profile)
    content = payload["messages"][1]["content"]
    assert "zero_candidate_release=true as a structural release" in content
    assert "retire the offending claim_id immediately" in content
    assert "reachable_panel_ids already connected to the body" in content
    assert "resolve on a concrete grounded fact or consequence already established" in content
    assert "Previous first-two anchor passages" in content
    assert "Do not lock passages three onward" in content


def test_story_spine_missing_field_retries_projection_contract(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    request = VisionChapterSynthesisRequest(
        analysis_run_id="spine-retry",
        instruction_version="v",
        instruction_sha256="a" * 64,
        instruction_text="x",
        expected_panel_ids=("a",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, r):
            self.requests.append(r)
            if len(self.requests) == 1:
                raise VisionResponseInvalid(
                    validation_subtype="story_spineunresolved_question_must_be_a_non-empty_string"
                )
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_: None)
    monkeypatch.setattr(
        pipeline, "_validated_synthesis_cache_output", lambda output, _request: output
    )
    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}
    assert provider.requests[1].retry_projection_contract is True


def test_causal_anchor_persists_through_semantic_retry(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    locked = tuple(
        {
            "passage_id": f"p{i}",
            "editorial_role": "hook" if i == 1 else f"beat{i}",
            "text": f"Grounded passage {i} stays causal.",
            "claim_ids": [f"c{i}"],
            "evidence_panel_ids": ["a"],
        }
        for i in range(1, 5)
    )
    request = VisionChapterSynthesisRequest(
        analysis_run_id="chain-retry",
        instruction_version="v",
        instruction_sha256="b" * 64,
        instruction_text="x",
        expected_panel_ids=("a",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, r):
            self.requests.append(r)
            if len(self.requests) == 1:
                raise VisionResponseInvalid(
                    validation_subtype="retention_passage_introduces_disconnected_claim",
                    retry_passages=locked,
                )
            if len(self.requests) == 2:
                raise VisionResponseInvalid(
                    validation_subtype="claim_evidence_lacks_semantic_anchor"
                )
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_: None)
    monkeypatch.setattr(
        pipeline, "_validated_synthesis_cache_output", lambda output, _request: output
    )
    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}
    assert provider.requests[2].retry_causal_arc is True
    assert provider.requests[2].retry_passages == locked


def test_retention_visual_story_alignment_rejects_unrelated_safe_panel():
    from app.services import vision_adapter as va

    observations = (
        {
            "panel_id": "direct",
            "source_asset_id": "scene-a",
            "visible_facts": ["A couple faces a marriage order."],
            "dialogue_or_ocr": ["MUST MARRY HER!"],
            "inferences": [],
        },
        {
            "panel_id": "safe",
            "source_asset_id": "scene-b",
            "visible_facts": ["An energy blast fills the battlefield."],
            "dialogue_or_ocr": [],
            "inferences": [],
        },
    )
    request = va.VisionChapterSynthesisRequest(
        analysis_run_id="visual-story-miss",
        instruction_version="v",
        instruction_sha256="a" * 64,
        instruction_text="x",
        expected_panel_ids=("direct", "safe"),
        coverage_manifest={},
        ordered_observations=observations,
        chunks=(),
        narrative_profile_id="retention_story_v1",
        preferred_visual_panel_ids=("safe",),
        preferred_visual_panel_ids_by_section={"hook": ("safe",)},
    )
    output = {
        "evidence_graph": {
            "claims": [
                {
                    "claim_id": "c1",
                    "text": "The queen's order forces a marriage consequence.",
                    "qualification": "Direct court consequence.",
                    "evidence_panel_ids": ["direct"],
                }
            ]
        },
        "script_passages": [
            {
                "passage_id": "p1",
                "editorial_role": "hook",
                "text": "The queen's order suddenly turns into a marriage consequence.",
                "claim_ids": ["c1"],
                "evidence_panel_ids": ["direct"],
            }
        ],
    }
    assert va._retention_visual_story_alignment_missing(output, request) == ("hook",)


def test_retention_visual_story_alignment_accepts_semantic_safe_panel():
    from app.services import vision_adapter as va

    observations = (
        {
            "panel_id": "direct",
            "source_asset_id": "scene-a",
            "visible_facts": ["The queen gives a court order."],
            "dialogue_or_ocr": [],
            "inferences": [],
        },
        {
            "panel_id": "safe",
            "source_asset_id": "scene-b",
            "visible_facts": ["A queen and man face a marriage consequence."],
            "dialogue_or_ocr": ["MUST MARRY HER!"],
            "inferences": [],
        },
    )
    request = va.VisionChapterSynthesisRequest(
        analysis_run_id="visual-story-hit",
        instruction_version="v",
        instruction_sha256="b" * 64,
        instruction_text="x",
        expected_panel_ids=("direct", "safe"),
        coverage_manifest={},
        ordered_observations=observations,
        chunks=(),
        narrative_profile_id="retention_story_v1",
        preferred_visual_panel_ids=("safe",),
        preferred_visual_panel_ids_by_section={"hook": ("safe",)},
    )
    output = {
        "evidence_graph": {
            "claims": [
                {
                    "claim_id": "c1",
                    "text": "The queen's order creates a marriage consequence.",
                    "qualification": "Grounded court outcome.",
                    "evidence_panel_ids": ["direct"],
                }
            ]
        },
        "script_passages": [
            {
                "passage_id": "p1",
                "editorial_role": "hook",
                "text": "The queen's order creates an immediate marriage consequence.",
                "claim_ids": ["c1"],
                "evidence_panel_ids": ["direct"],
            }
        ],
    }
    assert va._retention_visual_story_alignment_missing(output, request) == ()


def test_visual_story_alignment_failure_retries_story_selection(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    request = VisionChapterSynthesisRequest(
        analysis_run_id="visual-story-retry",
        instruction_version="v",
        instruction_sha256="c" * 64,
        instruction_text="x",
        expected_panel_ids=("safe",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            if len(self.requests) == 1:
                raise VisionResponseInvalid(
                    validation_subtype="retention_visual_story_alignment_missing"
                )
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_args: None)
    monkeypatch.setattr(
        pipeline, "_validated_synthesis_cache_output", lambda output, _request: output
    )
    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}
    retry = provider.requests[1]
    assert retry.retry_visual_story_alignment is True
    assert retry.retry_causal_arc is False
    assert retry.retry_passages is None
    assert retry.retry_visual_selection is False


def test_retention_provider_validates_structure_and_visual_before_text_checks():
    import inspect

    from app.services import vision_adapter as va

    source = inspect.getsource(va.OpenAICompatibleVisionProvider.synthesize)
    structural = source.index("validate_text_checks=False")
    visual = source.index("validate_synthesis_visual_selection(validated_result, request)")
    final_contract = source.index(
        "analyzer_contract.validate_analyzer_output(\n                    validated_result,"
    )

    assert structural < visual < final_contract


def test_visual_story_retry_preserves_active_structural_repairs(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    locked = tuple(
        {
            "passage_id": f"p{i}",
            "editorial_role": "beat",
            "text": "Grounded narration stays on the same causal arc.",
            "claim_ids": ["c1"],
            "evidence_panel_ids": ["a"],
        }
        for i in range(1, 5)
    )
    diagnostics = {"claim_id": "c1", "candidate_panels": [{"panel_id": "a"}]}
    request = VisionChapterSynthesisRequest(
        analysis_run_id="sticky-visual",
        instruction_version="v",
        instruction_sha256="e" * 64,
        instruction_text="x",
        expected_panel_ids=("a",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
        retry_causal_arc=True,
        retry_claim_semantic_grounding=True,
        retry_claim_semantic_diagnostics=diagnostics,
        retry_local_claim_grounding=True,
        retry_passages=locked,
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            if len(self.requests) == 1:
                raise VisionResponseInvalid(
                    validation_subtype="retention_visual_story_alignment_missing"
                )
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_: None)
    monkeypatch.setattr(
        pipeline, "_validated_synthesis_cache_output", lambda output, _request: output
    )
    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}
    retry = provider.requests[1]
    assert retry.retry_visual_story_alignment is True
    assert retry.retry_causal_arc is True
    assert retry.retry_claim_semantic_grounding is True
    assert retry.retry_claim_semantic_diagnostics == diagnostics
    assert retry.retry_local_claim_grounding is True
    assert retry.retry_passages == locked


def test_retry_passage_capture_supports_retention_four_to_six():
    from app.services import vision_adapter as va

    def candidate(count):
        return {"script_passages": [{"passage_id": f"p{i}"} for i in range(count)]}

    assert len(va._retry_passages_from_candidate(candidate(4))) == 4
    assert len(va._retry_passages_from_candidate(candidate(5))) == 5
    assert len(va._retry_passages_from_candidate(candidate(6))) == 6
    assert va._retry_passages_from_candidate(candidate(3)) is None
    assert va._retry_passages_from_candidate(candidate(7)) is None


def test_text_only_lock_discards_provider_semantic_drift_for_word_count():
    from app.services import vision_adapter as va

    locked = {
        "observations": [{"panel_id": "a"}],
        "coverage_manifest": {"coverage": "locked"},
        "continuity_ledger": {"causal_links": [{"from_panel_id": "a", "to_panel_id": "b"}]},
        "evidence_graph": {"claims": [{"claim_id": "c1", "evidence_panel_ids": ["a"]}]},
        "narrative_outline": {"ending_kind": "consequence"},
        "script_passages": [
            {
                "passage_id": "p1",
                "editorial_role": "hook",
                "text": "Old copied wording one.",
                "claim_ids": ["c1"],
                "evidence_panel_ids": ["a"],
            },
            {
                "passage_id": "p2",
                "editorial_role": "payoff",
                "text": "Old copied wording two.",
                "claim_ids": ["c1"],
                "evidence_panel_ids": ["a"],
            },
            {
                "passage_id": "p3",
                "editorial_role": "beat",
                "text": "Old copied wording three.",
                "claim_ids": ["c1"],
                "evidence_panel_ids": ["a"],
            },
            {
                "passage_id": "p4",
                "editorial_role": "ending",
                "text": "Old copied wording four.",
                "claim_ids": ["c1"],
                "evidence_panel_ids": ["a"],
            },
        ],
    }
    request = va.VisionChapterSynthesisRequest(
        analysis_run_id="text-lock",
        instruction_version="v",
        instruction_sha256="a" * 64,
        instruction_text="x",
        expected_panel_ids=("a", "b"),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
        retry_word_counts=(10, 20, 20, 20),
        retry_text_only_locked_output=locked,
    )
    drifted = {
        "continuity_ledger": {"causal_links": [{"from_panel_id": "x", "to_panel_id": "y"}]},
        "evidence_graph": {"claims": [{"claim_id": "evil", "evidence_panel_ids": ["b"]}]},
        "script_passages": [
            {"passage_id": f"p{i}", "text": f"Fresh paraphrase number {i}."} for i in range(1, 5)
        ],
    }
    repaired = va._apply_text_only_retry_lock(drifted, request)
    assert repaired["evidence_graph"] == locked["evidence_graph"]
    assert repaired["continuity_ledger"] == locked["continuity_ledger"]
    assert repaired["narrative_outline"] == locked["narrative_outline"]
    assert [p["claim_ids"] for p in repaired["script_passages"]] == [["c1"]] * 4
    assert [p["text"] for p in repaired["script_passages"]] == [
        "Fresh paraphrase number 1.",
        "Fresh paraphrase number 2.",
        "Fresh paraphrase number 3.",
        "Fresh paraphrase number 4.",
    ]


def test_anti_copy_retry_carries_structural_lock(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    locked_output = {
        "script_passages": [{"passage_id": f"p{i}"} for i in range(1, 5)],
        "evidence_graph": {"claims": ["locked"]},
    }
    locked_passages = tuple(
        {
            "passage_id": f"p{i}",
            "editorial_role": "beat",
            "text": "Copied wording here.",
            "claim_ids": ["c1"],
            "evidence_panel_ids": ["a"],
        }
        for i in range(1, 5)
    )
    request = VisionChapterSynthesisRequest(
        analysis_run_id="lock-chain",
        instruction_version="v",
        instruction_sha256="b" * 64,
        instruction_text="x",
        expected_panel_ids=("a",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            if len(self.requests) == 1:
                raise VisionResponseInvalid(
                    validation_subtype="script_passage_copies_source_dialogue",
                    retry_passages=locked_passages,
                    retry_locked_output=locked_output,
                )
            assert active_request.retry_dialogue_paraphrase is True
            assert active_request.retry_text_only_locked_output == locked_output
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_: None)
    monkeypatch.setattr(
        pipeline, "_validated_synthesis_cache_output", lambda output, _request: output
    )
    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}


def test_text_only_finishing_reserve_extends_attempt_twelve_only(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    locked_passages = tuple(
        {
            "passage_id": f"p{i}",
            "editorial_role": "hook" if i == 1 else f"beat{i}",
            "text": f"Grounded passage {i} needs fresh wording.",
            "claim_ids": [f"c{i}"],
            "evidence_panel_ids": ["a"],
        }
        for i in range(1, 5)
    )
    locked_output = {
        "script_passages": [dict(item) for item in locked_passages],
        "evidence_graph": {"claims": [{"claim_id": "c1", "evidence_panel_ids": ["a"]}]},
    }
    request = VisionChapterSynthesisRequest(
        analysis_run_id="text-tail",
        instruction_version="v",
        instruction_sha256="c" * 64,
        instruction_text="x",
        expected_panel_ids=("a",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            current = len(self.requests)
            if current <= pipeline._VISION_SYNTHESIS_MAX_ATTEMPTS - 1:
                raise VisionResponseInvalid(
                    validation_subtype="claim_evidence_lacks_semantic_anchor",
                    selection_diagnostics={"claim_id": f"progress-{current}"},
                )
            if current == pipeline._VISION_SYNTHESIS_MAX_ATTEMPTS:
                raise VisionResponseInvalid(
                    validation_subtype="script_passage_copies_source_dialogue",
                    retry_passages=locked_passages,
                    retry_locked_output=locked_output,
                )
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_: None)
    monkeypatch.setattr(
        pipeline, "_validated_synthesis_cache_output", lambda output, _request: output
    )
    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}
    assert len(provider.requests) == pipeline._VISION_SYNTHESIS_MAX_ATTEMPTS + 1
    assert provider.requests[-1].retry_text_only_locked_output == locked_output


def test_structural_failure_at_attempt_twelve_does_not_use_text_reserve(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    request = VisionChapterSynthesisRequest(
        analysis_run_id="structural-cap",
        instruction_version="v",
        instruction_sha256="d" * 64,
        instruction_text="x",
        expected_panel_ids=("a",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            raise VisionResponseInvalid(
                validation_subtype="claim_evidence_lacks_semantic_anchor",
                selection_diagnostics={"claim_id": f"progress-{len(self.requests)}"},
            )

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_: None)
    monkeypatch.setattr(
        pipeline, "_validated_synthesis_cache_output", lambda output, _request: output
    )
    import pytest

    with pytest.raises(VisionResponseInvalid):
        pipeline._synthesize_with_cache(provider, request)
    assert len(provider.requests) == pipeline._VISION_SYNTHESIS_MAX_ATTEMPTS


def test_visual_story_retry_carries_candidate_diagnostics(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    diag = {
        "missing_roles": ["pressure"],
        "passages": [{"passage_id": "p2", "candidate_panels": [{"panel_id": "safe"}]}],
    }
    request = VisionChapterSynthesisRequest(
        analysis_run_id="visual-diag",
        instruction_version="v",
        instruction_sha256="a" * 64,
        instruction_text="x",
        expected_panel_ids=("safe",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
        retry_causal_arc=True,
        retry_claim_semantic_grounding=True,
        retry_claim_semantic_diagnostics={"claim_id": "c1"},
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, r):
            self.requests.append(r)
            if len(self.requests) == 1:
                raise VisionResponseInvalid(
                    validation_subtype="retention_visual_story_alignment_missing",
                    selection_diagnostics=diag,
                )
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_: None)
    monkeypatch.setattr(
        pipeline, "_validated_synthesis_cache_output", lambda output, _request: output
    )

    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}
    retry = provider.requests[1]
    assert retry.retry_visual_story_alignment is True
    assert retry.retry_visual_story_diagnostics == diag
    assert retry.retry_causal_arc is True
    assert retry.retry_claim_semantic_grounding is True
    assert retry.retry_claim_semantic_diagnostics == {"claim_id": "c1"}


def test_unknown_continuity_entity_retries_projection_without_dropping_sticky_constraints(
    monkeypatch,
):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    request = VisionChapterSynthesisRequest(
        analysis_run_id="entity-lineage",
        instruction_version="v",
        instruction_sha256="b" * 64,
        instruction_text="x",
        expected_panel_ids=("a",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
        retry_causal_arc=True,
        retry_visual_story_alignment=True,
        retry_visual_story_diagnostics={"missing_roles": ["pressure"]},
        retry_claim_semantic_grounding=True,
        retry_claim_semantic_diagnostics={"claim_id": "c1"},
        retry_local_claim_grounding=True,
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, r):
            self.requests.append(r)
            if len(self.requests) == 1:
                raise VisionResponseInvalid(
                    validation_subtype="state_change_references_an_unknown_entity"
                )
            return {"accepted": True}

    provider = Provider()

    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_: None)
    monkeypatch.setattr(
        pipeline, "_validated_synthesis_cache_output", lambda output, _request: output
    )
    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}
    retry = provider.requests[1]
    assert retry.retry_projection_contract is True
    assert retry.retry_causal_arc is True
    assert retry.retry_visual_story_alignment is True
    assert retry.retry_visual_story_diagnostics == {"missing_roles": ["pressure"]}
    assert retry.retry_claim_semantic_grounding is True
    assert retry.retry_claim_semantic_diagnostics == {"claim_id": "c1"}
    assert retry.retry_local_claim_grounding is True


def test_visual_story_retry_releases_causal_anchor_when_opening_is_unframeable(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    locked = tuple({"passage_id": f"p{i}"} for i in range(1, 5))
    diag = {
        "missing_roles": ["hook"],
        "passages": [{"passage_id": "p1", "section": "hook", "candidate_panels": []}],
    }
    request = VisionChapterSynthesisRequest(
        analysis_run_id="visual-release",
        instruction_version="v",
        instruction_sha256="c" * 64,
        instruction_text="x",
        expected_panel_ids=("safe",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
        retry_causal_arc=True,
        retry_passages=locked,
        retry_claim_semantic_grounding=True,
        retry_claim_semantic_diagnostics={"claim_id": "old"},
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, r):
            self.requests.append(r)
            if len(self.requests) == 1:
                raise VisionResponseInvalid(
                    validation_subtype="retention_visual_story_alignment_missing",
                    selection_diagnostics=diag,
                )
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_: None)
    monkeypatch.setattr(
        pipeline, "_validated_synthesis_cache_output", lambda output, _request: output
    )
    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}
    retry = provider.requests[1]
    assert retry.retry_visual_story_alignment is True
    assert retry.retry_causal_arc is False
    assert retry.retry_passages is None
    assert retry.retry_claim_semantic_grounding is True
    assert retry.retry_claim_semantic_diagnostics is None


def test_pipeline_source_inputs_preserve_global_asset_order(monkeypatch):
    from app.services import pipeline

    asset = SimpleNamespace(
        id="asset-chronology",
        source_bounds_json={"x": 0, "y": 0, "width": 32, "height": 32},
        original_width=32,
        original_height=32,
        width=32,
        height=32,
        storage_key="unused",
        original_checksum="checksum",
        checksum="checksum",
        strip_order=0,
        region_order=0,
        source_family="chapter-184",
        order_index=47,
    )
    monkeypatch.setattr(pipeline.storage, "read_bytes", lambda _key: b"payload")
    inputs, by_id = pipeline._build_source_inputs((asset,))
    assert by_id[asset.id] is asset
    assert len(inputs) == 1
    assert inputs[0].source_sequence_order == 47
    assert inputs[0].strip_order == 0
    assert inputs[0].region_order == 0


def test_stable_observation_cache_remaps_reordered_panel_lineage(tmp_path, monkeypatch):
    from app.services import pipeline, visual_scoring
    from app.services.vision_adapter import VisionObservationRequest

    monkeypatch.setattr(pipeline.settings, "data_dir", tmp_path)

    class Provider:
        model_id = "model"
        endpoint = "https://provider.invalid"

    old_panel = {
        "panel_id": "old-panel",
        "source_asset_id": "asset-stable",
        "strip_region_id": "old-panel",
        "source_order": 99,
        "region_bounds": {"x": 0, "y": 10, "width": 32, "height": 48},
        "coverage_map_version": "vision-coverage-v2",
        "coverage_map_hash": "a" * 64,
        "mime_type": "image/jpeg",
        "payload": b"same-rendered-panel-bytes",
    }
    new_panel = {
        **old_panel,
        "panel_id": "new-panel",
        "strip_region_id": "new-panel",
        "source_order": 7,
        "coverage_map_hash": "b" * 64,
    }
    old_request = VisionObservationRequest(
        analysis_run_id="old",
        instruction_version="v",
        instruction_sha256="c" * 64,
        chunk_index=8,
        panels=(old_panel,),
        visual_instruction_version="visual-v",
        visual_instruction_sha256="d" * 64,
    )

    evidence = visual_scoring.unknown_visual_evidence(
        panel_id="old-panel",
        source_asset_id="asset-stable",
        source_order=99,
        reason="test unknown geometry",
    )
    row = {
        "panel_id": "old-panel",
        "visible_facts": ["A character is visible."],
        "dialogue_or_ocr": [],
        "inferences": [],
        "uncertainties": [],
        "entities": [],
        "state_changes": [],
        "causal_links": [],
        "evidence_refs": ["old-panel"],
        "visual_evidence": visual_scoring.panel_visual_evidence_json(evidence),
    }
    pipeline._store_stable_observation_row(Provider(), old_request, old_panel, row)
    new_request = VisionObservationRequest(
        analysis_run_id="new",
        instruction_version="v",
        instruction_sha256="c" * 64,
        chunk_index=0,
        panels=(new_panel,),
        visual_instruction_version="visual-v",
        visual_instruction_sha256="d" * 64,
    )
    loaded = pipeline._load_stable_observation_row(
        Provider(), new_request, "new-panel", new_panel, require_visual_evidence=True
    )
    assert loaded is not None
    assert loaded["panel_id"] == "new-panel"
    assert loaded["evidence_refs"] == ["new-panel"]
    assert loaded["visible_facts"] == row["visible_facts"]
    assert loaded["visual_evidence"]["panel_id"] == "new-panel"
    assert loaded["visual_evidence"]["source_order"] == 7
    assert loaded["visual_evidence"]["source_asset_id"] == "asset-stable"
    assert "evidence_hash" not in loaded["visual_evidence"]
    observation = dict(loaded)
    observation["visual_evidence"] = {
        **loaded["visual_evidence"],
        "contract_version": visual_scoring.VISUAL_EVIDENCE_CONTRACT_VERSION,
        "evidence_hash": "",
    }
    persisted, _ = visual_scoring.ensure_panel_visual_evidence(
        observation, panel_id="new-panel", source_asset_id="asset-stable", source_order=7
    )
    assert persisted["visual_evidence"]["evidence_hash"] != row["visual_evidence"]["evidence_hash"]


def test_stable_observation_remap_keeps_known_geometry_in_provider_json_shape():
    from app.services import pipeline
    from app.services.vision_adapter import validate_visual_evidence_observation

    row = {
        "panel_id": "old-panel",
        "visible_facts": ["A face and a speech balloon are visible."],
        "dialogue_or_ocr": [],
        "inferences": [],
        "uncertainties": [],
        "entities": [],
        "state_changes": [],
        "causal_links": [],
        "evidence_refs": ["old-panel"],
        "visual_evidence": {
            "contract_version": "COLOR_AGNOSTIC_BALLOON_FREE_V1",
            "panel_id": "old-panel",
            "source_asset_id": "asset",
            "source_order": 9,
            "balloon_regions": [
                {
                    "region_id": "b1",
                    "kind": "speech_balloon",
                    "normalized_bbox": [0.1, 0.1, 0.4, 0.3],
                    "normalized_polygon": [],
                    "confidence": 0.9,
                    "evidence_source": "visual_geometry",
                    "mask_status": "known_nonempty",
                }
            ],
            "protected_regions": [
                {
                    "region_id": "f1",
                    "kind": "face",
                    "normalized_bbox": [0.2, 0.35, 0.55, 0.8],
                    "normalized_polygon": [],
                    "confidence": 0.88,
                    "evidence_source": "visual_geometry",
                    "required": True,
                    "minimum_coverage": 0.8,
                }
            ],
            "balloon_mask_status": "known_nonempty",
            "mask_confidence": 0.9,
            "evidence_source": "visual_geometry",
            "mask_reason": "visible geometry",
        },
    }
    panel = {
        "panel_id": "new-panel",
        "source_asset_id": "asset",
        "source_order": 3,
    }
    remapped = pipeline._remap_stable_observation_row(row, "new-panel", panel)
    visual = remapped["visual_evidence"]
    assert isinstance(visual["balloon_regions"][0]["normalized_bbox"], list)
    assert isinstance(visual["protected_regions"][0]["normalized_bbox"], list)
    validated = validate_visual_evidence_observation(
        visual,
        expected_panel_id="new-panel",
        expected_source_asset_id="asset",
        expected_source_order=3,
    )
    assert validated["balloon_regions"][0]["normalized_bbox"] == [0.1, 0.1, 0.4, 0.3]


def test_retention_semantic_morphology_accepts_marriage_marry_and_require_required():
    from app.services import analyzer_contract

    graph = {
        "claims": [
            {
                "claim_id": "c1",
                "claim_type": "fact",
                "text": "Royal law requires marriage.",
                "qualification": "The rule is directly stated.",
                "evidence_panel_ids": ["panel-1"],
            }
        ]
    }
    observations = [
        {
            "panel_id": "panel-1",
            "visible_facts": [],
            "dialogue_or_ocr": ["ACCORDING TO ROYAL LAW, YOU ARE REQUIRED TO MARRY HER."],
            "inferences": [],
            "uncertainties": [],
        }
    ]
    analyzer_contract._validate_retention_claim_semantic_grounding(graph, observations)


def test_retention_semantic_candidates_use_source_index_when_source_order_missing():
    from app.services import analyzer_contract

    anchors = {"mana"}
    observations = {
        "late": {
            "panel_id": "late",
            "source_index": 20,
            "visible_facts": ["Mana glows."],
            "dialogue_or_ocr": [],
            "inferences": [],
            "uncertainties": [],
        },
        "early": {
            "panel_id": "early",
            "source_index": 5,
            "visible_facts": ["Mana pulses."],
            "dialogue_or_ocr": [],
            "inferences": [],
            "uncertainties": [],
        },
    }
    rows = analyzer_contract._retention_semantic_candidate_panels(anchors, observations)
    assert [row["panel_id"] for row in rows[:2]] == ["early", "late"]
    assert [row["source_order"] for row in rows[:2]] == [5, 20]


def test_visual_story_diagnostics_marks_replacement_when_safe_candidates_do_not_support_old_beat():
    from app.services import vision_adapter
    from app.services.vision_adapter import VisionChapterSynthesisRequest

    request = VisionChapterSynthesisRequest(
        analysis_run_id="diag",
        instruction_version="v",
        instruction_sha256="a" * 64,
        instruction_text="x",
        expected_panel_ids=("claim-panel", "safe-panel"),
        coverage_manifest={},
        chunks=(),
        narrative_profile_id="retention_story_v1",
        ordered_observations=(
            {
                "panel_id": "claim-panel",
                "source_asset_id": "asset-a",
                "source_index": 1,
                "visible_facts": [],
                "dialogue_or_ocr": ["THE QUEEN ORDERS A MONTH OF TREATMENT."],
                "inferences": [],
                "uncertainties": [],
            },
            {
                "panel_id": "safe-panel",
                "source_asset_id": "asset-b",
                "source_index": 2,
                "visible_facts": ["A swordswoman leaps through the air."],
                "dialogue_or_ocr": [],
                "inferences": [],
                "uncertainties": [],
            },
        ),
        preferred_visual_panel_ids=("safe-panel",),
        preferred_visual_panel_ids_by_section={"hook": ("safe-panel",)},
    )
    output = {
        "evidence_graph": {
            "claims": [
                {
                    "claim_id": "c1",
                    "text": "The queen orders treatment.",
                    "qualification": "direct order",
                    "evidence_panel_ids": ["claim-panel"],
                }
            ]
        },
        "script_passages": [
            {
                "passage_id": "p1",
                "editorial_role": "hook",
                "text": "The queen orders nonstop treatment.",
                "claim_ids": ["c1"],
                "evidence_panel_ids": ["claim-panel"],
            }
        ],
    }
    diag = vision_adapter._retention_visual_story_alignment_diagnostics(output, request, ("hook",))
    row = diag["passages"][0]
    assert row["replacement_required"] is True
    assert row["candidate_panels"][0]["panel_id"] == "safe-panel"
    assert "text" not in row
    assert "claim_ids" not in row
    assert "claim_evidence_panel_ids" not in row
    assert "Discard the rejected beat completely" in row["replacement_instruction"]


def test_semantic_candidate_shortlist_preserves_late_rare_anchor():
    from app.services import analyzer_contract

    observations = {
        f"queen-{index}": {
            "panel_id": f"queen-{index}",
            "source_index": index,
            "visible_facts": ["The queen is visible."],
            "dialogue_or_ocr": [],
            "inferences": [],
            "uncertainties": [],
        }
        for index in range(12)
    }
    observations["marry-late"] = {
        "panel_id": "marry-late",
        "source_index": 99,
        "visible_facts": [],
        "dialogue_or_ocr": ["YOU MUST MARRY HER."],
        "inferences": [],
        "uncertainties": [],
    }
    rows = analyzer_contract._retention_semantic_candidate_panels({"queen", "marry"}, observations)
    assert "marry-late" in [row["panel_id"] for row in rows]
    rare = next(row for row in rows if row["panel_id"] == "marry-late")
    assert rare["overlap_anchors"] == ["marry"]


def test_semantic_meta_words_are_not_story_anchors():
    from app.services import analyzer_contract

    tokens = analyzer_contract._semantic_anchor_tokens(
        "directly supported context grounded inference activation consequence marriage"
    )
    assert tokens == {"marry"}


def test_semantic_question_mark_is_a_questioning_anchor():
    from app.services import analyzer_contract

    tokens = analyzer_contract._semantic_anchor_tokens("JUST BECAUSE YOU LOVE ME?")
    assert {"question", "love"}.issubset(tokens)


def test_retention_semantic_grounding_accepts_explicit_question_plus_local_force_evidence():
    from app.services import analyzer_contract

    graph = {
        "claims": [
            {
                "claim_id": "claim_rejection",
                "claim_type": "fact",
                "text": "Penelope questions why he forces his love on her.",
                "qualification": "direct dialogue",
                "evidence_panel_ids": ["question", "force"],
            }
        ]
    }
    observations = [
        {
            "panel_id": "question",
            "source_index": 108,
            "visible_facts": ["Pink-haired female character."],
            "dialogue_or_ocr": ["JUST BECAUSE YOU LOVE ME?"],
            "inferences": [],
            "uncertainties": [],
        },
        {
            "panel_id": "force",
            "source_index": 112,
            "visible_facts": [],
            "dialogue_or_ocr": [
                "AND YOU'RE FORCING YOUR RELENTLESS LOVE ON ME WHILE PROTECTING ME WHEN I NEVER ASKED YOU TO."
            ],
            "inferences": [],
            "uncertainties": [],
        },
    ]
    analyzer_contract._validate_retention_claim_semantic_grounding(graph, observations)


def test_visual_support_tokens_normalize_story_morphology():
    from app.services import vision_adapter

    tokens = vision_adapter._visual_support_tokens(
        "Marriage required purification after healing exhaustion."
    )
    assert {"marry", "require", "purify", "heal", "exhaust"}.issubset(tokens)


def test_visual_story_diagnostics_recognize_marriage_marry_safe_support():
    from app.services import vision_adapter
    from app.services.vision_adapter import VisionChapterSynthesisRequest

    request = VisionChapterSynthesisRequest(
        analysis_run_id="visual-morph",
        instruction_version="v",
        instruction_sha256="e" * 64,
        instruction_text="x",
        expected_panel_ids=("law", "safe-marry"),
        coverage_manifest={},
        chunks=(),
        narrative_profile_id="retention_story_v1",
        ordered_observations=(
            {
                "panel_id": "law",
                "source_asset_id": "a",
                "source_index": 1,
                "visible_facts": [],
                "dialogue_or_ocr": ["ROYAL LAW APPLIES."],
                "inferences": [],
                "uncertainties": [],
            },
            {
                "panel_id": "safe-marry",
                "source_asset_id": "b",
                "source_index": 2,
                "visible_facts": ["A man and woman hold hands."],
                "dialogue_or_ocr": ["MUST MARRY HER!"],
                "inferences": [],
                "uncertainties": [],
            },
        ),
        preferred_visual_panel_ids=("safe-marry",),
        preferred_visual_panel_ids_by_section={"hook": ("safe-marry",)},
    )

    output = {
        "evidence_graph": {
            "claims": [
                {
                    "claim_id": "c1",
                    "text": "Royal law requires marriage.",
                    "qualification": "law consequence",
                    "evidence_panel_ids": ["law"],
                }
            ]
        },
        "script_passages": [
            {
                "passage_id": "p1",
                "editorial_role": "hook",
                "text": "Royal law suddenly turns this into a marriage.",
                "claim_ids": ["c1"],
                "evidence_panel_ids": ["law"],
            }
        ],
    }
    diag = vision_adapter._retention_visual_story_alignment_diagnostics(output, request, ("hook",))
    row = diag["passages"][0]
    assert row["replacement_required"] is False
    candidate = next(item for item in row["candidate_panels"] if item["panel_id"] == "safe-marry")
    assert "marry" in candidate["overlap_tokens"]


def test_preferred_visual_evidence_digest_is_safe_only_and_chronological():
    from app.services.vision_adapter import (
        VisionChapterSynthesisRequest,
        _preferred_visual_evidence_digest,
    )

    observations = (
        {
            "panel_id": "unsafe",
            "source_index": 1,
            "visible_facts": ["Unsafe fact"],
            "dialogue_or_ocr": [],
        },
        {
            "panel_id": "late",
            "source_index": 8,
            "visible_facts": ["Late safe action"],
            "dialogue_or_ocr": ["LATE"],
        },
        {
            "panel_id": "early",
            "source_index": 3,
            "visible_facts": ["Early safe reaction"],
            "dialogue_or_ocr": ["EARLY"],
        },
    )
    request = VisionChapterSynthesisRequest(
        analysis_run_id="safe-digest",
        instruction_version="v",
        instruction_sha256="f" * 64,
        instruction_text="x",
        expected_panel_ids=("unsafe", "late", "early"),
        coverage_manifest={},
        ordered_observations=observations,
        chunks=(),
        preferred_visual_panel_ids=("late", "early", "unsafe"),
        preferred_visual_panel_ids_by_section={
            "hook": ("early",),
            "setup": ("late",),
            "cta": ("late",),
        },
    )
    rows = _preferred_visual_evidence_digest(request)
    assert [row["panel_id"] for row in rows] == ["early", "late"]
    assert rows[0]["safe_sections"] == ["hook"]
    assert rows[1]["safe_sections"] == ["setup", "cta"]
    assert rows[0]["visible_facts"] == ["Early safe reaction"]
    assert "unsafe" not in [row["panel_id"] for row in rows]


def test_retention_wire_includes_safe_visual_digest_and_visual_first_instruction():
    from app.services import narrative_identity, vision_adapter

    profile = narrative_identity.get_narrative_identity("retention_story_v1")
    version, digest, instruction = narrative_identity.load_narrative_instruction(profile.profile_id)
    request = vision_adapter.VisionChapterSynthesisRequest(
        analysis_run_id="safe-wire",
        instruction_version=version,
        instruction_sha256=digest,
        instruction_text=instruction,
        expected_panel_ids=("safe",),
        coverage_manifest={},
        ordered_observations=(
            {
                "panel_id": "safe",
                "source_index": 4,
                "visible_facts": ["A woman raises her sword."],
                "dialogue_or_ocr": ["MUST MARRY HER!"],
                "inferences": [],
                "uncertainties": [],
            },
        ),
        chunks=(),
        narrative_profile_id=profile.profile_id,
        narrative_profile_version=profile.profile_version,
        narrative_profile_sha256=profile.contract_sha256,
        preferred_visual_panel_ids=("safe",),
        preferred_visual_panel_ids_by_section={"hook": ("safe",), "setup": ("safe",)},
    )
    payload = vision_adapter._build_synthesis_payload(
        request, request.expected_panel_ids, "mock", profile
    )
    content = payload["messages"][1]["content"]
    assert vision_adapter.SYNTHESIS_WIRE_CONTRACT_VERSION == "vision-synthesis-wire-v13"
    assert '"preferred_visual_evidence"' in content
    assert '"safe_sections":["hook","setup"]' in content
    assert "Choose the production-safe visual anchor BEFORE writing each passage claim" in content
    assert "hook MUST center on a preferred_visual_evidence row" in content


def test_visual_story_retry_accepts_nearby_safe_scene_anchor():
    from app.services import vision_adapter as va

    obs = (
        {
            "panel_id": "direct",
            "source_asset_id": "a",
            "source_index": 10,
            "visible_facts": [],
            "dialogue_or_ocr": ["THE QUEEN ORDERS A MONTH OF TREATMENT."],
            "inferences": [],
        },
        {
            "panel_id": "safe",
            "source_asset_id": "b",
            "source_index": 13,
            "visible_facts": ["A red-haired queen raises her hand as magic glows."],
            "dialogue_or_ocr": [],
            "inferences": [],
        },
    )
    req = va.VisionChapterSynthesisRequest(
        analysis_run_id="x",
        instruction_version="v",
        instruction_sha256="a" * 64,
        instruction_text="x",
        expected_panel_ids=("direct", "safe"),
        coverage_manifest={},
        ordered_observations=obs,
        chunks=(),
        narrative_profile_id="retention_story_v1",
        preferred_visual_panel_ids=("safe",),
        preferred_visual_panel_ids_by_section={"hook": ("safe",)},
        retry_visual_story_alignment=True,
    )
    out = {
        "evidence_graph": {
            "claims": [
                {
                    "claim_id": "c",
                    "text": "The queen orders treatment.",
                    "qualification": "Grounded.",
                    "evidence_panel_ids": ["direct"],
                }
            ]
        },
        "script_passages": [
            {
                "passage_id": "p",
                "editorial_role": "hook",
                "text": "The queen orders treatment.",
                "claim_ids": ["c"],
                "evidence_panel_ids": ["direct"],
            }
        ],
    }
    assert va._retention_visual_story_alignment_missing(out, req) == ()


def test_visual_story_replacement_candidates_prefer_nearby_chronology():
    from app.services import vision_adapter as va

    obs = (
        {
            "panel_id": "direct",
            "source_asset_id": "a",
            "source_index": 100,
            "visible_facts": ["The queen needs treatment."],
            "dialogue_or_ocr": [],
            "inferences": [],
        },
        {
            "panel_id": "far",
            "source_asset_id": "b",
            "source_index": 10,
            "visible_facts": ["A swordswoman attacks."],
            "dialogue_or_ocr": [],
            "inferences": [],
        },
        {
            "panel_id": "near",
            "source_asset_id": "c",
            "source_index": 103,
            "visible_facts": ["A woman collapses in pain."],
            "dialogue_or_ocr": [],
            "inferences": [],
        },
    )
    req = va.VisionChapterSynthesisRequest(
        analysis_run_id="x",
        instruction_version="v",
        instruction_sha256="b" * 64,
        instruction_text="x",
        expected_panel_ids=("direct", "far", "near"),
        coverage_manifest={},
        ordered_observations=obs,
        chunks=(),
        narrative_profile_id="retention_story_v1",
        preferred_visual_panel_ids=("far", "near"),
        preferred_visual_panel_ids_by_section={"hook": ("far", "near")},
    )
    out = {
        "evidence_graph": {
            "claims": [
                {
                    "claim_id": "c",
                    "text": "The queen needs treatment.",
                    "qualification": "Grounded.",
                    "evidence_panel_ids": ["direct"],
                }
            ]
        },
        "script_passages": [
            {
                "passage_id": "p",
                "editorial_role": "hook",
                "text": "The queen needs treatment.",
                "claim_ids": ["c"],
                "evidence_panel_ids": ["direct"],
            }
        ],
    }
    diag = va._retention_visual_story_alignment_diagnostics(out, req, ("hook",))
    row = diag["passages"][0]
    assert row["replacement_required"] is True
    assert row["candidate_panels"][0]["panel_id"] == "near"
    assert row["candidate_panels"][0]["source_index"] == 103
    assert row["candidate_panels"][0]["chronology_distance"] == 3


def test_visual_story_retry_rejects_direct_but_semantically_unrelated_safe_evidence():
    from app.services import vision_adapter as va

    obs = (
        {
            "panel_id": "detail",
            "source_asset_id": "a",
            "source_index": 100,
            "visible_facts": [],
            "dialogue_or_ocr": ["TRUSTED MANA CAN PURIFY THE CURSE."],
            "inferences": [],
        },
        {
            "panel_id": "safe",
            "source_asset_id": "b",
            "source_index": 110,
            "visible_facts": ["A gray-haired man rests with eyes closed."],
            "dialogue_or_ocr": [],
            "inferences": [],
        },
    )
    req = va.VisionChapterSynthesisRequest(
        analysis_run_id="x",
        instruction_version="v",
        instruction_sha256="c" * 64,
        instruction_text="x",
        expected_panel_ids=("detail", "safe"),
        coverage_manifest={},
        ordered_observations=obs,
        chunks=(),
        narrative_profile_id="retention_story_v1",
        preferred_visual_panel_ids=("safe",),
        preferred_visual_panel_ids_by_section={"hook": ("safe",)},
        retry_visual_story_alignment=True,
    )
    out = {
        "evidence_graph": {
            "claims": [
                {
                    "claim_id": "c",
                    "text": "Trusted mana can purify the curse.",
                    "qualification": "Grounded.",
                    "evidence_panel_ids": ["detail", "safe"],
                }
            ]
        },
        "script_passages": [
            {
                "passage_id": "p",
                "editorial_role": "hook",
                "text": "Trusted mana can purify the curse.",
                "claim_ids": ["c"],
                "evidence_panel_ids": ["detail", "safe"],
            }
        ],
    }
    assert va._retention_visual_story_alignment_missing(out, req) == ("hook",)
    diag = va._retention_visual_story_alignment_diagnostics(out, req, ("hook",))
    assert diag["passages"][0]["replacement_required"] is True


def test_retention_semantic_grounding_rejects_distant_stitched_evidence():
    from app.services import analyzer_contract

    graph = {
        "claims": [
            {
                "claim_id": "law",
                "claim_type": "fact",
                "text": "Royal law requires marriage after sharing the queen chamber.",
                "qualification": "Direct rule.",
                "evidence_panel_ids": ["law", "marry"],
            }
        ]
    }
    observations = [
        {
            "panel_id": "law",
            "source_index": 10,
            "visible_facts": [],
            "dialogue_or_ocr": [
                "ACCORDING TO ROYAL LAW, ANYONE SHARING THE ROYAL BEDCHAMBER WITH THE QUEEN..."
            ],
            "inferences": [],
            "uncertainties": [],
        },
        {
            "panel_id": "marry",
            "source_index": 40,
            "visible_facts": [],
            "dialogue_or_ocr": ["MUST MARRY HER!"],
            "inferences": [],
            "uncertainties": [],
        },
    ]
    with pytest.raises(analyzer_contract.AnalyzerContractError) as exc_info:
        analyzer_contract._validate_retention_claim_semantic_grounding(graph, observations)
    assert exc_info.value.diagnostics["semantic_window_max_span"] == 12


def test_retention_semantic_grounding_accepts_local_split_evidence_window():
    from app.services import analyzer_contract

    graph = {
        "claims": [
            {
                "claim_id": "law",
                "claim_type": "fact",
                "text": "Royal law requires marriage after sharing the queen chamber.",
                "qualification": "Direct rule.",
                "evidence_panel_ids": ["law", "marry"],
            }
        ]
    }
    observations = [
        {
            "panel_id": "law",
            "source_index": 20,
            "visible_facts": [],
            "dialogue_or_ocr": [
                "ACCORDING TO ROYAL LAW, ANYONE SHARING THE ROYAL BEDCHAMBER WITH THE QUEEN..."
            ],
            "inferences": [],
            "uncertainties": [],
        },
        {
            "panel_id": "marry",
            "source_index": 21,
            "visible_facts": [],
            "dialogue_or_ocr": ["MUST MARRY HER!"],
            "inferences": [],
            "uncertainties": [],
        },
    ]
    analyzer_contract._validate_retention_claim_semantic_grounding(graph, observations)


def test_retention_causal_chain_rejects_backward_chronology():
    from app.services import analyzer_contract

    continuity = {
        "chunks": [{"panel_ids": ["a", "b", "c", "d"]}],
        "causal_links": [{"from_panel_id": "c", "to_panel_id": "b"}],
    }
    with pytest.raises(analyzer_contract.AnalyzerContractError, match="backward in chronology"):
        analyzer_contract._validate_retention_causal_chain(
            continuity, {"claims": []}, [{}, {}, {}, {}]
        )


def test_retention_hook_can_tease_later_event_when_body_reaches_it():
    from app.services import analyzer_contract

    continuity = {
        "chunks": [{"panel_ids": ["a", "b", "c", "d"]}],
        "causal_links": [
            {"from_panel_id": "a", "to_panel_id": "b"},
            {"from_panel_id": "b", "to_panel_id": "c"},
            {"from_panel_id": "c", "to_panel_id": "d"},
        ],
    }
    graph = {
        "claims": [
            {"claim_id": "hook", "evidence_panel_ids": ["d"]},
            {"claim_id": "setup", "evidence_panel_ids": ["a"]},
            {"claim_id": "middle", "evidence_panel_ids": ["b"]},
        ]
    }
    passages = [
        {"claim_ids": ["hook"]},
        {"claim_ids": ["setup"]},
        {"claim_ids": ["middle"]},
        {"claim_ids": ["hook"]},
    ]
    analyzer_contract._validate_retention_causal_chain(continuity, graph, passages)


def test_retention_hook_teaser_must_be_reachable_from_body():
    from app.services import analyzer_contract

    continuity = {
        "chunks": [{"panel_ids": ["a", "b", "c", "d"]}],
        "causal_links": [
            {"from_panel_id": "a", "to_panel_id": "b"},
            {"from_panel_id": "c", "to_panel_id": "d"},
        ],
    }
    graph = {
        "claims": [
            {"claim_id": "hook", "evidence_panel_ids": ["d"]},
            {"claim_id": "setup", "evidence_panel_ids": ["a"]},
            {"claim_id": "middle", "evidence_panel_ids": ["b"]},
        ]
    }
    passages = [
        {"claim_ids": ["hook"]},
        {"claim_ids": ["setup"]},
        {"claim_ids": ["middle"]},
        {"claim_ids": ["middle"]},
    ]
    with pytest.raises(
        analyzer_contract.AnalyzerContractError, match="hook teaser is not reachable"
    ):
        analyzer_contract._validate_retention_causal_chain(continuity, graph, passages)


def test_semantic_retry_instruction_preserves_modality_and_local_window():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    adapter = (root / "app/services/vision_adapter.py").read_text(encoding="utf-8")
    assert "semantic_window_max_span" in adapter
    assert "WILL do something" in adapter
    assert "does NOT prove MUST" in adapter
    assert "Never borrow a rare anchor from a distant scene" in adapter


def test_causal_retry_instruction_treats_hook_as_teaser_not_seed():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    adapter = (root / "app/services/vision_adapter.py").read_text(encoding="utf-8")
    assert "first passage may be a later teaser" in adapter
    assert "must never be used as a backward causal seed" in adapter
    assert "Every continuity_ledger causal link MUST move forward in source chronology" in adapter


def test_deterministic_semantic_repair_uses_one_local_window():
    from app.services import vision_adapter

    output = {
        "evidence_graph": {"claims": [{"claim_id": "law", "evidence_panel_ids": ["law-panel"]}]},
        "script_passages": [{"claim_ids": ["law"], "evidence_panel_ids": ["law-panel"]}],
    }
    diagnostics = {
        "claim_id": "law",
        "claim_anchors": ["chamber", "law", "marry", "queen", "require", "treatment"],
        "matched_claim_anchors": ["chamber", "law", "queen"],
        "required_anchor_matches": 5,
        "critical_claim_anchors": ["chamber", "law", "marry", "treatment"],
        "semantic_window_max_span": 12,
        "candidate_panels": [
            {
                "panel_id": "treatment-near",
                "source_order": 223,
                "overlap_anchors": ["chamber", "treatment"],
            },
            {
                "panel_id": "law-panel",
                "source_order": 224,
                "overlap_anchors": ["chamber", "law", "queen"],
            },
            {
                "panel_id": "marry-near",
                "source_order": 225,
                "overlap_anchors": ["marry", "require"],
            },
            {
                "panel_id": "treatment-far",
                "source_order": 193,
                "overlap_anchors": ["require", "treatment"],
            },
        ],
    }
    repaired = vision_adapter._repair_semantic_claim_evidence_from_diagnostics(
        output, diagnostics, ("treatment-near", "law-panel", "marry-near", "treatment-far")
    )
    assert repaired is not None
    evidence = repaired["evidence_graph"]["claims"][0]["evidence_panel_ids"]
    assert "treatment-near" in evidence and "marry-near" in evidence
    assert "treatment-far" not in evidence


def test_deterministic_semantic_repair_refuses_distant_modal_anchor():
    from app.services import vision_adapter

    output = {
        "evidence_graph": {
            "claims": [{"claim_id": "declaration", "evidence_panel_ids": ["declare"]}]
        },
        "script_passages": [{"claim_ids": ["declaration"], "evidence_panel_ids": ["declare"]}],
    }
    diagnostics = {
        "claim_id": "declaration",
        "claim_anchors": ["declare", "lloyd", "require", "treatment"],
        "matched_claim_anchors": ["declare", "lloyd", "treatment"],
        "required_anchor_matches": 4,
        "critical_claim_anchors": ["declare", "require", "treatment"],
        "semantic_window_max_span": 12,
        "candidate_panels": [
            {
                "panel_id": "declare",
                "source_order": 212,
                "overlap_anchors": ["declare", "lloyd", "treatment"],
            },
            {
                "panel_id": "require-far",
                "source_order": 193,
                "overlap_anchors": ["lloyd", "require", "treatment"],
            },
        ],
    }
    assert (
        vision_adapter._repair_semantic_claim_evidence_from_diagnostics(
            output, diagnostics, ("declare", "require-far")
        )
        is None
    )


def test_retention_semantic_morphology_accepts_accompanied_for_accompany():
    from app.services import analyzer_contract

    graph = {
        "claims": [
            {
                "claim_id": "c",
                "claim_type": "fact",
                "text": "Magentano declares Lloyd will accompany her for one month for treatment.",
                "qualification": "Direct declaration.",
                "evidence_panel_ids": ["p"],
            }
        ]
    }
    observations = [
        {
            "panel_id": "p",
            "source_index": 10,
            "visible_facts": [],
            "dialogue_or_ocr": [
                "I, MAGENTANO, DECLARE THAT STARTING TODAY, I WILL BE ACCOMPANIED BY LLOYD FRONTERA FOR ONE MONTH FOR TREATMENT."
            ],
            "inferences": [],
            "uncertainties": [],
        }
    ]
    analyzer_contract._validate_retention_claim_semantic_grounding(graph, observations)


def test_modal_repair_downgrades_unsupported_must_to_evidence_backed_will():
    from app.services import analyzer_contract, vision_adapter

    text = "Magentano declares Lloyd must accompany her for one month for treatment."
    output = {
        "evidence_graph": {
            "claims": [
                {
                    "claim_id": "c",
                    "claim_type": "fact",
                    "text": text,
                    "qualification": "Direct declaration.",
                    "evidence_panel_ids": ["p"],
                }
            ]
        },
        "script_passages": [{"claim_ids": ["c"], "text": text, "evidence_panel_ids": ["p"]}],
    }
    diagnostics = {
        "claim_id": "c",
        "claim_anchors": [
            "magentano",
            "declare",
            "lloyd",
            "require",
            "accompany",
            "month",
            "treatment",
        ],
        "missing_critical_anchors": ["require"],
        "candidate_panels": [
            {
                "panel_id": "p",
                "source_order": 10,
                "overlap_anchors": [
                    "magentano",
                    "declare",
                    "lloyd",
                    "accompany",
                    "month",
                    "treatment",
                ],
                "evidence_excerpt": [
                    "I, MAGENTANO, DECLARE THAT STARTING TODAY, I WILL BE ACCOMPANIED BY LLOYD FRONTERA FOR ONE MONTH FOR TREATMENT."
                ],
            }
        ],
    }
    repaired = vision_adapter._repair_unsupported_must_from_diagnostics(output, diagnostics, ("p",))
    assert repaired is not None
    claim = repaired["evidence_graph"]["claims"][0]
    passage = repaired["script_passages"][0]
    assert " must " not in claim["text"].casefold()
    assert " will " in claim["text"].casefold()
    assert " will " in passage["text"].casefold()
    observations = [
        {
            "panel_id": "p",
            "source_index": 10,
            "visible_facts": [],
            "dialogue_or_ocr": [
                "I, MAGENTANO, DECLARE THAT STARTING TODAY, I WILL BE ACCOMPANIED BY LLOYD FRONTERA FOR ONE MONTH FOR TREATMENT."
            ],
            "inferences": [],
            "uncertainties": [],
        }
    ]
    analyzer_contract._validate_retention_claim_semantic_grounding(
        repaired["evidence_graph"], observations
    )


def test_modal_repair_refuses_requires_or_missing_will_evidence():
    from app.services import vision_adapter

    diagnostics = {
        "claim_id": "c",
        "claim_anchors": ["law", "require", "marry"],
        "missing_critical_anchors": ["require"],
        "candidate_panels": [
            {
                "panel_id": "p",
                "source_order": 10,
                "overlap_anchors": ["law", "marry"],
                "evidence_excerpt": ["THE LAW MENTIONS MARRIAGE."],
            }
        ],
    }
    output_requires = {
        "evidence_graph": {
            "claims": [
                {
                    "claim_id": "c",
                    "text": "Royal law requires marriage.",
                    "evidence_panel_ids": ["p"],
                }
            ]
        },
        "script_passages": [
            {
                "claim_ids": ["c"],
                "text": "Royal law requires marriage.",
                "evidence_panel_ids": ["p"],
            }
        ],
    }
    assert (
        vision_adapter._repair_unsupported_must_from_diagnostics(
            output_requires, diagnostics, ("p",)
        )
        is None
    )
    output_must = {
        "evidence_graph": {
            "claims": [
                {
                    "claim_id": "c",
                    "text": "Royal law must cause marriage.",
                    "evidence_panel_ids": ["p"],
                }
            ]
        },
        "script_passages": [
            {
                "claim_ids": ["c"],
                "text": "Royal law must cause marriage.",
                "evidence_panel_ids": ["p"],
            }
        ],
    }
    assert (
        vision_adapter._repair_unsupported_must_from_diagnostics(output_must, diagnostics, ("p",))
        is None
    )


def test_relevant_visual_cluster_does_not_chain_hop_away_from_seed():
    from app.services import vision_adapter as va
    obs = tuple({"panel_id": panel_id, "source_asset_id": f"asset-{panel_id}", "source_index": index,
                 "visible_facts": ["Queen treatment begins."] if panel_id == "seed" else ["quiet unrelated frame"],
                 "dialogue_or_ocr": [], "inferences": []}
                for panel_id, index in (("seed", 0), ("near", 10), ("hop", 20), ("far", 30)))
    claim = {"claim_id": "c", "text": "queen treatment", "qualification": "grounded", "evidence_panel_ids": ["seed"]}
    passage = {"text": "queen treatment begins", "claim_ids": ["c"], "evidence_panel_ids": ["seed"]}
    relevant = va._passage_relevant_visual_ids(passage, ("seed", "near", "hop", "far"), {"c": claim}, {o["panel_id"]: o for o in obs})
    assert relevant == ("seed", "near")


def test_two_generic_overlaps_do_not_seed_distant_visual():
    from app.services import vision_adapter as va
    observations = {
        "evidence": {"panel_id": "evidence", "source_asset_id": "evidence-asset", "source_index": 0, "visible_facts": ["Royal queen marriage law announced."], "dialogue_or_ocr": [], "inferences": []},
        "generic": {"panel_id": "generic", "source_asset_id": "generic-asset", "source_index": 100, "visible_facts": ["Royal queen watches quietly."], "dialogue_or_ocr": [], "inferences": []},
    }
    claim = {"claim_id": "c", "text": "royal queen marriage law", "qualification": "grounded", "evidence_panel_ids": ["evidence"]}
    passage = {"text": "royal queen marriage law", "claim_ids": ["c"], "evidence_panel_ids": ["evidence"]}
    relevant = va._passage_relevant_visual_ids(passage, ("generic",), {"c": claim}, observations)
    assert relevant == ()



def test_rhetorical_passage_tokens_do_not_seed_distant_visual():
    from app.services import vision_adapter as va
    observations = {
        "evidence": {"panel_id": "evidence", "source_asset_id": "evidence-asset", "source_index": 0,
                     "visible_facts": ["Royal marriage law applies."], "dialogue_or_ocr": [], "inferences": []},
        "random": {"panel_id": "random", "source_asset_id": "random-asset", "source_index": 100,
                   "visible_facts": ["They catch him and save the queen."], "dialogue_or_ocr": [], "inferences": []},
    }
    claim = {"claim_id": "c", "text": "Royal marriage law applies.", "qualification": "grounded", "evidence_panel_ids": ["evidence"]}
    passage = {"text": "The legal catch appears as they try to save the queen.", "claim_ids": ["c"], "evidence_panel_ids": ["evidence"]}
    relevant = va._passage_relevant_visual_ids(passage, ("random",), {"c": claim}, observations)
    assert relevant == ()


def test_retention_semantic_expansion_stays_local_to_section_evidence():
    from types import SimpleNamespace

    from app.services import reference_visual_review as review

    script = SimpleNamespace(
        editorial_metadata={"narrative_identity": {"profile_id": "retention_story_v1"}},
        sections=[
            {
                "section": "cta",
                "text": "Then the swords come out and the admission token rides on the result.",
                "evidence_panel_ids": ["wager"],
                "evidence": [{"claim_id": "c1", "panel_ids": ["wager"]}],
            }
        ],
    )
    regions = [
        SimpleNamespace(
            panel_id="old-line",
            source_order=20,
            observation_json={
                "visible_facts": ["Namgung clan direct line beside a sword illustration."],
                "dialogue_or_ocr": [],
                "inferences": [],
            },
        ),
        SimpleNamespace(
            panel_id="wager",
            source_order=100,
            observation_json={
                "visible_facts": ["Two men prepare to fight."],
                "dialogue_or_ocr": ["PUT THE ADMISSION TOKEN ON THE LINE."],
                "inferences": [],
            },
        ),
        SimpleNamespace(
            panel_id="local-action",
            source_order=108,
            observation_json={
                "visible_facts": ["Two men clash with swords over the admission token."],
                "dialogue_or_ocr": [],
                "inferences": [],
            },
        ),
    ]
    claims = {"cta": ("Namgung puts the admission token on the line for a sparring match.",)}
    expanded = review.expand_retention_section_evidence(
        script, regions, {"cta": ("wager",)}, claim_text_by_section=claims
    )
    assert "wager" in expanded["cta"]
    assert "local-action" in expanded["cta"]
    assert "old-line" not in expanded["cta"]


def test_retention_expansion_adds_one_bounded_preceding_context_panel():
    from types import SimpleNamespace

    from app.services import reference_visual_review as review

    script = SimpleNamespace(
        editorial_metadata={"narrative_identity": {"profile_id": "retention_story_v1"}},
        sections=[{
            "section": "cta",
            "text": "Aaron faces the final.",
            "evidence_panel_ids": ["direct"],
            "evidence": [{"claim_id": "c1", "panel_ids": ["direct"]}],
        }],
    )
    regions = [
        SimpleNamespace(panel_id="distant", source_order=90, observation_json={"visible_facts": ["Quiet corridor."]}),
        SimpleNamespace(panel_id="context", source_order=98, observation_json={"visible_facts": ["Blue energy crosses the arena."]}),
        SimpleNamespace(panel_id="direct", source_order=100, observation_json={"visible_facts": ["Aaron stares forward."]}),
    ]
    expanded = review.expand_retention_section_evidence(script, regions, {"cta": ("direct",)})
    assert "context" in expanded["cta"]
    assert "distant" not in expanded["cta"]


def test_retention_expansion_does_not_add_distant_predecessor():
    from types import SimpleNamespace

    from app.services import reference_visual_review as review

    script = SimpleNamespace(
        editorial_metadata={"narrative_identity": {"profile_id": "retention_story_v1"}},
        sections=[{
            "section": "cta",
            "text": "Aaron faces the final.",
            "evidence_panel_ids": ["direct"],
            "evidence": [{"claim_id": "c1", "panel_ids": ["direct"]}],
        }],
    )
    regions = [
        SimpleNamespace(panel_id="old", source_order=96, observation_json={"visible_facts": ["Blue energy crosses the arena."]}),
        SimpleNamespace(panel_id="direct", source_order=100, observation_json={"visible_facts": ["Aaron stares forward."]}),
    ]
    expanded = review.expand_retention_section_evidence(script, regions, {"cta": ("direct",)})
    assert expanded["cta"] == ("direct",)


def test_retention_expansion_adds_grounded_next_section_boundary_handoff():
    from types import SimpleNamespace

    from app.services import reference_visual_review as review

    script = SimpleNamespace(
        editorial_metadata={"narrative_identity": {"profile_id": "retention_story_v1"}},
        sections=[
            {
                "section": "hook",
                "text": "A wooden door appears.",
                "evidence_panel_ids": ["door"],
                "evidence": [{"claim_id": "h1", "panel_ids": ["door"]}],
            },
            {
                "section": "setup",
                "text": "He opens the doorway.",
                "evidence_panel_ids": ["doorway"],
                "evidence": [{"claim_id": "s1", "panel_ids": ["doorway"]}],
            },
        ],
    )
    regions = [
        SimpleNamespace(
            panel_id="door",
            source_order=100,
            observation_json={"visible_facts": ["Wooden door."], "dialogue_or_ocr": [], "inferences": []},
        ),
        SimpleNamespace(
            panel_id="doorway",
            source_order=102,
            observation_json={"visible_facts": ["Open doorway."], "dialogue_or_ocr": [], "inferences": []},
        ),
    ]
    expanded = review.expand_retention_section_evidence(
        script,
        regions,
        {"hook": ("door",), "setup": ("doorway",)},
    )
    assert "doorway" in expanded["hook"]


def test_retention_final_section_reuses_concrete_recap_and_one_successor():
    from types import SimpleNamespace

    from app.services import reference_visual_review as review
    script = SimpleNamespace(
        editorial_metadata={"narrative_identity": {"profile_id": "retention_story_v1"}},
        sections=[
            {
                "section": "setup",
                "text": "The wooden door opens.",
                "evidence_panel_ids": ["door", "weak"],
                "evidence": [{"claim_id": "s1", "panel_ids": ["door", "weak"]}],
            },
            {
                "section": "cta",
                "text": "Standing beside the wooden door, the offer could turn the prison into storage.",
                "evidence_panel_ids": ["offer"],
                "evidence": [{"claim_id": "c1", "panel_ids": ["offer"]}],
            },
        ],
    )
    regions = [
        SimpleNamespace(
            panel_id="door",
            source_order=100,
            observation_json={"visible_facts": ["Large wooden door."], "dialogue_or_ocr": [], "inferences": []},
        ),
        SimpleNamespace(
            panel_id="weak",
            source_order=110,
            observation_json={"visible_facts": ["Wooden floor."], "dialogue_or_ocr": [], "inferences": []},
        ),
        SimpleNamespace(
            panel_id="offer",
            source_order=200,
            observation_json={"visible_facts": ["Creature makes an offer beside the prison."], "dialogue_or_ocr": [], "inferences": []},
        ),
        SimpleNamespace(
            panel_id="after",
            source_order=202,
            observation_json={"visible_facts": ["The creature waves beside the doorway."], "dialogue_or_ocr": [], "inferences": []},
        ),
    ]
    expanded = review.expand_retention_section_evidence(
        script,
        regions,
        {"setup": ("door", "weak"), "cta": ("offer",)},
    )
    assert "door" in expanded["cta"]
    assert "weak" not in expanded["cta"]
    assert "after" in expanded["cta"]


def test_visual_section_capacity_retries_by_reselecting_visual_story(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    request = VisionChapterSynthesisRequest(
        analysis_run_id="visual-capacity-reselect-test",
        instruction_version="test-v1",
        instruction_sha256="d" * 64,
        instruction_text="test",
        expected_panel_ids=("panel-1",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )
    locked = ({
        "passage_id": "p1",
        "editorial_role": "cta",
        "text": "An unframeable old beat.",
        "claim_ids": ["c1"],
        "evidence_panel_ids": ["panel-1"],
    },)
    diagnostics = {
        "passages": [{
            "passage_id": "p1",
            "role": "cliff",
            "section": "cta",
            "relevant_section_capacity": 0,
            "relevant_generic_capacity": 0,
            "capacity_zero": True,
            "required_section": 0,
            "safe_candidate_panels": [{
                "panel_id": "panel-safe",
                "source_index": 12,
                "excerpt": "A frameable replacement beat.",
            }],
        }]
    }

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            if len(self.requests) == 1:
                raise VisionResponseInvalid(
                    validation_subtype="production_visual_section_capacity_insufficient",
                    retry_passages=locked,
                    selection_diagnostics=diagnostics,
                )
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(
        pipeline, "_validate_synthesis_subtitle_admission", lambda output, active_request: None
    )
    monkeypatch.setattr(
        pipeline, "_validated_synthesis_cache_output", lambda output, active_request: output
    )

    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}
    retry_request = provider.requests[1]
    assert retry_request.retry_visual_story_alignment is True
    assert retry_request.retry_visual_story_diagnostics == diagnostics
    assert retry_request.retry_visual_selection is False
    assert retry_request.retry_causal_arc is False
    assert retry_request.retry_claim_semantic_grounding is True
    assert retry_request.retry_passages == locked


def test_visual_capacity_zero_carries_passage_diagnostics():
    import pytest

    from app.services import vision_adapter as va

    observations = (
        {
            "panel_id": "evidence",
            "source_asset_id": "asset-evidence",
            "source_index": 0,
            "visible_facts": ["Queen treatment begins."],
            "dialogue_or_ocr": [],
            "inferences": [],
        },
        {
            "panel_id": "safe",
            "source_asset_id": "asset-safe",
            "source_index": 100,
            "visible_facts": ["Unrelated quiet landscape."],
            "dialogue_or_ocr": [],
            "inferences": [],
        },
    )
    request = va.VisionChapterSynthesisRequest(
        analysis_run_id="capacity-diagnostics-test",
        instruction_version="test-v1",
        instruction_sha256="e" * 64,
        instruction_text="test",
        expected_panel_ids=("evidence", "safe"),
        coverage_manifest={},
        ordered_observations=observations,
        chunks=(),
        target_word_count_min=115,
        target_word_count_max=125,
        preferred_visual_panel_ids=("safe",),
        preferred_visual_panel_ids_by_section={"hook": ("safe",)},
    )
    output = {
        "script_passages": [{
            "passage_id": "p1",
            "editorial_role": "hook",
            "text": "Queen treatment begins.",
            "claim_ids": ["c1"],
            "evidence_panel_ids": ["evidence"],
        }],
        "evidence_graph": {"claims": [{
            "claim_id": "c1",
            "text": "Queen treatment begins.",
            "qualification": "grounded",
            "evidence_panel_ids": ["evidence"],
        }]},
    }

    with pytest.raises(va.VisionResponseInvalid) as caught:
        va.validate_synthesis_visual_selection(output, request)
    assert caught.value.validation_subtype == "production_visual_section_capacity_insufficient"
    diagnostics = caught.value.selection_diagnostics
    assert diagnostics.get("passages")
    row = diagnostics["passages"][0]
    assert row["section"] == "hook"
    assert row["relevant_section_capacity"] == 0
    assert row["relevant_generic_capacity"] == 0
    assert row["capacity_zero"] is True
    assert row["claim_ids"] == ["c1"]
    assert row["claims"][0]["text"] == "Queen treatment begins."
    assert row["safe_candidate_panels"][0]["panel_id"] == "safe"
    assert row["safe_candidate_panels"][0]["source_index"] == 100
    assert "Unrelated quiet landscape" in row["safe_candidate_panels"][0]["excerpt"]
    assert caught.value.retry_passages is not None

def test_visual_capacity_zero_releases_opening_anchor(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid

    locked = (
        {
            "passage_id": "p1",
            "editorial_role": "hook",
            "text": "Unframeable opening beat.",
            "claim_ids": ["c1"],
            "evidence_panel_ids": ["panel-1"],
        },
        {
            "passage_id": "p2",
            "editorial_role": "setup",
            "text": "Old setup tied to the rejected opening.",
            "claim_ids": ["c2"],
            "evidence_panel_ids": ["panel-2"],
        },
    )
    diagnostics = {
        "passages": [
            {
                "passage_id": "p1",
                "role": "hook",
                "section": "hook",
                "relevant_section_capacity": 0,
                "relevant_generic_capacity": 0,
                "capacity_zero": True,
                "safe_candidate_panels": [],
            }
        ]
    }
    request = VisionChapterSynthesisRequest(
        analysis_run_id="visual-capacity-opening-release",
        instruction_version="test-v1",
        instruction_sha256="f" * 64,
        instruction_text="test",
        expected_panel_ids=("panel-1", "panel-2"),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
        retry_causal_arc=True,
        retry_passages=locked,
        retry_claim_semantic_grounding=True,
        retry_claim_semantic_diagnostics={"claim_id": "old"},
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            if len(self.requests) == 1:
                raise VisionResponseInvalid(
                    validation_subtype="production_visual_section_capacity_insufficient",
                    retry_passages=locked,
                    selection_diagnostics=diagnostics,
                )
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_: None)
    monkeypatch.setattr(
        pipeline, "_validated_synthesis_cache_output", lambda output, _request: output
    )
    assert pipeline._synthesize_with_cache(provider, request) == {"accepted": True}
    retry = provider.requests[1]
    assert retry.retry_visual_story_alignment is True
    assert retry.retry_visual_story_diagnostics == diagnostics
    assert retry.retry_causal_arc is False
    assert retry.retry_passages is None
    assert retry.retry_claim_semantic_grounding is True
    assert retry.retry_claim_semantic_diagnostics is None
    assert retry.retry_visual_selection is False


def test_visual_capacity_retry_prompt_requires_safe_first_beat_replacement():
    from app.services import narrative_identity as identity
    from app.services import vision_adapter as va

    profile = identity.get_narrative_identity("retention_story_v1")
    version, digest, instruction = identity.load_narrative_instruction(profile.profile_id)
    locked = (
        {
            "passage_id": "p1",
            "editorial_role": "hook",
            "text": "Grounded opening remains unchanged.",
            "claim_ids": ["c1"],
            "evidence_panel_ids": ["safe-hook"],
        },
        {
            "passage_id": "p2",
            "editorial_role": "pressure",
            "text": "This downstream beat must be replaced.",
            "claim_ids": ["c2"],
            "evidence_panel_ids": ["unsafe"],
        },
    )
    diagnostics = {
        "passages": [
            {
                "passage_id": "p2",
                "role": "pressure",
                "section": "conflict",
                "capacity_zero": True,
                "relevant_section_capacity": 0,
                "relevant_generic_capacity": 0,
                "safe_candidate_panels": [
                    {
                        "panel_id": "safe-conflict",
                        "source_index": 42,
                        "excerpt": "Two fighters collide in a sword exchange.",
                    }
                ],
            }
        ]
    }
    request = va.VisionChapterSynthesisRequest(
        analysis_run_id="capacity-prompt-test",
        instruction_version=version,
        instruction_sha256=digest,
        instruction_text=instruction,
        expected_panel_ids=("safe-hook", "unsafe", "safe-conflict"),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
        narrative_profile_id=profile.profile_id,
        narrative_profile_version=profile.profile_version,
        narrative_profile_sha256=profile.contract_sha256,
        retry_visual_story_alignment=True,
        retry_visual_story_diagnostics=diagnostics,
        retry_claim_semantic_grounding=True,
        retry_passages=locked,
    )
    payload = va._build_synthesis_payload(request, request.expected_panel_ids, "mock", profile)
    content = payload["messages"][1]["content"]
    assert "If relevant visual capacity is zero, adding filler panels cannot repair the passage." in content
    assert "Discard/rewrite the beat from production-safe evidence" in content
    assert "Production-safe capacity retry" in content
    assert "Preserve every non-target passage_id" in content
    assert "safe-conflict" in content
    assert "Two fighters collide in a sword exchange." in content


def test_visual_story_retry_prompt_unlocks_rejected_target_claim_and_evidence():
    from app.services import narrative_identity as identity
    from app.services import vision_adapter as va

    profile = identity.get_narrative_identity("retention_story_v1")
    version, digest, instruction = identity.load_narrative_instruction(profile.profile_id)
    locked = (
        {
            "passage_id": "p1",
            "editorial_role": "setup",
            "text": "A healthy setup stays grounded.",
            "claim_ids": ["c1"],
            "evidence_panel_ids": ["safe-setup"],
        },
        {
            "passage_id": "p2",
            "editorial_role": "resolve",
            "text": "The rejected beat must be replaced.",
            "claim_ids": ["c2"],
            "evidence_panel_ids": ["old-panel"],
        },
    )
    diagnostics = {
        "passages": [
            {
                "passage_id": "p2",
                "role": "resolve",
                "section": "conflict",
                "replacement_required": True,
                "capacity_zero": False,
                "candidate_panels": [
                    {
                        "panel_id": "safe-replacement",
                        "source_index": 73,
                        "excerpt": "She has one shot to execute Vinter's plan.",
                    }
                ],
            }
        ]
    }
    request = va.VisionChapterSynthesisRequest(
        analysis_run_id="visual-story-unlock-test",
        instruction_version=version,
        instruction_sha256=digest,
        instruction_text=instruction,
        expected_panel_ids=("safe-setup", "old-panel", "safe-replacement"),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
        narrative_profile_id=profile.profile_id,
        narrative_profile_version=profile.profile_version,
        narrative_profile_sha256=profile.contract_sha256,
        retry_visual_story_alignment=True,
        retry_visual_story_diagnostics=diagnostics,
        retry_claim_semantic_grounding=True,
        retry_claim_semantic_diagnostics={"claim_id": "c2"},
        retry_passages=locked,
    )

    payload = va._build_synthesis_payload(request, request.expected_panel_ids, "mock", profile)
    content = payload["messages"][1]["content"]
    assert "Visual-story alignment retry" in content
    assert "Preserve every non-target passage_id" in content
    assert "allow replacing text, claim_ids, evidence_panel_ids" in content
    assert "Select a candidate_panel first" in content
    assert "safe-replacement" in content
    assert "failed a narration-length or subtitle-layout gate" not in content
    assert "change only passage text" not in content


def test_semantic_retry_payload_requires_evidence_near_atomic_claims():
    from app.services import narrative_identity
    from app.services import vision_adapter as va

    profile = narrative_identity.get_narrative_identity("retention_story_v1")
    version, digest, instruction = narrative_identity.load_narrative_instruction(profile.profile_id)
    request = va.VisionChapterSynthesisRequest(
        analysis_run_id="semantic-convergence-wire",
        instruction_version=version,
        instruction_sha256=digest,
        instruction_text=instruction,
        expected_panel_ids=("safe",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
        narrative_profile_id=profile.profile_id,
        narrative_profile_version=profile.profile_version,
        narrative_profile_sha256=profile.contract_sha256,
        retry_claim_semantic_grounding=True,
        retry_claim_semantic_diagnostics={"claim_id": "c1"},
    )
    payload = va._build_synthesis_payload(request, request.expected_panel_ids, "mock", profile)
    content = payload["messages"][1]["content"]
    assert "claim.text as an evidence key, not polished narration" in content
    assert "SMALLEST atomic proposition" in content
    assert "Natural paraphrase belongs in script_passages" in content
    assert "do not append who/when/where/why" in content
