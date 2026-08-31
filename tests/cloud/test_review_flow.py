"""Cloud multimodal regression tests grouped by responsibility."""

from __future__ import annotations

from tests.cloud.mass_support import (
    _FakeProvider,
    _identity,
    _module,
    _panels,
    _visual_row,
    importlib,
    pytest,
    replace,
)


def test_prepare_project_panels_preserves_segmentation_review_code(monkeypatch):
    module = _module()
    segmentation = importlib.import_module("app.services.segmentation")
    pipeline = importlib.import_module("app.services.pipeline")
    payload = b"not decoded because lineage is rejected first"
    input_row = segmentation.SourceAssetInput(
        source_asset_id="partial-source",
        original_checksum="f" * 64,
        original_width=100,
        original_height=100,
        source_bounds=(0, 0, 100, 80),
        strip_order=0,
        region_order=0,
        payload=payload,
        decoded_width=100,
        decoded_height=80,
    )
    monkeypatch.setattr(pipeline, "project_assets", lambda _db, _project_id: ())
    monkeypatch.setattr(pipeline, "image_assets", lambda assets: list(assets))
    monkeypatch.setattr(pipeline, "_build_source_inputs", lambda _assets: ((input_row,), {}))

    with pytest.raises(module.CloudStageError) as caught:
        module.prepare_project_panels(object(), "project-a")

    assert caught.value.code == "segmentation.coverage_incomplete"
    assert caught.value.reviewable is True

def test_review_preview_requests_audited_segmentation_auto_override(monkeypatch):
    module = _module()
    from types import SimpleNamespace

    captured = {}

    def fake_prepare(_db, _project_id, **kwargs):
        captured.update(kwargs)
        raise module.CloudStageError("segmentation.ambiguous_boundary", reviewable=True)

    class Store:
        def load(self, _project_id):
            return None

        def save(self, record):
            self.record = record

    service = module.CloudBatchService.__new__(module.CloudBatchService)
    service.runner = SimpleNamespace(
        model_identity=SimpleNamespace(identity_hash="model-hash"),
        assess_strip_boundaries=lambda _request: {},
    )
    service.store = Store()
    service.review_root = None
    monkeypatch.setattr(module, "prepare_project_panels", fake_prepare)

    result = service.run_project(object(), "project-a", review_only_preview=True)

    assert result.state == module.ChapterState.NEEDS_REVIEW
    assert captured["review_only_auto_override"] is True

def test_review_project_restores_prepared_manifest_before_cold_prepare(monkeypatch):
    module = _module()
    from types import SimpleNamespace

    panels = _panels(module)
    failed = module.ChapterJobRecord(
        job_id="project-a",
        state=module.ChapterState.NEEDS_REVIEW,
        error_code="cloud.narrative_not_grounded",
        stage_results={
            "prepared_panel_manifest": {"manifest": "durable"},
            "segmentation": {"status": "RECONCILED"},
        },
    )

    class Store:
        def load(self, _project_id):
            return failed

        def save(self, _record):
            return None

    service = module.CloudBatchService.__new__(module.CloudBatchService)
    service.runner = SimpleNamespace(
        model_identity=SimpleNamespace(identity_hash="m" * 64),
        assess_strip_boundaries=lambda _request: {},
    )
    service.store = Store()
    service.review_root = None
    restored = {}

    def restore(_db, _project_id, manifest):
        restored["manifest"] = manifest
        return panels, {"status": "RECONCILED"}

    monkeypatch.setattr(module, "_restore_project_prepared_manifest", restore)
    monkeypatch.setattr(
        module,
        "prepare_project_panels",
        lambda *_args, **_kwargs: pytest.fail("review resume must not cold-prepare a durable manifest"),
    )
    monkeypatch.setattr(service, "run_job", lambda *_args, **_kwargs: failed)

    result = service.run_project(object(), "project-a", review_only_preview=True)

    assert result is failed
    assert restored["manifest"] == {"manifest": "durable"}

