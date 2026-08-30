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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.services import (
    editorial_visual_planner,
    framing_analysis,  # noqa: F401 - retained as the test/integration monkeypatch boundary
    reference_profile,
    subtitle_karaoke,
)
from app.services import (
    script as script_service,
)

REPAIR_CONTRACT_VERSION = "visual_narrative_repair_v12"
REPAIR_EDITORIAL_SECTIONS = ("hook", "setup", "conflict", "twist", "cta")
VISUAL_SECTION_REMAP_VERSION = "visual_section_remap_v1"
REPAIR_PROMPT_VERSION = "visual-narrative-repair-v12"
REPAIR_TARGET_WORD_MIN = 115
REPAIR_TARGET_WORD_GOAL = 120
REPAIR_TARGET_WORD_MAX = 125
REPAIR_ADAPTIVE_MIN_UNIQUE_PANELS = 7
REPAIR_ADAPTIVE_MIN_SHOT_SECONDS = 3.0
REPAIR_ADAPTIVE_TARGET_SHOT_SECONDS = 3.5
REPAIR_DURATION_POLICY_STANDARD = "standard_50_60_v1"
REPAIR_DURATION_POLICY_ADAPTIVE = "coherent_capacity_adaptive_v1"
MAX_REPAIR_ATTEMPTS = 3
HOOK_STORY_SELECTION_VERSION = "grounded_hook_selection_v1"
_HOOK_CURIOSITY_MARKERS = frozenset({
    "but", "however", "instead", "only", "until", "except", "secret",
    "reveals", "revealed", "realizes", "discovers", "hidden", "trap",
    "danger", "risk", "threat", "betrayal", "attack", "impossible",
    "unexpected", "consequence", "cost", "fails", "wrong", "mystery",
})
_FLAT_HOOK_PREFIXES = (
    "we see ", "this panel ", "the panel ", "a man is ", "a woman is ",
    "a character is ", "someone is ", "next ", "then ", "after that ",
)
_VISUAL_RECAP_PATTERNS = (
    r"\bpanels?\b",
    r"\bsequence\b",
    r"\bclose[- ]?ups?\b",
    r"\b(?:is|are|was|were) shown\b",
    r"\bdepicts?\b",
    r"\bvisible\b",
)
_VISUAL_APPEARANCE_PATTERN = r"\b(?:appears?|reappears?)\b"
_STIFF_SPOKEN_PROSE_PATTERNS = (
    r"\bduring the course of\b",
    r"\b(?:main|primary) confrontation phase\b",
    r"\b(?:phase|stage) now\b",
    r"\b(?:responding|following|counter) \w+ that followed\b",
    r"\b(?:together )?now at last\b",
    r"\bthe (?:male|female)\b",
    r"\bswung (?:his|her) forward\b",
    r"\bduel freezes in place\b",
)
_STORY_BRIDGE_PATTERN = (
    r"\b(?:but|so|yet|because|however|instead|while|meanwhile|until|therefore|later|earlier|afterward|before|when|as)\b"
)
_HOOK_ACTION_MARKERS = frozenset({
    "combat", "sword", "swords", "weapon", "weapons", "clash", "attack",
})
_HOOK_REACTION_MARKERS = frozenset({
    "distressed", "surprised", "danger", "threat", "explosion",
})
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


def _hook_claim_score(claim: Mapping[str, Any]) -> int:
    """Rank grounded claims for curiosity without inventing new stakes."""
    text = str(claim.get("text", "") or "").casefold()
    tokens = set(re.findall(r"[a-z']+", text))
    score = sum(marker in tokens for marker in _HOOK_CURIOSITY_MARKERS)
    if any(phrase in text for phrase in ("turns out", "doesn't know", "cannot", "can't", "no longer")):
        score += 2
    if any(word in tokens for word in ("reveal", "twist", "shock", "surprise", "betray")):
        score += 2
    if tokens.intersection(_HOOK_ACTION_MARKERS):
        score += 4
    if tokens.intersection(_HOOK_REACTION_MARKERS):
        score += 2
    return int(score)


