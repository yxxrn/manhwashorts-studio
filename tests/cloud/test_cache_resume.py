"""Cloud multimodal regression tests grouped by responsibility."""

from __future__ import annotations

from tests.cloud.mass_support import (
    SimpleNamespace,
    _FakeProvider,
    _identity,
    _module,
    _panels,
    _visual_row,
    importlib,
    json,
    pytest,
    re,
    replace,
)


def test_durable_visual_repair_result_is_reused_when_feasible_sections_are_covered():
    module = _module()
    repair = importlib.import_module("app.services.visual_narrative_repair")
    entry = repair.FeasibleVisualRecord(
        panel_region_id="region-safe",
        panel_id="panel-safe",
        source_asset_id="asset-safe",
        source_order=10,
        eligible_sections=(),
        eligible_beats=("beat-safe",),
        resolution_state="UPSCALED",
        feasible_rois=(
            {
                "kind": "primary",
                "roi_label": "primary",
                "crop_box": [0, 0, 1080, 1920],
                "telemetry": {},
            },
        ),
        visual_strengths={"edge_connected_blank_fraction": 0.0},
        evidence_hash="e" * 64,
        detector_version="detector-v1",
        mask_sha256="m" * 64,
        panel_size=(1080, 1920),
    )
    ledger = repair.FeasibleVisualLedger(
        entries=(entry,), model_identity_hash="model-hash"
    )
    narration = SimpleNamespace(
        evidence_graph={
            "claims": [
                {"claim_id": "claim-safe", "evidence_panel_ids": ["panel-safe"]}
            ]
        },
        passages=(
            {
                "passage_id": "p1",
                "claim_ids": ["claim-safe"],
                "evidence_panel_ids": ["panel-safe"],
            },
            {
                "passage_id": "p2",
                "claim_ids": ["claim-safe"],
                "evidence_panel_ids": ["panel-safe"],
            },
        ),
    )

    assert module._durable_visual_repair_covers_missing_sections(
        narration,
        ledger=ledger,
        section_to_beats={"hook": ("beat-safe",), "setup": ("beat-safe",)},
        missing_sections=("hook",),
    ) is True

def test_durable_visual_repair_rejects_stale_prompt_generation():
    module = _module()
    narration = SimpleNamespace(
        prompt_version="visual-narrative-repair-v8", prompt_sha256="old"
    )
    assert module._durable_visual_repair_covers_missing_sections(
        narration,
        ledger=SimpleNamespace(),
        section_to_beats={},
        missing_sections=(),
        expected_prompt_version="visual-narrative-repair-v9",
        expected_prompt_sha256="new",
    ) is False

def test_durable_visual_repair_rejects_stale_capacity_bundle():
    module = _module()
    repair = importlib.import_module("app.services.visual_narrative_repair")
    entry = repair.FeasibleVisualRecord(
        panel_region_id="region-safe",
        panel_id="panel-safe",
        source_asset_id="asset-safe",
        source_order=1,
        eligible_sections=(),
        eligible_beats=("beat-safe",),
        resolution_state="OK",
        feasible_rois=({
            "kind": "primary",
            "roi_label": "primary",
            "crop_box": [0, 0, 100, 100],
            "focus": [0.5, 0.5, 0.5, 0.5],
        },),
        visual_strengths={"edge_connected_blank_fraction": 0.0},
        evidence_hash="e" * 64,
        detector_version="detector-v1",
        mask_sha256="m" * 64,
        panel_size=(1080, 1920),
    )

    ledger = repair.FeasibleVisualLedger(
        entries=(entry,), model_identity_hash="model-hash"
    )
    narration = SimpleNamespace(
        prompt_version="visual-narrative-repair-v9",
        prompt_sha256="new",
        evidence_graph={
            "claims": [
                {"claim_id": "claim-safe", "evidence_panel_ids": ["panel-safe"]}
            ]
        },
        passages=(
            {
                "passage_id": "p1",
                "text": "A grounded consequence changes the situation immediately.",
                "claim_ids": ["claim-safe"],
                "evidence_panel_ids": ["panel-safe"],
            },
        ),
        estimated_duration_s=None,
    )
    plan = {
        "feasible": True,
        "rows": [{
            "passage_index": 0,
            "section": "hook",
            "required_visual_slots": 1,
            "available_visual_slots": 1,

            "claim_ids": ["claim-safe"],
            "evidence_panel_ids": ["panel-other"],
            "max_lexical_words": 20,
            "hook_priority_score": 1,
        }],
    }
    assert module._durable_visual_repair_covers_missing_sections(
        narration,
        ledger=ledger,
        section_to_beats={"hook": ("beat-safe",)},
        missing_sections=(),
        capacity_safe_claim_plan=plan,
        expected_prompt_version="visual-narrative-repair-v9",
        expected_prompt_sha256="new",
    ) is False

def test_infeasible_visual_repair_capacity_fails_before_provider_even_with_invalid_cache(monkeypatch):
    module = _module()
    repair = importlib.import_module("app.services.visual_narrative_repair")
    from types import SimpleNamespace

    entry = repair.FeasibleVisualRecord(
        panel_region_id="region-safe",
        panel_id="panel-safe",
        source_asset_id="asset-safe",
        source_order=10,
        eligible_sections=(),
        eligible_beats=("beat-safe",),
        resolution_state="UPSCALED",
        feasible_rois=(
            {
                "kind": "primary",
                "roi_label": "primary",
                "crop_box": [0, 0, 1080, 1920],
                "telemetry": {},
            },
        ),
        visual_strengths={"edge_connected_blank_fraction": 0.0},
        evidence_hash="e" * 64,
        detector_version="detector-v1",
        mask_sha256="m" * 64,
        panel_size=(1080, 1920),
    )
    ledger = repair.FeasibleVisualLedger(
        entries=(entry,), model_identity_hash="model-hash"
    )
    visual = SimpleNamespace(
        panels=(
            {
                "panel_id": "panel-safe",
                "source_asset_id": "asset-safe",
                "source_order": 10,
            },
        ),
        source_hash="v" * 64,
        visual_evidence_hash="visual-hash",
    )
    story_map = SimpleNamespace(
        claims=({"claim_id": "claim-safe"},),
        as_dict=lambda: {
            "beats": [{"beat_id": "beat-safe", "panel_ids": ["panel-safe"]}],
            "claims": [{"claim_id": "claim-safe", "panel_ids": ["panel-safe"]}],
        },
        story_map_hash="s" * 64,
    )

    class Cache:
        def get(self, _key):
            return {"cached": True}

        def put(self, _key, _value):
            raise AssertionError("the failed provider path must not cache a result")

    cached = SimpleNamespace(
        visual_evidence_hash=visual.visual_evidence_hash,
        evidence_graph={"claims": []},
        passages=(),
    )
    monkeypatch.setattr(
        module.NarrationResult,
        "from_dict",
        staticmethod(lambda _value: cached),
    )
    calls = {"count": 0}

    def fail_provider(*_args, **_kwargs):
        calls["count"] += 1
        raise module.CloudStageError("cloud.provider_request_failed")

    runner = module.CloudStageRunner(
        provider=SimpleNamespace(model_id=_identity(module).model),
        model_identity=_identity(module),
        cache=Cache(),
    )
    runner._narration_observations = lambda *_args: (
        [{"panel_id": "panel-safe"}],
        {"continuity_ledger": {}, "coverage_manifest": {}},
    )
    runner._call = fail_provider

    with pytest.raises(module.CloudStageError) as caught:
        runner.run_visual_narrative_repair(
            visual,
            story_map,
            None,
            ledger,
            {"hook": ("beat-missing",)},
        )

    assert caught.value.code == "visual.narrative_repair_ungrounded"
    assert caught.value.safe_metadata["failed_predicate"] == "visual.repair_capacity_plan_infeasible"
    assert calls["count"] == 0