def test_review_preview_failure_code_keeps_nested_stable_code():
    module = _module()

    assert module._review_failure_code(
        "reference_planning_failed: visual.visual_unavailable: no feasible panel"
    ) == "visual.visual_unavailable"
    assert module._review_failure_code(
        "reference.subtitle_overflow: review preview failed"
    ) == "reference.subtitle_overflow"
    assert module._review_failure_code("unstructured local failure") == "review.preview_failed"

def test_review_preview_failure_code_preserves_render_and_encoder_codes():
    module = _module()

    assert module._review_failure_code(
        "render.encoder_unavailable: review preview failed"
    ) == "render.encoder_unavailable"
    assert module._review_failure_code(
        "ffmpeg.filter_failed: review preview failed"
    ) == "ffmpeg.filter_failed"

def test_review_preview_failure_code_keeps_subtitle_stable_code():
    module = _module()

    assert module._review_failure_code(
        "subtitle.timing_out_of_bounds: sentence karaoke contract is invalid"
    ) == "subtitle.timing_out_of_bounds"

@pytest.mark.parametrize(
    "failure_code",
    (
        "cloud.narrative_not_grounded",
        "cloud.narrative_duration_out_of_range",
        "cloud.narrative_repair_micro_compaction_unavailable",
        "visual.narrative_repair_ungrounded",
        "subtitle.overflow",
    ),
)

def test_review_project_repairs_after_initial_narration_failure(monkeypatch, failure_code):
    module = _module()
    from types import SimpleNamespace

    panels = _panels(module)
    dropped_panel = replace(
        panels[0],
        panel_id="dropped-panel",
        source_asset_id="dropped-asset",
        source_order=999,
    )
    all_panels = panels + (dropped_panel,)
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
        causal_chain=({"from_beat": "beat-1", "to_beat": "beat-1", "reason": "the visible sequence continues"},),
        claims=({"claim_id": "claim-1", "panel_ids": [panel.panel_id for panel in panels], "qualification": "the panels support this"},),
        story_map_hash="s" * 64,
        model_identity_hash="m" * 64,
        prompt_version="cloud-causal-map-v2",
        prompt_sha256="c" * 64,
    )
    failed = module.ChapterJobRecord(
        job_id="project-a",
        state=module.ChapterState.NEEDS_REVIEW,
        error_code=failure_code,
        stage_results={
            "visual": visual.as_dict(),
            "story_map": story_map.as_dict(),
        },
    )
    partial_narration = SimpleNamespace(visual_evidence_hash=visual.visual_evidence_hash)
    persisted_narration = module.NarrationResult(
        spoken_text="Persisted narration candidate.",
        display_words=("PERSISTED", "NARRATION", "CANDIDATE"),
        passages=(),
        ending_kind="consequence",
        word_count=120,
        estimated_duration_s=52.17,
        qc_report={"duration_contract": {"word_count": 120, "estimated_duration_s": 52.17}},
        model_identity_hash="m" * 64,
        prompt_version="vision-first-story-analyzer-v3",
        prompt_sha256="n" * 64,
        visual_evidence_hash=visual.visual_evidence_hash,
    )
    if failure_code in {"visual.narrative_repair_ungrounded", "subtitle.overflow"}:
        failed.stage_results["narration"] = persisted_narration.as_dict()

    class Store:
        def __init__(self):
            self.saved = []

        def load(self, _job_id):
            return None

        def save(self, record):
            self.saved.append(record)

    service = module.CloudBatchService.__new__(module.CloudBatchService)
    service.runner = SimpleNamespace(
        model_identity=SimpleNamespace(identity_hash="m" * 64),
        assess_strip_boundaries=lambda _request: {},
        prompts={"visual_narrative_repair": ("repair-v1", "r" * 64, "")},
        _last_narration_result=(
            partial_narration
            if failure_code in {
                "cloud.narrative_duration_out_of_range",
                "cloud.narrative_repair_micro_compaction_unavailable",
            }
            else None
        ),
    )
    service.store = Store()
    service.review_root = None
    monkeypatch.setattr(
        module,
        "prepare_project_panels",
        lambda *_args, **_kwargs: (all_panels, {"status": "RECONCILED"}),
    )
    monkeypatch.setattr(
        module,
        "_build_project_prepared_manifest",
        lambda *_args, **_kwargs: {"manifest": "generated"},
    )
    monkeypatch.setattr(service, "run_job", lambda *_args, **_kwargs: failed)
    observed = {}

    def fake_repair(_db, _project_id, script_row, _panels, _result, **_kwargs):
        observed["script_row"] = script_row
        observed["result"] = _result
        observed["panel_ids"] = tuple(panel.panel_id for panel in _panels)
        return SimpleNamespace(
            narration=SimpleNamespace(
                as_dict=lambda: {"spoken_text": "repaired", "passages": []},
            ),
            visual=visual,
            story_map=story_map,
        ), SimpleNamespace(as_dict=lambda: {"entries": []}), ()

    monkeypatch.setattr(service, "_repair_review_narrative", fake_repair)
    monkeypatch.setattr(
        module,
        "persist_cloud_chapter",
            lambda *_args, **_kwargs: (
            SimpleNamespace(id="analysis-a"),
            SimpleNamespace(id="script-a", version=1, estimated_duration=50.0, sections=[]),
        ),
    )
    pipeline = importlib.import_module("app.services.pipeline")
    monkeypatch.setattr(pipeline, "build_timeline", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        pipeline,
        "render_silent_review_preview",
        lambda *_args, **_kwargs: (None, SimpleNamespace(as_dict=lambda: {"review": True})),
    )

    result = service.run_project(object(), "project-a", review_only_preview=True)

    assert observed["script_row"] is None
    if failure_code in {
        "cloud.narrative_duration_out_of_range",
        "cloud.narrative_repair_micro_compaction_unavailable",
    }:
        assert observed["result"].narration is partial_narration
    elif failure_code in {"visual.narrative_repair_ungrounded", "subtitle.overflow"}:
        assert observed["result"].narration.spoken_text == persisted_narration.spoken_text
    else:
        assert observed["result"] is None
    assert observed["panel_ids"] == tuple(panel.panel_id for panel in panels)
    assert result.state == module.ChapterState.REVIEW_PREVIEW_READY, (result.error_code, result.error_message)

