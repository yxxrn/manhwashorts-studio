"""Shared sentence-held word-karaoke contracts for preview and production renders."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence

from app.services.timeline import normalize_display_text, wrap_caption

SUBTITLE_CONTRACT_VERSION = "sentence_chunked_word_karaoke_v2"
# Barber Chop at the fixed 77px phone-readable size needs a narrower logical
# line budget than a character-only 30-column estimate.  The production ASS
# builder also checks rendered font width before encoding.
CAPTION_MAX_CHARS = 22
CAPTION_MAX_LINES = 2
CAPTION_ACTIVE_SCALE = 1.08
CAPTION_FONT_HEIGHT_RATIO = 0.04
CAPTION_SAFE_MARGIN_PX = 120
CAPTION_MIN_CHUNK_WORDS = 2
# Fast but authoritative neural-TTS timings can place a two-word display chunk
# around 0.7s. Keep a 0.65s floor so orphan-avoidance can rebalance 2+2 words
# without fabricating timing or changing the spoken narration.
CAPTION_MIN_CHUNK_DURATION_SECONDS = 0.65
SEMANTIC_BREAK_WORDS = frozenset(
    {
        "AND",
        "ACTION",
        "AS",
        "BEFORE",
        "BUT",
        "DESTRUCTIVE",
        "IF",
        "MEANWHILE",
        "SO",
        "THEN",
        "WHEN",
        "WHILE",
        "YET",
    }
)


def _karaoke_types():
    # Keep the canonical dataclasses in render.py for import compatibility;
    # this module owns only the shared timing/chunking behavior.
    from app.services.render import KaraokeSentenceGroup, KaraokeWord

    return KaraokeSentenceGroup, KaraokeWord


def _caption_partition_score(
    chunks: tuple[tuple[object, ...], ...],
    pause_boundaries: frozenset[int],
) -> tuple[int, int, float, tuple[int, ...]]:
    boundaries: list[int] = []
    position = 0
    semantic_penalty = 0
    for index, chunk in enumerate(chunks[:-1]):
        position += len(chunk)
        boundaries.append(position)
        next_word = getattr(chunks[index + 1][0], "text", "")
        if position not in pause_boundaries and next_word not in SEMANTIC_BREAK_WORDS:
            semantic_penalty += 1
    target_size = sum(len(chunk) for chunk in chunks) / len(chunks)
    balance_penalty = sum(abs(len(chunk) - target_size) for chunk in chunks)
    return semantic_penalty, len(chunks), balance_penalty, tuple(boundaries)


def caption_lines_fit(words: Sequence[object]) -> bool:
    lines = sentence_group_lines(words)
    return len(lines) <= CAPTION_MAX_LINES and (
        len(lines) == 1 or all(len(line.split()) >= 2 for line in lines)
    )


def sentence_group_lines(
    words: Sequence[object],
    *,
    max_chars: int | None = None,
) -> list[str]:
    """Return deterministic display lines without changing spoken text."""
    if max_chars is None:
        max_chars = CAPTION_MAX_CHARS
    return wrap_caption(
        " ".join(str(getattr(word, "text", "")) for word in words),
        max_chars,
    )


def _split_sentence_words(
    words: tuple[object, ...],
    pause_boundaries: frozenset[int] = frozenset(),
) -> tuple[tuple[object, ...], ...]:
    """Partition an overflowing sentence at stable semantic/pause boundaries."""
    if caption_lines_fit(words):
        return (words,)

    memo: dict[int, tuple[tuple[object, ...], ...] | None] = {}

    def solve(start: int) -> tuple[tuple[object, ...], ...] | None:
        if start == len(words):
            return ()
        if start in memo:
            return memo[start]
        candidates: list[tuple[tuple[object, ...], ...]] = []
        for end in range(start + CAPTION_MIN_CHUNK_WORDS, len(words) + 1):
            remainder = len(words) - end
            if remainder == 1:
                continue
            chunk = words[start:end]
            if not caption_lines_fit(chunk):
                continue
            if (
                getattr(chunk[-1], "end_time", 0.0)
                - getattr(chunk[0], "start_time", 0.0)
                < CAPTION_MIN_CHUNK_DURATION_SECONDS
            ):
                continue
            tail = solve(end)
            if tail is not None:
                candidates.append((chunk,) + tail)
        local_pause_boundaries = frozenset(
            boundary - start for boundary in pause_boundaries if boundary >= start
        )
        result = (
            min(
                candidates,
                key=lambda candidate: _caption_partition_score(
                    candidate, local_pause_boundaries
                ),
            )
            if candidates
            else None
        )
        memo[start] = result
        return result

    partition = solve(0)
    if partition is None:
        raise ValueError(
            "subtitle.overflow: sentence cannot be chunked within the two-line budget"
        )
    return partition


def _chunk_sentence_group(
    group: object,
    pause_boundaries: frozenset[int] = frozenset(),
) -> tuple[object, ...]:
    KaraokeSentenceGroup, _KaraokeWord = _karaoke_types()
    partitions = _split_sentence_words(tuple(group.words), pause_boundaries)
    if len(partitions) == 1:
        return (group,)
    return tuple(
        KaraokeSentenceGroup(
            group_id=f"{group.group_id}-chunk-{index}",
            words=tuple(chunk),
            start_time=chunk[0].start_time,
            end_time=chunk[-1].end_time,
        )
        for index, chunk in enumerate(partitions, start=1)
    )


def _timing_value(cue: Mapping[str, object], *keys: str) -> float:
    for key in keys:
        if key in cue:
            try:
                value = float(cue[key])
            except (TypeError, ValueError):
                break
            if not math.isfinite(value):
                break
            return value
    raise ValueError("subtitle.word_timing_invalid: cue timing is missing or invalid")


def build_sentence_caption_groups(
    spoken_text: str,
    timed_cues: Sequence[Mapping[str, object]],
    *,
    group_prefix: str = "sentence",
) -> tuple[object, ...]:
    """Build deterministic sentence/chunk groups from provider word timings.

    The input spoken text is never rewritten. Display normalization is applied
    only to the independently constructed ``KaraokeWord`` values.
    """
    KaraokeSentenceGroup, KaraokeWord = _karaoke_types()
    raw_tokens = re.findall(r"\S+", str(spoken_text))
    if not raw_tokens or not timed_cues:
        raise ValueError("subtitle.word_timing_missing: sentence cues are required")
    sentence_end_indexes = {
        index
        for index, token in enumerate(raw_tokens)
        if re.search(r"[.!?][\"')\]]*$", token)
    }
    expected_words = [normalize_display_text(token) for token in raw_tokens]
    if not all(expected_words):
        raise ValueError(
            "subtitle.word_timing_missing: punctuation-only spoken tokens are not timed"
        )
    words: list[object] = []
    indexed_cues: list[tuple[int, Mapping[str, object], object]] = []
    previous_index = -1
    for fallback_index, cue in enumerate(timed_cues):
        if not isinstance(cue, Mapping):
            raise ValueError("subtitle.word_timing_invalid: cue fields are invalid")
        try:
            spoken_index = int(cue.get("spoken_token_index", fallback_index))
            raw_word = str(cue.get("text", cue.get("word", cue.get("display_text", ""))))
            display_word = normalize_display_text(raw_word)
            start_time = _timing_value(cue, "start_s", "start")
            end_time = _timing_value(cue, "end_s", "end")
            if spoken_index <= previous_index or not 0 <= spoken_index < len(raw_tokens):
                raise ValueError("subtitle.word_timing_invalid: cue order is invalid")
            if display_word != expected_words[spoken_index]:
                raise ValueError("subtitle.word_timing_missing: cues do not cover display words")
            word = KaraokeWord(display_word, start_time, end_time)
        except ValueError:
            raise
        except (TypeError, KeyError):
            raise ValueError("subtitle.word_timing_invalid: cue fields are invalid") from None
        previous_index = spoken_index
        indexed_cues.append((spoken_index, cue, word))
        words.append(word)
    if [word.text for word in words] != expected_words:
        raise ValueError("subtitle.word_timing_missing: cues do not cover display words")

    sentence_groups: list[tuple[object, frozenset[int]]] = []
    current: list[object] = []
    current_pause_boundaries: set[int] = set()
    for spoken_index, _cue, word in indexed_cues:
        current.append(word)
        if re.search(r"[,;:]", raw_tokens[spoken_index]):
            current_pause_boundaries.add(len(current))
        if spoken_index in sentence_end_indexes:
            sentence_groups.append(
                (
                    KaraokeSentenceGroup(
                        group_id=f"{group_prefix}-{len(sentence_groups) + 1}",
                        words=tuple(current),
                        start_time=current[0].start_time,
                        end_time=current[-1].end_time,
                    ),
                    frozenset(current_pause_boundaries),
                )
            )
            current = []
            current_pause_boundaries = set()
    if current:
        sentence_groups.append(
            (
                KaraokeSentenceGroup(
                    group_id=f"{group_prefix}-{len(sentence_groups) + 1}",
                    words=tuple(current),
                    start_time=current[0].start_time,
                    end_time=current[-1].end_time,
                ),
                frozenset(current_pause_boundaries),
            )
        )
    if not sentence_groups or len(sentence_groups) != len(sentence_end_indexes):
        raise ValueError(
            "subtitle.sentence_boundary_missing: sentence punctuation is not covered"
        )
    return tuple(
        chunk
        for group, pause_boundaries in sentence_groups
        for chunk in _chunk_sentence_group(group, pause_boundaries)
    )


def provisional_caption_overflow_passage_indexes(
    passages: Sequence[Mapping[str, object]],
    duration_s: float,
) -> tuple[int, ...]:
    """Return passages that cannot satisfy the silent-review subtitle contract.

    Silent review allocates passage time with the same narration-duration
    estimator used for the script, so a word-proportional provisional timing
    is the deterministic pre-persistence equivalent. Spoken text is never
    rewritten here; this is only an admission check for narrative repair.
    """
    try:
        duration = float(duration_s)
    except (TypeError, ValueError):
        return ()
    if not math.isfinite(duration) or duration <= 0.0:
        return ()
    rows = [row for row in passages if isinstance(row, Mapping)]
    token_rows = [re.findall(r"\S+", str(row.get("text", "")).strip()) for row in rows]
    total_words = sum(len(tokens) for tokens in token_rows)
    if total_words <= 0:
        return ()
    seconds_per_word = duration / total_words
    overflow: list[int] = []
    cursor = 0.0
    for index, (row, tokens) in enumerate(zip(rows, token_rows, strict=True)):
        if not tokens:
            continue
        cues = [
            {
                "spoken_token_index": token_index,
                "word": token,
                "start": cursor + seconds_per_word * token_index,
                "end": cursor + seconds_per_word * (token_index + 1),
            }
            for token_index, token in enumerate(tokens)
        ]
        try:
            build_sentence_caption_groups(
                str(row.get("text", "")),
                cues,
                group_prefix=f"provisional-admission-{index + 1}",
            )
        except ValueError as exc:
            if str(exc).startswith("subtitle.overflow:"):
                overflow.append(index)
            else:
                raise
        cursor += seconds_per_word * len(tokens)
    return tuple(overflow)


def build_sentence_groups_from_segments(segments: Sequence[object]) -> tuple[object, ...]:
    """Build production groups from persisted segment text and word timings.

    Segment timings are provider-relative; this function shifts them onto the
    absolute timeline and refuses to synthesize missing provider timing.
    """
    if not segments:
        raise ValueError("subtitle.word_timing_missing: no timed audio segments")
    groups: list[object] = []
    for segment_index, segment in enumerate(segments):
        spoken_text = str(getattr(segment, "spoken_text", "") or "")
        if not spoken_text.strip():
            raise ValueError("subtitle.spoken_text_missing: spoken narration is required")
        raw_timings = list(getattr(segment, "word_timings", ()) or ())
        if not raw_timings:
            raise ValueError("subtitle.word_timing_missing: provider word timings are required")
        try:
            offset = float(getattr(segment, "start_time", 0.0))
        except (TypeError, ValueError):
            raise ValueError("subtitle.word_timing_invalid: segment start is invalid") from None
        timed_cues: list[dict[str, object]] = []
        for token_index, timing in enumerate(raw_timings):
            if not isinstance(timing, Mapping):
                raise ValueError("subtitle.word_timing_invalid: provider timing is malformed")
            start = _timing_value(timing, "start_s", "start") + offset
            end = _timing_value(timing, "end_s", "end") + offset
            timed_cues.append(
                {
                    "spoken_token_index": token_index,
                    "text": timing.get("text", timing.get("word", "")),
                    "start_s": start,
                    "end_s": end,
                }
            )
        groups.extend(
            build_sentence_caption_groups(
                spoken_text,
                timed_cues,
                group_prefix=f"segment-{segment_index + 1}-sentence",
            )
        )
    if any(
        left.end_time > right.start_time
        for left, right in zip(groups, groups[1:], strict=False)
    ):
        raise ValueError("subtitle.word_timing_overlap: segment groups overlap")
    return tuple(groups)


def validate_sentence_groups(
    groups: Sequence[object],
    *,
    duration: float | None = None,
) -> tuple[str, ...]:
    """Return stable blocking codes for the shared production subtitle contract."""
    if not groups:
        return ("subtitle.word_timing_missing",)
    failures: list[str] = []
    previous_end = 0.0
    for group in groups:
        words = tuple(getattr(group, "words", ()) or ())
        lines = sentence_group_lines(words)
        if len(words) < CAPTION_MIN_CHUNK_WORDS:
            failures.append("subtitle.sentence_group_invalid")
        if len(lines) > CAPTION_MAX_LINES or (
            len(lines) > 1 and any(len(line.split()) < 2 for line in lines)
        ):
            failures.append("subtitle.overflow")
        start = float(getattr(group, "start_time", 0.0))
        end = float(getattr(group, "end_time", 0.0))
        if start < previous_end - 0.01 or end <= start:
            failures.append("subtitle.timing_overlap")
        if duration is not None and (start < -0.01 or end > float(duration) + 0.01):
            failures.append("subtitle.timing_out_of_bounds")
        for word in words:
            if not re.fullmatch(r"[A-Z0-9]+", str(getattr(word, "text", ""))):
                failures.append("subtitle.display_punctuation")
            if float(getattr(word, "start_time", 0.0)) < start - 0.01 or float(
                getattr(word, "end_time", 0.0)
            ) > end + 0.01:
                failures.append("subtitle.word_timing_invalid")
        previous_end = end
    return tuple(dict.fromkeys(failures))


def contract_manifest(profile: object | None = None) -> dict[str, object]:
    """Return the serializable subtitle contract used by a production render."""
    font_name = "Barber Chop"
    return {
        "contract_version": SUBTITLE_CONTRACT_VERSION,
        "font_name": font_name,
        "font_height_ratio": CAPTION_FONT_HEIGHT_RATIO,
        "font_size_px": round(1920 * CAPTION_FONT_HEIGHT_RATIO),
        "max_chars_per_line": CAPTION_MAX_CHARS,
        "max_lines": CAPTION_MAX_LINES,
        "safe_margin_px": CAPTION_SAFE_MARGIN_PX,
        "active_word_color": "yellow",
        "active_word_scale": CAPTION_ACTIVE_SCALE,
        "inactive_word_color": "white",
        "italic": True,
        "outline_pixels": 6,
        "timing_source": "audio_segment.word_timings",
        "spoken_text_immutable": True,
        "profile_id": getattr(profile, "profile_id", None),
    }


__all__ = [
    "CAPTION_ACTIVE_SCALE",
    "CAPTION_FONT_HEIGHT_RATIO",
    "CAPTION_MAX_CHARS",
    "CAPTION_MAX_LINES",
    "CAPTION_SAFE_MARGIN_PX",
    "SUBTITLE_CONTRACT_VERSION",
    "build_sentence_caption_groups",
    "build_sentence_groups_from_segments",
    "caption_lines_fit",
    "contract_manifest",
    "sentence_group_lines",
    "validate_sentence_groups",
]
