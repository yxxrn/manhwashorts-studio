from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

PROMPT_VERSION = "vision-first-story-analyzer-v2"
PROMPT_PATH = (
    Path(__file__).resolve().parents[1] / "prompts" / ("vision_first_story_analyzer_v2.txt")
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

    def __init__(
        self,
        message: str = "analyzer contract validation failed",
        *,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.diagnostics = dict(diagnostics or {})


def _load_v2_instruction() -> tuple[str, str, str]:
    """Load the default v2 prompt without changing its legacy behavior."""

    try:
        text = PROMPT_PATH.read_text(encoding="utf-8")
        normalized = text.replace("\r\n", "\n")
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return PROMPT_VERSION, digest, normalized
    except (OSError, UnicodeError):
        raise AnalyzerContractError("analyzer instruction cannot be loaded") from None


def load_analyzer_instruction(*, narrative_profile_id: str | None = None) -> tuple[str, str, str]:
    """Load v2 by default or an explicitly selected verified identity."""

    if narrative_profile_id is None:
        return _load_v2_instruction()
    try:
        from app.services import narrative_identity

        return narrative_identity.load_narrative_instruction(narrative_profile_id)
    except narrative_identity.NarrativeIdentityError:
        raise AnalyzerContractError("unknown narrative profile") from None


def _fail(message: str, *, diagnostics: Mapping[str, Any] | None = None) -> None:
    raise AnalyzerContractError(message, diagnostics=diagnostics)


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


def _validate_observations(value: Any, expected: tuple[str, ...]) -> None:
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

        _nonempty_string(observation.get("coverage_map_version"), "coverage_map_version")
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
    for previous, current in zip(chunk_panel_ids, chunk_panel_ids[1:], strict=False):
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
        _require_fields(
            entity, ("entity_id", "canonical_name", "aliases", "panel_ids"), "continuity entity"
        )
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


_RETENTION_SEMANTIC_STOPWORDS = {
    "about",
    "after",
    "again",
    "against",
    "being",
    "because",
    "before",
    "could",
    "every",
    "from",
    "have",
    "into",
    "itself",
    "must",
    "only",
    "their",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "under",
    "very",
    "while",
    "with",
    "without",
    "would",
    "character",
    "characters",
    "panel",
    "panels",
    "scene",
    "shown",
    "visible",
    "direct",
    "directly",
    "evidence",
    "stated",
    "implied",
    "observed",
    "fact",
    "interpretation",
    "context",
    "supported",
    "support",
    "grounded",
    "inference",
    "qualification",
    "activation",
    "consequence",
}
_RETENTION_SEMANTIC_MORPHOLOGY = {
    "marry": "marry",
    "marries": "marry",
    "married": "marry",
    "marriage": "marry",
    "require": "require",
    "requires": "require",
    "required": "require",
    "requirement": "require",
    "requirements": "require",
    "need": "require",
    "needs": "require",
    "needed": "require",
    "purify": "purify",
    "purifies": "purify",
    "purified": "purify",
    "purification": "purify",
    "declare": "declare",
    "declares": "declare",
    "declared": "declare",
    "declaration": "declare",
    "heal": "heal",
    "heals": "heal",
    "healed": "heal",
    "healing": "heal",
    "exhaust": "exhaust",
    "exhausted": "exhaust",
    "exhaustion": "exhaust",
    "accompany": "accompany",
    "accompanies": "accompany",
    "accompanied": "accompany",
    "accompanying": "accompany",
    "stay": "stay",
    "stays": "stay",
    "stayed": "stay",
    "staying": "stay",
    "continuous": "continuous",
    "continuously": "continuous",
    "constant": "continuous",
    "constantly": "continuous",
    "deteriorate": "deteriorate",
    "deteriorates": "deteriorate",
    "deteriorated": "deteriorate",
    "deteriorating": "deteriorate",
    "deterioration": "deteriorate",
    "share": "share",
    "shares": "share",
    "shared": "share",
    "sharing": "share",
    "bedchamber": "chamber",
    "bedchambers": "chamber",
    "chambers": "chamber",
    "force": "force",
    "forces": "force",
    "forced": "force",
    "trust": "trust",
    "trusts": "trust",
    "trusted": "trust",
    "love": "love",
    "loves": "love",
    "loved": "love",
    "must": "require",
    "curse": "curse",
    "cursed": "curse",
}

_RETENTION_REQUIRED_LOCAL_ANCHORS = frozenset(
    {
        "law",
        "require",
        "marry",
        "share",
        "chamber",
        "force",
        "declare",
        "treatment",
        "heal",
        "purify",
        "curse",
        "mana",
        "trust",
        "love",
        "continuous",
    }
)


def _semantic_anchor_tokens(text: str) -> set[str]:
    result: set[str] = set()
    for token in _normalized_lexical_words(text):
        if token in {"yes", "yeah", "yep"} or token.startswith("affirmativ"):
            result.add("affirmative")
        elif token in {"no", "nope"} or token.startswith("negativ"):
            result.add("negative")
        token = _RETENTION_SEMANTIC_MORPHOLOGY.get(token, token)
        if len(token) >= 5 and token.endswith("ies"):
            token = token[:-3] + "y"
        elif len(token) >= 5 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        token = _RETENTION_SEMANTIC_MORPHOLOGY.get(token, token)
        if (len(token) >= 4 or token in {"law"}) and token not in _RETENTION_SEMANTIC_STOPWORDS:
            result.add(token)
    return result


def _retention_semantic_candidate_panels(
    anchors: set[str], observation_by_panel: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    ranked: list[tuple[int, int, str, list[str], list[str]]] = []
    for panel_id, obs in observation_by_panel.items():
        observed: set[str] = set()
        excerpts: list[str] = []
        for field in ("visible_facts", "dialogue_or_ocr", "inferences", "uncertainties"):
            for value in obs.get(field, []) or []:
                text = str(value).strip()
                observed |= _semantic_anchor_tokens(text)
                if text and len(excerpts) < 4:
                    excerpts.append(text[:240])
        overlap = sorted(anchors & observed)
        if not overlap:
            continue
        raw_order = obs.get("source_order")
        if isinstance(raw_order, bool) or not isinstance(raw_order, int):
            raw_order = obs.get("source_index", 10**9)
        source_order = (
            int(raw_order)
            if isinstance(raw_order, int) and not isinstance(raw_order, bool)
            else 10**9
        )
        ranked.append((len(overlap), source_order, panel_id, overlap, excerpts))
    ranked.sort(key=lambda row: (-row[0], row[1], row[2]))
    selected = list(ranked[:4])
    frequencies: dict[str, int] = {}
    for row in ranked:
        for anchor in row[3]:
            frequencies[anchor] = frequencies.get(anchor, 0) + 1
    for anchor in sorted(frequencies, key=lambda value: (frequencies[value], value)):
        if len(selected) >= 8:
            break
        candidate = next((row for row in ranked if anchor in row[3]), None)
        if candidate is not None and candidate not in selected:
            selected.append(candidate)
    for row in ranked:
        if len(selected) >= 8:
            break
        if row not in selected:
            selected.append(row)
    return [
        {
            "panel_id": panel_id,
            "source_order": None if source_order == 10**9 else source_order,
            "overlap_anchors": overlap,
            "evidence_excerpt": excerpts,
        }
        for _count, source_order, panel_id, overlap, excerpts in selected
    ]


def _validate_retention_claim_semantic_grounding(graph_value: Any, observations: Any) -> None:
    graph = _mapping(graph_value, "evidence_graph")
    claims = graph.get("claims")
    observation_by_panel: dict[str, Mapping[str, Any]] = {}
    order_by_panel: dict[str, int] = {}
    for fallback, raw in enumerate(observations):
        obs = _mapping(raw, "observation")
        panel_id = str(obs.get("panel_id", ""))
        if panel_id:
            observation_by_panel[panel_id] = obs
            raw_index = obs.get("source_index")
            order_by_panel[panel_id] = (
                int(raw_index)
                if isinstance(raw_index, int) and not isinstance(raw_index, bool)
                else fallback
            )
    max_span = 12
    for raw_claim in claims or []:
        claim = _mapping(raw_claim, "claim")
        anchors = _semantic_anchor_tokens(str(claim.get("text", "")))
        qualification_anchors = _semantic_anchor_tokens(str(claim.get("qualification", "")))
        evidence_rows: list[tuple[int, set[str], bool]] = []
        observed: set[str] = set()
        for panel_id in claim.get("evidence_panel_ids", []) or []:
            pid = str(panel_id)
            obs = observation_by_panel.get(pid)
            if not obs:
                continue
            panel_tokens: set[str] = set()
            for field in ("visible_facts", "dialogue_or_ocr", "inferences", "uncertainties"):
                for value in obs.get(field, []) or []:
                    panel_tokens |= _semantic_anchor_tokens(str(value))
            observed |= panel_tokens
            has_dialogue = any(str(value).strip() for value in (obs.get("dialogue_or_ocr") or []))
            evidence_rows.append((order_by_panel.get(pid, 10**9), panel_tokens, has_dialogue))
        required_overlap = min(5, max(1, (2 * len(anchors) + 2) // 3)) if anchors else 0
        best_local: set[str] = set()
        best_local_has_dialogue = False
        rows = sorted(evidence_rows, key=lambda row: row[0])
        for left in range(len(rows)):
            local: set[str] = set()
            local_has_dialogue = False
            for right in range(left, len(rows)):
                if rows[right][0] - rows[left][0] > max_span:
                    break
                local |= rows[right][1]
                local_has_dialogue = local_has_dialogue or rows[right][2]
                matched_local = anchors & local
                if len(matched_local) > len(best_local):
                    best_local = matched_local
                    best_local_has_dialogue = local_has_dialogue
                elif len(matched_local) == len(best_local) and local_has_dialogue:
                    best_local_has_dialogue = True
        if (
            "declare" in anchors
            and "declare" not in best_local
            and best_local_has_dialogue
            and len((anchors - {"declare"}) & best_local) >= 2
        ):
            best_local = set(best_local)
            best_local.add("declare")
        dialogue_act_match = bool(best_local & {"affirmative", "negative"})
        critical = anchors & _RETENTION_REQUIRED_LOCAL_ANCHORS
        missing_critical = critical - best_local
        required_for_retry = max(required_overlap, len(best_local) + len(missing_critical))
        if (
            anchors
            and not dialogue_act_match
            and (len(best_local) < required_overlap or missing_critical)
        ):
            _fail(
                "claim evidence lacks semantic anchor",
                diagnostics={
                    "claim_id": str(claim.get("claim_id", "")),
                    "claim_text": str(claim.get("text", ""))[:500],
                    "qualification": str(claim.get("qualification", ""))[:500],
                    "evidence_panel_ids": [
                        str(v) for v in (claim.get("evidence_panel_ids", []) or [])
                    ],
                    "claim_anchors": sorted(anchors)[:80],
                    "qualification_anchors": sorted(qualification_anchors)[:80],
                    "observed_anchors": sorted(observed)[:160],
                    "matched_claim_anchors": sorted(best_local)[:80],
                    "required_anchor_matches": required_for_retry,
                    "critical_claim_anchors": sorted(critical),
                    "missing_critical_anchors": sorted(missing_critical),
                    "semantic_window_max_span": max_span,
                    "candidate_panels": _retention_semantic_candidate_panels(
                        anchors, observation_by_panel
                    ),
                },
            )


def _validate_retention_causal_chain(
    continuity_value: Any, graph_value: Any, passages_value: Any
) -> None:
    continuity = _mapping(continuity_value, "continuity_ledger")
    graph = _mapping(graph_value, "evidence_graph")
    claims = graph.get("claims")
    if (
        not isinstance(claims, list)
        or not isinstance(passages_value, list)
        or len(passages_value) < 4
    ):
        _fail("retention causal chain cannot be evaluated")
    claim_by_id = {str(item.get("claim_id")): item for item in claims if isinstance(item, Mapping)}
    panel_order: dict[str, int] = {}
    cursor = 0
    for raw_chunk in continuity.get("chunks", []) or []:
        if not isinstance(raw_chunk, Mapping):
            continue
        for value in raw_chunk.get("panel_ids", []) or []:
            pid = str(value)
            if pid not in panel_order:
                panel_order[pid] = cursor
                cursor += 1
    edges: dict[str, set[str]] = {}
    for raw in continuity.get("causal_links", []) or []:
        if not isinstance(raw, Mapping):
            continue
        source = str(raw.get("from_panel_id", ""))
        target = str(raw.get("to_panel_id", ""))
        if not source or not target:
            continue
        if panel_order and (
            source not in panel_order
            or target not in panel_order
            or panel_order[target] <= panel_order[source]
        ):
            _fail(
                "retention causal link moves backward in chronology",
                diagnostics={
                    "from_panel_id": source,
                    "to_panel_id": target,
                    "from_source_order": panel_order.get(source),
                    "to_source_order": panel_order.get(target),
                },
            )
        edges.setdefault(source, set()).add(target)

    def reachable_nodes(starts: set[str]) -> set[str]:
        seen = set(starts)
        frontier = list(starts)
        while frontier:
            current = frontier.pop()
            for nxt in edges.get(current, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)
        return seen

    def reachable(starts: set[str], targets: set[str]) -> bool:
        return bool(reachable_nodes(starts) & targets)

    hook = _mapping(passages_value[0], "script passage")
    hook_evidence = {
        str(v)
        for cid in _string_list(hook.get("claim_ids"), "passage claim_ids", allow_empty=False)
        for v in (claim_by_id.get(cid, {}).get("evidence_panel_ids", []) or [])
    }
    body_seen: set[str] = set()
    prior_evidence: set[str] = set()
    setup_evidence: set[str] = set()
    for body_index, raw_passage in enumerate(passages_value[1:]):
        passage = _mapping(raw_passage, "script passage")
        claim_ids = _string_list(passage.get("claim_ids"), "passage claim_ids", allow_empty=False)
        current: set[str] = set()
        for claim_id in claim_ids:
            claim = claim_by_id.get(claim_id)
            evidence = (
                {str(v) for v in (claim.get("evidence_panel_ids", []) or [])} if claim else set()
            )
            current |= evidence
            if (
                body_index >= 1
                and claim_id not in body_seen
                and not reachable(prior_evidence, evidence)
            ):
                reachable_panel_ids = reachable_nodes(prior_evidence)
                reachable_claims: list[dict[str, Any]] = []
                for candidate_id, candidate in claim_by_id.items():
                    if candidate_id in body_seen or candidate_id == claim_id:
                        continue
                    candidate_evidence = {
                        str(v) for v in (candidate.get("evidence_panel_ids", []) or [])
                    }
                    if not candidate_evidence or not (candidate_evidence & reachable_panel_ids):
                        continue
                    reachable_claims.append(
                        {
                            "claim_id": candidate_id,
                            "claim_text": str(candidate.get("text", ""))[:300],
                            "evidence_panel_ids": sorted(
                                candidate_evidence,
                                key=lambda pid: (panel_order.get(pid, 10**9), pid),
                            )[:12],
                        }
                    )
                reachable_claims.sort(
                    key=lambda item: (
                        min(
                            (panel_order.get(str(pid), 10**9) for pid in item["evidence_panel_ids"]),
                            default=10**9,
                        ),
                        str(item["claim_id"]),
                    )
                )
                reachable_claims = reachable_claims[:16]
                _fail(
                    "retention passage introduces disconnected claim",
                    diagnostics={
                        "passage_number": body_index + 2,
                        "passage_id": str(passage.get("passage_id", "")),
                        "claim_id": claim_id,
                        "claim_text": str(claim.get("text", ""))[:500] if claim else "",
                        "claim_evidence_panel_ids": sorted(
                            evidence,
                            key=lambda pid: (panel_order.get(pid, 10**9), pid),
                        )[:24],
                        "prior_body_evidence_panel_ids": sorted(
                            prior_evidence,
                            key=lambda pid: (panel_order.get(pid, 10**9), pid),
                        )[-32:],
                        "reachable_panel_ids": sorted(
                            reachable_panel_ids,
                            key=lambda pid: (panel_order.get(pid, 10**9), pid),
                        )[-64:],
                        "reachable_candidate_claims": reachable_claims,
                    },
                )
        if body_index == 0:
            setup_evidence = set(current)
        prior_evidence.update(current)
        body_seen.update(claim_ids)
    hook_orders = [panel_order[v] for v in hook_evidence if v in panel_order]
    setup_orders = [panel_order[v] for v in setup_evidence if v in panel_order]
    is_late_teaser = bool(hook_orders and setup_orders and min(hook_orders) > min(setup_orders))
    if is_late_teaser and not reachable(setup_evidence, hook_evidence):
        reachable_from_setup = reachable_nodes(setup_evidence)
        hook_claim_ids = _string_list(
            hook.get("claim_ids"), "passage claim_ids", allow_empty=False
        )
        _fail(
            "retention hook teaser is not reachable from body causal chain",
            diagnostics={
                "passage_number": 1,
                "passage_id": str(hook.get("passage_id", "")),
                "claim_id": hook_claim_ids[0] if len(hook_claim_ids) == 1 else "",
                "hook_claim_ids": hook_claim_ids,
                "hook_evidence_panel_ids": sorted(
                    hook_evidence,
                    key=lambda pid: (panel_order.get(pid, 10**9), pid),
                )[:24],
                "setup_evidence_panel_ids": sorted(
                    setup_evidence,
                    key=lambda pid: (panel_order.get(pid, 10**9), pid),
                )[:24],
                "reachable_panel_ids": sorted(
                    reachable_from_setup,
                    key=lambda pid: (panel_order.get(pid, 10**9), pid),
                )[-64:],
            },
        )


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
    return {tuple(words[index : index + size]) for index in range(max(0, len(words) - size + 1))}


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


def source_dialogue_copy_diagnostics(observations: Any, passages: Any) -> dict[str, Any]:
    """Return one minimal verbatim-overlap diagnostic without exposing full source text."""
    try:
        signatures = _source_dialogue_ngrams(observations)
        if not signatures or not isinstance(passages, (list, tuple)):
            return {}
        by_size: dict[int, set[tuple[str, ...]]] = {}
        for signature in signatures:
            by_size.setdefault(len(signature), set()).add(signature)
        for passage_index, passage in enumerate(passages):
            if not isinstance(passage, Mapping):
                continue
            text = passage.get("text")
            if not isinstance(text, str):
                continue
            words = _normalized_lexical_words(text)
            for size in sorted(by_size):
                if len(words) < size:
                    continue
                matches = _ngrams(words, size) & by_size[size]
                if matches:
                    matched = min(matches)
                    return {
                        "passage_index": passage_index,
                        "ngram_size": size,
                        "matched_phrase": " ".join(matched),
                    }
    except (KeyError, TypeError, ValueError):
        return {}
    return {}


def contains_source_dialogue_copy(observations: Any, passages: Any) -> bool:
    """Detect substantial verbatim source dialogue while allowing faithful paraphrase."""
    return bool(source_dialogue_copy_diagnostics(observations, passages))


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

        claim_ids = _string_list(passage["claim_ids"], "passage claim_ids", allow_empty=False)
        if not set(claim_ids) <= set(claim_evidence):
            _fail("script passage references an unknown claim")
        evidence_panel_ids = set(
            _panel_refs(passage["evidence_panel_ids"], expected, "passage evidence")
        )
        required_evidence = set().union(*(claim_evidence[claim_id] for claim_id in claim_ids))
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
    validate_text_checks: bool = True,
) -> None:
    if not isinstance(value, list) or not profile.passage_min <= len(value) <= profile.passage_max:
        _fail("script_passages must contain four to six passages")
    passage_ids: set[str] = set()
    covered_claim_evidence: dict[str, set[str]] = {claim_id: set() for claim_id in claim_evidence}
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
        if validate_text_checks:
            if _contains_channel_cta(text):
                _fail("generic channel CTA language is not allowed")
            normalized_text = " ".join(_normalized_lexical_words(text))
            if any(marker in normalized_text for marker in _V3_GENERIC_HYPE):
                _fail("generic hype language is not allowed")
            if not allow_dialogue_copy and contains_source_dialogue_copy(observations, (passage,)):
                _fail("script passage copies source dialogue")
        claim_ids = _string_list(passage["claim_ids"], "passage claim_ids", allow_empty=False)
        claim_id_set = set(claim_ids)
        if not claim_id_set <= set(claim_evidence):
            _fail("script passage references an unknown claim")
        evidence = set(_panel_refs(passage["evidence_panel_ids"], expected, "passage evidence"))
        for claim_id in claim_ids:
            local_claim_evidence = evidence & claim_evidence[claim_id]
            if not local_claim_evidence:
                _fail("script passage claim lacks local evidence")
            covered_claim_evidence[claim_id].update(local_claim_evidence)
    if any(
        covered_claim_evidence[claim_id] != required
        for claim_id, required in claim_evidence.items()
    ):
        _fail("script passage evidence does not cover its claims")
    if validate_text_checks:
        if getattr(profile, "profile_id", "") == "retention_story_v1":
            hook_text = _nonempty_string(value[0]["text"], "retention hook text").strip()
            hook_words = hook_text.split()
            if not 8 <= len(hook_words) <= 14:
                _fail("retention hook must contain 8-14 words")
            if len(_normalized_sentences(hook_text)) != 1:
                _fail("retention hook must be one sentence")
        final_text = _nonempty_string(value[-1]["text"], "final script passage text").rstrip()
        _validate_v3_ending(outline, final_text, profile)


def _validate_output(
    output: Any,
    expected: tuple[str, ...],
    *,
    narrative_profile_id: str | None = None,
    allow_dialogue_copy: bool = False,
    validate_text_checks: bool = True,
) -> None:
    profile = None
    if narrative_profile_id is not None:
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
    if (
        profile is not None
        and getattr(profile, "profile_id", "") == "retention_story_v1"
        and getattr(profile, "profile_version", "") >= "1.1.0"
    ):
        _validate_retention_claim_semantic_grounding(
            document["evidence_graph"], document["observations"]
        )
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
            validate_text_checks=validate_text_checks,
        )
        if (
            getattr(profile, "profile_id", "") == "retention_story_v1"
            and getattr(profile, "profile_version", "") >= "1.5.0"
        ):
            _validate_retention_causal_chain(
                document["continuity_ledger"],
                document["evidence_graph"],
                document["script_passages"],
            )


def validate_analyzer_output(
    output: Mapping[str, Any],
    *,
    expected_panel_ids: Sequence[str],
    narrative_profile_id: str | None = None,
    allow_dialogue_copy: bool = False,
    validate_text_checks: bool = True,
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
            validate_text_checks=validate_text_checks,
        )
    except AnalyzerContractError:
        raise
    except Exception:
        raise AnalyzerContractError("malformed analyzer output") from None