def test_review_repair_forwards_persisted_panel_crop_fallback(monkeypatch):
    module = _module()
    from pathlib import Path
    from types import SimpleNamespace

    pipeline = importlib.import_module("app.services.pipeline")
    reference_profile = importlib.import_module("app.services.reference_profile")
    repair = importlib.import_module("app.services.visual_narrative_repair")

    class Database:
        def get(self, *_args):
            return SimpleNamespace(template="reference_matched_shorts_v2")

    monkeypatch.setattr(
        reference_profile,
        "resolve_reference_profile",
        lambda _template: SimpleNamespace(),
    )
    monkeypatch.setattr(pipeline, "project_assets", lambda *_args: ())
    monkeypatch.setattr(pipeline, "image_assets", lambda *_args: ())
    monkeypatch.setattr(
        pipeline,
        "latest_analysis",
        lambda *_args: SimpleNamespace(panel_regions=()),
    )
    observed = {}

    def fake_load(*_args, **kwargs):
        observed.update(kwargs)
        return (SimpleNamespace(panel_id="panel-1"),)

    monkeypatch.setattr(
        pipeline,
        "_load_reference_panel_fallback_candidates",
        fake_load,
    )
    monkeypatch.setattr(
        repair,
        "default_section_to_beats",
        lambda *_args: {"hook": ("beat-1",)},
    )
    monkeypatch.setattr(
        repair,
        "build_feasible_visual_ledger",
        lambda *_args, **_kwargs: SimpleNamespace(entries=("entry",)),
    )
    monkeypatch.setattr(repair, "missing_visual_sections", lambda *_args: ())

    monkeypatch.setattr(
        module,
        "_durable_visual_repair_covers_missing_sections",
        lambda *_args, **_kwargs: True,
    )

    service = module.CloudBatchService.__new__(module.CloudBatchService)
    service.runner = SimpleNamespace(
        model_identity=SimpleNamespace(identity_hash="m" * 64),
    )
    result = SimpleNamespace(
        visual=SimpleNamespace(),
        story_map=SimpleNamespace(
            beats=({"beat_id": "beat-1", "panel_ids": ["panel-1"]},),
        ),
        narration=SimpleNamespace(),
    )

    outcome = service._repair_review_narrative(
        Database(),
        "project-a",
        SimpleNamespace(sections=({"section": "hook"},)),
        (),
        result,
        review_source_upscale_policy="review_silent_source_upscale_v1",
        review_source_root=Path("/review"),
    )

    assert outcome[0] is result
    assert observed["allow_persisted_panel_crop_fallback"] is True
    assert observed["allow_conservative_full_panel"] is True

