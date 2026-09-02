from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

PROMPT_VERSION = "vision-first-story-analyzer-v2"
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / (
    "vision_first_story_analyzer_v2.txt"
)

_REQUIRED_OUTPUT_KEYS = frozenset(
    {
        "observations",
        "continuity_ledger",
        "evidence_graph",
        "coverage_manifest",
        "narrative_outline",
        "script_passages",
    }
)
_REQUIRED_OBSERVATION_KEYS = frozenset(
    {
        "panel_id",
        "source_asset_id",
        "strip_region_id",
        "source_index",
        "region_bounds",
        "coverage_map_version",
        "coverage_map_hash",
        "visible_facts",
        "dialogue_or_ocr",
        "inferences",
        "uncertainties",
        "evidence_refs",
    }
)
_STORY_SPINE_FIELDS = (
    "who_wants_what",
    "obstacle",
    "decision",
    "consequence",
    "changed_stakes",
    "unresolved_question",
)
_SCRIPT_PASSAGE_KEYS = frozenset(
    {
        "passage_id",
        "editorial_role",
        "text",
        "claim_ids",
        "evidence_panel_ids",
    }
)
_EDITORIAL_ROLES = (
    "hook",
    "setup",
    "escalation",
    "editorial_insight",
    "payoff_open_loop",
)
_ROLE_WORD_LIMITS = {
    "hook": (8, 18),
    "setup": (15, 28),
    "escalation": (22, 38),
    "editorial_insight": (15, 30),
    "payoff_open_loop": (10, 24),
}
_CTA_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bsubscribe\b",
        r"\bfollow[\s-]+for[\s-]+more\b",
        r"\bplease\s+like\b",
        r"\blike\s+this\s+video\b",
        r"\b(?:drop|hit)\s+(?:a\s+)?like\b",
        r"\bplease\s+comment\b",
        r"\bcomment\s+below\b",
        r"\bleave\s+(?:a\s+)?comment\b",
        r"\btell\s+us\s+in\s+comments\b",
    )
)


class AnalyzerContractError(ValueError):
    """Stable fail-closed error for analyzer input or output contracts."""

    code = "analyzer_contract_invalid"

    def __init__(self, message: str = "analyzer contract validation failed") -> None:
        super().__init__(message)


def _load_v2_instruction() -> tuple[str, str, str]:
    """Load the default v2 prompt without changing its legacy behavior."""

    try:
        text = PROMPT_PATH.read_text(encoding="utf-8")
        normalized = text.replace("\r\n", "\n")
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return PROMPT_VERSION, digest, normalized
    except (OSError, UnicodeError):
        raise AnalyzerContractError("analyzer instruction cannot be loaded") from None


def load_analyzer_instruction(
    *, narrative_profile_id: str | None = None
) -> tuple[str, str, str]:
    """Load v2 by default or an explicitly selected verified identity."""

    if narrative_profile_id is None:
        return _load_v2_instruction()
    try:
        from app.services import narrative_identity

        return narrative_identity.load_narrative_instruction(narrative_profile_id)
    except narrative_identity.NarrativeIdentityError:
        raise AnalyzerContractError("unknown narrative profile") from None


def _fail(message: str) -> None:
    raise AnalyzerContractError(message)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{label} must be an object")
    return value


def _require_fields(value: Mapping[str, Any], fields: Sequence[str], label: str) -> None:
    missing = [field for field in fields if field not in value]
    if missing:
        _fail(f"{label} is missing required fields")


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{label} must be a non-empty string")
    return value


def _string_list(value: Any, label: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list):
        _fail(f"{label} must be a list")
    if not allow_empty and not value:
        _fail(f"{label} must not be empty")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        _fail(f"{label} must contain non-empty strings")
    return value


def _panel_refs(value: Any, expected: tuple[str, ...], label: str) -> list[str]:
    refs = _string_list(value, label, allow_empty=False)
    if not set(refs) <= set(expected):
        _fail(f"{label} contains an unknown panel")
    return refs


