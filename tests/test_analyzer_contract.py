import copy
import hashlib
import importlib
from pathlib import Path

import pytest

PROMPT_VERSION = "vision-first-story-analyzer-v1"
REQUIRED_OUTPUT_KEYS = {
    "observations",
    "continuity_ledger",
    "evidence_graph",
    "coverage_manifest",
    "narrative_outline",
    "script_passages",
}
STORY_SPINE_FIELDS = (
    "who_wants_what",
    "obstacle",
    "decision",
    "consequence",
    "changed_stakes",
    "unresolved_question",
)
GENERIC_CTA_MARKERS = ("subscribe", "like and subscribe", "follow for more")


def _contract_module():
    try:
        return importlib.import_module("app.services.analyzer_contract")
    except Exception as exc:
        pytest.fail(
            "analyzer contract import boundary is unavailable in the test body: "
            f"{exc}"
        )


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


def _contract_error(module):
    error_type = getattr(module, "AnalyzerContractError", None)
    if not isinstance(error_type, type) or not issubclass(error_type, Exception):
        pytest.fail("AnalyzerContractError is missing from analyzer_contract")
    return error_type


def _validate(module, chapter, expected_panel_ids=None):
    validator = getattr(module, "validate_analyzer_output", None)
    if not callable(validator):
        pytest.fail("analyzer_contract.validate_analyzer_output is missing")
    if expected_panel_ids is None:
        expected_panel_ids = tuple(
            item["panel_id"] for item in chapter["observations"]
        )
    validator(chapter, expected_panel_ids=tuple(expected_panel_ids))


def _observation(panel_id, asset_id, source_index, fact, inference):
    return {
        "panel_id": panel_id,
        "source_asset_id": asset_id,
        "strip_region_id": f"region-{panel_id}",
        "source_index": source_index,
        "region_bounds": {"x": 0, "y": source_index * 100, "width": 800, "height": 100},
        "coverage_map_version": "coverage-v1",
        "coverage_map_hash": "coverage-hash-v1",
        "visible_facts": [fact],
        "dialogue_or_ocr": [],
        "inferences": [inference],
        "uncertainties": [],
        "evidence_refs": [panel_id],
    }


