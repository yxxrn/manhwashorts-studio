"""RED contract tests for the v2 five-role analyzer passage contract."""

from __future__ import annotations

import copy
import hashlib
import importlib
import re
from typing import Any

import pytest

V2_PROMPT_VERSION = "vision-first-story-analyzer-v2"
EDITORIAL_ROLES = (
    "hook",
    "setup",
    "escalation",
    "editorial_insight",
    "payoff_open_loop",
)
ROLE_WORD_LIMITS = {
    "hook": (8, 18),
    "setup": (15, 28),
    "escalation": (22, 38),
    "editorial_insight": (15, 30),
    "payoff_open_loop": (10, 24),
}
PASSAGE_KEYS = {
    "passage_id",
    "editorial_role",
    "text",
    "claim_ids",
    "evidence_panel_ids",
}
CTA_MARKERS = (
    "subscribe",
    "follow for more",
    "follow-for-more",
    "like",
    "comment",
    "comment below",
)


def _contract_module():
    try:
        return importlib.import_module("app.services.analyzer_contract")
    except Exception as exc:
        pytest.fail(
            "analyzer v2 import boundary is unavailable in the test body: "
            f"{exc}"
        )


def _contract_error(module):
    error_type = getattr(module, "AnalyzerContractError", None)
    if not isinstance(error_type, type) or not issubclass(error_type, Exception):
        pytest.fail("AnalyzerContractError is missing from analyzer_contract")
    return error_type


def _load_instruction(module):
    loader = getattr(module, "load_analyzer_instruction", None)
    if not callable(loader):
        pytest.fail("analyzer_contract.load_analyzer_instruction is missing")
    result = loader()
    assert isinstance(result, tuple) and len(result) == 3
    version, digest, normalized = result
    assert isinstance(version, str)
    assert isinstance(digest, str)
    assert isinstance(normalized, str)
    return version, digest, normalized


def _validate(module, chapter, expected_panel_ids=None):
    validator = getattr(module, "validate_analyzer_output", None)
    if not callable(validator):
        pytest.fail("analyzer_contract.validate_analyzer_output is missing")
    if expected_panel_ids is None:
        expected_panel_ids = tuple(
            item["panel_id"] for item in chapter["observations"]
        )
    validator(chapter, expected_panel_ids=tuple(expected_panel_ids))


def _observation(panel_id: str, source_index: int, fact: str, inference: str):
    return {
        "panel_id": panel_id,
        "source_asset_id": "asset-dock",
        "strip_region_id": f"region-{panel_id}",
        "source_index": source_index,
        "region_bounds": {
            "x": 0,
            "y": source_index * 100,
            "width": 800,
            "height": 100,
        },
        "coverage_map_version": "coverage-v2",
        "coverage_map_hash": "coverage-hash-v2",
        "visible_facts": [fact],
        "dialogue_or_ocr": [],
        "inferences": [inference],
        "uncertainties": [],
        "evidence_refs": [panel_id],
    }


