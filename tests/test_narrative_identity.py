"""Contract tests for the versioned Sharp Friend narrative identity."""

import hashlib
import importlib
from dataclasses import replace
from pathlib import Path

import pytest


PROMPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "prompts"
    / "vision_first_story_analyzer_v3.txt"
)


def _read_v3_prompt_directly() -> str:
    if not PROMPT_PATH.exists():
        pytest.fail(f"missing v3 prompt resource: {PROMPT_PATH.name}")
    raw = PROMPT_PATH.read_bytes()
    assert b"\r" not in raw
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        pytest.fail(f"v3 prompt is not UTF-8: {exc}")


def test_v3_prompt_resource_is_lf_utf8_and_normative():
    prompt = _read_v3_prompt_directly()
    lowered = prompt.lower()
    required = (
        "contract id: vision-first_editorial_story_engine.analyzer",
        "version: vision-first-story-analyzer-v3",
        "narrative profile: sharp_friend_v1",
        "observe every ordered panel",
        "reconcile all panel, observation, chunk, coverage, continuity, synthesis, and claim gates before prose",
        "story_spine",
        "ending_kind",
        "wants",
        "obstacle",
        "decision",
        "consequence",
        "changed stakes",
        "unresolved direction",
        "contractions",
        "varied sentence lengths",
        "causal connectors",
        "selective evidence-grounded commentary",
        "four to six",
        "cliffhanger",
        "open_question",
        "spoken text",
        "do not generate display_text",
        "no fixed intro",
        "no channel cta",
        "copied speech-balloon dialogue",
        "do not invent an identity, motive, relationship, event, or causal link",
    )
    for fragment in required:
        assert fragment in lowered, fragment
    assert lowered.index("observe every ordered panel") < lowered.index("ending_kind")
    assert lowered.index("ending_kind") < lowered.index("four to six")


def _identity_module():
    try:
        return importlib.import_module("app.services.narrative_identity")
    except Exception as exc:
        pytest.fail(f"narrative identity import failed in the test body: {exc}")


def _identity_error(module):
    error_type = getattr(module, "NarrativeIdentityError", None)
    assert isinstance(error_type, type)
    assert issubclass(error_type, Exception)
    return error_type


def test_profile_is_frozen_and_has_exact_sharp_friend_identity_fields():
    module = _identity_module()
    profile = getattr(module, "SHARP_FRIEND_V1", None)
    assert profile.profile_id == "sharp_friend_v1"
    assert profile.profile_version == "1.0.0"
    assert profile.language == "en-US"
    assert profile.identity == (
        "a clever, friendly, perceptive friend under controlled tension"
    )
    assert (profile.target_word_min, profile.target_word_max) == (90, 125)
    assert (profile.passage_min, profile.passage_max) == (4, 6)
    assert profile.allowed_ending_kinds == (
        "cliffhanger",
        "consequence",
        "open_question",
    )
    assert profile.prompt_version == "vision-first-story-analyzer-v3"
    assert profile.prompt_filename == "vision_first_story_analyzer_v3.txt"
    assert len(profile.contract_sha256) == 64
    with pytest.raises((AttributeError, TypeError)):
        profile.profile_id = "other"


def test_unknown_profile_fails_without_leaking_resource_details():
    module = _identity_module()
    error_type = _identity_error(module)
    with pytest.raises(error_type, match="unknown narrative identity") as caught:
        module.get_narrative_identity("not_a_real_profile")
    assert "vision_first_story_analyzer_v3" not in str(caught.value)
    assert "/" not in str(caught.value)


def test_loader_returns_lf_prompt_and_matches_profile_contract():
    module = _identity_module()
    version, digest, text = module.load_narrative_instruction("sharp_friend_v1")
    assert version == "vision-first-story-analyzer-v3"
    assert digest == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert "\r" not in text
    assert "observe every ordered panel" in text.lower()
    assert module.get_narrative_identity("sharp_friend_v1").contract_sha256


def test_profile_loader_rejects_profile_hash_tampering(monkeypatch):
    module = _identity_module()
    original = module.SHARP_FRIEND_V1
    registry = dict(module._PROFILE_REGISTRY)
    registry["sharp_friend_v1"] = replace(original, contract_sha256="0" * 64)
    monkeypatch.setattr(module, "_PROFILE_REGISTRY", registry)
    with pytest.raises(module.NarrativeIdentityError):
        module.load_narrative_instruction("sharp_friend_v1")