def _expected_panel_ids(value: Any) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        _fail("expected_panel_ids must be an ordered sequence")
    expected = tuple(value)
    if not expected or any(not isinstance(panel_id, str) for panel_id in expected):
        _fail("expected_panel_ids must contain non-empty strings")
    if any(not panel_id.strip() for panel_id in expected):
        _fail("expected_panel_ids must contain non-empty strings")
    if len(set(expected)) != len(expected):
        _fail("expected_panel_ids must be unique")
    return expected


def _validate_observations(
    value: Any, expected: tuple[str, ...]
) -> None:
    observations = value
    if not isinstance(observations, list) or len(observations) != len(expected):
        _fail("observations must contain every expected panel exactly once")

    for source_index, observation_value in enumerate(observations):
        observation = _mapping(observation_value, "observation")
        if set(observation) != _REQUIRED_OBSERVATION_KEYS:
            _fail("observation keys do not match the contract")
        panel_id = _nonempty_string(observation.get("panel_id"), "panel_id")
        if panel_id != expected[source_index]:
            _fail("observations are missing, duplicated, foreign, or out of order")
        _nonempty_string(observation.get("source_asset_id"), "source_asset_id")
        _nonempty_string(observation.get("strip_region_id"), "strip_region_id")
        observed_index = observation.get("source_index")
        if (
            not isinstance(observed_index, int)
            or isinstance(observed_index, bool)
            or observed_index != source_index
        ):
            _fail("observation source_index is not deterministic")

        bounds = _mapping(observation.get("region_bounds"), "region_bounds")
        if set(bounds) != {"x", "y", "width", "height"}:
            _fail("region_bounds must contain exactly x, y, width, and height")
        for coordinate in ("x", "y", "width", "height"):
            number = bounds.get(coordinate)
            if not isinstance(number, int) or isinstance(number, bool) or number < 0:
                _fail("region_bounds coordinates must be non-negative integers")
        if bounds["width"] == 0 or bounds["height"] == 0:
            _fail("region_bounds must have positive dimensions")

        _nonempty_string(
            observation.get("coverage_map_version"), "coverage_map_version"
        )
        _nonempty_string(observation.get("coverage_map_hash"), "coverage_map_hash")
        _string_list(observation.get("visible_facts"), "visible_facts", allow_empty=False)
        _string_list(observation.get("dialogue_or_ocr"), "dialogue_or_ocr")
        _string_list(observation.get("inferences"), "inferences")
        _string_list(observation.get("uncertainties"), "uncertainties")
        refs = _panel_refs(observation.get("evidence_refs"), expected, "evidence_refs")
        if panel_id not in refs:
            _fail("observation evidence_refs must include its own panel")


def _validate_coverage_manifest(value: Any, expected: tuple[str, ...]) -> None:
    manifest = _mapping(value, "coverage_manifest")
    _require_fields(
        manifest,
        (
            "total_panels",
            "processed_panels",
            "panel_ids",
            "source_content_coverage_ratio",
            "unresolved_material_area",
            "material_unresolved_regions",
            "reconciliation_complete",
        ),
        "coverage_manifest",
    )
    if manifest["total_panels"] != len(expected):
        _fail("coverage manifest total_panels is incomplete")
    if manifest["processed_panels"] != len(expected):
        _fail("coverage manifest processed_panels is incomplete")
    panel_ids = manifest["panel_ids"]
    if not isinstance(panel_ids, list) or tuple(panel_ids) != expected:
        _fail("coverage manifest panel order is not reconciled")
    ratio = manifest["source_content_coverage_ratio"]
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or ratio != 1.0:
        _fail("source_content_coverage_ratio must equal 1.0")
    unresolved = manifest["unresolved_material_area"]
    if isinstance(unresolved, bool) or unresolved != 0:
        _fail("unresolved_material_area must equal zero")
    if manifest["material_unresolved_regions"] != []:
        _fail("material unresolved regions block reconciliation")
    if manifest["reconciliation_complete"] is not True:
        _fail("coverage reconciliation is incomplete")


