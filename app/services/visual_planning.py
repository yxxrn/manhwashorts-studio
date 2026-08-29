"""Orchestration boundary between visual scoring and directed shot planning."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from app.services.camera_planner import apply_camera_plans
from app.services.shot_director import plan_shots
from app.services.visual_contracts import PanelCandidate


def plan_content_aware_scenes(
    spans: Iterable[object],
    candidates: list[PanelCandidate],
    min_scene_seconds: float = 2.0,
    max_scene_seconds: float = 6.0,
    preferred_asset_ids_by_section: Mapping[str, Iterable[str]] | None = None,
    max_asset_uses: int | None = None,
) -> list[dict]:
    """Plan directed shots and apply camera plans without re-scoring panels."""
    return apply_camera_plans(
        plan_shots(
            list(spans),
            candidates,
            min_scene_seconds,
            max_scene_seconds,
            preferred_asset_ids_by_section=preferred_asset_ids_by_section,
            max_asset_uses=max_asset_uses,
        )
    )
