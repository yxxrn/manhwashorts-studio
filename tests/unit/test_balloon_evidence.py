"""Task 1 contract tests for typed, fail-closed visual evidence."""

from importlib import import_module

import pytest

visual_scoring = import_module("app.services.visual_scoring")


def _boundary(name):
    return getattr(visual_scoring, name, None)


def _unknown():
    factory = _boundary("unknown_visual_evidence")
    assert factory is not None
    return factory(
        panel_id="panel-1",
        source_asset_id="asset-1",
        source_order=7,
        reason="visual geometry was not acquired by the current analyzer contract",
    )


def test_typed_visual_evidence_boundary_exists():
    for name in (
        "BalloonRegionEvidence",
        "ProtectedRegionEvidence",
        "PanelVisualEvidence",
        "VisualEvidenceError",
        "parse_panel_visual_evidence",
        "validate_panel_visual_evidence",
        "require_reference_ready_visual_evidence",
        "is_conservative_full_panel_visual_evidence",
        "panel_visual_evidence_json",
        "visual_evidence_hash",
        "ensure_panel_visual_evidence",
    ):
        assert _boundary(name) is not None, name


def test_unknown_visual_evidence_round_trips_but_blocks_reference_consumption():
    evidence = _unknown()
    validate = _boundary("validate_panel_visual_evidence")
    serialize = _boundary("panel_visual_evidence_json")
    parse = _boundary("parse_panel_visual_evidence")
    require_ready = _boundary("require_reference_ready_visual_evidence")
    assert all((validate, serialize, parse, require_ready))

    validate(evidence)
    serialized = serialize(evidence)
    assert serialized["panel_id"] == "panel-1"
    assert serialized["source_asset_id"] == "asset-1"
    assert serialized["source_order"] == 7
    assert serialized["balloon_mask_status"] == "unknown"
    assert serialized["mask_reason"]
    parsed = parse(serialized)
    assert parsed == evidence
    assert serialize(parsed) == serialized
    with pytest.raises(Exception) as caught:
        require_ready(parsed)
    assert "visual.balloon_mask_unknown" in str(caught.value)


def test_conservative_full_panel_fallback_requires_explicit_opt_in():
    fallback = visual_scoring.conservative_full_panel_visual_evidence(
        panel_id="panel-fallback",
        source_asset_id="asset-fallback",
        source_order=4,
        reason="geometry remained unknown after targeted retry",
    )

    assert visual_scoring.is_conservative_full_panel_visual_evidence(fallback)
    with pytest.raises(Exception, match="visual\\.balloon_mask_unknown"):
        visual_scoring.require_reference_ready_visual_evidence(fallback)
    assert (
        visual_scoring.require_reference_ready_visual_evidence(
            fallback,
            allow_conservative_full_panel=True,
        )
        == fallback
    )


def test_visual_evidence_parser_ignores_untrusted_optional_fields():
    evidence = visual_scoring.panel_visual_evidence_json(
        visual_scoring.unknown_visual_evidence(
            panel_id="panel-optional",
            source_asset_id="asset-optional",
            source_order=2,
            reason="geometry unavailable",
        )
    )
    evidence["provider_note"] = "ignored"
    parsed = visual_scoring.parse_panel_visual_evidence(evidence)

    assert parsed.panel_id == "panel-optional"
    assert not hasattr(parsed, "provider_note")


def test_missing_visual_sidecar_is_persisted_as_explicit_unknown_with_lineage():
    ensure = _boundary("ensure_panel_visual_evidence")
    assert ensure is not None
    observation = {
        "panel_id": "panel-2",
        "source_asset_id": "asset-2",
        "source_index": 3,
        "visible_facts": ["a figure stands near a gate"],
        "dialogue_or_ocr": [],
        "inferences": [],
        "uncertainties": [],
        "evidence_refs": ["panel-2"],
    }
    enriched, evidence = ensure(
        observation,
        panel_id="panel-2",
        source_asset_id="asset-2",
        source_order=3,
    )
    assert enriched is not observation
    assert enriched["panel_id"] == "panel-2"
    assert enriched["visual_evidence"]["panel_id"] == "panel-2"
    assert enriched["visual_evidence"]["source_asset_id"] == "asset-2"
    assert enriched["visual_evidence"]["source_order"] == 3
    assert enriched["visual_evidence"]["balloon_mask_status"] == "unknown"
    assert evidence.balloon_mask_status == "unknown"
    assert evidence.mask_reason


