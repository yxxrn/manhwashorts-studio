"""Cloud multimodal regression tests grouped by responsibility."""

from __future__ import annotations

from tests.cloud.mass_support import (
    UTC,
    _FakeProvider,
    _identity,
    _immutable_slot_fixture,
    _module,
    _narrative_output,
    _panels,
    _position_rewrite_text,
    _provider_position_vector,
    _visual_row,
    datetime,
    importlib,
    json,
    pytest,
    replace,
    timedelta,
)


def test_project_persistence_reuses_regular_script_gate_without_approval(monkeypatch):
    module = _module()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db import Base
    from app.models import Project, SourceAsset, StoryAnalysis, User, Workspace

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        user = User(email="cloud-owner@example.com", name="Cloud Owner", password_hash="test")
        workspace = Workspace(owner=user, name="Cloud Workspace")
        project = Project(workspace=workspace, title="Cloud chapter", chapter="1")
        db.add_all([user, workspace, project])
        db.flush()

        stale = StoryAnalysis(
            project_id=project.id,
            analysis_run_id="stale-280-panel-analysis",
            state="RECONCILED",
            created_at=datetime.now(UTC) + timedelta(days=1),
        )
        db.add(stale)
        db.flush()

        panels = tuple(
            module.CloudPanelInput(
                panel_id=f"persist-panel-{index}",
                source_asset_id=f"persist-asset-{index}",
                source_order=index,
                mime_type="image/png",
                payload=f"persist-payload-{index}".encode(),
                panel_bounds=(0, 0, 100, 100),
                source_dimensions=(100, 100),
                strip_region_id=f"persist-region-{index}",
                coverage_map_version="cloud-coverage-v1",
                coverage_map_hash="c" * 64,
            )
            for index in range(3)
        )
        for panel in panels:
            db.add(
                SourceAsset(
                    id=panel.source_asset_id,
                    project_id=project.id,
                    type="image",
                    original_filename=f"{panel.panel_id}.png",
                    storage_key=f"cloud/{panel.panel_id}.png",
                    checksum=panel.source_checksum,
                    original_checksum=panel.source_checksum,
                    original_width=100,
                    original_height=100,
                    width=100,
                    height=100,
                )
            )
        db.flush()

        runner = module.CloudStageRunner(provider=_FakeProvider(), model_identity=_identity(module))
        result = runner.run_chapter(panels)
        validation_flags = []
        analyzer_contract = importlib.import_module("app.services.analyzer_contract")
        original_validate = analyzer_contract.validate_analyzer_output

        def capture_validation(output, **kwargs):
            validation_flags.append(kwargs.get("allow_dialogue_copy", False))
            return original_validate(output, **kwargs)

        monkeypatch.setattr(analyzer_contract, "validate_analyzer_output", capture_validation)
        analysis, script = module.persist_cloud_chapter(
            db,
            project.id,
            panels,
            result,
            model_identity=runner.model_identity,
        )

        assert analysis.state == "SCRIPT_DRAFT"
        assert script.generator == "vision_evidence_v3"
        assert script.editorial_metadata["editorial_review_confirmed"] is False
        assert script.editorial_metadata["narrative_identity"]["profile_id"] == "sharp_friend_v1"
        assert len(analysis.panel_regions) == 3
        assert validation_flags == [False, False]
        assert module.regular_render_allowed(result) is False

