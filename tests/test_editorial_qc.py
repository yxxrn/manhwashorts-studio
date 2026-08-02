from app.services.editorial_qc import build_report


def test_qc_report_matches_required_contract():
    class Shot:
        asset_id = "p"
        roi_label = "face"
        focus_x = 0.2
        focus_y = 0.3
        focus_end_x = 0.7
        focus_end_y = 0.3
        start_time = 0.0
        end_time = 1.5
        section = "twist"

    class Cue:
        text = "The monster appears"

    report = build_report(scenes=[Shot(), Shot()], cues=[Cue()], duration=75.0)
    data = report.as_dict()
    required = {"duration", "resolution", "fps", "average_shot_duration", "longest_static_segment", "same_panel_same_crop_max", "unique_crop_count", "single_word_caption_ratio", "visual_evidence_confidence", "editorial_overlay_density", "template_similarity", "original_commentary", "editorial_visual_transformation", "episode_specificity", "template_repetition_risk", "rights_confidence", "source_cleanliness", "ending_has_payoff", "ending_has_visual_evidence", "full_playback_verified", "publish_allowed", "qc_pass", "failures"}
    assert required <= data.keys()
    assert data["duration"] == 75.0
    assert data["qc_pass"] is False
