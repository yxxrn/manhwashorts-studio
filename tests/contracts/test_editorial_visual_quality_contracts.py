from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw


def _review_sidecar(*, shot_count: int = 8, available_visuals: int = 15) -> dict:
    duration = 51.3
    shot_duration = duration / shot_count
    shots = []
    modes = ("slow_push", "guided_pan", "slow_pull", "focus_shift")
    for index in range(shot_count):
        start = index * shot_duration
        shots.append(
            {
                "panel_id": f"panel-{index}",
                "selected_roi": {"roi_label": "primary", "index": index},
                "start_time": start,
                "end_time": start + shot_duration,
                "motion_mode": modes[index % len(modes)],
                "transition": "none" if index == 0 else "fade",
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
            "transition_count": max(0, shot_count - 1),
            "max_unchanged_hold_s": 2.0,
            "mean_frame_diff": 2.0,
        },
    }


def test_review_frame_blank_accepts_geometry_safe_motion_preflight():
    from app.services import review_preview

    sidecar = _review_sidecar(shot_count=15, available_visuals=15)
    shot = sidecar["shots"][5]
    shot["framing_telemetry"]["motion_pixel_preflight"] = {
        "status": "safe", "max_motion_edge_blank_fraction": 0.0, "threshold": 0.08,
    }
    audit = sidecar["visual_motion_audit"]
    audit["max_frame_edge_blank_fraction"] = 0.20
    audit["per_frame_edge_blank_fractions"] = [{"max_edge_blank_fraction": 0.20}]
    audit["sample_frame_times_s"] = [shot["start_time"] + 0.5]
    result = review_preview._measured_visual_qc(sidecar)
    measured = result["visual_motion_audit"]
    assert measured["raw_max_frame_edge_blank_fraction"] == 0.20
    assert measured["corroborated_max_frame_edge_blank_fraction"] == 0.0


def test_review_frame_blank_rejects_without_safe_preflight():
    from app.services import review_preview

    sidecar = _review_sidecar(shot_count=15, available_visuals=15)
    shot = sidecar["shots"][5]
    audit = sidecar["visual_motion_audit"]
    audit["max_frame_edge_blank_fraction"] = 0.20
    audit["per_frame_edge_blank_fractions"] = [{"max_edge_blank_fraction": 0.20}]
    audit["sample_frame_times_s"] = [shot["start_time"] + 0.5]
    with pytest.raises(review_preview.ReviewPreviewError) as exc:
        review_preview._measured_visual_qc(sidecar)
    assert exc.value.code == "review.blank_edge_visible"


def test_review_density_contract_targets_three_to_four_seconds_per_visual():
    from app.services import review_preview

    contract = review_preview.review_visual_density_contract(51.3, 15)

    assert contract["minimum_required_visuals"] == 13
    assert contract["target_visuals"] == 15
    assert contract["max_seconds_per_visual"] == 4.0
    assert contract["min_seconds_per_visual"] == 3.0


def test_review_qc_rejects_eight_visuals_when_fifteen_safe_visuals_exist():
    from app.services import review_preview

    sidecar = _review_sidecar(shot_count=13, available_visuals=15)
    for index, shot in enumerate(sidecar["shots"]):
        shot["panel_id"] = f"panel-{index % 8}"
        shot["selected_roi"] = {"roi_label": "primary"}

    with pytest.raises(review_preview.ReviewPreviewError) as exc:
        review_preview._measured_visual_qc(sidecar)

    assert exc.value.code == "review.panel_repetition_excessive"


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

    assert len(transitions) == 11
    assert set(transitions) == set(range(1, 12))
    assert set(transitions.values()) <= {"fade", "slide_left", "slide_right"}


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



def test_edge_blank_span_metric_uses_full_frame_fraction():
    from app.services import framing_analysis

    image = Image.new("RGB", (64, 114), (220, 120, 80))
    ImageDraw.Draw(image).rectangle((0, 0, 0, 113), fill=(8, 8, 8))
    metrics = framing_analysis.color_agnostic_edge_blank_span_fractions(image)

    assert metrics["left"] == pytest.approx(1 / 64, abs=1e-6)
    assert metrics["max_edge_blank_fraction"] < 0.08

