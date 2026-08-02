"""Editorial shot planning between ROI detection and camera execution.

Panel scoring answers *which* image is useful. The Shot Director decides ROI order,
shot length, cuts, anticipation, narration timing, and camera intent/curve. The
Camera Planner only validates and translates that approved curve for rendering.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from app.services import visual_scoring
from app.services.roi_detection import ROI, rank_rois
from app.services.visual_scoring import PanelCandidate


@dataclass(frozen=True)
class ShotPlan:
    """One directed shot, ready for timeline persistence."""

    order_index: int
    section: str
    start_time: float
    end_time: float
    asset_id: str | None
    roi_label: str
    focus_x: float
    focus_y: float
    focus_end_x: float
    focus_end_y: float
    effect: str
    camera_intent: str
    camera_curve: str
    narration_timing: str
    transition: str
    visual_score: float = 0.0
    semantic_score: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


# A cut before 1.5s feels nervous; a cut after 3s makes one panel feel held.
_MIN_SHOT = 1.25
_MAX_SHOT = 3.0
_ANTICIPATION = 0.28


def _camera_intent(narration: str) -> str:
    """Choose editorial intent; curve selection belongs to camera_planner."""
    tags = visual_scoring.narration_tags(narration)
    if "explosion" in tags:
        return "explosion"
    if "attack" in tags:
        return "attack"
    if "action" in tags:
        return "action"
    if "victory" in tags:
        return "victory"
    if "reveal" in tags:
        return "reveal"
    if "thinking" in tags:
        return "thinking"
    if "dialogue" in tags:
        return "dialogue"
    return "neutral"


def _narration_timing(narration: str) -> str:
    """Decide whether visuals lead, sync, or follow narration."""
    tags = visual_scoring.narration_tags(narration)
    if tags & {"action", "attack", "explosion", "reveal", "victory"}:
        return "visual_lead"
    if tags & {"dialogue", "thinking"}:
        return "sync"
    return "narration_lead"


_CURVES: dict[str, tuple[str, ...]] = {
    "dialogue": ("slow_push_in", "pan_horizontal", "focus_shift"),
    "thinking": ("pan_horizontal", "focus_shift", "pan_diagonal"),
    "reveal": ("push_in", "focus_shift", "slow_push_in", "pan_vertical"),
    "action": ("punch_zoom", "micro_shake", "pan_diagonal", "focus_shift"),
    "attack": ("punch_zoom", "micro_shake", "pan_diagonal"),
    "explosion": ("impact_shake", "micro_shake", "punch_zoom"),
    "victory": ("dramatic_zoom_out", "slow_push_in", "pan_vertical"),
    "approach": ("pan_horizontal", "slow_push_in", "focus_shift"),
    "suspense": ("slow_push_in", "pan_horizontal", "focus_shift"),
    "neutral": (
        "slow_push_in", "pan_horizontal", "pan_vertical", "pan_diagonal",
        "slow_pull_out", "focus_shift", "orbit",
    ),
}


def _camera_curve(intent: str, index: int, recent: list[str]) -> str:
    """Make the editorial curve decision, including diversity."""
    options = _CURVES[intent]
    for offset in range(len(options)):
        curve = options[(index + offset) % len(options)]
        if curve not in recent[-2:]:
            return curve
    return options[index % len(options)]


def _directed_curve(
    intent: str, index: int, recent: list[str],
    focus_x: float, focus_y: float, end_x: float, end_y: float,
    previous_vector: tuple[float, float] | None = None,
) -> str:
    """Select a curve that agrees with the approved ROI movement."""
    dx, dy = end_x - focus_x, end_y - focus_y
    directional: tuple[str, ...] = ()
    if max(abs(dx), abs(dy)) >= 0.12:
        if abs(dx) >= abs(dy) * 1.35:
            directional = ("pan_horizontal",)
        elif abs(dy) >= abs(dx) * 1.35:
            directional = ("pan_vertical",)
        else:
            directional = ("pan_diagonal",)
    if intent in {"action", "attack", "explosion", "victory", "reveal"} or directional:
        directional = directional + _CURVES[intent]
    else:
        directional = _CURVES[intent]
    vector = (dx, dy)
    reverses = (
        previous_vector is not None
        and max(abs(dx), abs(dy)) >= 0.12
        and previous_vector[0] * vector[0] + previous_vector[1] * vector[1] < -0.01
    )
    if reverses and intent not in {"action", "attack", "explosion", "reveal", "victory"}:
        directional = tuple(curve for curve in directional if not curve.startswith("pan_"))
    for curve in directional:
        if curve not in recent[-2:]:
            return curve
    return _camera_curve(intent, index, recent)


def _pacing_max(section: str, tags: frozenset[str], dense: bool, default: float) -> float:
    """Choose an editorial hold ceiling by beat, not one global split size."""
    if tags & {"action", "attack", "explosion"}:
        return 2.25 if dense else min(default, 2.5)
    if tags & {"thinking", "dialogue"} or section == "cta":
        return default
    if section == "hook":
        return min(default, 2.6)
    if tags & {"reveal", "victory"} or section == "twist":
        return min(default, 2.5)
    return min(default, 2.75)


def _slot_weights(slots: int, section: str, tags: frozenset[str]) -> list[float]:
    """Shape hold/release rhythm while preserving the span's exact duration."""
    weights = [1.0] * slots
    if slots < 2:
        return weights
    if tags & {"reveal", "victory"} or section == "twist":
        weights[0] = 1.15  # suspense hold before the reveal lands
        weights[-1] = 0.85
    elif tags & {"action", "attack", "explosion"}:
        weights[0] = 0.9
        weights[-1] = 0.82  # release after impact; do not linger
    elif tags & {"dialogue", "thinking"}:
        weights[-1] = 1.08
    return weights