def test_visual_repair_contract_bump_scopes_stale_provider_cache():
    repair = importlib.import_module("app.services.visual_narrative_repair")
    ledger = type("Ledger", (), {"ledger_hash": "ledger-hash"})()
    common = {
        "ledger": ledger,
        "model_identity_hash": "model-hash",
        "prompt_sha256": "prompt-hash",
        "narration_hash": "narration-hash",
    }

    old_key = repair.repair_cache_key(
        **common,
        contract_version="visual_narrative_repair_v1",
    )
    current_key = repair.repair_cache_key(
        **common,
        contract_version=repair.REPAIR_CONTRACT_VERSION,
    )

    assert repair.REPAIR_CONTRACT_VERSION == "visual_narrative_repair_v12"
    assert repair.REPAIR_PROMPT_VERSION == "visual-narrative-repair-v12"
    assert old_key != current_key

def test_review_project_falls_back_to_current_materialization_on_stale_metadata_cache(
    monkeypatch,
):
    module = _module()
    from types import SimpleNamespace

    panels = _panels(module, "stale-metadata")
    first_failure = module.ChapterJobRecord(
        job_id="project-a",
        state=module.ChapterState.NEEDS_REVIEW,
        error_code="cloud.prepared_manifest_requires_materialization",
        stage_results={
            "prepared_panel_manifest": {"manifest": "durable"},
            "segmentation": {"status": "RECONCILED"},
        },
    )
    second_failure = module.ChapterJobRecord(
        job_id="project-a",
        state=module.ChapterState.NEEDS_REVIEW,
        error_code="cloud.narrative_not_grounded",
        stage_results={},
    )

    class Store:
        def load(self, _project_id):
            return first_failure

        def save(self, _record):
            return None

    service = module.CloudBatchService.__new__(module.CloudBatchService)
    service.runner = SimpleNamespace(
        model_identity=SimpleNamespace(identity_hash="m" * 64),
        assess_strip_boundaries=lambda _request: {},
    )
    service.store = Store()
    service.review_root = None
    prepared = {}
    monkeypatch.setattr(
        module,
        "_restore_project_prepared_manifest",
        lambda _db, _project_id, _manifest: (panels, {"status": "RECONCILED"}),
    )
    monkeypatch.setattr(
        module,
        "_build_project_prepared_manifest",
        lambda *_args, **_kwargs: {"manifest": "rebuilt"},
    )

    def fake_prepare(_db, _project_id, **kwargs):
        prepared.update(kwargs)
        return panels, {"status": "RECONCILED"}

    monkeypatch.setattr(module, "prepare_project_panels", fake_prepare)
    calls = []

    def fake_run_job(_job_id, passed_panels):
        calls.append(tuple(passed_panels))
        return first_failure if len(calls) == 1 else second_failure

    monkeypatch.setattr(service, "run_job", fake_run_job)

    result = service.run_project(object(), "project-a", review_only_preview=True)

    assert result is second_failure
    assert calls == [panels, panels]
    assert prepared["review_only_auto_override"] is True
    assert prepared["cached_segmentation"] == {"status": "RECONCILED"}

def test_legacy_visual_repair_checkpoint_is_provisional_resume_compatible():
    module = _module()
    base = _identity(module)
    current_identity = replace(
        base,
        prompt_versions=dict(base.prompt_versions)
        | {"visual_narrative_repair": module.CURRENT_VISUAL_REPAIR_PROMPT_VERSION},
    )
    legacy_identity = replace(
        current_identity,
        prompt_versions=dict(current_identity.prompt_versions)
        | {"visual_narrative_repair": module.LEGACY_VISUAL_REPAIR_PROMPT_VERSION},
    )
    runner = SimpleNamespace(
        prompts={
            "narration": ("vision-first-story-analyzer-v3", "n" * 64, ""),
            "visual_narrative_repair": (
                module.CURRENT_VISUAL_REPAIR_PROMPT_VERSION, "c" * 64, ""
            ),
        },
        model_identity=current_identity,
    )
    narration = SimpleNamespace(
        prompt_version=module.LEGACY_VISUAL_REPAIR_PROMPT_VERSION,
        prompt_sha256="l" * 64,
    )
    record = module.ChapterJobRecord(
        "project-legacy-repair",
        stage_results={
            "visual_repair": {
                "contract_version": module.LEGACY_VISUAL_REPAIR_CONTRACT_VERSION,
                "prompt_version": module.LEGACY_VISUAL_REPAIR_PROMPT_VERSION,
                "prompt_sha256": "l" * 64,
                "model_identity_hash": legacy_identity.identity_hash,
                "publish_allowed": False,
            }
        },
    )

    assert module._narration_is_legacy_visual_repair_checkpoint(
        record, narration, runner
    )
    assert module._narration_stage_prompt_is_compatible(record, narration, runner)
    assert not module._narration_is_current_visual_repair_checkpoint(
        record, narration, runner
    )

def test_repaired_narration_checkpoint_requires_exact_current_repair_metadata():
    module = _module()
    repair_prompt = ("visual-narrative-repair-v4", "r" * 64, "")
    runner = SimpleNamespace(
        prompts={
            "narration": ("vision-first-story-analyzer-v3", "n" * 64, ""),
            "visual_narrative_repair": repair_prompt,
        },
        model_identity=SimpleNamespace(identity_hash="m" * 64),
    )
    narration = SimpleNamespace(
        prompt_version=repair_prompt[0],
        prompt_sha256=repair_prompt[1],
    )
    record = module.ChapterJobRecord(
        "project-a",
        stage_results={
            "visual_repair": {
                "contract_version": module.visual_narrative_repair.REPAIR_CONTRACT_VERSION,
                "prompt_version": repair_prompt[0],
                "prompt_sha256": repair_prompt[1],
                "model_identity_hash": "m" * 64,
                "publish_allowed": False,
            }
        },
    )

    assert module._narration_stage_prompt_is_compatible(record, narration, runner)

    record.stage_results["visual_repair"]["prompt_sha256"] = "stale"
    assert not module._narration_stage_prompt_is_compatible(record, narration, runner)