def _chapter_one():
    panel_ids = ("panel-dock-1", "panel-dock-2", "panel-dock-3")
    return {
        "observations": [
            _observation(
                panel_ids[0],
                "asset-dock",
                0,
                "Mara grips a brass compass beside a locked dock gate.",
                "The compass appears to be the reason she returned.",
            ),
            _observation(
                panel_ids[1],
                "asset-dock",
                1,
                "Mara hides the compass when a guard turns toward her.",
                "She is trying to avoid being recognized.",
            ),
            _observation(
                panel_ids[2],
                "asset-dock",
                2,
                "The gate opens toward a dark boat while Mara remains outside.",
                "Her next decision may put the boat owner ahead of her.",
            ),
        ],
        "continuity_ledger": {
            "chunks": [
                {
                    "chunk_id": "chunk-dock-0",
                    "panel_ids": [panel_ids[0], panel_ids[1]],
                    "overlap_with_next": [panel_ids[1]],
                },
                {
                    "chunk_id": "chunk-dock-1",
                    "panel_ids": [panel_ids[1], panel_ids[2]],
                    "overlap_with_previous": [panel_ids[1]],
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
        "evidence_graph": {
            "claims": [
                {
                    "claim_id": "claim-dock-motive",
                    "claim_type": "fact",
                    "text": "Mara wants the compass before the boat leaves.",
                    "qualification": "Visible purpose is grounded in the compass and dock panels.",
                    "evidence_panel_ids": [panel_ids[0]],
                },
                {
                    "claim_id": "claim-dock-risk",
                    "claim_type": "interpretation",
                    "text": "The guard's approach changes Mara's route.",
                    "qualification": "The panels suggest this consequence; the guard's intent is not stated.",
                    "evidence_panel_ids": [panel_ids[0], panel_ids[1]],
                },
            ]
        },
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
                "changed_stakes": "The compass may leave with someone else.",
                "unresolved_question": "Who is inside the dark boat?",
            }
        },
        "script_passages": [
            {
                "passage_id": "passage-dock-1",
                "text": "Mara came for one thing, but the guard's arrival makes the dock a trap.",
                "claim_ids": ["claim-dock-motive", "claim-dock-risk"],
                "evidence_panel_ids": [panel_ids[0], panel_ids[1]],
            }
        ],
    }


def _chapter_two():
    panel_ids = ("panel-orchard-1", "panel-orchard-2", "panel-orchard-3")
    return {
        "observations": [
            _observation(
                panel_ids[0],
                "asset-orchard",
                0,
                "Ilan finds a red ribbon tied to a broken branch.",
                "The ribbon may be a signal left for Ilan.",
            ),
            _observation(
                panel_ids[1],
                "asset-orchard",
                1,
                "Ilan follows footprints away from the orchard wall.",
                "The footprints suggest someone left in a hurry.",
            ),
            _observation(
                panel_ids[2],
                "asset-orchard",
                2,
                "A lantern burns inside an empty watchtower.",
                "The missing traveler may still be nearby.",
            ),
        ],
        "continuity_ledger": {
            "chunks": [
                {
                    "chunk_id": "chunk-orchard-0",
                    "panel_ids": [panel_ids[0], panel_ids[1]],
                    "overlap_with_next": [panel_ids[1]],
                },
                {
                    "chunk_id": "chunk-orchard-1",
                    "panel_ids": [panel_ids[1], panel_ids[2]],
                    "overlap_with_previous": [panel_ids[1]],
                },
            ],
            "entities": [
                {
                    "entity_id": "entity-ilan",
                    "canonical_name": "Ilan",
                    "aliases": ["the courier"],
                    "panel_ids": list(panel_ids),
                }
            ],
            "motives": [
                {
                    "entity_id": "entity-ilan",
                    "text": "find the traveler who left the ribbon",
                    "evidence_panel_ids": [panel_ids[0]],
                }
            ],
            "state_changes": [
                {
                    "entity_id": "entity-ilan",
                    "from": "searching at the orchard wall",
                    "to": "following footprints toward the watchtower",
                    "evidence_panel_ids": [panel_ids[0], panel_ids[1], panel_ids[2]],
                }
            ],
            "causal_links": [
                {
                    "from_panel_id": panel_ids[0],
                    "to_panel_id": panel_ids[1],
                    "reason": "the ribbon points toward the footprints",
                    "evidence_panel_ids": [panel_ids[0], panel_ids[1]],
                },
                {
                    "from_panel_id": panel_ids[1],
                    "to_panel_id": panel_ids[2],
                    "reason": "the footprints lead to the lit watchtower",
                    "evidence_panel_ids": [panel_ids[1], panel_ids[2]],
                },
            ],
            "reconciled_after_final_chunk": True,
        },
        "evidence_graph": {
            "claims": [
                {
                    "claim_id": "claim-orchard-signal",
                    "claim_type": "fact",
                    "text": "Ilan follows a red ribbon and footprints.",
                    "qualification": "The ribbon and footprints are visible in sequence.",
                    "evidence_panel_ids": [panel_ids[0], panel_ids[1]],
                },
                {
                    "claim_id": "claim-orchard-lantern",
                    "claim_type": "interpretation",
                    "text": "The missing traveler may be near the watchtower.",
                    "qualification": "The lantern supports a possibility, not a confirmed identity.",
                    "evidence_panel_ids": [panel_ids[1], panel_ids[2]],
                },
            ]
        },
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
                "who_wants_what": "Ilan wants to find the traveler who left the ribbon.",
                "obstacle": "The trail leaves the orchard and the traveler's identity is unclear.",
                "decision": "Ilan follows the footprints to the watchtower.",
                "consequence": "A lit tower becomes the only sign of the traveler.",
                "changed_stakes": "The search has moved from the orchard into a possible ambush.",
                "unresolved_question": "Who lit the lantern?",
            }
        },
        "script_passages": [
            {
                "passage_id": "passage-orchard-1",
                "text": "The ribbon is not a message; it is a direction, and Ilan follows it toward the light.",
                "claim_ids": ["claim-orchard-signal", "claim-orchard-lantern"],
                "evidence_panel_ids": [panel_ids[0], panel_ids[1], panel_ids[2]],
            }
        ],
    }


