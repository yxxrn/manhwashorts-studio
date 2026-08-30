"""Pinned cloud multimodal stages and resumable review-only chapter jobs.

The module deliberately stops before TTS.  It reuses the existing visual
evidence, analyzer, and Sharp Friend validators; this layer owns provider
selection, stage identity, local reconciliation, cache keys, and batch state.
Provider output is untrusted JSON.  Canonical hashes are always computed here.
"""

# ruff: noqa: F401, F811 -- runner mixins resolve facade globals dynamically.
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import queue
import random
import re
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from app.constants import (
    STANDARD_FINAL_DURATION_MAX_SECONDS,
    STANDARD_FINAL_DURATION_MIN_SECONDS,
)
from app.services import (
    analyzer_contract,
    cloud_runner_parts,
    editorial_qc,
    narrative_identity,
    prepared_panel_manifest,
    quality,
    script,
    strip_segmentation,
    visual_narrative_repair,
    visual_scoring,
)
from app.services.vision_adapter import (
    VisionCapabilityError,
    VisionObservationRequest,
    VisionProviderRequestFailed,
    VisionRequestInvalid,
    VisionResponseInvalid,
)

CAUSAL_MAP_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "cloud_causal_map_v1.txt"
CAUSAL_MAP_PROMPT_VERSION = "cloud-causal-map-v2"
STRIP_BOUNDARY_PROMPT_PATH = (
    Path(__file__).resolve().parents[1] / "prompts" / "strip_boundary_assessment_v1.txt"
)
STRIP_BOUNDARY_PROMPT_VERSION = "strip-boundary-assessment-v1"
# Keep provider response envelopes to one image: this configured endpoint has
# returned incomplete structured JSON for multi-image requests.  Every ordered
# panel is still processed; local reconciliation owns complete coverage.
VISUAL_REQUEST_MAX_PANELS = 4  # live A/B: best throughput/repair balance with global provider gate
VISUAL_REQUEST_MAX_ESTIMATED_BYTES = 3_500_000  # preview-only: larger visual batches
VISUAL_FINAL_FRESH_SINGLETON_ATTEMPTS = 1  # confirm rare transient rejects with a fresh runner
VISUAL_REQUEST_OVERLAP = 0
VISUAL_ANALYSIS_WINDOW_VERSION = "visual-analysis-windows-v1"
VISUAL_ANALYSIS_WINDOW_MIN_RATIO = 3.0
VISUAL_ANALYSIS_WINDOW_MAX_COUNT = 12
VISUAL_ANALYSIS_WINDOW_MAX_WIDTH = 512
VISUAL_ANALYSIS_WINDOW_MAX_HEIGHT = 768
VISUAL_ANALYSIS_WINDOW_JPEG_QUALITY = 72
VISUAL_ANALYSIS_WINDOW_OVERLAP_FRACTION = 0.18
VISUAL_WINDOW_GEOMETRY_VERSION = "window-geometry-reconciled-v1"
VISUAL_WINDOW_GEOMETRY_WORKERS = 8
VISUAL_STREAM_VERSION = "visual-stream-v1"
VISUAL_STREAM_WORKER_COUNT = 8
VISUAL_STREAM_QUEUE_SIZE = 8
VISUAL_STREAM_WAVE_PANEL_TARGET = 32
STORY_MAP_CHUNK_STEP = 180
STORY_MAP_COVERAGE_FALLBACK_STEP = 60
# Some configured multimodal models still omit grounded panel references in a
# 30-panel response.  Keep the fallback bounded, but allow one smaller split
# before failing closed rather than treating a recoverable coverage omission as
# a project-level story failure.
STORY_MAP_COVERAGE_MIN_STEP = 15
STORY_MAP_COVERAGE_FINAL_STEP = 5
NARRATION_CHUNK_STEP = 180
NARRATION_COVERAGE_FALLBACK_STEP = 60
NARRATION_COVERAGE_MIN_STEP = 30
NARRATION_REPAIR_VERSION = "narration-targeted-repair-v6"
NARRATION_REPAIR_MAX_ATTEMPTS = 3
NARRATION_REPAIR_POSITION_MAX_ATTEMPTS = 3  # two bounded retries for positional prose repair
NARRATION_REPAIR_CANDIDATE_VERSION = "narration-repair-candidate-v1"
NARRATION_REPAIR_RESULT_VERSION = "narration-repair-result-v7"
NARRATION_REPAIR_CANDIDATE_STAGE = "narration_repair_candidate"
NARRATION_REPAIR_SLOT_REGISTRY_VERSION = "narration-repair-slot-registry-v1"
NARRATION_REPAIR_POSITION_REGISTRY_VERSION = "narration-repair-position-registry-v5"
NARRATION_REPAIR_PASSAGE_LINEAGE_VERSION = "narration-repair-passage-lineage-v1"
NARRATION_REPAIR_IDENTITY_VERSION = "narration-repair-identity-v1"
NARRATION_REPAIR_IDENTITY_MIGRATION_VERSION = "narration-repair-identity-migration-v1"
NARRATION_REPAIR_EVIDENCE_CLOSURE_VERSION = "narration-repair-evidence-closure-v2"
NARRATION_MICRO_COMPACTION_VERSION = "narration-micro-compaction-v3"
NARRATION_MICRO_COMPACTION_MIN_WORDS = 126
NARRATION_MICRO_COMPACTION_MAX_WORDS = 132
NARRATION_MICRO_EXPANSION_VERSION = "narration-micro-expansion-v1"
NARRATION_MICRO_EXPANSION_MIN_WORDS = 109
NARRATION_MICRO_EXPANSION_MAX_WORDS = 114
NARRATION_REPAIR_POSITION_MIN_WORDS = 7
NARRATION_REPAIR_POSITION_WORD_SLACK = 8
NARRATION_REPAIR_POSITION_MIN_COUNT = 4
NARRATION_REPAIR_POSITION_MAX_COUNT = 8
NARRATION_REPAIR_POSITION_MAX_SHARE = 0.25
NARRATION_REPAIR_POSITION_DOMINANCE_FLOOR = 24


def _position_word_budget_bounds(
    word_budget: int,
    *,
    max_word_budget: int | None = None,
) -> tuple[int, int]:
    maximum = word_budget + NARRATION_REPAIR_POSITION_WORD_SLACK
    if max_word_budget is not None:
        maximum = min(maximum, max_word_budget)
    return (
        max(
            NARRATION_REPAIR_POSITION_MIN_WORDS, word_budget - NARRATION_REPAIR_POSITION_WORD_SLACK
        ),
        maximum,
    )


_MICRO_COMPACTION_RULES = (
    ("it is", "it's", "it_is_to_its"),
    ("does not", "doesn't", "does_not_to_doesnt"),
    ("he is", "he's", "he_is_to_hes"),
    ("she is", "she's", "she_is_to_shes"),
    ("it will", "it'll", "it_will_to_itll"),
    ("I will", "I'll", "i_will_to_ill"),
    ("you will", "you'll", "you_will_to_youll"),
    ("he will", "he'll", "he_will_to_hell"),
    ("she will", "she'll", "she_will_to_shell"),
    ("we will", "we'll", "we_will_to_well"),
    ("they will", "they'll", "they_will_to_theyll"),
    ("I would", "I'd", "i_would_to_id"),
    ("you would", "you'd", "you_would_to_youd"),
    ("he would", "he'd", "he_would_to_hed"),
    ("she would", "she'd", "she_would_to_shed"),
    ("we would", "we'd", "we_would_to_wed"),
    ("they would", "they'd", "they_would_to_theyd"),
    ("did not", "didn't", "did_not_to_didnt"),
    ("could not", "couldn't", "could_not_to_couldnt"),
    ("should not", "shouldn't", "should_not_to_shouldnt"),
    ("would not", "wouldn't", "would_not_to_wouldnt"),
    ("must not", "mustn't", "must_not_to_mustnt"),
    ("they are", "they're", "they_are_to_theyre"),
    ("we are", "we're", "we_are_to_were"),
    ("you are", "you're", "you_are_to_youre"),
    ("do not", "don't", "do_not_to_dont"),
    ("is not", "isn't", "is_not_to_isnt"),
    ("are not", "aren't", "are_not_to_arent"),
    ("was not", "wasn't", "was_not_to_wasnt"),
    ("were not", "weren't", "were_not_to_werent"),
    ("will not", "won't", "will_not_to_wont"),
    ("have not", "haven't", "have_not_to_havent"),
    ("has not", "hasn't", "has_not_to_hasnt"),
    ("there is", "there's", "there_is_to_theres"),
    ("that is", "that's", "that_is_to_thats"),
    ("what is", "what's", "what_is_to_whats"),
    ("I am", "I'm", "i_am_to_im"),
    ("I have", "I've", "i_have_to_ive"),
    ("we have", "we've", "we_have_to_weve"),
    ("they have", "they've", "they_have_to_theyve"),
    ("you have", "you've", "you_have_to_youve"),
    ("it would", "it'd", "it_would_to_itd"),
    ("that would", "that'd", "that_would_to_thatd"),
    ("there would", "there'd", "there_would_to_thered"),
    ("that will", "that'll", "that_will_to_thatll"),
    ("there will", "there'll", "there_will_to_therell"),
)

_MICRO_EXPANSION_RULES = (
    ("I'll", "I will", "ill_to_i_will"),
    ("you'll", "you will", "youll_to_you_will"),
    ("he'll", "he will", "hell_to_he_will"),
    ("she'll", "she will", "shell_to_she_will"),
    ("we'll", "we will", "well_to_we_will"),
    ("they'll", "they will", "theyll_to_they_will"),
    ("didn't", "did not", "didnt_to_did_not"),
    ("couldn't", "could not", "couldnt_to_could_not"),
    ("shouldn't", "should not", "shouldnt_to_should_not"),
    ("wouldn't", "would not", "wouldnt_to_would_not"),
    ("mustn't", "must not", "mustnt_to_must_not"),
    ("they're", "they are", "theyre_to_they_are"),
    ("we're", "we are", "were_to_we_are"),
    ("you're", "you are", "youre_to_you_are"),
    ("don't", "do not", "dont_to_do_not"),
    ("isn't", "is not", "isnt_to_is_not"),
    ("aren't", "are not", "arent_to_are_not"),
    ("wasn't", "was not", "wasnt_to_was_not"),
    ("weren't", "were not", "werent_to_were_not"),
    ("won't", "will not", "wont_to_will_not"),
    ("haven't", "have not", "havent_to_have_not"),
    ("hasn't", "has not", "hasnt_to_has_not"),
    ("I'm", "I am", "im_to_i_am"),
    ("I've", "I have", "ive_to_i_have"),
    ("we've", "we have", "weve_to_we_have"),
    ("they've", "they have", "theyve_to_they_have"),
    ("you've", "you have", "youve_to_you_have"),
    ("that'll", "that will", "thatll_to_that_will"),
    ("there'll", "there will", "therell_to_there_will"),
)



