"""Slice E Task 6 review fixtures: approval only, with no media work."""

from __future__ import annotations

import copy

import pytest

from tests.factories.narrative import _seed_sharp_friend


@pytest.mark.parametrize("ending_kind", ("consequence", "open_question"))
def test_review_accepts_two_ending_styles_and_stops_before_media(
    db, monkeypatch, ending_kind: str
):
    from app.services import pipeline as pipeline_service

    project, analysis = _seed_sharp_friend(db)
    if ending_kind == "open_question":
        passages = copy.deepcopy(analysis.evidence_graph_json["script_passages"])
        passages[-1]["text"] = "Mara waits outside, but who claimed the dark boat?"
        analysis.evidence_graph_json = {
            **analysis.evidence_graph_json,
            "script_passages": passages,
        }
        reconciliation = dict(analysis.reconciliation_json)
        reconciliation["narrative_ending_kind"] = ending_kind
        analysis.reconciliation_json = reconciliation
        db.flush()

    script = pipeline_service.generate_script(
        db,
        project.id,
        actor_id="reviewer-1",
        narrative_profile_id="sharp_friend_v1",
    )
    for stage in ("generate_voiceover", "build_timeline"):
        monkeypatch.setattr(
            pipeline_service,
            stage,
            lambda *args, _stage=stage, **kwargs: pytest.fail(
                f"narrative review entered {_stage}"
            ),
        )
    monkeypatch.setattr(
        pipeline_service,
        "render_video",
        lambda *args, **kwargs: pytest.fail("narrative review entered render"),
        raising=False,
    )

    approved = pipeline_service.approve_script(
        db,
        script.id,
        actor_id="reviewer-1",
        editorial_review_confirmed=True,
    )

    assert approved.approved_by == "reviewer-1"
    assert approved.editorial_metadata["editorial_review_confirmed"] is True
    assert analysis.state == "SCRIPT_APPROVED"
    assert len(analysis.evidence_graph_json["script_passages"]) == 4


def test_review_rejects_cta_after_user_edit_without_changing_approval_state(db):
    from app.services import pipeline as pipeline_service

    project, _analysis = _seed_sharp_friend(db)
    script = pipeline_service.generate_script(
        db,
        project.id,
        actor_id="reviewer-1",
        narrative_profile_id="sharp_friend_v1",
    )
    edited = copy.deepcopy(script.sections)
    edited[0]["text"] = "Subscribe now for more stories."
    pipeline_service.update_script(
        db,
        script.id,
        edited,
        actor_id="reviewer-1",
    )

    with pytest.raises(pipeline_service.PipelineError, match="screening"):
        pipeline_service.approve_script(
            db,
            script.id,
            actor_id="reviewer-1",
            editorial_review_confirmed=True,
        )
    assert script.approved_at is None
    assert script.editorial_metadata["editorial_review_confirmed"] is False


def test_review_preserves_spoken_text_and_derives_display_later(db):
    from app.services import pipeline as pipeline_service
    from app.services import timeline

    project, _analysis = _seed_sharp_friend(db)
    script = pipeline_service.generate_script(
        db,
        project.id,
        actor_id="reviewer-1",
        narrative_profile_id="sharp_friend_v1",
    )

    spoken = [section["text"] for section in script.sections]
    display = [timeline.normalize_display_text(text) for text in spoken]
    assert any("." in text or "," in text for text in spoken)
    assert all("display_text" not in section for section in script.sections)
    assert all(token and token == token.upper() for token in display[0].split())
    assert any(display_text != spoken_text for display_text, spoken_text in zip(display, spoken, strict=True))


def test_review_uses_complete_ordered_panel_evidence_without_sampling(db):
    from app.models import PanelRegion
    from app.services import pipeline as pipeline_service

    project, analysis = _seed_sharp_friend(db)
    script = pipeline_service.generate_script(
        db,
        project.id,
        actor_id="reviewer-1",
        narrative_profile_id="sharp_friend_v1",
    )
    panels = list(
        db.query(PanelRegion)
        .filter(PanelRegion.story_analysis_id == analysis.id)
        .order_by(PanelRegion.source_order)
    )
    assert [panel.source_order for panel in panels] == [0, 1, 2]
    referenced = {
        panel_id
        for section in script.sections
        for panel_id in section["evidence_panel_ids"]
    }
    assert referenced == {panel.panel_id for panel in panels}


@pytest.mark.parametrize(
    "mutation",
    ("cta", "hype", "copied_dialogue", "missing_claim", "unqualified", "ending"),
)
def test_review_rejects_invalid_narrative_contracts_before_script_materialization(
    db, mutation: str
):
    from app.models import PanelRegion
    from app.services import pipeline as pipeline_service

    project, analysis = _seed_sharp_friend(db)
    passages = copy.deepcopy(analysis.evidence_graph_json["script_passages"])
    if mutation == "cta":
        passages[0]["text"] = "Subscribe now for more stories."
    elif mutation == "hype":
        passages[0]["text"] = "This is an epic battle with insane power."
    elif mutation == "missing_claim":
        passages[0]["claim_ids"] = ["claim-does-not-exist"]
    elif mutation == "unqualified":
        graph = copy.deepcopy(analysis.evidence_graph_json)
        graph["claims"][1]["qualification"] = ""
        analysis.evidence_graph_json = graph
    elif mutation == "ending":
        reconciliation = dict(analysis.reconciliation_json)
        reconciliation["narrative_ending_kind"] = "open_question"
        analysis.reconciliation_json = reconciliation
    else:
        observations = []
        for panel in db.query(PanelRegion).filter(PanelRegion.story_analysis_id == analysis.id):
            observation = dict(panel.observation_json)
            observation["dialogue_or_ocr"] = [
                "Mara waits outside and the next panel"
            ]
            panel.observation_json = observation
            observations.append(observation)
        assert observations
    analysis.evidence_graph_json = {
        **analysis.evidence_graph_json,
        "script_passages": passages,
    }
    db.flush()

    with pytest.raises(pipeline_service.PipelineError):
        pipeline_service.generate_script(
            db,
            project.id,
            actor_id="reviewer-1",
            narrative_profile_id="sharp_friend_v1",
        )


def test_review_template_risk_is_a_warning_not_an_automatic_rewrite(db):
    from app.services import pipeline as pipeline_service

    project, analysis = _seed_sharp_friend(db)
    passages = copy.deepcopy(analysis.evidence_graph_json["script_passages"])
    for passage in passages:
        passage["text"] = "The clue changes the choice because " + passage["text"]
    analysis.evidence_graph_json = {
        **analysis.evidence_graph_json,
        "script_passages": passages,
    }
    db.flush()

    script = pipeline_service.generate_script(
        db,
        project.id,
        actor_id="reviewer-1",
        narrative_profile_id="sharp_friend_v1",
    )

    assert any(warning["code"] == "narrative.template_risk" for warning in script.warnings)
    assert [section["text"] for section in script.sections] == [
        passage["text"] for passage in passages
    ]
