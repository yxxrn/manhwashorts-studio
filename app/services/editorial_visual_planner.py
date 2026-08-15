"""Automated editorial visual planning.

This is the public Director/visual-planner boundary. It consumes analyzed panel
candidates and narration spans, then returns an editorial shot list before camera
execution. It never re-scores panels.
"""
from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass, replace

from app.services import (
    director,
    framing_analysis,
    motion_director,
    review_source_upscale,
    visual_scoring,
)

_MAX_EDITORIAL_SHOT_SECONDS = 2.8
_REFERENCE_SHOT_INTERVAL_SECONDS = 1.28
_BEAT_PRIORITY = {
    "neutral": 0,
    "suspense": 1,
    "thinking": 2,
    "victory": 3,
    "reveal": 4,
    "action": 5,
    "attack": 6,
    "impact": 7,
    "explosion": 8,
}

_REFERENCE_ROI_KIND_ORDER = {
    "primary": 0,
    "alternate_roi": 1,
    "tighter_crop": 2,
}
_REFERENCE_ROI_KINDS = frozenset(_REFERENCE_ROI_KIND_ORDER)


class ReferencePlanningError(RuntimeError):
    """Raised when the selected reference profile cannot be satisfied safely."""

    code = "reference_planning_failed"

    def __init__(self, message: str, code: str | None = None) -> None:
        self.code = code or type(self).code
        super().__init__(f"{self.code}: {message}")


def _reference_group_counts(
    beats: list[director.StoryBeat],
    target_shots: int,
    max_counts_by_section: Mapping[str, int] | None = None,
) -> list[int]:
    groups: list[list[director.StoryBeat]] = []
    cursor = 0
    while cursor < len(beats):
        start = cursor
        section = beats[cursor].section
        while cursor < len(beats) and beats[cursor].section == section:
            cursor += 1
        groups.append(beats[start:cursor])
    if not groups:
        return []
    durations = [
        max(0.001, group[-1].end_time - group[0].start_time)
        for group in groups
    ]
    total = sum(durations)
    counts = [1] * len(groups)
    caps = [
        max(
            1,
            int((max_counts_by_section or {}).get(str(group[0].section), target_shots)),
        )
        for group in groups
    ]
    if sum(caps) < target_shots:
        raise ReferencePlanningError(
            "review cadence capacity is below the requested section coverage"
        )
    remaining = max(0, target_shots - len(groups))
    exact = [remaining * duration / total for duration in durations]
    counts = [
        min(cap, count + math.floor(value))
        for count, value, cap in zip(counts, exact, caps, strict=True)
    ]
    left = target_shots - sum(counts)
    order = sorted(
        range(len(groups)),
        key=lambda index: (-(exact[index] - math.floor(exact[index])), index),
    )
    while left:
        progressed = False
        for index in order:
            if left <= 0:
                break
            if counts[index] >= caps[index]:
                continue
            counts[index] += 1
            left -= 1
            progressed = True
        if not progressed:
            raise ReferencePlanningError(
                "review cadence allocation exceeded section capacity"
            )
    return counts


def _coalesce_beats(
    beats: list[director.StoryBeat],
    target_shots: int | None = None,
    max_counts_by_section: Mapping[str, int] | None = None,
) -> list[director.StoryBeat]:
    """Compress event fragments to the fixed 18-24 shot editorial budget."""
    if not beats:
        return []
    result: list[director.StoryBeat] = []
    target_counts = (
        _reference_group_counts(
            beats,
            target_shots,
            max_counts_by_section=max_counts_by_section,
        )
        if target_shots
        else []
    )
    cursor = 0
    section_index = 0
    while cursor < len(beats):
        section_start = cursor
        section = beats[cursor].section
        while cursor < len(beats) and beats[cursor].section == section:
            cursor += 1
        section_beats = beats[section_start:cursor]
        section_duration = max(
            0.0, section_beats[-1].end_time - section_beats[0].start_time
        )
        group_count = (
            target_counts[section_index]
            if target_shots
            else max(
                1,
                min(
                    len(section_beats),
                    math.ceil(section_duration / _MAX_EDITORIAL_SHOT_SECONDS),
                ),
            )
        )
        section_index += 1
        section_start_time = section_beats[0].start_time
        for group_index in range(group_count):
            group_start = (
                section_start_time
                + section_duration * group_index / group_count
            )
            group_end = (
                section_start_time
                + section_duration * (group_index + 1) / group_count
            )
            group = [
                beat
                for beat in section_beats
                if beat.end_time > group_start and beat.start_time < group_end
            ]
            if not group:
                group = [
                    min(
                        section_beats,
                        key=lambda beat: abs(beat.start_time - group_start),
                    )
                ]
            lead = max(
                group,
                key=lambda beat: (
                    _BEAT_PRIORITY.get(beat.kind, 0),
                    beat.end_time - beat.start_time,
                ),
            )
            timings = [
                timing
                for beat in group
                for timing in beat.word_timings
                if group_start <= float(timing.get("start", 0.0)) < group_end
            ]
            result.append(
                director.StoryBeat(
                    section=section,
                    text=group[0].text,
                    start_time=round(group_start, 3),
                    end_time=round(group_end, 3),
                    word_timings=timings,
                    kind=lead.kind,
                    emotion=lead.emotion,
                    camera_intent=lead.camera_intent,
                    visual_timing=lead.visual_timing,
                    composition=lead.composition,
                    timing_offset=lead.timing_offset,
                    impact_lock=any(beat.impact_lock for beat in group),
                )
            )
    return result


def _reference_section_durations(
    beats: list[director.StoryBeat], total_duration: float
) -> list[tuple[str, float]]:
    durations: list[tuple[str, float]] = []
    cursor = 0
    source_total = max(0.001, beats[-1].end_time - beats[0].start_time)
    while cursor < len(beats):
        start = cursor
        section = beats[cursor].section
        while cursor < len(beats) and beats[cursor].section == section:
            cursor += 1
        duration = max(0.001, beats[cursor - 1].end_time - beats[start].start_time)
        durations.append((section, total_duration * duration / source_total))
    correction = total_duration - sum(duration for _, duration in durations)
    if durations:
        section, duration = durations[-1]
        durations[-1] = (section, duration + correction)
    return durations


