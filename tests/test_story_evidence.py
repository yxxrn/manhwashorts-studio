"""RED tests for fail-closed story evidence persistence and blocking."""

from __future__ import annotations

import hashlib
import importlib
import io
from collections.abc import Mapping
from typing import Any

import pytest
from PIL import Image


def _pipeline_module():
    module = importlib.import_module("app.services.pipeline")
    assert module is not None
    return module


def _png_bytes(seed: int) -> bytes:
    image = Image.new("RGB", (8, 4), (40 + seed, 80, 120))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _seed_project(
    db,
    *,
    unresolved: bool = False,
    storage_failure: bool = False,
    text_only: bool = False,
):
    from app.constants import AssetType, LicenseType, RightsStatus
    from app.models import Project, SourceAsset, User, Workspace
    from app.security import hash_password
    from app.services import storage

    user = User(
        email="story-evidence@example.com",
        name="Evidence Fixture",
        password_hash=hash_password("pass12345"),
    )
    db.add(user)
    db.flush()
    workspace = Workspace(owner_id=user.id, name="Evidence Workspace")
    db.add(workspace)
    db.flush()
    project = Project(
        workspace_id=workspace.id,
        title="Evidence Fixture",
        manhwa_title="Synthetic Chapter",
        chapter="2",
        language="en",
    )
    db.add(project)
    db.flush()

    if text_only:
        db.add(
            SourceAsset(
                project_id=project.id,
                type=AssetType.TEXT,
                original_filename="legacy-recap.txt",
                extracted_text="Legacy recap text must not satisfy vision-first analysis.",
                mime_type="text/plain",
                rights_owner="fixture",
                license_type=LicenseType.OWNED,
                rights_status=RightsStatus.DECLARED,
                order_index=0,
            )
        )
        db.flush()
        return project.id

    for order_index in range(2):
        payload = b"not-an-image" if unresolved else _png_bytes(order_index)
        if storage_failure and order_index == 0:
            storage_key = "projects/missing/evidence.png"
            size_bytes = 0
            checksum = hashlib.sha256(b"missing").hexdigest()
        else:
            stored = storage.put_bytes(
                f"projects/{project.id}/images",
                f"panel-{order_index}.png",
                payload,
            )
            storage_key = stored.storage_key
            size_bytes = stored.size_bytes
            checksum = stored.checksum
        db.add(
            SourceAsset(
                project_id=project.id,
                type=AssetType.IMAGE,
                original_filename=f"panel-{order_index}.png",
                storage_key=storage_key,
                mime_type="image/png",
                size_bytes=size_bytes,
                checksum=checksum,
                width=8,
                height=4,
                original_checksum=f"original-{order_index}",
                original_width=8,
                original_height=4,
                source_bounds_json={"x": 0, "y": 0, "width": 8, "height": 4},
                strip_order=order_index,
                region_order=0,
                trim_classification="preserved",
                rights_owner="fixture",
                license_type=LicenseType.OWNED,
                rights_status=RightsStatus.DECLARED,
                order_index=order_index,
            )
        )
    db.flush()
    return project.id


def _panel_mapping(panel: Any) -> dict[str, Any]:
    if isinstance(panel, Mapping):
        return dict(panel)
    return {
        "panel_id": getattr(panel, "panel_id", ""),
        "source_asset_id": getattr(panel, "source_asset_id", ""),
        "strip_region_id": getattr(panel, "strip_region_id", ""),
        "source_order": getattr(panel, "source_order", 0),
        "region_bounds": getattr(panel, "bounds_json", {}) or {},
        "coverage_map_version": getattr(panel, "segmentation_version", "vision-coverage-v2"),
        "coverage_map_hash": getattr(panel, "coverage_map_hash", "fixture-map"),
    }


def _semantic_observation(panel: Any) -> dict[str, Any]:
    values = _panel_mapping(panel)
    panel_id = values["panel_id"]
    return {
        "panel_id": panel_id,
        "visible_facts": [f"Visible synthetic fact for {panel_id}"],
        "dialogue_or_ocr": [],
        "inferences": [],
        "uncertainties": [],
        "entities": [],
        "state_changes": [],
        "causal_links": [],
        "evidence_refs": [panel_id],
    }


