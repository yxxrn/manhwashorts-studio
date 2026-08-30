"""Deterministic human-editor motion grammar for CPU motion-comic shots."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, replace

MODES = frozenset({
    "hold", "slow_push", "slow_pull", "guided_pan", "focus_shift", "panel_reveal",
    "split_focus", "panel_stack", "impact", "atmospheric",
    "static_emphasis",
})
FORBIDDEN_CURVES = frozenset({
    "micro_shake", "impact_shake", "shake_zoom", "orbit", "punch_zoom",
    "whip_transition", "explosion",
})
ALLOWED_CURVES = frozenset({
    "static", "slow_push_in", "slow_pull_out", "pan_horizontal", "pan_vertical",
    "pan_diagonal", "focus_shift", "push_in", "reveal", "atmospheric",
    "static_emphasis",
})
NORMAL_ZOOM_MAX = 1.06
IMPACT_ZOOM_MAX = 1.08
_EMPHASIS_CURVES = frozenset({"push_in", "reveal"})
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


def safe_camera_curve(curve: str) -> str:
    """Map old or unknown curves to a stable, non-oscillating curve."""
    value = str(curve or "").strip()
    if value in FORBIDDEN_CURVES:
        return "static_emphasis"
    return value if value in ALLOWED_CURVES else "static"


def curve_zoom_cap(curve: str) -> float:
    """Return the exact normal or reveal zoom ceiling for a curve."""
    return IMPACT_ZOOM_MAX if safe_camera_curve(curve) in _EMPHASIS_CURVES else NORMAL_ZOOM_MAX


def _clamp_focus(value: float) -> float:
    return max(0.05, min(0.95, float(value)))


def _smoothstep(progress: float) -> float:
    return progress * progress * (3.0 - 2.0 * progress)


def sample_camera_curve(
    curve: str,
    frames: int,
    focus_x: float = 0.5,
    focus_y: float = 0.4,
    focus_end_x: float = 0.5,
    focus_end_y: float = 0.4,
    *,
    normal_zoom_delta: float | None = None,
    impact_zoom_delta: float | None = None,
    pan_zoom_delta: float | None = None,
) -> tuple[tuple[float, float, float], ...]:
    """Sample a deterministic monotonic ``(focus_x, focus_y, scale)`` path."""
    if frames < 2:
        raise ValueError("frames must be at least two")
    safe = safe_camera_curve(curve)
    start_x, start_y = _clamp_focus(focus_x), _clamp_focus(focus_y)
    end_x, end_y = _clamp_focus(focus_end_x), _clamp_focus(focus_end_y)
    normal_delta = max(
        0.0,
        float(NORMAL_ZOOM_MAX - 1.0 if normal_zoom_delta is None else normal_zoom_delta),
    )
    impact_delta = max(
        0.0,
        float(IMPACT_ZOOM_MAX - 1.0 if impact_zoom_delta is None else impact_zoom_delta),
    )
    pan_delta = max(normal_delta, float(normal_delta if pan_zoom_delta is None else pan_zoom_delta))
    samples: list[tuple[float, float, float]] = []
    for index in range(frames):
        progress = index / (frames - 1)
        eased = _smoothstep(progress)
        if safe in {"static", "static_emphasis"}:
            x, y = start_x, start_y
        else:
            x = start_x + (end_x - start_x) * eased
            y = start_y + (end_y - start_y) * eased
        if safe == "slow_push_in":
            scale = 1.0 + normal_delta * eased
        elif safe == "slow_pull_out":
            scale = 1.0 + normal_delta - normal_delta * eased
        elif safe in _EMPHASIS_CURVES:
            scale = 1.0 + impact_delta * eased
        elif safe == "static_emphasis":
            scale = 1.0 + normal_delta * 0.45
        elif safe == "atmospheric":
            scale = 1.0 + normal_delta * 0.55 * eased
        elif safe == "static":
            scale = 1.0
        elif safe in {"pan_horizontal", "pan_vertical", "pan_diagonal", "focus_shift"}:
            # Directional moves need headroom from the first frame; otherwise
            # normalized focus travel collapses to only a few output pixels.
            scale = 1.0 + pan_delta
        else:
            scale = 1.0 + normal_delta * eased
        samples.append((round(x, 9), round(y, 9), round(scale, 9)))
    return tuple(samples)

def _seed(seed: int, index: int, text: str) -> int:
    raw = f"{seed}:{index}:{text}".encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:4], "big")


def _choose_mode(options: list[str], previous: list[str], local_seed: int) -> str:
    """Choose a deterministic option while avoiding the recent mode history."""
    return next(
        (
            options[(local_seed + offset) % len(options)]
            for offset in range(len(options))
            if options[(local_seed + offset) % len(options)] not in previous
        ),
        options[local_seed % len(options)],
    )


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
        mode, intensity, reason = "impact", "high", "stable action emphasis without camera shake"
    elif "dialogue" in tags and roi_label == "speech_bubble":
        mode, intensity, reason = "split_focus", "low", "dialogue plus speech-bubble/face context"
    elif ("attack" in tags or "action" in tags) and roi_label in {"weapon", "magic_effect", "effect"}:
        mode, intensity, reason = "panel_stack", "medium", "stable action framing keeps context and detail"
    elif "attack" in tags or "action" in tags:
        mode = _choose_mode(["guided_pan", "focus_shift", "slow_pull", "atmospheric"], previous, local_seed)
        intensity, reason = "medium", "stable action subject receives a deterministic directional move"
    elif "reveal" in tags or section in {"twist", "cliffhanger"}:
        mode = _choose_mode(["panel_reveal", "focus_shift", "slow_pull", "atmospheric"], previous, local_seed)
        intensity, reason = "medium", "reveal receives a deterministic reveal-to-detail progression"
    elif "victory" in tags:
        mode = _choose_mode(["slow_pull", "atmospheric", "focus_shift"], previous, local_seed)
        intensity, reason = "low", "victory release with a deterministic mode variation"
    elif "thinking" in tags:
        mode = _choose_mode(["slow_push", "focus_shift", "guided_pan"], previous, local_seed)
        intensity, reason = "low", "thinking beat with a deterministic reading vector"
    elif "dialogue" in tags:
        mode = _choose_mode(["slow_push", "guided_pan", "focus_shift", "atmospheric"], previous, local_seed)
        intensity, reason = "low", "dialogue clarity with deterministic living-frame variation"
    elif "approach" in tags:
        mode = _choose_mode(["guided_pan", "slow_push", "focus_shift"], previous, local_seed)
        intensity, reason = "low", "approach direction with deterministic mode variation"
    else:
        mode = _choose_mode(["slow_push", "slow_pull", "guided_pan", "focus_shift", "atmospheric"], previous, local_seed)
        intensity, reason = "low", "new information receives deterministic internal motion variation"
    return MotionPlan(mode, intensity, reason, local_seed).validate()


def audit_motion(plans: Iterable[MotionPlan]) -> list[str]:
    items = [plan.validate() for plan in plans]
    issues: list[str] = []
    for left, right in zip(items, items[1:], strict=False):
        if left.mode == right.mode == "impact":
            issues.append("strong_effects_consecutive")
            issues.append("motion.emphasis_consecutive")
        if left.mode == right.mode and left.mode not in {"hold", "static_emphasis"}:
            issues.append("motion_mode_repeated")
    if any(not item.valid for item in items):
        issues.append("invalid_motion_plan")
    return sorted(set(issues))


def audit_camera_sequence(shots: Iterable[object]) -> list[str]:
    """Audit legacy curves, same-asset reversals, and repeated emphasis pushes."""
    items = list(shots)
    issues: list[str] = []
    for shot in items:
        curve = str(getattr(shot, "camera_curve", "") or getattr(shot, "effect", ""))
        if curve in FORBIDDEN_CURVES:
            issues.append("motion.forbidden_curve")
    for left, right in zip(items, items[1:], strict=False):
        left_curve = str(getattr(left, "camera_curve", "") or getattr(left, "effect", ""))
        right_curve = str(getattr(right, "camera_curve", "") or getattr(right, "effect", ""))
        if left_curve in _EMPHASIS_CURVES and right_curve in _EMPHASIS_CURVES:
            issues.append("motion.emphasis_push_consecutive")
        if getattr(left, "asset_id", None) != getattr(right, "asset_id", None):
            continue
        if getattr(left, "transition", "cut") in {"cut", "none"} or getattr(
            right, "transition", "cut"
        ) in {"cut", "none"}:
            continue
        if left_curve not in {"pan_horizontal", "pan_vertical", "pan_diagonal"} or right_curve not in {
            "pan_horizontal", "pan_vertical", "pan_diagonal"
        }:
            continue
        dx = float(getattr(left, "focus_end_x", 0.5)) - float(getattr(left, "focus_x", 0.5))
        dy = float(getattr(left, "focus_end_y", 0.5)) - float(getattr(left, "focus_y", 0.5))
        next_dx = float(getattr(right, "focus_end_x", 0.5)) - float(getattr(right, "focus_x", 0.5))
        next_dy = float(getattr(right, "focus_end_y", 0.5)) - float(getattr(right, "focus_y", 0.5))
        if max(abs(dx), abs(dy)) < 0.12 or max(abs(next_dx), abs(next_dy)) < 0.12:
            continue
        if dx * next_dx + dy * next_dy < -0.01:
            issues.append("motion.reversal_same_asset")
    return sorted(set(issues))


__all__ = [
    "ALLOWED_CURVES", "FORBIDDEN_CURVES", "IMPACT_ZOOM_MAX", "MODES",
    "NORMAL_ZOOM_MAX", "MotionPlan", "audit_camera_sequence", "audit_motion",
    "curve_zoom_cap", "plan_motion", "safe_camera_curve", "sample_camera_curve",
]
# ponytail: rules-based motion ceiling; replace only the planner, not renderer contracts.