def _reference_emphasis_indexes(shots: list[dict]) -> set[int]:
    """Choose emphasis slots from semantic priority in stable source order."""
    if len(shots) < 2:
        return set()
    target = max(1, min(len(shots) - 1, round(len(shots) * 0.25)))
    section_order = {
        section: index
        for index, section in enumerate(
            dict.fromkeys(str(shot.get("section", "")) for shot in shots)
        )
    }
    ranked: list[tuple[int, int, int, int]] = []
    for index, shot in enumerate(shots):
        section = str(shot.get("section", ""))
        intent = str(shot.get("camera_intent", "") or "").lower()
        intent_priority = max(
            (priority for tag, priority in _BEAT_PRIORITY.items() if tag in intent),
            default=0,
        )
        section_priority = _BEAT_PRIORITY.get(
            section,
            {"twist": 4, "cta": 4, "cliffhanger": 4}.get(section, 0),
        )
        if section in {"twist", "cta", "cliffhanger"}:
            section_priority += 2
        ranked.append(
            (-intent_priority, -section_priority, section_order.get(section, 0), index)
        )
    selected: list[int] = []
    per_section: dict[str, int] = {}
    section_caps: dict[str, int] = {}
    for shot in shots:
        section = str(shot.get("section", ""))
        section_count = sum(
            1 for item in shots if str(item.get("section", "")) == section
        )
        section_caps[section] = max(0, min(section_count - 1, math.ceil(section_count * 0.4)))
    for _intent_priority, _section_priority, _section_index, index in sorted(ranked):
        section = str(shots[index].get("section", ""))
        if per_section.get(section, 0) >= section_caps[section]:
            continue
        selected.append(index)
        per_section[section] = per_section.get(section, 0) + 1
        if len(selected) == target:
            break
    return set(selected)


def _reference_section_shot_durations(
    section_duration: float,
    shot_count: int,
    emphasis_count: int,
    profile: object,
    emphasis_positions: set[int] | None = None,
    *,
    allow_review_cadence_adaptation: bool = False,
) -> list[float]:
    if allow_review_cadence_adaptation:
        values = [section_duration / shot_count] * shot_count
    elif emphasis_count <= 0:
        values = [section_duration / shot_count] * shot_count
    else:
        emphasis_duration = min(
            profile.emphasis_max_s,
            max(profile.emphasis_min_s, section_duration / shot_count + 0.4),
        )
        normal_count = shot_count - emphasis_count
        if normal_count <= 0:
            raise ReferencePlanningError(
                f"{profile.profile_id} cannot leave a normal cadence in every section"
            )
        normal_duration = (
            section_duration - emphasis_count * emphasis_duration
        ) / normal_count
        positions = emphasis_positions or set(range(emphasis_count))
        values = [
            emphasis_duration if index in positions else normal_duration
            for index in range(shot_count)
        ]
    if not allow_review_cadence_adaptation and any(
        not (
            profile.hold_min_s <= value <= profile.hold_max_s
            or profile.emphasis_min_s <= value <= profile.emphasis_max_s
        )
        for value in values
    ):
        raise ReferencePlanningError(
            f"{profile.profile_id} cannot fit a section within its duration bands"
        )
    return values


def _apply_reference_motion(shots: list[dict], beats: list[director.StoryBeat]) -> None:
    """Attach one safe, purposeful MotionPlan to every reference shot."""
    curve_for = {
        "hold": "static",
        "static_emphasis": "static_emphasis",
        "slow_push": "slow_push_in",
        "slow_pull": "slow_pull_out",
        "guided_pan": "pan_horizontal",
        "focus_shift": "focus_shift",
        "panel_reveal": "reveal",
        "impact": "push_in",
        "atmospheric": "atmospheric",
        "split_focus": "focus_shift",
        "panel_stack": "slow_pull_out",
    }
    section_text = {
        beat.section: beat.text
        for beat in beats
        if beat.section not in {""}
    }
    history: list[str] = []
    for index, shot in enumerate(shots):
        text = str(shot.get("text", "") or section_text.get(shot.get("section", ""), ""))
        motion = motion_director.plan_motion(
            section=str(shot.get("section", "")),
            narration_tags=visual_scoring.narration_tags(text),
            roi_label=str(shot.get("roi_label", "")),
            duration=float(shot["end_time"]) - float(shot["start_time"]),
            history=history,
            seed=42,
            index=index,
        )
        history.append(motion.mode)
        shot["motion_mode"] = motion.mode
        shot["motion_intensity"] = motion.intensity
        shot["motion_reason"] = motion.reason or "reference stable motion intent"
        shot["camera_curve"] = motion_director.safe_camera_curve(
            curve_for.get(motion.mode, "static")
        )
        shot["overlay_text"] = ""


def _reference_roi_key(shot: Mapping[str, object]) -> tuple[object, ...]:
    return (
        shot.get("roi_label", ""),
        round(float(shot.get("focus_x", 0.0)), 3),
        round(float(shot.get("focus_y", 0.0)), 3),
        round(float(shot.get("focus_end_x", 0.0)), 3),
        round(float(shot.get("focus_end_y", 0.0)), 3),
    )



@dataclass(frozen=True)
class ReferenceROIAlternative:
    kind: str
    roi_label: str
    crop_box: tuple[int, int, int, int]
    focus: tuple[float, float, float, float]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ReferencePlanningError(
                "ROI kind is required", "visual.panel_lineage_unavailable"
            )
        if not isinstance(self.roi_label, str) or not self.roi_label.strip():
            raise ReferencePlanningError(
                "ROI label is required", "visual.panel_lineage_unavailable"
            )
        if "speech_bubble" in f"{self.kind}:{self.roi_label}".lower():
            raise ReferencePlanningError(
                "speech-bubble ROI is not renderable", "visual.balloon_mask_overlap"
            )
        if self.kind not in _REFERENCE_ROI_KINDS:
            raise ReferencePlanningError(
                "ROI kind is not a supported reference fallback phase",
                "visual.panel_lineage_unavailable",
            )
        if (
            not isinstance(self.crop_box, tuple)
            or len(self.crop_box) != 4
            or any(isinstance(value, bool) or not isinstance(value, int) for value in self.crop_box)
            or self.crop_box[0] < 0
            or self.crop_box[1] < 0
            or self.crop_box[2] <= self.crop_box[0]
            or self.crop_box[3] <= self.crop_box[1]
        ):
            raise ReferencePlanningError(
                "ROI crop_box must be a positive integer box",
                "visual.panel_lineage_unavailable",
            )
        if (
            not isinstance(self.focus, tuple)
            or len(self.focus) != 4
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0.0 <= float(value) <= 1.0
                for value in self.focus
            )
        ):
            raise ReferencePlanningError(
                "ROI focus must contain four normalized values",
                "visual.panel_lineage_unavailable",
            )


