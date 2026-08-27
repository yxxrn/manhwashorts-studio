"""Feasible-visual ledger and bounded narrative repair primitives.

This boundary is intentionally local-first: the renderer never relaxes a
visual gate.  A repair can only cite a panel that already has a feasible,
lineage-checked ROI in the current review contract.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.services import editorial_visual_planner, framing_analysis, subtitle_karaoke

REPAIR_CONTRACT_VERSION = "visual_narrative_repair_v3"
VISUAL_SECTION_REMAP_VERSION = "visual_section_remap_v1"
REPAIR_PROMPT_VERSION = "visual-narrative-repair-v4"
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
    source_asset_checksum: str = ""

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
            "source_asset_checksum": self.source_asset_checksum,
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
                source_asset_checksum=str(value.get("source_asset_checksum", "")),
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


@dataclass(frozen=True)
class FeasibleRenderPanel:
    """Canonical panel reference and the single ROI decision for rendering."""

    panel_id: str
    panel_region_id: str
    source_asset_id: str
    source_order: int
    source_asset_checksum: str
    panel_size: tuple[int, int]
    selected_roi: tuple[tuple[str, Any], ...]
    evidence_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "panel_id": self.panel_id,
            "panel_region_id": self.panel_region_id,
            "source_asset_id": self.source_asset_id,
            "source_order": self.source_order,
            "source_asset_checksum": self.source_asset_checksum,
            "panel_size": list(self.panel_size),
            "selected_roi": dict(self.selected_roi),
            "evidence_hash": self.evidence_hash,
        }


@dataclass(frozen=True)
class FeasibleRenderPlan:
    """Immutable feasibility output shared by repair, planning, and render.

    The plan contains references and one already-approved ROI per panel.  It
    never contains image bytes or a second panel identity model.  Render code
    may validate current bytes against these references, but must not perform
    another feasibility search.
    """

    ledger_hash: str
    panels: tuple[FeasibleRenderPanel, ...]
    plan_hash: str = field(init=False)

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.panels, key=lambda item: (item.source_order, item.panel_id)))
        if len({item.panel_id for item in ordered}) != len(ordered):
            raise VisualNarrativeRepairError(
                "render plan contains duplicate panel references",
                "visual.narrative_repair_stale_ledger",
            )
        object.__setattr__(self, "panels", ordered)
        object.__setattr__(self, "plan_hash", _hash({
            "contract_version": "feasible-render-plan-v1",
            "ledger_hash": self.ledger_hash,
            "panels": [item.as_dict() for item in ordered],
        }))

    @classmethod
    def from_ledger(cls, ledger: FeasibleVisualLedger) -> FeasibleRenderPlan:
        panels: list[FeasibleRenderPanel] = []
        for entry in ledger.entries:
            if not entry.feasible_rois:
                continue
            selected = min(
                entry.feasible_rois,
                key=lambda roi: (
                    float(dict(roi.get("telemetry", {})).get("edge_connected_blank_fraction", 1.0)),
                    -float(dict(roi.get("telemetry", {})).get("protected_retained_fraction", 0.0)),
                    str(roi.get("kind", "")),
                    str(roi.get("roi_label", "")),
                    tuple(int(value) for value in roi.get("crop_box", ())),
                ),
            )
            panels.append(
                FeasibleRenderPanel(
                    panel_id=entry.panel_id,
                    panel_region_id=entry.panel_region_id,
                    source_asset_id=entry.source_asset_id,
                    source_order=entry.source_order,
                    source_asset_checksum=entry.source_asset_checksum,
                    panel_size=entry.panel_size,
                    selected_roi=tuple(sorted(dict(selected).items(), key=lambda item: item[0])),
                    evidence_hash=entry.evidence_hash,
                )
            )
        return cls(ledger_hash=ledger.ledger_hash, panels=tuple(panels))

    @property
    def panel_ids(self) -> tuple[str, ...]:
        return tuple(item.panel_id for item in self.panels)

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": "feasible-render-plan-v1",
            "ledger_hash": self.ledger_hash,
            "plan_hash": self.plan_hash,
            "panels": [item.as_dict() for item in self.panels],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> FeasibleRenderPlan:
        try:
            if value.get("contract_version") != "feasible-render-plan-v1":
                raise ValueError("unsupported render plan")
            panels = tuple(
                FeasibleRenderPanel(
                    panel_id=str(item["panel_id"]),
                    panel_region_id=str(item["panel_region_id"]),
                    source_asset_id=str(item["source_asset_id"]),
                    source_order=int(item["source_order"]),
                    source_asset_checksum=str(item.get("source_asset_checksum", "")),
                    panel_size=tuple(int(part) for part in item["panel_size"]),
                    selected_roi=tuple(sorted(dict(item["selected_roi"]).items())),
                    evidence_hash=str(item["evidence_hash"]),
                )
                for item in value["panels"]
            )
            plan = cls(ledger_hash=str(value["ledger_hash"]), panels=panels)
        except (KeyError, TypeError, ValueError):
            raise VisualNarrativeRepairError(
                "render plan is malformed",
                "visual.narrative_repair_stale_ledger",
            ) from None
        if str(value.get("plan_hash", "")) != plan.plan_hash:
            raise VisualNarrativeRepairError(
                "render plan hash is stale",
                "visual.narrative_repair_stale_ledger",
            )
        return plan

    def require_panel(self, panel_id: str) -> FeasibleRenderPanel:
        for item in self.panels:
            if item.panel_id == str(panel_id):
                return item
        raise VisualNarrativeRepairError(
            "render plan panel reference is not feasible",
            "visual.narrative_repair_ungrounded",
        )

    def validate_current_panel(
        self,
        panel_id: str,
        *,
        source_asset_id: str,
        source_checksum: str,
        evidence_hash: str,
    ) -> None:
        """Validate current materialized lineage without recomputing ROI."""

        panel = self.require_panel(panel_id)
        if panel.source_asset_id != str(source_asset_id) or panel.evidence_hash != str(evidence_hash):
            raise VisualNarrativeRepairError(
                "render plan lineage is stale",
                "visual.narrative_repair_stale_ledger",
            )
        if panel.source_asset_checksum and panel.source_asset_checksum != str(source_checksum):
            raise VisualNarrativeRepairError(
                "render plan source checksum is stale",
                "visual.narrative_repair_stale_ledger",
            )


def build_feasible_visual_ledger(
    candidates: Sequence[object],
    *,
    profile: object,
    model_identity_hash: str,
    allow_source_resolution_warning: bool = False,
    allow_conservative_full_panel: bool = False,
) -> FeasibleVisualLedger:
    """Evaluate every candidate ROI and retain only genuinely feasible panels."""

    built: list[FeasibleVisualRecord] = []
    target_size = (int(profile.final_width), int(profile.final_height))
    for candidate in sorted(
        candidates,
        key=lambda item: (int(item.source_order), str(item.panel_id), str(item.panel_region_id)),
    ):
        if editorial_visual_planner.is_title_page_family(
            str(
                getattr(
                    getattr(candidate, "panel_candidate", None),
                    "source_family",
                    "",
                )
                or ""
            ),
            source_order=getattr(candidate, "source_order", None),
        ):
            continue
        manifest = getattr(candidate, "source_upscale_manifest", None)
        if not editorial_visual_planner.reference_profile.review_panel_source_geometry_is_renderable(
            tuple(int(value) for value in candidate.panel_size),
            manifest,
        ):
            continue
        resolution_state = "NATIVE"
        if isinstance(manifest, Mapping):
            resolution_state = str(manifest.get("resolution_state") or "NATIVE")
        feasible_rois: list[dict[str, Any]] = []
        try:
            ready_evidence = editorial_visual_planner.visual_scoring.require_reference_ready_visual_evidence(
                candidate.visual_evidence,
                allow_conservative_full_panel=allow_conservative_full_panel,
            )
        except Exception:
            continue
        allow_low_resolution = bool(
            allow_source_resolution_warning
            and isinstance(manifest, Mapping)
            and manifest.get("policy_id") == "review_silent_source_upscale_v1"
            and manifest.get("resolution_state") == "LOW_SOURCE_RESOLUTION"
            and manifest.get("non_native_warning") == "review.low_source_resolution"
        )
        for roi in tuple(getattr(candidate, "roi_alternatives", ()) or ()):
            try:
                is_feasible, telemetry = framing_analysis.candidate_is_feasible(
                    tuple(int(value) for value in roi.crop_box),
                    ready_evidence,
                    candidate.border_mask,
                    tuple(int(value) for value in candidate.panel_size),
                    target_size,
                    allow_source_resolution_warning=allow_low_resolution,
                    allow_conservative_full_panel=allow_conservative_full_panel,
                    review_aggressive_crop=allow_source_resolution_warning,
                    blank_target_fraction=getattr(profile, "framing_blank_target_fraction", None),
                )
            except Exception:
                continue
            if not is_feasible:
                continue
            edge_blank = getattr(roi, "edge_blank_fraction", None)
            if edge_blank is not None and float(edge_blank) > editorial_visual_planner.reference_profile.REVIEW_MAX_FRAME_EDGE_BLANK_FRACTION:
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
                source_asset_checksum=str(getattr(candidate, "source_checksum", "")),
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


def _narration_passages(narration: object) -> tuple[Mapping[str, Any], ...]:
    raw = narration.get("passages") if isinstance(narration, Mapping) else getattr(narration, "passages", None)
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    return tuple(item for item in raw if isinstance(item, Mapping))


def _narration_claims(narration: object) -> tuple[Mapping[str, Any], ...]:
    graph = narration.get("evidence_graph") if isinstance(narration, Mapping) else getattr(narration, "evidence_graph", None)
    raw = graph.get("claims") if isinstance(graph, Mapping) else None
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    return tuple(item for item in raw if isinstance(item, Mapping))


def _passage_section_name(index: int, passage_count: int, section_names: Sequence[str]) -> str:
    if not section_names:
        return ""
    if len(section_names) == 1:
        return str(section_names[0])
    if index == 0:
        return str(section_names[0])
    if index == passage_count - 1:
        return str(section_names[-1])
    if index == 1:
        return str(section_names[1])
    if index == passage_count - 2 and len(section_names) >= 4:
        return str(section_names[-2])
    return str(section_names[min(2, len(section_names) - 1)])


def narration_sections_with_infeasible_citations(narration: object, ledger: FeasibleVisualLedger, section_to_beats: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
    """Return editorial sections whose current claim/passage citations are stale."""
    passages = _narration_passages(narration)
    if not passages:
        return ()
    section_names = tuple(str(section) for section in section_to_beats if str(section).strip())
    if not section_names:
        return ()
    feasible_ids = set(ledger.feasible_panel_ids)
    claim_refs = {
        str(claim.get("claim_id", "")): {str(panel_id) for panel_id in (claim.get("evidence_panel_ids") or ()) if str(panel_id).strip()}
        for claim in _narration_claims(narration)
        if str(claim.get("claim_id", "")).strip()
    }
    stale: set[str] = set()
    for index, passage in enumerate(passages):
        refs = passage.get("evidence_panel_ids")
        passage_refs = {str(panel_id) for panel_id in refs if str(panel_id).strip()} if isinstance(refs, list) else set()
        invalid = not passage_refs or not passage_refs <= feasible_ids
        claim_ids = passage.get("claim_ids")
        if not invalid and isinstance(claim_ids, list):
            for claim_id in map(str, claim_ids):
                refs_for_claim = claim_refs.get(claim_id)
                if refs_for_claim is not None and (not refs_for_claim or not refs_for_claim <= feasible_ids):
                    invalid = True
                    break
        if invalid:
            section = _passage_section_name(index, len(passages), section_names)
            if section:
                stale.add(section)
    return tuple(section for section in section_names if section in stale)


def narration_sections_with_subtitle_overflow(
    narration: object,
    section_to_beats: Mapping[str, Sequence[str]],
) -> tuple[str, ...]:
    """Return sections whose prose cannot satisfy the fixed subtitle layout."""
    passages = _narration_passages(narration)
    if not passages:
        return ()
    raw_duration = (
        narration.get("estimated_duration_s")
        if isinstance(narration, Mapping)
        else getattr(narration, "estimated_duration_s", None)
    )
    failed = subtitle_karaoke.provisional_caption_overflow_passage_indexes(
        passages,
        raw_duration,
    )
    if not failed:
        return ()
    section_names = tuple(str(section) for section in section_to_beats if str(section).strip())
    overflow = {
        _passage_section_name(index, len(passages), section_names)
        for index in failed
    }
    return tuple(section for section in section_names if section in overflow)


def repair_scope_sections(narration: object, ledger: FeasibleVisualLedger, section_to_beats: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
    """Union visual, citation-lineage, and subtitle-renderability repair scope."""
    required = set(missing_visual_sections(ledger, section_to_beats))
    required.update(narration_sections_with_infeasible_citations(narration, ledger, section_to_beats))
    required.update(narration_sections_with_subtitle_overflow(narration, section_to_beats))
    ordered = [str(section) for section in section_to_beats if str(section).strip() and str(section) in required]
    extras = sorted(required - set(ordered))
    return (*ordered, *extras)


def _normalize_repair_reference_aliases(value: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the provider's legacy panel_ids alias before local validation.

    The alias is accepted only as a transport spelling.  The returned payload
    always uses evidence_panel_ids, and all existing feasibility, lineage,
    chronology, and claim-union checks remain authoritative.
    """

    if not isinstance(value, Mapping):
        raise VisualNarrativeRepairError(
            "repair evidence graph is incomplete",
            "visual.narrative_repair_ungrounded",
        )
    normalized = dict(value)
    for field_name in ("claims", "passages"):
        raw_rows = value.get(field_name)
        if not isinstance(raw_rows, list):
            raise VisualNarrativeRepairError(
                "repair evidence graph is incomplete",
                "visual.narrative_repair_ungrounded",
            )
        rows: list[dict[str, Any]] = []
        for raw_row in raw_rows:
            if not isinstance(raw_row, Mapping):
                raise VisualNarrativeRepairError(
                    "repair evidence row is malformed",
                    "visual.narrative_repair_ungrounded",
                )
            row = dict(raw_row)
            if "evidence_panel_ids" not in row and "panel_ids" in row:
                refs = row["panel_ids"]
                if not isinstance(refs, list):
                    raise VisualNarrativeRepairError(
                        "repair panel references are malformed",
                        "visual.narrative_repair_ungrounded",
                    )
                row["evidence_panel_ids"] = [str(ref) for ref in refs]
            rows.append(row)
        normalized[field_name] = rows
    return normalized


