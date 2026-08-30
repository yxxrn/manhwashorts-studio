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

from app.constants import (
    STANDARD_FINAL_DURATION_MAX_SECONDS,
    STANDARD_FINAL_DURATION_MIN_SECONDS,
)
from app.services import (
    director,
    framing_analysis,
    motion_director,
    reference_profile,
    review_source_upscale,
    roi_detection,
    visual_planning,
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
    "aggressive_crop": 3,
}
_REFERENCE_ROI_KINDS = frozenset(_REFERENCE_ROI_KIND_ORDER)


def _review_visual_shot_target(total_duration: float, available_visuals: int) -> int:
    """Prefer one grounded visual every three to four seconds in review."""

    return int(
        reference_profile.review_visual_density_contract(
            total_duration, available_visuals
        )["target_visuals"]
    )


def _review_effective_section_capacity(
    section_duration_s: float,
    roi_capacities: Sequence[int],
) -> int:
    """Prefer unique panels; use extra ROI capacity only when cadence requires it."""

    capacities = [max(0, int(value)) for value in roi_capacities]
    unique_capacity = sum(1 for value in capacities if value > 0)
    minimum_required = (
        max(1, math.ceil(float(section_duration_s) / reference_profile.REVIEW_MAX_SHOT_SECONDS - 1e-9))
        if section_duration_s > 0.0
        else 0
    )
    if unique_capacity >= minimum_required:
        return unique_capacity
    return sum(capacities)


def _review_transition_family(rank: int) -> str:
    """Use one soft fade family for every silent-review boundary."""

    del rank
    return "fade"


def _review_role(section: str, beat: str) -> str:
    section_key = str(section or "").strip().lower()
    beat_key = str(beat or "").strip().lower()
    if any(token in beat_key for token in ("detail", "insert", "object", "weapon", "hands")):
        return "detail"
    if any(token in beat_key for token in ("reaction", "surprise", "thinking")):
        return "reaction"
    if any(token in beat_key for token in ("attack", "impact", "action", "explosion")):
        return "action"
    if section_key == "setup":
        return "setup"
    if section_key in {"conflict", "hook"}:
        return "action"
    if section_key in {"twist", "cta", "payoff"}:
        return "reaction"
    return "neutral"


def _review_candidate_visual_fit_score(
    candidate: object,
    section: str = "",
    beat: str = "",
) -> float:
    """Score how readable a grounded panel is for the current editorial beat."""

    panel_candidate = getattr(candidate, "panel_candidate", None)
    features = getattr(panel_candidate, "features", None)
    if features is None:
        return 0.0
    role = _review_role(section, beat)
    face = float(getattr(features, "face_visibility", 0.0) or 0.0)
    expression = float(getattr(features, "facial_expression", 0.0) or 0.0)
    action = float(getattr(features, "action_pose", 0.0) or 0.0)
    impact = float(getattr(features, "impact_frame", 0.0) or 0.0)
    composition = float(getattr(features, "dramatic_composition", 0.0) or 0.0)
    weapons = float(getattr(features, "weapons", 0.0) or 0.0)
    effects = float(getattr(features, "visual_effects", 0.0) or 0.0)
    close_up = float(getattr(features, "close_up", 0.0) or 0.0)
    scenery = float(getattr(features, "scenery_only", 0.0) or 0.0)
    if role == "setup":
        score = 2.7 * face + 1.8 * composition + 0.8 * action - 0.8 * close_up
    elif role == "reaction":
        score = 3.0 * face + 2.2 * expression + 1.3 * composition + 0.4 * action
    elif role == "detail":
        score = 2.0 * weapons + 1.6 * effects + 1.2 * action + 0.8 * composition
    elif role == "action":
        score = 2.5 * action + 2.0 * impact + 1.4 * composition + 0.8 * face + 0.8 * effects
    else:
        score = 1.4 * face + 1.4 * action + 1.2 * composition + 0.6 * expression
    return round(score - 1.5 * scenery, 6)


def _review_candidate_priority_key(
    candidate: object,
    usage_counts: Mapping[str, int],
    section: str = "",
    beat: str = "",
) -> tuple[object, ...]:
    """Prefer unused, beat-readable grounded panels with deterministic ties."""

    panel_id = str(getattr(candidate, "panel_id", ""))
    return (
        1 if usage_counts.get(panel_id, 0) > 0 else 0,
        -_review_candidate_visual_fit_score(candidate, section, beat),
        *_review_candidate_order_key(candidate),
    )


def _normalized_region_box(region: object) -> tuple[float, float, float, float] | None:
    bbox = getattr(region, "normalized_bbox", None)
    if isinstance(bbox, (tuple, list)) and len(bbox) == 4:
        try:
            values = tuple(float(value) for value in bbox)
        except (TypeError, ValueError):
            values = ()
        if len(values) == 4 and values[2] > values[0] and values[3] > values[1]:
            return values  # type: ignore[return-value]
    polygon = tuple(getattr(region, "normalized_polygon", ()) or ())
    if polygon:
        try:
            xs = [float(point[0]) for point in polygon]
            ys = [float(point[1]) for point in polygon]
        except (IndexError, TypeError, ValueError):
            return None
        if xs and ys:
            return min(xs), min(ys), max(xs), max(ys)
    return None


def _normalized_crop_box(
    crop_box: Sequence[int], panel_size: tuple[int, int]
) -> tuple[float, float, float, float]:
    width, height = panel_size
    return (
        float(crop_box[0]) / width,
        float(crop_box[1]) / height,
        float(crop_box[2]) / width,
        float(crop_box[3]) / height,
    )


def _box_intersection_fraction(
    box: tuple[float, float, float, float],
    crop: tuple[float, float, float, float],
) -> float:
    left = max(box[0], crop[0])
    top = max(box[1], crop[1])
    right = min(box[2], crop[2])
    bottom = min(box[3], crop[3])
    overlap = max(0.0, right - left) * max(0.0, bottom - top)
    area = max(1e-9, (box[2] - box[0]) * (box[3] - box[1]))
    return min(1.0, overlap / area)