def _mask_identity(mask: framing_analysis.BorderMaskResult) -> str:
    try:
        return framing_analysis._mask_hash(
            mask.source_width,
            mask.source_height,
            mask.grid_width,
            mask.grid_height,
            mask.edge_connected_mask,
            mask.non_discardable_low_information_mask,
            mask.protected_mask,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise ReferencePlanningError(
            "border mask identity cannot be recomputed",
            "visual.panel_lineage_unavailable",
        ) from exc


def _validate_border_mask(
    mask: framing_analysis.BorderMaskResult,
    panel_size: tuple[int, int],
    contract_version: str,
) -> None:
    if not isinstance(mask, framing_analysis.BorderMaskResult):
        raise ReferencePlanningError(
            "border mask type is invalid", "visual.panel_lineage_unavailable"
        )
    try:
        shape_valid = (
            isinstance(panel_size, tuple)
            and len(panel_size) == 2
            and all(
                not isinstance(value, bool) and isinstance(value, int) and value > 0
                for value in panel_size
            )
            and isinstance(mask.source_width, int)
            and isinstance(mask.source_height, int)
            and isinstance(mask.grid_width, int)
            and isinstance(mask.grid_height, int)
            and not isinstance(mask.grid_width, bool)
            and not isinstance(mask.grid_height, bool)
            and mask.grid_width > 0
            and mask.grid_height > 0
            and (mask.source_width, mask.source_height) == panel_size
            and len(mask.edge_connected_mask) == mask.grid_height
            and len(mask.non_discardable_low_information_mask) == mask.grid_height
            and len(mask.protected_mask) == mask.grid_height
            and all(len(row) == mask.grid_width for row in mask.edge_connected_mask)
            and all(
                len(row) == mask.grid_width
                for row in mask.non_discardable_low_information_mask
            )
            and all(len(row) == mask.grid_width for row in mask.protected_mask)
        )
    except (AttributeError, TypeError, ValueError):
        shape_valid = False
    if not shape_valid:
        raise ReferencePlanningError(
            "border mask dimensions or shape do not match the panel",
            "visual.panel_lineage_unavailable",
        )
    if not framing_analysis.detector_contract_matches(
        contract_version, mask.detector_version
    ):
        raise ReferencePlanningError(
            "border mask detector contract does not match visual evidence",
            "visual.framing_contract_incompatible",
        )
    if not isinstance(mask.mask_sha256, str) or mask.mask_sha256 != _mask_identity(mask):
        raise ReferencePlanningError(
            "border mask identity cannot be verified",
            "visual.panel_lineage_unavailable",
        )


@dataclass(frozen=True)
class ReferencePanelFallbackCandidate:
    source_asset_id: str
    panel_region_id: str
    panel_id: str
    source_order: int
    panel_bounds: tuple[int, int, int, int]
    panel_size: tuple[int, int]
    border_mask: framing_analysis.BorderMaskResult
    source_asset_checksum: str
    visual_evidence: visual_scoring.PanelVisualEvidence
    evidence_hash: str
    eligible_sections: tuple[str, ...]
    eligible_beats: tuple[str, ...]
    roi_alternatives: tuple[ReferenceROIAlternative, ...]
    panel_candidate: visual_scoring.PanelCandidate
    source_upscale_manifest: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (
                self.source_asset_id,
                self.panel_region_id,
                self.panel_id,
                self.source_asset_checksum,
                self.evidence_hash,
            )
        ):
            raise ReferencePlanningError(
                "panel candidate identity is incomplete",
                "visual.panel_lineage_unavailable",
            )
        if (
            not isinstance(self.source_order, int)
            or isinstance(self.source_order, bool)
            or self.source_order <= 0
            or not isinstance(self.panel_bounds, tuple)
            or len(self.panel_bounds) != 4
            or any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in self.panel_bounds
            )
            or self.panel_bounds[2] <= self.panel_bounds[0]
            or self.panel_bounds[3] <= self.panel_bounds[1]
        ):
            raise ReferencePlanningError(
                "panel bounds or source order is invalid",
                "visual.panel_lineage_unavailable",
            )
        if (
            not isinstance(self.panel_size, tuple)
            or len(self.panel_size) != 2
            or any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in self.panel_size
            )
            or self.panel_size[0] <= 0
            or self.panel_size[1] <= 0
        ):
            raise ReferencePlanningError(
                "panel size is invalid", "visual.panel_lineage_unavailable"
            )
        x0, y0, x1, y1 = self.panel_bounds
        if x0 < 0 or y0 < 0:
            raise ReferencePlanningError(
                "panel bounds must be nonnegative",
                "visual.panel_lineage_unavailable",
            )
        if self.source_upscale_manifest is None:
            bounds_match_panel = (x1 - x0, y1 - y0) == self.panel_size
        else:
            try:
                review_source_upscale.validate_review_manifest_dimensions(
                    self.source_upscale_manifest, self.panel_size
                )
                prepared_bounds = tuple(
                    int(value)
                    for value in self.source_upscale_manifest["prepared_panel_bounds"]
                )
                source_bounds = tuple(
                    int(value)
                    for value in self.source_upscale_manifest["source_panel_bounds"]
                )
                bounds_match_panel = (
                    prepared_bounds == self.panel_bounds
                    and len(source_bounds) == 4
                    and source_bounds[2] > source_bounds[0]
                    and source_bounds[3] > source_bounds[1]
                )
            except (
                KeyError,
                TypeError,
                ValueError,
                review_source_upscale.ReviewSourceUpscaleError,
            ):
                bounds_match_panel = False
        if not bounds_match_panel:
            raise ReferencePlanningError(
                "panel bounds must match the prepared panel geometry",
                "visual.panel_lineage_unavailable",
            )
        try:
            evidence = (
                visual_scoring.parse_panel_visual_evidence(self.visual_evidence)
                if isinstance(self.visual_evidence, Mapping)
                else self.visual_evidence
            )
            visual_scoring.validate_panel_visual_evidence(evidence)
            local_hash = visual_scoring.visual_evidence_hash(evidence)
        except (visual_scoring.VisualEvidenceError, TypeError, ValueError) as exc:
            raise ReferencePlanningError(
                "panel visual evidence is invalid",
                "visual.panel_lineage_unavailable",
            ) from exc
        if (
            evidence.panel_id != self.panel_id
            or evidence.source_asset_id != self.source_asset_id
            or evidence.source_order != self.source_order
            or self.evidence_hash != local_hash
        ):
            raise ReferencePlanningError(
                "panel visual evidence lineage or hash does not match",
                "visual.panel_lineage_unavailable",
            )
        object.__setattr__(self, "visual_evidence", evidence)
        _validate_border_mask(
            self.border_mask, self.panel_size, evidence.contract_version
        )
        if (
            not isinstance(self.panel_candidate, visual_scoring.PanelCandidate)
            or self.panel_candidate.asset_id != self.source_asset_id
            or self.panel_candidate.order_index != self.source_order
        ):
            raise ReferencePlanningError(
                "render candidate does not match panel lineage",
                "visual.panel_lineage_unavailable",
            )
        if not self.roi_alternatives or not any(
            roi.kind == "primary" for roi in self.roi_alternatives
        ):
            raise ReferencePlanningError(
                "panel candidate requires a primary ROI alternative",
                "visual.panel_lineage_unavailable",
            )
        for roi in self.roi_alternatives:
            if (
                roi.crop_box[2] > self.panel_size[0]
                or roi.crop_box[3] > self.panel_size[1]
            ):
                raise ReferencePlanningError(
                    "ROI is outside the panel crop",
                    "visual.panel_lineage_unavailable",
                )


def _border_mask_json(mask: framing_analysis.BorderMaskResult) -> dict:
    return asdict(mask)