def test_review_repair_persisted_loader_preserves_exact_story_beat_lineage(monkeypatch):
    """Persisted candidates must keep exact beat lineage separate from editorial role."""
    module = _module()
    from pathlib import Path
    from types import SimpleNamespace

    pipeline = importlib.import_module("app.services.pipeline")
    reference_profile = importlib.import_module("app.services.reference_profile")
    repair = importlib.import_module("app.services.visual_narrative_repair")

    class Database:
        def get(self, *_args):
            return SimpleNamespace(template="reference_matched_shorts_v2")

    monkeypatch.setattr(
        reference_profile,
        "resolve_reference_profile",
        lambda _template: SimpleNamespace(),
    )
    monkeypatch.setattr(pipeline, "project_assets", lambda *_args: ())
    monkeypatch.setattr(pipeline, "image_assets", lambda *_args: ())
    monkeypatch.setattr(
        pipeline,
        "latest_analysis",
        lambda *_args: SimpleNamespace(
            panel_regions=(SimpleNamespace(panel_id="panel-1", source_order=1),)
        ),
    )
    observed = {}

    def fake_load(*_args, **kwargs):
        observed.update(kwargs)
        return (SimpleNamespace(panel_id="panel-1"),)

    monkeypatch.setattr(pipeline, "_load_reference_panel_fallback_candidates", fake_load)
    monkeypatch.setattr(
        repair,
        "default_section_to_beats",
        lambda *_args: {"hook": ("beat-1",)},
    )
    monkeypatch.setattr(
        repair,
        "build_feasible_visual_ledger",
        lambda *_args, **_kwargs: SimpleNamespace(entries=("entry",)),
    )
    monkeypatch.setattr(repair, "missing_visual_sections", lambda *_args: ())

    monkeypatch.setattr(
        module,
        "_durable_visual_repair_covers_missing_sections",
        lambda *_args, **_kwargs: True,
    )

    service = module.CloudBatchService.__new__(module.CloudBatchService)
    service.runner = SimpleNamespace(
        model_identity=SimpleNamespace(identity_hash="m" * 64),
    )
    result = SimpleNamespace(
        visual=SimpleNamespace(),
        story_map=SimpleNamespace(
            beats=({"beat_id": "beat-1", "panel_ids": ["panel-1"]},),
        ),
        narration=SimpleNamespace(),
    )

    outcome = service._repair_review_narrative(
        Database(),
        "project-a",
        SimpleNamespace(sections=({"section": "hook"},)),
        (),
        result,
        review_source_upscale_policy="review_silent_source_upscale_v1",
        review_source_root=Path("/review"),
    )

    assert outcome[0] is result
    assert observed["section_evidence_panel_ids"] == {"beat-1": ("panel-1",)}
    assert observed["beats_by_section"] == {"beat-1": ("beat-1",)}

