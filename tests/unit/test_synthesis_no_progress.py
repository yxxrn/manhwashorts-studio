from __future__ import annotations

import pytest

from app.services import pipeline
from app.services.vision_adapter import VisionChapterSynthesisRequest, VisionResponseInvalid


def _copy_error(text: str) -> VisionResponseInvalid:
    return VisionResponseInvalid(
        validation_subtype="script_passage_copies_source_dialogue",
        selection_diagnostics={
            "passage_index": 1,
            "ngram_size": 6,
            "matched_phrase": "lose nine out of ten times",
        },
        retry_passages=[
            {
                "passage_id": "p2",
                "editorial_role": "setup",
                "text": text,
                "claim_ids": ["c1"],
                "evidence_panel_ids": ["a"],
            }
        ],
    )


def test_structured_diagnostic_signature_ignores_unrelated_retry_prose_changes():
    first = _copy_error("First paraphrase still copies the source wording.")
    second = _copy_error("Different prose, but the same copied phrase remains.")

    assert pipeline._synthesis_retry_signature(first.validation_subtype, first) == (
        pipeline._synthesis_retry_signature(second.validation_subtype, second)
    )


def test_synthesis_breaks_after_same_structured_failure_without_progress():
    request = VisionChapterSynthesisRequest(
        analysis_run_id="no-progress-copy",
        instruction_version="v",
        instruction_sha256="a" * 64,
        instruction_text="x",
        expected_panel_ids=("a",),
        coverage_manifest={},
        ordered_observations=(),
        chunks=(),
    )

    class Provider:
        model_id = ""
        endpoint = ""

        def __init__(self) -> None:
            self.calls = 0

        def synthesize(self, _request):
            self.calls += 1
            if self.calls == 1:
                raise _copy_error("First paraphrase still copies the source wording.")
            raise _copy_error("Different prose, but the same copied phrase remains.")

    provider = Provider()
    with pytest.raises(VisionResponseInvalid):
        pipeline._synthesize_with_cache(provider, request)

    assert provider.calls == 2
