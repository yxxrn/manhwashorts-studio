from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from app.services import pipeline, reference_profile, render, subtitle_karaoke, visual_scoring


def _evidence(panel_id: str, asset_id: str, source_order: int):
    return visual_scoring.PanelVisualEvidence(
        contract_version="COLOR_AGNOSTIC_BALLOON_FREE_V1",
        panel_id=panel_id,
        source_asset_id=asset_id,
        source_order=source_order,
        balloon_regions=(),
        protected_regions=(),
        balloon_mask_status="known_empty",
        mask_confidence=0.96,
        evidence_source="vision_geometry_v1",
        mask_reason="provider confirmed no speech balloon geometry",
    )


def _region(panel_id: str, region_id: str, asset_id: str, source_order: int, bounds, checksum: str):
    evidence = _evidence(panel_id, asset_id, source_order)
    return SimpleNamespace(
        id=region_id,
        panel_id=panel_id,
        source_asset_id=asset_id,
        source_order=source_order,
        source_asset_checksum=checksum,
        bounds_json={
            "x": bounds[0],
            "y": bounds[1],
            "width": bounds[2] - bounds[0],
            "height": bounds[3] - bounds[1],
        },
        observation_json={"visual_evidence": visual_scoring.panel_visual_evidence_json(evidence)},
    )


def _candidate(asset_id: str, source_order: int, signature: str):
    return visual_scoring.PanelCandidate(
        asset_id=asset_id,
        order_index=source_order,
        features=visual_scoring.VisualFeatures(
            face_visibility=0.9,
            action_pose=0.8,
            dramatic_composition=0.9,
            focal_points=((0.5, 0.5),),
            visual_signature=signature,
        ),
        visual_score=1.0,
        semantic_score=1.0,
    )


def _crop(color: tuple[int, int, int], mark: bool) -> Image.Image:
    image = Image.new("RGB", (100, 200), color)
    if mark:
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 20, 85, 80), fill=(250, 250, 250))
        draw.line((5, 190, 95, 100), fill=(0, 0, 0), width=5)
    return image


def _builder_inputs():
    regions = (
        _region("panel-a", "region-a", "asset-a", 3, (0, 0, 100, 200), "asset-a-checksum"),
        _region("panel-b", "region-b", "asset-a", 3, (100, 0, 200, 200), "asset-a-checksum"),
    )
    crops = {
        "region-a": _crop((40, 60, 100), True),
        "region-b": _crop((110, 40, 40), False),
    }
    candidates = {
        "region-a": _candidate("asset-a", 3, "a"),
        "region-b": _candidate("asset-a", 3, "b"),
    }
    return regions, crops, candidates


def test_panel_candidate_builder_keeps_same_asset_regions_distinct():
    builder = pipeline._build_reference_panel_fallback_candidates
    regions, crops, candidates = _builder_inputs()
    result = builder(
        panel_regions=regions,
        panel_candidates_by_region_id=candidates,
        panel_crops_by_region_id=crops,
        section_evidence_panel_ids={"hook": ("panel-a", "panel-b")},
        section_citations={},
        beats_by_section={"hook": ("action",)},
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
    )
    assert [candidate.panel_region_id for candidate in result] == ["region-a", "region-b"]
    assert {candidate.panel_id for candidate in result} == {"panel-a", "panel-b"}
    assert len({candidate.border_mask.mask_sha256 for candidate in result}) == 2
    assert all(candidate.panel_size == (100, 200) for candidate in result)


def test_panel_candidate_builder_fans_out_integer_citation_by_source_order():
    builder = pipeline._build_reference_panel_fallback_candidates
    regions, crops, candidates = _builder_inputs()
    result = builder(
        panel_regions=regions,
        panel_candidates_by_region_id=candidates,
        panel_crops_by_region_id=crops,
        section_evidence_panel_ids={"hook": ()},
        section_citations={"hook": (3,)},
        beats_by_section={"hook": ("action",)},
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
    )
    assert {candidate.panel_id for candidate in result} == {"panel-a", "panel-b"}
    assert all("hook" in candidate.eligible_sections for candidate in result)


def test_review_only_unknown_geometry_uses_conservative_full_panel_when_facts_exist():
    region = _region("panel-unknown", "region-unknown", "asset-a", 3, (0, 0, 100, 200), "asset-a-checksum")
    unknown = visual_scoring.unknown_visual_evidence(
        panel_id="panel-unknown",
        source_asset_id="asset-a",
        source_order=3,
        reason="provider geometry was unavailable",
    )
    region.observation_json = {
        "visible_facts": ["a figure stands near a gate"],
        "visual_evidence": visual_scoring.panel_visual_evidence_json(unknown),
    }
    candidates = {"region-unknown": _candidate("asset-a", 3, "unknown")}
    crops = {"region-unknown": _crop((40, 60, 100), True)}

    assert pipeline._build_reference_panel_fallback_candidates(
        panel_regions=(region,),
        panel_candidates_by_region_id=candidates,
        panel_crops_by_region_id=crops,
        section_evidence_panel_ids={"hook": ("panel-unknown",)},
        section_citations={},
        beats_by_section={"hook": ("action",)},
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
    ) == ()

    result = pipeline._build_reference_panel_fallback_candidates(
        panel_regions=(region,),
        panel_candidates_by_region_id=candidates,
        panel_crops_by_region_id=crops,
        section_evidence_panel_ids={"hook": ("panel-unknown",)},
        section_citations={},
        beats_by_section={"hook": ("action",)},
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        allow_conservative_full_panel=True,
    )

    assert len(result) == 1
    assert result[0].visual_evidence.evidence_source == "conservative_full_panel_v1"
    assert [roi.roi_label for roi in result[0].roi_alternatives] == ["conservative_full_panel"]


def test_review_only_unknown_geometry_without_visible_facts_stays_excluded():
    region = _region("panel-unknown", "region-unknown", "asset-a", 3, (0, 0, 100, 200), "asset-a-checksum")
    unknown = visual_scoring.unknown_visual_evidence(
        panel_id="panel-unknown",
        source_asset_id="asset-a",
        source_order=3,
        reason="provider geometry was unavailable",
    )
    region.observation_json = {
        "visible_facts": [],
        "visual_evidence": visual_scoring.panel_visual_evidence_json(unknown),
    }

    result = pipeline._build_reference_panel_fallback_candidates(
        panel_regions=(region,),
        panel_candidates_by_region_id={"region-unknown": _candidate("asset-a", 3, "unknown")},
        panel_crops_by_region_id={"region-unknown": _crop((40, 60, 100), True)},
        section_evidence_panel_ids={"hook": ("panel-unknown",)},
        section_citations={},
        beats_by_section={"hook": ("action",)},
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        allow_conservative_full_panel=True,
    )

    assert result == ()


