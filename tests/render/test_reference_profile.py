import dataclasses
import hashlib
import importlib
import json

import pytest

SPEC_CANONICAL_KEYS = (
    "profile_id",
    "version",
    "duration_min_s",
    "duration_max_s",
    "shot_min",
    "shot_max",
    "hold_min_s",
    "hold_max_s",
    "emphasis_min_s",
    "emphasis_max_s",
    "hold_ratio_min",
    "hold_ratio_max",
    "emphasis_ratio_min",
    "emphasis_ratio_max",
    "mean_shot_min_s",
    "mean_shot_max_s",
    "hard_cut_ratio_min",
    "transition_min_s",
    "transition_max_s",
    "normal_zoom_max",
    "impact_zoom_max",
    "base_frame_zoom_max",
    "max_blank_fraction",
    "framing_contract_version",
    "framing_blank_target_fraction",
    "framing_balloon_intersection_max",
    "framing_mask_grid_long_edge",
    "framing_safe_area_margin",
    "caption_words_per_cue",
    "caption_uppercase",
    "caption_unicode_punctuation_allowed",
    "caption_top_sentence_allowed",
    "caption_safe_region",
    "caption_anchor",
    "caption_font_weight",
    "caption_font_height_ratio",
    "caption_italic",
    "caption_highlight_current_word",
    "caption_primary_color",
    "caption_outline_color",
    "caption_outline_pixels",
    "caption_shadow_color",
    "caption_shadow_alpha_max",
    "caption_alignment",
    "max_canonical_panel_uses",
    "consecutive_panel_reuse_allowed",
    "final_width",
    "final_height",
    "final_fps",
    "final_codec",
    "final_codec_profile",
    "final_pixel_format",
    "audio_lufs_target",
    "audio_true_peak_max_db",
    "unlicensed_music_sfx_allowed",
)


def _profile_module():
    try:
        return importlib.import_module("app.services.reference_profile")
    except Exception as exc:
        pytest.fail(
            "reference profile import boundary is unavailable in the test body: "
            f"{exc}"
        )


def test_reference_profile_has_the_complete_approved_contract():
    module = _profile_module()
    profile = module.REFERENCE_MATCHED_SHORTS_V1

    assert profile.profile_id == "reference_matched_shorts_v1"
    assert profile.version == "1.0.0"
    expected_values = {
        "duration_min_s": 50.0,
        "duration_max_s": 60.0,
        "shot_min": 36,
        "shot_max": 52,
        "hold_min_s": 0.65,
        "hold_max_s": 1.59,
        "emphasis_min_s": 1.6,
        "emphasis_max_s": 2.2,
        "hold_ratio_min": 0.70,
        "hold_ratio_max": 0.80,
        "emphasis_ratio_min": 0.20,
        "emphasis_ratio_max": 0.30,
        "mean_shot_min_s": 1.15,
        "mean_shot_max_s": 1.40,
        "hard_cut_ratio_min": 0.85,
        "transition_min_s": 0.12,
        "transition_max_s": 0.18,
        "normal_zoom_max": 1.08,
        "impact_zoom_max": 1.14,
        "base_frame_zoom_max": 1.35,
        "max_blank_fraction": 0.18,
        "framing_contract_version": "COLOR_AGNOSTIC_BALLOON_FREE_V1",
        "framing_blank_target_fraction": 0.03,
        "framing_balloon_intersection_max": 0.0,
        "framing_mask_grid_long_edge": 256,
        "framing_safe_area_margin": 0.03,
        "caption_words_per_cue": 1,
        "caption_uppercase": True,
        "caption_unicode_punctuation_allowed": False,
        "caption_top_sentence_allowed": False,
        "caption_safe_region": (0.15, 0.85, 0.50, 0.75),
        "caption_anchor": (0.50, 0.56),
        "caption_font_weight": "bold",
        "caption_font_height_ratio": 0.028,
        "caption_italic": True,
        "caption_highlight_current_word": False,
        "caption_primary_color": "white",
        "caption_outline_color": "black",
        "caption_outline_pixels": 6,
        "caption_shadow_color": "black",
        "caption_shadow_alpha_max": 0.35,
        "caption_alignment": 5,
        "max_canonical_panel_uses": 2,
        "consecutive_panel_reuse_allowed": False,
        "final_width": 1080,
        "final_height": 1920,
        "final_fps": 30,
        "final_codec": "h264",
        "final_codec_profile": "High",
        "final_pixel_format": "yuv420p",
        "audio_lufs_target": -14.0,
        "audio_true_peak_max_db": -1.5,
        "unlicensed_music_sfx_allowed": False,
    }
    for field_name, expected in expected_values.items():
        assert getattr(profile, field_name) == expected