def test_frame_edge_audit_detects_a_near_uniform_left_sidebar():
    from app.services import review_preview

    image = Image.new("RGB", (64, 114), (220, 120, 80))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 11, 113), fill=(8, 8, 8))

    metrics = review_preview._frame_edge_blank_metrics(image)

    assert metrics["left"] > 0.10
    assert metrics["max_edge_blank_fraction"] > 0.10


def test_frame_motion_audit_detects_sixty_fps_micro_hold_cadence(tmp_path):
    from app.services import reference_profile, review_preview

    frame_paths = []
    for index in range(60):
        # Move only once every four frames: this models integer-pixel zoompan
        # quantization at 60 fps (hold, hold, hold, jump).
        offset = index // 4
        image = Image.new("RGB", (64, 114), "#202038")
        draw = ImageDraw.Draw(image)
        draw.rectangle((8 + offset, 18, 30 + offset, 92), fill="#f0d040")
        path = tmp_path / f"frame-{index:03d}.png"
        image.save(path)
        frame_paths.append(path)

    metrics = review_preview._frame_motion_audit(frame_paths, 1.0)

    assert metrics["micro_hold_fraction"] > (
        reference_profile.REVIEW_MOTION_MAX_MICRO_HOLD_FRACTION
    )
    assert metrics["max_micro_hold_run_s"] >= 0.04


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


def test_motion_trajectory_audit_rejects_imperceptible_travel():
    from app.services import review_preview

    subtle = tuple((0.50, 0.50, 1.00 + index * 0.001) for index in range(8))

    with pytest.raises(review_preview.ReviewPreviewError) as exc:
        review_preview._audit_motion_trajectory(subtle)

    assert exc.value.code == "review.motion_imperceptible"


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



def test_review_join_requires_animation_at_every_boundary(tmp_path: Path):
    from app.services import render

    clips = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    scenes = [
        render.SceneInput(None, 0.0, 1.0, transition="none"),
        render.SceneInput(None, 1.0, 2.0, transition="cut"),
    ]

    with pytest.raises(render.RenderError) as exc:
        render.join_scene_clips(
            clips, scenes, tmp_path / "joined.mp4", 30, require_transitions=True
        )

    assert exc.value.code == "review.transition_missing"


def test_review_join_does_not_fallback_to_hard_cut(monkeypatch, tmp_path: Path):
    from app.services import render

    calls = []

    def fail_xfade(*_args, **_kwargs):
        calls.append(True)
        raise render.RenderError("xfade failed", code="ffmpeg.filter_failed")

    monkeypatch.setattr(render, "_run", fail_xfade)
    clips = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    scenes = [
        render.SceneInput(None, 0.0, 1.0, transition="none"),
        render.SceneInput(None, 1.0, 2.0, transition="fade"),
    ]

    with pytest.raises(render.RenderError) as exc:
        render.join_scene_clips(
            clips, scenes, tmp_path / "joined.mp4", 30, require_transitions=True
        )

    assert exc.value.code == "ffmpeg.filter_failed"
    assert calls == [True]

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



def test_reference_motion_preflight_corroborates_flat_art_with_reference_mask(monkeypatch, tmp_path):
    from app.services import framing_analysis, reference_profile, render

    image = Image.new("RGB", (64, 114), (220, 120, 80))
    ImageDraw.Draw(image).rectangle((0, 0, 11, 113), fill=(245, 245, 245))
    path = tmp_path / "flat-art-edge.jpg"
    image.save(path, quality=100)
    scene = render.SceneInput(
        image_path=path, start_time=0.0, end_time=2.0,
        focus_x=0.5, focus_y=0.5, focus_end_x=0.5, focus_end_y=0.5,
        camera_curve="slow_push_in", publish_allowed=False,
        border_mask={"present": True}, selected_roi={"crop_box": [0, 0, 64, 114]},
        framing_telemetry={"edge_connected_blank_fraction": 0.0},
    )
    monkeypatch.setattr(render, "_reference_border_mask_from_mapping", lambda _value: object())
    monkeypatch.setattr(framing_analysis, "_mask_crop_fraction", lambda *_args, **_kwargs: 0.0)
    safe, maximum = render._reference_motion_pixel_safety(
        path, scene, 64, 114, reference_profile.REFERENCE_MATCHED_SHORTS_V2
    )
    assert safe is True
    assert maximum == 0.0

