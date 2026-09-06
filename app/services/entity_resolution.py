"""Conservative OCR-backed character entity promotion."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'’.-]*")
PERSON_TITLES = (
    "young lady", "young master", "lady", "lord", "master", "elder", "sir",
    "saintess", "saint", "prince", "princess", "duke", "duchess", "commander",
    "captain", "chief", "doctor", "professor",
)
STRONG_CUES = ("name is", "named", "called", "choose", "support", "back", "meet", "find")
COMMON = frozenset(
    ["a", "about", "after", "again", "against", "all", "am", "an", "and", "are", "as", "at", "be", "because", "been", "before", "being", "but", "by", "can", "choose", "did", "do", "does", "for", "from", "get", "go", "had", "has", "have", "he", "her", "here", "hers", "him", "his", "how", "i", "if", "in", "instead", "into", "is", "it", "its", "just", "me", "my", "no", "not", "now", "of", "on", "one", "or", "our", "she", "so", "some", "that", "the", "their", "them", "then", "there", "they", "think", "this", "those", "to", "too", "up", "us", "want", "was", "we", "were", "what", "when", "where", "which", "who", "why", "will", "with", "would", "you", "your", "please", "wait", "first", "going", "see", "i'm", "i'll", "we'll", "you're", "it's", "that's", "don't", "can't", "won't"]
)
CONCEPT = frozenset(
    ["academy", "association", "class", "clan", "dungeon", "empire", "evasion", "gate", "guild", "kingdom", "leader", "level", "orb", "realm", "rank", "sect", "skill", "style", "system", "technique", "tower", "king", "queen", "emperor", "empress", "forest", "city", "palace", "young", "lady", "master", "lord", "elder", "sir", "saint", "saintess", "prince", "princess", "duke", "duchess", "commander", "captain", "chief", "doctor", "professor"]
)


@dataclass
class Candidate:
    display_name: str
    panel_ids: set[str] = field(default_factory=set)
    strong_hits: int = 0
    aliases: set[str] = field(default_factory=set)
    bound_roles: set[str] = field(default_factory=set)


def _clean(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _piece(value: str) -> str:
    if "-" in value:
        first, *rest = value.split("-")
        return _piece(first) + "".join(f"-{part.lower()}" for part in rest)
    mark = "’" if "’" in value else "'" if "'" in value else ""
    if mark:
        return mark.join(_piece(part) for part in value.split(mark))
    return value[:1].upper() + value[1:].lower()


def _display(tokens: Sequence[str]) -> str:
    return " ".join(_piece(token.strip(" .")) for token in tokens if token.strip(" ."))


def _key(value: str) -> str:
    return _clean(value).casefold()


def _tokens(value: str) -> list[str]:
    result: list[str] = []
    for token in TOKEN_RE.findall(value):
        lowered = token.casefold()
        if lowered.endswith("'s") or lowered.endswith("’s"):
            token = token[:-2]
        if token:
            result.append(token)
    return result


def _looks_name(tokens: Sequence[str], *, allow_single: bool = False) -> bool:
    if not tokens or len(tokens) > 4 or (len(tokens) == 1 and not allow_single):
        return False
    folded = [token.casefold().strip(".") for token in tokens]
    if any(token in COMMON or token in CONCEPT for token in folded):
        return False
    if any(len(token.strip(".-'’")) < 2 for token in folded):
        return False
    return all(token.isupper() or token[:1].isupper() for token in tokens)


def _name_prefix(value: str, *, allow_single: bool) -> list[str]:
    value = re.split(r"[,.!?;:\n]+", value, maxsplit=1)[0]
    picked: list[str] = []
    for token in _tokens(value)[:4]:
        folded = token.casefold().strip(".")
        if folded in COMMON or folded in CONCEPT:
            break
        if not (token.isupper() or token[:1].isupper()):
            break
        picked.append(token)
    for size in (2, 3, 4):
        if len(picked) >= size and _looks_name(picked[:size]):
            return picked[:size]
    if allow_single and picked and _looks_name(picked[:1], allow_single=True):
        return picked[:1]
    return []


def _register(
    candidates: dict[str, Candidate], tokens: Sequence[str], panel_id: str, *,
    alias: str = "", role: str = "",
) -> None:
    name = _display(tokens)
    if not name:
        return
    candidate = candidates.setdefault(_key(name), Candidate(name))
    candidate.panel_ids.add(panel_id)
    candidate.strong_hits += 1
    if alias:
        candidate.aliases.add(_clean(alias))
    if role:
        candidate.bound_roles.add(_clean(role).casefold())


def _strong(text: str, panel_id: str, candidates: dict[str, Candidate]) -> None:
    normalized = _clean(text)
    for title in PERSON_TITLES:
        for match in re.finditer(rf"\b{re.escape(title)}\b", normalized, re.IGNORECASE):
            name_tokens = _name_prefix(normalized[match.end():].lstrip(" ,:-"), allow_single=True)
            if name_tokens:
                pretty_title = " ".join(part.capitalize() for part in title.split())
                _register(
                    candidates, name_tokens, panel_id,
                    alias=f"{pretty_title} {_display(name_tokens)}", role=title,
                )
    for cue in STRONG_CUES:
        for match in re.finditer(rf"\b{re.escape(cue)}\b", normalized, re.IGNORECASE):
            name_tokens = _name_prefix(
                normalized[match.end():].lstrip(" ,:-"),
                allow_single=cue in {"name is", "named", "called"},
            )
            if name_tokens:
                _register(candidates, name_tokens, panel_id)


def _repeat_candidates(text: str, panel_id: str, occurrences: dict[str, set[str]], names: dict[str, str]) -> None:
    for clause in re.split(r"[.!?;:\n]+", _clean(text)):
        tokens = _tokens(clause)
        for size in (2,):
            for index in range(max(0, len(tokens) - size + 1)):
                window = tokens[index:index + size]
                if not _looks_name(window):
                    continue
                name = _display(window)
                occurrences[_key(name)].add(panel_id)
                names.setdefault(_key(name), name)


def build_continuity_entities(
    observations_by_panel: Mapping[str, Mapping[str, Any]], panel_ids: Sequence[str],
) -> list[dict[str, Any]]:
    """Return provider entities plus high-confidence explicit OCR character names."""
    provider_panels: dict[str, set[str]] = defaultdict(set)
    provider_names: dict[str, str] = {}
    candidates: dict[str, Candidate] = {}
    occurrences: dict[str, set[str]] = defaultdict(set)
    occurrence_names: dict[str, str] = {}

    for panel_id in panel_ids:
        observation = observations_by_panel.get(panel_id, {})
        if not isinstance(observation, Mapping):
            continue
        for entity in observation.get("entities", []):
            if isinstance(entity, str) and _clean(entity):
                canonical = _clean(entity)
                provider_names.setdefault(_key(canonical), canonical)
                provider_panels[_key(canonical)].add(panel_id)
        dialogue = observation.get("dialogue_or_ocr", [])
        if not isinstance(dialogue, list):
            continue
        for raw in dialogue:
            if not isinstance(raw, str) or not _clean(raw):
                continue
            _strong(raw, panel_id, candidates)
            _repeat_candidates(raw, panel_id, occurrences, occurrence_names)

    for key, panels in occurrences.items():
        if len(panels) < 2:
            continue
        candidate = candidates.setdefault(key, Candidate(occurrence_names[key]))
        candidate.panel_ids.update(panels)

    promoted = {
        key: candidate for key, candidate in candidates.items()
        if candidate.strong_hits or len(candidate.panel_ids) >= 2
    }
    for key, candidate in promoted.items():
        if key in provider_panels:
            candidate.panel_ids.update(provider_panels.pop(key))
            provider_names.pop(key, None)
        for role in tuple(candidate.bound_roles):
            role_key = _key(role)
            role_panels = provider_panels.get(role_key)
            if role_panels and role_panels.intersection(candidate.panel_ids):
                candidate.aliases.add(role)
                candidate.panel_ids.update(role_panels)
                provider_panels.pop(role_key, None)
                provider_names.pop(role_key, None)

    rows: list[dict[str, Any]] = []
    for key in sorted(provider_names):
        rows.append({
            "entity_id": f"visual-entity-{hashlib.sha256(key.encode()).hexdigest()[:12]}",
            "canonical_name": provider_names[key],
            "aliases": [],
            "panel_ids": [panel for panel in panel_ids if panel in provider_panels[key]],
        })
    for key in sorted(promoted):
        candidate = promoted[key]
        rows.append({
            "entity_id": f"visual-entity-{hashlib.sha256(key.encode()).hexdigest()[:12]}",
            "canonical_name": candidate.display_name,
            "aliases": sorted(
                {alias for alias in candidate.aliases if _key(alias) != key}, key=str.casefold
            ),
            "panel_ids": [panel for panel in panel_ids if panel in candidate.panel_ids],
        })
    if rows:
        return rows
    return [{
        "entity_id": "visual-entity-observed-context",
        "canonical_name": "observed context",
        "aliases": [],
        "panel_ids": list(panel_ids),
    }]
