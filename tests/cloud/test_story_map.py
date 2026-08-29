"""Cloud multimodal regression tests grouped by responsibility."""

from __future__ import annotations

from tests.cloud.mass_support import (
    _CausalMapClaimsOnlyProvider,
    _FakeProvider,
    _identity,
    _module,
    _narrative_output,
    _panels,
    _visual_row,
    pytest,
    replace,
)


def test_narration_reuses_validated_causal_claims_when_graph_is_omitted():
    module = _module()
    provider = _CausalMapClaimsOnlyProvider()
    panels = tuple(
        replace(
            panel,
            panel_bounds=(0, 0, 100, 100),
            source_dimensions=(100, 100),
            strip_region_id=panel.panel_id,
            coverage_map_version="coverage-v1",
            coverage_map_hash="b" * 64,
        )
        for panel in _panels(module)
    )
    runner = module.CloudStageRunner(provider=provider, model_identity=_identity(module))

    visual = runner.run_visual_evidence(panels)
    story_map = runner.run_story_map(visual)
    result = runner.run_narration(visual, story_map, panels=panels)

    assert {claim["claim_id"] for claim in result.evidence_graph["claims"]} <= {
        claim["claim_id"] for claim in story_map.claims
    }
    assert result.evidence_graph["claims"]

def test_causal_map_prompt_declares_exact_reconciled_object_fields():
    module = _module()

    version, digest, prompt = module._load_causal_prompt()

    assert version == "cloud-causal-map-v2"
    assert len(digest) == 64
    assert "beat_id, panel_ids, summary" in prompt
    assert "at least five ordered" in prompt
    assert "from_beat, to_beat, reason" in prompt
    assert "each claim has claim_id," in prompt
    assert "text, panel_ids, qualification" in prompt

def test_unknown_visual_geometry_uses_conservative_fallback_before_story_mapping():
    module = _module()
    provider = _FakeProvider(unknown_visual=True)
    runner = module.CloudStageRunner(provider=provider, model_identity=_identity(module))

    result = runner.run_chapter(_panels(module))

    assert result.state == module.ChapterState.READY_TO_RENDER
    assert result.visual.reconciled is True
    assert result.visual.panel_ids == tuple(panel.panel_id for panel in _panels(module))
    assert len([call for call in provider.calls if call[0] == "visual"]) == (
        1 + len(_panels(module))
    )
    assert all(
        row.get("fallback_mode") == "conservative_full_panel_v1"
        and row["visual_evidence"]["evidence_source"] == "conservative_full_panel_v1"
        for row in result.visual.panels
    )

def test_transient_invalid_story_map_is_retried_atomically():
    module = _module()
    provider = _FakeProvider(transient_story_map_invalid_count=1)
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        max_attempts=2,
    )
    visual = runner.run_visual_evidence(_panels(module))

    result = runner.run_story_map(visual)

    assert result.panel_ids == visual.panel_ids
    assert len([call for call in provider.calls if call[0] == "story_map"]) == 2