def test_ready_review_resume_repairs_stale_narration_before_first_persistence(monkeypatch):
    module = _module()
    from types import SimpleNamespace

    panels = _panels(module)
    visual = module.VisualStageResult(
        panels=tuple(_visual_row(panel.descriptor()) for panel in panels),
        source_hash="v" * 64,
        model_identity_hash="m" * 64,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="p" * 64,
    )
    story_map = module.StoryMapResult(
        panel_ids=tuple(panel.panel_id for panel in panels),
        beats=({"beat_id": "beat-1", "panel_ids": [panel.panel_id for panel in panels]},),
        causal_chain=(),
        claims=(),
        story_map_hash="s" * 64,
        model_identity_hash="m" * 64,
        prompt_version="cloud-causal-map-v2",
        prompt_sha256="c" * 64,
        visual_evidence_hash=visual.visual_evidence_hash,
    )
    original = module.NarrationResult(
        spoken_text="Original stale narration.",
        display_words=("ORIGINAL", "STALE", "NARRATION"),
        passages=(),
        ending_kind="consequence",
        word_count=120,
        estimated_duration_s=52.0,
        qc_report={},
        model_identity_hash="m" * 64,
        prompt_version="visual-narrative-repair-v8",
        prompt_sha256="n" * 64,
        visual_evidence_hash=visual.visual_evidence_hash,
    )
    repaired = replace(
        original,
        spoken_text="Repaired current narration.",
        qc_report={
            "visual_repair_text_only_duration_repair_v1": {
                "duration_policy_contract": {
                    "target_duration_min_s": 24.35,
                    "target_duration_max_s": 31.3,
                }
            }
        },
    )
    ready = module.ChapterJobRecord(
        job_id="project-a",
        state=module.ChapterState.READY_TO_RENDER,
        stage_results={
            "visual": visual.as_dict(),
            "story_map": story_map.as_dict(),
            "narration": original.as_dict(),
        },
    )

    class Store:
        def __init__(self):
            self.payload = ready.as_dict()

        def load(self, _job_id):
            return module.ChapterJobRecord.from_dict(self.payload)

        def save(self, record):
            self.payload = record.as_dict()

    service = module.CloudBatchService.__new__(module.CloudBatchService)
    service.runner = SimpleNamespace(
        model_identity=SimpleNamespace(identity_hash="m" * 64),
        assess_strip_boundaries=lambda _request: {},
        prompts={"visual_narrative_repair": ("visual-narrative-repair-v11", "r" * 64, "")},
    )
    service.store = Store()
    service.review_root = None
    monkeypatch.setattr(
        module,
        "prepare_project_panels",
        lambda *_args, **_kwargs: (panels, {"status": "RECONCILED"}),
    )
    monkeypatch.setattr(
        module,
        "_build_project_prepared_manifest",
        lambda *_args, **_kwargs: {"manifest": "generated"},
    )
    monkeypatch.setattr(
        service,
        "run_job",
        lambda *_args, **_kwargs: pytest.fail(
            "review resume with durable visual/story/narration must bypass normal narration stage"
        ),
    )
    old_script = SimpleNamespace(id="script-old", version=1, estimated_duration=52.0, sections=[])
    repair_calls = []
    ledger = SimpleNamespace(
        entries=(),
        as_dict=lambda: {"entries": [], "ledger_hash": "l" * 64},
    )

    def fake_repair(_db, _project_id, script_row, _panels, result, **_kwargs):
        repair_calls.append((script_row, result.narration.spoken_text))
        return (
            module.ChapterResult(
                state=module.ChapterState.READY_TO_RENDER,
                visual=visual,
                story_map=story_map,
                narration=repaired,
            ),
            ledger,
            ("hook",),
        )

    monkeypatch.setattr(service, "_repair_review_narrative", fake_repair)
    persisted = []

    def fake_persist(_db, _project_id, _panels, result, **_kwargs):
        persisted.append(result.narration.spoken_text)
        return (
            SimpleNamespace(id="analysis-a"),
            SimpleNamespace(id="script-a", version=2, estimated_duration=24.0, sections=[]),
        )

    monkeypatch.setattr(module, "persist_cloud_chapter", fake_persist)
    pipeline = importlib.import_module("app.services.pipeline")
    monkeypatch.setattr(pipeline, "latest_script_row", lambda *_args, **_kwargs: old_script)
    timeline_kwargs = {}

    def fake_build_timeline(*_args, **kwargs):
        timeline_kwargs.update(kwargs)
        return None

    monkeypatch.setattr(pipeline, "build_timeline", fake_build_timeline)
    monkeypatch.setattr(
        pipeline,
        "render_silent_review_preview",
        lambda *_args, **_kwargs: (None, SimpleNamespace(as_dict=lambda: {"review": True})),
    )

    result = service.run_project(object(), "project-a", review_only_preview=True)

    assert repair_calls == [(None, "Original stale narration.")]
    assert persisted == ["Repaired current narration."]
    assert result.state == module.ChapterState.REVIEW_PREVIEW_READY
    assert result.stage_results["narration"]["spoken_text"] == "Repaired current narration."
    assert timeline_kwargs["provisional_duration_bounds_s"] == (24.35, 31.3)

def test_review_render_failure_preserves_durable_visual_repair_checkpoint(monkeypatch):
    module = _module()
    from types import SimpleNamespace

    panels = _panels(module)
    visual = module.VisualStageResult(
        panels=tuple(_visual_row(panel.descriptor()) for panel in panels),
        source_hash="v" * 64,
        model_identity_hash="m" * 64,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="p" * 64,
    )
    story_map = module.StoryMapResult(
        panel_ids=tuple(panel.panel_id for panel in panels),
        beats=({"beat_id": "beat-1", "panel_ids": [panel.panel_id for panel in panels]},),
        causal_chain=(),
        claims=(),
        story_map_hash="s" * 64,
        model_identity_hash="m" * 64,
        prompt_version="cloud-causal-map-v2",
        prompt_sha256="c" * 64,
    )
    original_narration = module.NarrationResult(
        spoken_text="Original narration.",
        display_words=("ORIGINAL", "NARRATION"),
        passages=(),
        ending_kind="consequence",
        word_count=120,
        estimated_duration_s=52.0,
        qc_report={},
        model_identity_hash="m" * 64,
        prompt_version="vision-first-story-analyzer-v3",
        prompt_sha256="n" * 64,
        visual_evidence_hash=visual.visual_evidence_hash,
    )
    repaired_narration = replace(original_narration, spoken_text="Repaired narration.")
    failed = module.ChapterJobRecord(
        job_id="project-a",
        state=module.ChapterState.NEEDS_REVIEW,
        error_code="visual.narrative_repair_ungrounded",
        stage_results={
            "visual": visual.as_dict(),
            "story_map": story_map.as_dict(),
            "narration": original_narration.as_dict(),
        },
    )

    class Store:
        def __init__(self):
            self.payload = failed.as_dict()

        def load(self, _job_id):
            return module.ChapterJobRecord.from_dict(self.payload)

        def save(self, record):
            self.payload = record.as_dict()

    service = module.CloudBatchService.__new__(module.CloudBatchService)
    service.runner = SimpleNamespace(
        model_identity=SimpleNamespace(identity_hash="m" * 64),
        assess_strip_boundaries=lambda _request: {},
        prompts={"visual_narrative_repair": ("visual-narrative-repair-v4", "r" * 64, "")},
        _last_narration_result=None,
    )
    service.store = Store()
    service.review_root = None
    monkeypatch.setattr(
        module,
        "prepare_project_panels",
        lambda *_args, **_kwargs: (panels, {"status": "RECONCILED"}),
    )
    monkeypatch.setattr(
        module,
        "_build_project_prepared_manifest",
        lambda *_args, **_kwargs: {"manifest": "generated"},
    )
    monkeypatch.setattr(service, "run_job", lambda *_args, **_kwargs: failed)
    ledger = SimpleNamespace(entries=(), as_dict=lambda: {"entries": [], "ledger_hash": "l" * 64})
    monkeypatch.setattr(
        service,
        "_repair_review_narrative",
        lambda *_args, **_kwargs: (
            module.ChapterResult(
                state=module.ChapterState.READY_TO_RENDER,
                visual=visual,
                story_map=story_map,
                narration=repaired_narration,
            ),
            ledger,
            ("cta",),
        ),
    )
    monkeypatch.setattr(
        module,
        "persist_cloud_chapter",
        lambda *_args, **_kwargs: (
            SimpleNamespace(id="analysis-a"),
            SimpleNamespace(id="script-a", version=2, estimated_duration=50.0, sections=[]),
        ),
    )
    pipeline = importlib.import_module("app.services.pipeline")
    monkeypatch.setattr(
        pipeline,
        "build_timeline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("planner boom")),
    )

    result = service.run_project(object(), "project-a", review_only_preview=True)

    assert result.state == module.ChapterState.NEEDS_REVIEW
    assert result.error_code == "review.preview_failed"
    assert result.stage_results["narration"]["spoken_text"] == "Repaired narration."
    assert result.stage_results["feasible_visual_ledger"]["ledger_hash"] == "l" * 64
    assert result.stage_results["visual_repair"]["missing_sections"] == ["cta"]

