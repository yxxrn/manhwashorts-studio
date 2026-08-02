from app.services.director import analyze_span, analyze_story


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
