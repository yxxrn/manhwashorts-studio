"""Human-editor-style shot planning.

Panel scoring answers *which* image is useful. The Shot Director answers *how*
to stage it: ROI order, shot length, camera curve, diversity, and anticipation.
It deliberately consumes ``PanelCandidate`` rather than inspecting pixels, so
ROI/camera scheduling can evolve independently from panel detection.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.services import visual_scoring
from app.services.visual_scoring import PanelCandidate


@dataclass(frozen=True)
class ROI:
    """A ranked visual target inside one panel."""

    label: str
    x: float
    y: float
    priority: float


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
    camera_curve: str
    transition: str
    visual_score: float = 0.0
    semantic_score: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


# A cut before 1.5s feels nervous; a cut after 3s makes one panel feel held.
_MIN_SHOT = 1.25
_MAX_SHOT = 3.0
_ANTICIPATION = 0.28


def _clip(value: float) -> float:
    return max(0.05, min(0.95, float(value)))


def rank_rois(candidate: PanelCandidate | None, narration: str = "") -> tuple[ROI, ...]:
    """Turn existing focal points/features into ranked editorial targets.

    This is not a second detector. It labels the focal regions already exposed by
    visual scoring, keeping the Shot Director independent from CV/OCR tooling.
    """
    if candidate is None:
        return (ROI("composition", 0.5, 0.4, 0.1),)
    f = candidate.features
    tags = visual_scoring.narration_tags(narration)
    labels: list[tuple[str, float]] = []
    if f.face_visibility or "dialogue" in tags or "thinking" in tags:
        labels.append(("face", f.face_visibility + f.facial_expression + 0.2))
    if f.weapons or "weapon" in tags:
        labels.append(("weapon", f.weapons + 0.2))
    if f.monsters or "monster" in tags:
        labels.append(("opponent", f.monsters + 0.2))
    if f.visual_effects or "explosion" in tags:
        labels.append(("effect", f.visual_effects + f.impact_frame + 0.1))
    labels.append(("detail", f.object_density + f.dramatic_composition * 0.5))
    labels.sort(key=lambda item: item[1], reverse=True)
    labels = labels or [("composition", 0.1)]

    points = tuple(candidate.features.focal_points) or ((0.5, 0.4),)
    rois: list[ROI] = []
    for index, (x, y) in enumerate(points):
        label, base = labels[index % len(labels)]
        # Slightly discount fallback labels; each point remains usable.
        rois.append(ROI(label if index < len(labels) else f"{label}_{index + 1}", _clip(x), _clip(y), base - index * 0.03))
    return tuple(rois)


def _camera_options(narration: str, index: int) -> tuple[str, ...]:
    tags = visual_scoring.narration_tags(narration)
    if "explosion" in tags:
        return ("impact_shake", "micro_shake", "punch_zoom")
    if "attack" in tags or "action" in tags:
        return ("punch_zoom", "micro_shake", "pan_diagonal", "focus_shift")
    if "victory" in tags:
        return ("dramatic_zoom_out", "slow_push_in", "pan_vertical")
    if "reveal" in tags:
        return ("push_in", "focus_shift", "slow_push_in", "pan_vertical")
    if "thinking" in tags:
        return ("pan_horizontal", "focus_shift", "pan_diagonal")
    if "dialogue" in tags:
        return ("slow_push_in", "pan_horizontal", "focus_shift")
    return (
        "slow_push_in", "pan_horizontal", "pan_vertical", "pan_diagonal",
        "slow_pull_out", "focus_shift", "orbit",
    )


def _choose_effect(narration: str, index: int, recent: list[str]) -> str:
    options = _camera_options(narration, index)
    tags = visual_scoring.narration_tags(narration)
    start = 0 if tags else index % len(options)
    for offset in range(len(options)):
        effect = options[(start + offset) % len(options)]
        if effect not in recent[-2:]:
            return effect
    return options[index % len(options)]


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
    recent_effects: list[str] = []
    used: set[str] = set()
    signatures: set[str] = set()
    previous_order: int | None = None
    order = 0

    for span_index, span in enumerate(spans):
        span_list = spans
        next_start = span_list[span_index + 1].start_time if span_index + 1 < len(span_list) else span.end_time
        block_end = max(span.end_time, next_start)
        duration = max(0.0, block_end - span.start_time)
        candidate = _candidate_for(candidates, span.text, previous_order, used, signatures)
        rois = rank_rois(candidate, span.text)
        max_slots = max(1, int(duration // min_seconds))
        slots = min(
            6,
            max_slots,
            max(math.ceil(duration / max_seconds), min(len(rois), max_slots)),
        ) if duration else 0
        if not slots:
            continue
        slot_duration = duration / slots
        first_index = len(scenes)
        for slot in range(slots):
            start = span.start_time + slot * slot_duration
            end = span.start_time + (slot + 1) * slot_duration
            roi = rois[slot % len(rois)]
            next_roi = rois[(slot + 1) % len(rois)] if len(rois) > 1 else roi
            effect = _choose_effect(span.text, order, recent_effects)
            recent_effects.append(effect)
            scenes.append(
                ShotPlan(
                    order_index=order, section=span.section,
                    start_time=round(start, 3), end_time=round(end, 3),
                    asset_id=candidate.asset_id if candidate else None,
                    roi_label=roi.label,
                    focus_x=roi.x, focus_y=roi.y,
                    focus_end_x=next_roi.x, focus_end_y=next_roi.y,
                    effect=effect, camera_curve=effect,
                    transition="none" if order == 0 else ("cut" if candidate and scenes[-1].asset_id == candidate.asset_id else "fade"),
                    visual_score=candidate.visual_score if candidate else 0.0,
                    semantic_score=candidate.semantic_score if candidate else 0.0,
                )
            )
            order += 1
        if candidate:
            previous_order = candidate.order_index
            used.add(candidate.asset_id)
            if candidate.features.visual_signature:
                signatures.add(candidate.features.visual_signature)
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
