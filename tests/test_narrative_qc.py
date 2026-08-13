"""RED contract for non-rewriting Sharp Friend naturalness screening."""

from __future__ import annotations

from dataclasses import asdict

import pytest

from app.services import editorial_qc, narrative_identity, quality


def _profile():
    return narrative_identity.get_narrative_identity("sharp_friend_v1")


def _claims(*, qualified: bool = True, dialogue: str | None = None):
    claim = {
        "claim_id": "claim-route",
        "claim_type": "interpretation",
        "text": "The visible route changes the group's next decision.",
        "qualification": (
            "The sequence suggests this reading without proving the motive."
            if qualified
            else ""
        ),
        "evidence_panel_ids": ["panel-a", "panel-b"],
    }
    if dialogue is not None:
        claim["dialogue_or_ocr"] = [dialogue]
    return {"claim-route": claim}


def _passages(*, final: str = "That consequence leaves the next move unresolved."):
    return [
        {
            "passage_id": "p1",
            "editorial_role": "opening",
            "text": "The route looks quiet, but the choice underneath it is not.",
            "claim_ids": ["claim-route"],
            "evidence_panel_ids": ["panel-a", "panel-b"],
        },
        {
            "passage_id": "p2",
            "editorial_role": "turn",
            "text": "The group hesitates because the visible clue points somewhere new.",
            "claim_ids": ["claim-route"],
            "evidence_panel_ids": ["panel-a", "panel-b"],
        },
        {
            "passage_id": "p3",
            "editorial_role": "consequence",
            "text": final,
            "claim_ids": ["claim-route"],
            "evidence_panel_ids": ["panel-a", "panel-b"],
        },
        {
            "passage_id": "p4",
            "editorial_role": "open_loop",
            "text": "Meanwhile, the unanswered detail keeps the pressure moving forward.",
            "claim_ids": ["claim-route"],
            "evidence_panel_ids": ["panel-a", "panel-b"],
        },
    ]


def test_screen_naturalness_returns_deterministic_safe_metrics_without_prose():
    passages = _passages()
    report = editorial_qc.screen_narrative_naturalness(
        passages, _claims(), _profile()
    )
    again = editorial_qc.screen_narrative_naturalness(
        passages, _claims(), _profile()
    )

    assert report == again
    assert report.total_words == sum(len(item["text"].split()) for item in passages)
    assert report.sentence_length_p10 <= report.sentence_length_p50 <= report.sentence_length_p90
    assert report.sentence_length_variance >= 0.0
    assert 0.0 <= report.repeated_normalized_sentence_ratio <= 1.0
    assert 0.0 <= report.repeated_opening_ngram_ratio <= 1.0
    assert report.connector_diversity_count >= 2
    assert 0.0 <= report.causal_transition_coverage <= 1.0
    assert report.contraction_count == 0
    assert report.claim_evidence_coverage_ratio == 1.0
    assert report.qualified_interpretation_coverage_ratio == 1.0
    assert not any("route looks quiet" in str(value) for value in asdict(report).values())


def test_contractions_and_human_rhythm_are_allowed_without_a_quota():
    passages = _passages()
    passages[0]["text"] = "It's a small clue, yet it changes what the group can risk."
    report = editorial_qc.screen_narrative_naturalness(
        passages, _claims(), _profile()
    )
    results = quality.check_narrative_naturalness(report)

    assert report.contraction_count == 1
    assert not any(result.code == "narrative.rhythm_warning" for result in results)
    assert all(result.passed for result in results)


def test_screening_marks_repeated_openings_as_warning_and_does_not_rewrite():
    passages = _passages()
    passages[1]["text"] = passages[0]["text"]
    before = [item["text"] for item in passages]
    report = editorial_qc.screen_narrative_naturalness(
        passages, _claims(), _profile()
    )
    results = quality.check_narrative_naturalness(report)

    assert [item["text"] for item in passages] == before
    assert "narrative.template_risk" in report.warnings
    assert any(
        result.code == "narrative.template_risk"
        and result.severity == "warning"
        and not result.passed
        for result in results
    )
    assert not any(result.blocking for result in results)


@pytest.mark.parametrize(
    ("kind", "expected_code"),
    (
        ("cta", "narrative.cta"),
        ("hype", "narrative.generic_hype"),
        ("unsupported", "narrative.unsupported_claim"),
        ("unqualified", "narrative.interpretation_unqualified"),
        ("copied", "narrative.balloon_dialogue_copied"),
    ),
)
def test_prohibited_or_ungrounded_narration_is_blocking(kind, expected_code):
    passages = _passages()
    claims = _claims()
    if kind == "cta":
        passages[0]["text"] = "Like this video and subscribe for more chapters."
    elif kind == "hype":
        passages[0]["text"] = "This epic battle unleashes unstoppable attack energy."
    elif kind == "unsupported":
        passages[0]["claim_ids"] = ["claim-missing"]
    elif kind == "unqualified":
        claims = _claims(qualified=False)
    else:
        passages[0]["text"] = "The guard says hold the line before the route changes."
        claims = _claims(dialogue="The guard says hold the line before the route changes.")

    report = editorial_qc.screen_narrative_naturalness(passages, claims, _profile())
    results = quality.check_narrative_naturalness(report)

    assert any(result.code == expected_code and result.blocking for result in results)


def test_story_commentary_phrases_are_not_misclassified_as_cta():
    passages = _passages()
    passages[0]["text"] = "She moves like lightning, and I like this story's hidden clue."
    report = editorial_qc.screen_narrative_naturalness(
        passages, _claims(), _profile()
    )
    results = quality.check_narrative_naturalness(report)

    assert report.cta_hits == ()
    assert not any(result.code == "narrative.cta" for result in results)
