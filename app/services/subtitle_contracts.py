"""Dependency-light immutable contracts for sentence-held word karaoke."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class KaraokeWord:
    """One punctuation-free display word with authoritative word timing."""

    text: str
    start_time: float
    end_time: float

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Z0-9]+", self.text):
            raise ValueError("subtitle.display_punctuation: display words must be uppercase alphanumeric")
        if (
            not math.isfinite(self.start_time)
            or not math.isfinite(self.end_time)
            or self.start_time < 0.0
            or self.end_time <= self.start_time
        ):
            raise ValueError("subtitle.word_timing_invalid: word timing must be finite, nonnegative, and ordered")


@dataclass(frozen=True)
class KaraokeSentenceGroup:
    """A complete display sentence whose active word changes by timing."""

    group_id: str
    words: tuple[KaraokeWord, ...]
    start_time: float
    end_time: float

    def __post_init__(self) -> None:
        if not self.group_id or not self.words:
            raise ValueError("subtitle.sentence_group_invalid: group requires an id and words")
        if (
            not math.isfinite(self.start_time)
            or not math.isfinite(self.end_time)
            or self.start_time < 0.0
            or self.end_time <= self.start_time
            or self.start_time > self.words[0].start_time
            or self.end_time < self.words[-1].end_time
        ):
            raise ValueError("subtitle.sentence_timing_invalid: group timing does not contain words")
        if any(
            left.end_time > right.start_time
            for left, right in zip(self.words, self.words[1:], strict=False)
        ):
            raise ValueError("subtitle.word_timing_overlap: sentence word timings overlap")