def test_reference_motion_preflight_checks_final_tv_range_pixels(tmp_path):
    from app.services import framing_analysis, reference_profile, render

    image = Image.new("RGB", (64, 114), (60, 60, 60))
    pixels = image.load()
    for y in range(102, 114):
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


def test_review_density_minimum_does_not_shrink_to_available_capacity():
    from app.services import review_preview

    contract = review_preview.review_visual_density_contract(51.3, 5)

    assert contract["minimum_required_visuals"] == 13
    assert contract["target_visuals"] == 5


def test_review_qc_rejects_any_shot_over_four_seconds():
    from app.services import review_preview

    sidecar = _review_sidecar(shot_count=13, available_visuals=15)
    sidecar["shots"][0]["end_time"] = 4.001

    with pytest.raises(review_preview.ReviewPreviewError) as exc:
        review_preview._measured_visual_qc(sidecar)

    assert exc.value.code == "review.shot_duration_excessive"


def test_review_qc_requires_animation_at_every_boundary():
    from app.services import review_preview

    sidecar = _review_sidecar(shot_count=13, available_visuals=15)
    sidecar["shots"][6]["transition"] = "cut"
    sidecar["visual_motion_audit"]["transition_count"] = 11

    with pytest.raises(review_preview.ReviewPreviewError) as exc:
        review_preview._measured_visual_qc(sidecar)

    assert exc.value.code == "review.transition_missing"


def test_review_qc_rejects_static_motion_mode():
    from app.services import review_preview

    sidecar = _review_sidecar(shot_count=13, available_visuals=15)
    sidecar["shots"][3]["motion_mode"] = "hold"

    with pytest.raises(review_preview.ReviewPreviewError) as exc:
        review_preview._measured_visual_qc(sidecar)

    assert exc.value.code == "review.motion_static"


def test_review_living_frame_motion_is_varied_perceptible_and_never_static():
    from app.services import editorial_visual_planner, reference_profile

    shots = [
        {
            "focus_x": 0.4 + index * 0.01,
            "focus_y": 0.5,
            "focus_end_x": 0.7,
            "focus_end_y": 0.6,
            "motion_mode": "guided_pan",
            "camera_curve": "pan_horizontal",
            "motion_reason": "old",
        }
        for index in range(8)
    ]

    editorial_visual_planner._enforce_review_zoom_motion(shots)

    assert [shot["motion_mode"] for shot in shots] == [
        "slow_push", "guided_pan", "slow_pull", "focus_shift",
        "slow_push", "guided_pan", "slow_pull", "focus_shift",
    ]
    assert len({shot["motion_mode"] for shot in shots}) == 4
    for shot in shots:
        travel = (
            (shot["focus_end_x"] - shot["focus_x"]) ** 2
            + (shot["focus_end_y"] - shot["focus_y"]) ** 2
        ) ** 0.5
        if shot["motion_mode"] in {"guided_pan", "focus_shift"}:
            assert travel >= reference_profile.REVIEW_MOTION_MIN_FOCUS_TRAVEL
        else:
            assert travel == pytest.approx(0.0)
    assert reference_profile.REVIEW_MOTION_ZOOM_DELTA >= reference_profile.REVIEW_MOTION_MIN_ZOOM_DELTA


def test_review_qc_rejects_rendered_static_shot():
    from app.services import review_preview

    sidecar = _review_sidecar(shot_count=13, available_visuals=15)
    sidecar["visual_motion_audit"]["rendered_shot_motion_audit"] = {
        "shot_count": 13,
        "static_shot_count": 1,
        "stair_step_shot_count": 0,
    }
    with pytest.raises(review_preview.ReviewPreviewError) as exc:
        review_preview._measured_visual_qc(sidecar)
    assert exc.value.code == "review.motion_noop"


