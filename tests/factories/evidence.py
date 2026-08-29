"""Shared test factories extracted from regression modules."""
# ruff: noqa: F401

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

PANEL_IDS = ("panel-001", "panel-002", "panel-003")

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