def _validate_continuity(value: Any, expected: tuple[str, ...]) -> None:
    ledger = _mapping(value, "continuity_ledger")
    required = (
        "chunks",
        "entities",
        "motives",
        "state_changes",
        "causal_links",
        "reconciled_after_final_chunk",
    )
    _require_fields(ledger, required, "continuity_ledger")
    chunks = ledger["chunks"]
    if not isinstance(chunks, list) or not chunks:
        _fail("continuity requires at least one chunk")
    chunk_ids: set[str] = set()
    seen_panel_ids: set[str] = set()
    chunk_panel_ids: list[list[str]] = []
    for chunk_value in chunks:
        chunk = _mapping(chunk_value, "continuity chunk")
        _require_fields(chunk, ("chunk_id", "panel_ids"), "continuity chunk")
        chunk_id = _nonempty_string(chunk["chunk_id"], "chunk_id")
        if chunk_id in chunk_ids:
            _fail("continuity chunk IDs must be unique")
        chunk_ids.add(chunk_id)
        panel_ids = _panel_refs(chunk["panel_ids"], expected, "chunk panel_ids")
        if len(set(panel_ids)) != len(panel_ids):
            _fail("a continuity chunk cannot duplicate panels")
        chunk_panel_ids.append(panel_ids)
        seen_panel_ids.update(panel_ids)
    if seen_panel_ids != set(expected):
        _fail("continuity chunks do not cover every panel")
    for previous, current in zip(
        chunk_panel_ids, chunk_panel_ids[1:], strict=False
    ):
        if not set(previous).intersection(current):
            _fail("sequential chunks must overlap")
    if ledger["reconciled_after_final_chunk"] is not True:
        _fail("continuity must be reconciled after the final chunk")

    entities = ledger["entities"]
    if not isinstance(entities, list) or not entities:
        _fail("continuity entities are required")
    entity_ids: set[str] = set()
    for entity_value in entities:
        entity = _mapping(entity_value, "continuity entity")
        _require_fields(entity, ("entity_id", "canonical_name", "aliases", "panel_ids"), "continuity entity")
        entity_id = _nonempty_string(entity["entity_id"], "entity_id")
        if entity_id in entity_ids:
            _fail("continuity entity IDs must be unique")
        entity_ids.add(entity_id)
        _nonempty_string(entity["canonical_name"], "canonical_name")
        _string_list(entity["aliases"], "entity aliases")
        _panel_refs(entity["panel_ids"], expected, "entity panel_ids")

    motives = ledger["motives"]
    if not isinstance(motives, list):
        _fail("continuity motives must be a list")
    for motive_value in motives:
        motive = _mapping(motive_value, "motive")
        _require_fields(motive, ("entity_id", "text", "evidence_panel_ids"), "motive")
        if motive["entity_id"] not in entity_ids:
            _fail("motive references an unknown entity")
        _nonempty_string(motive["text"], "motive text")
        _panel_refs(motive["evidence_panel_ids"], expected, "motive evidence")

    state_changes = ledger["state_changes"]
    if not isinstance(state_changes, list):
        _fail("continuity state_changes must be a list")
    for change_value in state_changes:
        change = _mapping(change_value, "state change")
        _require_fields(
            change,
            ("entity_id", "from", "to", "evidence_panel_ids"),
            "state change",
        )
        if change["entity_id"] not in entity_ids:
            _fail("state change references an unknown entity")
        _nonempty_string(change["from"], "state change from")
        _nonempty_string(change["to"], "state change to")
        _panel_refs(change["evidence_panel_ids"], expected, "state change evidence")

    causal_links = ledger["causal_links"]
    if not isinstance(causal_links, list):
        _fail("continuity causal_links must be a list")
    for link_value in causal_links:
        link = _mapping(link_value, "causal link")
        _require_fields(
            link,
            ("from_panel_id", "to_panel_id", "reason", "evidence_panel_ids"),
            "causal link",
        )
        if link["from_panel_id"] not in expected or link["to_panel_id"] not in expected:
            _fail("causal link references an unknown panel")
        _nonempty_string(link["reason"], "causal link reason")
        _panel_refs(link["evidence_panel_ids"], expected, "causal link evidence")


