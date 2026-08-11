from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.constants import AssetType
from app.models import (
    AudioSegment,
    PanelRegion,
    Project,
    ScriptVersion,
    SourceAsset,
    StoryAnalysis,
    SubtitleCue,
    TimelineScene,
    User,
    Workspace,
)
from app.services import (
    editorial_qc,
    editorial_visual_planner,
    motion_director,
    pipeline,
    quality,
    reference_profile,
    visual_scoring,
)


def _spans(total: float = 40.901) -> list[SimpleNamespace]:
    sections = ("hook", "setup", "conflict", "twist", "cta")
    width = total / len(sections)
    return [
        SimpleNamespace(
            section=section,
            text=f"{section} reveals a grounded consequence.",
            start_time=index * width,
            end_time=(index + 1) * width,
            word_timings=[],
            dramatic_events=[],
            impact_lock=False,
        )
        for index, section in enumerate(sections)
    ]


def _candidates(count: int = 40) -> list[visual_scoring.PanelCandidate]:
    return [
        visual_scoring.PanelCandidate(
            asset_id=f"asset-{index}",
            order_index=index,
            features=visual_scoring.VisualFeatures(
                face_visibility=0.8,
                action_pose=0.2 + (index % 4) * 0.1,
                dramatic_composition=0.7,
                focal_points=((0.2 + (index % 3) * 0.25, 0.3), (0.75, 0.7)),
                visual_signature=f"signature-{index}",
            ),
            visual_score=1.0 - index / 1000,
            semantic_score=0.8,
            source_family=f"family-{index}",
        )
        for index in range(count)
    ]


def _profile_shots() -> tuple[object, list[dict]]:
    profile = reference_profile.REFERENCE_MATCHED_SHORTS_V1
    candidates = _candidates()
    citations = {
        section: tuple(f"asset-{section_index * 8 + offset}" for offset in range(8))
        for section_index, section in enumerate(("hook", "setup", "conflict", "twist", "cta"))
    }
    return profile, editorial_visual_planner.plan(
        _spans(),
        candidates,
        profile=profile,
        cited_asset_ids_by_section=citations,
    )


def _assert_reference_pacing(profile, shots: list[dict]) -> None:
    assert len(shots) == 32
    assert shots[0]["start_time"] == pytest.approx(0.0)
    assert shots[-1]["end_time"] == pytest.approx(40.901, abs=0.002)
    for left, right in zip(shots, shots[1:], strict=False):
        assert left["end_time"] == pytest.approx(right["start_time"], abs=0.002)
    durations = [shot["end_time"] - shot["start_time"] for shot in shots]
    normal = [duration for duration in durations if profile.hold_min_s <= duration <= profile.hold_max_s]
    emphasis = [duration for duration in durations if profile.emphasis_min_s <= duration <= profile.emphasis_max_s]
    assert len(normal) + len(emphasis) == len(durations)
    assert len(normal) / len(durations) >= profile.hold_ratio_min
    assert len(normal) / len(durations) <= profile.hold_ratio_max
    assert len(emphasis) / len(durations) >= profile.emphasis_ratio_min
    assert len(emphasis) / len(durations) <= profile.emphasis_ratio_max
    assert profile.mean_shot_min_s <= sum(durations) / len(durations) <= profile.mean_shot_max_s
    assert shots[0]["transition"] == "none"
    assert all(shot["transition"] == "cut" for shot in shots[1:])
    forbidden = {"micro_shake", "impact_shake", "shake_zoom", "orbit", "punch_zoom", "whip_transition"}
    assert not any(str(shot.get("camera_curve", "")) in forbidden for shot in shots)
    assert all(shot["motion_mode"] in motion_director.MODES for shot in shots)
    assert all(shot["motion_reason"].strip() for shot in shots)
    assert all(shot["camera_curve"] in motion_director.ALLOWED_CURVES for shot in shots)
    assert all(shot.get("overlay_text", "") == "" for shot in shots)

    uses: dict[str, list[int]] = {}
    for index, shot in enumerate(shots):
        asset_id = shot["asset_id"]
        uses.setdefault(asset_id, []).append(index)
        if index and asset_id == shots[index - 1]["asset_id"]:
            raise AssertionError("reference panels may not be reused consecutively")
    assert all(len(indexes) <= profile.max_canonical_panel_uses for indexes in uses.values())
    for indexes in uses.values():
        if len(indexes) == 2:
            first, second = (shots[indexes[0]], shots[indexes[1]])
            assert (first["roi_label"], first["focus_x"], first["focus_y"]) != (
                second["roi_label"], second["focus_x"], second["focus_y"]
            )


def test_reference_planner_matches_empirical_32_shot_pacing_and_is_deterministic():
    profile, first = _profile_shots()
    _assert_reference_pacing(profile, first)
    _profile_again, second = _profile_shots()
    assert first == second


def test_reference_emphasis_slots_prioritize_camera_intent_deterministically():
    shots = [
        {"section": "setup", "camera_intent": "neutral"},
        {"section": "setup", "camera_intent": "action"},
        {"section": "setup", "camera_intent": "neutral"},
        {"section": "setup", "camera_intent": "reveal"},
        {"section": "setup", "camera_intent": "neutral"},
        {"section": "setup", "camera_intent": "neutral"},
        {"section": "setup", "camera_intent": "neutral"},
        {"section": "setup", "camera_intent": "neutral"},
    ]
    first = editorial_visual_planner._reference_emphasis_indexes(shots)
    second = editorial_visual_planner._reference_emphasis_indexes(shots)
    assert first == second
    assert {1, 3}.issubset(first)


def test_reference_emphasis_skips_a_one_shot_section():
    profile = reference_profile.REFERENCE_MATCHED_SHORTS_V1
    widths = (0.65, 10.00, 10.00, 10.00, 10.00)
    sections = ("hook", "setup", "conflict", "twist", "cta")
    spans: list[SimpleNamespace] = []
    cursor = 0.0
    for section, width in zip(sections, widths, strict=True):
        spans.append(
            SimpleNamespace(
                section=section,
                text=f"{section} reveals a grounded consequence.",
                start_time=cursor,
                end_time=cursor + width,
                word_timings=[],
                dramatic_events=[],
                impact_lock=False,
            )
        )
        cursor += width
    shots = editorial_visual_planner.plan(spans, _candidates(), profile=profile)
    hook = [shot for shot in shots if shot["section"] == "hook"]
    assert len(hook) == 1
    hook_duration = hook[0]["end_time"] - hook[0]["start_time"]
    assert profile.hold_min_s <= hook_duration <= profile.hold_max_s