def test_persistence_round_trip_retains_701_prepared_panels_and_source_order():
    module = _module()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db import Base
    from app.models import Project, SourceAsset, StoryAnalysis, User, Workspace

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    source_orders = [index if index < 699 else index + 2 for index in range(701)]
    panels = tuple(
        module.CloudPanelInput(
            panel_id=f"roundtrip-panel-{index}",
            source_asset_id=f"roundtrip-asset-{index}",
            source_order=source_order,
            prepared_order=index,
            mime_type="image/png",
            payload=f"roundtrip-payload-{index}".encode(),
            panel_bounds=(0, 0, 100, 100),
            source_dimensions=(100, 100),
            strip_region_id=f"roundtrip-region-{index}",
            coverage_map_version="cloud-coverage-v1",
            coverage_map_hash="c" * 64,
        )
        for index, source_order in enumerate(source_orders)
    )

    with Session(engine) as db:
        user = User(email="roundtrip-owner@example.com", name="Roundtrip Owner", password_hash="test")
        workspace = Workspace(owner=user, name="Roundtrip Workspace")
        project = Project(workspace=workspace, title="701-panel chapter", chapter="1")
        db.add_all([user, workspace, project])
        db.flush()
        db.add_all(
            [
                SourceAsset(
                    id=panel.source_asset_id,
                    project_id=project.id,
                    type="image",
                    original_filename=f"{panel.panel_id}.png",
                    storage_key=f"cloud/{panel.panel_id}.png",
                    checksum=panel.source_checksum,
                    original_checksum=panel.source_checksum,
                    original_width=100,
                    original_height=100,
                    width=100,
                    height=100,
                )
                for panel in panels
            ]
        )
        db.flush()

        runner = module.CloudStageRunner(
            provider=_FakeProvider(),
            model_identity=_identity(module),
        )
        small_result = runner.run_chapter(_panels(module, "roundtrip-fixture"))
        panel_ids = [panel.panel_id for panel in panels]
        visual_rows = []
        for panel in panels:
            visual_row = _visual_row(panel.descriptor())
            visual_row["source_checksum"] = panel.source_checksum
            visual_rows.append(visual_row)
        visual = module.VisualStageResult(
            panels=tuple(visual_rows),
            source_hash="roundtrip-701-source",
            model_identity_hash=small_result.visual.model_identity_hash,
            prompt_version=small_result.visual.prompt_version,
            prompt_sha256=small_result.visual.prompt_sha256,
        )
        continuity = json.loads(json.dumps(small_result.narration.continuity_ledger))
        continuity["chunks"] = [
            {**dict(continuity["chunks"][0]), "panel_ids": panel_ids}
        ]
        for entity in continuity["entities"]:
            entity["panel_ids"] = panel_ids
        for motive in continuity["motives"]:
            motive["evidence_panel_ids"] = panel_ids
        for change in continuity["state_changes"]:
            change["evidence_panel_ids"] = panel_ids
        for link in continuity["causal_links"]:
            link["from_panel_id"] = panel_ids[0]
            link["to_panel_id"] = panel_ids[-1]
            link["evidence_panel_ids"] = panel_ids
        observations = []
        for index, panel in enumerate(panels):
            observation = dict(small_result.narration.observations[index % len(small_result.narration.observations)])
            observation.update(
                {
                    "panel_id": panel.panel_id,
                    "source_asset_id": panel.source_asset_id,
                    "evidence_refs": [panel.panel_id],
                    "source_index": index,
                }
            )
            observations.append(observation)
        evidence_graph = json.loads(json.dumps(small_result.narration.evidence_graph))
        for claim in evidence_graph["claims"]:
            claim["evidence_panel_ids"] = panel_ids
        passages = tuple(
            {**dict(passage), "evidence_panel_ids": panel_ids}
            for passage in small_result.narration.passages
        )
        narration = replace(
            small_result.narration,
            observations=tuple(observations),
            continuity_ledger=continuity,
            evidence_graph=evidence_graph,
            passages=passages,
            visual_evidence_hash=visual.visual_evidence_hash,
        )
        story_map = replace(
            small_result.story_map,
            panel_ids=tuple(panel_ids),
            visual_evidence_hash=visual.visual_evidence_hash,
        )
        result = module.ChapterResult(
            state=module.ChapterState.READY_TO_RENDER,
            visual=visual,
            story_map=story_map,
            narration=narration,
        )
        analysis, _script = module.persist_cloud_chapter(
            db,
            project.id,
            panels,
            result,
            model_identity=runner.model_identity,
        )
        analysis_id = analysis.id
        db.commit()

    with Session(engine) as db:
        persisted = db.get(StoryAnalysis, analysis_id)
        assert persisted is not None
        regions = sorted(
            persisted.panel_regions,
            key=lambda row: row.observation_json["source_index"],
        )
        assert len(regions) == 701
        assert [row.observation_json["source_index"] for row in regions] == list(range(701))
        assert [row.source_order for row in regions] == source_orders
        assert [row.panel_id for row in regions] == [panel.panel_id for panel in panels]
        assert persisted.coverage_manifest_json["panel_ids"] == [panel.panel_id for panel in panels]
        assert persisted.coverage_manifest_json["total_panels"] == 701
        assert persisted.coverage_manifest_json["processed_panels"] == 701
        assert persisted.coverage_manifest_json["processed_canonical_panel_count"] == 701

