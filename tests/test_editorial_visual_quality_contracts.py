from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw


def _review_sidecar(*, shot_count: int = 8, available_visuals: int = 15) -> dict:
    duration = 51.3
    shot_duration = duration / shot_count
    shots = []
    modes = ("slow_push", "guided_pan", "focus_shift", "slow_pull")
    for index in range(shot_count):
        start = index * shot_duration
        shots.append(
            {
                "panel_id": f"panel-{index}",
                "selected_roi": {"roi_label": "primary", "index": index},
                "start_time": start,
                "end_time": start + shot_duration,
                "motion_mode": modes[index % len(modes)],
                "transition": "fade" if index == 1 else "cut",
                "framing_telemetry": {
                    "edge_connected_blank_fraction": 0.0,
                    "available_visual_capacity": available_visuals,
                },
            }
        )
    return {
        "shots": shots,
        "visual_motion_audit": {
            "available_visuals": available_visuals,
            "unique_visuals": shot_count,
            "motion_mode_diversity": len(set(modes)),
            "transition_count": 1,
            "max_unchanged_hold_s": 2.0,
            "mean_frame_diff": 2.0,
        },
    }


def test_review_density_contract_targets_three_to_four_seconds_per_visual():
    from app.services import review_preview

    contract = review_preview.review_visual_density_contract(51.3, 15)

    assert contract["minimum_required_visuals"] == 13
    assert contract["target_visuals"] == 15
    assert contract["max_seconds_per_visual"] == 4.0
    assert contract["min_seconds_per_visual"] == 3.0


def test_review_qc_rejects_eight_visuals_when_fifteen_safe_visuals_exist():
    from app.services import review_preview

    with pytest.raises(review_preview.ReviewPreviewError) as exc:
        review_preview._measured_visual_qc(_review_sidecar())

    assert exc.value.code == "review.visual_density_insufficient"


def test_reference_planner_uses_profile_independent_density_target():
    from app.services import editorial_visual_planner

    assert editorial_visual_planner._review_visual_shot_target(51.3, 15) == 15
    assert editorial_visual_planner._review_visual_shot_target(51.3, 8) == 8


def test_reference_planner_prioritizes_unused_panel_before_reuse():
    from app.services import editorial_visual_planner

    reused = SimpleNamespace(
        panel_id="panel-a",
        panel_region_id="region-a",
        source_order=1,
    )
    unused = SimpleNamespace(
        panel_id="panel-b",
        panel_region_id="region-b",
        source_order=2,
    )

    ordered = sorted(
        (reused, unused),
        key=lambda candidate: editorial_visual_planner._review_candidate_priority_key(
            candidate,
            {"panel-a": 1},
        ),
    )

    assert [candidate.panel_id for candidate in ordered] == ["panel-b", "panel-a"]


def test_reference_roi_order_prefers_lower_measured_edge_blank_in_review():
    from app.services import editorial_visual_planner

    high_blank = editorial_visual_planner.ReferenceROIAlternative(
        kind="primary",
        roi_label="primary",
        crop_box=(0, 0, 100, 100),
        focus=(0.2, 0.5, 0.2, 0.5),
        edge_blank_fraction=0.07,
    )
    low_blank = editorial_visual_planner.ReferenceROIAlternative(
        kind="alternate_roi",
        roi_label="alternate",
        crop_box=(10, 0, 100, 100),
        focus=(0.8, 0.5, 0.8, 0.5),
        edge_blank_fraction=0.0,
    )

    ordered = editorial_visual_planner._ordered_review_roi_alternatives(
        (high_blank, low_blank)
    )

    assert [roi.roi_label for roi in ordered] == ["alternate", "primary"]


def test_reference_transition_schedule_spreads_short_visible_transitions():
    from app.services import editorial_visual_planner

    shots = [
        {"section": "hook" if index < 6 else "twist"}
        for index in range(12)
    ]

    transitions = editorial_visual_planner._review_transition_schedule(shots)

    assert len(transitions) >= 2
    assert all(index > 0 for index in transitions)
    assert len(transitions) <= 3