def test_reference_planner_restricts_valid_citations_and_audits_alignment():
    profile = reference_profile.REFERENCE_MATCHED_SHORTS_V1
    candidates = _candidates()
    citations = {
        section: tuple(f"asset-{section_index * 8 + offset}" for offset in range(8))
        for section_index, section in enumerate(("hook", "setup", "conflict", "twist", "cta"))
    }
    shots = editorial_visual_planner.plan(
        _spans(), candidates, profile=profile, cited_asset_ids_by_section=citations
    )
    for shot in shots:
        assert shot["asset_id"] in citations[shot["section"]]
        assert any("citation" in reason for reason in shot["alignment_reasons"])


def test_reference_planner_fails_closed_when_reuse_constraints_are_impossible():
    with pytest.raises(editorial_visual_planner.ReferencePlanningError, match="reference_matched_shorts_v1"):
        editorial_visual_planner.plan(
            _spans(), _candidates(4), profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1
        )


def test_reference_planner_satisfies_sixteen_panel_twice_reuse_contract():
    profile = reference_profile.REFERENCE_MATCHED_SHORTS_V1
    shots = editorial_visual_planner.plan(_spans(), _candidates(16), profile=profile)
    assert len(shots) == 32
    positions: dict[str, list[int]] = {}
    for index, shot in enumerate(shots):
        positions.setdefault(shot["asset_id"], []).append(index)
    assert set(positions) == {f"asset-{index}" for index in range(16)}
    assert all(len(indexes) == 2 for indexes in positions.values())
    for indexes in positions.values():
        assert indexes[1] != indexes[0] + 1
        first, second = (shots[indexes[0]], shots[indexes[1]])
        assert (first["roi_label"], first["focus_x"], first["focus_y"]) != (
            second["roi_label"], second["focus_x"], second["focus_y"]
        )
        assert any("reuse_purpose:" in reason for reason in second["alignment_reasons"])


def test_reference_editorial_qc_rejects_reuse_and_low_hard_cut_ratio():
    profile = reference_profile.REFERENCE_MATCHED_SHORTS_V1
    consecutive = _qc_scenes(32, 40.901)
    consecutive[1].asset_id = consecutive[0].asset_id
    consecutive[1].visual_signature = consecutive[0].visual_signature
    consecutive[1].roi_label = consecutive[0].roi_label
    consecutive[1].focus_x = consecutive[0].focus_x
    consecutive[1].focus_y = consecutive[0].focus_y
    consecutive_report = editorial_qc.build_report(
        scenes=consecutive, cues=[], duration=40.901, profile=profile, preview=True
    )
    assert "reference.panel_reuse_consecutive" in consecutive_report.failures
    assert "reference.panel_reuse_same_roi" in consecutive_report.failures

    same_roi = _qc_scenes(32, 40.901)
    same_roi[2].asset_id = same_roi[0].asset_id
    same_roi[2].visual_signature = same_roi[0].visual_signature
    same_roi[2].roi_label = same_roi[0].roi_label
    same_roi[2].focus_x = same_roi[0].focus_x
    same_roi[2].focus_y = same_roi[0].focus_y
    same_roi_report = editorial_qc.build_report(
        scenes=same_roi, cues=[], duration=40.901, profile=profile, preview=True
    )
    assert "reference.panel_reuse_same_roi" in same_roi_report.failures

    low_cuts = _qc_scenes(32, 40.901)
    for scene in low_cuts:
        scene.transition = "fade"
    low_cut_report = editorial_qc.build_report(
        scenes=low_cuts, cues=[], duration=40.901, profile=profile, preview=True
    )
    assert "reference.hard_cut_ratio_below_85pct" in low_cut_report.failures


def test_reference_planner_uses_sparse_citations_then_contextual_fallback():
    profile = reference_profile.REFERENCE_MATCHED_SHORTS_V1
    shots = editorial_visual_planner.plan(
        _spans(),
        _candidates(16),
        profile=profile,
        cited_asset_ids_by_section={"hook": ("asset-0", "asset-1")},
    )
    hook_shots = [shot for shot in shots if shot["section"] == "hook"]
    assert len(hook_shots) > 4
    anchors = {"asset-0", "asset-1"}
    assert {shot["asset_id"] for shot in hook_shots} & anchors
    contextual = [shot for shot in hook_shots if shot["asset_id"] not in anchors]
    assert contextual
    assert all(
        any("evidence_context_fallback" in reason for reason in shot["alignment_reasons"])
        for shot in contextual
    )
    assert all(
        not any("citation_alignment" in reason for reason in shot["alignment_reasons"])
        for shot in contextual
    )


def test_reference_planner_marks_missing_evidence_as_deterministic_fallback():
    profile = reference_profile.REFERENCE_MATCHED_SHORTS_V1
    invalid = dict.fromkeys(("hook", "setup", "conflict", "twist", "cta"), ())
    invalid["setup"] = ("missing-panel",)
    first = editorial_visual_planner.plan(
        _spans(), _candidates(), profile=profile, cited_asset_ids_by_section=invalid
    )
    second = editorial_visual_planner.plan(
        _spans(), _candidates(), profile=profile, cited_asset_ids_by_section=invalid
    )
    assert first == second
    setup_shots = [shot for shot in first if shot["section"] == "setup"]
    assert setup_shots
    assert all("missing-panel" not in shot["asset_id"] for shot in setup_shots)
    assert all(
        any("evidence_fallback" in reason or "evidence_unavailable" in reason for reason in shot["alignment_reasons"])
        for shot in setup_shots
    )
    assert all(not any("citation_alignment" in reason for reason in shot["alignment_reasons"]) for shot in setup_shots)


def _qc_scenes(
    count: int, duration: float, cadence: bool = True
) -> list[SimpleNamespace]:
    emphasis_count = round(count * 0.25) if cadence and count == 32 else 0
    emphasis_duration = 1.70
    normal_count = count - emphasis_count
    normal_duration = (
        (duration - emphasis_count * emphasis_duration) / normal_count
        if normal_count
        else duration / count
    )
    scenes = []
    for index in range(count):
        shot_duration = emphasis_duration if index < emphasis_count else normal_duration
        start = sum(
            emphasis_duration if prior < emphasis_count else normal_duration
            for prior in range(index)
        )
        scenes.append(
            SimpleNamespace(
                asset_id=f"asset-{index}",
                visual_signature=f"signature-{index}",
                start_time=start,
                end_time=start + shot_duration,
                focus_x=0.2,
                focus_y=0.3,
                focus_end_x=0.3,
                focus_end_y=0.3,
                motion_mode="hold",
                motion_reason="stable editorial hold",
                camera_curve="static",
                camera_intent="neutral",
                transition="none" if index == 0 else "cut",
                transition_duration=0.0,
                source_family=f"family-{index}",
                roi_label=f"roi-{index}",
                alignment_reasons=["citation:asset-{index}"],
            )
        )
    return scenes