def _case_preserving_replacement(original: str, replacement: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _micro_compaction_metadata(
    rewrites: Sequence[str],
    *,
    before_word_count: int,
    operation_types: Sequence[str] = (),
    applied: bool,
    failed_predicate: str | None = None,
) -> dict[str, Any]:
    return {
        "version": NARRATION_MICRO_COMPACTION_VERSION,
        "applied": applied,
        "before_word_count": before_word_count,
        "after_word_count": sum(script.narration_word_count(text) for text in rewrites),
        "operation_count": len(operation_types),
        "operation_types": list(operation_types),
        "result_hash": _hash({"rewrites": list(rewrites)}),
        "failed_predicate": failed_predicate,
    }


def _micro_compact_rewrites(
    rewrites: Sequence[str],
    *,
    total_words: int,
) -> tuple[tuple[str, ...], dict[str, Any]]:
    """Apply only deterministic, meaning-preserving contractions in a narrow window."""

    original = tuple(rewrites)
    if total_words <= 125:
        return original, _micro_compaction_metadata(
            original,
            before_word_count=total_words,
            applied=False,
        )
    if (
        not NARRATION_MICRO_COMPACTION_MIN_WORDS
        <= total_words
        <= NARRATION_MICRO_COMPACTION_MAX_WORDS
    ):
        return original, _micro_compaction_metadata(
            original,
            before_word_count=total_words,
            applied=False,
            failed_predicate="micro_compaction_window",
        )

    current = list(original)
    operation_types: list[str] = []
    while sum(script.narration_word_count(text) for text in current) > 125:
        replaced = False
        for position, text in enumerate(current):
            for source, replacement, operation_type in _MICRO_COMPACTION_RULES:
                pattern = re.compile(r"\b" + re.escape(source) + r"\b", re.IGNORECASE)
                match = pattern.search(text)
                if match is None:
                    continue
                current[position] = (
                    text[: match.start()]
                    + _case_preserving_replacement(match.group(0), replacement)
                    + text[match.end() :]
                )
                operation_types.append(operation_type)
                replaced = True
                break
            if replaced:
                break
        if not replaced:
            break

    compacted = tuple(current)
    after_words = sum(script.narration_word_count(text) for text in compacted)
    failed_predicate = "micro_compaction_no_safe_operation" if after_words > 125 else None
    return compacted, _micro_compaction_metadata(
        compacted,
        before_word_count=total_words,
        operation_types=operation_types,
        applied=bool(operation_types),
        failed_predicate=failed_predicate,
    )


def _micro_expand_rewrites(
    rewrites: Sequence[str],
    *,
    total_words: int,
) -> tuple[tuple[str, ...], dict[str, Any]]:
    """Expand only unambiguous contractions to rescue a narrow undershoot."""

    original = tuple(rewrites)
    metadata = {
        "version": NARRATION_MICRO_EXPANSION_VERSION,
        "applied": False,
        "before_word_count": total_words,
        "after_word_count": total_words,
        "operation_count": 0,
        "operation_types": [],
        "result_hash": _hash({"rewrites": list(original)}),
        "failed_predicate": None,
    }
    if total_words >= 115:
        return original, metadata
    if not NARRATION_MICRO_EXPANSION_MIN_WORDS <= total_words <= NARRATION_MICRO_EXPANSION_MAX_WORDS:
        return original, {**metadata, "failed_predicate": "micro_expansion_window"}

    current = list(original)
    operation_types: list[str] = []
    while sum(script.narration_word_count(text) for text in current) < 115:
        replaced = False
        for position, text in enumerate(current):
            for source, replacement, operation_type in _MICRO_EXPANSION_RULES:
                pattern = re.compile(r"(?<![\w'])" + re.escape(source) + r"(?![\w'])", re.IGNORECASE)
                match = pattern.search(text)
                if match is None:
                    continue
                current[position] = (
                    text[: match.start()]
                    + _case_preserving_replacement(match.group(0), replacement)
                    + text[match.end() :]
                )
                operation_types.append(operation_type)
                replaced = True
                break
            if replaced:
                break
        if not replaced:
            break

    expanded = tuple(current)
    after_words = sum(script.narration_word_count(text) for text in expanded)
    return expanded, {
        "version": NARRATION_MICRO_EXPANSION_VERSION,
        "applied": bool(operation_types),
        "before_word_count": total_words,
        "after_word_count": after_words,
        "operation_count": len(operation_types),
        "operation_types": operation_types,
        "result_hash": _hash({"rewrites": list(expanded)}),
        "failed_predicate": (
            "micro_expansion_no_safe_operation" if after_words < 115 else None
        ),
    }


LOCKED_STORY_BUDGET_NORMALIZATION_VERSION = "locked-story-budget-normalize-v1"
_LOCKED_STORY_OPTIONAL_MODIFIER_RULES = (
    (r"\b(?:brown|blonde|dark|black|blue|red|white|long)[ -]haired\s+", "", "drop_hair_modifier"),
    (r"\b(?:glowing|bright|sudden|clear|clearly|nearby)\s+", "", "drop_optional_visual_modifier"),
    (r"\bonce more\b\s*", "", "drop_repeat_modifier"),
    (r"\bagain\b\s*", "", "drop_repeat_modifier"),
)
_LOCKED_STORY_SAFE_CEILING_TRIM_RULES = (
    (r"\b(?:bright|sudden|suddenly|clear|clearly|nearby)\s+", "", "drop_safe_optional_modifier"),
    (r"\bonce more\b\s*", "", "drop_safe_repeat_modifier"),
    (r"\bagain and again\b", "repeatedly", "compact_safe_repeat_phrase"),
)


def _normalize_locked_story_budget(
    rewrites: Sequence[str],
    raw_positions: Sequence[Mapping[str, Any]],
    registry: Mapping[str, Any],
) -> tuple[tuple[str, ...], dict[str, Any]]:
    """Normalize only tiny word skews after claim/evidence scope is immutable."""
    original = tuple(rewrites)
    metadata: dict[str, Any] = {
        "version": LOCKED_STORY_BUDGET_NORMALIZATION_VERSION,
        "applied": False,
        "operations": [],
        "failed_predicate": None,
    }
    if str(registry.get("provider_context_mode", "")) != "locked_story_text_only":
        return original, metadata
    budgets = registry.get("passage_word_budgets")
    targets = registry.get("passage_word_targets") or budgets
    contexts = registry.get("selected_story_context")
    if (
        not isinstance(budgets, Mapping)
        or not isinstance(targets, Mapping)
        or not isinstance(contexts, list)
    ):
        return original, {**metadata, "failed_predicate": "normalization_context_missing"}
    current = list(original)
    passage_indexes: dict[str, list[int]] = {}
    passage_order: list[str] = []
    for index, raw_position in enumerate(raw_positions):
        passage_id = str(raw_position.get("passage_id", ""))
        if not passage_id:
            return original, {**metadata, "failed_predicate": "normalization_position_invalid"}
        if passage_id not in passage_indexes:
            passage_indexes[passage_id] = []
            passage_order.append(passage_id)
        passage_indexes[passage_id].append(index)
    context_by_index = {
        int(item.get("passage_index", index)): item
        for index, item in enumerate(contexts)
        if isinstance(item, Mapping)
    }
    operations: list[dict[str, Any]] = []
    before_counts = {
        passage_id: script.narration_word_count(
            " ".join(current[index] for index in indexes)
        )
        for passage_id, indexes in passage_indexes.items()
    }
    separate_targets = isinstance(registry.get("passage_word_targets"), Mapping) and bool(
        registry.get("passage_word_targets")
    )
    if separate_targets:
        ceiling_counts = {str(key): int(value) for key, value in budgets.items()}
        target_counts = {str(key): int(value) for key, value in targets.items()}
        if set(before_counts) != set(ceiling_counts) or set(before_counts) != set(target_counts):
            return original, {
                **metadata,
                "before_counts": before_counts,
                "failed_predicate": "normalization_budget_missing",
            }
        current = list(original)
        operations: list[dict[str, Any]] = []
        for passage_index, passage_id in enumerate(passage_order):
            indexes = passage_indexes[passage_id]
            ceiling = ceiling_counts[passage_id]
            count = script.narration_word_count(" ".join(current[index] for index in indexes))
            if count <= ceiling:
                continue
            if count - ceiling > 3:
                return original, {
                    **metadata,
                    "before_counts": before_counts,
                    "target_counts": target_counts,
                    "ceiling_counts": ceiling_counts,
                    "failed_predicate": "normalization_ceiling_delta_window",
                }
            while count > ceiling:
                changed = False
                for rewrite_index in indexes:
                    text = current[rewrite_index]
                    for pattern, replacement, operation_type in _LOCKED_STORY_SAFE_CEILING_TRIM_RULES:
                        match = re.search(pattern, text, flags=re.IGNORECASE)
                        if match is None:
                            continue
                        candidate = text[: match.start()] + replacement + text[match.end() :]
                        candidate = re.sub(r"\s{2,}", " ", candidate).strip()
                        next_count = script.narration_word_count(
                            " ".join(
                                candidate if index == rewrite_index else current[index]
                                for index in indexes
                            )
                        )
                        if next_count >= count:
                            continue
                        current[rewrite_index] = candidate
                        operations.append({
                            "passage_index": passage_index,
                            "operation": operation_type,
                            "before_words": count,
                            "after_words": next_count,
                        })
                        count = next_count
                        changed = True
                        break
                    if changed:
                        break
                if not changed:
                    return original, {
                        **metadata,
                        "before_counts": before_counts,
                        "target_counts": target_counts,
                        "ceiling_counts": ceiling_counts,
                        "operations": operations,
                        "failed_predicate": "normalization_trim_unavailable",
                    }
        after_counts = {
            passage_id: script.narration_word_count(" ".join(current[index] for index in indexes))
            for passage_id, indexes in passage_indexes.items()
        }
        if any(after_counts[key] > ceiling_counts[key] for key in after_counts):
            return original, {
                **metadata,
                "before_counts": before_counts,
                "after_counts": after_counts,
                "target_counts": target_counts,
                "ceiling_counts": ceiling_counts,
                "operations": operations,
                "failed_predicate": "normalization_ceiling_mismatch",
            }
        return tuple(current), {
            **metadata,
            "applied": bool(operations),
            "operation_count": len(operations),
            "operations": operations,
            "before_counts": before_counts,
            "after_counts": after_counts,
            "target_counts": target_counts,
            "ceiling_counts": ceiling_counts,
        }
    for passage_index, passage_id in enumerate(passage_order):
        if passage_id not in budgets:
            return original, {**metadata, "failed_predicate": "normalization_budget_missing"}
        target = int(targets[passage_id])
        indexes = passage_indexes[passage_id]
        count = script.narration_word_count(" ".join(current[index] for index in indexes))
        delta = count - target
        if abs(delta) > 3:
            return original, {
                **metadata,
                "before_counts": before_counts,
                "failed_predicate": "normalization_delta_window",
            }
        while count > target:
            changed = False
            for rewrite_index in indexes:
                text = current[rewrite_index]
                for pattern, replacement, operation_type in _LOCKED_STORY_OPTIONAL_MODIFIER_RULES:
                    match = re.search(pattern, text, flags=re.IGNORECASE)
                    if match is None:
                        continue
                    candidate = text[: match.start()] + replacement + text[match.end() :]
                    candidate = re.sub(r"\s{2,}", " ", candidate).strip()
                    next_count = script.narration_word_count(
                        " ".join(
                            candidate if index == rewrite_index else current[index]
                            for index in indexes
                        )
                    )
                    if next_count < target:
                        continue
                    current[rewrite_index] = candidate
                    operations.append({
                        "passage_index": passage_index,
                        "operation": operation_type,
                        "before_words": count,
                        "after_words": next_count,
                    })
                    count = next_count
                    changed = True
                    break
                if changed:
                    break
            if not changed:
                return original, {
                    **metadata,
                    "before_counts": before_counts,
                    "operations": operations,
                    "failed_predicate": "normalization_trim_unavailable",
                }
        if count < target:
            if target - count != 1:
                return original, {
                    **metadata,
                    "before_counts": before_counts,
                    "operations": operations,
                    "failed_predicate": "normalization_expand_window",
                }
            context = context_by_index.get(passage_index, {})
            bridge = context.get("incoming_bridge", {}) if isinstance(context, Mapping) else {}
            kind = str(bridge.get("kind", "")) if isinstance(bridge, Mapping) else ""
            prefix = {"teaser_rewind": "Earlier", "temporal_only": "Later", "causal": "So"}.get(kind)
            if not prefix:
                return original, {
                    **metadata,
                    "before_counts": before_counts,
                    "operations": operations,
                    "failed_predicate": "normalization_bridge_unavailable",
                }
            first_index = indexes[0]
            if re.match(rf"^{re.escape(prefix)}\b", current[first_index], flags=re.IGNORECASE):
                return original, {
                    **metadata,
                    "before_counts": before_counts,
                    "operations": operations,
                    "failed_predicate": "normalization_bridge_duplicate",
                }
            base_text = current[first_index]
            if base_text.startswith(("The ", "A ", "An ")):
                base_text = base_text[0].lower() + base_text[1:]
            current[first_index] = f"{prefix}, {base_text}"
            count = script.narration_word_count(" ".join(current[index] for index in indexes))
            operations.append({
                "passage_index": passage_index,
                "operation": f"add_{prefix.lower()}_bridge",
                "before_words": count - 1,
                "after_words": count,
            })
            if count != target:
                return original, {
                    **metadata,
                    "before_counts": before_counts,
                    "operations": operations,
                    "failed_predicate": "normalization_expand_mismatch",
                }
    after_counts = {
        passage_id: script.narration_word_count(" ".join(current[index] for index in indexes))
        for passage_id, indexes in passage_indexes.items()
    }
    expected_counts = {str(key): int(value) for key, value in targets.items()}
    if after_counts != expected_counts:
        return original, {
            **metadata,
            "before_counts": before_counts,
            "after_counts": after_counts,
            "operations": operations,
            "failed_predicate": "normalization_exact_budget_mismatch",
        }
    return tuple(current), {
        **metadata,
        "applied": bool(operations),
        "operation_count": len(operations),
        "operations": operations,
        "before_counts": before_counts,
        "after_counts": after_counts,
    }


def _narration_repair_provider_prior_context(
    candidate: NarrationResult,
    *,
    locked_story_text_only: bool,
) -> dict[str, Any]:
    """Hide rejected prose from locked-story provider repair while retaining lineage."""
    if not locked_story_text_only:
        return candidate.as_dict()
    return {
        "ending_kind": candidate.ending_kind,
        "passages": [
            {
                key: passage[key]
                for key in (
                    "passage_id",
                    "editorial_role",
                    "claim_ids",
                    "evidence_panel_ids",
                )
                if key in passage
            }
            for passage in candidate.passages
        ],
        "spoken_text_omitted": True,
    }


NARRATION_REPAIR_INSTRUCTION = (
    "TARGETED NARRATION POSITION REPAIR: return exactly one JSON object with "
    '{"rewrites": ["text for position 0", "..."]}. '
    "The rewrites array must contain one revised spoken-text string for every ordered "
    "position supplied in the context; array index N maps to position N. Never return, "
    "create, or rewrite claim IDs, evidence panel IDs, slot IDs, passage IDs, observations, "
    "beat IDs, or hashes. Preserve the supplied causal order and evidence-grounded meaning. "
    "In normal repair mode, word_budget_min/word_budget_max are drafting guidance and aim "
    "for approximately 120 total words. In "
    "CAPACITY-LOCKED WORD BUDGET MODE, the supplied position word_budget values are drafting "
    "targets, not exact quotas; every word_budget_max and passage_word_budget_max is a hard ceiling. Natural concise prose may land below a target as long as it stays within the supplied aggregate word/duration contract; the "
    "positions belonging to each passage must remain exactly one complete retained passage. "
    "The local validator enforces exact vector shape/order, trusted lineage, and the supplied "
    "duration policy. Standard mode is total 115-125 words and 50-60 seconds; adaptive "
    "capacity-locked mode uses its supplied shorter word/duration bounds instead. Recount every position, "
    "every passage, and the complete vector before returning. Do not invent facts, add "
    "citations, copy dialogue, or return wrappers or metadata. Every rewrite must paraphrase "
    "dialogue into third-person narrator language; never quote or preserve a four-word lexical "
    "sequence from dialogue_or_ocr. Quotation marks, capitalization changes, or renaming a "
    "speaker are not loopholes. Describe only the grounded event or consequence. Every "
    "supplied context panel and section belongs to the exact retained passage/claim evidence "
    "closure; never mix a same-chapter panel from another section. Position 0 is the hook: "
    "write it as a curiosity-first grounded teaser around its supplied claim, not as a panel "
    "description or flat recap. Never open with we see, this panel, a man is, a woman is, "
    "then, next, or after that. Later positions must advance cause, tension, turn, or consequence. "
    "Write spoken narration, not report prose: use concise natural phrases and direct verbs. "
    "Never use bureaucratic filler such as 'during the course of', formal process labels such as "
    "'confrontation phase', padded endings such as 'central battle now' or 'phase now', or redundant "
    "constructions such as 'responding swing that followed' or 'counter swing that followed'. "
    "When a style-stiff failure is supplied, simplify the wording rather than replacing it with "
    "synonymous formal filler; keep every grounded fact and the exact evidence scope unchanged."
)

EDITORIAL_SELECTION_VERSION = "editorial-selection-v1"
EDITORIAL_SELECTION_TARGET_BEATS = 10
EDITORIAL_SELECTION_MAX_PANELS_PER_BEAT = 4
STAGE_PARALLEL_WORKERS = 4
DEFAULT_VISUAL_PARALLEL_WORKERS = 8
MAX_VISUAL_PARALLEL_WORKERS = 32


def _configured_visual_parallel_workers(explicit: int | None = None) -> int:
    """Resolve a bounded visual worker count without changing validation gates."""

    raw = (
        explicit
        if explicit is not None
        else os.environ.get(
            "MS_VISUAL_PARALLEL_WORKERS",
            str(DEFAULT_VISUAL_PARALLEL_WORKERS),
        )
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_VISUAL_PARALLEL_WORKERS
    return max(1, min(MAX_VISUAL_PARALLEL_WORKERS, value))


_REVIEW_ERROR_CODE_PATTERN = re.compile(
    r"\b(?:cloud|visual|reference|review|subtitle|render|ffmpeg|encoder|quality|audio|timeline|media)\.[a-z0-9_.-]+\b"
)


def _review_failure_code(message: str) -> str:
    """Extract only a known stable code from a local review-stage error."""

    match = _REVIEW_ERROR_CODE_PATTERN.search(str(message))
    return match.group(0) if match else "review.preview_failed"


def _peak_rss_kb() -> int | None:
    try:
        import resource

        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if sys.platform == "darwin":
            value //= 1024
        return value
    except (ImportError, AttributeError, OSError, ValueError):
        return None


class CloudStageError(RuntimeError):
    """Safe, machine-readable failure at the cloud stage boundary."""

    def __init__(
        self,
        code: str,
        message: str = "cloud stage failed",
        *,
        reviewable: bool = False,
        safe_metadata: Mapping[str, Any] | None = None,
    ):
        self.code = code
        self.reviewable = reviewable
        self.safe_metadata = dict(safe_metadata or {})
        super().__init__(message)


class ChapterState(StrEnum):
    INGESTED = "INGESTED"
    VISUAL_ANALYZED = "VISUAL_ANALYZED"
    STORY_MAPPED = "STORY_MAPPED"
    SCRIPTED = "SCRIPTED"
    READY_TO_RENDER = "READY_TO_RENDER"
    REVIEW_PREVIEW_READY = "REVIEW_PREVIEW_READY"
    RENDERED = "RENDERED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    WAITING_PROVIDER = "WAITING_PROVIDER"
    FAILED = "FAILED"


@dataclass(frozen=True)
class CloudModelIdentity:
    provider: str
    model: str
    model_version: str
    endpoint: str
    prompt_versions: Mapping[str, str]

    def __post_init__(self) -> None:
        values = dict(self.prompt_versions)
        if (
            not self.provider.strip()
            or not self.model.strip()
            or not self.model_version.strip()
            or not isinstance(self.endpoint, str)
            or not self.endpoint.strip()
        ):
            raise CloudStageError("cloud.model_identity_invalid")
        if not values or any(
            not str(key).strip() or not str(value).strip() for key, value in values.items()
        ):
            raise CloudStageError("cloud.model_identity_invalid")
        object.__setattr__(
            self, "prompt_versions", tuple(sorted((str(k), str(v)) for k, v in values.items()))
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "model_version": self.model_version,
            "endpoint": self.endpoint,
            "prompt_versions": dict(self.prompt_versions),
        }

    @property
    def identity_hash(self) -> str:
        return _hash(self.as_dict())


@dataclass(frozen=True)
class CloudPanelInput:
    panel_id: str
    source_asset_id: str
    source_order: int
    mime_type: str
    payload: bytes
    source_checksum: str = ""
    source_family: str = ""
    payload_checksum: str = ""
    panel_bounds: tuple[int, int, int, int] | None = None
    source_dimensions: tuple[int, int] | None = None
    strip_region_id: str = ""
    coverage_map_version: str = ""
    coverage_map_hash: str = ""
    segmentation_version: str = ""
    identity_payload_checksum: str = ""
    identity_descriptor_hash: str = ""
    source_identity_hash: str = ""
    metadata_only: bool = False
    prepared_order: int | None = None

    def __post_init__(self) -> None:
        if not self.panel_id.strip() or not self.source_asset_id.strip():
            raise CloudStageError("cloud.panel_lineage_invalid")
        if (
            isinstance(self.source_order, bool)
            or not isinstance(self.source_order, int)
            or self.source_order < 0
        ):
            raise CloudStageError("cloud.panel_lineage_invalid")
        if self.prepared_order is not None and (
            isinstance(self.prepared_order, bool)
            or not isinstance(self.prepared_order, int)
            or self.prepared_order < 0
        ):
            raise CloudStageError("cloud.panel_lineage_invalid")
        if not self.mime_type.lower().startswith("image/") or not self.payload:
            raise CloudStageError("cloud.panel_payload_invalid")
        if self.identity_payload_checksum and (
            len(self.identity_payload_checksum) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.identity_payload_checksum.lower()
            )
        ):
            raise CloudStageError("cloud.payload_checksum_mismatch")
        for _field_name, value in (
            ("identity_descriptor_hash", self.identity_descriptor_hash),
            ("source_identity_hash", self.source_identity_hash),
        ):
            if value and (
                len(value) != 64
                or any(character not in "0123456789abcdef" for character in value.lower())
            ):
                raise CloudStageError("cloud.prepared_manifest_invalid")
        if self.metadata_only and not self.identity_payload_checksum:
            raise CloudStageError("cloud.prepared_manifest_invalid")
        payload_checksum = hashlib.sha256(self.payload).hexdigest()
        if self.payload_checksum and self.payload_checksum != payload_checksum:
            raise CloudStageError("cloud.payload_checksum_mismatch")
        if self.panel_bounds is not None and (
            len(self.panel_bounds) != 4
            or not all(
                isinstance(value, int) and not isinstance(value, bool)
                for value in self.panel_bounds
            )
            or self.panel_bounds[0] < 0
            or self.panel_bounds[1] < 0
            or self.panel_bounds[2] <= self.panel_bounds[0]
            or self.panel_bounds[3] <= self.panel_bounds[1]
        ):
            raise CloudStageError("cloud.panel_lineage_invalid")
        if self.source_dimensions is not None:
            if (
                len(self.source_dimensions) != 2
                or not all(
                    isinstance(value, int) and not isinstance(value, bool)
                    for value in self.source_dimensions
                )
                or any(value <= 0 for value in self.source_dimensions)
            ):
                raise CloudStageError("cloud.panel_lineage_invalid")
            if self.panel_bounds is not None and (
                self.panel_bounds[2] > self.source_dimensions[0]
                or self.panel_bounds[3] > self.source_dimensions[1]
            ):
                raise CloudStageError("cloud.panel_lineage_invalid")
        if self.coverage_map_hash and len(self.coverage_map_hash) != 64:
            raise CloudStageError("cloud.panel_lineage_invalid")
        object.__setattr__(self, "payload_checksum", payload_checksum)
        if not self.source_checksum:
            object.__setattr__(self, "source_checksum", payload_checksum)

    def descriptor(self) -> dict[str, Any]:
        descriptor = {
            "panel_id": self.panel_id,
            "source_asset_id": self.source_asset_id,
            "source_order": self.source_order,
            "mime_type": self.mime_type,
            "source_checksum": self.source_checksum,
            "payload_checksum": self.payload_checksum,
        }
        if self.panel_bounds is not None:
            descriptor["panel_bounds"] = list(self.panel_bounds)
        if self.source_dimensions is not None:
            descriptor["source_dimensions"] = list(self.source_dimensions)
        if self.strip_region_id:
            descriptor["strip_region_id"] = self.strip_region_id
        if self.coverage_map_version:
            descriptor["coverage_map_version"] = self.coverage_map_version
        if self.coverage_map_hash:
            descriptor["coverage_map_hash"] = self.coverage_map_hash
        if self.segmentation_version:
            descriptor["segmentation_version"] = self.segmentation_version
        if self.identity_payload_checksum:
            descriptor["identity_payload_checksum"] = self.identity_payload_checksum
        if self.identity_descriptor_hash:
            descriptor["identity_descriptor_hash"] = self.identity_descriptor_hash
        if self.source_identity_hash:
            descriptor["source_identity_hash"] = self.source_identity_hash
        if self.metadata_only:
            descriptor["metadata_only"] = True
        if self.prepared_order is not None:
            descriptor["prepared_order"] = self.prepared_order
        return descriptor


PANEL_ADMISSION_VERSION = "panel-admission-v1"
PANEL_ADMISSION_DETECTOR_VERSION = "panel-admission-local-v1"


@dataclass(frozen=True)
class PanelAdmissionResult:
    """Deterministic, local-only admission result before any vision request."""

    admitted: tuple[CloudPanelInput, ...]
    ledger: dict[str, Any]

    @property
    def needs_review(self) -> bool:
        return bool(self.ledger.get("counts", {}).get("needs_review", 0))

    def as_dict(self) -> dict[str, Any]:
        return dict(self.ledger)


def _admission_value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _admission_bounds(value: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        return None
    if not all(isinstance(item, int) and not isinstance(item, bool) for item in value):
        return None
    bounds = tuple(value)
    if bounds[0] < 0 or bounds[1] < 0 or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        return None
    return bounds


def _admission_area(bounds: tuple[int, int, int, int]) -> int:
    return (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])


def _admission_intersection_area(
    left: tuple[int, int, int, int], right: tuple[int, int, int, int]
) -> int:
    x0 = max(left[0], right[0])
    y0 = max(left[1], right[1])
    x1 = min(left[2], right[2])
    y1 = min(left[3], right[3])
    return max(0, x1 - x0) * max(0, y1 - y0)


def _admission_decision(
    *,
    panel: CloudPanelInput | None,
    reason_code: str,
    action: str,
    candidate_panel_ids: Sequence[str] = (),
    metrics: Mapping[str, Any] | None = None,
    region: Any = None,
) -> dict[str, Any]:
    bounds = (
        panel.panel_bounds
        if panel is not None
        else _admission_bounds(_admission_value(region, "bounds"))
    )
    source_asset_id = (
        panel.source_asset_id
        if panel is not None
        else str(_admission_value(region, "source_asset_id", "") or "")
    )
    source_checksum = (
        panel.source_checksum
        if panel is not None
        else str(_admission_value(region, "source_checksum", "") or "")
    )
    panel_id = (
        panel.panel_id
        if panel is not None
        else str(_admission_value(region, "region_id", "") or "")
    )
    source_order = (
        panel.source_order if panel is not None else _admission_value(region, "source_order")
    )
    return {
        "action": action,
        "candidate_panel_ids": list(candidate_panel_ids),
        "metrics": dict(metrics or {}),
        "original_bounds": list(bounds) if bounds is not None else None,
        "panel_id": panel_id,
        "reason_code": reason_code,
        "source_asset_id": source_asset_id,
        "source_asset_checksum": source_checksum,
        "source_order": source_order,
    }


def _admission_region_key(region: Any) -> tuple[str, tuple[int, int, int, int]] | None:
    source_asset_id = str(_admission_value(region, "source_asset_id", "") or "")
    bounds = _admission_bounds(_admission_value(region, "bounds"))
    return (source_asset_id, bounds) if source_asset_id and bounds is not None else None


def _admission_panel_key(panel: CloudPanelInput) -> tuple[Any, ...]:
    return (
        panel.source_checksum,
        panel.payload_checksum,
        panel.panel_bounds,
        panel.source_dimensions,
    )


def _admission_order(panel: CloudPanelInput) -> tuple[int, str]:
    return (
        panel.prepared_order if panel.prepared_order is not None else panel.source_order,
        panel.panel_id,
    )


def _admission_transition(
    source: str,
    target: str,
    input_count: int,
    output_count: int,
    reason_code: str,
    started: float,
) -> dict[str, Any]:
    return {
        "from": source,
        "to": target,
        "input_count": input_count,
        "output_count": output_count,
        "elapsed_s": round(max(0.0, time.monotonic() - started), 6),
        "reason_code": reason_code,
    }


def admit_panel_inputs(
    panels: Sequence[CloudPanelInput],
    *,
    raw_image_count: int | None = None,
    ingest_asset_count: int | None = None,
    candidate_regions: Sequence[Any] = (),
    panel_hints: Mapping[str, Mapping[str, Any]] | None = None,
    merge_candidates: Sequence[Mapping[str, Any]] = (),
    detector_version: str = PANEL_ADMISSION_DETECTOR_VERSION,
) -> PanelAdmissionResult:
    """Run the local panel-admission funnel before provider vision.

    The funnel never asks a model to classify basic source geometry. It keeps
    a complete candidate-region ledger, rejects only deterministic non-panel
    evidence, deduplicates only identical/near-identical source lineage, and
    turns protected or ambiguous material into a review blocker.
    """

    started = time.monotonic()
    ordered = tuple(sorted(panels, key=_admission_order))
    panel_ids = [panel.panel_id for panel in ordered]
    if len(panel_ids) != len(set(panel_ids)):
        raise CloudStageError(
            "cloud.panel_admission_invalid",
            safe_metadata={
                "reason_code": "admission.duplicate_panel_id",
                "panel_count": len(panel_ids),
            },
        )
    prepared_orders = [
        panel.prepared_order for panel in ordered if panel.prepared_order is not None
    ]
    if len(prepared_orders) != len(set(prepared_orders)):
        raise CloudStageError(
            "cloud.panel_admission_invalid",
            safe_metadata={
                "reason_code": "admission.duplicate_prepared_order",
                "panel_count": len(panel_ids),
            },
        )

    hints = panel_hints or {}
    regions = tuple(candidate_regions)
    region_by_key: dict[tuple[str, tuple[int, int, int, int]], Any] = {}
    decisions: list[dict[str, Any]] = []
    coverage_manifest: list[dict[str, Any]] = []
    for region in regions:
        region_key = _admission_region_key(region)
        region_id = str(_admission_value(region, "region_id", "") or "")
        region_class = str(_admission_value(region, "region_class", "") or "")
        bounds = _admission_bounds(_admission_value(region, "bounds"))
        coverage_manifest.append(
            {
                "bounds": list(bounds) if bounds is not None else None,
                "confidence": _admission_value(region, "confidence"),
                "evidence": str(_admission_value(region, "evidence", "") or ""),
                "region_class": region_class,
                "region_id": region_id,
                "source_asset_checksum": str(_admission_value(region, "source_checksum", "") or ""),
                "source_asset_id": str(_admission_value(region, "source_asset_id", "") or ""),
                "source_order": _admission_value(region, "source_order"),
            }
        )
        if region_key is None:
            raise CloudStageError(
                "cloud.panel_admission_invalid",
                safe_metadata={
                    "reason_code": "admission.invalid_candidate_region",
                    "region_id": region_id,
                },
            )
        if region_key in region_by_key:
            raise CloudStageError(
                "cloud.panel_admission_invalid",
                safe_metadata={
                    "reason_code": "admission.duplicate_candidate_region",
                    "region_id": region_id,
                },
            )
        region_by_key[region_key] = region

    rejected_non_panel = 0
    deduped = 0
    needs_review = 0
    active: list[CloudPanelInput] = []

    for region in regions:
        region_class = str(_admission_value(region, "region_class", "") or "")
        if region_class == "canonical_panel":
            continue
        if region_class != "unresolved_material":
            rejected_non_panel += 1
        decisions.append(
            _admission_decision(
                panel=None,
                region=region,
                reason_code=(
                    "admission.non_panel_transition"
                    if region_class == "verified_gutter"
                    else "admission.unresolved_material"
                ),
                action="needs_review" if region_class == "unresolved_material" else "reject",
                candidate_panel_ids=(),
                metrics={"region_class": region_class},
            )
        )
        if region_class == "unresolved_material":
            needs_review += 1

    for panel in ordered:
        hint = dict(hints.get(panel.panel_id, {}))
        region = region_by_key.get((panel.source_asset_id, panel.panel_bounds))
        region_class = str(_admission_value(region, "region_class", "") or "")
        classification = str(
            hint.get("classification") or hint.get("trim_classification") or ""
        ).lower()
        panel_decision = str(hint.get("panel_decision", "") or "").lower()
        if panel_decision == "reject" and not classification:
            classification = "blank"
        protected = bool(hint.get("protected_regions") or hint.get("protected"))
        dialogue = bool(hint.get("dialogue_or_ocr") or hint.get("dialogue"))
        ambiguous = bool(hint.get("ambiguous") or region_class == "unresolved_material")
        story_evidence = hint.get("story_evidence", True)
        if panel_decision == "reject" and "story_evidence" not in hint:
            story_evidence = False
        if region_class == "verified_gutter" or classification in {"gutter", "transition"}:
            rejected_non_panel += 1
            decisions.append(
                _admission_decision(
                    panel=panel,
                    reason_code="admission.non_panel_transition",
                    action="reject",
                    metrics=dict(hint.get("metrics", {})),
                )
            )
            continue
        if ambiguous or (
            classification in {"blank", "near_blank", "title", "cover"} and story_evidence is False
        ):
            if protected or dialogue:
                needs_review += 1
                decisions.append(
                    _admission_decision(
                        panel=panel,
                        reason_code="admission.protected_or_dialogue_ambiguous",
                        action="needs_review",
                        metrics=dict(hint.get("metrics", {})),
                    )
                )
                continue
            rejected_non_panel += 1
            reason_code = {
                "title": "admission.title_no_story_evidence",
                "cover": "admission.cover_no_story_evidence",
                "blank": "admission.blank_no_story_evidence",
                "near_blank": "admission.near_blank_no_story_evidence",
            }.get(classification, "admission.unresolved_material")
            if panel_decision == "reject":
                reason_code = "admission.ingest_rejected_asset"
            decisions.append(
                _admission_decision(
                    panel=panel,
                    reason_code=reason_code,
                    action="reject",
                    metrics=dict(hint.get("metrics", {})),
                )
            )
            continue
        active.append(panel)

    kept: list[CloudPanelInput] = []
    exact_seen: dict[tuple[Any, ...], CloudPanelInput] = {}
    for panel in active:
        exact_key = _admission_panel_key(panel)
        prior = exact_seen.get(exact_key)
        if prior is not None:
            deduped += 1
            decisions.append(
                _admission_decision(
                    panel=panel,
                    reason_code="admission.exact_duplicate",
                    action="dedupe",
                    candidate_panel_ids=(prior.panel_id,),
                    metrics={"duplicate_of": prior.panel_id},
                )
            )
            continue
        near_prior = None
        if panel.panel_bounds is not None and panel.source_dimensions is not None:
            panel_area = _admission_area(panel.panel_bounds)
            for candidate in kept:
                if (
                    candidate.source_checksum != panel.source_checksum
                    or candidate.source_dimensions != panel.source_dimensions
                    or candidate.panel_bounds is None
                    or panel_area <= 0
                ):
                    continue
                overlap = _admission_intersection_area(panel.panel_bounds, candidate.panel_bounds)
                smaller_area = min(panel_area, _admission_area(candidate.panel_bounds))
                if smaller_area and overlap / smaller_area >= 0.98:
                    near_prior = candidate
                    break
        if near_prior is not None:
            deduped += 1
            decisions.append(
                _admission_decision(
                    panel=panel,
                    reason_code="admission.near_duplicate_crop",
                    action="dedupe",
                    candidate_panel_ids=(near_prior.panel_id,),
                    metrics={"overlap_of_smaller_area": 1.0},
                )
            )
            continue
        exact_seen[exact_key] = panel
        kept.append(panel)
        decisions.append(
            _admission_decision(
                panel=panel,
                reason_code="admission.admitted",
                action="admit",
            )
        )

    by_id = {panel.panel_id: panel for panel in kept}
    consumed: set[str] = set()
    merged_panels: list[CloudPanelInput] = []
    merged_count = 0
    for candidate in sorted(merge_candidates, key=lambda item: tuple(item.get("panel_ids", ()))):
        raw_ids = candidate.get("panel_ids", ())
        ids = tuple(str(item) for item in raw_ids) if isinstance(raw_ids, (tuple, list)) else ()
        merged_panel = candidate.get("merged_panel")
        source_panels = [by_id.get(panel_id) for panel_id in ids]
        if (
            len(ids) < 2
            or len(set(ids)) != len(ids)
            or any(panel is None for panel in source_panels)
            or not isinstance(merged_panel, CloudPanelInput)
            or not candidate.get("geometry_verified")
            or not candidate.get("protected_regions_preserved")
        ):
            if ids and all(panel_id in by_id for panel_id in ids):
                needs_review += 1
                decisions.append(
                    _admission_decision(
                        panel=by_id[ids[0]],
                        reason_code="admission.oversegmentation_ambiguous",
                        action="needs_review",
                        candidate_panel_ids=ids,
                    )
                )
            continue
        ordered_group = sorted(
            (panel for panel in source_panels if panel is not None), key=_admission_order
        )
        if any(
            panel.source_asset_id != ordered_group[0].source_asset_id
            or panel.source_checksum != ordered_group[0].source_checksum
            or panel.source_dimensions != ordered_group[0].source_dimensions
            or panel.panel_bounds is None
            for panel in ordered_group
        ):
            needs_review += 1
            decisions.append(
                _admission_decision(
                    panel=ordered_group[0],
                    reason_code="admission.oversegmentation_ambiguous",
                    action="needs_review",
                    candidate_panel_ids=ids,
                )
            )
            continue
        bounds = [panel.panel_bounds for panel in ordered_group]
        assert all(item is not None for item in bounds)
        sorted_bounds = sorted(bounds, key=lambda item: (item[1], item[0]))
        if any(
            left[0] != right[0] or left[2] != right[2] or left[3] != right[1]
            for left, right in zip(sorted_bounds, sorted_bounds[1:], strict=False)
        ):
            needs_review += 1
            decisions.append(
                _admission_decision(
                    panel=ordered_group[0],
                    reason_code="admission.oversegmentation_ambiguous",
                    action="needs_review",
                    candidate_panel_ids=ids,
                )
            )
            continue
        union_bounds = (
            sorted_bounds[0][0],
            sorted_bounds[0][1],
            sorted_bounds[0][2],
            sorted_bounds[-1][3],
        )
        if merged_panel.panel_bounds != union_bounds:
            needs_review += 1
            decisions.append(
                _admission_decision(
                    panel=ordered_group[0],
                    reason_code="admission.oversegmentation_ambiguous",
                    action="needs_review",
                    candidate_panel_ids=ids,
                )
            )
            continue
        consumed.update(ids)
        merged_panels.append(merged_panel)
        merged_count += len(ids) - 1
        decisions.append(
            _admission_decision(
                panel=merged_panel,
                reason_code="admission.oversegmentation_merged",
                action="merge",
                candidate_panel_ids=ids,
                metrics={"merged_bounds": list(union_bounds)},
            )
        )

    final_panels = [panel for panel in kept if panel.panel_id not in consumed]
    final_panels.extend(merged_panels)
    final_panels = sorted(final_panels, key=_admission_order)
    raw_count = len(panels) if raw_image_count is None else int(raw_image_count)
    ingest_count = (
        len({panel.source_asset_id for panel in panels})
        if ingest_asset_count is None
        else int(ingest_asset_count)
    )
    candidate_count = len(regions) if regions else len(panels)
    canonical_count = sum(
        str(_admission_value(region, "region_class", "") or "") == "canonical_panel"
        for region in regions
    ) or len(panels)
    counts = {
        "raw_input_images": raw_count,
        "ingest_assets": ingest_count,
        "candidate_regions": candidate_count,
        "canonical_regions": canonical_count,
        "admitted_vision_panels": 0 if needs_review else len(final_panels),
        "rejected_non_panel": rejected_non_panel,
        "deduped": deduped,
        "merged": merged_count,
        "needs_review": needs_review,
    }
    transitions = [
        _admission_transition(
            "raw_input_images",
            "ingest_outputs",
            raw_count,
            ingest_count,
            "admission.ingest_validated",
            started,
        ),
        _admission_transition(
            "ingest_outputs",
            "candidate_regions",
            ingest_count,
            candidate_count,
            "admission.candidates_reconciled",
            started,
        ),
        _admission_transition(
            "candidate_regions",
            "canonical_regions",
            candidate_count,
            canonical_count,
            "admission.non_panel_transition"
            if rejected_non_panel
            else "admission.canonical_regions",
            started,
        ),
        _admission_transition(
            "canonical_regions",
            "admitted_vision_panels",
            canonical_count,
            counts["admitted_vision_panels"],
            "admission.needs_review" if needs_review else "admission.vision_admitted",
            started,
        ),
    ]
    ledger = {
        "contract_version": PANEL_ADMISSION_VERSION,
        "detector_version": detector_version,
        "counts": counts,
        "coverage_manifest": coverage_manifest,
        "decisions": sorted(
            decisions,
            key=lambda item: (
                item.get("source_order") is None,
                item.get("source_order") if item.get("source_order") is not None else 0,
                str(item.get("panel_id", "")),
                str(item.get("reason_code", "")),
            ),
        ),
        "transitions": transitions,
        "reduction_percentages": {
            key: round((raw_count - value) / raw_count * 100, 3) if raw_count else 0.0
            for key, value in {
                "admitted_vision_panels": counts["admitted_vision_panels"],
                "rejected_non_panel": counts["rejected_non_panel"],
            }.items()
        },
        "reason_codes": sorted(
            {str(item.get("reason_code")) for item in decisions if item.get("reason_code")}
        ),
    }
    ledger["ledger_hash"] = _hash(ledger)
    return PanelAdmissionResult(tuple(final_panels) if not needs_review else (), ledger)


def panel_admission_failure_ledger(
    panels: Sequence[CloudPanelInput],
    *,
    raw_image_count: int,
    ingest_asset_count: int,
    candidate_regions: Sequence[Any],
    panel_hints: Mapping[str, Mapping[str, Any]] | None = None,
    detector_version: str = PANEL_ADMISSION_DETECTOR_VERSION,
    reason_code: str,
) -> dict[str, Any]:
    """Return a local funnel ledger when a pre-vision boundary blocks.

    Segmentation can fail after some source groups have already streamed to
    vision.  Preserve the deterministic admission counts and transition
    timings in that case, but force the terminal admission count to zero so a
    partial stream can never be mistaken for an accepted vision set.
    """

    result = admit_panel_inputs(
        panels,
        raw_image_count=raw_image_count,
        ingest_asset_count=ingest_asset_count,
        candidate_regions=candidate_regions,
        panel_hints=panel_hints,
        detector_version=detector_version,
    )
    ledger = dict(result.ledger)
    counts = dict(ledger["counts"])
    counts["admitted_vision_panels"] = 0
    ledger["counts"] = counts
    reductions = dict(ledger["reduction_percentages"])
    raw_count = int(counts["raw_input_images"])
    reductions["admitted_vision_panels"] = (
        round((raw_count - counts["admitted_vision_panels"]) / raw_count * 100, 3)
        if raw_count
        else 0.0
    )
    ledger["reduction_percentages"] = reductions
    transitions = [dict(item) for item in ledger["transitions"]]
    if transitions:
        terminal = transitions[-1]
        terminal["output_count"] = 0
        terminal["reason_code"] = reason_code
    ledger["transitions"] = transitions
    ledger["status"] = "BLOCKED"
    ledger["terminal_reason_code"] = reason_code
    ledger["blocked_before_provider_vision"] = True
    ledger["ledger_hash"] = _hash(
        {key: value for key, value in ledger.items() if key != "ledger_hash"}
    )
    return ledger


class CloudMultimodalProvider(Protocol):
    model_id: str

    def observe(self, request: VisionObservationRequest) -> list[Mapping[str, Any]]: ...

    def complete_json(
        self,
        *,
        stage: str,
        prompt_version: str,
        prompt_sha256: str,
        prompt_text: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


class StageCache(Protocol):
    def get(self, key: str) -> Mapping[str, Any] | None: ...

    def put(self, key: str, value: Mapping[str, Any]) -> None: ...


class MemoryStageCache:
    def __init__(self) -> None:
        self._values: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> Mapping[str, Any] | None:
        value = self._values.get(key)
        return json.loads(json.dumps(value)) if value is not None else None

    def put(self, key: str, value: Mapping[str, Any]) -> None:
        self._values[key] = json.loads(json.dumps(value))

    def delete(self, key: str) -> None:
        self._values.pop(key, None)

    def iter_records(self, *, cache_type: str | None = None):
        for value in self._values.values():
            if cache_type is None or value.get("cache_type") == cache_type:
                yield json.loads(json.dumps(value))


class FileStageCache:
    """Atomic JSON stage cache for restartable local production jobs."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def checkpoint_path(self) -> Path:
        return self.root / "visual_checkpoints.jsonl"

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.root / f"{digest}.json"

    def get(self, key: str) -> Mapping[str, Any] | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return None
        return value if isinstance(value, Mapping) else None

    def put(self, key: str, value: Mapping[str, Any]) -> None:
        path = self._path(key)
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(_canonical(value), encoding="utf-8")
        temporary.replace(path)

    def delete(self, key: str) -> None:
        with suppress(FileNotFoundError):
            self._path(key).unlink()

    def iter_records(self, *, cache_type: str | None = None):
        for path in sorted(self.root.glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError):
                continue
            if isinstance(value, Mapping) and (
                cache_type is None or value.get("cache_type") == cache_type
            ):
                yield value


@dataclass(frozen=True)
class VisualStageResult:
    panels: tuple[dict[str, Any], ...]
    source_hash: str
    model_identity_hash: str
    prompt_version: str
    prompt_sha256: str
    reconciled: bool = True
    cache_identity_version: str = "legacy-descriptor-v1"
    panel_identity_hashes: tuple[str, ...] = ()
    rejected_panels: tuple[dict[str, Any], ...] = ()
    panel_attempt_ledger: tuple[dict[str, Any], ...] = ()

    @property
    def panel_ids(self) -> tuple[str, ...]:
        return tuple(item["panel_id"] for item in self.panels)

    @property
    def visual_evidence_hash(self) -> str:
        return _hash(
            {
                "contract_version": "visual-evidence-stage-v1",
                "source_hash": self.source_hash,
                "model_identity_hash": self.model_identity_hash,
                "prompt_version": self.prompt_version,
                "prompt_sha256": self.prompt_sha256,
                "panels": [dict(item) for item in self.panels],
            }
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {
            "panels": [dict(item) for item in self.panels],
            "rejected_panels": [dict(item) for item in self.rejected_panels],
            "panel_attempt_ledger": [dict(item) for item in self.panel_attempt_ledger],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> VisualStageResult:
        return cls(
            panels=tuple(dict(item) for item in value["panels"]),
            source_hash=str(value["source_hash"]),
            model_identity_hash=str(value["model_identity_hash"]),
            prompt_version=str(value["prompt_version"]),
            prompt_sha256=str(value["prompt_sha256"]),
            reconciled=bool(value.get("reconciled", True)),
            cache_identity_version=str(value.get("cache_identity_version", "legacy-descriptor-v1")),
            panel_identity_hashes=tuple(
                str(item) for item in value.get("panel_identity_hashes", ())
            ),
            rejected_panels=tuple(
                dict(item) for item in value.get("rejected_panels", ()) if isinstance(item, Mapping)
            ),
            panel_attempt_ledger=tuple(
                dict(item)
                for item in value.get("panel_attempt_ledger", ())
                if isinstance(item, Mapping)
            ),
        )


@dataclass(frozen=True)
class StoryMapResult:
    panel_ids: tuple[str, ...]
    beats: tuple[dict[str, Any], ...]
    causal_chain: tuple[dict[str, Any], ...]
    claims: tuple[dict[str, Any], ...]
    story_map_hash: str
    model_identity_hash: str
    prompt_version: str
    prompt_sha256: str
    visual_evidence_hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "panel_ids": list(self.panel_ids),
            "beats": [dict(item) for item in self.beats],
            "causal_chain": [dict(item) for item in self.causal_chain],
            "claims": [dict(item) for item in self.claims],
            "story_map_hash": self.story_map_hash,
            "model_identity_hash": self.model_identity_hash,
            "prompt_version": self.prompt_version,
            "prompt_sha256": self.prompt_sha256,
            "visual_evidence_hash": self.visual_evidence_hash,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StoryMapResult:
        return cls(
            panel_ids=tuple(value["panel_ids"]),
            beats=tuple(dict(item) for item in value["beats"]),
            causal_chain=tuple(dict(item) for item in value["causal_chain"]),
            claims=tuple(dict(item) for item in value["claims"]),
            story_map_hash=str(value["story_map_hash"]),
            model_identity_hash=str(value["model_identity_hash"]),
            prompt_version=str(value["prompt_version"]),
            prompt_sha256=str(value["prompt_sha256"]),
            visual_evidence_hash=str(value.get("visual_evidence_hash", "")),
        )


@dataclass(frozen=True)
class NarrationResult:
    spoken_text: str
    display_words: tuple[str, ...]
    passages: tuple[dict[str, Any], ...]
    ending_kind: str
    word_count: int
    estimated_duration_s: float
    qc_report: dict[str, Any]
    model_identity_hash: str
    prompt_version: str
    prompt_sha256: str
    requires_voice_timing: bool = True
    observations: tuple[dict[str, Any], ...] = ()
    continuity_ledger: dict[str, Any] = field(default_factory=dict)
    evidence_graph: dict[str, Any] = field(default_factory=dict)
    story_spine: dict[str, Any] = field(default_factory=dict)
    visual_evidence_hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {
            "display_words": list(self.display_words),
            "passages": [dict(item) for item in self.passages],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> NarrationResult:
        qc_report = dict(value["qc_report"])
        signals = dict(qc_report.get("signals", {}))
        for key in ("generic_hype_hits", "cta_hits", "warnings"):
            if key in signals:
                signals[key] = tuple(signals[key])
        if "signals" in qc_report:
            qc_report["signals"] = signals
        return cls(
            spoken_text=str(value["spoken_text"]),
            display_words=tuple(value["display_words"]),
            passages=tuple(dict(item) for item in value["passages"]),
            ending_kind=str(value["ending_kind"]),
            word_count=int(value["word_count"]),
            estimated_duration_s=float(value["estimated_duration_s"]),
            qc_report=qc_report,
            model_identity_hash=str(value["model_identity_hash"]),
            prompt_version=str(value["prompt_version"]),
            prompt_sha256=str(value["prompt_sha256"]),
            requires_voice_timing=bool(value.get("requires_voice_timing", True)),
            observations=tuple(dict(item) for item in value.get("observations", [])),
            continuity_ledger=dict(value.get("continuity_ledger", {})),
            evidence_graph=dict(value.get("evidence_graph", {})),
            story_spine=dict(value.get("story_spine", {})),
            visual_evidence_hash=str(value.get("visual_evidence_hash", "")),
        )


@dataclass(frozen=True)
class NarrationRepairSlot:
    """Locally trusted immutable identity for one repairable passage."""

    slot_id: str
    passage_id: str
    claim_ids: tuple[str, ...]
    evidence_panel_ids: tuple[str, ...]
    beat_id: str
    causal_position: int
    priority: int
    removable: bool

    def __post_init__(self) -> None:
        if (
            not self.slot_id.startswith("narration_slot_v1_")
            or not self.passage_id.strip()
            or not self.claim_ids
            or not self.evidence_panel_ids
            or not self.beat_id.strip()
            or isinstance(self.causal_position, bool)
            or not isinstance(self.causal_position, int)
            or self.causal_position < 0
            or isinstance(self.priority, bool)
            or not isinstance(self.priority, int)
            or not isinstance(self.removable, bool)
        ):
            raise CloudStageError("cloud.narrative_repair_slot_lineage_invalid", reviewable=True)
        if (
            any(not isinstance(value, str) or not value.strip() for value in self.claim_ids)
            or any(
                not isinstance(value, str) or not value.strip() for value in self.evidence_panel_ids
            )
            or len(set(self.claim_ids)) != len(self.claim_ids)
            or len(set(self.evidence_panel_ids)) != len(self.evidence_panel_ids)
        ):
            raise CloudStageError("cloud.narrative_repair_slot_lineage_invalid", reviewable=True)

    def as_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "passage_id": self.passage_id,
            "claim_ids": list(self.claim_ids),
            "evidence_panel_ids": list(self.evidence_panel_ids),
            "beat_id": self.beat_id,
            "causal_position": self.causal_position,
            "priority": self.priority,
            "removable": self.removable,
        }


@dataclass(frozen=True)
class NarrationRepairPosition:
    """Trusted claim-level rewrite position; provider never owns its identity."""

    position: int
    slot_id: str
    passage_id: str
    claim_ids: tuple[str, ...]
    evidence_panel_ids: tuple[str, ...]
    beat_id: str
    causal_position: int
    priority: int
    removable: bool
    word_budget: int
    word_budget_min: int = 0
    word_budget_max: int = 0

    def __post_init__(self) -> None:
        if (
            isinstance(self.position, bool)
            or not isinstance(self.position, int)
            or self.position < 0
            or not self.slot_id.startswith("narration_position_v1_")
            or not self.passage_id.strip()
            or not self.claim_ids
            or not self.evidence_panel_ids
            or not self.beat_id.strip()
            or isinstance(self.causal_position, bool)
            or not isinstance(self.causal_position, int)
            or self.causal_position < 0
            or isinstance(self.priority, bool)
            or not isinstance(self.priority, int)
            or not isinstance(self.removable, bool)
            or isinstance(self.word_budget, bool)
            or not isinstance(self.word_budget, int)
            or self.word_budget <= 0
            or (self.word_budget_min and self.word_budget_min <= 0)
            or (self.word_budget_max and self.word_budget_max <= 0)
            or (
                self.word_budget_min
                and self.word_budget_max
                and self.word_budget_min > self.word_budget_max
            )
            or any(not isinstance(value, str) or not value.strip() for value in self.claim_ids)
            or any(
                not isinstance(value, str) or not value.strip() for value in self.evidence_panel_ids
            )
            or len(set(self.claim_ids)) != len(self.claim_ids)
            or len(set(self.evidence_panel_ids)) != len(self.evidence_panel_ids)
        ):
            raise CloudStageError(
                "cloud.narrative_repair_position_selection_invalid", reviewable=True
            )
        default_min, default_max = _position_word_budget_bounds(self.word_budget)
        if not self.word_budget_min:
            object.__setattr__(self, "word_budget_min", default_min)
        if not self.word_budget_max:
            object.__setattr__(self, "word_budget_max", default_max)

    def as_dict(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "slot_id": self.slot_id,
            "passage_id": self.passage_id,
            "claim_ids": list(self.claim_ids),
            "evidence_panel_ids": list(self.evidence_panel_ids),
            "beat_id": self.beat_id,
            "causal_position": self.causal_position,
            "priority": self.priority,
            "removable": self.removable,
            "word_budget": self.word_budget,
            "word_budget_min": self.word_budget_min,
            "word_budget_max": self.word_budget_max,
        }


@dataclass(frozen=True)
class ChapterResult:
    state: ChapterState
    visual: VisualStageResult
    story_map: StoryMapResult
    narration: NarrationResult

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "visual": self.visual.as_dict(),
            "story_map": self.story_map.as_dict(),
            "narration": self.narration.as_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ChapterResult:
        return cls(
            state=ChapterState(str(value["state"])),
            visual=VisualStageResult.from_dict(value["visual"]),
            story_map=StoryMapResult.from_dict(value["story_map"]),
            narration=NarrationResult.from_dict(value["narration"]),
        )


@dataclass(frozen=True)
class ReviewOnlyRenderGate:
    allowed: bool
    audio_path: None = None
    music_path: None = None
    timing_source: str = "voice_required"
    publish_allowed: bool = False
    reason_code: str = "cloud.voice_timing_required"


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


_NARRATION_REPAIR_IDENTITY_SECTIONS = (
    "panel_lineage",
    "model",
    "prompt",
    "story",
    "selection",
    "slot_registry",
    "candidate",
)
_NARRATION_REPAIR_IDENTITY_IGNORED_FIELDS = frozenset({"prepared_order"})


def _canonical_repair_identity(value: object, *, key: str = "") -> object:
    """Normalize only representation-only fields for repair identity comparison."""

    if isinstance(value, Mapping):
        return {
            str(name): _canonical_repair_identity(item, key=str(name))
            for name, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(name) not in _NARRATION_REPAIR_IDENTITY_IGNORED_FIELDS
        }
    if isinstance(value, (list, tuple)):
        items = [_canonical_repair_identity(item, key=key) for item in value]
        if key == "panels" and all(isinstance(item, Mapping) for item in items):
            return sorted(items, key=lambda item: str(item.get("panel_id", "")))
        return items
    return value


def _repair_identity_counts(value: Mapping[str, Any]) -> dict[str, int]:
    panel_lineage = value.get("panel_lineage", {})
    story = value.get("story", {})
    slots = value.get("slot_registry", {})

    def _count(section: Mapping[str, Any], field: str) -> int:
        item = section.get(field, ()) if isinstance(section, Mapping) else ()
        return len(item) if isinstance(item, (list, tuple)) else 0

    return {
        "panel_count": _count(panel_lineage, "ordered_panel_ids"),
        "beat_count": int(story.get("beat_count", _count(story, "beat_ids")))
        if isinstance(story, Mapping)
        else 0,
        "claim_count": int(story.get("claim_count", _count(story, "claim_ids")))
        if isinstance(story, Mapping)
        else 0,
        "slot_count": _count(slots, "slot_ids"),
    }


def _repair_identity_shape_error(field: str) -> CloudStageError:
    return CloudStageError(
        "cloud.narrative_repair_identity_mismatch",
        reviewable=True,
        safe_metadata={
            "status": "rejected",
            "mismatch_field": field,
            "policy_version": NARRATION_REPAIR_IDENTITY_VERSION,
        },
    )


def _validate_repair_identity_shape(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise _repair_identity_shape_error("identity")
    if value.get("policy_version") != NARRATION_REPAIR_IDENTITY_VERSION:
        raise _repair_identity_shape_error("policy_version")
    for section in _NARRATION_REPAIR_IDENTITY_SECTIONS:
        if not isinstance(value.get(section), Mapping):
            raise _repair_identity_shape_error(section)
    panel_lineage = value["panel_lineage"]
    panel_ids = panel_lineage.get("ordered_panel_ids")
    panel_hashes = panel_lineage.get("panel_identity_hashes")
    if (
        not isinstance(panel_ids, (list, tuple))
        or not panel_ids
        or len({str(item) for item in panel_ids}) != len(panel_ids)
        or any(not str(item).strip() for item in panel_ids)
        or not isinstance(panel_hashes, (list, tuple))
        or len(panel_hashes) != len(panel_ids)
        or not str(panel_lineage.get("visual_evidence_hash", "")).strip()
    ):
        raise _repair_identity_shape_error("panel_lineage")
    model = value["model"]
    prompt = value["prompt"]
    if not str(model.get("identity_hash", "")).strip():
        raise _repair_identity_shape_error("model.identity_hash")
    if not str(prompt.get("version", "")).strip() or not str(prompt.get("sha256", "")).strip():
        raise _repair_identity_shape_error("prompt")
    story = value["story"]
    if not all(
        str(story.get(field, "")).strip()
        for field in ("beats_hash", "claims_hash", "causal_chain_hash", "story_map_hash")
    ):
        raise _repair_identity_shape_error("story")
    selection = value["selection"]
    if (
        not all(
            isinstance(selection.get(field), (list, tuple))
            for field in ("beat_ids", "panel_ids", "claim_ids")
        )
        or not str(selection.get("selection_hash", "")).strip()
    ):
        raise _repair_identity_shape_error("selection")
    slots = value["slot_registry"]
    if (
        not all(
            isinstance(slots.get(field), (list, tuple))
            for field in ("slot_ids", "claim_ids", "evidence_panel_ids")
        )
        or not str(slots.get("slot_order_hash", "")).strip()
    ):
        raise _repair_identity_shape_error("slot_registry")
    candidate = value["candidate"]
    if not all(
        str(candidate.get(field, "")).strip()
        for field in (
            "candidate_hash",
            "visual_evidence_hash",
            "model_identity_hash",
            "prompt_version",
            "prompt_sha256",
            "story_map_hash",
        )
    ):
        raise _repair_identity_shape_error("candidate")


def _first_repair_identity_difference(
    old: object,
    new: object,
    path: tuple[str, ...] = (),
) -> str:
    if isinstance(old, Mapping) and isinstance(new, Mapping):
        for key in sorted(set(old) | set(new), key=str):
            if key not in old or key not in new:
                return ".".join((*path, str(key)))
            difference = _first_repair_identity_difference(old[key], new[key], (*path, str(key)))
            if difference:
                return difference
        return ""
    if isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            return ".".join(path)
        for index, (old_item, new_item) in enumerate(zip(old, new, strict=True)):
            difference = _first_repair_identity_difference(old_item, new_item, (*path, str(index)))
            if difference:
                return difference
        return ""
    return "" if old == new else ".".join(path)


def reconcile_narration_repair_identity(
    old_metadata: Mapping[str, Any],
    current_metadata: Mapping[str, Any],
    *,
    old_identity_hash: str | None = None,
    new_identity_hash: str | None = None,
    reason: str,
) -> dict[str, Any]:
    """Compare cache dependencies without accepting provider prose or IDs."""

    _validate_repair_identity_shape(old_metadata)
    _validate_repair_identity_shape(current_metadata)
    old_canonical = _canonical_repair_identity(old_metadata)
    current_canonical = _canonical_repair_identity(current_metadata)
    assert isinstance(old_canonical, Mapping)
    assert isinstance(current_canonical, Mapping)
    counts = _repair_identity_counts(old_canonical)
    current_counts = _repair_identity_counts(current_canonical)
    count_record = {
        **{f"old_{key}": value for key, value in counts.items()},
        **{f"new_{key}": value for key, value in current_counts.items()},
    }
    comparison_hash = _hash({"old": old_canonical, "new": current_canonical})
    if old_canonical != current_canonical:
        mismatch_field = _first_repair_identity_difference(old_canonical, current_canonical)
        metadata = {
            "status": "rejected",
            "policy_version": NARRATION_REPAIR_IDENTITY_VERSION,
            "mismatch_field": mismatch_field or "identity",
            "canonical_comparison_hash": comparison_hash,
            "counts": count_record,
            "reason": str(reason),
        }
        raise CloudStageError(
            "cloud.narrative_repair_identity_mismatch",
            reviewable=True,
            safe_metadata=metadata,
        )
    return {
        "cache_type": NARRATION_REPAIR_IDENTITY_MIGRATION_VERSION,
        "status": "migrated",
        "policy_version": NARRATION_REPAIR_IDENTITY_VERSION,
        "old_identity_hash": old_identity_hash or _hash(old_metadata),
        "new_identity_hash": new_identity_hash or _hash(current_metadata),
        "canonical_comparison_hash": comparison_hash,
        "counts": count_record,
        "reason": str(reason),
    }


def persist_narration_repair_identity_migration(
    cache: StageCache,
    old_metadata: Mapping[str, Any],
    current_metadata: Mapping[str, Any],
    *,
    old_identity_hash: str,
    new_identity_hash: str,
    model_identity_hash: str,
    prompt_version: str,
    prompt_sha256: str,
    reason: str,
) -> dict[str, Any]:
    """Persist one metadata-only migration record and reuse it on warm resume."""

    record = reconcile_narration_repair_identity(
        old_metadata,
        current_metadata,
        old_identity_hash=old_identity_hash,
        new_identity_hash=new_identity_hash,
        reason=reason,
    )
    key = "narration-repair-identity-migration:" + _hash(
        {
            "old_identity_hash": old_identity_hash,
            "new_identity_hash": new_identity_hash,
            "model_identity_hash": model_identity_hash,
            "prompt_version": prompt_version,
            "prompt_sha256": prompt_sha256,
        }
    )
    existing = cache.get(key)
    if isinstance(existing, Mapping):
        return dict(existing)
    stored = {
        **record,
        "model_identity_hash": model_identity_hash,
        "prompt_version": prompt_version,
        "prompt_sha256": prompt_sha256,
    }
    cache.put(key, stored)
    return stored


def _continuity_ledger_is_valid(
    continuity_ledger: object,
    expected_panel_ids: Sequence[str],
) -> bool:
    expected = tuple(str(panel_id) for panel_id in expected_panel_ids)
    if not expected or len(set(expected)) != len(expected):
        return False
    try:
        analyzer_contract._validate_continuity(continuity_ledger, expected)
    except (
        analyzer_contract.AnalyzerContractError,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        return False
    return True


def _narration_continuity_is_valid(result: NarrationResult) -> bool:
    try:
        observation_ids = tuple(str(item.get("panel_id", "")) for item in result.observations)
    except (AttributeError, TypeError):
        return False
    return _continuity_ledger_is_valid(result.continuity_ledger, observation_ids)


def _reconcile_narration_full_scope(
    result: NarrationResult,
    *,
    observations: Sequence[Mapping[str, Any]],
    structural: Mapping[str, Any],
    expected_panel_ids: Sequence[str],
    visual_evidence_hash: str,
) -> NarrationResult:
    expected = tuple(str(panel_id) for panel_id in expected_panel_ids)
    full_observations = tuple(dict(item) for item in observations)
    observed = tuple(str(item.get("panel_id", "")) for item in full_observations)
    if observed != expected or len(set(observed)) != len(observed):
        raise CloudStageError("cloud.panel_lineage_invalid")
    continuity_ledger = structural.get("continuity_ledger")
    if not _continuity_ledger_is_valid(continuity_ledger, expected):
        raise CloudStageError("cloud.panel_coverage_incomplete")
    assert isinstance(continuity_ledger, Mapping)
    return replace(
        result,
        observations=full_observations,
        continuity_ledger=dict(continuity_ledger),
        visual_evidence_hash=visual_evidence_hash,
    )


def _narration_repair_contract_bounds(
    value: Mapping[str, Any] | None,
) -> dict[str, Any]:
    default = {
        "version": "standard_50_60_v1",
        "adaptive": False,
        "target_word_min": 115,
        "target_word_goal": 120,
        "target_word_max": 125,
        "target_duration_min_s": STANDARD_FINAL_DURATION_MIN_SECONDS,
        "target_duration_max_s": STANDARD_FINAL_DURATION_MAX_SECONDS,
    }
    if value is None:
        return default
    if not isinstance(value, Mapping):
        raise ValueError("narration repair duration contract must be a mapping")
    try:
        normalized = {
            "version": str(value["version"]),
            "adaptive": bool(value.get("adaptive", False)),
            "target_word_min": int(value["target_word_min"]),
            "target_word_goal": int(value.get("target_word_goal", value["target_word_min"])),
            "target_word_max": int(value["target_word_max"]),
            "target_duration_min_s": float(value["target_duration_min_s"]),
            "target_duration_max_s": float(value["target_duration_max_s"]),
        }
    except (KeyError, TypeError, ValueError):
        raise ValueError("narration repair duration contract is malformed") from None
    if (
        normalized["target_word_min"] <= 0
        or not normalized["target_word_min"] <= normalized["target_word_goal"] <= normalized["target_word_max"]
        or normalized["target_duration_min_s"] <= 0
        or normalized["target_duration_min_s"] > normalized["target_duration_max_s"]
    ):
        raise ValueError("narration repair duration contract is invalid")
    return normalized


def _narration_result_is_usable(
    result: NarrationResult,
    visual: VisualStageResult,
    *,
    require_duration: bool,
    require_grounding: bool = False,
    duration_policy_contract: Mapping[str, Any] | None = None,
) -> bool:
    """Reject stale or incomplete narration caches before repair/render."""

    try:
        if result.visual_evidence_hash != visual.visual_evidence_hash:
            return False
        if len(result.observations) != len(visual.panels):
            return False
        if not result.spoken_text.strip() or not result.display_words:
            return False
        if not 4 <= len(result.passages) <= 6:
            return False
        if int(result.word_count) <= 0 and not re.findall(r"[A-Za-z0-9]+", result.spoken_text):
            return False
        if require_duration:
            bounds = _narration_repair_contract_bounds(duration_policy_contract)
            duration_metrics = script.narration_duration_metrics(
                result.spoken_text,
                "dramatic",
            )
            canonical_duration = float(duration_metrics["estimated_duration_s"])
            canonical_word_count = int(duration_metrics["word_count"])
            if not bounds["target_duration_min_s"] <= canonical_duration <= bounds["target_duration_max_s"] or not math.isclose(
                float(result.estimated_duration_s),
                canonical_duration,
                rel_tol=0.0,
                abs_tol=0.001,
            ):
                return False
            if (
                not bounds["target_word_min"] <= canonical_word_count <= bounds["target_word_max"]
                or int(result.word_count) != canonical_word_count
            ):
                return False
            expected_contract = script.narration_duration_contract("dramatic")
            stored_contract = result.qc_report.get("duration_contract", {})
            if not isinstance(stored_contract, Mapping) or any(
                stored_contract.get(key) != value for key, value in expected_contract.items()
            ):
                return False
        if require_grounding:
            expected_display = tuple(re.findall(r"[A-Z0-9]+", result.spoken_text.upper()))
            if tuple(str(word) for word in result.display_words) != expected_display:
                return False
            observation_ids = tuple(str(item.get("panel_id", "")) for item in result.observations)
            visual_ids = tuple(str(item.get("panel_id", "")) for item in visual.panels)
            if observation_ids != visual_ids:
                return False
            if not _narration_continuity_is_valid(result):
                return False
            claims = result.evidence_graph.get("claims", ())
            if not isinstance(claims, (list, tuple)) or not claims:
                return False
            panel_ids = set(observation_ids)
            claim_map: dict[str, Mapping[str, Any]] = {}
            for claim in claims:
                if not isinstance(claim, Mapping):
                    return False
                claim_id = str(claim.get("claim_id", "")).strip()
                refs = claim.get("evidence_panel_ids", claim.get("panel_ids", ()))
                if not claim_id or claim_id in claim_map or not refs:
                    return False
                if any(str(panel_id) not in panel_ids for panel_id in refs):
                    return False
                claim_map[claim_id] = claim
            referenced_claim_ids: set[str] = set()
            for passage in result.passages:
                if not isinstance(passage, Mapping):
                    return False
                passage_claims = passage.get("claim_ids", ())
                passage_refs = passage.get("evidence_panel_ids", ())
                if not passage_claims or not passage_refs:
                    return False
                if any(str(claim_id) not in claim_map for claim_id in passage_claims):
                    return False
                if any(str(panel_id) not in panel_ids for panel_id in passage_refs):
                    return False
                referenced_claim_ids.update(str(claim_id) for claim_id in passage_claims)
            if referenced_claim_ids != set(claim_map):
                return False
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False
    return True


@dataclass(frozen=True)
class EditorialSelection:
    """Deterministic, panel-keyed beats selected after full chapter analysis."""

    beat_ids: tuple[str, ...]
    panel_ids: tuple[str, ...]
    claim_ids: tuple[str, ...]
    beat_scores: tuple[dict[str, Any], ...]
    selection_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": EDITORIAL_SELECTION_VERSION,
            "target_beats": EDITORIAL_SELECTION_TARGET_BEATS,
            "beat_ids": list(self.beat_ids),
            "panel_ids": list(self.panel_ids),
            "claim_ids": list(self.claim_ids),
            "beat_scores": [dict(item) for item in self.beat_scores],
            "selection_hash": self.selection_hash,
        }


def select_editorial_beats(
    visual: VisualStageResult,
    story_map: StoryMapResult,
    *,
    target_count: int = EDITORIAL_SELECTION_TARGET_BEATS,
) -> EditorialSelection:
    """Select 8-12 chronologically distributed strong beats without sampling."""

    if isinstance(target_count, bool) or not isinstance(target_count, int):
        raise CloudStageError("cloud.editorial_selection_invalid")
    target = max(8, min(12, target_count))
    panel_order = {
        str(panel.get("panel_id")): index
        for index, panel in enumerate(visual.panels)
        if isinstance(panel, Mapping) and str(panel.get("panel_id", "")).strip()
    }
    if not panel_order:
        raise CloudStageError("cloud.editorial_selection_invalid")
    claims = tuple(
        claim
        for claim in (getattr(story_map, "claims", ()) or ())
        if isinstance(claim, Mapping) and str(claim.get("claim_id", "")).strip()
    )
    beat_rows: list[dict[str, Any]] = []
    for raw_beat in story_map.beats:
        if not isinstance(raw_beat, Mapping):
            continue
        beat_id = str(raw_beat.get("beat_id", "")).strip()
        raw_panel_ids = raw_beat.get("panel_ids", ())
        if not beat_id or not isinstance(raw_panel_ids, (list, tuple)):
            continue
        panel_ids = tuple(
            str(panel_id) for panel_id in raw_panel_ids if str(panel_id) in panel_order
        )
        if not panel_ids:
            continue
        panel_set = set(panel_ids)
        beat_claims = []
        for claim in claims:
            refs = claim.get("evidence_panel_ids", claim.get("panel_ids", ()))
            if isinstance(refs, (list, tuple)) and panel_set.intersection(str(ref) for ref in refs):
                beat_claims.append(claim)
        first_order = min(panel_order[panel_id] for panel_id in panel_ids)
        state_changes = raw_beat.get("state_changes", ())
        causal_turns = raw_beat.get("causal_links", raw_beat.get("causal_turns", ()))
        score = (
            len(beat_claims) * 100
            + (len(state_changes) if isinstance(state_changes, (list, tuple)) else 0) * 5
            + (len(causal_turns) if isinstance(causal_turns, (list, tuple)) else 0) * 3
            + min(len(panel_ids), 16)
        )
        beat_rows.append(
            {
                "beat_id": beat_id,
                "panel_ids": panel_ids,
                "first_order": first_order,
                "score": score,
                "claims": tuple(beat_claims),
            }
        )
    if not beat_rows:
        raise CloudStageError("cloud.editorial_selection_invalid")
    beat_rows.sort(key=lambda row: (int(row["first_order"]), str(row["beat_id"])))
    count = min(target, len(beat_rows))
    selected_indexes: set[int] = set()
    for bucket in range(count):
        start = (bucket * len(beat_rows)) // count
        stop = ((bucket + 1) * len(beat_rows)) // count
        candidates = range(start, max(start + 1, stop))
        selected_indexes.add(
            max(
                candidates,
                key=lambda index: (
                    int(beat_rows[index]["score"]),
                    -int(beat_rows[index]["first_order"]),
                    str(beat_rows[index]["beat_id"]),
                ),
            )
        )
    selected = [
        beat_rows[index]
        for index in sorted(selected_indexes, key=lambda index: beat_rows[index]["first_order"])
    ]
    selected_panel_set: set[str] = set()
    selected_claim_ids: list[str] = []
    score_rows: list[dict[str, Any]] = []
    for row in selected:
        local_panel_ids: list[str] = []
        for claim in row["claims"]:
            claim_id = str(claim["claim_id"])
            if claim_id not in selected_claim_ids:
                selected_claim_ids.append(claim_id)
            refs = claim.get("evidence_panel_ids", claim.get("panel_ids", ()))
            if isinstance(refs, (list, tuple)):
                for panel_id in refs:
                    panel_id = str(panel_id)
                    if panel_id in row["panel_ids"] and panel_id not in local_panel_ids:
                        local_panel_ids.append(panel_id)
        for panel_id in (row["panel_ids"][0], row["panel_ids"][-1]):
            if panel_id not in local_panel_ids:
                local_panel_ids.append(panel_id)
        local_panel_ids.sort(key=panel_order.__getitem__)
        selected_panel_set.update(local_panel_ids[:EDITORIAL_SELECTION_MAX_PANELS_PER_BEAT])
        score_rows.append(
            {
                "beat_id": row["beat_id"],
                "source_order": int(row["first_order"]),
                "score": int(row["score"]),
                "selected_panel_ids": local_panel_ids[:EDITORIAL_SELECTION_MAX_PANELS_PER_BEAT],
                "claim_count": len(row["claims"]),
            }
        )
    panel_ids = tuple(panel_id for panel_id in panel_order if panel_id in selected_panel_set)
    beat_ids = tuple(str(row["beat_id"]) for row in selected)
    identity = {
        "version": EDITORIAL_SELECTION_VERSION,
        "beat_ids": list(beat_ids),
        "panel_ids": list(panel_ids),
        "claim_ids": list(selected_claim_ids),
        "beat_scores": score_rows,
        "visual_evidence_hash": visual.visual_evidence_hash,
        "story_map_hash": story_map.story_map_hash,
    }
    return EditorialSelection(
        beat_ids=beat_ids,
        panel_ids=panel_ids,
        claim_ids=tuple(selected_claim_ids),
        beat_scores=tuple(score_rows),
        selection_hash=_hash(identity),
    )


def _load_causal_prompt() -> tuple[str, str, str]:
    try:
        text = CAUSAL_MAP_PROMPT_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise CloudStageError("cloud.prompt_missing") from None
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if "Version: cloud-causal-map-v2" not in normalized:
        raise CloudStageError("cloud.prompt_invalid")
    return (
        CAUSAL_MAP_PROMPT_VERSION,
        hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        normalized,
    )


def _load_strip_boundary_prompt() -> tuple[str, str, str]:
    try:
        text = STRIP_BOUNDARY_PROMPT_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise CloudStageError("cloud.prompt_missing") from None
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if f"Version: {STRIP_BOUNDARY_PROMPT_VERSION}" not in normalized:
        raise CloudStageError("cloud.prompt_invalid")
    return (
        STRIP_BOUNDARY_PROMPT_VERSION,
        hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        normalized,
    )


def _prompt_specs() -> dict[str, tuple[str, str, str]]:
    return {
        "visual": visual_scoring.load_visual_evidence_instruction(),
        "story_map": _load_causal_prompt(),
        "narration": narrative_identity.load_narrative_instruction("sharp_friend_v1"),
        "segmentation": _load_strip_boundary_prompt(),
        "visual_narrative_repair": visual_narrative_repair.load_repair_prompt(),
    }


def _narration_retry_feedback(
    message_or_code: str,
    *,
    observed_word_count: int | None = None,
    target_word_min: int | None = None,
    target_word_max: int | None = None,
    target_word_count: int | None = None,
    capacity_locked: bool = False,
    failed_predicate: str | None = None,
    per_position_word_counts: Sequence[int | None] | None = None,
    expected_ranges: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """Return bounded, sanitized guidance for one rejected narration attempt."""

    value = str(message_or_code)
    if (
        "script passage evidence does not cover its claims" in value
        or "claim evidence contains an unknown panel" in value
    ):
        return (
            "repeat only exact current panel IDs and include every claim's evidence IDs "
            "in the referencing passage"
        )
    if "open_question ending must be evidence-grounded and end with ?" in value:
        return (
            "use open_question only when the final passage is an evidence-grounded question; "
            "otherwise choose consequence or cliffhanger without a question mark"
        )
    if "cloud.narrative_qc_blocked" in value:
        return (
            "use natural evidence-grounded prose and avoid generic hype, CTA language, "
            "copied dialogue, and unsupported claims"
        )
    if value == "cloud.narrative_repair_position_contract_invalid":
        return (
            "return exactly one rewrite string for every supplied ordered position; preserve the exact "
            "position count and order, do not merge or omit positions, and return no wrapper fields "
            "other than the rewrites array."
        )
    if value in {
        "cloud.narrative_repair_micro_compaction_unavailable",
        "cloud.narrative_repair_position_budget_invalid",
    }:
        observed = (
            f" The rejected vector had {int(observed_word_count)} lexical words."
            if isinstance(observed_word_count, int)
            and not isinstance(observed_word_count, bool)
            else ""
        )
        valid_min = (
            int(target_word_min)
            if isinstance(target_word_min, int)
            and not isinstance(target_word_min, bool)
            and target_word_min > 0
            else None
        )
        valid_max = (
            int(target_word_max)
            if isinstance(target_word_max, int)
            and not isinstance(target_word_max, bool)
            and target_word_max > 0
            else None
        )
        valid_target = (
            int(target_word_count)
            if isinstance(target_word_count, int)
            and not isinstance(target_word_count, bool)
            and target_word_count > 0
            else None
        )
        if capacity_locked and valid_target is not None:
            if valid_min is not None and valid_max is not None and observed_word_count is not None and observed_word_count < valid_min:
                target_guidance = (
                    f"raise the complete vector to at least {valid_min} lexical words and aim near {valid_target}, "
                    f"while staying at or below {valid_max}; add only grounded temporal or action detail already present in claim_context"
                )
            else:
                target_guidance = f"aim near {valid_target} lexical words total"
                if valid_min is not None and valid_max is not None:
                    target_guidance += (
                        f" while remaining inside the supplied {valid_min}-{valid_max} range"
                    )
        elif valid_min is not None and valid_max is not None:
            target_guidance = f"stay within {valid_min}-{valid_max} lexical words total"
            if valid_target is not None:
                target_guidance += f" and aim for {valid_target}"
        else:
            target_guidance = "target 115-120 lexical words total"
        locked_guidance = (
            " Every supplied position word_budget is a drafting target in capacity-locked mode, not an exact quota; "
            "never use filler, but do not return below target_word_min. If the vector is too short, add only grounded "
            "temporal ordering or action detail already present in claim_context, and never expand beyond the supplied adaptive contract."
            if capacity_locked
            else ""
        )
        surgical_guidance = ""
        if (
            isinstance(per_position_word_counts, Sequence)
            and not isinstance(per_position_word_counts, (str, bytes))
            and isinstance(expected_ranges, Sequence)
            and not isinstance(expected_ranges, (str, bytes))
        ):
            offenders: list[str] = []
            target_excess: list[str] = []
            for index, (count, expected) in enumerate(
                zip(per_position_word_counts, expected_ranges, strict=False)
            ):
                if not isinstance(count, int) or isinstance(count, bool) or not isinstance(expected, Mapping):
                    continue
                maximum = expected.get("max")
                target = expected.get("target")
                if isinstance(maximum, int) and not isinstance(maximum, bool) and count > maximum:
                    offenders.append(f"position {index}: {count}>{maximum}")
                elif isinstance(target, int) and not isinstance(target, bool) and count > target:
                    target_excess.append(f"position {index}: {count}>{target} target")
            minimum_text = (
                f" Keep the complete vector at or above {valid_min} words."
                if valid_min is not None
                else ""
            )
            if offenders:
                surgical_guidance = (
                    " Shorten ONLY these over-ceiling positions first: "
                    + ", ".join(offenders)
                    + ". Keep every other position at approximately its current length; do not shorten compliant positions."
                    + minimum_text
                )
            elif observed_word_count is not None and valid_max is not None and observed_word_count > valid_max:
                trim_to = min(valid_max, valid_target if valid_target is not None else valid_max)
                trim_count = max(1, observed_word_count - trim_to)
                candidates = target_excess or [
                    f"position {index}: {count}"
                    for index, count in enumerate(per_position_word_counts)
                    if isinstance(count, int) and not isinstance(count, bool)
                ]
                surgical_guidance = (
                    f" Remove at least {trim_count} lexical words from the complete vector, trimming the most verbose positions first"
                    + (": " + ", ".join(candidates) if candidates else "")
                    + ". Preserve IDs, evidence, chronology, and meaning; do not compensate by expanding other positions."
                    + minimum_text
                )
        return (
            "rewrite the same locked positions without changing passage, claim, or evidence "
            "lineage; obey every supplied word_budget_max as a hard ceiling, including each "
            f"passage_word_budget_max; {target_guidance}, shorten verbose positions first, "
            "and recount both every passage and the complete rewrite vector before returning."
            + locked_guidance
            + surgical_guidance
            + observed
        )
    return (
        "return strict JSON grounded only in the supplied ordered panels and claims; "
        "do not add foreign panel IDs or unsupported evidence"
    )


def _safe_narration_contract_diagnostic(
    message: str,
    output: Mapping[str, Any] | None,
) -> str:
    """Classify a local narration contract failure without echoing content."""

    lowered = str(message).casefold()
    envelope = output if isinstance(output, Mapping) else {}
    passages = envelope.get("script_passages")
    passage_count = len(passages) if isinstance(passages, list) else 0
    observations = envelope.get("observations")
    observation_count = len(observations) if isinstance(observations, list) else 0
    graph = envelope.get("evidence_graph")
    claims = graph.get("claims") if isinstance(graph, Mapping) else None
    claim_count = len(claims) if isinstance(claims, list) else 0
    if "coverage_map_version" in lowered:
        field, count = "coverage_map_version", observation_count
    elif "coverage_map_hash" in lowered:
        field, count = "coverage_map_hash", observation_count
    elif "dialogue_or_ocr" in lowered:
        field, count = "dialogue_or_ocr", observation_count
    elif "script passage evidence" in lowered or "passage evidence" in lowered:
        field, count = "passage_evidence", passage_count
    elif "claim" in lowered and "evidence" in lowered:
        field, count = "claim_evidence", claim_count
    elif (
        "script_passages" in lowered
        or "script passage" in lowered
        or "four to six passages" in lowered
    ):
        field, count = "script_passages", passage_count
    elif (
        "open_question ending" in lowered
        or "ending must be evidence-grounded" in lowered
        or "ending_kind" in lowered
    ):
        field, count = "ending_kind", 1
    elif "story_spine" in lowered:
        field, count = "story_spine", 6
    elif "narrative_outline" in lowered:
        field, count = "narrative_outline", 1
    else:
        field, count = "narrative_contract", 1
    return f"field={field};count={count}"


def _canonicalize_visual_repair_ending(
    outline: Mapping[str, Any],
    passages: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, str] | None]:
    """Align repair ending metadata to already-grounded final punctuation."""

    normalized = dict(outline)
    story_spine = dict(normalized.get("story_spine") or {})
    normalized["story_spine"] = story_spine
    if not passages:
        return normalized, None
    final_text = str(passages[-1].get("text", "")).strip()
    current = str(normalized.get("ending_kind", "")).strip()
    if not final_text or not current:
        return normalized, None
    target = "open_question" if final_text.endswith("?") else ("consequence" if current == "open_question" else current)
    question_repaired = not str(story_spine.get("unresolved_question", "")).strip()
    if question_repaired:
        story_spine["unresolved_question"] = final_text if final_text.endswith("?") else "What follows?"
    if target == current and not question_repaired:
        return normalized, None
    normalized["ending_kind"] = target
    return normalized, {
        "from": current,
        "to": target,
        "version": "visual-repair-ending-v1",
        **({"unresolved_question_repaired": "true"} if question_repaired else {}),
    }


def _visual_narrative_repair_analyzer_metadata(
    message: str,
    output: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Classify a repair analyzer failure without retaining provider text."""

    diagnostic = _safe_narration_contract_diagnostic(message, output)
    field_part, separator, count_part = diagnostic.partition(";count=")
    field = field_part.removeprefix("field=") or "narrative_contract"
    try:
        count = max(0, int(count_part)) if separator else 1
    except ValueError:
        count = 1
    return {
        "failed_predicate": "analyzer_contract_invalid",
        "failed_field": field,
        "failed_count": count,
    }


def _visual_narrative_repair_error_metadata(
    message: str,
    *,
    code: str,
) -> dict[str, str]:
    """Classify visual-repair failures without retaining provider/error prose."""

    lowered = str(message).casefold()
    predicates = (
        ("repair claim is malformed", "visual.repair_claim_malformed"),
        ("repair claim is unsupported", "visual.repair_claim_unsupported"),
        ("repair claim cites an infeasible panel", "visual.repair_claim_infeasible_panel"),
        (
            "repair claim evidence is outside story lineage",
            "visual.repair_claim_outside_story_lineage",
        ),
        ("repair has no claims", "visual.repair_claims_empty"),
        ("repair passage is malformed", "visual.repair_passage_malformed"),
        ("repair passage evidence is incomplete", "visual.repair_passage_evidence_incomplete"),
        ("repair passage cites unsupported evidence", "visual.repair_passage_unsupported_evidence"),
        (
            "repair passage evidence is outside its claim lineage",
            "visual.repair_passage_outside_claim_lineage",
        ),
        (
            "repair passage visual capacity is insufficient",
            "visual.repair_visual_capacity_shortfall",
        ),
        (
            "repair capacity plan is infeasible",
            "visual.repair_capacity_plan_infeasible",
        ),
        (
            "repair passage diverges from capacity plan",
            "visual.repair_capacity_plan_mismatch",
        ),
        (
            "repair passage exceeds capacity word budget",
            "visual.repair_capacity_word_budget",
        ),
        ("repair chronology is not ordered", "visual.repair_chronology"),
        ("repair claim evidence is incomplete", "visual.repair_claim_evidence_incomplete"),
        ("repair passage text is incomplete", "visual.repair_passage_text_incomplete"),
        (
            "visual-description prose instead of a story",
            "narrative.visual_recap_prose",
        ),
        ("hook opens as a flat panel description", "narrative.flat_hook"),
        ("flat sequential recap", "narrative.flat_sequential_recap"),
        ("hook ignores the grounded curiosity claim", "narrative.hook_weak"),
        ("stiff bureaucratic spoken prose", "narrative.stiff_spoken_prose"),
        ("repaired passages are malformed", "visual.repair_sections_malformed"),
        ("repaired passages do not cover every section", "visual.repair_sections_count"),
        ("repaired passages omit a missing section", "visual.repair_missing_section_omitted"),
        (
            "repaired section still has no feasible visual citation",
            "visual.repair_missing_section_without_feasible_citation",
        ),
    )
    for marker, predicate in predicates:
        if marker in lowered:
            return {"failed_predicate": predicate}
    if code == "cloud.narrative_qc_blocked":
        predicate = "narrative_quality_gate"
    elif code == "cloud.narrative_duration_out_of_range":
        predicate = "narration_duration_contract"
    elif code == "cloud.provider_response_invalid":
        predicate = "provider_response_shape"
    elif code == "cloud.narrative_not_grounded":
        predicate = "analyzer_contract_invalid"
    elif code == "visual.narrative_repair_ungrounded":
        predicate = "visual.repair_contract"
    else:
        predicate = str(code).strip() or "visual.repair_contract"
    return {"failed_predicate": predicate}


def _narrative_grounding_error(field: str, count: int) -> CloudStageError:
    return CloudStageError(
        "cloud.narrative_not_grounded",
        f"field={field};count={int(count)}",
        reviewable=True,
    )


def _visual_narrative_repair_retry_feedback(
    code: str,
    *,
    failed_field: str | None = None,
    failed_predicate: str | None = None,
    observed_word_count: int | None = None,
) -> str:
    """Return bounded, non-content guidance for one rejected repair attempt."""

    value = str(code)
    predicate = str(failed_predicate or "")
    if predicate in {
        "narrative.visual_recap_prose",
        "narrative.flat_hook",
        "narrative.flat_sequential_recap",
    } or value == "cloud.narrative_flat_recap":
        return (
            "rewrite as event-driven story prose, not visual-description prose: keep the exact "
            "capacity-plan claim_ids and evidence_panel_ids, but do not narrate panels, shots, "
            "sequences, close-ups, what is shown, what appears, or what is visible. State the "
            "grounded change, contrast, consequence, or uncertainty and connect adjacent beats "
            "causally when the supplied story map supports it; obey every max_lexical_words limit"
        )
    if predicate == "narrative.hook_weak" or value == "cloud.narrative_hook_weak":
        return (
            "keep the exact hook claim and panel bundle, but lead with why its grounded anomaly, "
            "risk, reveal, or consequence matters; do not open by describing who or what is visible"
        )
    if predicate == "narrative.stiff_spoken_prose" or value == "cloud.narrative_style_stiff":
        return (
            "rewrite only the prose in concise conversational narrator English while preserving the "
            "exact claim and panel bundle. Remove bureaucratic filler, redundant temporal wording, "
            "and formal process language; prefer short direct verbs and natural spoken phrasing. "
            "Do not add facts, stakes, dialogue, or causal claims that are not grounded"
        )
    if predicate == "visual.repair_missing_section_without_feasible_citation":
        return (
            "ensure every missing section has at least one feasible panel citation; "
            "replace stale citations with feasible panels and keep all passages grounded"
        )
    if predicate == "visual.repair_missing_section_omitted":
        return (
            "return enough ordered passages to cover every missing visual section, with "
            "non-empty claims and feasible panel citations"
        )
    if predicate == "visual.repair_passage_unsupported_evidence":
        return (
            "follow capacity_safe_claim_plan exactly by passage index: copy each row's "
            "claim_ids and evidence_panel_ids verbatim without substitution, omission, "
            "reordering, aliases, or extra panel references"
        )
    if predicate in {
        "visual.repair_passage_evidence_incomplete",
        "visual.repair_claim_evidence_incomplete",
    }:
        return (
            "for every passage, cite its existing claim IDs and complete feasible evidence; "
            "do not omit or add panel references"
        )
    if predicate in {
        "visual.repair_claim_infeasible_panel",
        "visual.repair_claim_outside_story_lineage",
    }:
        return (
            "use only claim IDs listed in feasible_claim_ids; for each selected claim, "
            "copy evidence_panel_ids only from that claim's feasible_claims entry and "
            "never rebind a claim to another feasible panel"
        )
    if predicate == "visual.repair_chronology":
        return (
            "keep the first passage as the hook. For every later passage, read "
            "chronology_contract and each feasible claim's min_source_order; choose a "
            "nondecreasing subsequence by min_source_order. The hook may be later, but "
            "never choose a later-ranked non-hook claim and then return to an earlier one"
        )
    if predicate in {
        "visual.repair_capacity_plan_mismatch",
        "visual.repair_capacity_word_budget",
    }:
        return (
            "follow capacity_safe_claim_plan exactly by passage index: copy each row's "
            "claim_ids and evidence_panel_ids without substitution, narrate only those "
            "grounded claims, aim for target_lexical_words, and never exceed "
            "max_lexical_words"
        )
    if predicate == "visual.repair_capacity_plan_infeasible":
        return (
            "the local grounded capacity plan is infeasible; do not invent claims, panels, "
            "or filler, and preserve the supplied evidence contract"
        )
    if predicate == "visual.repair_visual_capacity_shortfall":
        return (
            "follow capacity_contract and capacity_safe_claim_plan exactly; its evidence_panel_slot_capacity and per-claim visual_slot_capacity accounting over unique cited panels is authoritative. Do not add, remove, or substitute claims "
            "or panels. Shorten each passage to its target_lexical_words and never exceed its "
            "max_lexical_words. Its required_visual_slots must be covered by the existing "
            "unique panels; never add unrelated panels, and keep every shot at or below 4.0 seconds"
        )
    if predicate == "visual.repair_passage_outside_claim_lineage":
        return (
            "remove unrelated passage panels; every evidence_panel_id in a passage must belong "
            "to at least one claim_id in that same passage and preserve original claim lineage"
        )
    if predicate == "visual.repair_subtitle_overflow":
        return (
            "rewrite the affected passage with shorter subtitle-safe wording while preserving "
            "the same grounded claim IDs and evidence. Every display chunk must keep at least "
            "two words per line, at most two lines, and no line longer than 22 characters"
        )
    if value == "cloud.narrative_not_grounded" and failed_field == "passage_evidence":
        return (
            "for every returned passage, include each referenced claim ID's complete "
            "feasible evidence_panel_ids in that same passage; use only existing claim "
            "IDs and feasible panel IDs"
        )
    if value == "cloud.narrative_not_grounded" and failed_field == "claim_evidence":
        return (
            "every returned claim must have non-empty feasible evidence_panel_ids, and "
            "each passage using that claim must cite those same panels"
        )
    if value == "cloud.narrative_not_grounded" and failed_field == "script_passages":
        return (
            "return four to six complete passages with exact passage keys, unique IDs, "
            "existing claim IDs, and non-empty feasible evidence_panel_ids"
        )
    if value == "cloud.narrative_not_grounded" and failed_field == "ending_kind":
        return (
            "use open_question only when the final passage is an evidence-grounded "
            "question ending with ?; otherwise choose consequence or cliffhanger and "
            "do not end the final passage with a question mark"
        )
    if value == "cloud.narrative_repair_micro_compaction_unavailable":
        return (
            "return 4-6 grounded chronological passages at 118-122 lexical words; "
            "do not rely on contraction compaction or generic filler, and preserve "
            "existing claim IDs and feasible panel evidence"
        )
    if value in {
        "visual.narrative_repair_ungrounded",
        "cloud.narrative_not_grounded",
    }:
        return (
            "return 4-6 chronological passages using only existing claim IDs and feasible "
            "panel IDs; every claim's declared feasible evidence must be covered by its "
            "referencing passages, with 118-124 lexical words and no unsupported facts"
        )
    if value == "cloud.narrative_duration_out_of_range":
        observed = (
            f" The rejected result had {int(observed_word_count)} lexical words."
            if isinstance(observed_word_count, int)
            else ""
        )
        return (
            "return 4-6 concise chronological passages using only existing claim IDs and "
            "feasible panel IDs; preserve the required visual capacity and recount the complete "
            "spoken script before returning. Target 120-122 lexical words total."
            + observed
        )
    return (
        "return strict JSON with existing claim IDs, feasible panel IDs, grounded "
        "chronological passages, and no provider hashes or unsupported facts"
    )


def _cache_key(
    stage: str, source: object, identity: CloudModelIdentity, prompt: tuple[str, str, str]
) -> str:
    return _hash(
        {
            "stage": stage,
            "source": source,
            "model_identity": identity.as_dict(),
            "model_identity_hash": identity.identity_hash,
            "prompt_version": prompt[0],
            "prompt_sha256": prompt[1],
        }
    )




def _narration_stage_prompt_is_compatible(
    record: ChapterJobRecord,
    narration: NarrationResult,
    runner: CloudStageRunner,
) -> bool:
    """Accept base narration or an exact current visual-repair checkpoint."""
    narration_prompt = runner.prompts["narration"]
    if (
        narration.prompt_version == narration_prompt[0]
        and narration.prompt_sha256 == narration_prompt[1]
    ):
        return True
    repair_meta = record.stage_results.get("visual_repair")
    repair_prompt = runner.prompts.get("visual_narrative_repair")
    if not isinstance(repair_meta, Mapping) or repair_prompt is None:
        return False
    current = (
        str(repair_meta.get("contract_version", ""))
        == visual_narrative_repair.REPAIR_CONTRACT_VERSION
        and str(repair_meta.get("prompt_version", "")) == repair_prompt[0]
        and str(repair_meta.get("prompt_sha256", "")) == repair_prompt[1]
        and str(repair_meta.get("model_identity_hash", ""))
        == runner.model_identity.identity_hash
        and repair_meta.get("publish_allowed") is False
        and narration.prompt_version == repair_prompt[0]
        and narration.prompt_sha256 == repair_prompt[1]
    )
    return current or _narration_is_legacy_visual_repair_checkpoint(
        record, narration, runner
    )


def _narration_is_legacy_visual_repair_checkpoint(
    record: ChapterJobRecord,
    narration: NarrationResult,
    runner: CloudStageRunner,
) -> bool:
    """Admit one prior repair generation only as provisional resume input."""

    repair_meta = record.stage_results.get("visual_repair")
    if not isinstance(repair_meta, Mapping):
        return False
    return (
        narration.prompt_version == LEGACY_VISUAL_REPAIR_PROMPT_VERSION
        and str(repair_meta.get("contract_version", ""))
        == LEGACY_VISUAL_REPAIR_CONTRACT_VERSION
        and str(repair_meta.get("prompt_version", ""))
        == LEGACY_VISUAL_REPAIR_PROMPT_VERSION
        and str(repair_meta.get("prompt_sha256", "")) == narration.prompt_sha256
        and repair_meta.get("publish_allowed") is False
        and _stage_result_identity_is_compatible(
            str(repair_meta.get("model_identity_hash", "")),
            runner.model_identity,
            stage="narration",
        )
    )


def _narration_is_current_visual_repair_checkpoint(
    record: ChapterJobRecord,
    narration: NarrationResult,
    runner: CloudStageRunner,
) -> bool:
    """Return whether narration is the exact current visual-repair checkpoint."""
    repair_prompt = runner.prompts.get("visual_narrative_repair")
    return bool(
        repair_prompt is not None
        and narration.prompt_version == repair_prompt[0]
        and narration.prompt_sha256 == repair_prompt[1]
        and _narration_stage_prompt_is_compatible(record, narration, runner)
    )


def _stage_result_identity_is_compatible(
    cached_identity_hash: str,
    identity: CloudModelIdentity,
    *,
    stage: str,
) -> bool:
    """Allow only audited downstream-repair identity bumps for upstream stages."""
    if cached_identity_hash == identity.identity_hash:
        return True
    if stage not in {"visual", "story_map", "narration"}:
        return False
    prompt_versions = dict(identity.prompt_versions)
    if prompt_versions.get("visual_narrative_repair") != CURRENT_VISUAL_REPAIR_PROMPT_VERSION:
        return False
    compatible_versions = [LEGACY_VISUAL_REPAIR_PROMPT_VERSION]
    if stage in {"visual", "story_map"}:
        compatible_versions.extend(
            [
                EARLIER_UPSTREAM_VISUAL_REPAIR_PROMPT_VERSION,
                OLDER_UPSTREAM_VISUAL_REPAIR_PROMPT_VERSION,
                OLDEST_UPSTREAM_VISUAL_REPAIR_PROMPT_VERSION,
            ]
        )
    for repair_version in compatible_versions:
        legacy_versions = dict(prompt_versions)
        legacy_versions["visual_narrative_repair"] = repair_version
        payload = identity.as_dict()
        payload["prompt_versions"] = legacy_versions
        if cached_identity_hash == _hash(payload):
            return True
    return False


def _visual_narrative_repair_failure_metadata(
    *,
    ledger: visual_narrative_repair.FeasibleVisualLedger,
    section_to_beats: Mapping[str, Sequence[str]],
    attempt_count: int,
    failure_code: str,
) -> dict[str, Any]:
    """Return non-prose diagnostics for a bounded visual-repair rejection."""

    return {
        "contract_version": visual_narrative_repair.REPAIR_CONTRACT_VERSION,
        "attempt_count": int(attempt_count),
        "failure_code": str(failure_code),
        "feasible_panel_count": len(ledger.feasible_panel_ids),
        "feasible_roi_count": sum(len(entry.feasible_rois) for entry in ledger.entries),
        "missing_section_count": len(
            visual_narrative_repair.missing_visual_sections(ledger, section_to_beats)
        ),
        "ledger_hash": ledger.ledger_hash,
    }


class _ProviderConcurrencyGate:
    """Bound actual provider calls across nested visual workers.

    Batch workers and tall-panel geometry repair share this same gate so inner
    window parallelism cannot multiply the configured provider concurrency.
    """

    def __init__(self, limit: int) -> None:
        self.limit = max(1, int(limit))
        self._semaphore = threading.BoundedSemaphore(self.limit)
        self._lock = threading.Lock()
        self.in_flight = 0
        self.peak_in_flight = 0

    def call(self, operation):
        self._semaphore.acquire()
        with self._lock:
            self.in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            return operation()
        finally:
            with self._lock:
                self.in_flight = max(0, self.in_flight - 1)
            self._semaphore.release()

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "provider_concurrency_limit": self.limit,
                "provider_peak_in_flight": self.peak_in_flight,
                "provider_in_flight": self.in_flight,
            }


cloud_runner_parts.bind_runtime(sys.modules[__name__])


class CloudStageRunner(
    cloud_runner_parts.provider.ProviderMixin,
    cloud_runner_parts.visual.VisualEvidenceMixin,
    cloud_runner_parts.story.StoryMapMixin,
    cloud_runner_parts.narration.NarrationMixin,
    cloud_runner_parts.repair.NarrationRepairMixin,
    cloud_runner_parts.visual_repair.VisualNarrativeRepairMixin,
):
    """Run visual, causal-map, and narration calls with one pinned model."""

    def __init__(
        self,
        *,
        provider: CloudMultimodalProvider,
        model_identity: CloudModelIdentity,
        cache: StageCache | None = None,
        max_attempts: int = 2,
        max_requests: int | None = None,
        max_narration_requests: int | None = None,
        max_repair_requests: int | None = None,
        min_request_interval_s: float = 0.0,
        estimated_cost_per_request: float = 0.0,
        allow_balloon_unknown: bool = False,
        visual_checkpoint_path: Path | None = None,
        visual_parallel_workers: int = 8,
        provider_concurrency_gate: _ProviderConcurrencyGate | None = None,
    ) -> None:
        model_id = getattr(provider, "model_id", "")
        if not isinstance(model_id, str) or model_id != model_identity.model:
            raise CloudStageError("cloud.model_identity_mismatch")
        self.provider = provider
        self.model_identity = model_identity
        self.cache = cache
        self.max_attempts = max(1, int(max_attempts))
        # Preview-only relaxation: keep panels whose provider balloon status is
        # "unknown" instead of failing the whole visual stage. The feasible
        # ledger still skips them (they cannot pass framing), so this never
        # selects a balloon-unknown panel; it only unblocks preview renders.
        # Production keeps the strict contract (allow_balloon_unknown=False).
        self.allow_balloon_unknown = bool(allow_balloon_unknown)
        self.max_requests = max_requests if max_requests is None else max(1, int(max_requests))
        self.max_narration_requests = (
            None if max_narration_requests is None else max(1, int(max_narration_requests))
        )
        self.max_repair_requests = (
            None if max_repair_requests is None else max(1, int(max_repair_requests))
        )
        self._use_legacy_global_request_budget = (
            self.max_requests is not None
            and self.max_narration_requests is None
            and self.max_repair_requests is None
        )
        self.min_request_interval_s = max(0.0, float(min_request_interval_s))
        self.estimated_cost_per_request = max(0.0, float(estimated_cost_per_request))
        self.visual_parallel_workers = max(1, int(visual_parallel_workers))
        self._provider_concurrency_gate = provider_concurrency_gate
        inferred_checkpoint = getattr(cache, "checkpoint_path", None)
        if inferred_checkpoint is None:
            cache_root = getattr(cache, "root", None)
            inferred_checkpoint = (
                Path(cache_root) / "visual_checkpoints.jsonl" if cache_root is not None else None
            )
        self.visual_checkpoint_path = (
            Path(visual_checkpoint_path)
            if visual_checkpoint_path is not None
            else inferred_checkpoint
        )
        self._checkpoint_lock = threading.Lock()
        self.request_count = 0
        self.request_counts = {"narration": 0, "narration_repair": 0, "other": 0}
        self.estimated_cost_usd = 0.0
        self.last_response_shape_metrics: dict[str, Any] = {}
        self.last_visual_stream_metrics: dict[str, Any] = {}
        self.last_visual_failure_predicates: dict[str, int] = {}
        self._last_request_at = 0.0
        self.prompts = _prompt_specs()
        expected = dict(model_identity.prompt_versions)
        if any(
            stage in expected and expected[stage] != prompt[0]
            for stage, prompt in self.prompts.items()
        ):
            raise CloudStageError("cloud.prompt_identity_mismatch")

    def start_visual_evidence_stream(
        self,
        *,
        queue_size: int = VISUAL_STREAM_QUEUE_SIZE,
        max_panels: int = VISUAL_REQUEST_MAX_PANELS,
        max_estimated_bytes: int = VISUAL_REQUEST_MAX_ESTIMATED_BYTES,
        worker_count: int | None = None,
    ) -> _StreamingVisualEvidenceSession:
        """Start bounded producer/consumer visual analysis for cold preparation."""

        return _StreamingVisualEvidenceSession(
            self,
            queue_size=queue_size,
            max_panels=max_panels,
            max_estimated_bytes=max_estimated_bytes,
            worker_count=(
                self.visual_parallel_workers if worker_count is None else int(worker_count)
            ),
        )


















    def _run_story_map_coverage_fallback(
        self,
        prompt: tuple[str, str, str],
        visual: VisualStageResult,
        chunk_index: int,
        chunk: Sequence[Mapping[str, Any]],
        batch_count: int,
        *,
        step: int = STORY_MAP_COVERAGE_FALLBACK_STEP,
    ) -> StoryMapResult:
        """Reconcile a large response through smaller complete-coverage requests.

        The configured model can enumerate the supplied IDs in a 180-panel
        envelope while citing only a partial subset in its beats.  Coverage
        remains fail-closed; this bounded fallback asks deterministic 60-panel
        subchunks instead of inventing references or accepting a partial map.
        """

        step = max(STORY_MAP_COVERAGE_FINAL_STEP, int(step))
        subchunks = [chunk[index : index + step] for index in range(0, len(chunk), step)]
        results = [
            self._run_story_map_chunk(
                prompt,
                visual,
                chunk_index * 10_000 + sub_index,
                subchunk,
                batch_count * 10_000,
                coverage_step=step,
            )
            for sub_index, subchunk in enumerate(subchunks)
        ]
        beats: list[dict[str, Any]] = []
        causal_chain: list[dict[str, Any]] = []
        claims: list[dict[str, Any]] = []
        for sub_index, result in enumerate(results):
            prefix = f"sub{sub_index}__"
            beats.extend(dict(item, beat_id=prefix + str(item["beat_id"])) for item in result.beats)
            claims.extend(
                dict(item, claim_id=prefix + str(item["claim_id"])) for item in result.claims
            )
            causal_chain.extend(
                {
                    **dict(link),
                    "from_beat": prefix + str(link["from_beat"]),
                    "to_beat": prefix + str(link["to_beat"]),
                }
                for link in result.causal_chain
            )
        panel_ids = tuple(str(panel["panel_id"]) for panel in chunk)
        canonical = {
            "panel_ids": list(panel_ids),
            "beats": beats,
            "causal_chain": causal_chain,
            "claims": claims,
        }
        return StoryMapResult(
            panel_ids=panel_ids,
            beats=tuple(beats),
            causal_chain=tuple(causal_chain),
            claims=tuple(claims),
            story_map_hash=_hash(canonical),
            model_identity_hash=self.model_identity.identity_hash,
            prompt_version=prompt[0],
            prompt_sha256=prompt[1],
        )

    def _run_story_map_chunk(
        self,
        prompt: tuple[str, str, str],
        visual: VisualStageResult,
        chunk_index: int,
        chunk: Sequence[Mapping[str, Any]],
        batch_count: int,
        *,
        coverage_step: int = STORY_MAP_COVERAGE_FALLBACK_STEP,
    ) -> StoryMapResult:
        chunk_ids = tuple(str(panel["panel_id"]) for panel in chunk)
        chunk_source = {
            "panel_ids": list(chunk_ids),
            "visual": [dict(panel) for panel in chunk],
            "visual_source_hash": visual.source_hash,
            "batch_index": chunk_index,
            "batch_count": batch_count,
        }
        chunk_key = _cache_key(
            "story_map_chunk",
            chunk_source,
            self.model_identity,
            prompt,
        )
        if self.cache is not None and (cached := self.cache.get(chunk_key)) is not None:
            try:
                cached_result = StoryMapResult.from_dict(cached)
            except (KeyError, TypeError, ValueError):
                cached_result = None
            if (
                cached_result is not None
                and cached_result.panel_ids == chunk_ids
                and cached_result.model_identity_hash == self.model_identity.identity_hash
                and cached_result.prompt_version == prompt[0]
                and cached_result.prompt_sha256 == prompt[1]
                and cached_result.visual_evidence_hash == visual.visual_evidence_hash
            ):
                return cached_result

        retryable_story_codes = {
            "cloud.provider_request_failed",
            "cloud.provider_response_invalid",
            "cloud.provider_hash_forbidden",
            "cloud.panel_coverage_incomplete",
            "cloud.story_map_invalid",
            "cloud.story_claim_invalid",
        }
        result: StoryMapResult | None = None
        for attempt in range(self.max_attempts):
            attempt_source = {**chunk_source, "retry_attempt": attempt}
            try:
                raw = self._call(
                    lambda attempt_source=attempt_source: self.provider.complete_json(
                        stage="story_map",
                        prompt_version=prompt[0],
                        prompt_sha256=prompt[1],
                        prompt_text=prompt[2],
                        payload=attempt_source,
                    ),
                    request_stage="other",
                )
                result = self._reconcile_story_map(raw, chunk_ids, prompt)
            except CloudStageError as exc:
                if exc.code == "cloud.panel_coverage_incomplete":
                    if len(chunk) > coverage_step:
                        result = self._run_story_map_coverage_fallback(
                            prompt,
                            visual,
                            chunk_index,
                            chunk,
                            batch_count,
                            step=coverage_step,
                        )
                        break
                    if (
                        coverage_step > STORY_MAP_COVERAGE_MIN_STEP
                        and len(chunk) > STORY_MAP_COVERAGE_MIN_STEP
                    ):
                        result = self._run_story_map_coverage_fallback(
                            prompt,
                            visual,
                            chunk_index,
                            chunk,
                            batch_count,
                            step=STORY_MAP_COVERAGE_MIN_STEP,
                        )
                        break
                    if (
                        coverage_step >= STORY_MAP_COVERAGE_MIN_STEP
                        and len(chunk) > STORY_MAP_COVERAGE_FINAL_STEP
                    ):
                        result = self._run_story_map_coverage_fallback(
                            prompt,
                            visual,
                            chunk_index,
                            chunk,
                            batch_count,
                            step=STORY_MAP_COVERAGE_FINAL_STEP,
                        )
                        break
                if exc.code in retryable_story_codes and attempt + 1 < self.max_attempts:
                    continue
                raise
            break
        if result is None:
            raise CloudStageError("cloud.story_map_invalid")
        result = replace(result, visual_evidence_hash=visual.visual_evidence_hash)
        if self.cache is not None:
            self.cache.put(chunk_key, result.as_dict())
        return result







































    def run_chapter(self, panels: Sequence[CloudPanelInput]) -> ChapterResult:
        ordered = self._ordered_panels(panels)
        source = list(_visual_panel_identities(ordered))
        key = _cache_key("chapter", source, self.model_identity, self.prompts["narration"])
        if self.cache is not None and (cached := self.cache.get(key)) is not None:
            return ChapterResult.from_dict(cached)
        visual = self.run_visual_evidence(ordered)
        story_map = self.run_story_map(visual)
        narration = self.run_narration(visual, story_map, panels=ordered)
        result = ChapterResult(ChapterState.READY_TO_RENDER, visual, story_map, narration)
        if self.cache is not None:
            self.cache.put(key, result.as_dict())
        return result


def _stream_visual_batches(
    panels: Sequence[CloudPanelInput],
    *,
    max_panels: int = VISUAL_REQUEST_MAX_PANELS,
    max_estimated_bytes: int = VISUAL_REQUEST_MAX_ESTIMATED_BYTES,
) -> tuple[tuple[CloudPanelInput, ...], ...]:
    """Partition already validated panels without overlap for streaming work."""

    if max_panels < 1 or max_estimated_bytes < 1:
        raise CloudStageError("cloud.visual_stream_config_invalid")
    batches: list[tuple[CloudPanelInput, ...]] = []
    current: list[CloudPanelInput] = []
    estimated = 0
    seen_ids: set[str] = set()
    for panel in panels:
        if panel.panel_id in seen_ids:
            raise CloudStageError("cloud.panel_lineage_invalid")
        seen_ids.add(panel.panel_id)
        provider_payload, _ = _visual_provider_payload(panel)
        estimate = (len(provider_payload) * 4 + 2) // 3 + 768
        if current and (len(current) >= max_panels or estimated + estimate > max_estimated_bytes):
            batches.append(tuple(current))
            current = []
            estimated = 0
        current.append(panel)
        estimated += estimate
    if current:
        batches.append(tuple(current))
    return tuple(batches)


def _stream_retry_pending_ids(
    expected_ids: Sequence[str], accepted_ids: set[str]
) -> tuple[str, ...]:
    """Return only missing panel IDs in their original task order."""

    return tuple(panel_id for panel_id in expected_ids if panel_id not in accepted_ids)


def _stream_visual_chunk_cache_key(
    chunk: Sequence[CloudPanelInput],
    *,
    chunk_index: int,
    model_identity: CloudModelIdentity,
    prompt: tuple[str, str, str],
) -> str:
    return _hash(
        {
            "version": VISUAL_STREAM_VERSION,
            "chunk_index": int(chunk_index),
            "panel_ids": [panel.panel_id for panel in chunk],
            "panel_identity_hashes": [
                _visual_panel_identity_hash(panel, index) for index, panel in enumerate(chunk)
            ],
            "model_identity_hash": model_identity.identity_hash,
            "prompt_version": prompt[0],
            "prompt_sha256": prompt[1],
        }
    )


def _stream_validate_row(
    row: Mapping[str, Any],
    panel: CloudPanelInput,
    *,
    expected_identity_hash: str,
) -> dict[str, Any]:
    try:
        reusable = _visual_cached_row_is_reusable(row, panel)
    except (TypeError, ValueError, OverflowError):
        reusable = False
    if not reusable:
        raise CloudStageError("cloud.visual_stream_row_invalid")
    if (
        str(row.get("cache_identity_hash", "")) != expected_identity_hash
        or str(row.get("cache_identity_version", "")) != VISUAL_CACHE_IDENTITY_VERSION
    ):
        raise CloudStageError("cloud.visual_stream_row_invalid")
    return dict(row)


def _stream_validate_rejection(
    record: Mapping[str, Any],
    panel: CloudPanelInput,
    *,
    expected_identity_hash: str,
) -> dict[str, Any]:
    """Validate a terminal panel-local checkpoint before reusing it."""

    try:
        panel_id = str(record.get("panel_id", ""))
        source_order = int(record.get("source_order", -1))
        attempt_count = int(record.get("attempt_count", 0))
    except (TypeError, ValueError):
        raise CloudStageError("cloud.visual_stream_row_invalid") from None
    rejection_code = str(record.get("rejection_code", ""))
    predicate = str(record.get("failure_predicate", ""))
    if (
        panel_id != panel.panel_id
        or str(record.get("source_asset_id", "")) != panel.source_asset_id
        or source_order != int(panel.source_order)
        or str(record.get("source_checksum", "")) != panel.source_checksum
        or str(record.get("cache_identity_hash", "")) != expected_identity_hash
        or str(record.get("cache_identity_version", "")) != VISUAL_CACHE_IDENTITY_VERSION
        or str(record.get("stream_checkpoint_version", "")) != VISUAL_STREAM_VERSION
        or str(record.get("terminal_status", "")) != "rejected"
        or str(record.get("failure_scope", "")) != "panel_local_reject"
        or attempt_count < 1
        or _classify_visual_failure(
            rejection_code,
            singleton=True,
            predicate=predicate,
        )
        != "panel_local_reject"
    ):
        raise CloudStageError("cloud.visual_stream_row_invalid")
    return dict(record)


def _merge_stream_visual_rows(
    events: Sequence[Mapping[str, Any]],
    panels: Sequence[CloudPanelInput],
    *,
    rejected_panel_ids: Sequence[str] = (),
) -> tuple[dict[str, Any], ...]:
    """Validate worker rows and restore canonical panel order."""

    ordered = CloudStageRunner._ordered_panels(tuple(panels))
    expected = {panel.panel_id: panel for panel in ordered}
    rejected = {str(panel_id) for panel_id in rejected_panel_ids}
    if not rejected.issubset(expected):
        raise CloudStageError("cloud.visual_stream_row_invalid")
    expected_hashes = {
        panel.panel_id: _visual_panel_identity_hash(panel, index)
        for index, panel in enumerate(ordered)
    }
    merged: dict[str, dict[str, Any]] = {}
    try:
        for event in events:
            raw_rows = event.get("rows", ())
            if not isinstance(raw_rows, (list, tuple)):
                raise CloudStageError("cloud.visual_stream_row_invalid")
            for raw_row in raw_rows:
                if not isinstance(raw_row, Mapping):
                    raise CloudStageError("cloud.visual_stream_row_invalid")
                panel_id = str(raw_row.get("panel_id", ""))
                panel = expected.get(panel_id)
                if panel is None or panel_id in merged:
                    raise CloudStageError("cloud.visual_stream_row_invalid")
                merged[panel_id] = _stream_validate_row(
                    raw_row,
                    panel,
                    expected_identity_hash=expected_hashes[panel_id],
                )
    except CloudStageError:
        raise
    except (TypeError, ValueError):
        raise CloudStageError("cloud.visual_stream_row_invalid") from None
    missing = tuple(
        panel.panel_id
        for panel in ordered
        if panel.panel_id not in merged and panel.panel_id not in rejected
    )
    if missing:
        raise CloudStageError(
            "cloud.panel_coverage_incomplete",
            reviewable=True,
            safe_metadata={
                "expected_panel_count": len(ordered),
                "accepted_panel_count": len(merged),
                "missing_panel_count": len(missing),
            },
        )
    return tuple(merged[panel.panel_id] for panel in ordered if panel.panel_id in merged)


def _stream_percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = min(len(ordered) - 1, max(0, math.ceil(percentile * len(ordered)) - 1))
    return round(ordered[index], 4)


def _stream_error_category(code: str) -> str:
    lowered = str(code).lower()
    if "429" in lowered or "rate" in lowered:
        return "rate_limited"
    if "5xx" in lowered or "server" in lowered:
        return "server_error"
    if "timeout" in lowered:
        return "timeout"
    if "response_invalid" in lowered or "schema" in lowered:
        return "schema_reject"
    if "provider_request_failed" in lowered:
        return "transient_failure"
    return "other_failure"


# A provider/schema/geometry defect can be terminal for one panel without
# being a project integrity failure.  Keep this list explicit: unknown codes,
# transport failures, and lineage/payload failures remain hard stops.
_PANEL_LOCAL_REJECT_CODES = frozenset(
    {
        "cloud.provider_response_invalid",
        "cloud.visual_evidence_invalid",
        "visual.evidence_invalid",
        "visual.region_invalid",
        "visual.balloon_mask_unknown",
        "visual.balloon_geometry_invalid",
        "visual.blank_infeasible",
        "visual.crop_infeasible",
        "visual.roi_infeasible",
        "visual.protected_subject_coverage",
        "visual.protected_face_coverage",
        "visual.protected_action_coverage",
        "visual.protected_effect_coverage",
        "visual.protected_continuity_context_coverage",
        "visual.source_resolution_insufficient",
    }
)
_PANEL_LOCAL_REJECT_PREDICATES = frozenset(
    {
        "provider_response_invalid",
        "visible_facts_nonempty",
        "visible_facts_item",
        "observation_mapping",
        "visual_validator",
        "balloon_mask_unknown",
        "balloon_geometry_unknown",
        "blank_infeasible",
        "crop_infeasible",
        "roi_infeasible",
        "protected_region_coverage",
    }
)
_PROJECT_HARD_STOP_CODES = frozenset(
    {
        "cloud.panel_lineage_invalid",
        "cloud.visual_stream_row_invalid",
        "cloud.panel_payload_invalid",
        "cloud.panel_checksum_invalid",
        "cloud.prepared_manifest_invalid",
        "cloud.prepared_manifest_requires_materialization",
        "cloud.provider_request_failed",
        "cloud.provider_auth_invalid",
        "cloud.model_identity_invalid",
        "cloud.prompt_identity_mismatch",
        "cloud.source_checksum_invalid",
    }
)


def _classify_visual_failure(
    code: str,
    *,
    singleton: bool = False,
    predicate: str | None = None,
) -> str:
    """Classify a visual failure without turning integrity errors into rejects."""

    normalized = str(code or "").strip()
    if normalized in _PROJECT_HARD_STOP_CODES:
        return "project_hard_stop"
    if normalized in _PANEL_LOCAL_REJECT_CODES and singleton:
        return "panel_local_reject"
    if (
        singleton
        and normalized == "cloud.panel_coverage_incomplete"
        and str(predicate or "") in _PANEL_LOCAL_REJECT_PREDICATES
    ):
        return "panel_local_reject"
    return "project_hard_stop"


def _panel_local_rejection_code(code: str, predicate: str | None = None) -> str:
    """Return a stable non-prose reason code for one quarantined panel."""

    normalized = str(code or "").strip()
    if normalized in _PANEL_LOCAL_REJECT_CODES:
        return normalized
    predicate_code = str(predicate or "").strip()
    if predicate_code in {
        "visible_facts_nonempty",
        "visible_facts_item",
        "observation_mapping",
        "visual_validator",
    }:
        return "cloud.visual_evidence_invalid"
    if predicate_code in {"balloon_mask_unknown", "balloon_geometry_unknown"}:
        return "visual.balloon_mask_unknown"
    if predicate_code in {"blank_infeasible", "crop_infeasible", "roi_infeasible"}:
        return "visual.blank_infeasible"
    if predicate_code == "protected_region_coverage":
        return "visual.protected_subject_coverage"
    return "cloud.visual_evidence_invalid"


class _FixedVisualConcurrency:
    """Bounded fixed-width controller with observability-compatible metrics.

    Worker selection is intentionally made once when the stream starts.  The
    controller still records wave latency and failure categories so operators
    can tune the configured width between runs without creating or destroying
    workers while a run is in flight.
    """

    def __init__(
        self,
        worker_count: int,
        *,
        wave_panel_target: int = VISUAL_STREAM_WAVE_PANEL_TARGET,
    ) -> None:
        count = int(worker_count)
        if count < 1:
            raise CloudStageError("cloud.visual_stream_config_invalid")
        self.worker_count = count
        self.wave_panel_target = max(1, int(wave_panel_target))
        self.in_flight = 0
        self.peak_in_flight = 0
        self.previous_p95: float | None = None
        self._condition = threading.Condition()
        self._wave_started = time.monotonic()
        self._wave_panels = 0
        self._wave_requests = 0
        self._wave_latencies: list[float] = []
        self._wave_categories: dict[str, int] = {}
        self._waves: list[dict[str, Any]] = []

    @property
    def current_limit(self) -> int:
        return self.worker_count

    def acquire(self) -> None:
        with self._condition:
            while self.in_flight >= self.current_limit:
                self._condition.wait()
            self.in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self.in_flight)

    def release(
        self,
        *,
        panel_count: int,
        request_count: int,
        latency_s: float,
        categories: Mapping[str, int],
    ) -> None:
        with self._condition:
            self.in_flight = max(0, self.in_flight - 1)
            self._wave_panels += int(panel_count)
            self._wave_requests += int(request_count)
            self._wave_latencies.append(float(latency_s))
            for key, value in categories.items():
                self._wave_categories[key] = self._wave_categories.get(key, 0) + int(value)
            if self._wave_panels >= self.wave_panel_target:
                p95 = _stream_percentile(self._wave_latencies, 0.95)
                failures = sum(
                    self._wave_categories.get(key, 0)
                    for key in ("transient_failure", "timeout", "server_error")
                )
                rate_limited = self._wave_categories.get("rate_limited", 0)
                failure_rate = failures / max(1, self._wave_requests)
                unstable = (
                    rate_limited > 0
                    or failure_rate > 0.02
                    or (
                        p95 is not None
                        and self.previous_p95 is not None
                        and p95 > self.previous_p95 * 2
                    )
                )
                self._waves.append(
                    {
                        "workers": self.current_limit,
                        "panel_count": self._wave_panels,
                        "request_count": self._wave_requests,
                        "elapsed_s": round(time.monotonic() - self._wave_started, 4),
                        "throughput_panels_s": round(
                            self._wave_panels / max(0.001, time.monotonic() - self._wave_started),
                            4,
                        ),
                        "p50_latency_s": _stream_percentile(self._wave_latencies, 0.50),
                        "p95_latency_s": p95,
                        "categories": dict(self._wave_categories),
                        "stable": not unstable,
                    }
                )
                if p95 is not None:
                    self.previous_p95 = p95
                self._wave_started = time.monotonic()
                self._wave_panels = 0
                self._wave_requests = 0
                self._wave_latencies = []
                self._wave_categories = {}
            self._condition.notify_all()

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            waves = list(self._waves)
            if self._wave_panels:
                elapsed = max(0.001, time.monotonic() - self._wave_started)
                waves.append(
                    {
                        "workers": self.current_limit,
                        "panel_count": self._wave_panels,
                        "request_count": self._wave_requests,
                        "elapsed_s": round(elapsed, 4),
                        "throughput_panels_s": round(self._wave_panels / elapsed, 4),
                        "p50_latency_s": _stream_percentile(self._wave_latencies, 0.50),
                        "p95_latency_s": _stream_percentile(self._wave_latencies, 0.95),
                        "categories": dict(self._wave_categories),
                        "stable": None,
                    }
                )
            return {
                "worker_count": self.worker_count,
                "worker_levels": [self.worker_count],
                "selected_worker_level": self.current_limit,
                "peak_in_flight": self.peak_in_flight,
                "waves": waves,
            }