def _validate_claims(value: Any, expected: tuple[str, ...]) -> dict[str, set[str]]:
    graph = _mapping(value, "evidence_graph")
    claims = graph.get("claims")
    if not isinstance(claims, list) or not claims:
        _fail("evidence_graph claims are required")
    claim_evidence: dict[str, set[str]] = {}
    for claim_value in claims:
        claim = _mapping(claim_value, "claim")
        _require_fields(
            claim,
            ("claim_id", "claim_type", "text", "qualification", "evidence_panel_ids"),
            "claim",
        )
        claim_id = _nonempty_string(claim["claim_id"], "claim_id")
        if claim_id in claim_evidence:
            _fail("claim IDs must be unique")
        if claim["claim_type"] not in {"fact", "interpretation"}:
            _fail("claim_type must identify a fact or interpretation")
        _nonempty_string(claim["text"], "claim text")
        _nonempty_string(claim["qualification"], "claim qualification")
        claim_evidence[claim_id] = set(
            _panel_refs(claim["evidence_panel_ids"], expected, "claim evidence")
        )
    return claim_evidence


def _validate_narrative_outline(value: Any) -> None:
    outline = _mapping(value, "narrative_outline")
    _require_fields(outline, ("story_spine",), "narrative_outline")
    spine = _mapping(outline["story_spine"], "story_spine")
    if set(spine) != set(_STORY_SPINE_FIELDS):
        _fail("story_spine must contain all six reasoning fields")
    for field in _STORY_SPINE_FIELDS:
        _nonempty_string(spine.get(field), f"story_spine.{field}")


_V3_GENERIC_HYPE = (
    "epic battle",
    "unstoppable attack",
    "insane power",
)


def _validate_narrative_outline_v3(value: Any, profile: Any) -> Mapping[str, Any]:
    outline = _mapping(value, "narrative_outline")
    if set(outline) != {"story_spine", "ending_kind"}:
        _fail("v3 narrative_outline keys do not match the contract")
    spine = _mapping(outline["story_spine"], "story_spine")
    if set(spine) != set(_STORY_SPINE_FIELDS):
        _fail("story_spine must contain all six reasoning fields")
    for field in _STORY_SPINE_FIELDS:
        _nonempty_string(spine.get(field), f"story_spine.{field}")
    ending_kind = outline["ending_kind"]
    if ending_kind not in profile.allowed_ending_kinds:
        _fail("ending_kind is not supported by the narrative profile")
    return outline


def _normalized_lexical_words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.casefold(), flags=re.UNICODE)


def _normalized_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for raw_sentence in re.split(r"[.!?]+", text):
        words = _normalized_lexical_words(raw_sentence)
        if words:
            sentences.append(" ".join(words))
    return sentences


def _ngrams(words: list[str], size: int) -> set[tuple[str, ...]]:
    return {
        tuple(words[index : index + size])
        for index in range(max(0, len(words) - size + 1))
    }


def _source_dialogue_ngrams(observations: Any) -> set[tuple[str, ...]]:
    """Build adaptive verbatim signatures without punishing short shared terminology."""

    result: set[tuple[str, ...]] = set()
    for observation_value in observations:
        observation = _mapping(observation_value, "observation")
        for line in _string_list(observation["dialogue_or_ocr"], "dialogue_or_ocr"):
            words = _normalized_lexical_words(line)
            if len(words) < 4:
                continue
            size = len(words) if len(words) < 6 else 6
            result.update(_ngrams(words, size))
    return result


def contains_source_dialogue_copy(observations: Any, passages: Any) -> bool:
    """Detect substantial verbatim source dialogue while allowing faithful paraphrase."""

    try:
        signatures = _source_dialogue_ngrams(observations)
        if not signatures or not isinstance(passages, (list, tuple)):
            return False
        by_size: dict[int, set[tuple[str, ...]]] = {}
        for signature in signatures:
            by_size.setdefault(len(signature), set()).add(signature)
        for passage in passages:
            if not isinstance(passage, Mapping):
                continue
            text = passage.get("text")
            if not isinstance(text, str):
                continue
            words = _normalized_lexical_words(text)
            for size, source_ngrams in by_size.items():
                if len(words) >= size and _ngrams(words, size) & source_ngrams:
                    return True
    except (KeyError, TypeError, ValueError):
        return False
    return False