def _chapter():
    panel_ids = ("panel-dock-1", "panel-dock-2", "panel-dock-3")
    claims = [
        {
            "claim_id": "claim-dock-motive",
            "claim_type": "fact",
            "text": "Mara wants the brass compass before the boat leaves.",
            "qualification": "The compass and dock sequence visibly ground this purpose.",
            "evidence_panel_ids": [panel_ids[0]],
        },
        {
            "claim_id": "claim-dock-risk",
            "claim_type": "interpretation",
            "text": "The guard's approach changes Mara's route.",
            "qualification": "The panels suggest this consequence; the guard's intent is not stated.",
            "evidence_panel_ids": [panel_ids[0], panel_ids[1]],
        },
        {
            "claim_id": "claim-dock-boat",
            "claim_type": "fact",
            "text": "A dark boat is leaving while Mara remains outside.",
            "qualification": "The boat and Mara's position are visible in the final panel.",
            "evidence_panel_ids": [panel_ids[2]],
        },
    ]
    passages = [
        {
            "passage_id": "passage-dock-hook",
            "editorial_role": "hook",
            "text": (
                "Mara returns to the locked dock because the brass compass could leave on the dark boat."
            ),
            "claim_ids": ["claim-dock-motive"],
            "evidence_panel_ids": [panel_ids[0], panel_ids[1]],
        },
        {
            "passage_id": "passage-dock-setup",
            "editorial_role": "setup",
            "text": (
                "She hides the compass as a guard approaches, but the gate opens for a dark boat while she remains outside."
            ),
            "claim_ids": ["claim-dock-motive", "claim-dock-boat"],
            "evidence_panel_ids": list(panel_ids),
        },
        {
            "passage_id": "passage-dock-escalation",
            "editorial_role": "escalation",
            "text": (
                "That detail changes the threat: the guard may be more than a blocker, and he appears to control access to the boat while Mara's move leaves the evidence sailing away before she can ask who is waiting inside."
            ),
            "claim_ids": ["claim-dock-risk", "claim-dock-boat"],
            "evidence_panel_ids": list(panel_ids),
        },
        {
            "passage_id": "passage-dock-insight",
            "editorial_role": "editorial_insight",
            "text": (
                "The clever part is the compass: it turns a locked gate into a choice between staying visible and losing a crucial clue."
            ),
            "claim_ids": ["claim-dock-motive", "claim-dock-risk"],
            "evidence_panel_ids": [panel_ids[0], panel_ids[1]],
        },
        {
            "passage_id": "passage-dock-payoff",
            "editorial_role": "payoff_open_loop",
            "text": "Mara waits outside, but who did the gate open for inside the dark boat?",
            "claim_ids": ["claim-dock-boat"],
            "evidence_panel_ids": [panel_ids[2]],
        },
    ]
    return {
        "observations": [
            _observation(
                panel_ids[0],
                0,
                "Mara grips a brass compass beside a locked dock gate.",
                "The compass appears to be the reason she returned.",
            ),
            _observation(
                panel_ids[1],
                1,
                "Mara hides the compass when a guard turns toward her.",
                "She is trying to avoid being recognized.",
            ),
            _observation(
                panel_ids[2],
                2,
                "The gate opens toward a dark boat while Mara remains outside.",
                "The boat may carry the clue beyond Mara's reach.",
            ),
        ],
        "continuity_ledger": {
            "chunks": [
                {
                    "chunk_id": "chunk-dock-0",
                    "panel_ids": [panel_ids[0], panel_ids[1]],
                },
                {
                    "chunk_id": "chunk-dock-1",
                    "panel_ids": [panel_ids[1], panel_ids[2]],
                },
            ],
            "entities": [
                {
                    "entity_id": "entity-mara",
                    "canonical_name": "Mara",
                    "aliases": ["the cartographer"],
                    "panel_ids": list(panel_ids),
                }
            ],
            "motives": [
                {
                    "entity_id": "entity-mara",
                    "text": "recover the compass before the boat leaves",
                    "evidence_panel_ids": [panel_ids[0]],
                }
            ],
            "state_changes": [
                {
                    "entity_id": "entity-mara",
                    "from": "holding the compass openly",
                    "to": "hiding the compass",
                    "evidence_panel_ids": [panel_ids[0], panel_ids[1]],
                }
            ],
            "causal_links": [
                {
                    "from_panel_id": panel_ids[0],
                    "to_panel_id": panel_ids[1],
                    "reason": "the guard approaches the dock",
                    "evidence_panel_ids": [panel_ids[0], panel_ids[1]],
                },
                {
                    "from_panel_id": panel_ids[1],
                    "to_panel_id": panel_ids[2],
                    "reason": "Mara waits outside after hiding the compass",
                    "evidence_panel_ids": [panel_ids[1], panel_ids[2]],
                },
            ],
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
                "who_wants_what": "Mara wants the brass compass before the boat leaves.",
                "obstacle": "A guard approaches while the dock gate remains locked.",
                "decision": "Mara hides the compass and waits outside.",
                "consequence": "The boat can move before she reaches it.",
                "changed_stakes": "She may lose access to whoever the gate admitted.",
                "unresolved_question": "Who is inside the dark boat?",
            }
        },
        "script_passages": passages,
    }


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def _passage(chapter, role: str) -> dict[str, Any]:
    return next(
        passage
        for passage in chapter["script_passages"]
        if passage["editorial_role"] == role
    )


def _tokens(count: int) -> str:
    return " ".join(f"token{index}" for index in range(count))


