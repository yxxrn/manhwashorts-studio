from __future__ import annotations

from types import SimpleNamespace

from tests.cloud.mass_support import _module, _panels, _visual_row


def test_repair_for_production_repairs_without_silent_preview(monkeypatch):
    module = _module()
    panels = _panels(module)
    visual = module.VisualStageResult(
        panels=tuple(_visual_row(panel.descriptor()) for panel in panels),
        source_hash="v" * 64,
        model_identity_hash="m" * 64,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="p" * 64,
    )
    story = module.StoryMapResult(
        panel_ids=tuple(panel.panel_id for panel in panels),
        beats=({"beat_id": "beat-1", "panel_ids": [panel.panel_id for panel in panels]},),
        causal_chain=(),
        claims=({"claim_id": "claim-1", "panel_ids": [panel.panel_id for panel in panels]},),
        story_map_hash="s" * 64,
        model_identity_hash="m" * 64,
        prompt_version="cloud-causal-map-v2",
        prompt_sha256="c" * 64,
    )
    narration = module.NarrationResult(
        spoken_text="Grounded production repair candidate.",
        display_words=("GROUNDED", "REPAIR"),
        passages=(), ending_kind="consequence", word_count=120,
        estimated_duration_s=52.17,
        qc_report={}, model_identity_hash="m" * 64,
        prompt_version="repair-v1", prompt_sha256="r" * 64,
        visual_evidence_hash=visual.visual_evidence_hash,
    )
    failed = module.ChapterJobRecord(
        job_id="project-a", state=module.ChapterState.NEEDS_REVIEW,
        error_code="visual.narrative_repair_ungrounded",
        stage_results={"visual": visual.as_dict(), "story_map": story.as_dict(), "narration": narration.as_dict()},
    )

    class Store:
        def load(self, _job_id):
            return None

        def save(self, _record):
            return None

    service = module.CloudBatchService.__new__(module.CloudBatchService)
    service.runner = SimpleNamespace(
        model_identity=SimpleNamespace(identity_hash="m" * 64),
        assess_strip_boundaries=lambda _request: {},
        prompts={"visual_narrative_repair": ("repair-v1", "r" * 64, "")},
        _last_narration_result=None,
    )
    service.store = Store()
    service.review_root = None
    monkeypatch.setattr(
        module, "prepare_project_panels",
        lambda *_args, **_kwargs: (panels, {"status": "RECONCILED"}),
    )
    monkeypatch.setattr(
        module, "_build_project_prepared_manifest",
        lambda *_args, **_kwargs: {"manifest": "generated"},
    )
    monkeypatch.setattr(service, "run_job", lambda *_args, **_kwargs: failed)
    repaired = SimpleNamespace(narration=narration, visual=visual, story_map=story)
    ledger = SimpleNamespace(as_dict=lambda: {"entries": []})
    monkeypatch.setattr(
        service, "_repair_review_narrative",
        lambda *_args, **_kwargs: (repaired, ledger, ("hook",)),
    )
    script_row = SimpleNamespace(id="script-a", version=34, estimated_duration=52.0, sections=[])
    monkeypatch.setattr(
        module, "persist_cloud_chapter",
        lambda *_args, **_kwargs: (SimpleNamespace(id="analysis-a"), script_row),
    )
    pipeline = __import__("app.services.pipeline", fromlist=["pipeline"])
    monkeypatch.setattr(
        pipeline, "build_timeline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("silent timeline called")),
    )
    monkeypatch.setattr(
        pipeline, "render_silent_review_preview",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("silent preview called")),
    )

    result = service.run_project(object(), "project-a", repair_for_production=True)

    assert result.state == module.ChapterState.READY_TO_RENDER
    assert result.stage_results["voice_state"] == "WAITING_FOR_PRODUCTION"
    assert result.stage_results["publish_allowed"] is False
    assert "review_preview" not in result.stage_results
    assert result.stage_results["visual_repair"]["contract_version"] == (
        module.visual_narrative_repair.REPAIR_CONTRACT_VERSION
    )