def test_review_render_map_uses_repaired_feasible_panel_ids_across_beats():
    """A repaired section may cite a safe panel from a later causal beat."""
    module = _module()
    from types import SimpleNamespace

    ledger = SimpleNamespace(
        entries=(
            SimpleNamespace(
                panel_id="safe-later",
                eligible_beats=("beat-2",),
            ),
            SimpleNamespace(
                panel_id="safe-middle",
                eligible_beats=("beat-3",),
            ),
        )
    )
    script = SimpleNamespace(
        sections=(
            {
                "section": "hook",
                "evidence_panel_ids": ["safe-later"],
            },
            {
                "section": "setup",
                "evidence_panel_ids": ["stale-original"],
            },
            {
                "section": "cta",
                "evidence_panel_ids": ["safe-middle"],
            },
        )
    )

    observed = module._review_section_panel_ids(
        script,
        ledger,
        {
            "hook": ("beat-1",),
            "setup": ("beat-2",),
            "cta": ("beat-5",),
        },
    )

    assert observed == {
        "hook": ("safe-later",),
        "setup": ("safe-later",),
        "cta": ("safe-middle",),
    }

def test_persisted_review_reuses_exact_prepared_panel_payloads(monkeypatch):
    """A durable script must not force review back through segmented DB bytes."""
    module = _module()
    from pathlib import Path
    from types import SimpleNamespace

    pipeline = importlib.import_module("app.services.pipeline")
    reference_profile = importlib.import_module("app.services.reference_profile")
    repair = importlib.import_module("app.services.visual_narrative_repair")
    panels = _panels(module)
    observed = {}

    class Database:
        def get(self, *_args):
            return SimpleNamespace(template="reference_matched_shorts_v2")

    monkeypatch.setattr(
        reference_profile,
        "resolve_reference_profile",
        lambda _template: SimpleNamespace(),
    )
    monkeypatch.setattr(pipeline, "project_assets", lambda *_args: ())
    monkeypatch.setattr(pipeline, "image_assets", lambda *_args: ())
    monkeypatch.setattr(
        pipeline,
        "latest_analysis",
        lambda *_args: SimpleNamespace(panel_regions=()),
    )
    monkeypatch.setattr(
        pipeline,
        "_load_reference_panel_fallback_candidates",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        module,
        "_build_ephemeral_review_candidates",
        lambda received_panels, *_args, **_kwargs: (
            observed.update({"panels": received_panels}) or ("candidate",),
            {"hook": ("beat-1",)},
        ),
    )
    monkeypatch.setattr(
        repair,
        "default_section_to_beats",
        lambda *_args: {"hook": ("beat-1",)},
    )

    def fake_ledger(candidates, *_args, **_kwargs):
        return SimpleNamespace(entries=("entry",) if tuple(candidates) else ())

    monkeypatch.setattr(repair, "build_feasible_visual_ledger", fake_ledger)
    monkeypatch.setattr(
        repair,
        "missing_visual_sections",
        lambda ledger, *_args: () if ledger.entries else ("hook",),
    )

    monkeypatch.setattr(
        module,
        "_durable_visual_repair_covers_missing_sections",
        lambda *_args, **_kwargs: True,
    )

    service = module.CloudBatchService.__new__(module.CloudBatchService)
    service.runner = SimpleNamespace(
        model_identity=SimpleNamespace(identity_hash="m" * 64),
    )
    result = SimpleNamespace(
        visual=SimpleNamespace(),
        story_map=SimpleNamespace(
            beats=({"beat_id": "beat-1", "panel_ids": [panel.panel_id for panel in panels]},),
        ),
        narration=SimpleNamespace(),
    )

    outcome = service._repair_review_narrative(
        Database(),
        "project-a",
        SimpleNamespace(sections=({"section": "hook"},)),
        panels,
        result,
        review_source_upscale_policy="review_silent_source_upscale_v1",
        review_source_root=Path("/review"),
    )

    assert observed["panels"] is panels
    assert outcome[0] is result
    assert outcome[1].entries == ("entry",)
    assert outcome[2] == ()

