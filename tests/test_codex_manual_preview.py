from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "review" / "render_codex_manual_preview.py"


def module():
    spec = importlib.util.spec_from_file_location("codex_manual_preview", SCRIPT)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def manifest():
    return {
        "assets": [
            {
                "source_order": order,
                "asset_id": f"asset-{order}",
                "checksum": f"checksum-{order}",
                "width": 900,
                "height": 1400,
                "review_path": f"ordered/{order:03d}.jpg",
            }
            for order in range(24)
        ]
    }


def valid_plan():
    durations = [1.8, 2.2, 2.4, 2.4, 2.6, 2.4, 2.5, 2.5, 1.9, 2.1, 1.8,
                 2.1, 2.0, 1.9, 2.3, 2.4, 2.2, 2.6, 2.7, 2.8, 2.8, 2.6, 3.2]
    return {
        "contract_version": "codex_manual_vision_review_v2",
        "random_sampling": False,
        "publish_allowed": False,
        "rights_status": "internal review only",
        "fps": 60,
        "width": 1080,
        "height": 1920,
        "shots": [
            {
                "source_order": order,
                "duration": duration,
                "crop": [0.1, 0.1, 0.9, 0.9],
                "motion": "pan_right" if order % 2 else "pan_left",
            }
            for order, duration in zip(range(1, 24), durations, strict=True)
        ],
        "captions": [
            {"start_shot": 0, "end_shot": 2, "text": "THE BATTLEFIELD IS COLLAPSING"},
        ],
    }


def test_supported_motion_filters_are_distinct_and_hold_is_stable():
    loaded = module()
    motions = loaded.SUPPORTED_MOTIONS
    assert {"hold", "pan_left", "pan_right", "pan_up", "pan_down", "diagonal", "push_in", "pull_out"} <= set(motions)
    filters = {motion: loaded.build_motion_filter(motion, 2.0) for motion in motions}
    assert filters["hold"] == filters["hold"]
    assert len(set(filters.values())) == len(filters)


def test_rejects_unknown_motion_intent():
    with pytest.raises(ValueError, match="preview.motion_invalid"):
        module().build_motion_filter("shake", 2.0)


def test_accepts_legacy_30_fps_plan_for_reproducibility():
    plan = valid_plan()
    plan["fps"] = 30
    validated = module().validate_edit_plan(plan, manifest())
    assert validated.fps == 30


def test_accepts_exact_542_second_chronological_plan():
    validated = module().validate_edit_plan(valid_plan(), manifest())
    assert validated.total_duration == pytest.approx(54.2)
    assert [shot.source_order for shot in validated.shots] == list(range(1, 24))


def test_rejects_duration_outside_50_to_60_seconds():
    plan = valid_plan()
    plan["shots"][0]["duration"] = 10.0
    with pytest.raises(ValueError, match="preview.duration_out_of_range"):
        module().validate_edit_plan(plan, manifest())


def test_rejects_duplicate_or_missing_source_order():
    plan = valid_plan()
    plan["shots"][3]["source_order"] = 2
    with pytest.raises(ValueError, match="preview.source_order_coverage_invalid"):
        module().validate_edit_plan(plan, manifest())


def test_rejects_caption_punctuation():
    plan = valid_plan()
    plan["captions"][0]["text"] = "RUN NOW!"
    with pytest.raises(ValueError, match="preview.caption_contract_invalid"):
        module().validate_edit_plan(plan, manifest())


def test_rejects_invalid_normalized_crop():
    plan = valid_plan()
    plan["shots"][0]["crop"] = [0.4, 0.2, 0.3, 0.8]
    with pytest.raises(ValueError, match="preview.crop_invalid"):
        module().validate_edit_plan(plan, manifest())
