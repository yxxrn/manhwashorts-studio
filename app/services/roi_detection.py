"""ROI detection adapter for the editorial pipeline.

Visual analysis owns pixel inspection. This module turns its normalized features
into ranked editorial regions. Shot Director consumes ROIs; it does not inspect
pixels or invent focal regions.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services import visual_scoring
from app.services.visual_contracts import PanelCandidate


@dataclass(frozen=True)
class ROI:
    """Ranked visual region passed to the Shot Director."""

    label: str
    x: float
    y: float
    priority: float


def _clip(value: float) -> float:
    return max(0.05, min(0.95, float(value)))


def rank_rois(candidate: PanelCandidate | None, narration: str = "") -> tuple[ROI, ...]:
    """Return distinct editorial regions, not repeated generic focal points.

    The detector deliberately emits a small region vocabulary: face/eyes,
    hands, weapon, monster, effect, speech bubble, and detail. Coordinates come
    from CV face anchors plus the strongest image regions; the director decides
    how many fit the narration duration.
    """
    if candidate is None:
        return (ROI("composition", 0.5, 0.4, 0.1),)
    f = candidate.features
    tags = visual_scoring.narration_tags(narration)
    points = tuple(candidate.features.focal_points) or ((0.5, 0.4),)
    # Hand-authored candidates without detector anchors retain the established
    # fallback vocabulary. Real analyzed panels carry face_points and use the
    # richer semantic-region path below.
    if not f.face_points:
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
        return tuple(
            ROI(label, _clip(x), _clip(y), base - index * 0.03)
            for index, (x, y) in enumerate(points)
            for label, base in [labels[index % len(labels)]]
        )
    regions: list[tuple[str, float, float, float]] = []

    def add(label: str, x: float, y: float, priority: float) -> None:
        x, y = _clip(x), _clip(y)
        if any(label == old_label for old_label, _, _, _ in regions):
            return
        regions.append((label, x, y, priority))

    face_points = f.face_points
    for x, y in face_points:
        add("face", x, y, f.face_visibility + f.facial_expression + 0.45)
        add("eyes", x, y - 0.08, f.face_visibility + f.facial_expression + 0.35)
    if "dialogue" in tags or "thinking" in tags or f.ocr_text:
        x, y = points[-1]
        add("speech_bubble", x, min(y, 0.22), 0.55 if f.ocr_text else 0.3)
    if f.weapons or "weapon" in tags:
        x, y = points[min(1, len(points) - 1)]
        add("weapon", x, y, f.weapons + 0.35)
    if f.monsters or "monster" in tags:
        x, y = points[0]
        add("monster", x, y, f.monsters + 0.35)
    if f.visual_effects or "explosion" in tags:
        x, y = points[-1]
        add("magic_effect", x, y, f.visual_effects + f.impact_frame + 0.3)
    if f.action_pose:
        x, y = points[min(1, len(points) - 1)]
        add("hands", x, min(0.95, y + 0.12), f.action_pose + 0.25)
    for index, (x, y) in enumerate(points):
        add("detail" if index else "composition", x, y, f.object_density + f.dramatic_composition * 0.5 - index * 0.04)
    regions.sort(key=lambda item: item[3], reverse=True)
    return tuple(ROI(label, x, y, priority) for label, x, y, priority in regions) or (ROI("composition", 0.5, 0.4, 0.1),)


__all__ = ["ROI", "rank_rois"]
