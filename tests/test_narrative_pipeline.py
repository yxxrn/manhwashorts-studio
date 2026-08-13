"""Slice E Task 5 RED tests for narrative identity pipeline integration."""

from __future__ import annotations

import copy

import pytest


def _seed_sharp_friend(db):
    """Reuse the complete rights-safe evidence fixture, then opt it into v3."""

    from tests.test_script_evidence_gate import _project, _seed_analysis

    from app.models import PanelRegion
    from app.services import analyzer_contract, visual_scoring
    from app.services.narrative_identity import get_narrative_identity

    project = _project(db)
    row = _seed_analysis(db, project)
    profile = get_narrative_identity("sharp_friend_v1")
    instruction_version, instruction_sha256, _ = (
        analyzer_contract.load_analyzer_instruction(
            narrative_profile_id=profile.profile_id
        )
    )

    passages = copy.deepcopy(row.evidence_graph_json["script_passages"])
    passages = [passages[0], passages[1], passages[2], passages[4]]
    roles = ("opening_signal", "pressure_turn", "consequence", "sharp_close")
    for passage, role in zip(passages, roles, strict=True):
        passage["editorial_role"] = role
    passages[-1]["text"] = (
        "Mara waits outside, and the next panel will show who claimed the dark boat."
    )
    row.evidence_graph_json = {
        **row.evidence_graph_json,
        "script_passages": passages,
    }
    row.instruction_version = instruction_version
    row.instruction_sha256 = instruction_sha256
    row.reconciliation_json = {
        **row.reconciliation_json,
        "narrative_identity": {
            "profile_id": profile.profile_id,
            "version": profile.profile_version,
            "sha256": profile.contract_sha256,
        },
        "narrative_ending_kind": "consequence",
        "narrative_screening_warning_codes": [],
    }
    for panel in db.query(PanelRegion).filter(PanelRegion.story_analysis_id == row.id):
        observation, _ = visual_scoring.ensure_panel_visual_evidence(
            panel.observation_json,
            panel_id=panel.panel_id,
            source_asset_id=panel.source_asset_id,
            source_order=panel.source_order,
        )
        panel.observation_json = observation
    db.flush()
    return project, row


def _seed_v2(db):
    from tests.test_script_evidence_gate import _project, _seed_analysis

    project = _project(db)
    row = _seed_analysis(db, project)
    return project, row


def _script_count(db, project_id: str) -> int:
    from sqlalchemy import func, select

    from app.models import ScriptVersion

    return int(
        db.scalar(
            select(func.count())
            .select_from(ScriptVersion)
            .where(ScriptVersion.project_id == project_id)
        )
        or 0
    )


def test_sharp_friend_materializes_flexible_script_and_persists_identity(db):
    from app.services import pipeline as pipeline_service

    project, analysis = _seed_sharp_friend(db)

    script = pipeline_service.generate_script(
        db,
        project.id,
        actor_id="editor-1",
        narrative_profile_id="sharp_friend_v1",
    )

    assert analysis.state == "SCRIPT_DRAFT"
    assert script.generator == "vision_evidence_v3"
    assert len(script.sections) == 4
    assert [section["editorial_role"] for section in script.sections] == [
        "opening_signal",
        "pressure_turn",
        "consequence",
        "sharp_close",
    ]
    assert [section["text"] for section in script.sections] == [
        passage["text"] for passage in analysis.evidence_graph_json["script_passages"]
    ]
    identity = script.editorial_metadata["narrative_identity"]
    assert identity == {
        "profile_id": "sharp_friend_v1",
        "version": "1.0.0",
        "sha256": "134b544c9e2f74ca0b8c64ff55a27c831e76f77a08f26fc2a463112cb0678b3e",
        "human_review_required": True,
        "editorial_review_confirmed": False,
    }
    assert analysis.reconciliation_json["narrative_identity"]["profile_id"] == (
        "sharp_friend_v1"
    )
    assert analysis.reconciliation_json["narrative_ending_kind"] == "consequence"


@pytest.mark.parametrize(
    "mutation",
    ("profile_id", "version", "sha256", "missing"),
)
def test_sharp_friend_rejects_stale_or_corrupt_persisted_identity(
    db, mutation: str
):
    from app.services import pipeline as pipeline_service

    project, analysis = _seed_sharp_friend(db)
    identity = dict(analysis.reconciliation_json["narrative_identity"])
    if mutation == "profile_id":
        identity["profile_id"] = "unknown_profile"
    elif mutation == "version":
        identity["version"] = "0.0.0"
    elif mutation == "sha256":
        identity["sha256"] = "0" * 64
    else:
        identity = None
    reconciliation = dict(analysis.reconciliation_json)
    reconciliation["narrative_identity"] = identity
    analysis.reconciliation_json = reconciliation
    db.flush()

    with pytest.raises(pipeline_service.PipelineError, match="narrative_identity_invalid"):
        pipeline_service.generate_script(
            db,
            project.id,
            actor_id="editor-1",
            narrative_profile_id="sharp_friend_v1",
        )
    assert _script_count(db, project.id) == 0