def _review_face_boxes(candidate: ReferencePanelFallbackCandidate) -> tuple[tuple[float, float, float, float], ...]:
    boxes: list[tuple[float, float, float, float]] = []
    evidence = getattr(candidate, "visual_evidence", None)
    for region in tuple(getattr(evidence, "protected_regions", ()) or ()):
        if str(getattr(region, "kind", "")) != "face":
            continue
        box = _normalized_region_box(region)
        if box is not None:
            boxes.append(box)
    panel_candidate = getattr(candidate, "panel_candidate", None)
    features = getattr(panel_candidate, "features", None)
    # Provider geometry is authoritative. Local Haar boxes are only a fallback
    # for cached/legacy observations that do not expose a face region.
    if not boxes:
        boxes.extend(tuple(getattr(features, "face_boxes", ()) or ()))
    if not boxes:
        # Older/cached candidates may only have face centres. A conservative
        # proxy still lets ranking protect the head from an edge crop.
        for x, y in tuple(getattr(features, "face_points", ()) or ()):
            boxes.append(
                (
                    max(0.0, float(x) - 0.09),
                    max(0.0, float(y) - 0.075),
                    min(1.0, float(x) + 0.09),
                    min(1.0, float(y) + 0.075),
                )
            )
    deduped = {
        tuple(round(float(value), 5) for value in box)
        for box in boxes
        if len(box) == 4 and box[2] > box[0] and box[3] > box[1]
    }
    return tuple(sorted(deduped))


def _review_subject_boxes(candidate: ReferencePanelFallbackCandidate) -> tuple[tuple[float, float, float, float], ...]:
    boxes = []
    evidence = getattr(candidate, "visual_evidence", None)
    for region in tuple(getattr(evidence, "protected_regions", ()) or ()):
        if str(getattr(region, "kind", "")) != "subject":
            continue
        box = _normalized_region_box(region)
        if box is not None:
            boxes.append(box)
    return tuple(boxes)


def _review_crop_editorial_metrics(
    candidate: ReferencePanelFallbackCandidate,
    roi: ReferenceROIAlternative,
    telemetry: object,
    *,
    section: str,
    beat: str,
) -> dict[str, object]:
    """Measure human-facing composition quality beyond raw framing feasibility."""

    crop = _normalized_crop_box(roi.crop_box, candidate.panel_size)
    crop_width = max(1e-9, crop[2] - crop[0])
    crop_height = max(1e-9, crop[3] - crop[1])
    crop_area = crop_width * crop_height
    faces = _review_face_boxes(candidate)
    face_cutoff_count = 0
    face_margin_violation_count = 0
    visible_face_count = 0
    minimum_face_coverage = 1.0
    minimum_face_margin = 1.0
    for face in faces:
        coverage = _box_intersection_fraction(face, crop)
        if coverage <= 0.03:
            continue
        minimum_face_coverage = min(minimum_face_coverage, coverage)
        if coverage >= 0.50:
            visible_face_count += 1
        if coverage < reference_profile.REVIEW_FACE_MIN_VISIBLE_FRACTION:
            face_cutoff_count += 1
            continue
        face_width = max(1e-9, face[2] - face[0])
        face_height = max(1e-9, face[3] - face[1])
        margin = min(
            (face[0] - crop[0]) / face_width,
            (crop[2] - face[2]) / face_width,
            (face[1] - crop[1]) / face_height,
            (crop[3] - face[3]) / face_height,
        )
        minimum_face_margin = min(minimum_face_margin, max(0.0, margin))
        if margin < reference_profile.REVIEW_FACE_MIN_MARGIN_RATIO:
            face_margin_violation_count += 1

    subjects = _review_subject_boxes(candidate)
    if subjects:
        subject_completeness = max(_box_intersection_fraction(box, crop) for box in subjects)
    else:
        subject_completeness = float(
            telemetry.get("subject_coverage", 1.0)
            if isinstance(telemetry, Mapping)
            else getattr(telemetry, "subject_coverage", 1.0)
        )
    base_zoom = float(
        telemetry.get("base_zoom", 1.0)
        if isinstance(telemetry, Mapping)
        else getattr(telemetry, "base_zoom", 1.0)
    )
    role = _review_role(section, beat)
    detail_allowed = role == "detail"
    crop_center_x = (crop[0] + crop[2]) / 2.0
    crop_center_y = (crop[1] + crop[3]) / 2.0
    semantic_focus_label = ""
    semantic_focus_distance = 1.0
    panel_candidate = getattr(candidate, "panel_candidate", None)
    try:
        semantic_rois = roi_detection.rank_rois(panel_candidate)
    except (AttributeError, TypeError, ValueError):
        semantic_rois = ()
    for semantic_roi in semantic_rois:
        distance = math.hypot(
            float(semantic_roi.x) - crop_center_x,
            float(semantic_roi.y) - crop_center_y,
        )
        if distance < semantic_focus_distance:
            semantic_focus_distance = distance
            semantic_focus_label = str(semantic_roi.label)
    semantic_detail = any(
        token in str(roi.roi_label).lower()
        for token in ("hand", "detail", "insert")
    ) or (
        semantic_focus_distance <= 0.18
        and semantic_focus_label in {"hands", "detail"}
        and crop_area <= 0.35
    )
    production_scale_geometry = (
        candidate.panel_size[0] >= 720 and candidate.panel_size[1] >= 1280
    )
    extreme_crop = (
        production_scale_geometry
        and crop_area < reference_profile.REVIEW_DETAIL_CROP_MAX_AREA_FRACTION
        and base_zoom >= reference_profile.REVIEW_EXTREME_CROP_ZOOM
    )
    face_omission = bool(faces) and visible_face_count == 0 and not detail_allowed
    unjustified_detail = bool(
        not detail_allowed
        and (semantic_detail or (extreme_crop and visible_face_count == 0))
    )
    anomaly_flags: list[str] = []
    if face_cutoff_count:
        anomaly_flags.append("face_cutoff")
    if face_margin_violation_count:
        anomaly_flags.append("face_edge_crowding")
    if face_omission:
        anomaly_flags.append("face_omitted_by_crop")
    if unjustified_detail:
        anomaly_flags.append("unjustified_detail_crop")
    if subjects and subject_completeness < reference_profile.REVIEW_SUBJECT_MIN_COMPLETENESS:
        anomaly_flags.append("subject_incomplete")
    return {
        "version": "review-editorial-crop-quality-v1",
        "role": role,
        "crop_area_fraction": round(crop_area, 6),
        "face_region_count": len(faces),
        "visible_face_count": visible_face_count,
        "face_cutoff_count": face_cutoff_count,
        "face_margin_violation_count": face_margin_violation_count,
        "minimum_visible_face_fraction": round(minimum_face_coverage, 6),
        "minimum_face_margin_ratio": round(minimum_face_margin, 6),
        "face_omission": face_omission,
        "subject_region_count": len(subjects),
        "subject_completeness_score": round(subject_completeness, 6),
        "semantic_detail_roi": semantic_detail,
        "semantic_focus_label": semantic_focus_label,
        "semantic_focus_distance": round(semantic_focus_distance, 6),
        "extreme_crop": extreme_crop,
        "unjustified_detail_crop": unjustified_detail,
        "anomaly_flags": anomaly_flags,
    }