def _repair_claim_transport_key(claim_id: str) -> str | None:
    """Collapse only the provider's terminal claim-number underscore drift."""

    match = re.fullmatch(r"(?P<prefix>.+__claim)_?(?P<number>\d+)", str(claim_id))
    if match is None:
        return None
    return f"{match.group('prefix')}{match.group('number')}"


def canonicalize_repair_claim_ids(
    value: Mapping[str, Any],
    *,
    allowed_claim_ids: set[str],
) -> dict[str, Any]:
    """Map a narrow claim-ID transport drift back to trusted story lineage.

    Exact trusted IDs always win.  The only tolerated non-exact spelling is
    ``__claimN`` versus ``__claim_N`` at the terminal numeric suffix, and it
    is accepted only when that canonical spelling resolves to exactly one
    trusted StoryMap claim.  Unknown or ambiguous IDs remain fail-closed.
    """

    normalized = _normalize_repair_reference_aliases(value)
    allowed = {str(claim_id) for claim_id in allowed_claim_ids if str(claim_id).strip()}
    by_transport_key: dict[str, list[str]] = {}
    for claim_id in sorted(allowed):
        key = _repair_claim_transport_key(claim_id)
        if key is not None:
            by_transport_key.setdefault(key, []).append(claim_id)

    def resolve(claim_id: object) -> str:
        if not isinstance(claim_id, str) or not claim_id:
            raise VisualNarrativeRepairError(
                "repair claim is unsupported",
                "visual.narrative_repair_ungrounded",
            )
        if claim_id in allowed:
            return claim_id
        key = _repair_claim_transport_key(claim_id)
        candidates = by_transport_key.get(key, ()) if key is not None else ()
        if len(candidates) != 1:
            raise VisualNarrativeRepairError(
                "repair claim is unsupported",
                "visual.narrative_repair_ungrounded",
            )
        return candidates[0]

    claims: list[dict[str, Any]] = []
    for raw_claim in normalized["claims"]:
        claim = dict(raw_claim)
        claim["claim_id"] = resolve(claim.get("claim_id"))
        claims.append(claim)

    passages: list[dict[str, Any]] = []
    for raw_passage in normalized["passages"]:
        passage = dict(raw_passage)
        claim_ids = passage.get("claim_ids")
        if not isinstance(claim_ids, list) or not claim_ids:
            raise VisualNarrativeRepairError(
                "repair passage evidence is incomplete",
                "visual.narrative_repair_ungrounded",
            )
        passage["claim_ids"] = [resolve(claim_id) for claim_id in claim_ids]
        passages.append(passage)

    return {**normalized, "claims": claims, "passages": passages}


