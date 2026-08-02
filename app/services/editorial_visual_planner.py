"""Automated editorial visual planning.

This is the public Director/visual-planner boundary. It consumes analyzed panel
candidates and narration spans, then returns an editorial shot list before camera
execution. It never re-scores panels.
"""
from __future__ import annotations

from collections.abc import Iterable

from app.services import director, visual_scoring


def plan(spans: Iterable[object], candidates: list[object]) -> list[dict]:
    """Create a beat-aware, ROI-driven shot list before rendering."""
    beats = director.analyze_story(list(spans))
    shots = visual_scoring.plan_content_aware_scenes(beats, candidates)
    issues = director.audit_sequence(shots)
    for shot in shots:
        shot.setdefault("editorial_audit", issues)
        beat = next((b for b in beats if b.start_time <= shot["start_time"] < b.end_time), None)
        text = beat.text.lower() if beat else ""
        if any(token in text for token in ("because", "therefore", "which means", "the reason", "this explains")):
            shot["overlay_text"] = "CAUSE / EFFECT"
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
