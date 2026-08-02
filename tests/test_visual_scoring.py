from PIL import Image, ImageDraw

from app.services.camera_planner import execute_camera_plan
from app.services.roi_detection import rank_rois
from app.services.shot_director import plan_shots
from app.services.visual_scoring import (
    PanelCandidate,
    VisualFeatures,
    analyze_panel,
    camera_effect,
    narration_tags,
    plan_content_aware_scenes,
    select_panel,
    tune_weights,
)


def _candidate(asset_id: str, order: int, score: float, *, face=0.0, action=0.0):
    return PanelCandidate(
        asset_id,
        order,
        VisualFeatures(face_visibility=face, action_pose=action),
        score,
    )


def test_content_analysis_returns_features_not_geometry_only():
    image = Image.new("RGB", (320, 480), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((25, 25, 295, 455), fill="black")
    draw.ellipse((85, 80, 235, 230), fill="white")
    draw.line((40, 360, 280, 270), fill="white", width=12)
    import io

    buf = io.BytesIO()
    image.save(buf, "PNG")
    candidate = analyze_panel(buf.getvalue(), "action-panel", 3)
    assert candidate.features.object_density > 0
    assert candidate.features.focal_points


def test_selection_prefers_stronger_nearby_panel_over_order():
    weak = _candidate("weak", 2, 1.0)
    strong = _candidate("strong", 4, 6.0, face=1.0, action=1.0)
    chosen = select_panel([weak, strong], "the dragon finally attacked", previous_order=2)
    assert chosen is not None
    assert chosen.asset_id == "strong"


def test_repeated_panel_is_penalized():
    first = _candidate("same", 0, 5.0)
    alternative = _candidate("other", 1, 4.0)
    chosen = select_panel([first, alternative], "the scene continues", used_ids={"same"})
    assert chosen is not None
    assert chosen.asset_id == "other"


def test_repeated_panel_does_not_dominate_when_pool_has_fresh_panels():
    candidates = [_candidate(f"p{index}", index, 5.0 - index * 0.1) for index in range(4)]
    chosen = select_panel(candidates, "the scene continues", used_ids={"p0"})
    assert chosen is not None
    assert chosen.asset_id != "p0"


def test_semantic_tags_and_camera_plan():
    assert "explosion" in narration_tags("The explosion destroys the wall")
    assert camera_effect("the monster attacked", 0) == "punch_zoom"
    assert camera_effect("the dragon finally appears", 0) == "push_up"
    assert camera_effect("a huge explosion erupts", 0) == "shake_zoom"


def test_weights_are_tunable_without_architecture_change():
    tuned = tune_weights(face=4.0, empty=3.0)
    assert tuned.face == 4.0
    assert tuned.empty == 3.0


def test_content_aware_plan_uses_semantic_camera_and_focus():
    class Span:
        section = "conflict"
        start_time = 0.0
        end_time = 3.0
        text = "The dragon attacked with its weapon."

    candidate = _candidate("dragon", 4, 6.0, action=1.0)
    candidate = PanelCandidate(
        candidate.asset_id,
        candidate.order_index,
        VisualFeatures(monsters=1.0, weapons=1.0, focal_points=((0.2, 0.3),)),
        candidate.visual_score,
    )
    scenes = plan_content_aware_scenes([Span()], [candidate])
    assert scenes[0]["asset_id"] == "dragon"
    assert scenes[0]["effect"] == "punch_zoom"
    assert scenes[0]["focus_x"] == 0.2


def test_shot_director_exhausts_rois_and_diversifies_motion():
    class Span:
        section = "conflict"
        start_time = 0.0
        end_time = 8.0
        text = "The warrior attacked, then the monster won."

    candidate = PanelCandidate(
        "panel", 0,
        VisualFeatures(
            face_visibility=1.0, action_pose=1.0, monsters=1.0,
            focal_points=((0.2, 0.2), (0.7, 0.4), (0.5, 0.8)),
        ),
        8.0,
    )
    shots = plan_shots([Span()], [candidate])
    assert len(shots) == 3
    assert [shot.roi_label for shot in shots] == ["face", "opponent", "detail"]
    assert len({shot.effect for shot in shots}) == 3
    assert shots[0].asset_id == shots[1].asset_id == shots[2].asset_id
    assert shots[0].focus_end_x == shots[1].focus_x


def test_shot_director_anticipates_next_dramatic_beat():
    class Setup:
        section = "setup"
        start_time = 0.0
        end_time = 3.0
        text = "He waits in silence."

    class Reveal:
        section = "twist"
        start_time = 3.0
        end_time = 6.0
        text = "Then the dragon finally appears."

    candidate = _candidate("p", 0, 4.0, face=1.0)
    shots = plan_shots([Setup(), Reveal()], [candidate])
    assert shots[0].end_time < 3.0
    assert shots[1].start_time == shots[0].end_time
    assert shots[1].camera_intent == "reveal"
    assert shots[1].camera_curve in {"push_in", "focus_shift", "slow_push_in", "pan_vertical"}


def test_layers_keep_editorial_decisions_out_of_camera_planner():
    class Span:
        section = "conflict"
        start_time = 0.0
        end_time = 3.0
        text = "The warrior attacked."

    candidate = _candidate("p", 0, 4.0, action=1.0)
    rois = rank_rois(candidate, Span.text)
    shots = plan_shots([Span()], [candidate])
    executed = execute_camera_plan(shots[0].order_index, shots[0].camera_curve)
    assert rois
    assert shots[0].camera_intent == "attack"
    assert shots[0].narration_timing == "visual_lead"
    assert executed.effect == shots[0].camera_curve
    assert executed.camera_curve == shots[0].camera_curve


def test_shot_director_uses_cuts_inside_one_panel_and_fades_between_panels():
    class LongSpan:
        section = "conflict"
        start_time = 0.0
        end_time = 8.0
        text = "The warrior attacked, then the monster appeared."

    first = _candidate("first", 0, 5.0, action=1.0)
    second = PanelCandidate(
        "second", 1,
        VisualFeatures(monsters=1.0, focal_points=((0.8, 0.7),)),
        5.0,
    )
    same_panel = plan_shots([LongSpan()], [first])
    assert same_panel[0].transition == "none"
    assert all(shot.transition == "cut" for shot in same_panel[1:])

    class Setup:
        section = "setup"
        start_time = 0.0
        end_time = 3.0
        text = "He waits."

    class Reveal:
        section = "twist"
        start_time = 3.0
        end_time = 6.0
        text = "The monster appears."

    changed_panel = plan_shots([Setup(), Reveal()], [first, second])
    assert changed_panel[0].asset_id != changed_panel[-1].asset_id
    assert changed_panel[-1].transition == "fade"


def test_shot_director_switches_panel_only_after_roi_exhaustion():
    class Span:
        section = "conflict"
        start_time = 0.0
        end_time = 9.0
        text = "The battle continues."

    first = PanelCandidate(
        "first", 0,
        VisualFeatures(action_pose=1.0, focal_points=((0.2, 0.2), (0.4, 0.4))),
        6.0,
    )
    second = PanelCandidate(
        "second", 1,
        VisualFeatures(monsters=1.0, focal_points=((0.8, 0.8),)),
        5.0,
    )
    shots = plan_shots([Span()], [first, second])
    assert len(shots) == 3
    assert [shot.asset_id for shot in shots[:2]] == ["first", "first"]
    assert shots[2].asset_id == "second"
    assert shots[2].transition == "fade"
