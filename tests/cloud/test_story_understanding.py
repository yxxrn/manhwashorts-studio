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
    assert story_understanding.panel_story_signal(observation) > 0


def test_story_understanding_accepts_direct_ocr_fact_without_story_claim():
    result = story_understanding.validate_result(
        {
            "narration_ready_beats": [
                {
                    "beat_id": "beat-a",
                    "story_role": "reveal",
                    "fact": "Humanity nearly faced extinction in the remembered event.",
                    "narrative_function": "Preserve the grounded story meaning.",
                    "change": "", "consequence": "", "open_question": "",
                    "importance": 4, "evidence_strength": "ocr_explicit",
                    "evidence_panel_ids": ["p1"],
                    "source_claim_ids": [],
                    "confidence": "explicit",
                    "qualification": "",
                },
                {
                    "beat_id": "beat-b",
                    "story_role": "consequence",
                    "fact": "Miro remains in her own spacetime.",
                    "narrative_function": "Preserve the grounded story meaning.",
                    "change": "", "consequence": "", "open_question": "",
                    "importance": 4, "evidence_strength": "ocr_explicit",
                    "evidence_panel_ids": ["p2"],
                    "source_claim_ids": [],
                    "confidence": "explicit",
                    "qualification": "",
                },
            ],
            "entity_registry": [],
            "unresolved_threads": [],
        },
        expected_panel_ids=("p1", "p2"),
        story_map={"claims": []},
    )
    assert result["version"] == story_understanding.STORY_UNDERSTANDING_VERSION
    assert len(result["understanding_hash"]) == 64

