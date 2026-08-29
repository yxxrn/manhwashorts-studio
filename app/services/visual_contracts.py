"""Dependency-light visual data contracts shared by scoring, ROI, and shot planning."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VisualFeatures:
    face_visibility: float = 0.0
    facial_expression: float = 0.0
    action_pose: float = 0.0
    weapons: float = 0.0
    monsters: float = 0.0
    visual_effects: float = 0.0
    motion_lines: float = 0.0
    impact_frame: float = 0.0
    close_up: float = 0.0
    dramatic_composition: float = 0.0
    object_density: float = 0.0
    empty_background: float = 0.0
    scenery_only: float = 0.0
    transition: float = 0.0
    speech_balloon_dominance: float = 0.0
    ui_overlay_dominance: float = 0.0
    blank_dominance: float = 0.0
    ocr_text: str = ""
    semantic_tags: frozenset[str] = frozenset()
    focal_points: tuple[tuple[float, float], ...] = ((0.5, 0.4),)
    face_points: tuple[tuple[float, float], ...] = ()
    face_boxes: tuple[tuple[float, float, float, float], ...] = ()
    visual_signature: str = ""


@dataclass(frozen=True)
class PanelCandidate:
    asset_id: str
    order_index: int
    features: VisualFeatures
    visual_score: float
    semantic_score: float = 0.0
    source_family: str = ""