def test_ephemeral_review_registry_allows_title_visual_row_without_story_candidate():
    module = _module()
    import io

    from PIL import Image

    profile_module = importlib.import_module("app.services.reference_profile")
    payload = io.BytesIO()
    Image.new("RGB", (640, 640), (80, 90, 100)).save(payload, format="PNG")
    panels = tuple(
        module.CloudPanelInput(
            panel_id=f"panel-{index}",
            source_asset_id=f"asset-{index}",
            source_order=index,
            mime_type="image/png",
            payload=payload.getvalue(),
            panel_bounds=(0, 0, 640, 640),
            source_dimensions=(640, 640),
        )
        for index in range(6)
    )
    visual = module.VisualStageResult(
        panels=tuple(_visual_row(panel.descriptor()) for panel in panels),
        source_hash="v" * 64,
        model_identity_hash="m" * 64,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="p" * 64,
    )
    story_map = module.StoryMapResult(
        panel_ids=tuple(panel.panel_id for panel in panels),
        beats=tuple(
            {"beat_id": f"beat-{index}", "panel_ids": [f"panel-{index}"]}
            for index in range(1, 6)
        ),
        causal_chain=(),
        claims=(),
        story_map_hash="s" * 64,
        model_identity_hash="m" * 64,
        prompt_version="cloud-causal-map-v2",
        prompt_sha256="c" * 64,
    )

    candidates, section_to_beats = module._build_ephemeral_review_candidates(
        panels,
        visual,
        story_map,
        profile=profile_module.REFERENCE_MATCHED_SHORTS_V2,
        review_source_upscale_policy=None,
    )

    assert len(candidates) == 5
    assert all(candidate.source_order > 0 for candidate in candidates)
    assert len(section_to_beats) == 5

def test_ephemeral_review_registry_keeps_beat_mapping_when_section_has_no_claim_panel():
    module = _module()
    import io

    from PIL import Image

    profile_module = importlib.import_module("app.services.reference_profile")
    payload = io.BytesIO()
    Image.new("RGB", (640, 640), (80, 90, 100)).save(payload, format="PNG")
    panels = tuple(
        module.CloudPanelInput(
            panel_id=f"panel-{index}",
            source_asset_id=f"asset-{index}",
            source_order=index,
            mime_type="image/png",
            payload=payload.getvalue(),
            panel_bounds=(0, 0, 640, 640),
            source_dimensions=(640, 640),
        )
        for index in range(6)
    )
    visual = module.VisualStageResult(
        panels=tuple(_visual_row(panel.descriptor()) for panel in panels),
        source_hash="v" * 64,
        model_identity_hash="m" * 64,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="p" * 64,
    )
    story_map = module.StoryMapResult(
        panel_ids=tuple(panel.panel_id for panel in panels),
        beats=tuple(
            {"beat_id": f"beat-{index}", "panel_ids": [f"panel-{index}"]}
            for index in range(1, 6)
        ),
        causal_chain=(),
        claims=({"claim_id": "claim-1", "panel_ids": ["panel-1"]},),
        story_map_hash="s" * 64,
        model_identity_hash="m" * 64,
        prompt_version="cloud-causal-map-v2",
        prompt_sha256="c" * 64,
    )

    candidates, section_to_beats = module._build_ephemeral_review_candidates(
        panels,
        visual,
        story_map,
        profile=profile_module.REFERENCE_MATCHED_SHORTS_V2,
        review_source_upscale_policy=None,
    )

    assert {candidate.panel_id for candidate in candidates} == {"panel-1"}
    assert len(section_to_beats) == 5

def test_review_preview_state_is_distinct_from_voice_ready_render_state():
    module = _module()

    assert module.ChapterState.REVIEW_PREVIEW_READY.value == "REVIEW_PREVIEW_READY"
    result = type("Result", (), {"state": module.ChapterState.REVIEW_PREVIEW_READY})()
    assert module.regular_render_allowed(result) is False
    with pytest.raises(module.CloudStageError) as caught:
        module.require_final_render_ready(result)
    assert caught.value.code == "cloud.stage_not_ready"

