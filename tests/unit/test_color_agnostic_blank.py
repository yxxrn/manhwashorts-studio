"""Task 3 RED/GREEN tests for color-agnostic border framing telemetry."""

from dataclasses import replace
from importlib import import_module
from math import floor
from pathlib import Path

import pytest
from PIL import Image, ImageDraw


def _module(name: str):
    try:
        return import_module(name)
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing runtime boundary {name}: {exc}")


def _visual_scoring():
    return _module("app.services.visual_scoring")


def _framing_analysis():
    module = _module("app.services.framing_analysis")
    assert callable(getattr(module, "build_color_agnostic_border_mask", None))
    return module


def _evidence(*, protected_bbox=None, reason="the provider reported no balloon geometry"):
    visual = _visual_scoring()
    evidence_type = getattr(visual, "PanelVisualEvidence", None)
    assert evidence_type is not None
    protected_regions = ()
    if protected_bbox is not None:
        protected_type = getattr(visual, "ProtectedRegionEvidence", None)
        assert protected_type is not None
        protected_regions = (
            protected_type(
                region_id="subject-1",
                kind="subject",
                normalized_bbox=protected_bbox,
                normalized_polygon=(),
                confidence=0.98,
                evidence_source="vision_regions_v1",
                required=True,
                minimum_coverage=0.98,
            ),
        )
    evidence = evidence_type(
        contract_version="COLOR_AGNOSTIC_BALLOON_FREE_V1",
        panel_id="panel-1",
        source_asset_id="asset-1",
        source_order=1,
        balloon_regions=(),
        protected_regions=protected_regions,
        balloon_mask_status="known_empty",
        mask_confidence=0.96,
        evidence_source="vision_geometry_v1",
        mask_reason=reason,
        evidence_hash="",
    )
    serialized = visual.panel_visual_evidence_json(evidence)
    return visual.parse_panel_visual_evidence(serialized)


def _unknown_evidence():
    visual = _visual_scoring()
    return visual.unknown_visual_evidence(
        panel_id="panel-1",
        source_asset_id="asset-1",
        source_order=1,
        reason="provider geometry was unavailable for this legacy observation",
    )


def _gutter_image(background, *, size=(160, 240)):
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 70, size[0] - 30, size[1] - 30), fill=(20, 30, 40))
    return image


def _gradient_gutter():
    image = Image.new("RGB", (160, 240))
    pixels = image.load()
    for y in range(image.height):
        value = 30 + round(y * 0.2)
        for x in range(image.width):
            pixels[x, y] = (value, 100, 180)
    ImageDraw.Draw(image).rectangle((45, 80, 115, 210), fill=(230, 230, 230))
    return image


def _detector(image, evidence=None, *, grid_long_edge=256):
    module = _framing_analysis()
    return module.build_color_agnostic_border_mask(
        image,
        evidence or _evidence(),
        grid_long_edge=grid_long_edge,
    )


def _source_cell(result, x_ratio, y_ratio):
    x = min(result.grid_width - 1, floor(x_ratio * result.grid_width))
    y = min(result.grid_height - 1, floor(y_ratio * result.grid_height))
    return result.edge_connected_mask[y][x], result.non_discardable_low_information_mask[y][x]


def test_detector_result_is_frozen_and_has_versioned_masks():
    result = _detector(_gutter_image((255, 255, 255)))
    assert result.detector_version == "COLOR_AGNOSTIC_BALLOON_FREE_V1:grid256:structure4"
    assert len(result.mask_sha256) == 64
    with pytest.raises(AttributeError):
        result.mask_sha256 = "0" * 64


@pytest.mark.parametrize(
    "background",
    ((255, 255, 255), (0, 0, 0), (128, 128, 128), (18, 92, 177)),
)
def test_border_colors_are_not_the_blank_definition(background):
    result = _detector(_gutter_image(background))
    assert result.edge_connected_blank_fraction > 0.20


def test_mild_gradient_gutter_is_detected_by_structure():
    result = _detector(_gradient_gutter())
    assert result.edge_connected_blank_fraction > 0.20


@pytest.mark.parametrize(
    "background,art,outline",
    (
        ((245, 245, 245), (250, 250, 250), (10, 10, 10)),
        ((15, 15, 15), (5, 5, 5), (245, 245, 245)),
    ),
)
def test_meaningful_light_and_dark_protected_art_is_retained(background, art, outline):
    image = Image.new("RGB", (160, 240), background)
    draw = ImageDraw.Draw(image)
    draw.ellipse((20, 40, 140, 205), fill=art, outline=outline, width=5)
    result = _detector(image, _evidence(protected_bbox=(0.1, 0.15, 0.9, 0.9)))
    assert result.protected_retained_fraction >= 0.98
    assert _source_cell(result, 0.5, 0.5)[0] is False