def test_persistence_failure_rolls_back_uncommitted_analysis_and_regions(monkeypatch):
    module = _module()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db import Base
    from app.models import PanelRegion, Project, SourceAsset, StoryAnalysis, User, Workspace
    from app.services import pipeline

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        user = User(email="rollback-owner@example.com", name="Rollback Owner", password_hash="test")
        workspace = Workspace(owner=user, name="Rollback Workspace")
        project = Project(workspace=workspace, title="Rollback chapter", chapter="1")
        db.add_all([user, workspace, project])
        db.flush()
        panels = _panels(module, "rollback")
        db.add_all(
            [
                SourceAsset(
                    id=panel.source_asset_id,
                    project_id=project.id,
                    type="image",
                    original_filename=f"{panel.panel_id}.png",
                    storage_key=f"cloud/{panel.panel_id}.png",
                    checksum=panel.source_checksum,
                    original_checksum=panel.source_checksum,
                    original_width=100,
                    original_height=100,
                    width=100,
                    height=100,
                )
                for panel in panels
            ]
        )
        db.flush()
        runner = module.CloudStageRunner(
            provider=_FakeProvider(),
            model_identity=_identity(module),
        )
        result = runner.run_chapter(panels)

        def fail_after_flush(*args, **kwargs):
            raise RuntimeError("intentional persistence boundary failure")

        monkeypatch.setattr(pipeline, "generate_script", fail_after_flush)
        with pytest.raises(module.CloudStageError) as caught:
            module.persist_cloud_chapter(
                db,
                project.id,
                panels,
                result,
                model_identity=runner.model_identity,
            )
        assert caught.value.code == "cloud.persistence_failed"
        db.rollback()
        assert db.query(StoryAnalysis).count() == 0
        assert db.query(PanelRegion).count() == 0

def test_batch_cli_summary_does_not_serialize_stage_payloads():
    batch_cli = importlib.import_module("scripts.run_cloud_multimodal_batch")
    simple_namespace = __import__("types").SimpleNamespace
    record = simple_namespace(
        job_id="job-a",
        state=simple_namespace(value="NEEDS_REVIEW"),
        error_code="cloud.narrative_not_grounded",
        review_queue=[{"code": "cloud.narrative_not_grounded"}],
        stage_results={
            "narration": {"spoken_text": "provider text must not print"},
            "usage": {
                "request_count": 2,
                "request_counts": {"narration": 1, "narration_repair": 1},
            },
        },
    )

    summary = batch_cli._safe_job_summary(record)

    encoded = json.dumps(summary, sort_keys=True)
    assert summary["job_id"] == "job-a"
    assert summary["state"] == "NEEDS_REVIEW"
    assert summary["error_code"] == "cloud.narrative_not_grounded"
    assert summary["usage"] == {
        "request_count": 2,
        "request_counts": {"narration": 1, "narration_repair": 1},
    }
    assert summary["review_codes"] == ["cloud.narrative_not_grounded"]
    assert "stage_results" not in summary
    assert "provider text must not print" not in encoded

