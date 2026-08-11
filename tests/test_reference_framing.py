"""RED contracts for deterministic content-aware reference framing."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

TARGET_WIDTH = 360
TARGET_HEIGHT = 640
OVERSAMPLE_SIZE = (414, 736)


def _framing_helper():
    from app.services import render

    helper = getattr(render, "prepare_reference_frame", None)
    assert callable(helper), "missing render.prepare_reference_frame contract"
    return helper


def _cache_key_helper():
    from app.services import render

    helper = getattr(render, "reference_frame_cache_key", None)
    assert callable(helper), "missing render.reference_frame_cache_key contract"
    return helper


def _write_gutter_fixture(path: Path) -> None:
    image = Image.new("RGB", (900, 2400), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 1250, 900, 2390), fill=(32, 54, 92))
    draw.ellipse((230, 1120, 670, 1320), fill=(8, 8, 8))
    draw.rectangle((300, 1180, 600, 1260), fill=(240, 240, 240))
    image.save(path, "PNG")


def _write_focus_fixture(path: Path) -> None:
    image = Image.new("RGB", (900, 2400), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 80, 820, 1200), fill=(160, 45, 45))
    draw.ellipse((260, 420, 640, 620), fill=(10, 10, 10))
    draw.rectangle((80, 1200, 820, 2320), fill=(40, 70, 150))
    draw.ellipse((230, 1760, 670, 1980), fill=(10, 10, 10))
    image.save(path, "PNG")


def _blank_fraction(path: Path) -> float:
    with Image.open(path) as image:
        pixels = list(image.convert("RGB").getdata())
    blank = sum(
        1 for red, green, blue in pixels
        if red >= 245 and green >= 245 and blue >= 245
    )
    return blank / len(pixels)


def _result_path(result) -> Path:
    path = getattr(result, "path", None)
    assert path is not None, "reference preparation must expose its output path"
    path = Path(path)
    assert path.is_file(), f"prepared frame was not written: {path}"
    return path


def _visual_evidence(
    *,
    balloon_regions=(),
    protected_regions=(),
    status="known_empty",
    panel_id="panel-framing",
    source_asset_id="asset-framing",
    source_order=1,
):
    from app.services import visual_scoring

    evidence = visual_scoring.PanelVisualEvidence(
        contract_version="COLOR_AGNOSTIC_BALLOON_FREE_V1",
        panel_id=panel_id,
        source_asset_id=source_asset_id,
        source_order=source_order,
        balloon_regions=tuple(balloon_regions),
        protected_regions=tuple(protected_regions),
        balloon_mask_status=status,
        mask_confidence=0.96,
        evidence_source="vision_geometry_v1",
        mask_reason="affirmative visual geometry result",
        evidence_hash="",
    )
    return visual_scoring.parse_panel_visual_evidence(
        visual_scoring.panel_visual_evidence_json(evidence)
    )


def _balloon(bbox):
    from app.services import visual_scoring

    return visual_scoring.BalloonRegionEvidence(
        region_id="balloon-framing",
        kind="speech_balloon",
        normalized_bbox=bbox,
        normalized_polygon=(),
        confidence=0.99,
        evidence_source="vision_geometry_v1",
        mask_status="known_nonempty",
    )


def _protected(kind, bbox, minimum_coverage):
    from app.services import visual_scoring

    return visual_scoring.ProtectedRegionEvidence(
        region_id=f"protected-{kind}",
        kind=kind,
        normalized_bbox=bbox,
        normalized_polygon=(),
        confidence=0.99,
        evidence_source="vision_regions_v1",
        required=True,
        minimum_coverage=minimum_coverage,
    )


def test_reference_profile_declares_content_framing_and_hashes_every_field():
    from app.services import reference_profile

    profile = reference_profile.REFERENCE_MATCHED_SHORTS_V1
    assert profile.base_frame_zoom_max == pytest.approx(1.35)
    assert profile.max_blank_fraction == pytest.approx(0.18)
    framing_fields = {
        "framing_contract_version": "OTHER_CONTRACT",
        "framing_blank_target_fraction": 0.1,
        "framing_balloon_intersection_max": 0.01,
        "framing_mask_grid_long_edge": 128,
        "framing_safe_area_margin": 0.05,
    }
    assert profile.framing_contract_version == "COLOR_AGNOSTIC_BALLOON_FREE_V1"
    assert profile.framing_blank_target_fraction == pytest.approx(0.0)
    assert profile.framing_balloon_intersection_max == pytest.approx(0.0)
    assert profile.framing_mask_grid_long_edge == 256
    assert profile.framing_safe_area_margin == pytest.approx(0.03)
    canonical = reference_profile.canonical_profile_json(profile)
    fields = asdict(profile)
    assert set(json.loads(canonical)) == set(fields)
    assert canonical.count('"base_frame_zoom_max"') == 1
    assert canonical.count('"max_blank_fraction"') == 1
    for field in framing_fields:
        assert canonical.count(f'"{field}"') == 1
    assert reference_profile.profile_hash(profile) != reference_profile.profile_hash(
        replace(profile, max_blank_fraction=0.17)
    )
    assert reference_profile.profile_hash(profile) != reference_profile.profile_hash(
        replace(profile, base_frame_zoom_max=1.21)
    )
    for field, value in framing_fields.items():
        assert reference_profile.profile_hash(profile) != reference_profile.profile_hash(
            replace(profile, **{field: value})
        )


def test_candidate_rejects_any_nonzero_balloon_overlap():
    from app.services import framing_analysis

    image = Image.new("RGB", (1000, 1000), (40, 50, 60))
    evidence = _visual_evidence(
        balloon_regions=(_balloon((0.1, 0.1, 0.101, 0.101)),),
        status="known_nonempty",
    )
    mask = framing_analysis.build_color_agnostic_border_mask(
        image, evidence, grid_long_edge=64
    )

    feasible, telemetry = framing_analysis.candidate_is_feasible(
        (0, 0, 200, 200),
        evidence,
        mask,
        image.size,
        (100, 100),
    )

    assert feasible is False
    assert telemetry.balloon_mask_intersection_ratio > 0.0
    assert telemetry.rejection_code == "visual.balloon_mask_overlap"


@pytest.mark.parametrize(
    ("kind", "telemetry_field", "minimum"),
    (
        ("subject", "subject_coverage", 0.98),
        ("face", "face_coverage", 0.98),
        ("action", "action_coverage", 0.95),
        ("continuity_context", "continuity_context_coverage", 0.95),
        ("effect", "effect_coverage", 0.90),
    ),
)
def test_candidate_enforces_required_protected_coverage(kind, telemetry_field, minimum):
    from app.services import framing_analysis

    image = Image.new("RGB", (1000, 1000), (40, 50, 60))
    evidence = _visual_evidence(
        protected_regions=(_protected(kind, (0.75, 0.75, 0.95, 0.95), minimum),)
    )
    mask = framing_analysis.build_color_agnostic_border_mask(
        image, evidence, grid_long_edge=64
    )

    feasible, telemetry = framing_analysis.candidate_is_feasible(
        (0, 0, 400, 400),
        evidence,
        mask,
        image.size,
        (100, 100),
    )

    assert feasible is False
    assert getattr(telemetry, telemetry_field) < minimum
    assert telemetry.rejection_code == f"visual.protected_{kind}_coverage"


def test_candidate_rejects_crop_below_native_resolution_guard():
    from app.services import framing_analysis

    image = Image.new("RGB", (180, 180), (40, 50, 60))
    evidence = _visual_evidence()
    mask = framing_analysis.build_color_agnostic_border_mask(
        image, evidence, grid_long_edge=64
    )

    feasible, telemetry = framing_analysis.candidate_is_feasible(
        (0, 0, 100, 100),
        evidence,
        mask,
        image.size,
        (180, 180),
    )

    assert feasible is False
    assert telemetry.base_zoom > telemetry.source_resolution_zoom_cap
    assert telemetry.rejection_code == "visual.source_resolution_insufficient"


def test_reference_preparation_reports_blank_infeasible_telemetry(tmp_path):
    from app.services import framing_analysis, reference_profile, render

    source = tmp_path / "uniform-source.png"
    Image.new("RGB", (900, 2400), (64, 64, 64)).save(source)
    evidence = _visual_evidence()
    with Image.open(source) as image:
        mask = framing_analysis.build_color_agnostic_border_mask(
            image, evidence, grid_long_edge=64
        )

    prepared = render.prepare_reference_frame(
        source,
        tmp_path / "uniform-prepared.jpg",
        TARGET_WIDTH,
        TARGET_HEIGHT,
        0.5,
        0.5,
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        evidence=evidence,
        border_mask=mask,
    )

    assert prepared.telemetry is not None
    assert prepared.telemetry.edge_connected_blank_fraction > 0.0
    assert prepared.telemetry.fallback_reason == "visual.blank_infeasible"


@pytest.mark.parametrize("evidence", (None, {"malformed": True}), ids=("missing", "malformed"))
def test_reference_preparation_rejects_missing_or_malformed_evidence(tmp_path, evidence):
    from app.services import reference_profile, render

    source = tmp_path / "missing-evidence-source.png"
    Image.new("RGB", (900, 2400), (64, 64, 64)).save(source)

    with pytest.raises(render.RenderError, match="visual\\.panel_lineage_unavailable"):
        render.prepare_reference_frame(
            source,
            tmp_path / "missing-evidence-prepared.jpg",
            TARGET_WIDTH,
            TARGET_HEIGHT,
            0.5,
            0.5,
            profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
            evidence=evidence,
        )


def test_reference_preparation_rejects_structurally_valid_unknown_evidence(tmp_path):
    from app.services import reference_profile, render, visual_scoring

    source = tmp_path / "unknown-evidence-source.png"
    Image.new("RGB", (900, 2400), (64, 64, 64)).save(source)
    evidence = visual_scoring.unknown_visual_evidence(
        panel_id="panel-framing",
        source_asset_id="asset-framing",
        source_order=1,
        reason="provider geometry was unavailable",
    )

    with pytest.raises(render.RenderError, match="visual\\.balloon_mask_unknown"):
        render.prepare_reference_frame(
            source,
            tmp_path / "unknown-evidence-prepared.jpg",
            TARGET_WIDTH,
            TARGET_HEIGHT,
            0.5,
            0.5,
            profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
            evidence=evidence,
        )


def test_detector_public_boundary_rejects_unknown_and_malformed_evidence():
    from app.services import framing_analysis, visual_scoring

    image = Image.new("RGB", (160, 240), (64, 64, 64))
    unknown = visual_scoring.unknown_visual_evidence(
        panel_id="panel-framing",
        source_asset_id="asset-framing",
        source_order=1,
        reason="provider geometry was unavailable",
    )
    with pytest.raises(visual_scoring.VisualEvidenceError, match="visual\\.balloon_mask_unknown"):
        framing_analysis.build_color_agnostic_border_mask(image, unknown)
    with pytest.raises(visual_scoring.VisualEvidenceError, match="visual\\.panel_lineage_unavailable"):
        framing_analysis.build_color_agnostic_border_mask(image, None)


def test_reference_preparation_rejects_blank_gutter_and_preserves_focused_artwork(tmp_path):
    from app.services import reference_profile, render

    source = tmp_path / "white-top-artwork.png"
    _write_gutter_fixture(source)
    evidence = _visual_evidence()
    legacy = tmp_path / "legacy.jpg"
    render.crop_to_vertical(source, legacy, TARGET_WIDTH, TARGET_HEIGHT, 0.5, 0.2)
    assert _blank_fraction(legacy) > reference_profile.REFERENCE_MATCHED_SHORTS_V1.max_blank_fraction

    prepared = _framing_helper()(
        source,
        tmp_path / "prepared.jpg",
        TARGET_WIDTH,
        TARGET_HEIGHT,
        0.5,
        0.82,
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        evidence=evidence,
    )
    output = _result_path(prepared)
    with Image.open(output) as image:
        assert image.size == OVERSAMPLE_SIZE
    telemetry = prepared.telemetry
    assert telemetry is not None
    assert prepared.blank_fraction == pytest.approx(
        telemetry.edge_connected_blank_fraction
    )
    assert prepared.blank_fraction > 0.0
    assert telemetry.fallback_reason == 'visual.blank_infeasible'
    assert telemetry.rejection_code is None
    assert telemetry.balloon_mask_intersection_ratio == pytest.approx(0.0)
    assert telemetry.subject_coverage >= 0.98
    assert telemetry.face_coverage >= 0.98
    assert telemetry.action_coverage >= 0.95
    assert telemetry.continuity_context_coverage >= 0.95
    assert telemetry.effect_coverage >= 0.90
    assert prepared.base_zoom <= reference_profile.REFERENCE_MATCHED_SHORTS_V1.base_frame_zoom_max
    left, top, right, bottom = prepared.crop_box
    assert 0 <= left < right <= 900
    assert 0 <= top < bottom <= 2400
    assert top <= 0.82 * 2400 <= bottom


def test_reference_focus_changes_static_roi_and_same_inputs_are_deterministic(tmp_path):
    from app.services import reference_profile

    source = tmp_path / "two-focus-artworks.png"
    _write_focus_fixture(source)
    helper = _framing_helper()
    profile = reference_profile.REFERENCE_MATCHED_SHORTS_V1
    evidence = _visual_evidence()

    upper = helper(
        source,
        tmp_path / "upper-a.jpg",
        TARGET_WIDTH,
        TARGET_HEIGHT,
        0.5,
        0.28,
        profile=profile,
        evidence=evidence,
    )
    lower = helper(
        source,
        tmp_path / "lower.jpg",
        TARGET_WIDTH,
        TARGET_HEIGHT,
        0.5,
        0.80,
        profile=profile,
        evidence=evidence,
    )
    upper_repeat = helper(
        source,
        tmp_path / "upper-b.jpg",
        TARGET_WIDTH,
        TARGET_HEIGHT,
        0.5,
        0.28,
        profile=profile,
        evidence=evidence,
    )
    assert upper.crop_box != lower.crop_box
    assert Path(upper.path).read_bytes() != Path(lower.path).read_bytes()
    assert upper.crop_box == upper_repeat.crop_box
    assert Path(upper.path).read_bytes() == Path(upper_repeat.path).read_bytes()
    assert upper.blank_fraction <= profile.max_blank_fraction
    assert lower.blank_fraction <= profile.max_blank_fraction
    assert upper.base_zoom <= profile.base_frame_zoom_max
    assert lower.base_zoom <= profile.base_frame_zoom_max


def test_reference_frame_cache_key_includes_profile_and_framing_inputs(tmp_path):
    from app.services import reference_profile

    key = _cache_key_helper()
    profile = reference_profile.REFERENCE_MATCHED_SHORTS_V1
    common = {
        "image_path": tmp_path / "panel.png",
        "width": TARGET_WIDTH,
        "height": TARGET_HEIGHT,
        "focus_x": 0.5,
        "focus_y": 0.5,
        "end_x": 0.5,
        "end_y": 0.5,
    }
    baseline = key(**common, profile=profile)
    assert baseline != key(**common, profile=replace(profile, max_blank_fraction=0.17))
    assert baseline != key(**{**common, "focus_y": 0.8}, profile=profile)
    assert baseline != key(**common, profile=None)


def test_profile_none_keeps_legacy_editorial_frame_bytes(tmp_path):
    from app.services import render

    source = tmp_path / "legacy-source.png"
    _write_focus_fixture(source)
    direct = tmp_path / "direct.jpg"
    editorial = tmp_path / "editorial.jpg"
    render.crop_to_vertical(source, direct, TARGET_WIDTH, TARGET_HEIGHT, 0.5, 0.5)
    render.editorial_frame(
        source,
        editorial,
        TARGET_WIDTH,
        TARGET_HEIGHT,
        0.5,
        0.5,
        0.5,
        0.5,
        "normal",
        profile=None,
    )
    assert direct.read_bytes() == editorial.read_bytes()


def test_reference_preparation_does_not_mutate_source(tmp_path):
    from app.services import reference_profile

    source = tmp_path / "immutable-source.png"
    _write_focus_fixture(source)
    evidence = _visual_evidence()
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    _framing_helper()(
        source,
        tmp_path / "prepared.jpg",
        TARGET_WIDTH,
        TARGET_HEIGHT,
        0.5,
        0.8,
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        evidence=evidence,
    )
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_real_task9c1_panels_have_auditable_reference_preparation_smoke(tmp_path):
    from app.services import reference_profile

    sample_db = Path("data/p0-real3-luna-phase2-final/sample.db")
    if not sample_db.is_file():
        pytest.skip("Task9C1 real-panel fixture is not present in this checkout")
    storage_root = sample_db.parent / "storage"
    with sqlite3.connect(sample_db) as connection:
        rows = connection.execute(
            "SELECT id, order_index, storage_key, width, height "
            "FROM source_assets WHERE project_id LIKE '28f509%' AND type = 'image' "
            "ORDER BY order_index"
        ).fetchall()
    assert len(rows) == 24
    assert [row[1] for row in rows] == list(range(24))

    helper = _framing_helper()
    metrics = []
    prepared_paths = []
    for index, (asset_id, source_order, storage_key, width, height) in enumerate(rows):
        source = (storage_root / storage_key).resolve()
        assert source.is_file()
        with Image.open(source) as image:
            assert image.size == (width, height)
        evidence = _visual_evidence(
            panel_id=f"panel-{asset_id}",
            source_asset_id=asset_id,
            source_order=source_order,
        )
        result = helper(
            source,
            tmp_path / f"prepared-{index:03d}.jpg",
            TARGET_WIDTH,
            TARGET_HEIGHT,
            0.5,
            0.56,
            profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
            evidence=evidence,
        )
        prepared_path = _result_path(result)
        prepared_paths.append(prepared_path)
        assert result.telemetry is not None
        assert result.telemetry.mask_source == "vision_geometry_v1"
        metrics.append(
            {
                "source_order": source_order,
                "blank_fraction": result.blank_fraction,
                "base_zoom": result.base_zoom,
                "crop_box": list(result.crop_box),
                "edge_connected_blank_fraction": result.telemetry.edge_connected_blank_fraction,
            }
        )

    columns = 4
    tile_width, tile_height = 444, 780
    sheet = Image.new(
        "RGB",
        (columns * tile_width, ((len(metrics) + columns - 1) // columns) * tile_height),
        (24, 24, 30),
    )
    draw = ImageDraw.Draw(sheet)
    for index, (metric, prepared_path) in enumerate(
        zip(metrics, prepared_paths, strict=True)
    ):
        x = (index % columns) * tile_width
        y = (index // columns) * tile_height
        draw.text(
            (x + 8, y + 8),
            f"order {metric['source_order']:02d} blank {metric['blank_fraction']:.3f}",
            fill="white",
        )
        with Image.open(prepared_path) as prepared:
            thumb = prepared.convert("RGB")
            thumb.thumbnail((414, 736), Image.Resampling.LANCZOS)
            sheet.paste(thumb, (x + 15, y + 36))
    contact_sheet = tmp_path / "prepared-frame-contact-sheet.jpg"
    sheet.save(contact_sheet, "JPEG", quality=94)
    report = tmp_path / "reference-panel-smoke.json"
    report.write_text(
        json.dumps(
            {
                "asset_count": len(metrics),
                "blank_fraction_min": min(metric["blank_fraction"] for metric in metrics),
                "blank_fraction_max": max(metric["blank_fraction"] for metric in metrics),
                "blank_fraction_mean": sum(
                    metric["blank_fraction"] for metric in metrics
                ) / len(metrics),
                "max_blank_fraction": (
                    reference_profile.REFERENCE_MATCHED_SHORTS_V1.max_blank_fraction
                ),
                "contact_sheet": str(contact_sheet),
                "metrics": metrics,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    assert len(metrics) == 24
    assert contact_sheet.is_file()
    assert report.is_file()


def _ranking_telemetry(
    crop_box,
    evidence,
    border_mask,
    *,
    base_zoom,
    protected_retained_fraction=0.99,
    edge_blank=0.01,
):
    from app.services import framing_analysis

    return framing_analysis.FramingTelemetry(
        contract_version=evidence.contract_version,
        detector_version=border_mask.detector_version,
        mask_sha256=border_mask.mask_sha256,
        crop_box=crop_box,
        base_zoom=base_zoom,
        source_resolution_zoom_cap=3.0,
        protected_region_zoom_cap=3.0,
        edge_connected_blank_fraction=edge_blank,
        non_discardable_low_information_fraction=0.0,
        protected_retained_fraction=protected_retained_fraction,
        balloon_mask_intersection_ratio=0.0,
        subject_coverage=1.0,
        face_coverage=1.0,
        action_coverage=1.0,
        effect_coverage=1.0,
        continuity_context_coverage=1.0,
        mask_confidence=evidence.mask_confidence,
        mask_source=evidence.evidence_source,
    )


def test_reference_ranking_prefers_protected_retention_before_blank_and_zoom(
    tmp_path, monkeypatch
):
    from app.services import framing_analysis, reference_profile, render

    source = tmp_path / 'ranking-source.png'
    Image.new('RGB', (900, 2400), (64, 64, 64)).save(source)
    evidence = _visual_evidence()
    with Image.open(source) as image:
        border_mask = framing_analysis.build_color_agnostic_border_mask(
            image, evidence, grid_long_edge=64
        )

    monkeypatch.setattr(render, '_reference_scales', lambda _max_zoom: (1.0, 1.2))

    def fake_candidate(crop_box, candidate_evidence, candidate_mask, source_size, target_size):
        zoom = 1.0 if crop_box[2] - crop_box[0] > 800 else 1.2
        return True, _ranking_telemetry(
            crop_box,
            candidate_evidence,
            candidate_mask,
            base_zoom=zoom,
            protected_retained_fraction=0.99 if zoom == 1.0 else 0.90,
            edge_blank=0.20 if zoom == 1.0 else 0.05,
        )

    monkeypatch.setattr(framing_analysis, 'candidate_is_feasible', fake_candidate)
    prepared = render.prepare_reference_frame(
        source,
        tmp_path / 'ranking-prepared.jpg',
        TARGET_WIDTH,
        TARGET_HEIGHT,
        0.5,
        0.5,
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        evidence=evidence,
        border_mask=border_mask,
    )

    assert prepared.crop_box == (0, 400, 900, 2000)
    assert prepared.telemetry is not None
    assert prepared.telemetry.protected_retained_fraction == pytest.approx(0.99)


def test_reference_ranking_prefers_larger_tie_break_box_and_is_deterministic(
    tmp_path, monkeypatch
):
    from app.services import framing_analysis, reference_profile, render

    source = tmp_path / 'tie-source.png'
    Image.new('RGB', (900, 2400), (64, 64, 64)).save(source)
    evidence = _visual_evidence()
    with Image.open(source) as image:
        border_mask = framing_analysis.build_color_agnostic_border_mask(
            image, evidence, grid_long_edge=64
        )

    monkeypatch.setattr(render, '_reference_scales', lambda _max_zoom: (1.0, 1.2))

    def fake_candidate(crop_box, candidate_evidence, candidate_mask, source_size, target_size):
        return True, _ranking_telemetry(
            crop_box,
            candidate_evidence,
            candidate_mask,
            base_zoom=1.0,
            protected_retained_fraction=0.99,
            edge_blank=0.01,
        )

    monkeypatch.setattr(framing_analysis, 'candidate_is_feasible', fake_candidate)
    first = render.prepare_reference_frame(
        source,
        tmp_path / 'tie-first.jpg',
        TARGET_WIDTH,
        TARGET_HEIGHT,
        0.5,
        0.5,
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        evidence=evidence,
        border_mask=border_mask,
    )
    second = render.prepare_reference_frame(
        source,
        tmp_path / 'tie-second.jpg',
        TARGET_WIDTH,
        TARGET_HEIGHT,
        0.5,
        0.5,
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        evidence=evidence,
        border_mask=border_mask,
    )

    assert first.crop_box == (75, 534, 825, 1866)
    assert second.crop_box == first.crop_box
    assert Path(first.path).read_bytes() == Path(second.path).read_bytes()


def test_candidate_tightens_protected_zoom_cap_for_edge_region():
    from app.services import framing_analysis

    image = Image.new('RGB', (1000, 2000), (64, 64, 64))
    plain_evidence = _visual_evidence()
    edge_evidence = _visual_evidence(
        protected_regions=(
            _protected('subject', (0.01, 0.45, 0.11, 0.55), 0.98),
        )
    )
    plain_mask = framing_analysis.build_color_agnostic_border_mask(
        image, plain_evidence, grid_long_edge=64
    )
    edge_mask = framing_analysis.build_color_agnostic_border_mask(
        image, edge_evidence, grid_long_edge=64
    )

    plain_ok, plain_telemetry = framing_analysis.candidate_is_feasible(
        (0, 0, 1000, 2000),
        plain_evidence,
        plain_mask,
        (1000, 2000),
        (100, 200),
    )
    edge_ok, edge_telemetry = framing_analysis.candidate_is_feasible(
        (0, 0, 1000, 2000),
        edge_evidence,
        edge_mask,
        (1000, 2000),
        (100, 200),
    )

    assert plain_ok is True
    assert edge_ok is True
    assert edge_telemetry.protected_region_zoom_cap < edge_telemetry.source_resolution_zoom_cap
    assert edge_telemetry.protected_region_zoom_cap <= 1.03
    assert edge_telemetry.protected_region_zoom_cap <= plain_telemetry.protected_region_zoom_cap


def test_reference_rejects_incompatible_detector_contract(tmp_path):
    from app.services import framing_analysis, reference_profile, render

    source = tmp_path / 'contract-source.png'
    Image.new('RGB', (900, 2400), (64, 64, 64)).save(source)
    evidence = _visual_evidence()
    with Image.open(source) as image:
        border_mask = framing_analysis.build_color_agnostic_border_mask(
            image, evidence, grid_long_edge=64
        )
    incompatible = replace(border_mask, detector_version='OTHER_FRAMING_CONTRACT:grid')
    destination = tmp_path / 'contract-prepared.jpg'

    with pytest.raises(
        render.RenderError, match='visual.framing_contract_incompatible'
    ):
        render.prepare_reference_frame(
            source,
            destination,
            TARGET_WIDTH,
            TARGET_HEIGHT,
            0.5,
            0.5,
            profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
            evidence=evidence,
            border_mask=incompatible,
        )
    assert not destination.exists()


def test_reference_corrupt_source_fails_closed_but_legacy_fallback_remains(tmp_path):
    from app.services import reference_profile, render

    corrupt = tmp_path / 'corrupt-reference.png'
    corrupt.write_bytes(b'not-an-image')
    evidence = _visual_evidence()
    reference_destination = tmp_path / 'corrupt-prepared.jpg'

    with pytest.raises(
        render.RenderError, match='visual.panel_lineage_unavailable'
    ):
        render.prepare_reference_frame(
            corrupt,
            reference_destination,
            TARGET_WIDTH,
            TARGET_HEIGHT,
            0.5,
            0.5,
            profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
            evidence=evidence,
        )
    assert not reference_destination.exists()

    valid = tmp_path / 'legacy-source.png'
    _write_focus_fixture(valid)
    legacy_destination = tmp_path / 'legacy-prepared.jpg'
    legacy_result = render.prepare_reference_frame(
        valid,
        legacy_destination,
        TARGET_WIDTH,
        TARGET_HEIGHT,
        0.5,
        0.5,
        profile=None,
    )
    assert _result_path(legacy_result) == legacy_destination