def test_review_candidate_order_is_source_chronological_not_family_lexical():
    from app.services import editorial_visual_planner

    later = SimpleNamespace(
        source_order=43,
        panel_id="later",
        panel_region_id="later-region",
        panel_candidate=SimpleNamespace(source_family="001"),
    )
    earlier = SimpleNamespace(
        source_order=20,
        panel_id="earlier",
        panel_region_id="earlier-region",
        panel_candidate=SimpleNamespace(source_family="999"),
    )

    assert sorted((later, earlier), key=editorial_visual_planner._review_candidate_order_key) == [
        earlier,
        later,
    ]


def test_frame_edge_audit_detects_a_near_uniform_left_sidebar():
    from app.services import review_preview

    image = Image.new("RGB", (64, 114), (220, 120, 80))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 11, 113), fill=(8, 8, 8))

    metrics = review_preview._frame_edge_blank_metrics(image)

    assert metrics["left"] > 0.10
    assert metrics["max_edge_blank_fraction"] > 0.10


def test_motion_trajectory_audit_rejects_alternating_viewport_jumps():
    from app.services import review_preview

    alternating = (
        (0.40, 0.50, 1.00),
        (0.60, 0.50, 1.01),
        (0.40, 0.50, 1.02),
        (0.60, 0.50, 1.03),
    )

    with pytest.raises(review_preview.ReviewPreviewError) as exc:
        review_preview._audit_motion_trajectory(alternating)

    assert exc.value.code == "review.motion_jitter"


def test_motion_trajectory_audit_accepts_monotonic_smooth_path():
    from app.services import review_preview

    smooth = tuple(
        (0.40 + index * 0.02, 0.50, 1.00 + index * 0.005)
        for index in range(8)
    )

    metrics = review_preview._audit_motion_trajectory(smooth)

    assert metrics["jitter_violations"] == 0
    assert metrics["direction_reversals"] == 0


def test_transition_pixel_audit_blocks_planned_transition_with_no_visible_change(tmp_path: Path):
    from app.services import review_preview

    paths = []
    for index in range(4):
        path = tmp_path / f"frame-{index}.png"
        Image.new("RGB", (32, 32), (30, 40, 50)).save(path)
        paths.append(path)

    with pytest.raises(review_preview.ReviewPreviewError) as exc:
        review_preview._audit_transition_pixels(
            paths,
            [
                {"start_time": 0.0, "transition": "none"},
                {"start_time": 1.0, "transition": "slide_left"},
            ],
            duration=2.0,
        )

    assert exc.value.code == "review.transition_not_visible"


def test_renderer_maps_visible_transition_families_to_ffmpeg_filters():
    from app.services import render

    assert render._xfade_transition_name("dissolve") == "fade"
    assert render._xfade_transition_name("slide_left") == "slideleft"
    assert render._xfade_transition_name("slide_right") == "slideright"
    assert render._xfade_transition_name("cut") is None


def test_motion_filter_uses_continuous_time_for_camera_progress():
    from app.services import render

    expression = render._motion_filter(
        "slow_push_in", 1080, 1920, 2.0, 60,
    )

    assert "n/" in expression
    assert "sin(" not in expression
    assert "cos(" not in expression