def test_story_map_uses_bounded_ordered_chunks_and_resumes_from_durable_chunk_cache(tmp_path):
    module = _module()
    import threading
    import time

    panels = tuple(
        module.CloudPanelInput(
            panel_id=f"long-panel-{index:04d}",
            source_asset_id=f"long-asset-{index:04d}",
            source_order=index + 1,
            mime_type="image/png",
            payload=f"long-payload-{index}".encode(),
        )
        for index in range(721)
    )
    visual = module.VisualStageResult(
        panels=tuple(_visual_row(panel.descriptor()) for panel in panels),
        source_hash="long-visual-source",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )

    class ChunkProvider(_FakeProvider):
        def __post_init__(self):
            super().__post_init__()
            self.story_sizes = []
            self.active = 0
            self.max_active = 0
            self._lock = threading.Lock()

        def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
            if stage != "story_map":
                return super().complete_json(
                    stage=stage,
                    prompt_version=prompt_version,
                    prompt_sha256=prompt_sha256,
                    prompt_text=prompt_text,
                    payload=payload,
                )
            with self._lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                self.story_sizes.append((payload["batch_index"], len(payload["panel_ids"])))
            try:
                time.sleep(0.01)
                return super().complete_json(
                    stage=stage,
                    prompt_version=prompt_version,
                    prompt_sha256=prompt_sha256,
                    prompt_text=prompt_text,
                    payload=payload,
                )
            finally:
                with self._lock:
                    self.active -= 1

    cache_root = tmp_path / "stage-cache"
    first_provider = ChunkProvider()
    first_runner = module.CloudStageRunner(
        provider=first_provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(cache_root),
        max_attempts=1,
    )
    first = first_runner.run_story_map(visual)

    assert first.panel_ids == tuple(panel.panel_id for panel in panels)
    assert sorted(first_provider.story_sizes) == [
        (0, 180), (1, 180), (2, 180), (3, 180), (4, 1)
    ]
    assert first_provider.max_active <= 4
    assert len(first.beats) == 10

    second_provider = ChunkProvider()
    second_runner = module.CloudStageRunner(
        provider=second_provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(cache_root),
        max_attempts=1,
    )
    second = second_runner.run_story_map(visual)
    assert second == first
    assert second_provider.story_sizes == []

def test_story_map_resume_reuses_completed_chunks_after_one_chunk_failure(tmp_path):
    module = _module()
    panels = tuple(
        module.CloudPanelInput(
            panel_id=f"resume-panel-{index:04d}",
            source_asset_id=f"resume-asset-{index:04d}",
            source_order=index + 1,
            mime_type="image/png",
            payload=f"resume-payload-{index}".encode(),
        )
        for index in range(721)
    )
    visual = module.VisualStageResult(
        panels=tuple(_visual_row(panel.descriptor()) for panel in panels),
        source_hash="resume-visual-source",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )

    class PartialProvider(_FakeProvider):
        def __init__(self, failing_batch=None):
            super().__init__()
            self.failing_batch = failing_batch
            self.story_batches = []

        def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
            if stage == "story_map":
                batch = int(payload["batch_index"])
                self.story_batches.append(batch)
                if self.failing_batch == batch:
                    self.failing_batch = None
                    raise RuntimeError("bounded provider failure")
            return super().complete_json(
                stage=stage,
                prompt_version=prompt_version,
                prompt_sha256=prompt_sha256,
                prompt_text=prompt_text,
                payload=payload,
            )

    cache_root = tmp_path / "partial-cache"
    with pytest.raises(module.CloudStageError) as caught:
        module.CloudStageRunner(
            provider=PartialProvider(failing_batch=1),
            model_identity=_identity(module),
            cache=module.FileStageCache(cache_root),
            max_attempts=1,
        ).run_story_map(visual)
    assert caught.value.code == "cloud.provider_request_failed"

    recovering = PartialProvider()
    result = module.CloudStageRunner(
        provider=recovering,
        model_identity=_identity(module),
        cache=module.FileStageCache(cache_root),
        max_attempts=1,
    ).run_story_map(visual)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels)
    assert recovering.story_batches == [1]

def test_story_map_accepts_provider_ordered_beats_alias(tmp_path):
    module = _module()
    panels = _panels(module)
    visual = module.VisualStageResult(
        panels=tuple(_visual_row(panel.descriptor()) for panel in panels),
        source_hash="ordered-beats-source",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )

    class OrderedBeatsProvider(_FakeProvider):
        def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
            result = super().complete_json(
                stage=stage,
                prompt_version=prompt_version,
                prompt_sha256=prompt_sha256,
                prompt_text=prompt_text,
                payload=payload,
            )
            if stage == "story_map":
                result["ordered_beats"] = result.pop("beats")
            return result

    provider = OrderedBeatsProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(tmp_path / "ordered-beats-cache"),
        max_attempts=1,
    )

    result = runner.run_story_map(visual)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels)
    assert len(result.beats) == 2