def test_unattempted_conservative_visual_cache_is_not_reused():
    module = _module()
    panel = _panels(module)[0]
    row = {
        "panel_id": panel.panel_id,
        "source_asset_id": panel.source_asset_id,
        "source_order": panel.source_order,
        "source_checksum": panel.source_checksum,
        "observation": {"visible_facts": ["a grounded fact"]},
        "fallback_mode": "conservative_full_panel_v1",
    }

    assert module._visual_cached_row_is_reusable(row, panel) is False
    row["targeted_geometry_repair_attempted"] = True
    assert module._visual_cached_row_is_reusable(row, panel) is True

def test_transient_segmentation_failure_with_durable_progress_waits_for_provider():
    module = _module()

    class Store:
        def save(self, record):
            self.record = record

    service = module.CloudBatchService.__new__(module.CloudBatchService)
    service.runner = SimpleNamespace(
        request_count=1,
        request_counts={"other": 1},
        estimated_cost_usd=0.0,
    )
    service.store = Store()
    record = module.ChapterJobRecord(job_id="waiting-project")

    result = service._record_failure(
        record,
        module.CloudStageError(
            "segmentation.provider_request_failed",
            reviewable=False,
            safe_metadata={
                "status_code": 503,
                "retryable": True,
                "durable_progress": True,
            },
        ),
    )

    assert result.state == module.ChapterState.WAITING_PROVIDER
    assert result.review_queue[0]["safe_metadata"]["resume"]

def test_cache_key_is_idempotent_and_changes_with_source_or_model():
    module = _module()
    provider = _FakeProvider()
    cache = module.MemoryStageCache()
    runner = module.CloudStageRunner(provider=provider, model_identity=_identity(module), cache=cache)
    panels = _panels(module)

    first = runner.run_chapter(panels)
    call_count = len(provider.calls)
    second = runner.run_chapter(panels)
    assert second == first
    assert len(provider.calls) == call_count

    changed = list(panels)
    changed[0] = replace(changed[0], payload=b"different-content", payload_checksum="")
    runner.run_chapter(tuple(changed))
    assert len(provider.calls) > call_count

def test_batch_isolates_failure_and_resumes_from_durable_stage(tmp_path):
    module = _module()
    store = module.JsonJobStore(tmp_path)
    provider = _FakeProvider(fail_for_prefix="bad")
    runner = module.CloudStageRunner(provider=provider, model_identity=_identity(module))
    batch = module.CloudBatchService(runner=runner, store=store)

    records = batch.run_batch({"good": _panels(module, "good"), "bad": _panels(module, "bad")})

    assert records["good"].state == module.ChapterState.READY_TO_RENDER
    assert records["bad"].state == module.ChapterState.FAILED
    assert records["bad"].error_code == "cloud.provider_request_failed"
    assert (tmp_path / "good.json").exists()
    assert (tmp_path / "bad.json").exists()

    provider.fail_for_prefix = ""
    resumed = batch.run_job("bad", _panels(module, "bad"))
    assert resumed.state == module.ChapterState.READY_TO_RENDER
    assert resumed.stage_results["visual"]["reconciled"] is True

def test_batch_operator_entrypoint_exposes_resume_safe_project_options():
    try:
        cli = importlib.import_module("scripts.run_cloud_multimodal_batch")
    except Exception as exc:
        pytest.fail(f"cloud batch CLI import failed in test body: {exc}")

    parser = cli.build_parser()
    options = parser.parse_args(
        [
            "--project-id",
            "project-a",
            "--project-id",
            "project-b",
            "--state-dir",
            "ignored/cloud-jobs",
            "--model",
            "pinned-model",
        ]
    )
    assert options.project_id == ["project-a", "project-b"]
    assert options.state_dir == "ignored/cloud-jobs"
    assert options.model == "pinned-model"
    assert callable(cli.main)

def test_visual_checkpoint_is_scoped_and_durable(tmp_path):
    module = _module()
    checkpoint = tmp_path / "visual-checkpoints.jsonl"
    first_provider = _FakeProvider()
    first = module.CloudStageRunner(
        provider=first_provider,
        model_identity=_identity(module),
        visual_checkpoint_path=checkpoint,
    )
    first_result = first.run_visual_evidence(_panels(module, "checkpoint"))
    assert first_result.reconciled is True
    assert len([call for call in first_provider.calls if call[0] == "visual"]) == 1

    resumed_provider = _FakeProvider()
    resumed = module.CloudStageRunner(
        provider=resumed_provider,
        model_identity=_identity(module),
        visual_checkpoint_path=checkpoint,
    )
    resumed_result = resumed.run_visual_evidence(_panels(module, "checkpoint"))
    assert resumed_result == first_result
    assert not [call for call in resumed_provider.calls if call[0] == "visual"]

    unrelated_provider = _FakeProvider()
    unrelated = module.CloudStageRunner(
        provider=unrelated_provider,
        model_identity=_identity(module),
        visual_checkpoint_path=checkpoint,
    )
    unrelated.run_visual_evidence(_panels(module, "unrelated"))
    assert len([call for call in unrelated_provider.calls if call[0] == "visual"]) == 1

def test_cached_visual_reanalyzes_only_rows_without_visible_facts():
    module = _module()
    panels = _panels(module, "cached-facts")
    identity = _identity(module)
    seed_provider = _FakeProvider()
    seed_runner = module.CloudStageRunner(
        provider=seed_provider,
        model_identity=identity,
        cache=module.MemoryStageCache(),
    )
    valid = seed_runner.run_visual_evidence(panels)
    invalid_rows = [dict(row) for row in valid.panels]
    invalid_observation = dict(invalid_rows[1]["observation"])
    invalid_observation["visible_facts"] = []
    invalid_rows[1]["observation"] = invalid_observation
    invalid = replace(valid, panels=tuple(invalid_rows))
    cache = module.MemoryStageCache()
    cache.put(
        module._cache_key(
            "visual",
            list(module._visual_panel_identities(panels)),
            identity,
            seed_runner.prompts["visual"],
        ),
        invalid.as_dict(),
    )

    class _RepairProvider(_FakeProvider):
        def __post_init__(self):
            super().__post_init__()
            self.request_panel_ids = []

        def observe(self, request):
            self.request_panel_ids.append(
                tuple(panel["panel_id"] for panel in request.panels)
            )
            return super().observe(request)

    provider = _RepairProvider()
    resumed = module.CloudStageRunner(
        provider=provider,
        model_identity=identity,
        cache=cache,
        max_attempts=1,
    )
    result = resumed.run_visual_evidence(panels)

    assert provider.request_panel_ids == [(panels[1].panel_id,)]
    assert result.panels[1]["observation"]["visible_facts"] == [
        f"visible fact {panels[1].source_order}"
    ]

def test_resume_discovers_exact_cached_visual_subset_without_provider_call():
    module = _module()
    panels = _panels(module, "subset-cache")
    identity = _identity(module)
    seed_runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=identity,
        cache=module.MemoryStageCache(),
    )
    valid = seed_runner.run_visual_evidence(panels)
    subset = replace(
        valid,
        panels=tuple(valid.panels[:2]),
        source_hash="subset-source",
    )
    cache = module.MemoryStageCache()
    cache.put("durable-subset", subset.as_dict())
    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=identity,
        cache=cache,
    )

    found = module._find_cached_visual_subset(
        runner,
        panels,
        expected_source_hash="subset-source",
    )

    assert found is not None
    assert found.panel_ids == tuple(panel.panel_id for panel in panels[:2])