def test_review_qc_rejects_rendered_stair_step_motion():
    from app.services import review_preview

    sidecar = _review_sidecar(shot_count=13, available_visuals=15)
    sidecar["visual_motion_audit"]["rendered_shot_motion_audit"] = {
        "shot_count": 13,
        "static_shot_count": 0,
        "stair_step_shot_count": 1,
    }
    with pytest.raises(review_preview.ReviewPreviewError) as exc:
        review_preview._measured_visual_qc(sidecar)
    assert exc.value.code == "review.motion_jitter"


def test_review_qc_accepts_clean_rendered_shot_motion_audit():
    from app.services import review_preview

    sidecar = _review_sidecar(shot_count=13, available_visuals=15)
    sidecar["visual_motion_audit"]["rendered_shot_motion_audit"] = {
        "shot_count": 13,
        "static_shot_count": 0,
        "stair_step_shot_count": 0,
    }
    result = review_preview._measured_visual_qc(sidecar)
    assert result["visual_motion_audit"]["rendered_shot_motion_audit"][
        "stair_step_shot_count"
    ] == 0


def _editorial_candidate_for_crop(*, face=True, subject=True, face_score=0.8, action_score=0.4):
    from types import SimpleNamespace

    from app.services import visual_scoring

    protected = []
    if face:
        protected.append(
            visual_scoring.ProtectedRegionEvidence(
                region_id="face-1", kind="face",
                normalized_bbox=(0.35, 0.10, 0.65, 0.30),
                normalized_polygon=(), confidence=0.98,
                evidence_source="test_geometry", required=True,
                minimum_coverage=0.96,
            )
        )
    if subject:
        protected.append(
            visual_scoring.ProtectedRegionEvidence(
                region_id="subject-1", kind="subject",
                normalized_bbox=(0.25, 0.06, 0.75, 0.88),
                normalized_polygon=(), confidence=0.98,
                evidence_source="test_geometry", required=True,
                minimum_coverage=0.72,
            )
        )
    evidence = SimpleNamespace(protected_regions=tuple(protected))
    panel_candidate = visual_scoring.PanelCandidate(
        "asset", 10,
        visual_scoring.VisualFeatures(
            face_visibility=face_score,
            facial_expression=face_score,
            action_pose=action_score,
            impact_frame=action_score,
            dramatic_composition=0.8,
            face_boxes=((0.35, 0.10, 0.65, 0.30),) if face else (),
        ),
        visual_score=4.0,
    )
    return SimpleNamespace(
        panel_id="panel", panel_region_id="region", source_order=10,
        panel_size=(1000, 2000), visual_evidence=evidence,
        panel_candidate=panel_candidate,
    )


def test_review_editorial_crop_detects_face_cutoff_and_prefers_safe_face_crop():
    from app.services import editorial_visual_planner

    candidate = _editorial_candidate_for_crop()
    safe = editorial_visual_planner.ReferenceROIAlternative(
        kind="aggressive_crop", roi_label="safe", crop_box=(200, 100, 800, 1200),
        focus=(0.5, 0.3, 0.5, 0.3),
    )
    cut = editorial_visual_planner.ReferenceROIAlternative(
        kind="aggressive_crop", roi_label="cut", crop_box=(500, 100, 800, 650),
        focus=(0.65, 0.2, 0.65, 0.2),
    )
    safe_metrics = editorial_visual_planner._review_crop_editorial_metrics(
        candidate, safe, {"base_zoom": 1.6, "subject_coverage": 1.0},
        section="setup", beat="neutral",
    )
    cut_metrics = editorial_visual_planner._review_crop_editorial_metrics(
        candidate, cut, {"base_zoom": 3.2, "subject_coverage": 0.5},
        section="setup", beat="neutral",
    )
    assert safe_metrics["face_cutoff_count"] == 0
    assert cut_metrics["face_cutoff_count"] > 0
    safe_key = editorial_visual_planner._review_editorial_crop_quality_key(
        safe_metrics, blank_fraction=0.02, base_zoom=1.6,
        protected_retained_fraction=0.9, preferred_blank_fraction=0.03,
    )
    cut_key = editorial_visual_planner._review_editorial_crop_quality_key(
        cut_metrics, blank_fraction=0.0, base_zoom=3.2,
        protected_retained_fraction=1.0, preferred_blank_fraction=0.03,
    )
    assert safe_key < cut_key


