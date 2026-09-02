"""Grounded story-understanding helpers for narration preparation."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

STORY_UNDERSTANDING_VERSION = "story-understanding-v2"
STORY_SEMANTIC_AUDIT_VERSION = "story-semantic-audit-v1"
SEMANTIC_AUDIT_MIN_SUPPORTED_BEATS = 2
SEMANTIC_AUDIT_MIN_SUPPORTED_RATIO = 0.50
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "story_understanding_v2.txt"
AUDIT_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "story_semantic_audit_v1.txt"

_ALLOWED_CONFIDENCE = {"explicit", "qualified"}
_ALLOWED_EVIDENCE_STRENGTH = {
    "ocr_explicit",
    "visual_explicit",
    "supported_interpretation",
}
_NOISE_MARKERS = (
    "asurascans.com",
    "asura scans",
    "redice studio",
    "triple line studio",
    "copyright",
    "unauthorized reproduction",
    "read at ",
    "scanlation",
)
_STORY_TEXT_MARKERS = (
    "because",
    "therefore",
    "so that",
    "will ",
    "cannot",
    "can't",
    "must ",
    "decided",
    "truth",
    "past",
    "future",
    "years",
    "century",
    "war",
    "destroy",
    "extinction",
    "remember",
    "remain",
    "return",
    "lost",
    "coordinate",
    "spacetime",
    "dimension",
    "world",
)


class StoryUnderstandingError(ValueError):
    """Fail-closed error for malformed or ungrounded story understanding."""



def _load_prompt(path: Path, version: str) -> tuple[str, str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise StoryUnderstandingError("story understanding prompt is invalid") from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if f"Version: {version}" not in normalized:
        raise StoryUnderstandingError("story understanding prompt is invalid")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return version, digest, normalized


def load_instruction() -> tuple[str, str, str]:
    return _load_prompt(PROMPT_PATH, STORY_UNDERSTANDING_VERSION)


def load_semantic_audit_instruction() -> tuple[str, str, str]:
    return _load_prompt(AUDIT_PROMPT_PATH, STORY_SEMANTIC_AUDIT_VERSION)


def _words(value: object) -> list[str]:
    return re.findall(r"[^\W_]+(?:['’][^\W_]+)?", str(value or ""), flags=re.UNICODE)


def dialogue_text(value: object) -> str:
    """Normalize OCR/dialogue rows without leaking provider-specific shapes."""
    if isinstance(value, Mapping):
        value = value.get("text", "")
    return " ".join(str(value or "").split()).strip()


def story_text_is_meaningful(value: object) -> bool:
    text = dialogue_text(value)
    if not text or any(marker in text.casefold() for marker in _NOISE_MARKERS):
        return False
    words = _words(text)
    if len(words) < 3:
        return False
    alpha = sum(character.isalpha() for character in text)
    if alpha < max(4, len(text) // 5):
        return False
    unique = {word.casefold() for word in words}
    if len(unique) < 2:
        return False
    letters = [character for character in text if character.isalpha()]
    return not (letters and len(words) <= 3 and text.isupper() and text.count("!") >= 2)


def story_text_score(value: object) -> int:
    text = dialogue_text(value)
    if not story_text_is_meaningful(text):
        return -1000
    lowered = text.casefold()
    score = min(12, len(_words(text)))
    score += 5 * sum(marker in lowered for marker in _STORY_TEXT_MARKERS)
    if any(char.isdigit() for char in text):
        score += 4
    if "?" in text:
        score += 1
    if len(text) > 260:
        score -= 4
    return score


def rank_story_text(values: object, *, limit: int = 4) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    rows: list[tuple[int, int, str]] = []
    for index, value in enumerate(values):
        text = dialogue_text(value)
        score = story_text_score(text)
        if score > -1000:
            rows.append((-score, index, text))
    rows.sort()
    return [text for _, _, text in rows[: max(0, int(limit))]]


def panel_story_signal(observation: Mapping[str, Any] | None) -> int:
    if not isinstance(observation, Mapping):
        return 0
    dialogue = observation.get("dialogue_or_ocr", ())
    score = sum(max(0, story_text_score(value)) for value in (
        dialogue if isinstance(dialogue, Sequence) and not isinstance(dialogue, (str, bytes)) else ()
    ))
    for key, weight in (("state_changes", 5), ("causal_links", 5), ("inferences", 1)):
        values = observation.get(key, ())
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            score += weight * sum(bool(str(value).strip()) for value in values)
    return score


def _string_list(value: object, field: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise StoryUnderstandingError(f"{field} is invalid")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise StoryUnderstandingError(f"{field} is invalid")
    return [item.strip() for item in value]


def _claim_map(story_map: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    claims = story_map.get("claims", ())
    if not isinstance(claims, (list, tuple)):
        return {}
    return {
        str(claim["claim_id"]): claim
        for claim in claims
        if isinstance(claim, Mapping) and str(claim.get("claim_id", "")).strip()
    }


def _canonical_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def validate_result(
    value: object,
    *,
    expected_panel_ids: Sequence[str],
    story_map: Mapping[str, Any],
) -> dict[str, Any]:
    raw = value.get("story_understanding", value) if isinstance(value, Mapping) else value
    if not isinstance(raw, Mapping) or "understanding_hash" in raw:
        raise StoryUnderstandingError("story understanding response is invalid")
    beats = raw.get("narration_ready_beats")
    threads = raw.get("unresolved_threads")
    entities = raw.get("entity_registry", [])
    if (
        not isinstance(beats, list)
        or not 2 <= len(beats) <= 12
        or not isinstance(threads, list)
        or not isinstance(entities, list)
    ):
        raise StoryUnderstandingError("story understanding response is incomplete")

    expected = {str(panel_id) for panel_id in expected_panel_ids}
    claims = _claim_map(story_map)
    normalized_entities: list[dict[str, Any]] = []
    entity_ids: set[str] = set()
    for item in entities:
        if not isinstance(item, Mapping):
            raise StoryUnderstandingError("story entity is invalid")
        entity_id = str(item.get("entity_id", "")).strip()
        canonical_name = " ".join(str(item.get("canonical_name", "")).split()).strip()
        aliases = _string_list(item.get("aliases", []), "entity aliases")
        panel_ids = _string_list(
            item.get("evidence_panel_ids", []), "entity evidence_panel_ids", allow_empty=False
        )
        confidence = str(item.get("confidence", "")).strip().casefold()
        if (
            not entity_id
            or entity_id in entity_ids
            or not canonical_name
            or confidence not in _ALLOWED_CONFIDENCE
            or any(panel_id not in expected for panel_id in panel_ids)
        ):
            raise StoryUnderstandingError("story entity is invalid")
        entity_ids.add(entity_id)
        normalized_entities.append(
            {
                "entity_id": entity_id,
                "canonical_name": canonical_name,
                "aliases": aliases,
                "evidence_panel_ids": panel_ids,
                "confidence": confidence,
            }
        )

    seen: set[str] = set()
    normalized_beats: list[dict[str, Any]] = []
    for item in beats:
        if not isinstance(item, Mapping):
            raise StoryUnderstandingError("story understanding beat is invalid")
        beat_id = str(item.get("beat_id", "")).strip()
        role = str(item.get("story_role", "")).strip()
        fact = " ".join(str(item.get("fact", "")).split()).strip()
        narrative_function = " ".join(str(item.get("narrative_function", "")).split()).strip()
        change = " ".join(str(item.get("change", "")).split()).strip()
        consequence = " ".join(str(item.get("consequence", "")).split()).strip()
        open_question = " ".join(str(item.get("open_question", "")).split()).strip()
        confidence = str(item.get("confidence", "")).strip().casefold()
        evidence_strength = str(item.get("evidence_strength", "")).strip().casefold()
        qualification = " ".join(str(item.get("qualification", "")).split()).strip()
        importance = item.get("importance")
        if (
            not beat_id
            or beat_id in seen
            or not role
            or not fact
            or not narrative_function
            or confidence not in _ALLOWED_CONFIDENCE
            or evidence_strength not in _ALLOWED_EVIDENCE_STRENGTH
            or isinstance(importance, bool)
            or not isinstance(importance, int)
            or not 1 <= importance <= 5
        ):
            raise StoryUnderstandingError("story understanding beat is invalid")
        if confidence == "qualified" and not qualification:
            raise StoryUnderstandingError("qualified story understanding requires qualification")

        panel_ids = _string_list(
            item.get("evidence_panel_ids"), "evidence_panel_ids", allow_empty=False
        )
        if len(panel_ids) > 4 or any(panel_id not in expected for panel_id in panel_ids):
            raise StoryUnderstandingError("story understanding references invalid panels")
        claim_ids = _string_list(item.get("source_claim_ids", []), "source_claim_ids")
        if any(claim_id not in claims for claim_id in claim_ids):
            raise StoryUnderstandingError("story understanding references a foreign claim")
        for claim_id in claim_ids:
            refs = claims[claim_id].get(
                "evidence_panel_ids", claims[claim_id].get("panel_ids", ())
            )
            if not isinstance(refs, (list, tuple)) or not set(map(str, refs)).intersection(panel_ids):
                raise StoryUnderstandingError("story understanding claim lineage is invalid")
        beat_entities = _string_list(item.get("entity_ids", []), "beat entity_ids")
        if any(entity_id not in entity_ids for entity_id in beat_entities):
            raise StoryUnderstandingError("story understanding references a foreign entity")

        seen.add(beat_id)
        normalized_beats.append(
            {
                "beat_id": beat_id,
                "story_role": role,
                "fact": fact,
                "narrative_function": narrative_function,
                "change": change,
                "consequence": consequence,
                "open_question": open_question,
                "importance": importance,
                "evidence_strength": evidence_strength,
                "evidence_panel_ids": panel_ids,
                "source_claim_ids": claim_ids,
                "entity_ids": beat_entities,
                "confidence": confidence,
                "qualification": qualification,
            }
        )

    normalized_threads: list[dict[str, Any]] = []
    for item in threads:
        if not isinstance(item, Mapping):
            raise StoryUnderstandingError("unresolved thread is invalid")
        text = " ".join(str(item.get("text", "")).split()).strip()
        panel_ids = _string_list(
            item.get("evidence_panel_ids"), "thread evidence_panel_ids", allow_empty=False
        )
        thread_entities = _string_list(item.get("entity_ids", []), "thread entity_ids")
        if (
            not text
            or any(panel_id not in expected for panel_id in panel_ids)
            or any(entity_id not in entity_ids for entity_id in thread_entities)
        ):
            raise StoryUnderstandingError("unresolved thread is invalid")
        normalized_threads.append(
            {"text": text, "evidence_panel_ids": panel_ids, "entity_ids": thread_entities}
        )

    canonical: dict[str, Any] = {
        "version": STORY_UNDERSTANDING_VERSION,
        "entity_registry": normalized_entities,
        "narration_ready_beats": normalized_beats,
        "unresolved_threads": normalized_threads,
    }
    canonical["understanding_hash"] = _canonical_hash(canonical)
    return canonical


def apply_semantic_audit(
    value: Mapping[str, Any],
    audit: Mapping[str, Any],
) -> dict[str, Any]:
    """Drop isolated unsupported beats without rewriting grounded story work."""

    beats = [dict(beat) for beat in value.get("narration_ready_beats", ()) if isinstance(beat, Mapping)]
    verdicts = audit.get("verdicts", ())
    verdict_by_id = {
        str(item.get("beat_id", "")): bool(item.get("supported"))
        for item in verdicts
        if isinstance(item, Mapping)
    }
    beat_ids = [str(beat.get("beat_id", "")) for beat in beats]
    if not beats or set(verdict_by_id) != set(beat_ids):
        raise StoryUnderstandingError("semantic audit coverage is incomplete")

    supported = [beat for beat in beats if verdict_by_id[str(beat.get("beat_id", ""))]]
    dropped_ids = [beat_id for beat_id in beat_ids if not verdict_by_id[beat_id]]
    supported_ratio = len(supported) / len(beats)
    if (
        len(supported) < SEMANTIC_AUDIT_MIN_SUPPORTED_BEATS
        or supported_ratio < SEMANTIC_AUDIT_MIN_SUPPORTED_RATIO
    ):
        raise StoryUnderstandingError("semantic audit leaves insufficient grounded story")

    used_entities = {
        str(entity_id)
        for beat in supported
        for entity_id in beat.get("entity_ids", ())
    }
    entities = [
        dict(entity)
        for entity in value.get("entity_registry", ())
        if isinstance(entity, Mapping) and str(entity.get("entity_id", "")) in used_entities
    ]
    canonical: dict[str, Any] = {
        "version": STORY_UNDERSTANDING_VERSION,
        "entity_registry": entities,
        "narration_ready_beats": supported,
        "unresolved_threads": [
            dict(thread)
            for thread in value.get("unresolved_threads", ())
            if isinstance(thread, Mapping)
            and set(map(str, thread.get("entity_ids", ()))) <= used_entities
        ],
    }
    canonical["understanding_hash"] = _canonical_hash(canonical)
    canonical["semantic_audit_version"] = str(audit.get("version", ""))
    canonical["semantic_audit_hash"] = str(audit.get("audit_hash", ""))
    canonical["semantic_audit_dropped_beat_ids"] = dropped_ids
    canonical["semantic_audit_original_beat_count"] = len(beats)
    canonical["semantic_audit_supported_beat_count"] = len(supported)
    return canonical


def materialize_grounded_claims(value: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Expose narration-ready beats through the existing claim-lineage contract."""

    beats = value.get("narration_ready_beats", ())
    if not isinstance(beats, (list, tuple)):
        raise StoryUnderstandingError("story understanding beats are invalid")
    claims: list[dict[str, Any]] = []
    for index, beat in enumerate(beats):
        if not isinstance(beat, Mapping):
            raise StoryUnderstandingError("story understanding beat is invalid")
        beat_id = str(beat.get("beat_id", "")).strip()
        fact = " ".join(str(beat.get("fact", "")).split()).strip()
        panel_ids = beat.get("evidence_panel_ids", ())
        confidence = str(beat.get("confidence", "")).strip().casefold()
        qualification = " ".join(str(beat.get("qualification", "")).split()).strip()
        if not beat_id or not fact or not isinstance(panel_ids, (list, tuple)) or not panel_ids:
            raise StoryUnderstandingError("story understanding beat is invalid")
        digest = hashlib.sha256(f"{index}:{beat_id}:{fact}".encode()).hexdigest()[:16]
        claims.append(
            {
                "claim_id": f"story_understanding__{digest}",
                "claim_type": "fact" if confidence == "explicit" else "interpretation",
                "text": fact,
                "qualification": (
                    "Directly grounded by the story-understanding evidence."
                    if confidence == "explicit"
                    else qualification
                ),
                "evidence_panel_ids": [str(panel_id) for panel_id in panel_ids],
                "claim_origin": "story_understanding",
                "narrative_priority": "primary",
                "story_role": str(beat.get("story_role", "")),
                "narrative_function": str(beat.get("narrative_function", "")),
                "change": str(beat.get("change", "")),
                "consequence": str(beat.get("consequence", "")),
                "open_question": str(beat.get("open_question", "")),
                "importance": int(beat.get("importance", 1)),
                "evidence_strength": str(beat.get("evidence_strength", "")),
                "entity_ids": [str(value) for value in beat.get("entity_ids", ())],
            }
        )
    return tuple(claims)