_StreamingVisualEvidenceSession = cloud_runner_parts.streaming._StreamingVisualEvidenceSession


def prepare_project_panels(
    db: Any,
    project_id: str,
    *,
    boundary_assessor: Callable[[strip_segmentation.BoundaryRequest], Mapping[str, Any]]
    | None = None,
    review_root: Path | None = None,
    return_segmentation: bool = False,
    review_only_auto_override: bool = False,
    cached_segmentation: Mapping[str, Any] | None = None,
    panel_sink: Callable[[CloudPanelInput], None] | None = None,
    segmentation_checkpoint_identity: Mapping[str, Any] | None = None,
) -> tuple[CloudPanelInput, ...] | tuple[tuple[CloudPanelInput, ...], dict[str, Any]]:
    """Build immutable cloud inputs from the current project panel lineage.

    Segmentation and source decoding are reused from the regular pipeline.  No
    StoryAnalysis rows are written here; persistence happens only after all
    three cloud stages reconcile successfully.
    """

    from app.models import PanelRegion
    from app.services import pipeline, segmentation

    segmentation_checkpoint_root = (
        Path(review_root) / "segmentation-checkpoints" if review_root is not None else None
    )

    try:
        assets = pipeline.image_assets(pipeline.project_assets(db, project_id))
        inputs, asset_by_id = pipeline._build_source_inputs(assets)
    except Exception:
        raise CloudStageError("cloud.panel_coverage_incomplete") from None

    # Preserve the historical reconciliation-first error boundary for callers
    # that do not request streaming.  Streaming callers need the local
    # canonical map first so a completed source can materialize exact panel
    # IDs while later source groups are still being reconciled.
    reconciliation: Any | None = None
    if panel_sink is None:
        try:
            if cached_segmentation is not None:
                try:
                    reconciliation = strip_segmentation.restore_cached_reconciliation(
                        inputs,
                        cached_segmentation,
                    )
                except strip_segmentation.StripSegmentationError as exc:
                    if exc.code != "segmentation.cache_invalid":
                        raise
                    reconciliation = strip_segmentation.reconcile_sources(
                        inputs,
                        boundary_assessor=boundary_assessor,
                        review_root=review_root,
                        checkpoint_root=segmentation_checkpoint_root,
                        checkpoint_identity=segmentation_checkpoint_identity,
                    )
            else:
                reconciliation = strip_segmentation.reconcile_sources(
                    inputs,
                    boundary_assessor=boundary_assessor,
                    review_root=review_root,
                    checkpoint_root=segmentation_checkpoint_root,
                    checkpoint_identity=segmentation_checkpoint_identity,
                )
        except strip_segmentation.StripSegmentationError as exc:
            raise CloudStageError(
                exc.code,
                reviewable=exc.reviewable,
                safe_metadata=exc.safe_metadata,
            ) from None
        except Exception:
            raise CloudStageError("cloud.panel_coverage_incomplete") from None
        if reconciliation.status != "RECONCILED" and review_only_auto_override:
            try:
                reconciliation = strip_segmentation.apply_review_only_overrides(
                    reconciliation,
                    actor_id="review-preview-auto",
                    reason="review-only preview retained provider-confirmed separators and kept ambiguous artwork contiguous",
                )
            except strip_segmentation.StripSegmentationError as exc:
                raise CloudStageError(exc.code, reviewable=exc.reviewable) from None
        if reconciliation.status != "RECONCILED":
            review_codes = [
                report.review_code for report in reconciliation.reports if report.review_code
            ]
            raise CloudStageError(
                review_codes[0] if review_codes else "segmentation.coverage_incomplete",
                reviewable=True,
            )
    try:
        coverage = segmentation.build_complete_coverage_map(
            inputs,
            segmentation_version=segmentation.SEGMENTATION_VERSION,
        )
        errors = segmentation.verify_segmentation_completeness(
            pipeline._coverage_overviews(inputs, coverage), coverage
        )
        errors = tuple(sorted(set(coverage.reconciliation_errors + errors)))
    except Exception:
        raise CloudStageError("cloud.panel_coverage_incomplete") from None
    if errors or coverage.source_content_coverage_ratio != 1.0 or coverage.unresolved_material_area:
        raise CloudStageError("cloud.panel_coverage_incomplete")

    input_by_asset = {item.source_asset_id: item for item in inputs}
    regions = tuple(
        sorted(
            (region for region in coverage.regions if region.region_class == "canonical_panel"),
            key=lambda region: (
                str(getattr(asset_by_id.get(region.source_asset_id), "source_family", "") or ""),
                region.source_order,
                region.region_id,
            ),
        )
    )
    if not regions:
        raise CloudStageError("cloud.panel_coverage_incomplete")

    panel_hints: dict[str, dict[str, Any]] = {}
    stream_candidate_regions: list[dict[str, Any]] = []
    for region in coverage.regions:
        source_input = input_by_asset.get(region.source_asset_id)
        asset = asset_by_id.get(region.source_asset_id)
        if source_input is None or asset is None:
            raise CloudStageError("cloud.panel_lineage_invalid")
        quality = getattr(asset, "panel_quality", {}) or {}
        trim_classification = str(getattr(asset, "trim_classification", "") or "")
        story_evidence = trim_classification.lower() not in {
            "blank",
            "near_blank",
            "title",
            "cover",
            "gutter",
            "transition",
        }
        if str(getattr(asset, "panel_decision", "") or "").lower() == "reject":
            story_evidence = False
        if region.region_class == "canonical_panel":
            panel_hints[region.region_id] = {
                "panel_decision": str(getattr(asset, "panel_decision", "") or ""),
                "trim_classification": trim_classification,
                "story_evidence": story_evidence,
                "metrics": dict(quality) if isinstance(quality, Mapping) else {},
            }
        stream_candidate_regions.append(
            {
                "region_id": region.region_id,
                "source_asset_id": asset.id,
                "source_checksum": source_input.original_checksum,
                "source_order": region.source_order,
                "bounds": list(region.bounds),
                "region_class": region.region_class,
                "confidence": float(region.confidence),
                "evidence": region.evidence,
            }
        )

    panel_by_id: dict[str, CloudPanelInput] = {}
    stream_submitted_ids: set[str] = set()
    stream_admitted_keys: dict[tuple[Any, ...], CloudPanelInput] = {}
    stream_admitted_by_lineage: dict[tuple[str, tuple[int, int] | None], list[CloudPanelInput]] = {}
    last_panel_admission: PanelAdmissionResult | None = None
    region_order = {region.region_id: order for order, region in enumerate(regions)}
    regions_by_asset: dict[str, list[Any]] = {}
    for region in regions:
        regions_by_asset.setdefault(str(region.source_asset_id), []).append(region)

    def materialize_panel(region: Any) -> CloudPanelInput:
        cached_panel = panel_by_id.get(region.region_id)
        if cached_panel is not None:
            return cached_panel
        source_input = input_by_asset.get(region.source_asset_id)
        asset = asset_by_id.get(region.source_asset_id)
        if source_input is None or asset is None:
            raise CloudStageError("cloud.panel_lineage_invalid")
        panel_order = region_order[region.region_id]
        transient = PanelRegion(
            id=region.region_id,
            story_analysis_id="cloud-preview",
            source_asset_id=asset.id,
            source_asset_checksum=source_input.original_checksum,
            original_width=source_input.original_width,
            original_height=source_input.original_height,
            strip_region_id=region.region_id,
            panel_id=region.region_id,
            source_order=panel_order,
            bounds_json={
                "x": region.bounds[0],
                "y": region.bounds[1],
                "width": region.bounds[2] - region.bounds[0],
                "height": region.bounds[3] - region.bounds[1],
            },
        )
        try:
            payload = pipeline._encode_panel_payload(transient, source_input)
        except Exception:
            raise CloudStageError("cloud.panel_payload_invalid") from None
        panel = CloudPanelInput(
            panel_id=region.region_id,
            source_asset_id=asset.id,
            source_order=panel_order,
            mime_type="image/png",
            payload=payload,
            source_checksum=source_input.original_checksum,
            source_family=str(getattr(asset, "source_family", "") or ""),
            panel_bounds=region.bounds,
            source_dimensions=(source_input.original_width, source_input.original_height),
            strip_region_id=region.region_id,
            coverage_map_version=coverage.version,
            coverage_map_hash=coverage.map_sha256,
            segmentation_version=strip_segmentation.SEGMENTATION_VERSION,
            prepared_order=panel_order,
        )
        panel_by_id[panel.panel_id] = panel
        return panel

    candidate_region_by_key = {
        (str(item.get("source_asset_id", "")), tuple(item.get("bounds", ()))): item
        for item in stream_candidate_regions
        if isinstance(item, Mapping) and isinstance(item.get("bounds"), (list, tuple))
    }

    def admit_incremental_and_sink(panel: CloudPanelInput) -> None:
        """Admit one newly reconciled panel without rescanning a prefix."""

        nonlocal last_panel_admission
        if panel_sink is None:
            return
        exact_key = _admission_panel_key(panel)
        if exact_key in stream_admitted_keys:
            return
        lineage_key = (panel.source_checksum, panel.source_dimensions)
        for prior in stream_admitted_by_lineage.get(lineage_key, ()):
            if (
                prior.panel_bounds is not None
                and panel.panel_bounds is not None
                and _admission_area(panel.panel_bounds) > 0
                and _admission_intersection_area(panel.panel_bounds, prior.panel_bounds)
                / min(_admission_area(panel.panel_bounds), _admission_area(prior.panel_bounds))
                >= 0.98
            ):
                return
        region = candidate_region_by_key.get(
            (panel.source_asset_id, tuple(panel.panel_bounds or ()))
        )
        if region is None:
            raise CloudStageError("cloud.panel_admission_invalid")
        incremental = admit_panel_inputs(
            (panel,),
            raw_image_count=len(assets),
            ingest_asset_count=len(inputs),
            candidate_regions=(region,),
            panel_hints={panel.panel_id: dict((panel_hints or {}).get(panel.panel_id, {}))},
            detector_version=PANEL_ADMISSION_DETECTOR_VERSION,
        )
        last_panel_admission = incremental
        if incremental.needs_review:
            raise CloudStageError(
                "segmentation.panel_admission_needs_review",
                reviewable=True,
                safe_metadata={
                    "ledger_hash": incremental.ledger["ledger_hash"],
                    "needs_review": incremental.ledger["counts"]["needs_review"],
                },
            )
        if incremental.admitted:
            stream_admitted_keys[exact_key] = panel
            stream_admitted_by_lineage.setdefault(lineage_key, []).append(panel)
            if panel.panel_id not in stream_submitted_ids:
                panel_sink(panel)
                stream_submitted_ids.add(panel.panel_id)

    def admission_failure_metadata(reason_code: str) -> dict[str, Any]:
        if last_panel_admission is not None:
            ledger = panel_admission_failure_ledger(
                tuple(panel_by_id.values()),
                raw_image_count=len(assets),
                ingest_asset_count=len(inputs),
                candidate_regions=tuple(stream_candidate_regions),
                panel_hints=panel_hints,
                reason_code=reason_code,
            )
        else:
            ledger = panel_admission_failure_ledger(
                (),
                raw_image_count=len(assets),
                ingest_asset_count=len(inputs),
                candidate_regions=tuple(stream_candidate_regions),
                panel_hints=panel_hints,
                reason_code=reason_code,
            )
        return {"panel_admission": ledger}

    def on_reconciled(group: tuple[Any, ...], _result: Any) -> None:
        if panel_sink is None:
            return
        group_asset_ids = tuple(dict.fromkeys(str(item.source_asset_id) for item in group))
        group_regions = tuple(
            sorted(
                (
                    region
                    for asset_id in group_asset_ids
                    for region in regions_by_asset.get(asset_id, ())
                ),
                key=lambda region: region_order[region.region_id],
            )
        )
        if not group_regions:
            return
        for region in group_regions:
            admit_incremental_and_sink(materialize_panel(region))

    stream_callback = on_reconciled if panel_sink is not None else None
    if reconciliation is None:
        try:
            if cached_segmentation is not None:
                try:
                    reconciliation = strip_segmentation.restore_cached_reconciliation(
                        inputs,
                        cached_segmentation,
                    )
                except strip_segmentation.StripSegmentationError as exc:
                    if exc.code != "segmentation.cache_invalid":
                        raise
                    reconciliation = strip_segmentation.reconcile_sources(
                        inputs,
                        boundary_assessor=boundary_assessor,
                        review_root=review_root,
                        checkpoint_root=segmentation_checkpoint_root,
                        checkpoint_identity=segmentation_checkpoint_identity,
                        on_reconciled=stream_callback,
                    )
            else:
                reconciliation = strip_segmentation.reconcile_sources(
                    inputs,
                    boundary_assessor=boundary_assessor,
                    review_root=review_root,
                    checkpoint_root=segmentation_checkpoint_root,
                    checkpoint_identity=segmentation_checkpoint_identity,
                    on_reconciled=stream_callback,
                )
        except strip_segmentation.StripSegmentationError as exc:
            metadata = dict(admission_failure_metadata(exc.code))
            metadata.update(exc.safe_metadata)
            raise CloudStageError(
                exc.code,
                reviewable=exc.reviewable,
                safe_metadata=metadata,
            ) from None
        except CloudStageError as exc:
            metadata = dict(exc.safe_metadata)
            metadata.setdefault(
                "panel_admission", admission_failure_metadata(exc.code)["panel_admission"]
            )
            raise CloudStageError(
                exc.code,
                reviewable=exc.reviewable,
                safe_metadata=metadata,
            ) from None
        except Exception:
            code = "cloud.panel_coverage_incomplete"
            raise CloudStageError(code, safe_metadata=admission_failure_metadata(code)) from None
    if reconciliation.status != "RECONCILED" and review_only_auto_override:
        try:
            reconciliation = strip_segmentation.apply_review_only_overrides(
                reconciliation,
                actor_id="review-preview-auto",
                reason="review-only preview retained provider-confirmed separators and kept ambiguous artwork contiguous",
            )
        except strip_segmentation.StripSegmentationError as exc:
            raise CloudStageError(exc.code, reviewable=exc.reviewable) from None
    if reconciliation.status != "RECONCILED":
        review_codes = [
            report.review_code for report in reconciliation.reports if report.review_code
        ]
        code = review_codes[0] if review_codes else "segmentation.coverage_incomplete"
        raise CloudStageError(
            code,
            reviewable=True,
            safe_metadata=admission_failure_metadata(code),
        )
    panels: list[CloudPanelInput] = []
    for region in regions:
        panel = materialize_panel(region)
        panels.append(panel)
        admit_incremental_and_sink(panel)
    admission = admit_panel_inputs(
        panels,
        raw_image_count=len(assets),
        ingest_asset_count=len(inputs),
        candidate_regions=tuple(stream_candidate_regions),
        panel_hints=panel_hints,
        detector_version=PANEL_ADMISSION_DETECTOR_VERSION,
    )
    as_dict = getattr(reconciliation, "as_dict", None)
    segmentation_state = (
        dict(as_dict()) if callable(as_dict) else dict(getattr(reconciliation, "__dict__", {}))
    )
    segmentation_state["panel_admission"] = admission.ledger
    if admission.needs_review:
        raise CloudStageError(
            "segmentation.panel_admission_needs_review",
            reviewable=True,
            safe_metadata={
                "ledger_hash": admission.ledger["ledger_hash"],
                "needs_review": admission.ledger["counts"]["needs_review"],
            },
        )
    result = tuple(admission.admitted)
    if panel_sink is not None:
        for panel in result:
            if panel.panel_id not in stream_submitted_ids:
                panel_sink(panel)
    if return_segmentation:
        return result, segmentation_state
    return result


