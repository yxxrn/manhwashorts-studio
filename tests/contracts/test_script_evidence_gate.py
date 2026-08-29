"""RED contract tests for evidence-gated script materialization and approval."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

PANEL_IDS = ("panel-001", "panel-002", "panel-003")
ROLES = ("hook", "setup", "escalation", "editorial_insight", "payoff_open_loop")
SECTION_FOR_ROLE = {
    "hook": "hook",
    "setup": "setup",
    "escalation": "conflict",
    "editorial_insight": "twist",
    "payoff_open_loop": "cta",
}
COVERAGE_HASH = "a" * 64


def _project(db):
    from app.models import Project, User, Workspace
    from app.security import hash_password

    user = User(
        email="script-gate@example.com",
        name="Script Gate",
        password_hash=hash_password("pass12345"),
    )
    db.add(user)
    db.flush()
    workspace = Workspace(owner_id=user.id, name="Script Gate Workspace")
    db.add(workspace)
    db.flush()
    project = Project(
        workspace_id=workspace.id,
        title="Evidence Script Project",
        manhwa_title="Synthetic Chapter",
        chapter="1",
        language="en",
        target_duration=45,
    )
    db.add(project)
    db.flush()
    return project


def _provider_output(asset_ids: list[str]) -> dict[str, Any]:
    from app.services.analyzer_contract import load_analyzer_instruction

    version, digest, _ = load_analyzer_instruction()
    observations = []
    for index, (panel_id, asset_id) in enumerate(zip(PANEL_IDS, asset_ids, strict=True)):
        observations.append(
            {
                "panel_id": panel_id,
                "source_asset_id": asset_id,
                "strip_region_id": f"region-{index + 1}",
                "source_index": index,
                "region_bounds": {"x": 0, "y": 0, "width": 100, "height": 10},
                "coverage_map_version": "vision-coverage-v2",
                "coverage_map_hash": COVERAGE_HASH,
                "visible_facts": [
                    (
                        "A brass compass rests beside a locked dock gate."
                        if index == 0
                        else "Mara hides the compass as a guard approaches."
                        if index == 1
                        else "The gate opens toward a dark boat while Mara remains outside."
                    )
                ],
                "dialogue_or_ocr": [],
                "inferences": [
                    (
                        "The compass appears to matter to Mara."
                        if index == 0
                        else "Mara may be avoiding recognition."
                        if index == 1
                        else "The boat may carry whoever the gate admitted."
                    )
                ],
                "uncertainties": ["The visitor's identity is not visible."]
                if index == 2
                else [],
                "evidence_refs": [panel_id],
            }
        )

    claims = [
        {
            "claim_id": "claim-compass",
            "claim_type": "fact",
            "text": "Mara has the brass compass beside the locked dock.",
            "qualification": "The compass and locked gate are visible in panel-001.",
            "evidence_panel_ids": [PANEL_IDS[0]],
        },
        {
            "claim_id": "claim-hide",
            "claim_type": "interpretation",
            "text": "Mara hides the compass as a guard approaches.",
            "qualification": "Panel-002 shows the action and approach; the guard's intent is unstated.",
            "evidence_panel_ids": [PANEL_IDS[1]],
        },
        {
            "claim_id": "claim-boat",
            "claim_type": "fact",
            "text": "A dark boat is leaving while Mara remains outside.",
            "qualification": "The boat and Mara's position are visible in panel-003.",
            "evidence_panel_ids": [PANEL_IDS[2]],
        },
    ]
    passages = [
        {
            "passage_id": "passage-hook",
            "editorial_role": "hook",
            "text": "Mara returns to the locked dock because the brass compass could leave on the dark boat.",
            "claim_ids": ["claim-compass", "claim-boat"],
            "evidence_panel_ids": [PANEL_IDS[0], PANEL_IDS[2]],
        },
        {
            "passage_id": "passage-setup",
            "editorial_role": "setup",
            "text": "She hides the compass as a guard approaches, but the gate opens for a dark boat while she remains outside.",
            "claim_ids": ["claim-hide", "claim-boat"],
            "evidence_panel_ids": [PANEL_IDS[1], PANEL_IDS[2]],
        },
        {
            "passage_id": "passage-escalation",
            "editorial_role": "escalation",
            "text": "That detail changes the threat: the guard may be more than a blocker, and he appears to control access to the boat while Mara's move leaves the evidence sailing away before she can ask who is waiting inside.",
            "claim_ids": ["claim-hide", "claim-boat"],
            "evidence_panel_ids": [PANEL_IDS[1], PANEL_IDS[2]],
        },
        {
            "passage_id": "passage-insight",
            "editorial_role": "editorial_insight",
            "text": "The clever part is the compass: it turns a locked gate into a choice between staying visible and losing a crucial clue.",
            "claim_ids": ["claim-compass", "claim-hide"],
            "evidence_panel_ids": [PANEL_IDS[0], PANEL_IDS[1]],
        },
        {
            "passage_id": "passage-payoff",
            "editorial_role": "payoff_open_loop",
            "text": "Mara waits outside, but who did the gate open for inside the dark boat?",
            "claim_ids": ["claim-boat"],
            "evidence_panel_ids": [PANEL_IDS[2]],
        },
    ]
    return {
        "observations": observations,
        "continuity_ledger": {
            "chunks": [{"chunk_id": "chunk-001", "panel_ids": list(PANEL_IDS)}],
            "entities": [
                {
                    "entity_id": "entity-mara",
                    "canonical_name": "Mara",
                    "aliases": [],
                    "panel_ids": list(PANEL_IDS),
                }
            ],
            "motives": [],
            "state_changes": [],
            "causal_links": [],
            "reconciled_after_final_chunk": True,
        },
        "evidence_graph": {"claims": claims},
        "coverage_manifest": {
            "total_panels": len(PANEL_IDS),
            "processed_panels": len(PANEL_IDS),
            "panel_ids": list(PANEL_IDS),
            "source_content_coverage_ratio": 1.0,
            "unresolved_material_area": 0,
            "material_unresolved_regions": [],
            "reconciliation_complete": True,
            "coverage_map_version": "vision-coverage-v2",
            "coverage_map_hash": COVERAGE_HASH,
            "source_asset_count": len(PANEL_IDS),
            "original_source_space_area": 3000,
            "accounted_source_space_area": 3000,
            "canonical_panel_count": len(PANEL_IDS),
            "processed_canonical_panel_count": len(PANEL_IDS),
            "duplicate_overlap_observations": 0,
            "unreadable_low_confidence_panels": [],
            "ordering_uncertainties": [],
            "character_ambiguities": [],
        },
        "narrative_outline": {
            "story_spine": {
                "who_wants_what": "Mara wants to keep the compass.",
                "obstacle": "The locked gate and guard block her.",
                "decision": "She stays visible and watches the boat.",
                "consequence": "The evidence may leave without her.",
                "changed_stakes": "She may lose access to whoever the gate admitted.",
                "unresolved_question": "Who did the gate open for?",
            }
        },
        "script_passages": passages,
        "_instruction_version": version,
        "_instruction_sha256": digest,
    }


def _seed_analysis(db, project, *, state: str | None = "RECONCILED", blocking=None):
    from app.models import PanelRegion, SourceAsset, StoryAnalysis
    from app.services.analyzer_contract import load_analyzer_instruction

    version, digest, _ = load_analyzer_instruction()
    row = StoryAnalysis(
        project_id=project.id,
        analysis_run_id="run-script-gate-001",
        state=state,
        provider_type="openai_compatible",
        provider_name="synthetic-vision",
        model_name="synthetic-model",
        instruction_version=version,
        instruction_sha256=digest,
        blocking_reasons_json=blocking,
    )
    db.add(row)
    db.flush()

    assets = []
    for index in range(len(PANEL_IDS)):
        asset = SourceAsset(
            project_id=project.id,
            type="image",
            original_filename=f"synthetic-{index}.png",
            storage_key=f"synthetic/{project.id}/{index}.png",
            mime_type="image/png",
            size_bytes=1,
            checksum=f"{index + 1:064x}",
            width=100,
            height=10,
            rights_owner="Test Fixture",
            license_type="owned",
            rights_status="declared",
            order_index=index,
            original_checksum=f"{index + 1:064x}",
            original_width=100,
            original_height=10,
            source_bounds_json={"x": 0, "y": 0, "width": 100, "height": 10},
            strip_order=index,
            region_order=0,
            trim_classification="unsliced",
            coverage_map_hash=COVERAGE_HASH,
        )
        db.add(asset)
        assets.append(asset)
    db.flush()

    output = _provider_output([asset.id for asset in assets])
    row.coverage_manifest_json = output["coverage_manifest"]
    row.continuity_ledger_json = output["continuity_ledger"]
    row.evidence_graph_json = {
        **output["evidence_graph"],
        "script_passages": output["script_passages"],
    }
    row.story_spine_json = output["narrative_outline"]["story_spine"]
    row.reconciliation_json = {
        "coverage_map_hash": COVERAGE_HASH,
        "coverage_map_version": "vision-coverage-v2",
        "canonical_panel_count": len(PANEL_IDS),
        "processed_panel_count": len(PANEL_IDS),
        "chain_reconciled": True,
        "chain_errors": [],
    }
    for index, (panel_id, asset, observation) in enumerate(
        zip(PANEL_IDS, assets, output["observations"], strict=True)
    ):
        db.add(
            PanelRegion(
                story_analysis_id=row.id,
                source_asset_id=asset.id,
                source_asset_checksum=asset.checksum,
                original_width=100,
                original_height=10,
                strip_region_id=observation["strip_region_id"],
                panel_id=panel_id,
                source_order=index,
                bounds_json=observation["region_bounds"],
                region_class="canonical_panel",
                segmentation_confidence=1.0,
                segmentation_version="vision-coverage-v2",
                coverage_map_hash=COVERAGE_HASH,
                observation_json=observation,
                chunk_index=0,
                evidence_refs_json=observation["evidence_refs"],
            )
        )
    db.flush()
    return row


def _passages(row) -> list[dict]:
    return copy.deepcopy(row.evidence_graph_json["script_passages"])


def _script_sections(row) -> list[dict]:
    return [
        {
            "section": SECTION_FOR_ROLE[passage["editorial_role"]],
            "text": passage["text"],
            "locked": False,
            "editorial_role": passage["editorial_role"],
            "claim_ids": list(passage["claim_ids"]),
            "evidence_panel_ids": list(passage["evidence_panel_ids"]),
            "citations": [index % len(PANEL_IDS)],
        }
        for index, passage in enumerate(_passages(row))
    ]


def _seed_script(db, project, row, *, approved: bool = False):
    from app.models import ScriptVersion

    sections = _script_sections(row)
    script = ScriptVersion(
        project_id=project.id,
        version=1,
        sections=sections,
        hook_options=[sections[0]["text"]],
        selected_hook=0,
        estimated_duration=45.0,
        word_count=sum(len(section["text"].split()) for section in sections),
        warnings=[],
        generator="vision_evidence_v2",
        editorial_metadata={
            "analysis_id": row.id,
            "analysis_run_id": row.analysis_run_id,
            "instruction_version": row.instruction_version,
            "instruction_sha256": row.instruction_sha256,
            "human_review_required": True,
            "editorial_review_confirmed": approved,
            "editorial_review_actor": "human-reviewer" if approved else "",
        },
        approved_by="human-reviewer" if approved else "",
        approved_at=datetime.now(UTC) if approved else None,
    )
    db.add(script)
    db.flush()
    return script


def _script_count(db, project_id: str) -> int:
    from sqlalchemy import func, select

    from app.models import ScriptVersion

    return int(
        db.scalar(
            select(func.count()).select_from(ScriptVersion).where(
                ScriptVersion.project_id == project_id
            )
        )
        or 0
    )


def test_generate_script_requires_existing_analysis_and_never_autoruns(db, monkeypatch):
    from app.services import pipeline as pl

    project = _project(db)
    def unexpected_analysis(*args, **kwargs):
        pytest.fail("generate_script must not auto-run analysis")

    monkeypatch.setattr(pl, "run_analysis", unexpected_analysis)
    monkeypatch.setattr(
        pl.resolver_svc,
        "resolve_vision",
        lambda *args, **kwargs: pytest.fail("script generation must not resolve a provider"),
    )

    with pytest.raises(pl.PipelineError):
        pl.generate_script(db, project.id, actor_id="human-1")

    assert _script_count(db, project.id) == 0


@pytest.mark.parametrize("state", ("PROCESSING", "BLOCKED", None))
def test_generate_script_requires_latest_reconciled_analysis(db, state):
    from app.services import pipeline as pl

    project = _project(db)
    _seed_analysis(db, project, state=state)

    with pytest.raises(pl.PipelineError):
        pl.generate_script(db, project.id, actor_id="human-1")

    assert _script_count(db, project.id) == 0


def test_latest_blocked_analysis_cannot_use_an_older_reconciled_row(db):
    from app.services import pipeline as pl

    project = _project(db)
    older = _seed_analysis(db, project, state="RECONCILED")
    newer = _seed_analysis(
        db,
        project,
        state="BLOCKED",
        blocking={"codes": ["coverage_incomplete"], "findings": [{"stage": "coverage"}]},
    )
    newer.created_at = older.created_at + timedelta(seconds=1)
    db.flush()

    with pytest.raises(pl.PipelineError):
        pl.generate_script(db, project.id, actor_id="human-1")

    assert _script_count(db, project.id) == 0


@pytest.mark.parametrize("field", ("instruction_version", "instruction_sha256"))
def test_generate_script_requires_current_analyzer_instruction(db, field):
    from app.services import pipeline as pl

    project = _project(db)
    row = _seed_analysis(db, project)
    setattr(row, field, "vision-first-story-analyzer-v1" if field == "instruction_version" else "b" * 64)
    db.flush()

    with pytest.raises(pl.PipelineError):
        pl.generate_script(db, project.id, actor_id="human-1")

    assert _script_count(db, project.id) == 0


@pytest.mark.parametrize("mutation", ("ratio", "unresolved", "reconcile", "counts", "chain", "hash"))
def test_generate_script_requires_complete_matching_coverage(mutation, db):
    from app.services import pipeline as pl

    project = _project(db)
    row = _seed_analysis(db, project)
    manifest = copy.deepcopy(row.coverage_manifest_json)
    reconciliation = copy.deepcopy(row.reconciliation_json)
    if mutation == "ratio":
        manifest["source_content_coverage_ratio"] = 0.5
    elif mutation == "unresolved":
        manifest["unresolved_material_area"] = 1
    elif mutation == "reconcile":
        manifest["reconciliation_complete"] = False
    elif mutation == "counts":
        manifest["processed_panels"] = len(PANEL_IDS) - 1
    elif mutation == "chain":
        reconciliation["chain_reconciled"] = False
    else:
        reconciliation["coverage_map_hash"] = "b" * 64
    row.coverage_manifest_json = manifest
    row.reconciliation_json = reconciliation
    db.flush()

    with pytest.raises(pl.PipelineError):
        pl.generate_script(db, project.id, actor_id="human-1")

    assert _script_count(db, project.id) == 0


@pytest.mark.parametrize(
    "mutation",
    ("panel_observation", "continuity", "spine", "claims", "foreign_panel", "passages"),
)
def test_generate_script_revalidates_persisted_evidence(mutation, db):
    from app.services import pipeline as pl

    project = _project(db)
    row = _seed_analysis(db, project)
    if mutation == "panel_observation":
        row.panel_regions[0].observation_json = {}
    elif mutation == "continuity":
        row.continuity_ledger_json = {}
    elif mutation == "spine":
        row.story_spine_json = {"who_wants_what": "Mara wants the compass."}
    elif mutation == "claims":
        evidence = copy.deepcopy(row.evidence_graph_json)
        evidence["claims"] = []
        row.evidence_graph_json = evidence
    elif mutation == "foreign_panel":
        evidence = copy.deepcopy(row.evidence_graph_json)
        evidence["claims"][0]["evidence_panel_ids"] = ["foreign-panel"]
        row.evidence_graph_json = evidence
    else:
        evidence = copy.deepcopy(row.evidence_graph_json)
        evidence["script_passages"] = []
        row.evidence_graph_json = evidence
    db.flush()

    with pytest.raises(pl.PipelineError):
        pl.generate_script(db, project.id, actor_id="human-1")

    assert _script_count(db, project.id) == 0


def test_generate_script_materializes_provider_passages_without_legacy_generator(
    db, monkeypatch
):
    from app.services import pipeline as pl

    project = _project(db)
    row = _seed_analysis(db, project)

    def legacy_generator_called(*args, **kwargs):
        raise AssertionError("RulesScriptGenerator/template path was called")

    monkeypatch.setattr(pl.script_svc, "get_generator", legacy_generator_called)
    script = pl.generate_script(db, project.id, actor_id="human-1")

    expected = _passages(row)
    assert script.generator == "vision_evidence_v2"
    assert [section["text"] for section in script.sections] == [
        passage["text"] for passage in expected
    ]
    assert [section["section"] for section in script.sections] == [
        SECTION_FOR_ROLE[role] for role in ROLES
    ]
    assert [section["editorial_role"] for section in script.sections] == list(ROLES)
    assert all(section["claim_ids"] and section["evidence_panel_ids"] for section in script.sections)
    assert script.hook_options == [expected[0]["text"]]
    assert script.editorial_metadata == {
        "analysis_id": row.id,
        "analysis_run_id": row.analysis_run_id,
        "instruction_version": row.instruction_version,
        "instruction_sha256": row.instruction_sha256,
        "human_review_required": True,
        "editorial_review_confirmed": False,
        "editorial_review_actor": "",
    }
    db.refresh(row)
    assert row.state == "SCRIPT_DRAFT"


def test_generate_script_does_not_carry_locked_section_from_prior_run(db):
    from app.models import ScriptVersion
    from app.services import pipeline as pl

    project = _project(db)
    row = _seed_analysis(db, project)
    old = ScriptVersion(
        project_id=project.id,
        version=1,
        sections=[
            {
                "section": "hook",
                "text": "LOCKED OLD TEMPLATE CTA",
                "locked": True,
                "citations": [],
            }
        ],
        hook_options=["LOCKED OLD TEMPLATE CTA"],
        warnings=[],
        generator="rules",
    )
    db.add(old)
    db.flush()

    script = pl.generate_script(db, project.id, keep_locked=True, actor_id="human-1")
    assert all(section["text"] != "LOCKED OLD TEMPLATE CTA" for section in script.sections)
    assert [section["text"] for section in script.sections] == [
        passage["text"] for passage in _passages(row)
    ]


@pytest.mark.parametrize(
    ("confirmed", "actor_id"),
    ((False, "human-1"), (True, "")),
)
def test_approve_script_requires_explicit_confirmation_and_actor(db, confirmed, actor_id):
    from app.services import pipeline as pl

    project = _project(db)
    row = _seed_analysis(db, project, state="SCRIPT_DRAFT")
    script = _seed_script(db, project, row)

    with pytest.raises(pl.PipelineError):
        pl.approve_script(
            db,
            script.id,
            actor_id=actor_id,
            editorial_review_confirmed=confirmed,
        )

    db.refresh(script)
    db.refresh(row)
    assert script.approved_at is None
    assert row.state == "SCRIPT_DRAFT"


def test_approve_script_requires_current_evidence_and_records_human_review(db):
    from app.services import pipeline as pl

    project = _project(db)
    row = _seed_analysis(db, project, state="SCRIPT_DRAFT")
    script = _seed_script(db, project, row)
    approved = pl.approve_script(
        db,
        script.id,
        actor_id="human-reviewer",
        editorial_review_confirmed=True,
    )

    assert approved.approved_by == "human-reviewer"
    assert approved.approved_at is not None
    assert approved.editorial_metadata["human_review_required"] is True
    assert approved.editorial_metadata["editorial_review_confirmed"] is True
    assert approved.editorial_metadata["editorial_review_actor"] == "human-reviewer"
    db.refresh(row)
    assert row.state == "SCRIPT_APPROVED"


@pytest.mark.parametrize(
    "mutation",
    (
        "analysis_id",
        "analysis_run_id",
        "analysis_evidence",
        "coverage",
        "empty_claims",
        "foreign_claims",
        "empty_panels",
        "foreign_panels",
        "warning",
    ),
)
def test_approve_script_rejects_stale_or_corrupt_evidence(db, mutation):
    from app.services import pipeline as pl

    project = _project(db)
    row = _seed_analysis(db, project, state="SCRIPT_DRAFT")
    script = _seed_script(db, project, row)
    if mutation in {"analysis_id", "analysis_run_id"}:
        metadata = copy.deepcopy(script.editorial_metadata)
        metadata[mutation] = "stale-value"
        script.editorial_metadata = metadata
    elif mutation == "analysis_evidence":
        evidence = copy.deepcopy(row.evidence_graph_json)
        evidence["claims"][0]["evidence_panel_ids"] = ["foreign-panel"]
        row.evidence_graph_json = evidence
    elif mutation == "coverage":
        manifest = copy.deepcopy(row.coverage_manifest_json)
        manifest["source_content_coverage_ratio"] = 0.5
        row.coverage_manifest_json = manifest
    elif mutation in {"empty_claims", "foreign_claims", "empty_panels", "foreign_panels"}:
        sections = copy.deepcopy(script.sections)
        if mutation == "empty_claims":
            sections[0]["claim_ids"] = []
        elif mutation == "foreign_claims":
            sections[0]["claim_ids"] = ["foreign-claim"]
        elif mutation == "empty_panels":
            sections[0]["evidence_panel_ids"] = []
        else:
            sections[0]["evidence_panel_ids"] = ["foreign-panel"]
        script.sections = sections
    else:
        script.warnings = [
            {
                "code": "editorial.blocking",
                "severity": "error",
                "message": "synthetic blocking warning",
            }
        ]
    db.flush()

    with pytest.raises(pl.PipelineError):
        pl.approve_script(
            db,
            script.id,
            actor_id="human-reviewer",
            editorial_review_confirmed=True,
        )

    db.refresh(script)
    db.refresh(row)
    assert script.approved_at is None
    assert script.approved_by == ""
    assert row.state == "SCRIPT_DRAFT"


def test_update_script_clears_approval_and_round_trips_evidence_fields(db):
    from app.services import pipeline as pl

    project = _project(db)
    row = _seed_analysis(db, project, state="SCRIPT_APPROVED")
    script = _seed_script(db, project, row, approved=True)
    sections = _script_sections(row)
    sections[0]["text"] = "Mara rechecks the dock before the boat leaves."

    updated = pl.update_script(
        db,
        script.id,
        sections,
        selected_hook=0,
        actor_id="editor-2",
    )

    assert updated.approved_at is None
    assert updated.approved_by == ""
    assert updated.sections[0]["editorial_role"] == "hook"
    assert updated.sections[0]["claim_ids"] == ["claim-compass", "claim-boat"]
    assert updated.sections[0]["evidence_panel_ids"] == [PANEL_IDS[0], PANEL_IDS[2]]
    db.refresh(row)
    assert row.state == "SCRIPT_DRAFT"
    assert updated.editorial_metadata["editorial_review_confirmed"] is False


@pytest.mark.parametrize("mode", ("missing", "text_only", "blocked"))
def test_generate_draft_never_falls_back_to_legacy_or_rules(db, monkeypatch, mode):
    from app.models import SourceAsset, StoryAnalysis
    from app.services import pipeline as pl

    project = _project(db)
    if mode == "text_only":
        db.add(
            SourceAsset(
                project_id=project.id,
                type="text",
                original_filename="recap.txt",
                storage_key="synthetic/recap.txt",
                size_bytes=64,
                checksum="c" * 64,
                mime_type="text/plain",
                extracted_text="A synthetic text-only recap with enough words for a fixture.",
            )
        )
        db.flush()
    elif mode == "blocked":
        db.add(
            StoryAnalysis(
                project_id=project.id,
                state="BLOCKED",
                blocking_reasons_json={
                    "codes": ["vision_capability_missing"],
                    "findings": [],
                },
            )
        )
        db.flush()

    monkeypatch.setattr(
        pl,
        "run_legacy_text_analysis",
        lambda *args, **kwargs: pytest.fail("public draft must not run legacy analysis"),
    )
    monkeypatch.setattr(
        pl.script_svc,
        "get_generator",
        lambda *args, **kwargs: pytest.fail("public draft must not use RulesScriptGenerator"),
    )
    with pytest.raises(pl.PipelineError):
        pl.generate_draft(db, project.id, actor_id="human-1")
    assert _script_count(db, project.id) == 0


def test_generate_draft_stops_before_media_even_with_older_approved_script(db, monkeypatch):
    from app.models import ScriptVersion
    from app.services import pipeline as pl

    project = _project(db)
    row = _seed_analysis(db, project, state="RECONCILED")
    db.add(
        ScriptVersion(
            project_id=project.id,
            version=1,
            sections=[],
            hook_options=[],
            warnings=[],
            generator="vision_evidence_v2",
            editorial_metadata={},
            approved_by="older-reviewer",
            approved_at=datetime.now(UTC),
        )
    )
    db.flush()
    for name in ("generate_voiceover", "build_timeline", "project_cues"):
        monkeypatch.setattr(
            pl,
            name,
            lambda *args, _name=name, **kwargs: pytest.fail(
                f"generate_draft must not call {_name} before approval"
            ),
        )

    result = pl.generate_draft(db, project.id, actor_id="human-1")

    assert result["audio_duration"] == 0.0
    assert result["segments"] == 0
    assert result["scenes"] == 0
    assert result["cues"] == 0
    db.refresh(row)
    assert row.state == "SCRIPT_DRAFT"


def test_media_stages_reject_unapproved_latest_vision_script(db, monkeypatch):
    from app.services import pipeline as pl

    project = _project(db)
    row = _seed_analysis(db, project, state="RECONCILED")
    old_script = _seed_script(db, project, row, approved=True)
    assert old_script.approved_at is not None
    draft = pl.generate_script(db, project.id, actor_id="human-1")
    assert draft.approved_at is None

    monkeypatch.setattr(
        pl.resolver_svc,
        "resolve_tts",
        lambda *args, **kwargs: pytest.fail("TTS must not run for an unapproved latest script"),
    )
    with pytest.raises(pl.PipelineError, match="approved"):
        pl.generate_voiceover(db, project.id, actor_id="human-1")
    with pytest.raises(pl.PipelineError, match="approved"):
        pl.build_timeline(db, project.id, actor_id="human-1")
