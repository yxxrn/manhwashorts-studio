"""Regression contract for the deterministic narration benchmark."""
from __future__ import annotations

import json
from pathlib import Path

from app.services import narrative_benchmark

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "narrative_benchmark_v1.json"


def _cases():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["version"] == narrative_benchmark.BENCHMARK_VERSION
    assert len(payload["cases"]) == 10
    return payload["cases"]


def test_benchmark_fixture_matches_all_expected_outcomes():
    for case in _cases():
        result = narrative_benchmark.evaluate_narration(
            case["case_id"], case["passages"], case["claims"]
        )
        assert result.passed is case["expected_pass"], (case["case_id"], result)


def test_benchmark_is_deterministic_and_tracks_primary_story_usage():
    case = _cases()[0]
    first = narrative_benchmark.evaluate_narration(case["case_id"], case["passages"], case["claims"])
    second = narrative_benchmark.evaluate_narration(case["case_id"], case["passages"], case["claims"])
    assert first == second
    assert first.primary_claim_ratio == 1.0
    assert first.claim_evidence_coverage_ratio == 1.0
    assert first.ai_slop_hits == ()