def _continuity_order(
    rois: tuple[ROI, ...], x: float, y: float, transition: str = "fade",
) -> tuple[ROI, ...]:
    """Prefer nearby ROIs; fades enforce stronger compositional continuity."""
    penalty = 4.0 if transition == "fade" else 0.0
    return tuple(
        sorted(
            rois,
            key=lambda roi: roi.priority - penalty * math.hypot(roi.x - x, roi.y - y),
            reverse=True,
        )
    )


_EVENT_WORDS = {
    "action": {"attack", "attacked", "attacks", "strike", "struck", "hit", "serang", "menyerang", "memukul", "merampas", "menebas", "bertarung"},
    "reveal": {"reveal", "finally", "appears", "awakens", "muncul", "akhirnya", "ternyata", "datang", "hadir"},
    "explosion": {"explosion", "explode", "blast", "ledakan", "meledak"},
    "victory": {"victory", "wins", "won", "triumph", "menang", "kemenangan", "mengalahkan"},
}


def _event_times(span: object) -> list[float]:
    """Find all timed dramatic words inside a narration span."""
    tags = visual_scoring.narration_tags(span.text)
    words = getattr(span, "word_timings", []) or []
    events: list[float] = []
    for timing in words:
        token = re.sub(r"[^a-z]", "", str(timing.get("word", "")).lower())
        if any(token in _EVENT_WORDS[tag] for tag in tags if tag in _EVENT_WORDS):
            # AudioSpan timings are already on the master timeline.
            events.append(float(timing.get("start", 0.0)))
    return events


def _anticipate_events(
    scenes: list[ShotPlan], first: int, last: int,
    event_times: list[float], minimum: float, maximum: float, exact: bool = False,
) -> None:
    """Move internal cuts before multiple timed dramatic words."""
    if not event_times or last <= first:
        return
    for event_time in event_times:
        boundary = min(
            range(first, last),
            key=lambda index: abs(scenes[index].end_time - event_time),
        )
        left, right = scenes[boundary], scenes[boundary + 1]
        left_duration = left.end_time - left.start_time
        target = event_time if exact else event_time - min(0.18, left_duration * 0.18)
        lower = max(left.start_time + minimum, right.end_time - maximum)
        upper = min(right.end_time - minimum, left.start_time + maximum)
        cut = round(max(lower, min(target, upper)), 3)
        left_after = cut - left.start_time
        right_after = right.end_time - cut
        if lower <= cut <= upper and left_after <= maximum and right_after <= maximum:
            scenes[boundary] = ShotPlan(**{**left.as_dict(), "end_time": cut})
            scenes[boundary + 1] = ShotPlan(**{**right.as_dict(), "start_time": cut})


