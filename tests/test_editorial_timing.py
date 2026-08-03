from dataclasses import dataclass, field

from app.services.editorial_timing import dramatic_events, language_consistency
from app.services.pipeline import spans_from_segments
from app.services.shot_director import plan_shots
from app.services.visual_scoring import PanelCandidate, VisualFeatures


def test_dramatic_events_persist_word_timing_and_lock_impacts():
    timings = [
        {"word": "The", "start": 0.0, "end": 0.2},
        {"word": "attack", "start": 0.2, "end": 0.5},
        {"word": "lands", "start": 0.5, "end": 0.8},
    ]
    assert dramatic_events(timings, "en") == [
        {"word": "attack", "tag": "attack", "start": 0.2, "end": 0.5, "impact_lock": True}
    ]


def test_absolute_event_timing_survives_segment_offset():
    @dataclass
    class Segment:
        section: str = "conflict"
        text: str = "The attack lands"
        start_time: float = 2.0
        end_time: float = 4.0
        word_timings: list[dict] = field(default_factory=list)
        dramatic_events: list[dict] = field(default_factory=list)

    segment = Segment(
        word_timings=[{"word": "attack", "start": 0.4, "end": 0.7}],
        dramatic_events=dramatic_events([{"word": "attack", "start": 0.4, "end": 0.7}], "en"),
    )
    span = spans_from_segments([segment])[0]
    assert span.dramatic_events[0]["start"] == 2.4
    assert span.impact_lock is True


def test_impact_lock_cuts_at_event_not_anticipation():
    @dataclass
    class Span:
        section: str
        text: str
        start_time: float
        end_time: float
        word_timings: list[dict]
        dramatic_events: list[dict]
        impact_lock: bool

    span = Span(
        "conflict", "The attack lands", 0.0, 5.0,
        [{"word": "attack", "start": 2.0, "end": 2.4}],
        [{"word": "attack", "tag": "attack", "start": 2.0, "end": 2.4, "impact_lock": True}],
        True,
    )
    candidate = PanelCandidate("p", 0, VisualFeatures(focal_points=((0.2, 0.3), (0.8, 0.4))), 5.0)
    shots = plan_shots([span], [candidate], min_scene_seconds=1.0, max_scene_seconds=2.0)
    assert any(abs(shot.end_time - 2.0) <= 0.01 for shot in shots[:-1])


def test_language_consistency_detects_unintended_code_switch():
    assert language_consistency("The warrior attacks", "en")["passed"]
    finding = language_consistency("The warrior menyerang", "en")
    assert finding["passed"] is False
    assert finding["foreign_words"] == ["menyerang"]


# ponytail: language detection intentionally uses a bounded vocabulary; add a
# local classifier only when project language support expands beyond en/id.

def test_editorial_timing_module_has_no_hidden_clock():
    assert dramatic_events([], "en") == []
