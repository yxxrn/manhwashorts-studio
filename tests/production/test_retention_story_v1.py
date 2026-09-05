from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_retention_profile_is_verified_and_non_question_ending():
    from app.services import narrative_identity as identity

    profile = identity.get_narrative_identity("retention_story_v1")
    assert profile.profile_version == "1.0.0"
    assert profile.allowed_ending_kinds == ("cliffhanger", "consequence")
    assert profile.prompt_version == "vision-first-retention-story-v1"
    version, digest, text = identity.load_narrative_instruction(profile.profile_id)
    assert version == profile.prompt_version
    assert len(digest) == 64
    lowered = text.lower()
    assert "one dominant story arc" in lowered
    assert "8-14" in lowered
    assert "must not be a rhetorical question" in lowered
    assert "frameable is not a reason to cite it" in lowered


def test_retention_contract_requires_short_single_sentence_hook():
    from app.services import analyzer_contract as contract
    from app.services import narrative_identity as identity

    profile = identity.get_narrative_identity("retention_story_v1")
    passages = [
        {"passage_id": "p1", "editorial_role": "hook", "text": "Jin wakes in the past with a bracelet that rewinds time.", "claim_ids": ["c1"], "evidence_panel_ids": ["panel-1"]},
        {"passage_id": "p2", "editorial_role": "pressure", "text": "He sees the same bully threatening his sect again.", "claim_ids": ["c1"], "evidence_panel_ids": ["panel-1"]},
        {"passage_id": "p3", "editorial_role": "decision", "text": "This time he steps forward instead of staying quiet.", "claim_ids": ["c1"], "evidence_panel_ids": ["panel-1"]},
        {"passage_id": "p4", "editorial_role": "cliffhanger", "text": "Then the bracelet reveals one more thing it can rewind.", "claim_ids": ["c1"], "evidence_panel_ids": ["panel-1"]},
    ]
    outline = {"ending_kind": "cliffhanger", "story_spine": dict.fromkeys(contract._STORY_SPINE_FIELDS, "grounded")}
    contract._validate_script_passages_v3(passages, ("panel-1",), {"c1": {"panel-1"}}, [{"dialogue_or_ocr": []}], outline, profile)

    passages[0] = {**passages[0], "text": "Jin wakes in the distant past carrying every memory and a mysterious bracelet that can somehow rewind time itself."}
    with pytest.raises(contract.AnalyzerContractError, match="8-14 words"):
        contract._validate_script_passages_v3(passages, ("panel-1",), {"c1": {"panel-1"}}, [{"dialogue_or_ocr": []}], outline, profile)