def test_loader_normalizes_utf8_and_matches_versioned_hash_snapshot():
    module = _contract_module()
    version, digest, normalized = _load_instruction(module)

    assert version == PROMPT_VERSION
    assert normalized == normalized.replace("\r\n", "\n")
    assert "\r" not in normalized
    assert digest == hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    snapshot_path = Path(__file__).parent / "fixtures" / "vision_prompt_snapshot.sha256"
    if not snapshot_path.exists():
        pytest.fail(f"missing committed analyzer prompt snapshot: {snapshot_path}")
    assert snapshot_path.read_text(encoding="ascii").strip() == digest


def test_prompt_is_normative_and_orders_observation_before_synthesis():
    module = _contract_module()
    version, _, normalized = _load_instruction(module)
    assert version == PROMPT_VERSION
    prompt = normalized.lower()

    required_directives = (
        "must observe every ordered panel",
        "visible_fact",
        "dialogue_or_ocr",
        "inference",
        "uncertainty",
        "must track entities, aliases, motives, state changes, and causal links",
        "must reconcile continuity after the final chunk",
        "must not draft a recap until",
        "who wants what",
        "obstacle",
        "decision",
        "consequence",
        "changed stakes",
        "unresolved question",
        "conversational american english as cinematic story detective",
        "clever friend",
        "controlled tension",
        "hidden clues",
        "varied human sentence rhythm",
        "causal transitions",
        "rigid chronology",
        "generic cta",
        "fake hyperbole",
        "invented facts",
        "qualify every interpretation",
        "panel evidence",
        "claim ids",
        "automated anti-template",
        "do not replace human editorial review",
    )
    for directive in required_directives:
        assert directive in prompt, directive

    assert prompt.index("must observe every ordered panel") < prompt.index(
        "must not draft a recap until"
    )
    assert prompt.index("must not draft a recap until") < prompt.index(
        "who wants what"
    )


@pytest.mark.parametrize("missing_key", sorted(REQUIRED_OUTPUT_KEYS))
def test_complete_structured_output_rejects_each_missing_top_level_key(missing_key):
    module = _contract_module()
    chapter = _chapter_one()
    expected_panel_ids = ("panel-dock-1", "panel-dock-2", "panel-dock-3")
    _validate(module, chapter, expected_panel_ids=expected_panel_ids)

    missing = copy.deepcopy(chapter)
    del missing[missing_key]
    error_type = _contract_error(module)
    with pytest.raises(error_type):
        _validate(module, missing, expected_panel_ids=expected_panel_ids)
    assert missing_key not in missing


def _remove_observation(chapter):
    chapter["observations"].pop(1)


def _duplicate_panel_id(chapter):
    chapter["observations"][1]["panel_id"] = "panel-dock-1"


def _foreign_panel_id(chapter):
    chapter["observations"][1]["panel_id"] = "panel-foreign"


def _reorder_observations(chapter):
    chapter["observations"] = list(reversed(chapter["observations"]))


@pytest.mark.parametrize(
    "mutate",
    (
        pytest.param(_remove_observation, id="missing-observation"),
        pytest.param(_duplicate_panel_id, id="duplicate-panel-id"),
        pytest.param(_foreign_panel_id, id="foreign-panel-id"),
        pytest.param(_reorder_observations, id="reordered-observations"),
    ),
)
def test_observations_must_match_independent_expected_panel_order(mutate):
    module = _contract_module()
    chapter = _chapter_one()
    expected_panel_ids = ("panel-dock-1", "panel-dock-2", "panel-dock-3")
    mutate(chapter)
    before = copy.deepcopy(chapter)
    error_type = _contract_error(module)
    with pytest.raises(error_type):
        _validate(module, chapter, expected_panel_ids=expected_panel_ids)
    assert chapter == before


@pytest.mark.parametrize("field", STORY_SPINE_FIELDS)
@pytest.mark.parametrize("operation", ("delete", "empty"), ids=("delete", "empty"))
def test_story_spine_requires_each_reasoning_field(field, operation):
    module = _contract_module()
    chapter = _chapter_one()
    spine = chapter["narrative_outline"]["story_spine"]
    if operation == "delete":
        del spine[field]
    else:
        spine[field] = ""
    before = copy.deepcopy(chapter)
    error_type = _contract_error(module)
    with pytest.raises(error_type):
        _validate(module, chapter)
    assert chapter == before