def test_review_only_gate_never_invents_voice_word_timings():
    module = _module()
    provider = _FakeProvider()
    runner = module.CloudStageRunner(provider=provider, model_identity=_identity(module))
    result = runner.run_chapter(_panels(module))

    gate = module.review_only_render_gate(result)
    assert gate.allowed is True
    assert gate.audio_path is None
    assert gate.timing_source == "voice_required"
    with pytest.raises(module.CloudStageError) as caught:
        module.require_final_render_ready(result)
    assert caught.value.code == "cloud.voice_timing_required"



def test_metadata_only_first_persistence_materializes_only_claim_panels(monkeypatch):
    module = _module()
    from pathlib import Path
    from types import SimpleNamespace

    pipeline = importlib.import_module('app.services.pipeline')
    reference_profile = importlib.import_module('app.services.reference_profile')
    repair = importlib.import_module('app.services.visual_narrative_repair')
    panel_ids = ('panel-1', 'panel-2', 'panel-3')
    panels = tuple(
        SimpleNamespace(panel_id=panel_id, metadata_only=True, payload=b'marker')
        for panel_id in panel_ids
    )
    materialized = tuple(
        SimpleNamespace(
            panel_id=panel.panel_id,
            metadata_only=panel.panel_id == 'panel-3',
            payload=b'pixels' if panel.panel_id != 'panel-3' else b'marker',
        )
        for panel in panels
    )
    observed = {}

    class Database:
        def get(self, *_args):
            return SimpleNamespace(template='reference_matched_shorts_v2')

    monkeypatch.setattr(reference_profile, 'resolve_reference_profile', lambda _template: SimpleNamespace())
    monkeypatch.setattr(pipeline, 'project_assets', lambda *_args: ())
    monkeypatch.setattr(pipeline, 'image_assets', lambda *_args: ())

    def fake_materialize(_db, _project_id, received, *, required_panel_ids):
        observed['required'] = tuple(required_panel_ids)
        observed['received'] = received
        return materialized

    monkeypatch.setattr(module, '_materialize_metadata_only_panels', fake_materialize)
    monkeypatch.setattr(
        module,
        '_build_ephemeral_review_candidates',
        lambda received, *_args, **_kwargs: (
            observed.update({'ephemeral_panels': received}) or ('candidate',),
            {'hook': ('beat-1',)},
        ),
    )
    monkeypatch.setattr(
        repair,
        'build_feasible_visual_ledger',
        lambda *_args, **_kwargs: SimpleNamespace(entries=('entry',)),
    )
    monkeypatch.setattr(repair, 'repair_scope_sections', lambda *_args, **_kwargs: ())
    monkeypatch.setattr(
        module,
        '_durable_visual_repair_covers_missing_sections',
        lambda *_args, **_kwargs: True,
    )

    service = module.CloudBatchService.__new__(module.CloudBatchService)
    service.runner = SimpleNamespace(model_identity=SimpleNamespace(identity_hash='m' * 64))
    result = SimpleNamespace(
        visual=SimpleNamespace(),
        story_map=SimpleNamespace(
            beats=({'beat_id': 'beat-1', 'panel_ids': list(panel_ids)},),
            claims=({'claim_id': 'claim-1', 'panel_ids': ['panel-1', 'panel-2']},),
        ),
        narration=SimpleNamespace(),
    )

    outcome = service._repair_review_narrative(
        Database(),
        'project-a',
        None,
        panels,
        result,
        review_source_upscale_policy='review_silent_source_upscale_v1',
        review_source_root=Path('/review'),
    )

    assert observed['required'] == ('panel-1', 'panel-2')
    assert observed['received'] is panels
    assert observed['ephemeral_panels'] == materialized
    assert outcome[0] is result