def test_resume_discovers_checkpoint_visual_subset_without_scanning_stage_json(tmp_path):
    module = _module()
    panels = _panels(module, "checkpoint-subset")
    identity = _identity(module)
    seed_runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=identity,
        cache=module.MemoryStageCache(),
    )
    valid = seed_runner.run_visual_evidence(panels)
    checkpoint_path = tmp_path / "visual_checkpoints.jsonl"
    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=identity,
        cache=type(
            "NoScanCache",
            (),
            {"iter_records": lambda self: (_ for _ in ()).throw(AssertionError("stage scan"))},
        )(),
        visual_checkpoint_path=checkpoint_path,
    )
    scope = runner._checkpoint_scope([], runner.prompts["visual"])
    rows = [
        {
            **dict(row),
            "checkpoint_scope": scope,
            "checkpoint_version": module.VISUAL_CHECKPOINT_VERSION,
        }
        for row in valid.panels[:2]
    ]
    rows[0]["observation"] = {
        **rows[0]["observation"],
        "visible_facts": [],
    }
    checkpoint_path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )

    found = module._find_cached_visual_subset(
        runner,
        panels,
        expected_source_hash="checkpoint-source",
    )

    assert found is not None
    assert found.panel_ids == tuple(panel.panel_id for panel in panels[:2])
    assert found.source_hash == "checkpoint-source"

def test_materialized_visual_subset_reseeds_post_materialization_cache_key():
    module = _module()
    panels = _panels(module, "materialized-key")
    identity = _identity(module)
    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=identity,
        cache=module.MemoryStageCache(),
    )
    valid = runner.run_visual_evidence(panels)
    metadata_panels = tuple(
        replace(
            panel,
            mime_type="image/jpeg",
            metadata_only=True,
            identity_payload_checksum=f"{index + 1:064x}",
            identity_descriptor_hash=f"{index + 101:064x}",
        )
        for index, panel in enumerate(panels)
    )
    materialized_panels = tuple(
        replace(panel, mime_type="image/png", metadata_only=False)
        for panel in metadata_panels
    )

    module._seed_visual_subset_cache(runner, metadata_panels, valid)
    assert runner.cache.get(
        module._cache_key(
            "visual",
            list(module._visual_panel_identities(metadata_panels)),
            identity,
            runner.prompts["visual"],
        )
    ) is not None
    assert runner.cache.get(
        module._cache_key(
            "visual",
            list(module._visual_panel_identities(materialized_panels)),
            identity,
            runner.prompts["visual"],
        )
    ) is None

    module._seed_visual_subset_cache(runner, materialized_panels, valid)

    cached_materialized = runner.cache.get(
        module._cache_key(
            "visual",
            list(module._visual_panel_identities(materialized_panels)),
            identity,
            runner.prompts["visual"],
        )
    )
    assert cached_materialized is not None
    assert module.VisualStageResult.from_dict(cached_materialized) == valid

def test_incomplete_visual_stage_requires_checkpoint_subset_restore():
    module = _module()
    panels = _panels(module, "partial-visual")
    identity = _identity(module)
    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=identity,
        cache=module.MemoryStageCache(),
    )
    valid = runner.run_visual_evidence(panels)
    partial = replace(valid, panels=tuple(valid.panels[:2]))

    assert module._visual_cache_requires_subset_restore(
        runner,
        partial.as_dict(),
        panels,
    ) is True
    assert module._visual_cache_requires_subset_restore(
        runner,
        valid.as_dict(),
        panels,
    ) is False
    stale_identity = replace(
        valid,
        source_hash="stale-source",
        panel_identity_hashes=tuple("f" * 64 for _ in panels),
    )
    assert module._visual_cache_requires_subset_restore(
        runner,
        stale_identity.as_dict(),
        panels,
    ) is True

def test_review_resume_rejects_stale_visual_prompt_identity():
    module = _module()
    panels = _panels(module, "review-prompt-stale")
    identity = _identity(module)
    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=identity,
        cache=module.MemoryStageCache(),
    )
    visual = runner.run_visual_evidence(panels)
    story = module.StoryMapResult(
        panel_ids=visual.panel_ids,
        beats=({"beat_id": "beat-1", "panel_ids": list(visual.panel_ids)},),
        causal_chain=(),
        claims=(),
        story_map_hash="s" * 64,
        model_identity_hash=identity.identity_hash,
        prompt_version=runner.prompts["story_map"][0],
        prompt_sha256=runner.prompts["story_map"][1],
        visual_evidence_hash=visual.visual_evidence_hash,
    )

    assert module._review_resume_visual_story_is_current(runner, visual, story) is True

    stale_visual = replace(
        visual,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="f" * 64,
    )
    assert module._review_resume_visual_story_is_current(
        runner,
        stale_visual,
        story,
    ) is False
    assert module._visual_cache_requires_subset_restore(
        runner,
        stale_visual.as_dict(),
        panels,
    ) is True

def test_file_stage_cache_round_trips_durable_values(tmp_path):
    module = _module()
    cache = module.FileStageCache(tmp_path / "stage-cache")
    cache.put("visual-key", {"panels": [{"panel_id": "panel-a"}]})
    assert cache.get("visual-key") == {"panels": [{"panel_id": "panel-a"}]}

def test_resume_filters_poison_panels_before_cached_visual_source_hash():
    module = _module()
    panels = _panels(module, prefix="resume")
    cached_visual = {
        "panels": [
            {"panel_id": panels[0].panel_id},
            {"panel_id": panels[2].panel_id},
        ],
    }

    filtered = module._panels_for_cached_visual_stage(panels, cached_visual)

    assert [panel.panel_id for panel in filtered] == [
        panels[0].panel_id,
        panels[2].panel_id,
    ]

def test_resume_reindexes_execution_order_after_quarantining_panel():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, prefix="resume-order"))
    )
    cached_visual = {
        "panels": [
            {"panel_id": panels[0].panel_id},
            {"panel_id": panels[2].panel_id},
        ],
    }

    filtered = module._panels_for_cached_visual_stage(panels, cached_visual)

    assert [panel.panel_id for panel in filtered] == [
        panels[0].panel_id,
        panels[2].panel_id,
    ]
    assert [panel.source_order for panel in filtered] == [
        panels[0].source_order,
        panels[2].source_order,
    ]
    assert [panel.prepared_order for panel in filtered] == [0, 1]

def test_resume_reindex_preserves_cached_visual_identity_for_admitted_rows():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, prefix="resume-cache-id"))
    )
    rows = [
        {
            "panel_id": panel.panel_id,
            "source_asset_id": panel.source_asset_id,
            "source_order": panel.source_order,
            "source_checksum": panel.source_checksum,
            "cache_identity_hash": module._visual_panel_identity_hash(panel, index),
            "cache_identity_version": module.VISUAL_CACHE_IDENTITY_VERSION,
            "observation": {"visible_facts": ["grounded fact"]},
        }
        for index, panel in enumerate(panels)
    ]
    cached_visual = {"panels": [rows[0], rows[2]]}
    filtered = module._panels_for_cached_visual_stage(panels, cached_visual)

    merged = module._merge_stream_visual_rows(
        ({"rows": [rows[0], rows[2]]},),
        filtered,
    )

    assert [row["panel_id"] for row in merged] == [
        panels[0].panel_id,
        panels[2].panel_id,
    ]