def remap_same_beat_panel_citations(
    value: Mapping[str, Any],
    *,
    ledger: FeasibleVisualLedger,
    section_to_beats: Mapping[str, Sequence[str]],
    allowed_claim_panel_ids: Mapping[str, Sequence[str] | set[str]] | None = None,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Prefer safer same-story evidence without rebinding claim lineage."""

    normalized = _normalize_repair_reference_aliases(value)
    raw_claims = normalized["claims"]
    raw_passages = normalized["passages"]
    entries_by_panel = {entry.panel_id: entry for entry in ledger.entries}
    feasible_ids = set(entries_by_panel)
    ordered_sections = tuple(str(section) for section in section_to_beats)
    remaps: list[dict[str, Any]] = []
    usage_by_panel: dict[str, int] = {}

    allowed_lineage: dict[str, set[str]] | None = None
    if allowed_claim_panel_ids is not None:
        allowed_lineage = {
            str(claim_id): {
                str(panel_id)
                for panel_id in panel_ids
                if str(panel_id) in feasible_ids
            }
            for claim_id, panel_ids in allowed_claim_panel_ids.items()
        }

    claim_evidence_by_id: dict[str, set[str]] = {}
    for raw_claim in raw_claims:
        if not isinstance(raw_claim, Mapping):
            raise VisualNarrativeRepairError(
                "repair claim is malformed",
                "visual.narrative_repair_ungrounded",
            )
        claim_id = str(raw_claim.get("claim_id", ""))
        refs = raw_claim.get("evidence_panel_ids")
        claim_evidence_by_id[claim_id] = (
            {str(ref) for ref in refs} if isinstance(refs, list) else set()
        )

    def passage_min_source_order(raw_passage: Mapping[str, Any]) -> int | None:
        refs = raw_passage.get("evidence_panel_ids")
        if not isinstance(refs, list) or not refs:
            return None
        orders = [
            entries_by_panel[str(ref)].source_order
            for ref in refs
            if str(ref) in entries_by_panel
        ]
        return min(orders) if orders else None

    original_passage_orders = [
        passage_min_source_order(raw_passage)
        if isinstance(raw_passage, Mapping)
        else None
        for raw_passage in raw_passages
    ]
    original_non_hook_orders = original_passage_orders[1:]
    preserve_non_hook_chronology = bool(
        original_non_hook_orders
        and all(order is not None for order in original_non_hook_orders)
        and original_non_hook_orders == sorted(original_non_hook_orders)
    )

    def replacement_for(
        panel_id: str,
        section: str,
        *,
        allowed_panels: set[str] | None = None,
        min_source_order: int | None = None,
        max_source_order: int | None = None,
    ) -> str:
        current = entries_by_panel.get(panel_id)
        must_replace = allowed_panels is not None and panel_id not in allowed_panels
        if current is None and allowed_panels is None:
            return panel_id

        section_beats = {
            str(value) for value in (section_to_beats.get(section) or ())
        }
        current_beats = set(current.eligible_beats) if current is not None else set()
        candidate_beats = section_beats if must_replace or current is None else current_beats
        if not candidate_beats and current is not None:
            candidate_beats = current_beats

        current_blank = 1.0
        if current is not None:
            current_blank = float(
                current.visual_strengths.get("edge_connected_blank_fraction", 1.0)
            )
            if not math.isfinite(current_blank) or not 0.0 <= current_blank <= 1.0:
                current_blank = 1.0

        available = [
            entry
            for entry in ledger.entries
            if entry.panel_id != panel_id
            and (allowed_panels is None or entry.panel_id in allowed_panels)
            and (not candidate_beats or candidate_beats.intersection(entry.eligible_beats))
            and math.isfinite(
                float(entry.visual_strengths.get("edge_connected_blank_fraction", 1.0))
            )
            and usage_by_panel.get(entry.panel_id, 0) < len(entry.feasible_rois)
            and 0.0
            <= float(entry.visual_strengths.get("edge_connected_blank_fraction", 1.0))
            <= 1.0
            and (min_source_order is None or entry.source_order >= min_source_order)
            and (max_source_order is None or entry.source_order <= max_source_order)
        ]

        if must_replace or current is None:
            alternatives = available
            reason = "story-lineage feasible replacement"
        else:
            alternatives = [
                entry
                for entry in available
                if float(
                    entry.visual_strengths.get("edge_connected_blank_fraction", 1.0)
                )
                < current_blank - 1e-9
            ]
            reason = "same-beat lower edge-connected blank fraction"
            if (
                not alternatives
                and current is not None
                and usage_by_panel.get(panel_id, 0) >= len(current.feasible_rois)
            ):
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
                "from_source_order": current.source_order if current is not None else None,
                "to_source_order": selected.source_order,
                "from_blank_fraction": current_blank if current is not None else None,
                "to_blank_fraction": float(
                    selected.visual_strengths.get("edge_connected_blank_fraction", 1.0)
                ),
                "same_eligible_beats": sorted(
                    candidate_beats.intersection(selected.eligible_beats)
                ),
                "ledger_hash": ledger.ledger_hash,
                "reason": reason,
            }
        )
        return selected.panel_id

    mapped_claim_refs: dict[str, list[str]] = {}
    passages: list[dict[str, Any]] = []
    for index, raw_passage in enumerate(raw_passages):
        if not isinstance(raw_passage, Mapping):
            raise VisualNarrativeRepairError(
                "repair passage is malformed",
                "visual.narrative_repair_ungrounded",
            )
        section = ordered_sections[index] if index < len(ordered_sections) else ""
        refs = raw_passage.get("evidence_panel_ids")
        claim_ids = raw_passage.get("claim_ids")
        if not isinstance(refs, list) or not refs:
            raise VisualNarrativeRepairError(
                "repair passage evidence is incomplete",
                "visual.narrative_repair_ungrounded",
            )
        if not isinstance(claim_ids, list) or not claim_ids:
            raise VisualNarrativeRepairError(
                "repair passage evidence is incomplete",
                "visual.narrative_repair_ungrounded",
            )

        min_source_order: int | None = None
        max_source_order: int | None = None
        if preserve_non_hook_chronology and index > 0:
            if index > 1 and passages:
                min_source_order = passage_min_source_order(passages[-1])
            if index + 1 < len(original_passage_orders):
                max_source_order = original_passage_orders[index + 1]

        mapped_refs: list[str] = []
        passage_claim_ids = [str(claim_id) for claim_id in claim_ids]
        for raw_ref in refs:
            panel_id = str(raw_ref)
            supporting_claims = [
                claim_id
                for claim_id in passage_claim_ids
                if panel_id in claim_evidence_by_id.get(claim_id, set())
            ]
            allowed_panels: set[str] | None = None
            if allowed_lineage is not None and supporting_claims:
                lineage_sets = [
                    allowed_lineage.get(claim_id, set()) for claim_id in supporting_claims
                ]
                allowed_panels = (
                    set.intersection(*lineage_sets) if lineage_sets else set()
                )
            mapped = replacement_for(
                panel_id,
                section,
                allowed_panels=allowed_panels,
                min_source_order=min_source_order,
                max_source_order=max_source_order,
            )
            if mapped not in mapped_refs:
                mapped_refs.append(mapped)
            for claim_id in supporting_claims:
                if allowed_lineage is None or mapped in allowed_lineage.get(claim_id, set()):
                    claim_refs = mapped_claim_refs.setdefault(claim_id, [])
                    if mapped not in claim_refs:
                        claim_refs.append(mapped)

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
        if allowed_lineage is not None:
            covered = mapped_claim_refs.get(claim_id, [])
        else:
            covered = []
            for passage in passages:
                if claim_id not in {
                    str(item) for item in passage.get("claim_ids", ())
                }:
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
    *,
    minimum_passage_count: int = 0,
) -> tuple[list[dict[str, Any]], tuple[dict[str, Any], ...]]:
    """Merge adjacent passages that have only the same feasible panel.

    This is a review-only continuity repair. It preserves every spoken word,
    claim reference, and evidence reference while avoiding an impossible
    consecutive reuse of one exact panel when no alternate safe visual exists.
    """

    minimum_passage_count = max(0, int(minimum_passage_count))
    merged: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    source_count = len(passages)
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
                and source_count - (len(provenance) + 1) >= minimum_passage_count
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


def feasible_story_claims(
    story_map: Mapping[str, Any],
    ledger: FeasibleVisualLedger,
) -> list[dict[str, Any]]:
    """Return only StoryMap claims with original evidence still feasible."""

    feasible = set(ledger.feasible_panel_ids)
    rows: list[dict[str, Any]] = []
    raw_claims = story_map.get("claims", ())
    if not isinstance(raw_claims, Sequence) or isinstance(raw_claims, (str, bytes)):
        return rows
    for raw_claim in raw_claims:
        if not isinstance(raw_claim, Mapping):
            continue
        claim_id = str(raw_claim.get("claim_id", "")).strip()
        panel_ids = raw_claim.get("panel_ids")
        if not claim_id or not isinstance(panel_ids, list):
            continue
        evidence_panel_ids = [
            str(panel_id)
            for panel_id in panel_ids
            if str(panel_id) in feasible
        ]
        if not evidence_panel_ids:
            continue
        row: dict[str, Any] = {"claim_id": claim_id}
        for key in ("claim_type", "text", "qualification"):
            if key in raw_claim:
                row[key] = raw_claim.get(key)
        row["evidence_panel_ids"] = evidence_panel_ids
        rows.append(row)
    return rows


def build_repair_payload(
    *,
    narration: Mapping[str, Any],
    story_map: Mapping[str, Any],
    ledger: FeasibleVisualLedger,
    section_to_beats: Mapping[str, Sequence[str]],
    feasible_observations: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    missing = repair_scope_sections(narration, ledger, section_to_beats)
    render_plan = FeasibleRenderPlan.from_ledger(ledger)
    feasible_claim_rows = feasible_story_claims(story_map, ledger)
    return {
        "repair_contract_version": REPAIR_CONTRACT_VERSION,
        "feasible_ledger": ledger.as_dict(),
        "feasible_render_plan": render_plan.as_dict(),
        "feasible_panel_ids": list(ledger.feasible_panel_ids),
        "feasible_claim_ids": [str(row["claim_id"]) for row in feasible_claim_rows],
        "feasible_claims": feasible_claim_rows,
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
            "allowed_claim_ids_only": True,
            "claim_evidence_must_match_story_lineage": True,
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
    allowed_claim_panel_ids: Mapping[str, Sequence[str] | set[str]] | None = None,
) -> dict[str, Any]:
    """Reject any repair that cites a panel outside the feasible ledger."""

    feasible = set(ledger.feasible_panel_ids)
    normalized = _normalize_repair_reference_aliases(value)
    raw_claims = normalized["claims"]
    raw_passages = normalized["passages"]
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
        if allowed_claim_panel_ids is not None:
            story_lineage = {
                str(ref) for ref in allowed_claim_panel_ids.get(claim_id, ())
            }
            if not story_lineage or not reference_set <= story_lineage:
                raise VisualNarrativeRepairError(
                    "repair claim evidence is outside story lineage",
                    "visual.narrative_repair_ungrounded",
                )
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


def repair_cache_key(
    *,
    ledger: FeasibleVisualLedger,
    model_identity_hash: str,
    prompt_sha256: str,
    narration_hash: str,
    contract_version: str = REPAIR_CONTRACT_VERSION,
) -> str:
    return _hash({
        "contract_version": contract_version,
        "ledger_hash": ledger.ledger_hash,
        "model_identity_hash": model_identity_hash,
        "prompt_sha256": prompt_sha256,
        "narration_hash": narration_hash,
    })


__all__ = [
    "FeasibleRenderPanel",
    "FeasibleRenderPlan",
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
    "narration_sections_with_infeasible_citations",
    "remap_same_beat_panel_citations",
    "repair_scope_sections",
    "repair_cache_key",
    "validate_repaired_panel_references",
    "validate_repaired_section_visual_coverage",
]