def _telemetry_json(telemetry: object) -> dict:
    if is_dataclass(telemetry):
        return asdict(telemetry)
    if isinstance(telemetry, Mapping):
        return dict(telemetry)
    return {
        key: getattr(telemetry, key)
        for key in (
            "contract_version",
            "detector_version",
            "mask_sha256",
            "crop_box",
            "base_zoom",
            "source_resolution_zoom_cap",
            "protected_region_zoom_cap",
            "edge_connected_blank_fraction",
            "non_discardable_low_information_fraction",
            "protected_retained_fraction",
            "balloon_mask_intersection_ratio",
            "subject_coverage",
            "face_coverage",
            "action_coverage",
            "effect_coverage",
            "continuity_context_coverage",
            "mask_confidence",
            "mask_source",
            "fallback_reason",
            "rejection_code",
        )
        if hasattr(telemetry, key)
    }


def _roi_key(roi: ReferenceROIAlternative) -> tuple[object, ...]:
    return (roi.roi_label, roi.crop_box, roi.focus)


def _candidate_is_eligible(
    candidate: ReferencePanelFallbackCandidate,
    section: str,
    beat: str,
) -> bool:
    return (
        (not candidate.eligible_sections or section in candidate.eligible_sections)
        and (not candidate.eligible_beats or beat in candidate.eligible_beats)
    )


def _canonical_json_mapping(value: Mapping[str, object]) -> dict:
    return json.loads(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )


def _ordered_roi_alternatives(
    candidate: ReferencePanelFallbackCandidate,
) -> tuple[ReferenceROIAlternative, ...]:
    return tuple(
        sorted(
            candidate.roi_alternatives,
            key=lambda roi: (
                _REFERENCE_ROI_KIND_ORDER[roi.kind],
                roi.roi_label,
                roi.crop_box,
                roi.focus,
            ),
        )
    )


def _reference_panel_attempt(
    candidate: ReferencePanelFallbackCandidate,
    roi: ReferenceROIAlternative,
    *,
    profile: object,
    attempt_order: int,
    phase_kind: str,
    previously_used: bool,
    used_rois: set[tuple[object, ...]],
    allow_source_resolution_warning: bool = False,
) -> tuple[bool, object, dict]:
    evidence = candidate.visual_evidence
    entry_kind = phase_kind
    try:
        ready = visual_scoring.require_reference_ready_visual_evidence(evidence)
    except visual_scoring.VisualEvidenceError as exc:
        if exc.code == "visual.balloon_mask_unknown":
            raise ReferencePlanningError(str(exc), exc.code) from exc
        raise ReferencePlanningError(
            "panel readiness is invalid", "visual.panel_lineage_unavailable"
        ) from exc
    if previously_used and _roi_key(roi) in used_rois:
        telemetry = {
            "rejection_code": "visual.reuse_roi_duplicate",
            "crop_box": roi.crop_box,
        }
        return False, telemetry, {
            "attempt_order": attempt_order,
            "panel_region_id": candidate.panel_region_id,
            "panel_id": candidate.panel_id,
            "source_asset_id": candidate.source_asset_id,
            "source_asset_checksum": candidate.source_asset_checksum,
            "source_order": candidate.source_order,
            "panel_size": list(candidate.panel_size),
            "roi_label": roi.roi_label,
            "crop_box": list(roi.crop_box),
            "evidence_hash": candidate.evidence_hash,
            "detector_version": candidate.border_mask.detector_version,
            "mask_sha256": candidate.border_mask.mask_sha256,
            "telemetry": telemetry,
            "kind": entry_kind,
            "roi_kind": roi.kind,
            "accepted": False,
            "code": "visual.reuse_roi_duplicate",
            "reason": "a repeated panel requires a distinct ROI",
        }
    source_manifest = candidate.source_upscale_manifest
    allow_low_resolution = bool(
        allow_source_resolution_warning
        and isinstance(source_manifest, Mapping)
        and source_manifest.get("policy_id") == "review_silent_source_upscale_v1"
        and source_manifest.get("resolution_state") == "LOW_SOURCE_RESOLUTION"
        and source_manifest.get("non_native_warning") == "review.low_source_resolution"
    )
    feasibility_kwargs: dict[str, object] = {}
    if allow_low_resolution:
        feasibility_kwargs["allow_source_resolution_warning"] = True
    try:
        feasible, telemetry = framing_analysis.candidate_is_feasible(
            roi.crop_box,
            ready,
            candidate.border_mask,
            candidate.panel_size,
            (profile.final_width, profile.final_height),
            **feasibility_kwargs,
        )
    except framing_analysis.VisualEvidenceError as exc:
        if exc.code == "visual.balloon_mask_unknown":
            raise ReferencePlanningError(str(exc), exc.code) from exc
        telemetry = {
            "rejection_code": exc.code,
            "error": str(exc),
        }
        feasible = False
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ReferencePlanningError(
            "candidate feasibility boundary failed",
            "visual.panel_lineage_unavailable",
        ) from exc
    rejection = (
        getattr(telemetry, "rejection_code", None)
        if not isinstance(telemetry, Mapping)
        else telemetry.get("rejection_code")
    )
    accepted = bool(feasible)
    code = None if accepted else rejection or "visual.visual_unavailable"
    entry = {
        "attempt_order": attempt_order,
        "panel_region_id": candidate.panel_region_id,
        "panel_id": candidate.panel_id,
        "source_asset_id": candidate.source_asset_id,
        "source_asset_checksum": candidate.source_asset_checksum,
        "source_order": candidate.source_order,
        "panel_size": list(candidate.panel_size),
        "roi_label": roi.roi_label,
        "crop_box": list(roi.crop_box),
        "evidence_hash": candidate.evidence_hash,
        "detector_version": candidate.border_mask.detector_version,
        "mask_sha256": candidate.border_mask.mask_sha256,
        "telemetry": _telemetry_json(telemetry),
        "kind": entry_kind,
        "roi_kind": roi.kind,
        "accepted": accepted,
        "code": code,
        "reason": "accepted" if accepted else "candidate failed hard framing constraints",
    }
    return accepted, telemetry, entry


def _prioritize_resolution_candidates(
    candidates: Sequence[ReferencePanelFallbackCandidate],
) -> tuple[ReferencePanelFallbackCandidate, ...]:
    """Try automatic/native-resolution candidates before review warnings."""

    def is_low_resolution(candidate: ReferencePanelFallbackCandidate) -> bool:
        manifest = candidate.source_upscale_manifest
        return bool(
            isinstance(manifest, Mapping)
            and manifest.get("policy_id") == "review_silent_source_upscale_v1"
            and manifest.get("resolution_state") == "LOW_SOURCE_RESOLUTION"
            and manifest.get("non_native_warning") == "review.low_source_resolution"
        )

    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                is_low_resolution(candidate),
                candidate.source_order,
                candidate.panel_id,
                candidate.panel_region_id,
            ),
        )
    )


