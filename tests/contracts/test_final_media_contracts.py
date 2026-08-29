from dataclasses import dataclass

from app.services import visual_scoring
from app.services.editorial_qc import build_report
from app.services.encoders import CPU, Selection, video_args
from app.services.timeline import (
    AudioSpan,
    build_cues,
    normalize_display_text,
    validate_cues,
    wrap_caption,
)


def test_final_encoder_is_high_quality_but_default_cpu_baseline_is_unchanged():
    selection = Selection(CPU, requested="cpu")
    assert video_args(selection) == [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
    ]
    final_args = video_args(selection, final=True)
    assert "-preset" in final_args and final_args[final_args.index("-preset") + 1] == "slow"
    assert "-profile:v" in final_args and final_args[final_args.index("-profile:v") + 1] == "high"
    assert final_args[-2:] == ["-pix_fmt", "yuv420p"]


def test_caption_groups_are_readable_and_end_at_media():
    timings = [
        {"word": word, "start": index * 0.35, "end": (index + 1) * 0.35}
        for index, word in enumerate(
            [
                "The",
                "gate",
                "opens",
                "and",
                "the",
                "hidden",
                "guardian",
                "appears",
                "beneath",
                "ancient",
                "runes",
            ]
        )
    ]
    span = AudioSpan("hook", " ".join(item["word"] for item in timings), 0, 3.85, timings)
    cues = build_cues(
        [span],
        media_duration=3.85,
    )
    expected = [
        normalize_display_text(timing["word"])
        for timing in timings
        if normalize_display_text(timing["word"])
    ]

    assert cues
    assert [cue.text for cue in cues] == expected
    assert all(len(cue.text.split()) == 1 for cue in cues)
    assert all(cue.text == normalize_display_text(cue.text) for cue in cues)
    assert all(all(character.isalnum() for character in cue.text) for cue in cues)
    assert all(cue.text == cue.text.upper() for cue in cues)
    assert all(len(wrap_caption(cue.text)) <= 2 for cue in cues)
    assert all(cue.end_time <= 3.85 for cue in cues)
    assert all(cue.end_time > cue.start_time for cue in cues)
    assert all(
        right.start_time >= left.end_time - 1e-9
        for left, right in zip(cues, cues[1:], strict=False)
    )
    assert not [warning for warning in validate_cues(cues, 28, 2, media_duration=3.85) if warning["severity"] == "error"]


@dataclass
class _Shot:
    asset_id: str
    start_time: float
    end_time: float
    motion_mode: str
    motion_reason: str
    camera_intent: str = "neutral"
    transition: str = "fade"
    focus_x: float = 0.5
    focus_y: float = 0.4
    focus_end_x: float = 0.5
    focus_end_y: float = 0.4
    section: str = "setup"
    source_family: str = "family-a"
    visual_signature: str = ""


@dataclass
class _Cue:
    text: str
    start_time: float = 0.0
    end_time: float = 75.0


def test_editorial_qc_accepts_fixed_real_shot_budget_and_media_clamp():
    shot_duration = 57.7 / 24
    modes = ("slow_push", "guided_pan", "focus_shift", "slow_pull", "atmospheric")
    scenes = [
        _Shot(
            f"panel-{index}",
            index * shot_duration,
            (index + 1) * shot_duration,
            modes[index % len(modes)],
            "deterministic timeline motion",
        )
        for index in range(24)
    ]
    report = build_report(
        scenes=scenes,
        cues=[_Cue("The next reveal begins now", end_time=57.7)],
        duration=57.7,
        rights_confidence=5,
        source_cleanliness=5,
        minimum_duration=0,
    )
    assert 2.3 <= report.average_shot_duration <= 3.3
    assert not any(
        failure.startswith("average_shot_duration_outside_")
        for failure in report.failures
    )
    assert report.caption_end_overflow == 0


def test_normal_qc_requires_motion_variety_and_action_cuts():
    scenes = [
        _Shot("a", 0, 2, "slow_push", "story beat"),
        _Shot("b", 2, 4, "guided_pan", "story beat"),
        _Shot("c", 4, 6, "focus_shift", "story beat"),
        _Shot("d", 6, 8, "slow_pull", "story beat", camera_intent="action", transition="fade"),
    ]
    report = build_report(
        scenes=scenes,
        cues=[_Cue("The hidden guardian appears now")],
        duration=75,
        rights_confidence=5,
        source_cleanliness=5,
        minimum_duration=0,
    )
    assert report.motion_mode_diversity == 4
    assert "motion_mode_diversity_lt_4" not in report.failures
    assert report.action_transition_failures == 1
    assert "action_transition_not_hard_cut" in report.failures


def test_visual_penalties_and_progression_are_auditable_without_ocr():
    features = visual_scoring.VisualFeatures(
        speech_balloon_dominance=0.8,
        ui_overlay_dominance=0.7,
        blank_dominance=0.9,
    )
    candidate = visual_scoring.PanelCandidate(
        "panel-1", 8, features, 1.0, source_family="family-b"
    )
    reasons = visual_scoring.selection_reasons(
        candidate,
        "the reveal appears",
        previous_order=4,
        previous_source_family="family-a",
    )
    assert "chronology:forward" in reasons
    assert "source_family_progression:advance" in reasons
    assert any(reason.startswith("penalty:speech_balloon_dominance") for reason in reasons)
    breakdown = visual_scoring.score_breakdown(candidate)
    assert breakdown["ui_overlay_penalty"] == 0.7
    assert breakdown["blank_dominance_penalty"] == 0.9