def _empty_passage_claim_ids(chapter):
    chapter["script_passages"][0]["claim_ids"] = []


def _unknown_passage_claim_id(chapter):
    chapter["script_passages"][0]["claim_ids"] = ["claim-unknown"]


def _empty_claim_evidence(chapter):
    chapter["evidence_graph"]["claims"][0]["evidence_panel_ids"] = []


def _foreign_claim_evidence(chapter):
    chapter["evidence_graph"]["claims"][0]["evidence_panel_ids"] = [
        "panel-foreign"
    ]


@pytest.mark.parametrize(
    "mutate",
    (
        pytest.param(_empty_passage_claim_ids, id="empty-passage-claim-ids"),
        pytest.param(_unknown_passage_claim_id, id="unknown-passage-claim-id"),
        pytest.param(_empty_claim_evidence, id="empty-claim-evidence"),
        pytest.param(_foreign_claim_evidence, id="foreign-claim-evidence"),
    ),
)
def test_script_passages_require_linked_claim_and_panel_evidence(mutate):
    module = _contract_module()
    chapter = _chapter_two()
    mutate(chapter)
    before = copy.deepcopy(chapter)
    error_type = _contract_error(module)
    with pytest.raises(error_type):
        _validate(module, chapter)
    assert chapter == before


def test_coverage_and_overlapping_continuity_must_reconcile_before_script():
    module = _contract_module()
    chapter = _chapter_one()
    _validate(module, chapter)

    chunks = chapter["continuity_ledger"]["chunks"]
    assert set(chunks[0]["panel_ids"]).intersection(chunks[1]["panel_ids"])
    assert chapter["continuity_ledger"]["reconciled_after_final_chunk"] is True
    assert chapter["coverage_manifest"]["source_content_coverage_ratio"] == 1.0
    assert chapter["coverage_manifest"]["unresolved_material_area"] == 0

    blocked = copy.deepcopy(chapter)
    blocked["coverage_manifest"]["reconciliation_complete"] = False
    blocked["coverage_manifest"]["unresolved_material_area"] = 12
    blocked["continuity_ledger"]["reconciled_after_final_chunk"] = False
    error_type = _contract_error(module)
    with pytest.raises(error_type):
        _validate(module, blocked)


def test_every_claim_is_qualified_and_links_to_panel_evidence():
    module = _contract_module()
    chapter = _chapter_two()
    _validate(module, chapter)

    panel_ids = {
        item["panel_id"] for item in chapter["observations"]
    }
    for claim in chapter["evidence_graph"]["claims"]:
        assert claim["claim_id"]
        assert claim["qualification"]
        assert set(claim["evidence_panel_ids"]) <= panel_ids
        assert claim["evidence_panel_ids"]

    ungrounded = copy.deepcopy(chapter)
    ungrounded["evidence_graph"]["claims"][1]["qualification"] = ""
    ungrounded["evidence_graph"]["claims"][1]["evidence_panel_ids"] = []
    error_type = _contract_error(module)
    with pytest.raises(error_type):
        _validate(module, ungrounded)


def test_distinct_chapters_keep_source_entities_and_avoid_template_opening_or_cta():
    module = _contract_module()
    chapters = (_chapter_one(), _chapter_two())
    for chapter in chapters:
        _validate(module, chapter)
        entity_names = {
            entity["canonical_name"]
            for entity in chapter["continuity_ledger"]["entities"]
        }
        causal_reasons = {
            link["reason"]
            for link in chapter["continuity_ledger"]["causal_links"]
        }
        passage = chapter["script_passages"][0]["text"]
        assert entity_names
        assert causal_reasons
        assert not passage.startswith(("Then we see", "Here we see", "This story"))
        assert not any(marker in passage.lower() for marker in GENERIC_CTA_MARKERS)

    opening_texts = {
        chapter["script_passages"][0]["text"] for chapter in chapters
    }
    assert len(opening_texts) == 2


def test_validator_rejects_template_cta_instead_of_rewriting_output():
    module = _contract_module()
    chapter = _chapter_two()
    blocked = copy.deepcopy(chapter)
    blocked["script_passages"][0]["text"] += " Subscribe for more."
    error_type = _contract_error(module)
    with pytest.raises(error_type):
        _validate(module, blocked)
    assert blocked["script_passages"][0]["text"].endswith("Subscribe for more.")