def test_reference_loader_excludes_order_zero_front_matter_before_candidate_build(monkeypatch):
    import io

    image_bytes = io.BytesIO()
    _crop((40, 60, 100), True).save(image_bytes, format="PNG")
    asset = SimpleNamespace(
        id="asset-a",
        storage_key="asset-a.png",
        checksum="asset-checksum",
        original_checksum="asset-checksum",
        source_family="family-a",
    )
    regions = [
        _region("title", "region-title", "asset-a", 0, (0, 0, 100, 200), "asset-checksum"),
        _region("panel-a", "region-a", "asset-a", 1, (0, 0, 100, 200), "asset-checksum"),
    ]
    captured: list[str] = []

    def builder(**kwargs):
        captured.extend(str(region.id) for region in kwargs["panel_regions"])
        return ()

    monkeypatch.setattr(pipeline, "latest_analysis", lambda *_args: SimpleNamespace(id="analysis"))
    monkeypatch.setattr(pipeline.storage, "read_bytes", lambda _key: image_bytes.getvalue())
    monkeypatch.setattr(pipeline, "_build_reference_panel_fallback_candidates", builder)
    db = SimpleNamespace(scalars=lambda _statement: regions)
    script = SimpleNamespace(sections=[{"section": "hook", "evidence_panel_ids": ["panel-a"]}])

    pipeline._load_reference_panel_fallback_candidates(
        db,
        "project",
        script,
        [asset],
        reference_profile.REFERENCE_MATCHED_SHORTS_V1,
    )

    assert captured == ["region-a"]


def test_reference_loader_keeps_cited_order_zero_panel(monkeypatch):
    import io

    image_bytes = io.BytesIO()
    _crop((40, 60, 100), True).save(image_bytes, format="PNG")
    asset = SimpleNamespace(
        id="asset-a",
        storage_key="asset-a.png",
        checksum="asset-checksum",
        original_checksum="asset-checksum",
        source_family="family-a",
    )
    regions = [
        _region("title", "region-title", "asset-a", 0, (0, 0, 100, 200), "asset-checksum"),
        _region("panel-a", "region-a", "asset-a", 1, (0, 0, 100, 200), "asset-checksum"),
    ]
    captured: list[str] = []

    def builder(**kwargs):
        captured.extend(str(region.id) for region in kwargs["panel_regions"])
        return ()

    monkeypatch.setattr(pipeline, "latest_analysis", lambda *_args: SimpleNamespace(id="analysis"))
    monkeypatch.setattr(pipeline.storage, "read_bytes", lambda _key: image_bytes.getvalue())
    monkeypatch.setattr(pipeline, "_build_reference_panel_fallback_candidates", builder)
    db = SimpleNamespace(scalars=lambda _statement: regions)
    script = SimpleNamespace(sections=[{"section": "hook", "evidence_panel_ids": ["title"]}])

    pipeline._load_reference_panel_fallback_candidates(
        db,
        "project",
        script,
        [asset],
        reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        section_evidence_panel_ids={"hook": ("title",)},
    )

    assert captured == ["region-title", "region-a"]


@pytest.mark.parametrize("panel_ids", (("missing-panel",), ("panel-a", "foreign-panel")))
def test_panel_candidate_builder_rejects_missing_or_foreign_explicit_ids(panel_ids):
    builder = pipeline._build_reference_panel_fallback_candidates
    regions, crops, candidates = _builder_inputs()
    with pytest.raises(pipeline.PipelineError, match="visual.panel_lineage_unavailable"):
        builder(
            panel_regions=regions,
            panel_candidates_by_region_id=candidates,
            panel_crops_by_region_id=crops,
            section_evidence_panel_ids={"hook": panel_ids},
            section_citations={},
            beats_by_section={"hook": ("action",)},
            profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        )


def test_reference_scene_input_carries_exact_panel_review_identity():
    scene = render.SceneInput(
        image_path=None,
        start_time=0.0,
        end_time=1.0,
        source_asset_id="asset-a",
        source_order=3,
        panel_size=(100, 200),
        evidence_hash="evidence-hash",
        border_mask={"mask_sha256": "mask-hash"},
        selected_roi={"kind": "primary", "crop_box": [0, 0, 100, 200]},
        fallback_attempts=[{"accepted": True}],
        framing_telemetry={"crop_box": [0, 0, 100, 200]},
        transition="fade",
        publish_allowed=False,
    )
    assert scene.source_asset_id == "asset-a"
    assert scene.publish_allowed is False


def test_silent_reference_request_is_explicit_and_audio_free():
    request = render.RenderRequest(
        project_id="project",
        scenes=[],
        audio_path=None,
        silent_reference_review=True,
        output_override=None,
    )
    assert request.silent_reference_review is True
    assert request.audio_path is None


def test_bind_reference_requires_exact_planner_registry():
    binder = pipeline._bind_reference_panel_regions
    assert "candidate_registry" in inspect.signature(binder).parameters


def test_reference_review_result_can_expose_sidecar():
    result = render.RenderResult(
        output_path=Path("review.mp4"),
        subtitle_path=None,
        thumbnail_path=None,
        duration=1.0,
        width=1080,
        height=1920,
        checksum="",
        size_bytes=0,
        sidecar_path=Path("review.json"),
    )
    assert result.sidecar_path.name == "review.json"


def test_review_sidecar_preserves_motion_and_transition_intent():
    from app.services import render

    request = render.RenderRequest(
        project_id="project",
        scenes=[
            render.SceneInput(
                image_path=None,
                start_time=0.0,
                end_time=1.0,
                motion_mode="slow_push",
                camera_curve="slow_push_in",
                motion_intensity="medium",
                motion_reason="distinct evidence viewport",
                transition="fade",
                publish_allowed=False,
            )
        ],
        audio_path=None,
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        silent_reference_review=True,
    )
    sidecar = render._reference_review_sidecar(request, {"has_audio": False})

    assert sidecar["shots"][0]["motion_mode"] == "slow_push"
    assert sidecar["shots"][0]["camera_curve"] == "slow_push_in"
    assert sidecar["shots"][0]["transition"] == "fade"
    assert sidecar["shots"][0]["transition_duration_s"] == 0.18


def test_review_frame_motion_audit_measures_non_noop_change(tmp_path):
    from app.services import review_preview

    first = Image.new("L", (64, 64), 0)
    second = Image.new("L", (64, 64), 255)
    first_path = tmp_path / "frame-01.jpg"
    second_path = tmp_path / "frame-02.jpg"
    first.save(first_path)
    second.save(second_path)

    metrics = review_preview._frame_motion_audit([first_path, second_path], 1.0)

    assert metrics["mean_frame_diff"] > 0.25
    assert metrics["max_frame_diff"] > 0.25


class _EmptyScalars:
    def __iter__(self):
        return iter(())

    def first(self):
        return None


class _TimelineDb:
    def __init__(self):
        self.deleted = []

    def scalars(self, _statement):
        return _EmptyScalars()

    def delete(self, item):
        self.deleted.append(item)

    def add(self, _item):
        return None

    def flush(self):
        return None