def test_reference_profile_is_frozen_and_canonical_json_has_one_key_per_field():
    module = _profile_module()
    profile = module.REFERENCE_MATCHED_SHORTS_V1
    profile_fields = tuple(
        field.name for field in dataclasses.fields(type(profile))
    )

    assert profile_fields == SPEC_CANONICAL_KEYS
    assert dataclasses.is_dataclass(profile)
    assert dataclasses.fields(type(profile))
    assert type(profile).__dataclass_params__.frozen is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        profile.duration_min_s = 39.0

    canonical = module.canonical_profile_json(profile)
    expected_canonical = json.dumps(
        dataclasses.asdict(profile),
        sort_keys=True,
        separators=(",", ":"),
    )
    assert canonical == expected_canonical
    pairs = json.loads(canonical, object_pairs_hook=list)
    keys = [key for key, _value in pairs]
    assert len(keys) == len(profile_fields)
    assert len(set(keys)) == len(keys)
    assert tuple(keys) == tuple(sorted(keys))
    assert set(keys) == set(profile_fields)


def test_reference_profile_hash_is_stable_and_sensitive_to_each_field_category():
    module = _profile_module()
    profile = module.REFERENCE_MATCHED_SHORTS_V1
    canonical = module.canonical_profile_json(profile)
    expected_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    assert module.profile_hash(profile) == expected_hash
    assert module.profile_hash(profile) == module.profile_hash(profile)

    changed_profiles = {
        "caption": dataclasses.replace(
            profile, caption_font_weight="semibold"
        ),
        "motion": dataclasses.replace(profile, normal_zoom_max=1.05),
        "codec_audio": dataclasses.replace(
            profile,
            final_codec_profile="Main",
            audio_lufs_target=-13.0,
        ),
        "reuse": dataclasses.replace(profile, max_canonical_panel_uses=1),
    }
    changed_hashes = {
        category: module.profile_hash(changed)
        for category, changed in changed_profiles.items()
    }
    assert all(value != expected_hash for value in changed_hashes.values())
    assert len(set(changed_hashes.values())) == len(changed_hashes)

    framing_changes = (
        ("framing_contract_version", "OTHER_CONTRACT"),
        ("framing_blank_target_fraction", 0.01),
        ("framing_balloon_intersection_max", 0.01),
        ("framing_mask_grid_long_edge", 128),
        ("framing_safe_area_margin", 0.04),
    )
    assert all(
        module.profile_hash(dataclasses.replace(profile, **{field: value}))
        != expected_hash
        for field, value in framing_changes
    )


def _resolve_legacy_selector(module, selector):
    try:
        resolved = module.resolve_reference_profile(selector)
    except Exception as exc:
        reason_code = getattr(exc, "reason_code", None) or getattr(exc, "code", None)
        assert reason_code, (
            "an explicit resolver error must expose a stable reason_code or code"
        )
        return ("error", type(exc).__name__, reason_code)
    return ("value", resolved)


def test_reference_profile_v2_is_the_60_fps_default_contract():
    module = _profile_module()
    profile = module.REFERENCE_MATCHED_SHORTS_V2
    assert profile.profile_id == "reference_matched_shorts_v2"
    assert profile.version == "2.0.0"
    assert profile.final_fps == 60
    assert module.resolve_reference_profile(profile.profile_id) is profile


def test_reference_profile_resolution_is_explicit_and_legacy_safe():
    module = _profile_module()
    profile = module.REFERENCE_MATCHED_SHORTS_V1

    selected = module.resolve_reference_profile("reference_matched_shorts_v1")
    assert selected.profile_id == profile.profile_id
    assert module.profile_hash(selected) == module.profile_hash(profile)

    for selector in (None, "", "default", "legacy", "unknown_profile"):
        first = _resolve_legacy_selector(module, selector)
        second = _resolve_legacy_selector(module, selector)
        assert first == second
        if first[0] == "value":
            assert first[1] is None


def test_reference_duration_window_matches_narration_contract():
    """The render window must admit every narration the script contract accepts.

    The narration pipeline enforces 115-125 spoken words and 50-60 second
    candidates; a render profile that rejects durations above 50 seconds makes
    every strict-valid narration unrenderable. The shot window must keep the
    mean-shot band reachable across the whole duration window.
    """
    import math

    module = _profile_module()
    profile = module.REFERENCE_MATCHED_SHORTS_V1

    assert (profile.duration_min_s, profile.duration_max_s) == (50.0, 60.0)
    assert profile.shot_min >= math.ceil(
        profile.duration_min_s / profile.mean_shot_max_s
    )
    assert profile.shot_max <= math.floor(
        profile.duration_max_s / profile.mean_shot_min_s
    )


def test_review_framing_quality_key_stops_overcropping_inside_three_percent_target():
    reference_profile = _profile_module()
    inside_less_zoom = reference_profile.review_framing_quality_key(
        0.028, 1.875, 1.0
    )
    inside_more_zoom = reference_profile.review_framing_quality_key(
        0.0, 2.5, 1.0
    )
    outside = reference_profile.review_framing_quality_key(
        0.0469, 1.5, 1.0
    )

    assert inside_less_zoom < inside_more_zoom
    assert inside_more_zoom < outside
    assert reference_profile.REVIEW_PREFERRED_FRAME_EDGE_BLANK_FRACTION == 0.03
    assert reference_profile.REVIEW_MAX_FRAME_EDGE_BLANK_FRACTION == 0.08