def test_reference_motion_preflight_rejects_interior_blank_crossing(tmp_path):
    from app.services import reference_profile, render

    image = Image.new("RGB", (1150, 2040), (30, 30, 30))
    draw = ImageDraw.Draw(image)
    block = 80
    for y in range(0, 2040, block):
        for x in range(0, 1150, block):
            value = 30 if ((x // block + y // block) % 2 == 0) else 180
            draw.rectangle(
                (x, y, min(1149, x + block - 1), min(2039, y + block - 1)),
                fill=(value, value, value),
            )
    draw.rectangle((0, 110, 1149, 260), fill=(245, 245, 245))
    path = tmp_path / "interior-blank.jpg"
    image.save(path, quality=100)
    scene = render.SceneInput(
        image_path=path,
        start_time=0.0,
        end_time=2.0,
        focus_x=0.5,
        focus_y=0.95,
        focus_end_x=0.5,
        focus_end_y=0.95,
        camera_curve="slow_push_in",
        publish_allowed=False,
    )

    safe, maximum = render._reference_motion_pixel_safety(
        path,
        scene,
        100,
        178,
        reference_profile.REFERENCE_MATCHED_SHORTS_V2,
    )

    assert safe is False
    assert maximum > reference_profile.REVIEW_MAX_FRAME_EDGE_BLANK_FRACTION
    scene.camera_curve = "static"
    static_safe, static_maximum = render._reference_motion_pixel_safety(
        path,
        scene,
        100,
        178,
        reference_profile.REFERENCE_MATCHED_SHORTS_V2,
    )
    assert static_safe is True
    assert static_maximum <= reference_profile.REVIEW_MAX_FRAME_EDGE_BLANK_FRACTION


def test_reference_motion_preflight_preserves_safe_motion(tmp_path):
    from app.services import reference_profile, render

    path = tmp_path / "safe-motion.jpg"
    Image.new("RGB", (115, 204), (35, 45, 55)).save(path, quality=100)
    scene = render.SceneInput(
        image_path=path,
        start_time=0.0,
        end_time=2.0,
        focus_x=0.5,
        focus_y=0.5,
        focus_end_x=0.45,
        focus_end_y=0.47,
        camera_curve="slow_push_in",
        publish_allowed=False,
    )

    safe, maximum = render._reference_motion_pixel_safety(
        path,
        scene,
        100,
        178,
        reference_profile.REFERENCE_MATCHED_SHORTS_V2,
    )

    assert safe is True
    assert maximum == 0.0


def test_reference_motion_preflight_checks_final_tv_range_pixels(tmp_path):
    from app.services import framing_analysis, reference_profile, render

    image = Image.new("RGB", (64, 114), (60, 60, 60))
    pixels = image.load()
    for y in range(109, 114):
        for x in range(64):
            value = 130 + (20 if x % 2 else -20)
            pixels[x, y] = (value, value, value)
    path = tmp_path / "tv-range-edge.jpg"
    image.save(path, quality=100)
    assert framing_analysis.color_agnostic_edge_blank_fractions(image)[
        "max_edge_blank_fraction"
    ] == 0.0
    scene = render.SceneInput(
        image_path=path,
        start_time=0.0,
        end_time=2.0,
        camera_curve="static",
        motion_mode="hold",
        publish_allowed=False,
    )

    safe, maximum = render._reference_motion_pixel_safety(
        path, scene, 64, 114, reference_profile.REFERENCE_MATCHED_SHORTS_V2
    )

    assert safe is False
    assert maximum > reference_profile.REVIEW_MAX_FRAME_EDGE_BLANK_FRACTION


def test_reference_roi_metric_uses_final_tv_range_pixels():
    from app.services import reference_visual_review

    image = Image.new("RGB", (64, 114), (60, 60, 60))
    pixels = image.load()
    for y in range(109, 114):
        for x in range(64):
            value = 130 + (20 if x % 2 else -20)
            pixels[x, y] = (value, value, value)
    candidate = SimpleNamespace(
        features=SimpleNamespace(focal_points=((0.5, 0.5),))
    )
    profile = SimpleNamespace(final_width=64, final_height=114)

    alternatives = reference_visual_review.enumerate_reference_roi_alternatives(
        image.size, candidate, profile, image=image
    )
    primary = next(item for item in alternatives if item.roi_label == "panel_primary")

    assert primary.crop_box == (0, 0, 64, 114)
    assert primary.edge_blank_fraction == 1.0