def test_run_job_repairs_structurally_usable_dialogue_copy_narration(tmp_path):
    module = _module()
    panels = _panels(module)
    panel_ids = [panel.panel_id for panel in panels]
    copied_dialogue = "the crown must not leave this hall tonight"
    setup_runner = module.CloudStageRunner(
        provider=_FakeProvider(),
        model_identity=_identity(module),
        max_attempts=1,
    )
    visual = setup_runner.run_visual_evidence(panels)
    mutated_rows = [dict(row) for row in visual.panels]
    first_row = dict(mutated_rows[0])
    first_observation = dict(first_row["observation"])
    first_observation["dialogue_or_ocr"] = [copied_dialogue]
    first_row["observation"] = first_observation
    mutated_rows[0] = first_row
    visual = replace(visual, panels=tuple(mutated_rows))

    class DialogueCopyResumeProvider(_FakeProvider):
        def __post_init__(self):
            super().__post_init__()
            self.repair_payloads = []

        def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
            if stage == "narration_repair":
                self.calls.append((stage, prompt_version, prompt_sha256))
                self.repair_payloads.append(dict(payload))
                return _provider_position_vector(payload)
            return super().complete_json(
                stage=stage,
                prompt_version=prompt_version,
                prompt_sha256=prompt_sha256,
                prompt_text=prompt_text,
                payload=payload,
            )

    provider = DialogueCopyResumeProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(tmp_path / "dialogue-copy-resume-cache"),
        max_attempts=1,
    )
    runner_prompts = runner.prompts
    story_map = module.StoryMapResult(
        panel_ids=tuple(panel_ids),
        beats=(
            {
                "beat_id": "beat-all",
                "panel_ids": panel_ids,
                "summary": "the visible sequence develops",
            },
        ),
        causal_chain=(),
        claims=tuple(
            {
                "claim_id": f"claim-{index}",
                "claim_type": "fact",
                "text": f"The visible sequence develops claim {index}.",
                "panel_ids": panel_ids,
                "evidence_panel_ids": panel_ids,
                "qualification": "The ordered panels support this reading.",
            }
            for index in range(8)
        ),
        story_map_hash="s" * 64,
        model_identity_hash=_identity(module).identity_hash,
        prompt_version=runner_prompts["story_map"][0],
        prompt_sha256=runner_prompts["story_map"][1],
        visual_evidence_hash=visual.visual_evidence_hash,
    )

    output = _narrative_output("dialogue-copy-resume", panel_ids)
    output["evidence_graph"] = {"claims": [dict(claim) for claim in story_map.claims]}
    output["observations"][0]["dialogue_or_ocr"] = [copied_dialogue]
    filler = "the pressure keeps building here while the sequence turns"

    def sized_text(base: str, target: int) -> str:
        words = base.split()
        while len(words) < target:
            words.extend(filler.split())
        return " ".join(words[:target]) + "."

    passage_texts = [
        sized_text(
            "The opening beat keeps the pressure rising without stalling "
            "while the first visible choice narrows the route",
            30,
        ),
        sized_text(
            "The middle beats widen the stakes as the witness weighs the "
            "safer path against the cost of waiting",
            30,
        ),
        sized_text(
            "Each turn narrows the field so the claim stays tied to what "
            "the sequence shows",
            30,
        ),
        sized_text(
            "The closing beat shows that the crown must not leave this hall "
            "tonight so the guarded choice shifts the outcome",
            30,
        ),
    ]
    for passage, text, passage_index in zip(
        output["script_passages"], passage_texts, range(len(passage_texts)), strict=True
    ):
        passage["text"] = text
        passage["claim_ids"] = [
            f"claim-{passage_index * 2}",
            f"claim-{passage_index * 2 + 1}",
        ]
        passage["evidence_panel_ids"] = list(panel_ids)
    spoken = "\n\n".join(passage_texts)
    duration_metrics = module.script.narration_duration_metrics(spoken, "dramatic")
    candidate = module.NarrationResult(
        spoken_text=spoken,
        display_words=module.derive_display_words(spoken),
        passages=tuple(dict(item) for item in output["script_passages"]),
        ending_kind=str(output["narrative_outline"]["ending_kind"]),
        word_count=int(duration_metrics["word_count"]),
        estimated_duration_s=float(duration_metrics["estimated_duration_s"]),
        qc_report={
            "duration_contract": module.script.narration_duration_contract("dramatic"),
        },
        model_identity_hash=_identity(module).identity_hash,
        prompt_version=runner_prompts["narration"][0],
        prompt_sha256=runner_prompts["narration"][1],
        observations=tuple(dict(item) for item in output["observations"]),
        continuity_ledger=dict(output["continuity_ledger"]),
        evidence_graph=dict(output["evidence_graph"]),
        story_spine=dict(output["narrative_outline"]["story_spine"]),
        visual_evidence_hash=visual.visual_evidence_hash,
    )

    store = module.JsonJobStore(tmp_path / "dialogue-copy-resume-jobs")
    record = module.ChapterJobRecord(
        job_id="dialogue-copy-resume",
        stage_results={
            "visual": visual.as_dict(),
            "story_map": story_map.as_dict(),
            "narration": candidate.as_dict(),
        },
    )
    store.save(record)

    analyzer_contract = importlib.import_module("app.services.analyzer_contract")
    assert module.CloudStageRunner._narration_contract_failures(candidate) == (
        "cloud.narrative_source_dialogue_copy",
    )
    assert 115 <= int(duration_metrics["word_count"]) <= 125
    assert 50.0 <= float(duration_metrics["estimated_duration_s"]) <= 60.0

    resumed = module.CloudBatchService(runner=runner, store=store).run_job(
        "dialogue-copy-resume",
        panels,
    )

    assert resumed.state == module.ChapterState.READY_TO_RENDER
    assert [call[0] for call in provider.calls] == ["narration_repair"]
    assert len(provider.repair_payloads) == 1
    persisted = store.load("dialogue-copy-resume")
    assert persisted is not None
    repaired = module.NarrationResult.from_dict(
        persisted.stage_results["narration"]
    )
    assert 115 <= repaired.word_count <= 125
    assert not analyzer_contract.contains_source_dialogue_copy(
        repaired.observations,
        repaired.passages,
    )

    # A current visual-repair checkpoint owns its own bounded repair path.
    # Even if the base narration contract sees a condition it would normally
    # repair, resume must not route this checkpoint through narration_repair.
    provider.calls.clear()
    provider.repair_payloads.clear()
    repair_prompt = runner_prompts["visual_narrative_repair"]
    visual_checkpoint = replace(
        candidate,
        prompt_version=repair_prompt[0],
        prompt_sha256=repair_prompt[1],
    )
    checkpoint_store = module.JsonJobStore(tmp_path / "visual-repair-checkpoint-jobs")
    checkpoint_store.save(
        module.ChapterJobRecord(
            job_id="visual-repair-checkpoint",
            stage_results={
                "visual": visual.as_dict(),
                "story_map": story_map.as_dict(),
                "narration": visual_checkpoint.as_dict(),
                "visual_repair": {
                    "contract_version": module.visual_narrative_repair.REPAIR_CONTRACT_VERSION,
                    "prompt_version": repair_prompt[0],
                    "prompt_sha256": repair_prompt[1],
                    "model_identity_hash": runner.model_identity.identity_hash,
                    "publish_allowed": False,
                },
            },
        )
    )
    checkpoint_result = module.CloudBatchService(
        runner=runner, store=checkpoint_store
    ).run_job("visual-repair-checkpoint", panels)
    assert checkpoint_result.state == module.ChapterState.READY_TO_RENDER
    assert provider.calls == []