def _review_editorial_rejection_code(metrics: Mapping[str, object]) -> str | None:
    if int(metrics.get("face_cutoff_count", 0) or 0) > 0:
        return "visual.face_cutoff"
    if int(metrics.get("face_margin_violation_count", 0) or 0) > 0:
        return "visual.face_edge_crowding"
    if bool(metrics.get("face_omission", False)):
        return "visual.face_omitted"
    if bool(metrics.get("unjustified_detail_crop", False)):
        return "visual.unjustified_detail_crop"
    if (
        str(metrics.get("role", "")) in {"setup", "reaction"}
        and int(metrics.get("subject_region_count", 0) or 0) > 0
        and float(metrics.get("subject_completeness_score", 1.0))
        < reference_profile.REVIEW_SUBJECT_MIN_COMPLETENESS
    ):
        return "visual.subject_incomplete"
    return None


def _review_editorial_crop_quality_key(
    metrics: Mapping[str, object],
    *,
    blank_fraction: float,
    base_zoom: float,
    protected_retained_fraction: float,
    preferred_blank_fraction: float,
) -> tuple[object, ...]:
    subject_score = float(metrics.get("subject_completeness_score", 1.0))
    return (
        int(metrics.get("face_cutoff_count", 0) or 0),
        1 if bool(metrics.get("face_omission", False)) else 0,
        int(metrics.get("face_margin_violation_count", 0) or 0),
        1 if bool(metrics.get("unjustified_detail_crop", False)) else 0,
        round(1.0 - max(0.0, min(1.0, subject_score)), 6),
        *reference_profile.review_framing_quality_key(
            blank_fraction,
            base_zoom,
            protected_retained_fraction,
            preferred_blank_fraction=preferred_blank_fraction,
        ),
    )


def _ordered_review_roi_alternatives(
    alternatives: Sequence[ReferenceROIAlternative],
) -> tuple[ReferenceROIAlternative, ...]:
    """Try measured low-blank ROIs first for the review-only path."""

    return tuple(
        sorted(
            alternatives,
            key=lambda roi: (
                roi.edge_blank_fraction is None,
                float(roi.edge_blank_fraction)
                if roi.edge_blank_fraction is not None
                else 1.0,
                _REFERENCE_ROI_KIND_ORDER[roi.kind],
                roi.roi_label,
                roi.crop_box,
                roi.focus,
            ),
        )
    )


def _review_transition_schedule(
    shots: Sequence[Mapping[str, object]],
) -> dict[int, str]:
    """Animate every review boundary with a short deterministic transition."""

    return {
        index: _review_transition_family(index - 1)
        for index in range(1, len(shots))
    }