def test_reference_timeline_passes_only_exact_panel_candidates_to_planner(monkeypatch):
    from app.services import editorial_visual_planner

    regions, crops, candidates = _builder_inputs()
    candidate_tuple = pipeline._build_reference_panel_fallback_candidates(
        panel_regions=regions,
        panel_candidates_by_region_id=candidates,
        panel_crops_by_region_id=crops,
        section_evidence_panel_ids={"hook": ("panel-a", "panel-b")},
        section_citations={},
        beats_by_section={"hook": ()},
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
    )
    captured = {}

    def fake_plan(*args, **kwargs):
        captured.update(kwargs)
        return []

    project = SimpleNamespace(id="project", template=reference_profile.REFERENCE_MATCHED_SHORTS_V1.profile_id)
    asset = SimpleNamespace(id="asset-a", type="image")
    segment = SimpleNamespace(end_time=52.2)
    span = SimpleNamespace(section="hook", start_time=0.0, end_time=52.2)
    script = SimpleNamespace(id="script", sections=[{"section": "hook", "evidence_panel_ids": ["panel-a"]}])
    monkeypatch.setattr(pipeline, "get_project", lambda *_args: project)
    monkeypatch.setattr(pipeline, "current_script", lambda *_args: script)
    monkeypatch.setattr(
        pipeline,
        "_script_for_media",
        lambda *_args, **_kwargs: script,
    )
    monkeypatch.setattr(pipeline, "audio_segments", lambda *_args: [segment])
    monkeypatch.setattr(pipeline, "project_assets", lambda *_args: [asset])
    monkeypatch.setattr(pipeline, "image_assets", lambda _assets: [asset])
    monkeypatch.setattr(pipeline, "spans_from_segments", lambda _segments: [span])
    monkeypatch.setattr(pipeline.visual_scoring, "analyze_assets", lambda *_args: [object()])
    monkeypatch.setattr(pipeline, "_load_reference_panel_fallback_candidates", lambda *_args: candidate_tuple)
    monkeypatch.setattr(pipeline, "_bind_reference_panel_regions", lambda *args, **kwargs: [])
    monkeypatch.setattr(pipeline, "project_scenes", lambda *_args: [])
    monkeypatch.setattr(pipeline, "project_cues", lambda *_args: [])
    monkeypatch.setattr(pipeline.timeline_svc, "build_cues", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(pipeline.director_svc, "audit_sequence", lambda _planned: [])
    monkeypatch.setattr(pipeline, "audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(editorial_visual_planner, "plan", fake_plan)
    pipeline.build_timeline(_TimelineDb(), "project")
    assert captured["reference_panel_candidates"] == candidate_tuple
    assert captured["cited_asset_ids_by_section"] is None
    assert captured["citation_alignment_reasons_by_section"] is None


def test_reference_planning_failure_happens_before_scene_deletion(monkeypatch):
    from app.services import editorial_visual_planner

    regions, crops, candidates = _builder_inputs()
    candidate_tuple = pipeline._build_reference_panel_fallback_candidates(
        panel_regions=regions,
        panel_candidates_by_region_id=candidates,
        panel_crops_by_region_id=crops,
        section_evidence_panel_ids={"hook": ("panel-a", "panel-b")},
        section_citations={},
        beats_by_section={"hook": ()},
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
    )
    project = SimpleNamespace(id="project", template=reference_profile.REFERENCE_MATCHED_SHORTS_V1.profile_id)
    asset = SimpleNamespace(id="asset-a", type="image")
    segment = SimpleNamespace(end_time=52.2)
    span = SimpleNamespace(section="hook", start_time=0.0, end_time=52.2)
    script = SimpleNamespace(id="script", sections=[{"section": "hook", "evidence_panel_ids": ["panel-a"]}])
    db = _TimelineDb()
    monkeypatch.setattr(pipeline, "get_project", lambda *_args: project)
    monkeypatch.setattr(pipeline, "current_script", lambda *_args: script)
    monkeypatch.setattr(
        pipeline,
        "_script_for_media",
        lambda *_args, **_kwargs: script,
    )
    monkeypatch.setattr(pipeline, "audio_segments", lambda *_args: [segment])
    monkeypatch.setattr(pipeline, "project_assets", lambda *_args: [asset])
    monkeypatch.setattr(pipeline, "image_assets", lambda _assets: [asset])
    monkeypatch.setattr(pipeline, "spans_from_segments", lambda _segments: [span])
    monkeypatch.setattr(pipeline.visual_scoring, "analyze_assets", lambda *_args: [object()])
    monkeypatch.setattr(pipeline, "_load_reference_panel_fallback_candidates", lambda *_args: candidate_tuple)
    monkeypatch.setattr(pipeline, "project_scenes", lambda *_args: [object()])
    monkeypatch.setattr(pipeline, "project_cues", lambda *_args: [])
    monkeypatch.setattr(pipeline, "audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(editorial_visual_planner, "plan", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        editorial_visual_planner.ReferencePlanningError(
            "no feasible panel", "visual.visual_unavailable"
        )
    ))
    with pytest.raises(pipeline.PipelineError, match="visual.visual_unavailable"):
        pipeline.build_timeline(db, "project")
    assert db.deleted == []


def test_silent_reference_build_never_reads_script_or_calls_tts(monkeypatch, tmp_path):
    project = SimpleNamespace(
        id="project",
        template=reference_profile.REFERENCE_MATCHED_SHORTS_V1.profile_id,
        title="hidden",
    )
    job = SimpleNamespace(project_id="project", render_profile="Auto", encoder_requested="")
    db = SimpleNamespace(flush=lambda: None)
    scene = SimpleNamespace(start_time=0.0, end_time=40.901)
    scene_input = render.SceneInput(
        image_path=tmp_path / "panel.png",
        start_time=0.0,
        end_time=40.901,
        panel_region_id="region-a",
        panel_id="panel-a",
        source_asset_id="asset-a",
        source_order=3,
        transition="fade",
        publish_allowed=False,
    )
    monkeypatch.setattr(pipeline, "get_project", lambda *_args: project)
    monkeypatch.setattr(pipeline, "project_scenes", lambda *_args: [scene])
    monkeypatch.setattr(pipeline, "_reference_scene_inputs", lambda *_args, **_kwargs: [scene_input])
    monkeypatch.setattr(pipeline, "project_cues", lambda *_args: [])
    monkeypatch.setattr(pipeline.storage, "output_path", lambda *_args: tmp_path / "review.mp4")
    monkeypatch.setattr(pipeline, "current_script", lambda *_args: (_ for _ in ()).throw(AssertionError("script read")))
    monkeypatch.setattr(pipeline, "audio_segments", lambda *_args: (_ for _ in ()).throw(AssertionError("audio read")))
    monkeypatch.setattr(pipeline.tts_svc, "concat_audio", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("tts called")))
    request = pipeline.build_render_request(
        db,
        job,
        silent_reference_review=True,
        output_override=tmp_path / "isolated-review.mp4",
    )
    assert request.audio_path is None
    assert request.music_path is None
    assert request.silent_reference_review is True
    assert request.title_text == ""
    assert request.output_path == tmp_path / "isolated-review.mp4"
    assert request.scenes[0].transition == "fade"



def test_panel_candidate_builder_excludes_unreferenced_regions():
    regions, crops, candidates = _builder_inputs()
    regions = regions + (
        _region("panel-c", "region-c", "asset-a", 4, (0, 0, 100, 200), "asset-a-checksum"),
    )
    crops["region-c"] = _crop((80, 80, 80), False)
    candidates["region-c"] = _candidate("asset-a", 4, "c")
    result = pipeline._build_reference_panel_fallback_candidates(
        panel_regions=regions,
        panel_candidates_by_region_id=candidates,
        panel_crops_by_region_id=crops,
        section_evidence_panel_ids={"hook": ("panel-a",)},
        section_citations={},
        beats_by_section={"hook": ()},
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
    )
    assert [candidate.panel_region_id for candidate in result] == ["region-a"]


