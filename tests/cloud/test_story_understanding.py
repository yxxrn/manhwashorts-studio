"""Regression coverage for the grounded story-understanding prewriter stage."""
from __future__ import annotations

import pytest

from app.services import story_understanding
from tests.cloud.mass_support import _FakeProvider, _identity, _module, _panels


def test_story_signal_prefers_meaningful_dialogue_over_visual_noise():
    assert story_understanding.story_text_is_meaningful(
        "OUR TIME WILL BE OFFSET FROM THE HUMAN WORLD BY HALF A CENTURY."
    )
    assert not story_understanding.story_text_is_meaningful("AAARGH!!!")
    assert not story_understanding.story_text_is_meaningful("Read at ASURASCANS.COM")
    assert story_understanding.panel_story_signal(
        {"dialogue_or_ocr": ["THE FINAL WAR CANNOT BE AVOIDED."], "inferences": []}
    ) > story_understanding.panel_story_signal(
        {"dialogue_or_ocr": [], "inferences": ["blue light is visible"]}
    )


def test_story_signal_accepts_structured_ocr_rows():
    observation = {
        "dialogue_or_ocr": [{"text": "THE FINAL WAR CANNOT BE AVOIDED."}],
        "inferences": [],
    }
    assert story_understanding.dialogue_text(observation["dialogue_or_ocr"][0]) == (
        "THE FINAL WAR CANNOT BE AVOIDED."
    )
    assert story_understanding.panel_story_signal(observation) == 4


def test_story_understanding_accepts_direct_ocr_fact_without_story_claim():
    result = story_understanding.validate_result(
        {
            "narration_ready_beats": [
                {
                    "beat_id": "beat-a",
                    "story_role": "reveal",
                    "fact": "Humanity nearly faced extinction in the remembered event.",
                    "evidence_panel_ids": ["p1"],
                    "source_claim_ids": [],
                    "confidence": "explicit",
                    "qualification": "",
                },
                {
                    "beat_id": "beat-b",
                    "story_role": "consequence",
                    "fact": "Miro remains in her own spacetime.",
                    "evidence_panel_ids": ["p2"],
                    "source_claim_ids": [],
                    "confidence": "explicit",
                    "qualification": "",
                },
            ],
            "unresolved_threads": [],
        },
        expected_panel_ids=("p1", "p2"),
        story_map={"claims": []},
    )
    assert result["version"] == story_understanding.STORY_UNDERSTANDING_VERSION
    assert len(result["understanding_hash"]) == 64

def test_materialized_story_claims_preserve_exact_panel_lineage():
    claims = story_understanding.materialize_grounded_claims({
        "narration_ready_beats": [{
            "beat_id": "reveal-1",
            "story_role": "reveal",
            "fact": "Humanity nearly faced extinction.",
            "evidence_panel_ids": ["p9"],
            "source_claim_ids": [],
            "confidence": "explicit",
            "qualification": "",
        }]
    })
    assert len(claims) == 1
    assert claims[0]["claim_id"].startswith("story_understanding__")
    assert claims[0]["claim_type"] == "fact"
    assert claims[0]["evidence_panel_ids"] == ["p9"]
    assert claims == story_understanding.materialize_grounded_claims({
        "narration_ready_beats": [{
            "beat_id": "reveal-1", "story_role": "reveal",
            "fact": "Humanity nearly faced extinction.", "evidence_panel_ids": ["p9"],
            "source_claim_ids": [], "confidence": "explicit", "qualification": "",
        }]
    })


def test_story_understanding_rejects_foreign_panel_reference():
    with pytest.raises(story_understanding.StoryUnderstandingError):
        story_understanding.validate_result(
            {
                "narration_ready_beats": [
                    {
                        "beat_id": "beat-a",
                        "story_role": "setup",
                        "fact": "A grounded event occurs.",
                        "evidence_panel_ids": ["foreign"],
                        "source_claim_ids": [],
                        "confidence": "explicit",
                        "qualification": "",
                    },
                    {
                        "beat_id": "beat-b",
                        "story_role": "consequence",
                        "fact": "A grounded consequence follows.",
                        "evidence_panel_ids": ["p1"],
                        "source_claim_ids": [],
                        "confidence": "explicit",
                        "qualification": "",
                    },
                ],
                "unresolved_threads": [],
            },
            expected_panel_ids=("p1",),
            story_map={"claims": []},
        )


def test_narration_receives_story_understanding_before_writer(tmp_path):
    module = _module()
    provider = _FakeProvider()
    panels = _panels(module)
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(tmp_path / "story-understanding-cache"),
        max_attempts=1,
    )
    visual = runner.run_visual_evidence(panels)
    visual.panels[0]["observation"]["dialogue_or_ocr"] = [
        "The route to the human world has changed."
    ]
    story_map = runner.run_story_map(visual)
    result = runner.run_narration(visual, story_map, panels=panels)

    stages = [call[0] for call in provider.calls]
    assert stages.index("story_understanding") < stages.index("narration")
    payload = provider.narration_payloads[-1]
    context = payload["story_understanding"]
    assert context["version"] == story_understanding.STORY_UNDERSTANDING_VERSION
    assert context["narration_ready_beats"]
    assert result.qc_report["story_understanding_hash"] == context["understanding_hash"]
    assert result.qc_report["narration_cache_contract"] == "narration-final-v2"
