from app.services.shot_director import plan_shots
from app.services.visual_scoring import PanelCandidate, VisualFeatures


def test_asset_cooldown_prefers_new_panel_after_two_shots():
    class Span:
        section = "conflict"
        start_time = 0.0
        end_time = 8.0
        text = "The battle continues."

    first = PanelCandidate("first", 0, VisualFeatures(action_pose=1.0, focal_points=((.2, .2), (.4, .4), (.6, .6))), 6.0)
    second = PanelCandidate("second", 1, VisualFeatures(monsters=1.0, focal_points=((.8, .8),)), 5.0)
    shots = plan_shots([Span()], [first, second])
    assert shots[0].asset_id == shots[1].asset_id == "first"
    assert shots[2].asset_id == "second"