def test_v2_loads_the_committed_versioned_instruction():
    module = _contract_module()
    version, digest, normalized = _load_instruction(module)

    assert getattr(module, "PROMPT_VERSION", None) == V2_PROMPT_VERSION
    assert version == V2_PROMPT_VERSION
    prompt_path = getattr(module, "PROMPT_PATH", None)
    assert getattr(prompt_path, "name", None) == "vision_first_story_analyzer_v2.txt"
    assert normalized == normalized.replace("\r\n", "\n")
    assert "\r" not in normalized
    assert digest == hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def test_v2_prompt_states_the_ordered_five_role_editorial_contract():
    module = _contract_module()
    version, _, normalized = _load_instruction(module)
    assert version == V2_PROMPT_VERSION
    prompt = normalized.lower()

    required_fragments = (
        "observe every ordered panel",
        "complete reconciled chapter",
        "cinematic story detective",
        "varied human sentence rhythm",
        "motives, consequences, hidden clues",
        "qualified",
        "no fixed intro",
        "no title",
        "no cover",
        "channel cta",
        "38-50",
        "90-125 words",
        "exactly five",
        "hook",
        "setup",
        "escalation",
        "editorial_insight",
        "payoff_open_loop",
        "evidence-grounded unresolved story question",
    )
    for fragment in required_fragments:
        assert fragment in prompt, fragment

    assert prompt.index("observe every ordered panel") < prompt.index(
        "exactly five"
    )


def test_positive_v2_chapter_preserves_all_existing_evidence_gates():
    module = _contract_module()
    chapter = _chapter()
    _validate(module, chapter)

    passages = chapter["script_passages"]
    assert len(passages) == 5
    assert [passage["editorial_role"] for passage in passages] == list(
        EDITORIAL_ROLES
    )
    assert all(set(passage) == PASSAGE_KEYS for passage in passages)
    assert len({passage["passage_id"] for passage in passages}) == 5
    assert all(passage["claim_ids"] for passage in passages)
    assert all(
        set().union(
            *(
                next(
                    claim["evidence_panel_ids"]
                    for claim in chapter["evidence_graph"]["claims"]
                    if claim["claim_id"] == claim_id
                )
                for claim_id in passage["claim_ids"]
            )
        )
        <= set(passage["evidence_panel_ids"])
        for passage in passages
    )

    total_words = sum(_word_count(passage["text"]) for passage in passages)
    assert 90 <= total_words <= 125
    for passage in passages:
        minimum, maximum = ROLE_WORD_LIMITS[passage["editorial_role"]]
        assert minimum <= _word_count(passage["text"]) <= maximum
    assert passages[-1]["text"].rstrip().endswith("?")


def test_rejects_unexpected_top_level_output_key():
    module = _contract_module()
    chapter = _chapter()
    chapter["unexpected_top_level"] = "must be rejected"

    with pytest.raises(_contract_error(module)):
        _validate(module, chapter)


def test_v2_does_not_require_a_fixed_opening_sentence():
    module = _contract_module()
    chapter = copy.deepcopy(_chapter())
    chapter["script_passages"][0]["text"] = (
        "At the dock, Mara spots a brass compass beside the locked gate as a dark boat waits."
    )

    _validate(module, chapter)
    assert chapter["script_passages"][0]["text"] != _chapter()["script_passages"][0]["text"]


@pytest.mark.parametrize(
    "narrative_text",
    (
        "Mara moves like lightning beside the locked dock.",
        "Mara comments on the clue beside the locked dock.",
        "I like this story's hidden clue beside the locked dock today.",
    ),
)
def test_allows_narrative_like_and_comment_without_channel_cta(narrative_text):
    module = _contract_module()
    chapter = _chapter()
    _passage(chapter, "hook")["text"] = narrative_text

    _validate(module, chapter)


@pytest.mark.parametrize("count", (4, 6))
def test_script_passages_require_exactly_five(count):
    module = _contract_module()
    chapter = _chapter()
    if count == 4:
        chapter["script_passages"].pop()
    else:
        extra = copy.deepcopy(chapter["script_passages"][-1])
        extra["passage_id"] = "passage-dock-extra"
        chapter["script_passages"].append(extra)

    with pytest.raises(_contract_error(module)):
        _validate(module, chapter)


@pytest.mark.parametrize(
    "mutation",
    ("missing", "duplicate", "reordered", "unknown", "unexpected"),
)
def test_passages_require_exact_role_order_and_keys(mutation):
    module = _contract_module()
    chapter = _chapter()
    passages = chapter["script_passages"]
    if mutation == "missing":
        passages[0].pop("editorial_role")
    elif mutation == "duplicate":
        passages[1]["editorial_role"] = "hook"
    elif mutation == "reordered":
        passages[0]["editorial_role"], passages[1]["editorial_role"] = (
            passages[1]["editorial_role"],
            passages[0]["editorial_role"],
        )
    elif mutation == "unknown":
        passages[-1]["editorial_role"] = "teaser"
    else:
        passages[0]["unexpected"] = "must be rejected"

    with pytest.raises(_contract_error(module)):
        _validate(module, chapter)


