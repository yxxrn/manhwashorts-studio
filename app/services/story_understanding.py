"""Grounded story-understanding helpers for narration preparation."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

STORY_UNDERSTANDING_VERSION = "story-understanding-v1"
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "story_understanding_v1.txt"
_ALLOWED_CONFIDENCE = {"explicit", "qualified"}
_NOISE_MARKERS = (
    "asurascans.com",
    "asura scans",
    "redice studio",
    "triple line studio",
    "copyright",
    "unauthorized reproduction",
    "read at ",
)


class StoryUnderstandingError(ValueError):
    """Fail-closed error for malformed or ungrounded story understanding."""


def load_instruction() -> tuple[str, str, str]:
    try:
        text = PROMPT_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise StoryUnderstandingError("story understanding prompt is invalid") from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if f"Version: {STORY_UNDERSTANDING_VERSION}" not in normalized:
        raise StoryUnderstandingError("story understanding prompt is invalid")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return STORY_UNDERSTANDING_VERSION, digest, normalized


def _words(value: object) -> list[str]:
    return re.findall(r"[^\W_]+(?:['’][^\W_]+)?", str(value or ""), flags=re.UNICODE)


def story_text_is_meaningful(value: object) -> bool:
    text = " ".join(str(value or "").split()).strip()
    if not text or any(marker in text.casefold() for marker in _NOISE_MARKERS):
        return False
    words = _words(text)
    if len(words) < 3:
        return False
    alpha = sum(character.isalpha() for character in text)
    if alpha < max(4, len(text) // 5):
        return False
    unique = {word.casefold() for word in words}
    return len(unique) >= 2


def dialogue_text(value: object) -> str:
    """Normalize OCR/dialogue rows without leaking provider-specific shapes."""
    if isinstance(value, Mapping):
        value = value.get("text", "")
    return " ".join(str(value or "").split()).strip()


def panel_story_signal(observation: Mapping[str, Any] | None) -> int:
    if not isinstance(observation, Mapping):
        return 0
    dialogue = observation.get("dialogue_or_ocr", ())
    score = 0
    if isinstance(dialogue, Sequence) and not isinstance(dialogue, (str, bytes)):
        score += 4 * sum(
            story_text_is_meaningful(dialogue_text(value)) for value in dialogue
        )
    for key, weight in (("state_changes", 3), ("causal_links", 3), ("inferences", 1)):
        values = observation.get(key, ())
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            score += weight * sum(bool(str(value).strip()) for value in values)
    return score


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
        digest = hashlib.sha256(f"{index}:{beat_id}:{fact}".encode("utf-8")).hexdigest()[:16]
        claims.append({
            "claim_id": f"story_understanding__{digest}",
            "claim_type": "fact" if confidence == "explicit" else "interpretation",
            "text": fact,
            "qualification": "Directly grounded by the story-understanding evidence." if confidence == "explicit" else qualification,
            "evidence_panel_ids": [str(panel_id) for panel_id in panel_ids],
        })
    return tuple(claims)


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
    if not isinstance(beats, list) or not 2 <= len(beats) <= 12 or not isinstance(threads, list):
        raise StoryUnderstandingError("story understanding response is incomplete")
    expected = {str(panel_id) for panel_id in expected_panel_ids}
    claims = _claim_map(story_map)
    seen: set[str] = set()
    normalized_beats: list[dict[str, Any]] = []
    for item in beats:
        if not isinstance(item, Mapping):
            raise StoryUnderstandingError("story understanding beat is invalid")
        beat_id = str(item.get("beat_id", "")).strip()
        role = str(item.get("story_role", "")).strip()
        fact = " ".join(str(item.get("fact", "")).split()).strip()
        confidence = str(item.get("confidence", "")).strip().casefold()
        qualification = " ".join(str(item.get("qualification", "")).split()).strip()
        if not beat_id or beat_id in seen or not role or not fact or confidence not in _ALLOWED_CONFIDENCE:
            raise StoryUnderstandingError("story understanding beat is invalid")
        if confidence == "qualified" and not qualification:
            raise StoryUnderstandingError("qualified story understanding requires qualification")
        panel_ids = _string_list(item.get("evidence_panel_ids"), "evidence_panel_ids", allow_empty=False)
        if any(panel_id not in expected for panel_id in panel_ids):
            raise StoryUnderstandingError("story understanding references a foreign panel")
        claim_ids = _string_list(item.get("source_claim_ids", []), "source_claim_ids")
        if any(claim_id not in claims for claim_id in claim_ids):
            raise StoryUnderstandingError("story understanding references a foreign claim")
        for claim_id in claim_ids:
            refs = claims[claim_id].get("evidence_panel_ids", claims[claim_id].get("panel_ids", ()))
            if not isinstance(refs, (list, tuple)) or not set(map(str, refs)).intersection(panel_ids):
                raise StoryUnderstandingError("story understanding claim lineage is invalid")
        seen.add(beat_id)
        normalized_beats.append({
            "beat_id": beat_id,
            "story_role": role,
            "fact": fact,
            "evidence_panel_ids": panel_ids,
            "source_claim_ids": claim_ids,
            "confidence": confidence,
            "qualification": qualification,
        })

    normalized_threads: list[dict[str, Any]] = []
    for item in threads:
        if not isinstance(item, Mapping):
            raise StoryUnderstandingError("unresolved thread is invalid")
        text = " ".join(str(item.get("text", "")).split()).strip()
        panel_ids = _string_list(item.get("evidence_panel_ids"), "thread evidence_panel_ids", allow_empty=False)
        if not text or any(panel_id not in expected for panel_id in panel_ids):
            raise StoryUnderstandingError("unresolved thread is invalid")
        normalized_threads.append({"text": text, "evidence_panel_ids": panel_ids})

    canonical = {
        "version": STORY_UNDERSTANDING_VERSION,
        "narration_ready_beats": normalized_beats,
        "unresolved_threads": normalized_threads,
    }
    canonical["understanding_hash"] = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return canonical


def build_source_packet(
    observations: Sequence[Mapping[str, Any]],
    story_map: Mapping[str, Any],
    continuity_ledger: Mapping[str, Any],
) -> dict[str, Any]:
    compact_observations: list[dict[str, Any]] = []
    for observation in observations:
        dialogue = [
            dialogue_text(value)
            for value in observation.get("dialogue_or_ocr", ())
            if story_text_is_meaningful(dialogue_text(value))
        ]
        compact_observations.append({
            "panel_id": str(observation.get("panel_id", "")),
            "source_index": int(observation.get("source_index", 0)),
            "dialogue_or_ocr": dialogue[:4],
            "visible_facts": [str(value) for value in observation.get("visible_facts", ())][:2],
            "inferences": [str(value) for value in observation.get("inferences", ())][:2],
            "uncertainties": [str(value) for value in observation.get("uncertainties", ())][:2],
        })
    return {
        "version": STORY_UNDERSTANDING_VERSION,
        "panel_ids": [row["panel_id"] for row in compact_observations],
        "observations": compact_observations,
        "story_map": dict(story_map),
        "continuity_ledger": dict(continuity_ledger),
        "priority": "meaning_and_state_change_before_visual_inventory",
    }