def _canonical_observation(panel: Any, source_index: int) -> dict[str, Any]:
    values = _panel_mapping(panel)
    bounds = values.get("region_bounds") or {"x": 0, "y": 0, "width": 1, "height": 1}
    return {
        "panel_id": values["panel_id"],
        "source_asset_id": values.get("source_asset_id", "fixture-asset"),
        "strip_region_id": values.get("strip_region_id", values["panel_id"]),
        "source_index": source_index,
        "region_bounds": dict(bounds),
        "coverage_map_version": values.get("coverage_map_version", "vision-coverage-v2"),
        "coverage_map_hash": values.get("coverage_map_hash", "fixture-map"),
        "visible_facts": list(values.get("visible_facts", ["A visible synthetic fact"])),
        "dialogue_or_ocr": list(values.get("dialogue_or_ocr", [])),
        "inferences": list(values.get("inferences", [])),
        "uncertainties": list(values.get("uncertainties", [])),
        "evidence_refs": [values["panel_id"]],
    }


def _chunk_mapping(chunk: Any, index: int) -> dict[str, Any]:
    if isinstance(chunk, Mapping):
        return {
            "chunk_id": chunk.get("chunk_id", f"chunk-{index}"),
            "panel_ids": list(chunk.get("panel_ids", [])),
        }
    return {
        "chunk_id": f"chunk-{index}",
        "panel_ids": [getattr(panel, "panel_id", "") for panel in chunk],
    }


def _output_for_request(request, mode: str) -> dict[str, Any]:
    expected = tuple(request.expected_panel_ids)
    observations = [
        _canonical_observation(panel, index)
        for index, panel in enumerate(request.ordered_observations)
    ]
    chunks = [
        _chunk_mapping(chunk, index)
        for index, chunk in enumerate(request.chunks)
    ]
    manifest = dict(request.coverage_manifest)
    manifest.update(
        {
            "total_panels": len(expected),
            "processed_panels": len(expected),
            "panel_ids": list(expected),
            "source_content_coverage_ratio": 1.0,
            "unresolved_material_area": 0,
            "material_unresolved_regions": [],
            "reconciliation_complete": True,
        }
    )
    claim = {
        "claim_id": "claim-evidence-fixture",
        "claim_type": "fact",
        "text": "The evidence fixture contains two visible panels.",
        "qualification": "This is directly visible in the supplied panels.",
        "evidence_panel_ids": list(expected),
    }
    output = {
        "observations": observations,
        "continuity_ledger": {
            "chunks": chunks,
            "entities": [
                {
                    "entity_id": "entity-fixture",
                    "canonical_name": "Evidence witness",
                    "aliases": [],
                    "panel_ids": list(expected),
                }
            ],
            "motives": [],
            "state_changes": [],
            "causal_links": [],
            "reconciled_after_final_chunk": True,
        },
        "evidence_graph": {"claims": [claim]},
        "coverage_manifest": manifest,
        "narrative_outline": {
            "story_spine": {
                "who_wants_what": "The witness wants the visible truth.",
                "obstacle": "The clue is initially incomplete.",
                "decision": "The witness follows the clue.",
                "consequence": "The clue changes the immediate risk.",
                "changed_stakes": "The next choice now has consequences.",
                "unresolved_question": "What will the next panel reveal?",
            }
        },
        "script_passages": [
            {
                "passage_id": "passage-evidence-hook",
                "editorial_role": "hook",
                "text": "A visible clue appears before the witness can decide whether to follow it in the dark.",
                "claim_ids": [claim["claim_id"]],
                "evidence_panel_ids": list(expected),
            },
            {
                "passage_id": "passage-evidence-setup",
                "editorial_role": "setup",
                "text": "The witness studies the clue while the surrounding panels show a path toward an uncertain destination and leave the witness with one direction.",
                "claim_ids": [claim["claim_id"]],
                "evidence_panel_ids": list(expected),
            },
            {
                "passage_id": "passage-evidence-escalation",
                "editorial_role": "escalation",
                "text": "That movement raises the stakes because the clue points forward, yet the witness still cannot see who arranged it or what waits beyond the next panel, before the trail can disappear entirely.",
                "claim_ids": [claim["claim_id"]],
                "evidence_panel_ids": list(expected),
            },
            {
                "passage_id": "passage-evidence-insight",
                "editorial_role": "editorial_insight",
                "text": "The detail matters because a quiet image can change the witness's safest choice without warning while the clue remains visible.",
                "claim_ids": [claim["claim_id"]],
                "evidence_panel_ids": list(expected),
            },
            {
                "passage_id": "passage-evidence-payoff",
                "editorial_role": "payoff_open_loop",
                "text": "Who placed the clue there, and what will the next panel reveal?",
                "claim_ids": [claim["claim_id"]],
                "evidence_panel_ids": list(expected),
            },
        ],
    }
    if mode == "missing_observation" and output["observations"]:
        output["observations"].pop()
    elif mode == "missing_chunk_link" and output["continuity_ledger"]["chunks"]:
        output["continuity_ledger"]["chunks"][0]["panel_ids"] = []
    elif mode == "claim_without_evidence":
        output["evidence_graph"]["claims"][0]["evidence_panel_ids"] = []
    elif mode == "malformed":
        return {"observations": []}
    return output