def test_story_map_splits_incomplete_large_chunk_without_dropping_coverage(tmp_path):
    module = _module()
    panels = tuple(
        module.CloudPanelInput(
            panel_id=f"fallback-panel-{index:03d}",
            source_asset_id=f"fallback-asset-{index:03d}",
            source_order=index + 1,
            mime_type="image/png",
            payload=f"fallback-payload-{index}".encode(),
        )
        for index in range(61)
    )
    visual = module.VisualStageResult(
        panels=tuple(_visual_row(panel.descriptor()) for panel in panels),
        source_hash="fallback-source",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )

    class IncompleteLargeProvider(_FakeProvider):
        def __post_init__(self):
            super().__post_init__()
            self.story_sizes = []

        def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
            if stage == "story_map":
                self.story_sizes.append(len(payload["panel_ids"]))
            result = super().complete_json(
                stage=stage,
                prompt_version=prompt_version,
                prompt_sha256=prompt_sha256,
                prompt_text=prompt_text,
                payload=payload,
            )
            if stage == "story_map" and len(payload["panel_ids"]) > 60:
                result["ordered_beats"] = result.pop("beats")
                for beat in result["ordered_beats"]:
                    beat["panel_ids"] = [payload["panel_ids"][0]]
                for claim in result["claims"]:
                    claim["panel_ids"] = [payload["panel_ids"][0]]
            return result

    provider = IncompleteLargeProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(tmp_path / "fallback-cache"),
        max_attempts=1,
    )

    result = runner.run_story_map(visual)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels)
    assert sorted(provider.story_sizes) == [1, 60, 61]

def test_story_map_reduces_incomplete_60_panel_chunk_to_30_without_dropping_coverage(tmp_path):
    module = _module()
    panels = tuple(
        module.CloudPanelInput(
            panel_id=f"nested-fallback-panel-{index:03d}",
            source_asset_id=f"nested-fallback-asset-{index:03d}",
            source_order=index + 1,
            mime_type="image/png",
            payload=f"nested-fallback-payload-{index}".encode(),
        )
        for index in range(61)
    )
    visual = module.VisualStageResult(
        panels=tuple(_visual_row(panel.descriptor()) for panel in panels),
        source_hash="nested-fallback-source",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )

    class IncompleteMediumProvider(_FakeProvider):
        def __post_init__(self):
            super().__post_init__()
            self.story_sizes = []

        def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
            if stage == "story_map":
                self.story_sizes.append(len(payload["panel_ids"]))
            result = super().complete_json(
                stage=stage,
                prompt_version=prompt_version,
                prompt_sha256=prompt_sha256,
                prompt_text=prompt_text,
                payload=payload,
            )
            if stage == "story_map" and len(payload["panel_ids"]) > 30:
                result["ordered_beats"] = result.pop("beats")
                for beat in result["ordered_beats"]:
                    beat["panel_ids"] = [payload["panel_ids"][0]]
                for claim in result["claims"]:
                    claim["panel_ids"] = [payload["panel_ids"][0]]
            return result

    provider = IncompleteMediumProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(tmp_path / "nested-fallback-cache"),
        max_attempts=1,
    )

    result = runner.run_story_map(visual)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels)
    assert sorted(provider.story_sizes) == [1, 15, 15, 15, 15, 60, 61]

def test_story_map_reduces_provider_partial_coverage_below_current_minimum(tmp_path):
    module = _module()
    panels = tuple(
        module.CloudPanelInput(
            panel_id=f"small-fallback-panel-{index:03d}",
            source_asset_id=f"small-fallback-asset-{index:03d}",
            source_order=index + 1,
            mime_type="image/png",
            payload=f"small-fallback-payload-{index}".encode(),
        )
        for index in range(31)
    )
    visual = module.VisualStageResult(
        panels=tuple(_visual_row(panel.descriptor()) for panel in panels),
        source_hash="small-fallback-source",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )

    class IncompleteUntilSmallProvider(_FakeProvider):
        def __post_init__(self):
            super().__post_init__()
            self.story_sizes = []

        def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
            if stage == "story_map":
                self.story_sizes.append(len(payload["panel_ids"]))
            result = super().complete_json(
                stage=stage,
                prompt_version=prompt_version,
                prompt_sha256=prompt_sha256,
                prompt_text=prompt_text,
                payload=payload,
            )
            if stage == "story_map" and len(payload["panel_ids"]) > 15:
                for beat in result["beats"]:
                    beat["panel_ids"] = [payload["panel_ids"][0]]
                for claim in result["claims"]:
                    claim["panel_ids"] = [payload["panel_ids"][0]]
            return result

    provider = IncompleteUntilSmallProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(tmp_path / "small-fallback-cache"),
        max_attempts=1,
    )

    result = runner.run_story_map(visual)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels)
    assert sorted(provider.story_sizes) == [1, 15, 15, 31]
    assert max(provider.story_sizes) == 31