def _feasible_roi_capacity(
    candidate: ReferencePanelFallbackCandidate,
    profile: object,
    *,
    allow_source_resolution_warning: bool,
) -> int:
    """Count exact feasible ROI alternatives for review cadence allocation."""
    source_manifest = candidate.source_upscale_manifest
    allow_low_resolution = bool(
        allow_source_resolution_warning
        and isinstance(source_manifest, Mapping)
        and source_manifest.get("policy_id") == "review_silent_source_upscale_v1"
        and source_manifest.get("resolution_state") == "LOW_SOURCE_RESOLUTION"
        and source_manifest.get("non_native_warning") == "review.low_source_resolution"
    )
    feasibility_kwargs: dict[str, object] = {}
    if allow_low_resolution:
        feasibility_kwargs["allow_source_resolution_warning"] = True
    ready = visual_scoring.require_reference_ready_visual_evidence(
        candidate.visual_evidence
    )
    feasible = 0
    for roi in _ordered_roi_alternatives(candidate):
        accepted, _telemetry = framing_analysis.candidate_is_feasible(
            roi.crop_box,
            ready,
            candidate.border_mask,
            candidate.panel_size,
            (profile.final_width, profile.final_height),
            **feasibility_kwargs,
        )
        if accepted:
            feasible += 1
    return min(feasible, profile.max_canonical_panel_uses)


def _plan_reference_panel_candidates(
    spans: list[object],
    profile: object,
    panel_candidates: Sequence[ReferencePanelFallbackCandidate],
    *,
    allow_source_resolution_warning: bool = False,
    allow_review_cadence_adaptation: bool = False,
    allow_review_duration: bool = False,
) -> list[dict]:
    if not panel_candidates:
        raise ReferencePlanningError(
            "explicit panel candidate sequence is empty",
            "visual.panel_lineage_unavailable",
        )
    ordered = tuple(
        sorted(
            panel_candidates,
            key=lambda candidate: (
                candidate.source_order,
                candidate.panel_id,
                candidate.panel_region_id,
            ),
        )
    )
    seen_panel_ids: set[str] = set()
    seen_region_ids: set[str] = set()
    for candidate in ordered:
        if (
            candidate.panel_id in seen_panel_ids
            or candidate.panel_region_id in seen_region_ids
        ):
            raise ReferencePlanningError(
                "panel candidate identity is ambiguous",
                "visual.panel_lineage_unavailable",
            )
        seen_panel_ids.add(candidate.panel_id)
        seen_region_ids.add(candidate.panel_region_id)
        _validate_border_mask(
            candidate.border_mask,
            candidate.panel_size,
            candidate.visual_evidence.contract_version,
        )
        if not framing_analysis.detector_contract_matches(
            profile.framing_contract_version, candidate.border_mask.detector_version
        ):
            raise ReferencePlanningError(
                "panel mask contract does not match profile",
                "visual.framing_contract_incompatible",
            )
        try:
            visual_scoring.require_reference_ready_visual_evidence(
                candidate.visual_evidence
            )
        except visual_scoring.VisualEvidenceError as exc:
            raise ReferencePlanningError(str(exc), exc.code) from exc
    timing_candidates = [
        replace(
            candidate.panel_candidate,
            asset_id=f"__reference_panel__{candidate.panel_id}",
        )
        for candidate in ordered
    ]
    section_names = tuple(
        dict.fromkeys(str(span.section) for span in spans if str(span.section))
    )
    section_capacity: dict[str, int] = {}
    for section in section_names:
        eligible_for_section = [
            candidate
            for candidate in ordered
            if not candidate.eligible_sections or section in candidate.eligible_sections
        ]
        capacities = [
            _feasible_roi_capacity(
                candidate,
                profile,
                allow_source_resolution_warning=allow_source_resolution_warning,
            )
            for candidate in eligible_for_section
        ]
        # A single eligible panel cannot be selected twice in succession. Its
        # feasible ROI count is still useful for multi-panel reuse, but must
        # not inflate this section's cadence capacity beyond one shot.
        if len({candidate.panel_id for candidate in eligible_for_section}) == 1:
            section_capacity[section] = 1 if any(capacity > 0 for capacity in capacities) else 0
        else:
            section_capacity[section] = sum(capacities)
    if allow_review_cadence_adaptation and any(
        capacity < 1 for capacity in section_capacity.values()
    ):
        raise ReferencePlanningError(
            "no feasible exact panel candidate covers every story section",
            "visual.visual_unavailable",
        )
    base_shots = _plan_reference(
        spans,
        timing_candidates,
        profile,
        None,
        None,
        max_shots_by_section=(section_capacity if allow_review_cadence_adaptation else None),
        allow_review_cadence_adaptation=allow_review_cadence_adaptation,
        allow_review_duration=allow_review_duration,
    )
    uses: dict[str, int] = {}
    used_rois: dict[str, set[tuple[object, ...]]] = {}
    last_panel_id = ""
    selected_shots: list[dict] = []
    for shot_index, shot in enumerate(base_shots):
        section = str(shot.get("section", ""))
        beat = str(shot.get("camera_intent", "") or "")
        rotated = ordered[shot_index % len(ordered) :] + ordered[: shot_index % len(ordered)]
        eligible = [
            candidate
            for candidate in rotated
            if _candidate_is_eligible(candidate, section, beat)
            and uses.get(candidate.panel_id, 0) < profile.max_canonical_panel_uses
            and candidate.panel_id != last_panel_id
        ]
        eligible = list(_prioritize_resolution_candidates(eligible))
        if allow_review_cadence_adaptation:
            eligible = [
                candidate
                for candidate in eligible
                if _feasible_roi_capacity(
                    candidate,
                    profile,
                    allow_source_resolution_warning=allow_source_resolution_warning,
                )
                > len(used_rois.get(candidate.panel_id, set()))
            ]
            eligible.sort(
                key=lambda candidate: (
                    -(
                        _feasible_roi_capacity(
                            candidate,
                            profile,
                            allow_source_resolution_warning=allow_source_resolution_warning,
                        )
                        - len(used_rois.get(candidate.panel_id, set()))
                    ),
                    candidate.source_order,
                    candidate.panel_id,
                    candidate.panel_region_id,
                )
            )
        if not eligible:
            raise ReferencePlanningError(
                f"no exact panel candidate is eligible for section {section}",
                "visual.visual_unavailable",
            )
        attempts: list[dict] = []
        preferred_candidate = eligible[0]
        accepted_candidate: ReferencePanelFallbackCandidate | None = None
        accepted_roi: ReferenceROIAlternative | None = None
        accepted_telemetry: object | None = None
        accepted_phase: str | None = None
        for candidate in eligible:
            previous_uses = uses.get(candidate.panel_id, 0)
            is_preferred = candidate.panel_id == preferred_candidate.panel_id
            if is_preferred:
                roi_plan = tuple(
                    (roi, roi.kind) for roi in _ordered_roi_alternatives(candidate)
                )
            else:
                roi_plan = tuple(
                    (roi, "alternate_panel")
                    for roi in _ordered_roi_alternatives(candidate)
                )
            for roi, phase_kind in roi_plan:
                accepted, telemetry, entry = _reference_panel_attempt(
                    candidate,
                    roi,
                    profile=profile,
                    attempt_order=len(attempts),
                    phase_kind=phase_kind,
                    previously_used=previous_uses > 0,
                    used_rois=used_rois.get(candidate.panel_id, set()),
                    allow_source_resolution_warning=allow_source_resolution_warning,
                )
                attempts.append(entry)
                if accepted:
                    accepted_candidate = candidate
                    accepted_roi = roi
                    accepted_telemetry = telemetry
                    accepted_phase = phase_kind
                    break
            if accepted_candidate is not None:
                break
        if accepted_candidate is None or accepted_roi is None or accepted_telemetry is None:
            raise ReferencePlanningError(
                f"no feasible exact panel candidate for section {section}",
                "visual.visual_unavailable",
            )
        candidate = accepted_candidate
        roi = accepted_roi
        uses[candidate.panel_id] = uses.get(candidate.panel_id, 0) + 1
        used_rois.setdefault(candidate.panel_id, set()).add(_roi_key(roi))
        reasons = list(shot.get("alignment_reasons", ()))
        reasons.extend(
            (
                "panel_evidence_alignment",
                f"panel_lineage:{candidate.panel_id}",
                f"source_order:{candidate.source_order}",
            )
        )
        if candidate.panel_id != candidate.source_asset_id:
            reasons.append("panel_keyed_candidate")
        if uses[candidate.panel_id] > 1:
            reasons.append(f"reuse_purpose:distinct_roi:{roi.roi_label}")
        if accepted_phase == "alternate_roi":
            reasons.append("fallback:alternate_roi")
        elif accepted_phase == "tighter_crop":
            reasons.append("fallback:tighter_crop")
        elif accepted_phase == "alternate_panel":
            reasons.append("fallback:alternate_panel_same_beat")
        telemetry_record = _telemetry_json(accepted_telemetry)
        telemetry_record.update(
            {
                "panel_id": candidate.panel_id,
                "panel_region_id": candidate.panel_region_id,
                "source_asset_checksum": candidate.source_asset_checksum,
                "source_order": candidate.source_order,
                "panel_size": list(candidate.panel_size),
                "evidence_hash": candidate.evidence_hash,
                "selected_roi": {
                    "kind": roi.kind,
                    "roi_label": roi.roi_label,
                    "crop_box": list(roi.crop_box),
                    "focus": list(roi.focus),
                },
                "candidate_count": len(eligible),
                "selection_context": {
                    "section": section,
                    "beat": beat,
                    "preferred_panel_id": preferred_candidate.panel_id,
                    "selected_panel_id": candidate.panel_id,
                    "selected_attempt_order": len(attempts) - 1,
                    "attempt_count": len(attempts),
                    "candidate_count": len(eligible),
                },
            }
        )
        if shot.get("visual_review_warnings"):
            telemetry_record["visual_review_warnings"] = list(
                shot["visual_review_warnings"]
            )
            telemetry_record["nominal_target_shots"] = shot.get(
                "nominal_target_shots"
            )
            telemetry_record["planned_target_shots"] = shot.get(
                "planned_target_shots"
            )
        telemetry_record = _canonical_json_mapping(telemetry_record)
        accepted_attempt_order = len(attempts) - 1
        attempts[accepted_attempt_order]["telemetry"] = telemetry_record
        shot.update(
            {
                "asset_id": candidate.source_asset_id,
                "panel_region_id": candidate.panel_region_id,
                "panel_id": candidate.panel_id,
                "source_order": candidate.source_order,
                "panel_bounds": list(candidate.panel_bounds),
                "panel_size": list(candidate.panel_size),
                "border_mask": _border_mask_json(candidate.border_mask),
                "source_asset_checksum": candidate.source_asset_checksum,
                "visual_evidence": visual_scoring.panel_visual_evidence_json(
                    candidate.visual_evidence
                ),
                "evidence_hash": candidate.evidence_hash,
                "framing_telemetry": telemetry_record,
                "roi": {
                    "kind": roi.kind,
                    "roi_label": roi.roi_label,
                    "crop_box": list(roi.crop_box),
                    "focus": list(roi.focus),
                },
                "roi_label": roi.roi_label,
                "focus_x": roi.focus[0],
                "focus_y": roi.focus[1],
                "focus_end_x": roi.focus[2],
                "focus_end_y": roi.focus[3],
                "alignment_reasons": sorted(set(reasons)),
                "fallback_attempts": attempts,
            }
        )
        selected_shots.append(shot)
        last_panel_id = candidate.panel_id
    return selected_shots