def test_review_editorial_crop_rejects_unjustified_extreme_detail_without_face_context():
    from app.services import editorial_visual_planner

    candidate = _editorial_candidate_for_crop(face=False, subject=False)
    roi = editorial_visual_planner.ReferenceROIAlternative(
        kind="aggressive_crop", roi_label="content_rescue_tiny",
        crop_box=(400, 700, 650, 1100), focus=(0.5, 0.45, 0.5, 0.45),
    )
    metrics = editorial_visual_planner._review_crop_editorial_metrics(
        candidate, roi, {"base_zoom": 3.4, "subject_coverage": 1.0},
        section="conflict", beat="action",
    )
    assert metrics["extreme_crop"] is True
    assert metrics["unjustified_detail_crop"] is True
    assert "unjustified_detail_crop" in metrics["anomaly_flags"]


def test_review_beat_fit_prefers_face_for_reaction_and_action_for_conflict():
    from types import SimpleNamespace

    from app.services import editorial_visual_planner, visual_scoring

    face = SimpleNamespace(
        panel_candidate=visual_scoring.PanelCandidate(
            "face", 1,
            visual_scoring.VisualFeatures(
                face_visibility=1.0, facial_expression=1.0,
                action_pose=0.1, dramatic_composition=0.8,
            ), 5.0,
        )
    )
    action = SimpleNamespace(
        panel_candidate=visual_scoring.PanelCandidate(
            "action", 2,
            visual_scoring.VisualFeatures(
                face_visibility=0.1, action_pose=1.0, impact_frame=1.0,
                dramatic_composition=0.9, visual_effects=0.8,
            ), 5.0,
        )
    )
    assert editorial_visual_planner._review_candidate_visual_fit_score(
        face, "twist", "reaction"
    ) > editorial_visual_planner._review_candidate_visual_fit_score(
        action, "twist", "reaction"
    )
    assert editorial_visual_planner._review_candidate_visual_fit_score(
        action, "conflict", "action"
    ) > editorial_visual_planner._review_candidate_visual_fit_score(
        face, "conflict", "action"
    )


def test_review_editorial_qc_rejects_face_cutoff_and_non_hook_reversal():
    from app.services import review_preview

    sidecar = _review_sidecar(shot_count=8, available_visuals=8)
    for index, shot in enumerate(sidecar["shots"]):
        shot["source_order"] = 100 + index
        shot["framing_telemetry"]["selection_context"] = {
            "section": "hook" if index < 2 else "setup",
        }
        shot["framing_telemetry"]["editorial_crop_quality"] = {
            "role": "action" if index < 2 else "setup",
            "face_cutoff_count": 0,
            "face_omission": False,
            "unjustified_detail_crop": False,
            "subject_region_count": 1,
            "subject_completeness_score": 1.0,
            "anomaly_flags": [],
        }
    sidecar["shots"][3]["framing_telemetry"]["editorial_crop_quality"]["face_cutoff_count"] = 1
    with pytest.raises(review_preview.ReviewPreviewError) as exc:
        review_preview._measured_visual_qc(sidecar)
    assert exc.value.code == "review.face_cutoff"

    sidecar["shots"][3]["framing_telemetry"]["editorial_crop_quality"]["face_cutoff_count"] = 0
    sidecar["shots"][4]["source_order"] = 99
    with pytest.raises(review_preview.ReviewPreviewError) as exc:
        review_preview._measured_visual_qc(sidecar)
    assert exc.value.code == "review.sequence_incoherent"


def _wide_rescue_telemetry(framing_analysis, *, zoom: float, blank: float, code=None):
    return framing_analysis.FramingTelemetry(
        contract_version="test", detector_version="test", mask_sha256="m" * 64,
        crop_box=(0, 0, 100, 200), base_zoom=zoom,
        source_resolution_zoom_cap=5.0, protected_region_zoom_cap=5.0,
        edge_connected_blank_fraction=blank,
        non_discardable_low_information_fraction=0.0,
        protected_retained_fraction=1.0, balloon_mask_intersection_ratio=0.0,
        subject_coverage=1.0, face_coverage=1.0, action_coverage=1.0,
        effect_coverage=1.0, continuity_context_coverage=1.0,
        mask_confidence=1.0, mask_source="test", rejection_code=code,
    )


