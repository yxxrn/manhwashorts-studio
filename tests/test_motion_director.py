from app.services.motion_director import audit_motion, plan_motion


def test_motion_director_is_deterministic_and_explains_choice():
    a = plan_motion(section="conflict", narration_tags={"attack"}, roi_label="weapon", seed=7)
    b = plan_motion(section="conflict", narration_tags={"attack"}, roi_label="weapon", seed=7)
    assert a == b
    assert a.reason and a.mode in {"guided_pan", "impact", "hold", "panel_stack"}


def test_motion_director_rejects_consecutive_impacts():
    plans = [
        plan_motion(section="conflict", narration_tags={"impact"}, index=i)
        for i in range(2)
    ]
    assert "strong_effects_consecutive" in audit_motion(plans)