def _review_candidate_order_key(candidate: object) -> tuple[object, ...]:
    """Keep review selection in immutable source chronology."""

    return (
        int(getattr(candidate, "source_order", 0)),
        str(
            getattr(
                getattr(candidate, "panel_candidate", None),
                "source_family",
                "",
            )
            or ""
        ),
        str(getattr(candidate, "panel_id", "")),
        str(getattr(candidate, "panel_region_id", "")),
    )


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
    counts = (
        [
            max(1, math.ceil(duration / reference_profile.REVIEW_MAX_SHOT_SECONDS - 1e-9))
            for duration in durations
        ]
        if max_counts_by_section is not None
        else [1] * len(groups)
    )
    caps = [
        max(
            1,
            int((max_counts_by_section or {}).get(str(group[0].section), target_shots)),
        )
        for group in groups
    ]
    if any(count > cap for count, cap in zip(counts, caps, strict=True)):
        raise ReferencePlanningError(
            "review cadence section capacity cannot satisfy the four-second ceiling",
            "visual.capacity_insufficient",
        )
    if sum(caps) < target_shots:
        raise ReferencePlanningError(
            "review cadence capacity is below the requested section coverage"
        )
    if sum(counts) > target_shots:
        raise ReferencePlanningError(
            "review cadence target is below the per-section four-second minimum",
            "visual.capacity_insufficient",
        )
    remaining = max(0, target_shots - sum(counts))
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
    if allow_review_cadence_adaptation or emphasis_count <= 0:
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
    if not allow_review_cadence_adaptation and values:
        # The mixed emphasis/normal values must sum back to the exact section
        # duration: unabsorbed drift accumulates across sections and drives
        # the clamped final shot of the chapter to a negative duration.
        values = list(values)
        values[-1] = values[-1] + (section_duration - sum(values))
    if not allow_review_cadence_adaptation and any(
        not (
            profile.hold_min_s <= round(value, 3) <= profile.hold_max_s
            or profile.emphasis_min_s <= round(value, 3) <= profile.emphasis_max_s
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


def _review_focus_span(anchor: float, travel: float, *, reverse: bool) -> tuple[float, float]:
    """Return a bounded focus sweep centered as closely as possible on ``anchor``."""

    center = max(0.05, min(0.95, float(anchor)))
    travel = max(0.0, min(0.40, float(travel)))
    low = max(0.05, min(center - travel / 2.0, 0.95 - travel))
    high = min(0.95, low + travel)
    return (high, low) if reverse else (low, high)


def _enforce_review_zoom_motion(shots: list[dict]) -> None:
    """Apply visible, monotonic living-frame motion to every reference shot.

    The stabilized renderer uses floating-point affine sampling, so the old
    fixed-focus-only workaround is no longer necessary.  Keep motion smooth and
    deterministic, but require enough zoom/focus travel to read as intentional
    animation at normal playback speed.
    """

    pattern = (
        ("slow_push", "slow_push_in"),
        ("guided_pan", "pan_horizontal"),
        ("slow_pull", "slow_pull_out"),
        ("focus_shift", "focus_shift"),
    )
    for index, shot in enumerate(shots):
        anchor_x = float(shot.get("focus_x", 0.5))
        anchor_y = float(shot.get("focus_y", 0.5))
        mode, curve = pattern[index % len(pattern)]
        reverse = bool((index // len(pattern)) % 2)

        if mode in {"slow_push", "slow_pull"}:
            start_x = end_x = anchor_x
            start_y = end_y = anchor_y
        elif mode == "guided_pan":
            start_x, end_x = _review_focus_span(anchor_x, reference_profile.REVIEW_MOTION_PAN_FOCUS_TRAVEL, reverse=reverse)
            start_y = end_y = anchor_y
        else:
            start_x, end_x = _review_focus_span(anchor_x, reference_profile.REVIEW_MOTION_DIAGONAL_FOCUS_TRAVEL, reverse=reverse)
            # Counter-phase vertical travel reads as a deliberate diagonal
            # focus shift without the oscillation that previously caused jitter.
            start_y, end_y = _review_focus_span(anchor_y, reference_profile.REVIEW_MOTION_DIAGONAL_FOCUS_TRAVEL, reverse=not reverse)

        shot["focus_x"] = start_x
        shot["focus_y"] = start_y
        shot["focus_end_x"] = end_x
        shot["focus_end_y"] = end_y
        shot["motion_mode"] = mode
        shot["motion_intensity"] = "medium" if mode in {"guided_pan", "focus_shift"} else "low"
        shot["camera_curve"] = curve
        reason = str(shot.get("motion_reason", "") or "").strip()
        suffix = "review:perceptible_subpixel_living_frame_v4"
        shot["motion_reason"] = f"{reason}; {suffix}" if reason else suffix


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
    edge_blank_fraction: float | None = None

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
        if self.edge_blank_fraction is not None and not (
            isinstance(self.edge_blank_fraction, (int, float))
            and not isinstance(self.edge_blank_fraction, bool)
            and math.isfinite(float(self.edge_blank_fraction))
            and 0.0 <= float(self.edge_blank_fraction) <= 1.0
        ):
            raise ReferencePlanningError(
                "ROI edge blank fraction is invalid",
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


def _is_title_page_family(
    source_family: str,
    *,
    source_order: int | None = None,
) -> bool:
    return is_title_page_family(source_family, source_order=source_order)


def is_title_page_family(
    source_family: str,
    *,
    source_order: int | None = None,
) -> bool:
    """Return whether a source family is reserved for title/front matter."""

    # The immutable source order is authoritative for the current ingest layout:
    # order 0 is front matter/title, while later ``page__001`` families can be
    # ordinary first panels of a later source page and must remain eligible.
    parts = [part for part in str(source_family or "").split("__") if part]
    if source_order == 0:
        return True
    if source_order is not None and source_order > 0:
        return len(parts) == 3 and parts[1] == "002" and parts[2] == "001"
    # Preserve the family-only fallback for callers that do not have the
    # immutable source order available yet.
    if len(parts) == 2 and parts[-1] == "001":
        return True
    # A three-segment panel at page 2 panel 1 ("204__002__001") is a chapter
    # title splash in the pre-sliced source layout; exclude it too.
    return len(parts) == 3 and parts[1] == "002" and parts[2] == "001"


def _candidate_is_eligible(
    candidate: ReferencePanelFallbackCandidate,
    section: str,
    beat: str,
) -> bool:
    if _is_title_page_family(
        str(getattr(getattr(candidate, "panel_candidate", None), "source_family", "") or ""),
        source_order=getattr(candidate, "source_order", None),
    ):
        return False
    # An exact candidate without any explicit section/beat provenance is not
    # eligible for every section.  Treating empty eligibility as a wildcard
    # silently reintroduces the asset-level fallback that Task 7 forbids.
    if not candidate.eligible_sections and not candidate.eligible_beats:
        return False
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


def _review_framing_candidate_is_feasible(
    crop_box: tuple[int, int, int, int],
    ready: object,
    border_mask: object,
    panel_size: tuple[int, int],
    target_size: tuple[int, int],
    *,
    review_aggressive_crop: bool,
    standard_blank_target: float,
    allow_conservative_full_panel: bool,
    **feasibility_kwargs: object,
) -> tuple[bool, object]:
    """Apply the 8% review gate, then a tightly bounded wide-crop rescue.

    The rescue exists to avoid forcing an extreme detail crop merely to erase
    modest edge whitespace. Balloon and protected-region constraints are still
    evaluated by the authoritative framing boundary on the second pass.
    """

    feasible, telemetry = framing_analysis.candidate_is_feasible(
        crop_box, ready, border_mask, panel_size, target_size,
        blank_target_fraction=standard_blank_target,
        allow_conservative_full_panel=allow_conservative_full_panel,
        review_aggressive_crop=review_aggressive_crop,
        **feasibility_kwargs,
    )
    rejection = getattr(telemetry, "rejection_code", None)
    if (
        feasible
        or not review_aggressive_crop
        or rejection != "visual.blank_infeasible"
    ):
        return feasible, telemetry
    rescued, rescue_telemetry = framing_analysis.candidate_is_feasible(
        crop_box, ready, border_mask, panel_size, target_size,
        blank_target_fraction=reference_profile.REVIEW_COHERENCE_RESCUE_MAX_FRAME_EDGE_BLANK_FRACTION,
        allow_conservative_full_panel=allow_conservative_full_panel,
        review_aggressive_crop=review_aggressive_crop,
        **feasibility_kwargs,
    )
    if (
        rescued
        and float(getattr(rescue_telemetry, "base_zoom", 999.0))
        <= reference_profile.REVIEW_COHERENCE_RESCUE_MAX_BASE_ZOOM + 1e-9
    ):
        return True, replace(
            rescue_telemetry,
            fallback_reason=reference_profile.REVIEW_COHERENCE_RESCUE_REASON,
        )
    return feasible, telemetry


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
    review_aggressive_crop: bool = False,
    allow_conservative_full_panel: bool = False,
) -> tuple[bool, object, dict]:
    evidence = candidate.visual_evidence
    entry_kind = phase_kind
    try:
        ready = visual_scoring.require_reference_ready_visual_evidence(
            evidence,
            allow_conservative_full_panel=allow_conservative_full_panel,
        )
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
        feasible, telemetry = _review_framing_candidate_is_feasible(
            roi.crop_box,
            ready,
            candidate.border_mask,
            candidate.panel_size,
            (profile.final_width, profile.final_height),
            review_aggressive_crop=review_aggressive_crop,
            standard_blank_target=(
                reference_profile.REVIEW_MAX_FRAME_EDGE_BLANK_FRACTION
                if review_aggressive_crop
                else profile.framing_blank_target_fraction
            ),
            allow_conservative_full_panel=allow_conservative_full_panel,
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
    pixel_edge_blank_fraction = (
        (
            getattr(telemetry, "edge_connected_blank_fraction", None)
            if not isinstance(telemetry, Mapping)
            else telemetry.get("edge_connected_blank_fraction")
        )
        if review_aggressive_crop
        else roi.edge_blank_fraction
    )
    telemetry_fallback_reason = (
        getattr(telemetry, "fallback_reason", None)
        if not isinstance(telemetry, Mapping)
        else telemetry.get("fallback_reason")
    )
    allowed_pixel_blank_fraction = (
        reference_profile.REVIEW_COHERENCE_RESCUE_MAX_FRAME_EDGE_BLANK_FRACTION
        if review_aggressive_crop
        and telemetry_fallback_reason == reference_profile.REVIEW_COHERENCE_RESCUE_REASON
        else reference_profile.REVIEW_MAX_FRAME_EDGE_BLANK_FRACTION
    )
    if (
        accepted
        and pixel_edge_blank_fraction is not None
        and float(pixel_edge_blank_fraction) > allowed_pixel_blank_fraction
    ):
        telemetry = _telemetry_json(telemetry)
        telemetry.update(
            {
                "pixel_edge_blank_fraction": float(pixel_edge_blank_fraction),
                "rejection_code": "visual.blank_infeasible",
            }
        )
        accepted = False
        rejection = "visual.blank_infeasible"
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
    review_aggressive_crop: bool = False,
    allow_conservative_full_panel: bool = False,
    section: str = "",
    beat: str = "",
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
        candidate.visual_evidence,
        allow_conservative_full_panel=allow_conservative_full_panel,
    )
    feasible = 0
    for roi in _ordered_roi_alternatives(candidate):
        accepted, _telemetry = _review_framing_candidate_is_feasible(
            roi.crop_box,
            ready,
            candidate.border_mask,
            candidate.panel_size,
            (profile.final_width, profile.final_height),
            review_aggressive_crop=review_aggressive_crop,
            standard_blank_target=(
                reference_profile.REVIEW_MAX_FRAME_EDGE_BLANK_FRACTION
                if review_aggressive_crop
                else profile.framing_blank_target_fraction
            ),
            allow_conservative_full_panel=allow_conservative_full_panel,
            **feasibility_kwargs,
        )
        if accepted:
            if review_aggressive_crop:
                metrics = _review_crop_editorial_metrics(
                    candidate,
                    roi,
                    _telemetry,
                    section=section,
                    beat=beat,
                )
                if _review_editorial_rejection_code(metrics) is not None:
                    continue
            feasible += 1
    return min(feasible, profile.max_canonical_panel_uses)


def _plan_reference_panel_candidates(
    spans: list[object],
    profile: object,
    panel_candidates: Sequence[ReferencePanelFallbackCandidate],
    *,
    allow_source_resolution_warning: bool = False,
    allow_review_cadence_adaptation: bool = False,
    allow_standard_cadence_adaptation: bool = False,
    allow_review_duration: bool = False,
    review_duration_bounds_s: tuple[float, float] | None = None,
    allow_conservative_full_panel: bool = False,
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
                candidate.visual_evidence,
                allow_conservative_full_panel=allow_conservative_full_panel,
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
    cadence_adapted = bool(
        allow_review_cadence_adaptation or allow_standard_cadence_adaptation
    )
    section_names = tuple(
        dict.fromkeys(str(span.section) for span in spans if str(span.section))
    )
    section_capacity: dict[str, int] = {}
    review_capacity_by_panel: dict[str, int] = {}
    for section in section_names:
        eligible_for_section = [
            candidate
            for candidate in ordered
            if _candidate_is_eligible(candidate, section, "")
            and (not candidate.eligible_sections or section in candidate.eligible_sections)
        ]
        if not cadence_adapted:
            # The normal exact-panel path evaluates each ROI exactly once while
            # emitting the ordered fallback ledger below.  A speculative
            # feasibility pass here both duplicates expensive work and can
            # consume stateful test/provider seams before the ledger exists.
            section_capacity[section] = max(
                0,
                len({candidate.panel_id for candidate in eligible_for_section})
                * max(1, int(profile.max_canonical_panel_uses)),
            )
            continue
        capacities = [
            _feasible_roi_capacity(
                candidate,
                profile,
                allow_source_resolution_warning=allow_source_resolution_warning,
                review_aggressive_crop=cadence_adapted,
                allow_conservative_full_panel=allow_conservative_full_panel,
                section=section,
            )
            for candidate in eligible_for_section
        ]
        if allow_standard_cadence_adaptation:
            # Final production prefers one distinct evidence panel per shot.  A
            # panel may expose multiple safe ROIs, but counting those as extra
            # standard-production capacity can make the greedy source-order
            # planner reuse a panel early and strand later shots in the same
            # section.  Review/adaptive mode keeps multi-ROI fallback capacity.
            capacities = [min(1, capacity) for capacity in capacities]
        for candidate, capacity in zip(eligible_for_section, capacities, strict=True):
            review_capacity_by_panel[candidate.panel_id] = capacity
        section_spans = [span for span in spans if str(span.section) == section]
        section_duration = (
            max(float(span.end_time) for span in section_spans)
            - min(float(span.start_time) for span in section_spans)
            if section_spans
            else 0.0
        )
        # Distinct safe ROIs remain available as an emergency cadence fallback,
        # but they must not inflate the preferred shot target when unique panels
        # already satisfy the four-second ceiling. This prevents avoidable panel
        # repetition while preserving a safe fallback for genuinely sparse evidence.
        section_capacity[section] = _review_effective_section_capacity(
            section_duration, capacities
        )
    if cadence_adapted and any(
        capacity < 1 for capacity in section_capacity.values()
    ):
        raise ReferencePlanningError(
            "no feasible exact panel candidate covers every story section",
            "visual.visual_unavailable",
        )
    cadence_capacity_override = None
    if cadence_adapted:
        total_duration = (
            max(float(span.end_time) for span in spans)
            - min(float(span.start_time) for span in spans)
            if spans
            else 0.0
        )
        cadence_capacity_override = _review_effective_section_capacity(
            total_duration,
            [review_capacity_by_panel.get(candidate.panel_id, 0) for candidate in ordered],
        )
    base_shots = _plan_reference(
        spans,
        timing_candidates,
        profile,
        None,
        None,
        max_shots_by_section=(section_capacity if cadence_adapted else None),
        cadence_capacity_override=cadence_capacity_override,
        allow_review_cadence_adaptation=cadence_adapted,
        allow_review_duration=allow_review_duration,
        review_duration_bounds_s=review_duration_bounds_s,
    )
    uses: dict[str, int] = {}
    used_rois: dict[str, set[tuple[object, ...]]] = {}
    last_panel_id = ""
    recent_panel_ids: list[str] = []
    selected_shots: list[dict] = []
    section_shot_totals: dict[str, int] = {}
    for base_shot in base_shots:
        section_key = str(base_shot.get("section", ""))
        section_shot_totals[section_key] = section_shot_totals.get(section_key, 0) + 1
    section_shots_done: dict[str, int] = {}
    last_non_hook_source_order: int | None = None
    for shot_index, shot in enumerate(base_shots):
        section = str(shot.get("section", ""))
        beat = str(shot.get("camera_intent", "") or "")
        rotated = ordered[shot_index % len(ordered) :] + ordered[: shot_index % len(ordered)]
        panel_uses_cap = int(profile.max_canonical_panel_uses)
        eligible = [
            candidate
            for candidate in rotated
            if _candidate_is_eligible(candidate, section, beat)
            and uses.get(candidate.panel_id, 0)
            < (
                review_capacity_by_panel.get(candidate.panel_id, panel_uses_cap)
                if cadence_adapted
                else panel_uses_cap
            )
        ]
        # Apply the hard source-order boundary before soft anti-repeat
        # preferences. Otherwise a lower, non-recent panel can temporarily win
        # the reuse filter and then be removed by the monotonic-order gate,
        # stranding a later shot even though the current panel still has a safe
        # unused ROI available.
        if cadence_adapted and section != "hook" and last_non_hook_source_order is not None:
            eligible = [
                candidate
                for candidate in eligible
                if int(candidate.source_order) >= last_non_hook_source_order
            ]
        recent_set: set[str] = set()
        if not cadence_adapted:
            eligible = [
                candidate for candidate in eligible if candidate.panel_id != last_panel_id
            ]
        eligible = list(_prioritize_resolution_candidates(eligible))
        if cadence_adapted:
            eligible = [
                candidate
                for candidate in eligible
                if _feasible_roi_capacity(
                    candidate,
                    profile,
                    allow_source_resolution_warning=allow_source_resolution_warning,
                    review_aggressive_crop=cadence_adapted,
                    allow_conservative_full_panel=allow_conservative_full_panel,
                    section=section,
                    beat=beat,
                )
                > len(used_rois.get(candidate.panel_id, set()))
            ]
            remaining_in_section = max(
                1,
                section_shot_totals.get(section, 1)
                - section_shots_done.get(section, 0),
            )
            if remaining_in_section > 1 and eligible:
                # If the section can still finish with distinct panels, preserve
                # that path before considering extra ROI capacity. Otherwise a
                # visually strong late panel can be selected too early, forcing
                # an avoidable immediate repeat even though enough unique exact
                # evidence exists for every remaining shot.
                unique_viable: list[ReferencePanelFallbackCandidate] = []
                for candidate in eligible:
                    if uses.get(candidate.panel_id, 0) > 0:
                        continue
                    floor = int(candidate.source_order)
                    future_unique_ids = {
                        future.panel_id
                        for future in eligible
                        if uses.get(future.panel_id, 0) == 0
                        and int(future.source_order) >= floor
                    }
                    if len(future_unique_ids) >= remaining_in_section:
                        unique_viable.append(candidate)
                if unique_viable:
                    eligible = unique_viable
                else:
                    viable_with_future: list[ReferencePanelFallbackCandidate] = []
                    for candidate in eligible:
                        floor = int(candidate.source_order)
                        future_capacity = 0
                        for future in eligible:
                            if int(future.source_order) < floor:
                                continue
                            if allow_standard_cadence_adaptation:
                                remaining_capacity = (
                                    review_capacity_by_panel.get(future.panel_id, 0)
                                    - uses.get(future.panel_id, 0)
                                )
                            else:
                                remaining_capacity = _feasible_roi_capacity(
                                    future,
                                    profile,
                                    allow_source_resolution_warning=allow_source_resolution_warning,
                                    review_aggressive_crop=True,
                                    allow_conservative_full_panel=allow_conservative_full_panel,
                                    section=section,
                                    beat=beat,
                                ) - len(used_rois.get(future.panel_id, set()))
                            future_capacity += max(0, remaining_capacity)
                        if future_capacity >= remaining_in_section:
                            viable_with_future.append(candidate)
                    if viable_with_future:
                        eligible = viable_with_future
            # Reuse avoidance is editorial preference, not a hard feasibility
            # rule. Apply it only after the future-capacity guard has preserved
            # candidates that can still complete the remaining section shots.
            recent_set = set(
                recent_panel_ids[-reference_profile.REVIEW_PANEL_REUSE_WINDOW_SHOTS:]
            )
            not_recent = [
                candidate for candidate in eligible if candidate.panel_id not in recent_set
            ]
            if not_recent:
                eligible = not_recent
            else:
                non_reused = [
                    candidate for candidate in eligible if candidate.panel_id != last_panel_id
                ]
                eligible = non_reused or eligible
            eligible.sort(
                key=lambda candidate: _review_candidate_priority_key(
                    candidate, uses, section, beat
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
        accepted_editorial_metrics: Mapping[str, object] | None = None
        accepted_phase: str | None = None
        for candidate in eligible:
            previous_uses = uses.get(candidate.panel_id, 0)
            is_preferred = candidate.panel_id == preferred_candidate.panel_id
            roi_alternatives = _ordered_review_roi_alternatives(
                candidate.roi_alternatives
            ) if cadence_adapted else _ordered_roi_alternatives(
                candidate
            )
            if is_preferred:
                roi_plan = tuple(
                    (roi, roi.kind) for roi in roi_alternatives
                )
            else:
                roi_plan = tuple(
                    (roi, "alternate_panel")
                    for roi in roi_alternatives
                )
            accepted_attempts: list[
                tuple[tuple[object, ...], object, object, object, str, Mapping[str, object] | None]
            ] = []
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
                    review_aggressive_crop=cadence_adapted,
                    allow_conservative_full_panel=allow_conservative_full_panel,
                )
                attempts.append(entry)
                if accepted:
                    # Silent review may pick the least-blank accepted ROI so a
                    # full webtoon page crops to its dominant subject instead of
                    # the first (largest) feasible window. Evaluate every ROI
                    # before choosing; the regular path keeps first-wins.
                    if isinstance(telemetry, Mapping):
                        blank = float(telemetry.get("edge_connected_blank_fraction", 0.0))
                        base_zoom = float(telemetry.get("base_zoom", 999.0))
                        protected_retained = float(
                            telemetry.get("protected_retained_fraction", 0.0)
                        )
                    else:
                        blank = float(getattr(telemetry, "edge_connected_blank_fraction", 0.0))
                        base_zoom = float(getattr(telemetry, "base_zoom", 999.0))
                        protected_retained = float(
                            getattr(telemetry, "protected_retained_fraction", 0.0)
                        )
                    editorial_metrics: Mapping[str, object] | None = None
                    if cadence_adapted:
                        editorial_metrics = _review_crop_editorial_metrics(
                            candidate,
                            roi,
                            telemetry,
                            section=section,
                            beat=beat,
                        )
                        if isinstance(entry, dict):
                            entry["editorial_crop_quality"] = editorial_metrics
                        hard_editorial_code = _review_editorial_rejection_code(
                            editorial_metrics
                        )
                        if hard_editorial_code is not None:
                            if isinstance(entry, dict):
                                entry["accepted"] = False
                                entry["code"] = hard_editorial_code
                                entry["reason"] = "editorial crop rejected before review render"
                            continue
                        quality_key = _review_editorial_crop_quality_key(
                            editorial_metrics,
                            blank_fraction=blank,
                            base_zoom=base_zoom,
                            protected_retained_fraction=protected_retained,
                            preferred_blank_fraction=profile.framing_blank_target_fraction,
                        )
                    else:
                        quality_key = reference_profile.review_framing_quality_key(
                            blank,
                            base_zoom,
                            protected_retained,
                            preferred_blank_fraction=profile.framing_blank_target_fraction,
                        )
                    accepted_attempts.append(
                        (quality_key, roi, telemetry, entry, phase_kind, editorial_metrics)
                    )
                    if not cadence_adapted:
                        break
            if accepted_attempts:
                if cadence_adapted:
                    accepted_attempts.sort(key=lambda item: item[0])
                    _quality, roi, telemetry, entry, phase_kind, editorial_metrics = accepted_attempts[0]
                    chosen = entry
                    for _q2, _r2, _t2, other_entry, _p2, _m2 in accepted_attempts:
                        if other_entry is not chosen and isinstance(other_entry, dict):
                            other_entry["accepted"] = False
                else:
                    _quality, roi, telemetry, entry, phase_kind, editorial_metrics = accepted_attempts[0]
                accepted_candidate = candidate
                accepted_roi = roi
                accepted_telemetry = telemetry
                accepted_editorial_metrics = editorial_metrics
                accepted_phase = phase_kind
                break
            if accepted_candidate is not None:
                break
        if (
            accepted_candidate is None
            or accepted_roi is None
            or accepted_telemetry is None
            or (cadence_adapted and accepted_editorial_metrics is None)
        ):
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
            if cadence_adapted and candidate.panel_id in recent_set:
                reasons.append("reuse_purpose:grounded_capacity_exhausted")
            else:
                reasons.append(f"reuse_purpose:distinct_roi:{roi.roi_label}")
        if accepted_phase == "alternate_roi":
            reasons.append("fallback:alternate_roi")
        elif accepted_phase == "tighter_crop":
            reasons.append("fallback:tighter_crop")
        elif accepted_phase == "alternate_panel":
            reasons.append("fallback:alternate_panel_same_beat")
        telemetry_record = _telemetry_json(accepted_telemetry)
        if accepted_editorial_metrics is not None:
            telemetry_record["editorial_crop_quality"] = dict(accepted_editorial_metrics)
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
                    "pixel_edge_blank_fraction": (
                        telemetry_record.get("edge_connected_blank_fraction")
                        if cadence_adapted
                        else roi.edge_blank_fraction
                    ),
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
        if cadence_adapted:
            telemetry_record["available_visual_capacity"] = sum(
                section_capacity.values()
            )
        telemetry_record = _canonical_json_mapping(telemetry_record)
        accepted_index = next(
            (
                index
                for index, item in enumerate(attempts)
                if isinstance(item, dict) and item.get("accepted") is True
            ),
            len(attempts) - 1,
        )
        accepted_attempt_order = accepted_index
        attempts[accepted_attempt_order]["telemetry"] = telemetry_record
        shot.update(
            {
                "asset_id": candidate.source_asset_id,
                "source_family": str(
                    getattr(getattr(candidate, "panel_candidate", None), "source_family", "")
                    or ""
                ),
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
                    "pixel_edge_blank_fraction": (
                        telemetry_record.get("edge_connected_blank_fraction")
                        if cadence_adapted
                        else roi.edge_blank_fraction
                    ),
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
        recent_panel_ids.append(candidate.panel_id)
        last_panel_id = candidate.panel_id
        section_shots_done[section] = section_shots_done.get(section, 0) + 1
        if section != "hook":
            last_non_hook_source_order = int(candidate.source_order)
    # A pan/focus_shift curve with no focus travel renders as sub-pixel zoom
    # jitter. Degrade those shots to a pure zoom curve.
    for shot in selected_shots:
        curve = str(shot.get("camera_curve", ""))
        if curve in {"pan_horizontal", "focus_shift"}:
            fx = float(shot.get("focus_x", 0.0))
            fy = float(shot.get("focus_y", 0.0))
            ex = float(shot.get("focus_end_x", fx))
            ey = float(shot.get("focus_end_y", fy))
            if abs(ex - fx) < 1e-6 and abs(ey - fy) < 1e-6:
                shot["camera_curve"] = "slow_push_in"
                shot["motion_mode"] = "slow_push"
                shot["motion_reason"] = "static focus: pan/focus_shift downgraded to zoom"
    if cadence_adapted:
        _enforce_review_zoom_motion(selected_shots)
    if cadence_adapted and len(selected_shots) > 1:
        # The panel-candidate path reuses the base shot list but can replace
        # its panel/ROI identity during exact lineage binding. Reassert a
        # bounded visible transition after that selection so a base ``cut``
        # cannot silently erase review-edit intent.
        transition_boundaries = _review_transition_schedule(selected_shots)
        for index, shot in enumerate(selected_shots):
            shot["transition"] = (
                "none"
                if index == 0
                else transition_boundaries[index]
            )
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
    cadence_capacity_override: int | None = None,
    allow_review_duration: bool = False,
    review_duration_bounds_s: tuple[float, float] | None = None,
) -> list[dict]:
    if not spans or not candidates:
        raise ReferencePlanningError(
            f"{profile.profile_id} requires renderable panels and narration spans"
        )
    origin = min(float(span.start_time) for span in spans)
    end = max(float(span.end_time) for span in spans)
    total_duration = end - origin
    if allow_review_duration and review_duration_bounds_s is not None:
        try:
            duration_min_s = float(review_duration_bounds_s[0])
            duration_max_s = float(review_duration_bounds_s[1])
        except (IndexError, TypeError, ValueError):
            raise ReferencePlanningError(
                f"{profile.profile_id} received malformed review duration bounds"
            ) from None
        if duration_min_s <= 0.0 or duration_max_s < duration_min_s:
            raise ReferencePlanningError(
                f"{profile.profile_id} received malformed review duration bounds"
            )
    else:
        duration_min_s = (
            STANDARD_FINAL_DURATION_MIN_SECONDS
            if allow_review_duration
            else profile.duration_min_s
        )
        duration_max_s = (
            STANDARD_FINAL_DURATION_MAX_SECONDS
            if allow_review_duration
            else profile.duration_max_s
        )
    if not duration_min_s <= total_duration <= duration_max_s:
        raise ReferencePlanningError(
            f"{profile.profile_id} requires duration between "
            f"{duration_min_s:.1f} and {duration_max_s:.1f} seconds"
        )
    capacity = len(candidates) * profile.max_canonical_panel_uses
    if max_shots_by_section is not None:
        capacity = min(capacity, sum(max_shots_by_section.values()))
    if allow_review_cadence_adaptation and cadence_capacity_override is not None:
        capacity = min(capacity, max(0, int(cadence_capacity_override)))
    if allow_review_cadence_adaptation:
        density = reference_profile.review_visual_density_contract(total_duration, capacity)
        minimum_required = int(density["minimum_required_visuals"])
        if capacity < minimum_required:
            raise ReferencePlanningError(
                f"{profile.profile_id} has capacity for {capacity} review shots; "
                f"at least {minimum_required} are required by the four-second ceiling",
                "visual.capacity_insufficient",
            )
        nominal_target = _review_visual_shot_target(total_duration, capacity)
    else:
        nominal_target = max(
            profile.shot_min,
            min(
                profile.shot_max,
                round(total_duration / _REFERENCE_SHOT_INTERVAL_SECONDS),
            ),
        )
    target = nominal_target
    cadence_adapted = False
    if capacity < target:
        raise ReferencePlanningError(
            f"{profile.profile_id} cannot satisfy the panel reuse cap for {target} shots",
            "visual.capacity_insufficient" if allow_review_cadence_adaptation else None,
        )

    if capacity < target:
        raise ReferencePlanningError(
            f"{profile.profile_id} cannot satisfy the panel reuse cap for "
            f"{target} shots"
        )
    cadence_adapted = target != nominal_target or allow_review_cadence_adaptation

    beats = _coalesce_beats(
        director.analyze_story(spans),
        target_shots=target,
        max_counts_by_section=max_shots_by_section,
    )
    shots = visual_planning.plan_content_aware_scenes(
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

    if allow_review_cadence_adaptation and len(shots) > 1:
        review_transition_boundaries = _review_transition_schedule(shots)
    else:
        review_transition_boundaries = {}

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
            if shot_cursor + shot_index == len(shots) - 1:
                end_time = end
            else:
                # Store the exact in-band section duration: deriving the end
                # from an unrounded cursor can land a stored delta in the
                # rounding dead zone between the hold and emphasis bands.
                end_time = round(start + round(durations[shot_index], 3), 3)
            shot["start_time"] = start
            shot["end_time"] = end_time
            absolute_index = shot_cursor + shot_index
            shot["transition"] = (
                "none"
                if absolute_index == 0
                else (
                    review_transition_boundaries[absolute_index]
                    if allow_review_cadence_adaptation
                    else "cut"
                )
            )
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
            # Advance by the exact planned duration, not the rounded stored
            # end time: per-shot rounding accumulates and can push the final
            # clamped shot of the chapter to a negative duration.
            timeline_cursor += durations[shot_index]
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

    durations = [
        round(shot["end_time"] - shot["start_time"], 3) for shot in shots
    ]
    if cadence_adapted:
        if any(duration <= 0 for duration in durations):
            raise ReferencePlanningError(
                f"{profile.profile_id} produced a non-positive review shot duration"
            )
        if any(duration > reference_profile.REVIEW_MAX_SHOT_SECONDS + 1e-9 for duration in durations):
            raise ReferencePlanningError(
                f"{profile.profile_id} produced a review shot longer than "
                f"{reference_profile.REVIEW_MAX_SHOT_SECONDS:.1f} seconds",
                "visual.capacity_insufficient",
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
    allow_standard_cadence_adaptation: bool = False,
    allow_review_duration: bool = False,
    review_duration_bounds_s: tuple[float, float] | None = None,
    allow_conservative_full_panel: bool = False,
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
            allow_standard_cadence_adaptation=allow_standard_cadence_adaptation,
            allow_review_duration=allow_review_duration,
            review_duration_bounds_s=review_duration_bounds_s,
            allow_conservative_full_panel=allow_conservative_full_panel,
        )
    if profile is not None:
        return _plan_reference(
            span_list,
            candidates,
            profile,
            cited_asset_ids_by_section,
            citation_alignment_reasons_by_section,
            allow_review_cadence_adaptation=(
                allow_review_cadence_adaptation or allow_standard_cadence_adaptation
            ),
            cadence_capacity_override=(
                len(candidates) if allow_standard_cadence_adaptation else None
            ),
            allow_review_duration=allow_review_duration,
            review_duration_bounds_s=review_duration_bounds_s,
        )
    beats = _coalesce_beats(director.analyze_story(span_list))
    shots = visual_planning.plan_content_aware_scenes(beats, candidates)
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
        static_focus = (
            abs(float(shot.get("focus_end_x", shot.get("focus_x", 0.0))) - float(shot.get("focus_x", 0.0))) < 1e-6
            and abs(float(shot.get("focus_end_y", shot.get("focus_y", 0.0))) - float(shot.get("focus_y", 0.0))) < 1e-6
        )
        if static_focus and motion.mode in {"guided_pan", "focus_shift"}:
            # A pan/focus curve with no focus travel renders as a jittery
            # sub-pixel zoom. Degrade to a pure zoom curve.
            motion = motion_director.MotionPlan(
                mode="slow_push", intensity="low",
                reason="static focus: pan/focus_shift downgraded to zoom",
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
    "is_title_page_family",
    "plan",
]
