"""Automated editorial visual planning.

This is the public Director/visual-planner boundary. It consumes analyzed panel
candidates and narration spans, then returns an editorial shot list before camera
execution. It never re-scores panels.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from app.services import director, motion_director, visual_scoring

_MAX_EDITORIAL_SHOT_SECONDS = 2.8
_REFERENCE_SHOT_INTERVAL_SECONDS = 1.28
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


class ReferencePlanningError(RuntimeError):
    """Raised when the selected reference profile cannot be satisfied safely."""

    code = "reference_planning_failed"


def _reference_group_counts(
    beats: list[director.StoryBeat], target_shots: int
) -> list[int]:
    groups: list[list[director.StoryBeat]] = []
    cursor = 0
    while cursor < len(beats):
        start = cursor
        section = beats[cursor].section
        while cursor < len(beats) and beats[cursor].section == section:
            cursor += 1
        groups.append(beats[start:cursor])
    if not groups:
        return []
    durations = [
        max(0.001, group[-1].end_time - group[0].start_time)
        for group in groups
    ]
    total = sum(durations)
    counts = [1] * len(groups)
    remaining = max(0, target_shots - len(groups))
    exact = [remaining * duration / total for duration in durations]
    counts = [count + math.floor(value) for count, value in zip(counts, exact, strict=True)]
    left = target_shots - sum(counts)
    order = sorted(
        range(len(groups)),
        key=lambda index: (-(exact[index] - math.floor(exact[index])), index),
    )
    for index in order[:left]:
        counts[index] += 1
    return counts


def _coalesce_beats(
    beats: list[director.StoryBeat], target_shots: int | None = None
) -> list[director.StoryBeat]:
    """Compress event fragments to the fixed 18-24 shot editorial budget."""
    if not beats:
        return []
    result: list[director.StoryBeat] = []
    target_counts = _reference_group_counts(beats, target_shots) if target_shots else []
    cursor = 0
    section_index = 0
    while cursor < len(beats):
        section_start = cursor
        section = beats[cursor].section
        while cursor < len(beats) and beats[cursor].section == section:
            cursor += 1
        section_beats = beats[section_start:cursor]
        section_duration = max(
            0.0, section_beats[-1].end_time - section_beats[0].start_time
        )
        group_count = (
            target_counts[section_index]
            if target_shots
            else max(
                1,
                min(
                    len(section_beats),
                    math.ceil(section_duration / _MAX_EDITORIAL_SHOT_SECONDS),
                ),
            )
        )
        section_index += 1
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


def _reference_section_durations(
    beats: list[director.StoryBeat], total_duration: float
) -> list[tuple[str, float]]:
    durations: list[tuple[str, float]] = []
    cursor = 0
    source_total = max(0.001, beats[-1].end_time - beats[0].start_time)
    while cursor < len(beats):
        start = cursor
        section = beats[cursor].section
        while cursor < len(beats) and beats[cursor].section == section:
            cursor += 1
        duration = max(0.001, beats[cursor - 1].end_time - beats[start].start_time)
        durations.append((section, total_duration * duration / source_total))
    correction = total_duration - sum(duration for _, duration in durations)
    if durations:
        section, duration = durations[-1]
        durations[-1] = (section, duration + correction)
    return durations


def _reference_emphasis_indexes(shots: list[dict]) -> set[int]:
    """Choose emphasis slots from semantic priority in stable source order."""
    if len(shots) < 2:
        return set()
    target = max(1, min(len(shots) - 1, round(len(shots) * 0.25)))
    section_order = {
        section: index
        for index, section in enumerate(
            dict.fromkeys(str(shot.get("section", "")) for shot in shots)
        )
    }
    ranked: list[tuple[int, int, int, int]] = []
    for index, shot in enumerate(shots):
        section = str(shot.get("section", ""))
        intent = str(shot.get("camera_intent", "") or "").lower()
        intent_priority = max(
            (priority for tag, priority in _BEAT_PRIORITY.items() if tag in intent),
            default=0,
        )
        section_priority = _BEAT_PRIORITY.get(
            section,
            {"twist": 4, "cta": 4, "cliffhanger": 4}.get(section, 0),
        )
        if section in {"twist", "cta", "cliffhanger"}:
            section_priority += 2
        ranked.append(
            (-intent_priority, -section_priority, section_order.get(section, 0), index)
        )
    selected: list[int] = []
    per_section: dict[str, int] = {}
    section_caps: dict[str, int] = {}
    for shot in shots:
        section = str(shot.get("section", ""))
        section_count = sum(
            1 for item in shots if str(item.get("section", "")) == section
        )
        section_caps[section] = max(0, min(section_count - 1, math.ceil(section_count * 0.4)))
    for _intent_priority, _section_priority, _section_index, index in sorted(ranked):
        section = str(shots[index].get("section", ""))
        if per_section.get(section, 0) >= section_caps[section]:
            continue
        selected.append(index)
        per_section[section] = per_section.get(section, 0) + 1
        if len(selected) == target:
            break
    return set(selected)


def _reference_section_shot_durations(
    section_duration: float,
    shot_count: int,
    emphasis_count: int,
    profile: object,
    emphasis_positions: set[int] | None = None,
) -> list[float]:
    if emphasis_count <= 0:
        values = [section_duration / shot_count] * shot_count
    else:
        emphasis_duration = min(
            profile.emphasis_max_s,
            max(profile.emphasis_min_s, section_duration / shot_count + 0.4),
        )
        normal_count = shot_count - emphasis_count
        if normal_count <= 0:
            raise ReferencePlanningError(
                f"{profile.profile_id} cannot leave a normal cadence in every section"
            )
        normal_duration = (
            section_duration - emphasis_count * emphasis_duration
        ) / normal_count
        positions = emphasis_positions or set(range(emphasis_count))
        values = [
            emphasis_duration if index in positions else normal_duration
            for index in range(shot_count)
        ]
    if any(
        not (
            profile.hold_min_s <= value <= profile.hold_max_s
            or profile.emphasis_min_s <= value <= profile.emphasis_max_s
        )
        for value in values
    ):
        raise ReferencePlanningError(
            f"{profile.profile_id} cannot fit a section within its duration bands"
        )
    return values


def _apply_reference_motion(shots: list[dict], beats: list[director.StoryBeat]) -> None:
    """Attach one safe, purposeful MotionPlan to every reference shot."""
    curve_for = {
        "hold": "static",
        "static_emphasis": "static_emphasis",
        "slow_push": "slow_push_in",
        "slow_pull": "slow_pull_out",
        "guided_pan": "pan_horizontal",
        "focus_shift": "focus_shift",
        "panel_reveal": "reveal",
        "impact": "push_in",
        "atmospheric": "atmospheric",
        "split_focus": "focus_shift",
        "panel_stack": "slow_pull_out",
    }
    section_text = {
        beat.section: beat.text
        for beat in beats
        if beat.section not in {""}
    }
    history: list[str] = []
    for index, shot in enumerate(shots):
        text = str(shot.get("text", "") or section_text.get(shot.get("section", ""), ""))
        motion = motion_director.plan_motion(
            section=str(shot.get("section", "")),
            narration_tags=visual_scoring.narration_tags(text),
            roi_label=str(shot.get("roi_label", "")),
            duration=float(shot["end_time"]) - float(shot["start_time"]),
            history=history,
            seed=42,
            index=index,
        )
        history.append(motion.mode)
        shot["motion_mode"] = motion.mode
        shot["motion_intensity"] = motion.intensity
        shot["motion_reason"] = motion.reason or "reference stable motion intent"
        shot["camera_curve"] = motion_director.safe_camera_curve(
            curve_for.get(motion.mode, "static")
        )
        shot["overlay_text"] = ""


def _reference_roi_key(shot: Mapping[str, object]) -> tuple[object, ...]:
    return (
        shot.get("roi_label", ""),
        round(float(shot.get("focus_x", 0.0)), 3),
        round(float(shot.get("focus_y", 0.0)), 3),
        round(float(shot.get("focus_end_x", 0.0)), 3),
        round(float(shot.get("focus_end_y", 0.0)), 3),
    )


def _plan_reference(
    spans: list[object],
    candidates: list[object],
    profile: object,
    cited_asset_ids_by_section: Mapping[str, Iterable[str]] | None,
    citation_alignment_reasons_by_section: Mapping[str, Iterable[str]] | None,
) -> list[dict]:
    if not spans or not candidates:
        raise ReferencePlanningError(
            f"{profile.profile_id} requires renderable panels and narration spans"
        )
    origin = min(float(span.start_time) for span in spans)
    end = max(float(span.end_time) for span in spans)
    total_duration = end - origin
    if not profile.duration_min_s <= total_duration <= profile.duration_max_s:
        raise ReferencePlanningError(
            f"{profile.profile_id} requires duration between "
            f"{profile.duration_min_s:.1f} and {profile.duration_max_s:.1f} seconds"
        )
    target = max(
        profile.shot_min,
        min(profile.shot_max, round(total_duration / _REFERENCE_SHOT_INTERVAL_SECONDS)),
    )
    if len(candidates) * profile.max_canonical_panel_uses < target:
        raise ReferencePlanningError(
            f"{profile.profile_id} cannot satisfy the panel reuse cap for "
            f"{target} shots"
        )

    beats = _coalesce_beats(director.analyze_story(spans), target_shots=target)
    shots = visual_scoring.plan_content_aware_scenes(
        beats,
        candidates,
        min_scene_seconds=profile.hold_min_s,
        max_scene_seconds=profile.emphasis_max_s,
        preferred_asset_ids_by_section=cited_asset_ids_by_section,
        max_asset_uses=profile.max_canonical_panel_uses,
    )
    if len(shots) != target:
        raise ReferencePlanningError(
            f"{profile.profile_id} planned {len(shots)} shots; expected {target}"
        )

    # With exactly the minimum viable panel pool, deterministic chronological
    # rotation is the only safe way to use every panel twice without exceeding
    # the cap. Citation-constrained plans retain the scorer's evidence order.
    if cited_asset_ids_by_section is None and len(candidates) * 2 == target:
        ordered = sorted(candidates, key=lambda candidate: (candidate.order_index, candidate.asset_id))
        for index, shot in enumerate(shots):
            candidate = ordered[index % len(ordered)]
            shot["asset_id"] = candidate.asset_id
            shot["source_family"] = candidate.source_family
            shot["visual_score"] = candidate.visual_score
            shot["semantic_score"] = candidate.semantic_score
            shot["visual_signature"] = candidate.features.visual_signature
            reasons = list(shot.get("alignment_reasons", ()))
            reasons.append("reference:deterministic_chronology")
            shot["alignment_reasons"] = reasons

    emphasis_indexes = _reference_emphasis_indexes(shots)
    section_durations = _reference_section_durations(beats, total_duration)
    shot_cursor = 0
    timeline_cursor = origin
    candidate_ids = {str(candidate.asset_id) for candidate in candidates}
    for section, section_duration in section_durations:
        section_shots = []
        while shot_cursor + len(section_shots) < len(shots) and shots[shot_cursor + len(section_shots)]["section"] == section:
            section_shots.append(shots[shot_cursor + len(section_shots)])
        if not section_shots:
            raise ReferencePlanningError(
                f"{profile.profile_id} lost section {section} during shot planning"
            )
        section_indexes = set(range(shot_cursor, shot_cursor + len(section_shots)))
        section_emphasis = emphasis_indexes & section_indexes
        durations = _reference_section_shot_durations(
            section_duration,
            len(section_shots),
            len(section_emphasis),
            profile,
            {index - shot_cursor for index in section_emphasis},
        )
        for shot_index, shot in enumerate(section_shots):
            start = round(timeline_cursor, 3)
            end_time = (
                end
                if shot_cursor + shot_index == len(shots) - 1
                else round(timeline_cursor + durations[shot_index], 3)
            )
            shot["start_time"] = start
            shot["end_time"] = end_time
            shot["transition"] = "none" if shot_cursor + shot_index == 0 else "cut"
            reasons = list(shot.get("alignment_reasons", ()))
            valid = (
                {
                    str(asset_id)
                    for asset_id in cited_asset_ids_by_section.get(section, ())
                    if str(asset_id) in candidate_ids
                }
                if cited_asset_ids_by_section
                else set()
            )
            if cited_asset_ids_by_section is not None:
                if valid and shot.get("asset_id") in valid:
                    reasons.append(f"citation_alignment:{shot['asset_id']}")
                elif valid:
                    anchor = sorted(valid)[0]
                    reasons.append(f"evidence_context_fallback:anchor:{anchor}")
                    reasons.extend(
                        citation_alignment_reasons_by_section.get(section, ())
                        if citation_alignment_reasons_by_section
                        else ()
                    )
                else:
                    reasons.extend(citation_alignment_reasons_by_section.get(section, ()) if citation_alignment_reasons_by_section else ())
                    reasons.append("evidence_fallback:unavailable")
            shot["alignment_reasons"] = sorted(set(reasons))
            timeline_cursor = end_time
        shot_cursor += len(section_shots)

    if cited_asset_ids_by_section is not None:
        for section, anchors in cited_asset_ids_by_section.items():
            valid = {
                str(asset_id)
                for asset_id in anchors
                if str(asset_id) in candidate_ids
            }
            if valid and not any(
                shot.get("section") == section and shot.get("asset_id") in valid
                for shot in shots
            ):
                raise ReferencePlanningError(
                    f"{profile.profile_id} could not place a cited anchor for section {section}"
                )

    positions: dict[str, list[int]] = {}
    for index, shot in enumerate(shots):
        asset_id = shot.get("asset_id")
        if not asset_id:
            raise ReferencePlanningError(f"{profile.profile_id} produced a shot without a panel")
        positions.setdefault(str(asset_id), []).append(index)
        if index and asset_id == shots[index - 1].get("asset_id"):
            raise ReferencePlanningError(
                f"{profile.profile_id} produced consecutive panel reuse"
            )
    for _asset_id, indexes in positions.items():
        if len(indexes) > profile.max_canonical_panel_uses:
            raise ReferencePlanningError(
                f"{profile.profile_id} exceeded the canonical panel reuse cap"
            )
        if len(indexes) == 2:
            first, second = (shots[indexes[0]], shots[indexes[1]])
            if (
                first.get("roi_label", ""),
                round(float(first.get("focus_x", 0.0)), 3),
                round(float(first.get("focus_y", 0.0)), 3),
            ) == (
                second.get("roi_label", ""),
                round(float(second.get("focus_x", 0.0)), 3),
                round(float(second.get("focus_y", 0.0)), 3),
            ):
                second["roi_label"] = f"{second.get('roi_label', 'roi')}_reuse_alt"
                second["focus_x"] = round(1.0 - float(second.get("focus_x", 0.5)), 3)
                second["focus_end_x"] = round(1.0 - float(second.get("focus_end_x", 0.5)), 3)
            second_reasons = list(second.get("alignment_reasons", ()))
            second_reasons.append(
                f"reuse_purpose:distinct_roi:{first.get('roi_label', 'roi')}->"
                f"{second.get('roi_label', 'roi')}"
            )
            second["alignment_reasons"] = sorted(set(second_reasons))

    durations = [shot["end_time"] - shot["start_time"] for shot in shots]
    normal = [duration for duration in durations if profile.hold_min_s <= duration <= profile.hold_max_s]
    emphasis = [duration for duration in durations if profile.emphasis_min_s <= duration <= profile.emphasis_max_s]
    if len(normal) + len(emphasis) != len(durations):
        raise ReferencePlanningError(f"{profile.profile_id} produced an unsupported shot duration")
    if len(normal) / len(durations) < profile.hold_ratio_min:
        raise ReferencePlanningError(f"{profile.profile_id} normal hold ratio is below the profile minimum")
    if len(normal) / len(durations) > profile.hold_ratio_max:
        raise ReferencePlanningError(f"{profile.profile_id} normal hold ratio exceeds the profile maximum")
    if len(emphasis) / len(durations) < profile.emphasis_ratio_min:
        raise ReferencePlanningError(f"{profile.profile_id} emphasis ratio is below the profile minimum")
    if len(emphasis) / len(durations) > profile.emphasis_ratio_max:
        raise ReferencePlanningError(f"{profile.profile_id} emphasis ratio exceeds the profile maximum")
    mean = sum(durations) / len(durations)
    if not profile.mean_shot_min_s <= mean <= profile.mean_shot_max_s:
        raise ReferencePlanningError(f"{profile.profile_id} mean shot duration is outside the profile band")
    _apply_reference_motion(shots, beats)
    return shots


def plan(
    spans: Iterable[object],
    candidates: list[object],
    profile: object | None = None,
    cited_asset_ids_by_section: Mapping[str, Iterable[str]] | None = None,
    citation_alignment_reasons_by_section: Mapping[str, Iterable[str]] | None = None,
) -> list[dict]:
    """Create a beat-aware, ROI-driven shot list before rendering."""
    span_list = list(spans)
    if profile is not None:
        return _plan_reference(
            span_list,
            candidates,
            profile,
            cited_asset_ids_by_section,
            citation_alignment_reasons_by_section,
        )
    beats = _coalesce_beats(director.analyze_story(span_list))
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


__all__ = ["ReferencePlanningError", "classify_source_text", "plan"]
