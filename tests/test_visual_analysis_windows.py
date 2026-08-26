from __future__ import annotations

import io

from PIL import Image, ImageDraw


def _tall_payload(width: int = 900, height: int = 9000) -> bytes:
    image = Image.new("RGB", (width, height), (74, 88, 112))
    draw = ImageDraw.Draw(image)
    for y in range(0, height, 180):
        draw.rectangle((0, y, width - 1, min(height - 1, y + 70)), fill=(120, 63, 91))
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=90)
    return output.getvalue()


def test_tall_panel_uses_complete_overlapping_detail_windows():
    from app.services.cloud_multimodal import (
        CloudPanelInput,
        _visual_analysis_windows,
        _visual_request_panel,
    )

    payload = _tall_payload()
    panel = CloudPanelInput(
        panel_id="panel-tall",
        source_asset_id="asset-tall",
        source_order=0,
        mime_type="image/jpeg",
        payload=payload,
        panel_bounds=(0, 0, 900, 9000),
        source_dimensions=(900, 9000),
        prepared_order=0,
    )
    windows = _visual_analysis_windows(panel)
    assert 2 <= len(windows) <= 12
    assert windows[0]["y0"] == 0
    assert windows[-1]["y1"] == 9000
    for left, right in zip(windows, windows[1:], strict=False):
        assert right["y0"] < left["y1"]
        assert left["overlap_below"] == left["y1"] - right["y0"]
        assert right["overlap_above"] == left["y1"] - right["y0"]
        assert left["encoded_width"] <= 512
        assert left["encoded_height"] <= 768
    request_panel = _visual_request_panel(panel)
    assert request_panel["analysis_window_source_size"] == [900, 9000]
    assert len(request_panel["analysis_windows"]) == len(windows)


def test_window_geometry_reconciles_into_canonical_panel_coordinates():
    import pytest

    from app.services.cloud_multimodal import (
        CloudPanelInput,
        _reconcile_window_geometry,
        _visual_analysis_windows,
    )

    payload = _tall_payload(width=200, height=2400)
    panel = CloudPanelInput(
        panel_id="panel-geometry",
        source_asset_id="asset-geometry",
        source_order=0,
        mime_type="image/jpeg",
        payload=payload,
        panel_bounds=(0, 0, 200, 2400),
        source_dimensions=(200, 2400),
        prepared_order=0,
    )
    windows = _visual_analysis_windows(panel)
    assert len(windows) >= 2
    evidence = {}
    for window in windows:
        idx = window["window_index"]
        balloons = []
        if idx == 1:
            balloons = [
                {
                    "region_id": "b1",
                    "kind": "speech_balloon",
                    "normalized_bbox": [0.2, 0.25, 0.8, 0.75],
                    "normalized_polygon": None,
                    "confidence": 0.9,
                    "evidence_source": "visual_geometry",
                    "mask_status": "known_nonempty",
                }
            ]
        evidence[idx] = {
            "balloon_mask_status": "known_nonempty" if balloons else "known_empty",
            "balloon_regions": balloons,
            "protected_regions": [],
            "mask_confidence": 0.9,
            "evidence_source": "visual_geometry",
            "mask_reason": "test",
            "panel_id": f"window-{idx}",
            "source_asset_id": "asset-geometry",
            "source_order": 0,
        }
    result = _reconcile_window_geometry(panel, windows, evidence)
    assert result is not None
    assert result["balloon_mask_status"] == "known_nonempty"
    assert len(result["balloon_regions"]) == 1
    source = windows[1]
    expected_y0 = (source["y0"] + 0.25 * (source["y1"] - source["y0"])) / 2400
    expected_y1 = (source["y0"] + 0.75 * (source["y1"] - source["y0"])) / 2400
    assert result["balloon_regions"][0]["normalized_bbox"] == pytest.approx(
        [0.2, expected_y0, 0.8, expected_y1]
    )


def test_visual_runner_repairs_tall_unknown_geometry_with_windows():
    import hashlib

    from app.services import visual_scoring
    from app.services.cloud_multimodal import (
        CloudModelIdentity,
        CloudPanelInput,
        CloudStageRunner,
        MemoryStageCache,
    )

    class FakeProvider:
        model_id = "fake-vision"

        def observe(self, request):
            panel = request.panels[0]
            panel_id = panel["panel_id"]
            transient = "::window::" in panel_id
            return [
                {
                    "panel_id": panel_id,
                    "visible_facts": ["visible action"],
                    "dialogue_or_ocr": [],
                    "inferences": [],
                    "uncertainties": [],
                    "entities": [],
                    "state_changes": [],
                    "causal_links": [],
                    "evidence_refs": [panel_id],
                    "visual_evidence": {
                        "balloon_mask_status": "known_empty" if transient else "unknown",
                        "balloon_regions": [],
                        "protected_regions": [],
                        "mask_confidence": 0.9 if transient else 0.0,
                        "evidence_source": "visual_geometry" if transient else "unknown_geometry",
                        "mask_reason": "validated window" if transient else "unknown geometry",
                        "panel_id": panel_id,
                        "source_asset_id": panel["source_asset_id"],
                        "source_order": panel["source_order"],
                    },
                }
            ]

    payload = _tall_payload(width=220, height=1200)
    panel = CloudPanelInput(
        panel_id="panel-live",
        source_asset_id="asset-live",
        source_order=0,
        mime_type="image/jpeg",
        payload=payload,
        source_checksum=hashlib.sha256(payload).hexdigest(),
        panel_bounds=(0, 0, 220, 1200),
        source_dimensions=(220, 1200),
        prepared_order=0,
    )
    prompt_version, _, _ = visual_scoring.load_visual_evidence_instruction()
    identity = CloudModelIdentity(
        provider="fake",
        model="fake-vision",
        model_version="test",
        endpoint="local",
        prompt_versions={"visual": prompt_version},
    )
    runner = CloudStageRunner(
        provider=FakeProvider(),
        model_identity=identity,
        cache=MemoryStageCache(),
        max_attempts=2,
        visual_parallel_workers=8,
    )
    row = runner.run_visual_evidence((panel,)).panels[0]
    assert row["visual_evidence"]["balloon_mask_status"] == "known_empty"
    assert row["geometry_mode"] == "window-geometry-reconciled-v1"
    assert row.get("fallback_mode") is None
    assert row["window_geometry_request_count"] >= 2
