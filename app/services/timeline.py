"""Timeline and subtitle construction (PRD FR-06, FR-07).

The timeline is derived from the voice-over, never the reverse: audio segment
durations define when each visual must appear, so the two can never drift.
When the user changes a scene's length the audio stays authoritative and the
remaining scenes are redistributed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache
from typing import Any

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
    source_family: str = ""
    focus_x: float = 0.5
    focus_y: float = 0.4
    focus_end_x: float = 0.5
    focus_end_y: float = 0.4
    roi_label: str = ""
    camera_curve: str = "slow_push_in"
    motion_mode: str = "hold"
    motion_intensity: str = "low"
    motion_reason: str = ""
    camera_intent: str = "neutral"
    narration_timing: str = "narration_lead"
    effect: str = "kenburns_in"
    disabled_effects: list[str] = field(default_factory=list)
    overlay_text: str = ""
    transition: str = "fade"
    alignment_score: float = 0.0
    alignment_reasons: list[str] = field(default_factory=list)
    rejected_candidates: list[dict] = field(default_factory=list)
    visual_signature: str = ""
    panel_region_id: str | None = None
    panel_id: str = ""
    panel_bounds: tuple[int, int, int, int] | None = None
    visual_evidence: dict[str, Any] | None = None
    source_asset_checksum: str = ""

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
    dramatic_events: list[dict] = field(default_factory=list)
    impact_lock: bool = False

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

_PUNCTUATION_ONLY = re.compile(r"^[\W_]+$", re.UNICODE)

_CAPTION_MIN_WORDS = 4
_CAPTION_MAX_WORDS = 7
# Function words that usually need a following lexical word at a caption boundary.
_DANGLING_WORDS = frozenset({
    # Articles and determiners.
    "a", "an", "the", "this", "that", "these", "those", "some", "any",
    "each", "every", "either", "neither", "another", "all", "both", "few",
    "many", "more", "most", "much", "my", "your", "his", "her", "its",
    "our", "their",
    # Prepositions.
    "of", "to", "in", "on", "at", "for", "from", "by", "with", "as",
    "into", "onto", "over", "under", "about", "against", "among", "around",
    "behind", "between", "during", "except", "through", "toward", "towards",
    "upon", "without",
    # Coordinating and subordinating conjunctions.
    "and", "or", "but", "nor", "yet", "so", "if", "when", "while",
    "because", "although", "though", "unless", "until", "whether", "than",
    # Negation, auxiliaries and modal verbs.
    "am", "is", "are", "was", "were", "be", "been", "being", "do", "does",
    "did", "have", "has", "had", "can", "could", "may", "might", "must",
    "shall", "should", "will", "would", "not",
    # Personal and relative pronouns.
    "i", "me", "you", "he", "him", "she", "it", "we", "us", "they",
    "them", "who", "whom", "which", "whose", "what",
})


def is_caption_boundary(word: str) -> bool:
    """Return whether a function word should not close a caption when avoidable."""
    normalized = re.sub(r"[^a-z'-]+$", "", str(word).lower())
    return normalized in _DANGLING_WORDS


def _is_dangling(word: str) -> bool:
    return is_caption_boundary(word)


def normalize_display_text(text: str) -> str:
    """Return one uppercase, punctuation-free display representation."""
    compact = " ".join(str(text or "").split())
    value = "".join(character for character in compact if character.isalnum() or character.isspace())
    return " ".join(value.upper().split())


def spoken_tokens(text: str) -> list[str]:
    """Words eligible for karaoke; punctuation remains attached to words."""
    return [
        token
        for token in re.findall(r"\S+", str(text or ""))
        if normalize_display_text(token)
    ]



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


def _caption_fits(text: str, max_chars: int, max_lines: int) -> bool:
    return len(wrap_caption(text, max_chars)) <= max_lines


def _rebalance_groups(
    groups: list[list[str]], max_chars: int, max_lines: int,
) -> list[list[str]]:
    """Keep groups in the 4-7 word range when the source permits it."""
    groups = [group for group in groups if group]
    index = 0
    while index < len(groups) - 1:
        current = groups[index]
        following = groups[index + 1]
        if len(current) < _CAPTION_MIN_WORDS:
            combined = current + following
            if (
                len(combined) <= _CAPTION_MAX_WORDS
                and _caption_fits(" ".join(combined), max_chars, max_lines)
            ):
                groups[index:index + 2] = [combined]
                continue
            needed = _CAPTION_MIN_WORDS - len(current)
            if len(following) - needed >= _CAPTION_MIN_WORDS:
                current_candidate = current + following[:needed]
                following_candidate = following[needed:]
                if (
                    _caption_fits(" ".join(current_candidate), max_chars, max_lines)
                    and _caption_fits(" ".join(following_candidate), max_chars, max_lines)
                ):
                    groups[index:index + 2] = [current_candidate, following_candidate]
        index += 1

    if len(groups) > 1 and len(groups[-1]) < _CAPTION_MIN_WORDS:
        previous = groups[-2]
        final = groups[-1]
        combined = previous + final
        if (
            len(combined) <= _CAPTION_MAX_WORDS
            and _caption_fits(" ".join(combined), max_chars, max_lines)
        ):
            groups[-2:] = [combined]
        else:
            needed = _CAPTION_MIN_WORDS - len(final)
            if len(previous) - needed >= _CAPTION_MIN_WORDS:
                previous_candidate = previous[:-needed]
                final_candidate = previous[-needed:] + final
                if (
                    _caption_fits(" ".join(previous_candidate), max_chars, max_lines)
                    and _caption_fits(" ".join(final_candidate), max_chars, max_lines)
                ):
                    groups[-2:] = [previous_candidate, final_candidate]

    # Shift a function word when it would leave a semantic boundary dangling,
    # provided both resulting groups remain readable.
    for index in range(len(groups) - 1):
        current, following = groups[index], groups[index + 1]
        if not current:
            continue
        last = re.sub(r"[^a-z'-]+$", "", current[-1].lower())
        if last not in _DANGLING_WORDS:
            continue
        max_take = min(_CAPTION_MAX_WORDS - len(current), len(following) - _CAPTION_MIN_WORDS)
        for take in range(1, max_take + 1):
            candidate = current + following[:take]
            candidate_last = re.sub(r"[^a-z'-]+$", "", candidate[-1].lower())
            if (
                candidate_last not in _DANGLING_WORDS
                and _caption_fits(" ".join(candidate), max_chars, max_lines)
                and _caption_fits(" ".join(following[take:]), max_chars, max_lines)
            ):
                groups[index] = candidate
                groups[index + 1] = following[take:]
                break
    return groups


def _greedy_groups(
    words: list[str], max_chars: int, max_lines: int,
) -> list[list[str]]:
    groups: list[list[str]] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if current and (
            len(current) >= _CAPTION_MAX_WORDS
            or not _caption_fits(candidate, max_chars, max_lines)
        ):
            groups.append(current)
            current = [word]
        else:
            current.append(word)
    if current:
        groups.append(current)
    return _rebalance_groups(groups, max_chars, max_lines)


def _caption_boundary_score(word: str) -> int:
    if re.search(r"[.!?][\"')\]]*$", str(word).strip()):
        return 4
    if re.search(r"[,;:][\"')\]]*$", str(word).strip()):
        return 1
    return 0


def _optimal_groups(
    words: list[str], max_chars: int, max_lines: int,
) -> list[list[str]]:
    """Partition words while avoiding function-word endings and favoring clauses."""
    if not words:
        return []
    if len(words) < _CAPTION_MIN_WORDS:
        return [words]

    target_groups = max(1, round(len(words) / 6.25))

    @cache
    def options(start: int) -> dict[int, tuple[int, float, tuple[tuple[str, ...], ...]]]:
        if start == len(words):
            return {0: (0, 0.0, ())}
        result: dict[int, tuple[int, float, tuple[tuple[str, ...], ...]]] = {}
        for end in range(
            start + _CAPTION_MIN_WORDS,
            min(len(words), start + _CAPTION_MAX_WORDS) + 1,
        ):
            group = words[start:end]
            if not _caption_fits(" ".join(group), max_chars, max_lines):
                continue
            for tail_count, (tail_bad, tail_score, tail_path) in options(end).items():
                count = tail_count + 1
                candidate = (
                    tail_bad + int(is_caption_boundary(group[-1])),
                    tail_score
                    + _caption_boundary_score(group[-1])
                    - abs(len(group) - 6) * 0.1,
                    (tuple(group), *tail_path),
                )
                previous = result.get(count)
                if previous is None or (
                    candidate[0] < previous[0]
                    or candidate[0] == previous[0] and candidate[1] > previous[1]
                ):
                    result[count] = candidate
        return result

    solutions = options(0)
    if not solutions:
        return _greedy_groups(words, max_chars, max_lines)
    _, (_, _, path) = min(
        solutions.items(),
        key=lambda item: (item[1][0], abs(item[0] - target_groups), -item[1][1]),
    )
    return [list(group) for group in path]


def _group_words(
    words: list[str], max_chars: int, max_lines: int,
) -> list[list[str]]:
    return _optimal_groups(words, max_chars, max_lines)


def _group_timings(
    timings: list[dict], max_chars: int, max_lines: int,
) -> list[list[dict]]:
    groups = _group_words(
        [str(timing.get("word", "")) for timing in timings],
        max_chars,
        max_lines,
    )
    result: list[list[dict]] = []
    cursor = 0
    for group in groups:
        result.append(timings[cursor:cursor + len(group)])
        cursor += len(group)
    return result


def _rebalance_cues(
    cues: list[CueSpec], max_chars: int, max_lines: int,
) -> list[CueSpec]:
    """Repair short boundary cues that occur when spans meet."""
    index = 0
    while index < len(cues) - 1:
        left, right = cues[index], cues[index + 1]
        left_words = str(left.text).split()
        right_words = str(right.text).split()
        if len(left_words) < _CAPTION_MIN_WORDS:
            combined = left_words + right_words
            if (
                len(combined) <= _CAPTION_MAX_WORDS
                and _caption_fits(" ".join(combined), max_chars, max_lines)
            ):
                left.text = normalize_display_text(" ".join(combined))
                left.end_time = right.end_time
                cues.pop(index + 1)
                continue
        index += 1
    if len(cues) > 1 and len(str(cues[-1].text).split()) < _CAPTION_MIN_WORDS:
        left, right = cues[-2], cues[-1]
        left_words, right_words = str(left.text).split(), str(right.text).split()
        combined = left_words + right_words
        if (
            len(combined) <= _CAPTION_MAX_WORDS
            and _caption_fits(" ".join(combined), max_chars, max_lines)
        ):
            left.text = normalize_display_text(" ".join(combined))
            left.end_time = right.end_time
            cues.pop()
    for index, cue in enumerate(cues):
        cue.order_index = index
    return cues


def build_cues(
    spans: list[AudioSpan],
    max_chars: int = MAX_SUBTITLE_CHARS_PER_LINE,
    max_lines: int = MAX_SUBTITLE_LINES,
    min_cue_seconds: float = 0.45,
    media_duration: float | None = None,
) -> list[CueSpec]:
    """Build one punctuation-free display cue for each spoken word."""
    cues: list[CueSpec] = []
    order = 0

    for span in spans:
        timings = list(span.word_timings or [])
        if timings:
            for timing in timings:
                word = str(timing.get("word", ""))
                display = normalize_display_text(word)
                if not display:
                    continue
                cues.append(
                    CueSpec(
                        order_index=order,
                        text=display,
                        start_time=float(timing.get("start", span.start_time)),
                        end_time=float(timing.get("end", span.end_time)),
                    )
                )
                order += 1
            continue

        tokens = spoken_tokens(span.text)
        if not tokens:
            continue
        slice_len = span.duration / len(tokens)
        for index, token in enumerate(tokens):
            display = normalize_display_text(token)
            if not display:
                continue
            cues.append(
                CueSpec(
                    order_index=order,
                    text=display,
                    start_time=round(span.start_time + index * slice_len, 3),
                    end_time=round(span.start_time + (index + 1) * slice_len, 3),
                )
            )
            order += 1

    for index, cue in enumerate(cues):
        if cue.duration < min_cue_seconds:
            wanted = cue.start_time + min_cue_seconds
            limit = cues[index + 1].start_time if index + 1 < len(cues) else wanted
            cue.end_time = max(cue.end_time, min(wanted, limit))
        if media_duration is not None:
            cue.end_time = min(cue.end_time, media_duration)
            cue.start_time = min(max(0.0, cue.start_time), media_duration)
        else:
            cue.start_time = max(0.0, cue.start_time)
        cue.end_time = round(max(cue.start_time, cue.end_time), 3)
    return cues


def _chunk_text(
    text: str,
    max_chars: int = MAX_SUBTITLE_CHARS_PER_LINE,
    max_lines: int = MAX_SUBTITLE_LINES,
) -> list[str]:
    """Split text into readable 4-7 word chunks."""
    words = re.findall(r"\S+", text)
    return [" ".join(group) for group in _group_words(words, max_chars, max_lines)]

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
    for cue in cues:
        display = normalize_display_text(cue.text)
        if not display or cue.end_time <= cue.start_time:
            continue
        lines = wrap_caption(display, max_chars)
        i = len(blocks) + 1
        blocks.append(
            f"{i}\n{_srt_timestamp(cue.start_time)} --> {_srt_timestamp(cue.end_time)}\n"
            + "\n".join(lines)
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def validate_cues(
    cues: list[CueSpec],
    max_chars: int,
    max_lines: int,
    media_duration: float | None = None,
) -> list[dict]:
    """Validate one-word, display-safe cues and their timing boundaries."""
    warnings: list[dict] = []

    for cue in cues:
        raw = str(cue.text or "")
        normalized = normalize_display_text(raw)
        words = normalized.split()
        if not normalized:
            warnings.append(
                {
                    "code": "subtitle.display_empty",
                    "severity": "error",
                    "message": f"Cue {cue.order_index + 1} contains no display word.",
                }
            )
            continue
        if len(words) != 1:
            warnings.append(
                {
                    "code": "subtitle.display_multiword",
                    "severity": "error",
                    "message": f"Cue {cue.order_index + 1} must contain exactly one word.",
                }
            )
        if raw != raw.upper():
            warnings.append(
                {
                    "code": "subtitle.display_not_uppercase",
                    "severity": "error",
                    "message": f"Cue {cue.order_index + 1} is not uppercase.",
                }
            )
        if any(not (character.isalnum() or character.isspace()) for character in raw):
            warnings.append(
                {
                    "code": "subtitle.display_punctuation",
                    "severity": "error",
                    "message": f"Cue {cue.order_index + 1} contains punctuation or symbols.",
                }
            )
        lines = wrap_caption(normalized, max_chars)
        if len(lines) > max_lines:
            warnings.append(
                {
                    "code": "subtitle.too_many_lines",
                    "severity": "error",
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
        if cue.start_time < 0.0 or cue.end_time < 0.0:
            warnings.append(
                {
                    "code": "subtitle.before_media",
                    "severity": "error",
                    "message": f"Cue {cue.order_index + 1} starts before media.",
                }
            )
        if cue.end_time <= cue.start_time:
            warnings.append(
                {
                    "code": "subtitle.non_positive_timing",
                    "severity": "error",
                    "message": f"Cue {cue.order_index + 1} has non-positive timing.",
                }
            )
        if media_duration is not None and (
            cue.start_time > media_duration + 0.01
            or cue.end_time > media_duration + 0.01
        ):
            warnings.append(
                {
                    "code": "subtitle.after_media",
                    "severity": "error",
                    "message": f"Cue {cue.order_index + 1} ends after media duration.",
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