def support_only_claims(claims: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    result: list[dict[str, Any]] = []
    for claim in claims:
        row = dict(claim)
        row["claim_origin"] = str(row.get("claim_origin", "visual_story_map"))
        row["narrative_priority"] = "support_only"
        result.append(row)
    return tuple(result)


def primary_story_panel_weights(value: Mapping[str, Any] | None) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    weights: dict[str, int] = {}
    strength_bonus = {
        "ocr_explicit": 40,
        "visual_explicit": 20,
        "supported_interpretation": 10,
    }
    for beat in value.get("narration_ready_beats", ()):
        if not isinstance(beat, Mapping):
            continue
        importance = int(beat.get("importance", 1) or 1)
        bonus = strength_bonus.get(str(beat.get("evidence_strength", "")), 0)
        for panel_id in beat.get("evidence_panel_ids", ()):
            key = str(panel_id)
            weights[key] = max(weights.get(key, 0), importance * 100 + bonus)
    return weights


def project_to_panels(value: Mapping[str, Any], panel_ids: Sequence[str]) -> dict[str, Any]:
    """Keep only fully supported beats after editorial selection."""

    allowed = {str(panel_id) for panel_id in panel_ids}
    beats = [
        dict(beat)
        for beat in value.get("narration_ready_beats", ())
        if isinstance(beat, Mapping)
        and set(map(str, beat.get("evidence_panel_ids", ()))) <= allowed
    ]
    if len(beats) < 2:
        raise StoryUnderstandingError("editorial selection dropped story understanding")
    used_entities = {
        str(entity_id)
        for beat in beats
        for entity_id in beat.get("entity_ids", ())
    }
    entities = [
        dict(entity)
        for entity in value.get("entity_registry", ())
        if isinstance(entity, Mapping) and str(entity.get("entity_id", "")) in used_entities
    ]
    threads = [
        dict(thread)
        for thread in value.get("unresolved_threads", ())
        if isinstance(thread, Mapping)
        and set(map(str, thread.get("evidence_panel_ids", ()))) <= allowed
    ]
    canonical: dict[str, Any] = {
        "version": STORY_UNDERSTANDING_VERSION,
        "entity_registry": entities,
        "narration_ready_beats": beats,
        "unresolved_threads": threads,
        "source_understanding_hash": str(value.get("understanding_hash", "")),
        "semantic_audit_version": str(value.get("semantic_audit_version", "")),
        "source_semantic_audit_hash": str(value.get("semantic_audit_hash", "")),
    }
    canonical["understanding_hash"] = _canonical_hash(canonical)
    return canonical


def _compact_story_map(story_map: Mapping[str, Any]) -> dict[str, Any]:
    claims = []
    for claim in story_map.get("claims", ()):
        if not isinstance(claim, Mapping):
            continue
        claims.append(
            {
                "claim_id": str(claim.get("claim_id", "")),
                "text": str(claim.get("text", "")),
                "qualification": str(claim.get("qualification", "")),
                "evidence_panel_ids": [
                    str(value)
                    for value in claim.get("evidence_panel_ids", claim.get("panel_ids", ()))
                ],
            }
        )
    beats = []
    for beat in story_map.get("beats", ()):
        if isinstance(beat, Mapping):
            beats.append(
                {
                    "beat_id": str(beat.get("beat_id", "")),
                    "summary": str(beat.get("summary", "")),
                    "panel_ids": [str(value) for value in beat.get("panel_ids", ())],
                }
            )
    return {
        "panel_ids": [str(value) for value in story_map.get("panel_ids", ())],
        "beats": beats,
        "claims": claims,
        "causal_chain": [
            dict(link) for link in story_map.get("causal_chain", ()) if isinstance(link, Mapping)
        ],
    }


def _compact_continuity(continuity_ledger: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: [
            dict(value) if isinstance(value, Mapping) else value
            for value in continuity_ledger.get(key, ())
        ]
        for key in ("entities", "motives", "state_changes", "causal_links")
        if isinstance(continuity_ledger.get(key, ()), (list, tuple))
    }


def build_source_packet(
    observations: Sequence[Mapping[str, Any]],
    story_map: Mapping[str, Any],
    continuity_ledger: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a full-chapter but aggressively compressed story packet."""

    compact_observations: list[dict[str, Any]] = []
    for observation in observations:
        dialogue = rank_story_text(observation.get("dialogue_or_ocr", ()), limit=4)
        signal = panel_story_signal(observation)
        visible = [
            " ".join(str(value).split())
            for value in observation.get("visible_facts", ())
            if str(value).strip()
        ]
        inferences = [
            " ".join(str(value).split())
            for value in observation.get("inferences", ())
            if str(value).strip()
        ]
        compact_observations.append(
            {
                "panel_id": str(observation.get("panel_id", "")),
                "source_index": int(observation.get("source_index", 0)),
                "story_signal": int(signal),
                "dialogue_or_ocr": dialogue,
                "visible_facts": visible[:1],
                "inferences": inferences[:1] if signal > 0 else [],
            }
        )
    return {
        "version": STORY_UNDERSTANDING_VERSION,
        "scope": "full_chapter_preselection",
        "panel_ids": [row["panel_id"] for row in compact_observations],
        "observations": compact_observations,
        "story_map": _compact_story_map(story_map),
        "continuity_ledger": _compact_continuity(continuity_ledger),
        "priority": "meaning_state_change_and_story_function_before_visual_inventory",
    }


def build_semantic_audit_packet(
    understanding: Mapping[str, Any],
    observations: Sequence[Mapping[str, Any]],
    story_map: Mapping[str, Any],
    continuity_ledger: Mapping[str, Any],
) -> dict[str, Any]:
    panel_ids = {
        str(panel_id)
        for beat in understanding.get("narration_ready_beats", ())
        if isinstance(beat, Mapping)
        for panel_id in beat.get("evidence_panel_ids", ())
    }
    observation_rows = []
    for observation in observations:
        panel_id = str(observation.get("panel_id", ""))
        if panel_id not in panel_ids:
            continue
        observation_rows.append(
            {
                "panel_id": panel_id,
                "dialogue_or_ocr": rank_story_text(
                    observation.get("dialogue_or_ocr", ()), limit=6
                ),
                "visible_facts": [
                    " ".join(str(value).split())
                    for value in observation.get("visible_facts", ())
                    if str(value).strip()
                ][:3],
                "inferences": [
                    " ".join(str(value).split())
                    for value in observation.get("inferences", ())
                    if str(value).strip()
                ][:2],
                "uncertainties": [
                    " ".join(str(value).split())
                    for value in observation.get("uncertainties", ())
                    if str(value).strip()
                ][:2],
            }
        )
    return {
        "version": STORY_SEMANTIC_AUDIT_VERSION,
        "beats": [dict(beat) for beat in understanding.get("narration_ready_beats", ())],
        "entity_registry": [dict(value) for value in understanding.get("entity_registry", ())],
        "observations": observation_rows,
        "story_map": _compact_story_map(story_map),
        "continuity_ledger": _compact_continuity(continuity_ledger),
    }


def validate_semantic_audit(
    value: object,
    *,
    expected_beat_ids: Sequence[str],
) -> dict[str, Any]:
    raw = value.get("semantic_audit", value) if isinstance(value, Mapping) else value
    if not isinstance(raw, Mapping):
        raise StoryUnderstandingError("semantic audit response is invalid")
    verdicts = raw.get("verdicts")
    if not isinstance(verdicts, list):
        raise StoryUnderstandingError("semantic audit response is invalid")
    expected = tuple(str(value) for value in expected_beat_ids)
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in verdicts:
        if not isinstance(item, Mapping):
            raise StoryUnderstandingError("semantic audit verdict is invalid")
        beat_id = str(item.get("beat_id", "")).strip()
        supported = item.get("supported")
        reason = " ".join(str(item.get("reason", "")).split()).strip()
        if (
            beat_id not in expected
            or beat_id in seen
            or not isinstance(supported, bool)
            or not reason
        ):
            raise StoryUnderstandingError("semantic audit verdict is invalid")
        seen.add(beat_id)
        normalized.append({"beat_id": beat_id, "supported": supported, "reason": reason})
    if seen != set(expected):
        raise StoryUnderstandingError("semantic audit coverage is incomplete")
    canonical: dict[str, Any] = {
        "version": STORY_SEMANTIC_AUDIT_VERSION,
        "verdicts": normalized,
    }
    canonical["audit_hash"] = _canonical_hash(canonical)
    canonical["unsupported_beat_ids"] = [
        item["beat_id"] for item in normalized if item["supported"] is False
    ]
    return canonical
