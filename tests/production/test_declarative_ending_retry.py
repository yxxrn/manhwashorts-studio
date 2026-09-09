from app.services import pipeline, vision_adapter
from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid


def _passages():
    return tuple(
        {
            "passage_id": f"p{i}",
            "editorial_role": role,
            "text": text,
            "claim_ids": [f"c{i}"],
            "evidence_panel_ids": ["panel-1"],
        }
        for i, (role, text) in enumerate(
            (
                ("hook", "Lloyd wins the first legal exchange."),
                ("setup", "The rival pushes back with a new demand."),
                ("conflict", "Lloyd answers by changing the water system."),
                ("consequence", "The contract flips the pressure back on them?"),
            ),
            start=1,
        )
    )


def _request(**kwargs):
    base = {
        "analysis_run_id": "ending-retry-test",
        "instruction_version": "test-v1",
        "instruction_sha256": "a" * 64,
        "instruction_text": "test",
        "expected_panel_ids": ("panel-1",),
        "coverage_manifest": {},
        "ordered_observations": (),
        "chunks": (),
        "narrative_profile_id": "sharp_friend_v1",
        "target_word_count_min": 115,
        "target_word_count_max": 125,
    }
    base.update(kwargs)
    return VisionChapterSynthesisRequest(**base)


def test_declarative_ending_is_text_only_retry_subtype():
    assert "non-question_ending_kind_must_not_end_with_" in (
        vision_adapter.TEXT_ONLY_SYNTHESIS_RETRY_SUBTYPES
    )


def test_declarative_ending_lock_only_allows_final_text_change():
    locked = _passages()
    locked_output = {"script_passages": [dict(item) for item in locked]}
    candidate = {
        "script_passages": [
            {**dict(item), "text": f"changed {index}"}
            for index, item in enumerate(locked)
        ]
    }
    request = _request(
        retry_declarative_ending=True,
        retry_text_only_locked_output=locked_output,
    )
    rebuilt = vision_adapter._apply_text_only_retry_lock(candidate, request)
    rebuilt_passages = rebuilt["script_passages"]
    assert [item["text"] for item in rebuilt_passages[:-1]] == [
        item["text"] for item in locked[:-1]
    ]
    assert rebuilt_passages[-1]["text"] == "changed 3"
    assert rebuilt_passages[-1]["claim_ids"] == locked[-1]["claim_ids"]
    assert rebuilt_passages[-1]["evidence_panel_ids"] == locked[-1]["evidence_panel_ids"]


def test_pipeline_routes_declarative_ending_to_bounded_text_only_retry(monkeypatch):
    locked = _passages()
    locked_output = {"script_passages": [dict(item) for item in locked]}

    class Provider:
        def __init__(self):
            self.requests = []

        def synthesize(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                raise VisionResponseInvalid(
                    validation_subtype="non-question_ending_kind_must_not_end_with_",
                    passage_word_counts=(7, 9, 8, 9),
                    retry_passages=locked,
                    retry_locked_output=locked_output,
                )
            return {"accepted": True}

    provider = Provider()
    monkeypatch.setattr(pipeline, "_validate_synthesis_subtitle_admission", lambda *_: None)
    monkeypatch.setattr(
        pipeline,
        "_validated_synthesis_cache_output",
        lambda output, _request: output,
    )
    result = pipeline._synthesize_with_cache(provider, _request())
    assert result == {"accepted": True}
    assert len(provider.requests) == 2
    retry = provider.requests[1]
    assert retry.retry_declarative_ending is True
    assert retry.retry_text_only_locked_output == locked_output
    assert retry.retry_passages == locked
    assert retry.retry_word_counts is None
    assert retry.retry_visual_selection is False
    assert retry.retry_dialogue_paraphrase is False
