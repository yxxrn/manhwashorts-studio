"""Shared test factories extracted from regression modules."""
# ruff: noqa: F401

from __future__ import annotations

import copy

import pytest


def _seed_sharp_friend(db):
    """Reuse the complete rights-safe evidence fixture, then opt it into v3."""

    from app.models import PanelRegion
    from app.services import analyzer_contract, visual_scoring
    from app.services.narrative_identity import get_narrative_identity
    from tests.factories.evidence import _project, _seed_analysis

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