def test_reference_qc_uses_corrected_duration_bands_and_ratios():
    profile = reference_profile.REFERENCE_MATCHED_SHORTS_V1
    report = editorial_qc.build_report(
        scenes=_qc_scenes(32, 40.901),
        cues=[],
        duration=40.901,
        profile=profile,
        preview=True,
    )
    assert not any(code.startswith("reference.") for code in report.failures)

    invalid = editorial_qc.build_report(
        scenes=_qc_scenes(27, 45.0),
        cues=[],
        duration=45.0,
        profile=profile,
        preview=True,
    )
    assert "reference.shot_count_outside_28_36" in invalid.failures
    assert "reference.hold_ratio_below_70pct" in invalid.failures
    assert "reference.emphasis_ratio_over_30pct" in invalid.failures

    too_uniform = _qc_scenes(32, 40.901, cadence=False)
    uniform_report = editorial_qc.build_report(
        scenes=too_uniform,
        cues=[],
        duration=40.901,
        profile=profile,
        preview=True,
    )
    assert "reference.hold_ratio_over_80pct" in uniform_report.failures
    assert "reference.emphasis_ratio_below_20pct" in uniform_report.failures
    assert "motion_reason_missing" not in report.failures


def test_quality_repetition_check_applies_reference_reuse_cap():
    scenes = _qc_scenes(32, 40.901)
    scenes[2].asset_id = scenes[0].asset_id
    scenes[2].visual_signature = scenes[0].visual_signature
    scenes[4].asset_id = scenes[0].asset_id
    scenes[4].visual_signature = scenes[0].visual_signature
    results = quality.check_repetition_and_motion(
        scenes, profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1
    )
    assert any(result.code == "reference.panel_reuse_over_2" for result in results)


def test_quality_run_all_resolves_reference_profile_from_project(monkeypatch):
    captured: dict[str, object] = {}

    def repetition(_scenes, profile=None):
        captured["profile"] = profile
        return []

    monkeypatch.setattr(quality, "check_repetition_and_motion", repetition)
    project = SimpleNamespace(template="reference_matched_shorts_v1", target_duration=41, language="en")
    quality.run_all(project, [], None, [], [], [])
    assert captured["profile"] is reference_profile.REFERENCE_MATCHED_SHORTS_V1