def _plan_reference(
    spans: list[object],
    candidates: list[object],
    profile: object,
    cited_asset_ids_by_section: Mapping[str, Iterable[str]] | None,
    citation_alignment_reasons_by_section: Mapping[str, Iterable[str]] | None,
    *,
    allow_review_cadence_adaptation: bool = False,
    max_shots_by_section: Mapping[str, int] | None = None,
    allow_review_duration: bool = False,
) -> list[dict]:
    if not spans or not candidates:
        raise ReferencePlanningError(
            f"{profile.profile_id} requires renderable panels and narration spans"
        )
    origin = min(float(span.start_time) for span in spans)
    end = max(float(span.end_time) for span in spans)
    total_duration = end - origin
    duration_min_s = 50.0 if allow_review_duration else profile.duration_min_s
    duration_max_s = 60.0 if allow_review_duration else profile.duration_max_s
    if not duration_min_s <= total_duration <= duration_max_s:
        raise ReferencePlanningError(
            f"{profile.profile_id} requires duration between "
            f"{duration_min_s:.1f} and {duration_max_s:.1f} seconds"
        )
    nominal_target = max(
        profile.shot_min,
        min(profile.shot_max, round(total_duration / _REFERENCE_SHOT_INTERVAL_SECONDS)),
    )
    target = nominal_target
    capacity = len(candidates) * profile.max_canonical_panel_uses
    if max_shots_by_section is not None:
        capacity = min(capacity, sum(max_shots_by_section.values()))
    cadence_adapted = False
    if capacity < target:
        if not allow_review_cadence_adaptation:
            raise ReferencePlanningError(
                f"{profile.profile_id} cannot satisfy the panel reuse cap for "
                f"{target} shots"
            )
        section_count = len({str(span.section) for span in spans if str(span.section)})
        if capacity < max(1, section_count):
            raise ReferencePlanningError(
                f"{profile.profile_id} has insufficient feasible panel capacity for "
                f"{section_count} story sections"
            )
        target = capacity

    if capacity < target:
        raise ReferencePlanningError(
            f"{profile.profile_id} cannot satisfy the panel reuse cap for "
            f"{target} shots"
        )
    cadence_adapted = target != nominal_target

    beats = _coalesce_beats(
        director.analyze_story(spans),
        target_shots=target,
        max_counts_by_section=max_shots_by_section,
    )
    shots = visual_scoring.plan_content_aware_scenes(
        beats,
        candidates,
        min_scene_seconds=profile.hold_min_s,
        max_scene_seconds=profile.emphasis_max_s,
        preferred_asset_ids_by_section=cited_asset_ids_by_section,
        max_asset_uses=profile.max_canonical_panel_uses,
    )
    if cadence_adapted and len(shots) != target:
        section_order = list(dict.fromkeys(str(beat.section) for beat in beats))
        target_counts = _reference_group_counts(
            beats,
            target,
            max_counts_by_section=max_shots_by_section,
        )
        collapsed: list[dict] = []
        for section, section_target in zip(section_order, target_counts, strict=True):
            section_shots = [
                shot for shot in shots if str(shot.get("section", "")) == section
            ]
            if len(section_shots) < section_target:
                raise ReferencePlanningError(
                    f"{profile.profile_id} could not preserve review cadence for section {section}"
                )
            positions = (
                [0]
                if section_target == 1
                else [
                    round(index * (len(section_shots) - 1) / (section_target - 1))
                    for index in range(section_target)
                ]
            )
            collapsed.extend(dict(section_shots[position]) for position in positions)
        shots = collapsed
    if len(shots) != target:
        raise ReferencePlanningError(
            f"{profile.profile_id} planned {len(shots)} shots; expected {target}"
        )

    if cadence_adapted:
        warning = "visual.cadence_adapted_to_feasible_capacity"
        for shot in shots:
            reasons = list(shot.get("alignment_reasons", ()))
            reasons.append(warning)
            shot["alignment_reasons"] = sorted(set(reasons))
            shot["visual_review_warnings"] = [warning]
            shot["nominal_target_shots"] = nominal_target
            shot["planned_target_shots"] = target

    # With exactly the minimum viable panel pool, deterministic chronological
    # rotation is the only safe way to use every panel twice without exceeding
    # the cap. Citation-constrained plans retain the scorer's evidence order.
    if cited_asset_ids_by_section is None and len(candidates) * 2 == target:
        ordered = sorted(candidates, key=lambda candidate: (candidate.order_index, candidate.asset_id))
        for index, shot in enumerate(shots):
            candidate = ordered[index % len(ordered)]
            shot["asset_id"] = candidate.asset_id
            shot["source_family"] = candidate.source_family
            shot["visual_score"] = candidate.visual_score
            shot["semantic_score"] = candidate.semantic_score
            shot["visual_signature"] = candidate.features.visual_signature
            reasons = list(shot.get("alignment_reasons", ()))
            reasons.append("reference:deterministic_chronology")
            shot["alignment_reasons"] = reasons

    emphasis_indexes = _reference_emphasis_indexes(shots)
    section_durations = _reference_section_durations(beats, total_duration)
    shot_cursor = 0
    timeline_cursor = origin
    candidate_ids = {str(candidate.asset_id) for candidate in candidates}
    for section, section_duration in section_durations:
        section_shots = []
        while shot_cursor + len(section_shots) < len(shots) and shots[shot_cursor + len(section_shots)]["section"] == section:
            section_shots.append(shots[shot_cursor + len(section_shots)])
        if not section_shots:
            raise ReferencePlanningError(
                f"{profile.profile_id} lost section {section} during shot planning"
            )
        section_indexes = set(range(shot_cursor, shot_cursor + len(section_shots)))
        section_emphasis = emphasis_indexes & section_indexes
        durations = _reference_section_shot_durations(
            section_duration,
            len(section_shots),
            len(section_emphasis),
            profile,
            {index - shot_cursor for index in section_emphasis},
            allow_review_cadence_adaptation=cadence_adapted,
        )
        for shot_index, shot in enumerate(section_shots):
            start = round(timeline_cursor, 3)
            end_time = (
                end
                if shot_cursor + shot_index == len(shots) - 1
                else round(timeline_cursor + durations[shot_index], 3)
            )
            shot["start_time"] = start
            shot["end_time"] = end_time
            shot["transition"] = "none" if shot_cursor + shot_index == 0 else "cut"
            reasons = list(shot.get("alignment_reasons", ()))
            valid = (
                {
                    str(asset_id)
                    for asset_id in cited_asset_ids_by_section.get(section, ())
                    if str(asset_id) in candidate_ids
                }
                if cited_asset_ids_by_section
                else set()
            )
            if cited_asset_ids_by_section is not None:
                if valid and shot.get("asset_id") in valid:
                    reasons.append(f"citation_alignment:{shot['asset_id']}")
                elif valid:
                    anchor = sorted(valid)[0]
                    reasons.append(f"evidence_context_fallback:anchor:{anchor}")
                    reasons.extend(
                        citation_alignment_reasons_by_section.get(section, ())
                        if citation_alignment_reasons_by_section
                        else ()
                    )
                else:
                    reasons.extend(citation_alignment_reasons_by_section.get(section, ()) if citation_alignment_reasons_by_section else ())
                    reasons.append("evidence_fallback:unavailable")
            shot["alignment_reasons"] = sorted(set(reasons))
            timeline_cursor = end_time
        shot_cursor += len(section_shots)

    if cited_asset_ids_by_section is not None:
        for section, anchors in cited_asset_ids_by_section.items():
            valid = {
                str(asset_id)
                for asset_id in anchors
                if str(asset_id) in candidate_ids
            }
            if valid and not any(
                shot.get("section") == section and shot.get("asset_id") in valid
                for shot in shots
            ):
                raise ReferencePlanningError(
                    f"{profile.profile_id} could not place a cited anchor for section {section}"
                )

    positions: dict[str, list[int]] = {}
    for index, shot in enumerate(shots):
        asset_id = shot.get("asset_id")
        if not asset_id:
            raise ReferencePlanningError(f"{profile.profile_id} produced a shot without a panel")
        positions.setdefault(str(asset_id), []).append(index)
        if (
            not cadence_adapted
            and index
            and asset_id == shots[index - 1].get("asset_id")
        ):
            raise ReferencePlanningError(
                f"{profile.profile_id} produced consecutive panel reuse"
            )
    for _asset_id, indexes in positions.items():
        if not cadence_adapted and len(indexes) > profile.max_canonical_panel_uses:
            raise ReferencePlanningError(
                f"{profile.profile_id} exceeded the canonical panel reuse cap"
            )
        if not cadence_adapted and len(indexes) == 2:
            first, second = (shots[indexes[0]], shots[indexes[1]])
            if (
                first.get("roi_label", ""),
                round(float(first.get("focus_x", 0.0)), 3),
                round(float(first.get("focus_y", 0.0)), 3),
            ) == (
                second.get("roi_label", ""),
                round(float(second.get("focus_x", 0.0)), 3),
                round(float(second.get("focus_y", 0.0)), 3),
            ):
                second["roi_label"] = f"{second.get('roi_label', 'roi')}_reuse_alt"
                second["focus_x"] = round(1.0 - float(second.get("focus_x", 0.5)), 3)
                second["focus_end_x"] = round(1.0 - float(second.get("focus_end_x", 0.5)), 3)
            second_reasons = list(second.get("alignment_reasons", ()))
            second_reasons.append(
                f"reuse_purpose:distinct_roi:{first.get('roi_label', 'roi')}->"
                f"{second.get('roi_label', 'roi')}"
            )
            second["alignment_reasons"] = sorted(set(second_reasons))

    durations = [shot["end_time"] - shot["start_time"] for shot in shots]
    if cadence_adapted:
        if any(duration <= 0 for duration in durations):
            raise ReferencePlanningError(
                f"{profile.profile_id} produced a non-positive review shot duration"
            )
    else:
        normal = [duration for duration in durations if profile.hold_min_s <= duration <= profile.hold_max_s]
        emphasis = [duration for duration in durations if profile.emphasis_min_s <= duration <= profile.emphasis_max_s]
        if len(normal) + len(emphasis) != len(durations):
            raise ReferencePlanningError(f"{profile.profile_id} produced an unsupported shot duration")
        if len(normal) / len(durations) < profile.hold_ratio_min:
            raise ReferencePlanningError(f"{profile.profile_id} normal hold ratio is below the profile minimum")
        if len(normal) / len(durations) > profile.hold_ratio_max:
            raise ReferencePlanningError(f"{profile.profile_id} normal hold ratio exceeds the profile maximum")
        if len(emphasis) / len(durations) < profile.emphasis_ratio_min:
            raise ReferencePlanningError(f"{profile.profile_id} emphasis ratio is below the profile minimum")
        if len(emphasis) / len(durations) > profile.emphasis_ratio_max:
            raise ReferencePlanningError(f"{profile.profile_id} emphasis ratio exceeds the profile maximum")
        mean = sum(durations) / len(durations)
        if not profile.mean_shot_min_s <= mean <= profile.mean_shot_max_s:
            raise ReferencePlanningError(f"{profile.profile_id} mean shot duration is outside the profile band")
    _apply_reference_motion(shots, beats)
    return shots