def test_sealed_internal_low_information_is_diagnostic_not_discardable():
    image = Image.new("RGB", (160, 240), (20, 20, 20))
    draw = ImageDraw.Draw(image)
    for y in range(0, image.height, 4):
        for x in range(0, image.width, 4):
            color = (245, 245, 245) if (x // 4 + y // 4) % 2 else (10, 10, 10)
            draw.rectangle((x, y, min(x + 3, image.width - 1), min(y + 3, image.height - 1)), fill=color)
    draw.rectangle((55, 95, 105, 145), fill=(128, 128, 128))
    result = _detector(image)
    edge, internal = _source_cell(result, 0.5, 0.5)
    assert internal is True
    assert edge is False
    assert result.non_discardable_low_information_fraction > 0.0


def test_source_area_mapping_is_integer_exact_and_ratios_are_six_decimals():
    image = _gutter_image((128, 128, 128))
    result = _detector(image, grid_long_edge=64)
    areas = []
    for y in range(result.grid_height):
        y0 = floor(y * image.height / result.grid_height)
        y1 = floor((y + 1) * image.height / result.grid_height)
        for x in range(result.grid_width):
            x0 = floor(x * image.width / result.grid_width)
            x1 = floor((x + 1) * image.width / result.grid_width)
            areas.append((x1 - x0) * (y1 - y0))
    assert sum(areas) == image.width * image.height
    edge_area = sum(
        area
        for area, cell in zip(
            areas,
            [cell for row in result.edge_connected_mask for cell in row],
            strict=True,
        )
        if cell
    )
    assert result.edge_connected_blank_fraction == round(
        edge_area / (image.width * image.height), 6
    )


def test_mask_hash_and_protected_geometry_are_deterministic_and_content_bound():
    image = _gutter_image((18, 92, 177))
    evidence = _evidence(protected_bbox=(0.2, 0.2, 0.8, 0.8))
    module = _framing_analysis()
    first = _detector(image, evidence)
    second = _detector(image, evidence)
    assert first.mask_sha256 == second.mask_sha256
    assert module.canonical_protected_geometry(evidence) == module.canonical_protected_geometry(evidence)
    assert "subject-1" in module.canonical_protected_geometry(evidence)[0]
    assert first.mask_sha256 != _detector(image, _evidence()).mask_sha256


def test_cache_identity_isolated_from_legacy_key_and_covers_each_detector_field(tmp_path):
    render = _module("app.services.render")
    reference_profile = _module("app.services.reference_profile")
    key = getattr(render, "reference_frame_cache_key", None)
    assert callable(key), "missing render.reference_frame_cache_key contract"
    profile = reference_profile.REFERENCE_MATCHED_SHORTS_V1
    common = {
        "image_path": Path(tmp_path) / "panel.png",
        "width": 360,
        "height": 640,
        "focus_x": 0.5,
        "focus_y": 0.5,
        "end_x": 0.5,
        "end_y": 0.5,
    }
    evidence = _evidence()
    protected = _evidence(protected_bbox=(0.2, 0.2, 0.8, 0.8))
    mask = _detector(_gutter_image((128, 128, 128)), evidence)
    baseline = key(**common, profile=None)
    assert baseline == key(**common, profile=None)
    assert baseline == key(**common, profile=None, border_mask=mask, evidence=evidence)
    profile_key = key(**common, profile=profile)
    assert profile_key == key(**common, profile=profile)
    variants = (
        key(
            **common,
            profile=profile,
            border_mask=replace(mask, detector_version="other-detector"),
            evidence=evidence,
        ),
        key(
            **common,
            profile=profile,
            border_mask=replace(mask, mask_sha256="0" * 64),
            evidence=evidence,
        ),
        key(**common, profile=profile, border_mask=mask, evidence=_unknown_evidence()),
        key(
            **common,
            profile=profile,
            border_mask=mask,
            evidence=_evidence(reason="a different affirmative geometry reason"),
        ),
        key(**common, profile=profile, border_mask=mask, evidence=protected),
    )
    assert all(candidate != profile_key for candidate in variants)