def _slots(duration: float, roi_count: int) -> int:
    if duration <= 0:
        return 0
    # Every meaningful ROI gets its own shot when the narration allows it.
    return max(1, min(6, max(math.ceil(duration / _MAX_SHOT), min(roi_count, 4))))


def _candidate_for(
    candidates: list[PanelCandidate], text: str, previous_order: int | None,
    used: set[str], signatures: set[str],
) -> PanelCandidate | None:
    return visual_scoring.select_panel(
        candidates, text, previous_order=previous_order, used_ids=used, used_signatures=signatures,
    )


def plan_shots(
    spans: list[object], candidates: list[PanelCandidate],
    min_scene_seconds: float = _MIN_SHOT, max_scene_seconds: float = _MAX_SHOT,
) -> list[ShotPlan]:
    """Direct a timeline with ROI cuts, motion diversity, and anticipation."""
    if not spans:
        return []
    # Keep public tuning knobs useful while retaining the anti-slideshow ceiling.
    max_seconds = max(1.5, min(float(max_scene_seconds), _MAX_SHOT))
    min_seconds = max(1.0, min(float(min_scene_seconds), max_seconds - 0.05))
    scenes: list[ShotPlan] = []
    boundaries: list[tuple[int, int, int]] = []
    recent_curves: list[str] = []
    used: set[str] = set()
    signatures: set[str] = set()
    previous_order: int | None = None
    previous_focus: tuple[float, float] | None = None
    previous_asset_id: str | None = None
    previous_vector: tuple[float, float] | None = None
    order = 0

    for span_index, span in enumerate(spans):
        span_list = spans
        next_start = span_list[span_index + 1].start_time if span_index + 1 < len(span_list) else span.end_time
        block_end = max(span.end_time, next_start)
        duration = max(0.0, block_end - span.start_time)
        previous_asset_id = scenes[-1].asset_id if scenes else None
        candidate = _candidate_for(candidates, span.text, previous_order, used, signatures)
        rois = rank_rois(candidate, span.text)
        if previous_focus and candidate and candidate.asset_id != previous_asset_id:
            rois = _continuity_order(rois, *previous_focus, "fade")
        roi_cursor = 0
        if candidate:
            used.add(candidate.asset_id)
            if candidate.features.visual_signature:
                signatures.add(candidate.features.visual_signature)
        tags = visual_scoring.narration_tags(span.text)
        timings = getattr(span, "word_timings", []) or []
        word_count = len(timings)
        active_duration = max(
            0.1,
            (
                float(timings[-1].get("end", 0.0))
                - float(timings[0].get("start", 0.0))
            )
            if timings
            else duration,
        )
        word_rate = word_count / active_duration
        # Dense action gets a faster editorial rhythm; reflective/dialogue beats
        # keep longer holds because the camera itself remains alive.
        dense_action = bool(tags & {"action", "attack", "explosion"}) and word_rate >= 1.8
        span_max = _pacing_max(span.section, tags, dense_action, max_seconds)
        event_times = _event_times(span)
        slot_cap = 10 if span_max < max_seconds else 6
        max_slots = max(1, int(duration // min_seconds))
        rhythm_slots = math.ceil(duration / span_max)
        if dense_action and rhythm_slots > 1:
            rhythm_slots += 1  # leave room for a release beat after impact
        slots = min(
            slot_cap,
            max_slots,
            max(
                rhythm_slots,
                min(len(rois), max_slots),
                min(len(event_times) + 2, max_slots),
            ),
        ) if duration else 0
        if not slots:
            continue
        weights = _slot_weights(slots, span.section, tags)
        weight_total = sum(weights)
        slot_durations = [duration * weight / weight_total for weight in weights]
        first_index = len(scenes)
        for slot in range(slots):
            # Stay on one panel until every meaningful ROI has been shown. Only
            # then ask panel selection for the next image.
            if roi_cursor >= len(rois):
                previous_order = candidate.order_index if candidate else previous_order
                previous_asset_id = candidate.asset_id if candidate else previous_asset_id
                candidate = _candidate_for(candidates, span.text, previous_order, used, signatures)
                rois = rank_rois(candidate, span.text)
                if previous_focus and candidate and candidate.asset_id != previous_asset_id:
                    rois = _continuity_order(rois, *previous_focus, "fade")
                roi_cursor = 0
                if candidate:
                    used.add(candidate.asset_id)
                    if candidate.features.visual_signature:
                        signatures.add(candidate.features.visual_signature)
            start = span.start_time + sum(slot_durations[:slot])
            end = start + slot_durations[slot]
            roi = rois[roi_cursor]
            next_roi = rois[(roi_cursor + 1) % len(rois)] if len(rois) > 1 else roi
            roi_cursor += 1
            # Director-owned intent/timing wins; legacy AudioSpan callers still
            # use the deterministic compatibility classifier.
            camera_intent = getattr(span, "camera_intent", None) or _camera_intent(span.text)
            camera_curve = _directed_curve(
                camera_intent, order, recent_curves,
                roi.x, roi.y, next_roi.x, next_roi.y,
                previous_vector,
            )
            recent_curves.append(camera_curve)
            scenes.append(
                ShotPlan(
                    order_index=order, section=span.section,
                    start_time=round(start, 3), end_time=round(end, 3),
                    asset_id=candidate.asset_id if candidate else None,
                    roi_label=roi.label,
                    focus_x=roi.x, focus_y=roi.y,
                    focus_end_x=next_roi.x, focus_end_y=next_roi.y,
                    effect=camera_curve, camera_intent=camera_intent,
                    camera_curve=camera_curve,
                    narration_timing=(
                        getattr(span, "visual_timing", None)
                        or _narration_timing(span.text)
                    ),
                    transition=(
                        "none" if order == 0
                        else ("cut" if candidate and scenes[-1].asset_id == candidate.asset_id else "fade")
                    ),
                    visual_score=candidate.visual_score if candidate else 0.0,
                    semantic_score=candidate.semantic_score if candidate else 0.0,
                )
            )
            previous_focus = (next_roi.x, next_roi.y)
            vector = (next_roi.x - roi.x, next_roi.y - roi.y)
            if max(abs(vector[0]), abs(vector[1])) >= 0.12:
                previous_vector = vector
            order += 1
        _anticipate_events(
            scenes, first_index, len(scenes) - 1, event_times, min_seconds, span_max,
            getattr(span, "impact_lock", False),
        )
        if candidate:
            previous_order = candidate.order_index
        if first_index > 0 and scenes:
            boundaries.append((first_index - 1, first_index, span_index))

    # Let a dramatic next beat arrive slightly early. No overlap: the boundary is
    # moved, preserving an exact continuous timeline for concat rendering.
    for left, right, boundary_span in boundaries:
        next_text = spans[boundary_span].text
        tags = visual_scoring.narration_tags(next_text)
        if tags & {"action", "attack", "reveal", "explosion", "victory"}:
            lead = min(_ANTICIPATION, (scenes[left].end_time - scenes[left].start_time) * 0.18)
            cut = round(scenes[right].start_time - lead, 3)
            if cut - scenes[left].start_time >= min_seconds:
                scenes = _replace_end(scenes, left, cut)
                scenes = _replace_start(scenes, right, cut)
    return scenes


def _replace_end(scenes: list[ShotPlan], index: int, end: float) -> list[ShotPlan]:
    item = scenes[index]
    scenes[index] = ShotPlan(**{**item.as_dict(), "end_time": end})
    return scenes


def _replace_start(scenes: list[ShotPlan], index: int, start: float) -> list[ShotPlan]:
    item = scenes[index]
    scenes[index] = ShotPlan(**{**item.as_dict(), "start_time": start})
    return scenes


__all__ = ["ROI", "ShotPlan", "plan_shots", "rank_rois"]

# ponytail: deterministic editorial ceiling; upgrade ROI labels with a local
# vision encoder without changing ShotPlan or the renderer contract.