def test_durable_preflight_reuses_repaired_narration_without_pixel_recompute(monkeypatch):
    module = _module()
    from pathlib import Path
    from types import SimpleNamespace

    pipeline = importlib.import_module("app.services.pipeline")
    reference_profile = importlib.import_module("app.services.reference_profile")
    repair = importlib.import_module("app.services.visual_narrative_repair")
    review_source_upscale = importlib.import_module("app.services.review_source_upscale")

    model_hash = "m" * 64
    visual_hash = "v" * 64
    source_hash = "x" * 64
    story_hash = "s" * 64
    prompt_hash = "r" * 64
    ledger = repair.FeasibleVisualLedger(entries=(), model_identity_hash=model_hash)
    profile = SimpleNamespace()
    identity = {
        "version": "review-feasible-ledger-preflight-v1",
        "repair_contract_version": repair.REPAIR_CONTRACT_VERSION,
        "model_identity_hash": model_hash,
        "visual_evidence_hash": visual_hash,
        "visual_source_hash": source_hash,
        "story_map_hash": story_hash,
        "story_map_visual_evidence_hash": visual_hash,        "profile_hash": "profile-hash",
        "upscale_policy_id": "review_silent_source_upscale_v1",
        "section_to_beats": {"hook": ["beat-1"]},
    }
    preflight = {
        "identity": identity,
        "identity_hash": module._hash(identity),
        "ledger": ledger.as_dict(),
    }

    class Database:
        def get(self, *_args):
            return SimpleNamespace(template="reference_matched_shorts_v2")

    class Store:
        def load(self, _project_id):
            return SimpleNamespace(
                stage_results={"feasible_visual_ledger_preflight": preflight}
            )

    monkeypatch.setattr(reference_profile, "resolve_reference_profile", lambda _template: profile)
    monkeypatch.setattr(reference_profile, "profile_hash", lambda _profile: "profile-hash")
    monkeypatch.setattr(
        review_source_upscale,
        "validate_review_upscale_request",
        lambda *_args, **_kwargs: SimpleNamespace(
            policy_id="review_silent_source_upscale_v1",
            allow_low_source_resolution_warning=True,
        ),
    )
    monkeypatch.setattr(repair, "repair_scope_sections", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(
        repair,
        "build_repair_payload",
        lambda **_kwargs: {"capacity_safe_claim_plan": {}},
    )
    monkeypatch.setattr(
        module,
        "_durable_visual_repair_covers_missing_sections",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        pipeline,
        "project_assets",
        lambda *_args: pytest.fail("durable preflight must skip pixel materialization"),
    )

    story_map = SimpleNamespace(
        beats=({"beat_id": "beat-1"},),
        claims=(),
        story_map_hash=story_hash,
        visual_evidence_hash=visual_hash,
        as_dict=lambda: {"beats": [{"beat_id": "beat-1"}]},
    )
    narration = SimpleNamespace(
        prompt_version="visual-narrative-repair-v14",
        prompt_sha256=prompt_hash,
        as_dict=lambda: {},
    )
    result = SimpleNamespace(
        visual=SimpleNamespace(visual_evidence_hash=visual_hash, source_hash=source_hash),
        story_map=story_map,
        narration=narration,
    )
    service = module.CloudBatchService.__new__(module.CloudBatchService)
    service.store = Store()
    service.runner = SimpleNamespace(
        model_identity=SimpleNamespace(identity_hash=model_hash),
        prompts={
            "visual_narrative_repair": (
                "visual-narrative-repair-v14",
                prompt_hash,
                "",
            )
        },
    )

    outcome = service._repair_review_narrative(
        Database(),
        "project-a",
        SimpleNamespace(sections=({"section": "hook"},)),
        (),
        result,
        review_source_upscale_policy="review_silent_source_upscale_v1",
        review_source_root=Path("/review"),
    )

    assert outcome[0] is result
    assert outcome[1] == ledger
    assert outcome[2] == ()