def test_story_map_recovers_when_fifteen_panel_response_is_still_partial(tmp_path):
    module = _module()
    panels = tuple(
        module.CloudPanelInput(
            panel_id=f"five-panel-fallback-{index:03d}",
            source_asset_id=f"five-panel-asset-{index:03d}",
            source_order=index + 1,
            mime_type="image/png",
            payload=f"five-panel-payload-{index}".encode(),
        )
        for index in range(16)
    )
    visual = module.VisualStageResult(
        panels=tuple(_visual_row(panel.descriptor()) for panel in panels),
        source_hash="five-panel-fallback-source",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )

    class IncompleteUntilFiveProvider(_FakeProvider):
        def __post_init__(self):
            super().__post_init__()
            self.story_sizes = []

        def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
            if stage == "story_map":
                self.story_sizes.append(len(payload["panel_ids"]))
            result = super().complete_json(
                stage=stage,
                prompt_version=prompt_version,
                prompt_sha256=prompt_sha256,
                prompt_text=prompt_text,
                payload=payload,
            )
            if stage == "story_map" and len(payload["panel_ids"]) > 5:
                for beat in result["beats"]:
                    beat["panel_ids"] = [payload["panel_ids"][0]]
                for claim in result["claims"]:
                    claim["panel_ids"] = [payload["panel_ids"][0]]
            return result

    provider = IncompleteUntilFiveProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(tmp_path / "five-panel-fallback-cache"),
        max_attempts=1,
    )

    result = runner.run_story_map(visual)

    assert result.panel_ids == tuple(panel.panel_id for panel in panels)
    assert sorted(provider.story_sizes) == [1, 5, 5, 5, 15, 16]

def test_narration_uses_one_final_reduce_call_after_editorial_selection(tmp_path):
    module = _module()
    panels = tuple(
        module.CloudPanelInput(
            panel_id=f"narration-fallback-panel-{index:03d}",
            source_asset_id=f"narration-fallback-asset-{index:03d}",
            source_order=index + 1,
            mime_type="image/png",
            payload=f"narration-fallback-payload-{index}".encode(),
            panel_bounds=(0, 0, 100, 100),
            source_dimensions=(100, 100),
            strip_region_id=f"narration-fallback-region-{index}",
            coverage_map_version="coverage-v1",
            coverage_map_hash="b" * 64,
        )
        for index in range(181)
    )
    visual_rows = []
    for panel in panels:
        observation = _visual_row(panel.descriptor())
        visual_rows.append(
            {
                "panel_id": panel.panel_id,
                "source_asset_id": panel.source_asset_id,
                "source_order": panel.source_order,
                "source_checksum": panel.source_checksum,
                "observation": observation,
                "visual_evidence": observation["visual_evidence"],
                "evidence_hash": "",
            }
        )
    visual = module.VisualStageResult(
        panels=tuple(visual_rows),
        source_hash="narration-fallback-source",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )
    panel_ids = [panel.panel_id for panel in panels]
    story_map = module.StoryMapResult(
        panel_ids=tuple(panel_ids),
        beats=({"beat_id": "beat-all", "panel_ids": panel_ids, "summary": "the visible sequence develops"},),
        causal_chain=({"from_beat": "beat-all", "to_beat": "beat-all", "reason": "the visible sequence continues"},),
        claims=({
            "claim_id": "claim-all",
            "text": "The visible sequence develops.",
            "panel_ids": panel_ids,
            "qualification": "The ordered panels support this reading.",
        },),
        story_map_hash="s" * 64,
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="cloud-causal-map-v2",
        prompt_sha256="c" * 64,
    )

    class EditorialReduceProvider(_FakeProvider):
        def __post_init__(self):
            super().__post_init__()
            self.narration_sizes = []

        def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
            if stage == "narration":
                panel_ids = list(payload["panel_ids"])
                self.narration_sizes.append(len(panel_ids))
                output = _narrative_output("cloud", panel_ids[:3])
                for passage in output["script_passages"]:
                    passage["evidence_panel_ids"] = panel_ids
                for claim in output["evidence_graph"]["claims"]:
                    claim["evidence_panel_ids"] = panel_ids
                return output
            return super().complete_json(
                stage=stage,
                prompt_version=prompt_version,
                prompt_sha256=prompt_sha256,
                prompt_text=prompt_text,
                payload=payload,
            )

    provider = EditorialReduceProvider()
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(tmp_path / "narration-fallback-cache"),
        max_attempts=1,
    )

    result = runner.run_narration(visual, story_map, panels=panels)

    assert len(result.observations) == len(panels)
    assert provider.narration_sizes == [4]
    assert result.qc_report["narration_topology"] == "chapter_evidence_reduce_v1"
    assert result.qc_report["editorial_selection"]["selection_hash"]