def _contains_channel_cta(text: str) -> bool:
    lowered = text.casefold()
    return any(pattern.search(lowered) for pattern in _CTA_PATTERNS)


def _validate_script_passages(
    value: Any, expected: tuple[str, ...], claim_evidence: dict[str, set[str]]
) -> None:
    if not isinstance(value, list) or len(value) != len(_EDITORIAL_ROLES):
        _fail("script_passages must contain exactly five passages")

    passage_ids: set[str] = set()
    opening_words: set[tuple[str, ...]] = set()
    repeated_sentences: set[str] = set()
    total_words = 0
    for expected_role, passage_value in zip(_EDITORIAL_ROLES, value, strict=True):
        passage = _mapping(passage_value, "script passage")
        if set(passage) != _SCRIPT_PASSAGE_KEYS:
            _fail("script passage keys do not match the contract")
        passage_id = _nonempty_string(passage["passage_id"], "passage_id")
        if passage_id in passage_ids:
            _fail("passage IDs must be unique")
        passage_ids.add(passage_id)

        role = _nonempty_string(passage["editorial_role"], "editorial_role")
        if role != expected_role:
            _fail("script passage roles are missing, duplicated, or out of order")
        text = _nonempty_string(passage["text"], "script passage text")
        total_words += len(text.split())
        minimum_words, maximum_words = _ROLE_WORD_LIMITS[expected_role]
        word_count = len(text.split())
        if not minimum_words <= word_count <= maximum_words:
            _fail("script passage word count is outside its role guardrail")
        if _contains_channel_cta(text):
            _fail("generic channel CTA language is not allowed")

        first_three = tuple(_normalized_lexical_words(text)[:3])
        if first_three in opening_words:
            _fail("script passage openings must be varied")
        opening_words.add(first_three)
        passage_sentences = set(_normalized_sentences(text))
        if passage_sentences & repeated_sentences:
            _fail("script passages must not repeat a sentence")
        repeated_sentences.update(passage_sentences)

        claim_ids = _string_list(
            passage["claim_ids"], "passage claim_ids", allow_empty=False
        )
        if not set(claim_ids) <= set(claim_evidence):
            _fail("script passage references an unknown claim")
        evidence_panel_ids = set(
            _panel_refs(passage["evidence_panel_ids"], expected, "passage evidence")
        )
        required_evidence = set().union(
            *(claim_evidence[claim_id] for claim_id in claim_ids)
        )
        if not required_evidence <= evidence_panel_ids:
            _fail("script passage evidence does not cover its claims")

    if not 90 <= total_words <= 125:
        _fail("script passage narration must contain 90-125 words")
    payoff_text = value[-1]["text"]
    if not payoff_text.rstrip().endswith("?"):
        _fail("payoff_open_loop must end with an evidence-grounded question")


def _validate_v3_ending(
    outline: Mapping[str, Any],
    final_text: str,
    profile: Any,
) -> None:
    ending_kind = outline["ending_kind"]
    unresolved = _nonempty_string(
        outline["story_spine"]["unresolved_question"],
        "story_spine.unresolved_question",
    )
    if ending_kind == "open_question":
        if not final_text.endswith("?") or not unresolved:
            _fail("open_question ending must be evidence-grounded and end with ?")
    elif ending_kind in {"cliffhanger", "consequence"} and final_text.endswith("?"):
        _fail("non-question ending kind must not end with ?")