def test_later_gate_failure_persists_success_shape_metrics(tmp_path):
    module = _module()
    runner, candidate, _visual, story_map = _immutable_slot_fixture(module)
    registry = runner._build_narration_repair_position_registry(candidate, story_map)
    counts = [row["word_budget"] for row in registry["positions"]]
    raw = {
        "rewrites": [
            _position_rewrite_text(count, f"durable{index}_")
            for index, count in enumerate(counts)
        ]
    }
    reconciled = runner._reconcile_narration_repair_vector(raw, registry, candidate)
    runner.last_response_shape_metrics = dict(
        reconciled.pop("_response_shape_metrics")
    )

    store = module.JsonJobStore(tmp_path)
    service = module.CloudBatchService(runner=runner, store=store)
    record = module.ChapterJobRecord(job_id="later-gate-shape")
    failure = module.CloudStageError(
        "cloud.narrative_word_count_out_of_range",
        reviewable=True,
    )
    failure_metrics = runner._response_shape_metrics_for_failure(failure.code)
    assert failure_metrics["failed_code"] == failure.code
    assert runner.last_response_shape_metrics["failed_code"] == failure.code
    service._record_failure(record, failure)

    persisted = store.load("later-gate-shape")
    assert persisted is not None
    metrics = persisted.review_queue[-1]["safe_metadata"]
    assert metrics["slot_order_hash"] == registry["slot_order_hash"]
    assert metrics["array_count"] == len(counts)
    assert metrics["per_position_word_counts"] == counts
    assert metrics["failed_code"] == failure.code
    assert metrics["failed_predicate"] == failure.code
    assert "durable0_word" not in json.dumps(metrics)

