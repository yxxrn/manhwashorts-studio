"""Feasible-visual ledger and bounded narrative repair primitives.

This boundary is intentionally local-first: the renderer never relaxes a
visual gate.  A repair can only cite a panel that already has a feasible,
lineage-checked ROI in the current review contract.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.services import framing_analysis

REPAIR_CONTRACT_VERSION = "visual_narrative_repair_v1"
VISUAL_SECTION_REMAP_VERSION = "visual_section_remap_v1"
REPAIR_PROMPT_VERSION = "visual-narrative-repair-v2"
MAX_REPAIR_ATTEMPTS = 3
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "visual_narrative_repair_v1.txt"


class VisualNarrativeRepairError(ValueError):
    """Safe, stable failure at the visual-aware narrative boundary."""

    def __init__(self, message: str, code: str = "visual.narrative_repair_failed", *, reviewable: bool = True):
        self.code = code
        self.reviewable = reviewable
        super().__init__(message)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def load_repair_prompt() -> tuple[str, str, str]:
    try:
        text = PROMPT_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise VisualNarrativeRepairError("repair prompt is unavailable", "cloud.prompt_missing", reviewable=False) from None
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if f"Version: {REPAIR_PROMPT_VERSION}" not in normalized:
        raise VisualNarrativeRepairError("repair prompt is invalid", "cloud.prompt_invalid", reviewable=False)
    return REPAIR_PROMPT_VERSION, hashlib.sha256(normalized.encode("utf-8")).hexdigest(), normalized


@dataclass(frozen=True)
class FeasibleVisualRecord:
    panel_region_id: str
    panel_id: str
    source_asset_id: str
    source_order: int
    eligible_sections: tuple[str, ...]
    eligible_beats: tuple[str, ...]
    resolution_state: str
    feasible_rois: tuple[dict[str, Any], ...]
    visual_strengths: Mapping[str, float]
    evidence_hash: str
    detector_version: str
    mask_sha256: str
    panel_size: tuple[int, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "panel_region_id": self.panel_region_id,
            "panel_id": self.panel_id,
            "source_asset_id": self.source_asset_id,
            "source_order": self.source_order,
            "eligible_sections": list(self.eligible_sections),
            "eligible_beats": list(self.eligible_beats),
            "resolution_state": self.resolution_state,
            "feasible_rois": [dict(item) for item in self.feasible_rois],
            "visual_strengths": dict(self.visual_strengths),
            "evidence_hash": self.evidence_hash,
            "detector_version": self.detector_version,
            "mask_sha256": self.mask_sha256,
            "panel_size": list(self.panel_size),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> FeasibleVisualRecord:
        try:
            return cls(
                panel_region_id=str(value["panel_region_id"]),
                panel_id=str(value["panel_id"]),
                source_asset_id=str(value["source_asset_id"]),
                source_order=int(value["source_order"]),
                eligible_sections=tuple(str(item) for item in value.get("eligible_sections", ())),
                eligible_beats=tuple(str(item) for item in value.get("eligible_beats", ())),
                resolution_state=str(value["resolution_state"]),
                feasible_rois=tuple(dict(item) for item in value["feasible_rois"]),
                visual_strengths={str(key): float(item) for key, item in dict(value["visual_strengths"]).items()},
                evidence_hash=str(value["evidence_hash"]),
                detector_version=str(value["detector_version"]),
                mask_sha256=str(value["mask_sha256"]),
                panel_size=tuple(int(item) for item in value["panel_size"]),
            )
        except (KeyError, TypeError, ValueError):
            raise VisualNarrativeRepairError("visual ledger entry is malformed", "visual.narrative_repair_stale_ledger") from None


@dataclass(frozen=True)
class FeasibleVisualLedger:
    entries: tuple[FeasibleVisualRecord, ...]
    model_identity_hash: str
    ledger_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.model_identity_hash.strip():
            raise VisualNarrativeRepairError("visual ledger model identity is missing", "visual.narrative_repair_stale_ledger")
        ordered = tuple(sorted(self.entries, key=lambda item: (item.source_order, item.panel_id, item.panel_region_id)))
        if len({item.panel_id for item in ordered}) != len(ordered):
            raise VisualNarrativeRepairError("visual ledger contains duplicate panels", "visual.narrative_repair_stale_ledger")
        object.__setattr__(self, "entries", ordered)
        object.__setattr__(self, "ledger_hash", _hash({
            "contract_version": REPAIR_CONTRACT_VERSION,
            "model_identity_hash": self.model_identity_hash,
            "entries": [item.as_dict() for item in ordered],
        }))

    @property
    def feasible_panel_ids(self) -> tuple[str, ...]:
        return tuple(item.panel_id for item in self.entries)

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": REPAIR_CONTRACT_VERSION,
            "model_identity_hash": self.model_identity_hash,
            "ledger_hash": self.ledger_hash,
            "entries": [item.as_dict() for item in self.entries],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> FeasibleVisualLedger:
        try:
            ledger = cls(
                entries=tuple(FeasibleVisualRecord.from_dict(item) for item in value["entries"]),
                model_identity_hash=str(value["model_identity_hash"]),
            )
        except (KeyError, TypeError, ValueError):
            raise VisualNarrativeRepairError("visual ledger is malformed", "visual.narrative_repair_stale_ledger") from None
        if value.get("contract_version") != REPAIR_CONTRACT_VERSION or value.get("ledger_hash") != ledger.ledger_hash:
            raise VisualNarrativeRepairError("visual ledger hash is stale", "visual.narrative_repair_stale_ledger")
        return ledger


def build_feasible_visual_ledger(
    candidates: Sequence[object],
    *,
    profile: object,
    model_identity_hash: str,
    allow_source_resolution_warning: bool = False,
) -> FeasibleVisualLedger:
    """Evaluate every candidate ROI and retain only genuinely feasible panels."""

    built: list[FeasibleVisualRecord] = []
    target_size = (int(profile.final_width), int(profile.final_height))
    for candidate in sorted(
        candidates,
        key=lambda item: (int(item.source_order), str(item.panel_id), str(item.panel_region_id)),
    ):
        manifest = getattr(candidate, "source_upscale_manifest", None)
        resolution_state = "NATIVE"
        if isinstance(manifest, Mapping):
            resolution_state = str(manifest.get("resolution_state") or "NATIVE")
        feasible_rois: list[dict[str, Any]] = []
        for roi in tuple(getattr(candidate, "roi_alternatives", ()) or ()):
            try:
                is_feasible, telemetry = framing_analysis.candidate_is_feasible(
                    tuple(int(value) for value in roi.crop_box),
                    candidate.visual_evidence,
                    candidate.border_mask,
                    tuple(int(value) for value in candidate.panel_size),
                    target_size,
                    allow_source_resolution_warning=allow_source_resolution_warning,
                    review_aggressive_crop=allow_source_resolution_warning,
                )
            except Exception:
                continue
            if not is_feasible:
                continue
            telemetry_dict = asdict(telemetry)
            feasible_rois.append({
                "kind": str(roi.kind),
                "roi_label": str(roi.roi_label),
                "crop_box": [int(value) for value in roi.crop_box],
                "telemetry": telemetry_dict,
            })
        if not feasible_rois:
            continue
        best = feasible_rois[0]["telemetry"]
        strength_fields = (
            "protected_retained_fraction",
            "subject_coverage",
            "face_coverage",
            "action_coverage",
            "effect_coverage",
            "continuity_context_coverage",
            "edge_connected_blank_fraction",
            "base_zoom",
        )
        strengths = {
            field_name: float(best.get(field_name, 0.0))
            for field_name in strength_fields
            if isinstance(best.get(field_name), (int, float))
        }
        built.append(
            FeasibleVisualRecord(
                panel_region_id=str(candidate.panel_region_id),
                panel_id=str(candidate.panel_id),
                source_asset_id=str(candidate.source_asset_id),
                source_order=int(candidate.source_order),
                eligible_sections=tuple(sorted(str(value) for value in (candidate.eligible_sections or ()))),
                eligible_beats=tuple(sorted(str(value) for value in (candidate.eligible_beats or ()))),
                resolution_state=resolution_state,
                feasible_rois=tuple(feasible_rois),
                visual_strengths=strengths,
                evidence_hash=str(candidate.evidence_hash),
                detector_version=str(candidate.border_mask.detector_version),
                mask_sha256=str(candidate.border_mask.mask_sha256),
                panel_size=tuple(int(value) for value in candidate.panel_size),
            )
        )
    return FeasibleVisualLedger(entries=tuple(built), model_identity_hash=model_identity_hash)


def missing_visual_sections(
    ledger: FeasibleVisualLedger,
    section_to_beats: Mapping[str, Sequence[str]],
) -> tuple[str, ...]:
    missing: list[str] = []
    for section in sorted(section_to_beats):
        allowed = {str(value) for value in (section_to_beats.get(section) or ())}
        if not any(allowed.intersection(entry.eligible_beats) for entry in ledger.entries):
            missing.append(str(section))
    return tuple(missing)


def remap_same_beat_panel_citations(
    value: Mapping[str, Any],
    *,
    ledger: FeasibleVisualLedger,
    section_to_beats: Mapping[str, Sequence[str]],
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Prefer a lower-blank panel without crossing the supported story beat.

    The provider may select any feasible panel, but a review render should not
    retain avoidable edge-connected blank space when another panel in the same
    evidence beat supports the same section.  This local rewrite changes only
    panel references; prose and claim text remain immutable.  The returned
    provenance is persisted with the narrative QC report.
    """

    raw_claims = value.get("claims")
    raw_passages = value.get("passages")
    if not isinstance(raw_claims, list) or not isinstance(raw_passages, list):
        raise VisualNarrativeRepairError(
            "repair evidence graph is incomplete",
            "visual.narrative_repair_ungrounded",
        )
    entries_by_panel = {entry.panel_id: entry for entry in ledger.entries}
    ordered_sections = tuple(str(section) for section in section_to_beats)
    remaps: list[dict[str, Any]] = []
    usage_by_panel: dict[str, int] = {}

    def replacement_for(panel_id: str, section: str) -> str:
        current = entries_by_panel.get(panel_id)
        if current is None:
            return panel_id
        current_beats = set(current.eligible_beats)
        if not current_beats:
            return panel_id
        current_blank = float(
            current.visual_strengths.get("edge_connected_blank_fraction", 1.0)
        )
        if not math.isfinite(current_blank) or not 0.0 <= current_blank <= 1.0:
            return panel_id
        available = [
            entry
            for entry in ledger.entries
            if entry.panel_id != panel_id
            and current_beats.intersection(entry.eligible_beats)
            and math.isfinite(
                float(entry.visual_strengths.get("edge_connected_blank_fraction", 1.0))
            )
            and usage_by_panel.get(entry.panel_id, 0) < len(entry.feasible_rois)
            and 0.0 <= float(entry.visual_strengths.get("edge_connected_blank_fraction", 1.0)) <= 1.0
        ]
        alternatives = [
            entry
            for entry in available
            if float(entry.visual_strengths.get("edge_connected_blank_fraction", 1.0)) < current_blank - 1e-9
        ]
        reason = "same-beat lower edge-connected blank fraction"
        if not alternatives and usage_by_panel.get(panel_id, 0) >= len(current.feasible_rois):
            alternatives = available
            reason = "same-beat alternate after ROI capacity exhausted"
        if not alternatives:
            return panel_id
        selected = min(
            alternatives,
            key=lambda entry: (
                float(entry.visual_strengths.get("edge_connected_blank_fraction", 1.0)),
                -float(entry.visual_strengths.get("protected_retained_fraction", 0.0)),
                entry.source_order,
                entry.panel_id,
                entry.panel_region_id,
            ),
        )
        remaps.append(
            {
                "contract_version": VISUAL_SECTION_REMAP_VERSION,
                "section": section,
                "from_panel_id": panel_id,
                "to_panel_id": selected.panel_id,
                "from_source_order": current.source_order,
                "to_source_order": selected.source_order,
                "from_blank_fraction": current_blank,
                "to_blank_fraction": float(
                    selected.visual_strengths.get("edge_connected_blank_fraction", 1.0)
                ),
                "same_eligible_beats": sorted(current_beats.intersection(selected.eligible_beats)),
                "ledger_hash": ledger.ledger_hash,
                "reason": reason,
            }
        )
        return selected.panel_id

    passages: list[dict[str, Any]] = []
    for index, raw_passage in enumerate(raw_passages):
        if not isinstance(raw_passage, Mapping):
            raise VisualNarrativeRepairError(
                "repair passage is malformed",
                "visual.narrative_repair_ungrounded",
            )
        section = ordered_sections[index] if index < len(ordered_sections) else ""
        refs = raw_passage.get("evidence_panel_ids")
        if not isinstance(refs, list) or not refs:
            raise VisualNarrativeRepairError(
                "repair passage evidence is incomplete",
                "visual.narrative_repair_ungrounded",
            )
        mapped_refs: list[str] = []
        for raw_ref in refs:
            mapped = replacement_for(str(raw_ref), section)
            if mapped not in mapped_refs:
                mapped_refs.append(mapped)
        passage = dict(raw_passage)
        passage["evidence_panel_ids"] = mapped_refs
        passages.append(passage)
        for panel_id in mapped_refs:
            usage_by_panel[panel_id] = usage_by_panel.get(panel_id, 0) + 1

    claims: list[dict[str, Any]] = []
    for raw_claim in raw_claims:
        if not isinstance(raw_claim, Mapping):
            raise VisualNarrativeRepairError(
                "repair claim is malformed",
                "visual.narrative_repair_ungrounded",
            )
        claim = dict(raw_claim)
        claim_id = str(claim.get("claim_id", ""))
        covered: list[str] = []
        for passage in passages:
            if claim_id not in {str(item) for item in passage.get("claim_ids", ())}:
                continue
            for panel_id in passage["evidence_panel_ids"]:
                if panel_id not in covered:
                    covered.append(panel_id)
        if covered:
            claim["evidence_panel_ids"] = covered
        claims.append(claim)

    return {"claims": claims, "passages": passages}, tuple(remaps)