def test_new_project_api_defaults_to_reference_profile_and_41_seconds(auth_client):
    response = auth_client.post("/api/projects", json={"title": "Reference default"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["template"] == "reference_matched_shorts_v1"
    assert body["target_duration"] == 41


def test_new_project_form_submits_reference_profile_explicitly():
    root = Path(__file__).resolve().parents[1]
    html = (root / "app" / "templates" / "index.html").read_text(encoding="utf-8")
    javascript = (root / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'id="p-template"' in html
    assert 'value="reference_matched_shorts_v1"' in html
    assert 'template: $(\'p-template\').value' in javascript
    assert "|| 41" in javascript


def _add_reference_project(db, duration: float, with_asset: bool = False) -> tuple[Project, ScriptVersion]:
    user = User(email=f"reference-{duration}-{with_asset}@example.com")
    workspace = Workspace(owner=user)
    project = Project(
        workspace=workspace,
        title="Reference timeline",
        template="reference_matched_shorts_v1",
        target_duration=41,
    )
    script = ScriptVersion(
        project=project,
        generator="vision_evidence_v2",
        sections=[
            {"section": section, "text": f"{section} evidence", "citations": []}
            for section in ("hook", "setup", "conflict", "twist", "cta")
        ],
        approved_by="reviewer",
        approved_at=datetime.now(UTC),
    )
    db.add(project)
    db.flush()
    for index, section in enumerate(("hook", "setup", "conflict", "twist", "cta")):
        start = duration * index / 5
        db.add(
            AudioSegment(
                script_version_id=script.id,
                section=section,
                order_index=index,
                text="grounded narration",
                spoken_text="grounded narration",
                start_time=start,
                end_time=duration * (index + 1) / 5,
                duration=duration / 5,
            )
        )
    if with_asset:
        asset = SourceAsset(
            project_id=project.id,
            type=AssetType.IMAGE,
            original_filename="synthetic.png",
            storage_key="projects/reference/synthetic.png",
            width=1080,
            height=1920,
        )
        db.add(asset)
        db.flush()
        analysis = StoryAnalysis(
            project_id=project.id,
            analysis_run_id="reference-run",
            state="RECONCILED",
            coverage_manifest_json={"coverage_ratio": 1.0, "unresolved_material_area": 0},
            reconciliation_json={"chain_reconciled": True, "coverage_map_hash": "a" * 64},
        )
        db.add(analysis)
        db.flush()
        panel = PanelRegion(
            story_analysis_id=analysis.id,
            source_asset_id=asset.id,
            strip_region_id="strip-3",
            panel_id="panel-3",
            source_order=3,
            original_width=1080,
            original_height=1920,
            bounds_json={"x": 0, "y": 0, "width": 1080, "height": 1920},
            coverage_map_hash="a" * 64,
        )
        db.add(panel)
        db.flush()
        sections = list(script.sections)
        sections[0] = {
            **sections[0],
            "evidence_panel_ids": [panel.panel_id],
            "citations": [3],
        }
        script.sections = sections
    db.flush()
    return project, script


def test_reference_timeline_rejects_bad_audio_before_deleting_existing_rows(db):
    project, _script = _add_reference_project(db, 37.0)
    old_scene = TimelineScene(
        project_id=project.id,
        order_index=0,
        start_time=0.0,
        end_time=1.0,
        transition="none",
    )
    old_cue = SubtitleCue(project_id=project.id, order_index=0, text="OLD", start_time=0.0, end_time=1.0)
    db.add_all([old_scene, old_cue])
    db.flush()

    with pytest.raises(pipeline.PipelineError, match="reference_matched_shorts_v1.*38.*50"):
        pipeline.build_timeline(db, project.id)
    assert db.get(TimelineScene, old_scene.id) is not None
    assert db.get(SubtitleCue, old_cue.id) is not None


def test_reference_timeline_preserves_rows_when_panel_pool_is_impossible(db):
    project, _script = _add_reference_project(db, 40.901)
    old_scene = TimelineScene(
        project_id=project.id,
        order_index=0,
        start_time=0.0,
        end_time=1.0,
        transition="none",
    )
    old_cue = SubtitleCue(
        project_id=project.id,
        order_index=0,
        text="OLD",
        start_time=0.0,
        end_time=1.0,
    )
    db.add_all([old_scene, old_cue])
    db.flush()

    with pytest.raises(pipeline.PipelineError, match="reference_planning_failed"):
        pipeline.build_timeline(db, project.id)
    assert db.get(TimelineScene, old_scene.id) is not None
    assert db.get(SubtitleCue, old_cue.id) is not None


def test_reference_timeline_passes_profile_and_section_citations_to_planner(db, monkeypatch):
    project, script = _add_reference_project(db, 40.901, with_asset=True)
    captured: dict[str, object] = {}

    def fake_score(_images, _reader):
        return []

    class StopPlanning(RuntimeError):
        pass

    def fake_plan(_spans, _candidates, **kwargs):
        captured.update(kwargs)
        raise StopPlanning

    monkeypatch.setattr(pipeline.visual_scoring, "analyze_assets", fake_score)
    monkeypatch.setattr(editorial_visual_planner, "plan", fake_plan)
    with pytest.raises(StopPlanning):
        pipeline.build_timeline(db, project.id)
    assert captured["profile"] is reference_profile.REFERENCE_MATCHED_SHORTS_V1
    citation_map = captured["cited_asset_ids_by_section"]
    assert project.assets[0].id in citation_map["hook"]
    assert "3" not in citation_map["hook"]
    assert script.sections[0]["evidence_panel_ids"] == ["panel-3"]
    assert script.sections[0]["citations"] == [3]


def _task6_evidence(panel_id, asset_id, source_order, *, status="known_empty"):
    from dataclasses import replace

    if status == "unknown":
        evidence = visual_scoring.PanelVisualEvidence(
            contract_version=visual_scoring.VISUAL_EVIDENCE_CONTRACT_VERSION,
            panel_id=panel_id,
            source_asset_id=asset_id,
            source_order=source_order,
            balloon_regions=(),
            protected_regions=(),
            balloon_mask_status="unknown",
            mask_confidence=0.0,
            evidence_source="vision_geometry_unavailable",
            mask_reason="geometry unavailable for this panel",
        )
    else:
        evidence = visual_scoring.PanelVisualEvidence(
            contract_version=visual_scoring.VISUAL_EVIDENCE_CONTRACT_VERSION,
            panel_id=panel_id,
            source_asset_id=asset_id,
            source_order=source_order,
            balloon_regions=(),
            protected_regions=(),
            balloon_mask_status="known_empty",
            mask_confidence=1.0,
            evidence_source="vision_geometry",
            mask_reason="vision adapter affirmatively found no speech balloon",
        )
    return replace(evidence, evidence_hash=visual_scoring.visual_evidence_hash(evidence))


def _task6_mask(framing, panel_size=(100, 200), *, edge=False):
    edge_mask = ((edge,),)
    empty_mask = ((False,),)
    mask_sha256 = framing._mask_hash(
        panel_size[0], panel_size[1], 1, 1, edge_mask, empty_mask, empty_mask
    )
    return framing.BorderMaskResult(
        detector_version=framing.DETECTOR_VERSION,
        source_width=panel_size[0],
        source_height=panel_size[1],
        grid_width=1,
        grid_height=1,
        edge_connected_mask=edge_mask,
        non_discardable_low_information_mask=empty_mask,
        protected_mask=empty_mask,
        edge_connected_blank_fraction=1.0 if edge else 0.0,
        non_discardable_low_information_fraction=0.0,
        protected_retained_fraction=1.0,
        mask_sha256=mask_sha256,
    )


def _task6_wrapper(
    panel_id,
    asset_id,
    source_order,
    *,
    framing,
    status="known_empty",
    roi_alternatives=None,
    mask_edge=False,
):
    from app.services import editorial_visual_planner as planner

    panel_size = (100, 200)
    evidence = _task6_evidence(
        panel_id, asset_id, source_order, status=status
    )
    panel_candidate = visual_scoring.PanelCandidate(
        asset_id=asset_id,
        order_index=source_order,
        features=visual_scoring.VisualFeatures(
            face_visibility=1.0,
            action_pose=1.0,
            dramatic_composition=1.0,
            focal_points=((0.5, 0.5),),
            visual_signature=f"task6-{panel_id}",
        ),
        visual_score=1.0,
        semantic_score=1.0,
        source_family="task6-family",
    )
    roi_type = getattr(planner, "ReferenceROIAlternative", None)
    candidate_type = getattr(planner, "ReferencePanelFallbackCandidate", None)
    assert roi_type is not None and candidate_type is not None
    alternatives = roi_alternatives or (
        roi_type(
            kind="primary",
            roi_label=f"roi-{panel_id}",
            crop_box=(0, 0, panel_size[0], panel_size[1]),
            focus=(0.5, 0.5, 0.5, 0.5),
        ),
        roi_type(
            kind="alternate_roi",
            roi_label=f"roi-{panel_id}-reuse",
            crop_box=(0, 0, panel_size[0] - 1, panel_size[1] - 1),
            focus=(0.45, 0.45, 0.55, 0.55),
        ),
    )
    return candidate_type(
        source_asset_id=asset_id,
        panel_region_id=f"region-{panel_id}",
        panel_id=panel_id,
        source_order=source_order,
        panel_bounds=(0, 0, panel_size[0], panel_size[1]),
        panel_size=panel_size,
        border_mask=_task6_mask(framing, panel_size, edge=mask_edge),
        source_asset_checksum=f"checksum-{asset_id}",
        visual_evidence=evidence,
        evidence_hash=evidence.evidence_hash,
        eligible_sections=("hook", "setup", "conflict", "twist", "cta"),
        eligible_beats=(),
        roi_alternatives=tuple(alternatives),
        panel_candidate=panel_candidate,
    )


def _task6_telemetry(framing, evidence, mask, crop_box=(0, 0, 100, 200)):
    return framing.FramingTelemetry(
        contract_version=evidence.contract_version,
        detector_version=mask.detector_version,
        mask_sha256=mask.mask_sha256,
        crop_box=crop_box,
        base_zoom=1.0,
        source_resolution_zoom_cap=1.15,
        protected_region_zoom_cap=1.15,
        edge_connected_blank_fraction=mask.edge_connected_blank_fraction,
        non_discardable_low_information_fraction=0.0,
        protected_retained_fraction=1.0,
        balloon_mask_intersection_ratio=0.0,
        subject_coverage=1.0,
        face_coverage=1.0,
        action_coverage=1.0,
        effect_coverage=1.0,
        continuity_context_coverage=1.0,
        mask_confidence=evidence.mask_confidence,
        mask_source=evidence.evidence_source,
        fallback_reason="",
        rejection_code=None,
    )


def _task6_wrappers(count=16, *, framing, shared=False, first_alternatives=None):
    wrappers = []
    if shared:
        wrappers.append(
            _task6_wrapper(
                "panel-shared-a",
                "asset-shared",
                1,
                framing=framing,
                roi_alternatives=first_alternatives,
            )
        )
        wrappers.append(
            _task6_wrapper(
                "panel-shared-b",
                "asset-shared",
                2,
                framing=framing,
                mask_edge=True,
            )
        )
        start = 1
        total = count - 1
    else:
        start = 0
        total = count
    for index in range(start, total):
        wrappers.append(
            _task6_wrapper(
                f"panel-{index}",
                f"asset-{index}",
                index + (2 if shared else 1),
                    framing=framing,
                roi_alternatives=(
                    first_alternatives if first_alternatives and index == start else None
                ),
            )
        )
    return tuple(wrappers)


def test_task6_panel_keyed_types_are_frozen_and_exact():
    import importlib

    planner = importlib.import_module("app.services.editorial_visual_planner")
    roi_type = getattr(planner, "ReferenceROIAlternative", None)
    candidate_type = getattr(planner, "ReferencePanelFallbackCandidate", None)
    assert roi_type is not None
    assert candidate_type is not None
    assert roi_type.__dataclass_params__.frozen is True
    assert candidate_type.__dataclass_params__.frozen is True
    assert tuple(roi_type.__dataclass_fields__) == (
        "kind",
        "roi_label",
        "crop_box",
        "focus",
    )
    assert tuple(candidate_type.__dataclass_fields__) == (
        "source_asset_id",
        "panel_region_id",
        "panel_id",
        "source_order",
        "panel_bounds",
        "panel_size",
        "border_mask",
        "source_asset_checksum",
        "visual_evidence",
        "evidence_hash",
        "eligible_sections",
        "eligible_beats",
        "roi_alternatives",
        "panel_candidate",
    )


def test_task6_wrapper_rejects_lineage_mask_hash_and_feasibility_bypass():
    import importlib
    from dataclasses import replace

    planner = importlib.import_module("app.services.editorial_visual_planner")
    framing = importlib.import_module("app.services.framing_analysis")
    candidate_type = getattr(planner, "ReferencePanelFallbackCandidate", None)
    assert candidate_type is not None
    valid = _task6_wrapper("panel-valid", "asset-valid", 1, framing=framing)
    with pytest.raises(Exception, match="visual.panel_lineage_unavailable"):
        candidate_type(
            **{
                **valid.__dict__,
                "panel_size": (101, 200),
            }
        )
    with pytest.raises(Exception, match="visual.panel_lineage_unavailable"):
        candidate_type(
            **{
                **valid.__dict__,
                "evidence_hash": "0" * 64,
            }
        )
    with pytest.raises(Exception, match="visual.panel_lineage_unavailable"):
        candidate_type(
            **{
                **valid.__dict__,
                "border_mask": replace(valid.border_mask, mask_sha256="0" * 64),
            }
        )
    assert "feasible" not in candidate_type.__dataclass_fields__


def test_task6_explicit_panel_path_calls_exact_feasibility_and_returns_lineage_ledger(monkeypatch):
    import importlib

    planner = importlib.import_module("app.services.editorial_visual_planner")
    framing = importlib.import_module("app.services.framing_analysis")
    candidate_type = getattr(planner, "ReferencePanelFallbackCandidate", None)
    assert candidate_type is not None
    wrappers = _task6_wrappers(framing=framing)
    calls = []

    def fake_feasibility(crop_box, evidence, border_mask, panel_size, target_size):
        calls.append(
            (
                crop_box,
                evidence.panel_id,
                border_mask.mask_sha256,
                panel_size,
                target_size,
            )
        )
        return True, _task6_telemetry(framing, evidence, border_mask, crop_box)

    monkeypatch.setattr(framing, "candidate_is_feasible", fake_feasibility)
    shots = planner.plan(
        _spans(),
        [wrapper.panel_candidate for wrapper in wrappers],
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        reference_panel_candidates=wrappers,
    )
    assert len(shots) == 32
    assert calls
    assert all(call[-1] == (1080, 1920) for call in calls)
    assert all(call[1] in {wrapper.panel_id for wrapper in wrappers} for call in calls)
    assert all("panel_region_id" in shot for shot in shots)
    assert all("panel_id" in shot for shot in shots)
    assert all(isinstance(shot["fallback_attempts"], list) for shot in shots)
    assert not hasattr(shots, "fallback_attempts")


def test_task6_same_asset_panels_keep_evidence_and_masks_distinct(monkeypatch):
    import importlib

    planner = importlib.import_module("app.services.editorial_visual_planner")
    framing = importlib.import_module("app.services.framing_analysis")
    candidate_type = getattr(planner, "ReferencePanelFallbackCandidate", None)
    assert candidate_type is not None
    wrappers = _task6_wrappers(17, framing=framing, shared=True)
    seen = []

    def fake_feasibility(crop_box, evidence, border_mask, panel_size, target_size):
        seen.append((evidence.panel_id, border_mask.mask_sha256))
        return True, _task6_telemetry(framing, evidence, border_mask, crop_box)

    monkeypatch.setattr(framing, "candidate_is_feasible", fake_feasibility)
    shots = planner.plan(
        _spans(),
        [wrapper.panel_candidate for wrapper in wrappers],
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        reference_panel_candidates=wrappers,
    )
    selected = {shot["panel_id"] for shot in shots}
    assert {"panel-shared-a", "panel-shared-b"} <= selected
    mask_by_panel = {
        wrapper.panel_id: wrapper.border_mask.mask_sha256 for wrapper in wrappers
    }
    assert all(mask_by_panel[panel_id] == mask_sha for panel_id, mask_sha in seen)
    assert any(panel_id == "panel-shared-a" for panel_id, _mask_sha in seen)
    assert any(panel_id == "panel-shared-b" for panel_id, _mask_sha in seen)


def test_task6_fallback_ledger_uses_same_panel_alternatives_before_other_panel(monkeypatch):
    import importlib

    planner = importlib.import_module("app.services.editorial_visual_planner")
    framing = importlib.import_module("app.services.framing_analysis")
    roi_type = getattr(planner, "ReferenceROIAlternative", None)
    candidate_type = getattr(planner, "ReferencePanelFallbackCandidate", None)
    assert roi_type is not None and candidate_type is not None
    alternatives = (
        roi_type("primary", "unsafe-first", (0, 0, 100, 200), (0.5, 0.5, 0.5, 0.5)),
        roi_type("alternate_roi", "safe-second", (0, 0, 100, 200), (0.5, 0.5, 0.5, 0.5)),
    )
    wrappers = _task6_wrappers(
        framing=framing, first_alternatives=alternatives
    )
    attempts = []

    def fake_feasibility(crop_box, evidence, border_mask, panel_size, target_size):
        attempts.append((evidence.panel_id, crop_box))
        telemetry = _task6_telemetry(framing, evidence, border_mask, crop_box)
        if evidence.panel_id == "panel-0" and len(attempts) == 1:
            return False, replace(telemetry, rejection_code="visual.balloon_mask_overlap")
        return True, telemetry

    from dataclasses import replace
    monkeypatch.setattr(framing, "candidate_is_feasible", fake_feasibility)
    shots = planner.plan(
        _spans(),
        [wrapper.panel_candidate for wrapper in wrappers],
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        reference_panel_candidates=wrappers,
    )
    first = shots[0]
    assert first["panel_id"] == "panel-0"
    assert [entry["roi_label"] for entry in first["fallback_attempts"][:2]] == [
        "unsafe-first",
        "safe-second",
    ]
    assert first["fallback_attempts"][0]["accepted"] is False
    assert first["fallback_attempts"][1]["accepted"] is True
    assert attempts[:2] == [("panel-0", (0, 0, 100, 200))] * 2


def test_task6_unknown_is_structural_but_reference_plan_and_qc_fail_closed():
    import importlib

    planner = importlib.import_module("app.services.editorial_visual_planner")
    quality_module = importlib.import_module("app.services.quality")
    framing = importlib.import_module("app.services.framing_analysis")
    candidate_type = getattr(planner, "ReferencePanelFallbackCandidate", None)
    assert candidate_type is not None
    unknown = _task6_wrapper(
        "panel-unknown", "asset-unknown", 1, framing=framing, status="unknown"
    )
    with pytest.raises(Exception, match="visual.balloon_mask_unknown"):
        planner.plan(
            _spans(),
            [unknown.panel_candidate],
            profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
            reference_panel_candidates=(unknown,),
        )
    evidence = unknown.visual_evidence
    mask = unknown.border_mask
    telemetry = _task6_telemetry(framing, evidence, mask)
    results = quality_module.check_reference_framing(
        [_task6_qc_scene(unknown, telemetry)],
        {(unknown.source_asset_id, unknown.panel_region_id): evidence},
        {(unknown.source_asset_id, unknown.panel_region_id): mask},
        {(unknown.source_asset_id, unknown.panel_region_id): unknown.panel_size},
        {(unknown.source_asset_id, unknown.panel_region_id): telemetry},
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
    )
    assert any(result.code == "visual.balloon_mask_unknown" for result in results)
    editorial_results = editorial_qc.check_reference_framing(
        [_task6_qc_scene(unknown, telemetry)],
        {(unknown.source_asset_id, unknown.panel_region_id): evidence},
        {(unknown.source_asset_id, unknown.panel_region_id): mask},
        {(unknown.source_asset_id, unknown.panel_region_id): unknown.panel_size},
        {(unknown.source_asset_id, unknown.panel_region_id): telemetry},
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
    )
    assert any(
        result.code == "visual.balloon_mask_unknown" for result in editorial_results
    )


def test_task6_rejects_ambiguous_lineage_and_speech_bubble_roi():
    import importlib
    from dataclasses import replace

    planner = importlib.import_module("app.services.editorial_visual_planner")
    framing = importlib.import_module("app.services.framing_analysis")
    candidate_type = getattr(planner, "ReferencePanelFallbackCandidate", None)
    roi_type = getattr(planner, "ReferenceROIAlternative", None)
    assert candidate_type is not None and roi_type is not None
    valid = _task6_wrapper("panel-valid-2", "asset-valid-2", 2, framing=framing)
    for changed in (
        {"panel_id": ""},
        {"panel_bounds": (0, 0, 0, 200)},
        {"source_asset_checksum": ""},
        {
            "visual_evidence": _task6_evidence(
                "foreign-panel", "asset-valid-2", 2
            ),
            "evidence_hash": "0" * 64,
        },
        {
            "panel_candidate": replace(
                valid.panel_candidate, asset_id="foreign-asset"
            )
        },
    ):
        with pytest.raises(Exception, match="visual.panel_lineage_unavailable"):
            candidate_type(**{**valid.__dict__, **changed})
    with pytest.raises(Exception, match="visual.balloon_mask_overlap"):
        roi_type(
            kind="speech_bubble",
            roi_label="speech-bubble",
            crop_box=(0, 0, 100, 200),
            focus=(0.5, 0.5, 0.5, 0.5),
        )


def test_task6_no_feasible_panel_fails_with_stable_visual_unavailable(monkeypatch):
    import importlib

    planner = importlib.import_module("app.services.editorial_visual_planner")
    framing = importlib.import_module("app.services.framing_analysis")
    wrappers = _task6_wrappers(framing=framing)

    def reject_all(crop_box, evidence, border_mask, panel_size, target_size):
        return False, replace(
            _task6_telemetry(framing, evidence, border_mask, crop_box),
            rejection_code="visual.balloon_mask_overlap",
        )

    from dataclasses import replace
    monkeypatch.setattr(framing, "candidate_is_feasible", reject_all)
    with pytest.raises(Exception, match="visual.visual_unavailable") as caught:
        planner.plan(
            _spans(),
            [wrapper.panel_candidate for wrapper in wrappers],
            profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
            reference_panel_candidates=wrappers,
        )
    assert caught.value.code == "visual.visual_unavailable"


def test_task6_same_asset_panels_fill_exact_capacity(monkeypatch):
    framing = __import__("app.services.framing_analysis", fromlist=["x"])
    wrappers = _task6_wrappers(count=16, framing=framing, shared=True)

    def fake_feasibility(crop_box, evidence, mask, panel_size, target_size):
        return True, _task6_telemetry(framing, evidence, mask, crop_box)

    monkeypatch.setattr(framing, "candidate_is_feasible", fake_feasibility)
    shots = editorial_visual_planner.plan(
        _spans(),
        [wrapper.panel_candidate for wrapper in wrappers],
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        reference_panel_candidates=wrappers,
    )
    assert len(shots) == 32
    counts: dict[str, int] = {}
    for shot in shots:
        counts[shot["panel_id"]] = counts.get(shot["panel_id"], 0) + 1
    assert len(counts) == 16
    assert set(counts) == {wrapper.panel_id for wrapper in wrappers}
    assert set(counts.values()) == {2}
    assert sum(shot["asset_id"] == "asset-shared" for shot in shots) == 4


@pytest.mark.parametrize(
    "bounds",
    (
        (-1, 0, 100, 200),
        (0, -1, 100, 200),
        (0, 0, 99, 200),
        (0, 0, 100, 199),
    ),
)
def test_task6_rejects_negative_or_dimension_mismatched_panel_bounds(bounds):
    from dataclasses import replace

    framing = __import__("app.services.framing_analysis", fromlist=["x"])
    valid = _task6_wrapper("panel-bounds", "asset-bounds", 1, framing=framing)
    with pytest.raises(editorial_visual_planner.ReferencePlanningError) as error:
        replace(valid, panel_bounds=bounds)
    assert error.value.code == "visual.panel_lineage_unavailable"


def test_task6_contract_mismatch_has_dedicated_failure_code():
    from dataclasses import replace

    framing = __import__("app.services.framing_analysis", fromlist=["x"])
    wrappers = _task6_wrappers(count=16, framing=framing)
    mismatched_profile = replace(
        reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        framing_contract_version="UNSUPPORTED_REFERENCE_CONTRACT",
    )
    with pytest.raises(editorial_visual_planner.ReferencePlanningError) as error:
        editorial_visual_planner.plan(
            _spans(),
            [wrapper.panel_candidate for wrapper in wrappers],
            profile=mismatched_profile,
            reference_panel_candidates=wrappers,
        )
    assert error.value.code == "visual.framing_contract_incompatible"


def _task6_out_of_order_rois(planner):
    return (
        planner.ReferenceROIAlternative(
            kind="tighter_crop",
            roi_label="tight-first-input",
            crop_box=(1, 1, 99, 199),
            focus=(0.45, 0.45, 0.55, 0.55),
        ),
        planner.ReferenceROIAlternative(
            kind="alternate_roi",
            roi_label="alternate-second-input",
            crop_box=(0, 0, 99, 200),
            focus=(0.40, 0.45, 0.60, 0.55),
        ),
        planner.ReferenceROIAlternative(
            kind="primary",
            roi_label="primary-last-input",
            crop_box=(0, 0, 100, 200),
            focus=(0.50, 0.50, 0.50, 0.50),
        ),
    )


def test_task6_enforces_same_panel_phase_order_and_reason(monkeypatch):
    from dataclasses import replace

    framing = __import__("app.services.framing_analysis", fromlist=["x"])
    wrappers = _task6_wrappers(
        count=16,
        framing=framing,
        first_alternatives=_task6_out_of_order_rois(editorial_visual_planner),
    )

    first_attempt_state = {"done": False}

    def fake_feasibility(crop_box, evidence, mask, panel_size, target_size):
        telemetry = _task6_telemetry(framing, evidence, mask, crop_box)
        if evidence.panel_id == "panel-0" and not first_attempt_state["done"]:
            if crop_box == (0, 0, 99, 200):
                first_attempt_state["done"] = True
                return True, telemetry
            return False, replace(telemetry, rejection_code="visual.visual_unavailable")
        return True, telemetry

    monkeypatch.setattr(framing, "candidate_is_feasible", fake_feasibility)
    shots = editorial_visual_planner.plan(
        _spans(),
        [wrapper.panel_candidate for wrapper in wrappers],
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        reference_panel_candidates=wrappers,
    )
    first = shots[0]
    assert first["panel_id"] == "panel-0"
    assert [entry["kind"] for entry in first["fallback_attempts"][:2]] == [
        "primary",
        "alternate_roi",
    ]
    assert "fallback:alternate_panel_same_beat" not in first["alignment_reasons"]
    assert "fallback:alternate_roi" in first["alignment_reasons"]


def test_task6_enforces_alternate_panel_phase_after_same_panel_attempts(monkeypatch):
    framing = __import__("app.services.framing_analysis", fromlist=["x"])
    wrappers = _task6_wrappers(
        count=16,
        framing=framing,
        first_alternatives=_task6_out_of_order_rois(editorial_visual_planner),
    )

    panel_zero_attempts = {"count": 0}

    def fake_feasibility(crop_box, evidence, mask, panel_size, target_size):
        telemetry = _task6_telemetry(framing, evidence, mask, crop_box)
        if evidence.panel_id == "panel-0" and panel_zero_attempts["count"] < 3:
            panel_zero_attempts["count"] += 1
            return False, replace(telemetry, rejection_code="visual.visual_unavailable")
        return True, telemetry

    from dataclasses import replace

    monkeypatch.setattr(framing, "candidate_is_feasible", fake_feasibility)
    shots = editorial_visual_planner.plan(
        _spans(),
        [wrapper.panel_candidate for wrapper in wrappers],
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        reference_panel_candidates=wrappers,
    )
    first = shots[0]
    assert first["panel_id"] != "panel-0"
    assert [entry["kind"] for entry in first["fallback_attempts"][:4]] == [
        "primary",
        "alternate_roi",
        "tighter_crop",
        "alternate_panel",
    ]
    assert "fallback:alternate_panel_same_beat" in first["alignment_reasons"]


def test_task6_shot_contains_accepted_telemetry_and_selection_context(monkeypatch):
    framing = __import__("app.services.framing_analysis", fromlist=["x"])
    wrappers = _task6_wrappers(count=16, framing=framing)

    def fake_feasibility(crop_box, evidence, mask, panel_size, target_size):
        return True, _task6_telemetry(framing, evidence, mask, crop_box)

    monkeypatch.setattr(framing, "candidate_is_feasible", fake_feasibility)
    shots = editorial_visual_planner.plan(
        _spans(),
        [wrapper.panel_candidate for wrapper in wrappers],
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        reference_panel_candidates=wrappers,
    )
    for shot in shots:
        telemetry = shot["framing_telemetry"]
        assert telemetry["crop_box"] == shot["roi"]["crop_box"]
        assert telemetry["mask_sha256"] == shot["border_mask"]["mask_sha256"]
        assert telemetry["candidate_count"] >= 1
        assert telemetry["selection_context"]["selected_panel_id"] == shot["panel_id"]
        assert telemetry["selection_context"]["selected_attempt_order"] >= 0


def _task6_qc_scene(candidate, telemetry, *, roi_crop=None, fallback_attempts=None):
    from dataclasses import asdict

    crop = roi_crop if roi_crop is not None else telemetry.crop_box
    telemetry_json = asdict(telemetry)
    telemetry_json["selected_roi"] = {
        "kind": "primary",
        "roi_label": "qc-roi",
        "crop_box": list(crop),
        "focus": [0.5, 0.5, 0.5, 0.5],
    }
    default_attempt = {
        "attempt_order": 0,
        "panel_region_id": candidate.panel_region_id,
        "panel_id": candidate.panel_id,
        "source_asset_id": candidate.source_asset_id,
        "source_asset_checksum": candidate.source_asset_checksum,
        "source_order": candidate.source_order,
        "panel_size": list(candidate.panel_size),
        "roi_label": "qc-roi",
        "crop_box": list(crop),
        "evidence_hash": candidate.evidence_hash,
        "detector_version": candidate.border_mask.detector_version,
        "mask_sha256": candidate.border_mask.mask_sha256,
        "telemetry": dict(telemetry_json),
        "kind": "primary",
        "roi_kind": "primary",
        "accepted": True,
        "code": None,
        "reason": "accepted",
    }
    return {
        "asset_id": candidate.source_asset_id,
        "source_asset_id": candidate.source_asset_id,
        "panel_region_id": candidate.panel_region_id,
        "panel_id": candidate.panel_id,
        "source_order": candidate.source_order,
        "panel_bounds": list(candidate.panel_bounds),
        "panel_size": list(candidate.panel_size),
        "source_asset_checksum": candidate.source_asset_checksum,
        "evidence_hash": candidate.evidence_hash,
        "visual_evidence": visual_scoring.panel_visual_evidence_json(candidate.visual_evidence),
        "border_mask": asdict(candidate.border_mask),
        "roi": {"crop_box": list(crop), "roi_label": "qc-roi", "kind": "primary"},
        "framing_telemetry": telemetry_json,
        "fallback_attempts": fallback_attempts or [default_attempt],
    }


def test_task6_qc_uses_scene_exact_telemetry_for_reused_panel(monkeypatch):
    from dataclasses import replace

    framing = __import__("app.services.framing_analysis", fromlist=["x"])
    candidate = _task6_wrapper("panel-qc", "asset-qc", 1, framing=framing)
    good = _task6_telemetry(framing, candidate.visual_evidence, candidate.border_mask)
    distinct = replace(good, crop_box=(1, 1, 100, 200))
    key = (candidate.source_asset_id, candidate.panel_region_id)
    common = (
        {key: candidate.visual_evidence},
        {key: candidate.border_mask},
        {key: candidate.panel_size},
        {key: good},
    )
    valid_results = quality.check_reference_framing(
        [
            _task6_qc_scene(candidate, good),
            _task6_qc_scene(candidate, distinct, roi_crop=distinct.crop_box),
        ],
        *common,
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
    )
    assert [result.code for result in valid_results] == ["visual.reference_framing"]
    tampered_results = quality.check_reference_framing(
        [
            _task6_qc_scene(candidate, good),
            _task6_qc_scene(candidate, distinct, roi_crop=good.crop_box),
        ],
        *common,
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
    )
    assert "visual.panel_lineage_unavailable" in {
        result.code for result in tampered_results
    }


def test_task6_qc_rejects_tampered_accepted_ledger():
    from copy import deepcopy

    framing = __import__("app.services.framing_analysis", fromlist=["x"])
    candidate = _task6_wrapper("panel-ledger", "asset-ledger", 1, framing=framing)
    telemetry = _task6_telemetry(framing, candidate.visual_evidence, candidate.border_mask)
    scene = _task6_qc_scene(candidate, telemetry)
    tampered = deepcopy(scene)
    tampered["fallback_attempts"][0]["crop_box"] = [1, 1, 100, 200]
    key = (candidate.source_asset_id, candidate.panel_region_id)
    results = quality.check_reference_framing(
        [tampered],
        {key: candidate.visual_evidence},
        {key: candidate.border_mask},
        {key: candidate.panel_size},
        {key: telemetry},
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
    )
    assert "visual.panel_lineage_unavailable" in {result.code for result in results}


def test_task6_qc_compares_complete_border_mask_snapshot():
    from copy import deepcopy

    framing = __import__("app.services.framing_analysis", fromlist=["x"])
    candidate = _task6_wrapper("panel-mask-snapshot", "asset-mask-snapshot", 1, framing=framing)
    telemetry = _task6_telemetry(framing, candidate.visual_evidence, candidate.border_mask)
    scene = _task6_qc_scene(candidate, telemetry)
    tampered = deepcopy(scene)
    tampered["border_mask"]["grid_width"] = 2
    key = (candidate.source_asset_id, candidate.panel_region_id)
    results = quality.check_reference_framing(
        [tampered],
        {key: candidate.visual_evidence},
        {key: candidate.border_mask},
        {key: candidate.panel_size},
        {key: telemetry},
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
    )
    assert "visual.panel_lineage_unavailable" in {result.code for result in results}


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("subject_coverage", float("nan")),
        ("face_coverage", float("inf")),
        ("balloon_mask_intersection_ratio", -0.01),
        ("edge_connected_blank_fraction", 1.01),
    ),
)
def test_task6_qc_rejects_nonfinite_or_out_of_range_telemetry(field, value):
    from dataclasses import replace

    framing = __import__("app.services.framing_analysis", fromlist=["x"])
    candidate = _task6_wrapper("panel-fraction", "asset-fraction", 1, framing=framing)
    telemetry = _task6_telemetry(framing, candidate.visual_evidence, candidate.border_mask)
    telemetry = replace(telemetry, **{field: value})
    scene = _task6_qc_scene(candidate, telemetry)
    key = (candidate.source_asset_id, candidate.panel_region_id)
    results = quality.check_reference_framing(
        [scene],
        {key: candidate.visual_evidence},
        {key: candidate.border_mask},
        {key: candidate.panel_size},
        {key: telemetry},
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
    )
    assert "visual.panel_lineage_unavailable" in {result.code for result in results}


def test_task6_alternate_panel_runs_all_roi_phases(monkeypatch):
    from dataclasses import replace

    framing = __import__("app.services.framing_analysis", fromlist=["x"])
    wrappers = _task6_wrappers(
        count=16,
        framing=framing,
        first_alternatives=_task6_out_of_order_rois(editorial_visual_planner),
    )
    panel_zero_attempts = {"count": 0}
    panel_one_primary_failed = {"value": False}

    def fake_feasibility(crop_box, evidence, mask, panel_size, target_size):
        telemetry = _task6_telemetry(framing, evidence, mask, crop_box)
        if evidence.panel_id == "panel-0" and panel_zero_attempts["count"] < 3:
            panel_zero_attempts["count"] += 1
            return False, replace(telemetry, rejection_code="visual.visual_unavailable")
        if evidence.panel_id == "panel-1" and not panel_one_primary_failed["value"]:
            if crop_box == (0, 0, 100, 200):
                panel_one_primary_failed["value"] = True
                return False, replace(telemetry, rejection_code="visual.visual_unavailable")
            return True, telemetry
        return True, telemetry

    monkeypatch.setattr(framing, "candidate_is_feasible", fake_feasibility)
    shots = editorial_visual_planner.plan(
        _spans(),
        [wrapper.panel_candidate for wrapper in wrappers],
        profile=reference_profile.REFERENCE_MATCHED_SHORTS_V1,
        reference_panel_candidates=wrappers,
    )
    first = shots[0]
    assert first["panel_id"] == "panel-1"
    assert first["fallback_attempts"][-1]["kind"] == "alternate_panel"
    assert first["fallback_attempts"][-1]["roi_kind"] == "alternate_roi"