def validate_repaired_hook_quality(
    passages: Sequence[Mapping[str, Any]],
    claims: Sequence[Mapping[str, Any]],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail obvious flat recap while keeping hook selection evidence-grounded."""
    if not passages:
        raise VisualNarrativeRepairError("hook passage is missing", "cloud.narrative_hook_weak")
    claim_map = {str(item.get("claim_id", "")): item for item in claims if isinstance(item, Mapping)}
    first = passages[0]
    text = str(first.get("text", "")).strip()
    lower = text.casefold()
    flat_opening = any(lower.startswith(prefix) for prefix in _FLAT_HOOK_PREFIXES)
    first_claim_ids = [str(value) for value in first.get("claim_ids", ())]
    first_score = max(
        (_hook_claim_score(claim_map[claim_id]) for claim_id in first_claim_ids if claim_id in claim_map),
        default=0,
    )
    rows = plan.get("rows") if isinstance(plan, Mapping) else None
    planned_score = 0
    if isinstance(rows, list) and rows and isinstance(rows[0], Mapping):
        planned_score = int(rows[0].get("hook_priority_score", 0) or 0)
    recap_prefix_count = sum(
        str(item.get("text", "")).strip().casefold().startswith(("then ", "next ", "after that "))
        for item in passages
    )
    visual_recap_flags = [
        any(re.search(pattern, str(item.get("text", "")).casefold()) for pattern in _VISUAL_RECAP_PATTERNS)
        or bool(re.search(_VISUAL_APPEARANCE_PATTERN, str(item.get("text", "")).casefold()))
        for item in passages
    ]
    visual_recap_passages = sum(visual_recap_flags)
    appearance_recap_passages = sum(
        bool(re.search(_VISUAL_APPEARANCE_PATTERN, str(item.get("text", "")).casefold()))
        for item in passages
    )
    recap_threshold = max(2, math.ceil(len(passages) * 0.6))
    story_bridge_count = sum(
        bool(re.search(_STORY_BRIDGE_PATTERN, str(item.get("text", "")).casefold()))
        for item in passages
    )
    stiff_spoken_passages = sum(
        any(
            re.search(pattern, str(item.get("text", "")).casefold())
            for pattern in _STIFF_SPOKEN_PROSE_PATTERNS
        )
        for item in passages
    )
    if flat_opening:
        raise VisualNarrativeRepairError("hook opens as a flat panel description", "cloud.narrative_flat_recap")
    if recap_prefix_count >= 2:
        raise VisualNarrativeRepairError("narration reads as a flat sequential recap", "cloud.narrative_flat_recap")
    if visual_recap_passages >= recap_threshold or appearance_recap_passages >= recap_threshold:
        raise VisualNarrativeRepairError(
            "narration reads as visual-description prose instead of a story",
            "cloud.narrative_flat_recap",
        )
    if len(passages) >= 4 and story_bridge_count < 2:
        raise VisualNarrativeRepairError(
            "narration lacks enough story bridges between grounded events",
            "cloud.narrative_flat_recap",
        )
    if stiff_spoken_passages:
        raise VisualNarrativeRepairError(
            "narration uses stiff bureaucratic spoken prose",
            "cloud.narrative_style_stiff",
        )
    if planned_score > 0 and first_score <= 0:
        raise VisualNarrativeRepairError("hook ignores the grounded curiosity claim", "cloud.narrative_hook_weak")
    return {
        "version": HOOK_STORY_SELECTION_VERSION,
        "status": "pass",
        "hook_claim_score": first_score,
        "planned_hook_score": planned_score,
        "flat_recap_detected": False,
        "visual_recap_passage_count": visual_recap_passages,
        "appearance_recap_passage_count": appearance_recap_passages,
        "visual_recap_threshold": recap_threshold,
        "story_bridge_count": story_bridge_count,
        "stiff_spoken_passage_count": stiff_spoken_passages,
    }


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
                    *editorial_visual_planner._review_editorial_crop_quality_key(
                        dict(roi.get("telemetry", {})).get("editorial_crop_quality", {}),
                        blank_fraction=float(dict(roi.get("telemetry", {})).get("edge_connected_blank_fraction", 1.0)),
                        base_zoom=float(dict(roi.get("telemetry", {})).get("base_zoom", 999.0)),
                        protected_retained_fraction=float(dict(roi.get("telemetry", {})).get("protected_retained_fraction", 0.0)),
                        preferred_blank_fraction=editorial_visual_planner.reference_profile.REVIEW_PREFERRED_FRAME_EDGE_BLANK_FRACTION,
                    ),
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
    editorial_sections: Sequence[str] | None = None,
) -> FeasibleVisualLedger:
    """Evaluate every candidate ROI and retain only genuinely feasible panels."""

    built: list[FeasibleVisualRecord] = []
    target_size = (int(profile.final_width), int(profile.final_height))
    target_editorial_sections = tuple(
        dict.fromkeys(
            str(value)
            for value in (editorial_sections or ())
            if str(value).strip()
        )
    )
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
        editorial_safe_sections: set[str] = set()
        editorial_safe_beats: set[str] = set()
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
                feasibility_kwargs: dict[str, object] = {}
                if allow_low_resolution:
                    feasibility_kwargs["allow_source_resolution_warning"] = True
                is_feasible, telemetry = (
                    editorial_visual_planner._review_framing_candidate_is_feasible(
                        tuple(int(value) for value in roi.crop_box),
                        ready_evidence,
                        candidate.border_mask,
                        tuple(int(value) for value in candidate.panel_size),
                        target_size,
                        review_aggressive_crop=allow_source_resolution_warning,
                        standard_blank_target=editorial_visual_planner.reference_profile.REVIEW_MAX_FRAME_EDGE_BLANK_FRACTION,
                        allow_conservative_full_panel=allow_conservative_full_panel,
                        **feasibility_kwargs,
                    )
                )
            except Exception:
                continue
            if not is_feasible:
                continue
            telemetry_dict = asdict(telemetry)
            edge_blank = telemetry_dict.get("edge_connected_blank_fraction")
            allowed_blank = (
                editorial_visual_planner.reference_profile.REVIEW_COHERENCE_RESCUE_MAX_FRAME_EDGE_BLANK_FRACTION
                if telemetry_dict.get("fallback_reason")
                == editorial_visual_planner.reference_profile.REVIEW_COHERENCE_RESCUE_REASON
                else editorial_visual_planner.reference_profile.REVIEW_MAX_FRAME_EDGE_BLANK_FRACTION
            )
            if (
                isinstance(edge_blank, (int, float))
                and float(edge_blank) > allowed_blank
            ):
                continue
            sections = (
                target_editorial_sections
                or tuple(str(value) for value in (candidate.eligible_sections or ()))
                or ("",)
            )
            beats = tuple(str(value) for value in (candidate.eligible_beats or ())) or ("",)
            safe_contexts: list[tuple[str, str, Mapping[str, object]]] = []
            for section in sections:
                for beat in beats:
                    metrics = editorial_visual_planner._review_crop_editorial_metrics(
                        candidate,
                        roi,
                        telemetry_dict,
                        section=section,
                        beat=beat,
                    )
                    if editorial_visual_planner._review_editorial_rejection_code(metrics) is None:
                        safe_contexts.append((section, beat, metrics))
            if not safe_contexts:
                continue
            best_section, best_beat, best_metrics = min(
                safe_contexts,
                key=lambda item: editorial_visual_planner._review_editorial_crop_quality_key(
                    item[2],
                    blank_fraction=float(telemetry_dict.get("edge_connected_blank_fraction", 1.0)),
                    base_zoom=float(telemetry_dict.get("base_zoom", 999.0)),
                    protected_retained_fraction=float(
                        telemetry_dict.get("protected_retained_fraction", 0.0)
                    ),
                    preferred_blank_fraction=float(
                        getattr(
                            profile,
                            "framing_blank_target_fraction",
                            editorial_visual_planner.reference_profile.REVIEW_PREFERRED_FRAME_EDGE_BLANK_FRACTION,
                        )
                    ),
                ),
            )
            del best_section, best_beat
            telemetry_dict["editorial_crop_quality"] = dict(best_metrics)
            safe_sections = sorted({section for section, _beat, _metrics in safe_contexts if section})
            safe_beats = sorted({beat for _section, beat, _metrics in safe_contexts if beat})
            editorial_safe_sections.update(safe_sections)
            editorial_safe_beats.update(safe_beats)
            feasible_rois.append({
                "kind": str(roi.kind),
                "roi_label": str(roi.roi_label),
                "crop_box": [int(value) for value in roi.crop_box],
                "telemetry": telemetry_dict,
                "editorial_safe_sections": safe_sections,
                "editorial_safe_beats": safe_beats,
            })
        if not feasible_rois:
            continue
        feasible_rois.sort(
            key=lambda item: (
                *editorial_visual_planner._review_editorial_crop_quality_key(
                    item["telemetry"].get("editorial_crop_quality", {}),
                    blank_fraction=float(item["telemetry"].get("edge_connected_blank_fraction", 1.0)),
                    base_zoom=float(item["telemetry"].get("base_zoom", 999.0)),
                    protected_retained_fraction=float(item["telemetry"].get("protected_retained_fraction", 0.0)),
                    preferred_blank_fraction=float(
                        getattr(
                            profile,
                            "framing_blank_target_fraction",
                            editorial_visual_planner.reference_profile.REVIEW_PREFERRED_FRAME_EDGE_BLANK_FRACTION,
                        )
                    ),
                ),
                str(item.get("kind", "")),
                str(item.get("roi_label", "")),
            )
        )
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
                eligible_sections=tuple(
                    sorted(editorial_safe_sections)
                    if editorial_safe_sections
                    else sorted(str(value) for value in (candidate.eligible_sections or ()))
                ),
                eligible_beats=tuple(
                    sorted(editorial_safe_beats)
                    if editorial_safe_beats
                    else sorted(str(value) for value in (candidate.eligible_beats or ()))
                ),
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


def _passage_visual_durations(
    passages: Sequence[Mapping[str, Any]],
    total_duration_s: object = None,
) -> tuple[float, ...]:
    """Estimate each passage duration using the canonical narration contract."""

    texts = [str(passage.get("text", "")).strip() for passage in passages]
    word_counts = [script_service.narration_word_count(text) for text in texts]
    total_words = sum(word_counts)
    try:
        total_duration = float(total_duration_s)
    except (TypeError, ValueError, OverflowError):
        total_duration = 0.0
    if math.isfinite(total_duration) and total_duration > 0.0 and total_words > 0:
        return tuple(total_duration * count / total_words for count in word_counts)
    return tuple(
        float(script_service.narration_duration_metrics(text, "dramatic")["estimated_duration_s"])
        if text
        else 0.0
        for text in texts
    )


def _panel_visual_slot_capacity(
    panel_id: str,
    ledger: FeasibleVisualLedger,
    *,
    section: str | None = None,
) -> int:
    """Count distinct feasible ROI slots, optionally constrained by editorial role."""

    entry = next((item for item in ledger.entries if item.panel_id == panel_id), None)
    if entry is None:
        return 0
    section_name = str(section or "").strip()
    distinct = {
        (
            str(roi.get("kind", "")),
            str(roi.get("roi_label", "")),
            tuple(int(value) for value in (roi.get("crop_box") or ())),
        )
        for roi in entry.feasible_rois
        if isinstance(roi, Mapping)
        and (
            not section_name
            or section_name
            in {str(value) for value in (roi.get("editorial_safe_sections") or ())}
        )
    }
    return min(
        len(distinct),
        int(reference_profile.REFERENCE_MATCHED_SHORTS_V2.max_canonical_panel_uses),
    )


def _passage_visual_capacity_metrics(
    passages: Sequence[Mapping[str, Any]],
    ledger: FeasibleVisualLedger,
    *,
    total_duration_s: object = None,
) -> tuple[dict[str, Any], ...]:
    durations = _passage_visual_durations(passages, total_duration_s)
    rows: list[dict[str, Any]] = []
    for index, (passage, duration) in enumerate(zip(passages, durations, strict=True)):
        refs = passage.get("evidence_panel_ids")
        panel_ids = tuple(
            dict.fromkeys(str(value) for value in refs if str(value).strip())
        ) if isinstance(refs, list) else ()
        capacity = sum(_panel_visual_slot_capacity(panel_id, ledger) for panel_id in panel_ids)
        required = (
            max(1, math.ceil(duration / reference_profile.REVIEW_MAX_SHOT_SECONDS))
            if duration > 0.0
            else 0
        )
        rows.append({
            "passage_index": index,
            "duration_s": round(duration, 6),
            "required_visual_slots": required,
            "available_visual_slots": capacity,
            "evidence_panel_ids": list(panel_ids),
            "shortfall": max(0, required - capacity),
        })
    return tuple(rows)


def validate_repaired_visual_capacity(
    passages: Sequence[Mapping[str, Any]],
    ledger: FeasibleVisualLedger,
    *,
    total_duration_s: object = None,
) -> None:
    """Reject repaired prose that cannot keep every shot at or below four seconds."""

    if not isinstance(passages, Sequence) or isinstance(passages, (str, bytes)):
        raise VisualNarrativeRepairError(
            "repaired passages are malformed",
            "visual.narrative_repair_ungrounded",
        )
    metrics = _passage_visual_capacity_metrics(
        passages, ledger, total_duration_s=total_duration_s
    )
    if any(int(row["shortfall"]) > 0 for row in metrics):
        raise VisualNarrativeRepairError(
            "repair passage visual capacity is insufficient",
            "visual.narrative_repair_ungrounded",
        )


def narration_sections_with_visual_capacity_shortfall(
    narration: object,
    ledger: FeasibleVisualLedger,
    section_to_beats: Mapping[str, Sequence[str]],
) -> tuple[str, ...]:
    """Return sections whose grounded visual slots cannot satisfy max shot duration."""

    passages = _narration_passages(narration)
    if not passages:
        return ()
    raw_duration = (
        narration.get("estimated_duration_s")
        if isinstance(narration, Mapping)
        else getattr(narration, "estimated_duration_s", None)
    )
    metrics = _passage_visual_capacity_metrics(
        passages, ledger, total_duration_s=raw_duration
    )
    section_names = tuple(
        str(section) for section in section_to_beats if str(section).strip()
    )
    short = {
        _passage_section_name(index, len(passages), section_names)
        for index, row in enumerate(metrics)
        if int(row["shortfall"]) > 0
    }
    return tuple(section for section in section_names if section in short)


def repair_scope_sections(narration: object, ledger: FeasibleVisualLedger, section_to_beats: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
    """Union visual, citation, subtitle, and cadence-capacity repair scope."""
    required = set(missing_visual_sections(ledger, section_to_beats))
    required.update(narration_sections_with_infeasible_citations(narration, ledger, section_to_beats))
    required.update(narration_sections_with_subtitle_overflow(narration, section_to_beats))
    required.update(
        narration_sections_with_visual_capacity_shortfall(
            narration, ledger, section_to_beats
        )
    )
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

    def allowed_panels_for_reference(
        raw_passage: Mapping[str, Any], panel_id: str
    ) -> set[str] | None:
        if allowed_lineage is None:
            return None
        claim_ids = raw_passage.get("claim_ids")
        if not isinstance(claim_ids, list):
            return set()
        supporting_claims = [
            str(claim_id)
            for claim_id in claim_ids
            if panel_id in claim_evidence_by_id.get(str(claim_id), set())
        ]
        if not supporting_claims:
            return set()
        lineage_sets = [allowed_lineage.get(claim_id, set()) for claim_id in supporting_claims]
        return set.intersection(*lineage_sets) if lineage_sets else set()

    def passage_max_repairable_source_order(
        raw_passage: Mapping[str, Any], section: str
    ) -> int | None:
        refs = raw_passage.get("evidence_panel_ids")
        if not isinstance(refs, list) or not refs:
            return None
        section_beats = {str(value) for value in (section_to_beats.get(section) or ())}
        maxima: list[int] = []
        for raw_ref in refs:
            panel_id = str(raw_ref)
            current = entries_by_panel.get(panel_id)
            allowed_panels = allowed_panels_for_reference(raw_passage, panel_id)
            current_beats = set(current.eligible_beats) if current is not None else set()
            candidate_beats = current_beats or section_beats
            candidates = [
                entry
                for entry in ledger.entries
                if (allowed_panels is None or entry.panel_id in allowed_panels)
                and (not candidate_beats or candidate_beats.intersection(entry.eligible_beats))
                and math.isfinite(
                    float(entry.visual_strengths.get("edge_connected_blank_fraction", 1.0))
                )
                and 0.0
                <= float(entry.visual_strengths.get("edge_connected_blank_fraction", 1.0))
                <= 1.0
            ]
            if not candidates:
                return None
            maxima.append(max(entry.source_order for entry in candidates))
        return min(maxima) if maxima else None

    def replacement_for(
        panel_id: str,
        section: str,
        *,
        allowed_panels: set[str] | None = None,
        min_source_order: int | None = None,
        max_source_order: int | None = None,
    ) -> str:
        current = entries_by_panel.get(panel_id)
        lineage_violation = allowed_panels is not None and panel_id not in allowed_panels
        chronology_violation = current is not None and (
            (min_source_order is not None and current.source_order < min_source_order)
            or (max_source_order is not None and current.source_order > max_source_order)
        )
        must_replace = lineage_violation or chronology_violation
        if current is None and allowed_panels is None:
            return panel_id

        section_beats = {
            str(value) for value in (section_to_beats.get(section) or ())
        }
        current_beats = set(current.eligible_beats) if current is not None else set()
        candidate_beats = (
            section_beats if lineage_violation or current is None else current_beats
        )
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
            if chronology_violation:
                reason = (
                    "claim-lineage chronology repair"
                    if allowed_panels is not None
                    else "same-beat chronology repair"
                )
            else:
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

        def selection_key(entry: FeasibleVisualRecord) -> tuple[object, ...]:
            blank = float(
                entry.visual_strengths.get("edge_connected_blank_fraction", 1.0)
            )
            retained = -float(
                entry.visual_strengths.get("protected_retained_fraction", 0.0)
            )
            if (
                chronology_violation
                and current is not None
                and min_source_order is not None
                and current.source_order < min_source_order
            ):
                return (
                    entry.source_order,
                    blank,
                    retained,
                    entry.panel_id,
                    entry.panel_region_id,
                )
            if (
                chronology_violation
                and current is not None
                and max_source_order is not None
                and current.source_order > max_source_order
            ):
                return (
                    -entry.source_order,
                    blank,
                    retained,
                    entry.panel_id,
                    entry.panel_region_id,
                )
            return (
                blank,
                retained,
                entry.source_order,
                entry.panel_id,
                entry.panel_region_id,
            )

        selected = min(alternatives, key=selection_key)
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
        if index > 1 and passages:
            min_source_order = passage_min_source_order(passages[-1])
        if index > 0 and index + 1 < len(raw_passages):
            next_passage = raw_passages[index + 1]
            if isinstance(next_passage, Mapping):
                next_section = (
                    ordered_sections[index + 1]
                    if index + 1 < len(ordered_sections)
                    else ""
                )
                max_source_order = passage_max_repairable_source_order(
                    next_passage, next_section
                )

        mapped_refs: list[str] = []
        passage_claim_ids = [str(claim_id) for claim_id in claim_ids]
        for raw_ref in refs:
            panel_id = str(raw_ref)
            supporting_claims = [
                claim_id
                for claim_id in passage_claim_ids
                if panel_id in claim_evidence_by_id.get(claim_id, set())
            ]
            allowed_panels = allowed_panels_for_reference(raw_passage, panel_id)
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
        source_orders = [
            next(
                entry.source_order
                for entry in ledger.entries
                if entry.panel_id == panel_id
            )
            for panel_id in evidence_panel_ids
        ]
        row["evidence_source_orders"] = source_orders
        row["min_source_order"] = min(source_orders)
        row["max_source_order"] = max(source_orders)
        panel_slot_capacity = {
            panel_id: _panel_visual_slot_capacity(panel_id, ledger)
            for panel_id in evidence_panel_ids
        }
        section_slot_capacity = {
            section: {
                panel_id: capacity
                for panel_id in evidence_panel_ids
                if (capacity := _panel_visual_slot_capacity(
                    panel_id,
                    ledger,
                    section=section,
                )) > 0
            }
            for section in REPAIR_EDITORIAL_SECTIONS
        }
        section_slot_capacity = {
            section: capacities
            for section, capacities in section_slot_capacity.items()
            if capacities
        }
        row["evidence_panel_slot_capacity"] = panel_slot_capacity
        if section_slot_capacity:
            row["evidence_panel_slot_capacity_by_section"] = section_slot_capacity
            row["editorial_safe_sections"] = sorted(section_slot_capacity)
        row["visual_slot_capacity"] = sum(panel_slot_capacity.values())
        row["unique_panel_count"] = len(evidence_panel_ids)
        rows.append(row)
    return rows


def _unique_claim_panel_capacity(
    feasible_claim_rows: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    """Return the authoritative maximum slot capacity for each claim-backed panel."""

    capacities: dict[str, int] = {}
    for row in feasible_claim_rows:
        raw = row.get("evidence_panel_slot_capacity")
        if not isinstance(raw, Mapping):
            continue
        for panel_id, raw_capacity in raw.items():
            panel = str(panel_id).strip()
            try:
                capacity = max(0, int(raw_capacity))
            except (TypeError, ValueError):
                capacity = 0
            if panel and capacity > 0:
                capacities[panel] = max(capacities.get(panel, 0), capacity)
    return capacities


def _largest_remainder_slot_allocation(
    weights: Sequence[int],
    total_slots: int,
) -> tuple[int, ...]:
    """Scale passage slot demand to an exact total while keeping every passage non-empty."""

    count = len(weights)
    if count == 0:
        return ()
    if total_slots < count:
        return tuple(0 for _ in weights)
    normalized = [max(1, int(value)) for value in weights]
    remaining = total_slots - count
    weight_sum = sum(normalized)
    raw_extras = [remaining * value / weight_sum for value in normalized]
    extras = [math.floor(value) for value in raw_extras]
    residual = remaining - sum(extras)
    order = sorted(
        range(count),
        key=lambda index: (-(raw_extras[index] - extras[index]), index),
    )
    for index in order[:residual]:
        extras[index] += 1
    return tuple(1 + value for value in extras)



def _claim_story_prefixes(claim_id: str) -> tuple[str, ...]:
    """Return hierarchical story scopes from broadest to most specific."""

    raw = str(claim_id).strip()
    if not raw:
        return ()
    parts = raw.split("__")
    story_parts: list[str] = []
    for part in parts:
        if part.startswith("claim"):
            break
        story_parts.append(part)
    return tuple(
        "__".join(story_parts[:depth])
        for depth in range(1, len(story_parts) + 1)
    )



def _connected_story_scope_chain(
    prefix: str,
    rows: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    """Return a connected immediate-child chain, or empty when rows only share a prefix.

    A broad story prefix is not enough to establish continuity. When a window
    crosses sibling scopes, it may only move forward through adjacent numbered
    siblings (for example ``sub1 -> sub2``), may never revisit a sibling, and
    must preserve the chronological row ordering. A window contained entirely
    within one scope remains connected.
    """

    root_scope = str(prefix) == "__root__"
    prefix_parts = () if root_scope else tuple(str(prefix).split("__"))
    if (not root_scope and not prefix_parts) or not rows:
        return ()
    blocks: list[str] = []
    for row in rows:
        story_parts: list[str] = []
        for part in str(row.get("claim_id", "")).split("__"):
            if part.startswith("claim"):
                break
            story_parts.append(part)
        if not story_parts:
            return ()
        if root_scope:
            child_scope = story_parts[0]
        else:
            if tuple(story_parts[: len(prefix_parts)]) != prefix_parts:
                return ()
            if len(story_parts) == len(prefix_parts):
                child_scope = str(prefix)
            else:
                child_scope = "__".join((*prefix_parts, story_parts[len(prefix_parts)]))
        if not blocks or child_scope != blocks[-1]:
            blocks.append(child_scope)

    if len(blocks) <= 1:
        # Synthetic root is only a fallback for a genuinely cross-root
        # contiguous window.  A single top-level root must still satisfy its
        # own deeper-scope continuity checks and may not bypass interleaving.
        return () if root_scope else tuple(blocks)
    if (not root_scope and str(prefix) in blocks) or len(set(blocks)) != len(blocks):
        return ()

    numbered: list[tuple[str, int]] = []
    for scope in blocks:
        leaf = scope.rsplit("__", 1)[-1]
        match = re.fullmatch(r"(?P<stem>.*?)(?P<number>\d+)", leaf)
        if match is None:
            return ()
        numbered.append((match.group("stem"), int(match.group("number"))))
    if len({stem for stem, _number in numbered}) != 1:
        return ()
    if any(right != left + 1 for (_stem, left), (_stem2, right) in zip(numbered, numbered[1:], strict=False)):
        return ()
    return tuple(blocks)


def _select_coherent_claim_window(
    feasible_claim_rows: Sequence[Mapping[str, Any]],
    *,
    minimum_unique_panels: int,
    preferred_unique_panels: int | None = None,
    window_is_feasible: Callable[[Sequence[Mapping[str, Any]]], bool] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select the narrowest sufficiently deep story scope with enough unique visuals.

    Story-map claim IDs encode a hierarchy (for example b1__sub2__sub1__claim_2).
    We first prefer the deepest scope that can independently sustain the full
    narration cadence. Inside that scope we then choose the narrowest chronological
    claim window that still owns the required number of unique feasible panels.
    Adjacent numbered top-level roots may be joined when no deeper scope can
    sustain the requested capacity; non-adjacent or revisited roots remain invalid.
    This prevents unrelated roots from being mixed merely to gain visual headroom.
    """

    minimum = max(1, int(minimum_unique_panels))
    preferred = max(
        minimum,
        int(preferred_unique_panels) if preferred_unique_panels is not None else minimum,
    )
    rows = [dict(row) for row in feasible_claim_rows]
    rows.sort(
        key=lambda row: (
            int(row.get("min_source_order", 0)),
            int(row.get("max_source_order", 0)),
            str(row.get("claim_id", "")),
        )
    )

    scopes: dict[str, list[dict[str, Any]]] = {}
    synthetic_root_rows: list[dict[str, Any]] = []
    top_level_roots: set[str] = set()
    for row in rows:
        claim_id = str(row.get("claim_id", "")).strip()
        prefixes = _claim_story_prefixes(claim_id)
        for prefix in prefixes:
            scopes.setdefault(prefix, []).append(row)
        if prefixes:
            synthetic_root_rows.append(row)
            top_level_roots.add(prefixes[0])
    if len(top_level_roots) >= 2:
        scopes["__root__"] = synthetic_root_rows

    def row_panels(row: Mapping[str, Any]) -> tuple[str, ...]:
        raw = row.get("evidence_panel_ids", ())
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            return ()
        return tuple(str(panel_id) for panel_id in raw if str(panel_id).strip())

    candidates: list[dict[str, Any]] = []
    for prefix, scoped_rows in scopes.items():
        unique_scope_panels = {
            panel_id for row in scoped_rows for panel_id in row_panels(row)
        }
        if len(unique_scope_panels) < minimum:
            continue
        depth = 0 if prefix == "__root__" else prefix.count("__") + 1
        for start in range(len(scoped_rows)):
            unique_panels: set[str] = set()
            for end in range(start, len(scoped_rows)):
                unique_panels.update(row_panels(scoped_rows[end]))
                if len(unique_panels) < minimum:
                    continue
                window = scoped_rows[start : end + 1]
                connected_scope_chain = _connected_story_scope_chain(prefix, window)
                if not connected_scope_chain:
                    continue
                min_order = min(int(row.get("min_source_order", 0)) for row in window)
                max_order = max(int(row.get("max_source_order", 0)) for row in window)
                if window_is_feasible is not None and not window_is_feasible(window):
                    continue
                candidates.append(
                    {
                        "prefix": prefix,
                        "depth": depth,
                        "rows": window,
                        "unique_panels": set(unique_panels),
                        "source_min": min_order,
                        "source_max": max_order,
                        "connected_scope_chain": connected_scope_chain,
                    }
                )
                if len(unique_panels) >= preferred:
                    break

    if not candidates and not scopes and rows:
        all_panels = {panel_id for row in rows for panel_id in row_panels(row)}
        if (
            len(all_panels) >= minimum
            and (window_is_feasible is None or window_is_feasible(rows))
        ):
            candidates.append(
                {
                    "prefix": "unstructured",
                    "depth": 0,
                    "rows": rows,
                    "unique_panels": all_panels,
                    "source_min": min(int(row.get("min_source_order", 0)) for row in rows),
                    "source_max": max(int(row.get("max_source_order", 0)) for row in rows),
                }
            )

    if not candidates:
        aggregate_panels = {panel_id for row in rows for panel_id in row_panels(row)}
        aggregate_insufficient = len(aggregate_panels) < minimum
        selected_rows = rows if aggregate_insufficient else []
        return selected_rows, {
            "rule": "story_coherence_window_v2",
            "feasible": False,
            "minimum_unique_panels": minimum,
            "preferred_unique_panels": preferred,
            "selected_scope_prefix": "",
            "selected_scope_depth": 0,
            "selected_unique_panel_count": len(aggregate_panels) if aggregate_insufficient else 0,
            "selected_claim_count": len(selected_rows),
            "reason": (
                "aggregate_visual_capacity_below_narration_minimum"
                if aggregate_insufficient
                else "no_coherent_story_scope_has_section_safe_capacity"
                if window_is_feasible is not None
                else "no_single_story_scope_has_required_visual_capacity"
            ),
        }

    def candidate_key(candidate: Mapping[str, Any]) -> tuple[object, ...]:
        window_rows = candidate["rows"]
        hook_score = max((_hook_claim_score(row) for row in window_rows), default=0)
        story_signal_score = sum(_hook_claim_score(row) for row in window_rows)
        unique_count = len(candidate["unique_panels"])
        source_min = int(candidate["source_min"])
        source_max = int(candidate["source_max"])
        return (
            -int(candidate["depth"]),
            max(0, preferred - unique_count),
            -story_signal_score,
            source_max - source_min,
            max(0, unique_count - preferred),
            len(window_rows),
            -hook_score,
            source_min,
            str(candidate["prefix"]),
        )

    selected = min(candidates, key=candidate_key)
    selected_rows = [dict(row) for row in selected["rows"]]
    selected_panels = sorted(
        {panel_id for row in selected_rows for panel_id in row_panels(row)}
    )
    return selected_rows, {
        "rule": "story_coherence_window_v2",
        "section_capacity_aware": window_is_feasible is not None,
        "feasible": True,
        "minimum_unique_panels": minimum,
        "preferred_unique_panels": preferred,
        "selected_preferred_capacity_met": len(selected_panels) >= preferred,
        "selected_story_signal_score": sum(
            _hook_claim_score(row) for row in selected_rows
        ),
        "selected_scope_prefix": str(selected["prefix"]),
        "selected_scope_depth": int(selected["depth"]),
        "selected_connected_scope_chain": list(selected.get("connected_scope_chain", ())),
        "selected_source_order_min": int(selected["source_min"]),
        "selected_source_order_max": int(selected["source_max"]),
        "selected_source_order_span": int(selected["source_max"]) - int(selected["source_min"]),
        "selected_unique_panel_count": len(selected_panels),
        "selected_panel_ids": selected_panels,
        "selected_claim_count": len(selected_rows),
        "selected_claim_ids": [str(row.get("claim_id", "")) for row in selected_rows],
        "rejected_claim_count": max(0, len(rows) - len(selected_rows)),
    }


def _rebalance_visual_capacity_requirements(
    visual_capacity_requirements: Sequence[Mapping[str, Any]],
    feasible_claim_rows: Sequence[Mapping[str, Any]],
    *,
    max_words_per_visual_slot: int,
    target_word_min: int = REPAIR_TARGET_WORD_MIN,
    target_word_goal: int = REPAIR_TARGET_WORD_GOAL,
    target_word_max: int = REPAIR_TARGET_WORD_MAX,
    duration_policy: str = REPAIR_DURATION_POLICY_STANDARD,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Make passage cadence feasible before prose generation, never after rendering."""

    rows = [dict(row) for row in visual_capacity_requirements]
    panel_capacity = _unique_claim_panel_capacity(feasible_claim_rows)
    total_claim_slots = sum(panel_capacity.values())
    total_unique_claim_panels = len(panel_capacity)
    slot_word_capacity = max(1, int(max_words_per_visual_slot))
    minimum_slots_for_narration = math.ceil(
        target_word_min / slot_word_capacity
    )
    preferred_words_per_slot = max(1, slot_word_capacity - 1)
    preferred_slots_for_narration = math.ceil(
        target_word_min / preferred_words_per_slot
    )
    if not rows:
        return rows, {
            "rule": "visual_capacity_rebalance_v3",
            "feasible": False,
            "reason": "no_passage_requirements",
            "claim_backed_visual_slots": total_claim_slots,
            "claim_backed_unique_panels": total_unique_claim_panels,
            "usable_nonrepeating_visual_slots": total_unique_claim_panels,
            "minimum_visual_slots_for_narration": minimum_slots_for_narration,
        }
    original = [max(1, int(row.get("required_visual_slots", 1))) for row in rows]
    original_total = sum(original)
    target_slots = min(
        total_unique_claim_panels,
        max(
            minimum_slots_for_narration,
            preferred_slots_for_narration,
            min(original_total, total_claim_slots),
        ),
    )
    feasible = (
        total_unique_claim_panels >= minimum_slots_for_narration
        and target_slots >= len(rows)
    )
    allocation = (
        _largest_remainder_slot_allocation(original, target_slots)
        if feasible
        else tuple(0 for _ in rows)
    )
    if feasible and allocation and allocation[0] > 2:
        adjusted = list(allocation)
        excess = adjusted[0] - 2
        adjusted[0] = 2
        for _ in range(excess):
            deficits = [original[index] - adjusted[index] for index in range(1, len(adjusted))]
            if deficits and max(deficits) > 0:
                best = max(
                    range(1, len(adjusted)),
                    key=lambda index: (original[index] - adjusted[index], index),
                )
            else:
                best = min(2, len(adjusted) - 1)
            adjusted[best] += 1
        allocation = tuple(adjusted)
    rebalanced: list[dict[str, Any]] = []
    for row, old_required, new_required in zip(rows, original, allocation, strict=True):
        updated = dict(row)
        updated["original_required_visual_slots"] = old_required
        updated["required_visual_slots"] = int(new_required)
        updated["capacity_rebalanced"] = bool(new_required != old_required)
        rebalanced.append(updated)
    max_total_words = min(
        target_word_max,
        target_slots * max(1, int(max_words_per_visual_slot)),
    ) if feasible else 0
    return rebalanced, {
        "rule": "visual_capacity_rebalance_v3",
        "feasible": bool(feasible and max_total_words >= target_word_min),
        "claim_backed_visual_slots": total_claim_slots,
        "claim_backed_unique_panels": total_unique_claim_panels,
        "usable_nonrepeating_visual_slots": total_unique_claim_panels,
        "minimum_visual_slots_for_narration": minimum_slots_for_narration,
        "preferred_visual_slots_for_narration": preferred_slots_for_narration,
        "preferred_words_per_visual_slot": preferred_words_per_slot,
        "hook_visual_slot_cap": 2,
        "original_required_visual_slots": original_total,
        "target_visual_slots": target_slots if feasible else 0,
        "target_word_count_min": target_word_min,
        "target_word_count_max": max_total_words,
        "target_word_count_goal": (
            target_word_goal
            if max_total_words >= target_word_goal
            else target_word_min
            if max_total_words >= target_word_min
            else 0
        ),
        "duration_policy": duration_policy,
        "rebalanced": bool(feasible and tuple(original) != allocation),
    }


def _attach_capacity_word_budgets(
    plan: Mapping[str, Any],
    *,
    max_words_per_visual_slot: int,
    target_word_count: int,
) -> dict[str, Any]:
    """Attach deterministic per-passage word targets bounded by grounded visual slots."""

    normalized = dict(plan)
    rows = [dict(row) for row in normalized.get("rows", ()) if isinstance(row, Mapping)]
    ceilings = [
        max(0, int(row.get("available_visual_slots", 0))) * max(1, int(max_words_per_visual_slot))
        for row in rows
    ]
    if not rows or sum(ceilings) < target_word_count:
        normalized["feasible"] = False
        normalized["word_budget_feasible"] = False
        normalized["rows"] = rows
        return normalized
    minimums = [min(7, ceiling) for ceiling in ceilings]
    if sum(minimums) > target_word_count:
        minimums = [min(1, ceiling) for ceiling in ceilings]
    remaining = target_word_count - sum(minimums)
    headroom = [ceiling - minimum for ceiling, minimum in zip(ceilings, minimums, strict=True)]
    extras = [0 for _ in rows]
    if remaining > 0 and sum(headroom) > 0:
        raw = [remaining * room / sum(headroom) for room in headroom]
        extras = [min(room, math.floor(value)) for room, value in zip(headroom, raw, strict=True)]
        residual = remaining - sum(extras)
        order = sorted(
            range(len(rows)),
            key=lambda index: (-(raw[index] - math.floor(raw[index])), index),
        )
        for index in order:
            if residual <= 0:
                break
            if extras[index] < headroom[index]:
                extras[index] += 1
                residual -= 1
        if residual > 0:
            for index in range(len(rows)):
                while residual > 0 and extras[index] < headroom[index]:
                    extras[index] += 1
                    residual -= 1
    targets = [minimum + extra for minimum, extra in zip(minimums, extras, strict=True)]
    for row, ceiling, target in zip(rows, ceilings, targets, strict=True):
        row["max_lexical_words"] = ceiling
        row["target_lexical_words"] = target
    normalized["rows"] = rows
    normalized["word_budget_feasible"] = sum(targets) == target_word_count
    normalized["target_word_count"] = target_word_count
    normalized["feasible"] = bool(normalized.get("feasible") and normalized["word_budget_feasible"])
    return normalized


def _capacity_safe_claim_plan(
    feasible_claim_rows: Sequence[Mapping[str, Any]],
    visual_capacity_requirements: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Find a chronology- and section-safe claim bundle per passage.

    Every selected panel must be feasible for the target editorial section, so
    a conflict/action crop cannot later be forced into setup, twist, or CTA.
    Hook remains a teaser exception and may rewind to earlier chronology for the
    second passage; all later passages preserve nondecreasing claim order.
    """

    ordered_claims = sorted(
        (dict(row) for row in feasible_claim_rows),
        key=lambda row: (
            int(row.get("min_source_order", 0)),
            int(row.get("max_source_order", 0)),
            str(row.get("claim_id", "")),
        ),
    )
    requirements = [
        max(0, int(row.get("required_visual_slots", 0)))
        for row in visual_capacity_requirements
    ]
    sections = [str(row.get("section", "")).strip() for row in visual_capacity_requirements]
    if not requirements or any(required <= 0 for required in requirements):
        return {
            "rule": "ordered_unique_panel_capacity_search_v4",
            "section_capacity_aware": True,
            "preserve_passage_count": True,
            "rows": [
                {
                    "passage_index": int(row.get("passage_index", index)),
                    "section": sections[index],
                    "required_visual_slots": requirements[index],
                    "available_visual_slots": 0,
                    "claim_ids": [],
                    "evidence_panel_ids": [],
                    "evidence_panel_slot_capacity": {},
                    "claim_min_source_orders": [],
                    "feasible": False,
                }
                for index, row in enumerate(visual_capacity_requirements)
            ],
            "feasible": False,
        }

    claim_bundles: list[dict[str, Any]] = []
    for index, claim in enumerate(ordered_claims):
        claim_id = str(claim.get("claim_id", "")).strip()
        raw_caps = claim.get("evidence_panel_slot_capacity")
        if not claim_id or not isinstance(raw_caps, Mapping):
            continue
        caps: dict[str, int] = {}
        for panel_id, raw_capacity in raw_caps.items():
            panel = str(panel_id).strip()
            try:
                capacity = max(0, int(raw_capacity))
            except (TypeError, ValueError):
                capacity = 0
            if panel and capacity > 0:
                caps[panel] = capacity
        if not caps:
            continue
        raw_by_section = claim.get("evidence_panel_slot_capacity_by_section")
        explicit_section_caps = isinstance(raw_by_section, Mapping)
        by_section: dict[str, dict[str, int]] = {}
        if explicit_section_caps:
            for raw_section, raw_section_caps in raw_by_section.items():
                if not isinstance(raw_section_caps, Mapping):
                    continue
                section_caps: dict[str, int] = {}
                for panel_id, raw_capacity in raw_section_caps.items():
                    panel = str(panel_id).strip()
                    try:
                        capacity = max(0, int(raw_capacity))
                    except (TypeError, ValueError):
                        capacity = 0
                    if panel in caps and capacity > 0:
                        section_caps[panel] = capacity
                if section_caps:
                    by_section[str(raw_section)] = section_caps
        claim_bundles.append(
            {
                "ordered_index": index,
                "claim_id": claim_id,
                "min_source_order": int(claim.get("min_source_order", 0)),
                "hook_score": _hook_claim_score(claim),
                "panel_caps": caps,
                "panel_caps_by_section": by_section,
                "section_caps_explicit": explicit_section_caps,
            }
        )

    def bundle_caps(bundle: Mapping[str, Any], section: str) -> dict[str, int]:
        by_section = bundle.get("panel_caps_by_section")
        if bool(bundle.get("section_caps_explicit")):
            if not isinstance(by_section, Mapping):
                return {}
            raw = by_section.get(section)
            if not isinstance(raw, Mapping):
                return {}
            return {str(panel_id): int(capacity) for panel_id, capacity in raw.items()}
        raw = bundle.get("panel_caps")
        return (
            {str(panel_id): int(capacity) for panel_id, capacity in raw.items()}
            if isinstance(raw, Mapping)
            else {}
        )

    def available_capacity(
        start_index: int,
        used_panels: frozenset[str],
        passage_index: int,
    ) -> int:
        remaining_sections = set(sections[passage_index:])
        panel_ids: set[str] = set()
        for bundle in claim_bundles:
            if int(bundle["ordered_index"]) < start_index:
                continue
            for section in remaining_sections:
                for panel_id in bundle_caps(bundle, section):
                    if panel_id not in used_panels:
                        panel_ids.add(panel_id)
        return len(panel_ids)

    def candidate_bundles(
        start_index: int,
        used_panels: frozenset[str],
        required: int,
        section: str,
        *,
        hook_mode: bool = False,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []

        def walk(
            position: int,
            selected: tuple[dict[str, Any], ...],
            selected_caps: dict[str, int],
        ) -> None:
            capacity = len(selected_caps)
            if selected and capacity >= required:
                results.append(
                    {
                        "claims": selected,
                        "panel_caps": dict(selected_caps),
                        "capacity": capacity,
                        "hook_score": max(int(item.get("hook_score", 0)) for item in selected),
                        "hook_score_sum": sum(int(item.get("hook_score", 0)) for item in selected),
                        "last_index": int(selected[-1]["ordered_index"]),
                    }
                )
                return
            for next_position in range(position, len(claim_bundles)):
                bundle = claim_bundles[next_position]
                ordered_index = int(bundle["ordered_index"])
                if ordered_index < start_index:
                    continue
                caps = bundle_caps(bundle, section)
                if not caps:
                    continue
                bundle_panels = set(caps)
                if bundle_panels & used_panels or bundle_panels & set(selected_caps):
                    continue
                merged = dict(selected_caps)
                merged.update(caps)
                walk(next_position + 1, (*selected, bundle), merged)

        walk(0, (), {})
        results.sort(
            key=lambda item: (
                max(0, required - len(item["panel_caps"])),
                -int(item.get("hook_score", 0)) if hook_mode else 0,
                -int(item.get("hook_score_sum", 0)) if hook_mode else 0,
                abs(len(item["panel_caps"]) - required),
                max(0, int(item["capacity"]) - len(item["panel_caps"])),
                int(item["capacity"]) - required,
                int(item["last_index"]),
                tuple(str(claim["claim_id"]) for claim in item["claims"]),
            )
        )
        return results

    def solve(
        passage_index: int,
        start_index: int,
        used_panels: frozenset[str],
    ) -> list[dict[str, Any]] | None:
        if passage_index >= len(requirements):
            return []
        required_remaining = sum(requirements[passage_index:])
        if available_capacity(start_index, used_panels, passage_index) < required_remaining:
            return None
        required = requirements[passage_index]
        section = sections[passage_index]
        hook_mode = passage_index == 0
        candidate_start = 0 if hook_mode else start_index
        for candidate in candidate_bundles(
            candidate_start,
            used_panels,
            required,
            section,
            hook_mode=hook_mode,
        ):
            panel_caps = candidate["panel_caps"]
            candidate_panels = frozenset(str(panel_id) for panel_id in panel_caps)
            tail = solve(
                passage_index + 1,
                0 if hook_mode else int(candidate["last_index"]) + 1,
                used_panels | candidate_panels,
            )
            if tail is not None:
                return [candidate, *tail]
        return None

    solution = solve(0, 0, frozenset())
    plan_rows: list[dict[str, Any]] = []
    for index, requirement in enumerate(visual_capacity_requirements):
        required = requirements[index]
        candidate = solution[index] if solution is not None else None
        claims = list(candidate["claims"]) if isinstance(candidate, Mapping) else []
        panel_caps = (
            {str(key): int(value) for key, value in candidate["panel_caps"].items()}
            if isinstance(candidate, Mapping)
            else {}
        )
        available = len(panel_caps)
        raw_roi_capacity = sum(panel_caps.values())
        plan_rows.append(
            {
                "passage_index": int(requirement.get("passage_index", index)),
                "section": sections[index],
                "required_visual_slots": required,
                "available_visual_slots": available,
                "unique_panel_count": len(panel_caps),
                "unique_panel_shortfall": max(0, required - len(panel_caps)),
                "raw_roi_slot_capacity": raw_roi_capacity,
                "panel_reuse_slots": max(0, raw_roi_capacity - len(panel_caps)),
                "hook_teaser": index == 0,
                "hook_priority_score": int(candidate.get("hook_score", 0)) if isinstance(candidate, Mapping) else 0,
                "claim_ids": [str(claim["claim_id"]) for claim in claims],
                "evidence_panel_ids": list(panel_caps),
                "evidence_panel_slot_capacity": panel_caps,
                "claim_min_source_orders": [int(claim["min_source_order"]) for claim in claims],
                "feasible": bool(candidate is not None and available >= required),
            }
        )
    return {
        "rule": "ordered_unique_panel_capacity_search_v4",
        "section_capacity_aware": True,
        "preserve_passage_count": True,
        "rows": plan_rows,
        "feasible": solution is not None and all(bool(row["feasible"]) for row in plan_rows),
    }

def _claim_bundle_aware_capacity_plan(
    feasible_claim_rows: Sequence[Mapping[str, Any]],
    visual_capacity_requirements: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Repair slot allocation when atomic claim evidence bundles make it impossible.

    Total unique-panel demand remains unchanged. Only the per-passage split may
    move, preserving a two-slot hook cap and preferring the smallest deviation
    from the cadence rebalance. This prevents false capacity failures such as
    an 8-panel story whose atomic claim bundles are sized 3/2/1/1/1 while the
    generic proportional split happened to be 2/2/2/1/1.
    """

    rows = [dict(row) for row in visual_capacity_requirements]
    initial_plan = _capacity_safe_claim_plan(feasible_claim_rows, rows)
    current = tuple(max(1, int(row.get("required_visual_slots", 1))) for row in rows)
    metadata: dict[str, Any] = {
        "rule": "claim_bundle_aware_slot_rebalance_v1",
        "applied": False,
        "original_allocation": list(current),
        "selected_allocation": list(current),
        "target_visual_slots": sum(current),
    }
    if initial_plan.get("feasible") or not rows:
        return rows, initial_plan, metadata

    total = sum(current)
    count = len(rows)
    if total < count:
        return rows, initial_plan, metadata

    allocations: list[tuple[int, ...]] = []

    def compose(remaining: int, index: int, prefix: tuple[int, ...]) -> None:
        if index == count - 1:
            if remaining >= 1:
                allocations.append((*prefix, remaining))
            return
        minimum_tail = count - index - 1
        max_value = remaining - minimum_tail
        if index == 0:
            max_value = min(max_value, 2)
        for value in range(1, max_value + 1):
            compose(remaining - value, index + 1, (*prefix, value))

    compose(total, 0, ())
    allocations = [allocation for allocation in allocations if allocation != current]
    allocations.sort(
        key=lambda allocation: (
            sum(abs(value - current[index]) for index, value in enumerate(allocation)),
            abs(allocation[0] - current[0]),
            tuple(-value for value in allocation[1:]),
            allocation,
        )
    )
    for allocation in allocations:
        candidate_rows: list[dict[str, Any]] = []
        for index, (row, required) in enumerate(zip(rows, allocation, strict=True)):
            updated = dict(row)
            updated["required_visual_slots"] = int(required)
            updated["claim_bundle_rebalanced"] = int(required) != current[index]
            candidate_rows.append(updated)
        candidate_plan = _capacity_safe_claim_plan(feasible_claim_rows, candidate_rows)
        if candidate_plan.get("feasible"):
            return candidate_rows, candidate_plan, {
                **metadata,
                "applied": True,
                "selected_allocation": list(allocation),
            }
    return rows, initial_plan, metadata


def recover_missing_capacity_plan_references(
    passages: Sequence[object],
    plan: Mapping[str, Any],
) -> list[object]:
    """Fill only omitted/empty provider reference fields from the mandatory plan.

    Non-empty provider references are intentionally preserved so the strict plan
    validator can reject substitutions. This is transport recovery, not evidence
    rebinding.
    """

    rows = plan.get("rows") if isinstance(plan, Mapping) else None
    if not isinstance(rows, list) or len(rows) != len(passages):
        return [dict(item) if isinstance(item, Mapping) else item for item in passages]
    recovered: list[object] = []
    for raw_passage, plan_row in zip(passages, rows, strict=True):
        if not isinstance(raw_passage, Mapping) or not isinstance(plan_row, Mapping):
            recovered.append(raw_passage)
            continue
        passage = dict(raw_passage)
        claim_ids = passage.get("claim_ids")
        if claim_ids is None or claim_ids == []:
            passage["claim_ids"] = [str(value) for value in plan_row.get("claim_ids", ())]
        evidence_refs = passage.get("evidence_panel_ids")
        alias_refs = passage.get("panel_ids")
        if (
            (evidence_refs is None or evidence_refs == [])
            and (alias_refs is None or alias_refs == [])
        ):
            passage["evidence_panel_ids"] = [
                str(value) for value in plan_row.get("evidence_panel_ids", ())
            ]
        recovered.append(passage)
    return recovered



def lock_capacity_plan_references(
    passages: Sequence[object],
    plan: Mapping[str, Any],
) -> list[object]:
    """Make local capacity-plan claim/evidence refs authoritative by passage index."""
    rows = plan.get("rows") if isinstance(plan, Mapping) else None
    if not isinstance(rows, list) or len(rows) != len(passages):
        return [dict(item) if isinstance(item, Mapping) else item for item in passages]
    locked: list[object] = []
    for raw_passage, plan_row in zip(passages, rows, strict=True):
        if not isinstance(raw_passage, Mapping) or not isinstance(plan_row, Mapping):
            locked.append(raw_passage)
            continue
        passage = dict(raw_passage)
        passage["claim_ids"] = [str(value) for value in plan_row.get("claim_ids", ())]
        passage["evidence_panel_ids"] = [
            str(value) for value in plan_row.get("evidence_panel_ids", ())
        ]
        passage.pop("panel_ids", None)
        locked.append(passage)
    return locked

def repaired_references_match_capacity_plan(
    passages: Sequence[Mapping[str, Any]],
    plan: Mapping[str, Any],
) -> bool:
    """Return whether provider reference metadata exactly matches the local plan."""
    rows = plan.get("rows") if isinstance(plan, Mapping) else None
    if not isinstance(rows, list) or len(passages) != len(rows):
        return False
    for passage, row in zip(passages, rows, strict=True):
        if not isinstance(passage, Mapping) or not isinstance(row, Mapping):
            return False
        claim_ids = passage.get("claim_ids")
        panel_ids = passage.get("evidence_panel_ids")
        if not isinstance(claim_ids, list) or not isinstance(panel_ids, list):
            return False
        if [str(value) for value in claim_ids] != [str(value) for value in row.get("claim_ids", ())]:
            return False
        if [str(value) for value in panel_ids] != [str(value) for value in row.get("evidence_panel_ids", ())]:
            return False
    return True


def validate_repaired_capacity_safe_claim_plan(
    passages: Sequence[Mapping[str, Any]],
    plan: Mapping[str, Any],
    *,
    enforce_word_budget: bool = True,
) -> None:
    """Require repaired evidence bundles and passage length to match the local safe plan."""

    rows = plan.get("rows") if isinstance(plan, Mapping) else None
    if not isinstance(rows, list) or not bool(plan.get("feasible")):
        raise VisualNarrativeRepairError(
            "repair capacity plan is infeasible",
            "visual.narrative_repair_ungrounded",
        )
    if len(passages) != len(rows):
        raise VisualNarrativeRepairError(
            "repair passage diverges from capacity plan",
            "visual.narrative_repair_ungrounded",
        )
    for passage, row in zip(passages, rows, strict=True):
        if not isinstance(passage, Mapping) or not isinstance(row, Mapping):
            raise VisualNarrativeRepairError(
                "repair passage diverges from capacity plan",
                "visual.narrative_repair_ungrounded",
            )
        claim_ids = passage.get("claim_ids")
        evidence_panel_ids = passage.get("evidence_panel_ids")
        if (
            not isinstance(claim_ids, list)
            or not isinstance(evidence_panel_ids, list)
            or [str(value) for value in claim_ids] != [str(value) for value in row.get("claim_ids", ())]
            or [str(value) for value in evidence_panel_ids] != [str(value) for value in row.get("evidence_panel_ids", ())]
        ):
            raise VisualNarrativeRepairError(
                "repair passage diverges from capacity plan",
                "visual.narrative_repair_ungrounded",
            )
        if not enforce_word_budget:
            continue
        word_count = script_service.narration_word_count(str(passage.get("text", "")))
        max_words = int(row.get("max_lexical_words", 0))
        if max_words <= 0 or word_count > max_words:
            raise VisualNarrativeRepairError(
                "repair passage exceeds capacity word budget",
                "visual.narrative_repair_ungrounded",
            )



def _selected_story_context(
    story_map: Mapping[str, Any],
    capacity_plan: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return transition-only story semantics for the mandatory passage plan."""
    beats = [dict(item) for item in story_map.get("beats", ()) if isinstance(item, Mapping)]
    causal = [dict(item) for item in story_map.get("causal_chain", ()) if isinstance(item, Mapping)]
    plan_rows = [dict(item) for item in capacity_plan.get("rows", ()) if isinstance(item, Mapping)]
    beat_order = {str(beat.get("beat_id", "")): index for index, beat in enumerate(beats)}

    selected: list[dict[str, Any]] = []
    previous_beat_ids: set[str] = set()
    for index, row in enumerate(plan_rows):
        evidence = {str(value) for value in row.get("evidence_panel_ids", ()) if str(value).strip()}
        relevant = [
            beat for beat in beats
            if evidence.intersection({str(value) for value in beat.get("panel_ids", ())})
        ]
        relevant.sort(key=lambda beat: beat_order.get(str(beat.get("beat_id", "")), 10**9))
        beat_ids = [str(beat.get("beat_id", "")) for beat in relevant if str(beat.get("beat_id", "")).strip()]
        if index == 0:
            bridge = {"kind": "hook_teaser", "causal_wording_allowed": False, "reason": ""}
        elif index == 1:
            bridge = {
                "kind": "teaser_rewind",
                "causal_wording_allowed": False,
                "reason": "return to the earliest retained chronology after the teaser",
            }
        else:
            current_ids = set(beat_ids)
            direct = [
                edge for edge in causal
                if str(edge.get("from_beat", "")) in previous_beat_ids
                and str(edge.get("to_beat", "")) in current_ids
                and str(edge.get("reason", "")).strip()
            ]
            if direct:
                bridge = {
                    "kind": "causal",
                    "causal_wording_allowed": True,
                    "reason": str(direct[0]["reason"]),
                }
            else:
                bridge = {
                    "kind": "temporal_only",
                    "causal_wording_allowed": False,
                    "reason": "chronological continuation only; do not invent causality",
                }
        selected.append({
            "passage_index": int(row.get("passage_index", index)),
            "section": str(row.get("section", "")),
            "claim_ids": [str(value) for value in row.get("claim_ids", ())],
            "evidence_panel_ids": [str(value) for value in row.get("evidence_panel_ids", ())],
            "beat_ids": beat_ids,
            "beat_context": [
                {"beat_id": str(beat.get("beat_id", "")), "summary": str(beat.get("summary", ""))}
                for beat in relevant
            ],
            "incoming_bridge": bridge,
            "context_is_transition_only": True,
        })
        previous_beat_ids = set(beat_ids)
    return selected

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
    all_feasible_claim_rows = feasible_story_claims(story_map, ledger)
    feasible_claim_rows = list(all_feasible_claim_rows)
    passages = [
        dict(item) for item in narration.get("passages", ()) if isinstance(item, Mapping)
    ]
    section_names = tuple(str(section) for section in section_to_beats if str(section).strip())
    if passages:
        capacity_rows = list(
            _passage_visual_capacity_metrics(
                passages,
                ledger,
                total_duration_s=narration.get("estimated_duration_s"),
            )
        )
    else:
        capacity_rows = [
            {
                "passage_index": index,
                "duration_s": 0.0,
                "required_visual_slots": 1,
                "available_visual_slots": 0,
                "evidence_panel_ids": [],
                "shortfall": 1,
            }
            for index in range(len(section_names))
        ]
    original_visual_capacity_requirements = [
        {
            **dict(row),
            "section": _passage_section_name(
                int(row["passage_index"]), len(capacity_rows), section_names
            ),
        }
        for row in capacity_rows
    ]
    duration_contract = script_service.narration_duration_contract("dramatic")
    narration_words_per_second = float(duration_contract["words_per_second"])
    max_words_per_visual_slot = max(
        1,
        math.floor(
            reference_profile.REVIEW_MAX_SHOT_SECONDS * narration_words_per_second
        ),
    )
    standard_minimum_panels = math.ceil(
        REPAIR_TARGET_WORD_MIN / max_words_per_visual_slot
    )
    adaptive_minimum_panels = max(
        len(section_names),
        min(REPAIR_ADAPTIVE_MIN_UNIQUE_PANELS, standard_minimum_panels),
    )
    window_feasibility_cache: dict[tuple[str, ...], bool] = {}

    def coherent_window_is_section_safe(
        window_rows: Sequence[Mapping[str, Any]],
    ) -> bool:
        cache_key = tuple(str(row.get("claim_id", "")) for row in window_rows)
        cached = window_feasibility_cache.get(cache_key)
        if cached is not None:
            return cached
        unique_panels = {
            str(panel_id)
            for row in window_rows
            for panel_id in (row.get("evidence_panel_ids") or ())
            if str(panel_id).strip()
        }
        selected_count = len(unique_panels)
        if selected_count >= standard_minimum_panels:
            policy = REPAIR_DURATION_POLICY_STANDARD
            word_min = REPAIR_TARGET_WORD_MIN
            word_goal = REPAIR_TARGET_WORD_GOAL
            word_max = REPAIR_TARGET_WORD_MAX
        else:
            policy = REPAIR_DURATION_POLICY_ADAPTIVE
            min_words_per_slot = max(
                1,
                math.ceil(REPAIR_ADAPTIVE_MIN_SHOT_SECONDS * narration_words_per_second),
            )
            target_words_per_slot = max(
                min_words_per_slot,
                round(REPAIR_ADAPTIVE_TARGET_SHOT_SECONDS * narration_words_per_second),
            )
            word_min = selected_count * min_words_per_slot
            word_goal = selected_count * target_words_per_slot
            word_max = selected_count * max_words_per_visual_slot
        requirements, rebalance = _rebalance_visual_capacity_requirements(
            original_visual_capacity_requirements,
            window_rows,
            max_words_per_visual_slot=max_words_per_visual_slot,
            target_word_min=word_min,
            target_word_goal=word_goal,
            target_word_max=word_max,
            duration_policy=policy,
        )
        if not bool(rebalance.get("feasible")):
            window_feasibility_cache[cache_key] = False
            return False
        requirements, plan, _metadata = _claim_bundle_aware_capacity_plan(
            window_rows, requirements
        )
        plan = _attach_capacity_word_budgets(
            plan,
            max_words_per_visual_slot=max_words_per_visual_slot,
            target_word_count=int(rebalance.get("target_word_count_goal", 0)),
        )
        feasible = bool(plan.get("feasible"))
        window_feasibility_cache[cache_key] = feasible
        return feasible

    standard_claim_rows, standard_coherence_window = _select_coherent_claim_window(
        feasible_claim_rows,
        minimum_unique_panels=standard_minimum_panels,
        preferred_unique_panels=standard_minimum_panels,
        window_is_feasible=coherent_window_is_section_safe,
    )
    if bool(standard_coherence_window.get("feasible")):
        feasible_claim_rows = standard_claim_rows
        coherence_window = standard_coherence_window
    else:
        feasible_claim_rows, coherence_window = _select_coherent_claim_window(
            feasible_claim_rows,
            minimum_unique_panels=adaptive_minimum_panels,
            preferred_unique_panels=min(
                standard_minimum_panels,
                adaptive_minimum_panels + 1,
            ),
            window_is_feasible=coherent_window_is_section_safe,
        )
    selected_unique_panels = int(coherence_window.get("selected_unique_panel_count", 0))
    if selected_unique_panels >= standard_minimum_panels:
        duration_policy = REPAIR_DURATION_POLICY_STANDARD
        target_word_min = REPAIR_TARGET_WORD_MIN
        target_word_goal = REPAIR_TARGET_WORD_GOAL
        target_word_max = REPAIR_TARGET_WORD_MAX
    else:
        duration_policy = REPAIR_DURATION_POLICY_ADAPTIVE
        min_words_per_slot = max(1, math.ceil(
            REPAIR_ADAPTIVE_MIN_SHOT_SECONDS * narration_words_per_second
        ))
        target_words_per_slot = max(min_words_per_slot, round(
            REPAIR_ADAPTIVE_TARGET_SHOT_SECONDS * narration_words_per_second
        ))
        target_word_min = selected_unique_panels * min_words_per_slot
        target_word_goal = selected_unique_panels * target_words_per_slot
        target_word_max = selected_unique_panels * max_words_per_visual_slot
    visual_capacity_requirements, capacity_rebalance = (
        _rebalance_visual_capacity_requirements(
            original_visual_capacity_requirements,
            feasible_claim_rows,
            max_words_per_visual_slot=max_words_per_visual_slot,
            target_word_min=target_word_min,
            target_word_goal=target_word_goal,
            target_word_max=target_word_max,
            duration_policy=duration_policy,
        )
    )
    coherence_feasible = bool(coherence_window.get("feasible"))
    if not coherence_feasible:
        capacity_rebalance = {
            **capacity_rebalance,
            "feasible": False,
            "reason": str(coherence_window.get("reason", "coherence_window_infeasible")),
        }
    (
        visual_capacity_requirements,
        capacity_safe_claim_plan,
        claim_bundle_rebalance,
    ) = _claim_bundle_aware_capacity_plan(
        feasible_claim_rows,
        visual_capacity_requirements,
    )
    capacity_rebalance = {
        **capacity_rebalance,
        "claim_bundle_rebalance": claim_bundle_rebalance,
    }
    if not coherence_feasible:
        capacity_safe_claim_plan = {
            **capacity_safe_claim_plan,
            "feasible": False,
            "word_budget_feasible": False,
        }
    capacity_safe_claim_plan = _attach_capacity_word_budgets(
        capacity_safe_claim_plan,
        max_words_per_visual_slot=max_words_per_visual_slot,
        target_word_count=int(capacity_rebalance.get("target_word_count_goal", 0)),
    )
    selected_story_context = _selected_story_context(story_map, capacity_safe_claim_plan)
    target_duration_min_s = script_service.estimate_narration_duration(
        " ".join(["word"] * int(capacity_rebalance.get("target_word_count_min", 0))),
        "dramatic",
    )
    target_duration_max_s = script_service.estimate_narration_duration(
        " ".join(["word"] * int(capacity_rebalance.get("target_word_count_max", 0))),
        "dramatic",
    )
    duration_policy_contract = {
        "version": duration_policy,
        "adaptive": duration_policy == REPAIR_DURATION_POLICY_ADAPTIVE,
        "selected_unique_panel_count": selected_unique_panels,
        "standard_minimum_unique_panels": standard_minimum_panels,
        "target_word_min": int(capacity_rebalance.get("target_word_count_min", 0)),
        "target_word_goal": int(capacity_rebalance.get("target_word_count_goal", 0)),
        "target_word_max": int(capacity_rebalance.get("target_word_count_max", 0)),
        "target_duration_min_s": target_duration_min_s,
        "target_duration_max_s": target_duration_max_s,
        "max_shot_duration_s": reference_profile.REVIEW_MAX_SHOT_SECONDS,
    }
    return {
        "repair_contract_version": REPAIR_CONTRACT_VERSION,
        "feasible_ledger": ledger.as_dict(),
        "feasible_render_plan": render_plan.as_dict(),
        "feasible_panel_ids": list(ledger.feasible_panel_ids),
        "all_feasible_claim_ids": [
            str(row["claim_id"]) for row in all_feasible_claim_rows
        ],
        "feasible_claim_ids": [str(row["claim_id"]) for row in feasible_claim_rows],
        "feasible_claims": feasible_claim_rows,
        "coherence_window": coherence_window,
        "chronology_contract": {
            "hook_exception": True,
            "non_hook_rule": "nondecreasing_min_source_order",
            "claim_order_field": "min_source_order",
            "claims_by_source_order": [
                {
                    "claim_id": str(row["claim_id"]),
                    "min_source_order": int(row["min_source_order"]),
                    "max_source_order": int(row["max_source_order"]),
                    "evidence_panel_ids": list(row["evidence_panel_ids"]),
                    "evidence_source_orders": list(row["evidence_source_orders"]),
                    "evidence_panel_slot_capacity": dict(
                        row["evidence_panel_slot_capacity"]
                    ),
                    "visual_slot_capacity": int(row["visual_slot_capacity"]),
                }
                for row in sorted(
                    feasible_claim_rows,
                    key=lambda item: (
                        int(item["min_source_order"]),
                        int(item["max_source_order"]),
                        str(item["claim_id"]),
                    ),
                )
            ],
        },
        "feasible_by_beat": {
            beat: [entry.panel_id for entry in ledger.entries if beat in entry.eligible_beats]
            for beat in sorted({beat for entry in ledger.entries for beat in entry.eligible_beats})
        },
        "missing_sections": list(missing),
        "original_visual_capacity_requirements": original_visual_capacity_requirements,
        "visual_capacity_requirements": visual_capacity_requirements,
        "capacity_rebalance": capacity_rebalance,
        "capacity_safe_claim_plan": capacity_safe_claim_plan,
        "selected_story_context": selected_story_context,
        "duration_policy_contract": duration_policy_contract,
        "capacity_contract": {
            "max_shot_duration_s": reference_profile.REVIEW_MAX_SHOT_SECONDS,
            "narration_words_per_second": narration_words_per_second,
            "max_lexical_words_per_visual_slot": max_words_per_visual_slot,
            "claim_capacity_field": "visual_slot_capacity",
            "panel_capacity_field": "evidence_panel_slot_capacity",
            "prefer_unique_panels_before_second_roi": True,
            "requirements_by_passage": visual_capacity_requirements,
            "rebalance": capacity_rebalance,
            "coherence_window": coherence_window,
            "coherence_precedes_headroom": True,
            "plan_is_mandatory": True,
            "duration_policy": duration_policy_contract,
        },
        "section_to_beats": {str(key): [str(value) for value in values] for key, values in sorted(section_to_beats.items())},
        "story_map": dict(story_map),
        "current_narration": {
            "passages": passages,
            "ending_kind": narration.get("ending_kind"),
            "estimated_duration_s": narration.get("estimated_duration_s"),
        },
        "feasible_observations": [dict(item) for item in feasible_observations],
        "constraints": {
            "same_pinned_model": True,
            "allowed_panel_ids_only": True,
            "allowed_claim_ids_only": True,
            "claim_evidence_must_match_story_lineage": True,
            "preserve_causal_order": True,
            "non_hook_claims_must_follow_chronology_contract": True,
            "no_copied_dialogue": True,
            "no_invented_facts": True,
            "target_passages": "4-6",
            "target_words": f"{capacity_rebalance.get('target_word_count_min', REPAIR_TARGET_WORD_MIN)}-{capacity_rebalance.get('target_word_count_max', REPAIR_TARGET_WORD_MAX)}",
            "target_duration_s": (
                f"{target_duration_min_s:.2f}-{target_duration_max_s:.2f}"
            ),
            "max_shot_duration_s": reference_profile.REVIEW_MAX_SHOT_SECONDS,
            "visual_capacity_must_cover_every_passage": True,
            "passage_panels_must_belong_to_passage_claims": True,
            "follow_capacity_safe_claim_plan": True,
            "preserve_passage_count_for_capacity_plan": True,
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
        passage_claim_ids = tuple(map(str, claim_ids))
        supported_refs = set().union(*(claim_refs[claim_id] for claim_id in passage_claim_ids))
        if not passage_refs <= supported_refs:
            raise VisualNarrativeRepairError(
                "repair passage evidence is outside its claim lineage",
                "visual.narrative_repair_ungrounded",
            )
        for claim_id in passage_claim_ids:
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
    capacity_plan_hash: str = "",
    contract_version: str = REPAIR_CONTRACT_VERSION,
) -> str:
    return _hash({
        "contract_version": contract_version,
        "ledger_hash": ledger.ledger_hash,
        "model_identity_hash": model_identity_hash,
        "prompt_sha256": prompt_sha256,
        "narration_hash": narration_hash,
        "capacity_plan_hash": capacity_plan_hash,
    })


__all__ = [
    "FeasibleRenderPanel",
    "FeasibleRenderPlan",
    "FeasibleVisualLedger",
    "FeasibleVisualRecord",
    "MAX_REPAIR_ATTEMPTS",
    "REPAIR_CONTRACT_VERSION",
    "REPAIR_PROMPT_VERSION",
    "HOOK_STORY_SELECTION_VERSION",
    "VISUAL_SECTION_REMAP_VERSION",
    "VisualNarrativeRepairError",
    "build_feasible_visual_ledger",
    "build_repair_payload",
    "coalesce_adjacent_duplicate_panel_passages",
    "default_section_to_beats",
    "load_repair_prompt",
    "missing_visual_sections",
    "narration_sections_with_infeasible_citations",
    "narration_sections_with_visual_capacity_shortfall",
    "remap_same_beat_panel_citations",
    "repair_scope_sections",
    "repair_cache_key",
    "validate_repaired_panel_references",
    "validate_repaired_hook_quality",
    "recover_missing_capacity_plan_references",
    "lock_capacity_plan_references",
    "repaired_references_match_capacity_plan",
    "validate_repaired_capacity_safe_claim_plan",
    "validate_repaired_visual_capacity",
    "validate_repaired_section_visual_coverage",
]