def _validate_script_passages_v3(
    value: Any,
    expected: tuple[str, ...],
    claim_evidence: dict[str, set[str]],
    observations: Any,
    outline: Mapping[str, Any],
    profile: Any,
    *,
    allow_dialogue_copy: bool = False,
) -> None:
    if not isinstance(value, list) or not profile.passage_min <= len(value) <= profile.passage_max:
        _fail("script_passages must contain four to six passages")
    passage_ids: set[str] = set()
    covered_claim_evidence: dict[str, set[str]] = {
        claim_id: set() for claim_id in claim_evidence
    }
    for passage_value in value:
        passage = _mapping(passage_value, "script passage")
        if set(passage) != _SCRIPT_PASSAGE_KEYS:
            _fail("script passage keys do not match the v3 contract")
        passage_id = _nonempty_string(passage["passage_id"], "passage_id")
        if passage_id in passage_ids:
            _fail("passage IDs must be unique")
        passage_ids.add(passage_id)
        _nonempty_string(passage["editorial_role"], "editorial_role")
        text = _nonempty_string(passage["text"], "script passage text")
        if _contains_channel_cta(text):
            _fail("generic channel CTA language is not allowed")
        normalized_text = " ".join(_normalized_lexical_words(text))
        if any(marker in normalized_text for marker in _V3_GENERIC_HYPE):
            _fail("generic hype language is not allowed")
        if not allow_dialogue_copy and contains_source_dialogue_copy(
            observations, (passage,)
        ):
            _fail("script passage copies source dialogue")
        claim_ids = _string_list(
            passage["claim_ids"], "passage claim_ids", allow_empty=False
        )
        if not set(claim_ids) <= set(claim_evidence):
            _fail("script passage references an unknown claim")
        evidence = set(
            _panel_refs(passage["evidence_panel_ids"], expected, "passage evidence")
        )
        for claim_id in claim_ids:
            covered_claim_evidence[claim_id].update(evidence & claim_evidence[claim_id])
    if any(
        covered_claim_evidence[claim_id] != required
        for claim_id, required in claim_evidence.items()
    ):
        _fail("script passage evidence does not cover its claims")
    final_text = _nonempty_string(value[-1]["text"], "final script passage text").rstrip()
    _validate_v3_ending(outline, final_text, profile)


def _validate_output(
    output: Any,
    expected: tuple[str, ...],
    *,
    narrative_profile_id: str | None = None,
    allow_dialogue_copy: bool = False,
) -> None:
    profile = None
    if narrative_profile_id is not None:
        if narrative_profile_id != "sharp_friend_v1":
            _fail("unknown narrative profile")
        try:
            from app.services import narrative_identity

            profile = narrative_identity.get_narrative_identity(narrative_profile_id)
        except narrative_identity.NarrativeIdentityError:
            _fail("unknown narrative profile")
    document = _mapping(output, "analyzer output")
    if set(document) != _REQUIRED_OUTPUT_KEYS:
        _fail("analyzer output structures do not match the contract")
    _validate_observations(document["observations"], expected)
    _validate_coverage_manifest(document["coverage_manifest"], expected)
    _validate_continuity(document["continuity_ledger"], expected)
    claim_evidence = _validate_claims(document["evidence_graph"], expected)
    if profile is None:
        _validate_narrative_outline(document["narrative_outline"])
        _validate_script_passages(document["script_passages"], expected, claim_evidence)
    else:
        outline = _validate_narrative_outline_v3(document["narrative_outline"], profile)
        _validate_script_passages_v3(
            document["script_passages"],
            expected,
            claim_evidence,
            document["observations"],
            outline,
            profile,
            allow_dialogue_copy=allow_dialogue_copy,
        )


def validate_analyzer_output(
    output: Mapping[str, Any],
    *,
    expected_panel_ids: Sequence[str],
    narrative_profile_id: str | None = None,
    allow_dialogue_copy: bool = False,
) -> None:
    """Validate complete analyzer output without mutating or repairing it.

    Preview relaxation: ``allow_dialogue_copy`` lets narration passages that
    overlap common dialogue n-grams (names/locations in a text-heavy strip)
    pass the anti-copy gate; production keeps the strict contract.
    """

    try:
        expected = _expected_panel_ids(expected_panel_ids)
        _validate_output(
            output,
            expected,
            narrative_profile_id=narrative_profile_id,
            allow_dialogue_copy=allow_dialogue_copy,
        )
    except AnalyzerContractError:
        raise
    except Exception:
        raise AnalyzerContractError("malformed analyzer output") from None