def plan(
    spans: Iterable[object],
    candidates: list[object],
    profile: object | None = None,
    cited_asset_ids_by_section: Mapping[str, Iterable[str]] | None = None,
    citation_alignment_reasons_by_section: Mapping[str, Iterable[str]] | None = None,
    reference_panel_candidates: Sequence[ReferencePanelFallbackCandidate] | None = None,
    *,
    allow_source_resolution_warning: bool = False,
    allow_review_cadence_adaptation: bool = False,
    allow_review_duration: bool = False,
) -> list[dict]:
    """Create a beat-aware, ROI-driven shot list before rendering."""
    span_list = list(spans)
    if profile is not None and reference_panel_candidates is not None:
        return _plan_reference_panel_candidates(
            span_list,
            profile,
            tuple(reference_panel_candidates),
            allow_source_resolution_warning=allow_source_resolution_warning,
            allow_review_cadence_adaptation=allow_review_cadence_adaptation,
            allow_review_duration=allow_review_duration,
        )
    if profile is not None:
        return _plan_reference(
            span_list,
            candidates,
            profile,
            cited_asset_ids_by_section,
            citation_alignment_reasons_by_section,
            allow_review_cadence_adaptation=allow_review_cadence_adaptation,
            allow_review_duration=allow_review_duration,
        )
    beats = _coalesce_beats(director.analyze_story(span_list))
    shots = visual_scoring.plan_content_aware_scenes(beats, candidates)
    history: list[str] = []
    motion_plans = []
    curve_for = {
        "hold": "static", "static_emphasis": "static", "slow_push": "slow_push_in",
        "slow_pull": "slow_pull_out", "guided_pan": "pan_horizontal", "focus_shift": "focus_shift",
        "panel_reveal": "push_in", "impact": "push_in",
        "atmospheric": "slow_push_in", "split_focus": "focus_shift", "panel_stack": "slow_pull_out",
    }
    for index, shot in enumerate(shots):
        beat = next((b for b in beats if b.start_time <= shot["start_time"] < b.end_time), None)
        tags = visual_scoring.narration_tags(beat.text if beat else "")
        motion = motion_director.plan_motion(
            section=shot.get("section", ""), narration_tags=tags,
            roi_label=shot.get("roi_label", ""), duration=shot["end_time"] - shot["start_time"],
            history=history, seed=42, index=index,
        )
        history.append(motion.mode)
        motion_plans.append(motion)
        shot["motion_mode"] = motion.mode
        shot["motion_intensity"] = motion.intensity
        shot["motion_reason"] = motion.reason
        shot["camera_curve"] = curve_for[motion.mode]
        shot.setdefault("alignment_score", 0.0)
        shot.setdefault("alignment_reasons", [])
        shot.setdefault("rejected_candidates", [])
        shot.setdefault("visual_signature", "")
        shot.setdefault("editorial_audit", [])
        text = beat.text.lower() if beat else ""
        if any(token in text for token in ("because", "therefore", "which means", "the reason", "this explains")):
            shot["overlay_text"] = "CAUSE / EFFECT"

    # Normal renders need enough visual grammar to read as an edit rather than
    # one repeated Ken Burns move. Rebalance only neutral/release beats so
    # impact, speech-bubble, and panel-stack intent remains intact.
    if len(motion_plans) >= 4 and len({plan.mode for plan in motion_plans}) < 4:
        diversity_modes = ("slow_push", "guided_pan", "focus_shift", "slow_pull", "atmospheric")
        used_modes = {plan.mode for plan in motion_plans}
        for index, plan in enumerate(motion_plans):
            if len(used_modes) >= 4:
                break
            if plan.mode in {"impact", "split_focus", "panel_stack"}:
                continue
            replacement = next((mode for mode in diversity_modes if mode not in used_modes), "")
            if not replacement:
                break
            replacement_plan = motion_director.MotionPlan(
                replacement,
                plan.intensity,
                f"{plan.reason}; diversity rebalance",
                plan.seed,
            ).validate()
            motion_plans[index] = replacement_plan
            shots[index]["motion_mode"] = replacement_plan.mode
            shots[index]["motion_intensity"] = replacement_plan.intensity
            shots[index]["motion_reason"] = replacement_plan.reason
            shots[index]["camera_curve"] = curve_for[replacement_plan.mode]
            used_modes.add(replacement_plan.mode)

    shot_issues = director.audit_sequence(shots) + motion_director.audit_motion(motion_plans)
    for shot in shots:
        shot["editorial_audit"] = sorted(set(shot.get("editorial_audit", []) + shot_issues))
    return shots


def classify_source_text(text: str) -> dict[str, bool]:
    """Return conservative source flags used by the test/publication gate."""
    lower = text.lower()
    watermark_words = (
        "asurascans", "discord.gg", "follow us", "continue reading", "read the novel"
    )
    return {
        "has_ocr": bool(text.strip()),
        "watermark_detected": any(word in lower for word in watermark_words),
    }


__all__ = [
    "ReferencePlanningError",
    "ReferenceROIAlternative",
    "ReferencePanelFallbackCandidate",
    "classify_source_text",
    "plan",
]
