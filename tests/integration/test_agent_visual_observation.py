"""RED/GREEN contract tests for the review-only agent-vision observation boundary.

The pathway lets a vision-capable executing agent persist balloon/protected
geometry through the normal evidence contract. It never accepts an external
hash or contract, never permits unknown status, stays review-only, and every
frame gate continues to consume the geometry unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, PanelRegion, Project, SourceAsset, StoryAnalysis, User, Workspace
from app.services import agent_visual_observation as avo
from app.services import framing_analysis, pipeline, reference_profile, visual_scoring


def _raw_observation(**overrides):
    raw = {
        "balloon_mask_status": "known_empty",
        "mask_confidence": 0.9,
        "mask_reason": "no speech balloons are visible in the panel",
        "balloon_regions": [],
        "protected_regions": [],
    }
    raw.update(overrides)
    return raw


def _engine_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return Session()


def _seed_db(db, *, checksum="c" * 64):
    db.add(User(id="user-1", email="agent-test@local.invalid"))
    db.add(Workspace(id="ws-1", owner_id="user-1", name="agent-test"))
    db.add(Project(id="proj-1", workspace_id="ws-1", title="agent-vision-test"))
    db.add(
        SourceAsset(
            id="asset-1",
            project_id="proj-1",
            type="image",
            checksum=checksum,
            width=100,
            height=200,
        )
    )
    db.add(StoryAnalysis(id="analysis-1", project_id="proj-1"))
    db.add(
        PanelRegion(
            id="region-1",
            story_analysis_id="analysis-1",
            source_asset_id="asset-1",
            source_asset_checksum=checksum,
            panel_id="panel-1",
            source_order=1,
            original_width=100,
            original_height=200,
            bounds_json={"x": 0, "y": 0, "width": 100, "height": 200},
            observation_json={
                "region_bounds": {"x": 0, "y": 0, "width": 100, "height": 200},
                "visible_facts": ["pre-existing observation field"],
                "visual_evidence": visual_scoring.panel_visual_evidence_json(
                    visual_scoring.unknown_visual_evidence(
                        panel_id="panel-1",
                        source_asset_id="asset-1",
                        source_order=1,
                        reason="geometry was not observed",
                    )
                ),
            },
        )
    )
    db.commit()


def _apply_defaults(db, raw=None, **kwargs):
    settings = {
        "agent_label": "claude-test-agent",
        "silent_reference_review": True,
        "publish_allowed": False,
    }
    settings.update(kwargs)
    return avo.apply_agent_panel_observations(
        db,
        "proj-1",
        [{"panel_id": "panel-1", **(_raw_observation() if raw is None else raw)}],
        **settings,
    )


# ---------------------------------------------------------------------------
# validate_agent_panel_observation
# ---------------------------------------------------------------------------


def test_agent_observation_rejects_supplied_hash_contract_and_lineage():
    base = {"panel_id": "panel-1", "source_asset_id": "asset-1", "source_order": 1}
    for key in ("evidence_hash", "contract_version", "evidence_source"):
        raw = _raw_observation(**{key: "deadbeef"})
        with pytest.raises(avo.AgentObservationError) as exc:
            avo.validate_agent_panel_observation(raw, **base, agent_label="agent-x")
        assert exc.value.code == "agent_observation.supplied_contract_forbidden"
    raw = _raw_observation(panel_id="other")
    with pytest.raises(avo.AgentObservationError) as exc:
        avo.validate_agent_panel_observation(raw, **base, agent_label="agent-x")
    assert exc.value.code == "agent_observation.supplied_lineage_forbidden"


def test_agent_observation_rejects_unknown_status():
    raw = _raw_observation(balloon_mask_status="unknown")
    with pytest.raises(avo.AgentObservationError) as exc:
        avo.validate_agent_panel_observation(
            raw, panel_id="p", source_asset_id="a", source_order=1, agent_label="x"
        )
    assert exc.value.code == "agent_observation.unknown_status_forbidden"


def test_agent_observation_requires_nonempty_label_and_reason():
    base = {"panel_id": "p", "source_asset_id": "a", "source_order": 1}
    with pytest.raises(avo.AgentObservationError) as exc:
        avo.validate_agent_panel_observation(_raw_observation(), **base, agent_label="  ")
    assert exc.value.code == "agent_observation.agent_label_required"
    with pytest.raises(avo.AgentObservationError) as exc:
        avo.validate_agent_panel_observation(
            _raw_observation(mask_reason="  "), **base, agent_label="x"
        )
    assert exc.value.code == "agent_observation.reason_required"


def test_agent_observation_validates_normalized_geometry():
    base = {"panel_id": "p", "source_asset_id": "a", "source_order": 1}
    raw = _raw_observation(
        balloon_mask_status="known_nonempty",
        balloon_regions=[
            {
                "region_id": "b1",
                "kind": "speech_balloon",
                "normalized_bbox": [0.1, 0.1, 1.4, 0.5],
                "normalized_polygon": None,
                "confidence": 0.9,
                "mask_status": "known_nonempty",
            }
        ],
    )
    with pytest.raises(avo.AgentObservationError) as exc:
        avo.validate_agent_panel_observation(raw, **base, agent_label="x")
    assert exc.value.code == "agent_observation.geometry_invalid"
    raw = _raw_observation(
        protected_regions=[
            {"region_id": "s1", "kind": "narration", "normalized_bbox": None, "confidence": 0.9}
        ]
    )
    with pytest.raises(avo.AgentObservationError) as exc:
        avo.validate_agent_panel_observation(raw, **base, agent_label="x")
    assert exc.value.code == "agent_observation.geometry_invalid"


def test_agent_observation_known_nonempty_requires_balloon_geometry():
    base = {"panel_id": "p", "source_asset_id": "a", "source_order": 1}
    raw = _raw_observation(balloon_mask_status="known_nonempty", balloon_regions=[])
    with pytest.raises(avo.AgentObservationError) as exc:
        avo.validate_agent_panel_observation(raw, **base, agent_label="x")
    assert exc.value.code == "agent_observation.geometry_invalid"


def test_agent_observation_produces_locally_hashed_sidecar():
    raw = _raw_observation(
        balloon_mask_status="known_nonempty",
        balloon_regions=[
            {
                "region_id": "b1",
                "kind": "speech_balloon",
                "normalized_bbox": [0.1, 0.1, 0.5, 0.4],
                "normalized_polygon": None,
                "confidence": 0.92,
                "mask_status": "known_nonempty",
            }
        ],
        protected_regions=[
            {
                "region_id": "s1",
                "kind": "subject",
                "normalized_bbox": [0.2, 0.4, 0.9, 0.95],
                "normalized_polygon": None,
                "confidence": 0.9,
                "required": True,
                "minimum_coverage": 0.8,
            }
        ],
    )
    payload = avo.validate_agent_panel_observation(
        raw, panel_id="panel-9", source_asset_id="asset-9", source_order=9,
        agent_label="claude-test-agent",
    )
    assert payload["contract_version"] == visual_scoring.VISUAL_EVIDENCE_CONTRACT_VERSION
    assert payload["evidence_source"] == avo.AGENT_EVIDENCE_SOURCE
    assert payload["evidence_hash"]
    assert payload["mask_reason"].startswith("agent:claude-test-agent; ")
    parsed = visual_scoring.parse_panel_visual_evidence(payload)
    assert visual_scoring.visual_evidence_hash(parsed) == payload["evidence_hash"]
    assert visual_scoring.require_reference_ready_visual_evidence(parsed).balloon_mask_status == "known_nonempty"


# ---------------------------------------------------------------------------
# apply_agent_panel_observations
# ---------------------------------------------------------------------------


def test_apply_requires_review_only_acknowledgment():
    db = _engine_session()
    _seed_db(db)
    with pytest.raises(avo.AgentObservationError) as exc:
        _apply_defaults(db, publish_allowed=True)
    assert exc.value.code == "agent_observation.publish_forbidden"
    with pytest.raises(avo.AgentObservationError) as exc:
        _apply_defaults(db, silent_reference_review=False)
    assert exc.value.code == "agent_observation.silent_review_required"
    db.rollback()
    db.close()


def test_apply_rejects_lineage_mismatch():
    db = _engine_session()
    _seed_db(db)
    with pytest.raises(avo.AgentObservationError) as exc:
        avo.apply_agent_panel_observations(
            db,
            "proj-1",
            [{"panel_id": "panel-missing", **_raw_observation()}],
            agent_label="x",
            silent_reference_review=True,
            publish_allowed=False,
        )
    assert exc.value.code == "visual.panel_lineage_unavailable"
    db.rollback()
    # stale checksum on the region itself
    region = db.query(PanelRegion).filter_by(panel_id="panel-1").one()
    region.source_asset_checksum = "d" * 64
    db.commit()
    with pytest.raises(avo.AgentObservationError) as exc:
        _apply_defaults(db)
    assert exc.value.code == "visual.panel_lineage_unavailable"
    db.rollback()
    db.close()


def test_apply_persists_sidecar_and_preserves_other_observation_fields():
    db = _engine_session()
    _seed_db(db)
    report = _apply_defaults(db)
    assert report["applied"] == ["panel-1"]
    region = db.query(PanelRegion).filter_by(panel_id="panel-1").one()
    observation = region.observation_json
    assert observation["visible_facts"] == ["pre-existing observation field"]
    assert observation["region_bounds"]["width"] == 100
    payload = observation["visual_evidence"]
    assert payload["balloon_mask_status"] == "known_empty"
    assert payload["evidence_source"] == avo.AGENT_EVIDENCE_SOURCE
    parsed = visual_scoring.parse_panel_visual_evidence(payload)
    assert parsed.panel_id == "panel-1"
    assert parsed.source_order == 1
    assert visual_scoring.visual_evidence_hash(parsed) == payload["evidence_hash"]
    assert report["entries"][0]["evidence_hash"] == payload["evidence_hash"]
    assert report["publish_allowed"] is False
    db.rollback()
    db.close()


def test_apply_rejects_duplicate_panel_and_ledger_records_provenance(tmp_path):
    db = _engine_session()
    _seed_db(db)
    with pytest.raises(avo.AgentObservationError) as exc:
        avo.apply_agent_panel_observations(
            db,
            "proj-1",
            [
                {"panel_id": "panel-1", **_raw_observation()},
                {"panel_id": "panel-1", **_raw_observation()},
            ],
            agent_label="x",
            silent_reference_review=True,
            publish_allowed=False,
        )
    assert exc.value.code == "agent_observation.duplicate_panel"
    report = _apply_defaults(db, output_dir=tmp_path)
    ledger_path = report["ledger_path"]
    assert ledger_path and ledger_path.exists()
    import json

    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert ledger["contract_version"] == avo.AGENT_OBSERVATION_CONTRACT_VERSION
    assert ledger["agent_label"] == "claude-test-agent"
    assert ledger["publish_allowed"] is False
    assert ledger["entries"][0]["panel_id"] == "panel-1"
    db.rollback()
    db.close()


# ---------------------------------------------------------------------------
# downstream consumption: gates unchanged
# ---------------------------------------------------------------------------


def _region_from_payload(payload):
    return SimpleNamespace(
        id="region-1",
        panel_id="panel-1",
        source_asset_id="asset-1",
        source_order=3,
        source_asset_checksum="asset-1-checksum",
        bounds_json={"x": 0, "y": 0, "width": 100, "height": 200},
        observation_json={
            "region_bounds": {"x": 0, "y": 0, "width": 100, "height": 200},
            "visual_evidence": payload,
        },
    )


def _crop(color=(40, 60, 100)):
    return Image.new("RGB", (100, 200), color)


def _candidate_features():
    return visual_scoring.PanelCandidate(
        asset_id="asset-1",
        order_index=3,
        features=visual_scoring.VisualFeatures(
            face_visibility=0.9,
            action_pose=0.8,
            dramatic_composition=0.9,
            focal_points=((0.5, 0.5),),
            visual_signature="sig-1",
        ),
        visual_score=1.0,
        semantic_score=1.0,
    )


def test_applied_agent_sidecar_builds_feasible_reference_candidate():
    raw = _raw_observation()
    payload = avo.validate_agent_panel_observation(
        raw, panel_id="panel-1", source_asset_id="asset-1", source_order=3,
        agent_label="claude-test-agent",
    )
    result = pipeline._build_reference_panel_fallback_candidates(
        panel_regions=(_region_from_payload(payload),),
        panel_candidates_by_region_id={"region-1": _candidate_features()},
        panel_crops_by_region_id={"region-1": _crop()},
        section_evidence_panel_ids={"hook": ("panel-1",)},
        section_citations={},
        beats_by_section={},
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
    )
    assert len(result) == 1
    evidence = result[0].visual_evidence
    assert evidence.evidence_source == avo.AGENT_EVIDENCE_SOURCE
    assert "claude-test-agent" in evidence.mask_reason
    assert result[0].border_mask.mask_sha256


def test_agent_balloon_geometry_still_hits_balloon_gate():
    raw = _raw_observation(
        balloon_mask_status="known_nonempty",
        balloon_regions=[
            {
                "region_id": "b1",
                "kind": "speech_balloon",
                "normalized_bbox": [0.2, 0.2, 0.8, 0.8],
                "normalized_polygon": None,
                "confidence": 0.95,
                "mask_status": "known_nonempty",
            }
        ],
    )
    payload = avo.validate_agent_panel_observation(
        raw, panel_id="panel-1", source_asset_id="asset-1", source_order=3,
        agent_label="claude-test-agent",
    )
    evidence = visual_scoring.parse_panel_visual_evidence(payload)
    mask = framing_analysis.build_color_agnostic_border_mask(_crop(), evidence)
    accepted, telemetry = framing_analysis.candidate_is_feasible(
        (0, 0, 100, 200),
        evidence,
        mask,
        (100, 200),
        (1080, 1920),
        allow_source_resolution_warning=True,
    )
    assert accepted is False
    assert telemetry.rejection_code == "visual.balloon_mask_overlap"


def test_blank_gate_still_measured_from_pixels_not_agent_claims():
    image = Image.new("RGB", (100, 200), (40, 60, 100))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 100, 60), fill=(250, 250, 250))  # broad top blank band
    evidence = visual_scoring.parse_panel_visual_evidence(
        avo.validate_agent_panel_observation(
            _raw_observation(),
            panel_id="panel-1",
            source_asset_id="asset-1",
            source_order=3,
            agent_label="claude-test-agent",
        )
    )
    mask = framing_analysis.build_color_agnostic_border_mask(image, evidence)
    blank = framing_analysis._mask_crop_fraction(mask, (0, 0, 100, 200))
    assert blank > 0.03  # the pixel mask, not the agent claim, sets the blank gate