class _Provider:
    def __init__(self, mode: str = "valid") -> None:
        self.mode = mode
        self.observe_requests = []
        self.synthesis_requests = []

    def capability(self):
        from app.services.vision_adapter import VisionCapabilityReport

        return VisionCapabilityReport(
            provider_type="openai_compatible",
            provider_name="story-fixture",
            model="fixture-model",
            image_input=True,
            structured_json=True,
            available=True,
            blocking_reason=None,
        )

    def observe(self, request):
        self.observe_requests.append(request)
        return [_semantic_observation(panel) for panel in request.panels]

    def synthesize(self, request):
        self.synthesis_requests.append(request)
        if self.mode == "prompt_mismatch":
            from app.services.analyzer_contract import AnalyzerContractError

            raise AnalyzerContractError("instruction contract mismatch")
        if self.mode == "response_invalid":
            from app.services.vision_adapter import VisionResponseInvalid

            raise VisionResponseInvalid()
        if self.mode == "provider_error":
            from app.services.vision_adapter import VisionProviderRequestFailed

            raise VisionProviderRequestFailed()
        return _output_for_request(request, self.mode)


def _install_provider(monkeypatch, provider):
    module = _pipeline_module()
    report = provider.capability()
    monkeypatch.setattr(
        module.resolver_svc,
        "resolve_vision",
        lambda db, workspace_id: (provider, report),
        raising=True,
    )
    monkeypatch.setattr(
        module.resolver_svc,
        "resolve_analyzer",
        lambda *args, **kwargs: pytest.fail("RulesAnalyzer/text fallback was called"),
        raising=True,
    )
    return module


def _install_missing_provider(monkeypatch):
    module = _pipeline_module()
    from app.services.vision_adapter import VisionCapabilityReport

    report = VisionCapabilityReport(
        provider_type="openai_compatible",
        provider_name="none",
        model=None,
        image_input=False,
        structured_json=False,
        available=False,
        blocking_reason="vision_capability_missing",
    )
    monkeypatch.setattr(
        module.resolver_svc,
        "resolve_vision",
        lambda db, workspace_id: (None, report),
        raising=True,
    )
    monkeypatch.setattr(
        module.resolver_svc,
        "resolve_analyzer",
        lambda *args, **kwargs: pytest.fail("RulesAnalyzer/text fallback was called"),
        raising=True,
    )
    return module


def _run_analysis_or_report_red(module, db, project_id):
    try:
        return module.run_analysis(db, project_id)
    except Exception as exc:
        pytest.fail(
            "pipeline_red_current_text_path: "
            f"{type(exc).__name__}: {exc}"
        )


def _assert_blocked(module, db, project_id, expected_code):
    row = _run_analysis_or_report_red(module, db, project_id)
    assert row.state == "BLOCKED"
    reasons = row.blocking_reasons_json
    assert set(reasons) == {"codes", "findings"}
    assert expected_code in reasons["codes"]
    assert reasons["codes"] == list(dict.fromkeys(reasons["codes"]))
    assert all(isinstance(finding, dict) for finding in reasons["findings"])
    return row


def test_missing_provider_blocks_without_legacy_text_fallback(db, monkeypatch):
    from sqlalchemy import select

    from app.models import ScriptVersion

    project_id = _seed_project(db)
    module = _install_missing_provider(monkeypatch)
    row = _assert_blocked(module, db, project_id, "vision_capability_missing")

    assert row.state != "RECONCILED"
    assert db.scalars(select(ScriptVersion).where(ScriptVersion.project_id == project_id)).all() == []