def test_sharp_friend_override_must_match_persisted_identity(db):
    from app.services import pipeline as pipeline_service

    project, _ = _seed_sharp_friend(db)

    with pytest.raises(pipeline_service.PipelineError, match="narrative_profile"):
        pipeline_service.generate_script(
            db,
            project.id,
            actor_id="editor-1",
            narrative_profile_id="other_profile",
        )
    assert _script_count(db, project.id) == 0


def test_sharp_friend_approval_remains_explicit_and_accepts_four_sections(
    db,
):
    from app.services import pipeline as pipeline_service

    project, _ = _seed_sharp_friend(db)
    script = pipeline_service.generate_script(
        db,
        project.id,
        actor_id="editor-1",
        narrative_profile_id="sharp_friend_v1",
    )

    with pytest.raises(pipeline_service.PipelineError):
        pipeline_service.approve_script(
            db,
            script.id,
            actor_id="editor-1",
            editorial_review_confirmed=False,
        )
    assert script.approved_at is None

    approved = pipeline_service.approve_script(
        db,
        script.id,
        actor_id="editor-1",
        editorial_review_confirmed=True,
    )
    assert approved.approved_by == "editor-1"
    assert approved.editorial_metadata["editorial_review_confirmed"] is True
    assert approved.editorial_metadata["editorial_review_actor"] == "editor-1"


def test_sharp_friend_draft_never_calls_legacy_or_media_stages(db, monkeypatch):
    from app.services import pipeline as pipeline_service

    project, _ = _seed_sharp_friend(db)
    for name in ("run_legacy_text_analysis", "generate_voiceover", "build_timeline"):
        monkeypatch.setattr(
            pipeline_service,
            name,
            lambda *args, _name=name, **kwargs: pytest.fail(
                f"sharp_friend draft called {_name}"
            ),
        )
    monkeypatch.setattr(
        pipeline_service.script_svc,
        "get_generator",
        lambda *args, **kwargs: pytest.fail("sharp_friend draft called template generator"),
        raising=False,
    )

    script = pipeline_service.generate_script(
        db,
        project.id,
        actor_id="editor-1",
        narrative_profile_id="sharp_friend_v1",
    )
    assert script.approved_at is None


def test_v2_without_identity_stays_legacy_and_uses_five_roles(db):
    from app.services import pipeline as pipeline_service

    project, analysis = _seed_v2(db)
    script = pipeline_service.generate_script(db, project.id, actor_id="editor-1")

    assert analysis.state == "SCRIPT_DRAFT"
    assert script.generator == "vision_evidence_v2"
    assert len(script.sections) == 5
    assert "narrative_identity" not in script.editorial_metadata


def test_analysis_status_exposes_only_safe_narrative_identity_summary(db):
    from app.services import pipeline as pipeline_service

    project, _ = _seed_sharp_friend(db)
    status = pipeline_service.analysis_status(db, project.id)

    assert status["narrative_profile_id"] == "sharp_friend_v1"
    assert status["narrative_profile_version"] == "1.0.0"
    assert len(status["narrative_profile_sha256"]) == 64
    assert status["narrative_screening_warning_codes"] == []
    forbidden = {
        "api_key",
        "storage_path",
        "payload",
        "raw_provider_output",
        "observations",
        "claims",
        "script_passages",
        "prompt_text",
    }
    assert forbidden.isdisjoint(status)


def test_api_accepts_profile_on_script_request_and_returns_safe_metadata(auth_client):
    from tests.test_vision_status_api import _make_project, _seed_analysis

    from app.db import SessionLocal
    from app.services import pipeline as pipeline_service

    project_id = _make_project(auth_client)
    # The API fixture is v2; this request still proves the new optional field is
    # accepted without changing the default path. The service rejects the
    # mismatch rather than silently selecting a different identity.
    _seed_analysis(project_id)
    response = auth_client.post(
        f"/api/projects/{project_id}/script",
        json={"narrative_profile_id": "sharp_friend_v1"},
    )
    assert response.status_code == 422, response.text
    assert "narrative" in response.text
    with SessionLocal() as db:
        assert pipeline_service.latest_script_row(db, project_id) is None


def test_profile_request_schema_is_optional_for_existing_v2_callers():
    from app.schemas import AnalysisRequest, ScriptGenerateRequest

    assert AnalysisRequest().narrative_profile_id is None
    assert ScriptGenerateRequest().narrative_profile_id is None