def test_profile_loader_rejects_prompt_filename_tampering(monkeypatch):
    module = _identity_module()
    original = module.SHARP_FRIEND_V1
    registry = dict(module._PROFILE_REGISTRY)
    registry["sharp_friend_v1"] = replace(
        original,
        prompt_filename="vision_first_story_analyzer_v1.txt",
    )
    monkeypatch.setattr(module, "_PROFILE_REGISTRY", registry)
    with pytest.raises(module.NarrativeIdentityError):
        module.load_narrative_instruction("sharp_friend_v1")


def test_analyzer_loader_defaults_to_unchanged_v2_and_selects_v3_only_explicitly():
    module = importlib.import_module("app.services.analyzer_contract")
    default_version, default_digest, default_text = module.load_analyzer_instruction()
    assert default_version == "vision-first-story-analyzer-v2"
    assert "Version: vision-first-story-analyzer-v2" in default_text
    selected_version, selected_digest, selected_text = module.load_analyzer_instruction(
        narrative_profile_id="sharp_friend_v1"
    )
    assert selected_version == "vision-first-story-analyzer-v3"
    assert selected_digest != default_digest
    assert selected_text != default_text


def test_analyzer_unknown_explicit_profile_fails_closed_without_v2_fallback():
    module = importlib.import_module("app.services.analyzer_contract")
    with pytest.raises(module.AnalyzerContractError) as caught:
        module.load_analyzer_instruction(narrative_profile_id="missing_profile")
    assert caught.value.code == "analyzer_contract_invalid"
    assert "vision-first-story-analyzer-v2" not in str(caught.value)
    assert "missing_profile" not in str(caught.value)


def _v3_chapter(
    *,
    chapter_prefix: str,
    passages: list[dict[str, object]],
    ending_kind: str,
    dialogue: list[str] | None = None,
) -> dict[str, object]:
    panel_ids = tuple(f"{chapter_prefix}-panel-{index}" for index in range(3))
    dialogue_values = dialogue or []
    observations = []
    for source_index, panel_id in enumerate(panel_ids):
        observations.append(
            {
                "panel_id": panel_id,
                "source_asset_id": f"{chapter_prefix}-asset",
                "strip_region_id": f"{chapter_prefix}-region-{source_index}",
                "source_index": source_index,
                "region_bounds": {
                    "x": 0,
                    "y": source_index * 100,
                    "width": 800,
                    "height": 100,
                },
                "coverage_map_version": "coverage-v3-test",
                "coverage_map_hash": f"{chapter_prefix}-coverage",
                "visible_facts": [f"{chapter_prefix} fact {source_index}"],
                "dialogue_or_ocr": dialogue_values if source_index == 0 else [],
                "inferences": [f"{chapter_prefix} inference {source_index}"],
                "uncertainties": [],
                "evidence_refs": [panel_id],
            }
        )
    claims = [
        {
            "claim_id": f"{chapter_prefix}-claim-fact",
            "claim_type": "fact",
            "text": f"{chapter_prefix} fact claim is visible.",
            "qualification": "The panel visibly supports this fact.",
            "evidence_panel_ids": [panel_ids[0]],
        },
        {
            "claim_id": f"{chapter_prefix}-claim-interpretation",
            "claim_type": "interpretation",
            "text": f"{chapter_prefix} decision may change the route.",
            "qualification": "The sequence suggests this consequence but does not prove intent.",
            "evidence_panel_ids": [panel_ids[1], panel_ids[2]],
        },
    ]
    return {
        "observations": observations,
        "continuity_ledger": {
            "chunks": [
                {
                    "chunk_id": f"{chapter_prefix}-chunk-0",
                    "panel_ids": list(panel_ids[:2]),
                },
                {
                    "chunk_id": f"{chapter_prefix}-chunk-1",
                    "panel_ids": list(panel_ids[1:]),
                },
            ],
            "entities": [
                {
                    "entity_id": f"{chapter_prefix}-entity",
                    "canonical_name": f"{chapter_prefix} witness",
                    "aliases": [],
                    "panel_ids": list(panel_ids),
                }
            ],
            "motives": [],
            "state_changes": [],
            "causal_links": [],
            "reconciled_after_final_chunk": True,
        },
        "evidence_graph": {"claims": claims},
        "coverage_manifest": {
            "total_panels": 3,
            "processed_panels": 3,
            "panel_ids": list(panel_ids),
            "source_content_coverage_ratio": 1.0,
            "unresolved_material_area": 0,
            "material_unresolved_regions": [],
            "reconciliation_complete": True,
        },
        "narrative_outline": {
            "story_spine": {
                "who_wants_what": f"{chapter_prefix} wants an answer.",
                "obstacle": f"{chapter_prefix} faces a locked route.",
                "decision": f"{chapter_prefix} chooses a risky opening.",
                "consequence": f"{chapter_prefix} changes the immediate balance.",
                "changed_stakes": f"{chapter_prefix} may lose the next chance.",
                "unresolved_question": f"What will {chapter_prefix} do next?",
            },
            "ending_kind": ending_kind,
        },
        "script_passages": passages,
    }


