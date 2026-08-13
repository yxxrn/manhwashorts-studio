"""Contract tests for the versioned Sharp Friend narrative identity."""

from pathlib import Path

import pytest


PROMPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "prompts"
    / "vision_first_story_analyzer_v3.txt"
)


def _read_v3_prompt_directly() -> str:
    if not PROMPT_PATH.exists():
        pytest.fail(f"missing v3 prompt resource: {PROMPT_PATH.name}")
    raw = PROMPT_PATH.read_bytes()
    assert b"\r" not in raw
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        pytest.fail(f"v3 prompt is not UTF-8: {exc}")


def test_v3_prompt_resource_is_lf_utf8_and_normative():
    prompt = _read_v3_prompt_directly()
    lowered = prompt.lower()
    required = (
        "contract id: vision-first_editorial_story_engine.analyzer",
        "version: vision-first-story-analyzer-v3",
        "narrative profile: sharp_friend_v1",
        "observe every ordered panel",
        "reconcile all panel, observation, chunk, coverage, continuity, synthesis, and claim gates before prose",
        "story_spine",
        "ending_kind",
        "wants",
        "obstacle",
        "decision",
        "consequence",
        "changed stakes",
        "unresolved direction",
        "contractions",
        "varied sentence lengths",
        "causal connectors",
        "selective evidence-grounded commentary",
        "four to six",
        "cliffhanger",
        "open_question",
        "spoken text",
        "do not generate display_text",
        "no fixed intro",
        "no channel cta",
        "copied speech-balloon dialogue",
        "do not invent an identity, motive, relationship, event, or causal link",
    )
    for fragment in required:
        assert fragment in lowered, fragment
    assert lowered.index("observe every ordered panel") < lowered.index("ending_kind")
    assert lowered.index("ending_kind") < lowered.index("four to six")