def test_reference_roi_alternatives_have_distinct_source_geometry():
    alternatives = pipeline._reference_roi_alternatives(
        (100, 200),
        _candidate("asset-a", 3, "single-focus"),
        reference_profile.REFERENCE_MATCHED_SHORTS_V1,
    )
    geometry = {(item.crop_box, item.focus) for item in alternatives}
    assert len(geometry) == len(alternatives)
    assert len({item.crop_box for item in alternatives}) >= 2
    assert not any(
        item.kind == "alternate_roi" and item.crop_box == alternatives[0].crop_box
        for item in alternatives
    )


def test_stale_panel_checksum_fails_before_candidate_builder(monkeypatch):
    import io

    region = _region("panel-a", "region-a", "asset-a", 3, (0, 0, 100, 200), "stale")
    asset = SimpleNamespace(
        id="asset-a",
        storage_key="asset-a.png",
        checksum="current",
        original_checksum="current",
        source_family="family-a",
    )
    image_bytes = io.BytesIO()
    _crop((40, 60, 100), True).save(image_bytes, format="PNG")
    db = SimpleNamespace(scalars=lambda _statement: [region])
    script = SimpleNamespace(
        sections=[{"section": "hook", "evidence_panel_ids": ["panel-a"], "citations": [3]}]
    )
    called = False

    def builder(**_kwargs):
        nonlocal called
        called = True
        return ()

    monkeypatch.setattr(pipeline, "latest_analysis", lambda *_args: SimpleNamespace(id="analysis"))
    monkeypatch.setattr(pipeline.storage, "read_bytes", lambda _key: image_bytes.getvalue())
    monkeypatch.setattr(pipeline, "_build_reference_panel_fallback_candidates", builder)
    with pytest.raises(pipeline.PipelineError, match="visual.panel_lineage_unavailable"):
        pipeline._load_reference_panel_fallback_candidates(
            db,
            "project",
            script,
            [asset],
            reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        )
    assert called is False