def test_narration_cache_requires_complete_grounded_result_even_with_matching_visual_hash():
    module = _module()
    panels = _panels(module)
    rows = tuple(_visual_row(panel.descriptor()) for panel in panels)
    visual = module.VisualStageResult(
        panels=rows,
        source_hash="cache-validity-source",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )
    spoken_text = " ".join(
        ["One grounded turn changes what follows."] * 20
    )
    display_words = tuple(re.findall(r"[A-Z0-9]+", spoken_text.upper()))
    valid = module.NarrationResult(
        spoken_text=spoken_text,
        display_words=display_words,
        passages=tuple(
            {
                "passage_id": f"p{index}",
                "editorial_role": "role",
                "text": "A grounded turn changes what follows.",
                "claim_ids": ["claim"],
                "evidence_panel_ids": [panel.panel_id],
            }
            for index in range(4)
            for panel in (panels[index % len(panels)],)
        ),
        ending_kind="consequence",
        word_count=120,
        estimated_duration_s=52.17,
        observations=tuple(rows),
        continuity_ledger={},
        evidence_graph={"claims": []},
        story_spine={},
        qc_report={
            "duration_contract": module.script.narration_duration_metrics(
                spoken_text,
                "dramatic",
            ),
        },
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="vision-first-story-analyzer-v3",
        prompt_sha256="n" * 64,
        visual_evidence_hash=visual.visual_evidence_hash,
    )
    stale = replace(valid, word_count=0, spoken_text="", display_words=())

    assert module._narration_result_is_usable(
        valid,
        visual,
        require_duration=True,
    ) is True
    assert module._narration_result_is_usable(
        stale,
        visual,
        require_duration=True,
    ) is False
    missing_contract = replace(valid, qc_report={})
    assert module._narration_result_is_usable(
        missing_contract,
        visual,
        require_duration=True,
    ) is False
    contract_invalid = replace(
        valid,
        word_count=172,
        estimated_duration_s=69.57,
    )
    assert module._narration_result_is_usable(
        contract_invalid,
        visual,
        require_duration=True,
    ) is False
    assert module._narration_result_is_usable(
        contract_invalid,
        visual,
        require_duration=False,
    ) is True

def test_candidate_load_uses_equivalent_identity_migration_record(monkeypatch):
    module = _module()
    identity = _identity(module)
    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=identity,
        cache=module.MemoryStageCache(),
        max_attempts=1,
    )
    prompt = runner.prompts["narration"]
    source = {"source_identity": "current"}
    candidate_payload = {"spoken_text": "candidate"}
    candidate = __import__("types").SimpleNamespace(
        as_dict=lambda: candidate_payload,
        visual_evidence_hash="v" * 64,
    )
    visual = __import__("types").SimpleNamespace(
        visual_evidence_hash="v" * 64,
        panels=(),
    )
    record = {
        "cache_type": module.NARRATION_REPAIR_CANDIDATE_VERSION,
        "candidate": candidate_payload,
        "candidate_hash": module._hash(candidate_payload),
        "source_identity_hash": "old-identity",
        "model_identity_hash": identity.identity_hash,
        "prompt_version": prompt[0],
        "prompt_sha256": prompt[1],
        "failure_codes": ["cloud.narrative_duration_out_of_range"],
    }
    runner.cache.put(runner._narration_repair_candidate_key(source, prompt), record)
    migrated = {
        **record,
        "source_identity_hash": module._hash(source),
        "identity_migration": {"status": "migrated"},
    }
    monkeypatch.setattr(
        module.NarrationResult,
        "from_dict",
        classmethod(lambda _cls, _payload: candidate),
    )
    monkeypatch.setattr(
        runner,
        "_migrate_narration_repair_candidate_record",
        lambda **_kwargs: migrated,
    )
    monkeypatch.setattr(
        runner,
        "_narration_contract_failures",
        lambda _candidate: ("cloud.narrative_duration_out_of_range",),
    )
    monkeypatch.setattr(module, "_narration_result_is_usable", lambda *_args, **_kwargs: True)

    loaded = runner._load_narration_repair_candidate(
        source=source,
        prompt=prompt,
        visual=visual,
    )

    assert loaded == (candidate, ("cloud.narrative_duration_out_of_range",))

def test_visual_cache_identity_ignores_transient_preparation_fields():
    module = _module()
    panels = _panels(module, "identity")
    equivalent = tuple(
        replace(
            panel,
            source_order=panel.source_order + 700,
            source_family="temporary-preparation-family",
            strip_region_id=f"temporary-{panel.panel_id}",
            coverage_map_version="new-review-metadata",
            coverage_map_hash="c" * 64,
            segmentation_version="new-segmentation-metadata",
        )
        for panel in panels
    )
    ordered = module.CloudStageRunner._ordered_panels(panels)
    equivalent_ordered = module.CloudStageRunner._ordered_panels(equivalent)

    assert module._visual_panel_identity_hashes(ordered) == module._visual_panel_identity_hashes(
        equivalent_ordered
    )
    assert module._visual_source_hash(ordered) == module._visual_source_hash(equivalent_ordered)

    changed_crop = list(panels)
    changed_crop[0] = replace(changed_crop[0], panel_bounds=(0, 0, 90, 100))
    assert module._visual_panel_identity_hashes(ordered)[0] != module._visual_panel_identity_hashes(
        module.CloudStageRunner._ordered_panels(tuple(changed_crop))
    )[0]

def test_equivalent_preparation_migrates_legacy_visual_cache_without_provider_call():
    module = _module()
    panels = _panels(module, "migration")
    identity = _identity(module)
    runner = module.CloudStageRunner(provider=_FakeProvider(), model_identity=identity)
    prompt = runner.prompts["visual"]
    legacy = module.VisualStageResult(
        panels=tuple(
            _visual_row(panel.descriptor())
            | {
                "source_asset_id": panel.source_asset_id,
                "source_order": panel.source_order,
                "source_checksum": panel.source_checksum,
            }
            for panel in panels
        ),
        source_hash=module._hash([panel.descriptor() for panel in panels]),
        model_identity_hash=identity.identity_hash,
        prompt_version=prompt[0],
        prompt_sha256=prompt[1],
    ).as_dict()

    migrated = module._migrate_visual_cache_identity(
        legacy,
        panels,
        model_identity=identity,
        prompt=prompt,
    )

    assert migrated is not None
    assert migrated["source_hash"] == module._visual_source_hash(
        module.CloudStageRunner._ordered_panels(panels)
    )
    assert migrated["cache_identity_version"] == module.VISUAL_CACHE_IDENTITY_VERSION
    assert len(migrated["panel_identity_hashes"]) == len(panels)

    lineage = {
        "observations": [
            {
                "panel_id": panel.panel_id,
                "source_asset_id": panel.source_asset_id,
                "source_index": index,
                "region_bounds": {
                    "x": panel.panel_bounds[0],
                    "y": panel.panel_bounds[1],
                    "width": panel.panel_bounds[2] - panel.panel_bounds[0],
                    "height": panel.panel_bounds[3] - panel.panel_bounds[1],
                },
            }
            for index, panel in enumerate(panels)
        ]
    }
    legacy_with_lineage = dict(legacy)
    legacy_with_lineage["source_hash"] = "f" * 64
    migrated_with_lineage = module._migrate_visual_cache_identity(
        legacy_with_lineage,
        panels,
        model_identity=identity,
        prompt=prompt,
        persisted_lineage=lineage,
    )
    assert migrated_with_lineage is not None
    assert (
        migrated_with_lineage["cache_identity_migration_proof"]
        == "persisted_lineage_and_payload_derivation"
    )
    bad_lineage = {
        "observations": [dict(item) for item in lineage["observations"]]
    }
    bad_lineage["observations"][0] = dict(bad_lineage["observations"][0])
    bad_lineage["observations"][0]["region_bounds"] = {
        **bad_lineage["observations"][0]["region_bounds"],
        "width": 99,
    }
    assert (
        module._migrate_visual_cache_identity(
            legacy_with_lineage,
            panels,
            model_identity=identity,
            prompt=prompt,
            persisted_lineage=bad_lineage,
        )
        is None
    )

    changed = list(panels)
    changed[-1] = replace(changed[-1], payload=b"tampered-payload", payload_checksum="")
    assert (
        module._migrate_visual_cache_identity(
            legacy,
            tuple(changed),
            model_identity=identity,
            prompt=prompt,
        )
        is None
    )

