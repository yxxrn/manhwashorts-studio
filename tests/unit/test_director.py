from app.services.director import analyze_span, analyze_story, audit_sequence


def test_director_splits_suspense_and_reveal_before_camera():
    class Span:
        section = "conflict"
        text = "He quietly walked toward the cave until a giant monster suddenly appeared."
        start_time = 0.0
        end_time = 6.0
        word_timings = [
            {"word": "walked", "start": 0.3, "end": 0.8},
            {"word": "until", "start": 2.1, "end": 2.4},
            {"word": "suddenly", "start": 3.0, "end": 3.4},
            {"word": "appeared", "start": 3.6, "end": 4.1},
        ]

    beats = analyze_span(Span())
    assert [beat.kind for beat in beats] == ["suspense", "reveal", "reveal", "reveal"]
    assert beats[0].visual_timing == "visual_before"
    assert beats[1].camera_intent == "reveal"
    assert beats[1].timing_offset == -0.18
    assert beats[1].end_time <= beats[2].start_time


def test_director_preserves_sections_and_story_order():
    class Span:
        section = "twist"
        text = "The hero finally wins."
        start_time = 0.0
        end_time = 2.0
        word_timings = []

    beats = analyze_story([Span()])
    assert len(beats) == 1
    assert beats[0].section == "twist"
    assert beats[0].kind == "victory"
    assert beats[0].camera_intent == "victory"


def test_director_does_not_charge_inter_section_silence_to_previous_section():
    class Span:
        text = "A quiet beat."
        word_timings = []

        def __init__(self, section, start_time, end_time):
            self.section = section
            self.start_time = start_time
            self.end_time = end_time

    beats = analyze_story([Span("hook", 0.0, 7.882), Span("setup", 8.062, 10.0)])
    hook = [beat for beat in beats if beat.section == "hook"]
    assert hook[-1].end_time == 7.882


def test_director_still_bridges_gaps_inside_the_same_section():
    class Span:
        text = "A quiet beat."
        word_timings = []

        def __init__(self, section, start_time, end_time):
            self.section = section
            self.start_time = start_time
            self.end_time = end_time

    beats = analyze_story([Span("setup", 0.0, 2.0), Span("setup", 2.18, 4.0)])
    assert beats[0].end_time == 2.18


def test_director_locks_impact_on_the_key_word():
    class Span:
        section = "conflict"
        text = "The sword hits the shield."
        start_time = 0.0
        end_time = 2.0
        word_timings = [{"word": "hits", "start": 1.0, "end": 1.2}]

    beat = analyze_span(Span())[0]
    assert beat.kind == "impact"
    assert beat.impact_lock
    assert beat.timing_offset == 0.0


def test_human_editor_audit_rejects_repeated_roi_and_curve():
    class Shot:
        asset_id = "panel"
        roi_label = "face"
        camera_curve = "slow_push_in"
        start_time = 0.0
        end_time = 1.0

    first, second, third = Shot(), Shot(), Shot()
    second.start_time, second.end_time = 1.0, 2.0
    third.start_time, third.end_time = 2.0, 3.0
    issues = audit_sequence([first, second, third])
    assert "repeated_roi" in issues
    assert "repeated_camera_curve" in issues
