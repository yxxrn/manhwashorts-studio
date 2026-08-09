"""Execute camera curves already chosen by the Shot Director.

This layer contains no panel, ROI, timing, transition, or narration decisions.
It only validates the director's semantic camera command and exposes the renderer
field. Editorial scheduling stays in ``shot_director.py``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.services.motion_director import ALLOWED_CURVES, safe_camera_curve

_SUPPORTED_CURVES = frozenset(
    ALLOWED_CURVES
)


@dataclass(frozen=True)
class CameraPlan:
    order_index: int
    effect: str
    camera_curve: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "order_index": self.order_index,
            "effect": self.effect,
            "camera_curve": self.camera_curve,
        }


def execute_camera_plan(order_index: int, camera_curve: str) -> CameraPlan:
    """Translate one approved director curve into renderer instructions."""
    safe = safe_camera_curve(camera_curve)
    if camera_curve not in _SUPPORTED_CURVES and safe == "static":
        raise ValueError(f"unsupported camera curve: {camera_curve}")
    return CameraPlan(order_index, safe, safe)


def apply_camera_plans(shots: Iterable[object]) -> list[dict[str, Any]]:
    """Preserve Shot Director decisions; add only renderer execution fields."""
    output: list[dict[str, Any]] = []
    for shot in shots:
        data = shot.as_dict() if hasattr(shot, "as_dict") else dict(shot)
        camera = execute_camera_plan(data["order_index"], data["camera_curve"])
        data.update(camera.as_dict())
        output.append(data)
    return output


__all__ = ["CameraPlan", "apply_camera_plans", "execute_camera_plan"]