def coalesce_adjacent_duplicate_panel_passages(
    passages: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], tuple[dict[str, Any], ...]]:
    """Merge adjacent passages that have only the same feasible panel.

    This is a review-only continuity repair. It preserves every spoken word,
    claim reference, and evidence reference while avoiding an impossible
    consecutive reuse of one exact panel when no alternate safe visual exists.
    """

    merged: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    for raw in passages:
        if not isinstance(raw, Mapping):
            raise VisualNarrativeRepairError(
                "repair passage is malformed",
                "visual.narrative_repair_ungrounded",
            )
        current = dict(raw)
        references = current.get("evidence_panel_ids")
        if not isinstance(references, list) or not references:
            raise VisualNarrativeRepairError(
                "repair passage evidence is incomplete",
                "visual.narrative_repair_ungrounded",
            )
        if merged:
            previous = merged[-1]
            previous_refs = previous.get("evidence_panel_ids")
            if (
                isinstance(previous_refs, list)
                and len(previous_refs) == 1
                and len(references) == 1
                and str(previous_refs[0]) == str(references[0])
            ):
                previous_claims = list(previous.get("claim_ids") or ())
                current_claims = list(current.get("claim_ids") or ())
                claims = list(dict.fromkeys([*previous_claims, *current_claims]))
                previous_text = str(previous.get("text", "")).strip()
                current_text = str(current.get("text", "")).strip()
                if not previous_text or not current_text:
                    raise VisualNarrativeRepairError(
                        "repair passage text is incomplete",
                        "visual.narrative_repair_ungrounded",
                    )
                previous_id = str(previous.get("passage_id", ""))
                current_id = str(current.get("passage_id", ""))
                previous["text"] = f"{previous_text} {current_text}"
                previous["claim_ids"] = claims
                previous["evidence_panel_ids"] = [str(references[0])]
                provenance.append(
                    {
                        "contract_version": "visual_sequence_coalesce_v1",
                        "kind": "adjacent_duplicate_panel",
                        "panel_id": str(references[0]),
                        "from_passage_ids": [previous_id, current_id],
                        "reason": "only one feasible exact panel was available for adjacent passages",
                    }
                )
                continue
        merged.append(current)
    return merged, tuple(provenance)