def _fit_passage(text: str, word_count: int, ending: str) -> str:
    words = text.split()[:word_count]
    assert len(words) == word_count
    return " ".join(words).rstrip(".!?") + ending


def _passages(
    prefix: str,
    count: int,
    ending_kind: str,
    total_words: int | None = None,
) -> list[dict[str, object]]:
    if total_words is None:
        budgets = {4: [24, 24, 26, 26], 6: [16, 16, 17, 17, 17, 17]}[count]
    else:
        base, remainder = divmod(total_words, count)
        budgets = [base + (index < remainder) for index in range(count)]
    source_texts = [
        "At the first panel, the witness spots a red signal and chooses the locked route while pressure gathers around the gate. The visible clue gives this decision a concrete cost.",
        "By the next beat, the guarded path closes, and the visible clue changes what the witness can safely attempt. The sequence keeps the motive grounded without proving every hidden intention.",
        "Then the sequence turns: the witness may risk an opening, while the panels suggest that someone else controls the route. That qualified possibility matters because the immediate choice narrows.",
        "The consequence is concrete, because losing that opening could strand the witness before the hidden direction becomes clear. The evidence supports the pressure, not an invented identity or motive.",
        "Still, the evidence leaves room for a sharper turn rather than proving the hidden motive behind the waiting group. A careful friend would keep that uncertainty visible.",
        "For now, the next move remains unresolved, and the final image keeps the cost close to the surface. The chapter earns its tension from what the panels show and withhold.",
    ]
    labels = (
        "opening_observation",
        "rising_choice",
        "pressure_turn",
        "visible_consequence",
        "qualified_insight",
        "unresolved_direction",
    )
    claim_ids = (f"{prefix}-claim-fact", f"{prefix}-claim-interpretation")
    panel_ids = [f"{prefix}-panel-{index}" for index in range(3)]
    passages: list[dict[str, object]] = []
    for index, budget in enumerate(budgets):
        ending = "."
        if index == count - 1:
            ending = "?" if ending_kind == "open_question" else "."
        passages.append(
            {
                "passage_id": f"{prefix}-passage-{index}",
                "editorial_role": labels[index],
                "text": _fit_passage(source_texts[index], budget, ending),
                "claim_ids": [claim_ids[index % 2]],
                "evidence_panel_ids": panel_ids,
            }
        )
    return passages


def _validate_v3(chapter: dict[str, object]) -> None:
    module = importlib.import_module("app.services.analyzer_contract")
    expected = tuple(item["panel_id"] for item in chapter["observations"])
    module.validate_analyzer_output(
        chapter,
        expected_panel_ids=expected,
        narrative_profile_id="sharp_friend_v1",
    )


@pytest.mark.parametrize("count", (4, 6))
def test_v3_accepts_four_or_six_grounded_passages(count):
    chapter = _v3_chapter(
        chapter_prefix=f"chapter-{count}",
        passages=_passages(f"chapter-{count}", count, "consequence"),
        ending_kind="consequence",
    )
    _validate_v3(chapter)