def test_exact_reference_preparation_uses_persisted_roi_pixels(monkeypatch, tmp_path):
    import importlib
    from dataclasses import asdict

    framing = importlib.import_module("app.services.framing_analysis")
    source = Image.new("RGB", (100, 200), (20, 20, 180))
    ImageDraw.Draw(source).rectangle((0, 0, 49, 199), fill=(220, 30, 30))
    source_path = tmp_path / "panel.png"
    source.save(source_path)
    evidence = _evidence("panel-a", "asset-a", 3)
    mask = framing.build_color_agnostic_border_mask(source, evidence)
    selected_box = (0, 0, 50, 200)
    telemetry = framing.FramingTelemetry(
        contract_version=reference_profile.REFERENCE_MATCHED_SHORTS_V1.framing_contract_version,
        detector_version=mask.detector_version,
        mask_sha256=mask.mask_sha256,
        crop_box=selected_box,
        base_zoom=1.0,
        source_resolution_zoom_cap=1.35,
        protected_region_zoom_cap=1.35,
        edge_connected_blank_fraction=0.0,
        non_discardable_low_information_fraction=0.0,
        protected_retained_fraction=1.0,
        balloon_mask_intersection_ratio=0.0,
        subject_coverage=1.0,
        face_coverage=1.0,
        action_coverage=1.0,
        effect_coverage=1.0,
        continuity_context_coverage=1.0,
        mask_confidence=0.96,
        mask_source="vision_geometry_v1",
    )
    calls = []

    def feasible(box, *_args, **_kwargs):
        calls.append(box)
        return True, telemetry

    monkeypatch.setattr(framing, "candidate_is_feasible", feasible)
    scene = render.SceneInput(
        image_path=source_path,
        start_time=0.0,
        end_time=1.0,
        panel_region_id="region-a",
        panel_id="panel-a",
        source_asset_id="asset-a",
        source_order=3,
        panel_size=(100, 200),
        evidence_hash=visual_scoring.visual_evidence_hash(evidence),
        visual_evidence=visual_scoring.panel_visual_evidence_json(evidence),
        border_mask=asdict(mask),
        selected_roi={"kind": "primary", "roi_label": "selected", "crop_box": list(selected_box)},
        framing_telemetry=asdict(telemetry),
        publish_allowed=False,
    )
    assert hasattr(render, "_prepare_exact_reference_frame")
    destination = tmp_path / "prepared.jpg"
    render._prepare_exact_reference_frame(
        scene=scene,
        dest=destination,
        width=1080,
        height=1920,
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
    )
    assert calls == [selected_box]
    with Image.open(destination) as prepared:
        red, _green, blue = prepared.convert("RGB").getpixel((prepared.width // 2, prepared.height // 2))
    assert red > blue


def test_reference_ledger_keeps_full_mask_only_on_accepted_attempt():
    import importlib
    from dataclasses import asdict

    review = importlib.import_module("app.services.reference_visual_review")
    framing = importlib.import_module("app.services.framing_analysis")
    _regions, crops, candidates = _builder_inputs()
    candidate = pipeline._build_reference_panel_fallback_candidates(
        panel_regions=_regions[:1],
        panel_candidates_by_region_id={"region-a": candidates["region-a"]},
        panel_crops_by_region_id={"region-a": crops["region-a"]},
        section_evidence_panel_ids={"hook": ("panel-a",)},
        section_citations={},
        beats_by_section={"hook": ()},
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
    )[0]
    shot = {
        "panel_region_id": candidate.panel_region_id,
        "fallback_attempts": [
            {"accepted": False, "panel_region_id": candidate.panel_region_id, "mask_sha256": candidate.border_mask.mask_sha256},
            {"accepted": True, "panel_region_id": candidate.panel_region_id},
        ],
    }
    assert hasattr(review, "attach_accepted_mask_snapshot")
    ledger = review.attach_accepted_mask_snapshot(shot, {candidate.panel_region_id: candidate})
    assert "border_mask" not in ledger[0]
    assert ledger[1]["border_mask"] == asdict(candidate.border_mask)
    assert ledger[1]["detector_version"] == framing.DETECTOR_VERSION


def test_silent_request_rejects_invalid_cue_without_mutating_persistence(monkeypatch):
    project = SimpleNamespace(id="project")
    job = SimpleNamespace(project_id="project", encoder_requested="")
    scene = render.SceneInput(
        image_path=Path("panel.png"),
        start_time=0.0,
        end_time=1.0,
        source_asset_id="asset-a",
        panel_region_id="region-a",
        panel_id="panel-a",
        publish_allowed=False,
    )
    persisted = SimpleNamespace(order_index=0, text="TWO WORDS", start_time=-1.0, end_time=5.0)
    db = SimpleNamespace(flush=lambda: pytest.fail("silent review mutated persistence"))
    monkeypatch.setattr(
        pipeline,
        "project_scenes",
        lambda *_args: [SimpleNamespace(start_time=0.0, end_time=1.0)],
    )
    monkeypatch.setattr(pipeline, "_reference_scene_inputs", lambda *_args, **_kwargs: [scene])
    monkeypatch.setattr(pipeline, "project_cues", lambda *_args: [persisted])
    with pytest.raises(pipeline.PipelineError, match="reference.subtitle_invalid"):
        pipeline._build_silent_reference_request(
            db, job, project, reference_profile.REFERENCE_MATCHED_SHORTS_V1, output_override=None
        )
    assert persisted.start_time == -1.0
    assert persisted.end_time == 5.0


def test_silent_request_requires_publish_false(monkeypatch):
    project = SimpleNamespace(id="project")
    job = SimpleNamespace(project_id="project", encoder_requested="")
    scene = render.SceneInput(
        image_path=Path("panel.png"),
        start_time=0.0,
        end_time=1.0,
        source_asset_id="asset-a",
        panel_region_id="region-a",
        panel_id="panel-a",
        publish_allowed=True,
    )
    persisted = SimpleNamespace(order_index=0, text="WORD", start_time=0.0, end_time=1.0)
    db = SimpleNamespace(flush=lambda: None)
    monkeypatch.setattr(
        pipeline,
        "project_scenes",
        lambda *_args: [SimpleNamespace(start_time=0.0, end_time=1.0)],
    )
    monkeypatch.setattr(pipeline, "_reference_scene_inputs", lambda *_args, **_kwargs: [scene])
    monkeypatch.setattr(pipeline, "project_cues", lambda *_args: [persisted])
    with pytest.raises(pipeline.PipelineError, match="publish_allowed"):
        pipeline._build_silent_reference_request(
            db, job, project, reference_profile.REFERENCE_MATCHED_SHORTS_V1, output_override=None
        )


def test_silent_sidecar_contains_mask_identity_not_full_grids():
    scene = render.SceneInput(
        image_path=Path("panel.png"),
        start_time=0.0,
        end_time=1.0,
        source_asset_id="asset-a",
        source_order=3,
        panel_region_id="region-a",
        panel_id="panel-a",
        panel_size=(100, 200),
        evidence_hash="evidence",
        border_mask={
            "detector_version": "detector",
            "mask_sha256": "mask",
            "source_width": 100,
            "source_height": 200,
            "edge_connected_mask": [[True]],
        },
        selected_roi={"kind": "primary", "crop_box": [0, 0, 100, 200]},
        fallback_attempts=[],
        framing_telemetry={"mask_sha256": "mask"},
        publish_allowed=False,
    )
    request = SimpleNamespace(
        project_id="project",
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        scenes=[scene],
    )
    assert hasattr(render, "_reference_review_sidecar")
    sidecar = render._reference_review_sidecar(
        request, {"has_audio": False, "duration": 1.0, "width": 1080, "height": 1920}
    )
    assert "edge_connected_mask" not in str(sidecar)
    assert sidecar["shots"][0]["border_mask"] == {
        "detector_version": "detector",
        "mask_sha256": "mask",
        "source_width": 100,
        "source_height": 200,
    }


def test_silent_sidecar_is_json_safe_for_in_memory_ledger_values():
    import json
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class InMemoryTelemetry:
        mask_sha256: str = "mask"

    telemetry = InMemoryTelemetry()
    scene = render.SceneInput(
        image_path=Path("panel.png"),
        start_time=0.0,
        end_time=1.0,
        source_asset_id="asset-a",
        source_order=3,
        panel_region_id="region-a",
        panel_id="panel-a",
        panel_size=(100, 200),
        evidence_hash="evidence",
        border_mask={"mask_sha256": "mask"},
        selected_roi={"kind": "primary", "crop_box": [0, 0, 100, 200]},
        fallback_attempts=[{"accepted": False, "telemetry": telemetry}],
        framing_telemetry=telemetry,
        review_source_upscale_manifest=telemetry,
        publish_allowed=False,
    )
    request = SimpleNamespace(
        project_id="project",
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        scenes=[scene],
    )

    sidecar = render._reference_review_sidecar(
        request, {"has_audio": False, "duration": 1.0, "width": 1080, "height": 1920}
    )

    json.dumps(sidecar, ensure_ascii=False, sort_keys=True)


def test_reference_ledger_rejects_tampered_mask_and_nonfinite_telemetry():
    import copy
    from dataclasses import asdict

    from app.services import framing_analysis, reference_visual_review

    regions, crops, candidates = _builder_inputs()
    candidate = pipeline._build_reference_panel_fallback_candidates(
        panel_regions=regions[:1],
        panel_candidates_by_region_id={"region-a": candidates["region-a"]},
        panel_crops_by_region_id={"region-a": crops["region-a"]},
        section_evidence_panel_ids={"hook": ("panel-a",)},
        section_citations={},
        beats_by_section={"hook": ()},
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
    )[0]
    _feasible, telemetry = framing_analysis.candidate_is_feasible(
        candidate.roi_alternatives[0].crop_box,
        candidate.visual_evidence,
        candidate.border_mask,
        candidate.panel_size,
        (
            reference_profile.REFERENCE_MATCHED_SHORTS_V1.final_width,
            reference_profile.REFERENCE_MATCHED_SHORTS_V1.final_height,
        ),
    )
    selected_roi = {
        "kind": candidate.roi_alternatives[0].kind,
        "roi_label": candidate.roi_alternatives[0].roi_label,
        "crop_box": list(candidate.roi_alternatives[0].crop_box),
    }
    telemetry_json = asdict(telemetry)
    telemetry_json["selected_roi"] = selected_roi
    entry = {
        "accepted": True,
        "panel_region_id": candidate.panel_region_id,
        "panel_id": candidate.panel_id,
        "source_asset_id": candidate.source_asset_id,
        "source_asset_checksum": candidate.source_asset_checksum,
        "source_order": candidate.source_order,
        "panel_size": list(candidate.panel_size),
        "evidence_hash": candidate.evidence_hash,
        "border_mask": asdict(candidate.border_mask),
        "detector_version": candidate.border_mask.detector_version,
        "mask_sha256": candidate.border_mask.mask_sha256,
        "roi_label": selected_roi["roi_label"],
        "crop_box": selected_roi["crop_box"],
        "telemetry": telemetry_json,
    }
    tampered_mask = copy.deepcopy(entry)
    edge_rows = [list(row) for row in tampered_mask["border_mask"]["edge_connected_mask"]]
    edge_rows[0][0] = not edge_rows[0][0]
    tampered_mask["border_mask"]["edge_connected_mask"] = tuple(tuple(row) for row in edge_rows)
    with pytest.raises(reference_visual_review.ReferenceReviewError):
        reference_visual_review.validate_accepted_fallback_ledger(
            [tampered_mask],
            panel_region_id=candidate.panel_region_id,
            panel_id=candidate.panel_id,
            source_asset_id=candidate.source_asset_id,
            source_asset_checksum=candidate.source_asset_checksum,
            source_order=candidate.source_order,
            panel_size=candidate.panel_size,
            evidence=candidate.visual_evidence,
            border_mask=candidate.border_mask,
            selected_roi=selected_roi,
            framing_telemetry=telemetry_json,
        )
    tampered_telemetry = copy.deepcopy(entry)
    tampered_telemetry["telemetry"]["edge_connected_blank_fraction"] = float("nan")
    with pytest.raises(reference_visual_review.ReferenceReviewError):
        reference_visual_review.validate_accepted_fallback_ledger(
            [tampered_telemetry],
            panel_region_id=candidate.panel_region_id,
            panel_id=candidate.panel_id,
            source_asset_id=candidate.source_asset_id,
            source_asset_checksum=candidate.source_asset_checksum,
            source_order=candidate.source_order,
            panel_size=candidate.panel_size,
            evidence=candidate.visual_evidence,
            border_mask=candidate.border_mask,
            selected_roi=selected_roi,
            framing_telemetry=tampered_telemetry["telemetry"],
        )


def test_reference_planner_tries_all_roi_phases_on_alternate_panel(monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace

    from app.services import editorial_visual_planner, framing_analysis

    regions, crops, candidates = _builder_inputs()
    regions = (
        regions[0],
        _region("panel-b", "region-b", "asset-a", 3, (0, 0, 200, 200), "asset-a-checksum"),
    )
    crops = {
        "region-a": crops["region-a"],
        "region-b": Image.new("RGB", (200, 200), (110, 40, 40)),
    }
    candidate_b = replace(
        candidates["region-b"],
        features=replace(
            candidates["region-b"].features,
            focal_points=((0.5, 0.5), (0.9, 0.5)),
        ),
    )
    candidate_tuple = pipeline._build_reference_panel_fallback_candidates(
        panel_regions=regions,
        panel_candidates_by_region_id={"region-a": candidates["region-a"], "region-b": candidate_b},
        panel_crops_by_region_id=crops,
        section_evidence_panel_ids={"hook": ("panel-a", "panel-b")},
        section_citations={},
        beats_by_section={"hook": ()},
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
    )
    candidate_b_record = next(item for item in candidate_tuple if item.panel_id == "panel-b")
    alternate_box = next(
        item.crop_box for item in candidate_b_record.roi_alternatives if item.kind == "alternate_roi"
    )
    real_feasible = framing_analysis.candidate_is_feasible

    def fake_feasible(box, evidence, mask, panel_size, target_size, **_kwargs):
        _accepted, telemetry = real_feasible(box, evidence, mask, panel_size, target_size)
        return box == alternate_box, replace(telemetry, rejection_code=None if box == alternate_box else "visual.visual_unavailable")

    monkeypatch.setattr(framing_analysis, "candidate_is_feasible", fake_feasible)
    monkeypatch.setattr(
        editorial_visual_planner,
        "_plan_reference",
        lambda *_args, **_kwargs: [
            {
                "order_index": 0,
                "section": "hook",
                "start_time": 0.0,
                "end_time": 40.901,
                "camera_intent": "neutral",
                "effect": "static",
                "asset_id": "unused",
                "transition": "cut",
                "focus_x": 0.5,
                "focus_y": 0.5,
                "focus_end_x": 0.5,
                "focus_end_y": 0.5,
            }
        ],
    )
    result = editorial_visual_planner._plan_reference_panel_candidates(
        [SimpleNamespace(section="hook", start_time=0.0, end_time=40.901)],
        reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        candidate_tuple,
    )
    accepted = next(item for item in result[0]["fallback_attempts"] if item["accepted"] is True)
    assert result[0]["panel_id"] == "panel-b"
    assert accepted["kind"] == "alternate_panel"
    assert accepted["roi_kind"] == "alternate_roi"


def test_reference_planner_counts_distinct_roi_capacity_for_single_panel(monkeypatch):
    from types import SimpleNamespace

    from app.services import editorial_visual_planner

    regions, crops, candidates = _builder_inputs()
    candidate_tuple = pipeline._build_reference_panel_fallback_candidates(
        panel_regions=regions[:1],
        panel_candidates_by_region_id={"region-a": candidates["region-a"]},
        panel_crops_by_region_id={"region-a": crops["region-a"]},
        section_evidence_panel_ids={"setup": ("panel-a",)},
        section_citations={},
        beats_by_section={"setup": ()},
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
    )
    captured = {}

    monkeypatch.setattr(editorial_visual_planner, "_feasible_roi_capacity", lambda *_args, **_kwargs: 2)

    def fake_reference(*_args, **kwargs):
        captured["capacity"] = kwargs["max_shots_by_section"]["setup"]
        return [
            {
                "order_index": index,
                "section": "setup",
                "start_time": float(index * 25),
                "end_time": float((index + 1) * 25),
                "camera_intent": "neutral",
                "effect": "static",
                "asset_id": "unused",
                "focus_x": 0.5,
                "focus_y": 0.5,
                "focus_end_x": 0.5,
                "focus_end_y": 0.5,
            }
            for index in range(kwargs["max_shots_by_section"]["setup"])
        ]

    monkeypatch.setattr(editorial_visual_planner, "_plan_reference", fake_reference)

    def fake_attempt(candidate, roi, **kwargs):
        entry = {
            "accepted": True,
            "panel_region_id": candidate.panel_region_id,
            "panel_id": candidate.panel_id,
            "source_asset_id": candidate.source_asset_id,
            "source_order": candidate.source_order,
            "source_asset_checksum": candidate.source_asset_checksum,
            "panel_size": list(candidate.panel_size),
            "evidence_hash": candidate.evidence_hash,
            "roi_label": roi.roi_label,
            "crop_box": list(roi.crop_box),
            "roi_kind": roi.kind,
            "telemetry": {},
        }
        return True, {"rejection_code": None}, entry

    monkeypatch.setattr(editorial_visual_planner, "_reference_panel_attempt", fake_attempt)
    result = editorial_visual_planner._plan_reference_panel_candidates(
        [SimpleNamespace(section="setup", start_time=0.0, end_time=50.0)],
        reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        candidate_tuple,
        allow_review_cadence_adaptation=True,
        allow_review_duration=True,
    )

    assert captured["capacity"] == 2
    assert len(result) == 2


def test_review_planner_uses_distinct_roi_capacity_before_long_holds(monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace

    from app.services import editorial_visual_planner

    regions = []
    crops = {}
    candidates = {}
    for index in range(5):
        panel_id = f"panel-{index}"
        region_id = f"region-{index}"
        asset_id = f"asset-{index}"
        regions.append(
            _region(
                panel_id,
                region_id,
                asset_id,
                index + 3,
                (0, 0, 100, 200),
                f"{asset_id}-checksum",
            )
        )
        crops[region_id] = _crop((40 + index * 20, 60, 100), True)
        candidates[region_id] = _candidate(asset_id, index + 3, f"signature-{index}")
    candidate_tuple = pipeline._build_reference_panel_fallback_candidates(
        panel_regions=regions,
        panel_candidates_by_region_id=candidates,
        panel_crops_by_region_id=crops,
        section_evidence_panel_ids={"setup": tuple(item.panel_id for item in regions)},
        section_citations={},
        beats_by_section={"setup": ()},
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
    )
    alternate_rois = (
        editorial_visual_planner.ReferenceROIAlternative(
            kind="primary",
            roi_label="primary",
            crop_box=(0, 0, 100, 200),
            focus=(0.25, 0.5, 0.25, 0.5),
        ),
        editorial_visual_planner.ReferenceROIAlternative(
            kind="alternate_roi",
            roi_label="alternate",
            crop_box=(0, 0, 100, 200),
            focus=(0.75, 0.5, 0.75, 0.5),
        ),
    )
    candidate_tuple = tuple(
        replace(candidate, roi_alternatives=alternate_rois)
        for candidate in candidate_tuple
    )
    sections = ("hook", "setup", "conflict", "twist", "cta")
    candidate_tuple = tuple(
        replace(candidate, eligible_sections=(sections[index],))
        for index, candidate in enumerate(candidate_tuple)
    )

    monkeypatch.setattr(
        editorial_visual_planner,
        "_feasible_roi_capacity",
        lambda *_args, **_kwargs: 2,
    )
    original_reference = editorial_visual_planner._plan_reference

    def forced_cut_reference(*args, **kwargs):
        shots = original_reference(*args, **kwargs)
        for shot in shots:
            shot["transition"] = "cut"
        return shots

    monkeypatch.setattr(
        editorial_visual_planner,
        "_plan_reference",
        forced_cut_reference,
    )

    def fake_attempt(candidate, roi, **kwargs):
        duplicate = tuple(editorial_visual_planner._roi_key(roi)) in kwargs["used_rois"]
        entry = {
            "panel_region_id": candidate.panel_region_id,
            "panel_id": candidate.panel_id,
            "source_asset_id": candidate.source_asset_id,
            "source_order": candidate.source_order,
            "source_asset_checksum": candidate.source_asset_checksum,
            "panel_size": list(candidate.panel_size),
            "evidence_hash": candidate.evidence_hash,
            "roi_label": roi.roi_label,
            "crop_box": list(roi.crop_box),
            "roi_kind": roi.kind,
            "kind": kwargs["phase_kind"],
            "accepted": not duplicate,
            "code": "visual.reuse_roi_duplicate" if duplicate else None,
            "telemetry": {"edge_connected_blank_fraction": 0.0},
        }
        return not duplicate, {"edge_connected_blank_fraction": 0.0}, entry

    monkeypatch.setattr(editorial_visual_planner, "_reference_panel_attempt", fake_attempt)
    result = editorial_visual_planner._plan_reference_panel_candidates(
        [
            SimpleNamespace(
                section=section,
                start_time=index * 10.26,
                end_time=(index + 1) * 10.26,
                text="The pressure shifts as the next move changes the stakes.",
            )
            for index, section in enumerate(sections)
        ],
        reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        candidate_tuple,
        allow_review_cadence_adaptation=True,
        allow_review_duration=True,
    )

    assert len(result) == 10
    assert max(shot["end_time"] - shot["start_time"] for shot in result) < 6.0
    assert len({shot["panel_id"] for shot in result}) == 5
    assert all(
        len({shot["roi_label"] for shot in result if shot["panel_id"] == panel_id}) == 2
        for panel_id in {shot["panel_id"] for shot in result}
    )
    assert any(shot["transition"] == "fade" for shot in result[1:])


def test_silent_render_video_uses_persisted_roi_without_reselection(monkeypatch, tmp_path):
    from dataclasses import asdict

    from app.services import framing_analysis, storage

    source = Image.new("RGB", (100, 200), (20, 20, 180))
    ImageDraw.Draw(source).rectangle((0, 0, 49, 199), fill=(220, 30, 30))
    source_path = tmp_path / "panel.png"
    source.save(source_path)
    evidence = _evidence("panel-a", "asset-a", 3)
    mask = framing_analysis.build_color_agnostic_border_mask(source, evidence)
    scene = render.SceneInput(
        image_path=source_path,
        start_time=0.0,
        end_time=1.0,
        panel_region_id="region-a",
        panel_id="panel-a",
        source_asset_id="asset-a",
        source_order=3,
        panel_size=(100, 200),
        evidence_hash=visual_scoring.visual_evidence_hash(evidence),
        visual_evidence=visual_scoring.panel_visual_evidence_json(evidence),
        border_mask=asdict(mask),
        selected_roi={"kind": "primary", "roi_label": "persisted", "crop_box": [0, 0, 50, 200]},
        framing_telemetry={},
        publish_allowed=False,
    )
    request = render.RenderRequest(
        project_id="project",
        scenes=[scene],
        audio_path=None,
        output_path=tmp_path / "review.mp4",
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        silent_reference_review=True,
    )
    exact_calls = []

    def exact_prepare(*, scene, dest, width, height, profile):
        exact_calls.append(tuple(scene.selected_roi["crop_box"]))
        Image.new("RGB", (width, height), (1, 2, 3)).save(dest, "JPEG")
        return dest

    monkeypatch.setattr(render, "_prepare_exact_reference_frame", exact_prepare)
    monkeypatch.setattr(render, "editorial_frame", lambda *_args, **_kwargs: pytest.fail("reselected ROI"))
    monkeypatch.setattr(render, "_validate_reference_encoder", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(storage, "workspace_dir", lambda *_args: tmp_path / "work")
    monkeypatch.setattr(render, "render_scene_clip", lambda _scene, _prepared, dest, *_args, **_kwargs: dest.write_bytes(b"clip") or dest)
    monkeypatch.setattr(render, "join_scene_clips", lambda _clips, _scenes, dest, *_args, **_kwargs: dest.write_bytes(b"joined") or dest)
    monkeypatch.setattr(render, "validate_reference_output", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(render, "probe", lambda _path: {"duration": 1.0, "width": 1080, "height": 1920, "fps": 30.0, "codec": "h264", "profile": "High", "pix_fmt": "yuv420p", "has_audio": False})
    monkeypatch.setattr(render, "_run", lambda command, **_kwargs: Path(command[-1]).write_bytes(b"artifact") or "")
    result = render.render_video(request)
    assert exact_calls == [(0, 0, 50, 200)]
    assert result.sidecar_path is not None


def test_silent_reference_duration_uses_review_window_without_relaxing_voice_profile():
    from app.services import pipeline

    profile = reference_profile.REFERENCE_MATCHED_SHORTS_V2

    assert pipeline._reference_duration_bounds(profile, silent_reference_review=True) == (50.0, 60.0)
    assert pipeline._reference_duration_bounds(profile, silent_reference_review=False) == (
        profile.duration_min_s,
        profile.duration_max_s,
    )


def test_silent_review_duration_matches_rounded_rendered_scene_sum():
    scenes = [
        render.SceneInput(
            image_path=None,
            start_time=index * 1.0004,
            end_time=(index + 1) * 1.0004,
            publish_allowed=False,
        )
        for index in range(30)
    ]
    rendered_duration = pipeline._silent_review_media_duration(scenes)
    absolute_end = max(scene.end_time for scene in scenes)
    assert rendered_duration == sum(scene.duration for scene in scenes) == 30.0
    assert absolute_end > rendered_duration

    late_word = render.KaraokeWord("LATE", 29.5, absolute_end)
    group = render.KaraokeSentenceGroup(
        group_id="rounding-regression",
        words=(render.KaraokeWord("ON", 29.0, 29.5), late_word),
        start_time=29.0,
        end_time=absolute_end,
    )
    assert subtitle_karaoke.validate_sentence_groups((group,), duration=rendered_duration) == (
        "subtitle.timing_out_of_bounds",
    )
    assert subtitle_karaoke.validate_sentence_groups((group,), duration=absolute_end) == ()


def test_silent_reference_planner_receives_explicit_review_duration_flag(monkeypatch):
    from app.services import editorial_visual_planner

    captured = {}

    def fake_panel_plan(*_args, **kwargs):
        captured["allow_review_duration"] = kwargs["allow_review_duration"]
        return []

    monkeypatch.setattr(
        editorial_visual_planner,
        "_plan_reference_panel_candidates",
        fake_panel_plan,
    )
    editorial_visual_planner.plan(
        [],
        [],
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V2,
        reference_panel_candidates=(),
        allow_review_duration=True,
    )

    assert captured["allow_review_duration"] is True



def test_silent_request_rejects_cue_crossing_scene_boundary(monkeypatch):
    project = SimpleNamespace(id="project")
    job = SimpleNamespace(project_id="project", encoder_requested="")
    scene_inputs = [
        render.SceneInput(
            image_path=Path("panel-a.png"),
            start_time=0.0,
            end_time=1.0,
            source_asset_id="asset-a",
            panel_region_id="region-a",
            panel_id="panel-a",
            publish_allowed=False,
        ),
        render.SceneInput(
            image_path=Path("panel-b.png"),
            start_time=1.0,
            end_time=2.0,
            source_asset_id="asset-b",
            panel_region_id="region-b",
            panel_id="panel-b",
            publish_allowed=False,
        ),
    ]
    persisted = SimpleNamespace(
        order_index=0, text="WORD", start_time=0.8, end_time=1.2
    )
    db = SimpleNamespace(flush=lambda: pytest.fail("silent review mutated persistence"))
    monkeypatch.setattr(
        pipeline, "project_scenes", lambda *_args: [
            SimpleNamespace(start_time=0.0, end_time=1.0),
            SimpleNamespace(start_time=1.0, end_time=2.0),
        ]
    )
    monkeypatch.setattr(pipeline, "_reference_scene_inputs", lambda *_args, **_kwargs: scene_inputs)
    monkeypatch.setattr(pipeline, "project_cues", lambda *_args: [persisted])
    with pytest.raises(pipeline.PipelineError, match="reference.subtitle_invalid"):
        pipeline._build_silent_reference_request(
            db, job, project, reference_profile.REFERENCE_MATCHED_SHORTS_V1, output_override=None
        )


def test_silent_render_rejects_cue_crossing_scene_boundary_before_encoder(monkeypatch):
    scenes = [
        render.SceneInput(
            image_path=None, start_time=0.0, end_time=1.0, publish_allowed=False
        ),
        render.SceneInput(
            image_path=None, start_time=1.0, end_time=2.0, publish_allowed=False
        ),
    ]
    request = render.RenderRequest(
        project_id="project",
        scenes=scenes,
        audio_path=None,
        cues=[render.CueSpec(order_index=0, text="WORD", start_time=0.8, end_time=1.2)],
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        silent_reference_review=True,
    )
    monkeypatch.setattr(
        render.encoders, "select", lambda *_args: pytest.fail("encoder selected before cue validation")
    )
    with pytest.raises(render.RenderError, match="reference.subtitle_invalid"):
        render.render_video(request)


def test_reference_ledger_rejects_non_mapping_selected_roi():
    from dataclasses import asdict

    from app.services import framing_analysis, reference_visual_review

    regions, crops, candidates = _builder_inputs()
    candidate = pipeline._build_reference_panel_fallback_candidates(
        panel_regions=regions[:1],
        panel_candidates_by_region_id={"region-a": candidates["region-a"]},
        panel_crops_by_region_id={"region-a": crops["region-a"]},
        section_evidence_panel_ids={"hook": ("panel-a",)},
        section_citations={},
        beats_by_section={"hook": ()},
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
    )[0]
    _accepted, telemetry = framing_analysis.candidate_is_feasible(
        candidate.roi_alternatives[0].crop_box,
        candidate.visual_evidence,
        candidate.border_mask,
        candidate.panel_size,
        (
            reference_profile.REFERENCE_MATCHED_SHORTS_V1.final_width,
            reference_profile.REFERENCE_MATCHED_SHORTS_V1.final_height,
        ),
    )
    selected_roi = {
        "kind": candidate.roi_alternatives[0].kind,
        "roi_label": candidate.roi_alternatives[0].roi_label,
        "crop_box": list(candidate.roi_alternatives[0].crop_box),
    }
    telemetry_json = asdict(telemetry)
    telemetry_json["selected_roi"] = selected_roi
    entry = {
        "accepted": True,
        "panel_region_id": candidate.panel_region_id,
        "panel_id": candidate.panel_id,
        "source_asset_id": candidate.source_asset_id,
        "source_asset_checksum": candidate.source_asset_checksum,
        "source_order": candidate.source_order,
        "panel_size": list(candidate.panel_size),
        "evidence_hash": candidate.evidence_hash,
        "border_mask": asdict(candidate.border_mask),
        "detector_version": candidate.border_mask.detector_version,
        "mask_sha256": candidate.border_mask.mask_sha256,
        "roi_label": selected_roi["roi_label"],
        "crop_box": selected_roi["crop_box"],
        "telemetry": telemetry_json,
    }
    with pytest.raises(reference_visual_review.ReferenceReviewError):
        reference_visual_review.validate_accepted_fallback_ledger(
            [entry],
            panel_region_id=candidate.panel_region_id,
            panel_id=candidate.panel_id,
            source_asset_id=candidate.source_asset_id,
            source_asset_checksum=candidate.source_asset_checksum,
            source_order=candidate.source_order,
            panel_size=candidate.panel_size,
            evidence=candidate.visual_evidence,
            border_mask=candidate.border_mask,
            selected_roi=None,
            framing_telemetry=telemetry_json,
        )
def test_review_qc_rejects_hardcoded_subtitle_contract_without_measurements():
    from app.services import review_preview

    with pytest.raises(review_preview.ReviewPreviewError) as exc:
        review_preview._measured_subtitle_qc(
            {},
            {"font_name": "Barber Chop", "safe_margin_px": 120},
        )

    assert exc.value.code == "review.subtitle_measurement_missing"


def test_review_qc_rejects_sixteen_percent_blank_shot():
    from app.services import review_preview

    with pytest.raises(review_preview.ReviewPreviewError) as exc:
        review_preview._measured_visual_qc(
            {"shots": [{"framing_telemetry": {"edge_connected_blank_fraction": 0.16}}]}
        )

    assert exc.value.code == "review.blank_space_exceeds_target"


def test_review_qc_accepts_measured_pixel_safe_subtitle_and_three_percent_blank():
    from app.services import review_preview

    subtitle = review_preview._measured_subtitle_qc(
        {
            "subtitle_evidence": {
                "font_name": "Barber Chop",
                "font_file_sha256": "abc",
                "max_active_text_width_px": 820,
                "safe_text_width_px": 828,
                "minimum_horizontal_clearance_px": 130,
                "max_lines_measured": 2,
            }
        },
        {"font_name": "Barber Chop", "safe_margin_px": 120},
    )
    visual = review_preview._measured_visual_qc(
        {"shots": [{"framing_telemetry": {"edge_connected_blank_fraction": 0.03}}]}
    )

    assert subtitle["font_file_sha256"] == "abc"
    assert visual["max_edge_blank_fraction"] == 0.03


def test_review_qc_rejects_excessive_visual_hold_and_missing_diversity_metrics():
    from app.services import review_preview

    sidecar = {
        "shots": [
            {
                "framing_telemetry": {"edge_connected_blank_fraction": 0.0},
                "panel_id": "panel-a",
                "selected_roi": {"roi_label": "primary"},
            }
        ],
        "visual_motion_audit": {
            "max_unchanged_hold_s": 8.0,
            "unique_visuals": 1,
            "available_visuals": 8,
            "motion_mode_diversity": 1,
            "transition_count": 0,
        },
    }

    with pytest.raises(review_preview.ReviewPreviewError) as exc:
        review_preview._measured_visual_qc(sidecar)

    assert exc.value.code == "review.visual_hold_excessive"