def test_current_visual_cache_reuses_exact_review_subset_with_full_manifest_hash_vector():
    module = _module()
    base_panels = _panels(module, "review-subset")
    full_panels = tuple(
        replace(
            panel,
            metadata_only=True,
            prepared_order=index,
            identity_payload_checksum="a" * 64,
            identity_descriptor_hash=(f"{index + 1:064x}"),
            source_identity_hash="c" * 64,
        )
        for index, panel in enumerate(base_panels)
    )
    identity = _identity(module)
    runner = module.CloudStageRunner(
        provider=_FakeProvider(), model_identity=identity
    )
    prompt = runner.prompts["visual"]
    accepted = (full_panels[0], full_panels[2])
    rows = tuple(
        _visual_row(
            {
                "panel_id": panel.panel_id,
                "source_asset_id": panel.source_asset_id,
                "source_order": panel.source_order,
            }
        )
        | {
            "source_asset_id": panel.source_asset_id,
            "source_order": panel.source_order,
            "source_checksum": panel.source_checksum,
            "cache_identity_hash": panel.identity_descriptor_hash,
            "cache_identity_version": module.VISUAL_CACHE_IDENTITY_VERSION,
        }
        for panel in accepted
    )
    cached = module.VisualStageResult(
        panels=rows,
        source_hash="d" * 64,
        model_identity_hash=identity.identity_hash,
        prompt_version=prompt[0],
        prompt_sha256=prompt[1],
        cache_identity_version=module.VISUAL_CACHE_IDENTITY_VERSION,
        panel_identity_hashes=module._visual_panel_identity_hashes(full_panels),
    ).as_dict()
    subset_panels = module._panels_for_cached_visual_stage(full_panels, cached)
    assert module._visual_cache_requires_subset_restore(
        runner, cached, full_panels, allow_admitted_subset=False
    ) is True
    assert module._visual_cache_requires_subset_restore(
        runner, cached, full_panels, allow_admitted_subset=True
    ) is False

    migrated = module._migrate_visual_cache_identity(
        cached,
        subset_panels,
        model_identity=identity,
        prompt=prompt,
    )

    assert migrated is not None
    assert migrated == cached
    assert len(migrated["panel_identity_hashes"]) == 3

    tampered = dict(cached)
    tampered_rows = [dict(row) for row in rows]
    tampered_rows[1]["cache_identity_hash"] = "f" * 64
    tampered["panels"] = tampered_rows
    assert (
        module._migrate_visual_cache_identity(
            tampered,
            subset_panels,
            model_identity=identity,
            prompt=prompt,
        )
        is None
    )
    assert module._visual_cache_requires_subset_restore(
        runner, tampered, full_panels, allow_admitted_subset=True
    ) is True

def test_visual_runner_reuses_metadata_only_legacy_cache_without_provider_call():
    module = _module()
    base_panels = _panels(module, "runner-migration")
    panels = tuple(
        replace(
            panel,
            metadata_only=True,
            prepared_order=index,
            identity_payload_checksum="a" * 64,
            identity_descriptor_hash="b" * 64,
            source_identity_hash="c" * 64,
        )
        for index, panel in enumerate(base_panels)
    )
    base_identity = _identity(module)
    current_identity = replace(
        base_identity,
        prompt_versions=dict(base_identity.prompt_versions)
        | {"visual_narrative_repair": module.CURRENT_VISUAL_REPAIR_PROMPT_VERSION},
    )
    legacy_identity = replace(
        current_identity,
        prompt_versions=dict(current_identity.prompt_versions)
        | {"visual_narrative_repair": module.LEGACY_VISUAL_REPAIR_PROMPT_VERSION},
    )
    provider = _FakeProvider()

    def fail_observe(_request):
        raise AssertionError("legacy visual cache must be reused before provider call")

    provider.observe = fail_observe
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=current_identity,
    )
    prompt = runner.prompts["visual"]
    legacy_descriptors = [module._legacy_visual_descriptor(panel) for panel in panels]
    legacy = module.VisualStageResult(
        panels=tuple(
            _visual_row(descriptor)
            | {
                "source_asset_id": panel.source_asset_id,
                "source_order": panel.source_order,
                "source_checksum": panel.source_checksum,
            }
            for panel, descriptor in zip(panels, legacy_descriptors, strict=True)
        ),
        source_hash=module._hash(legacy_descriptors),
        model_identity_hash=legacy_identity.identity_hash,
        prompt_version=prompt[0],
        prompt_sha256=prompt[1],
    ).as_dict()

    class Cache:
        def __init__(self):
            self.put_values = {}

        def get(self, _key):
            return None

        def put(self, key, value):
            self.put_values[key] = dict(value)

        def iter_records(self):
            yield legacy

    cache = Cache()
    runner.cache = cache
    result = runner.run_visual_evidence(panels)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels)
    assert provider.calls == []
    assert len(cache.put_values) == 1
    migrated = next(iter(cache.put_values.values()))
    assert migrated["model_identity_hash"] == current_identity.identity_hash
    assert migrated["cache_identity_migration_proof"] == (
        "legacy_model_identity_and_descriptor_hash"
    )

def test_batch_resume_migrates_legacy_visual_without_visual_provider_call(tmp_path):
    module = _module()
    panels = _panels(module, "service-migration")
    identity = _identity(module)
    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=identity,
    )
    prompt = runner.prompts["visual"]
    legacy = module.VisualStageResult(
        panels=tuple(
            _visual_row(panel.descriptor())
            | {
                "source_asset_id": panel.source_asset_id,
                "source_order": panel.source_order,
                "source_checksum": panel.source_checksum,
            }
            for panel in panels
        ),
        source_hash=module._hash([panel.descriptor() for panel in panels]),
        model_identity_hash=identity.identity_hash,
        prompt_version=prompt[0],
        prompt_sha256=prompt[1],
    ).as_dict()
    store = module.JsonJobStore(tmp_path)
    record = module.ChapterJobRecord(job_id="service-migration")
    record.stage_results["visual"] = legacy
    store.save(record)

    provider = _FakeProvider()
    service = module.CloudBatchService(
        runner=module.CloudStageRunner(provider=provider, model_identity=identity),
        store=store,
    )
    result = service.run_job("service-migration", panels)

    # The compact fixture intentionally stops at the existing story-map
    # grounding gate; migration has already happened before that boundary.
    assert result.state == module.ChapterState.NEEDS_REVIEW
    assert result.error_code == "cloud.narrative_not_grounded"
    assert not [call for call in provider.calls if call[0] == "visual"]
    persisted = store.load("service-migration")
    assert persisted is not None
    assert (
        persisted.stage_results["visual"]["cache_identity_version"]
        == module.VISUAL_CACHE_IDENTITY_VERSION
    )