def default_section_to_beats(
    section_names: Sequence[str],
    story_map_beats: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[str, ...]]:
    """Map the normal five editorial passages to ordered causal beats."""

    names = tuple(str(value) for value in section_names if str(value).strip())
    beats = tuple(
        str(item.get("beat_id", ""))
        for item in story_map_beats
        if isinstance(item, Mapping) and str(item.get("beat_id", "")).strip()
    )
    if not names or not beats or len(names) > len(beats):
        raise VisualNarrativeRepairError("story beat mapping is incomplete", "visual.narrative_repair_stale_ledger")
    if len(names) == 1:
        return {names[0]: beats}
    result: dict[str, tuple[str, ...]] = {}
    for index, name in enumerate(names):
        if index == 0:
            selected = beats[:1]
        elif index == len(names) - 1:
            selected = beats[-1:]
        elif index == 1:
            selected = beats[1:2]
        elif index == len(names) - 2:
            selected = beats[-2:-1]
        else:
            start = 2
            stop = max(start + 1, len(beats) - 2)
            selected = beats[start:stop]
        result[name] = tuple(selected)
    return result


def build_repair_payload(
    *,
    narration: Mapping[str, Any],
    story_map: Mapping[str, Any],
    ledger: FeasibleVisualLedger,
    section_to_beats: Mapping[str, Sequence[str]],
    feasible_observations: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    missing = missing_visual_sections(ledger, section_to_beats)
    return {
        "repair_contract_version": REPAIR_CONTRACT_VERSION,
        "feasible_ledger": ledger.as_dict(),
        "feasible_panel_ids": list(ledger.feasible_panel_ids),
        "feasible_by_beat": {
            beat: [entry.panel_id for entry in ledger.entries if beat in entry.eligible_beats]
            for beat in sorted({beat for entry in ledger.entries for beat in entry.eligible_beats})
        },
        "missing_sections": list(missing),
        "section_to_beats": {str(key): [str(value) for value in values] for key, values in sorted(section_to_beats.items())},
        "story_map": dict(story_map),
        "current_narration": {
            "passages": [dict(item) for item in narration.get("passages", ()) if isinstance(item, Mapping)],
            "ending_kind": narration.get("ending_kind"),
        },
        "feasible_observations": [dict(item) for item in feasible_observations],
        "constraints": {
            "same_pinned_model": True,
            "allowed_panel_ids_only": True,
            "preserve_causal_order": True,
            "no_copied_dialogue": True,
            "no_invented_facts": True,
            "target_passages": "4-6",
            "target_words": "115-125",
            "target_duration_s": "50-60",
        },
    }


def validate_repaired_panel_references(
    value: Mapping[str, Any],
    *,
    ledger: FeasibleVisualLedger,
    allowed_claim_ids: set[str],
) -> dict[str, Any]:
    """Reject any repair that cites a panel outside the feasible ledger."""

    feasible = set(ledger.feasible_panel_ids)
    raw_claims = value.get("claims")
    raw_passages = value.get("passages")
    if not isinstance(raw_claims, list) or not isinstance(raw_passages, list):
        raise VisualNarrativeRepairError("repair evidence graph is incomplete", "visual.narrative_repair_ungrounded")
    claim_refs: dict[str, set[str]] = {}
    for raw_claim in raw_claims:
        if not isinstance(raw_claim, Mapping):
            raise VisualNarrativeRepairError("repair claim is malformed", "visual.narrative_repair_ungrounded")
        claim_id = str(raw_claim.get("claim_id", ""))
        refs = raw_claim.get("evidence_panel_ids")
        if not claim_id or claim_id not in allowed_claim_ids or not isinstance(refs, list) or not refs:
            raise VisualNarrativeRepairError("repair claim is unsupported", "visual.narrative_repair_ungrounded")
        reference_set = {str(ref) for ref in refs}
        if not reference_set <= feasible:
            raise VisualNarrativeRepairError("repair claim cites an infeasible panel", "visual.narrative_repair_ungrounded")
        claim_refs[claim_id] = reference_set
    if not claim_refs:
        raise VisualNarrativeRepairError("repair has no claims", "visual.narrative_repair_ungrounded")
    covered_claim_refs: dict[str, set[str]] = {claim_id: set() for claim_id in claim_refs}
    non_hook_orders: list[int] = []
    for passage_index, raw_passage in enumerate(raw_passages):
        if not isinstance(raw_passage, Mapping):
            raise VisualNarrativeRepairError("repair passage is malformed", "visual.narrative_repair_ungrounded")
        claim_ids = raw_passage.get("claim_ids")
        refs = raw_passage.get("evidence_panel_ids")
        if not isinstance(claim_ids, list) or not claim_ids or not isinstance(refs, list) or not refs:
            raise VisualNarrativeRepairError("repair passage evidence is incomplete", "visual.narrative_repair_ungrounded")
        if not set(map(str, claim_ids)) <= set(claim_refs) or not {str(ref) for ref in refs} <= feasible:
            raise VisualNarrativeRepairError("repair passage cites unsupported evidence", "visual.narrative_repair_ungrounded")
        passage_refs = {str(ref) for ref in refs}
        for claim_id in map(str, claim_ids):
            covered_claim_refs[claim_id].update(passage_refs & claim_refs[claim_id])
        if passage_index > 0:
            non_hook_orders.append(min(next(entry.source_order for entry in ledger.entries if entry.panel_id == str(ref)) for ref in passage_refs))
    if non_hook_orders != sorted(non_hook_orders):
        raise VisualNarrativeRepairError("repair chronology is not ordered", "visual.narrative_repair_ungrounded")
    if any(covered_claim_refs[claim_id] != required for claim_id, required in claim_refs.items()):
        raise VisualNarrativeRepairError("repair claim evidence is incomplete", "visual.narrative_repair_ungrounded")
    return {"claims": [dict(item) for item in raw_claims], "passages": [dict(item) for item in raw_passages]}


def validate_repaired_section_visual_coverage(
    passages: Sequence[Mapping[str, Any]],
    *,
    ledger: FeasibleVisualLedger,
    section_to_beats: Mapping[str, Sequence[str]],
    missing_sections: Sequence[str],
) -> None:
    """Require every section missing from the visual ledger to be repaired.

    Panel-reference validation alone is insufficient: a provider can return a
    structurally valid narration while retaining the old citations for a
    section whose every ROI was rejected.  This gate maps passage positions to
    the same section order used by ``pipeline.generate_script`` and requires a
    feasible citation for each previously missing section before the result
    may be cached or persisted.
    """

    if not isinstance(passages, Sequence) or isinstance(passages, (str, bytes)):
        raise VisualNarrativeRepairError(
            "repaired passages are malformed",
            "visual.narrative_repair_ungrounded",
        )
    section_names = tuple(str(section).strip() for section in section_to_beats if str(section).strip())
    if not section_names or len(passages) < len(section_names):
        raise VisualNarrativeRepairError(
            "repaired passages do not cover every section",
            "visual.narrative_repair_ungrounded",
        )
    feasible_panel_ids = set(ledger.feasible_panel_ids)
    missing = tuple(str(section).strip() for section in missing_sections if str(section).strip())
    if not missing:
        return

    passage_sections: list[str] = []
    passage_count = len(passages)
    for index in range(passage_count):
        if index == 0:
            section = "hook"
        elif index == passage_count - 1:
            section = "cta"
        elif index == passage_count - 2 and passage_count >= 5:
            section = "twist"
        else:
            section = "conflict" if index >= 2 else "setup"
        passage_sections.append(section)

    for section in missing:
        positions = [index for index, name in enumerate(passage_sections) if name == section]
        if not positions:
            raise VisualNarrativeRepairError(
                "repaired passages omit a missing section",
                "visual.narrative_repair_ungrounded",
            )
        repaired = False
        for index in positions:
            passage = passages[index]
            references = passage.get("evidence_panel_ids") if isinstance(passage, Mapping) else None
            if isinstance(references, list) and any(str(panel_id) in feasible_panel_ids for panel_id in references):
                repaired = True
                break
        if not repaired:
            raise VisualNarrativeRepairError(
                "repaired section still has no feasible visual citation",
                "visual.narrative_repair_ungrounded",
            )


def repair_cache_key(*, ledger: FeasibleVisualLedger, model_identity_hash: str, prompt_sha256: str, narration_hash: str) -> str:
    return _hash({
        "contract_version": REPAIR_CONTRACT_VERSION,
        "ledger_hash": ledger.ledger_hash,
        "model_identity_hash": model_identity_hash,
        "prompt_sha256": prompt_sha256,
        "narration_hash": narration_hash,
    })


__all__ = [
    "FeasibleVisualLedger",
    "FeasibleVisualRecord",
    "MAX_REPAIR_ATTEMPTS",
    "REPAIR_CONTRACT_VERSION",
    "REPAIR_PROMPT_VERSION",
    "VISUAL_SECTION_REMAP_VERSION",
    "VisualNarrativeRepairError",
    "build_feasible_visual_ledger",
    "build_repair_payload",
    "coalesce_adjacent_duplicate_panel_passages",
    "default_section_to_beats",
    "load_repair_prompt",
    "missing_visual_sections",
    "remap_same_beat_panel_citations",
    "repair_cache_key",
    "validate_repaired_panel_references",
    "validate_repaired_section_visual_coverage",
]
