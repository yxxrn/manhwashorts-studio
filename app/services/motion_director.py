"""Deterministic human-editor motion grammar for CPU motion-comic shots."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, replace

MODES = frozenset({
    "hold", "slow_push", "slow_pull", "guided_pan", "focus_shift", "panel_reveal",
    "split_focus", "panel_stack", "impact", "whip_transition", "atmospheric",
    "static_emphasis",
})
_STRONG = frozenset({"attack", "action", "impact", "explosion"})


@dataclass(frozen=True)
class MotionPlan:
    mode: str
    intensity: str
    reason: str
    seed: int
    valid: bool = True

    def validate(self) -> MotionPlan:
        if self.mode not in MODES:
            return replace(self, mode="hold", intensity="low", reason="safe fallback", valid=False)
        if self.mode == "impact" and self.intensity == "high" and not self.reason:
            return replace(self, intensity="medium", valid=False)
        return self


def _seed(seed: int, index: int, text: str) -> int:
    raw = f"{seed}:{index}:{text}".encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:4], "big")


def plan_motion(
    *, section: str, narration_tags: Iterable[str], roi_label: str = "",
    duration: float = 1.5, history: Iterable[str] = (), seed: int = 42,
    index: int = 0,
) -> MotionPlan:
    """Choose one purposeful mode; history prevents mechanical repetition."""
    tags = set(narration_tags)
    previous = list(history)[-2:]
    local_seed = _seed(seed, index, f"{section}:{roi_label}:{duration}")
    if "explosion" in tags or "impact" in tags:
        mode, intensity, reason = "impact", "high", "impact tag and action ROI"
    elif "dialogue" in tags and roi_label == "speech_bubble":
        mode, intensity, reason = "split_focus", "low", "dialogue plus speech-bubble/face context"
    elif ("attack" in tags or "action" in tags) and roi_label in {"weapon", "magic_effect", "effect"}:
        mode, intensity, reason = "panel_stack", "medium", "action object needs wide context plus detail"
    elif "attack" in tags or "action" in tags:
        options = ["guided_pan", "impact", "hold"]
        mode = next((options[(local_seed + n) % len(options)] for n in range(len(options)) if options[(local_seed + n) % len(options)] not in previous), "hold")
        intensity, reason = ("medium", "attack tag and subject ROI") if mode != "hold" else ("low", "action release beat")
    elif "reveal" in tags or section in {"twist", "cliffhanger"}:
        mode, intensity, reason = ("panel_reveal", "medium", "reveal needs hold then reveal")
    elif "victory" in tags:
        mode, intensity, reason = "slow_pull", "low", "victory release"
    elif "thinking" in tags:
        mode, intensity, reason = "slow_push", "low", "thinking beat"
    elif "dialogue" in tags:
        mode, intensity, reason = "slow_push", "low", "dialogue clarity with living frame"
    elif "approach" in tags:
        mode, intensity, reason = "guided_pan", "low", "approach direction"
    else:
        mode, intensity, reason = "slow_push", "low", "new information receives continuous internal motion"
    return MotionPlan(mode, intensity, reason, local_seed).validate()


def audit_motion(plans: Iterable[MotionPlan]) -> list[str]:
    items = [plan.validate() for plan in plans]
    issues: list[str] = []
    for left, right in zip(items, items[1:], strict=False):
        if left.mode == right.mode == "impact":
            issues.append("strong_effects_consecutive")
        if left.mode == right.mode and left.mode not in {"hold", "static_emphasis"}:
            issues.append("motion_mode_repeated")
    if any(not item.valid for item in items):
        issues.append("invalid_motion_plan")
    return sorted(set(issues))


__all__ = ["MODES", "MotionPlan", "audit_motion", "plan_motion"]
# ponytail: rules-based motion ceiling; replace only the planner, not renderer contracts.