def test_known_empty_requires_affirmative_provenance_and_confidence():
    evidence_type = _boundary("PanelVisualEvidence")
    validate = _boundary("validate_panel_visual_evidence")
    assert evidence_type is not None and validate is not None
    evidence = evidence_type(
        contract_version="COLOR_AGNOSTIC_BALLOON_FREE_V1",
        panel_id="panel-empty",
        source_asset_id="asset-empty",
        source_order=1,
        balloon_regions=(),
        protected_regions=(),
        balloon_mask_status="known_empty",
        mask_confidence=0.0,
        evidence_source="",
        mask_reason="",
        evidence_hash="",
    )
    with pytest.raises(Exception) as caught:
        validate(evidence)
    assert "visual.balloon_mask_empty_unproven" in str(caught.value)


def test_known_nonempty_requires_valid_normalized_geometry_and_lineage():
    evidence_type = _boundary("PanelVisualEvidence")
    balloon_type = _boundary("BalloonRegionEvidence")
    validate = _boundary("validate_panel_visual_evidence")
    assert evidence_type is not None and balloon_type is not None and validate is not None
    region = balloon_type(
        region_id="balloon-1",
        kind="speech_balloon",
        normalized_bbox=None,
        normalized_polygon=(),
        confidence=0.9,
        evidence_source="vision",
        mask_status="known_nonempty",
    )
    evidence = evidence_type(
        contract_version="COLOR_AGNOSTIC_BALLOON_FREE_V1",
        panel_id="panel-known",
        source_asset_id="asset-known",
        source_order=2,
        balloon_regions=(region,),
        protected_regions=(),
        balloon_mask_status="known_nonempty",
        mask_confidence=0.9,
        evidence_source="vision",
        mask_reason="provider supplied a speech-region geometry",
        evidence_hash="",
    )
    with pytest.raises(Exception) as caught:
        validate(evidence)
    assert "visual.balloon_geometry_invalid" in str(caught.value)


def test_duplicate_region_ids_and_out_of_bounds_geometry_fail_closed():
    evidence_type = _boundary("PanelVisualEvidence")
    balloon_type = _boundary("BalloonRegionEvidence")
    protected_type = _boundary("ProtectedRegionEvidence")
    validate = _boundary("validate_panel_visual_evidence")
    assert all((evidence_type, balloon_type, protected_type, validate))
    balloon = balloon_type(
        region_id="same",
        kind="speech_balloon",
        normalized_bbox=(0.1, 0.1, 0.4, 0.4),
        normalized_polygon=(),
        confidence=0.9,
        evidence_source="vision",
        mask_status="known_nonempty",
    )
    protected = protected_type(
        region_id="same",
        kind="subject",
        normalized_bbox=(0.0, 0.0, 1.1, 0.5),
        normalized_polygon=(),
        confidence=0.9,
        evidence_source="vision",
        required=True,
        minimum_coverage=0.5,
    )
    evidence = evidence_type(
        contract_version="COLOR_AGNOSTIC_BALLOON_FREE_V1",
        panel_id="panel-bad",
        source_asset_id="asset-bad",
        source_order=3,
        balloon_regions=(balloon,),
        protected_regions=(protected,),
        balloon_mask_status="known_nonempty",
        mask_confidence=0.9,
        evidence_source="vision",
        mask_reason="provider supplied geometry",
        evidence_hash="",
    )
    with pytest.raises(Exception) as caught:
        validate(evidence)
    assert "visual.region_invalid" in str(caught.value)


def test_present_sidecar_is_validated_without_mutating_observation_and_hash_is_stable():
    ensure = _boundary("ensure_panel_visual_evidence")
    assert ensure is not None
    raw = {
        "panel_id": "panel-present",
        "source_asset_id": "asset-present",
        "source_order": 4,
        "balloon_mask_status": "known_empty",
        "mask_confidence": 0.96,
        "evidence_source": "vision_geometry_v1",
        "mask_reason": "the provider explicitly reported no speech region",
        "balloon_regions": [],
        "protected_regions": [],
        "contract_version": "COLOR_AGNOSTIC_BALLOON_FREE_V1",
        "evidence_hash": "",
    }
    observation = {"panel_id": "panel-present", "visual_evidence": raw}
    enriched, evidence = ensure(
        observation,
        panel_id="panel-present",
        source_asset_id="asset-present",
        source_order=4,
    )
    assert observation["visual_evidence"] is raw
    assert enriched["visual_evidence"] is not raw
    assert enriched["visual_evidence"]["evidence_hash"]
    assert evidence.evidence_hash == enriched["visual_evidence"]["evidence_hash"]
    assert _boundary("visual_evidence_hash")(evidence) == evidence.evidence_hash
