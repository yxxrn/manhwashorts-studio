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