@pytest.mark.parametrize("mutation", ("empty", "unknown", "missing_evidence"))
def test_passages_require_grounded_nonempty_claim_evidence(mutation):
    module = _contract_module()
    chapter = _chapter()
    passage = chapter["script_passages"][0]
    if mutation == "empty":
        passage["claim_ids"] = []
    elif mutation == "unknown":
        passage["claim_ids"] = ["claim-not-in-graph"]
    else:
        passage["evidence_panel_ids"] = ["panel-dock-2"]

    with pytest.raises(_contract_error(module)):
        _validate(module, chapter)


def test_total_and_role_word_bounds_are_measurable():
    chapter = _chapter()
    passages = chapter["script_passages"]
    total_words = sum(_word_count(passage["text"]) for passage in passages)

    assert 90 <= total_words <= 125
    assert [
        _word_count(passage["text"]) for passage in passages
    ] == [16, 20, 38, 22, 14]
    assert all(
        ROLE_WORD_LIMITS[passage["editorial_role"]][0]
        <= _word_count(passage["text"])
        <= ROLE_WORD_LIMITS[passage["editorial_role"]][1]
        for passage in passages
    )


@pytest.mark.parametrize(
    ("role", "count"),
    (
        ("hook", 7),
        ("setup", 29),
        ("escalation", 21),
        ("editorial_insight", 31),
        ("payoff_open_loop", 25),
    ),
)
def test_each_role_rejects_text_outside_its_word_guardrail(role, count):
    module = _contract_module()
    chapter = _chapter()
    _passage(chapter, role)["text"] = _tokens(count)

    with pytest.raises(_contract_error(module)):
        _validate(module, chapter)


def test_total_narration_rejects_too_few_words():
    module = _contract_module()
    chapter = _chapter()
    for passage in chapter["script_passages"]:
        passage["text"] = "short words"

    with pytest.raises(_contract_error(module)):
        _validate(module, chapter)


def test_total_narration_rejects_too_many_words():
    module = _contract_module()
    chapter = _chapter()
    escalation = _passage(chapter, "escalation")
    escalation["text"] = f"{escalation['text']} {_tokens(40)}"

    with pytest.raises(_contract_error(module)):
        _validate(module, chapter)


@pytest.mark.parametrize("marker", CTA_MARKERS)
def test_rejects_generic_channel_cta_language(marker):
    module = _contract_module()
    chapter = _chapter()
    _passage(chapter, "hook")["text"] = (
        f"Mara returns to the locked dock; please {marker} before the clue disappears."
    )

    with pytest.raises(_contract_error(module)):
        _validate(module, chapter)


def test_payoff_must_end_on_an_evidence_grounded_open_question():
    module = _contract_module()
    chapter = _chapter()
    _passage(chapter, "payoff_open_loop")["text"] = (
        "Mara watches the dark boat leave with the compass alone."
    )

    with pytest.raises(_contract_error(module)):
        _validate(module, chapter)


def test_rejects_repeated_normalized_opening_phrases():
    module = _contract_module()
    chapter = _chapter()
    _passage(chapter, "setup")["text"] = (
        "Mara returns to the locked gate while the guard watches from the boat, and the compass stays hidden."
    )

    with pytest.raises(_contract_error(module)):
        _validate(module, chapter)


def test_rejects_verbatim_repeated_sentences_across_passages():
    module = _contract_module()
    chapter = _chapter()
    _passage(chapter, "hook")["text"] = (
        "Mara returns to the dock. The gate hides a clue."
    )
    _passage(chapter, "setup")["text"] = (
        "She watches the guard closely. The gate hides a clue. The boat waits beyond the water."
    )

    with pytest.raises(_contract_error(module)):
        _validate(module, chapter)


def test_allows_repeated_normalized_sentence_within_one_passage():
    module = _contract_module()
    chapter = _chapter()
    _passage(chapter, "hook")["text"] = (
        "Mara waits. Mara waits. The brass compass could leave on the dark boat."
    )

    _validate(module, chapter)