def test_stream_finish_rejects_partial_final_batch_and_persists_metrics():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-partial"))
    )

    class _DropOneProvider(_FakeProvider):
        def observe(self, request):
            rows = super().observe(request)
            return [
                row
                for row in rows
                if row.get("panel_id") != "stream-partial-panel-2"
            ]

    provider = _DropOneProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.MemoryStageCache(),
        max_attempts=1,
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
    assert [item["panel_id"] for item in result.rejected_panels] == [
        "stream-partial-panel-2"
    ]
    assert runner.last_visual_stream_metrics["submitted_panel_count"] == len(panels)
    assert runner.last_visual_stream_metrics["accepted_panel_count"] == len(panels) - 1
    assert runner.last_visual_stream_metrics["missing_panel_count"] == 0
    assert runner.last_visual_stream_metrics["rejected_panel_count"] == 1
    assert runner.last_visual_stream_metrics["rejected_panel_ids"] == [
        "stream-partial-panel-2"
    ]

def test_stream_targets_singleton_repair_for_batch_rows_without_visible_facts():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-singleton-repair"))
    )

    class _BatchNeedsSingletonRepairProvider(_FakeProvider):
        def observe(self, request):
            rows = super().observe(request)
            if len(request.panels) > 1:
                for row in rows:
                    row["visible_facts"] = []
            return rows

    provider = _BatchNeedsSingletonRepairProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.MemoryStageCache(),
        max_attempts=1,
    )
    stream = runner.start_visual_evidence_stream(
        queue_size=1,
        max_panels=len(panels),
        max_estimated_bytes=10_000_000,
    )
    for panel in panels:
        stream.submit(panel)

    result = stream.finish(panels)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels)
    assert len(provider.calls) == 1 + len(panels)
    assert runner.last_visual_stream_metrics["visual_failure_predicates"] == {
        "visible_facts_nonempty": len(panels)
    }

def test_stream_repairs_multiple_omitted_batch_rows_as_singletons():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-omitted-singletons"))
    )
    omitted_ids = {panels[-2].panel_id, panels[-1].panel_id}

    class _BatchOmitsRowsProvider(_FakeProvider):
        def __init__(self):
            super().__init__()
            self.request_sizes = []

        def observe(self, request):
            self.request_sizes.append(len(request.panels))
            rows = super().observe(request)
            if len(request.panels) > 1:
                return [
                    row for row in rows if row.get("panel_id") not in omitted_ids
                ]
            return rows

    provider = _BatchOmitsRowsProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.MemoryStageCache(),
        max_attempts=2,
    )
    stream = runner.start_visual_evidence_stream(
        queue_size=1,
        max_panels=len(panels),
        max_estimated_bytes=10_000_000,
    )
    for panel in panels:
        stream.submit(panel)

    result = stream.finish(panels)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels)
    assert provider.request_sizes == [len(panels), 1, 1]
    assert runner.last_visual_stream_metrics["missing_panel_count"] == 0
    assert runner.last_visual_stream_metrics["retry_count"] == 2

