from PIL import Image, ImageDraw

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
