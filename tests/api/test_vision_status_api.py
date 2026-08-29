"""RED contract tests for safe analysis status and explicit script review."""

from __future__ import annotations

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
COVERAGE_HASH = "b" * 64


def _make_project(client) -> str:
    response = client.post(
        "/api/projects",
        json={
            "title": "Safe Vision Status",
            "manhwa_title": "Synthetic Evidence",
            "chapter": "1",
            "language": "en",
            "target_duration": 45,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _analysis_payload(asset_ids: list[str]) -> dict[str, Any]:
    panel_ids = list(PANEL_IDS)
    observations = [
        {
            "panel_id": panel_ids[0],
            "source_asset_id": asset_ids[0],
            "strip_region_id": "region-001",
            "source_index": 0,
            "region_bounds": {"x": 0, "y": 0, "width": 800, "height": 100},
            "coverage_map_version": "vision-coverage-v2",
            "coverage_map_hash": COVERAGE_HASH,
            "visible_facts": ["A brass compass rests beside a locked dock gate."],
            "dialogue_or_ocr": [],
            "inferences": ["The compass appears to matter to Mara."],
            "uncertainties": [],
            "evidence_refs": [panel_ids[0]],
        },
        {
            "panel_id": panel_ids[1],
            "source_asset_id": asset_ids[1],
            "strip_region_id": "region-002",
            "source_index": 1,
            "region_bounds": {"x": 0, "y": 0, "width": 800, "height": 100},
            "coverage_map_version": "vision-coverage-v2",
            "coverage_map_hash": COVERAGE_HASH,
            "visible_facts": ["Mara hides the compass as a guard approaches."],
            "dialogue_or_ocr": [],
            "inferences": ["Mara may be avoiding recognition."],
            "uncertainties": ["The guard's intent is not visible."],
            "evidence_refs": [panel_ids[1]],
        },
        {
            "panel_id": panel_ids[2],
            "source_asset_id": asset_ids[2],
            "strip_region_id": "region-003",
            "source_index": 2,
            "region_bounds": {"x": 0, "y": 0, "width": 800, "height": 100},
            "coverage_map_version": "vision-coverage-v2",
            "coverage_map_hash": COVERAGE_HASH,
            "visible_facts": [
                "The gate opens toward a dark boat while Mara remains outside."
            ],
            "dialogue_or_ocr": [],
            "inferences": ["The boat may carry whoever the gate admitted."],
            "uncertainties": ["The visitor's identity is not visible."],
            "evidence_refs": [panel_ids[2]],
        },
    ]
    claims = [
        {
            "claim_id": "claim-compass",
            "claim_type": "fact",
            "text": "Mara has the brass compass beside the locked dock.",
            "qualification": "The compass and locked gate are visible in panel-001.",
            "evidence_panel_ids": [panel_ids[0]],
        },
        {
            "claim_id": "claim-hide",
            "claim_type": "interpretation",
            "text": "Mara hides the compass as a guard approaches.",
            "qualification": "Panel-002 shows the action and approach; the guard's intent is unstated.",
            "evidence_panel_ids": [panel_ids[1]],
        },
        {
            "claim_id": "claim-boat",
            "claim_type": "fact",
            "text": "A dark boat is leaving while Mara remains outside.",
            "qualification": "The boat and Mara's position are visible in panel-003.",
            "evidence_panel_ids": [panel_ids[2]],
        },
    ]
    passages = [
        {
            "passage_id": "passage-dock-hook",
            "editorial_role": "hook",
            "text": "Mara returns to the locked dock because the brass compass could leave on the dark boat.",
            "claim_ids": ["claim-compass", "claim-boat"],
            "evidence_panel_ids": [panel_ids[0], panel_ids[2]],
        },
        {
            "passage_id": "passage-dock-setup",
            "editorial_role": "setup",
            "text": "She hides the compass as a guard approaches, but the gate opens for a dark boat while she remains outside.",
            "claim_ids": ["claim-hide", "claim-boat"],
            "evidence_panel_ids": [panel_ids[1], panel_ids[2]],
        },
        {
            "passage_id": "passage-dock-escalation",
            "editorial_role": "escalation",
            "text": "That detail changes the threat: the guard may be more than a blocker, and he appears to control access to the boat while Mara's move leaves the evidence sailing away before she can ask who is waiting inside.",
            "claim_ids": ["claim-hide", "claim-boat"],
            "evidence_panel_ids": [panel_ids[1], panel_ids[2]],
        },
        {
            "passage_id": "passage-dock-insight",
            "editorial_role": "editorial_insight",
            "text": "The clever part is the compass: it turns a locked gate into a choice between staying visible and losing a crucial clue.",
            "claim_ids": ["claim-compass", "claim-hide"],
            "evidence_panel_ids": [panel_ids[0], panel_ids[1]],
        },
        {
            "passage_id": "passage-dock-payoff",
            "editorial_role": "payoff_open_loop",
            "text": "Mara watches the dark boat leave. Who did the gate admit?",
            "claim_ids": ["claim-boat"],
            "evidence_panel_ids": [panel_ids[2]],
        },
    ]
    return {
        "observations": observations,
        "continuity_ledger": {
            "chunks": [{"chunk_id": "chunk-status-0", "panel_ids": panel_ids}],
            "entities": [
                {
                    "entity_id": "entity-mara",
                    "canonical_name": "Mara",
                    "aliases": ["the cartographer"],
                    "panel_ids": panel_ids,
                }
            ],
            "motives": [
                {
                    "entity_id": "entity-mara",
                    "text": "keep the compass from leaving",
                    "evidence_panel_ids": [panel_ids[0], panel_ids[1]],
                }
            ],
            "state_changes": [
                {
                    "entity_id": "entity-mara",
                    "from": "holding the compass openly",
                    "to": "hiding the compass",
                    "evidence_panel_ids": [panel_ids[0], panel_ids[1]],
                }
            ],
            "causal_links": [
                {
                    "from_panel_id": panel_ids[0],
                    "to_panel_id": panel_ids[1],
                    "reason": "the guard approaches the dock",
                    "evidence_panel_ids": [panel_ids[0], panel_ids[1]],
                },
                {
                    "from_panel_id": panel_ids[1],
                    "to_panel_id": panel_ids[2],
                    "reason": "Mara waits outside after hiding the compass",
                    "evidence_panel_ids": [panel_ids[1], panel_ids[2]],
                },
            ],
            "reconciled_after_final_chunk": True,
        },
        "evidence_graph": {"claims": claims},
        "coverage_manifest": {
            "total_assets": 3,
            "original_source_space_area": 240000,
            "accounted_source_space_area": 240000,
            "total_panels": 3,
            "total_canonical_panels": 3,
            "persisted_canonical_panels": 3,
            "processed_panels": 3,
            "processed_canonical_panel_count": 3,
            "duplicate_overlap_observations": 0,
            "panel_ids": panel_ids,
            "source_content_coverage_ratio": 1.0,
            "unresolved_material_area": 0,
            "material_unresolved_regions": [],
            "reconciliation_complete": True,
            "unreadable_low_confidence_panels": [],
            "ordering_uncertainties": [],
            "character_ambiguities": [],
            "tile_ranges": [],
            "tile_overlap": [],
            "coverage_map_version": "vision-coverage-v2",
            "coverage_map_hash": COVERAGE_HASH,
            "claim_to_panel_refs": {
                claim["claim_id"]: claim["evidence_panel_ids"] for claim in claims
            },
        },
        "narrative_outline": {
            "story_spine": {
                "who_wants_what": "Mara wants the brass compass before the boat leaves.",
                "obstacle": "A guard approaches while the dock gate remains locked.",
                "decision": "Mara hides the compass and waits outside.",
                "consequence": "The boat can move before she reaches it.",
                "changed_stakes": "She may lose access to whoever the gate admitted.",
                "unresolved_question": "Who is inside the dark boat?",
            }
        },
        "script_passages": passages,
    }


def _seed_analysis(
    project_id: str,
    *,
    state: str = "RECONCILED",
    blocking: dict[str, Any] | None = None,
) -> Any:
    from app.db import SessionLocal
    from app.models import PanelRegion, SourceAsset, StoryAnalysis
    from app.services.analyzer_contract import load_analyzer_instruction

    version, digest, _ = load_analyzer_instruction()
    with SessionLocal() as db:
        row = StoryAnalysis(
            project_id=project_id,
            analysis_run_id="run-status-001",
            state=state,
            provider_type="openai_compatible",
            provider_name="synthetic-provider",
            model_name="synthetic-vision",
            instruction_version=version,
            instruction_sha256=digest,
            blocking_reasons_json=blocking,
        )
        db.add(row)
        db.flush()
        assets = []
        for index in range(len(PANEL_IDS)):
            asset = SourceAsset(
                project_id=project_id,
                type="image",
                original_filename=f"synthetic-status-{index}.png",
                storage_key=f"synthetic-status/{project_id}/{index}.png",
                mime_type="image/png",
                size_bytes=1,
                checksum=f"{index + 1:064x}",
                width=800,
                height=100,
                rights_owner="Synthetic Test Fixture",
                license_type="owned",
                rights_status="declared",
                order_index=index,
                original_checksum=f"{index + 1:064x}",
                original_width=800,
                original_height=100,
                source_bounds_json={"x": 0, "y": 0, "width": 800, "height": 100},
                strip_order=index,
                region_order=0,
                trim_classification="unsliced",
                coverage_map_hash=COVERAGE_HASH,
            )
            db.add(asset)
            assets.append(asset)
        db.flush()

        payload = _analysis_payload([asset.id for asset in assets])
        row.coverage_manifest_json = payload["coverage_manifest"]
        row.continuity_ledger_json = payload["continuity_ledger"]
        row.evidence_graph_json = {
            **payload["evidence_graph"],
            "script_passages": payload["script_passages"],
        }
        row.story_spine_json = payload["narrative_outline"]["story_spine"]
        row.reconciliation_json = {
            "coverage_map_version": "vision-coverage-v2",
            "coverage_map_hash": COVERAGE_HASH,
            "canonical_panel_count": len(PANEL_IDS),
            "processed_panel_count": len(PANEL_IDS),
            "chain_reconciled": True,
            "chain_errors": [],
        }
        for index, (panel_id, asset, observation) in enumerate(
            zip(PANEL_IDS, assets, payload["observations"], strict=True)
        ):
            db.add(
                PanelRegion(
                    story_analysis_id=row.id,
                    source_asset_id=asset.id,
                    source_asset_checksum=asset.checksum,
                    original_width=asset.original_width,
                    original_height=asset.original_height,
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
        db.commit()
        db.refresh(row)
        return row.id


def seed_reconciled_analysis_for_project_images(project_id: str) -> str:
    """Seed valid v2 evidence while retaining the project's real image assets."""
    from app.db import SessionLocal
    from app.models import PanelRegion, SourceAsset, StoryAnalysis
    from app.services.analyzer_contract import load_analyzer_instruction

    with SessionLocal() as db:
        assets = list(
            db.query(SourceAsset)
            .filter(SourceAsset.project_id == project_id, SourceAsset.type == "image")
            .order_by(SourceAsset.order_index, SourceAsset.id)
            .all()
        )
        if not assets:
            raise AssertionError("vision fixture requires at least one image asset")
        selected_assets = [assets[index % len(assets)] for index in range(len(PANEL_IDS))]
        version, digest, _ = load_analyzer_instruction()
        row = StoryAnalysis(
            project_id=project_id,
            analysis_run_id="run-existing-images-001",
            state="RECONCILED",
            provider_type="openai_compatible",
            provider_name="synthetic-provider",
            model_name="synthetic-vision",
            instruction_version=version,
            instruction_sha256=digest,
        )
        db.add(row)
        db.flush()
        payload = _analysis_payload([asset.id for asset in selected_assets])
        row.coverage_manifest_json = payload["coverage_manifest"]
        row.continuity_ledger_json = payload["continuity_ledger"]
        row.evidence_graph_json = {
            **payload["evidence_graph"],
            "script_passages": payload["script_passages"],
        }
        row.story_spine_json = payload["narrative_outline"]["story_spine"]
        row.reconciliation_json = {
            "coverage_map_version": "vision-coverage-v2",
            "coverage_map_hash": COVERAGE_HASH,
            "canonical_panel_count": len(PANEL_IDS),
            "processed_panel_count": len(PANEL_IDS),
            "chain_reconciled": True,
            "chain_errors": [],
        }
        for index, (panel_id, asset, observation) in enumerate(
            zip(PANEL_IDS, selected_assets, payload["observations"], strict=True)
        ):
            db.add(
                PanelRegion(
                    story_analysis_id=row.id,
                    source_asset_id=asset.id,
                    source_asset_checksum=asset.checksum,
                    original_width=asset.original_width or asset.width or 800,
                    original_height=asset.original_height or asset.height or 100,
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
        db.commit()
        return row.id


def _seed_script(project_id: str) -> str:
    from app.db import SessionLocal
    from app.models import ScriptVersion, StoryAnalysis
    from app.services.analyzer_contract import load_analyzer_instruction

    version, digest, _ = load_analyzer_instruction()
    with SessionLocal() as db:
        analysis = db.query(StoryAnalysis).filter_by(project_id=project_id).one()
        analysis.state = "SCRIPT_DRAFT"
        sections = [
            {
                "section": SECTION_FOR_ROLE[passage["editorial_role"]],
                "text": passage["text"],
                "locked": False,
                "editorial_role": passage["editorial_role"],
                "claim_ids": list(passage["claim_ids"]),
                "evidence_panel_ids": list(passage["evidence_panel_ids"]),
                "evidence": [
                    {
                        "claim_id": claim_id,
                        "panel_ids": list(passage["evidence_panel_ids"]),
                    }
                    for claim_id in passage["claim_ids"]
                ],
                "citations": [index % len(PANEL_IDS)],
            }
            for index, passage in enumerate(analysis.evidence_graph_json["script_passages"])
        ]
        script = ScriptVersion(
            project_id=project_id,
            version=1,
            sections=sections,
            hook_options=[sections[0]["text"]],
            selected_hook=0,
            estimated_duration=45.0,
            word_count=sum(len(section["text"].split()) for section in sections),
            warnings=[],
            generator="vision_evidence_v2",
            editorial_metadata={
                "analysis_id": analysis.id,
                "analysis_run_id": analysis.analysis_run_id,
                "instruction_version": version,
                "instruction_sha256": digest,
                "human_review_required": True,
                "editorial_review_confirmed": False,
            },
        )
        db.add(script)
        db.commit()
        db.refresh(script)
        return script.id


def _script_count(project_id: str) -> int:
    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models import ScriptVersion

    with SessionLocal() as db:
        return db.scalar(
            select(func.count()).select_from(ScriptVersion).where(
                ScriptVersion.project_id == project_id
            )
        )


def test_analysis_status_returns_safe_summary_only(auth_client):
    project_id = _make_project(auth_client)
    _seed_analysis(project_id)

    response = auth_client.get(f"/api/projects/{project_id}/analysis/status")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "RECONCILED"
    assert body["run_id"] == "run-status-001"
    assert body["provider_type"] == "openai_compatible"
    assert body["provider_name"] == "synthetic-provider"
    assert body["model"] == "synthetic-vision"
    assert body["instruction_version"]
    assert len(body["instruction_sha256"]) == 64
    assert body["coverage_map_version"] == "vision-coverage-v2"
    assert body["coverage_map_hash"] == COVERAGE_HASH
    assert body["total_panels"] == 3
    assert body["processed_panels"] == 3
    assert body["source_content_coverage_ratio"] == 1.0
    assert body["unresolved_material_area"] == 0
    assert body["reconciliation_complete"] is True
    assert body["chain_reconciled"] is True
    assert body["claim_count"] == 3
    assert body["passage_count"] == 5
    assert body["blocking_codes"] == []
    assert body["findings"] == []
    forbidden = {
        "api_key",
        "storage_path",
        "payload",
        "raw_provider_output",
        "observations",
        "claims",
        "script_passages",
    }
    assert forbidden.isdisjoint(body)
    assert "status-secret" not in response.text


def test_text_only_analysis_returns_200_blocked_and_no_script(auth_client):
    project_id = _make_project(auth_client)

    response = auth_client.post(f"/api/projects/{project_id}/analysis")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "BLOCKED"
    assert "vision_capability_missing" in body["blocking_reasons"]["codes"]
    assert _script_count(project_id) == 0


def test_script_before_reconciled_returns_422(auth_client):
    project_id = _make_project(auth_client)
    _seed_analysis(project_id, state="PROCESSING")

    response = auth_client.post(
        f"/api/projects/{project_id}/script", json={"keep_locked": False}
    )

    assert response.status_code == 422, response.text
    assert _script_count(project_id) == 0


@pytest.mark.parametrize(
    "payload",
    [pytest.param(None, id="missing"), pytest.param({}, id="empty"), pytest.param(
        {"editorial_review_confirmed": False}, id="false"
    )],
)
def test_script_approve_requires_explicit_confirmation(auth_client, payload):
    project_id = _make_project(auth_client)
    analysis_id = _seed_analysis(project_id, state="SCRIPT_DRAFT")
    script_id = _seed_script(project_id)
    request = {} if payload is None else payload

    response = auth_client.post(
        f"/api/projects/{project_id}/script/approve",
        json=request,
    )

    assert response.status_code == 422, response.text
    from app.db import SessionLocal
    from app.models import ScriptVersion, StoryAnalysis

    with SessionLocal() as db:
        script = db.get(ScriptVersion, script_id)
        analysis = db.get(StoryAnalysis, analysis_id)
        assert script.approved_at is None
        assert script.approved_by == ""
        assert analysis.state == "SCRIPT_DRAFT"


def test_script_approve_true_fixture_succeeds_and_confirms(auth_client):
    project_id = _make_project(auth_client)
    analysis_id = _seed_analysis(project_id, state="SCRIPT_DRAFT")
    script_id = _seed_script(project_id)

    response = auth_client.post(
        f"/api/projects/{project_id}/script/approve",
        json={"editorial_review_confirmed": True},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["editorial_metadata"]["human_review_required"] is True
    assert body["editorial_metadata"]["editorial_review_confirmed"] is True
    assert body["editorial_metadata"]["editorial_review_actor"]
    from app.db import SessionLocal
    from app.models import ScriptVersion, StoryAnalysis

    with SessionLocal() as db:
        script = db.get(ScriptVersion, script_id)
        analysis = db.get(StoryAnalysis, analysis_id)
        assert script.approved_at is not None
        assert script.approved_by
        assert analysis.state == "SCRIPT_APPROVED"


def test_script_update_round_trips_editorial_evidence_fields(auth_client):
    project_id = _make_project(auth_client)
    _seed_analysis(project_id, state="SCRIPT_DRAFT")
    _seed_script(project_id)

    response = auth_client.patch(
        f"/api/projects/{project_id}/script",
        json={
            "sections": [
                {
                    "section": "hook",
                    "text": "Mara watches the gate.",
                    "locked": False,
                    "citations": [0],
                    "editorial_role": "hook",
                    "claim_ids": ["claim-compass"],
                    "evidence_panel_ids": ["panel-001"],
                    "evidence": [
                        {"claim_id": "claim-compass", "panel_ids": ["panel-001"]}
                    ],
                }
            ],
            "selected_hook": 0,
        },
    )

    assert response.status_code == 200, response.text
    section = response.json()["sections"][0]
    assert section["editorial_role"] == "hook"
    assert section["claim_ids"] == ["claim-compass"]
    assert section["evidence_panel_ids"] == ["panel-001"]
    assert section["evidence"] == [
        {"claim_id": "claim-compass", "panel_ids": ["panel-001"]}
    ]


@pytest.mark.parametrize("mode", ("missing", "text_only", "blocked"))
def test_public_draft_never_calls_legacy_or_rules_fallback(auth_client, monkeypatch, mode):
    from app.services import pipeline as pl

    project_id = _make_project(auth_client)
    if mode == "text_only":
        response = auth_client.post(
            f"/api/projects/{project_id}/assets/text",
            json={
                "text": "A synthetic text-only recap with enough words for a fixture.",
                "rights": {"declared": True, "rights_owner": "Synthetic"},
            },
        )
        assert response.status_code == 201, response.text
    elif mode == "blocked":
        response = auth_client.post(f"/api/projects/{project_id}/analysis")
        assert response.status_code == 200
        assert response.json()["state"] == "BLOCKED"

    monkeypatch.setattr(
        pl,
        "run_legacy_text_analysis",
        lambda *args, **kwargs: pytest.fail("public /draft must not run legacy analysis"),
    )
    monkeypatch.setattr(
        pl.script_svc,
        "get_generator",
        lambda *args, **kwargs: pytest.fail("public /draft must not use RulesScriptGenerator"),
    )
    response = auth_client.post(f"/api/projects/{project_id}/draft")
    assert response.status_code == 422, response.text
    assert _script_count(project_id) == 0