def test_missing_provider_resolves_before_panel_encoding(db, monkeypatch):
    project_id = _seed_project(db)
    module = _install_missing_provider(monkeypatch)

    def fail_if_encoded(*args, **kwargs):
        pytest.fail("panel transport must not be built before capability resolution")

    monkeypatch.setattr(module, "_encode_panel_payload", fail_if_encoded, raising=True)
    row = module.run_analysis(db, project_id)

    assert row.state == "BLOCKED"
    assert row.blocking_reasons_json["codes"] == ["vision_capability_missing"]


def test_unresolved_material_blocks_before_provider_calls(db, monkeypatch):
    project_id = _seed_project(db, unresolved=True)
    provider = _Provider()
    module = _install_provider(monkeypatch, provider)

    _assert_blocked(module, db, project_id, "coverage_incomplete")
    assert provider.observe_requests == []
    assert provider.synthesis_requests == []


@pytest.mark.parametrize(
    ("mode", "expected_code"),
    (
        ("missing_observation", "analysis_observation_missing"),
        ("missing_chunk_link", "analysis_chunk_link_missing"),
        ("claim_without_evidence", "analysis_claim_evidence_missing"),
        ("malformed", "analysis_incomplete"),
        ("prompt_mismatch", "analyzer_contract_invalid"),
    ),
)
def test_synthesis_reconciliation_failures_are_distinct_and_fail_closed(
    db, monkeypatch, mode, expected_code
):
    from sqlalchemy import select

    from app.models import ScriptVersion

    project_id = _seed_project(db)
    provider = _Provider(mode)
    module = _install_provider(monkeypatch, provider)
    row = _assert_blocked(module, db, project_id, expected_code)

    assert row.state == "BLOCKED"
    assert db.scalars(select(ScriptVersion).where(ScriptVersion.project_id == project_id)).all() == []


def test_provider_request_failure_is_safe_and_never_reconciled(db, monkeypatch):
    project_id = _seed_project(db)
    provider = _Provider("provider_error")
    module = _install_provider(monkeypatch, provider)

    row = _assert_blocked(module, db, project_id, "vision_provider_request_failed")

    assert row.state == "BLOCKED"
    assert row.evidence_graph_json is None or "script_passages" not in row.evidence_graph_json


def test_invalid_vision_response_blocks_without_script_or_secret_payload(db, monkeypatch):
    project_id = _seed_project(db)
    provider = _Provider("response_invalid")
    module = _install_provider(monkeypatch, provider)

    row = _assert_blocked(module, db, project_id, "vision_response_invalid")

    assert row.evidence_graph_json is None or not row.evidence_graph_json.get(
        "script_passages"
    )
    findings_text = repr(row.blocking_reasons_json["findings"]).lower()
    assert "data:image" not in findings_text
    assert "payload" not in findings_text
    assert "secret" not in findings_text


def test_missing_storage_blocks_without_partial_reconciled_row(db, monkeypatch):
    from sqlalchemy import select

    from app.models import StoryAnalysis

    project_id = _seed_project(db, storage_failure=True)
    provider = _Provider()
    module = _install_provider(monkeypatch, provider)
    row = _assert_blocked(module, db, project_id, "coverage_incomplete")

    assert row.state == "BLOCKED"
    assert db.scalars(
        select(StoryAnalysis).where(
            StoryAnalysis.project_id == project_id,
            StoryAnalysis.state == "RECONCILED",
        )
    ).all() == []
    assert provider.observe_requests == []


def test_text_only_asset_cannot_enter_run_analysis_or_call_legacy_resolver(db, monkeypatch):
    project_id = _seed_project(db, text_only=True)
    module = _pipeline_module()
    monkeypatch.setattr(
        module.resolver_svc,
        "resolve_analyzer",
        lambda *args, **kwargs: pytest.fail("legacy resolver was called"),
        raising=True,
    )
    monkeypatch.setattr(
        module.resolver_svc,
        "resolve_vision",
        lambda *args, **kwargs: (None, None),
        raising=True,
    )

    row = _run_analysis_or_report_red(module, db, project_id)

    assert row.state == "BLOCKED"
    assert "vision_capability_missing" in row.blocking_reasons_json["codes"]