def test_retention_section_maps_merge_repeated_conflict_and_keep_story_text():
    from app.services import reference_visual_review as review

    script = SimpleNamespace(
        editorial_metadata={"narrative_identity": {"profile_id": "retention_story_v1"}},
        sections=[
            {"section": "hook", "text": "hook beat", "evidence_panel_ids": ["p1"], "citations": [1]},
            {"section": "conflict", "text": "first conflict beat", "evidence_panel_ids": ["p2"], "citations": [2]},
            {"section": "conflict", "text": "second conflict beat", "evidence_panel_ids": ["p3"], "citations": [3]},
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
        face_visibility=0.1, facial_expression=0.1, action_pose=0.1,
        impact_frame=0.1, dramatic_composition=0.1, weapons=0.0,
        visual_effects=0.0, close_up=0.0, scenery_only=0.0,
    )
    strong_features = SimpleNamespace(
        face_visibility=1.0, facial_expression=1.0, action_pose=1.0,
        impact_frame=1.0, dramatic_composition=1.0, weapons=1.0,
        visual_effects=1.0, close_up=0.0, scenery_only=0.0,
    )
    story_match = SimpleNamespace(
        panel_id="story", panel_region_id="r1", source_order=20,
        story_relevance_by_section={"conflict": 2.0},
        panel_candidate=SimpleNamespace(features=weak_features, source_family="ch1"),
    )
    pretty_only = SimpleNamespace(
        panel_id="pretty", panel_region_id="r2", source_order=10,
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
    assert 'run state identity does not match requested narrative profile' in source


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
            'passage_id': 'p1',
            'editorial_role': 'hook',
            'text': 'Source wording remains too close in this passage.',
            'claim_ids': ['c1'],
            'evidence_panel_ids': ['panel-1'],
        },
    )
    request = VisionChapterSynthesisRequest(
        analysis_run_id='retry-mode-transition-test',
        instruction_version='test-v1',
        instruction_sha256='c' * 64,
        instruction_text='test',
        expected_panel_ids=('panel-1',),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )

    class Provider:
        model_id = ''
        endpoint = ''

        def __init__(self):
            self.requests = []

        def synthesize(self, active_request):
            self.requests.append(active_request)
            if len(self.requests) == 1:
                raise VisionResponseInvalid(
                    validation_subtype='production_visual_selection_insufficient',
                    retry_passages=locked,
                )
            if len(self.requests) == 2:
                assert active_request.retry_visual_selection is True
                assert active_request.retry_dialogue_paraphrase is False
                raise VisionResponseInvalid(
                    validation_subtype='script_passage_copies_source_dialogue',
                    passage_word_counts=(8,),
                    retry_passages=locked,
                )
            return {'accepted': True}

    provider = Provider()
    monkeypatch.setattr(pipeline, '_validate_synthesis_subtitle_admission', lambda output, active_request: None)
    monkeypatch.setattr(pipeline, '_validated_synthesis_cache_output', lambda output, active_request: output)

    assert pipeline._synthesize_with_cache(provider, request) == {'accepted': True}
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
        {"passage_id":"p1","editorial_role":"hook","text":"Jin wakes holding every memory from the future intact.","claim_ids":["c1"],"evidence_panel_ids":["panel-1"]},
        {"passage_id":"p2","editorial_role":"pressure","text":"A rival closes in while the sect loses its protection.","claim_ids":["c1","c2"],"evidence_panel_ids":["panel-2"]},
        {"passage_id":"p3","editorial_role":"decision","text":"He chooses the dangerous path and moves before anyone else.","claim_ids":["c2"],"evidence_panel_ids":["panel-2"]},
        {"passage_id":"p4","editorial_role":"cliffhanger","text":"Then the same enemy appears at the training ground again.","claim_ids":["c2"],"evidence_panel_ids":["panel-2"]},
    ]
    outline={"ending_kind":"cliffhanger","story_spine":dict.fromkeys(contract._STORY_SPINE_FIELDS,"grounded")}
    with pytest.raises(contract.AnalyzerContractError, match="claim lacks local evidence"):
        contract._validate_script_passages_v3(passages,("panel-1","panel-2"),{"c1":{"panel-1"},"c2":{"panel-2"}},[{"dialogue_or_ocr":[]}],outline,profile)


def test_profile_script_coverage_allows_aggregate_claim_evidence_but_requires_local_overlap():
    from app.services.pipeline_stages.script import _profile_claim_coverage_error
    claims={"c1":{"evidence_panel_ids":["p1","p2"]}}
    sections=[{"claim_ids":["c1"],"evidence_panel_ids":["p1"]},{"claim_ids":["c1"],"evidence_panel_ids":["p2"]}]
    assert _profile_claim_coverage_error(sections,claims,{"p1","p2"}) is None
    bad=[{"claim_ids":["c1"],"evidence_panel_ids":["p1"]},{"claim_ids":["c1"],"evidence_panel_ids":["p3"]}]
    assert _profile_claim_coverage_error(bad,claims,{"p1","p2","p3"}) == "script section evidence does not ground its claim"


def test_local_claim_failure_retries_with_locked_narration(monkeypatch):
    from app.services import pipeline
    from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid
    locked=({"passage_id":"p1","editorial_role":"hook","text":"Grounded narration stays exactly the same here.","claim_ids":["c1"],"evidence_panel_ids":["panel-1"]},)
    request=VisionChapterSynthesisRequest(analysis_run_id="local-claim-retry-test",instruction_version="test-v1",instruction_sha256="d"*64,instruction_text="test",expected_panel_ids=("panel-1",),coverage_manifest={},ordered_observations=(),chunks=())
    class Provider:
        model_id = ""
        endpoint = ""
        def __init__(self): self.requests=[]
        def synthesize(self,active_request):
            self.requests.append(active_request)
            if len(self.requests)==1:
                raise VisionResponseInvalid(validation_subtype="script_passage_claim_lacks_local_evidence",retry_passages=locked)
            return {"accepted":True}
    provider=Provider()
    monkeypatch.setattr(pipeline,"_validate_synthesis_subtitle_admission",lambda output,active_request:None)
    monkeypatch.setattr(pipeline,"_validated_synthesis_cache_output",lambda output,active_request:output)
    assert pipeline._synthesize_with_cache(provider,request)=={"accepted":True}
    retry=provider.requests[1]
    assert retry.retry_local_claim_grounding is True
    assert retry.retry_visual_selection is False
    assert retry.retry_dialogue_paraphrase is False
    assert retry.retry_passages==locked