def test_editorial_selection_is_bounded_ordered_and_panel_keyed():
    module = _module()
    panels = tuple(
        module.CloudPanelInput(
            panel_id=f"selection-panel-{index:03d}",
            source_asset_id=f"selection-asset-{index:03d}",
            source_order=index + 1,
            mime_type="image/png",
            payload=f"selection-payload-{index}".encode(),
        )
        for index in range(240)
    )
    visual = module.VisualStageResult(
        panels=tuple(_visual_row(panel.descriptor()) for panel in panels),
        source_hash="selection-source",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="balloon-free-visual-evidence-v1",
        prompt_sha256="v" * 64,
    )
    beats = []
    claims = []
    for beat_index in range(24):
        panel_ids = [
            panel.panel_id
            for panel in panels[beat_index * 10:(beat_index + 1) * 10]
        ]
        beat_id = f"selection-beat-{beat_index:02d}"
        claim_id = f"selection-claim-{beat_index:02d}"
        beats.append({
            "beat_id": beat_id,
            "panel_ids": panel_ids,
            "summary": f"beat {beat_index}",
            "state_changes": [f"change {beat_index}"],
        })
        claims.append({
            "claim_id": claim_id,
            "claim_type": "fact",
            "text": f"fact {beat_index}",
            "qualification": "visible evidence supports this",
            "evidence_panel_ids": panel_ids[:2],
        })
    story_map = module.StoryMapResult(
        panel_ids=tuple(panel.panel_id for panel in panels),
        beats=tuple(beats),
        causal_chain=tuple(
            {
                "from_beat": beats[index]["beat_id"],
                "to_beat": beats[index + 1]["beat_id"],
                "reason": "ordered consequence",
            }
            for index in range(len(beats) - 1)
        ),
        claims=tuple(claims),
        story_map_hash="story-selection",
        model_identity_hash=_identity(module).identity_hash,
        prompt_version="cloud-causal-map-v2",
        prompt_sha256="c" * 64,
        visual_evidence_hash=visual.visual_evidence_hash,
    )

    selection = module.select_editorial_beats(visual, story_map, target_count=10)

    assert 8 <= len(selection.beat_ids) <= 12
    assert len(selection.panel_ids) == len(set(selection.panel_ids))
    assert selection.beat_ids == tuple(
        beat_id
        for beat_id in selection.beat_ids
    )
    visual_order = {panel.panel_id: index for index, panel in enumerate(panels)}
    assert tuple(sorted(selection.panel_ids, key=visual_order.get)) == selection.panel_ids
    assert set(selection.claim_ids) <= {claim["claim_id"] for claim in claims}
    assert selection.selection_hash