def test_run_project_streams_preparation_and_passes_one_precomputed_visual_result(monkeypatch):
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-entrypoint"))
    )
    provider = _FakeProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.MemoryStageCache(),
        max_attempts=1,
    )

    class Store:
        def __init__(self):
            self.saved = []

        def load(self, _project_id):
            return None

        def save(self, record):
            self.saved.append(record)

    service = module.CloudBatchService.__new__(module.CloudBatchService)
    service.runner = runner
    service.store = Store()
    service.review_root = None
    prepared_kwargs = {}
    captured = {}

    def fake_prepare(_db, _project_id, **kwargs):
        prepared_kwargs.update(kwargs)
        sink = kwargs["panel_sink"]
        for panel in panels:
            sink(panel)
        return panels, {"status": "RECONCILED"}

    monkeypatch.setattr(module, "prepare_project_panels", fake_prepare)
    monkeypatch.setattr(
        module,
        "_build_project_prepared_manifest",
        lambda *_args, **_kwargs: {"manifest": "streamed"},
    )

    def fake_run_job(_job_id, passed_panels, *, precomputed_visual=None):
        captured["panels"] = tuple(passed_panels)
        captured["visual"] = precomputed_visual
        return module.ChapterJobRecord(
            job_id="stream-entrypoint",
            state=module.ChapterState.NEEDS_REVIEW,
            error_code="test.stop_after_visual",
            stage_results={
                "visual": precomputed_visual.as_dict()
                if precomputed_visual is not None
                else {},
            },
        )

    monkeypatch.setattr(service, "run_job", fake_run_job)
    result = service.run_project(object(), "stream-entrypoint")

    assert callable(prepared_kwargs["panel_sink"])
    assert captured["visual"] is not None
    assert captured["visual"].panel_ids == tuple(panel.panel_id for panel in panels)
    assert captured["panels"] == panels
    assert result.error_code == "test.stop_after_visual"
    assert runner.last_visual_stream_metrics["accepted_panel_count"] == len(panels)
    assert runner.last_visual_stream_metrics["request_count"] == len(provider.calls)

def test_stream_skips_rebatch_for_semantic_attention_misses_with_two_attempts():
    module = _module()
    panels = tuple(
        replace(panel, prepared_order=index)
        for index, panel in enumerate(_panels(module, "stream-direct-singleton-repair"))
    )

    class _BatchNeedsSingletonRepairProvider(_FakeProvider):
        def observe(self, request):
            rows = super().observe(request)
            if len(request.panels) > 1:
                for row in rows:
                    row["visible_facts"] = []
            return rows

    provider = _BatchNeedsSingletonRepairProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.MemoryStageCache(),
        max_attempts=2,
    )
    result = runner.run_visual_evidence(panels)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels)
    assert len(provider.calls) == 1 + len(panels)

def test_repair_only_identity_bump_reuses_persisted_visual_without_rehash():
    module = _module()
    panels = _panels(module, "repair-only-visual")
    base = _identity(module)
    current = replace(base, prompt_versions=dict(base.prompt_versions) | {"visual_narrative_repair": module.CURRENT_VISUAL_REPAIR_PROMPT_VERSION})
    legacy = replace(current, prompt_versions=dict(current.prompt_versions) | {"visual_narrative_repair": module.LEGACY_VISUAL_REPAIR_PROMPT_VERSION})
    runner = module.CloudStageRunner(provider=_FakeProvider(), model_identity=current)
    prompt = runner.prompts["visual"]
    rows = tuple(_visual_row(panel.descriptor()) | {"source_asset_id": panel.source_asset_id, "source_order": panel.source_order, "source_checksum": panel.source_checksum} for panel in panels)
    cached = module.VisualStageResult(panels=rows, source_hash=module._visual_source_hash(panels), model_identity_hash=legacy.identity_hash, prompt_version=prompt[0], prompt_sha256=prompt[1], cache_identity_version=module.VISUAL_CACHE_IDENTITY_VERSION, panel_identity_hashes=module._visual_panel_identity_hashes(panels))
    before_hash = cached.visual_evidence_hash
    migrated = module._migrate_visual_cache_identity(cached.as_dict(), panels, model_identity=current, prompt=prompt)
    assert migrated is not None
    reused = module.VisualStageResult.from_dict(migrated)
    assert reused.model_identity_hash == legacy.identity_hash
    assert reused.visual_evidence_hash == before_hash

