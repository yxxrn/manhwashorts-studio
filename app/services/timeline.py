"""Timeline and subtitle construction (PRD FR-06, FR-07).

The timeline is derived from the voice-over, never the reverse: audio segment
durations define when each visual must appear, so the two can never drift.
When the user changes a scene's length the audio stays authoritative and the
remaining scenes are redistributed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.constants import (
    MAX_SUBTITLE_CHARS_PER_LINE,
    MAX_SUBTITLE_LINES,
    ScriptSection,
)


@dataclass
class SceneSpec:
    """A planned visual scene before it becomes a DB row."""

    order_index: int
    section: str
    start_time: float
    end_time: float
    asset_id: str | None = None
    focus_x: float = 0.5
    focus_y: float = 0.4
    effect: str = "kenburns_in"
    overlay_text: str = ""
    transition: str = "fade"

    @property
    def duration(self) -> float:
        return round(max(0.0, self.end_time - self.start_time), 3)


@dataclass
class CueSpec:
    """A subtitle cue before it becomes a DB row."""

    order_index: int
    text: str
    start_time: float
    end_time: float

    @property
    def duration(self) -> float:
        return round(max(0.0, self.end_time - self.start_time), 3)


# Effects alternate so consecutive panels do not feel static. The extra
# directional pushes matter on webtoon strips: a still panel now has a clear
# reading vector instead of only zooming around its centre.
_EFFECT_CYCLE = (
    "kenburns_in",
    "pan_right",
    "push_up",
    "kenburns_out",
    "pan_left",
    "push_down",
    "pan_diagonal",
)

# The hook needs the strongest visual, so it always gets a push-in.
_SECTION_EFFECT = {
    ScriptSection.HOOK.value: "kenburns_in",
    ScriptSection.CTA.value: "static",
}


@dataclass
class AudioSpan:
    """Timing of one narration segment on the master timeline."""

    section: str
    text: str
    start_time: float
    end_time: float
    word_timings: list[dict] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return round(max(0.0, self.end_time - self.start_time), 3)


def lay_out_audio(
    segments: list[tuple[str, str, float, list[dict]]],
    gap: float = 0.18,
) -> list[AudioSpan]:
    """Place ``(section, text, duration, word_timings)`` tuples end to end.

    Word timings arrive relative to their own clip, so they are shifted onto
    the master timeline here.
    """
    spans: list[AudioSpan] = []
    cursor = 0.0
    for i, (section, text, duration, timings) in enumerate(segments):
        start = cursor
        end = start + max(0.0, duration)
        shifted = [
            {
                "word": t["word"],
                "start": round(t["start"] + start, 3),
                "end": round(t["end"] + start, 3),
            }
            for t in (timings or [])
        ]
        spans.append(
            AudioSpan(
                section=section,
                text=text,
                start_time=round(start, 3),
                end_time=round(end, 3),
                word_timings=shifted,
            )
        )
        cursor = end + (gap if i < len(segments) - 1 else 0.0)
    return spans


def plan_scenes(
    spans: list[AudioSpan],
    asset_ids: list[str],
    min_scene_seconds: float = 2.0,
    max_scene_seconds: float = 6.0,
) -> list[SceneSpec]:
    """Assign available images across the narration timeline.

    Long beats are split into multiple scenes so the video keeps moving; short
    beats get one scene. Images cycle if there are fewer images than slots,
    which is preferable to leaving black frames.
    """
    if not spans:
        return []

    scenes: list[SceneSpec] = []
    order = 0
    asset_count = len(asset_ids)

    for span_index, span in enumerate(spans):
        # Absorb the silence between beats into this span's visuals. Without
        # this the video is shorter than the narration by the sum of the gaps,
        # and ffmpeg's -shortest clips the end of the last line.
        next_start = (
            spans[span_index + 1].start_time if span_index + 1 < len(spans) else span.end_time
        )
        block_end = max(span.end_time, next_start)
        duration = round(block_end - span.start_time, 3)
        if duration <= 0:
            continue

        # How many scenes this beat should be split into.
        slots = max(1, min(4, int(duration // max_scene_seconds) + 1))
        if duration / slots < min_scene_seconds and slots > 1:
            slots = max(1, int(duration // min_scene_seconds))
        slots = max(1, slots)

        slot_duration = duration / slots
        for s in range(slots):
            start = span.start_time + s * slot_duration
            end = span.start_time + (s + 1) * slot_duration
            asset_id = asset_ids[order % asset_count] if asset_count else None
            effect = _SECTION_EFFECT.get(span.section, _EFFECT_CYCLE[order % len(_EFFECT_CYCLE)])
            scenes.append(
                SceneSpec(
                    order_index=order,
                    section=span.section,
                    start_time=round(start, 3),
                    end_time=round(end, 3),
                    asset_id=asset_id,
                    effect=effect,
                    transition="fade" if order > 0 else "none",
                )
            )
            order += 1

    # Close any gap at the tail so video length matches audio length.
    if scenes:
        scenes[-1].end_time = max(scenes[-1].end_time, spans[-1].end_time)
    return scenes


def redistribute(scenes: list[SceneSpec], total_duration: float) -> list[SceneSpec]:
    """Rescale scene boundaries to exactly cover ``total_duration``.

    Called after a user edits scene lengths or when audio is regenerated.
    """
    if not scenes or total_duration <= 0:
        return scenes
    span = scenes[-1].end_time - scenes[0].start_time
    if span <= 0:
        return scenes
    factor = total_duration / span
    cursor = 0.0
    for scene in scenes:
        length = scene.duration * factor
        scene.start_time = round(cursor, 3)
        scene.end_time = round(cursor + length, 3)
        cursor = scene.end_time
    scenes[-1].end_time = round(total_duration, 3)
    return scenes


# --- subtitles -------------------------------------------------------------


def wrap_caption(text: str, max_chars: int = MAX_SUBTITLE_CHARS_PER_LINE) -> list[str]:
    """Greedy wrap into short lines that read well on a phone."""
    words = re.findall(r"\S+", text)
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def build_cues(
    spans: list[AudioSpan],
    max_chars: int = MAX_SUBTITLE_CHARS_PER_LINE,
    max_lines: int = MAX_SUBTITLE_LINES,
    min_cue_seconds: float = 0.7,
) -> list[CueSpec]:
    """Chunk narration into cues timed from real word timings.

    Falls back to proportional splitting when a provider returns no timings.
    """
    cues: list[CueSpec] = []
    order = 0

    def fits(text: str) -> bool:
        """True if ``text`` wraps within the line limit.

        Checking the real wrap is necessary: a 56-character string can still
        need three 28-character lines once words break unevenly.
        """
        return len(wrap_caption(text, max_chars)) <= max_lines

    for span in spans:
        timings = span.word_timings
        if not timings:
            # No word data: split text evenly across the span.
            chunks = _chunk_text(span.text, max_chars, max_lines)
            if not chunks:
                continue
            slice_len = span.duration / len(chunks)
            for i, chunk in enumerate(chunks):
                cues.append(
                    CueSpec(
                        order_index=order,
                        text=chunk,
                        start_time=round(span.start_time + i * slice_len, 3),
                        end_time=round(span.start_time + (i + 1) * slice_len, 3),
                    )
                )
                order += 1
            continue

        current: list[dict] = []
        for timing in timings:
            candidate = " ".join([*(t["word"] for t in current), timing["word"]])
            if current and not fits(candidate):
                cues.append(
                    CueSpec(
                        order_index=order,
                        text=" ".join(t["word"] for t in current),
                        start_time=current[0]["start"],
                        end_time=current[-1]["end"],
                    )
                )
                order += 1
                current = [timing]
            else:
                current.append(timing)
        if current:
            cues.append(
                CueSpec(
                    order_index=order,
                    text=" ".join(t["word"] for t in current),
                    start_time=current[0]["start"],
                    end_time=current[-1]["end"],
                )
            )
            order += 1

    # Enforce a readable minimum, without overlapping the next cue. The
    # max() guard matters: if the next cue already starts earlier than this
    # one ends, naively clamping to it would produce a negative duration.
    for i, cue in enumerate(cues):
        if cue.duration < min_cue_seconds:
            wanted = cue.start_time + min_cue_seconds
            limit = cues[i + 1].start_time if i + 1 < len(cues) else wanted
            cue.end_time = round(max(cue.end_time, min(wanted, limit)), 3)
    return cues


def _chunk_text(
    text: str,
    max_chars: int = MAX_SUBTITLE_CHARS_PER_LINE,
    max_lines: int = MAX_SUBTITLE_LINES,
) -> list[str]:
    """Split text into chunks that each wrap within ``max_lines`` lines."""
    words = re.findall(r"\S+", text)
    chunks: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(wrap_caption(candidate, max_chars)) > max_lines:
            chunks.append(current)
            current = word
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _srt_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, rem = divmod(int(seconds), 3600)
    minutes, secs = divmod(rem, 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis == 1000:  # rounding carry
        secs += 1
        millis = 0
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def to_srt(cues: list[CueSpec], max_chars: int = MAX_SUBTITLE_CHARS_PER_LINE) -> str:
    """Serialise cues as SRT (FR-07 export)."""
    blocks: list[str] = []
    for i, cue in enumerate(cues, start=1):
        lines = wrap_caption(cue.text, max_chars)
        blocks.append(
            f"{i}\n{_srt_timestamp(cue.start_time)} --> {_srt_timestamp(cue.end_time)}\n"
            + "\n".join(lines)
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def validate_cues(cues: list[CueSpec], max_chars: int, max_lines: int) -> list[dict]:
    """Warn about cues that break the readability rules."""
    warnings: list[dict] = []
    for cue in cues:
        lines = wrap_caption(cue.text, max_chars)
        if len(lines) > max_lines:
            warnings.append(
                {
                    "code": "subtitle.too_many_lines",
                    "severity": "warning",
                    "message": f"Cue {cue.order_index + 1} needs {len(lines)} lines "
                    f"(limit {max_lines}): '{cue.text[:40]}...'",
                }
            )
        if cue.duration < 0.4:
            warnings.append(
                {
                    "code": "subtitle.too_fast",
                    "severity": "warning",
                    "message": f"Cue {cue.order_index + 1} shows for only "
                    f"{cue.duration:.2f}s, too fast to read.",
                }
            )
    for a, b in zip(cues, cues[1:], strict=False):
        if b.start_time < a.end_time - 0.01:
            warnings.append(
                {
                    "code": "subtitle.overlap",
                    "severity": "error",
                    "message": f"Cues {a.order_index + 1} and {b.order_index + 1} overlap.",
                }
            )
    return warnings
