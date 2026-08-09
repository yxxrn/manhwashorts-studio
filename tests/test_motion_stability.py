"""RED contract tests for deterministic reference-matched camera motion."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, replace

import pytest
from PIL import Image, ImageChops, ImageDraw

FORBIDDEN_CURVES = {
    "micro_shake",
    "impact_shake",
    "shake_zoom",
    "orbit",
    "punch_zoom",
    "whip_transition",
    "explosion",
}


@dataclass
class _Scene:
    asset_id: str = "panel-1"
    start_time: float = 0.0
    end_time: float = 2.0
    focus_x: float = 0.2
    focus_y: float = 0.3
    focus_end_x: float = 0.8
    focus_end_y: float = 0.7
    camera_curve: str = "slow_push_in"
    effect: str = "slow_push_in"
    motion_mode: str = "slow_push"
    motion_reason: str = "stable reading move"
    camera_intent: str = "neutral"
    transition: str = "cut"
    source_family: str = "family-a"
    visual_signature: str = "signature-1"


@dataclass
class _Cue:
    text: str
    start_time: float
    end_time: float


def _monotonic(values: list[float]) -> bool:
    increasing = all(right >= left - 1e-9 for left, right in zip(values, values[1:], strict=False))
    decreasing = all(right <= left + 1e-9 for left, right in zip(values, values[1:], strict=False))
    return increasing or decreasing


def _grid(path):
    image = Image.new("RGB", (400, 711), "#202038")
    draw = ImageDraw.Draw(image)
    for x in range(0, 400, 40):
        draw.line((x, 0, x, 711), fill="#d0d060", width=3)
    for y in range(0, 711, 40):
        draw.line((0, y, 400, y), fill="#60d0d0", width=3)
    draw.rectangle((45, 80, 150, 220), fill="#d04040")
    draw.rectangle((245, 470, 360, 640), fill="#4040d0")
    image.save(path, "PNG")


def test_forbidden_curves_are_not_director_choices_or_camera_plans():
    from app.services import camera_planner, motion_director, shot_director

    assert not FORBIDDEN_CURVES.intersection(motion_director.MODES)
    for choices in shot_director._CURVES.values():
        assert not FORBIDDEN_CURVES.intersection(choices)
    for index, curve in enumerate(sorted(FORBIDDEN_CURVES)):
        plan = camera_planner.execute_camera_plan(index, curve)
        assert plan.camera_curve not in FORBIDDEN_CURVES
        assert plan.effect not in FORBIDDEN_CURVES


def test_camera_sampler_is_deterministic_monotonic_and_capped_for_121_frames():
    from app.services import motion_director

    curves = (
        "static",
        "slow_push_in",
        "slow_pull_out",
        "pan_horizontal",
        "pan_vertical",
        "pan_diagonal",
        "focus_shift",
        "push_in",
        "reveal",
        "atmospheric",
        "static_emphasis",
    )
    for curve in curves:
        samples = motion_director.sample_camera_curve(
            curve,
            121,
            focus_x=0.2,
            focus_y=0.3,
            focus_end_x=0.8,
            focus_end_y=0.7,
        )
        assert len(samples) == 121
        assert samples == motion_director.sample_camera_curve(
            curve, 121, 0.2, 0.3, 0.8, 0.7
        )
        assert _monotonic([sample[0] for sample in samples])
        assert _monotonic([sample[1] for sample in samples])
        assert _monotonic([sample[2] for sample in samples])
        cap = 1.08 if curve in {"push_in", "reveal"} else 1.06
        assert min(sample[2] for sample in samples) >= 1.0 - 1e-9
        assert max(sample[2] for sample in samples) <= cap + 1e-9
        assert all(0.05 <= sample[0] <= 0.95 for sample in samples)
        assert all(0.05 <= sample[1] <= 0.95 for sample in samples)


def test_action_motion_is_stable_and_emphasis_cannot_repeat():
    from app.services import motion_director

    action = motion_director.plan_motion(
        section="conflict", narration_tags={"attack"}, roi_label="weapon", index=0
    )
    impact = motion_director.plan_motion(
        section="conflict", narration_tags={"explosion"}, roi_label="effect", index=1
    )
    assert "stable action" in action.reason.lower()
    assert "stable action" in impact.reason.lower()
    issues = motion_director.audit_motion(
        [
            motion_director.MotionPlan("impact", "high", "stable action push", 1),
            motion_director.MotionPlan("impact", "high", "stable action push", 2),
        ]
    )
    assert "strong_effects_consecutive" in issues
    assert "motion.emphasis_consecutive" in issues


def test_visual_planner_maps_impact_to_stable_action_motion():
    from app.services import editorial_visual_planner
    from app.services.visual_scoring import PanelCandidate, VisualFeatures

    class Span:
        section = "conflict"
        start_time = 0.0
        end_time = 2.0
        text = "The explosion hits the gate and the attacker charges."
        word_timings = []

    candidate = PanelCandidate(
        "panel-1",
        0,
        VisualFeatures(
            dramatic_composition=0.8,
            action_pose=0.9,
            object_density=0.6,
            focal_points=((0.25, 0.3), (0.75, 0.65)),
        ),
        5.0,
    )
    shots = editorial_visual_planner.plan([Span()], [candidate])
    assert shots
    assert not any(shot["camera_curve"] in FORBIDDEN_CURVES for shot in shots)
    assert all("stable action" in shot["motion_reason"].lower() for shot in shots)


def test_render_filter_is_even_quantized_and_contains_no_oscillation():
    from app.services import render

    for curve in ("slow_push_in", "slow_pull_out", "pan_diagonal", "focus_shift", "push_in"):
        filter_text = render._motion_filter(
            curve, 240, 426, 4.0, 30, 0.2, 0.3, 0.8, 0.7
        ).lower()
        assert "floor(" in filter_text
        assert "trunc" not in filter_text
        assert "sin(" not in filter_text
        assert "cos(" not in filter_text
        assert "abs(" not in filter_text
        assert "max(0" in filter_text


def test_legacy_motion_renders_stable_but_qc_blocks_forbidden_curve():
    from app.services import editorial_qc, quality, render

    filter_text = render._motion_filter("impact_shake", 240, 426, 1.0, 30).lower()
    assert "sin(" not in filter_text
    assert "cos(" not in filter_text
    assert "abs(" not in filter_text
    assert "floor(" in filter_text

    scene = _Scene(camera_curve="impact_shake", effect="impact_shake")
    report = editorial_qc.build_report(
        scenes=[scene], cues=[_Cue("WORD", 0.0, 2.0)], duration=2.0,
        minimum_duration=0.0, preview=True,
    )
    assert "motion.forbidden_curve" in report.failures
    results = quality.check_repetition_and_motion([scene])
    assert any(result.code == "motion.forbidden_curve" and result.blocking for result in results)


def test_same_asset_pan_reversal_is_a_blocking_auditable_failure():
    from app.services import editorial_qc

    left = _Scene(
        asset_id="same-panel",
        focus_x=0.2,
        focus_y=0.5,
        focus_end_x=0.8,
        focus_end_y=0.5,
        camera_curve="pan_horizontal",
        transition="fade",
    )
    right = _Scene(
        asset_id="same-panel",
        start_time=2.0,
        end_time=4.0,
        focus_x=0.8,
        focus_y=0.5,
        focus_end_x=0.2,
        focus_end_y=0.5,
        camera_curve="pan_horizontal",
        transition="fade",
    )
    report = editorial_qc.build_report(
        scenes=[left, right], cues=[_Cue("WORD", 0.0, 1.0)], duration=4.0,
        minimum_duration=0.0, preview=True,
    )
    assert "motion.reversal_same_asset" in report.failures


    hard_cut_report = editorial_qc.build_report(
        scenes=[replace(left, transition="cut"), replace(right, transition="cut")],
        cues=[_Cue("WORD", 0.0, 1.0)], duration=4.0,
        minimum_duration=0.0, preview=True,
    )
    assert "motion.reversal_same_asset" not in hard_cut_report.failures


def test_one_word_subtitle_qc_replaces_obsolete_group_failures():
    from app.services import editorial_qc

    scenes = [
        _Scene(
            asset_id=f"panel-{index}",
            start_time=float(index),
            end_time=float(index + 1),
            motion_mode="static_emphasis",
            camera_curve="static_emphasis",
        )
        for index in range(4)
    ]
    cues = [_Cue("THE", 0.0, 1.0), _Cue("GATE", 1.0, 2.0), _Cue("OPENS", 2.0, 3.0)]
    report = editorial_qc.build_report(
        scenes=scenes, cues=cues, duration=4.0, minimum_duration=0.0, preview=False
    )
    obsolete = {
        "single_word_caption_ratio_ge_15pct",
        "caption_group_outside_4_7_words",
        "caption_dangling_boundary",
        "caption_final_one_word",
    }
    assert not obsolete.intersection(report.failures)


def test_invalid_display_cue_is_blocked_by_motion_qc_matrix():
    from app.services import quality
    from app.services.timeline import CueSpec

    cues = [
        CueSpec(0, "two WORDS", 0.0, 1.0),
        CueSpec(1, "BAD!", 0.9, 2.0),
        CueSpec(2, "lower", 2.0, 3.0),
    ]
    results = quality.check_subtitles(cues)
    codes = {result.code for result in results if result.blocking}
    assert {
        "subtitle.display_multiword",
        "subtitle.display_punctuation",
        "subtitle.display_not_uppercase",
        "subtitle.overlap",
    }.issubset(codes)


@pytest.mark.slow
def test_real_ffmpeg_grid_motion_decodes_and_legacy_fallback_is_static(tmp_path):
    from app.config import settings
    from app.services import encoders, render

    source = tmp_path / "grid.png"
    _grid(source)
    selection = encoders.Selection(encoders.CPU, requested="cpu")
    curves = ("slow_push_in", "slow_pull_out", "pan_horizontal", "focus_shift", "impact_shake")
    moving = set(curves[:-1])

    for index, curve in enumerate(curves):
        output = tmp_path / f"motion-{index}.mp4"
        render.render_scene_clip(
            render.SceneInput(
                image_path=source,
                start_time=0.0,
                end_time=0.3,
                focus_x=0.2,
                focus_y=0.3,
                focus_end_x=0.8,
                focus_end_y=0.7,
                camera_curve=curve,
                effect=curve,
                motion_mode="hold",
                transition="cut",
            ),
            source,
            output,
            240,
            426,
            30,
            encoder=selection,
            preview=True,
        )
        assert render.probe(output)["width"] == 240
        subprocess.run(
            [settings.ffmpeg_bin, "-v", "error", "-i", str(output), "-f", "null", "-"],
            check=True,
        )
        first = tmp_path / f"first-{index}.png"
        last = tmp_path / f"last-{index}.png"
        subprocess.run(
            [settings.ffmpeg_bin, "-y", "-v", "error", "-i", str(output), "-vf", "select=eq(n\\,0)", "-frames:v", "1", str(first)],
            check=True,
        )
        subprocess.run(
            [settings.ffmpeg_bin, "-y", "-v", "error", "-sseof", "-0.05", "-i", str(output), "-frames:v", "1", str(last)],
            check=True,
        )
        with Image.open(first) as first_frame, Image.open(last) as last_frame:
            different = ImageChops.difference(first_frame.convert("RGB"), last_frame.convert("RGB")).getbbox() is not None
        if curve in moving:
            assert different, f"{curve} did not move the synthetic grid"
        else:
            assert different, "legacy impact_shake fallback lost its stable subject move"