def test_retention_wire_requires_local_claim_grounding():
    from app.services import narrative_identity as identity
    from app.services import vision_adapter
    profile=identity.get_narrative_identity("retention_story_v1")
    version,digest,instruction=identity.load_narrative_instruction(profile.profile_id)
    request=vision_adapter.VisionChapterSynthesisRequest(analysis_run_id="local-wire",instruction_version=version,instruction_sha256=digest,instruction_text=instruction,expected_panel_ids=("panel-1",),coverage_manifest={},ordered_observations=(),chunks=(),narrative_profile_id=profile.profile_id,narrative_profile_version=profile.profile_version,narrative_profile_sha256=profile.contract_sha256)
    payload=vision_adapter._build_synthesis_payload(request,request.expected_panel_ids,"mock",profile)
    assert "must be locally grounded" in payload["messages"][1]["content"]


def test_retention_full_ledger_semantic_expansion_adds_direct_and_beat_matches():
    from types import SimpleNamespace

    from app.services import reference_visual_review as review

    script=SimpleNamespace(
        editorial_metadata={"narrative_identity":{"profile_id":"retention_story_v1"}},
        sections=[{
            "section":"twist",
            "text":"Jin reaches the Human-Faced Spider and uses his sword technique for the elixir.",
            "evidence_panel_ids":["broad"],
            "evidence":[{"claim_id":"c1","panel_ids":["direct"]}],
        }],
    )
    regions=[
        SimpleNamespace(panel_id="direct",source_order=10,observation_json={"visible_facts":["Jin stands in a cave."]}),
        SimpleNamespace(panel_id="semantic",source_order=20,observation_json={"visible_facts":["Human faced spider beside a sword and elixir."]}),
        SimpleNamespace(panel_id="noise",source_order=30,observation_json={"visible_facts":["Children cry in a courtyard."]}),
    ]
    expanded=review.expand_retention_section_evidence(script,regions,{"twist":("broad",)})
    assert expanded["twist"][0]=="broad"
    assert "direct" in expanded["twist"]
    assert "semantic" in expanded["twist"]
    assert "noise" not in expanded["twist"]


def test_retention_direct_claim_evidence_gets_strong_story_relevance_boost():
    from types import SimpleNamespace

    from app.services import reference_visual_review as review

    story={"hook":("Jin awakens with memories that rewrite his death.",)}
    direct={"hook":("panel-direct",)}
    direct_region=SimpleNamespace(
        panel_id="panel-direct",source_order=10,
        observation_json={"visible_facts":["Black-haired Jin lies awake in bed holding an object."]},
    )
    broad_region=SimpleNamespace(
        panel_id="panel-broad",source_order=20,
        observation_json={"visible_facts":["Memories of death surround Jin as he awakens."]},
    )
    direct_score=review._retention_story_relevance(direct_region,("hook",),story,direct)["hook"]
    broad_score=review._retention_story_relevance(broad_region,("hook",),story,direct)["hook"]
    assert direct_score >= 4.0
    assert direct_score > broad_score


def test_retention_claim_text_affinity_bridges_equivalent_story_wording():
    from types import SimpleNamespace

    from app.services import reference_visual_review as review

    region = SimpleNamespace(
        panel_id="snow-pill", source_order=10,
        observation_json={"visible_facts":["The Snow Plum Pill rests on a red cushion."]},
    )
    story = {"conflict": ("He decides to claim every spiritual elixir before the war.",)}
    claim_text = {"conflict": ("Jin plans to claim the Snow Plum Pill and Fire Ginseng.",)}
    without = review._retention_story_relevance(region,("conflict",),story)["conflict"]
    with_claim = review._retention_story_relevance(
        region,("conflict",),story,claim_text_by_section=claim_text
    )["conflict"]
    assert without == 0.0
    assert with_claim > 0.0

def test_retention_tokens_normalize_basic_story_plurals():
    from app.services import reference_visual_review as review

    tokens = review._retention_tokens("memories spiritual elixirs bracelets")
    assert {"memory", "elixir", "bracelet"}.issubset(tokens)