def test_out_of_range_candidate_stays_out_of_final_narration_cache():
    module = _module()
    identity = _identity(module)
    runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=identity,
        cache=module.MemoryStageCache(),
    )
    prompt = runner.prompts["narration"]
    source = {
        "visual_evidence_hash": "v" * 64,
        "story_map_hash": "s" * 64,
        "selection_hash": "e" * 64,
    }
    candidate = module.NarrationResult(
        spoken_text="A candidate that needs repair.",
        display_words=("A", "CANDIDATE"),
        passages=(),
        ending_kind="consequence",
        word_count=172,
        estimated_duration_s=69.57,
        observations=(),
        continuity_ledger={},
        evidence_graph={},
        story_spine={},
        qc_report={},
        model_identity_hash=identity.identity_hash,
        prompt_version=prompt[0],
        prompt_sha256=prompt[1],
        visual_evidence_hash=source["visual_evidence_hash"],
    )
    runner._store_narration_repair_candidate(
        source=source,
        prompt=prompt,
        result=candidate,
        failure_codes=(
            "cloud.narrative_duration_out_of_range",
            "cloud.narrative_word_count_out_of_range",
        ),
    )
    candidate_record = runner.cache.get(
        runner._narration_repair_candidate_key(source, prompt)
    )
    assert candidate_record["cache_type"] == module.NARRATION_REPAIR_CANDIDATE_VERSION
    assert runner.cache.get(
        module._cache_key("narration", source, identity, prompt)
    ) is None

def test_stream_checkpoint_reuses_panel_identity_when_batch_position_changes(tmp_path):
    module = _module()
    base = _panels(module, "stream-reuse")
    first_panel = replace(base[1], prepared_order=1)
    earlier_panel = replace(base[0], prepared_order=0)
    checkpoint = tmp_path / "visual_checkpoints.jsonl"
    provider = _FakeProvider()

    first_runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.MemoryStageCache(),
        max_attempts=1,
        visual_checkpoint_path=checkpoint,
    )
    first_stream = first_runner.start_visual_evidence_stream(
        queue_size=1,
        max_panels=1,
        max_estimated_bytes=10_000_000,
    )
    first_stream.submit(first_panel)
    first_stream.finish((first_panel,))
    calls_after_first = len(provider.calls)

    resumed_runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.MemoryStageCache(),
        max_attempts=1,
        visual_checkpoint_path=checkpoint,
    )
    resumed_stream = resumed_runner.start_visual_evidence_stream(
        queue_size=1,
        max_panels=1,
        max_estimated_bytes=10_000_000,
    )
    resumed_stream.submit(earlier_panel)
    resumed_stream.submit(first_panel)
    result = resumed_stream.finish((earlier_panel, first_panel))

    assert result.panel_ids == (earlier_panel.panel_id, first_panel.panel_id)
    assert len(provider.calls) - calls_after_first == 1

def test_stream_quarantines_terminal_panel_local_failure_and_reuses_checkpoint(
    tmp_path,
):
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-quarantine"))
    )
    poison_id = panels[-1].panel_id

    class _PoisonPanelProvider(_FakeProvider):
        def observe(self, request):
            rows = super().observe(request)
            for row in rows:
                if row.get("panel_id") == poison_id:
                    row["visible_facts"] = []
            return rows

    provider = _PoisonPanelProvider()
    checkpoint = tmp_path / "visual_checkpoints.jsonl"
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.MemoryStageCache(),
        max_attempts=1,
        visual_checkpoint_path=checkpoint,
    )
    stream = runner.start_visual_evidence_stream(
        queue_size=1,
        max_panels=len(panels),
        max_estimated_bytes=10_000_000,
    )
    for panel in panels:
        stream.submit(panel)

    result = stream.finish(panels)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels[:-1])
    assert [item["panel_id"] for item in result.rejected_panels] == [poison_id]
    assert result.rejected_panels[0]["failure_scope"] == "panel_local_reject"
    assert result.rejected_panels[0]["terminal_status"] == "rejected"
    assert runner.last_visual_stream_metrics["missing_panel_count"] == 0
    assert runner.last_visual_stream_metrics["rejected_panel_count"] == 1
    assert runner.last_visual_stream_metrics["rejected_panel_ids"] == [poison_id]
    assert any(
        json.loads(line)["panel_id"] == poison_id
        and json.loads(line)["terminal_status"] == "rejected"
        for line in checkpoint.read_text(encoding="utf-8").splitlines()
    )

    round_trip = module.VisualStageResult.from_dict(result.as_dict())
    assert round_trip.rejected_panels == result.rejected_panels

    calls_before_resume = len(provider.calls)
    resumed_runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.MemoryStageCache(),
        max_attempts=1,
        visual_checkpoint_path=checkpoint,
    )
    resumed_stream = resumed_runner.start_visual_evidence_stream(
        queue_size=1,
        max_panels=len(panels),
        max_estimated_bytes=10_000_000,
    )
    for panel in panels:
        resumed_stream.submit(panel)
    resumed = resumed_stream.finish(panels)

    assert resumed.panel_ids == result.panel_ids
    assert [item["panel_id"] for item in resumed.rejected_panels] == [poison_id]
    assert len(provider.calls) == calls_before_resume

def test_success_merge_preserves_newer_durable_preflight_checkpoint(tmp_path):
    module = _module()
    store = module.JsonJobStore(tmp_path)
    service = module.CloudBatchService.__new__(module.CloudBatchService)
    service.store = store
    stale = module.ChapterJobRecord(
        job_id="success-preflight-checkpoint",
        stage_results={"narration": {"word_count": 61}},
    )
    durable = module.ChapterJobRecord(
        job_id="success-preflight-checkpoint",
        stage_results={
            "feasible_visual_ledger_preflight": {
                "identity_hash": "j" * 64,
                "ledger": {
                    "contract_version": module.visual_narrative_repair.REPAIR_CONTRACT_VERSION
                },
            }
        },
        review_queue=[{"code": "prior", "reason": "prior"}],
    )
    store.save(durable)
    merged = service._merge_latest_durable_progress(stale)
    merged.stage_results["review_preview"] = {"output_path": "preview.mp4"}
    store.save(merged)
    persisted = store.load("success-preflight-checkpoint")
    assert persisted is not None
    assert persisted.stage_results["narration"]["word_count"] == 61
    assert persisted.stage_results["feasible_visual_ledger_preflight"]["identity_hash"] == "j" * 64
    assert persisted.stage_results["review_preview"]["output_path"] == "preview.mp4"
    assert persisted.review_queue == [{"code": "prior", "reason": "prior"}]

def test_record_failure_preserves_newer_durable_preflight_checkpoint(tmp_path):
    module = _module()
    store = module.JsonJobStore(tmp_path)
    service = module.CloudBatchService.__new__(module.CloudBatchService)
    service.store = store
    service.runner = SimpleNamespace(
        request_count=0, request_counts={}, estimated_cost_usd=0.0
    )
    stale = module.ChapterJobRecord(job_id="preflight-checkpoint")
    durable = module.ChapterJobRecord(
        job_id="preflight-checkpoint",
        stage_results={
            "feasible_visual_ledger_preflight": {
                "identity_hash": "i" * 64,
                "ledger": {"contract_version": module.visual_narrative_repair.REPAIR_CONTRACT_VERSION},
            }
        },
        review_queue=[{"code": "prior", "reason": "prior"}],
    )
    store.save(durable)
    service._record_failure(
        stale,
        module.CloudStageError("visual.narrative_repair_ungrounded", reviewable=True),
    )
    persisted = store.load("preflight-checkpoint")
    assert persisted is not None
    assert persisted.stage_results["feasible_visual_ledger_preflight"]["identity_hash"] == "i" * 64
    assert [row["code"] for row in persisted.review_queue] == [
        "prior", "visual.narrative_repair_ungrounded"
    ]

