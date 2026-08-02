"""Story-level editorial direction before ROI and camera execution.

The Director owns beat boundaries, emotional intent, and whether a visual should
lead, sync with, or follow narration. It never scores panels or emits FFmpeg
filters. Shot Sequencer consumes these immutable beat decisions.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class StoryBeat:
    section: str
    text: str
    start_time: float
    end_time: float
    word_timings: list[dict]
    kind: str
    emotion: str
    camera_intent: str
    visual_timing: str
    composition: str = "rule_of_thirds"


_TRIGGER_GROUPS = {
    "action": {"fight", "fights", "fought", "battle", "battles", "battled", "charges", "chases", "jumps", "runs", "bertarung", "berlari", "mengejar"},
    "reveal": {"suddenly", "finally", "appeared", "appears", "revealed", "awakens", "muncul", "akhirnya", "ternyata"},
    "attack": {"attacks", "attacked", "strikes", "struck", "punches", "slashes", "menyerang", "menebas", "memukul"},
    "impact": {"hits", "hit", "impact", "crashes", "hantam", "menghantam"},
    "explosion": {"explodes", "explosion", "blast", "erupts", "meledak", "ledakan"},
    "victory": {"wins", "won", "defeated", "victory", "menang", "mengalahkan"},
    "thinking": {"thinks", "thought", "realizes", "wonders", "remembers", "berpikir", "menyadari"},
}

_INTENT = {
    "action": ("attack", "punch_zoom"),
    "dialogue": ("dialogue", "slow_drift"),
    "thinking": ("thinking", "slow_push_in"),
    "reveal": ("reveal", "push_in"),
    "attack": ("attack", "punch_zoom"),
    "impact": ("impact", "micro_shake"),
    "explosion": ("explosion", "impact_shake"),
    "victory": ("victory", "slow_pull_out"),
    "approach": ("approach", "slow_drift"),
    "suspense": ("suspense", "slow_push_in"),
    "neutral": ("neutral", "slow_drift"),
}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-zA-ZÀ-ÿ]+", text.lower())


def _kind_for(text: str, section: str) -> str:
    tokens = set(_words(text))
    for kind in ("explosion", "impact", "attack", "action", "victory", "reveal", "thinking"):
        if tokens & _TRIGGER_GROUPS[kind]:
            return kind
    if tokens & {"dialogue", "says", "said", "tells", "asks", "berkata"}:
        return "dialogue"
    if tokens & {"walks", "walked", "approaches", "toward", "menuju", "berjalan"}:
        return "approach"
    if tokens & {"quietly", "silently", "dark", "waits", "waiting", "diam"}:
        return "suspense"
    if section == "twist":
        return "reveal"
    if section == "cta":
        return "victory"
    return "neutral"


def _beat(section: str, text: str, start: float, end: float, timings: list[dict], kind: str, timing: str) -> StoryBeat:
    intent, _camera_curve = _INTENT[kind]
    emotion = {"reveal": "surprise", "attack": "urgency", "impact": "shock", "explosion": "chaos", "victory": "triumph", "suspense": "tension", "thinking": "reflection"}.get(kind, "neutral")
    composition = "negative_space" if kind in {"suspense", "approach"} else "rule_of_thirds"
    return StoryBeat(section, text, round(start, 3), round(end, 3), timings, intent, emotion, intent, timing, composition)


def analyze_span(span: object, block_end: float | None = None) -> list[StoryBeat]:
    """Split one narration span around timed dramatic events."""
    start = float(span.start_time)
    end = max(float(span.end_time), float(block_end or span.end_time))
    timings = list(getattr(span, "word_timings", []) or [])
    kind = _kind_for(str(span.text), str(span.section))
    events = [(float(t.get("start", start)), _kind_for(str(t.get("word", "")), str(span.section))) for t in timings if _kind_for(str(t.get("word", "")), str(span.section)) not in {"neutral", "approach", "suspense"}]
    if not events:
        return [_beat(str(span.section), str(span.text), start, end, timings, kind, "visual_during")]
    beats: list[StoryBeat] = []
    cursor = start
    for event_start, event_kind in events:
        event_start = max(cursor, min(event_start, end))
        if event_start - cursor >= 0.45:
            before = [t for t in timings if cursor <= float(t.get("start", 0)) < event_start]
            beats.append(_beat(str(span.section), str(span.text), cursor, event_start, before, "suspense" if event_kind == "reveal" else kind, "visual_before"))
        event_end = min(end, max(event_start + 0.7, event_start + 1.15))
        event_timings = [t for t in timings if event_start <= float(t.get("start", 0)) < event_end]
        beats.append(_beat(str(span.section), str(span.text), event_start, event_end, event_timings, event_kind, "visual_sync"))
        cursor = event_end
    if end - cursor >= 0.35:
        beats.append(_beat(str(span.section), str(span.text), cursor, end, [t for t in timings if float(t.get("start", 0)) >= cursor], kind, "visual_after"))
    return beats


def analyze_story(spans: Iterable[object]) -> list[StoryBeat]:
    """Plan all story beats before Shot Sequencer or Camera Planner runs."""
    source = list(spans)
    output: list[StoryBeat] = []
    for index, span in enumerate(source):
        next_start = source[index + 1].start_time if index + 1 < len(source) else span.end_time
        output.extend(analyze_span(span, max(float(span.end_time), float(next_start))))
    return output


__all__ = ["StoryBeat", "analyze_span", "analyze_story"]

# ponytail: deterministic beat parser ceiling; replace only this module with an
# LLM director later while keeping StoryBeat's contract stable.
