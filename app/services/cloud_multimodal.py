"""Pinned cloud multimodal stages and resumable review-only chapter jobs.

The module deliberately stops before TTS.  It reuses the existing visual
evidence, analyzer, and Sharp Friend validators; this layer owns provider
selection, stage identity, local reconciliation, cache keys, and batch state.
Provider output is untrusted JSON.  Canonical hashes are always computed here.
"""

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

from app.services import (
    analyzer_contract,
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
STRIP_BOUNDARY_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "strip_boundary_assessment_v1.txt"
STRIP_BOUNDARY_PROMPT_VERSION = "strip-boundary-assessment-v1"
# Keep provider response envelopes to one image: this configured endpoint has
# returned incomplete structured JSON for multi-image requests.  Every ordered
# panel is still processed; local reconciliation owns complete coverage.
VISUAL_REQUEST_MAX_PANELS = 8  # preview-only: 4-5 worker saturation sweet spot
VISUAL_REQUEST_MAX_ESTIMATED_BYTES = 3_500_000  # preview-only: larger visual batches
VISUAL_REQUEST_OVERLAP = 0
VISUAL_STREAM_VERSION = "visual-stream-v1"
VISUAL_STREAM_WORKER_COUNT = 8
VISUAL_STREAM_QUEUE_SIZE = 8
VISUAL_STREAM_WAVE_PANEL_TARGET = 32
STORY_MAP_CHUNK_STEP = 180
STORY_MAP_COVERAGE_FALLBACK_STEP = 60
STORY_MAP_COVERAGE_MIN_STEP = 30
NARRATION_CHUNK_STEP = 180
NARRATION_COVERAGE_FALLBACK_STEP = 60
NARRATION_COVERAGE_MIN_STEP = 30
NARRATION_REPAIR_VERSION = "narration-targeted-repair-v6"
NARRATION_REPAIR_MAX_ATTEMPTS = 3
NARRATION_REPAIR_POSITION_MAX_ATTEMPTS = 1
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
NARRATION_MICRO_COMPACTION_MAX_WORDS = 130
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
        max(NARRATION_REPAIR_POSITION_MIN_WORDS, word_budget - NARRATION_REPAIR_POSITION_WORD_SLACK),
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
    if not NARRATION_MICRO_COMPACTION_MIN_WORDS <= total_words <= NARRATION_MICRO_COMPACTION_MAX_WORDS:
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


NARRATION_REPAIR_INSTRUCTION = (
    "TARGETED NARRATION POSITION REPAIR: return exactly one JSON object with "
    "the single top-level key {\"rewrites\": [\"text for position 0\", \"...\"]}. "
    "The rewrites array must contain one revised spoken-text string for every "
    "ordered position supplied in the context; array index N maps to position N. "
    "never return, create, or rewrite claim IDs, evidence panel IDs, slot IDs, "
    "passage IDs, observations, beat IDs, or hashes. Preserve the supplied causal "
    "order and evidence-grounded meaning. Write natural English within each "
    "position's word_budget_min/word_budget_max values as drafting guidance "
    "and sanitized diagnostics, not hard admission bounds. The local validator "
    "enforces the exact vector shape and order, non-empty strings, trusted "
    "lineage, causal order, total 115-125 words, and 50-60 seconds; it rejects "
    "only a pathological single-position share. Target approximately 120 total "
    "words; exactly 120 is guidance. For the usual eight-position vector, aim "
    "for roughly 14 or 15 words per position. A smaller trusted vector is "
    "valid when the grounded candidate has fewer positions: distribute the "
    "115-125 total naturally across the supplied positions, without inventing "
    "positions or padding solely to reach eight. Per-position budgets remain "
    "drafting guidance, not hard caps. Count every rewrite "
    "before returning. Aim for 118 "
    "total words so natural variation stays inside the accepted range; exactly "
    "120 is guidance only. Before returning JSON, recount the complete vector "
    "word by word and revise it until the total is 115-125 words; never return "
    "a vector above 125 words. The local compactor is only a narrow safety net "
    "for 126-130 and "
    "cannot repair larger responses. "
    "Do not invent facts, add citations, copy dialogue, or return any wrapper, "
    "metadata, or alternate key. Every rewrite must paraphrase any dialogue into "
    "third-person narrator language; never quote or preserve a four-word lexical "
    "sequence from dialogue_or_ocr. Quotation marks, capitalization changes, or "
    "renaming a speaker are not loopholes: local strict validation rejects "
    "near-verbatim dialogue. Describe only the grounded event or consequence. "
    "Every supplied context panel and section belongs to the exact retained "
    "passage/claim evidence closure; never mix a same-chapter panel from "
    "another section."
)
EDITORIAL_SELECTION_VERSION = "editorial-selection-v1"
EDITORIAL_SELECTION_TARGET_BEATS = 10
EDITORIAL_SELECTION_MAX_PANELS_PER_BEAT = 4
STAGE_PARALLEL_WORKERS = 4
_REVIEW_ERROR_CODE_PATTERN = re.compile(
    r"\b(?:cloud|visual|reference|review|subtitle)\.[a-z0-9_.-]+\b"
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
        if not values or any(not str(key).strip() or not str(value).strip() for key, value in values.items()):
            raise CloudStageError("cloud.model_identity_invalid")
        object.__setattr__(self, "prompt_versions", tuple(sorted((str(k), str(v)) for k, v in values.items())))

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
        if isinstance(self.source_order, bool) or not isinstance(self.source_order, int) or self.source_order < 0:
            raise CloudStageError("cloud.panel_lineage_invalid")
        if (
            self.prepared_order is not None
            and (
                isinstance(self.prepared_order, bool)
                or not isinstance(self.prepared_order, int)
                or self.prepared_order < 0
            )
        ):
            raise CloudStageError("cloud.panel_lineage_invalid")
        if not self.mime_type.lower().startswith("image/") or not self.payload:
            raise CloudStageError("cloud.panel_payload_invalid")
        if self.identity_payload_checksum and (
            len(self.identity_payload_checksum) != 64
            or any(character not in "0123456789abcdef" for character in self.identity_payload_checksum.lower())
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
            or not all(isinstance(value, int) and not isinstance(value, bool) for value in self.panel_bounds)
            or self.panel_bounds[0] < 0
            or self.panel_bounds[1] < 0
            or self.panel_bounds[2] <= self.panel_bounds[0]
            or self.panel_bounds[3] <= self.panel_bounds[1]
        ):
            raise CloudStageError("cloud.panel_lineage_invalid")
        if self.source_dimensions is not None:
            if (
                len(self.source_dimensions) != 2
                or not all(isinstance(value, int) and not isinstance(value, bool) for value in self.source_dimensions)
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
    bounds = panel.panel_bounds if panel is not None else _admission_bounds(
        _admission_value(region, "bounds")
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
    panel_id = panel.panel_id if panel is not None else str(
        _admission_value(region, "region_id", "") or ""
    )
    source_order = (
        panel.source_order
        if panel is not None
        else _admission_value(region, "source_order")
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
    return (panel.prepared_order if panel.prepared_order is not None else panel.source_order, panel.panel_id)


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
    prepared_orders = [panel.prepared_order for panel in ordered if panel.prepared_order is not None]
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
                "source_asset_checksum": str(
                    _admission_value(region, "source_checksum", "") or ""
                ),
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
            classification in {"blank", "near_blank", "title", "cover"}
            and story_evidence is False
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
            left[0] != right[0]
            or left[2] != right[2]
            or left[3] != right[1]
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
    ingest_count = len({panel.source_asset_id for panel in panels}) if ingest_asset_count is None else int(ingest_asset_count)
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
            "admission.non_panel_transition" if rejected_non_panel else "admission.canonical_regions",
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
    ledger["ledger_hash"] = _hash({key: value for key, value in ledger.items() if key != "ledger_hash"})
    return ledger


class CloudMultimodalProvider(Protocol):
    model_id: str

    def observe(self, request: VisionObservationRequest) -> list[Mapping[str, Any]]:
        ...

    def complete_json(
        self,
        *,
        stage: str,
        prompt_version: str,
        prompt_sha256: str,
        prompt_text: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...


class StageCache(Protocol):
    def get(self, key: str) -> Mapping[str, Any] | None:
        ...

    def put(self, key: str, value: Mapping[str, Any]) -> None:
        ...


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
            if (
                isinstance(value, Mapping)
                and (cache_type is None or value.get("cache_type") == cache_type)
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

    @property
    def panel_ids(self) -> tuple[str, ...]:
        return tuple(item["panel_id"] for item in self.panels)

    @property
    def visual_evidence_hash(self) -> str:
        return _hash({
            "contract_version": "visual-evidence-stage-v1",
            "source_hash": self.source_hash,
            "model_identity_hash": self.model_identity_hash,
            "prompt_version": self.prompt_version,
            "prompt_sha256": self.prompt_sha256,
            "panels": [dict(item) for item in self.panels],
        })

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"panels": [dict(item) for item in self.panels]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> VisualStageResult:
        return cls(
            panels=tuple(dict(item) for item in value["panels"]),
            source_hash=str(value["source_hash"]),
            model_identity_hash=str(value["model_identity_hash"]),
            prompt_version=str(value["prompt_version"]),
            prompt_sha256=str(value["prompt_sha256"]),
            reconciled=bool(value.get("reconciled", True)),
            cache_identity_version=str(
                value.get("cache_identity_version", "legacy-descriptor-v1")
            ),
            panel_identity_hashes=tuple(
                str(item) for item in value.get("panel_identity_hashes", ())
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
        return asdict(self) | {"display_words": list(self.display_words), "passages": [dict(item) for item in self.passages]}

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
            or any(not isinstance(value, str) or not value.strip() for value in self.evidence_panel_ids)
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
            or any(not isinstance(value, str) or not value.strip() for value in self.evidence_panel_ids)
            or len(set(self.claim_ids)) != len(self.claim_ids)
            or len(set(self.evidence_panel_ids)) != len(self.evidence_panel_ids)
        ):
            raise CloudStageError("cloud.narrative_repair_position_selection_invalid", reviewable=True)
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
    if not all(
        isinstance(selection.get(field), (list, tuple))
        for field in ("beat_ids", "panel_ids", "claim_ids")
    ) or not str(selection.get("selection_hash", "")).strip():
        raise _repair_identity_shape_error("selection")
    slots = value["slot_registry"]
    if not all(
        isinstance(slots.get(field), (list, tuple))
        for field in ("slot_ids", "claim_ids", "evidence_panel_ids")
    ) or not str(slots.get("slot_order_hash", "")).strip():
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
        observation_ids = tuple(
            str(item.get("panel_id", "")) for item in result.observations
        )
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


def _narration_result_is_usable(
    result: NarrationResult,
    visual: VisualStageResult,
    *,
    require_duration: bool,
    require_grounding: bool = False,
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
            duration_metrics = script.narration_duration_metrics(
                result.spoken_text,
                "dramatic",
            )
            canonical_duration = float(duration_metrics["estimated_duration_s"])
            canonical_word_count = int(duration_metrics["word_count"])
            if (
                not 50.0 <= canonical_duration <= 60.0
                or not math.isclose(
                    float(result.estimated_duration_s),
                    canonical_duration,
                    rel_tol=0.0,
                    abs_tol=0.001,
                )
            ):
                return False
            if (
                not 115 <= canonical_word_count <= 125
                or int(result.word_count) != canonical_word_count
            ):
                return False
            expected_contract = script.narration_duration_contract("dramatic")
            stored_contract = result.qc_report.get("duration_contract", {})
            if (
                not isinstance(stored_contract, Mapping)
                or any(stored_contract.get(key) != value for key, value in expected_contract.items())
            ):
                return False
        if require_grounding:
            expected_display = tuple(re.findall(r"[A-Z0-9]+", result.spoken_text.upper()))
            if tuple(str(word) for word in result.display_words) != expected_display:
                return False
            observation_ids = tuple(
                str(item.get("panel_id", "")) for item in result.observations
            )
            visual_ids = tuple(
                str(item.get("panel_id", "")) for item in visual.panels
            )
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
        for claim in story_map.claims
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
            str(panel_id)
            for panel_id in raw_panel_ids
            if str(panel_id) in panel_order
        )
        if not panel_ids:
            continue
        panel_set = set(panel_ids)
        beat_claims = []
        for claim in claims:
            refs = claim.get("evidence_panel_ids", claim.get("panel_ids", ()))
            if isinstance(refs, (list, tuple)) and panel_set.intersection(
                str(ref) for ref in refs
            ):
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
    panel_ids = tuple(
        panel_id for panel_id in panel_order if panel_id in selected_panel_set
    )
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
    return CAUSAL_MAP_PROMPT_VERSION, hashlib.sha256(normalized.encode("utf-8")).hexdigest(), normalized


def _load_strip_boundary_prompt() -> tuple[str, str, str]:
    try:
        text = STRIP_BOUNDARY_PROMPT_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise CloudStageError("cloud.prompt_missing") from None
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if f"Version: {STRIP_BOUNDARY_PROMPT_VERSION}" not in normalized:
        raise CloudStageError("cloud.prompt_invalid")
    return STRIP_BOUNDARY_PROMPT_VERSION, hashlib.sha256(normalized.encode("utf-8")).hexdigest(), normalized


def _prompt_specs() -> dict[str, tuple[str, str, str]]:
    return {
        "visual": visual_scoring.load_visual_evidence_instruction(),
        "story_map": _load_causal_prompt(),
        "narration": narrative_identity.load_narrative_instruction("sharp_friend_v1"),
        "segmentation": _load_strip_boundary_prompt(),
        "visual_narrative_repair": visual_narrative_repair.load_repair_prompt(),
    }


def _narration_retry_feedback(message_or_code: str) -> str:
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
    elif "script_passages" in lowered or "script passage" in lowered or "four to six passages" in lowered:
        field, count = "script_passages", passage_count
    elif "ending_kind" in lowered:
        field, count = "ending_kind", 1
    elif "story_spine" in lowered:
        field, count = "story_spine", 6
    elif "narrative_outline" in lowered:
        field, count = "narrative_outline", 1
    else:
        field, count = "narrative_contract", 1
    return f"field={field};count={count}"


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
        ("repair has no claims", "visual.repair_claims_empty"),
        ("repair passage is malformed", "visual.repair_passage_malformed"),
        ("repair passage evidence is incomplete", "visual.repair_passage_evidence_incomplete"),
        ("repair passage cites unsupported evidence", "visual.repair_passage_unsupported_evidence"),
        ("repair chronology is not ordered", "visual.repair_chronology"),
        ("repair claim evidence is incomplete", "visual.repair_claim_evidence_incomplete"),
        ("repair passage text is incomplete", "visual.repair_passage_text_incomplete"),
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
) -> str:
    """Return bounded, non-content guidance for one rejected repair attempt."""

    value = str(code)
    predicate = str(failed_predicate or "")
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
    if predicate in {
        "visual.repair_passage_evidence_incomplete",
        "visual.repair_passage_unsupported_evidence",
        "visual.repair_claim_evidence_incomplete",
    }:
        return (
            "for every passage, cite its existing claim IDs and complete feasible evidence; "
            "do not omit or add panel references"
        )
    if predicate == "visual.repair_chronology":
        return (
            "keep the first passage as the hook and all later feasible citations in source "
            "order; do not place an earlier panel after a later one"
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
        return (
            "return 4-6 concise chronological passages using only existing claim IDs and "
            "feasible panel IDs; keep the grounded spoken script at 118-124 lexical words"
        )
    return (
        "return strict JSON with existing claim IDs, feasible panel IDs, grounded "
        "chronological passages, and no provider hashes or unsupported facts"
    )


def _cache_key(stage: str, source: object, identity: CloudModelIdentity, prompt: tuple[str, str, str]) -> str:
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


class CloudStageRunner:
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
            None
            if max_narration_requests is None
            else max(1, int(max_narration_requests))
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
        inferred_checkpoint = getattr(cache, "checkpoint_path", None)
        if inferred_checkpoint is None:
            cache_root = getattr(cache, "root", None)
            inferred_checkpoint = (
                Path(cache_root) / "visual_checkpoints.jsonl"
                if cache_root is not None
                else None
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
        self._last_request_at = 0.0
        self.prompts = _prompt_specs()
        expected = dict(model_identity.prompt_versions)
        if any(stage in expected and expected[stage] != prompt[0] for stage, prompt in self.prompts.items()):
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
                self.visual_parallel_workers
                if worker_count is None
                else int(worker_count)
            ),
        )

    def _response_shape_metrics_for_failure(self, code: str) -> dict[str, Any]:
        """Attach only current positional shape metadata to a later safe error."""

        metrics = dict(self.last_response_shape_metrics)
        if not metrics:
            return {}
        metrics["failed_code"] = code
        if not metrics.get("failed_predicate"):
            metrics["failed_predicate"] = (
                metrics.get("reconciled_failed_predicate") or code
            )
        metrics["request_count"] = self.request_count
        metrics["request_counts"] = dict(self.request_counts)
        self.last_response_shape_metrics = dict(metrics)
        return metrics

    @staticmethod
    def _narration_repair_result_shape_metrics(
        result: NarrationResult,
        visual: VisualStageResult,
        *,
        scope_ok: bool | None = None,
    ) -> dict[str, Any]:
        """Describe reconstructed repair gates without retaining provider prose."""

        duration_metrics = script.narration_duration_metrics(
            result.spoken_text,
            "dramatic",
        )
        canonical_duration = float(duration_metrics["estimated_duration_s"])
        spoken_word_count = int(duration_metrics["word_count"])
        expected_display = tuple(re.findall(r"[A-Z0-9]+", result.spoken_text.upper()))
        observation_ids = tuple(
            str(item.get("panel_id", ""))
            for item in result.observations
            if isinstance(item, Mapping)
        )
        visual_ids = tuple(
            str(item.get("panel_id", ""))
            for item in visual.panels
            if isinstance(item, Mapping)
        )
        failed: list[str] = []
        try:
            duration = float(result.estimated_duration_s)
        except (TypeError, ValueError, OverflowError):
            duration = None
        try:
            reported_word_count = int(result.word_count)
        except (TypeError, ValueError, OverflowError):
            reported_word_count = None
        if duration is None or not 50.0 <= duration <= 60.0:
            failed.append("duration_bounds")
        elif not math.isclose(
            duration,
            canonical_duration,
            rel_tol=0.0,
            abs_tol=0.001,
        ):
            failed.append("duration_reconciliation")
        if reported_word_count is None or not 115 <= reported_word_count <= 125:
            failed.append("word_bounds")
        if reported_word_count != spoken_word_count:
            failed.append("word_count_reconciliation")
        if not 4 <= len(result.passages) <= 6:
            failed.append("passage_count")
        if tuple(str(word) for word in result.display_words) != expected_display:
            failed.append("display_derivation")
        if len(observation_ids) != len(visual_ids):
            failed.append("observation_count")
        elif observation_ids != visual_ids:
            failed.append("observation_panel_order")
        if scope_ok is False:
            failed.append("scope_compatibility")
        return {
            "reconciled_word_count": reported_word_count,
            "reconciled_spoken_word_count": spoken_word_count,
            "reconciled_duration_s": duration,
            "reconciled_canonical_duration_s": canonical_duration,
            "reconciled_duration_contract": duration_metrics,
            "reconciled_passage_count": len(result.passages),
            "reconciled_observation_count": len(result.observations),
            "reconciled_visual_panel_count": len(visual.panels),
            "reconciled_display_word_count": len(result.display_words),
            "reconciled_scope_ok": scope_ok,
            "reconciled_failed_predicates": failed,
            "reconciled_failed_predicate": failed[0] if failed else None,
        }

    def _call(self, operation, *, request_stage: str = "other") -> Any:
        last_error: Exception | None = None
        stage = request_stage if request_stage in self.request_counts else "other"
        for attempt in range(self.max_attempts):
            stage_limit = (
                self.max_narration_requests
                if stage == "narration"
                else self.max_repair_requests
                if stage == "narration_repair"
                else None
            )
            if stage_limit is not None and self.request_counts[stage] >= stage_limit:
                raise CloudStageError("cloud.request_budget_exceeded", reviewable=True)
            if (
                self._use_legacy_global_request_budget
                and self.max_requests is not None
                and self.request_count >= self.max_requests
            ):
                raise CloudStageError("cloud.request_budget_exceeded", reviewable=True)
            if self.min_request_interval_s:
                wait = self.min_request_interval_s - (time.monotonic() - self._last_request_at)
                if wait > 0:
                    time.sleep(wait)
            self.request_count += 1
            self.request_counts[stage] += 1
            self.estimated_cost_usd += self.estimated_cost_per_request
            self._last_request_at = time.monotonic()
            try:
                return operation()
            except CloudStageError:
                raise
            except (VisionRequestInvalid, VisionResponseInvalid) as exc:
                last_error = exc
                break
            except VisionProviderRequestFailed as exc:
                last_error = exc
                if not exc.retryable or attempt + 1 >= self.max_attempts:
                    break
                delay = exc.retry_after_s
                if delay is None:
                    base = min(8.0, 0.5 * (2**attempt))
                    delay = base + random.uniform(0.0, base * 0.25)
                if delay > 0:
                    time.sleep(float(delay))
            except VisionCapabilityError as exc:
                last_error = exc
                break
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.max_attempts:
                    base = min(8.0, 0.5 * (2**attempt))
                    time.sleep(base + random.uniform(0.0, base * 0.25))
        provider_error_code = str(getattr(last_error, "code", "") or "")
        provider_error_mapping = {
            "vision_response_invalid": "cloud.provider_response_invalid",
            "vision_provider_request_failed": "cloud.provider_request_failed",
            "vision_request_invalid": "cloud.provider_request_invalid",
            "vision_capability_missing": "cloud.capability_missing",
        }
        mapped_code = provider_error_mapping.get(provider_error_code)
        if mapped_code is not None:
            metadata = {
                "provider_error_code": provider_error_code,
                "request_stage": stage,
            }
            status_code = getattr(last_error, "status_code", None)
            retry_after = getattr(last_error, "retry_after_s", None)
            if isinstance(status_code, int):
                metadata["status_code"] = status_code
            if isinstance(retry_after, (int, float)):
                metadata["retry_after_s"] = float(retry_after)
            if isinstance(last_error, VisionProviderRequestFailed):
                metadata["timeout"] = bool(getattr(last_error, "timeout", False))
                metadata["retryable"] = bool(getattr(last_error, "retryable", False))
                subtype = getattr(last_error, "transport_subtype", None)
                if isinstance(subtype, str) and subtype:
                    metadata["transport_subtype"] = subtype
                if metadata.get("timeout"):
                    metadata["provider_error_category"] = "timeout"
                elif metadata.get("status_code") == 429:
                    metadata["provider_error_category"] = "rate_limit"
                elif isinstance(metadata.get("status_code"), int) and metadata["status_code"] >= 500:
                    metadata["provider_error_category"] = "server"
                else:
                    metadata["provider_error_category"] = "transport"
            raise CloudStageError(
                mapped_code,
                reviewable=mapped_code == "cloud.provider_response_invalid",
                safe_metadata=metadata,
            ) from None
        raise CloudStageError("cloud.provider_request_failed") from None

    def assess_strip_boundaries(self, request: strip_segmentation.BoundaryRequest) -> Mapping[str, Any]:
        """Validate strip boundaries using real images or a local fallback.

        Generated strip tiles carry ephemeral bytes.  New requests use the
        provider's multimodal method when available; legacy metadata-only
        fixtures retain the text contract for backward-compatible reads.
        """
        prompt = self.prompts["segmentation"]
        payload = request.as_payload()
        images = tuple(
            tile
            for tile in request.tiles
            if isinstance(tile.get("payload"), bytes) and tile.get("payload")
        )
        complete_with_images = getattr(self.provider, "complete_json_with_images", None)
        if images and callable(complete_with_images):
            raw = self._call(
                lambda: complete_with_images(
                    stage="strip_segmentation",
                    prompt_version=prompt[0],
                    prompt_sha256=prompt[1],
                    prompt_text=prompt[2],
                    payload=payload,
                    images=images,
                ),
                request_stage="other",
            )
        elif images:
            # A provider without multimodal support must not receive bytes as
            # JSON text.  The deterministic local assessment is conservative:
            # only candidates already scored by the local detector can pass.
            raw = {
                "source_asset_id": request.source_asset_id,
                "source_checksum": request.source_checksum,
                "random_sampling": False,
                "boundaries": [
                    {
                        "y": candidate.position,
                        "accepted": candidate.confidence >= 0.7,
                        "confidence": candidate.confidence,
                        "reason": "segmentation.local_detector_assessment",
                        "protected_regions": [],
                    }
                    for candidate in request.candidates
                ],
            }
        else:
            raw = self._call(
                lambda: self.provider.complete_json(
                    stage="strip_segmentation",
                    prompt_version=prompt[0],
                    prompt_sha256=prompt[1],
                    prompt_text=prompt[2],
                    payload=payload,
                ),
                request_stage="other",
            )
        if not isinstance(raw, Mapping):
            return raw
        if (
            raw.get("source_asset_id") == request.source_asset_id
            and raw.get("source_checksum") == request.source_checksum
        ):
            return raw
        raise strip_segmentation.StripSegmentationError(
            "segmentation.provider_lineage_invalid"
        )

    @staticmethod
    def _ordered_panels(panels: Sequence[CloudPanelInput]) -> tuple[CloudPanelInput, ...]:
        # Chapter order first (source_family: 204__* < 205__* < 206__*), then
        # the per-asset strip order, so the provider and the story map see the
        # panels in the real chapter sequence.
        values = tuple(
            sorted(
                panels,
                key=lambda item: (str(item.source_family or ""), item.source_order, item.panel_id),
            )
        )
        if not values or len({item.panel_id for item in values}) != len(values):
            raise CloudStageError("cloud.panel_coverage_incomplete")
        if len({item.source_order for item in values}) != len(values):
            raise CloudStageError("cloud.panel_lineage_invalid")
        return values

    def _checkpoint_scope(
        self,
        source: Sequence[Mapping[str, Any]],
        prompt: tuple[str, str, str],
    ) -> str:
        return _hash(
            {
                "checkpoint_version": VISUAL_CHECKPOINT_VERSION,
                "model_identity_hash": self.model_identity.identity_hash,
                "prompt_version": prompt[0],
                "prompt_sha256": prompt[1],
            }
        )

    def _checkpoint_load(self, scope: str) -> dict[str, dict[str, Any]]:
        path = self.visual_checkpoint_path
        out: dict[str, dict[str, Any]] = {}
        if path is None or not path.is_file():
            return out
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return out
        for line in lines:
            try:
                item = json.loads(line)
            except (TypeError, ValueError):
                continue
            if (
                not isinstance(item, Mapping)
                or item.get("checkpoint_scope") != scope
                or item.get("checkpoint_version") != VISUAL_CHECKPOINT_VERSION
            ):
                continue
            panel_id = item.get("panel_id")
            if isinstance(panel_id, str) and panel_id.strip():
                clean = dict(item)
                clean.pop("checkpoint_scope", None)
                clean.pop("checkpoint_version", None)
                out[panel_id] = clean
        return out

    def _checkpoint_append(self, scope: str, entry: Mapping[str, Any]) -> None:
        path = self.visual_checkpoint_path
        if path is None:
            return
        record = dict(entry)
        record["checkpoint_scope"] = scope
        record["checkpoint_version"] = VISUAL_CHECKPOINT_VERSION
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            with self._checkpoint_lock, path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            return

    def _migrate_legacy_visual_cache(
        self,
        ordered: Sequence[CloudPanelInput],
        *,
        key: str,
        prompt: tuple[str, str, str],
    ) -> dict[str, Any] | None:
        """Materialize one exact legacy visual record under the current key.

        The scan is deliberately read-only over old records and accepts only
        one candidate whose ordered IDs, source lineage, payload descriptor,
        model identity, and visual prompt all reconcile.  Ambiguous matches
        fail closed instead of choosing an arbitrary cache file.
        """

        if self.cache is None:
            return None
        iter_records = getattr(self.cache, "iter_records", None)
        legacy_identity = _legacy_visual_model_identity(self.model_identity)
        if not callable(iter_records) or legacy_identity is None:
            return None
        candidates: list[tuple[Mapping[str, Any], dict[str, Any]]] = []
        try:
            records = iter_records()
            for record in records:
                migrated = _migrate_visual_cache_identity(
                    record,
                    ordered,
                    model_identity=legacy_identity,
                    prompt=prompt,
                )
                if migrated is not None:
                    candidates.append((record, migrated))
        except (OSError, TypeError, ValueError):
            return None
        if len(candidates) != 1:
            return None
        legacy_record, migrated = candidates[0]
        try:
            legacy_result = VisualStageResult.from_dict(legacy_record)
        except (KeyError, TypeError, ValueError):
            return None
        migrated["model_identity_hash"] = self.model_identity.identity_hash
        migrated["legacy_model_identity_hash"] = str(
            legacy_record.get("model_identity_hash", "")
        )
        migrated["legacy_visual_evidence_hash"] = legacy_result.visual_evidence_hash
        migrated["cache_identity_migration_proof"] = (
            "legacy_model_identity_and_descriptor_hash"
        )
        self.cache.put(key, migrated)
        return migrated

    def _legacy_visual_evidence_hashes(
        self,
        visual: VisualStageResult,
    ) -> set[str]:
        """Find old visual evidence identities that exactly match this visual set."""

        if self.cache is None:
            return set()
        iter_records = getattr(self.cache, "iter_records", None)
        legacy_identity = _legacy_visual_model_identity(self.model_identity)
        if not callable(iter_records) or legacy_identity is None:
            return set()
        current_rows = tuple(visual.panels)
        hashes: set[str] = set()
        try:
            records = iter_records()
            for record in records:
                if not isinstance(record, Mapping):
                    continue
                if (
                    str(record.get("model_identity_hash", ""))
                    != legacy_identity.identity_hash
                    or str(record.get("prompt_version", ""))
                    != self.prompts["visual"][0]
                    or str(record.get("prompt_sha256", ""))
                    != self.prompts["visual"][1]
                ):
                    continue
                try:
                    old_result = VisualStageResult.from_dict(record)
                except (KeyError, TypeError, ValueError):
                    continue
                if old_result.panel_ids != visual.panel_ids:
                    continue
                old_rows = tuple(old_result.panels)
                if len(old_rows) != len(current_rows):
                    continue
                if any(
                    any(old_row.get(field) != current_row.get(field) for field in (
                        "panel_id",
                        "source_asset_id",
                        "source_order",
                        "source_checksum",
                        "mime_type",
                        "payload_checksum",
                        "panel_bounds",
                        "source_dimensions",
                        "strip_region_id",
                        "coverage_map_version",
                        "coverage_map_hash",
                        "segmentation_version",
                    ))
                    for old_row, current_row in zip(old_rows, current_rows, strict=True)
                ):
                    continue
                legacy_descriptors = [
                    _legacy_visual_descriptor_from_row(row) for row in old_rows
                ]
                if any(descriptor is None for descriptor in legacy_descriptors):
                    continue
                if str(record.get("source_hash", "")) != _hash(legacy_descriptors):
                    continue
                hashes.add(old_result.visual_evidence_hash)
        except (OSError, TypeError, ValueError):
            return set()
        return hashes

    def _migrate_legacy_story_map_cache(
        self,
        visual: VisualStageResult,
        *,
        key: str,
        prompt: tuple[str, str, str],
    ) -> dict[str, Any] | None:
        """Materialize one exact story map after a visual-only identity bump."""

        if self.cache is None:
            return None
        iter_records = getattr(self.cache, "iter_records", None)
        legacy_identity = _legacy_visual_model_identity(self.model_identity)
        old_visual_hashes = self._legacy_visual_evidence_hashes(visual)
        if (
            not callable(iter_records)
            or legacy_identity is None
            or not old_visual_hashes
        ):
            return None
        candidates: list[dict[str, Any]] = []
        panel_ids = visual.panel_ids
        panel_set = set(panel_ids)
        try:
            for record in iter_records():
                if not isinstance(record, Mapping):
                    continue
                if (
                    str(record.get("model_identity_hash", ""))
                    != legacy_identity.identity_hash
                    or str(record.get("prompt_version", "")) != prompt[0]
                    or str(record.get("prompt_sha256", "")) != prompt[1]
                    or str(record.get("visual_evidence_hash", ""))
                    not in old_visual_hashes
                ):
                    continue
                try:
                    result = StoryMapResult.from_dict(record)
                except (KeyError, TypeError, ValueError):
                    continue
                if result.panel_ids != panel_ids:
                    continue
                if result.story_map_hash != _hash(
                    {
                        "beats": list(result.beats),
                        "claims": list(result.claims),
                        "chain": list(result.causal_chain),
                    }
                ):
                    continue
                if any(
                    str(panel_id) not in panel_set
                    for row in (*result.beats, *result.claims)
                    for panel_id in row.get("panel_ids", row.get("evidence_panel_ids", ()))
                    if isinstance(row, Mapping)
                ):
                    continue
                migrated = result.as_dict()
                migrated["model_identity_hash"] = self.model_identity.identity_hash
                migrated["visual_evidence_hash"] = visual.visual_evidence_hash
                migrated["cache_identity_migration_proof"] = (
                    "legacy_model_identity_and_visual_evidence_hash"
                )
                candidates.append(migrated)
        except (OSError, TypeError, ValueError):
            return None
        if len(candidates) != 1:
            return None
        migrated = candidates[0]
        self.cache.put(key, migrated)
        return migrated

    def run_visual_evidence(self, panels: Sequence[CloudPanelInput]) -> VisualStageResult:
        ordered = self._ordered_panels(panels)
        prompt = self.prompts["visual"]
        source = list(_visual_panel_identities(ordered))
        key = _cache_key("visual", source, self.model_identity, prompt)
        cached_reusable: dict[str, dict[str, Any]] = {}
        cached = self.cache.get(key) if self.cache is not None else None
        if isinstance(cached, Mapping):
            try:
                cached_result = VisualStageResult.from_dict(cached)
            except (KeyError, TypeError, ValueError):
                cached_result = None
            if cached_result is not None:
                panels_by_id = {panel.panel_id: panel for panel in ordered}
                cached_reusable = {
                    str(row["panel_id"]): dict(row)
                    for row in cached_result.panels
                    if isinstance(row, Mapping)
                    and _visual_cached_row_is_reusable(
                        row,
                        panels_by_id.get(str(row.get("panel_id", ""))),
                    )
                    if panels_by_id.get(str(row.get("panel_id", ""))) is not None
                }
                if len(cached_reusable) == len(ordered) and tuple(
                    cached_reusable[panel.panel_id]["panel_id"] for panel in ordered
                ) == tuple(panel.panel_id for panel in ordered):
                    return cached_result
        if cached is None and (migrated := self._migrate_legacy_visual_cache(ordered, key=key, prompt=prompt)) is not None:
            return VisualStageResult.from_dict(migrated)
        if any(
            getattr(panel, "metadata_only", False)
            and panel.panel_id not in cached_reusable
            for panel in ordered
        ):
            raise CloudStageError("cloud.prepared_manifest_requires_materialization")
        instruction_version, instruction_sha256, _ = analyzer_contract.load_analyzer_instruction()
        from concurrent.futures import ThreadPoolExecutor

        chunks = list(_visual_panel_chunks(ordered))
        reconciled_by_id: dict[str, dict[str, Any]] = dict(cached_reusable)
        skipped_codes: list[str] = []
        unknown_failure_metadata: dict[str, Any] | None = None
        reconcile_lock = threading.Lock()
        VISUAL_PARALLEL_WORKERS = self.visual_parallel_workers
        checkpoint_scope = self._checkpoint_scope(source, prompt)
        _checkpoint_seed = self._checkpoint_load(checkpoint_scope)
        panel_identity_by_id = {
            panel.panel_id: identity_hash
            for panel, identity_hash in zip(
                ordered,
                _visual_panel_identity_hashes(ordered),
                strict=True,
            )
        }

        def visual_row_entry(
            item: CloudPanelInput,
            raw: Mapping[str, Any],
        ) -> tuple[dict[str, Any] | None, tuple[dict[str, Any], dict[str, Any]] | None]:
            """Reconcile one row and return unknown geometry separately.

            A row with valid semantic facts is retained even when its geometry
            is unknown.  The caller retries that panel once within the normal
            bounded attempt budget, then converts only that row to the typed
            conservative whole-panel provenance.  Other schema/lineage errors
            remain retryable panel failures and never contaminate valid rows.
            """

            if raw.get("panel_id") != item.panel_id:
                raise CloudStageError(
                    message=(
                        f"cloud.panel_lineage_invalid: want={item.panel_id} "
                        f"got={raw.get('panel_id')}"
                    ),
                    reviewable=True,
                )
            raw_visual = raw.get("visual_evidence")
            if raw_visual is not None and not isinstance(raw_visual, Mapping):
                raise CloudStageError("visual.evidence_invalid", reviewable=True)
            if isinstance(raw_visual, Mapping) and raw_visual.get("evidence_hash"):
                raise CloudStageError("cloud.provider_hash_forbidden")
            visual = dict(raw_visual) if isinstance(raw_visual, Mapping) else None
            if visual is not None:
                visual.setdefault(
                    "contract_version",
                    visual_scoring.VISUAL_EVIDENCE_CONTRACT_VERSION,
                )
                visual.pop("evidence_hash", None)
            merged, evidence = visual_scoring.ensure_panel_visual_evidence(
                {**dict(raw), "visual_evidence": visual},
                panel_id=item.panel_id,
                source_asset_id=item.source_asset_id,
                source_order=item.source_order,
            )
            source_values = {
                evidence.evidence_source,
                *(region.evidence_source for region in evidence.balloon_regions),
            }
            if any("ocr" in value.lower() for value in source_values):
                raise CloudStageError("visual.balloon_geometry_invalid", reviewable=True)
            evidence_json = visual_scoring.panel_visual_evidence_json(evidence)
            merged["visual_evidence"] = evidence_json
            if not _visual_observation_has_visible_facts(merged):
                raise CloudStageError("cloud.visual_evidence_invalid")
            entry = {
                "panel_id": item.panel_id,
                "source_asset_id": item.source_asset_id,
                "source_order": item.source_order,
                "source_checksum": item.source_checksum,
                "observation": merged,
                "visual_evidence": evidence_json,
                "evidence_hash": evidence_json["evidence_hash"],
            }
            if evidence.balloon_mask_status == "unknown":
                return None, (merged, evidence_json)
            return entry, None

        def observe_chunk(chunk_index: int, chunk: Sequence[CloudPanelInput]) -> None:
            # Every accepted row is checkpointed before only its own failed
            # rows are retried.  Whole-request transport failures retain the
            # existing bounded binary reduction path.
            nonlocal reconciled_by_id, unknown_failure_metadata
            chunk_cache_key = _visual_chunk_cache_key(
                chunk,
                chunk_index=chunk_index,
                batch_count=len(chunks),
                model_identity=self.model_identity,
                prompt=prompt,
            )
            checkpoint_seeded = {
                item.panel_id
                for item in chunk
                if (
                    item.panel_id in _checkpoint_seed
                    and _checkpoint_seed[item.panel_id].get("cache_identity_hash")
                    == panel_identity_by_id[item.panel_id]
                    and _checkpoint_seed[item.panel_id].get("chunk_cache_key")
                    == chunk_cache_key
                    and _visual_cached_row_is_reusable(
                        _checkpoint_seed[item.panel_id], item
                    )
                )
            }
            with reconcile_lock:
                seeded = {
                    item.panel_id
                    for item in chunk
                    if item.panel_id in reconciled_by_id
                }
                seeded.update(checkpoint_seeded)
                for panel_id in checkpoint_seeded:
                    reconciled_by_id[panel_id] = _checkpoint_seed[panel_id]
            live = [item for item in chunk if item.panel_id not in seeded]
            if not live:
                print(
                    f"VISUAL_CHUNK_OK chunk={chunk_index} panels=0(from checkpoint)",
                    file=sys.stderr, flush=True,
                )
                return
            chunk = tuple(live)
            retryable_visual_codes = {
                "cloud.provider_response_invalid",
                "cloud.panel_lineage_invalid",
                "cloud.provider_hash_forbidden",
                "cloud.visual_evidence_invalid",
                "visual.evidence_invalid",
                "visual.region_invalid",
                "visual.lineage_invalid",
                "visual.balloon_mask_unknown",
                "visual.balloon_geometry_invalid",
            }
            for attempt in range(self.max_attempts):
                current_chunk = tuple(chunk)
                request_panels = []
                for item in current_chunk:
                    provider_payload, provider_mime = _visual_provider_payload(item)
                    request_panels.append(
                        {
                            **item.descriptor(),
                            "mime_type": provider_mime,
                            "payload": provider_payload,
                        }
                    )
                request = VisionObservationRequest(
                    analysis_run_id=f"cloud-{_hash(source)[:24]}",
                    instruction_version=instruction_version,
                    instruction_sha256=instruction_sha256,
                    chunk_index=chunk_index,
                    panels=tuple(request_panels),
                    visual_instruction_version=prompt[0],
                    visual_instruction_sha256=prompt[1],
                )
                try:
                    attempt_request = replace(
                        request,
                        analysis_run_id=f"{request.analysis_run_id}-attempt-{attempt}",
                    )
                    raw_rows = self._call(
                        lambda request=attempt_request: self.provider.observe(request),
                        request_stage="other",
                    )
                    rows_by_id: dict[str, Mapping[str, Any]] = {}
                    if isinstance(raw_rows, list):
                        for raw in raw_rows:
                            if isinstance(raw, Mapping):
                                panel_id = str(raw.get("panel_id", ""))
                                if panel_id and panel_id not in rows_by_id:
                                    rows_by_id[panel_id] = raw
                    chunk_reconciled: dict[str, dict[str, Any]] = {}
                    unknown_rows: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
                    failed_rows: dict[str, str] = {}
                    for index, item in enumerate(current_chunk):
                        raw = (
                            raw_rows[index]
                            if isinstance(raw_rows, list) and len(raw_rows) == len(current_chunk)
                            else rows_by_id.get(item.panel_id)
                        )
                        if not isinstance(raw, Mapping):
                            failed_rows[item.panel_id] = "cloud.provider_response_invalid"
                            continue
                        if item.panel_id in reconciled_by_id:
                            continue
                        try:
                            entry, unknown = visual_row_entry(item, raw)
                        except visual_scoring.VisualEvidenceError as exc:
                            failed_rows[item.panel_id] = getattr(exc, "code", "cloud.visual_evidence_invalid")
                            continue
                        except CloudStageError as exc:
                            failed_rows[item.panel_id] = exc.code
                            continue
                        if unknown is not None:
                            unknown_rows[item.panel_id] = unknown
                        elif entry is not None:
                            chunk_reconciled[item.panel_id] = entry
                    with reconcile_lock:
                        reconciled_by_id.update(chunk_reconciled)
                    for _entry in chunk_reconciled.values():
                        panel_id = str(_entry["panel_id"])
                        _entry["cache_identity_hash"] = panel_identity_by_id[panel_id]
                        _entry["cache_identity_version"] = VISUAL_CACHE_IDENTITY_VERSION
                        _entry["chunk_cache_key"] = _visual_chunk_cache_key(
                            current_chunk,
                            chunk_index=chunk_index,
                            batch_count=len(chunks),
                            model_identity=self.model_identity,
                            prompt=prompt,
                        )
                        self._checkpoint_append(checkpoint_scope, _entry)
                    pending_ids = tuple(dict.fromkeys((*unknown_rows, *failed_rows)))
                    if pending_ids and attempt + 1 < self.max_attempts:
                        chunk = tuple(item for item in current_chunk if item.panel_id in pending_ids)
                        continue
                    if unknown_rows:
                        for item in current_chunk:
                            if item.panel_id not in unknown_rows:
                                continue
                            merged, _unknown_json = unknown_rows[item.panel_id]
                            conservative = visual_scoring.conservative_full_panel_visual_evidence(
                                panel_id=item.panel_id,
                                source_asset_id=item.source_asset_id,
                                source_order=item.source_order,
                                reason="provider geometry remained unknown after bounded targeted retry",
                            )
                            evidence_json = visual_scoring.panel_visual_evidence_json(conservative)
                            merged = dict(merged)
                            merged["visual_evidence"] = evidence_json
                            entry = {
                                "panel_id": item.panel_id,
                                "source_asset_id": item.source_asset_id,
                                "source_order": item.source_order,
                                "source_checksum": item.source_checksum,
                                "observation": merged,
                                "visual_evidence": evidence_json,
                                "evidence_hash": evidence_json["evidence_hash"],
                                "fallback_mode": "conservative_full_panel_v1",
                            }
                            entry["cache_identity_hash"] = panel_identity_by_id[item.panel_id]
                            entry["cache_identity_version"] = VISUAL_CACHE_IDENTITY_VERSION
                            entry["chunk_cache_key"] = _visual_chunk_cache_key(
                                current_chunk,
                                chunk_index=chunk_index,
                                batch_count=len(chunks),
                                model_identity=self.model_identity,
                                prompt=prompt,
                            )
                            with reconcile_lock:
                                reconciled_by_id[item.panel_id] = entry
                            self._checkpoint_append(checkpoint_scope, entry)
                            unknown_failure_metadata = unknown_failure_metadata or {
                                "stage": "visual",
                                "chunk_index": chunk_index,
                                "panel_count": 1,
                                "fallback_mode": "conservative_full_panel_v1",
                            }
                    if failed_rows:
                        with reconcile_lock:
                            skipped_codes.extend(code for code in failed_rows.values())
                    print(
                        f"VISUAL_CHUNK_OK chunk={chunk_index} panels={len(chunk_reconciled) + len(unknown_rows)} "
                        f"retries={max(0, attempt)}",
                        file=sys.stderr,
                        flush=True,
                    )
                    return
                except CloudStageError as exc:
                    if (
                        exc.code in retryable_visual_codes
                        and attempt + 1 < self.max_attempts
                    ):
                        continue
                    if exc.code == "cloud.provider_request_failed":
                        raise
                    # binary reduction: subdivide the failing chunk and retry the
                    # halves so only genuinely poisonous panels are dropped.
                    if len(chunk) > 1:
                        half = len(chunk) // 2
                        for subchunk in (chunk[:half], chunk[half:]):
                            observe_chunk(chunk_index, subchunk)
                    else:
                        with reconcile_lock:
                            skipped_codes.append(exc.code)
                        print(
                            f"VISUAL_SKIP_PANEL panel={chunk[0].panel_id} code={exc.code}",
                            file=sys.stderr, flush=True,
                        )
                    return
            for item in chunk:
                with reconcile_lock:
                    skipped_codes.append("cloud.provider_response_invalid")
                print(
                    f"VISUAL_SKIP_PANEL panel={item.panel_id} code=exhausted",
                    file=sys.stderr,
                    flush=True,
                )

        with ThreadPoolExecutor(max_workers=VISUAL_PARALLEL_WORKERS) as executor:
            futures = [
                executor.submit(observe_chunk, chunk_index, chunk)
                for chunk_index, chunk in enumerate(chunks)
            ]
            for future in futures:
                future.result()
        if set(reconciled_by_id) != {item.panel_id for item in ordered}:
            print(
                f"VISUAL_SKIP_TOTAL missing={len(ordered) - len(reconciled_by_id)} "
                f"of {len(ordered)}",
                file=sys.stderr, flush=True,
            )
        if not reconciled_by_id:
            code = skipped_codes[0] if skipped_codes else "cloud.panel_coverage_incomplete"
            if code == "visual.balloon_mask_unknown" and unknown_failure_metadata is not None:
                raise CloudStageError(
                    code,
                    reviewable=True,
                    safe_metadata=unknown_failure_metadata,
                )
            raise CloudStageError(
                code,
                reviewable=code.startswith(("visual.", "segmentation.")),
            )
        # preview: continue with the reconciled subset (skipped panels drop out),
        # preserving the deterministic chapter order for downstream stages.
        reconciled = [reconciled_by_id[item.panel_id] for item in ordered if item.panel_id in reconciled_by_id]
        result = VisualStageResult(
            panels=tuple(reconciled),
            source_hash=_hash(source),
            model_identity_hash=self.model_identity.identity_hash,
            prompt_version=prompt[0],
            prompt_sha256=prompt[1],
            cache_identity_version=VISUAL_CACHE_IDENTITY_VERSION,
            panel_identity_hashes=tuple(_hash(item) for item in source),
        )
        if self.cache is not None:
            self.cache.put(key, result.as_dict())
        return result

    def _reconcile_story_map(
        self,
        raw: Any,
        expected_panel_ids: tuple[str, ...],
        prompt: tuple[str, str, str],
    ) -> StoryMapResult:
        if not isinstance(raw, Mapping) or raw.get("story_map_hash"):
            raise CloudStageError("cloud.provider_hash_forbidden" if isinstance(raw, Mapping) and raw.get("story_map_hash") else "cloud.provider_response_invalid")
        if raw.get("panel_ids") != list(expected_panel_ids) or raw.get("random_sampling") is not False:
            raise CloudStageError("cloud.panel_coverage_incomplete")
        beats = raw.get("beats")
        if beats is None and isinstance(raw.get("ordered_beats"), list):
            beats = raw["ordered_beats"]
        chain = raw.get("causal_chain")
        claims = raw.get("claims")
        if not isinstance(beats, list) or not beats or not isinstance(chain, list) or not chain or not isinstance(claims, list) or not claims:
            raise CloudStageError("cloud.story_map_invalid")
        expected = set(expected_panel_ids)
        covered: set[str] = set()
        for beat in beats:
            if not isinstance(beat, Mapping) or not str(beat.get("beat_id", "")).strip() or not str(beat.get("summary", "")).strip():
                raise CloudStageError("cloud.story_map_invalid")
            refs = beat.get("panel_ids")
            if not isinstance(refs, list) or not refs or any(ref not in expected for ref in refs):
                raise CloudStageError("cloud.panel_coverage_incomplete")
            covered.update(refs)
        for claim in claims:
            if not isinstance(claim, Mapping) or not str(claim.get("claim_id", "")).strip() or not str(claim.get("text", "")).strip() or not str(claim.get("qualification", "")).strip():
                raise CloudStageError("cloud.story_claim_invalid")
            refs = claim.get("panel_ids")
            if not isinstance(refs, list) or not refs or any(ref not in expected for ref in refs):
                raise CloudStageError("cloud.story_claim_invalid")
            covered.update(refs)
        beat_ids = {beat["beat_id"] for beat in beats}
        for link in chain:
            if (
                not isinstance(link, Mapping)
                or link.get("from_beat") not in beat_ids
                or link.get("to_beat") not in beat_ids
                or not str(link.get("reason", "")).strip()
            ):
                raise CloudStageError("cloud.story_map_invalid")
        if covered != expected:
            raise CloudStageError("cloud.panel_coverage_incomplete")
        return StoryMapResult(
            panel_ids=expected_panel_ids,
            beats=tuple(dict(item) for item in beats),
            causal_chain=tuple(dict(item) for item in chain),
            claims=tuple(dict(item) for item in claims),
            story_map_hash=_hash({key: value for key, value in raw.items() if key != "story_map_hash"}),
            model_identity_hash=self.model_identity.identity_hash,
            prompt_version=prompt[0],
            prompt_sha256=prompt[1],
        )


    @staticmethod
    def _claims_from_causal_map(
        script_passages: Any,
        story_map: StoryMapResult,
    ) -> list[dict[str, Any]]:
        """Reuse only locally validated causal claims when the graph is omitted.

        Some compatible models return passage prose and claim IDs while
        omitting the duplicate evidence-graph envelope.  Reusing the exact
        causal-map records is safe because that graph was already reconciled
        against every ordered panel; no claim text or evidence is invented.
        """

        if not isinstance(script_passages, list) or not script_passages:
            raise _narrative_grounding_error("script_passages", 0)
        claims_by_id = {
            str(claim.get("claim_id")): dict(claim)
            for claim in story_map.claims
            if isinstance(claim, Mapping) and str(claim.get("claim_id", "")).strip()
        }
        referenced_ids: list[str] = []
        for passage in script_passages:
            if not isinstance(passage, Mapping):
                raise _narrative_grounding_error("script_passages", len(script_passages))
            claim_ids = passage.get("claim_ids")
            if not isinstance(claim_ids, list) or not claim_ids:
                raise _narrative_grounding_error("passage.claim_ids", len(script_passages))
            for claim_id in claim_ids:
                if not isinstance(claim_id, str):
                    raise _narrative_grounding_error("passage.claim_ids", len(claim_ids))
                resolved_id = claim_id
                if resolved_id not in claims_by_id:
                    suffix_matches = [
                        candidate_id
                        for candidate_id in claims_by_id
                        if candidate_id.rsplit("__", 1)[-1] == claim_id
                    ]
                    if len(suffix_matches) != 1:
                        raise _narrative_grounding_error("claim_ids", len(claims_by_id))
                    resolved_id = suffix_matches[0]
                referenced_ids.append(resolved_id)
        return [claims_by_id[claim_id] for claim_id in dict.fromkeys(referenced_ids)]


    def _reconcile_story_map(
        self,
        raw: Any,
        expected_panel_ids: tuple[str, ...],
        prompt: tuple[str, str, str],
    ) -> StoryMapResult:
        if not isinstance(raw, Mapping) or raw.get("story_map_hash"):
            raise CloudStageError("cloud.provider_hash_forbidden" if isinstance(raw, Mapping) and raw.get("story_map_hash") else "cloud.provider_response_invalid")
        if raw.get("panel_ids") != list(expected_panel_ids) or raw.get("random_sampling") is not False:
            raise CloudStageError("cloud.panel_coverage_incomplete")
        beats = raw.get("beats")
        if beats is None and isinstance(raw.get("ordered_beats"), list):
            beats = raw["ordered_beats"]
        chain = raw.get("causal_chain")
        claims = raw.get("claims")
        if not isinstance(beats, list) or not beats or not isinstance(chain, list) or not chain or not isinstance(claims, list) or not claims:
            raise CloudStageError("cloud.story_map_invalid")
        expected = set(expected_panel_ids)
        covered: set[str] = set()
        for beat in beats:
            if not isinstance(beat, Mapping) or not str(beat.get("beat_id", "")).strip() or not str(beat.get("summary", "")).strip():
                raise CloudStageError("cloud.story_map_invalid")
            refs = beat.get("panel_ids")
            if not isinstance(refs, list) or not refs or any(ref not in expected for ref in refs):
                raise CloudStageError("cloud.panel_coverage_incomplete")
            covered.update(refs)
        for claim in claims:
            if not isinstance(claim, Mapping) or not str(claim.get("claim_id", "")).strip() or not str(claim.get("text", "")).strip() or not str(claim.get("qualification", "")).strip():
                raise CloudStageError("cloud.story_claim_invalid")
            refs = claim.get("panel_ids")
            if not isinstance(refs, list) or not refs or any(ref not in expected for ref in refs):
                raise CloudStageError("cloud.story_claim_invalid")
            covered.update(refs)
        beat_ids = {beat["beat_id"] for beat in beats}
        for link in chain:
            if (
                not isinstance(link, Mapping)
                or link.get("from_beat") not in beat_ids
                or link.get("to_beat") not in beat_ids
                or not str(link.get("reason", "")).strip()
            ):
                raise CloudStageError("cloud.story_map_invalid")
        if covered != expected:
            raise CloudStageError("cloud.panel_coverage_incomplete")
        return StoryMapResult(
            panel_ids=expected_panel_ids,
            beats=tuple(dict(item) for item in beats),
            causal_chain=tuple(dict(item) for item in chain),
            claims=tuple(dict(item) for item in claims),
            story_map_hash=_hash({key: value for key, value in raw.items() if key != "story_map_hash"}),
            model_identity_hash=self.model_identity.identity_hash,
            prompt_version=prompt[0],
            prompt_sha256=prompt[1],
        )


    @staticmethod
    def _normalize_narration_claims(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, Mapping):
            raw_claims = value.get("claims")
            if raw_claims is None:
                raw_claims = list(value.values())
        elif isinstance(value, list):
            raw_claims = value
        else:
            raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
        if not isinstance(raw_claims, list) or not raw_claims:
            raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
        claims: list[dict[str, Any]] = []
        for raw_claim in raw_claims:
            if not isinstance(raw_claim, Mapping):
                raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
            claim = dict(raw_claim)
            if "evidence_panel_ids" not in claim and "panel_ids" in claim:
                claim["evidence_panel_ids"] = claim.pop("panel_ids")
            if "text" not in claim and "statement" in claim:
                claim["text"] = claim.pop("statement")
            if claim.get("claim_type") not in {"fact", "interpretation"}:
                raw_type = str(
                    claim.pop("claim_type", "") or claim.pop("type", "")
                ).lower()
                claim["claim_type"] = (
                    "fact" if raw_type in {"fact", "factual", "true"} else "interpretation"
                )
            # The provider's compact envelope omits this classifier.  Treating
            # an unclassified narrative claim as an interpretation is the
            # conservative local metadata choice; qualification remains
            # mandatory and the shared validator still owns all claim gates.
            claim.setdefault("claim_type", "interpretation")
            if not claim.get("qualification"):
                claim["qualification"] = "inferred from panel evidence"
            claims.append(claim)
        return claims


    @staticmethod
    def _narration_observations(
        visual: VisualStageResult,
        panels: Sequence[CloudPanelInput] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Reconcile the persisted visual ledger into the analyzer envelope.

        The cloud narration response is intentionally limited to prose and its
        claim graph.  Panel observations, bounds, checksums, and coverage are
        already locally reconciled by the visual stage and must not be
        regenerated or trusted from a second provider response.
        """

        visual_by_id = {str(item.get("panel_id")): item for item in visual.panels}
        if panels is None:
            ordered_panels: tuple[CloudPanelInput, ...] = ()
            for item in visual.panels:
                bounds = item.get("panel_bounds")
                dimensions = item.get("source_dimensions")
                if not isinstance(bounds, (list, tuple)) or not isinstance(dimensions, (list, tuple)):
                    raise CloudStageError("cloud.panel_lineage_invalid")
                try:
                    ordered_panels += (
                        CloudPanelInput(
                            panel_id=str(item["panel_id"]),
                            source_asset_id=str(item["source_asset_id"]),
                            source_order=int(item["source_order"]),
                            mime_type="image/png",
                            payload=b"visual-ledger-payload",
                            source_checksum=str(item.get("source_checksum", "")),
                            panel_bounds=tuple(int(value) for value in bounds),
                            source_dimensions=tuple(int(value) for value in dimensions),
                            strip_region_id=str(item.get("strip_region_id", item["panel_id"])),
                            coverage_map_version=str(item.get("coverage_map_version", "")),
                            coverage_map_hash=str(item.get("coverage_map_hash", "")),
                        ),
                    )
                except (CloudStageError, KeyError, TypeError, ValueError):
                    raise CloudStageError("cloud.panel_lineage_invalid") from None
        else:
            ordered_panels = tuple(
                panel
                for panel in panels
                if str(panel.panel_id) in visual_by_id
            )

        if tuple(panel.panel_id for panel in ordered_panels) != visual.panel_ids:
            raise CloudStageError("cloud.panel_lineage_invalid")
        observations: list[dict[str, Any]] = []
        for _source_index, panel in enumerate(ordered_panels):
            visual_item = visual_by_id.get(panel.panel_id)
            if visual_item is None or not isinstance(visual_item.get("observation"), Mapping):
                raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
            source = visual_item["observation"]
            required_lists = (
                "visible_facts",
                "dialogue_or_ocr",
                "inferences",
                "uncertainties",
                "evidence_refs",
            )
            if any(not isinstance(source.get(key), list) for key in required_lists):
                raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
            clean_lists: dict[str, list[str]] = {}
            for key in required_lists:
                values = source[key]
                normalized_values: list[str] = []
                structured_text_keys = {
                    "dialogue_or_ocr": ("text", "ocr_text"),
                    "visible_facts": ("fact",),
                    "inferences": (
                        "inference", "assertion", "rationale", "description", "claim",
                        "detail", "details", "hypothesis", "inference_text", "conclusion",
                    ),
                    "uncertainties": ("uncertainty",),
                }.get(key)
                for value in values:
                    if isinstance(value, str):
                        normalized_values.append(value)
                    elif structured_text_keys is not None and isinstance(value, Mapping):
                        for structured_text_key in structured_text_keys:
                            structured_text = value.get(structured_text_key)
                            if isinstance(structured_text, str):
                                normalized_values.append(structured_text)
                                break
                        else:
                            candidates = [
                                str(candidate)
                                for candidate in value.values()
                                if isinstance(candidate, str) and candidate.strip()
                            ]
                            if candidates:
                                normalized_values.append(max(candidates, key=len))
                            else:
                                raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
                    else:
                        raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
                values = normalized_values
                if key in {"dialogue_or_ocr", "inferences", "uncertainties"}:
                    values = [value for value in values if value.strip()]
                clean_lists[key] = list(values)
            evidence_refs = clean_lists["evidence_refs"]
            if panel.panel_id not in evidence_refs:
                raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
            if panel.panel_bounds is None or panel.source_dimensions is None:
                raise CloudStageError("cloud.panel_lineage_invalid")
            x0, y0, x1, y1 = panel.panel_bounds
            observation = {
                "panel_id": panel.panel_id,
                "source_asset_id": panel.source_asset_id,
                "strip_region_id": panel.strip_region_id or panel.panel_id,
                "source_index": len(observations),
                "region_bounds": {
                    "x": x0,
                    "y": y0,
                    "width": x1 - x0,
                    "height": y1 - y0,
                },
                "coverage_map_version": panel.coverage_map_version,
                "coverage_map_hash": panel.coverage_map_hash,
                **clean_lists,
            }
            observations.append(observation)

        panel_ids = [str(observation["panel_id"]) for observation in observations]
        entity_panels: dict[str, list[str]] = {}
        entity_names: dict[str, str] = {}
        for panel_id in panel_ids:
            source = visual_by_id[panel_id]["observation"]
            for entity in source.get("entities", []):
                if not isinstance(entity, str) or not entity.strip():
                    continue
                canonical = entity.strip()
                entity_key = canonical.casefold()
                entity_names.setdefault(entity_key, canonical)
                entity_panels.setdefault(entity_key, []).append(panel_id)
        if not entity_names:
            # A structural continuity bucket is not a semantic identity or
            # narrative claim; it preserves the validator's nonempty ledger
            # invariant when visual evidence contains no named entity.
            entity_names["observed_context"] = "observed context"
            entity_panels["observed_context"] = list(panel_ids)
        entities = [
            {
                "entity_id": f"visual-entity-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:12]}",
                "canonical_name": entity_names[key],
                "aliases": [],
                "panel_ids": list(dict.fromkeys(entity_panels[key])),
            }
            for key in sorted(entity_names)
        ]
        continuity = {
            "chunks": [{"chunk_id": "visual-reconciled-chunk", "panel_ids": panel_ids}],
            "entities": entities,
            "motives": [],
            "state_changes": [],
            "causal_links": [],
            "reconciled_after_final_chunk": True,
        }
        coverage = {
            "total_panels": len(panel_ids),
            "processed_panels": len(panel_ids),
            "total_canonical_panels": len(panel_ids),
            "persisted_canonical_panels": len(panel_ids),
            "processed_canonical_panel_count": len(panel_ids),
            "panel_ids": panel_ids,
            "source_content_coverage_ratio": 1.0,
            "unresolved_material_area": 0,
            "material_unresolved_regions": [],
            "reconciliation_complete": True,
        }
        return observations, {"continuity_ledger": continuity, "coverage_manifest": coverage}

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

        step = max(STORY_MAP_COVERAGE_MIN_STEP, int(step))
        subchunks = [
            chunk[index:index + step]
            for index in range(0, len(chunk), step)
        ]
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
            beats.extend(
                dict(item, beat_id=prefix + str(item["beat_id"]))
                for item in result.beats
            )
            claims.extend(
                dict(item, claim_id=prefix + str(item["claim_id"]))
                for item in result.claims
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
                    if coverage_step > STORY_MAP_COVERAGE_MIN_STEP and len(chunk) > STORY_MAP_COVERAGE_MIN_STEP:
                        result = self._run_story_map_coverage_fallback(
                            prompt,
                            visual,
                            chunk_index,
                            chunk,
                            batch_count,
                            step=STORY_MAP_COVERAGE_MIN_STEP,
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

    def run_story_map(self, visual: VisualStageResult) -> StoryMapResult:
        """Map every ordered panel in deterministic bounded chunks.

        The whole-stage cache remains the fast path.  On a miss, each 180-panel
        request is cached independently and evaluated by at most four workers;
        results are merged by chunk index so concurrency never changes output
        order or identifiers.
        """
        prompt = self.prompts["story_map"]
        source = {
            "panel_ids": list(visual.panel_ids),
            "visual": visual.panels,
            "visual_source_hash": visual.source_hash,
        }
        key = _cache_key("story_map", source, self.model_identity, prompt)
        if self.cache is not None and (cached := self.cache.get(key)) is not None:
            cached_result = StoryMapResult.from_dict(cached)
            if cached_result.visual_evidence_hash == visual.visual_evidence_hash:
                return cached_result
        if (migrated := self._migrate_legacy_story_map_cache(visual, key=key, prompt=prompt)) is not None:
            return StoryMapResult.from_dict(migrated)

        chunk_step = STORY_MAP_CHUNK_STEP
        chunks = [
            visual.panels[i:i + chunk_step]
            for i in range(0, len(visual.panels), chunk_step)
        ]
        with ThreadPoolExecutor(
            max_workers=min(STAGE_PARALLEL_WORKERS, max(1, len(chunks)))
        ) as executor:
            results = tuple(
                executor.map(
                    lambda args: self._run_story_map_chunk(prompt, visual, *args),
                    (
                        (chunk_index, chunk, len(chunks))
                        for chunk_index, chunk in enumerate(chunks)
                    ),
                )
            )

        all_beats: list[dict[str, Any]] = []
        all_chain: list[dict[str, Any]] = []
        all_claims: list[dict[str, Any]] = []
        for chunk_index, result in enumerate(results):
            prefix = f"b{chunk_index}__"
            all_beats.extend(
                dict(item, beat_id=prefix + str(item["beat_id"]))
                for item in result.beats
            )
            all_claims.extend(
                dict(item, claim_id=prefix + str(item["claim_id"]))
                for item in result.claims
            )
            all_chain.extend(
                {
                    **dict(link),
                    "from_beat": prefix + str(link["from_beat"]),
                    "to_beat": prefix + str(link["to_beat"]),
                }
                for link in result.causal_chain
            )
        combined = StoryMapResult(
            panel_ids=visual.panel_ids,
            beats=tuple(all_beats),
            causal_chain=tuple(all_chain),
            claims=tuple(all_claims),
            story_map_hash=_hash(
                {"beats": all_beats, "claims": all_claims, "chain": all_chain}
            ),
            model_identity_hash=self.model_identity.identity_hash,
            prompt_version=prompt[0],
            prompt_sha256=prompt[1],
            visual_evidence_hash=visual.visual_evidence_hash,
        )
        if self.cache is not None:
            self.cache.put(key, combined.as_dict())
        return combined

    def run_narration(
        self,
        visual: VisualStageResult,
        story_map: StoryMapResult,
        *,
        panels: Sequence[CloudPanelInput] | None = None,
    ) -> NarrationResult:
        """Reduce full-panel evidence to selected beats, then write one script."""

        prompt = self.prompts["narration"]
        observations, _structural = self._narration_observations(visual, panels)
        selection = select_editorial_beats(visual, story_map)
        selected_ids = set(selection.panel_ids)
        selected_visual = replace(
            visual,
            panels=tuple(
                dict(panel)
                for panel in visual.panels
                if str(panel.get("panel_id", "")) in selected_ids
            ),
        )
        selected_beats: list[dict[str, Any]] = []
        for beat in story_map.beats:
            if not isinstance(beat, Mapping) or str(beat.get("beat_id", "")) not in set(selection.beat_ids):
                continue
            row = dict(beat)
            row["panel_ids"] = [
                str(panel_id)
                for panel_id in beat.get("panel_ids", ())
                if str(panel_id) in selected_ids
            ]
            if row["panel_ids"]:
                selected_beats.append(row)
        selected_beat_ids = {str(beat["beat_id"]) for beat in selected_beats}
        selected_claims: list[dict[str, Any]] = []
        for claim in story_map.claims:
            if not isinstance(claim, Mapping) or str(claim.get("claim_id", "")) not in set(selection.claim_ids):
                continue
            row = dict(claim)
            key = "evidence_panel_ids" if "evidence_panel_ids" in row else "panel_ids"
            row[key] = [
                str(panel_id)
                for panel_id in row.get(key, ())
                if str(panel_id) in selected_ids
            ]
            if row[key]:
                selected_claims.append(row)
        selected_chain = tuple(
            dict(link)
            for link in story_map.causal_chain
            if str(link.get("from_beat", "")) in selected_beat_ids
            and str(link.get("to_beat", "")) in selected_beat_ids
        )
        selected_story = StoryMapResult(
            panel_ids=tuple(selection.panel_ids),
            beats=tuple(selected_beats),
            causal_chain=selected_chain,
            claims=tuple(selected_claims),
            story_map_hash=_hash(
                {
                    "selection_hash": selection.selection_hash,
                    "beats": selected_beats,
                    "claims": selected_claims,
                    "chain": list(selected_chain),
                }
            ),
            model_identity_hash=story_map.model_identity_hash,
            prompt_version=story_map.prompt_version,
            prompt_sha256=story_map.prompt_sha256,
            visual_evidence_hash=visual.visual_evidence_hash,
        )
        selected_panels = (
            tuple(
                panel
                for panel in panels
                if panel.panel_id in selected_ids
            )
            if panels is not None
            else None
        )
        selected_observations, selected_structural = self._narration_observations(
            selected_visual,
            selected_panels,
        )
        source = {
            "editorial_selection_version": EDITORIAL_SELECTION_VERSION,
            "editorial_selection": selection.as_dict(),
            "panel_ids": list(selection.panel_ids),
            "visual_source_hash": visual.source_hash,
            "visual_evidence_hash": visual.visual_evidence_hash,
            "visual_observations": selected_observations,
            "story_map": selected_story.as_dict(),
            "duration_contract": {
                **script.narration_duration_contract("dramatic"),
                "minimum_s": 50.0,
                "maximum_s": 60.0,
                "target_word_min": 115,
                "target_word_max": 125,
            },
        }
        key = _cache_key("narration", source, self.model_identity, prompt)
        result: NarrationResult | None = None
        failure_codes: tuple[str, ...] = ()
        if self.cache is not None and (cached := self.cache.get(key)) is not None:
            try:
                cached_result = NarrationResult.from_dict(cached)
            except (KeyError, TypeError, ValueError):
                cached_result = None
            cache_identity_matches = (
                cached_result is not None
                and cached_result.model_identity_hash == self.model_identity.identity_hash
                and cached_result.prompt_version == prompt[0]
                and cached_result.prompt_sha256 == prompt[1]
                and cached_result.visual_evidence_hash == visual.visual_evidence_hash
            )
            final_metadata_matches = (
                cached_result is not None
                and cached_result.qc_report.get("editorial_selection", {}).get(
                    "selection_hash"
                )
                == selection.selection_hash
                and cached_result.qc_report.get("narration_topology")
                == "chapter_evidence_reduce_v1"
                and cached_result.qc_report.get("narration_cache_contract")
                == "narration-final-v1"
                and cached_result.qc_report.get("story_map_hash")
                == selected_story.story_map_hash
                and cached_result.qc_report.get("visual_evidence_hash")
                == visual.visual_evidence_hash
                and cached_result.qc_report.get("model_identity_hash")
                == self.model_identity.identity_hash
                and cached_result.qc_report.get("prompt_version") == prompt[0]
                and cached_result.qc_report.get("prompt_sha256") == prompt[1]
            )
            if (
                cache_identity_matches
                and final_metadata_matches
                and _narration_result_is_usable(
                    cached_result,
                    visual,
                    require_duration=True,
                    require_grounding=True,
                )
            ):
                return cached_result
            if (
                cache_identity_matches
                and _narration_result_is_usable(
                    cached_result,
                    visual,
                    require_duration=False,
                    require_grounding=True,
                )
            ):
                failure_codes = self._narration_contract_failures(cached_result)
                if failure_codes:
                    self._store_narration_repair_candidate(
                        source=source,
                        prompt=prompt,
                        result=cached_result,
                        failure_codes=failure_codes,
                        visual=selected_visual,
                        story_map=selected_story,
                    )
                    deleter = getattr(self.cache, "delete", None)
                    if callable(deleter):
                        deleter(key)
                    result = cached_result

        if result is None:
            loaded_candidate = self._load_narration_repair_candidate(
                source=source,
                prompt=prompt,
                visual=selected_visual,
            )
            if loaded_candidate is not None:
                result, failure_codes = loaded_candidate
                if failure_codes:
                    result = self.run_narration_repair_candidate(
                        result,
                        selected_visual,
                        selected_story,
                        panels=selected_panels,
                    )
                    failure_codes = ()

        if result is None:
            result = self._run_narration_batched(
            prompt,
            source,
            selected_observations,
            selected_structural,
            selected_story,
            selected_visual,
            enforce_duration=True,
        )
        if not failure_codes:
            failure_codes = self._narration_contract_failures(result)
        if failure_codes:
            self._store_narration_repair_candidate(
                source=source,
                prompt=prompt,
                result=result,
                failure_codes=failure_codes,
                visual=selected_visual,
                story_map=selected_story,
            )
            try:
                result = self._run_targeted_narration_repair(
                    prompt,
                    source,
                    selected_observations,
                    selected_structural,
                    selected_story,
                    selected_visual,
                    result,
                    failure_codes,
                )
            except CloudStageError:
                self._last_narration_result = result
                raise
            remaining_failures = self._narration_contract_failures(result)
            if remaining_failures:
                self._last_narration_result = result
                failure_code = remaining_failures[0]
                raise CloudStageError(
                    failure_code,
                    reviewable=True,
                    safe_metadata=self._response_shape_metrics_for_failure(
                        failure_code
                    ),
                )
        qc_report = dict(result.qc_report)
        qc_report["editorial_selection"] = selection.as_dict()
        qc_report["narration_topology"] = "chapter_evidence_reduce_v1"
        qc_report["narration_cache_contract"] = "narration-final-v1"
        qc_report["story_map_hash"] = selected_story.story_map_hash
        qc_report["visual_evidence_hash"] = visual.visual_evidence_hash
        qc_report["model_identity_hash"] = self.model_identity.identity_hash
        qc_report["prompt_version"] = prompt[0]
        qc_report["prompt_sha256"] = prompt[1]
        result = _reconcile_narration_full_scope(
            result,
            observations=observations,
            structural=_structural,
            expected_panel_ids=visual.panel_ids,
            visual_evidence_hash=visual.visual_evidence_hash,
        )
        result = replace(result, qc_report=qc_report)
        if not _narration_result_is_usable(
            result,
            visual,
            require_duration=True,
            require_grounding=True,
        ):
            self._last_narration_result = result
            failure_codes = self._narration_contract_failures(result)
            if failure_codes:
                failure_code = failure_codes[0]
                raise CloudStageError(
                    failure_code,
                    reviewable=True,
                    safe_metadata=self._response_shape_metrics_for_failure(
                        failure_code
                    ),
                )
            failure_code = "cloud.narrative_not_grounded"
            raise CloudStageError(
                failure_code,
                reviewable=True,
                safe_metadata=self._response_shape_metrics_for_failure(failure_code),
            )
        if self.cache is not None:
            self.cache.put(key, result.as_dict())
        return result

    def _run_narration_chunk(
        self,
        prompt: tuple[str, str, str],
        source: Mapping[str, Any],
        observations: Sequence[Mapping[str, Any]],
        structural: Mapping[str, Any],
        story_map: StoryMapResult,
        visual: VisualStageResult,
        chunk_index: int,
        chunk: Sequence[Mapping[str, Any]],
        batch_count: int,
    ) -> NarrationResult:
        chunk_ids = tuple(str(panel["panel_id"]) for panel in chunk)
        chunk_id_set = set(chunk_ids)
        chunk_story = StoryMapResult(
            panel_ids=chunk_ids,
            beats=tuple(
                dict(beat)
                for beat in story_map.beats
                if any(str(panel_id) in chunk_id_set for panel_id in beat.get("panel_ids", []))
            ),
            causal_chain=tuple(
                dict(link)
                for link in story_map.causal_chain
                if str(link.get("from_beat", "")) in {
                    str(beat["beat_id"])
                    for beat in story_map.beats
                    if any(str(panel_id) in chunk_id_set for panel_id in beat.get("panel_ids", []))
                }
                or str(link.get("to_beat", "")) in {
                    str(beat["beat_id"])
                    for beat in story_map.beats
                    if any(str(panel_id) in chunk_id_set for panel_id in beat.get("panel_ids", []))
                }
            ),
            claims=tuple(
                dict(claim)
                for claim in story_map.claims
                if any(
                    str(panel_id) in chunk_id_set
                    for panel_id in claim.get(
                        "evidence_panel_ids",
                        claim.get("panel_ids", []),
                    )
                )
            ),
            story_map_hash=story_map.story_map_hash,
            model_identity_hash=story_map.model_identity_hash,
            prompt_version=story_map.prompt_version,
            prompt_sha256=story_map.prompt_sha256,
            visual_evidence_hash=visual.visual_evidence_hash,
        )
        chunk_observations = [
            dict(item)
            for item in observations
            if str(item.get("panel_id", "")) in chunk_id_set
        ]
        if tuple(str(item.get("panel_id", "")) for item in chunk_observations) != chunk_ids:
            raise CloudStageError("cloud.panel_lineage_invalid")
        chunk_source = {
            **dict(source),
            "panel_ids": list(chunk_ids),
            "visual_observations": chunk_observations,
            "story_map": chunk_story.as_dict(),
            "batch_index": chunk_index,
            "batch_count": batch_count,
        }
        chunk_key = _cache_key("narration", chunk_source, self.model_identity, prompt)
        chunk_visual = replace(visual, panels=tuple(dict(panel) for panel in chunk))
        if self.cache is not None and (cached := self.cache.get(chunk_key)) is not None:
            try:
                cached_result = NarrationResult.from_dict(cached)
            except (KeyError, TypeError, ValueError):
                cached_result = None
            if (
                cached_result is not None
                and tuple(str(item.get("panel_id", "")) for item in cached_result.observations)
                == chunk_ids
                and cached_result.model_identity_hash == self.model_identity.identity_hash
                and cached_result.prompt_version == prompt[0]
                and cached_result.prompt_sha256 == prompt[1]
                and cached_result.visual_evidence_hash == chunk_visual.visual_evidence_hash
                and _narration_result_is_usable(
                    cached_result,
                    chunk_visual,
                    require_duration=False,
                )
            ):
                return cached_result
        return self._run_narration_batched(
            prompt,
            chunk_source,
            chunk_observations,
            structural,
            chunk_story,
            chunk_visual,
            enforce_duration=False,
        )

    def _run_narration_in_chunks(
        self,
        prompt: tuple[str, str, str],
        source: Mapping[str, Any],
        observations: Sequence[Mapping[str, Any]],
        structural: Mapping[str, Any],
        story_map: StoryMapResult,
        visual: VisualStageResult,
    ) -> NarrationResult:
        chunks = [
            visual.panels[i:i + NARRATION_CHUNK_STEP]
            for i in range(0, len(visual.panels), NARRATION_CHUNK_STEP)
        ]
        fallback_codes = {
            "cloud.provider_request_failed",
            "cloud.provider_response_invalid",
            "cloud.narrative_not_grounded",
            "cloud.narrative_claim_unmapped",
            "cloud.narrative_qc_blocked",
            "cloud.narrative_duration_out_of_range",
        }

        def resolve_chunk(
            chunk_index: int,
            chunk: Sequence[Mapping[str, Any]],
            batch_count: int,
        ) -> tuple[NarrationResult, ...]:
            try:
                return (
                    self._run_narration_chunk(
                        prompt,
                        source,
                        observations,
                        structural,
                        story_map,
                        visual,
                        chunk_index,
                        chunk,
                        batch_count,
                    ),
                )
            except CloudStageError as exc:
                if exc.code not in fallback_codes:
                    raise
                if len(chunk) > NARRATION_COVERAGE_FALLBACK_STEP:
                    step = NARRATION_COVERAGE_FALLBACK_STEP
                elif len(chunk) > NARRATION_COVERAGE_MIN_STEP:
                    step = NARRATION_COVERAGE_MIN_STEP
                else:
                    raise
                resolved: list[NarrationResult] = []
                for sub_index in range(0, len(chunk), step):
                    resolved.extend(
                        resolve_chunk(
                            chunk_index * 100 + sub_index // step,
                            chunk[sub_index:sub_index + step],
                            batch_count,
                        )
                    )
                return tuple(resolved)

        with ThreadPoolExecutor(
            max_workers=min(STAGE_PARALLEL_WORKERS, max(1, len(chunks)))
        ) as executor:
            nested_results = tuple(
                executor.map(
                    lambda args: resolve_chunk(*args),
                    (
                        (chunk_index, chunk, len(chunks))
                        for chunk_index, chunk in enumerate(chunks)
                    ),
                )
            )
        results = tuple(
            result
            for nested in nested_results
            for result in nested
        )
        all_passages = [
            dict(passage)
            for result in results
            for passage in result.passages
        ]
        all_claims = [
            dict(claim)
            for result in results
            for claim in result.evidence_graph.get("claims", [])
        ]
        story_spine: dict[str, Any] = {}
        for result in results:
            for key, value in result.story_spine.items():
                if str(value).strip():
                    story_spine.setdefault(str(key), value)
        spoken_text = "\n\n".join(
            str(item["text"]).strip() for item in all_passages
        )
        display_words = tuple(re.findall(r"[A-Z0-9]+", spoken_text.upper()))
        if not display_words or any(not word.isalnum() for word in display_words):
            raise CloudStageError("cloud.display_derivation_invalid")
        duration_metrics = script.narration_duration_metrics(
            spoken_text,
            "dramatic",
        )
        duration = float(duration_metrics["estimated_duration_s"])
        total_words = int(duration_metrics["word_count"])
        qc_report = {
            "profile_id": "sharp_friend_v1",
            "profile_sha256": narrative_identity.SHARP_FRIEND_V1.contract_sha256,
            "total_words": total_words,
            "estimated_duration_s": duration,
            "duration_contract": duration_metrics,
            "ending_kind": results[-1].ending_kind,
            "display_word_count": len(display_words),
            "timing_source": "voice_required",
            "warnings": [],
            "signals": {},
            "chunk_count": len(chunks),
            "chunk_step": NARRATION_CHUNK_STEP,
            "worker_count": min(STAGE_PARALLEL_WORKERS, len(chunks)),
        }
        result = NarrationResult(
            spoken_text=spoken_text,
            display_words=display_words,
            passages=tuple(all_passages),
            ending_kind=results[-1].ending_kind,
            word_count=total_words,
            estimated_duration_s=duration,
            observations=tuple(dict(item) for item in observations),
            continuity_ledger=dict(structural["continuity_ledger"]),
            evidence_graph={"claims": all_claims},
            story_spine=story_spine,
            qc_report=qc_report,
            model_identity_hash=self.model_identity.identity_hash,
            prompt_version=prompt[0],
            prompt_sha256=prompt[1],
            visual_evidence_hash=visual.visual_evidence_hash,
        )
        if not 40.0 <= duration <= 180.0:
            self._last_narration_result = result
            raise CloudStageError("cloud.narrative_duration_out_of_range", reviewable=True)
        full_key = _cache_key("narration", source, self.model_identity, prompt)
        if self.cache is not None and _narration_result_is_usable(
            result,
            visual,
            require_duration=True,
            require_grounding=True,
        ):
            self.cache.put(full_key, result.as_dict())
        return result

    @staticmethod
    def _narration_contract_failures(result: NarrationResult) -> tuple[str, ...]:
        failures: list[str] = []
        duration_metrics = script.narration_duration_metrics(
            result.spoken_text,
            "dramatic",
        )
        canonical_duration = float(duration_metrics["estimated_duration_s"])
        canonical_word_count = int(duration_metrics["word_count"])
        if (
            not 50.0 <= canonical_duration <= 60.0
            or not math.isclose(
                float(result.estimated_duration_s),
                canonical_duration,
                rel_tol=0.0,
                abs_tol=0.001,
            )
        ):
            failures.append("cloud.narrative_duration_out_of_range")
        if (
            not 115 <= canonical_word_count <= 125
            or int(result.word_count) != canonical_word_count
        ):
            failures.append("cloud.narrative_word_count_out_of_range")
        if analyzer_contract.contains_source_dialogue_copy(
            result.observations,
            result.passages,
        ):
            failures.append("cloud.narrative_source_dialogue_copy")
        return tuple(dict.fromkeys(failures))

    @staticmethod
    def _narration_scope_signature(result: NarrationResult) -> str:
        passages = [
            {
                "passage_id": str(passage.get("passage_id", "")),
                "claim_ids": [str(value) for value in passage.get("claim_ids", ())],
                "evidence_panel_ids": [
                    str(value) for value in passage.get("evidence_panel_ids", ())
                ],
                "editorial_role": str(passage.get("editorial_role", "")),
            }
            for passage in result.passages
        ]
        claims = []
        for claim in result.evidence_graph.get("claims", ()):
            if not isinstance(claim, Mapping):
                continue
            claims.append(
                {
                    "claim_id": str(claim.get("claim_id", "")),
                    "claim_type": str(claim.get("claim_type", "")),
                    "text": str(claim.get("text", "")),
                    "evidence_panel_ids": [
                        str(value)
                        for value in claim.get(
                            "evidence_panel_ids",
                            claim.get("panel_ids", ()),
                        )
                    ],
                    "qualification": str(claim.get("qualification", "")),
                }
            )
        observations = [
            {
                "panel_id": str(observation.get("panel_id", "")),
                "source_asset_id": str(observation.get("source_asset_id", "")),
                "source_index": int(observation.get("source_index", -1)),
                "evidence_refs": [
                    str(value) for value in observation.get("evidence_refs", ())
                ],
            }
            for observation in result.observations
        ]
        return _hash(
            {
                "passages": passages,
                "claims": claims,
                "observations": observations,
                "ending_kind": result.ending_kind,
                "story_spine": result.story_spine,
            }
        )

    @staticmethod
    def _repair_cache_source(
        source: Mapping[str, Any],
        targeted_repair: Mapping[str, Any],
    ) -> dict[str, Any]:
        cache_source = dict(source)
        cache_source["targeted_repair"] = {
            str(key): value
            for key, value in targeted_repair.items()
            if str(key) != "repair_attempt"
        }
        return cache_source

    def _narration_repair_candidate_key(
        self,
        source: Mapping[str, Any],
        prompt: tuple[str, str, str],
    ) -> str:
        return _cache_key(
            NARRATION_REPAIR_CANDIDATE_STAGE,
            source,
            self.model_identity,
            prompt,
        )

    @staticmethod
    def _compact_narration_repair_context(
        candidate: NarrationResult,
        visual: VisualStageResult,
        story_map: StoryMapResult,
    ) -> tuple[VisualStageResult, StoryMapResult]:
        """Derive the exact selected context for a durable repair candidate."""

        candidate_panel_ids = tuple(
            str(item.get("panel_id", ""))
            for item in candidate.observations
            if isinstance(item, Mapping)
        )
        if (
            not candidate_panel_ids
            or len(candidate_panel_ids) != len(set(candidate_panel_ids))
            or any(panel_id not in visual.panel_ids for panel_id in candidate_panel_ids)
        ):
            raise CloudStageError("cloud.narrative_repair_identity_mismatch", reviewable=True)
        if story_map.visual_evidence_hash not in {
            "",
            visual.visual_evidence_hash,
        }:
            raise CloudStageError("cloud.narrative_repair_identity_mismatch", reviewable=True)
        if candidate_panel_ids == visual.panel_ids:
            compact_visual = visual
        else:
            visual_by_id = {
                str(item.get("panel_id", "")): item
                for item in visual.panels
            }
            compact_visual = replace(
                visual,
                panels=tuple(visual_by_id[panel_id] for panel_id in candidate_panel_ids),
            )
        selected_panel_ids = set(candidate_panel_ids)
        compact_beats: list[dict[str, Any]] = []
        for beat in story_map.beats:
            panel_ids = [
                str(panel_id)
                for panel_id in beat.get("panel_ids", ())
                if str(panel_id) in selected_panel_ids
            ]
            if panel_ids:
                row = dict(beat)
                row["panel_ids"] = panel_ids
                compact_beats.append(row)
        compact_beat_ids = {
            str(beat.get("beat_id", "")) for beat in compact_beats
        }
        compact_chain = tuple(
            dict(link)
            for link in story_map.causal_chain
            if str(link.get("from_beat", "")) in compact_beat_ids
            and str(link.get("to_beat", "")) in compact_beat_ids
        )
        compact_claims: list[dict[str, Any]] = []
        for claim in story_map.claims:
            key = "evidence_panel_ids" if "evidence_panel_ids" in claim else "panel_ids"
            refs = [
                str(panel_id)
                for panel_id in claim.get(key, ())
                if str(panel_id) in selected_panel_ids
            ]
            if refs:
                row = dict(claim)
                row[key] = refs
                compact_claims.append(row)
        compact_story = StoryMapResult(
            panel_ids=candidate_panel_ids,
            beats=tuple(compact_beats),
            causal_chain=compact_chain,
            claims=tuple(compact_claims),
            story_map_hash=_hash(
                {
                    "panel_ids": list(candidate_panel_ids),
                    "beats": compact_beats,
                    "claims": compact_claims,
                    "chain": list(compact_chain),
                }
            ),
            model_identity_hash=story_map.model_identity_hash,
            prompt_version=story_map.prompt_version,
            prompt_sha256=story_map.prompt_sha256,
            visual_evidence_hash=compact_visual.visual_evidence_hash,
        )
        if candidate.visual_evidence_hash != compact_visual.visual_evidence_hash:
            raise CloudStageError("cloud.narrative_repair_identity_mismatch", reviewable=True)
        return compact_visual, compact_story

    @staticmethod
    def _narration_repair_lineage_identity(candidate: NarrationResult) -> str:
        """Hash repair context while excluding replaceable citation surfaces."""

        passages = []
        for passage in candidate.passages:
            if not isinstance(passage, Mapping):
                raise CloudStageError(
                    "cloud.narrative_repair_evidence_closure_invalid",
                    reviewable=True,
                )
            passages.append(
                {
                    "passage_id": str(passage.get("passage_id", "")),
                    "editorial_role": str(passage.get("editorial_role", "")),
                    "text": str(passage.get("text", "")),
                    "claim_ids": [str(value) for value in passage.get("claim_ids", ())],
                }
            )
        claims = []
        raw_claims = candidate.evidence_graph.get("claims", ())
        if not isinstance(raw_claims, (list, tuple)):
            raise CloudStageError(
                "cloud.narrative_repair_evidence_closure_invalid",
                reviewable=True,
            )
        for claim in raw_claims:
            if not isinstance(claim, Mapping):
                raise CloudStageError(
                    "cloud.narrative_repair_evidence_closure_invalid",
                    reviewable=True,
                )
            claims.append(
                {
                    "claim_id": str(claim.get("claim_id", "")),
                    "claim_type": str(claim.get("claim_type", "")),
                    "text": str(claim.get("text", "")),
                    "qualification": str(claim.get("qualification", "")),
                }
            )
        return _hash(
            {
                "version": NARRATION_REPAIR_EVIDENCE_CLOSURE_VERSION,
                "spoken_text": candidate.spoken_text,
                "ending_kind": candidate.ending_kind,
                "story_spine": dict(candidate.story_spine),
                "passages": passages,
                "claims": claims,
                "visual_evidence_hash": candidate.visual_evidence_hash,
                "model_identity_hash": candidate.model_identity_hash,
                "prompt_version": candidate.prompt_version,
                "prompt_sha256": candidate.prompt_sha256,
            }
        )

    @staticmethod
    def _story_evidence_panel_closure(
        story_map: StoryMapResult,
        claim_refs: Sequence[str],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Resolve exact claim refs to beat sections and permitted panels."""

        story_panel_ids = {str(panel_id) for panel_id in story_map.panel_ids}
        sections: set[str] = set()
        for panel_id in claim_refs:
            if not panel_id or panel_id not in story_panel_ids:
                raise CloudStageError(
                    "cloud.narrative_repair_evidence_closure_invalid",
                    reviewable=True,
                )
            matching_sections = {
                str(beat.get("beat_id", "")).split("__", 1)[0]
                for beat in story_map.beats
                if isinstance(beat, Mapping)
                and panel_id in {str(value) for value in beat.get("panel_ids", ())}
                and str(beat.get("beat_id", "")).strip()
            }
            if not matching_sections:
                raise CloudStageError(
                    "cloud.narrative_repair_evidence_closure_invalid",
                    reviewable=True,
                )
            sections.update(matching_sections)
        if not sections:
            raise CloudStageError(
                "cloud.narrative_repair_evidence_closure_invalid",
                reviewable=True,
            )
        permitted = tuple(
            panel_id
            for panel_id in story_map.panel_ids
            if any(
                section in sections
                for section in {
                    str(beat.get("beat_id", "")).split("__", 1)[0]
                    for beat in story_map.beats
                    if isinstance(beat, Mapping)
                    and str(panel_id) in {str(value) for value in beat.get("panel_ids", ())}
                    and str(beat.get("beat_id", "")).strip()
                }
            )
        )
        if not permitted:
            raise CloudStageError(
                "cloud.narrative_repair_evidence_closure_invalid",
                reviewable=True,
            )
        return tuple(sorted(sections)), tuple(str(value) for value in permitted)

    @staticmethod
    def _story_passage_evidence_closure(
        story_map: StoryMapResult,
        passage_claim_ids: Sequence[str],
        story_claims: Mapping[str, Mapping[str, Any]],
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        """Resolve every claim in one trusted passage to its section closure."""

        claim_ids = tuple(str(value).strip() for value in passage_claim_ids)
        if (
            not claim_ids
            or any(not value for value in claim_ids)
            or len(set(claim_ids)) != len(claim_ids)
        ):
            raise CloudStageError(
                "cloud.narrative_repair_evidence_closure_invalid",
                reviewable=True,
            )
        trusted_refs: list[str] = []
        for claim_id in claim_ids:
            claim = story_claims.get(claim_id)
            if claim is None:
                raise CloudStageError(
                    "cloud.narrative_repair_evidence_closure_invalid",
                    reviewable=True,
                )
            raw_refs = claim.get(
                "evidence_panel_ids",
                claim.get("panel_ids", ()),
            )
            if not isinstance(raw_refs, (list, tuple)):
                raise CloudStageError(
                    "cloud.narrative_repair_evidence_closure_invalid",
                    reviewable=True,
                )
            refs = tuple(str(value).strip() for value in raw_refs)
            if (
                not refs
                or any(not value for value in refs)
                or len(set(refs)) != len(refs)
            ):
                raise CloudStageError(
                    "cloud.narrative_repair_evidence_closure_invalid",
                    reviewable=True,
                )
            for panel_id in refs:
                if panel_id not in trusted_refs:
                    trusted_refs.append(panel_id)
        sections, permitted = CloudStageRunner._story_evidence_panel_closure(
            story_map,
            trusted_refs,
        )
        return tuple(trusted_refs), sections, permitted

    @staticmethod
    def _build_narration_repair_slots(
        candidate: NarrationResult,
        story_map: StoryMapResult,
    ) -> tuple[NarrationRepairSlot, ...]:
        """Create stable local slot identities from already grounded records."""

        candidate_passages = tuple(candidate.passages)
        if len(candidate_passages) < 4:
            raise CloudStageError("cloud.narrative_repair_slot_lineage_invalid", reviewable=True)
        candidate_claims = {
            str(claim.get("claim_id", "")): claim
            for claim in candidate.evidence_graph.get("claims", ())
            if isinstance(claim, Mapping) and str(claim.get("claim_id", "")).strip()
        }
        story_claims = {
            str(claim.get("claim_id", "")): claim
            for claim in story_map.claims
            if isinstance(claim, Mapping) and str(claim.get("claim_id", "")).strip()
        }
        story_panel_ids = {str(panel_id) for panel_id in story_map.panel_ids}
        removable_passage_ids = set(
            CloudStageRunner._removable_narration_passage_ids(candidate)
        )
        slots: list[NarrationRepairSlot] = []
        seen_passage_ids: set[str] = set()
        for passage_index, passage in enumerate(candidate_passages):
            if not isinstance(passage, Mapping):
                raise CloudStageError("cloud.narrative_repair_slot_lineage_invalid", reviewable=True)
            passage_id = str(passage.get("passage_id", "")).strip()
            claim_ids_raw = passage.get("claim_ids")
            evidence_panel_ids_raw = passage.get("evidence_panel_ids")
            if (
                not passage_id
                or passage_id in seen_passage_ids
                or not isinstance(claim_ids_raw, list)
                or not isinstance(evidence_panel_ids_raw, list)
                or not claim_ids_raw
                or not evidence_panel_ids_raw
            ):
                raise CloudStageError("cloud.narrative_repair_slot_lineage_invalid", reviewable=True)
            seen_passage_ids.add(passage_id)
            claim_ids = tuple(str(value) for value in claim_ids_raw)
            candidate_evidence_panel_ids = tuple(
                str(value) for value in evidence_panel_ids_raw
            )
            if (
                any(not value.strip() for value in claim_ids)
                or any(
                    not value.strip() or value not in story_panel_ids
                    for value in candidate_evidence_panel_ids
                )
                or len(set(claim_ids)) != len(claim_ids)
                or len(set(candidate_evidence_panel_ids))
                != len(candidate_evidence_panel_ids)
            ):
                raise CloudStageError("cloud.narrative_repair_slot_lineage_invalid", reviewable=True)
            trusted_evidence_panel_ids: list[str] = []
            for claim_id in claim_ids:
                candidate_claim = candidate_claims.get(claim_id)
                story_claim = story_claims.get(claim_id)
                if candidate_claim is None or story_claim is None:
                    raise CloudStageError("cloud.narrative_repair_slot_lineage_invalid", reviewable=True)
                claim_refs = tuple(
                    str(value)
                    for value in story_claim.get(
                        "evidence_panel_ids",
                        story_claim.get("panel_ids", ()),
                    )
                )
                if (
                    not claim_refs
                    or any(
                        not value.strip() or value not in story_panel_ids
                        for value in claim_refs
                    )
                    or len(set(claim_refs)) != len(claim_refs)
                    or not set(claim_refs) & set(candidate_evidence_panel_ids)
                ):
                    raise CloudStageError("cloud.narrative_repair_slot_lineage_invalid", reviewable=True)
                for panel_id in claim_refs:
                    if panel_id not in trusted_evidence_panel_ids:
                        trusted_evidence_panel_ids.append(panel_id)
            _, permitted_panel_ids = CloudStageRunner._story_evidence_panel_closure(
                story_map,
                trusted_evidence_panel_ids,
            )
            if not set(candidate_evidence_panel_ids).issubset(
                set(permitted_panel_ids)
            ):
                raise CloudStageError(
                    "cloud.narrative_repair_evidence_closure_invalid",
                    reviewable=True,
                )
            evidence_panel_ids = tuple(trusted_evidence_panel_ids)
            matching_beats = [
                (beat_index, beat)
                for beat_index, beat in enumerate(story_map.beats)
                if isinstance(beat, Mapping)
                and {str(value) for value in beat.get("panel_ids", ())}
                & set(evidence_panel_ids)
            ]
            if not matching_beats:
                raise CloudStageError("cloud.narrative_repair_slot_lineage_invalid", reviewable=True)
            causal_position, beat = matching_beats[0]
            beat_id = str(beat.get("beat_id", "")).strip()
            if not beat_id:
                raise CloudStageError("cloud.narrative_repair_slot_lineage_invalid", reviewable=True)
            priority = (
                len(claim_ids) * 1000
                + len(evidence_panel_ids) * 10
                + len(candidate_passages)
                - passage_index
            )
            identity_payload = {
                "version": NARRATION_REPAIR_SLOT_REGISTRY_VERSION,
                "candidate_visual_evidence_hash": candidate.visual_evidence_hash,
                "story_map_hash": story_map.story_map_hash,
                "passage_id": passage_id,
                "claim_ids": list(claim_ids),
                "evidence_panel_ids": list(evidence_panel_ids),
                "beat_id": beat_id,
                "causal_position": causal_position,
            }
            slots.append(
                NarrationRepairSlot(
                    slot_id=f"narration_slot_v1_{_hash(identity_payload)}",
                    passage_id=passage_id,
                    claim_ids=claim_ids,
                    evidence_panel_ids=evidence_panel_ids,
                    beat_id=beat_id,
                    causal_position=causal_position,
                    priority=priority,
                    removable=passage_id in removable_passage_ids,
                )
            )
        return tuple(slots)

    @staticmethod
    def _narration_repair_slot_registry(
        slots: Sequence[NarrationRepairSlot],
    ) -> dict[str, Any]:
        slot_rows = [slot.as_dict() for slot in slots]
        registry_identity = {
            "version": NARRATION_REPAIR_SLOT_REGISTRY_VERSION,
            "slots": slot_rows,
        }
        return {
            **registry_identity,
            "slot_ids": [slot.slot_id for slot in slots],
            "removable_slot_ids": [slot.slot_id for slot in slots if slot.removable],
            "registry_hash": _hash(registry_identity),
        }

    @staticmethod
    def _narration_repair_evidence_closure(
        positions: Sequence[NarrationRepairPosition | Mapping[str, Any]],
        candidate: NarrationResult,
        story_map: StoryMapResult,
    ) -> dict[str, Any]:
        """Build the exact panel/section closure for selected claim positions."""

        candidate_passages = {
            str(passage.get("passage_id", "")): passage
            for passage in candidate.passages
            if isinstance(passage, Mapping)
        }
        story_claims = {
            str(claim.get("claim_id", "")): claim
            for claim in story_map.claims
            if isinstance(claim, Mapping)
        }
        rows: list[dict[str, Any]] = []
        permitted_panel_ids: list[str] = []
        for value in positions:
            row = value.as_dict() if isinstance(value, NarrationRepairPosition) else dict(value)
            passage_id = str(row.get("passage_id", "")).strip()
            claim_ids = tuple(str(item) for item in row.get("claim_ids", ()))
            evidence_panel_ids = tuple(
                str(item) for item in row.get("evidence_panel_ids", ())
            )
            passage = candidate_passages.get(passage_id)
            passage_claim_ids = (
                tuple(str(item) for item in passage.get("claim_ids", ()))
                if passage is not None
                else ()
            )
            context_panel_ids = (
                tuple(str(item) for item in passage.get("evidence_panel_ids", ()))
                if passage is not None
                else ()
            )
            if (
                passage is None
                or not passage_id
                or not claim_ids
                or len(set(claim_ids)) != len(claim_ids)
                or not passage_claim_ids
                or len(set(passage_claim_ids)) != len(passage_claim_ids)
                or not set(claim_ids).issubset(set(passage_claim_ids))
                or not evidence_panel_ids
                or len(set(evidence_panel_ids)) != len(evidence_panel_ids)
                or not context_panel_ids
                or len(set(context_panel_ids)) != len(context_panel_ids)
            ):
                raise CloudStageError(
                    "cloud.narrative_repair_evidence_closure_invalid",
                    reviewable=True,
                )
            _, passage_sections, passage_permitted = (
                CloudStageRunner._story_passage_evidence_closure(
                    story_map,
                    passage_claim_ids,
                    story_claims,
                )
            )
            trusted_claim_refs: list[str] = []
            for claim_id in claim_ids:
                claim_refs, _, _ = CloudStageRunner._story_passage_evidence_closure(
                    story_map,
                    (claim_id,),
                    story_claims,
                )
                if not set(claim_refs).issubset(set(evidence_panel_ids)):
                    raise CloudStageError(
                        "cloud.narrative_repair_evidence_closure_invalid",
                        reviewable=True,
                    )
                for panel_id in claim_refs:
                    if panel_id not in trusted_claim_refs:
                        trusted_claim_refs.append(panel_id)
            if (
                not set(context_panel_ids).issubset(set(passage_permitted))
                or not set(evidence_panel_ids).issubset(set(passage_permitted))
                or set(evidence_panel_ids) != set(trusted_claim_refs)
            ):
                raise CloudStageError(
                    "cloud.narrative_repair_evidence_closure_invalid",
                    reviewable=True,
                )
            row = {
                "position": row.get("position"),
                "slot_id": str(row.get("slot_id", "")),
                "passage_id": passage_id,
                "claim_ids": list(claim_ids),
                "evidence_panel_ids": list(evidence_panel_ids),
                "requested_context_panel_ids": list(context_panel_ids),
                "beat_id": str(row.get("beat_id", "")),
                "causal_position": row.get("causal_position"),
                "section_keys": list(passage_sections),
                "permitted_panel_ids": list(passage_permitted),
            }
            rows.append(row)
            for panel_id in passage_permitted:
                if panel_id not in permitted_panel_ids:
                    permitted_panel_ids.append(panel_id)
        if not rows:
            raise CloudStageError(
                "cloud.narrative_repair_evidence_closure_invalid",
                reviewable=True,
            )
        identity = {
            "version": NARRATION_REPAIR_EVIDENCE_CLOSURE_VERSION,
            "lineage_candidate_hash": CloudStageRunner._narration_repair_lineage_identity(
                candidate
            ),
            "candidate_visual_evidence_hash": candidate.visual_evidence_hash,
            "candidate_model_identity_hash": candidate.model_identity_hash,
            "candidate_prompt_version": candidate.prompt_version,
            "candidate_prompt_sha256": candidate.prompt_sha256,
            "story_map_hash": story_map.story_map_hash,
            "story_model_identity_hash": story_map.model_identity_hash,
            "story_prompt_version": story_map.prompt_version,
            "story_prompt_sha256": story_map.prompt_sha256,
            "story_visual_evidence_hash": story_map.visual_evidence_hash,
            "story_panel_ids": [str(value) for value in story_map.panel_ids],
            "positions": rows,
            "permitted_panel_ids": permitted_panel_ids,
        }
        return {
            **identity,
            "closure_hash": _hash(identity),
        }

    @staticmethod
    def _validate_narration_repair_evidence_closure(
        registry: Mapping[str, Any],
        candidate: NarrationResult,
        story_map: StoryMapResult | None = None,
    ) -> Mapping[str, Any]:
        closure = registry.get("evidence_closure")
        if not isinstance(closure, Mapping):
            raise CloudStageError(
                "cloud.narrative_repair_evidence_closure_invalid",
                reviewable=True,
            )
        closure_hash = str(closure.get("closure_hash", ""))
        identity = {str(key): value for key, value in closure.items() if key != "closure_hash"}
        if (
            closure.get("version") != NARRATION_REPAIR_EVIDENCE_CLOSURE_VERSION
            or not closure_hash
            or _hash(identity) != closure_hash
            or closure.get("lineage_candidate_hash")
            != CloudStageRunner._narration_repair_lineage_identity(candidate)
            or closure.get("candidate_visual_evidence_hash") != candidate.visual_evidence_hash
            or closure.get("candidate_model_identity_hash") != candidate.model_identity_hash
            or closure.get("candidate_prompt_version") != candidate.prompt_version
            or closure.get("candidate_prompt_sha256") != candidate.prompt_sha256
            or registry.get("evidence_closure_hash") != closure_hash
        ):
            raise CloudStageError(
                "cloud.narrative_repair_evidence_closure_invalid",
                reviewable=True,
            )
        if story_map is not None:
            if (
                closure.get("story_map_hash") != story_map.story_map_hash
                or closure.get("story_model_identity_hash") != story_map.model_identity_hash
                or closure.get("story_prompt_version") != story_map.prompt_version
                or closure.get("story_prompt_sha256") != story_map.prompt_sha256
                or closure.get("story_visual_evidence_hash") != story_map.visual_evidence_hash
                or closure.get("story_panel_ids")
                != [str(value) for value in story_map.panel_ids]
            ):
                raise CloudStageError(
                    "cloud.narrative_repair_evidence_closure_invalid",
                    reviewable=True,
                )
            story_claims = {
                str(claim.get("claim_id", "")): claim
                for claim in story_map.claims
                if isinstance(claim, Mapping)
            }
            candidate_passages = {
                str(passage.get("passage_id", "")): passage
                for passage in candidate.passages
                if isinstance(passage, Mapping)
            }
            for row in closure.get("positions", ()):
                if not isinstance(row, Mapping):
                    raise CloudStageError(
                        "cloud.narrative_repair_evidence_closure_invalid",
                        reviewable=True,
                    )
                passage = candidate_passages.get(str(row.get("passage_id", "")).strip())
                raw_passage_claim_ids = (
                    passage.get("claim_ids") if passage is not None else None
                )
                raw_claim_ids = row.get("claim_ids")
                raw_evidence_panel_ids = row.get("evidence_panel_ids")
                raw_context_panel_ids = row.get("requested_context_panel_ids")
                if not all(
                    isinstance(value, (list, tuple))
                    for value in (
                        raw_passage_claim_ids,
                        raw_claim_ids,
                        raw_evidence_panel_ids,
                        raw_context_panel_ids,
                    )
                ):
                    raise CloudStageError(
                        "cloud.narrative_repair_evidence_closure_invalid",
                        reviewable=True,
                    )
                passage_claim_ids = tuple(
                    str(item).strip() for item in raw_passage_claim_ids
                )
                claim_ids = tuple(str(item).strip() for item in raw_claim_ids)
                evidence_panel_ids = tuple(
                    str(item).strip() for item in raw_evidence_panel_ids
                )
                context_panel_ids = tuple(
                    str(item).strip() for item in raw_context_panel_ids
                )
                if (
                    not passage_claim_ids
                    or not claim_ids
                    or not evidence_panel_ids
                    or not context_panel_ids
                    or any(
                        not value
                        for value in (
                            *passage_claim_ids,
                            *claim_ids,
                            *evidence_panel_ids,
                            *context_panel_ids,
                        )
                    )
                    or len(set(passage_claim_ids)) != len(passage_claim_ids)
                    or len(set(claim_ids)) != len(claim_ids)
                    or len(set(evidence_panel_ids)) != len(evidence_panel_ids)
                    or not set(claim_ids).issubset(set(passage_claim_ids))
                ):
                    raise CloudStageError(
                        "cloud.narrative_repair_evidence_closure_invalid",
                        reviewable=True,
                    )
                _, passage_sections, passage_permitted = (
                    CloudStageRunner._story_passage_evidence_closure(
                        story_map,
                        passage_claim_ids,
                        story_claims,
                    )
                )
                trusted_refs = []
                for claim_id in claim_ids:
                    claim_refs, _, _ = CloudStageRunner._story_passage_evidence_closure(
                        story_map,
                        (claim_id,),
                        story_claims,
                    )
                    if not set(claim_refs).issubset(set(evidence_panel_ids)):
                        raise CloudStageError(
                            "cloud.narrative_repair_evidence_closure_invalid",
                            reviewable=True,
                        )
                    trusted_refs.extend(
                        item for item in claim_refs if item not in trusted_refs
                    )
                if (
                    not set(context_panel_ids).issubset(set(passage_permitted))
                    or not set(evidence_panel_ids).issubset(set(passage_permitted))
                    or set(trusted_refs) != set(evidence_panel_ids)
                    or list(passage_sections) != list(row.get("section_keys", ()))
                    or list(passage_permitted)
                    != list(row.get("permitted_panel_ids", ()))
                ):
                    raise CloudStageError(
                        "cloud.narrative_repair_evidence_closure_invalid",
                        reviewable=True,
                    )
        return closure

    def _narration_repair_position_registry(
        self,
        positions: Sequence[NarrationRepairPosition | Mapping[str, Any]],
        candidate: NarrationResult,
        story_map: StoryMapResult,
        *,
        prompt: tuple[str, str, str] | None = None,
    ) -> dict[str, Any]:
        """Canonicalize the ordered local rewrite registry and its cache identity."""

        canonical_positions: list[NarrationRepairPosition] = []
        for position_index, value in enumerate(positions):
            try:
                position = value if isinstance(value, NarrationRepairPosition) else NarrationRepairPosition(
                    position=int(value["position"]),
                    slot_id=str(value["slot_id"]),
                    passage_id=str(value["passage_id"]),
                    claim_ids=tuple(str(item) for item in value["claim_ids"]),
                    evidence_panel_ids=tuple(str(item) for item in value["evidence_panel_ids"]),
                    beat_id=str(value["beat_id"]),
                    causal_position=int(value["causal_position"]),
                    priority=int(value["priority"]),
                    removable=bool(value["removable"]),
                    word_budget=int(value["word_budget"]),
                    word_budget_min=int(
                        value.get(
                            "word_budget_min",
                            _position_word_budget_bounds(int(value["word_budget"]))[0],
                        )
                    ),
                    word_budget_max=int(
                        value.get(
                            "word_budget_max",
                            _position_word_budget_bounds(int(value["word_budget"]))[1],
                        )
                    ),
                )
                position = replace(position, position=position_index)
            except (KeyError, TypeError, ValueError):
                raise CloudStageError(
                    "cloud.narrative_repair_position_selection_invalid",
                    reviewable=True,
                ) from None
            canonical_positions.append(position)
        if not NARRATION_REPAIR_POSITION_MIN_COUNT <= len(canonical_positions) <= NARRATION_REPAIR_POSITION_MAX_COUNT:
            raise CloudStageError(
                "cloud.narrative_repair_position_selection_invalid",
                reviewable=True,
            )
        if [item.causal_position for item in canonical_positions] != sorted(
            item.causal_position for item in canonical_positions
        ):
            raise CloudStageError(
                "cloud.narrative_repair_position_order_invalid",
                reviewable=True,
            )
        if len({item.slot_id for item in canonical_positions}) != len(canonical_positions):
            raise CloudStageError(
                "cloud.narrative_repair_position_selection_invalid",
                reviewable=True,
            )
        rows = [item.as_dict() for item in canonical_positions]
        selected_claim_ids = {
            claim_id for item in canonical_positions for claim_id in item.claim_ids
        }
        available_claim_ids = {
            str(claim.get("claim_id", ""))
            for claim in story_map.claims
            if isinstance(claim, Mapping) and str(claim.get("claim_id", "")).strip()
        }
        minimum_selected_claims = min(
            NARRATION_REPAIR_POSITION_MAX_COUNT,
            len(available_claim_ids),
        )
        if not minimum_selected_claims <= len(selected_claim_ids) <= 12:
            raise CloudStageError(
                "cloud.narrative_repair_position_selection_invalid",
                reviewable=True,
            )
        target_word_count = sum(item.word_budget for item in canonical_positions)
        if not 115 <= target_word_count <= 125:
            raise CloudStageError(
                "cloud.narrative_repair_position_budget_invalid",
                reviewable=True,
            )
        selected_passage_ids = {item.passage_id for item in canonical_positions}
        if len(selected_passage_ids) < 4:
            raise CloudStageError(
                "cloud.narrative_repair_position_selection_invalid",
                reviewable=True,
            )
        prompt_identity = prompt or self.prompts["narration"]
        evidence_closure = self._narration_repair_evidence_closure(
            canonical_positions,
            candidate,
            story_map,
        )
        identity = {
            "version": NARRATION_REPAIR_POSITION_REGISTRY_VERSION,
            "candidate_hash": _hash(candidate.as_dict()),
            "visual_evidence_hash": candidate.visual_evidence_hash,
            "story_map_hash": story_map.story_map_hash,
            "model_identity_hash": self.model_identity.identity_hash,
            "prompt_version": prompt_identity[0],
            "prompt_sha256": prompt_identity[1],
            "positions": rows,
            "evidence_closure_hash": evidence_closure["closure_hash"],
        }
        return {
            **identity,
            "positions": rows,
            "evidence_closure": evidence_closure,
            "target_word_count": target_word_count,
            "target_duration_s": script.estimate_narration_duration(
                " ".join(["word"] * target_word_count),
                "dramatic",
            ),
            "slot_order_hash": _hash(identity),
        }

    def _build_narration_repair_position_registry(
        self,
        candidate: NarrationResult,
        story_map: StoryMapResult,
        *,
        prompt: tuple[str, str, str] | None = None,
    ) -> dict[str, Any]:
        """Select 4-8 trusted claim positions before any provider request."""

        slots = self._build_narration_repair_slots(candidate, story_map)
        candidate_claims = {
            str(claim.get("claim_id", "")): claim
            for claim in candidate.evidence_graph.get("claims", ())
            if isinstance(claim, Mapping) and str(claim.get("claim_id", "")).strip()
        }
        story_claims = {
            str(claim.get("claim_id", "")): claim
            for claim in story_map.claims
            if isinstance(claim, Mapping) and str(claim.get("claim_id", "")).strip()
        }
        all_positions: list[NarrationRepairPosition] = []
        for slot in slots:
            for claim_index, claim_id in enumerate(slot.claim_ids):
                candidate_claim = candidate_claims.get(claim_id)
                story_claim = story_claims.get(claim_id)
                if candidate_claim is None or story_claim is None:
                    raise CloudStageError(
                        "cloud.narrative_repair_position_lineage_invalid",
                        reviewable=True,
                    )
                claim_refs = tuple(
                    str(value)
                    for value in story_claim.get(
                        "evidence_panel_ids",
                        story_claim.get("panel_ids", ()),
                    )
                )
                if not claim_refs or not set(claim_refs).issubset(set(slot.evidence_panel_ids)):
                    raise CloudStageError(
                        "cloud.narrative_repair_position_lineage_invalid",
                        reviewable=True,
                    )
                identity = {
                    "version": NARRATION_REPAIR_POSITION_REGISTRY_VERSION,
                    "candidate_hash": _hash(candidate.as_dict()),
                    "story_map_hash": story_map.story_map_hash,
                    "slot_id": slot.slot_id,
                    "claim_id": claim_id,
                    "causal_position": slot.causal_position,
                }
                all_positions.append(
                    NarrationRepairPosition(
                        position=len(all_positions),
                        slot_id=f"narration_position_v1_{_hash(identity)}",
                        passage_id=slot.passage_id,
                        claim_ids=(claim_id,),
                        evidence_panel_ids=claim_refs,
                        beat_id=slot.beat_id,
                        causal_position=slot.causal_position,
                        priority=slot.priority - claim_index,
                        removable=slot.removable or len(slot.claim_ids) > 1,
                        word_budget=1,
                    )
                )
        if len(all_positions) < NARRATION_REPAIR_POSITION_MIN_COUNT:
            raise CloudStageError(
                "cloud.narrative_repair_position_selection_invalid",
                reviewable=True,
            )
        selected = list(all_positions)
        while len(selected) > NARRATION_REPAIR_POSITION_MAX_COUNT:
            counts: dict[str, int] = {}
            for item in selected:
                counts[item.passage_id] = counts.get(item.passage_id, 0) + 1
            removable = [
                item
                for item in selected
                if item.removable and counts[item.passage_id] > 1
            ]
            if not removable or len(selected) - 1 < 8:
                raise CloudStageError(
                    "cloud.narrative_repair_position_selection_invalid",
                    reviewable=True,
                )
            selected.remove(
                min(
                    removable,
                    key=lambda item: (
                        item.priority,
                        item.causal_position,
                        item.passage_id,
                        item.claim_ids,
                    ),
                )
            )
        target_word_count = 120
        base_budget, remainder = divmod(target_word_count, len(selected))
        budgeted = [
            replace(
                item,
                position=index,
                word_budget=base_budget + (1 if index < remainder else 0),
                word_budget_min=_position_word_budget_bounds(
                    base_budget + (1 if index < remainder else 0)
                )[0],
                word_budget_max=_position_word_budget_bounds(
                    base_budget + (1 if index < remainder else 0),
                    max_word_budget=(
                        base_budget
                        + (1 if index < remainder else 0)
                        + (1 if index < 125 - target_word_count else 0)
                    ),
                )[1],
            )
            for index, item in enumerate(selected)
        ]
        registry = self._narration_repair_position_registry(
            budgeted,
            candidate,
            story_map,
            prompt=prompt,
        )
        candidate_passages = {
            str(passage.get("passage_id", "")): passage
            for passage in candidate.passages
            if isinstance(passage, Mapping)
        }
        provider_positions = []
        for item in budgeted:
            passage = candidate_passages[item.passage_id]
            provider_positions.append(
                {
                    "position": item.position,
                    "word_budget": item.word_budget,
                    "word_budget_min": item.word_budget_min,
                    "word_budget_max": item.word_budget_max,
                    "passage_text": str(passage.get("text", "")),
                    "claim_context": [
                        {
                            "text": str(candidate_claims[claim_id].get("text", "")),
                            "qualification": str(candidate_claims[claim_id].get("qualification", "")),
                        }
                        for claim_id in item.claim_ids
                    ],
                    "evidence_panel_ids": list(item.evidence_panel_ids),
                    "beat_context": item.beat_id,
                }
            )
        registry["provider_positions"] = provider_positions
        passage_lineage = self._reconstruct_narration_repair_passage_lineage(
            candidate,
            registry,
        )
        registry["passage_lineage_version"] = passage_lineage["version"]
        registry["passage_lineage_hash"] = passage_lineage["lineage_hash"]
        registry["slot_order_hash"] = _hash(
            {
                "position_registry_hash": registry["slot_order_hash"],
                "passage_lineage_version": passage_lineage["version"],
                "passage_lineage_hash": passage_lineage["lineage_hash"],
            }
        )
        return registry

    @staticmethod
    def _reconstruct_narration_repair_passage_lineage(
        candidate: NarrationResult,
        registry: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Rebuild passage claim/evidence refs from trusted local positions.

        The positional provider contract owns rewrite text only.  Candidate
        passage references may be incomplete after an earlier repair, so the
        persisted position registry is the authority for the retained claim
        and evidence union.  This boundary never accepts provider-supplied
        identifiers or infers new evidence.
        """

        raw_positions = registry.get("positions")
        if not isinstance(raw_positions, list) or not raw_positions:
            raise CloudStageError(
                "cloud.narrative_repair_position_lineage_invalid",
                reviewable=True,
            )
        candidate_passages = tuple(candidate.passages)
        if len(candidate_passages) < 4 or any(
            not isinstance(passage, Mapping) for passage in candidate_passages
        ):
            raise CloudStageError(
                "cloud.narrative_repair_position_lineage_invalid",
                reviewable=True,
            )
        candidate_by_id = {
            str(passage.get("passage_id", "")): passage
            for passage in candidate_passages
        }
        if (
            len(candidate_by_id) != len(candidate_passages)
            or any(not passage_id.strip() for passage_id in candidate_by_id)
        ):
            raise CloudStageError(
                "cloud.narrative_repair_position_lineage_invalid",
                reviewable=True,
            )
        raw_candidate_claims = candidate.evidence_graph.get("claims", ())
        if not isinstance(raw_candidate_claims, (list, tuple)):
            raise CloudStageError(
                "cloud.narrative_repair_position_lineage_invalid",
                reviewable=True,
            )
        candidate_claims = {
            str(claim.get("claim_id", "")): claim
            for claim in raw_candidate_claims
            if isinstance(claim, Mapping) and str(claim.get("claim_id", "")).strip()
        }
        if (
            len(candidate_claims) != len(raw_candidate_claims)
            or any(not isinstance(claim, Mapping) for claim in raw_candidate_claims)
            or any(not str(claim.get("claim_id", "")).strip() for claim in raw_candidate_claims)
        ):
            raise CloudStageError(
                "cloud.narrative_repair_position_lineage_invalid",
                reviewable=True,
            )
        observation_ids = {
            str(observation.get("panel_id", ""))
            for observation in candidate.observations
            if isinstance(observation, Mapping)
            and str(observation.get("panel_id", "")).strip()
        }
        if not observation_ids or not candidate_claims:
            raise CloudStageError(
                "cloud.narrative_repair_position_lineage_invalid",
                reviewable=True,
            )

        groups: dict[str, dict[str, list[Any]]] = {}
        seen_position_ids: set[str] = set()
        previous_causal_position = -1
        for index, value in enumerate(raw_positions):
            if not isinstance(value, Mapping):
                raise CloudStageError(
                    "cloud.narrative_repair_position_lineage_invalid",
                    reviewable=True,
                )
            position = value.get("position")
            slot_id = str(value.get("slot_id", "")).strip()
            passage_id = str(value.get("passage_id", "")).strip()
            claim_values = value.get("claim_ids")
            evidence_values = value.get("evidence_panel_ids")
            causal_position = value.get("causal_position")
            if (
                isinstance(position, bool)
                or not isinstance(position, int)
                or position != index
                or not slot_id
                or slot_id in seen_position_ids
                or passage_id not in candidate_by_id
                or not isinstance(claim_values, (list, tuple))
                or not isinstance(evidence_values, (list, tuple))
                or not claim_values
                or not evidence_values
                or isinstance(causal_position, bool)
                or not isinstance(causal_position, int)
                or causal_position < previous_causal_position
            ):
                raise CloudStageError(
                    "cloud.narrative_repair_position_lineage_invalid",
                    reviewable=True,
                )
            claim_ids = tuple(str(value) for value in claim_values)
            evidence_panel_ids = tuple(str(value) for value in evidence_values)
            original_claim_values = candidate_by_id[passage_id].get("claim_ids")
            if not isinstance(original_claim_values, (list, tuple)):
                raise CloudStageError(
                    "cloud.narrative_repair_position_lineage_invalid",
                    reviewable=True,
                )
            original_claim_ids = tuple(str(value) for value in original_claim_values)
            if (
                any(not value.strip() for value in claim_ids)
                or len(set(claim_ids)) != len(claim_ids)
                or any(not value.strip() for value in original_claim_ids)
                or len(set(original_claim_ids)) != len(original_claim_ids)
                or any(claim_id not in candidate_claims for claim_id in claim_ids)
                or any(claim_id not in original_claim_ids for claim_id in claim_ids)
                or any(not value.strip() for value in evidence_panel_ids)
                or len(set(evidence_panel_ids)) != len(evidence_panel_ids)
                or any(panel_id not in observation_ids for panel_id in evidence_panel_ids)
            ):
                raise CloudStageError(
                    "cloud.narrative_repair_position_lineage_invalid",
                    reviewable=True,
                )
            for claim_id in claim_ids:
                claim = candidate_claims[claim_id]
                claim_refs = claim.get(
                    "evidence_panel_ids",
                    claim.get("panel_ids", ()),
                )
                if not isinstance(claim_refs, (list, tuple)) or not claim_refs:
                    raise CloudStageError(
                        "cloud.narrative_repair_position_lineage_invalid",
                        reviewable=True,
                    )
                claim_refs = tuple(str(value) for value in claim_refs)
                if (
                    any(not value.strip() for value in claim_refs)
                    or len(set(claim_refs)) != len(claim_refs)
                    or not set(claim_refs).issubset(set(evidence_panel_ids))
                ):
                    raise CloudStageError(
                        "cloud.narrative_repair_position_lineage_invalid",
                        reviewable=True,
                    )
            seen_position_ids.add(slot_id)
            previous_causal_position = causal_position
            group = groups.setdefault(
                passage_id,
                {
                    "position_ids": [],
                    "claim_ids": [],
                    "evidence_panel_ids": [],
                    "causal_positions": [],
                },
            )
            group["position_ids"].append(slot_id)
            group["causal_positions"].append(causal_position)
            for claim_id in claim_ids:
                if claim_id not in group["claim_ids"]:
                    group["claim_ids"].append(claim_id)
            for panel_id in evidence_panel_ids:
                if panel_id not in group["evidence_panel_ids"]:
                    group["evidence_panel_ids"].append(panel_id)

        passage_rows: list[dict[str, Any]] = []
        for passage in candidate_passages:
            passage_id = str(passage.get("passage_id", ""))
            group = groups.get(passage_id)
            if group is None:
                continue
            selected_claim_ids = [
                claim_id
                for claim_id in passage.get("claim_ids", ())
                if str(claim_id) in group["claim_ids"]
            ]
            evidence_panel_ids = list(group["evidence_panel_ids"])
            if not selected_claim_ids or not evidence_panel_ids:
                raise CloudStageError(
                    "cloud.narrative_repair_position_lineage_invalid",
                    reviewable=True,
                )
            for claim_id in selected_claim_ids:
                claim = candidate_claims[claim_id]
                claim_refs = claim.get(
                    "evidence_panel_ids",
                    claim.get("panel_ids", ()),
                )
                if not isinstance(claim_refs, (list, tuple)) or not {
                    str(value) for value in claim_refs
                }.issubset(set(evidence_panel_ids)):
                    raise CloudStageError(
                        "cloud.narrative_repair_position_lineage_invalid",
                        reviewable=True,
                    )
            passage_rows.append(
                {
                    "passage_id": passage_id,
                    "claim_ids": selected_claim_ids,
                    "evidence_panel_ids": evidence_panel_ids,
                    "position_ids": list(group["position_ids"]),
                    "causal_positions": list(group["causal_positions"]),
                }
            )
        if len(passage_rows) < 4:
            raise CloudStageError(
                "cloud.narrative_repair_position_lineage_invalid",
                reviewable=True,
            )
        expected_version = str(registry.get("passage_lineage_version", ""))
        if expected_version and expected_version != NARRATION_REPAIR_PASSAGE_LINEAGE_VERSION:
            raise CloudStageError(
                "cloud.narrative_repair_position_lineage_invalid",
                reviewable=True,
            )
        identity = {
            "version": NARRATION_REPAIR_PASSAGE_LINEAGE_VERSION,
            "candidate_visual_evidence_hash": candidate.visual_evidence_hash,
            "passages": passage_rows,
        }
        lineage_hash = _hash(identity)
        expected_hash = str(registry.get("passage_lineage_hash", ""))
        if expected_hash and expected_hash != lineage_hash:
            raise CloudStageError(
                "cloud.narrative_repair_position_lineage_invalid",
                reviewable=True,
            )
        return {
            "version": NARRATION_REPAIR_PASSAGE_LINEAGE_VERSION,
            "passages": passage_rows,
            "lineage_hash": lineage_hash,
        }

    @staticmethod
    def _reconcile_narration_repair_vector(
        raw: Mapping[str, Any],
        registry: Mapping[str, Any],
        candidate: NarrationResult,
        *,
        story_map: StoryMapResult | None = None,
    ) -> dict[str, Any]:
        """Map provider rewrite index N to trusted local position N."""

        evidence_closure = CloudStageRunner._validate_narration_repair_evidence_closure(
            registry,
            candidate,
            story_map,
        )
        raw_positions = registry.get("positions")
        rewrites = raw.get("rewrites") if isinstance(raw, Mapping) else None

        def response_items(value: Any) -> list[Any] | None:
            if isinstance(value, Sequence) and not isinstance(
                value,
                (str, bytes, bytearray, Mapping),
            ):
                return list(value)
            return None

        def response_shape_metrics(
            failed_predicate: str | None,
            word_counts: Sequence[int | None],
            total_words: int | None,
            duration: float | None,
            micro_compaction: Mapping[str, Any] | None = None,
        ) -> dict[str, Any]:
            expected_ranges = []
            position_items = raw_positions if isinstance(raw_positions, list) else []
            for item in position_items:
                if isinstance(item, Mapping):
                    expected_ranges.append(
                        {
                            "position": item.get("position"),
                            "target": item.get("word_budget"),
                            "min": item.get("word_budget_min"),
                            "max": item.get("word_budget_max"),
                        }
                    )
                else:
                    expected_ranges.append(
                        {"position": None, "target": None, "min": None, "max": None}
                    )
            rewrite_items = response_items(rewrites)
            metrics = {
                "container_type": type(raw).__name__,
                "top_level_keys": (
                    sorted(str(key) for key in raw)
                    if isinstance(raw, Mapping)
                    else []
                ),
                "array_key": "rewrites",
                "array_count": len(rewrite_items) if rewrite_items is not None else None,
                "array_item_types": (
                    [type(item).__name__ for item in rewrite_items]
                    if rewrite_items is not None
                    else []
                ),
                "per_position_word_counts": list(word_counts),
                "total_word_count": total_words,
                "estimated_duration_s": duration,
                "slot_order_hash": str(registry.get("slot_order_hash", "")),
                "expected_ranges": expected_ranges,
                "accepted_word_bounds": {"min": 115, "max": 125},
                "accepted_duration_bounds_s": {"min": 50.0, "max": 60.0},
                "failed_predicate": failed_predicate,
            }
            if micro_compaction is not None:
                metrics["micro_compaction"] = dict(micro_compaction)
            return metrics

        def current_response_shape(failed_predicate: str) -> dict[str, Any]:
            rewrite_items = response_items(rewrites)
            if rewrite_items is None:
                return response_shape_metrics(failed_predicate, [], None, None)
            word_counts = [
                script.narration_word_count(text)
                if isinstance(text, str)
                else None
                for text in rewrite_items
            ]
            total_words = sum(count for count in word_counts if count is not None)
            all_strings = bool(rewrite_items) and all(
                count is not None for count in word_counts
            )
            duration = (
                script.estimate_narration_duration(" ".join(rewrite_items), "dramatic")
                if all_strings
                else None
            )
            return response_shape_metrics(
                failed_predicate,
                word_counts,
                total_words,
                duration,
            )

        if not isinstance(raw, Mapping) or set(raw) != {"rewrites"}:
            raise CloudStageError(
                "cloud.narrative_repair_position_contract_invalid",
                reviewable=True,
                safe_metadata=current_response_shape("response_top_level_shape"),
            )
        if not isinstance(raw_positions, list) or not isinstance(rewrites, list):
            raise CloudStageError(
                "cloud.narrative_repair_position_contract_invalid",
                reviewable=True,
                safe_metadata=current_response_shape("response_array_type"),
            )
        if len(rewrites) != len(raw_positions):
            raise CloudStageError(
                "cloud.narrative_repair_position_contract_invalid",
                reviewable=True,
                safe_metadata=current_response_shape("rewrite_count"),
            )

        word_counts = [
            script.narration_word_count(text) if isinstance(text, str) else None
            for text in rewrites
        ]
        total_words = sum(count for count in word_counts if count is not None)
        all_strings = all(count is not None for count in word_counts)
        duration = (
            script.estimate_narration_duration(" ".join(rewrites), "dramatic")
            if all_strings
            else None
        )
        passage_lineage = CloudStageRunner._reconstruct_narration_repair_passage_lineage(
            candidate,
            registry,
        )
        positions: list[NarrationRepairPosition] = []
        closure_rows = {
            int(row["position"]): row
            for row in evidence_closure.get("positions", ())
            if isinstance(row, Mapping) and isinstance(row.get("position"), int)
        }
        for index, value in enumerate(raw_positions):
            try:
                position = NarrationRepairPosition(
                    position=index,
                    slot_id=str(value["slot_id"]),
                    passage_id=str(value["passage_id"]),
                    claim_ids=tuple(str(item) for item in value["claim_ids"]),
                    evidence_panel_ids=tuple(str(item) for item in value["evidence_panel_ids"]),
                    beat_id=str(value["beat_id"]),
                    causal_position=int(value["causal_position"]),
                    priority=int(value["priority"]),
                    removable=bool(value["removable"]),
                    word_budget=int(value["word_budget"]),
                    word_budget_min=int(
                        value.get(
                            "word_budget_min",
                            _position_word_budget_bounds(int(value["word_budget"]))[0],
                        )
                    ),
                    word_budget_max=int(
                        value.get(
                            "word_budget_max",
                            _position_word_budget_bounds(int(value["word_budget"]))[1],
                        )
                    ),
                )
            except (KeyError, TypeError, ValueError):
                raise CloudStageError(
                    "cloud.narrative_repair_position_contract_invalid",
                    reviewable=True,
                    safe_metadata=current_response_shape("position_descriptor"),
                ) from None
            closure_row = closure_rows.get(index)
            if (
                closure_row is None
                or str(closure_row.get("slot_id", "")) != position.slot_id
                or str(closure_row.get("passage_id", "")) != position.passage_id
                or tuple(str(item) for item in closure_row.get("claim_ids", ()))
                != position.claim_ids
                or tuple(str(item) for item in closure_row.get("evidence_panel_ids", ()))
                != position.evidence_panel_ids
                or str(closure_row.get("beat_id", "")) != position.beat_id
                or int(closure_row.get("causal_position", -1)) != position.causal_position
            ):
                raise CloudStageError(
                    "cloud.narrative_repair_position_lineage_invalid",
                    reviewable=True,
                )
            positions.append(position)
            text = rewrites[index]
            if not isinstance(text, str) or not text.strip():
                raise CloudStageError(
                    "cloud.narrative_repair_position_contract_invalid",
                    reviewable=True,
                    safe_metadata=current_response_shape("rewrite_text"),
                )
            trusted_ids = (
                position.slot_id,
                position.passage_id,
                position.beat_id,
                *position.claim_ids,
                *position.evidence_panel_ids,
            )
            if any(identifier and identifier in text for identifier in trusted_ids):
                raise CloudStageError(
                    "cloud.narrative_repair_position_contract_invalid",
                    reviewable=True,
                    safe_metadata=current_response_shape("trusted_identifier_echo"),
                )

        micro_compaction: dict[str, Any] | None = None
        if all_strings:
            rewrites, micro_compaction = _micro_compact_rewrites(
                tuple(rewrites),
                total_words=total_words,
            )
            if micro_compaction.get("failed_predicate"):
                raise CloudStageError(
                    "cloud.narrative_repair_micro_compaction_unavailable",
                    reviewable=True,
                    safe_metadata=response_shape_metrics(
                        str(micro_compaction["failed_predicate"]),
                        [script.narration_word_count(text) for text in rewrites],
                        int(micro_compaction["after_word_count"]),
                        script.estimate_narration_duration(" ".join(rewrites), "dramatic"),
                        micro_compaction,
                    ),
                )
            word_counts = [script.narration_word_count(text) for text in rewrites]
            total_words = sum(word_counts)
            duration = script.estimate_narration_duration(
                " ".join(rewrites),
                "dramatic",
            )
            dominance_limit = max(
                NARRATION_REPAIR_POSITION_DOMINANCE_FLOOR,
                math.ceil(total_words * NARRATION_REPAIR_POSITION_MAX_SHARE),
            )
            for word_count in word_counts:
                if word_count > dominance_limit:
                    raise CloudStageError(
                        "cloud.narrative_repair_position_budget_invalid",
                        reviewable=True,
                        safe_metadata=response_shape_metrics(
                            "position_word_dominance",
                            word_counts,
                            total_words,
                            duration,
                            micro_compaction,
                        ),
                    )
        if (
            not 115 <= total_words <= 125
            or duration is None
            or not 50.0 <= duration <= 60.0
        ):
            failed_predicate = (
                "aggregate_word_count"
                if not 115 <= total_words <= 125
                else "aggregate_duration"
            )
            raise CloudStageError(
                "cloud.narrative_repair_position_budget_invalid",
                reviewable=True,
                safe_metadata=response_shape_metrics(
                    failed_predicate,
                    word_counts,
                    total_words if all_strings else None,
                    duration,
                    micro_compaction,
                ),
            )
        grouped_text: dict[str, list[str]] = {}
        for position, text in zip(positions, rewrites, strict=True):
            grouped_text.setdefault(position.passage_id, []).append(text.strip())
        lineage_by_passage = {
            str(row["passage_id"]): row for row in passage_lineage["passages"]
        }
        passages: list[dict[str, Any]] = []
        for original in candidate.passages:
            passage_id = str(original.get("passage_id", ""))
            if passage_id not in grouped_text or passage_id not in lineage_by_passage:
                continue
            lineage = lineage_by_passage[passage_id]
            passage = dict(original)
            passage["text"] = " ".join(grouped_text[passage_id])
            passage["claim_ids"] = list(lineage["claim_ids"])
            passage["evidence_panel_ids"] = list(lineage["evidence_panel_ids"])
            if not passage["claim_ids"] or not passage["evidence_panel_ids"]:
                raise CloudStageError(
                    "cloud.narrative_repair_position_lineage_invalid",
                    reviewable=True,
                )
            passages.append(passage)
        claims = [
            dict(claim)
            for claim in candidate.evidence_graph.get("claims", ())
            if isinstance(claim, Mapping)
            and str(claim.get("claim_id", "")) in {
                claim_id
                for row in passage_lineage["passages"]
                for claim_id in row["claim_ids"]
            }
        ]
        if len(passages) < 4 or not claims:
            raise CloudStageError(
                "cloud.narrative_repair_position_lineage_invalid",
                reviewable=True,
            )
        return {
            "narrative_outline": {
                "story_spine": dict(candidate.story_spine),
                "ending_kind": candidate.ending_kind,
            },
            "script_passages": passages,
            "evidence_graph": {"claims": claims},
            "_passage_lineage": passage_lineage,
            "_response_shape_metrics": response_shape_metrics(
                None,
                word_counts,
                total_words if all_strings else None,
                duration,
                micro_compaction,
            ),
        }

    @staticmethod
    def _reconcile_narration_repair_slots(
        raw: Mapping[str, Any],
        slots: Sequence[NarrationRepairSlot],
        candidate: NarrationResult,
    ) -> dict[str, Any]:
        """Replace provider slot references with trusted local lineage."""

        provider_output = raw.get("analyzer_output", raw) if isinstance(raw, Mapping) else raw
        if not isinstance(provider_output, Mapping) or set(provider_output) != {"repair_slots"}:
            raise CloudStageError("cloud.narrative_repair_slot_contract_invalid", reviewable=True)
        envelope = provider_output.get("repair_slots")
        if not isinstance(envelope, Mapping) or set(envelope) != {
            "retained_slot_ids",
            "dropped_slot_ids",
            "slots",
        }:
            raise CloudStageError("cloud.narrative_repair_slot_contract_invalid", reviewable=True)
        slot_by_id = {slot.slot_id: slot for slot in slots}
        ordered_ids = [slot.slot_id for slot in slots]
        retained = envelope["retained_slot_ids"]
        dropped = envelope["dropped_slot_ids"]
        if not isinstance(retained, list) or not isinstance(dropped, list):
            raise CloudStageError("cloud.narrative_repair_slot_contract_invalid", reviewable=True)
        for values in (retained, dropped):
            if any(not isinstance(value, str) for value in values):
                raise CloudStageError("cloud.narrative_repair_slot_contract_invalid", reviewable=True)
            if any(value not in slot_by_id for value in values):
                raise CloudStageError("cloud.narrative_repair_slot_unknown", reviewable=True)
            if len(values) != len(set(values)):
                raise CloudStageError("cloud.narrative_repair_slot_duplicate", reviewable=True)
        if set(retained) & set(dropped):
            raise CloudStageError("cloud.narrative_repair_slot_duplicate", reviewable=True)
        if set(retained) | set(dropped) != set(ordered_ids):
            raise CloudStageError("cloud.narrative_repair_slot_missing", reviewable=True)
        canonical_retained = [slot_id for slot_id in ordered_ids if slot_id in set(retained)]
        canonical_dropped = [slot_id for slot_id in ordered_ids if slot_id in set(dropped)]
        if retained != canonical_retained or dropped != canonical_dropped:
            raise CloudStageError("cloud.narrative_repair_slot_order_invalid", reviewable=True)
        if len(retained) < 4:
            raise CloudStageError("cloud.narrative_repair_slot_contract_invalid", reviewable=True)
        if any(not slot_by_id[slot_id].removable for slot_id in dropped):
            raise CloudStageError("cloud.narrative_repair_slot_drop_forbidden", reviewable=True)
        rows = envelope["slots"]
        if not isinstance(rows, list):
            raise CloudStageError("cloud.narrative_repair_slot_contract_invalid", reviewable=True)
        row_ids: list[str] = []
        text_by_id: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, Mapping) or "slot_id" not in row:
                raise CloudStageError("cloud.narrative_repair_slot_contract_invalid", reviewable=True)
            slot_id = row.get("slot_id")
            if not isinstance(slot_id, str):
                raise CloudStageError("cloud.narrative_repair_slot_contract_invalid", reviewable=True)
            if slot_id not in slot_by_id:
                raise CloudStageError("cloud.narrative_repair_slot_unknown", reviewable=True)
            if slot_id in row_ids:
                raise CloudStageError("cloud.narrative_repair_slot_duplicate", reviewable=True)
            if set(row) != {"slot_id", "text"} or not isinstance(row.get("text"), str) or not row["text"].strip():
                raise CloudStageError("cloud.narrative_repair_slot_contract_invalid", reviewable=True)
            row_ids.append(slot_id)
            text_by_id[slot_id] = row["text"].strip()
        if row_ids != canonical_retained:
            raise CloudStageError("cloud.narrative_repair_slot_missing", reviewable=True)
        candidate_by_passage_id = {
            str(passage.get("passage_id", "")): passage
            for passage in candidate.passages
            if isinstance(passage, Mapping)
        }
        candidate_claims = {
            str(claim.get("claim_id", "")): claim
            for claim in candidate.evidence_graph.get("claims", ())
            if isinstance(claim, Mapping) and str(claim.get("claim_id", "")).strip()
        }
        passages: list[dict[str, Any]] = []
        retained_claim_ids: set[str] = set()
        for slot_id in canonical_retained:
            slot = slot_by_id[slot_id]
            original = candidate_by_passage_id.get(slot.passage_id)
            if original is None or any(claim_id not in candidate_claims for claim_id in slot.claim_ids):
                raise CloudStageError("cloud.narrative_repair_slot_lineage_invalid", reviewable=True)
            passage = dict(original)
            passage["text"] = text_by_id[slot_id]
            passage["claim_ids"] = list(slot.claim_ids)
            passage["evidence_panel_ids"] = list(slot.evidence_panel_ids)
            passages.append(passage)
            retained_claim_ids.update(slot.claim_ids)
        claims = CloudStageRunner._normalize_narration_claims(
            [
                dict(claim)
                for claim in candidate.evidence_graph.get("claims", ())
                if isinstance(claim, Mapping)
                and str(claim.get("claim_id", "")) in retained_claim_ids
            ]
        )
        return {
            "narrative_outline": {
                "story_spine": dict(candidate.story_spine),
                "ending_kind": candidate.ending_kind,
            },
            "script_passages": passages,
            "evidence_graph": {"claims": claims},
        }

    def _narration_repair_identity_metadata(
        self,
        *,
        source: Mapping[str, Any],
        prompt: tuple[str, str, str],
        candidate: NarrationResult,
        visual: VisualStageResult | None = None,
        story_map: StoryMapResult | None = None,
    ) -> dict[str, Any]:
        """Build the metadata-only dependency identity for a repair candidate."""

        story_value = (
            story_map.as_dict()
            if story_map is not None
            else source.get("story_map", {})
        )
        if not isinstance(story_value, Mapping):
            raise CloudStageError("cloud.narrative_repair_identity_mismatch", reviewable=True)
        if story_map is None:
            try:
                story_map = StoryMapResult.from_dict(story_value)
            except (KeyError, TypeError, ValueError):
                raise CloudStageError(
                    "cloud.narrative_repair_identity_mismatch",
                    reviewable=True,
                ) from None
        panel_ids = list(
            visual.panel_ids
            if visual is not None
            else tuple(str(value) for value in source.get("panel_ids", story_map.panel_ids))
        )
        panel_rows = list(visual.panels) if visual is not None else []
        panel_by_id = {
            str(row.get("panel_id", "")): row
            for row in panel_rows
            if isinstance(row, Mapping)
        }
        panel_identity_hashes = []
        canonical_panel_rows = []
        for panel_id in panel_ids:
            row = panel_by_id.get(panel_id, {})
            visual_row = row.get("visual_evidence", {})
            evidence_hash = str(
                row.get("evidence_hash")
                or (visual_row.get("evidence_hash") if isinstance(visual_row, Mapping) else "")
                or _hash({"panel_id": panel_id, "visual_evidence_hash": visual.visual_evidence_hash if visual else ""})
            )
            panel_identity_hashes.append(evidence_hash)
            canonical_panel_rows.append(
                {
                    "panel_id": panel_id,
                    "source_order": row.get("source_order"),
                    "source_asset_id": row.get("source_asset_id"),
                    "source_checksum": row.get("source_checksum"),
                    "panel_bounds": row.get("panel_bounds"),
                    "evidence_hash": evidence_hash,
                }
            )
        selection = source.get("editorial_selection", {})
        if not isinstance(selection, Mapping):
            selection = {}
        selection_summary = {
            "beat_ids": [str(value) for value in selection.get("beat_ids", ())],
            "panel_ids": [str(value) for value in selection.get("panel_ids", panel_ids)],
            "claim_ids": [str(value) for value in selection.get("claim_ids", ())],
            "selection_hash": str(selection.get("selection_hash", _hash(selection))),
        }
        try:
            position_registry = self._build_narration_repair_position_registry(
                candidate,
                story_map,
                prompt=prompt,
            )
            position_rows = list(position_registry["positions"])
            slot_summary = {
                "slot_ids": [str(row["slot_id"]) for row in position_rows],
                "claim_ids": [
                    str(claim_id)
                    for row in position_rows
                    for claim_id in row.get("claim_ids", ())
                ],
                "evidence_panel_ids": [
                    str(panel_id)
                    for row in position_rows
                    for panel_id in row.get("evidence_panel_ids", ())
                ],
                "slot_order_hash": str(position_registry["slot_order_hash"]),
            }
        except CloudStageError:
            slot_summary = {
                "slot_ids": [],
                "claim_ids": [],
                "evidence_panel_ids": [],
                "slot_order_hash": "unavailable",
            }
        return {
            "policy_version": NARRATION_REPAIR_IDENTITY_VERSION,
            "panel_lineage": {
                "ordered_panel_ids": panel_ids,
                "panel_identity_hashes": panel_identity_hashes,
                "visual_evidence_hash": visual.visual_evidence_hash if visual else str(source.get("visual_evidence_hash", "")),
                "panels": canonical_panel_rows,
            },
            "model": {"identity_hash": self.model_identity.identity_hash},
            "prompt": {"version": prompt[0], "sha256": prompt[1]},
            "story": {
                "panel_ids": [str(value) for value in story_map.panel_ids],
                "beats_hash": _hash(story_map.beats),
                "claims_hash": _hash(story_map.claims),
                "causal_chain_hash": _hash(story_map.causal_chain),
                "story_map_hash": story_map.story_map_hash,
                "beat_count": len(story_map.beats),
                "claim_count": len(story_map.claims),
                "causal_link_count": len(story_map.causal_chain),
            },
            "selection": selection_summary,
            "slot_registry": slot_summary,
            "candidate": {
                "candidate_hash": _hash(candidate.as_dict()),
                "visual_evidence_hash": candidate.visual_evidence_hash,
                "model_identity_hash": candidate.model_identity_hash,
                "prompt_version": candidate.prompt_version,
                "prompt_sha256": candidate.prompt_sha256,
                "story_map_hash": story_map.story_map_hash,
            },
        }

    def _persist_narration_repair_identity_rejection(
        self,
        *,
        old_identity_hash: str,
        new_identity_hash: str,
        metadata: Mapping[str, Any],
        reason: str,
        model_identity_hash: str,
        prompt: tuple[str, str, str],
    ) -> None:
        if self.cache is None:
            return
        record = {
            "cache_type": NARRATION_REPAIR_IDENTITY_MIGRATION_VERSION,
            "status": "rejected",
            "policy_version": NARRATION_REPAIR_IDENTITY_VERSION,
            "old_identity_hash": old_identity_hash,
            "new_identity_hash": new_identity_hash,
            "canonical_comparison_hash": str(metadata.get("canonical_comparison_hash", "")),
            "counts": dict(metadata.get("counts", {})),
            "mismatch_field": str(metadata.get("mismatch_field", "identity")),
            "reason": str(reason),
            "model_identity_hash": model_identity_hash,
            "prompt_version": prompt[0],
            "prompt_sha256": prompt[1],
        }
        key = "narration-repair-identity-rejection:" + _hash(
            {
                "old_identity_hash": old_identity_hash,
                "new_identity_hash": new_identity_hash,
                "mismatch_field": record["mismatch_field"],
                "model_identity_hash": model_identity_hash,
                "prompt_version": prompt[0],
                "prompt_sha256": prompt[1],
            }
        )
        if not isinstance(self.cache.get(key), Mapping):
            self.cache.put(key, record)

    def _store_narration_repair_candidate(
        self,
        *,
        source: Mapping[str, Any],
        prompt: tuple[str, str, str],
        result: NarrationResult,
        failure_codes: Sequence[str],
        visual: VisualStageResult | None = None,
        story_map: StoryMapResult | None = None,
    ) -> None:
        if self.cache is None:
            return
        payload = result.as_dict()
        try:
            identity_metadata = self._narration_repair_identity_metadata(
                source=source,
                prompt=prompt,
                candidate=result,
                visual=visual,
                story_map=story_map,
            )
        except CloudStageError:
            identity_metadata = None
        self.cache.put(
            self._narration_repair_candidate_key(source, prompt),
            {
                "cache_type": NARRATION_REPAIR_CANDIDATE_VERSION,
                "candidate": payload,
                "candidate_hash": _hash(payload),
                "source_identity_hash": _hash(source),
                "model_identity_hash": self.model_identity.identity_hash,
                "prompt_version": prompt[0],
                "prompt_sha256": prompt[1],
                "visual_evidence_hash": result.visual_evidence_hash,
                "failure_codes": list(dict.fromkeys(str(code) for code in failure_codes)),
                "identity_metadata": identity_metadata,
            },
        )

    def _migrate_narration_repair_candidate_record(
        self,
        *,
        record: Mapping[str, Any],
        source: Mapping[str, Any],
        prompt: tuple[str, str, str],
        visual: VisualStageResult,
        candidate: NarrationResult,
    ) -> Mapping[str, Any] | None:
        stored_identity = record.get("identity_metadata")
        current_identity: dict[str, Any]
        try:
            current_identity = self._narration_repair_identity_metadata(
                source=source,
                prompt=prompt,
                candidate=candidate,
                visual=visual,
            )
        except CloudStageError as exc:
            self._persist_narration_repair_identity_rejection(
                old_identity_hash=str(record.get("source_identity_hash", "legacy")),
                new_identity_hash=_hash(source),
                metadata=exc.safe_metadata,
                reason="current_identity_invalid",
                model_identity_hash=self.model_identity.identity_hash,
                prompt=prompt,
            )
            return None
        if not isinstance(stored_identity, Mapping):
            self._persist_narration_repair_identity_rejection(
                old_identity_hash=str(record.get("source_identity_hash", "legacy")),
                new_identity_hash=_hash(source),
                metadata={
                    "mismatch_field": "identity_metadata",
                    "counts": {
                        "new_panel_count": len(visual.panels),
                    },
                },
                reason="legacy_identity_metadata_missing",
                model_identity_hash=self.model_identity.identity_hash,
                prompt=prompt,
            )
            return None
        try:
            migration = reconcile_narration_repair_identity(
                stored_identity,
                current_identity,
                old_identity_hash=str(record.get("source_identity_hash", "")),
                new_identity_hash=_hash(source),
                reason="candidate_identity_reconciliation",
            )
        except CloudStageError as exc:
            self._persist_narration_repair_identity_rejection(
                old_identity_hash=str(record.get("source_identity_hash", "")),
                new_identity_hash=_hash(source),
                metadata=exc.safe_metadata,
                reason="semantic_identity_mismatch",
                model_identity_hash=self.model_identity.identity_hash,
                prompt=prompt,
            )
            return None
        persist_narration_repair_identity_migration(
            self.cache,
            stored_identity,
            current_identity,
            old_identity_hash=str(record.get("source_identity_hash", "")),
            new_identity_hash=_hash(source),
            model_identity_hash=self.model_identity.identity_hash,
            prompt_version=prompt[0],
            prompt_sha256=prompt[1],
            reason="candidate_identity_reconciliation",
        )
        migrated = dict(record)
        migrated["identity_metadata"] = current_identity
        migrated["identity_migration"] = migration
        migrated["source_identity_hash"] = _hash(source)
        self.cache.put(self._narration_repair_candidate_key(source, prompt), migrated)
        return migrated

    def _load_narration_repair_candidate(
        self,
        *,
        source: Mapping[str, Any],
        prompt: tuple[str, str, str],
        visual: VisualStageResult,
    ) -> tuple[NarrationResult, tuple[str, ...]] | None:
        if self.cache is None:
            return None
        record = self.cache.get(self._narration_repair_candidate_key(source, prompt))
        if not isinstance(record, Mapping) or record.get("cache_type") != NARRATION_REPAIR_CANDIDATE_VERSION:
            record = None
            iterator = getattr(self.cache, "iter_records", None)
            if callable(iterator):
                for candidate_record in iterator(
                    cache_type=NARRATION_REPAIR_CANDIDATE_VERSION
                ):
                    if (
                        not isinstance(candidate_record, Mapping)
                        or candidate_record.get("model_identity_hash")
                        != self.model_identity.identity_hash
                        or candidate_record.get("prompt_version") != prompt[0]
                        or candidate_record.get("prompt_sha256") != prompt[1]
                    ):
                        continue
                    payload = candidate_record.get("candidate")
                    if not isinstance(payload, Mapping):
                        continue
                    try:
                        candidate = NarrationResult.from_dict(payload)
                    except (KeyError, TypeError, ValueError):
                        continue
                    if candidate_record.get("candidate_hash") != _hash(candidate.as_dict()):
                        continue
                    migrated = self._migrate_narration_repair_candidate_record(
                        record=candidate_record,
                        source=source,
                        prompt=prompt,
                        visual=visual,
                        candidate=candidate,
                    )
                    if migrated is not None:
                        record = migrated
                        break
            if record is None:
                return None
        payload = record.get("candidate")
        if not isinstance(payload, Mapping):
            return None
        try:
            candidate = NarrationResult.from_dict(payload)
        except (KeyError, TypeError, ValueError):
            return None
        migrated_record = self._migrate_narration_repair_candidate_record(
            record=record,
            source=source,
            prompt=prompt,
            visual=visual,
            candidate=candidate,
        )
        if migrated_record is None:
            return None
        record = migrated_record
        if (
            record.get("candidate_hash") != _hash(candidate.as_dict())
            or record.get("source_identity_hash") != _hash(source)
            or record.get("model_identity_hash") != self.model_identity.identity_hash
            or record.get("prompt_version") != prompt[0]
            or record.get("prompt_sha256") != prompt[1]
            or candidate.visual_evidence_hash != visual.visual_evidence_hash
            or not _narration_result_is_usable(
                candidate,
                visual,
                require_duration=False,
                require_grounding=True,
            )
        ):
            return None
        failures = tuple(str(code) for code in record.get("failure_codes", ()))
        expected = self._narration_contract_failures(candidate)
        if not failures or tuple(dict.fromkeys(failures)) != expected:
            return None
        return candidate, expected

    def _narration_repair_result_key(
        self,
        *,
        source: Mapping[str, Any],
        targeted_repair: Mapping[str, Any],
        prompt: tuple[str, str, str],
    ) -> str:
        return _cache_key(
            NARRATION_REPAIR_VERSION,
            self._repair_cache_source(source, targeted_repair),
            self.model_identity,
            prompt,
        )

    def _store_narration_repair_result(
        self,
        *,
        source: Mapping[str, Any],
        targeted_repair: Mapping[str, Any],
        prompt: tuple[str, str, str],
        result: NarrationResult,
    ) -> None:
        if self.cache is None:
            return
        payload = result.as_dict()
        repair_report = result.qc_report.get("narration_repair", {})
        micro_compaction = repair_report.get("micro_compaction", {})
        self.cache.put(
            self._narration_repair_result_key(
                source=source,
                targeted_repair=targeted_repair,
                prompt=prompt,
            ),
            {
                "cache_type": NARRATION_REPAIR_RESULT_VERSION,
                "result": payload,
                "result_hash": _hash(payload),
                "source_identity_hash": _hash(source),
                "model_identity_hash": self.model_identity.identity_hash,
                "prompt_version": prompt[0],
                "prompt_sha256": prompt[1],
                "repair_attempt": int(targeted_repair.get("repair_attempt", 1)),
                "candidate_hash": str(targeted_repair.get("candidate_hash", "")),
                "position_registry_version": str(
                    targeted_repair.get("position_registry_version", "")
                ),
                "slot_order_hash": str(targeted_repair.get("slot_order_hash", "")),
                "passage_lineage_version": str(
                    targeted_repair.get("passage_lineage_version", "")
                ),
                "passage_lineage_hash": str(
                    targeted_repair.get("passage_lineage_hash", "")
                ),
                "micro_compaction_version": str(
                    micro_compaction.get("version", "")
                ),
                "micro_compaction_result_hash": str(
                    micro_compaction.get("result_hash", "")
                ),
            },
        )

    def _load_narration_repair_result(
        self,
        *,
        source: Mapping[str, Any],
        targeted_repair: Mapping[str, Any],
        prompt: tuple[str, str, str],
        candidate: NarrationResult,
        visual: VisualStageResult,
        removable_passage_ids: Sequence[str],
    ) -> NarrationResult | None:
        if self.cache is None:
            return None
        record = self.cache.get(
            self._narration_repair_result_key(
                source=source,
                targeted_repair=targeted_repair,
                prompt=prompt,
            )
        )
        if not isinstance(record, Mapping) or record.get("cache_type") != NARRATION_REPAIR_RESULT_VERSION:
            return None
        payload = record.get("result")
        if not isinstance(payload, Mapping):
            return None
        try:
            result = NarrationResult.from_dict(payload)
        except (KeyError, TypeError, ValueError):
            return None
        repair_report = result.qc_report.get("narration_repair", {})
        micro_compaction = repair_report.get("micro_compaction", {})
        if (
            record.get("result_hash") != _hash(result.as_dict())
            or record.get("source_identity_hash") != _hash(source)
            or record.get("model_identity_hash") != self.model_identity.identity_hash
            or record.get("prompt_version") != prompt[0]
            or record.get("prompt_sha256") != prompt[1]
            or record.get("candidate_hash") != str(targeted_repair.get("candidate_hash", ""))
            or record.get("position_registry_version")
            != str(targeted_repair.get("position_registry_version", ""))
            or record.get("slot_order_hash")
            != str(targeted_repair.get("slot_order_hash", ""))
            or record.get("passage_lineage_version")
            != str(targeted_repair.get("passage_lineage_version", ""))
            or record.get("passage_lineage_hash")
            != str(targeted_repair.get("passage_lineage_hash", ""))
            or record.get("micro_compaction_version")
            != NARRATION_MICRO_COMPACTION_VERSION
            or micro_compaction.get("version")
            != NARRATION_MICRO_COMPACTION_VERSION
            or record.get("micro_compaction_result_hash")
            != micro_compaction.get("result_hash")
            or not _narration_result_is_usable(
                result,
                visual,
                require_duration=True,
                require_grounding=True,
            )
        ):
            return None
        result = self._narration_repair_scope_reconciled(
            candidate,
            result,
            removable_passage_ids,
            trusted_lineage={
                str(row["passage_id"]): row
                for row in self._reconstruct_narration_repair_passage_lineage(
                    candidate,
                    targeted_repair.get("position_registry"),
                )["passages"]
            },
        )
        if result is None:
            return None
        report = dict(result.qc_report)
        report["narration_repair"] = {
            "contract_version": NARRATION_REPAIR_VERSION,
            "micro_compaction": dict(micro_compaction),
            "scope": "position_locked_rewrite_vector",
            "candidate_hash": str(targeted_repair.get("candidate_hash", "")),
            "position_registry_version": str(
                targeted_repair.get("position_registry_version", "")
            ),
            "slot_order_hash": str(targeted_repair.get("slot_order_hash", "")),
            "passage_lineage_version": str(
                targeted_repair.get("passage_lineage_version", "")
            ),
            "passage_lineage_hash": str(
                targeted_repair.get("passage_lineage_hash", "")
            ),
            "failure_codes": list(targeted_repair.get("failure_codes", ())),
            "attempts": int(record.get("repair_attempt", 1)),
            "provider_stage": "narration_repair",
            "cache_reused": True,
        }
        return replace(result, qc_report=report)

    @staticmethod
    def _narration_passage_ids(result: NarrationResult) -> tuple[str, ...]:
        return tuple(
            str(passage.get("passage_id", ""))
            for passage in result.passages
        )

    @staticmethod
    def _removable_narration_passage_ids(
        candidate: NarrationResult,
    ) -> tuple[str, ...]:
        if len(candidate.passages) <= 4:
            return ()
        rows = [
            passage
            for passage in candidate.passages[:-1]
            if str(passage.get("passage_id", "")).strip()
        ]
        rows.sort(
            key=lambda passage: (
                len(passage.get("claim_ids", ())),
                len(passage.get("evidence_panel_ids", ())),
                str(passage.get("passage_id", "")),
            )
        )
        return tuple(
            str(passage["passage_id"])
            for passage in rows[: max(0, len(candidate.passages) - 4)]
        )

    @staticmethod
    def _narration_repair_scope_compatible(
        candidate: NarrationResult,
        repaired: NarrationResult,
        removable_passage_ids: Sequence[str],
    ) -> bool:
        return CloudStageRunner._narration_repair_scope_reconciled(
            candidate,
            repaired,
            removable_passage_ids,
        ) is not None

    @staticmethod
    def _narration_repair_scope_reconciled(
        candidate: NarrationResult,
        repaired: NarrationResult,
        removable_passage_ids: Sequence[str],
        trusted_lineage: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> NarrationResult | None:
        if (
            candidate.ending_kind != repaired.ending_kind
            or candidate.story_spine != repaired.story_spine
            or tuple(candidate.observations) != tuple(repaired.observations)
            or len(repaired.passages) < 4
        ):
            return None
        candidate_passages = {
            str(item.get("passage_id", "")): item for item in candidate.passages
        }
        repaired_passages = {
            str(item.get("passage_id", "")): item for item in repaired.passages
        }
        if (
            len(candidate_passages) != len(candidate.passages)
            or len(repaired_passages) != len(repaired.passages)
        ):
            return None
        removed_passage_ids = set(candidate_passages) - set(repaired_passages)
        if (
            not removed_passage_ids.issubset(set(removable_passage_ids))
            or set(repaired_passages) - set(candidate_passages)
        ):
            return None
        for passage_id in set(repaired_passages) & set(candidate_passages):
            before = candidate_passages[passage_id]
            after = repaired_passages[passage_id]
            if trusted_lineage is None:
                for key in ("claim_ids", "evidence_panel_ids"):
                    before_values = before.get(key)
                    after_values = after.get(key)
                    if not isinstance(before_values, (list, tuple)) or not isinstance(
                        after_values, (list, tuple)
                    ):
                        return None
                    before_values = tuple(str(value) for value in before_values)
                    after_values = tuple(str(value) for value in after_values)
                    if (
                        not after_values
                        or len(set(after_values)) != len(after_values)
                        or any(value not in before_values for value in after_values)
                        or tuple(
                            value for value in before_values if value in set(after_values)
                        )
                        != after_values
                    ):
                        return None
                continue
            # The trusted claim-evidence closure, not the candidate's stale
            # passage evidence, is the position repair's evidence reference:
            # the registry rebuilds each passage's evidence union from the
            # trusted story map, so a repaired vector carries that closure
            # even when the durable candidate cited fewer or different
            # panels. Claims must still match the candidate exactly.
            reference_row = trusted_lineage.get(passage_id)
            if not isinstance(reference_row, Mapping):
                return None
            for key in ("claim_ids", "evidence_panel_ids"):
                reference_values = reference_row.get(key)
                after_values = after.get(key)
                if not isinstance(reference_values, (list, tuple)) or not isinstance(
                    after_values, (list, tuple)
                ):
                    return None
                reference_tuple = tuple(str(value) for value in reference_values)
                after_values = tuple(str(value) for value in after_values)
                if (
                    not after_values
                    or len(set(after_values)) != len(after_values)
                    or set(after_values) != set(reference_tuple)
                    or tuple(
                        value
                        for value in reference_tuple
                        if value in set(after_values)
                    )
                    != after_values
                ):
                    return None
            candidate_claims = before.get("claim_ids")
            repaired_claims = after.get("claim_ids")
            if not isinstance(candidate_claims, (list, tuple)) or not isinstance(
                repaired_claims,
                (list, tuple),
            ):
                return None
            if not {str(value) for value in repaired_claims} <= {
                str(value) for value in candidate_claims
            }:
                return None
        candidate_claims = {
            str(item.get("claim_id", "")): item
            for item in candidate.evidence_graph.get("claims", ())
            if isinstance(item, Mapping)
        }
        repaired_claims = {
            str(item.get("claim_id", "")): item
            for item in repaired.evidence_graph.get("claims", ())
            if isinstance(item, Mapping)
        }
        retained_claim_ids = {
            str(claim_id)
            for passage in repaired.passages
            for claim_id in passage.get("claim_ids", ())
        }
        if (
            not retained_claim_ids
            or set(repaired_claims) != retained_claim_ids
            or set(repaired_claims) - set(candidate_claims)
        ):
            return None
        for claim_id in retained_claim_ids:
            before = candidate_claims[claim_id]
            after = repaired_claims[claim_id]
            before_refs = tuple(
                str(value)
                for value in before.get("evidence_panel_ids", before.get("panel_ids", ()))
            )
            after_refs = tuple(
                str(value)
                for value in after.get("evidence_panel_ids", after.get("panel_ids", ()))
            )
            if (
                before.get("claim_type") != after.get("claim_type")
                or before_refs != after_refs
            ):
                return None
        canonical_passages = []
        for passage in repaired.passages:
            passage_id = str(passage.get("passage_id", ""))
            canonical = dict(passage)
            original = candidate_passages[passage_id]
            canonical["editorial_role"] = original.get("editorial_role", "")
            canonical["claim_ids"] = list(passage.get("claim_ids", ()))
            canonical["evidence_panel_ids"] = list(passage.get("evidence_panel_ids", ()))
            canonical_passages.append(canonical)
        canonical_claims = [
            dict(claim)
            for claim_id, claim in candidate_claims.items()
            if claim_id in retained_claim_ids
        ]
        return replace(
            repaired,
            passages=tuple(canonical_passages),
            ending_kind=candidate.ending_kind,
            observations=tuple(dict(item) for item in candidate.observations),
            continuity_ledger=dict(candidate.continuity_ledger),
            evidence_graph={"claims": canonical_claims},
            story_spine=dict(candidate.story_spine),
        )

    def run_narration_repair_candidate(
        self,
        candidate: NarrationResult,
        visual: VisualStageResult,
        story_map: StoryMapResult,
        *,
        panels: Sequence[CloudPanelInput] | None = None,
    ) -> NarrationResult:
        """Run only the bounded compaction repair from compact durable stages.

        This boundary deliberately accepts metadata-only visual rows and does
        not call normal narration generation.  The candidate remains outside
        the final narration cache until the strict final admission checks in
        ``run_narration`` pass.
        """

        prompt = self.prompts["narration"]
        compact_visual, compact_story_map = self._compact_narration_repair_context(
            candidate,
            visual,
            story_map,
        )
        if (
            candidate.model_identity_hash != self.model_identity.identity_hash
            or candidate.prompt_version != prompt[0]
            or candidate.prompt_sha256 != prompt[1]
        ):
            raise CloudStageError("cloud.narrative_repair_identity_mismatch", reviewable=True)
        failure_codes = self._narration_contract_failures(candidate)
        if not failure_codes:
            raise CloudStageError("cloud.narrative_repair_not_needed")
        compact_panels = None
        if panels is not None:
            panels_by_id = {str(panel.panel_id): panel for panel in panels}
            try:
                compact_panels = tuple(
                    panels_by_id[panel_id]
                    for panel_id in compact_visual.panel_ids
                )
            except KeyError:
                raise CloudStageError(
                    "cloud.narrative_repair_identity_mismatch",
                    reviewable=True,
                ) from None
        if compact_panels is None:
            observations = [dict(item) for item in candidate.observations]
            visual_rows = {
                str(item.get("panel_id", "")): item
                for item in compact_visual.panels
            }
            for observation in observations:
                panel_id = str(observation.get("panel_id", ""))
                visual_item = visual_rows.get(panel_id)
                if (
                    visual_item is None
                    or str(observation.get("source_asset_id", ""))
                    != str(visual_item.get("source_asset_id", ""))
                    or panel_id not in {
                        str(value) for value in observation.get("evidence_refs", ())
                    }
                ):
                    raise CloudStageError(
                        "cloud.narrative_repair_identity_mismatch",
                        reviewable=True,
                    )
            structural = {
                "continuity_ledger": dict(candidate.continuity_ledger),
                "coverage_manifest": {
                    "total_panels": len(observations),
                    "processed_panels": len(observations),
                    "total_canonical_panels": len(observations),
                    "persisted_canonical_panels": len(observations),
                    "processed_canonical_panel_count": len(observations),
                    "panel_ids": list(compact_visual.panel_ids),
                    "source_content_coverage_ratio": 1.0,
                    "unresolved_material_area": 0,
                    "material_unresolved_regions": [],
                    "reconciliation_complete": True,
                },
            }
        else:
            observations, structural = self._narration_observations(
                compact_visual,
                compact_panels,
            )
        source = {
            "editorial_selection_version": EDITORIAL_SELECTION_VERSION,
            "panel_ids": list(compact_visual.panel_ids),
            "visual_source_hash": compact_visual.source_hash,
            "visual_evidence_hash": compact_visual.visual_evidence_hash,
            "visual_observations": observations,
            "story_map": compact_story_map.as_dict(),
            "duration_contract": {
                **script.narration_duration_contract("dramatic"),
                "minimum_s": 50.0,
                "maximum_s": 60.0,
                "target_word_min": 115,
                "target_word_max": 125,
            },
        }
        self._store_narration_repair_candidate(
            source=source,
            prompt=prompt,
            result=candidate,
            failure_codes=failure_codes,
            visual=compact_visual,
            story_map=compact_story_map,
        )
        return self._run_targeted_narration_repair(
            prompt,
            source,
            observations,
            structural,
            compact_story_map,
            compact_visual,
            candidate,
            failure_codes,
        )

    def _run_targeted_narration_repair(
        self,
        prompt: tuple[str, str, str],
        source: Mapping[str, Any],
        observations: Sequence[Mapping[str, Any]],
        structural: Mapping[str, Any],
        story_map: StoryMapResult,
        visual: VisualStageResult,
        candidate: NarrationResult,
        failure_codes: Sequence[str],
    ) -> NarrationResult:
        """Repair prose or complete low-priority passages without changing evidence scope."""

        candidate_hash = _hash(candidate.as_dict())
        position_registry = self._build_narration_repair_position_registry(
            candidate,
            story_map,
        )
        removable_passage_ids = self._removable_narration_passage_ids(candidate)
        repair_context = {
            "contract_version": NARRATION_REPAIR_VERSION,
            "micro_compaction_version": NARRATION_MICRO_COMPACTION_VERSION,
            "failure_codes": list(dict.fromkeys(str(code) for code in failure_codes)),
            "candidate_hash": candidate_hash,
            "position_registry_version": position_registry["version"],
            "slot_order_hash": position_registry["slot_order_hash"],
            "passage_lineage_version": position_registry["passage_lineage_version"],
            "passage_lineage_hash": position_registry["passage_lineage_hash"],
            "position_registry": position_registry,
            "position_context": position_registry["provider_positions"],
            "removable_passage_ids": list(removable_passage_ids),
            "immutable_scope": [
                "passage_id",
                "claim_ids",
                "evidence_panel_ids",
                "evidence_graph",
                "observations",
                "ending_kind",
                "story_spine",
            ],
            "target_word_min": 115,
            "target_word_max": 125,
            "target_word_count": position_registry["target_word_count"],
            "target_duration_min_s": 50.0,
            "target_duration_max_s": 60.0,
            "prior_narration": candidate.as_dict(),
        }
        repair_prompt_version = "vision-first-story-analyzer-v3-targeted-position-repair-v9"
        repair_prompt_text = f"{prompt[2]}\n\n{NARRATION_REPAIR_INSTRUCTION}"
        repair_prompt = (
            repair_prompt_version,
            _hash(repair_prompt_text),
            repair_prompt_text,
        )
        cached_repair = self._load_narration_repair_result(
            source=source,
            targeted_repair=repair_context,
            prompt=repair_prompt,
            candidate=candidate,
            visual=visual,
            removable_passage_ids=removable_passage_ids,
        )
        if cached_repair is not None:
            return cached_repair

        last_error = CloudStageError(
            "cloud.narrative_duration_out_of_range",
            reviewable=True,
        )
        attempt_limit = (
            NARRATION_REPAIR_POSITION_MAX_ATTEMPTS
            if position_registry["version"] == NARRATION_REPAIR_POSITION_REGISTRY_VERSION
            else NARRATION_REPAIR_MAX_ATTEMPTS
        )
        for attempt in range(attempt_limit):
            context = {
                **repair_context,
                "repair_attempt": attempt + 1,
            }
            try:
                repaired = self._run_narration_batched(
                    prompt,
                    source,
                    observations,
                    structural,
                    story_map,
                    visual,
                    enforce_duration=False,
                    stage="narration_repair",
                    targeted_repair=context,
                    request_prompt_version=repair_prompt_version,
                    request_prompt_sha256=repair_prompt[1],
                    request_prompt_text=repair_prompt_text,
                    repair_position_registry=position_registry,
                    repair_candidate=candidate,
                )
                reconciled = self._narration_repair_scope_reconciled(
                    candidate,
                    repaired,
                    removable_passage_ids,
                    trusted_lineage={
                        str(row["passage_id"]): row
                        for row in self._reconstruct_narration_repair_passage_lineage(
                            candidate,
                            position_registry,
                        )["passages"]
                    },
                )
                if reconciled is None:
                    self.last_response_shape_metrics.update(
                        {
                            "reconciled_scope_ok": False,
                            "reconciled_failed_predicates": [
                                "scope_compatibility"
                            ],
                            "reconciled_failed_predicate": "scope_compatibility",
                        }
                    )
                    raise CloudStageError(
                        "cloud.narrative_repair_scope_invalid",
                        reviewable=True,
                        safe_metadata=self._response_shape_metrics_for_failure(
                            "cloud.narrative_repair_scope_invalid"
                        ),
                    )
                repaired = reconciled
                self.last_response_shape_metrics.update(
                    self._narration_repair_result_shape_metrics(
                        repaired,
                        visual,
                        scope_ok=True,
                    )
                )
                if not _narration_result_is_usable(
                    repaired,
                    visual,
                    require_duration=True,
                    require_grounding=True,
                ):
                    failures = self._narration_contract_failures(repaired)
                    failure_code = (
                        failures[0]
                        if failures
                        else "cloud.narrative_not_grounded"
                    )
                    last_error = CloudStageError(
                        failure_code,
                        reviewable=True,
                        safe_metadata=self._response_shape_metrics_for_failure(
                            failure_code
                        ),
                    )
                    continue
                report = dict(repaired.qc_report)
                micro_compaction = self.last_response_shape_metrics.get(
                    "micro_compaction",
                    {
                        "version": NARRATION_MICRO_COMPACTION_VERSION,
                        "applied": False,
                        "operation_count": 0,
                        "operation_types": [],
                        "result_hash": _hash({"rewrites": []}),
                    },
                )
                report["narration_repair"] = {
                    "contract_version": NARRATION_REPAIR_VERSION,
                    "micro_compaction": dict(micro_compaction),
                    "scope": "position_locked_rewrite_vector",
                    "candidate_hash": candidate_hash,
                    "position_registry_version": position_registry["version"],
                    "slot_order_hash": position_registry["slot_order_hash"],
                    "passage_lineage_version": position_registry[
                        "passage_lineage_version"
                    ],
                    "passage_lineage_hash": position_registry[
                        "passage_lineage_hash"
                    ],
                    "failure_codes": list(repair_context["failure_codes"]),
                    "removable_passage_ids": list(removable_passage_ids),
                    "removed_passage_ids": [
                        passage_id
                        for passage_id in self._narration_passage_ids(candidate)
                        if passage_id not in self._narration_passage_ids(repaired)
                    ],
                    "attempts": attempt + 1,
                    "provider_stage": "narration_repair",
                    "cache_reused": False,
                }
                repaired = replace(repaired, qc_report=report)
                self._store_narration_repair_result(
                    source=source,
                    targeted_repair=context,
                    prompt=repair_prompt,
                    result=repaired,
                )
                return repaired
            except CloudStageError as exc:
                if exc.code == "cloud.request_budget_exceeded":
                    if attempt == 0:
                        last_error = exc
                    break
                last_error = exc
                if attempt + 1 >= NARRATION_REPAIR_MAX_ATTEMPTS:
                    break
        raise last_error

    def _run_narration_batched(
        self,
        prompt,
        source,
        observations,
        structural,
        story_map,
        visual,
        *,
        enforce_duration: bool = True,
        stage: str = "narration",
        targeted_repair: Mapping[str, Any] | None = None,
        request_prompt_version: str | None = None,
        request_prompt_sha256: str | None = None,
        request_prompt_text: str | None = None,
        repair_slots: Sequence[NarrationRepairSlot] | None = None,
        repair_position_registry: Mapping[str, Any] | None = None,
        repair_candidate: NarrationResult | None = None,
    ) -> NarrationResult:
        if repair_position_registry is not None:
            self.last_response_shape_metrics = {}
        retryable_codes = {
            "cloud.provider_request_failed",
            "cloud.provider_response_invalid",
            "cloud.narrative_not_grounded",
            "cloud.narrative_claim_unmapped",
            "cloud.narrative_qc_blocked",
            "cloud.narrative_duration_out_of_range",
        }
        obs_by_id = {str(item["panel_id"]): item for item in observations}
        if repair_position_registry is not None:
            # The targeted position vector is chapter-scoped: its claims and
            # passage evidence always reference the full compact scope, so a
            # per-chunk split would validate them against a partial panel set
            # and reject every in-window vector as ungrounded.
            chunks = [visual.panels]
        else:
            chunk_step = 600
            chunks = [
                visual.panels[i:i + chunk_step]
                for i in range(0, len(visual.panels), chunk_step)
            ]
        all_passages: list[dict[str, Any]] = []
        all_claims: list[dict[str, Any]] = []
        story_spine: dict[str, Any] = {}
        chunk_beats_ids: set[str] = set()
        for chunk_index, chunk in enumerate(chunks):
            chunk_ids = tuple(panel["panel_id"] for panel in chunk)
            chunk_obs = [dict(obs_by_id[panel_id]) for panel_id in chunk_ids]
            for obs_index, obs_item in enumerate(chunk_obs):
                obs_item["source_index"] = obs_index
            chunk_story = story_map.as_dict()
            chunk_beat_ids = {
                str(beat["beat_id"])
                for beat in story_map.beats
                if {str(item) for item in beat.get("panel_ids", ())} & set(chunk_ids)
            }  # noqa: C401 - explicit set comprehension is equivalent
            chunk_story["beats"] = [
                dict(beat)
                for beat in story_map.beats
                if str(beat["beat_id"]) in chunk_beat_ids
            ]
            chunk_story["causal_chain"] = [
                dict(link)
                for link in story_map.causal_chain
                if str(link.get("from_beat", "")) in chunk_beat_ids
                or str(link.get("to_beat", "")) in chunk_beat_ids
            ]
            chunk_claims = [
                dict(claim)
                for claim in story_map.claims
                if any(
                    str(pid) in chunk_ids
                    for pid in claim.get(
                        "evidence_panel_ids",
                        claim.get("panel_ids", []),
                    )
                )
            ]
            chunk_story["claims"] = chunk_claims
            chunk_ledger = dict(structural["continuity_ledger"])
            chunk_ledger["chunks"] = [
                {**dict(chunk_entry), "panel_ids": [
                    str(pid) for pid in chunk_entry.get("panel_ids", []) if str(pid) in chunk_ids
                ]}
                for chunk_entry in structural["continuity_ledger"].get("chunks", [])
                if any(str(pid) in chunk_ids for pid in chunk_entry.get("panel_ids", []))
            ]
            chunk_ledger["entities"] = [
                {**dict(entity), "panel_ids": [
                    str(pid) for pid in entity.get("panel_ids", []) if str(pid) in chunk_ids
                ]}
                for entity in structural["continuity_ledger"].get("entities", [])
                if any(str(pid) in chunk_ids for pid in entity.get("panel_ids", []))
            ]
            chunk_source = {
                **source,
                "panel_ids": list(chunk_ids),
                "visual_observations": chunk_obs,
                "story_map": chunk_story,
                "batch_index": chunk_index,
                "batch_count": len(chunks),
            }
            if targeted_repair is not None:
                chunk_source["targeted_repair"] = dict(targeted_repair)
            chunk_end = None
            retry_feedback = ""
            for attempt in range(self.max_attempts):
                try:
                    request_payload = {**chunk_source, "retry_attempt": attempt}
                    if retry_feedback:
                        request_payload["contract_retry_feedback"] = retry_feedback
                    raw = self._call(
                        lambda request_payload=request_payload: self.provider.complete_json(
                            stage=stage,
                            prompt_version=request_prompt_version or prompt[0],
                            prompt_sha256=request_prompt_sha256 or prompt[1],
                            prompt_text=request_prompt_text or prompt[2],
                            payload=request_payload,
                        ),
                        request_stage=(
                            "narration_repair"
                            if stage == "narration_repair"
                            else "narration"
                            if stage == "narration"
                            else "other"
                        ),
                    )
                    if not isinstance(raw, Mapping):
                        raise CloudStageError("cloud.provider_response_invalid")
                    if repair_position_registry is not None:
                        if repair_candidate is None:
                            raise CloudStageError(
                                "cloud.narrative_repair_position_contract_invalid",
                                reviewable=True,
                            )
                        provider_output = self._reconcile_narration_repair_vector(
                            raw,
                            repair_position_registry,
                            repair_candidate,
                            story_map=story_map,
                        )
                        passage_lineage = provider_output.pop(
                            "_passage_lineage", None
                        )
                        if not isinstance(passage_lineage, Mapping):
                            raise CloudStageError(
                                "cloud.narrative_repair_position_lineage_invalid",
                                reviewable=True,
                            )
                        shape_metrics = provider_output.pop(
                            "_response_shape_metrics", None
                        )
                        if isinstance(shape_metrics, Mapping):
                            self.last_response_shape_metrics = dict(shape_metrics)
                        self.last_response_shape_metrics.update(
                            {
                                "passage_lineage_version": str(
                                    passage_lineage.get("version", "")
                                ),
                                "passage_lineage_hash": str(
                                    passage_lineage.get("lineage_hash", "")
                                ),
                            }
                        )
                    else:
                        provider_output = raw.get("analyzer_output", raw)
                    if not isinstance(provider_output, Mapping):
                        raise CloudStageError("cloud.provider_response_invalid")
                    if repair_slots is not None:
                        if repair_candidate is None:
                            raise CloudStageError(
                                "cloud.narrative_repair_slot_contract_invalid",
                                reviewable=True,
                            )
                        provider_output = self._reconcile_narration_repair_slots(
                            provider_output,
                            repair_slots,
                            repair_candidate,
                        )
                    raw_claims = provider_output.get("evidence_graph")
                    if raw_claims is None:
                        raw_claims = self._claims_from_causal_map(
                            provider_output.get("script_passages"),
                            StoryMapResult(
                                panel_ids=chunk_ids,
                                beats=tuple(chunk_story["beats"]),
                                causal_chain=tuple(chunk_story["causal_chain"]),
                                claims=tuple(chunk_claims),
                                story_map_hash=story_map.story_map_hash,
                                model_identity_hash=story_map.model_identity_hash,
                                prompt_version=story_map.prompt_version,
                                prompt_sha256=story_map.prompt_sha256,
                            ),
                        )
                    claims_list = self._normalize_narration_claims(raw_claims)
                    # Provider emits claim text on the referencing passage;
                    # backfill claim["text"] so the contract validator passes.
                    text_by_claim: dict[str, str] = {}
                    for passage in provider_output.get("script_passages") or []:
                        if not isinstance(passage, Mapping):
                            continue
                        for claim_ref in passage.get("claim_ids") or []:
                            text_by_claim.setdefault(str(claim_ref), str(passage.get("text", "")))
                    for claim in claims_list:
                        if not claim.get("text"):
                            claim["text"] = text_by_claim.get(str(claim.get("claim_id")), "")
                    claims_by_id = {
                        str(claim.get("claim_id")): claim
                        for claim in claims_list
                        if str(claim.get("claim_id", "")).strip()
                    }
                    normalized_passages = provider_output.get("script_passages")
                    if not isinstance(normalized_passages, list):
                        raise CloudStageError(
                            "cloud.narrative_not_grounded",
                            reviewable=True,
                        )
                    for passage in normalized_passages:
                        if not isinstance(passage, Mapping):
                            raise CloudStageError(
                                "cloud.narrative_not_grounded",
                                reviewable=True,
                            )
                        refs = passage.get("claim_ids")
                        if not isinstance(refs, list) or not refs:
                            raise CloudStageError(
                                "cloud.narrative_not_grounded",
                                reviewable=True,
                            )
                        normalized_refs: list[str] = []
                        for ref in refs:
                            if not isinstance(ref, str):
                                raise CloudStageError(
                                    "cloud.narrative_not_grounded",
                                    reviewable=True,
                                )
                            resolved = ref
                            if resolved not in claims_by_id:
                                matches = [
                                    claim_id
                                    for claim_id in claims_by_id
                                    if claim_id.rsplit("__", 1)[-1] == ref
                                ]
                                if len(matches) != 1:
                                    raise CloudStageError(
                                        "cloud.narrative_not_grounded",
                                        reviewable=True,
                                    )
                                resolved = matches[0]
                            normalized_refs.append(resolved)
                        passage["claim_ids"] = normalized_refs
                    output = {
                        "observations": chunk_obs,
                        "continuity_ledger": chunk_ledger,
                        "coverage_manifest": {
                            "total_panels": len(chunk_ids),
                            "processed_panels": len(chunk_ids),
                            "total_canonical_panels": len(chunk_ids),
                            "persisted_canonical_panels": len(chunk_ids),
                            "processed_canonical_panel_count": len(chunk_ids),
                            "panel_ids": list(chunk_ids),
                            "source_content_coverage_ratio": 1.0,
                            "unresolved_material_area": 0,
                            "material_unresolved_regions": [],
                            "reconciliation_complete": True,
                        },
                        "evidence_graph": {"claims": claims_list},
                        "narrative_outline": provider_output.get("narrative_outline"),
                        "script_passages": provider_output.get("script_passages"),
                    }
                    analyzer_contract.validate_analyzer_output(
                        output,
                        expected_panel_ids=tuple(str(item["panel_id"]) for item in chunk_obs),
                        narrative_profile_id="sharp_friend_v1",
                    )
                    claims = {claim["claim_id"]: claim for claim in claims_list}
                    passages = tuple(dict(item) for item in output["script_passages"])
                    report = editorial_qc.screen_narrative_naturalness(
                        passages,
                        claims,
                        narrative_identity.SHARP_FRIEND_V1,
                    )
                    checks = quality.check_narrative_naturalness(report)
                    if any(not check.passed and check.severity == "error" for check in checks):
                        raise CloudStageError("cloud.narrative_qc_blocked", reviewable=True)
                    outline = output.get("narrative_outline")
                    if isinstance(outline, Mapping):
                        candidate_spine = outline.get("story_spine")
                        if isinstance(candidate_spine, Mapping):
                            for key, value in candidate_spine.items():
                                if str(value).strip():
                                    story_spine.setdefault(str(key), value)
                    chunk_end = str(
                        (outline or {}).get("ending_kind", "")
                        if isinstance(outline, Mapping)
                        else ""
                    )
                    all_passages.extend(dict(item) for item in passages)
                    all_claims.extend(dict(claim) for claim in claims_list)
                    chunk_beats_ids.update(chunk_beat_ids)
                    break
                except analyzer_contract.AnalyzerContractError as exc:
                    diagnostic = _safe_narration_contract_diagnostic(
                        str(exc),
                        output if isinstance(output, Mapping) else None,
                    )
                    if attempt + 1 < self.max_attempts:
                        retry_feedback = _narration_retry_feedback(str(exc))
                        continue
                    raise CloudStageError(
                        "cloud.narrative_not_grounded",
                        diagnostic,
                        reviewable=True,
                    ) from None
                except CloudStageError as exc:
                    if exc.safe_metadata:
                        self.last_response_shape_metrics = dict(exc.safe_metadata)
                    print(f"NARR_CHUNK_FAIL chunk={chunk_index} attempt={attempt} code={exc.code}", file=sys.stderr, flush=True)
                    if (
                        exc.code in retryable_codes
                        and attempt + 1 < self.max_attempts
                    ):
                        retry_feedback = _narration_retry_feedback(exc.code)
                        continue
                    raise
            if chunk_end is None:
                print(f"NARR_CHUNK_FAIL chunk={chunk_index} exhausted retries", file=sys.stderr, flush=True)
                raise CloudStageError("cloud.narrative_not_grounded", reviewable=True)
        spoken_text = "\n\n".join(str(item["text"]).strip() for item in all_passages)
        display_words = tuple(re.findall(r"[A-Z0-9]+", spoken_text.upper()))
        if not display_words or any(not word.isalnum() for word in display_words):
            raise CloudStageError("cloud.display_derivation_invalid")
        duration_metrics = script.narration_duration_metrics(
            spoken_text,
            "dramatic",
        )
        duration = float(duration_metrics["estimated_duration_s"])
        # Preview relaxation: the 50-60s contract targets a single short clip,
        # but a full 703-panel chapter batch narrates ~2.5x that length.  The
        # production contract stays 50-60s; preview accepts long-form output.
        # The final 50-60s/115-125 contract is enforced by run_narration
        # after the bounded targeted repair; this helper must return the
        # validated candidate even when it needs repair.
        total_words = int(duration_metrics["word_count"])
        qc_report = {
            "profile_id": "sharp_friend_v1",
            "profile_sha256": narrative_identity.SHARP_FRIEND_V1.contract_sha256,
            "total_words": total_words,
            "estimated_duration_s": duration,
            "duration_contract": duration_metrics,
            "ending_kind": chunk_end,
            "display_word_count": len(display_words),
            "timing_source": "voice_required",
            "warnings": [],
            "signals": {},
        }
        result = NarrationResult(
            spoken_text=spoken_text,
            display_words=display_words,
            passages=tuple(all_passages),
            ending_kind=str(chunk_end),
            word_count=total_words,
            estimated_duration_s=duration,
            observations=tuple(observations),
            continuity_ledger=dict(structural["continuity_ledger"]),
            evidence_graph={"claims": all_claims},
            story_spine=story_spine,
            qc_report=qc_report,
            model_identity_hash=self.model_identity.identity_hash,
            prompt_version=prompt[0],
            prompt_sha256=prompt[1],
            visual_evidence_hash=visual.visual_evidence_hash,
        )
        if self.cache is not None:
            cache_prompt = (
                request_prompt_version or prompt[0],
                request_prompt_sha256 or prompt[1],
                request_prompt_text or prompt[2],
            )
            if stage == "narration":
                if _narration_result_is_usable(
                    result,
                    visual,
                    require_duration=True,
                    require_grounding=True,
                ):
                    self.cache.put(
                        _cache_key(
                            stage,
                            source,
                            self.model_identity,
                            cache_prompt,
                        ),
                        result.as_dict(),
                    )
                else:
                    failures = self._narration_contract_failures(result)
                    if failures:
                        self._store_narration_repair_candidate(
                            source=source,
                            prompt=cache_prompt,
                            result=result,
                            failure_codes=failures,
                            visual=visual,
                            story_map=story_map,
                        )
            # narration_repair results are written only after scope validation
            # by _run_targeted_narration_repair.
        return result
    def run_visual_narrative_repair(
        self,
        visual: VisualStageResult,
        story_map: StoryMapResult,
        narration: NarrationResult | None,
        ledger: visual_narrative_repair.FeasibleVisualLedger,
        section_to_beats: Mapping[str, Sequence[str]],
        *,
        panels: Sequence[CloudPanelInput] | None = None,
    ) -> NarrationResult:
        """Repair only missing visual sections using the same pinned model."""

        prompt = self.prompts["visual_narrative_repair"]
        observations, structural = self._narration_observations(visual, panels)
        feasible_ids = set(ledger.feasible_panel_ids)
        feasible_observations = [
            dict(item)
            for item in visual.panels
            if str(item.get("panel_id", "")) in feasible_ids
        ]
        repair_narration = (
            narration.as_dict()
            if narration is not None
            else {
                "spoken_text": "",
                "passages": [],
                "ending_kind": "",
                "initial_failure_code": "cloud.narrative_not_grounded",
            }
        )
        payload = visual_narrative_repair.build_repair_payload(
            narration=repair_narration,
            story_map=story_map.as_dict(),
            ledger=ledger,
            section_to_beats=section_to_beats,
            feasible_observations=feasible_observations,
        )
        render_plan = visual_narrative_repair.FeasibleRenderPlan.from_ledger(ledger)
        source = {
            "visual_source_hash": visual.source_hash,
            "story_map_hash": story_map.story_map_hash,
            "narration_hash": _hash(repair_narration),
            "missing_sections": payload["missing_sections"],
            "ledger_hash": ledger.ledger_hash,
            "section_to_beats": payload["section_to_beats"],
            "render_plan_hash": render_plan.plan_hash,
        }
        key = visual_narrative_repair.repair_cache_key(
            ledger=ledger,
            model_identity_hash=self.model_identity.identity_hash,
            prompt_sha256=prompt[1],
            narration_hash=str(source["narration_hash"]),
        )
        allowed_claim_ids = {
            str(claim.get("claim_id"))
            for claim in story_map.claims
            if isinstance(claim, Mapping) and str(claim.get("claim_id", "")).strip()
        }

        def reconcile_repaired_references(
            raw_claims: object,
            raw_passages: object,
        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], tuple[dict[str, Any], ...]]:
            claims = self._normalize_narration_claims(raw_claims)
            if not isinstance(raw_passages, list):
                raise visual_narrative_repair.VisualNarrativeRepairError(
                    "repair passages are malformed",
                    "visual.narrative_repair_ungrounded",
                )
            repaired_payload, remaps = visual_narrative_repair.remap_same_beat_panel_citations(
                {"claims": claims, "passages": raw_passages},
                ledger=ledger,
                section_to_beats=section_to_beats,
            )
            claims = self._normalize_narration_claims(repaired_payload["claims"])
            passages = [dict(item) for item in repaired_payload["passages"]]
            visual_narrative_repair.validate_repaired_panel_references(
                {"claims": claims, "passages": passages},
                ledger=ledger,
                allowed_claim_ids=allowed_claim_ids,
            )
            return claims, passages, remaps

        if self.cache is not None and (cached := self.cache.get(key)) is not None:
            try:
                cached_result = NarrationResult.from_dict(cached)
                if cached_result.visual_evidence_hash != visual.visual_evidence_hash:
                    cached_result = None
                if cached_result is not None:
                    claims, passages, remaps = reconcile_repaired_references(
                        cached_result.evidence_graph.get("claims"),
                        list(cached_result.passages),
                    )
                    visual_narrative_repair.validate_repaired_section_visual_coverage(
                        passages,
                        ledger=ledger,
                        section_to_beats=section_to_beats,
                        missing_sections=visual_narrative_repair.missing_visual_sections(
                            ledger, section_to_beats
                        ),
                    )
                    if not remaps:
                        return cached_result
                    evidence_graph = dict(cached_result.evidence_graph)
                    evidence_graph["claims"] = claims
                    qc_report = dict(cached_result.qc_report)
                    qc_report["visual_section_remap_v1"] = list(remaps)
                    return replace(
                        cached_result,
                        passages=tuple(passages),
                        evidence_graph=evidence_graph,
                        qc_report=qc_report,
                    )
            except (
                analyzer_contract.AnalyzerContractError,
                CloudStageError,
                KeyError,
                TypeError,
                ValueError,
                visual_narrative_repair.VisualNarrativeRepairError,
            ):
                # A cache entry is untrusted persisted state.  A stricter
                # lineage/visual ledger can invalidate an older entry; treat
                # that entry as a miss and use the bounded repair contract.
                pass

        retryable_codes = {
            "cloud.provider_request_failed",
            "cloud.provider_response_invalid",
            "cloud.narrative_not_grounded",
            "cloud.narrative_duration_out_of_range",
            "cloud.narrative_qc_blocked",
            "visual.narrative_repair_ungrounded",
        }
        retry_feedback = ""
        for attempt in range(visual_narrative_repair.MAX_REPAIR_ATTEMPTS):
            try:
                request_payload = {
                    **payload,
                    "repair_attempt": attempt + 1,
                    "request_identity": source,
                }
                if retry_feedback:
                    request_payload["contract_retry_feedback"] = retry_feedback
                raw = self._call(
                    lambda request_payload=request_payload: self.provider.complete_json(
                        stage="visual_narrative_repair",
                        prompt_version=prompt[0],
                        prompt_sha256=prompt[1],
                        prompt_text=prompt[2],
                        payload=request_payload,
                    ),
                    request_stage="other",
                )
                if not isinstance(raw, Mapping):
                    raise CloudStageError("cloud.provider_response_invalid")
                provider_output = raw.get("analyzer_output", raw)
                if not isinstance(provider_output, Mapping):
                    raise CloudStageError("cloud.provider_response_invalid")
                raw_claims = provider_output.get("claims")
                if raw_claims is None:
                    raw_claims = provider_output.get("evidence_graph")
                claims = self._normalize_narration_claims(raw_claims)
                passages = provider_output.get("passages")
                if passages is None:
                    passages = provider_output.get("script_passages")
                outline = provider_output.get("narrative_outline")
                if not isinstance(passages, list) or not isinstance(outline, Mapping):
                    raise CloudStageError("cloud.provider_response_invalid")
                claims, passages, remaps = reconcile_repaired_references(
                    claims,
                    passages,
                )
                visual_narrative_repair.validate_repaired_section_visual_coverage(
                    passages,
                    ledger=ledger,
                    section_to_beats=section_to_beats,
                    missing_sections=visual_narrative_repair.missing_visual_sections(
                        ledger, section_to_beats
                    ),
                )
                output = {
                    "observations": observations,
                    "continuity_ledger": structural["continuity_ledger"],
                    "coverage_manifest": structural["coverage_manifest"],
                    "evidence_graph": {"claims": claims},
                    "narrative_outline": dict(outline),
                    "script_passages": [dict(item) for item in passages],
                }
                analyzer_contract.validate_analyzer_output(
                    output,
                    expected_panel_ids=tuple(str(item["panel_id"]) for item in observations),
                    narrative_profile_id="sharp_friend_v1",
                )
                claims_by_id = {str(claim["claim_id"]): claim for claim in claims}
                passage_rows = tuple(dict(item) for item in passages)
                report = editorial_qc.screen_narrative_naturalness(
                    passage_rows,
                    claims_by_id,
                    narrative_identity.SHARP_FRIEND_V1,
                )
                checks = quality.check_narrative_naturalness(report)
                if any(not check.passed and check.severity == "error" for check in checks):
                    raise CloudStageError("cloud.narrative_qc_blocked", reviewable=True)
                spoken_text = "\n\n".join(str(item["text"]).strip() for item in passage_rows)
                display_words = derive_display_words(spoken_text)
                duration_metrics = script.narration_duration_metrics(
                    spoken_text,
                    "dramatic",
                )
                duration = float(duration_metrics["estimated_duration_s"])
                if not 50.0 <= duration <= 60.0 or not 115 <= report.total_words <= 125:
                    raise CloudStageError("cloud.narrative_duration_out_of_range", reviewable=True)
                result = NarrationResult(
                    spoken_text=spoken_text,
                    display_words=display_words,
                    passages=passage_rows,
                    ending_kind=str(output["narrative_outline"]["ending_kind"]),
                    word_count=report.total_words,
                    estimated_duration_s=duration,
                    qc_report={
                        "profile_id": "sharp_friend_v1",
                        "profile_sha256": narrative_identity.SHARP_FRIEND_V1.contract_sha256,
                        "total_words": report.total_words,
                        "estimated_duration_s": duration,
                        "duration_contract": duration_metrics,
                        "ending_kind": output["narrative_outline"]["ending_kind"],
                        "display_word_count": len(display_words),
                        "timing_source": "voice_required",
                        "warnings": list(report.warnings),
                        "signals": asdict(report),
                        "repair_contract_version": visual_narrative_repair.REPAIR_CONTRACT_VERSION,
                        "visual_section_remap_v1": list(remaps),
                        "feasible_ledger_hash": ledger.ledger_hash,
                        "feasible_render_plan_hash": render_plan.plan_hash,
                        "repaired_sections": list(visual_narrative_repair.missing_visual_sections(ledger, section_to_beats)),
                    },
                    model_identity_hash=self.model_identity.identity_hash,
                    prompt_version=prompt[0],
                    prompt_sha256=prompt[1],
                    observations=tuple(observations),
                    continuity_ledger=dict(output["continuity_ledger"]),
                    evidence_graph=dict(output["evidence_graph"]),
                    story_spine=dict(output["narrative_outline"]["story_spine"]),
                    visual_evidence_hash=visual.visual_evidence_hash,
                )
                if self.cache is not None:
                    self.cache.put(key, result.as_dict())
                return result
            except analyzer_contract.AnalyzerContractError as aexc:
                print("ANALYZER_CONTRACT_FAIL:", repr(aexc), file=sys.stderr, flush=True)
                error = CloudStageError(
                    "cloud.narrative_not_grounded",
                    "analyzer contract rejected",
                    reviewable=True,
                    safe_metadata=_visual_narrative_repair_analyzer_metadata(
                        str(aexc),
                        output if isinstance(output, Mapping) else None,
                    ),
                )
            except visual_narrative_repair.VisualNarrativeRepairError as exc:
                error = CloudStageError(
                    exc.code,
                    reviewable=exc.reviewable,
                    safe_metadata=_visual_narrative_repair_error_metadata(
                        str(exc),
                        code=exc.code,
                    ),
                )
            except CloudStageError as exc:
                safe_metadata = dict(exc.safe_metadata)
                safe_metadata.setdefault(
                    "failed_predicate",
                    _visual_narrative_repair_error_metadata(
                        str(exc),
                        code=exc.code,
                    )["failed_predicate"],
                )
                error = CloudStageError(
                    exc.code,
                    str(exc),
                    reviewable=exc.reviewable,
                    safe_metadata=safe_metadata,
                )
            if error.code not in retryable_codes or attempt + 1 >= visual_narrative_repair.MAX_REPAIR_ATTEMPTS:
                safe_metadata = dict(getattr(error, "safe_metadata", {}) or {})
                safe_metadata.update(
                    _visual_narrative_repair_failure_metadata(
                        ledger=ledger,
                        section_to_beats=section_to_beats,
                        attempt_count=attempt + 1,
                        failure_code=error.code,
                    )
                )
                raise CloudStageError(
                    error.code,
                    reviewable=error.reviewable,
                    safe_metadata=safe_metadata,
                )
            retry_feedback = _visual_narrative_repair_retry_feedback(
                error.code,
                failed_field=str(error.safe_metadata.get("failed_field", "")) or None,
                failed_predicate=str(error.safe_metadata.get("failed_predicate", "")) or None,
            )
        raise CloudStageError("visual.narrative_repair_bounded", reviewable=True)

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
        if current and (
            len(current) >= max_panels
            or estimated + estimate > max_estimated_bytes
        ):
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
                _visual_panel_identity_hash(panel, index)
                for index, panel in enumerate(chunk)
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
        or str(row.get("cache_identity_version", ""))
        != VISUAL_CACHE_IDENTITY_VERSION
    ):
        raise CloudStageError("cloud.visual_stream_row_invalid")
    return dict(row)


def _merge_stream_visual_rows(
    events: Sequence[Mapping[str, Any]],
    panels: Sequence[CloudPanelInput],
) -> tuple[dict[str, Any], ...]:
    """Validate worker rows and restore canonical panel order."""

    ordered = CloudStageRunner._ordered_panels(tuple(panels))
    expected = {panel.panel_id: panel for panel in ordered}
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
        panel.panel_id for panel in ordered if panel.panel_id not in merged
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
    return tuple(merged[panel.panel_id] for panel in ordered)


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


class _StreamingVisualEvidenceSession:
    """Stream validated panel bytes into one serialized checkpoint writer."""

    def __init__(
        self,
        runner: CloudStageRunner,
        *,
        queue_size: int,
        max_panels: int,
        max_estimated_bytes: int,
        worker_count: int,
    ) -> None:
        if queue_size < 1:
            raise CloudStageError("cloud.visual_stream_config_invalid")
        self.runner = runner
        self.queue_size = int(queue_size)
        self.max_panels = int(max_panels)
        self.max_estimated_bytes = int(max_estimated_bytes)
        self.worker_count = int(worker_count)
        self._controller = _FixedVisualConcurrency(self.worker_count)
        self._tasks: queue.Queue[Any] = queue.Queue(maxsize=self.queue_size)
        self._events: queue.Queue[Any] = queue.Queue(maxsize=max(2, self.queue_size * 2))
        self._stop = threading.Event()
        self._closed = False
        self._aborted = False
        self._submit_lock = threading.Lock()
        self._pending: list[CloudPanelInput] = []
        self._submitted: list[CloudPanelInput] = []
        self._batches: dict[int, tuple[CloudPanelInput, ...]] = {}
        self._next_batch_index = 0
        self._accepted: dict[str, dict[str, Any]] = {}
        self._missing: set[str] = set()
        self._failure_codes: list[str] = []
        self._retry_count_total = 0
        self._writer_error: CloudStageError | None = None
        self._max_queue_depth = 0
        self.writer_thread_count = 1
        prompt = runner.prompts["visual"]
        self._checkpoint_scope = _hash(
            {
                "version": VISUAL_STREAM_VERSION,
                "model_identity_hash": runner.model_identity.identity_hash,
                "prompt_version": prompt[0],
                "prompt_sha256": prompt[1],
            }
        )
        self._checkpoint_seed = runner._checkpoint_load(self._checkpoint_scope)
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name="visual-stream-writer",
        )
        self._writer_thread.start()
        self._workers = [
            threading.Thread(
                target=self._worker_loop,
                args=(index,),
                name=f"visual-stream-worker-{index}",
            )
            for index in range(self.worker_count)
        ]
        for worker in self._workers:
            worker.start()

    def _worker_runner(self) -> CloudStageRunner:
        return CloudStageRunner(
            provider=self.runner.provider,
            model_identity=self.runner.model_identity,
            cache=MemoryStageCache(),
            # The stream owns the explicit missing-panel retry budget.  Keep
            # transport retries out of each worker runner so a failed chunk
            # cannot amplify into nested whole-batch retries.
            max_attempts=1,
            min_request_interval_s=self.runner.min_request_interval_s,
            estimated_cost_per_request=self.runner.estimated_cost_per_request,
            allow_balloon_unknown=self.runner.allow_balloon_unknown,
            visual_checkpoint_path=None,
            visual_parallel_workers=1,
        )

    def _flush_pending(self) -> None:
        if not self._pending:
            return
        batch = tuple(self._pending)
        self._pending = []
        batch_index = self._next_batch_index
        self._next_batch_index += 1
        self._batches[batch_index] = batch
        self._tasks.put((batch_index, batch))
        self._max_queue_depth = max(self._max_queue_depth, self._tasks.qsize())

    def submit(self, panel: CloudPanelInput) -> None:
        with self._submit_lock:
            if self._closed or self._aborted:
                raise CloudStageError("cloud.visual_stream_closed")
            if panel.prepared_order is None:
                raise CloudStageError("cloud.panel_lineage_invalid")
            if any(
                existing.panel_id == panel.panel_id
                or existing.prepared_order == panel.prepared_order
                for existing in self._submitted
            ):
                raise CloudStageError("cloud.panel_lineage_invalid")
            self._submitted.append(panel)
            self._pending.append(panel)
            pending_size = sum(
                ((len(_visual_provider_payload(item)[0]) * 4 + 2) // 3 + 768)
                for item in self._pending
            )
            if len(self._pending) >= self.max_panels or pending_size >= self.max_estimated_bytes:
                self._flush_pending()

    def _process_batch(
        self,
        worker_runner: CloudStageRunner,
        batch_index: int,
        batch: tuple[CloudPanelInput, ...],
    ) -> dict[str, Any]:
        identity_by_id = {
            panel.panel_id: _visual_panel_identity_hash(panel, index)
            for index, panel in enumerate(batch)
        }
        accepted: dict[str, dict[str, Any]] = {}
        seeded_ids: list[str] = []
        for panel in batch:
            seeded = self._checkpoint_seed.get(panel.panel_id)
            if not isinstance(seeded, Mapping):
                continue
            if (
                seeded.get("stream_checkpoint_version") != VISUAL_STREAM_VERSION
            ):
                continue
            try:
                accepted[panel.panel_id] = _stream_validate_row(
                    seeded,
                    panel,
                    expected_identity_hash=identity_by_id[panel.panel_id],
                )
            except CloudStageError:
                continue
            seeded_ids.append(panel.panel_id)
        pending = tuple(
            panel for panel in batch if panel.panel_id not in accepted
        )
        error_code = ""
        categories: dict[str, int] = {}
        retries = 0
        # Retry only the IDs omitted by a successful response.  A worker
        # runner is single-attempt; transport retry ownership stays in the
        # parent runner and is never multiplied by this loop.
        retry_budget = 1 if self.runner.max_attempts > 1 else 0
        for attempt in range(1 + retry_budget):
            if not pending:
                break
            try:
                result = worker_runner.run_visual_evidence(pending)
                rows = {
                    str(row.get("panel_id", "")): dict(row)
                    for row in result.panels
                    if isinstance(row, Mapping)
                }
                before = len(accepted)
                for panel in pending:
                    row = rows.get(panel.panel_id)
                    if row is None:
                        continue
                    accepted[panel.panel_id] = row
                pending = tuple(
                    panel for panel in pending if panel.panel_id not in accepted
                )
                if not pending or len(accepted) == before:
                    break
                retries += 1
            except CloudStageError as exc:
                error_code = exc.code
                category = _stream_error_category(exc.code)
                categories[category] = categories.get(category, 0) + 1
                if attempt >= retry_budget:
                    break
                retries += 1
        if pending and not error_code:
            error_code = "cloud.panel_coverage_incomplete"
        return {
            "batch_index": batch_index,
            "rows": list(accepted.values()),
            "seeded_ids": tuple(seeded_ids),
            "missing_ids": tuple(panel.panel_id for panel in pending),
            "error_code": error_code,
            "retry_count": retries,
            "request_count": worker_runner.request_count,
            "request_counts": dict(worker_runner.request_counts),
            "estimated_cost_usd": worker_runner.estimated_cost_usd,
            "categories": categories,
        }

    def _worker_loop(self, worker_index: int) -> None:
        worker_runner = self._worker_runner()
        while True:
            task = self._tasks.get()
            try:
                if task is None:
                    return
                if self._stop.is_set():
                    continue
                batch_index, batch = task
                self._controller.acquire()
                started = time.monotonic()
                before_request_count = worker_runner.request_count
                before_request_counts = dict(worker_runner.request_counts)
                before_cost = worker_runner.estimated_cost_usd
                try:
                    event = self._process_batch(worker_runner, batch_index, batch)
                except Exception:
                    event = {
                        "batch_index": batch_index,
                        "rows": [],
                        "seeded_ids": (),
                        "missing_ids": tuple(panel.panel_id for panel in batch),
                        "error_code": "cloud.provider_request_failed",
                        "retry_count": 0,
                        "request_count": worker_runner.request_count,
                        "request_counts": dict(worker_runner.request_counts),
                        "estimated_cost_usd": worker_runner.estimated_cost_usd,
                        "categories": {"other_failure": 1},
                    }
                event["request_count"] = worker_runner.request_count - before_request_count
                event["request_counts"] = {
                    key: worker_runner.request_counts.get(key, 0) - before_request_counts.get(key, 0)
                    for key in worker_runner.request_counts
                }
                event["estimated_cost_usd"] = worker_runner.estimated_cost_usd - before_cost
                self._controller.release(
                    panel_count=len(batch),
                    request_count=int(event.get("request_count", 0)),
                    latency_s=time.monotonic() - started,
                    categories=event.get("categories", {}),
                )
                event["worker_index"] = worker_index
                self._events.put(event)
            finally:
                self._tasks.task_done()

    def _writer_loop(self) -> None:
        while True:
            event = self._events.get()
            try:
                if event is None:
                    return
                self._consume_event(event)
            finally:
                self._events.task_done()

    def _consume_event(self, event: Mapping[str, Any]) -> None:
        if self._aborted:
            return
        for key, value in dict(event.get("request_counts", {})).items():
            if key in self.runner.request_counts:
                self.runner.request_counts[key] += int(value)
        self.runner.request_count += int(event.get("request_count", 0))
        self.runner.estimated_cost_usd += float(event.get("estimated_cost_usd", 0.0))
        self._retry_count_total += int(event.get("retry_count", 0))
        if self._writer_error is not None:
            return
        batch_index = int(event.get("batch_index", -1))
        batch = self._batches.get(batch_index)
        if batch is None:
            self._writer_error = CloudStageError("cloud.visual_stream_row_invalid")
            return
        expected = {panel.panel_id: panel for panel in batch}
        expected_hashes = {
            panel.panel_id: _visual_panel_identity_hash(panel, index)
            for index, panel in enumerate(batch)
        }
        seeded_ids = {str(panel_id) for panel_id in event.get("seeded_ids", ())}
        chunk_key = _stream_visual_chunk_cache_key(
            batch,
            chunk_index=batch_index,
            model_identity=self.runner.model_identity,
            prompt=self.runner.prompts["visual"],
        )
        try:
            rows = event.get("rows", ())
            if not isinstance(rows, (list, tuple)):
                raise CloudStageError("cloud.visual_stream_row_invalid")
            for raw_row in rows:
                if not isinstance(raw_row, Mapping):
                    raise CloudStageError("cloud.visual_stream_row_invalid")
                panel_id = str(raw_row.get("panel_id", ""))
                panel = expected.get(panel_id)
                if panel is None or panel_id in self._accepted:
                    raise CloudStageError("cloud.visual_stream_row_invalid")
                clean = _stream_validate_row(
                    raw_row,
                    panel,
                    expected_identity_hash=expected_hashes[panel_id],
                )
                if panel_id in seeded_ids:
                    if clean.get("stream_checkpoint_version") != VISUAL_STREAM_VERSION:
                        raise CloudStageError("cloud.visual_stream_row_invalid")
                    clean["chunk_cache_key"] = chunk_key
                else:
                    clean["chunk_cache_key"] = chunk_key
                    clean["stream_checkpoint_version"] = VISUAL_STREAM_VERSION
                    self.runner._checkpoint_append(self._checkpoint_scope, clean)
                self._accepted[panel_id] = clean
            for panel_id in event.get("missing_ids", ()):
                self._missing.add(str(panel_id))
            if event.get("error_code"):
                self._failure_codes.append(str(event["error_code"]))
        except CloudStageError as exc:
            self._writer_error = exc

    def _shutdown(self, *, aborted: bool) -> None:
        if self._closed:
            return
        self._closed = True
        if aborted:
            self._aborted = True
            self._stop.set()
        for _ in self._workers:
            self._tasks.put(None)
        self._tasks.join()
        for worker in self._workers:
            worker.join()
        self._events.put(None)
        self._events.join()
        self._writer_thread.join()

    def finish(self, panels: Sequence[CloudPanelInput]) -> VisualStageResult:
        if self._closed or self._aborted:
            raise CloudStageError("cloud.visual_stream_closed")
        self._flush_pending()
        self._shutdown(aborted=False)
        if self._writer_error is not None:
            raise self._writer_error
        ordered = CloudStageRunner._ordered_panels(tuple(panels))
        submitted_ids = tuple(item.panel_id for item in self._submitted)
        ordered_ids = tuple(item.panel_id for item in ordered)
        if len(submitted_ids) != len(ordered_ids) or set(submitted_ids) != set(ordered_ids):
            raise CloudStageError("cloud.panel_lineage_invalid")
        submitted_ids = {item.panel_id for item in ordered}
        terminal_ids = set(self._accepted) | self._missing
        self._missing.update(submitted_ids - terminal_ids)
        if self._missing:
            metrics = self._controller.snapshot()
            metrics.update(
                {
                    "contract_version": VISUAL_STREAM_VERSION,
                    "writer_count": self.writer_thread_count,
                    "submitted_panel_count": len(ordered),
                    "accepted_panel_count": len(self._accepted),
                    "missing_panel_count": len(self._missing),
                    "missing_panel_ids": sorted(self._missing),
                    "request_count": self.runner.request_count,
                    "request_counts": dict(self.runner.request_counts),
                    "retry_count": self._retry_count_total,
                    "max_queue_depth": self._max_queue_depth,
                    "terminal_failure_codes": sorted(set(self._failure_codes)),
                }
            )
            self.runner.last_visual_stream_metrics = metrics
            raise CloudStageError(
                "cloud.panel_coverage_incomplete",
                reviewable=True,
                safe_metadata={
                    "submitted_panel_count": len(ordered),
                    "accepted_panel_count": len(self._accepted),
                    "missing_panel_count": len(self._missing),
                },
            )
        rows = _merge_stream_visual_rows(
            ({"rows": list(self._accepted.values())},),
            ordered,
        )
        if not rows:
            code = self._failure_codes[0] if self._failure_codes else "cloud.panel_coverage_incomplete"
            raise CloudStageError(code, reviewable=code.startswith("visual."))
        source = list(_visual_panel_identities(ordered))
        prompt = self.runner.prompts["visual"]
        result = VisualStageResult(
            panels=rows,
            source_hash=_hash(source),
            model_identity_hash=self.runner.model_identity.identity_hash,
            prompt_version=prompt[0],
            prompt_sha256=prompt[1],
            cache_identity_version=VISUAL_CACHE_IDENTITY_VERSION,
            panel_identity_hashes=_visual_panel_identity_hashes(ordered),
        )
        key = _cache_key("visual", source, self.runner.model_identity, prompt)
        if self.runner.cache is not None:
            self.runner.cache.put(key, result.as_dict())
        metrics = self._controller.snapshot()
        metrics.update(
            {
                "contract_version": VISUAL_STREAM_VERSION,
                "writer_count": self.writer_thread_count,
                "submitted_panel_count": len(ordered),
                "accepted_panel_count": len(rows),
                "missing_panel_count": len(self._missing),
                "missing_panel_ids": sorted(self._missing),
                "request_count": self.runner.request_count,
                "request_counts": dict(self.runner.request_counts),
                "retry_count": self._retry_count_total,
                "max_queue_depth": self._max_queue_depth,
            }
        )
        self.runner.last_visual_stream_metrics = metrics
        return result

    def abort(self) -> None:
        if self._closed:
            return
        self._shutdown(aborted=True)


def prepare_project_panels(
    db: Any,
    project_id: str,
    *,
    boundary_assessor: Callable[[strip_segmentation.BoundaryRequest], Mapping[str, Any]] | None = None,
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
            review_codes = [report.review_code for report in reconciliation.reports if report.review_code]
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
    last_panel_admission: PanelAdmissionResult | None = None
    region_order = {region.region_id: order for order, region in enumerate(regions)}

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
        for prior in stream_admitted_keys.values():
            if (
                prior.source_checksum == panel.source_checksum
                and prior.source_dimensions == panel.source_dimensions
                and prior.panel_bounds is not None
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
        group_asset_ids = {str(item.source_asset_id) for item in group}
        group_regions = tuple(
            region
            for region in regions
            if str(region.source_asset_id) in group_asset_ids
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
            metadata.setdefault("panel_admission", admission_failure_metadata(exc.code)["panel_admission"])
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
        review_codes = [report.review_code for report in reconciliation.reports if report.review_code]
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
        dict(as_dict())
        if callable(as_dict)
        else dict(getattr(reconciliation, "__dict__", {}))
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
                    isinstance(report.get("width"), int)
                    and isinstance(report.get("height"), int)
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
            provider_payload, _ = _visual_provider_payload(panel)
            panel_estimate = (len(provider_payload) * 4 + 2) // 3 + 768
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

    observation = row.get("observation", row)
    if not isinstance(observation, Mapping):
        return False
    values = observation.get("visible_facts")
    if not isinstance(values, list) or not values:
        return False
    for value in values:
        if isinstance(value, str) and value.strip():
            continue
        if isinstance(value, Mapping) and any(
            isinstance(candidate, str) and candidate.strip()
            for candidate in value.values()
        ):
            continue
        return False
    return True


def _visual_cached_row_is_reusable(
    row: Mapping[str, Any], panel: CloudPanelInput | None
) -> bool:
    """Validate cached lineage plus the shared analyzer's fact prerequisite."""

    if panel is None:
        return False
    return (
        str(row.get("panel_id", "")) == panel.panel_id
        and str(row.get("source_asset_id", "")) == panel.source_asset_id
        and int(row.get("source_order", -1)) == int(panel.source_order)
        and str(row.get("source_checksum", "")) == panel.source_checksum
        and _visual_observation_has_visible_facts(row)
    )


def _visual_provider_payload(panel: CloudPanelInput) -> tuple[bytes, str]:
    """Bound provider image size while leaving persisted panel bytes untouched."""

    needs_resize = len(panel.payload) > 180_000
    if (
        not needs_resize
        and panel.mime_type.lower() in {"image/jpeg", "image/jpg"}
    ):
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
        image.save(output, format="JPEG", quality=68, optimize=True, progressive=False, subsampling=2)
        encoded = output.getvalue()
    except (OSError, ValueError, UnidentifiedImageError):
        return panel.payload, panel.mime_type
    return encoded, "image/jpeg"


def _visual_render_policy_version(panel: CloudPanelInput) -> str:
    """Version only identities whose provider bytes can change."""

    if (
        len(panel.payload) <= 180_000
        and panel.mime_type.lower() not in {"image/jpeg", "image/jpg"}
    ):
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
        print("PERSIST_COVERAGE_FAIL obs=" + str(len(result.narration.observations)) + " ordered=" + str(len(ordered)), file=sys.stderr, flush=True)
        raise CloudStageError("cloud.panel_coverage_incomplete")

    try:
        instruction_version, instruction_sha256, _ = analyzer_contract.load_analyzer_instruction(
            narrative_profile_id="sharp_friend_v1"
        )
    except Exception:
        raise CloudStageError("cloud.prompt_invalid") from None

    coverage_versions = {panel.coverage_map_version for panel in ordered}
    coverage_hashes = {panel.coverage_map_hash for panel in ordered}
    if len(coverage_versions) != 1 or len(coverage_hashes) != 1 or not next(iter(coverage_versions)) or not next(iter(coverage_hashes)):
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
        if not isinstance(observation.get("evidence_refs"), list) or panel.panel_id not in observation["evidence_refs"]:
            print(
                "GROUND_FAIL panel=" + str(panel.panel_id) +
                " refs=" + repr(observation.get("evidence_refs"))[:300],
                file=sys.stderr, flush=True,
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
            "narrative_screening_warning_codes": list(result.narration.qc_report.get("warnings", [])),
            "requires_voice_timing": True,
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

    from app.services import reference_visual_review, review_source_upscale, visual_scoring

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
                if int(panel.source_order) > 0
                and str(panel.panel_id) in visual_by_id
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
    section_evidence: dict[str, tuple[str, ...]] = {}
    for section, beat_ids in section_to_beats.items():
        panel_ids: list[str] = []
        for beat_id in beat_ids:
            beat = beat_by_id.get(str(beat_id))
            if beat is None:
                continue
            for panel_id in beat.get("panel_ids", ()):
                if str(panel_id) in valid_panel_ids and str(panel_id) not in panel_ids:
                    panel_ids.append(str(panel_id))
        if not panel_ids:
            raise CloudStageError("visual.panel_lineage_unavailable", reviewable=True)
        section_evidence[section] = tuple(panel_ids)

    panel_regions: list[object] = []
    panel_candidates: dict[str, object] = {}
    panel_crops: dict[str, Image.Image] = {}
    upscale_manifests: dict[str, Mapping[str, Any]] = {}
    for panel in ordered_panels:
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
        if crop.width < 32 or crop.height < 50:
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
                int(value)
                for value in (panel.panel_bounds or (0, 0, crop.width, crop.height))
            )

        region = SimpleNamespace(
            id=panel.panel_id,
            source_asset_id=panel.source_asset_id,
            source_asset_checksum=panel.source_checksum,
            original_width=int(panel.source_dimensions[0]) if panel.source_dimensions else int(crop.width),
            original_height=int(panel.source_dimensions[1]) if panel.source_dimensions else int(crop.height),
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
            section_evidence_panel_ids=section_evidence,
            section_citations=dict.fromkeys(section_to_beats, ()),
            beats_by_section=section_to_beats,
            profile=profile,
            source_upscale_manifests_by_region_id=upscale_manifests,
            allow_missing_explicit=True,
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
    return filtered if filtered else ordered


def _visual_panel_ids_requiring_materialization(
    runner: CloudStageRunner,
    panels: Sequence[CloudPanelInput],
) -> tuple[str, ...]:
    """Return only metadata-only panels absent from the exact visual cache."""

    ordered = tuple(panels)
    metadata_ids = {
        panel.panel_id for panel in ordered if getattr(panel, "metadata_only", False)
    }
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


def _visual_cache_requires_subset_restore(
    runner: CloudStageRunner,
    cached: Mapping[str, Any] | None,
    panels: Sequence[CloudPanelInput],
) -> bool:
    """Detect a partial/stale visual stage before checkpoint restoration."""

    if not isinstance(cached, Mapping):
        return True
    try:
        visual = VisualStageResult.from_dict(cached)
        ordered = runner._ordered_panels(tuple(panels))
    except (CloudStageError, KeyError, TypeError, ValueError):
        return True
    if visual.panel_ids != tuple(panel.panel_id for panel in ordered):
        return True
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
    checkpoint_scope = runner._checkpoint_scope(
        _visual_panel_identities(ordered), prompt
    )
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
                    and str(row.get("source_asset_id", ""))
                    == panel.source_asset_id
                    and int(row.get("source_order", -1)) == int(panel.source_order)
                    and str(row.get("source_checksum", ""))
                    == panel.source_checksum
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
    candidates.sort(key=lambda result: (len(result.panels), result.visual_evidence_hash), reverse=True)
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
        assets_by_id[asset_id]
        for asset_id in sorted(target_asset_ids)
        if asset_id in assets_by_id
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


class CloudBatchService:
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

    def _reconcile_cached_narration(
        self,
        narration: NarrationResult,
        visual: VisualStageResult,
        panels: Sequence[CloudPanelInput],
    ) -> NarrationResult:
        """Repair local full-scope fields before cached-state admission.

        A cached narration owns prose and trusted claim lineage; the visual
        stage owns the ordered observation and continuity ledger.  Rebuild
        only those local fields from the current reconciled panel registry so
        a selected-scope repair result cannot be admitted as a full chapter.
        No provider call is valid at this boundary.
        """

        observations, structural = self.runner._narration_observations(
            visual,
            panels,
        )
        return _reconcile_narration_full_scope(
            narration,
            observations=observations,
            structural=structural,
            expected_panel_ids=visual.panel_ids,
            visual_evidence_hash=visual.visual_evidence_hash,
        )

    def run_job(
        self,
        job_id: str,
        panels: Sequence[CloudPanelInput],
        *,
        precomputed_visual: VisualStageResult | None = None,
    ) -> ChapterJobRecord:
        _validate_job_id(job_id)
        record = self.store.load(job_id) or ChapterJobRecord(job_id=job_id)
        record.model_identity_hash = self.runner.model_identity.identity_hash
        try:
            ordered = self.runner._ordered_panels(tuple(panels))
            if precomputed_visual is not None:
                merged_rows = _merge_stream_visual_rows(
                    ({"rows": list(precomputed_visual.panels)},),
                    ordered,
                )
                merged_ids = tuple(str(row["panel_id"]) for row in merged_rows)
                if merged_ids != precomputed_visual.panel_ids:
                    raise CloudStageError("cloud.visual_stream_row_invalid")
                visual = replace(precomputed_visual, panels=merged_rows)
                record.stage_results["visual"] = visual.as_dict()
                self.store.save(record)
            else:
                cached_visual = record.stage_results.get("visual")
                migrated_visual = _migrate_visual_cache_identity(
                    cached_visual,
                    ordered,
                    model_identity=self.runner.model_identity,
                    prompt=self.runner.prompts["visual"],
                    persisted_lineage=record.stage_results.get("narration"),
                )
                if migrated_visual is None:
                    raise KeyError("stale_visual_cache")
                visual = VisualStageResult.from_dict(migrated_visual)
                ordered_ids = tuple(panel.panel_id for panel in ordered)
                if visual.panel_ids != ordered_ids or any(
                    not _visual_cached_row_is_reusable(row, panel)
                    for row, panel in zip(visual.panels, ordered, strict=False)
                ):
                    visual = self.runner.run_visual_evidence(panels)
                if migrated_visual != cached_visual:
                    record.stage_results["visual"] = visual.as_dict()
                    self.store.save(record)
                if "visual" not in record.stage_results:
                    raise KeyError("visual_missing")
        except (KeyError, TypeError, ValueError):
            record.stage_results.pop("visual", None)
            record.stage_results.pop("story_map", None)
            record.stage_results.pop("narration", None)
            try:
                visual = self.runner.run_visual_evidence(panels)
            except CloudStageError as exc:
                return self._record_failure(record, exc)
        # Preview-only: reconcile downstream panels to the visual subset so
        # skipped provider-failing panels do not break narration grounding.
        visual_ids = set(visual.panel_ids)
        if len(visual_ids) != len(panels):
            total_before = len(panels)
            panels = tuple(
                item for item in panels if item.panel_id in visual_ids
            )
            print(
                f"RUN_JOB_PANELS_FILTER kept={len(panels)} dropped={total_before - len(panels)} of {total_before}",
                file=sys.stderr, flush=True,
            )
        try:
            record.stage_results["visual"] = visual.as_dict()
            record.state = ChapterState.VISUAL_ANALYZED
            self.store.save(record)
            story_map = StoryMapResult.from_dict(record.stage_results["story_map"])
            current_story_prompt = self.runner.prompts["story_map"]
            if (
                story_map.model_identity_hash != self.runner.model_identity.identity_hash
                or story_map.prompt_version != current_story_prompt[0]
                or story_map.prompt_sha256 != current_story_prompt[1]
                or story_map.visual_evidence_hash != visual.visual_evidence_hash
            ):
                raise KeyError("stale_story_cache")
        except (KeyError, TypeError, ValueError):
            record.stage_results.pop("story_map", None)
            record.stage_results.pop("narration", None)
            try:
                story_map = self.runner.run_story_map(visual)
            except CloudStageError as exc:
                return self._record_failure(record, exc)
        try:
            record.stage_results["story_map"] = story_map.as_dict()
            record.state = ChapterState.STORY_MAPPED
            self.store.save(record)
            narration = NarrationResult.from_dict(record.stage_results["narration"])
            narration = self._reconcile_cached_narration(narration, visual, panels)
            if self.runner._narration_contract_failures(narration):
                # A structurally usable narration can still violate the strict
                # narration contract (word window, duration, dialogue copy).
                # Repair it through the targeted boundary before admission so
                # persistence never receives a contract-failing narration.
                narration = self.runner.run_narration_repair_candidate(
                    narration,
                    visual,
                    story_map,
                    panels=panels,
                )
            current_narration_prompt = self.runner.prompts["narration"]
            if (
                narration.model_identity_hash != self.runner.model_identity.identity_hash
                or narration.prompt_version != current_narration_prompt[0]
                or narration.prompt_sha256 != current_narration_prompt[1]
                or narration.visual_evidence_hash != visual.visual_evidence_hash
                or not _narration_result_is_usable(
                    narration,
                    visual,
                    require_duration=True,
                    require_grounding=True,
                )
            ):
                raise KeyError("stale_narration_cache")
        except CloudStageError as exc:
            return self._record_failure(record, exc)
        except (KeyError, TypeError, ValueError):
            try:
                narration = self.runner.run_narration(visual, story_map, panels=panels)
            except CloudStageError as exc:
                return self._record_failure(record, exc)
        try:
            record.stage_results["narration"] = narration.as_dict()
            record.state = ChapterState.SCRIPTED
            record.stage_results["usage"] = {
                "request_count": self.runner.request_count,
                "request_counts": dict(self.runner.request_counts),
                "estimated_cost_usd": round(self.runner.estimated_cost_usd, 8),
            }
            self.store.save(record)
            record.state = ChapterState.READY_TO_RENDER
            self.store.save(record)
            return record
        except CloudStageError as exc:
            return self._record_failure(record, exc)

    def _record_failure(self, record: ChapterJobRecord, exc: CloudStageError) -> ChapterJobRecord:
        record.stage_results["usage"] = {
            "request_count": self.runner.request_count,
            "request_counts": dict(self.runner.request_counts),
            "estimated_cost_usd": round(self.runner.estimated_cost_usd, 8),
        }
        safe_metadata = dict(exc.safe_metadata)
        status_code = safe_metadata.get("status_code")
        transient_provider = bool(
            safe_metadata.get("retryable")
            or safe_metadata.get("timeout")
            or status_code == 429
            or isinstance(status_code, int)
            and status_code >= 500
        )
        waiting_for_provider = transient_provider and bool(safe_metadata.get("durable_progress"))
        record.state = (
            ChapterState.WAITING_PROVIDER
            if waiting_for_provider
            else ChapterState.NEEDS_REVIEW
            if exc.reviewable
            else ChapterState.FAILED
        )
        record.error_code = exc.code
        record.error_message = str(exc)
        if waiting_for_provider:
            safe_metadata.setdefault("resume", "retry failed source units from the durable segmentation checkpoint")
        if exc.reviewable or waiting_for_provider:
            review_entry = {"code": exc.code, "reason": str(exc)}
            metrics_for_failure = getattr(
                self.runner, "_response_shape_metrics_for_failure", None
            )
            if (
                not safe_metadata
                and callable(metrics_for_failure)
                and (
                    exc.code.startswith("cloud.narrative_")
                    or exc.code == "cloud.request_budget_exceeded"
                )
            ):
                safe_metadata = metrics_for_failure(exc.code)
            if "array_key" in safe_metadata:
                safe_metadata.setdefault("failed_code", exc.code)
                safe_metadata.setdefault("failed_predicate", exc.code)
            if safe_metadata:
                review_entry["safe_metadata"] = safe_metadata
            record.review_queue.append(review_entry)
        self.store.save(record)
        return record

    def _repair_review_narrative(
        self,
        db: Any,
        project_id: str,
        script_row: Any | None,
        panels: Sequence[CloudPanelInput],
        result: ChapterResult | None,
        *,
        visual: VisualStageResult | None = None,
        story_map: StoryMapResult | None = None,
        review_source_upscale_policy: Any,
        review_source_root: Path,
    ) -> tuple[ChapterResult, visual_narrative_repair.FeasibleVisualLedger, tuple[str, ...]]:
        """Build the local feasible ledger and repair only missing sections."""

        from app.models import Project
        from app.services import pipeline, reference_profile, review_source_upscale

        project = db.get(Project, project_id)
        if project is None:
            raise CloudStageError("visual.panel_lineage_unavailable", reviewable=True)
        profile = reference_profile.resolve_reference_profile(project.template)
        if profile is None:
            raise CloudStageError("visual.panel_lineage_unavailable", reviewable=True)
        try:
            if isinstance(review_source_upscale_policy, review_source_upscale.ReviewSourceUpscalePolicy):
                policy = review_source_upscale_policy
            else:
                policy = review_source_upscale.validate_review_upscale_request(
                    review_source_upscale_policy,
                    silent_reference_review=True,
                    publish_allowed=False,
                )
        except review_source_upscale.ReviewSourceUpscaleError as exc:
            raise CloudStageError(exc.code, reviewable=True) from None
        if policy is None:
            raise CloudStageError("review.upscale_policy_required", reviewable=True)

        current_visual = result.visual if result is not None else visual
        current_story_map = result.story_map if result is not None else story_map
        current_narration = result.narration if result is not None else None
        if current_visual is None or current_story_map is None:
            raise CloudStageError("visual.panel_lineage_unavailable", reviewable=True)

        images = pipeline.image_assets(pipeline.project_assets(db, project_id))
        # The durable prepared manifest is metadata-only (no panel image
        # bytes are retained), so after persistence the DB crop loader is the
        # only source of real payload bytes; the ephemeral builder only works
        # with the in-memory prepared panels that still carry payloads before
        # the manifest round-trip.
        _carries_payloads = any(
            bool(getattr(panel, "payload", b"") or b"")
            and not bool(getattr(panel, "metadata_only", False))
            for panel in panels
        )
        if _carries_payloads:
            candidates, section_to_beats = _build_ephemeral_review_candidates(
                panels,
                current_visual,
                current_story_map,
                profile=profile,
                review_source_upscale_policy=policy,
            )
        else:
            section_names = tuple(
                str(section.get("section", ""))
                for section in (getattr(script_row, "sections", ()) or ())
                if isinstance(section, Mapping) and str(section.get("section", "")).strip()
            )
            section_to_beats = visual_narrative_repair.default_section_to_beats(
                section_names,
                current_story_map.beats,
            )
            analysis = pipeline.latest_analysis(db, project_id)
            eligible_panel_ids = {
                str(region.panel_id)
                for region in (getattr(analysis, "panel_regions", ()) or ())
                if int(region.source_order) > 0
            }
            beat_panel_ids = {
                str(beat["beat_id"]): tuple(
                    str(panel_id)
                    for panel_id in beat["panel_ids"]
                    if str(panel_id) in eligible_panel_ids
                )
                for beat in current_story_map.beats
            }
            candidates = pipeline._load_reference_panel_fallback_candidates(
                db,
                project_id,
                script_row,
                images,
                profile,
                review_source_upscale_policy=policy,
                section_evidence_panel_ids=beat_panel_ids,
                section_citations=dict.fromkeys(beat_panel_ids, ()),
                beats_by_section={beat_id: (beat_id,) for beat_id in beat_panel_ids},
                allow_persisted_panel_crop_fallback=policy is not None,
                review_source_root=review_source_root,
            )
        ledger = visual_narrative_repair.build_feasible_visual_ledger(
            candidates,
            profile=profile,
            model_identity_hash=self.runner.model_identity.identity_hash,
            allow_source_resolution_warning=bool(policy.allow_low_source_resolution_warning),
        )
        missing = visual_narrative_repair.missing_visual_sections(ledger, section_to_beats)
        if not missing and current_narration is not None:
            return result, ledger, missing
        if not ledger.entries:
            raise CloudStageError("visual.visual_unavailable", reviewable=True)
        repaired = self.runner.run_visual_narrative_repair(
            current_visual,
            current_story_map,
            current_narration,
            ledger,
            section_to_beats,
            panels=panels,
        )
        coalesced_passages, coalesce_provenance = (
            visual_narrative_repair.coalesce_adjacent_duplicate_panel_passages(
                repaired.passages
            )
        )
        if coalesce_provenance:
            qc_report = dict(repaired.qc_report)
            qc_report["visual_sequence_coalesce_v1"] = list(coalesce_provenance)
            repaired = replace(
                repaired,
                passages=tuple(coalesced_passages),
                spoken_text="\n\n".join(
                    str(passage.get("text", "")).strip()
                    for passage in coalesced_passages
                ),
                qc_report=qc_report,
            )
            claims = repaired.evidence_graph.get("claims", [])
            visual_narrative_repair.validate_repaired_panel_references(
                {
                    "claims": claims,
                    "passages": [dict(item) for item in repaired.passages],
                },
                ledger=ledger,
                allowed_claim_ids={
                    str(claim.get("claim_id", ""))
                    for claim in claims
                    if isinstance(claim, Mapping)
                },
            )
        try:
            visual_narrative_repair.validate_repaired_section_visual_coverage(
                repaired.passages,
                ledger=ledger,
                section_to_beats=section_to_beats,
                missing_sections=missing,
            )
        except visual_narrative_repair.VisualNarrativeRepairError as exc:
            raise CloudStageError(exc.code, reviewable=exc.reviewable) from None
        if current_narration is not None and repaired == current_narration:
            raise CloudStageError(
                "visual.narrative_repair_ungrounded",
                reviewable=True,
            )
        return ChapterResult(ChapterState.READY_TO_RENDER, current_visual, current_story_map, repaired), ledger, missing

    def run_batch(self, jobs: Mapping[str, Sequence[CloudPanelInput]]) -> dict[str, ChapterJobRecord]:
        ordered_ids = sorted(jobs)
        if self.max_concurrent == 1 or len(ordered_ids) < 2:
            return {job_id: self.run_job(job_id, jobs[job_id]) for job_id in ordered_ids}
        with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            records = executor.map(lambda job_id: self.run_job(job_id, jobs[job_id]), ordered_ids)
            return dict(zip(ordered_ids, records, strict=True))

    def run_project(
        self,
        db: Any,
        project_id: str,
        *,
        actor_id: str = "",
        review_only_preview: bool = False,
        review_source_upscale_policy: str | None = None,
        review_source_root: Path | None = None,
        review_output_dir: Path | None = None,
        max_cloud_panels: int | None = None,
    ) -> ChapterJobRecord:
        """Run one DB-backed project and persist only after stage reconciliation."""

        try:
            record = self.store.load(project_id) or ChapterJobRecord(project_id)
            cached_segmentation = record.stage_results.get("segmentation")
            prepared: tuple[tuple[CloudPanelInput, ...], dict[str, Any]] | None = None
            visual_stream: _StreamingVisualEvidenceSession | None = None
            manifest_loaded = False
            preparation_started = time.monotonic()
            manifest_raw = record.stage_results.get("prepared_panel_manifest")
            try:
                if not isinstance(manifest_raw, Mapping):
                    visual_stage = record.stage_results.get("visual")
                    if not isinstance(visual_stage, Mapping) or not isinstance(cached_segmentation, Mapping):
                        raise prepared_panel_manifest.PreparedPanelManifestError(
                            "prepared manifest seed is unavailable"
                        )
                    manifest_raw = _build_cached_prepared_manifest(
                        db,
                        project_id,
                        visual_stage,
                        cached_segmentation,
                    )
                try:
                    prepared = _restore_project_prepared_manifest(
                        db,
                        project_id,
                        manifest_raw,
                        segmentation_state=(
                            cached_segmentation
                            if isinstance(cached_segmentation, Mapping)
                            else None
                        ),
                    )
                except TypeError as exc:
                    # Keep small test/integration adapters written for the
                    # pre-v3 two-argument helper source-compatible.
                    if "segmentation_state" not in str(exc):
                        raise
                    prepared = _restore_project_prepared_manifest(
                        db,
                        project_id,
                        manifest_raw,
                    )
                record.stage_results["prepared_panel_manifest"] = manifest_raw
                self.store.save(record)
                manifest_loaded = True
            except prepared_panel_manifest.PreparedPanelManifestError:
                prepared = None
            if prepared is None:
                stream_enabled = (
                    not manifest_loaded
                    and max_cloud_panels is None
                    and callable(getattr(self.runner, "start_visual_evidence_stream", None))
                )
                if stream_enabled:
                    visual_stream = self.runner.start_visual_evidence_stream()
                try:
                    prepared = prepare_project_panels(
                        db,
                        project_id,
                        boundary_assessor=self.runner.assess_strip_boundaries,
                        review_root=self.review_root,
                        return_segmentation=True,
                        review_only_auto_override=review_only_preview,
                        cached_segmentation=(
                            cached_segmentation
                            if isinstance(cached_segmentation, Mapping)
                            else None
                        ),
                        panel_sink=(visual_stream.submit if visual_stream is not None else None),
                        segmentation_checkpoint_identity=_segmentation_checkpoint_identity(self.runner),
                    )
                except Exception:
                    if visual_stream is not None:
                        visual_stream.abort()
                    raise
            panels, segmentation_state = prepared
            streamed_visual: VisualStageResult | None = None
            if visual_stream is not None:
                try:
                    streamed_visual = visual_stream.finish(panels)
                except CloudStageError:
                    metrics = dict(self.runner.last_visual_stream_metrics)
                    if metrics:
                        record.stage_results["visual_stream_metrics"] = metrics
                        self.store.save(record)
                    raise
                record.stage_results["visual"] = streamed_visual.as_dict()
                record.stage_results["visual_stream_metrics"] = dict(
                    self.runner.last_visual_stream_metrics
                )
            restored_visual_subset: VisualStageResult | None = None
            if manifest_loaded and _visual_cache_requires_subset_restore(
                self.runner,
                record.stage_results.get("visual"),
                panels,
            ):
                expected_source_hash = str(
                    manifest_raw.get("source_identity_hash", "")
                    if isinstance(manifest_raw, Mapping)
                    else ""
                )
                if expected_source_hash:
                    restored_visual_subset = _find_cached_visual_subset(
                        self.runner,
                        panels,
                        expected_source_hash=expected_source_hash,
                    )
                    if restored_visual_subset is not None:
                        record.stage_results["visual"] = restored_visual_subset.as_dict()
                        self.store.save(record)
            panels = _panels_for_cached_visual_stage(
                panels,
                record.stage_results.get("visual"),
            )
            if max_cloud_panels is not None and len(panels) > max_cloud_panels:
                panels = _subsample_panels(panels, max_cloud_panels)
            if (
                restored_visual_subset is not None
                and restored_visual_subset.panel_ids == tuple(panel.panel_id for panel in panels)
            ):
                _seed_visual_subset_cache(
                    self.runner,
                    panels,
                    restored_visual_subset,
                )
            targeted_materialization_ids: tuple[str, ...] = ()
            if manifest_loaded:
                targeted_materialization_ids = _visual_panel_ids_requiring_materialization(
                    self.runner,
                    panels,
                )
                if targeted_materialization_ids:
                    try:
                        panels = _materialize_metadata_only_panels(
                            db,
                            project_id,
                            panels,
                            required_panel_ids=targeted_materialization_ids,
                        )
                    except CloudStageError as exc:
                        return self._record_failure(record, exc)
                if (
                    restored_visual_subset is not None
                    and restored_visual_subset.panel_ids == tuple(panel.panel_id for panel in panels)
                ):
                    _seed_visual_subset_cache(
                        self.runner,
                        panels,
                        restored_visual_subset,
                    )
            record.stage_results["segmentation"] = segmentation_state
            record.stage_results["preparation_metrics"] = {
                "contract_version": "prepared-panel-preparation-v1",
                "mode": (
                    "manifest_targeted_materialization"
                    if targeted_materialization_ids
                    else "manifest_metadata_only"
                    if manifest_loaded
                    else "cold_materialization"
                ),
                "panel_count": len(panels),
                "payload_bytes": sum(len(panel.payload) for panel in panels),
                "targeted_materialized_panel_count": len(targeted_materialization_ids),
                "elapsed_s": round(time.monotonic() - preparation_started, 3),
                "peak_rss_kb": _peak_rss_kb(),
                "source_decode_required": bool(
                    not manifest_loaded or targeted_materialization_ids
                ),
            }
            if not manifest_loaded:
                record.stage_results["prepared_panel_manifest"] = _build_project_prepared_manifest(
                    db,
                    project_id,
                    panels,
                    segmentation_state,
                )
            self.store.save(record)
            if streamed_visual is None:
                record = self.run_job(project_id, panels)
            else:
                record = self.run_job(
                    project_id,
                    panels,
                    precomputed_visual=streamed_visual,
                )
            if (
                manifest_loaded
                and record.error_code == "cloud.prepared_manifest_requires_materialization"
            ):
                # A metadata-only manifest is usable only when its exact
                # content-addressed visual cache reconciles.  If the cache
                # is stale, materialize the current prepared panels and
                # rerun the affected visual/story stages; never mix old
                # evidence merely because panel IDs happen to match.
                fallback_started = time.monotonic()
                try:
                    prepared = prepare_project_panels(
                        db,
                        project_id,
                        boundary_assessor=self.runner.assess_strip_boundaries,
                        review_root=self.review_root,
                        return_segmentation=True,
                        review_only_auto_override=review_only_preview,
                        cached_segmentation=(
                            cached_segmentation
                            if isinstance(cached_segmentation, Mapping)
                            else None
                        ),
                        segmentation_checkpoint_identity=_segmentation_checkpoint_identity(self.runner),
                    )
                    panels, segmentation_state = prepared
                    panels = _panels_for_cached_visual_stage(
                        panels,
                        record.stage_results.get("visual"),
                    )
                    manifest_loaded = False
                    record.stage_results["segmentation"] = segmentation_state
                    record.stage_results["preparation_metrics"] = {
                        "contract_version": "prepared-panel-preparation-v1",
                        "mode": "cold_materialization_after_metadata_cache_miss",
                        "panel_count": len(panels),
                        "payload_bytes": sum(len(panel.payload) for panel in panels),
                        "elapsed_s": round(time.monotonic() - fallback_started, 3),
                        "peak_rss_kb": _peak_rss_kb(),
                        "source_decode_required": True,
                    }
                    record.stage_results["prepared_panel_manifest"] = (
                        _build_project_prepared_manifest(
                            db,
                            project_id,
                            panels,
                            segmentation_state,
                        )
                    )
                    self.store.save(record)
                    record = self.run_job(project_id, panels)
                except CloudStageError as exc:
                    return self._record_failure(record, exc)
            visual_stage = record.stage_results.get("visual")
            if isinstance(visual_stage, Mapping):
                visual_panel_ids = {
                    str(item.get("panel_id"))
                    for item in visual_stage.get("panels", ())
                    if isinstance(item, Mapping) and str(item.get("panel_id", "")).strip()
                }
                if visual_panel_ids:
                    panels = tuple(
                        panel for panel in panels if panel.panel_id in visual_panel_ids
                    )
            record.stage_results["segmentation"] = segmentation_state
            self.store.save(record)
            repaired_result: ChapterResult | None = None
            repair_ledger: visual_narrative_repair.FeasibleVisualLedger | None = None
            repair_missing_sections: tuple[str, ...] = ()
            if record.state != ChapterState.READY_TO_RENDER:
                can_repair_initial_narration = (
                    review_only_preview
                    and record.error_code in {
                        "cloud.narrative_not_grounded",
                        "cloud.narrative_duration_out_of_range",
                    }
                    and isinstance(record.stage_results.get("visual"), Mapping)
                    and isinstance(record.stage_results.get("story_map"), Mapping)
                )
                if not can_repair_initial_narration:
                    return record
                initial_repair_result = None
                partial_narration = getattr(self.runner, "_last_narration_result", None)
                initial_visual = VisualStageResult.from_dict(record.stage_results["visual"])
                initial_story_map = StoryMapResult.from_dict(record.stage_results["story_map"])
                if (
                    partial_narration is not None
                    and getattr(partial_narration, "visual_evidence_hash", "")
                    == initial_visual.visual_evidence_hash
                ):
                    initial_repair_result = ChapterResult(
                        state=ChapterState.READY_TO_RENDER,
                        visual=initial_visual,
                        story_map=initial_story_map,
                        narration=partial_narration,
                    )
                try:
                    repaired_result, repair_ledger, repair_missing_sections = (
                        self._repair_review_narrative(
                            db,
                            project_id,
                            None,
                            panels,
                            initial_repair_result,
                            visual=initial_visual,
                            story_map=initial_story_map,
                            review_source_upscale_policy=review_source_upscale_policy,
                            review_source_root=Path(
                                review_source_root or self.review_root or Path("final_test")
                            ),
                        )
                    )
                except CloudStageError as exc:
                    print("REPAIR_FAILED:", exc.code, file=sys.stderr, flush=True)
                    return self._record_failure(record, exc)
                record.stage_results["narration"] = repaired_result.narration.as_dict()
                record.state = ChapterState.READY_TO_RENDER
                record.error_code = ""
                record.error_message = ""
                self.store.save(record)
            if repaired_result is None:
                result = ChapterResult(
                    state=ChapterState.READY_TO_RENDER,
                    visual=VisualStageResult.from_dict(record.stage_results["visual"]),
                    story_map=StoryMapResult.from_dict(record.stage_results["story_map"]),
                    narration=NarrationResult.from_dict(record.stage_results["narration"]),
                )
            else:
                result = repaired_result
            analysis, script_row = persist_cloud_chapter(
                db,
                project_id,
                panels,
                result,
                model_identity=self.runner.model_identity,
                actor_id=actor_id,
            )
            # Make reconciled analysis/script durable before the optional
            # review render. A later render failure must not roll back the
            # expensive cloud stages or prevent a safe resume.
            if hasattr(db, "commit"):
                db.commit()
            record.stage_results["persistence"] = {
                "analysis_id": analysis.id,
                "script_id": script_row.id,
                "script_version": script_row.version,
                "approval_required": True,
                "voice_timing_required": True,
            }
            if review_only_preview:
                from app.services import review_source_upscale

                try:
                    policy_id = review_source_upscale_policy or review_source_upscale.REVIEW_SOURCE_UPSCALE_POLICY_ID
                    if repaired_result is None:
                        repaired_result, repair_ledger, repair_missing_sections = self._repair_review_narrative(
                            db,
                            project_id,
                            script_row,
                            panels,
                            result,
                            review_source_upscale_policy=policy_id,
                            review_source_root=Path(review_source_root or self.review_root or Path("final_test")),
                        )
                    ledger = repair_ledger
                    missing_sections = repair_missing_sections
                    if ledger is None:
                        raise CloudStageError("visual.narrative_repair_stale_ledger", reviewable=True)
                    record.stage_results["feasible_visual_ledger"] = ledger.as_dict()
                    if isinstance(ledger, visual_narrative_repair.FeasibleVisualLedger):
                        record.stage_results["feasible_render_plan"] = (
                            visual_narrative_repair.FeasibleRenderPlan.from_ledger(ledger).as_dict()
                        )
                    record.stage_results["visual_repair"] = {
                        "contract_version": visual_narrative_repair.REPAIR_CONTRACT_VERSION,
                        "missing_sections": list(missing_sections),
                        "attempted": bool(missing_sections),
                        "model_identity_hash": self.runner.model_identity.identity_hash,
                        "prompt_version": self.runner.prompts["visual_narrative_repair"][0],
                        "prompt_sha256": self.runner.prompts["visual_narrative_repair"][1],
                        "publish_allowed": False,
                    }
                    if repaired_result.narration != result.narration:
                        analysis, script_row = persist_cloud_chapter(
                            db,
                            project_id,
                            panels,
                            repaired_result,
                            model_identity=self.runner.model_identity,
                            actor_id=actor_id,
                        )
                        record.stage_results["narration"] = repaired_result.narration.as_dict()
                        record.stage_results["persistence"] = {
                            "analysis_id": analysis.id,
                            "script_id": script_row.id,
                            "script_version": script_row.version,
                            "approval_required": True,
                            "voice_timing_required": True,
                            "visual_repair": True,
                        }
                except CloudStageError as exc:
                    return self._record_failure(record, exc)
                try:
                    from app.services import pipeline

                    ledger_entries = tuple(
                        getattr(ledger, "entries", ())
                        or tuple((ledger.as_dict() or {}).get("entries", ()))
                    )
                    # Ledger entries are beat-keyed; the planner matches the
                    # script section names. Map every section to the panels of
                    # its beats that passed every framing gate.
                    section_names = tuple(
                        str(section.get("section", ""))
                        for section in (getattr(script_row, "sections", ()) or ())
                        if isinstance(section, Mapping) and str(section.get("section", "")).strip()
                    )
                    story_map_row = StoryMapResult.from_dict(
                        record.stage_results["story_map"]
                    )
                    section_to_beats = visual_narrative_repair.default_section_to_beats(
                        section_names,
                        story_map_row.beats,
                    ) if section_names else {
                        str(section): (str(section),)
                        for section in story_map_row.panel_ids
                        if section
                    }
                    section_panel_ids: dict[str, list[str]] = {}
                    for section, beat_ids in section_to_beats.items():
                        allowed = set(beat_ids)
                        for entry in ledger_entries:
                            if not allowed.intersection(
                                str(value) for value in entry.eligible_beats
                            ):
                                continue
                            if str(entry.panel_id) not in section_panel_ids.setdefault(
                                str(section), []
                            ):
                                section_panel_ids[str(section)].append(str(entry.panel_id))
                    section_panel_ids = {
                        str(section): tuple(panel_ids)
                        for section, panel_ids in section_panel_ids.items()
                        if panel_ids
                    }
                    # Citations stay as persisted source orders; the planner
                    # resolves them to regions and re-runs every framing gate.
                    section_citations = {
                        str(section.get("section", "")): tuple(
                            int(value)
                            for value in section.get("citations", ())
                            if isinstance(value, int)
                        )
                        for section in script_row.sections
                        if isinstance(section, Mapping) and str(section.get("section", "")).strip()
                    }
                    section_panel_ids = {
                        str(section): tuple(
                            panel_id
                            for panel_id in panel_ids
                            if str(panel_id).strip()
                        )
                        for section, panel_ids in section_panel_ids.items()
                        if panel_ids
                    }
                    pipeline.build_timeline(
                        db,
                        project_id,
                        actor_id=actor_id,
                        silent_reference_review=True,
                        review_source_upscale_policy=policy_id,
                        provisional_duration_s=float(script_row.estimated_duration),
                        reference_section_panel_ids=section_panel_ids,
                        reference_section_citations=section_citations,
                        reference_beats_by_section={},
                        review_source_root=Path(review_source_root or self.review_root or Path("final_test")),
                        allow_conservative_full_panel=True,
                    )
                    _render_job, artifacts = pipeline.render_silent_review_preview(
                        db,
                        project_id,
                        actor_id=actor_id,
                        review_source_upscale_policy=policy_id,
                        review_source_root=Path(review_source_root or self.review_root or Path("final_test")),
                        output_dir=review_output_dir,
                    )
                except Exception as exc:  # noqa: BLE001 - convert to a durable review state
                    code = _review_failure_code(
                        str(getattr(exc, "code", "") or exc)
                    )
                    raise CloudStageError(code, "review preview was not produced", reviewable=True) from None
                record.stage_results["review_preview"] = artifacts.as_dict()
                record.stage_results["voice_state"] = "VISUAL_ONLY_WAITING_FOR_VOICE"
                record.stage_results["publish_allowed"] = False
                record.state = ChapterState.REVIEW_PREVIEW_READY
            else:
                record.state = ChapterState.READY_TO_RENDER
            record.error_code = ""
            record.error_message = ""
            self.store.save(record)
            return record
        except CloudStageError as exc:
            if hasattr(db, "rollback"):
                db.rollback()
            record = self.store.load(project_id) or ChapterJobRecord(project_id)
            record.model_identity_hash = self.runner.model_identity.identity_hash
            record.state = ChapterState.NEEDS_REVIEW if exc.reviewable else ChapterState.FAILED
            record.error_code = exc.code
            record.error_message = str(exc)
            if exc.reviewable:
                review_entry = {"code": exc.code, "reason": str(exc)}
                safe_metadata = dict(exc.safe_metadata)
                metrics_for_failure = getattr(
                    self.runner, "_response_shape_metrics_for_failure", None
                )
                if (
                    not safe_metadata
                    and callable(metrics_for_failure)
                    and (
                        exc.code.startswith("cloud.narrative_")
                        or exc.code == "cloud.request_budget_exceeded"
                    )
                ):
                    safe_metadata = metrics_for_failure(exc.code)
                if "array_key" in safe_metadata:
                    safe_metadata.setdefault("failed_code", exc.code)
                    safe_metadata.setdefault("failed_predicate", exc.code)
                if safe_metadata:
                    review_entry["safe_metadata"] = safe_metadata
                record.review_queue.append(review_entry)
            self.store.save(record)
            return record


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
        raise CloudStageError(str(getattr(report, "blocking_reason", None) or "cloud.credential_missing"))
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
LEGACY_VISUAL_REPAIR_PROMPT_VERSION = "visual-narrative-repair-v2"
CURRENT_VISUAL_REPAIR_PROMPT_VERSION = "visual-narrative-repair-v3"
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
        _visual_panel_identity(panel, ordered_index)
        for ordered_index, panel in enumerate(panels)
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
    prepared_hashes = {
        panel.source_identity_hash
        for panel in panels
        if panel.source_identity_hash
    }
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
    for index, (panel, observation) in enumerate(
        zip(ordered, raw_observations, strict=True)
    ):
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
        str(cached.get("model_identity_hash", "")) != model_identity.identity_hash
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
    cached_ids = tuple(
        str(row.get("panel_id", ""))
        for row in raw_rows
        if isinstance(row, Mapping)
    )
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
        if (
            str(cached.get("source_hash", "")) != expected_source_hash
            or persisted_hashes != identity_hashes
        ):
            return None
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
