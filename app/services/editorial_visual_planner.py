"""Automated editorial visual planning.

This is the public Director/visual-planner boundary. It consumes analyzed panel
candidates and narration spans, then returns an editorial shot list before camera
execution. It never re-scores panels.
"""
from __future__ import annotations

from collections.abc import Iterable

from app.services import director, motion_director, visual_scoring


def plan(spans: Iterable[object], candidates: list[object]) -> list[dict]:
    """Create a beat-aware, ROI-driven shot list before rendering."""
    beats = director.analyze_story(list(spans))
    shots = visual_scoring.plan_content_aware_scenes(beats, candidates)
    history: list[str] = []
    motion_plans = []
    curve_for = {
        "hold": "static", "static_emphasis": "static", "slow_push": "slow_push_in",
        "slow_pull": "slow_pull_out", "guided_pan": "pan_horizontal", "focus_shift": "focus_shift",
        "panel_reveal": "push_in", "impact": "impact_shake", "whip_transition": "pan_horizontal",
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


__all__ = ["classify_source_text", "plan"]
