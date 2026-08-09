"""Automated editorial visual planning.

This is the public Director/visual-planner boundary. It consumes analyzed panel
candidates and narration spans, then returns an editorial shot list before camera
execution. It never re-scores panels.
"""
from __future__ import annotations

import math
from collections.abc import Iterable

from app.services import director, motion_director, visual_scoring

_MAX_EDITORIAL_SHOT_SECONDS = 2.8
_BEAT_PRIORITY = {
    "neutral": 0,
    "suspense": 1,
    "thinking": 2,
    "victory": 3,
    "reveal": 4,
    "action": 5,
    "attack": 6,
    "impact": 7,
    "explosion": 8,
}


def _coalesce_beats(beats: list[director.StoryBeat]) -> list[director.StoryBeat]:
    """Compress event fragments to the fixed 18-24 shot editorial budget."""
    if not beats:
        return []
    result: list[director.StoryBeat] = []
    cursor = 0
    while cursor < len(beats):
        section_start = cursor
        section = beats[cursor].section
        while cursor < len(beats) and beats[cursor].section == section:
            cursor += 1
        section_beats = beats[section_start:cursor]
        section_duration = max(
            0.0, section_beats[-1].end_time - section_beats[0].start_time
        )
        group_count = max(
            1,
            min(
                len(section_beats),
                math.ceil(section_duration / _MAX_EDITORIAL_SHOT_SECONDS),
            ),
        )
        section_start_time = section_beats[0].start_time
        for group_index in range(group_count):
            group_start = (
                section_start_time
                + section_duration * group_index / group_count
            )
            group_end = (
                section_start_time
                + section_duration * (group_index + 1) / group_count
            )
            group = [
                beat
                for beat in section_beats
                if beat.end_time > group_start and beat.start_time < group_end
            ]
            if not group:
                group = [
                    min(
                        section_beats,
                        key=lambda beat: abs(beat.start_time - group_start),
                    )
                ]
            lead = max(
                group,
                key=lambda beat: (
                    _BEAT_PRIORITY.get(beat.kind, 0),
                    beat.end_time - beat.start_time,
                ),
            )
            timings = [
                timing
                for beat in group
                for timing in beat.word_timings
                if group_start <= float(timing.get("start", 0.0)) < group_end
            ]
            result.append(
                director.StoryBeat(
                    section=section,
                    text=group[0].text,
                    start_time=round(group_start, 3),
                    end_time=round(group_end, 3),
                    word_timings=timings,
                    kind=lead.kind,
                    emotion=lead.emotion,
                    camera_intent=lead.camera_intent,
                    visual_timing=lead.visual_timing,
                    composition=lead.composition,
                    timing_offset=lead.timing_offset,
                    impact_lock=any(beat.impact_lock for beat in group),
                )
            )
    return result

def plan(spans: Iterable[object], candidates: list[object]) -> list[dict]:
    """Create a beat-aware, ROI-driven shot list before rendering."""
    beats = _coalesce_beats(director.analyze_story(list(spans)))
    shots = visual_scoring.plan_content_aware_scenes(beats, candidates)
    history: list[str] = []
    motion_plans = []
    curve_for = {
        "hold": "static", "static_emphasis": "static", "slow_push": "slow_push_in",
        "slow_pull": "slow_pull_out", "guided_pan": "pan_horizontal", "focus_shift": "focus_shift",
        "panel_reveal": "push_in", "impact": "push_in",
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

    # Normal renders need enough visual grammar to read as an edit rather than
    # one repeated Ken Burns move. Rebalance only neutral/release beats so
    # impact, speech-bubble, and panel-stack intent remains intact.
    if len(motion_plans) >= 4 and len({plan.mode for plan in motion_plans}) < 4:
        diversity_modes = ("slow_push", "guided_pan", "focus_shift", "slow_pull", "atmospheric")
        used_modes = {plan.mode for plan in motion_plans}
        for index, plan in enumerate(motion_plans):
            if len(used_modes) >= 4:
                break
            if plan.mode in {"impact", "split_focus", "panel_stack"}:
                continue
            replacement = next((mode for mode in diversity_modes if mode not in used_modes), "")
            if not replacement:
                break
            replacement_plan = motion_director.MotionPlan(
                replacement,
                plan.intensity,
                f"{plan.reason}; diversity rebalance",
                plan.seed,
            ).validate()
            motion_plans[index] = replacement_plan
            shots[index]["motion_mode"] = replacement_plan.mode
            shots[index]["motion_intensity"] = replacement_plan.intensity
            shots[index]["motion_reason"] = replacement_plan.reason
            shots[index]["camera_curve"] = curve_for[replacement_plan.mode]
            used_modes.add(replacement_plan.mode)

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
