"""ROI detection adapter for the editorial pipeline.

Visual analysis owns pixel inspection. This module turns its normalized features
into ranked editorial regions. Shot Director consumes ROIs; it does not inspect
pixels or invent focal regions.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services import visual_scoring
from app.services.visual_scoring import PanelCandidate


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
    """Rank all available focal points by narration relevance and salience."""
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
    return tuple(
        ROI(
            label if index < len(labels) else f"{label}_{index + 1}",
            _clip(x), _clip(y), base - index * 0.03,
        )
        for index, (x, y) in enumerate(points)
        for label, base in [labels[index % len(labels)]]
    )


__all__ = ["ROI", "rank_rois"]