def test_v3_accepts_grounded_open_question_and_non_question_consequence():
    consequence = _v3_chapter(
        chapter_prefix="consequence",
        passages=_passages("consequence", 4, "consequence"),
        ending_kind="consequence",
    )
    _validate_v3(consequence)
    assert not consequence["script_passages"][-1]["text"].rstrip().endswith("?")

    question = _v3_chapter(
        chapter_prefix="question",
        passages=_passages("question", 6, "open_question"),
        ending_kind="open_question",
    )
    _validate_v3(question)
    assert question["script_passages"][-1]["text"].rstrip().endswith("?")


def test_v3_accepts_cliffhanger_and_natural_contractions():
    chapter = _v3_chapter(
        chapter_prefix="cliffhanger",
        passages=_passages("cliffhanger", 4, "cliffhanger"),
        ending_kind="cliffhanger",
    )
    chapter["script_passages"][0]["text"] = "It isn't safe, but the witness keeps the clue close."
    _validate_v3(chapter)


def test_v3_target_word_range_is_advisory_not_a_hard_failure():
    chapter = _v3_chapter(
        chapter_prefix="short-grounded",
        passages=_passages("short-grounded", 4, "cliffhanger", total_words=72),
        ending_kind="cliffhanger",
    )
    _validate_v3(chapter)


def test_v3_changes_with_evidence_and_does_not_require_a_fixed_opening():
    first = _v3_chapter(
        chapter_prefix="dock",
        passages=_passages("dock", 4, "consequence"),
        ending_kind="consequence",
    )
    second = _v3_chapter(
        chapter_prefix="tower",
        passages=_passages("tower", 6, "cliffhanger"),
        ending_kind="cliffhanger",
    )
    _validate_v3(first)
    _validate_v3(second)
    assert first["observations"] != second["observations"]
    assert first["script_passages"][0]["text"] != second["script_passages"][0]["text"]


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_claim",
        "foreign_panel",
        "unsupported_claim",
        "unqualified_interpretation",
        "copied_dialogue",
        "cta",
        "generic_hype",
        "ending_mismatch",
        "unknown_ending_kind",
    ),
)
def test_v3_rejects_ungrounded_or_forbidden_narration(mutation):
    module = importlib.import_module("app.services.analyzer_contract")
    chapter = _v3_chapter(
        chapter_prefix="reject",
        passages=_passages("reject", 4, "consequence"),
        ending_kind="consequence",
        dialogue=["the marked gate opens for the patient red-eyed stranger"],
    )
    passage = chapter["script_passages"][0]
    if mutation == "missing_claim":
        passage["claim_ids"] = []
    elif mutation == "foreign_panel":
        passage["evidence_panel_ids"] = ["foreign-panel"]
    elif mutation == "unsupported_claim":
        passage["claim_ids"] = ["claim-not-in-graph"]
    elif mutation == "unqualified_interpretation":
        chapter["evidence_graph"]["claims"][1]["qualification"] = ""
    elif mutation == "copied_dialogue":
        passage["text"] = "The marked gate opens for the patient red-eyed stranger."
    elif mutation == "cta":
        passage["text"] = "The gate shifts, so subscribe for more."
    elif mutation == "generic_hype":
        passage["text"] = "The epic battle changes the route."
    elif mutation == "ending_mismatch":
        chapter["script_passages"][-1]["text"] += "?"
    else:
        chapter["narrative_outline"]["ending_kind"] = "teaser"
    with pytest.raises(module.AnalyzerContractError) as caught:
        _validate_v3(chapter)
    assert caught.value.code == "analyzer_contract_invalid"
    assert "marked gate opens" not in str(caught.value)


def test_v3_reuses_shared_observation_coverage_and_continuity_gates():
    module = importlib.import_module("app.services.analyzer_contract")
    chapter = _v3_chapter(
        chapter_prefix="shared-gates",
        passages=_passages("shared-gates", 4, "consequence"),
        ending_kind="consequence",
    )
    chapter["observations"].pop()
    with pytest.raises(module.AnalyzerContractError):
        _validate_v3(chapter)

    chapter = _v3_chapter(
        chapter_prefix="coverage-gate",
        passages=_passages("coverage-gate", 4, "consequence"),
        ending_kind="consequence",
    )
    chapter["coverage_manifest"]["source_content_coverage_ratio"] = 0.9
    with pytest.raises(module.AnalyzerContractError):
        _validate_v3(chapter)
