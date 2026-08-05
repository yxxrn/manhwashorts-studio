from collections import Counter
from dataclasses import dataclass

from app.services import director, editorial_visual_planner
from app.services.quality import check_repetition_and_motion
from app.services.shot_director import plan_shots
from app.services.timeline import AudioSpan, build_cues, is_caption_boundary, wrap_caption
from app.services.visual_scoring import (
    PanelCandidate,
    VisualFeatures,
    asset_use_cap,
    select_panel,
)


def _candidate(asset_id: str, order: int) -> PanelCandidate:
    return PanelCandidate(
        asset_id,
        order,
        VisualFeatures(
            action_pose=0.7,
            object_density=0.6,
            dramatic_composition=0.7,
            focal_points=((0.2, 0.3), (0.7, 0.4), (0.5, 0.7)),
        ),
        5.0,
    )


def _fixture_spans():
    class Span:
        def __init__(self, section: str, start: float, end: float, text: str):
            self.section = section
            self.start_time = start
            self.end_time = end
            self.text = text
            self.word_timings = []

    return [
        Span("hook", 0.0, 9.06, "Why would the hunter enter the gate"),
        Span("setup", 9.24, 20.70, "The hunter accepts the dangerous jobs"),
        Span("conflict", 20.88, 33.02, "Inside, the floor collapses and he attacks"),
        Span("twist", 33.20, 46.03, "Then the system finally appears"),
        Span("cta", 46.21, 57.77, "Tell us what he should do next"),
    ]


def _real_p0_transcript_spans():
    sections = (
        (
            "hook",
            0.0,
            9.06,
            "Why would the guild send its weakest hunter into a gate that stronger teams refused to touch? The answer is more dangerous than the mission itself.",
        ),
        (
            "setup",
            9.24,
            20.70,
            "Rian is an E-rank hunter who has spent years accepting the jobs nobody else wants. He is not chosen because he is powerful. He is chosen because nobody expects him to come back.",
        ),
        (
            "conflict",
            20.88,
            33.02,
            "Inside the abandoned gate, the route closes behind him. The floor gives way, the exit disappears, and a strange system offers him a brutal bargain: complete its training or be sent to a punishment zone.",
        ),
        (
            "twist",
            33.20,
            46.03,
            "Rian fails again and again, but each failure leaves him stronger than before. Which means the system may not be testing whether he can survive. It may be deciding what kind of hunter he is allowed to become.",
        ),
        (
            "cta",
            46.21,
            57.77,
            "The guild only knows that one person entered and nobody knows what came out. Would you accept Rian's bargain, or look for another way? Tell us your theory in the comments.",
        ),
    )
    spans = []
    for section, start, end, text in sections:
        words = text.split()
        step = (end - start) / len(words)
        timings = [
            {
                "word": word,
                "start": round(start + index * step, 3),
                "end": round(start + (index + 1) * step, 3),
            }
            for index, word in enumerate(words)
        ]
        spans.append(AudioSpan(section, text, start, end, timings))
    return spans


def test_real_p0_transcript_has_semantic_caption_endings():
    cues = build_cues(_real_p0_transcript_spans(), media_duration=57.7)

    assert 26 <= len(cues) <= 28
    assert all(4 <= len(cue.text.split()) <= 7 for cue in cues)
    assert all(len(wrap_caption(cue.text)) <= 2 for cue in cues)
    assert all(cue.end_time <= 57.7 for cue in cues)
    assert not any(is_caption_boundary(cue.text.split()[-1]) for cue in cues)


def test_selection_respects_explicit_per_asset_cap_when_alternatives_exist():
    chosen = select_panel(
        [_candidate("dominant", 0), _candidate("fresh", 1)],
        "the scene continues",
        usage_counts={"dominant": 2},
        max_asset_uses=2,
    )
    assert chosen is not None
    assert chosen.asset_id == "fresh"


def test_57_second_fixture_stays_in_short_pacing_window_and_cap():
    shots = plan_shots(
        _fixture_spans(),
        [_candidate(f"panel-{index:02d}", index) for index in range(24)],
    )
    assert 18 <= len(shots) <= 24
    average = sum(shot.end_time - shot.start_time for shot in shots) / len(shots)
    assert 2.3 <= average <= 3.3
    counts = Counter(shot.asset_id for shot in shots)
    assert max(counts.values()) <= asset_use_cap(len(shots))


def test_old_twelve_of_thirty_seven_asset_pattern_is_a_blocking_qc_failure():
    @dataclass
    class Shot:
        asset_id: str
        start_time: float
        end_time: float
        focus_x: float
        focus_y: float
        focus_end_x: float
        focus_end_y: float
        motion_mode: str = "slow_push"

    shots = [
        Shot("dominant", index * 1.55, (index + 1) * 1.55, 0.1 + index * 0.01, 0.2, 0.8, 0.7)
        for index in range(12)
    ]
    shots.extend(
        Shot(f"other-{index}", (index + 12) * 1.55, (index + 13) * 1.55, 0.2, 0.2, 0.7, 0.7)
        for index in range(25)
    )
    results = check_repetition_and_motion(shots)
    assert any(result.code == "visual.asset_reuse_cap" and result.blocking for result in results)


def test_real_p0_event_fragment_structure_stays_within_fixed_shot_budget():
    section_counts = (4, 5, 5, 12, 11)
    section_fragment_durations = (
        [9.06 / 4] * 4,
        [11.46 / 5] * 5,
        [12.14 / 5] * 5,
        [1.15] * 11 + [0.18],
        [1.15] * 10 + [0.06],
    )
    spans = _fixture_spans()
    beats = []
    for span, count, durations in zip(
        spans, section_counts, section_fragment_durations, strict=True
    ):
        assert len(durations) == count
        start = span.start_time
        for duration in durations:
            beats.append(
                director.StoryBeat(
                    section=span.section,
                    text=span.text,
                    start_time=round(start, 3),
                    end_time=round(start + duration, 3),
                    word_timings=[],
                    kind="neutral",
                    emotion="neutral",
                    camera_intent="neutral",
                    visual_timing="visual_during",
                )
            )
            start += duration

    assert len(beats) == 37
    coalesced = editorial_visual_planner._coalesce_beats(beats)
    shots = plan_shots(coalesced, [_candidate(f"panel-{index:02d}", index) for index in range(24)])

    assert 18 <= len(shots) <= 24
    average = sum(shot.end_time - shot.start_time for shot in shots) / len(shots)
    assert 2.3 <= average <= 3.3
    counts = Counter(shot.asset_id for shot in shots)
    assert max(counts.values()) <= asset_use_cap(len(shots))