def test_materialized_story_claims_preserve_exact_panel_lineage():
    claims = story_understanding.materialize_grounded_claims({
        "entity_registry": [],
        "narration_ready_beats": [{
            "beat_id": "reveal-1",
            "story_role": "reveal",
            "fact": "Humanity nearly faced extinction.",
            "narrative_function": "Preserve the reveal.", "change": "",
            "consequence": "", "open_question": "", "importance": 5,
            "evidence_strength": "ocr_explicit", "entity_ids": [],
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
        "entity_registry": [],
        "narration_ready_beats": [{
            "beat_id": "reveal-1", "story_role": "reveal",
            "fact": "Humanity nearly faced extinction.", "narrative_function": "Preserve the reveal.",
            "change": "", "consequence": "", "open_question": "", "importance": 5,
            "evidence_strength": "ocr_explicit", "entity_ids": [], "evidence_panel_ids": ["p9"],
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
    assert any(
        str(beat.get("beat_id", "")).startswith("su--")
        for beat in payload["story_map"]["beats"]
    )
    assert result.qc_report["story_understanding_hash"] == context["understanding_hash"]
    assert result.qc_report["narration_cache_contract"] == "narration-final-v3"
    assert stages.index("story_semantic_audit") < stages.index("narration")


class _SelectiveAuditProvider(_FakeProvider):
    def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
        if stage == "story_semantic_audit":
            self.calls.append((stage, prompt_version, prompt_sha256))
            return {
                "verdicts": [
                    {
                        "beat_id": str(beat["beat_id"]),
                        "supported": str(beat["beat_id"]) != "invented-beat",
                        "reason": (
                            "Grounded by evidence."
                            if str(beat["beat_id"]) != "invented-beat"
                            else "The proposed detail is absent from the supplied evidence."
                        ),
                    }
                    for beat in payload.get("beats", [])
                ]
            }
        result = super().complete_json(
            stage=stage,
            prompt_version=prompt_version,
            prompt_sha256=prompt_sha256,
            prompt_text=prompt_text,
            payload=payload,
        )
        if stage == "story_understanding":
            result["narration_ready_beats"].append(
                {
                    "beat_id": "invented-beat",
                    "story_role": "reveal",
                    "fact": "An unsupported secret mechanism is revealed.",
                    "narrative_function": "Do not preserve this unsupported addition.",
                    "change": "",
                    "consequence": "",
                    "open_question": "",
                    "importance": 5,
                    "evidence_strength": "supported_interpretation",
                    "evidence_panel_ids": [str(payload["panel_ids"][0])],
                    "source_claim_ids": [],
                    "entity_ids": [],
                    "confidence": "qualified",
                    "qualification": "This is intentionally unsupported for regression coverage.",
                }
            )
        return result


def test_semantic_audit_drops_isolated_bad_beat_without_regenerating_story(tmp_path):
    module = _module()
    provider = _SelectiveAuditProvider()
    panels = _panels(module, "semantic-filter")
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(tmp_path / "semantic-filter-cache"),
        max_attempts=2,
    )
    visual = runner.run_visual_evidence(panels)
    story_map = runner.run_story_map(visual)
    result = runner.run_narration(visual, story_map, panels=panels)

    stages = [call[0] for call in provider.calls]
    assert stages.count("story_understanding") == 1
    assert stages.count("story_semantic_audit") == 1
    context = provider.narration_payloads[-1]["story_understanding"]
    beat_ids = [beat["beat_id"] for beat in context["narration_ready_beats"]]
    assert "invented-beat" not in beat_ids
    assert len(beat_ids) == 2
    assert result.qc_report["story_semantic_audit_dropped_beat_ids"] == ["invented-beat"]
    assert result.qc_report["story_semantic_audit_supported_beat_count"] == 2


class _InsufficientAuditProvider(_FakeProvider):
    def complete_json(self, *, stage, prompt_version, prompt_sha256, prompt_text="", payload):
        if stage == "story_semantic_audit":
            self.calls.append((stage, prompt_version, prompt_sha256))
            return {
                "verdicts": [
                    {
                        "beat_id": str(beat["beat_id"]),
                        "supported": str(beat["beat_id"]) == "understanding-1",
                        "reason": "Only one beat is grounded strongly enough for this regression case.",
                    }
                    for beat in payload.get("beats", [])
                ]
            }
        return super().complete_json(
            stage=stage,
            prompt_version=prompt_version,
            prompt_sha256=prompt_sha256,
            prompt_text=prompt_text,
            payload=payload,
        )


def test_semantic_audit_retries_when_too_little_grounded_story_remains(tmp_path):
    module = _module()
    provider = _InsufficientAuditProvider()
    panels = _panels(module, "semantic-retry")
    runner = module.CloudStageRunner(
        provider=provider,
        model_identity=_identity(module),
        cache=module.FileStageCache(tmp_path / "semantic-retry-cache"),
        max_attempts=2,
    )
    visual = runner.run_visual_evidence(panels)
    story_map = runner.run_story_map(visual)

    with pytest.raises(module.CloudStageError) as exc_info:
        runner.run_narration(visual, story_map, panels=panels)
    assert exc_info.value.code == "cloud.story_understanding_ungrounded"
    stages = [call[0] for call in provider.calls]
    assert stages.count("story_understanding") == 2


def test_grounded_outline_ignores_provider_reasoning_when_story_context_exists():
    module = _module()
    context = {
        "narration_ready_beats": [
            {
                "beat_id": "grounded-setup",
                "story_role": "setup",
                "fact": "A verified deal is offered.",
                "change": "The offer creates a new choice.",
                "consequence": "",
                "open_question": "",
            },
            {
                "beat_id": "grounded-consequence",
                "story_role": "consequence",
                "fact": "The cost remains unresolved.",
                "change": "",
                "consequence": "Accepting the deal carries a stated cost.",
                "open_question": "What will be chosen?",
            },
        ]
    }
    provider_outline = {
        "story_spine": dict.fromkeys(("who_wants_what", "obstacle", "decision", "consequence", "changed_stakes", "unresolved_question"), "invented lore"),
        "ending_kind": "cliffhanger",
    }
    outline = module.CloudStageRunner._grounded_narrative_outline(
        context,
        [{"text": "The choice remains open?"}],
        provider_outline,
    )
    assert "invented lore" not in outline["story_spine"].values()
    assert outline["story_spine"]["who_wants_what"] == "A verified deal is offered."
    assert outline["story_spine"]["consequence"] == "Accepting the deal carries a stated cost."
    assert outline["ending_kind"] == "open_question"
