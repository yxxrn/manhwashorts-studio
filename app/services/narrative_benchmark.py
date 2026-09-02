"""Deterministic regression benchmark for grounded short-form narration."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from app.services import editorial_qc, narrative_identity, quality

BENCHMARK_VERSION = "narrative-benchmark-v1"
MIN_PRIMARY_CLAIM_RATIO = 0.60
MAX_VISUAL_DESCRIPTION_RATIO = 0.25
MAX_MECHANICAL_OPENING_RATIO = 0.50
MAX_REPEATED_OPENING_RATIO = 0.25


@dataclass(frozen=True)
class NarrativeBenchmarkResult:
    version: str
    case_id: str
    passed: bool
    total_words: int
    primary_claim_ratio: float
    claim_evidence_coverage_ratio: float
    qualified_interpretation_coverage_ratio: float
    visual_description_ratio: float
    mechanical_opening_ratio: float
    repeated_opening_ngram_ratio: float
    sentence_length_variance: float
    ai_slop_hits: tuple[str, ...]
    reporter_prose_hits: tuple[str, ...]
    blocking_codes: tuple[str, ...]
    warning_codes: tuple[str, ...]
    benchmark_failures: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _primary_claim_ratio(
    passages: Sequence[Mapping[str, object]],
    claims: Mapping[str, Mapping[str, object]],
) -> float:
    referenced: list[str] = []
    for passage in passages:
        claim_ids = passage.get("claim_ids", ())
        if isinstance(claim_ids, Sequence) and not isinstance(claim_ids, (str, bytes)):
            for claim_id in claim_ids:
                key = str(claim_id)
                if key in claims and key not in referenced:
                    referenced.append(key)
    if not referenced:
        return 0.0
    primary = 0
    for claim_id in referenced:
        claim = claims[claim_id]
        if (
            claim_id.startswith("story_understanding__")
            or str(claim.get("narrative_priority", "")) == "primary"
            or str(claim.get("claim_origin", "")) == "story_understanding"
        ):
            primary += 1
    return round(primary / len(referenced), 6)


def evaluate_narration(
    case_id: str,
    passages: Sequence[Mapping[str, object]],
    claims: Mapping[str, Mapping[str, object]],
    *,
    require_primary: bool = True,
) -> NarrativeBenchmarkResult:
    profile = narrative_identity.get_narrative_identity("sharp_friend_v1")
    report = editorial_qc.screen_narrative_naturalness(passages, claims, profile)
    checks = quality.check_narrative_naturalness(report)
    blocking = tuple(
        sorted(check.code for check in checks if not check.passed and check.blocking)
    )
    warnings = tuple(
        sorted(check.code for check in checks if not check.passed and not check.blocking)
    )
    primary_ratio = _primary_claim_ratio(passages, claims)
    failures: list[str] = []
    if blocking:
        failures.append("benchmark.blocking_qc")
    if report.claim_evidence_coverage_ratio < 1.0:
        failures.append("benchmark.grounding_incomplete")
    if report.qualified_interpretation_coverage_ratio < 1.0:
        failures.append("benchmark.interpretation_unqualified")
    if require_primary and primary_ratio < MIN_PRIMARY_CLAIM_RATIO:
        failures.append("benchmark.primary_story_usage_low")
    if report.visual_description_ratio > MAX_VISUAL_DESCRIPTION_RATIO:
        failures.append("benchmark.visual_description_high")
    if report.mechanical_opening_ratio > MAX_MECHANICAL_OPENING_RATIO:
        failures.append("benchmark.mechanical_openings_high")
    if report.repeated_opening_ngram_ratio > MAX_REPEATED_OPENING_RATIO:
        failures.append("benchmark.template_openings_high")
    if report.ai_slop_hits:
        failures.append("benchmark.ai_slop_detected")
    return NarrativeBenchmarkResult(
        version=BENCHMARK_VERSION,
        case_id=str(case_id),
        passed=not failures,
        total_words=report.total_words,
        primary_claim_ratio=primary_ratio,
        claim_evidence_coverage_ratio=report.claim_evidence_coverage_ratio,
        qualified_interpretation_coverage_ratio=report.qualified_interpretation_coverage_ratio,
        visual_description_ratio=report.visual_description_ratio,
        mechanical_opening_ratio=report.mechanical_opening_ratio,
        repeated_opening_ngram_ratio=report.repeated_opening_ngram_ratio,
        sentence_length_variance=report.sentence_length_variance,
        ai_slop_hits=report.ai_slop_hits,
        reporter_prose_hits=report.reporter_prose_hits,
        blocking_codes=blocking,
        warning_codes=warnings,
        benchmark_failures=tuple(failures),
    )