def _segmentation_checkpoint_identity(runner: Any) -> dict[str, Any]:
    """Build a compatibility-safe identity for durable source checkpoints."""

    identity: dict[str, Any] = {
        "model_identity_hash": str(
            getattr(getattr(runner, "model_identity", None), "identity_hash", "")
        ),
    }
    prompts = getattr(runner, "prompts", {})
    prompt = prompts.get("segmentation") if isinstance(prompts, Mapping) else None
    if isinstance(prompt, (tuple, list)) and len(prompt) >= 2:
        identity.update({"prompt_version": str(prompt[0]), "prompt_sha256": str(prompt[1])})
    return identity


def _project_source_asset_metadata(db: Any, project_id: str) -> tuple[dict[str, Any], ...]:
    from app.services import pipeline

    assets = pipeline.image_assets(pipeline.project_assets(db, project_id))
    return prepared_panel_manifest.source_asset_metadata(assets)


def _build_project_prepared_manifest(
    db: Any,
    project_id: str,
    panels: Sequence[CloudPanelInput],
    segmentation_state: Mapping[str, Any],
    *,
    feasible_visual_ledger: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source_assets = _project_source_asset_metadata(db, project_id)
    return prepared_panel_manifest.build_compact_manifest_from_descriptors(
        [panel.descriptor() for panel in panels],
        segmentation_state,
        panel_identity_hashes=_visual_panel_identity_hashes(tuple(panels)),
        source_identity_hash=_visual_source_hash(tuple(panels)),
        source_assets=source_assets,
    )


def _build_cached_prepared_manifest(
    db: Any,
    project_id: str,
    visual_stage: Mapping[str, Any],
    segmentation_state: Mapping[str, Any],
) -> dict[str, Any]:
    """Reconcile a metadata-only manifest from durable visual/strip stages."""

    visual = VisualStageResult.from_dict(visual_stage)
    source_assets = _project_source_asset_metadata(db, project_id)
    asset_by_id = {str(item["source_asset_id"]): item for item in source_assets}
    reports = segmentation_state.get("reports")
    if not isinstance(reports, list) or not reports:
        raise prepared_panel_manifest.PreparedPanelManifestError(
            "segmentation reports are unavailable"
        )
    reports_by_id = {
        str(report.get("source_asset_id", "")): report
        for report in reports
        if isinstance(report, Mapping) and str(report.get("source_asset_id", "")).strip()
    }
    if len(reports_by_id) != len(reports):
        raise prepared_panel_manifest.PreparedPanelManifestError(
            "segmentation report lineage is duplicated"
        )

    def report_key(report: Mapping[str, Any]) -> tuple[int, int, str]:
        asset = asset_by_id.get(str(report.get("source_asset_id", "")), {})
        return (
            int(asset.get("strip_order", 0)),
            int(asset.get("region_order", 0)),
            str(report.get("source_asset_id", "")),
        )

    canonical_regions: list[tuple[str, str, tuple[int, int], tuple[int, int, int, int], str]] = []
    for report in sorted(reports_by_id.values(), key=report_key):
        asset_id = str(report.get("source_asset_id", ""))
        asset = asset_by_id.get(asset_id)
        spans = report.get("spans")
        dimensions = report.get("source_dimensions")
        if (
            asset is None
            or not isinstance(spans, list)
            or (
                not isinstance(dimensions, list)
                and not (
                    isinstance(report.get("width"), int) and isinstance(report.get("height"), int)
                )
            )
        ):
            raise prepared_panel_manifest.PreparedPanelManifestError(
                "segmentation report geometry is malformed"
            )
        if isinstance(dimensions, list) and len(dimensions) == 2:
            width, height = (int(dimensions[0]), int(dimensions[1]))
        else:
            width, height = (int(report["width"]), int(report["height"]))
        if width <= 0 or height <= 0:
            raise prepared_panel_manifest.PreparedPanelManifestError(
                "segmentation report dimensions are invalid"
            )
        checksum = str(report.get("source_checksum", ""))
        if checksum != asset["source_checksum"]:
            raise prepared_panel_manifest.PreparedPanelManifestError(
                "segmentation report source checksum mismatch"
            )
        coverage_hash = str(report.get("analysis_hash", ""))
        if len(coverage_hash) != 64:
            coverage_hash = ""
        for span in spans:
            if (
                not isinstance(span, list)
                or len(span) != 2
                or any(isinstance(value, bool) or not isinstance(value, int) for value in span)
                or span[0] < 0
                or span[1] <= span[0]
                or span[1] > height
            ):
                raise prepared_panel_manifest.PreparedPanelManifestError(
                    "segmentation span is invalid"
                )
            canonical_regions.append(
                (
                    asset_id,
                    checksum,
                    (width, height),
                    (0, int(span[0]), width, int(span[1])),
                    coverage_hash,
                )
            )

    identity_hashes = tuple(str(item) for item in visual.panel_identity_hashes)
    if len(identity_hashes) != len(visual.panels):
        row_identity_hashes = tuple(
            str(item.get("cache_identity_hash", ""))
            for item in visual.panels
            if isinstance(item, Mapping)
        )
        if len(row_identity_hashes) != len(visual.panels) or any(
            len(item) != 64 for item in row_identity_hashes
        ):
            raise prepared_panel_manifest.PreparedPanelManifestError(
                "cached visual identity count is incomplete"
            )
        identity_hashes = row_identity_hashes
    descriptors: list[dict[str, Any]] = []
    seen_panel_ids: set[str] = set()
    previous_source_order = -1
    for visual_index, panel in enumerate(visual.panels):
        if not isinstance(panel, Mapping):
            raise prepared_panel_manifest.PreparedPanelManifestError(
                "cached visual panel is malformed"
            )
        panel_id = str(panel.get("panel_id", "")).strip()
        try:
            source_order = panel["source_order"]
        except (KeyError, TypeError, ValueError):
            raise prepared_panel_manifest.PreparedPanelManifestError(
                "cached visual source order is malformed"
            ) from None
        if (
            not panel_id
            or panel_id in seen_panel_ids
            or isinstance(source_order, bool)
            or not isinstance(source_order, int)
            or source_order < 0
            or source_order <= previous_source_order
        ):
            raise prepared_panel_manifest.PreparedPanelManifestError(
                "cached visual panel order or identity is invalid"
            )
        seen_panel_ids.add(panel_id)
        previous_source_order = source_order
        if source_order >= len(canonical_regions):
            raise prepared_panel_manifest.PreparedPanelManifestError(
                "cached visual source order is outside segmentation coverage"
            )
        asset_id, checksum, dimensions, bounds, coverage_hash = canonical_regions[source_order]
        if (
            str(panel.get("source_asset_id", "")) != asset_id
            or str(panel.get("source_checksum", "")) != checksum
        ):
            raise prepared_panel_manifest.PreparedPanelManifestError(
                "cached visual panel does not match segmentation lineage"
            )
        cached_bounds = panel.get("panel_bounds")
        if cached_bounds is not None and list(cached_bounds) != list(bounds):
            raise prepared_panel_manifest.PreparedPanelManifestError(
                "cached visual panel crop identity mismatch"
            )
        cached_dimensions = panel.get("source_dimensions")
        if cached_dimensions is not None and list(cached_dimensions) != list(dimensions):
            raise prepared_panel_manifest.PreparedPanelManifestError(
                "cached visual panel dimensions mismatch"
            )
        cached_coverage_hash = str(panel.get("coverage_map_hash", ""))
        if cached_coverage_hash and cached_coverage_hash != coverage_hash:
            raise prepared_panel_manifest.PreparedPanelManifestError(
                "cached visual coverage identity mismatch"
            )
        cached_identity_hash = str(panel.get("cache_identity_hash", ""))
        if cached_identity_hash and cached_identity_hash != identity_hashes[visual_index]:
            raise prepared_panel_manifest.PreparedPanelManifestError(
                "cached visual payload identity mismatch"
            )
        descriptors.append(
            {
                "panel_id": panel_id,
                "source_asset_id": asset_id,
                "source_order": source_order,
                "prepared_order": visual_index,
                "source_checksum": checksum,
                "panel_bounds": list(bounds),
                "source_dimensions": list(dimensions),
                "strip_region_id": str(panel.get("strip_region_id", panel_id)),
                "coverage_map_version": str(segmentation_state.get("version", "")),
                "coverage_map_hash": coverage_hash,
                "segmentation_version": str(segmentation_state.get("detector_version", "")),
                "source_family": str(asset_by_id[asset_id].get("source_family", "")),
                "identity_descriptor_hash": identity_hashes[visual_index],
                "identity_payload_checksum": identity_hashes[visual_index],
                "source_identity_hash": visual.source_hash,
                "metadata_only": True,
            }
        )
    return prepared_panel_manifest.build_compact_manifest_from_descriptors(
        descriptors,
        segmentation_state,
        panel_identity_hashes=identity_hashes,
        source_identity_hash=visual.source_hash,
        source_assets=source_assets,
    )


def _restore_project_prepared_manifest(
    db: Any,
    project_id: str,
    raw_manifest: Mapping[str, Any],
    *,
    segmentation_state: Mapping[str, Any] | None = None,
) -> tuple[tuple[CloudPanelInput, ...], dict[str, Any]]:
    manifest = prepared_panel_manifest.validate_manifest(raw_manifest)
    prepared_panel_manifest.require_source_assets_match(
        manifest,
        _project_source_asset_metadata(db, project_id),
    )
    panels = prepared_panel_manifest.restore_cloud_panels(manifest, CloudPanelInput)
    if _visual_source_hash(panels) != manifest.source_identity_hash:
        raise prepared_panel_manifest.PreparedPanelManifestError(
            "prepared panel source identity mismatch"
        )
    restored_segmentation = dict(manifest.segmentation_state)
    if manifest.manifest_version == prepared_panel_manifest.COMPACT_MANIFEST_VERSION:
        dependency_hash = str(restored_segmentation.get("segmentation_dependency_hash", ""))
        if segmentation_state is not None and _hash(dict(segmentation_state)) != dependency_hash:
            raise prepared_panel_manifest.PreparedPanelManifestError(
                "segmentation dependency hash mismatch"
            )
        if segmentation_state is not None:
            restored_segmentation = dict(segmentation_state)
    return panels, restored_segmentation


def _visual_panel_chunks(
    panels: Sequence[CloudPanelInput],
    *,
    max_panels: int = VISUAL_REQUEST_MAX_PANELS,
    max_estimated_bytes: int = VISUAL_REQUEST_MAX_ESTIMATED_BYTES,
    overlap: int = VISUAL_REQUEST_OVERLAP,
) -> tuple[tuple[CloudPanelInput, ...], ...]:
    """Partition ordered panels into bounded, deterministic visual requests."""

    if max_panels <= 0 or max_estimated_bytes <= 0 or overlap < 0 or overlap >= max_panels:
        raise ValueError("invalid visual request chunk limits")
    ordered = tuple(panels)
    chunks: list[tuple[CloudPanelInput, ...]] = []
    start = 0
    while start < len(ordered):
        current: list[CloudPanelInput] = []
        estimated = 0
        index = start
        while index < len(ordered) and len(current) < max_panels:
            panel = ordered[index]
            panel_estimate = _visual_request_estimated_bytes(panel)
            if current and estimated + panel_estimate > max_estimated_bytes:
                break
            current.append(panel)
            estimated += panel_estimate
            index += 1
        if not current:
            current.append(ordered[start])
            index = start + 1
        chunks.append(tuple(current))
        if index >= len(ordered):
            break
        start = max(index - overlap, start + 1)
    return tuple(chunks)


def _visual_observation_has_visible_facts(row: Mapping[str, Any]) -> bool:
    """Return whether one visual row can satisfy the narration observation gate."""

    return _visual_observation_failure_predicate(row) is None


def _visual_observation_failure_predicate(row: Mapping[str, Any]) -> str | None:
    """Return a non-prose predicate for a visual row that fails semantic admission."""

    observation = row.get("observation", row)
    if not isinstance(observation, Mapping):
        return "observation_mapping"
    values = observation.get("visible_facts")
    if not isinstance(values, list) or not values:
        return "visible_facts_nonempty"
    for value in values:
        if isinstance(value, str) and value.strip():
            continue
        if isinstance(value, Mapping) and any(
            isinstance(candidate, str) and candidate.strip() for candidate in value.values()
        ):
            continue
        return "visible_facts_item"
    return None


def _visual_cached_row_is_reusable(row: Mapping[str, Any], panel: CloudPanelInput | None) -> bool:
    """Validate cached lineage plus the shared analyzer's fact prerequisite."""

    if panel is None:
        return False
    if (
        row.get("fallback_mode") == "conservative_full_panel_v1"
        and row.get("targeted_geometry_repair_attempted") is not True
    ):
        # Older review rows were admitted after the multi-panel response left
        # geometry unknown. Revisit each such row exactly once through the
        # singleton geometry boundary; once that bounded attempt is recorded,
        # the conservative fallback is a stable, auditable cache result.
        return False
    return (
        str(row.get("panel_id", "")) == panel.panel_id
        and str(row.get("source_asset_id", "")) == panel.source_asset_id
        and int(row.get("source_order", -1)) == int(panel.source_order)
        and str(row.get("source_checksum", "")) == panel.source_checksum
        and _visual_observation_has_visible_facts(row)
    )


def _visual_analysis_windows(panel: CloudPanelInput) -> tuple[dict[str, Any], ...]:
    """Return complete overlapping detail windows for one unusually tall panel.

    The windows are ephemeral provider inputs only.  They never become source
    assets or panel IDs, so the canonical lineage and chronology remain tied to
    the original panel while the vision model still receives readable detail.
    """

    try:
        import io

        from PIL import Image, UnidentifiedImageError

        with Image.open(io.BytesIO(panel.payload)) as source:
            source.load()
            image = source.convert("RGB")
    except (OSError, ValueError, UnidentifiedImageError):
        return ()
    width, height = image.size
    if width <= 0 or height <= 0 or height / width < VISUAL_ANALYSIS_WINDOW_MIN_RATIO:
        return ()

    tile_height = max(1024, int(round(width * 2.0)))
    tile_height = min(height, tile_height)
    overlap = max(128, int(round(tile_height * VISUAL_ANALYSIS_WINDOW_OVERLAP_FRACTION)))
    overlap = min(overlap, max(0, tile_height - 1))
    step = max(1, tile_height - overlap)
    estimated_count = 1 + max(0, (height - tile_height + step - 1) // step)
    if estimated_count > VISUAL_ANALYSIS_WINDOW_MAX_COUNT:
        # Grow windows just enough to keep the complete source within the hard
        # request-count bound; overlap remains explicit and non-zero.
        tile_height = min(
            height,
            max(
                tile_height,
                (height + VISUAL_ANALYSIS_WINDOW_MAX_COUNT - 1) // VISUAL_ANALYSIS_WINDOW_MAX_COUNT
                + overlap,
            ),
        )
        overlap = max(128, int(round(tile_height * VISUAL_ANALYSIS_WINDOW_OVERLAP_FRACTION)))
        overlap = min(overlap, max(0, tile_height - 1))
        step = max(1, tile_height - overlap)

    windows: list[dict[str, Any]] = []
    start = 0
    index = 0
    while start < height:
        end = min(height, start + tile_height)
        crop = image.crop((0, start, width, end))
        preview = crop
        preview.thumbnail(
            (VISUAL_ANALYSIS_WINDOW_MAX_WIDTH, VISUAL_ANALYSIS_WINDOW_MAX_HEIGHT),
            Image.Resampling.LANCZOS,
        )
        output = io.BytesIO()
        preview.save(
            output,
            format="JPEG",
            quality=VISUAL_ANALYSIS_WINDOW_JPEG_QUALITY,
            optimize=True,
            progressive=False,
            subsampling=2,
        )
        payload = output.getvalue()
        windows.append(
            {
                "window_index": index,
                "y0": start,
                "y1": end,
                "overlap_above": 0 if start == 0 else overlap,
                "overlap_below": 0 if end == height else overlap,
                "mime_type": "image/jpeg",
                "encoded_width": preview.width,
                "encoded_height": preview.height,
                "payload": payload,
            }
        )
        if end == height:
            break
        next_start = end - overlap
        if next_start <= start:
            break
        start = next_start
        index += 1
    if not windows or windows[0]["y0"] != 0 or windows[-1]["y1"] != height:
        return ()
    if len(windows) > VISUAL_ANALYSIS_WINDOW_MAX_COUNT:
        return ()
    return tuple(windows)


def _window_geometry_bbox(
    bbox: Sequence[float] | None,
    *,
    y0: int,
    y1: int,
    full_height: int,
) -> list[float] | None:
    if bbox is None or len(bbox) != 4 or full_height <= 0 or y1 <= y0:
        return None
    x0, top, x1, bottom = (float(value) for value in bbox)
    span = y1 - y0
    return [
        x0,
        (y0 + top * span) / full_height,
        x1,
        (y0 + bottom * span) / full_height,
    ]


def _window_geometry_polygon(
    polygon: Sequence[Sequence[float]] | None,
    *,
    y0: int,
    y1: int,
    full_height: int,
) -> list[list[float]] | None:
    if not polygon:
        return None
    span = y1 - y0
    if full_height <= 0 or span <= 0:
        return None
    return [[float(point[0]), (y0 + float(point[1]) * span) / full_height] for point in polygon]


def _geometry_region_bbox(region: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    bbox = region.get("normalized_bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        return tuple(float(value) for value in bbox)
    polygon = region.get("normalized_polygon")
    if isinstance(polygon, (list, tuple)) and polygon:
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
        return (min(xs), min(ys), max(xs), max(ys))
    return None


def _geometry_iou(
    left: tuple[float, float, float, float] | None,
    right: tuple[float, float, float, float] | None,
) -> float:
    if left is None or right is None:
        return 0.0
    x0 = max(left[0], right[0])
    y0 = max(left[1], right[1])
    x1 = min(left[2], right[2])
    y1 = min(left[3], right[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    if intersection <= 0.0:
        return 0.0
    la = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    ra = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    return intersection / max(1e-9, la + ra - intersection)


def _dedupe_window_geometry_regions(
    regions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for raw in sorted(
        (dict(region) for region in regions),
        key=lambda region: (
            -float(region.get("confidence", 0.0)),
            str(region.get("kind", "")),
            str(region.get("region_id", "")),
        ),
    ):
        bbox = _geometry_region_bbox(raw)
        duplicate = next(
            (
                existing
                for existing in kept
                if existing.get("kind") == raw.get("kind")
                and _geometry_iou(_geometry_region_bbox(existing), bbox) >= 0.55
            ),
            None,
        )
        if duplicate is None:
            kept.append(raw)
    kept.sort(
        key=lambda region: (
            (_geometry_region_bbox(region) or (0.0, 0.0, 0.0, 0.0))[1],
            (_geometry_region_bbox(region) or (0.0, 0.0, 0.0, 0.0))[0],
            str(region.get("kind", "")),
            str(region.get("region_id", "")),
        )
    )
    for index, region in enumerate(kept):
        region["region_id"] = f"window-region-{index:03d}"
        region["evidence_source"] = VISUAL_WINDOW_GEOMETRY_VERSION
    return kept


def _reconcile_window_geometry(
    panel: CloudPanelInput,
    windows: Sequence[Mapping[str, Any]],
    evidence_by_window: Mapping[int, Mapping[str, Any]],
) -> dict[str, Any] | None:
    if not windows or len(evidence_by_window) != len(windows):
        return None
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(panel.payload)) as source:
            full_height = int(source.height)
    except Exception:
        return None
    balloon_regions: list[dict[str, Any]] = []
    protected_regions: list[dict[str, Any]] = []
    confidences: list[float] = []
    for window in windows:
        index = int(window["window_index"])
        visual = evidence_by_window.get(index)
        if not isinstance(visual, Mapping):
            return None
        status = visual.get("balloon_mask_status")
        if status not in {"known_empty", "known_nonempty"}:
            return None
        confidence = visual.get("mask_confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            return None
        confidences.append(float(confidence))
        y0 = int(window["y0"])
        y1 = int(window["y1"])
        for group, destination in (
            ("balloon_regions", balloon_regions),
            ("protected_regions", protected_regions),
        ):
            for raw in visual.get(group, ()):
                if not isinstance(raw, Mapping):
                    return None
                mapped = dict(raw)
                mapped["normalized_bbox"] = _window_geometry_bbox(
                    raw.get("normalized_bbox"), y0=y0, y1=y1, full_height=full_height
                )
                mapped["normalized_polygon"] = _window_geometry_polygon(
                    raw.get("normalized_polygon"), y0=y0, y1=y1, full_height=full_height
                )
                mapped["region_id"] = f"w{index}-{raw.get('region_id', 'region')}"
                mapped["evidence_source"] = VISUAL_WINDOW_GEOMETRY_VERSION
                destination.append(mapped)
    balloons = _dedupe_window_geometry_regions(balloon_regions)
    protected = _dedupe_window_geometry_regions(protected_regions)
    # Region IDs share one namespace in the validator.
    for index, region in enumerate(protected):
        region["region_id"] = f"window-protected-{index:03d}"
    return {
        "contract_version": visual_scoring.VISUAL_EVIDENCE_CONTRACT_VERSION,
        "panel_id": panel.panel_id,
        "source_asset_id": panel.source_asset_id,
        "source_order": panel.source_order,
        "balloon_regions": balloons,
        "protected_regions": protected,
        "balloon_mask_status": "known_nonempty" if balloons else "known_empty",
        "mask_confidence": min(confidences) if confidences else 0.0,
        "evidence_source": VISUAL_WINDOW_GEOMETRY_VERSION,
        "mask_reason": "all overlapping detail windows returned validated geometry and were reconciled into canonical panel coordinates",
    }


def _visual_request_panel(panel: CloudPanelInput) -> dict[str, Any]:
    overview_payload, overview_mime = _visual_provider_payload(panel)
    result = {
        **panel.descriptor(),
        "mime_type": overview_mime,
        "payload": overview_payload,
    }
    windows = _visual_analysis_windows(panel)
    if windows:
        try:
            import io

            from PIL import Image

            with Image.open(io.BytesIO(panel.payload)) as source:
                source_size = [int(source.width), int(source.height)]
        except Exception:
            source_size = []
        if len(source_size) == 2:
            result["analysis_window_version"] = VISUAL_ANALYSIS_WINDOW_VERSION
            result["analysis_window_source_size"] = source_size
            result["analysis_windows"] = windows
    return result


def _visual_request_estimated_bytes(panel: CloudPanelInput) -> int:
    request_panel = _visual_request_panel(panel)
    payloads = [request_panel["payload"]]
    payloads.extend(
        window["payload"]
        for window in request_panel.get("analysis_windows", ())
        if isinstance(window, Mapping) and isinstance(window.get("payload"), bytes)
    )
    return sum((len(payload) * 4 + 2) // 3 + 768 for payload in payloads)


def _visual_provider_payload(panel: CloudPanelInput) -> tuple[bytes, str]:
    """Bound provider image size while leaving persisted panel bytes untouched."""

    needs_resize = len(panel.payload) > 180_000
    if not needs_resize and panel.mime_type.lower() in {"image/jpeg", "image/jpg"}:
        return panel.payload, panel.mime_type
    try:
        import io

        from PIL import Image, UnidentifiedImageError

        with Image.open(io.BytesIO(panel.payload)) as source:
            source.load()
            image = source.convert("RGB")
        if needs_resize:
            image.thumbnail((384, 576), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(
            output, format="JPEG", quality=68, optimize=True, progressive=False, subsampling=2
        )
        encoded = output.getvalue()
    except (OSError, ValueError, UnidentifiedImageError):
        return panel.payload, panel.mime_type
    return encoded, "image/jpeg"


def _visual_render_policy_version(panel: CloudPanelInput) -> str:
    """Version only identities whose provider bytes can change."""

    if len(panel.payload) <= 180_000 and panel.mime_type.lower() not in {"image/jpeg", "image/jpg"}:
        return f"{VISUAL_RENDER_PAYLOAD_VERSION}:{VISUAL_CANONICAL_JPEG_VERSION}"
    return VISUAL_RENDER_PAYLOAD_VERSION


def persist_cloud_chapter(
    db: Any,
    project_id: str,
    panels: Sequence[CloudPanelInput],
    result: ChapterResult,
    *,
    model_identity: CloudModelIdentity,
    actor_id: str = "",
) -> tuple[Any, Any]:
    """Persist only a fully reconciled cloud result through existing pipeline gates."""

    from app.models import PanelRegion, SourceAsset, StoryAnalysis
    from app.services import analyzer_contract, narrative_identity, pipeline, visual_scoring

    if result.state != ChapterState.READY_TO_RENDER:
        raise CloudStageError("cloud.stage_not_ready")
    ordered = CloudStageRunner._ordered_panels(tuple(panels))
    visual_by_id = {item["panel_id"]: item for item in result.visual.panels}
    if tuple(item.panel_id for item in ordered) != result.visual.panel_ids:
        raise CloudStageError("cloud.panel_lineage_invalid")
    if len(result.narration.observations) != len(ordered):
        print(
            "PERSIST_COVERAGE_FAIL obs="
            + str(len(result.narration.observations))
            + " ordered="
            + str(len(ordered)),
            file=sys.stderr,
            flush=True,
        )
        raise CloudStageError("cloud.panel_coverage_incomplete")

    try:
        instruction_version, instruction_sha256, _ = analyzer_contract.load_analyzer_instruction(
            narrative_profile_id="sharp_friend_v1"
        )
    except Exception:
        raise CloudStageError("cloud.prompt_invalid") from None

    coverage_versions = {panel.coverage_map_version for panel in ordered}
    coverage_hashes = {panel.coverage_map_hash for panel in ordered}
    if (
        len(coverage_versions) != 1
        or len(coverage_hashes) != 1
        or not next(iter(coverage_versions))
        or not next(iter(coverage_hashes))
    ):
        raise CloudStageError("cloud.panel_lineage_invalid")
    coverage_version = next(iter(coverage_versions))
    coverage_hash = next(iter(coverage_hashes))
    panel_ids = tuple(panel.panel_id for panel in ordered)
    persisted_observations: list[dict[str, Any]] = []
    panel_rows: list[PanelRegion] = []
    for index, (panel, source_observation) in enumerate(
        zip(ordered, result.narration.observations, strict=True)
    ):
        if panel.panel_bounds is None or panel.source_dimensions is None:
            raise CloudStageError("cloud.panel_lineage_invalid")
        asset = db.get(SourceAsset, panel.source_asset_id)
        if asset is None:
            raise CloudStageError("cloud.panel_lineage_invalid")
        stored_checksum = asset.original_checksum or asset.checksum
        if panel.source_checksum != stored_checksum:
            raise CloudStageError("cloud.source_checksum_mismatch")
        visual = visual_by_id.get(panel.panel_id)
        if visual is None or visual.get("source_checksum") != panel.source_checksum:
            raise CloudStageError("cloud.panel_lineage_invalid")
        visual_evidence = visual.get("visual_evidence")
        try:
            parsed_evidence = visual_scoring.parse_panel_visual_evidence(visual_evidence)
            if (
                parsed_evidence.panel_id != panel.panel_id
                or parsed_evidence.source_asset_id != panel.source_asset_id
                or parsed_evidence.source_order != panel.source_order
            ):
                raise ValueError
        except Exception:
            raise CloudStageError("cloud.panel_lineage_invalid") from None

        bounds = panel.panel_bounds
        observation = dict(source_observation)
        observation.update(
            {
                "panel_id": panel.panel_id,
                "source_asset_id": panel.source_asset_id,
                "strip_region_id": panel.strip_region_id or panel.panel_id,
                "source_index": index,
                "region_bounds": {
                    "x": bounds[0],
                    "y": bounds[1],
                    "width": bounds[2] - bounds[0],
                    "height": bounds[3] - bounds[1],
                },
                "coverage_map_version": coverage_version,
                "coverage_map_hash": coverage_hash,
                "visual_evidence": dict(visual_evidence),
            }
        )
        if (
            not isinstance(observation.get("evidence_refs"), list)
            or panel.panel_id not in observation["evidence_refs"]
        ):
            print(
                "GROUND_FAIL panel="
                + str(panel.panel_id)
                + " refs="
                + repr(observation.get("evidence_refs"))[:300],
                file=sys.stderr,
                flush=True,
            )
            raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
        persisted_observations.append(observation)
        panel_rows.append(
            PanelRegion(
                source_asset_id=panel.source_asset_id,
                source_asset_checksum=panel.source_checksum,
                original_width=panel.source_dimensions[0],
                original_height=panel.source_dimensions[1],
                strip_region_id=panel.strip_region_id or panel.panel_id,
                panel_id=panel.panel_id,
                source_order=panel.source_order,
                bounds_json=observation["region_bounds"],
                region_class="canonical_panel",
                segmentation_confidence=1.0,
                segmentation_version=coverage_version,
                coverage_map_hash=coverage_hash,
                observation_json=observation,
                evidence_refs_json=list(observation["evidence_refs"]),
            )
        )

    output = {
        "observations": persisted_observations,
        "continuity_ledger": dict(result.narration.continuity_ledger),
        "evidence_graph": dict(result.narration.evidence_graph),
        "coverage_manifest": {
            "total_panels": len(ordered),
            "processed_panels": len(ordered),
            "total_canonical_panels": len(ordered),
            "persisted_canonical_panels": len(ordered),
            "processed_canonical_panel_count": len(ordered),
            "panel_ids": list(panel_ids),
            "source_content_coverage_ratio": 1.0,
            "unresolved_material_area": 0,
            "material_unresolved_regions": [],
            "reconciliation_complete": True,
            "coverage_map_version": coverage_version,
            "coverage_map_hash": coverage_hash,
        },
        "narrative_outline": {
            "story_spine": dict(result.narration.story_spine),
            "ending_kind": result.narration.ending_kind,
        },
        "script_passages": [dict(item) for item in result.narration.passages],
    }
    output["evidence_graph"]["script_passages"] = list(output["script_passages"])
    validator_output = dict(output)
    validator_output["observations"] = [
        {key: value for key, value in observation.items() if key != "visual_evidence"}
        for observation in persisted_observations
    ]
    try:
        analyzer_contract.validate_analyzer_output(
            validator_output,
            expected_panel_ids=panel_ids,
            narrative_profile_id="sharp_friend_v1",
        )
    except Exception:
        raise CloudStageError("cloud.narrative_not_grounded", reviewable=True) from None

    narration_qc = result.narration.qc_report if isinstance(result.narration.qc_report, Mapping) else {}
    raw_duration_policy = narration_qc.get("duration_policy_contract")
    if not isinstance(raw_duration_policy, Mapping):
        for container_key in ("visual_repair_text_only_duration_repair_v1", "narration_repair"):
            container = narration_qc.get(container_key)
            if isinstance(container, Mapping) and isinstance(container.get("duration_policy_contract"), Mapping):
                raw_duration_policy = container["duration_policy_contract"]
                break
    duration_policy_contract = (
        _narration_repair_contract_bounds(raw_duration_policy)
        if isinstance(raw_duration_policy, Mapping)
        else None
    )

    row = StoryAnalysis(
        project_id=project_id,
        analysis_run_id=f"cloud-{result.visual.source_hash[:24]}",
        state="RECONCILED",
        provider_type=model_identity.provider,
        provider_name=model_identity.provider,
        model_name=model_identity.model,
        instruction_version=instruction_version,
        instruction_sha256=instruction_sha256,
        coverage_manifest_json=output["coverage_manifest"],
        continuity_ledger_json=output["continuity_ledger"],
        evidence_graph_json=output["evidence_graph"],
        story_spine_json=output["narrative_outline"]["story_spine"],
        blocking_reasons_json=None,
        reconciliation_json={
            "coverage_map_version": coverage_version,
            "coverage_map_hash": coverage_hash,
            "canonical_panel_count": len(ordered),
            "processed_panel_count": len(ordered),
            "chain_reconciled": True,
            "chain_errors": [],
            "provenance": "cloud_multimodal_mass_production_v1",
            "random_sampling": False,
            "model_identity": model_identity.as_dict(),
            "model_identity_hash": model_identity.identity_hash,
            "stage_prompt_versions": dict(model_identity.prompt_versions),
            "stage_prompt_hashes": {
                "visual": result.visual.prompt_sha256,
                "story_map": result.story_map.prompt_sha256,
                "narration": result.narration.prompt_sha256,
            },
            "narrative_identity": {
                "profile_id": narrative_identity.SHARP_FRIEND_V1.profile_id,
                "version": narrative_identity.SHARP_FRIEND_V1.profile_version,
                "sha256": narrative_identity.SHARP_FRIEND_V1.contract_sha256,
            },
            "narrative_ending_kind": result.narration.ending_kind,
            "narrative_screening_warning_codes": list(
                result.narration.qc_report.get("warnings", [])
            ),
            "requires_voice_timing": True,
            "duration_policy_contract": duration_policy_contract,
        },
    )
    pipeline._derive_legacy_fields(row, output)
    db.add(row)
    db.flush()
    for panel_row in panel_rows:
        panel_row.story_analysis_id = row.id
    db.add_all(panel_rows)
    db.flush()
    try:
        script_row = pipeline.generate_script(
            db,
            project_id,
            actor_id=actor_id,
            narrative_profile_id="sharp_friend_v1",
            analysis_id=row.id,
        )
    except Exception:
        import traceback as _tb

        _tb.print_exc()
        raise CloudStageError("cloud.persistence_failed") from None
    return row, script_row


@dataclass
class ChapterJobRecord:
    job_id: str
    state: ChapterState = ChapterState.INGESTED
    stage_results: dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    error_message: str = ""
    review_queue: list[dict[str, str]] = field(default_factory=list)
    model_identity_hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "state": self.state.value,
            "stage_results": self.stage_results,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "review_queue": self.review_queue,
            "model_identity_hash": self.model_identity_hash,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ChapterJobRecord:
        return cls(
            job_id=str(value["job_id"]),
            state=ChapterState(str(value.get("state", ChapterState.INGESTED.value))),
            stage_results=dict(value.get("stage_results", {})),
            error_code=str(value.get("error_code", "")),
            error_message=str(value.get("error_message", "")),
            review_queue=list(value.get("review_queue", [])),
            model_identity_hash=str(value.get("model_identity_hash", "")),
        )


class JsonJobStore:
    """Small ignored local state store; no schema migration or DB artifact."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def load(self, job_id: str) -> ChapterJobRecord | None:
        _validate_job_id(job_id)
        path = self.root / f"{job_id}.json"
        if not path.is_file():
            return None
        try:
            return ChapterJobRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, KeyError):
            raise CloudStageError("cloud.job_state_invalid") from None

    def save(self, record: ChapterJobRecord) -> None:
        _validate_job_id(record.job_id)
        path = self.root / f"{record.job_id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(_canonical(record.as_dict()), encoding="utf-8")
        tmp.replace(path)


def _validate_job_id(job_id: str) -> None:
    if (
        not isinstance(job_id, str)
        or not job_id.strip()
        or job_id in {".", ".."}
        or any(separator in job_id for separator in ("/", "\\"))
    ):
        raise CloudStageError("cloud.job_id_invalid")


def _build_ephemeral_review_candidates(
    panels: Sequence[CloudPanelInput],
    visual: VisualStageResult,
    story_map: StoryMapResult,
    *,
    profile: object,
    review_source_upscale_policy: object,
) -> tuple[tuple[object, ...], dict[str, tuple[str, ...]]]:
    """Build the review ledger before narration is durable.

    Review-only repair may be needed when the first narration response is not
    grounded.  At that point visual/story stages are durable in the job store,
    but ``PanelRegion``/``ScriptVersion`` rows do not exist yet.  This helper
    uses the exact panel payloads and reconciled visual sidecars already held
    by the job, then hands them to the same panel-keyed candidate builder used
    after persistence.  It never invents evidence or promotes this transient
    registry to production state.
    """

    from types import SimpleNamespace

    from PIL import Image, UnidentifiedImageError

    from app.services import (
        reference_profile,
        reference_visual_review,
        review_source_upscale,
        visual_scoring,
    )

    visual_by_id = {
        str(row.get("panel_id")): row
        for row in visual.panels
        if isinstance(row, Mapping) and str(row.get("panel_id", "")).strip()
    }
    ordered_panels = tuple(
        sorted(
            (
                panel
                for panel in panels
                if int(panel.source_order) > 0 and str(panel.panel_id) in visual_by_id
            ),
            key=lambda panel: (int(panel.source_order), str(panel.panel_id)),
        )
    )
    if not ordered_panels:
        raise CloudStageError("visual.panel_lineage_unavailable", reviewable=True)

    beat_by_id = {
        str(beat.get("beat_id")): beat
        for beat in story_map.beats
        if isinstance(beat, Mapping) and str(beat.get("beat_id", "")).strip()
    }
    section_names = ("hook", "setup", "conflict", "twist", "cta")
    section_to_beats = visual_narrative_repair.default_section_to_beats(
        section_names,
        story_map.beats,
    )
    valid_panel_ids = {panel.panel_id for panel in ordered_panels}
    claim_panel_ids = {
        str(panel_id)
        for claim in story_map.claims
        if isinstance(claim, Mapping)
        for panel_id in (claim.get("panel_ids") or ())
        if str(panel_id).strip()
    }
    beat_evidence: dict[str, tuple[str, ...]] = {}
    for beat_id, beat in beat_by_id.items():
        panel_ids: list[str] = []
        for panel_id in beat.get("panel_ids", ()):
            panel_id = str(panel_id)
            if (
                panel_id in valid_panel_ids
                and (not claim_panel_ids or panel_id in claim_panel_ids)
                and panel_id not in panel_ids
            ):
                panel_ids.append(panel_id)
        if panel_ids:
            beat_evidence[beat_id] = tuple(panel_ids)
    if not beat_evidence:
        raise CloudStageError("visual.panel_lineage_unavailable", reviewable=True)

    panel_regions: list[object] = []
    panel_candidates: dict[str, object] = {}
    panel_crops: dict[str, Image.Image] = {}
    upscale_manifests: dict[str, Mapping[str, Any]] = {}
    for panel in ordered_panels:
        # Repair claims may only cite original StoryMap claim evidence. Keep
        # the full beat mapping above for missing-section detection, but avoid
        # decoding/scoring panels that can never pass claim-lineage validation.
        if claim_panel_ids and panel.panel_id not in claim_panel_ids:
            continue
        raw_visual = visual_by_id.get(panel.panel_id)
        raw_evidence = raw_visual.get("visual_evidence") if raw_visual else None
        if not isinstance(raw_evidence, Mapping):
            raise CloudStageError("visual.panel_lineage_unavailable", reviewable=True)
        try:
            with Image.open(io.BytesIO(panel.payload)) as source:
                source.load()
                crop = source.convert("RGB")
        except (OSError, UnidentifiedImageError, ValueError):
            raise CloudStageError("visual.panel_lineage_unavailable", reviewable=True) from None

        # Gutter slivers (a few px tall) drive upscale scale into the hundreds
        # and blow PIL's decompression-bomb limit. Skip them like the loader does.
        if not reference_profile.review_panel_source_geometry_is_renderable(crop.size):
            continue

        if review_source_upscale_policy is not None:
            bounds = panel.panel_bounds or (0, 0, crop.width, crop.height)
            dimensions = panel.source_dimensions or crop.size
            try:
                prepared, manifest = review_source_upscale.prepare_review_panel(
                    crop,
                    policy=review_source_upscale_policy,
                    source_asset_id=panel.source_asset_id,
                    panel_region_id=panel.panel_id,
                    source_asset_checksum=panel.source_checksum,
                    source_panel_bounds=tuple(int(value) for value in bounds),
                    source_dimensions=tuple(int(value) for value in dimensions),
                )
            except review_source_upscale.ReviewSourceUpscaleError as exc:
                raise CloudStageError(exc.code, reviewable=True) from None
            crop.close()
            crop = prepared
            upscale_manifests[panel.panel_id] = manifest
            prepared_bounds = review_source_upscale.transform_panel_bounds(
                tuple(int(value) for value in bounds), manifest
            )
        else:
            prepared_bounds = tuple(
                int(value) for value in (panel.panel_bounds or (0, 0, crop.width, crop.height))
            )

        region = SimpleNamespace(
            id=panel.panel_id,
            source_asset_id=panel.source_asset_id,
            source_asset_checksum=panel.source_checksum,
            original_width=int(panel.source_dimensions[0])
            if panel.source_dimensions
            else int(crop.width),
            original_height=int(panel.source_dimensions[1])
            if panel.source_dimensions
            else int(crop.height),
            strip_region_id=panel.strip_region_id or panel.panel_id,
            panel_id=panel.panel_id,
            source_order=int(panel.source_order),
            bounds_json={
                "x": prepared_bounds[0],
                "y": prepared_bounds[1],
                "width": prepared_bounds[2] - prepared_bounds[0],
                "height": prepared_bounds[3] - prepared_bounds[1],
            },
            observation_json={"visual_evidence": dict(raw_evidence)},
        )
        encoded = io.BytesIO()
        crop.save(encoded, format="PNG")
        panel_regions.append(region)
        panel_crops[panel.panel_id] = crop
        panel_candidates[panel.panel_id] = visual_scoring.analyze_panel(
            encoded.getvalue(),
            asset_id=panel.source_asset_id,
            order_index=int(panel.source_order),
            source_family="",
        )

    try:
        candidates = reference_visual_review.build_reference_panel_fallback_candidates(
            panel_regions=panel_regions,
            panel_candidates_by_region_id=panel_candidates,
            panel_crops_by_region_id=panel_crops,
            section_evidence_panel_ids=beat_evidence,
            section_citations=dict.fromkeys(beat_evidence, ()),
            beats_by_section={beat_id: (beat_id,) for beat_id in beat_evidence},
            profile=profile,
            source_upscale_manifests_by_region_id=upscale_manifests,
            allow_missing_explicit=True,
            allow_conservative_full_panel=review_source_upscale_policy is not None,
        )
    except reference_visual_review.ReferenceReviewError as exc:
        import traceback as _tb

        _tb.print_exc()
        raise CloudStageError(exc.code, reviewable=True) from None
    return tuple(candidates), section_to_beats


def _subsample_panels(panels: Sequence[Any], limit: int) -> tuple[Any, ...]:
    """Deterministically keep `limit` panels spread across reading order.

    Long webtoon strips segment into far more panels than a single provider
    story-map/narration payload can hold in one context window. Uniform
    strided subsampling preserves chapter/reading coverage without biasing
    toward any section. Visual evidence is cached per-panel, so re-running
    with a reduced set stays cheap.
    """
    panels = tuple(panels)
    if len(panels) <= limit:
        return panels
    step = len(panels) / limit
    indices = [int(i * step) for i in range(limit)]
    return tuple(panels[idx] for idx in indices)


def _panels_for_cached_visual_stage(
    panels: Sequence[CloudPanelInput],
    cached_visual: Mapping[str, Any] | None,
) -> tuple[CloudPanelInput, ...]:
    """Align the resume input with a valid persisted visual subset.

    A review run may have durably dropped poison panels after visual
    reconciliation. Filtering before run_job lets its existing source-hash
    check compare the same ordered panel set instead of resending the dropped
    inputs. A malformed/empty cache never filters the live input.
    """

    ordered = tuple(panels)
    if not isinstance(cached_visual, Mapping):
        return ordered
    raw_rows = cached_visual.get("panels")
    if not isinstance(raw_rows, list):
        return ordered
    cached_ids = {
        str(row.get("panel_id"))
        for row in raw_rows
        if isinstance(row, Mapping) and str(row.get("panel_id", "")).strip()
    }
    if not cached_ids:
        return ordered
    filtered = tuple(panel for panel in ordered if str(panel.panel_id) in cached_ids)
    if not filtered:
        return ordered
    # A full visual result has not quarantined anything. Preserve the exact
    # prepared inputs so streaming a successful visual stage cannot mutate
    # prepared_order or cache identity before run_job sees them.
    if len(filtered) == len(ordered):
        return ordered
    # ``source_order`` remains the immutable reading-order lineage and may
    # contain gaps after panel-local quarantine.  ``prepared_order`` is the
    # execution order for this admitted subset, so rebuild it contiguously
    # without changing any source/crop/payload identity.  Freeze the visual
    # cache identity from the original prepared slot before changing that
    # derived subset order; quarantining a sibling must not invalidate an
    # already accepted provider row.
    original_indices = {panel.panel_id: index for index, panel in enumerate(ordered)}
    return tuple(
        replace(
            panel,
            prepared_order=index,
            identity_descriptor_hash=_visual_panel_identity_hash(
                panel,
                (
                    panel.prepared_order
                    if panel.prepared_order is not None
                    else original_indices[panel.panel_id]
                ),
            ),
        )
        for index, panel in enumerate(filtered)
    )


def _visual_panel_ids_requiring_materialization(
    runner: CloudStageRunner,
    panels: Sequence[CloudPanelInput],
) -> tuple[str, ...]:
    """Return only metadata-only panels absent from the exact visual cache."""

    ordered = tuple(panels)
    metadata_ids = {panel.panel_id for panel in ordered if getattr(panel, "metadata_only", False)}
    if not metadata_ids or runner.cache is None:
        return tuple(panel.panel_id for panel in ordered if panel.panel_id in metadata_ids)
    ordered = runner._ordered_panels(ordered)
    prompt = runner.prompts["visual"]
    key = _cache_key(
        "visual",
        list(_visual_panel_identities(ordered)),
        runner.model_identity,
        prompt,
    )
    cached = runner.cache.get(key)
    if not isinstance(cached, Mapping):
        return tuple(panel.panel_id for panel in ordered if panel.panel_id in metadata_ids)
    try:
        cached_result = VisualStageResult.from_dict(cached)
    except (KeyError, TypeError, ValueError):
        return tuple(panel.panel_id for panel in ordered if panel.panel_id in metadata_ids)
    panels_by_id = {panel.panel_id: panel for panel in ordered}
    reusable_ids = {
        str(row.get("panel_id", ""))
        for row in cached_result.panels
        if isinstance(row, Mapping)
        and _visual_cached_row_is_reusable(
            row,
            panels_by_id.get(str(row.get("panel_id", ""))),
        )
    }
    return tuple(
        panel.panel_id
        for panel in ordered
        if panel.panel_id in metadata_ids and panel.panel_id not in reusable_ids
    )


def _seed_visual_subset_cache(
    runner: CloudStageRunner,
    panels: Sequence[CloudPanelInput],
    visual: VisualStageResult,
) -> None:
    """Expose one restored visual result under the current input identity."""

    if runner.cache is None:
        return
    runner.cache.put(
        _cache_key(
            "visual",
            list(_visual_panel_identities(tuple(panels))),
            runner.model_identity,
            runner.prompts["visual"],
        ),
        visual.as_dict(),
    )


def _review_resume_visual_story_is_current(
    runner: Any,
    visual: VisualStageResult | None,
    story: StoryMapResult | None,
) -> bool:
    """Allow review fast-resume only on current visual/story prompt identities."""

    if visual is None or story is None:
        return False
    prompts = getattr(runner, "prompts", {})
    visual_prompt = prompts.get("visual") if isinstance(prompts, Mapping) else None
    story_prompt = prompts.get("story_map") if isinstance(prompts, Mapping) else None
    if not _stage_result_identity_is_compatible(
        visual.model_identity_hash,
        runner.model_identity,
        stage="visual",
    ):
        return False
    if visual_prompt is not None and (
        visual.prompt_version != visual_prompt[0]
        or visual.prompt_sha256 != visual_prompt[1]
    ):
        return False
    if not _stage_result_identity_is_compatible(
        story.model_identity_hash,
        runner.model_identity,
        stage="story_map",
    ):
        return False
    if story_prompt is not None and (
        story.prompt_version != story_prompt[0]
        or story.prompt_sha256 != story_prompt[1]
    ):
        return False
    return story.visual_evidence_hash == visual.visual_evidence_hash


def _visual_cache_requires_subset_restore(
    runner: CloudStageRunner,
    cached: Mapping[str, Any] | None,
    panels: Sequence[CloudPanelInput],
    *,
    allow_admitted_subset: bool = False,
) -> bool:
    """Detect a partial/stale visual stage before checkpoint restoration.

    Review-only resume may intentionally persist a strict admitted subset after
    quarantining provider-failing siblings.  Prefer that durable subset over an
    unrelated smaller cache candidate only when its exact row identities still
    reconcile against the current prepared manifest.
    """

    if not isinstance(cached, Mapping):
        return True
    try:
        visual = VisualStageResult.from_dict(cached)
        ordered = runner._ordered_panels(tuple(panels))
    except (CloudStageError, KeyError, TypeError, ValueError):
        return True
    prompt = runner.prompts["visual"]
    if (
        not _stage_result_identity_is_compatible(
            visual.model_identity_hash,
            runner.model_identity,
            stage="visual",
        )
        or visual.prompt_version != prompt[0]
        or visual.prompt_sha256 != prompt[1]
    ):
        return True
    ordered_ids = tuple(panel.panel_id for panel in ordered)
    if visual.panel_ids != ordered_ids:
        if not allow_admitted_subset:
            return True
        subset = _panels_for_cached_visual_stage(ordered, cached)
        if (
            len(subset) != len(visual.panel_ids)
            or tuple(panel.panel_id for panel in subset) != visual.panel_ids
        ):
            return True
        migrated = _migrate_visual_cache_identity(
            cached,
            runner._ordered_panels(subset),
            model_identity=runner.model_identity,
            prompt=runner.prompts["visual"],
        )
        return migrated is None
    if visual.source_hash != _visual_source_hash(ordered):
        return True
    if tuple(visual.panel_identity_hashes) != _visual_panel_identity_hashes(ordered):
        return True
    for row, panel in zip(visual.panels, ordered, strict=True):
        try:
            if not _visual_cached_row_is_reusable(row, panel):
                return True
        except (TypeError, ValueError):
            return True
    return False


def _find_cached_visual_subset(
    runner: CloudStageRunner,
    panels: Sequence[CloudPanelInput],
    *,
    expected_source_hash: str,
) -> VisualStageResult | None:
    """Restore the largest exact visual cache subset after a resume gap."""

    ordered = tuple(panels)
    prompt = runner.prompts["visual"]
    checkpoint_scope = runner._checkpoint_scope(_visual_panel_identities(ordered), prompt)
    checkpoint_rows = runner._checkpoint_load(checkpoint_scope)
    if checkpoint_rows:
        checkpoint_panels: list[dict[str, Any]] = []
        panel_identity_hashes: list[str] = []
        for panel in ordered:
            row = checkpoint_rows.get(panel.panel_id)
            if not isinstance(row, Mapping):
                continue
            try:
                lineage_valid = (
                    str(row.get("panel_id", "")) == panel.panel_id
                    and str(row.get("source_asset_id", "")) == panel.source_asset_id
                    and int(row.get("source_order", -1)) == int(panel.source_order)
                    and str(row.get("source_checksum", "")) == panel.source_checksum
                )
            except (TypeError, ValueError):
                lineage_valid = False
            if not lineage_valid:
                continue
            row_identity_hash = str(row.get("cache_identity_hash", ""))
            if (
                panel.identity_descriptor_hash
                and row_identity_hash
                and row_identity_hash != panel.identity_descriptor_hash
            ):
                continue
            checkpoint_panels.append(dict(row))
            panel_identity_hashes.append(row_identity_hash)
        if checkpoint_panels:
            return VisualStageResult(
                panels=tuple(checkpoint_panels),
                source_hash=expected_source_hash,
                model_identity_hash=runner.model_identity.identity_hash,
                prompt_version=prompt[0],
                prompt_sha256=prompt[1],
                reconciled=True,
                cache_identity_version=VISUAL_CACHE_IDENTITY_VERSION,
                panel_identity_hashes=tuple(panel_identity_hashes),
            )

    iter_records = getattr(runner.cache, "iter_records", None)
    if not callable(iter_records):
        return None
    panel_by_id = {panel.panel_id: panel for panel in ordered}
    ordered_index = {panel.panel_id: index for index, panel in enumerate(ordered)}
    candidates: list[VisualStageResult] = []
    try:
        records = iter_records()
        for raw in records:
            if not isinstance(raw, Mapping):
                continue
            try:
                result = VisualStageResult.from_dict(raw)
            except (KeyError, TypeError, ValueError):
                continue
            if (
                result.model_identity_hash != runner.model_identity.identity_hash
                or result.prompt_version != prompt[0]
                or result.prompt_sha256 != prompt[1]
                or result.source_hash != expected_source_hash
                or not result.panels
            ):
                continue
            row_ids: list[str] = []
            row_indexes: list[int] = []
            valid = True
            for row in result.panels:
                if not isinstance(row, Mapping):
                    valid = False
                    break
                panel_id = str(row.get("panel_id", ""))
                panel = panel_by_id.get(panel_id)
                if panel is None or panel_id in row_ids:
                    valid = False
                    break
                try:
                    if (
                        str(row.get("source_asset_id", "")) != panel.source_asset_id
                        or int(row.get("source_order", -1)) != panel.source_order
                        or str(row.get("source_checksum", "")) != panel.source_checksum
                    ):
                        valid = False
                        break
                except (TypeError, ValueError):
                    valid = False
                    break
                row_ids.append(panel_id)
                row_indexes.append(ordered_index[panel_id])
            if valid and row_indexes == sorted(row_indexes):
                candidates.append(result)
    except (OSError, TypeError, ValueError):
        return None
    if not candidates:
        return None
    candidates.sort(
        key=lambda result: (len(result.panels), result.visual_evidence_hash), reverse=True
    )
    largest = candidates[0]
    if (
        len(candidates) > 1
        and len(candidates[1].panels) == len(largest.panels)
        and candidates[1].panel_ids != largest.panel_ids
    ):
        return None
    return largest


def _materialize_metadata_only_panels(
    db: Any,
    project_id: str,
    panels: Sequence[CloudPanelInput],
    *,
    required_panel_ids: Sequence[str],
) -> tuple[CloudPanelInput, ...]:
    """Decode exact source crops only for cache rows that need provider input."""

    required = {str(panel_id) for panel_id in required_panel_ids if str(panel_id).strip()}
    ordered = tuple(panels)
    if not required:
        return ordered
    from app.models import PanelRegion
    from app.services import pipeline

    assets = tuple(pipeline.image_assets(pipeline.project_assets(db, project_id)))
    assets_by_id = {str(asset.id): asset for asset in assets}
    target_panels = tuple(
        panel
        for panel in ordered
        if panel.panel_id in required and getattr(panel, "metadata_only", False)
    )
    if len(target_panels) != len(required):
        raise CloudStageError("cloud.prepared_manifest_invalid")
    target_asset_ids = {panel.source_asset_id for panel in target_panels}
    selected_assets = tuple(
        assets_by_id[asset_id] for asset_id in sorted(target_asset_ids) if asset_id in assets_by_id
    )
    if len(selected_assets) != len(target_asset_ids):
        raise CloudStageError("cloud.panel_lineage_invalid")
    try:
        source_inputs, _ = pipeline._build_source_inputs(selected_assets)
    except Exception:
        raise CloudStageError("cloud.panel_payload_invalid") from None
    source_by_id = {str(item.source_asset_id): item for item in source_inputs}
    materialized: list[CloudPanelInput] = []
    for panel in ordered:
        if panel.panel_id not in required or not getattr(panel, "metadata_only", False):
            materialized.append(panel)
            continue
        source_input = source_by_id.get(panel.source_asset_id)
        bounds = panel.panel_bounds
        dimensions = panel.source_dimensions
        if source_input is None or bounds is None or dimensions is None:
            raise CloudStageError("cloud.panel_lineage_invalid")
        if (
            source_input.original_checksum != panel.source_checksum
            or int(source_input.original_width) != int(dimensions[0])
            or int(source_input.original_height) != int(dimensions[1])
        ):
            raise CloudStageError("cloud.panel_lineage_invalid")
        transient = PanelRegion(
            id=panel.panel_id,
            story_analysis_id="cloud-preview",
            source_asset_id=panel.source_asset_id,
            source_asset_checksum=panel.source_checksum,
            original_width=dimensions[0],
            original_height=dimensions[1],
            strip_region_id=panel.strip_region_id or panel.panel_id,
            panel_id=panel.panel_id,
            source_order=panel.source_order,
            bounds_json={
                "x": bounds[0],
                "y": bounds[1],
                "width": bounds[2] - bounds[0],
                "height": bounds[3] - bounds[1],
            },
        )
        try:
            payload = pipeline._encode_panel_payload(transient, source_input)
        except Exception:
            raise CloudStageError("cloud.panel_payload_invalid") from None
        materialized.append(
            replace(
                panel,
                mime_type="image/png",
                payload=payload,
                payload_checksum="",
                metadata_only=False,
            )
        )
    return tuple(materialized)


def _durable_visual_repair_covers_missing_sections(
    narration: NarrationResult,
    *,
    ledger: visual_narrative_repair.FeasibleVisualLedger,
    section_to_beats: Mapping[str, Sequence[str]],
    missing_sections: Sequence[str],
    capacity_safe_claim_plan: Mapping[str, Any] | None = None,
    expected_prompt_version: str | None = None,
    expected_prompt_sha256: str | None = None,
) -> bool:
    """Return whether a persisted narration already satisfies the current repair contract."""

    if expected_prompt_version is not None and str(getattr(narration, "prompt_version", "")) != expected_prompt_version:
        return False
    if expected_prompt_sha256 is not None and str(getattr(narration, "prompt_sha256", "")) != expected_prompt_sha256:
        return False
    try:
        if visual_narrative_repair.narration_sections_with_subtitle_overflow(
            narration, section_to_beats
        ):
            return False
        claims = narration.evidence_graph.get("claims", ())
        passages = tuple(dict(item) for item in narration.passages)
        if not isinstance(claims, (list, tuple)):
            return False
        allowed_claim_ids = {
            str(claim.get("claim_id", ""))
            for claim in claims
            if isinstance(claim, Mapping) and str(claim.get("claim_id", "")).strip()
        }
        visual_narrative_repair.validate_repaired_panel_references(
            {"claims": list(claims), "passages": list(passages)},
            ledger=ledger,
            allowed_claim_ids=allowed_claim_ids,
        )
        visual_narrative_repair.validate_repaired_section_visual_coverage(
            passages,
            ledger=ledger,
            section_to_beats=section_to_beats,
            missing_sections=missing_sections,
        )
        visual_narrative_repair.validate_repaired_visual_capacity(
            passages,
            ledger,
            total_duration_s=getattr(narration, "estimated_duration_s", None),
        )
        if capacity_safe_claim_plan is not None:
            visual_narrative_repair.validate_repaired_capacity_safe_claim_plan(
                passages,
                capacity_safe_claim_plan,
            )
            visual_narrative_repair.validate_repaired_hook_quality(
                passages,
                tuple(item for item in claims if isinstance(item, Mapping)),
                capacity_safe_claim_plan,
            )
    except (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        visual_narrative_repair.VisualNarrativeRepairError,
    ):
        return False
    return True


class CloudBatchService(cloud_runner_parts.batch.CloudBatchMixin):
    def __init__(
        self,
        *,
        runner: CloudStageRunner,
        store: JsonJobStore,
        max_concurrent: int = 1,
        review_root: Path | None = Path("data/segmentation-review"),
    ) -> None:
        self.runner = runner
        self.store = store
        self.max_concurrent = max(1, int(max_concurrent))
        self.review_root = Path(review_root) if review_root is not None else None









def _review_section_panel_ids(
    script_row: Any,
    ledger: visual_narrative_repair.FeasibleVisualLedger,
    section_to_beats: Mapping[str, Sequence[str]],
) -> dict[str, tuple[str, ...]]:
    """Map review sections to feasible cited panels without losing remaps.

    A visual repair may move a section's evidence to a later causal beat when
    its original panels have no safe ROI.  The persisted script is the trusted
    source for that explicit remap; beat eligibility remains the deterministic
    fallback for sections that did not need one.
    """

    entries = tuple(getattr(ledger, "entries", ()) or ())
    feasible_ids = {
        str(entry.get("panel_id", ""))
        if isinstance(entry, Mapping)
        else str(getattr(entry, "panel_id", ""))
        for entry in entries
    }
    by_section: dict[str, tuple[str, ...]] = {}
    for raw_section in tuple(getattr(script_row, "sections", ()) or ()):
        if not isinstance(raw_section, Mapping):
            continue
        section = str(raw_section.get("section", "")).strip()
        if not section:
            continue
        explicit = raw_section.get("evidence_panel_ids") or ()
        explicit_ids = (
            tuple(dict.fromkeys(str(value) for value in explicit if str(value).strip()))
            if isinstance(explicit, Sequence) and not isinstance(explicit, (str, bytes))
            else ()
        )
        selected = tuple(panel_id for panel_id in explicit_ids if panel_id in feasible_ids)
        if not selected:
            allowed_beats = {str(value) for value in section_to_beats.get(section, ())}
            fallback: list[str] = []
            for entry in entries:
                panel_id = (
                    str(entry.get("panel_id", ""))
                    if isinstance(entry, Mapping)
                    else str(getattr(entry, "panel_id", ""))
                )
                eligible_beats = (
                    entry.get("eligible_beats", ())
                    if isinstance(entry, Mapping)
                    else getattr(entry, "eligible_beats", ())
                )
                if (
                    panel_id
                    and allowed_beats.intersection(str(value) for value in eligible_beats)
                    and panel_id not in fallback
                ):
                    fallback.append(panel_id)
            selected = tuple(fallback)
        if selected:
            by_section[section] = selected
    return by_section


def review_only_render_gate(result: ChapterResult | ChapterJobRecord) -> ReviewOnlyRenderGate:
    state = result.state
    if state != ChapterState.READY_TO_RENDER:
        raise CloudStageError("cloud.stage_not_ready")
    return ReviewOnlyRenderGate(allowed=True)


def require_final_render_ready(result: ChapterResult | ChapterJobRecord) -> None:
    if result.state != ChapterState.READY_TO_RENDER:
        raise CloudStageError("cloud.stage_not_ready")
    raise CloudStageError("cloud.voice_timing_required")


def regular_render_allowed(result: ChapterResult | ChapterJobRecord) -> bool:
    return result.state == ChapterState.READY_TO_RENDER and False


def _default_stage_cache_root() -> Path:
    configured = os.environ.get("MS_DATA_DIR", "").strip()
    base = Path(configured) if configured else Path(__file__).resolve().parents[2] / "data"
    return base / "cloud-stage-cache"


def resolve_cloud_runner(
    db,
    workspace_id: str,
    *,
    model: str | None = None,
    cache: StageCache | None = None,
    max_attempts: int = 2,
    max_requests: int | None = None,
    max_narration_requests: int | None = None,
    max_repair_requests: int | None = None,
    min_request_interval_s: float = 0.0,
    estimated_cost_per_request: float = 0.0,
    allow_balloon_unknown: bool = False,
    visual_parallel_workers: int | None = None,
) -> CloudStageRunner:
    """Resolve only a verified BYOK multimodal credential; never local fallback."""

    from app.services import resolver

    if cache is None:
        cache = FileStageCache(_default_stage_cache_root())
    try:
        provider, report = resolver.resolve_vision(db, workspace_id)
    except Exception:
        raise CloudStageError("cloud.credential_missing") from None
    if provider is None or report is None or not report.available:
        raise CloudStageError(
            str(getattr(report, "blocking_reason", None) or "cloud.credential_missing")
        )
    selected_model = str(getattr(report, "model", "") or "")
    if model is not None and model != selected_model:
        raise CloudStageError("cloud.model_identity_mismatch")
    endpoint = str(getattr(provider, "endpoint", "configured_by_byok"))
    identity = CloudModelIdentity(
        provider=str(getattr(report, "provider_name", "openai_compatible")),
        model=selected_model,
        model_version="verified_byok",
        endpoint=endpoint,
        prompt_versions={stage: prompt[0] for stage, prompt in _prompt_specs().items()},
    )
    return CloudStageRunner(
        provider=provider,
        model_identity=identity,
        cache=cache,
        max_attempts=max_attempts,
        max_requests=max_requests,
        max_narration_requests=max_narration_requests,
        max_repair_requests=max_repair_requests,
        min_request_interval_s=min_request_interval_s,
        estimated_cost_per_request=estimated_cost_per_request,
        allow_balloon_unknown=allow_balloon_unknown,
        visual_parallel_workers=_configured_visual_parallel_workers(visual_parallel_workers),
    )


def derive_display_words(spoken_text: str) -> tuple[str, ...]:
    words = tuple(re.findall(r"[A-Z0-9]+", spoken_text.upper()))
    if not words or any(not word.isalnum() for word in words):
        raise CloudStageError("cloud.display_derivation_invalid")
    return words


__all__ = [
    "ChapterJobRecord",
    "ChapterResult",
    "ChapterState",
    "CloudBatchService",
    "CloudModelIdentity",
    "CloudPanelInput",
    "CloudStageError",
    "CloudStageRunner",
    "JsonJobStore",
    "MemoryStageCache",
    "FileStageCache",
    "NarrationResult",
    "persist_cloud_chapter",
    "panel_admission_failure_ledger",
    "prepare_project_panels",
    "ReviewOnlyRenderGate",
    "StoryMapResult",
    "VisualStageResult",
    "derive_display_words",
    "regular_render_allowed",
    "require_final_render_ready",
    "resolve_cloud_runner",
    "review_only_render_gate",
    "NARRATION_REPAIR_IDENTITY_MIGRATION_VERSION",
    "NARRATION_REPAIR_IDENTITY_VERSION",
    "persist_narration_repair_identity_migration",
    "reconcile_narration_repair_identity",
]

VISUAL_CACHE_IDENTITY_VERSION = "visual-cache-identity-v2"
LEGACY_VISUAL_CACHE_IDENTITY_VERSION = "legacy-descriptor-v1"
# The visual payload/cache contract is independent of the targeted narrative
# repair prompt.  This explicit migration pair preserves a valid visual cache
# when that downstream prompt version changes.
LEGACY_VISUAL_REPAIR_CONTRACT_VERSION = "visual_narrative_repair_v12"
LEGACY_VISUAL_REPAIR_PROMPT_VERSION = "visual-narrative-repair-v12"
EARLIER_UPSTREAM_VISUAL_REPAIR_PROMPT_VERSION = "visual-narrative-repair-v11"
OLDER_UPSTREAM_VISUAL_REPAIR_PROMPT_VERSION = "visual-narrative-repair-v10"
OLDEST_UPSTREAM_VISUAL_REPAIR_PROMPT_VERSION = "visual-narrative-repair-v9"
CURRENT_VISUAL_REPAIR_PROMPT_VERSION = "visual-narrative-repair-v13"
VISUAL_RENDER_PAYLOAD_VERSION = (
    "visual-provider-payload-v1:max-bytes=180000:max-size=384x576:"
    "jpeg-quality=68:subsampling=2:lanczos"
)
VISUAL_CANONICAL_JPEG_VERSION = "canonical-jpeg-v1"
VISUAL_CHECKPOINT_VERSION = "visual-checkpoint-v2"


def _visual_panel_identity(panel: CloudPanelInput, ordered_index: int) -> dict[str, Any]:
    """Return only stable values that change the visual model input."""

    bounds = panel.panel_bounds
    dimensions = panel.source_dimensions
    if bounds is not None and dimensions is not None:
        source_width, source_height = dimensions
        normalized_crop = [
            f"{coordinate}/{denominator}"
            for coordinate, denominator in (
                (bounds[0], source_width),
                (bounds[1], source_height),
                (bounds[2], source_width),
                (bounds[3], source_height),
            )
        ]
        crop_transform: dict[str, Any] = {
            "source_dimensions": [source_width, source_height],
            "normalized_crop_box": normalized_crop,
            "crop_size": [bounds[2] - bounds[0], bounds[3] - bounds[1]],
        }
    else:
        crop_transform = {
            "source_dimensions": list(dimensions) if dimensions is not None else None,
            "normalized_crop_box": None,
            "crop_size": None,
        }
    if panel.identity_payload_checksum:
        rendered_payload_hash = panel.identity_payload_checksum
        rendered_mime = panel.mime_type
    else:
        rendered_payload, rendered_mime = _visual_provider_payload(panel)
        rendered_payload_hash = hashlib.sha256(rendered_payload).hexdigest()
    return {
        "ordered_panel_index": int(
            panel.prepared_order if panel.prepared_order is not None else ordered_index
        ),
        "panel_id": panel.panel_id,
        "source_asset_checksum": panel.source_checksum,
        "crop_transform": crop_transform,
        "rendered_payload": {
            "policy_version": _visual_render_policy_version(panel),
            "mime_type": rendered_mime,
            "sha256": rendered_payload_hash,
        },
    }


def _visual_panel_identities(
    panels: Sequence[CloudPanelInput],
) -> tuple[dict[str, Any], ...]:
    return tuple(
        _visual_panel_identity(panel, ordered_index) for ordered_index, panel in enumerate(panels)
    )


def _visual_panel_identity_hash(
    panel: CloudPanelInput,
    ordered_index: int,
) -> str:
    if panel.identity_descriptor_hash:
        return panel.identity_descriptor_hash
    return _hash(_visual_panel_identity(panel, ordered_index))


def _visual_panel_identity_hashes(
    panels: Sequence[CloudPanelInput],
) -> tuple[str, ...]:
    return tuple(
        _visual_panel_identity_hash(panel, ordered_index)
        for ordered_index, panel in enumerate(tuple(panels))
    )


def _visual_source_hash(panels: Sequence[CloudPanelInput]) -> str:
    prepared_hashes = {panel.source_identity_hash for panel in panels if panel.source_identity_hash}
    if len(prepared_hashes) == 1 and all(panel.identity_descriptor_hash for panel in panels):
        return next(iter(prepared_hashes))
    return _hash(list(_visual_panel_identities(tuple(panels))))


def _legacy_visual_descriptor(
    panel: CloudPanelInput,
    *,
    source_order: int | None = None,
) -> dict[str, Any]:
    """Recreate the descriptor used before metadata-only identity fields.

    ``CloudPanelInput.descriptor`` now includes prepared-manifest metadata.
    Those fields are intentionally excluded here because they did not affect
    the legacy provider payload or its cache identity.
    """

    descriptor: dict[str, Any] = {
        "panel_id": panel.panel_id,
        "source_asset_id": panel.source_asset_id,
        "source_order": panel.source_order if source_order is None else int(source_order),
        "mime_type": panel.mime_type,
        "source_checksum": panel.source_checksum,
        "payload_checksum": panel.payload_checksum,
    }
    if panel.panel_bounds is not None:
        descriptor["panel_bounds"] = list(panel.panel_bounds)
    if panel.source_dimensions is not None:
        descriptor["source_dimensions"] = list(panel.source_dimensions)
    if panel.strip_region_id:
        descriptor["strip_region_id"] = panel.strip_region_id
    if panel.coverage_map_version:
        descriptor["coverage_map_version"] = panel.coverage_map_version
    if panel.coverage_map_hash:
        descriptor["coverage_map_hash"] = panel.coverage_map_hash
    if panel.segmentation_version:
        descriptor["segmentation_version"] = panel.segmentation_version
    return descriptor


def _legacy_visual_descriptor_from_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Build the old descriptor from a persisted visual row, fail-closed."""

    required = (
        "panel_id",
        "source_asset_id",
        "source_order",
        "mime_type",
        "source_checksum",
        "payload_checksum",
    )
    if any(key not in row for key in required):
        return None
    descriptor = {key: row[key] for key in required}
    for key in (
        "panel_bounds",
        "source_dimensions",
        "strip_region_id",
        "coverage_map_version",
        "coverage_map_hash",
        "segmentation_version",
    ):
        value = row.get(key)
        if value is not None and value != "":
            descriptor[key] = value
    return descriptor


def _legacy_visual_model_identity(
    identity: CloudModelIdentity,
) -> CloudModelIdentity | None:
    """Return the one audited pre-repair identity eligible for visual reuse."""

    prompt_versions = dict(identity.prompt_versions)
    if prompt_versions.get("visual_narrative_repair") != CURRENT_VISUAL_REPAIR_PROMPT_VERSION:
        return None
    prompt_versions["visual_narrative_repair"] = LEGACY_VISUAL_REPAIR_PROMPT_VERSION
    return replace(identity, prompt_versions=prompt_versions)


def _visual_chunk_cache_key(
    chunk: Sequence[CloudPanelInput],
    *,
    chunk_index: int,
    batch_count: int,
    model_identity: CloudModelIdentity,
    prompt: tuple[str, str, str],
) -> str:
    source = {
        "identity_version": VISUAL_CACHE_IDENTITY_VERSION,
        "chunk_index": int(chunk_index),
        "batch_count": int(batch_count),
        "panels": list(_visual_panel_identities(tuple(chunk))),
    }
    return _cache_key("visual_chunk", source, model_identity, prompt)


def _persisted_visual_lineage_matches(
    lineage: Mapping[str, Any] | None,
    ordered: Sequence[CloudPanelInput],
) -> bool:
    if not isinstance(lineage, Mapping):
        return False
    raw_observations = lineage.get("observations")
    if not isinstance(raw_observations, list) or len(raw_observations) != len(ordered):
        return False
    for index, (panel, observation) in enumerate(zip(ordered, raw_observations, strict=True)):
        if not isinstance(observation, Mapping):
            return False
        if (
            str(observation.get("panel_id", "")) != panel.panel_id
            or str(observation.get("source_asset_id", "")) != panel.source_asset_id
            or observation.get("source_index") != index
        ):
            return False
        bounds = observation.get("region_bounds")
        if not isinstance(bounds, Mapping) or panel.panel_bounds is None:
            return False
        try:
            persisted_bounds = (
                int(bounds["x"]),
                int(bounds["y"]),
                int(bounds["x"]) + int(bounds["width"]),
                int(bounds["y"]) + int(bounds["height"]),
            )
        except (KeyError, TypeError, ValueError):
            return False
        if persisted_bounds != panel.panel_bounds:
            return False
    return True


def _migrate_visual_cache_identity(
    cached: Mapping[str, Any] | None,
    panels: Sequence[CloudPanelInput],
    *,
    model_identity: CloudModelIdentity,
    prompt: tuple[str, str, str],
    persisted_lineage: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Validate or migrate a legacy visual cache without provider calls."""

    if not isinstance(cached, Mapping):
        return None
    if (
        not _stage_result_identity_is_compatible(
            str(cached.get("model_identity_hash", "")),
            model_identity,
            stage="visual",
        )
        or str(cached.get("prompt_version", "")) != prompt[0]
        or str(cached.get("prompt_sha256", "")) != prompt[1]
        or not bool(cached.get("reconciled", False))
    ):
        return None
    raw_rows = cached.get("panels")
    if not isinstance(raw_rows, list):
        return None
    try:
        ordered = CloudStageRunner._ordered_panels(tuple(panels))
    except (CloudStageError, TypeError, ValueError):
        return None
    if len(raw_rows) != len(ordered):
        return None
    expected_ids = tuple(panel.panel_id for panel in ordered)
    cached_ids = tuple(str(row.get("panel_id", "")) for row in raw_rows if isinstance(row, Mapping))
    if cached_ids != expected_ids or len(set(cached_ids)) != len(cached_ids):
        return None
    cached_orders: list[int] = []
    for panel, row in zip(ordered, raw_rows, strict=True):
        if not isinstance(row, Mapping):
            return None
        row_order = row.get("source_order")
        if (
            isinstance(row_order, bool)
            or not isinstance(row_order, int)
            or row_order < 0
            or str(row.get("source_asset_id", "")) != panel.source_asset_id
            or str(row.get("source_checksum", "")) != panel.source_checksum
        ):
            return None
        cached_orders.append(row_order)
    if cached_orders != sorted(set(cached_orders)):
        return None

    identity_hashes = _visual_panel_identity_hashes(ordered)
    expected_source_hash = _visual_source_hash(ordered)
    identity_version = str(cached.get("cache_identity_version", ""))
    if identity_version == VISUAL_CACHE_IDENTITY_VERSION:
        persisted_hashes = tuple(str(item) for item in cached.get("panel_identity_hashes", ()))
        source_matches = str(cached.get("source_hash", "")) == expected_source_hash
        if source_matches and persisted_hashes == identity_hashes:
            return dict(cached)

        # Review-only resume can durably keep an admitted visual subset while
        # retaining aggregate identity from the larger prepared manifest. The
        # aggregate source/hash metadata may therefore be stale after subset
        # restoration. Reuse is still safe only when every admitted row carries
        # the exact current content-addressed panel identity and the persisted
        # aggregate vector proves this was a strict larger-manifest subset.
        row_identity_hashes = tuple(
            str(row.get("cache_identity_hash", ""))
            for row in raw_rows
            if isinstance(row, Mapping)
        )
        strict_manifest_subset = len(persisted_hashes) > len(identity_hashes)
        if (
            not strict_manifest_subset
            or len(row_identity_hashes) != len(identity_hashes)
            or row_identity_hashes != identity_hashes
            or any(not value for value in row_identity_hashes)
        ):
            return None
        # Preserve the original aggregate source hash so a StoryMap generated
        # from this exact accepted visual subset keeps the same evidence hash.
        # No omitted sibling is admitted downstream; run_project filters inputs
        # to these row IDs before run_job reaches this boundary.
        return dict(cached)

    if identity_version not in {"", LEGACY_VISUAL_CACHE_IDENTITY_VERSION}:
        return None
    legacy_descriptors = [
        _legacy_visual_descriptor(panel, source_order=int(row["source_order"]))
        for panel, row in zip(ordered, raw_rows, strict=True)
    ]
    legacy_source_hash = _hash(legacy_descriptors)
    migration_proof = "legacy_descriptor_hash"
    if str(cached.get("source_hash", "")) != legacy_source_hash:
        if not _persisted_visual_lineage_matches(persisted_lineage, ordered):
            return None
        migration_proof = "persisted_lineage_and_payload_derivation"

    migrated = dict(cached)
    migrated["source_hash"] = expected_source_hash
    migrated["cache_identity_version"] = VISUAL_CACHE_IDENTITY_VERSION
    migrated["panel_identity_hashes"] = list(identity_hashes)
    migrated["legacy_source_hash"] = legacy_source_hash
    migrated["cache_identity_migration_proof"] = migration_proof
    return migrated