def test_review_wide_crop_rescue_only_retries_blank_infeasible(monkeypatch):
    from app.services import editorial_visual_planner, framing_analysis, reference_profile

    calls = []
    def fake(*_args, blank_target_fraction, **_kwargs):
        calls.append(blank_target_fraction)
        if len(calls) == 1:
            return False, _wide_rescue_telemetry(
                framing_analysis, zoom=1.2, blank=0.12, code="visual.blank_infeasible"
            )
        return True, _wide_rescue_telemetry(framing_analysis, zoom=1.2, blank=0.12)
    monkeypatch.setattr(framing_analysis, "candidate_is_feasible", fake)
    ok, telemetry = editorial_visual_planner._review_framing_candidate_is_feasible(
        (0, 0, 100, 200), object(), object(), (100, 200), (100, 200),
        review_aggressive_crop=True, standard_blank_target=0.08,
        allow_conservative_full_panel=False,
    )
    assert ok is True
    assert calls == [0.08, reference_profile.REVIEW_COHERENCE_RESCUE_MAX_FRAME_EDGE_BLANK_FRACTION]
    assert telemetry.fallback_reason == reference_profile.REVIEW_COHERENCE_RESCUE_REASON


def test_review_wide_crop_rescue_rejects_excessive_zoom(monkeypatch):
    from app.services import editorial_visual_planner, framing_analysis

    def fake(*_args, blank_target_fraction, **_kwargs):
        if blank_target_fraction <= 0.08:
            return False, _wide_rescue_telemetry(
                framing_analysis, zoom=1.6, blank=0.12, code="visual.blank_infeasible"
            )
        return True, _wide_rescue_telemetry(framing_analysis, zoom=1.6, blank=0.12)
    monkeypatch.setattr(framing_analysis, "candidate_is_feasible", fake)
    ok, telemetry = editorial_visual_planner._review_framing_candidate_is_feasible(
        (0, 0, 100, 200), object(), object(), (100, 200), (100, 200),
        review_aggressive_crop=True, standard_blank_target=0.08,
        allow_conservative_full_panel=False,
    )
    assert ok is False
    assert telemetry.rejection_code == "visual.blank_infeasible"


def test_review_frame_blank_accepts_tagged_coherence_rescue_within_rescue_limit():
    from app.services import reference_profile, review_preview

    sidecar = _review_sidecar(shot_count=15, available_visuals=15)
    shot = sidecar["shots"][5]
    shot["framing_telemetry"]["fallback_reason"] = (
        reference_profile.REVIEW_COHERENCE_RESCUE_REASON
    )
    audit = sidecar["visual_motion_audit"]
    audit["max_frame_edge_blank_fraction"] = 0.15
    audit["per_frame_edge_blank_fractions"] = [{"max_edge_blank_fraction": 0.15}]
    audit["sample_frame_times_s"] = [shot["start_time"] + 0.5]
    result = review_preview._measured_visual_qc(sidecar)
    measured = result["visual_motion_audit"]
    assert measured["raw_max_frame_edge_blank_fraction"] == 0.15
    assert measured["corroborated_max_frame_edge_blank_fraction"] == pytest.approx(0.08)


def test_review_frame_blank_rejects_tagged_rescue_above_rescue_limit():
    from app.services import reference_profile, review_preview

    sidecar = _review_sidecar(shot_count=15, available_visuals=15)
    shot = sidecar["shots"][5]
    shot["framing_telemetry"]["fallback_reason"] = (
        reference_profile.REVIEW_COHERENCE_RESCUE_REASON
    )
    audit = sidecar["visual_motion_audit"]
    audit["max_frame_edge_blank_fraction"] = 0.18
    audit["per_frame_edge_blank_fractions"] = [{"max_edge_blank_fraction": 0.18}]
    audit["sample_frame_times_s"] = [shot["start_time"] + 0.5]
    with pytest.raises(review_preview.ReviewPreviewError) as exc:
        review_preview._measured_visual_qc(sidecar)
    assert exc.value.code == "review.blank_edge_visible"
