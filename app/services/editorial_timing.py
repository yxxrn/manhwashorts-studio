"""Deterministic dramatic-word timing and language consistency checks."""
from __future__ import annotations

import re

_EVENT_WORDS: dict[str, set[str]] = {
    "attack": {"attack", "attacked", "attacks", "strike", "struck", "hit", "serang", "menyerang", "memukul", "merampas", "menebas", "bertarung"},
    "reveal": {"reveal", "finally", "appears", "awakens", "muncul", "akhirnya", "ternyata", "datang", "hadir"},
    "explosion": {"explosion", "explode", "blast", "ledakan", "meledak"},
    "victory": {"victory", "wins", "won", "triumph", "menang", "kemenangan", "mengalahkan"},
}
_ENGLISH = {"the", "a", "an", "and", "is", "are", "was", "were", "to", "of", "in", "with", "on", "this", "that"}
_INDONESIAN = {"yang", "dan", "adalah", "ini", "itu", "untuk", "dengan", "di", "ke", "dari", "akan", "telah", "sudah", "menyerang", "memukul", "menang", "muncul"}


def _token(word: str) -> str:
    return re.sub(r"[^a-z]", "", str(word).lower())


def dramatic_events(word_timings: list[dict], language: str) -> list[dict]:
    """Persist event words with absolute-to-clip timing and impact locks."""
    events: list[dict] = []
    for timing in word_timings or []:
        token = _token(timing.get("word", ""))
        for tag, words in _EVENT_WORDS.items():
            if token in words:
                events.append({
                    "word": timing.get("word", ""), "tag": tag,
                    "start": round(float(timing.get("start", 0.0)), 3),
                    "end": round(float(timing.get("end", 0.0)), 3),
                    "impact_lock": tag in {"attack", "explosion"},
                })
                break
    return events


def language_consistency(text: str, expected: str) -> dict:
    """Flag mixed narration, not a narration written entirely in the other language."""
    tokens = {_token(word) for word in re.findall(r"\S+", text)}
    own = _ENGLISH if expected == "en" else _INDONESIAN
    foreign_set = _INDONESIAN if expected == "en" else _ENGLISH
    own_words = sorted(tokens & own)
    foreign = sorted(tokens & foreign_set)
    # A source may be tagged with the wrong project language; that is a separate
    # editorial decision. Code-switching requires evidence of both languages.
    mixed = bool(own_words and foreign)
    return {
        "passed": not mixed,
        "foreign_words": foreign,
        "expected_words": own_words,
        "expected": expected,
    }


__all__ = ["dramatic_events", "language_consistency"]
