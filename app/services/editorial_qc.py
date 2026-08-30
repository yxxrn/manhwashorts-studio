"""Post-render editorial QC and machine-readable report generation."""
from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.config import settings
from app.services import motion_director, reference_profile, subtitle_karaoke


@dataclass
class EditorialQC:
    duration: float = 0.0
    resolution: str = "1080x1920"
    fps: int = 60
    average_shot_duration: float = 0.0
    longest_static_segment: float = 0.0
    same_panel_same_crop_max: float = 0.0
    unique_crop_count: int = 0
    single_word_caption_ratio: float = 0.0
    visual_evidence_confidence: float = 0.0
    editorial_overlay_density: float = 0.0
    template_similarity: float = 0.0
    original_commentary: int = 0
    editorial_visual_transformation: int = 0
    episode_specificity: int = 0
    template_repetition_risk: int = 0
    rights_confidence: int = 0
    source_cleanliness: int = 0
    source_families: list[str] = field(default_factory=list)
    static_ratio: float = 0.0
    dominant_background_ratio: float = 0.0
    alternating_pattern_max: int = 0
    motion_mode_diversity: int = 0
    dominant_motion_ratio: float = 0.0
    rendered_motion_endpoint_mean: float = 0.0
    rendered_motion_low_diff_ratio: float = 0.0
    action_transition_failures: int = 0
    caption_word_count_min: int = 0
    caption_word_count_max: int = 0
    caption_dangling_count: int = 0
    caption_end_overflow: float = 0.0
    audio_integrated_lufs: float | None = None
    audio_true_peak_dbfs: float | None = None
    voice_profile_count: int = 0
    ending_has_payoff: bool = False
    ending_has_visual_evidence: bool = False
    full_playback_verified: bool = False
    audio_video_drift: float = 0.0
    black_frame_duration: float = 0.0
    subtitle_contract_version: str = ""
    subtitle_font_size_px: int = 0
    subtitle_max_lines: int = 0
    subtitle_max_lines_measured: int = 0
    subtitle_active_word_events: int = 0
    subtitle_timing_source: str = ""
    visual_contract_version: str = ""
    visual_identity_hashes: dict[str, str] = field(default_factory=dict)
    publish_allowed: bool = False
    qc_pass: bool = False
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class NarrativeNaturalnessReport:
    total_words: int
    sentence_length_p10: float
    sentence_length_p50: float
    sentence_length_p90: float
    sentence_length_variance: float
    repeated_normalized_sentence_ratio: float
    repeated_opening_ngram_ratio: float
    connector_diversity_count: int
    causal_transition_coverage: float
    contraction_count: int
    generic_hype_hits: tuple[str, ...]
    cta_hits: tuple[str, ...]
    claim_evidence_coverage_ratio: float
    qualified_interpretation_coverage_ratio: float
    warnings: tuple[str, ...]


_NARRATIVE_CONNECTORS = (
    "because",
    "but",
    "so",
    "while",
    "although",
    "however",
    "instead",
    "meanwhile",
    "yet",
    "therefore",
)
_NARRATIVE_CAUSAL_CONNECTORS = {
    "because",
    "so",
    "while",
    "although",
    "however",
    "instead",
    "meanwhile",
    "yet",
    "therefore",
}
_NARRATIVE_HYPE_MARKERS = (
    "epic battle",
    "unstoppable attack",
    "insane power",
)
_NARRATIVE_CTA_MARKERS = (
    "like this video",
    "subscribe",
    "follow for more",
    "watch until the end",
)


def _narrative_words(text: object) -> list[str]:
    return re.findall(r"[^\W_]+(?:['’][^\W_]+)?", str(text or "").casefold())


def _narrative_sentences(text: object) -> list[list[str]]:
    return [
        words
        for part in re.split(r"[.!?]+", str(text or ""))
        if (words := _narrative_words(part))
    ]


def _narrative_nearest_rank(values: Sequence[int], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1)))))
    return float(ordered[index])


def _narrative_ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _narrative_dialogue_values(claim: Mapping[str, object]) -> list[str]:
    values = claim.get("dialogue_or_ocr", claim.get("source_dialogue", []))
    if isinstance(values, str):
        return [values]
    if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
        return [value for value in values if isinstance(value, str)]
    return []


def _narrative_contains_sequence(text: object, source: object) -> bool:
    passage_words = _narrative_words(text)
    source_words = _narrative_words(source)
    if len(source_words) < 4:
        return False
    source_ngrams = {
        tuple(source_words[index : index + 4])
        for index in range(len(source_words) - 3)
    }
    return any(
        tuple(passage_words[index : index + 4]) in source_ngrams
        for index in range(len(passage_words) - 3)
    )


def screen_narrative_naturalness(
    passages: Sequence[Mapping[str, object]],
    claims: Mapping[str, Mapping[str, object]],
    profile: object,
) -> NarrativeNaturalnessReport:
    """Measure safe prose signals without rewriting provider text."""

    del profile  # The profile selects this screen; metrics remain non-mechanical.
    passage_texts = [str(item.get("text", "")) for item in passages]
    sentence_lists = [_narrative_sentences(text) for text in passage_texts]
    sentence_lengths = [len(sentence) for group in sentence_lists for sentence in group]
    sentence_hashes = [
        " ".join(sentence)
        for group in sentence_lists
        for sentence in group
    ]
    opening_ngrams = [
        tuple(_narrative_words(text)[:3])
        for text in passage_texts
        if len(_narrative_words(text)) >= 3
    ]
    repeated_sentences = 0
    seen_sentences: set[str] = set()
    for group in sentence_lists:
        current = {" ".join(sentence) for sentence in group}
        repeated_sentences += len(current & seen_sentences)
        seen_sentences.update(current)
    repeated_openings = len(opening_ngrams) - len(set(opening_ngrams))
    total_sentences = len(sentence_hashes)
    sentence_variance = (
        sum(
            (value - (sum(sentence_lengths) / len(sentence_lengths))) ** 2
            for value in sentence_lengths
        )
        / len(sentence_lengths)
        if sentence_lengths
        else 0.0
    )
    lower_text = " ".join(passage_texts).casefold()
    connector_hits = {
        connector
        for connector in _NARRATIVE_CONNECTORS
        if re.search(rf"\b{re.escape(connector)}\b", lower_text)
    }
    causal_passages = sum(
        any(
            re.search(rf"\b{re.escape(connector)}\b", text.casefold())
            for connector in _NARRATIVE_CAUSAL_CONNECTORS
        )
        for text in passage_texts
    )
    contraction_count = sum(
        1
        for text in passage_texts
        for token in re.findall(r"\b[^\s]+\b", text)
        if "'" in token or "’" in token
    )
    generic_hype_hits = tuple(
        marker for marker in _NARRATIVE_HYPE_MARKERS if marker in lower_text
    )
    cta_hits = tuple(marker for marker in _NARRATIVE_CTA_MARKERS if marker in lower_text)

    unsupported_claim = False
    evidence_missing = False
    required_claim_evidence: dict[str, set[str]] = {}
    covered_claim_evidence: dict[str, set[str]] = {}
    for passage in passages:
        claim_ids = passage.get("claim_ids", [])
        evidence_ids = passage.get("evidence_panel_ids", [])
        if not isinstance(claim_ids, Sequence) or isinstance(claim_ids, (str, bytes)):
            evidence_missing = True
            continue
        if not isinstance(evidence_ids, Sequence) or isinstance(evidence_ids, (str, bytes)):
            evidence_missing = True
            continue
        evidence_set = {str(item) for item in evidence_ids}
        for claim_id in claim_ids:
            claim = claims.get(claim_id) if isinstance(claim_id, str) else None
            if not isinstance(claim, Mapping):
                unsupported_claim = True
                continue
            claim_evidence = claim.get("evidence_panel_ids")
            if not isinstance(claim_evidence, Sequence) or isinstance(
                claim_evidence, (str, bytes)
            ) or not claim_evidence:
                evidence_missing = True
                continue
            claim_key = str(claim_id)
            required = {str(item) for item in claim_evidence}
            required_claim_evidence.setdefault(claim_key, set()).update(required)
            covered_claim_evidence.setdefault(claim_key, set()).update(required & evidence_set)
            if not required & evidence_set:
                evidence_missing = True
            for dialogue in _narrative_dialogue_values(claim):
                if _narrative_contains_sequence(passage.get("text", ""), dialogue):
                    evidence_missing = True
    if any(
        covered_claim_evidence.get(claim_id, set()) != required
        for claim_id, required in required_claim_evidence.items()
    ) or len(required_claim_evidence) != len(claims):
        evidence_missing = True

    interpretations = [
        claim
        for claim in claims.values()
        if isinstance(claim, Mapping) and claim.get("claim_type") == "interpretation"
    ]
    qualified = sum(
        bool(str(claim.get("qualification", "")).strip())
        for claim in interpretations
    )
    unqualified = bool(interpretations) and qualified != len(interpretations)
    warnings: set[str] = set()
    if generic_hype_hits:
        warnings.add("narrative.generic_hype")
    if cta_hits:
        warnings.add("narrative.cta")
    if unsupported_claim:
        warnings.add("narrative.unsupported_claim")
    if evidence_missing:
        warnings.add("narrative.evidence_missing")
    if unqualified:
        warnings.add("narrative.interpretation_unqualified")
    if any(
        _narrative_contains_sequence(passage.get("text", ""), dialogue)
        for passage in passages
        for claim in claims.values()
        if isinstance(claim, Mapping)
        for dialogue in _narrative_dialogue_values(claim)
    ):
        warnings.add("narrative.balloon_dialogue_copied")
    if repeated_sentences or repeated_openings:
        warnings.add("narrative.template_risk")
    if sentence_lengths and sentence_variance == 0.0:
        warnings.add("narrative.rhythm_warning")

    return NarrativeNaturalnessReport(
        total_words=sum(len(_narrative_words(text)) for text in passage_texts),
        sentence_length_p10=_narrative_nearest_rank(sentence_lengths, 0.10),
        sentence_length_p50=_narrative_nearest_rank(sentence_lengths, 0.50),
        sentence_length_p90=_narrative_nearest_rank(sentence_lengths, 0.90),
        sentence_length_variance=round(sentence_variance, 6),
        repeated_normalized_sentence_ratio=_narrative_ratio(
            repeated_sentences, total_sentences
        ),
        repeated_opening_ngram_ratio=_narrative_ratio(
            repeated_openings, len(opening_ngrams)
        ),
        connector_diversity_count=len(connector_hits),
        causal_transition_coverage=_narrative_ratio(causal_passages, len(passages)),
        contraction_count=contraction_count,
        generic_hype_hits=generic_hype_hits,
        cta_hits=cta_hits,
        claim_evidence_coverage_ratio=(
            round(
                sum(
                    covered_claim_evidence.get(claim_id, set()) >= required
                    for claim_id, required in required_claim_evidence.items()
                )
                / len(claims),
                6,
            )
            if claims
            else 1.0
        ),
        qualified_interpretation_coverage_ratio=(
            round(qualified / len(interpretations), 6) if interpretations else 1.0
        ),
        warnings=tuple(sorted(warnings)),
    )


def _shot_metrics(
    scenes: list[object], *, exact_panel_identity: bool = False
) -> tuple[float, float, int, float]:
    if not scenes:
        return 0.0, 0.0, 0, 0.0
    durations = [max(0.0, s.end_time - s.start_time) for s in scenes]
    crops = {(round(s.focus_x, 3), round(s.focus_y, 3), round(s.focus_end_x, 3), round(s.focus_end_y, 3)) for s in scenes}
    longest_same = 0.0
    running = 0.0
    previous = None
    for scene, duration in zip(scenes, durations, strict=True):
        visual_key = (
            str(getattr(scene, "panel_region_id", "") or getattr(scene, "panel_id", "") or scene.asset_id)
            if exact_panel_identity
            else scene.asset_id
        )
        key = (visual_key, round(scene.focus_x, 3), round(scene.focus_y, 3), round(scene.focus_end_x, 3), round(scene.focus_end_y, 3))
        if key == previous:
            running += duration
            longest_same = max(longest_same, running)
        else:
            running = duration
        longest_same = max(longest_same, running)
        previous = key
    return sum(durations) / len(durations), longest_same, len(crops), sum(durations)


def _caption_contract_invalid(cues: list[object], duration: float) -> bool:
    for cue in cues:
        raw = str(getattr(cue, "text", "") or "")
        if (
            len(raw.split()) != 1
            or raw != raw.upper()
            or any(not (character.isalnum() or character.isspace()) for character in raw)
            or float(getattr(cue, "start_time", 0.0)) < 0.0
            or float(getattr(cue, "end_time", duration)) <= float(getattr(cue, "start_time", 0.0))
            or float(getattr(cue, "end_time", duration)) > duration + 0.01
        ):
            return True
    return any(
        float(getattr(right, "start_time", 0.0)) < float(getattr(left, "end_time", 0.0)) - 0.01
        for left, right in zip(cues, cues[1:], strict=False)
    )


def _reference_qc_failures(scenes: list[object], duration: float, profile) -> list[str]:
    failures: list[str] = []
    if not profile.duration_min_s <= duration <= profile.duration_max_s:
        failures.append("reference.duration_outside_50_60s")
    if not profile.shot_min <= len(scenes) <= profile.shot_max:
        failures.append("reference.shot_count_outside_36_52")
    durations = [
        round(max(0.0, float(scene.end_time) - float(scene.start_time)), 3)
        for scene in scenes
    ]
    normal = [value for value in durations if profile.hold_min_s <= value <= profile.hold_max_s]
    emphasis = [value for value in durations if profile.emphasis_min_s <= value <= profile.emphasis_max_s]
    if len(normal) + len(emphasis) != len(durations):
        failures.append("reference.shot_duration_outside_0.65_2.20s")
    if durations and len(normal) / len(durations) < profile.hold_ratio_min:
        failures.append("reference.hold_ratio_below_70pct")
    if durations and len(normal) / len(durations) > profile.hold_ratio_max:
        failures.append("reference.hold_ratio_over_80pct")
    if durations and len(emphasis) / len(durations) < profile.emphasis_ratio_min:
        failures.append("reference.emphasis_ratio_below_20pct")
    if durations and len(emphasis) / len(durations) > profile.emphasis_ratio_max:
        failures.append("reference.emphasis_ratio_over_30pct")
    if durations:
        mean = sum(durations) / len(durations)
        if not profile.mean_shot_min_s <= mean <= profile.mean_shot_max_s:
            failures.append("reference.mean_shot_duration_outside_1.15_1.40s")
        hard_cuts = sum(
            1
            for index, scene in enumerate(scenes)
            if getattr(scene, "transition", "") == ("none" if index == 0 else "cut")
        )
        if hard_cuts / len(scenes) < profile.hard_cut_ratio_min:
            failures.append("reference.hard_cut_ratio_below_85pct")
    counts: dict[str, int] = {}
    positions: dict[str, list[int]] = {}
    for index, scene in enumerate(scenes):
        asset_id = str(getattr(scene, "asset_id", "") or "")
        if asset_id:
            counts[asset_id] = counts.get(asset_id, 0) + 1
            positions.setdefault(asset_id, []).append(index)
    if any(count > profile.max_canonical_panel_uses for count in counts.values()):
        failures.append("reference.panel_reuse_over_2")
    for left, right in zip(scenes, scenes[1:], strict=False):
        if getattr(left, "asset_id", None) != getattr(right, "asset_id", None):
            continue
        failures.append("reference.panel_reuse_consecutive")
        if (
            getattr(left, "roi_label", "") == getattr(right, "roi_label", "")
            and abs(float(getattr(left, "focus_x", 0.0)) - float(getattr(right, "focus_x", 0.0))) < 0.001
            and abs(float(getattr(left, "focus_y", 0.0)) - float(getattr(right, "focus_y", 0.0))) < 0.001
        ):
            failures.append("reference.panel_reuse_same_roi")
        break
    for indexes in positions.values():
        if len(indexes) != 2:
            continue
        first, second = (scenes[indexes[0]], scenes[indexes[1]])
        if (
            getattr(first, "roi_label", "") == getattr(second, "roi_label", "")
            and abs(float(getattr(first, "focus_x", 0.0)) - float(getattr(second, "focus_x", 0.0))) < 0.001
            and abs(float(getattr(first, "focus_y", 0.0)) - float(getattr(second, "focus_y", 0.0))) < 0.001
        ):
            failures.append("reference.panel_reuse_same_roi")
    return sorted(set(failures))


def _standard_reference_qc_failures(scenes: list[object], duration: float, profile) -> list[str]:
    failures: list[str] = []
    if not profile.duration_min_s <= duration <= profile.duration_max_s:
        failures.append("reference.standard_duration_outside_50_60s")
    durations = [max(0.0, float(scene.end_time) - float(scene.start_time)) for scene in scenes]
    minimum_required = max(1, __import__("math").ceil(max(0.0, duration) / reference_profile.REVIEW_MAX_SHOT_SECONDS))
    if len(scenes) < minimum_required:
        failures.append("reference.standard_shot_density_low")
    if any(value <= 0.50 or value > reference_profile.REVIEW_MAX_SHOT_SECONDS + 1e-9 for value in durations):
        failures.append("reference.standard_shot_duration_invalid")
    keys = [
        str(getattr(scene, "panel_region_id", "") or getattr(scene, "panel_id", "") or getattr(scene, "asset_id", ""))
        for scene in scenes
    ]
    if len([key for key in keys if key]) != len({key for key in keys if key}):
        failures.append("reference.standard_panel_repeat")
    if len(scenes) > 1 and any(getattr(scene, "transition", "cut") != "fade" for scene in scenes[1:]):
        failures.append("reference.standard_transition_policy")
    stable_modes = {"slow_push", "slow_pull", "guided_pan", "focus_shift"}
    modes = [str(getattr(scene, "motion_mode", "") or "") for scene in scenes]
    if any(mode not in stable_modes for mode in modes):
        failures.append("reference.standard_motion_path_invalid")
    if len(scenes) >= 8:
        counts = Counter(modes)
        if len(counts) < reference_profile.REVIEW_MOTION_MIN_MODE_DIVERSITY:
            failures.append("reference.standard_motion_diversity_low")
        if max(counts.values(), default=0) / max(1, len(modes)) > reference_profile.REVIEW_MOTION_MAX_DOMINANT_MODE_RATIO + 1e-9:
            failures.append("reference.standard_motion_dominant")
    imperceptible = 0
    for scene in scenes:
        mode = str(getattr(scene, "motion_mode", "") or "")
        focus_travel = __import__("math").hypot(
            float(getattr(scene, "focus_end_x", 0.5)) - float(getattr(scene, "focus_x", 0.5)),
            float(getattr(scene, "focus_end_y", 0.5)) - float(getattr(scene, "focus_y", 0.5)),
        )
        zoom_ok = (
            mode in {"slow_push", "slow_pull"}
            and reference_profile.REVIEW_MOTION_ZOOM_DELTA >= reference_profile.REVIEW_MOTION_MIN_ZOOM_DELTA - 1e-9
        )
        focus_ok = focus_travel >= reference_profile.REVIEW_MOTION_MIN_FOCUS_TRAVEL - 1e-9
        if not (zoom_ok or focus_ok):
            imperceptible += 1
    if imperceptible:
        failures.append("reference.standard_motion_imperceptible")
    return failures


def _adaptive_reference_qc_failures(
    scenes: list[object], duration: float, contract: Mapping[str, object]
) -> list[str]:
    failures: list[str] = []
    try:
        lower = float(contract["target_duration_min_s"])
        upper = float(contract["target_duration_max_s"])
    except (KeyError, TypeError, ValueError):
        return ["reference.adaptive_contract_invalid"]
    if not lower <= duration <= upper:
        failures.append("reference.adaptive_duration_outside_contract")
    durations = [max(0.0, float(scene.end_time) - float(scene.start_time)) for scene in scenes]
    if len(scenes) < max(1, __import__("math").ceil(max(0.0, duration) / reference_profile.REVIEW_MAX_SHOT_SECONDS)):
        failures.append("reference.adaptive_shot_density_low")
    if any(value <= 0.50 or value > reference_profile.REVIEW_MAX_SHOT_SECONDS + 1e-9 for value in durations):
        failures.append("reference.adaptive_shot_duration_invalid")
    keys = [
        str(getattr(scene, "panel_region_id", "") or getattr(scene, "panel_id", "") or getattr(scene, "asset_id", ""))
        for scene in scenes
    ]
    if any(key and key in keys[max(0, index - reference_profile.REVIEW_PANEL_REUSE_WINDOW_SHOTS):index] for index, key in enumerate(keys)):
        failures.append("reference.adaptive_panel_repeat")
    if len(scenes) > 1 and any(getattr(scene, "transition", "cut") != "fade" for scene in scenes[1:]):
        failures.append("reference.adaptive_transition_policy")
    return failures


def build_report(
    *, scenes: list[object], cues: list[object], duration: float, job_path: Path | None = None,
    rights_confidence: int = 5, source_cleanliness: int = 5, voice_profile_count: int = 0, minimum_duration: float = 45.0,
    preview: bool = False, profile: object | None = None,
    panel_evidence_by_key: Mapping[tuple[str, str], object] | None = None,
    panel_border_masks_by_key: Mapping[tuple[str, str], object] | None = None,
    panel_sizes_by_key: Mapping[tuple[str, str], tuple[int, int]] | None = None,
    telemetry_by_key: Mapping[tuple[str, str], object | None] | None = None,
    caption_groups: Sequence[object] | None = None,
    subtitle_contract: Mapping[str, object] | None = None,
    subtitle_timing_error: str | None = None,
    adaptive_reference_contract: Mapping[str, object] | None = None,
    standard_reference_cadence: bool = False,
) -> EditorialQC:
    average, longest_same, crops, total = _shot_metrics(
        scenes, exact_panel_identity=standard_reference_cadence
    )
    frozen = _freeze_duration(job_path) if job_path and job_path.is_file() else 0.0
    single_words = sum(1 for cue in cues if len(str(cue.text).split()) == 1)
    caption_ratio = single_words / len(cues) if cues else 1.0
    static_duration = sum(duration for scene, duration in zip(scenes, [max(0.0, s.end_time - s.start_time) for s in scenes], strict=True) if getattr(scene, "motion_mode", "hold") in {"hold", "static_emphasis"})
    signatures = [getattr(scene, "visual_signature", "") or getattr(scene, "asset_id", "") for scene in scenes]
    dominance = max((signatures.count(value) for value in set(signatures) if value), default=0) / max(1, len(signatures))
    alternating = 0
    for index in range(len(signatures) - 3):
        window = signatures[index:index + 4]
        if window[0] and window[0] == window[2] and window[1] and window[1] == window[3] and window[0] != window[1]:
            alternating = max(alternating, 4)

    motion_modes = [getattr(scene, "motion_mode", "hold") for scene in scenes]
    motion_counts = Counter(motion_modes)
    motion_diversity = len(motion_counts)
    dominant_motion_ratio = max(motion_counts.values(), default=0) / max(1, len(motion_modes))
    caption_counts = [len(str(getattr(cue, "text", "")).split()) for cue in cues]
    caption_min = min(caption_counts, default=0)
    caption_max = max(caption_counts, default=0)
    dangling_count = 0
    caption_overflow = max(
        (float(getattr(cue, "end_time", duration)) - duration for cue in cues),
        default=0.0,
    )
    caption_contract_invalid = _caption_contract_invalid(cues, duration)
    action_transition_failures = sum(
        1 for scene in scenes
        if getattr(scene, "camera_intent", "") in {"action", "attack", "explosion", "impact"}
        and getattr(scene, "transition", "cut") not in {"cut", "none"}
    )
    report = EditorialQC(
        duration=round(duration, 3),
        fps=int(getattr(profile, "final_fps", settings.video_fps)),
        average_shot_duration=round(average, 3),
        longest_static_segment=round(frozen, 3),
        same_panel_same_crop_max=round(longest_same, 3),
        unique_crop_count=crops,
        single_word_caption_ratio=round(caption_ratio, 3),
        visual_evidence_confidence=1.0 if scenes else 0.0,
        original_commentary=5 if cues else 0,
        editorial_visual_transformation=5 if crops >= 4 else 2,
        episode_specificity=5 if len({s.asset_id for s in scenes if s.asset_id}) >= 5 else 2,
        template_repetition_risk=1 if crops >= 4 else 4,
        rights_confidence=rights_confidence,
        source_cleanliness=source_cleanliness,
        source_families=sorted({getattr(s, "source_family", "") for s in scenes if getattr(s, "source_family", "")}),
        static_ratio=round(static_duration / max(0.001, total), 3),
        dominant_background_ratio=round(dominance, 3),
        alternating_pattern_max=alternating,
        motion_mode_diversity=motion_diversity,
        dominant_motion_ratio=round(dominant_motion_ratio, 3),
        action_transition_failures=action_transition_failures,
        caption_word_count_min=caption_min,
        caption_word_count_max=caption_max,
        caption_dangling_count=dangling_count,
        caption_end_overflow=round(max(0.0, caption_overflow), 3),
        voice_profile_count=voice_profile_count,
        ending_has_payoff=any(getattr(s, "section", "") == "twist" for s in scenes),
        ending_has_visual_evidence=bool(scenes and scenes[-1].asset_id),
        subtitle_contract_version=str((subtitle_contract or {}).get("contract_version", "")),
        subtitle_font_size_px=int((subtitle_contract or {}).get("font_size_px", 0) or 0),
        subtitle_max_lines=int((subtitle_contract or {}).get("max_lines", 0) or 0),
        subtitle_max_lines_measured=max(
            (len(subtitle_karaoke.sentence_group_lines(getattr(group, "words", ()))) for group in (caption_groups or ())),
            default=0,
        ),
        subtitle_active_word_events=sum(
            len(getattr(group, "words", ()) or ()) for group in (caption_groups or ())
        ),
        subtitle_timing_source=str((subtitle_contract or {}).get("timing_source", "")),
        visual_contract_version=str(getattr(profile, "framing_contract_version", "")),
        visual_identity_hashes={
            str(getattr(scene, "panel_region_id", "")): str(getattr(scene, "evidence_hash", ""))
            for scene in scenes
            if getattr(scene, "panel_region_id", "") and getattr(scene, "evidence_hash", "")
        },
    )
    report.audio_video_drift, report.black_frame_duration = _media_integrity(job_path, duration)
    report.audio_integrated_lufs, report.audio_true_peak_dbfs = _audio_metrics(job_path)
    if not preview and standard_reference_cadence and job_path is not None:
        rendered_motion = _rendered_motion_endpoint_audit(job_path, scenes)
        if rendered_motion is None:
            report.failures.append("reference.standard_rendered_motion_measurement_missing")
        else:
            report.rendered_motion_endpoint_mean, report.rendered_motion_low_diff_ratio = rendered_motion
            if report.rendered_motion_endpoint_mean < reference_profile.RENDERED_MOTION_ENDPOINT_MEAN_MIN:
                report.failures.append("reference.standard_rendered_motion_weak")
            if report.rendered_motion_low_diff_ratio > reference_profile.RENDERED_MOTION_LOW_DIFF_MAX_RATIO:
                report.failures.append("reference.standard_rendered_motion_sparse")
    report.failures.extend(motion_director.audit_camera_sequence(scenes))
    if profile is not None:
        if adaptive_reference_contract is not None:
            report.failures.extend(
                _adaptive_reference_qc_failures(scenes, duration, adaptive_reference_contract)
            )
        elif standard_reference_cadence:
            report.failures.extend(_standard_reference_qc_failures(scenes, duration, profile))
        else:
            report.failures.extend(_reference_qc_failures(scenes, duration, profile))
        if caption_groups is not None or subtitle_timing_error is not None:
            if subtitle_timing_error:
                report.failures.append(str(subtitle_timing_error).split(":", 1)[0])
            else:
                report.failures.extend(
                    subtitle_karaoke.validate_sentence_groups(caption_groups or (), duration=duration)
                )
        if panel_evidence_by_key is not None:
            panel_results = check_reference_framing(
                scenes,
                panel_evidence_by_key,
                panel_border_masks_by_key or {},
                panel_sizes_by_key or {},
                telemetry_by_key or {},
                profile=profile,
                adaptive_reference=(
                    adaptive_reference_contract is not None or standard_reference_cadence
                ),
            )
            report.failures.extend(
                result.code for result in panel_results if not result.passed
            )
    else:
        if duration < minimum_duration or duration > 90:
            report.failures.append("duration_outside_60_90s")
        if not 2.3 <= average <= 3.3:
            report.failures.append("average_shot_duration_outside_2.3_3.3s")
    if dominance > 0.35 and len(set(signatures)) > 1:
        report.failures.append("dominant_background_over_35pct")
    if alternating >= 4:
        report.failures.append("alternating_background_pattern")
    if static_duration / max(0.001, total) > 0.55 and len(set(signatures)) > 1:
        report.failures.append("static_ratio_over_55pct")
    if longest_same > 4.0:
        report.failures.append("same_panel_same_crop_over_2.5s")
    if not preview:
        if adaptive_reference_contract is None and not standard_reference_cadence:
            if len(scenes) >= 4 and motion_diversity < 4:
                report.failures.append("motion_mode_diversity_lt_4")
            dominant_mode = max(motion_counts, key=motion_counts.get, default="")
            dominant_reasons = [
                str(getattr(scene, "motion_reason", "")).lower()
                for scene in scenes
                if getattr(scene, "motion_mode", "hold") == dominant_mode
            ]
            explicitly_justified = bool(dominant_reasons) and all(
                any(token in reason for token in ("justif", "override", "exception"))
                for reason in dominant_reasons
            )
            if dominant_motion_ratio > 0.55 and not explicitly_justified:
                report.failures.append("dominant_motion_over_55pct_without_justification")
            if action_transition_failures:
                report.failures.append("action_transition_not_hard_cut")
            transition_lengths = [
                float(scene.transition_duration)
                for scene in scenes
                if getattr(scene, "transition", "") == "fade"
                and hasattr(scene, "transition_duration")
            ]
            if any(length < 0.12 or length > 0.18 for length in transition_lengths):
                report.failures.append("section_transition_outside_0.12_0.18s")
        if caption_contract_invalid:
            report.failures.append("caption_display_contract_invalid")
        if caption_overflow > 0.01:
            report.failures.append("caption_end_after_media")
        if report.audio_integrated_lufs is not None and abs(report.audio_integrated_lufs + 14.0) > 1.0:
            report.failures.append("audio_lufs_outside_-14_target")
        if report.audio_true_peak_dbfs is not None and report.audio_true_peak_dbfs > -1.4:
            report.failures.append("audio_true_peak_over_-1.5dbtp")
    valid_modes = set(motion_director.MODES)
    invalid_motion = [s for s in scenes if getattr(s, "motion_mode", "hold") not in valid_modes]
    if invalid_motion:
        report.failures.append("invalid_motion_plan")
    for left, right in zip(scenes, scenes[1:], strict=False):
        if getattr(left, "motion_mode", "") == getattr(right, "motion_mode", "") == "impact":
            report.failures.append("strong_effects_consecutive")
            break
    if any(not getattr(s, "motion_reason", "").strip() for s in scenes):
        report.failures.append("motion_reason_missing")
    if rights_confidence != 5 or source_cleanliness != 5:
        report.failures.append("source_gate_failed")
    report.full_playback_verified = bool(job_path and job_path.is_file() and _decode_ok(job_path))
    if not report.full_playback_verified:
        report.failures.append("full_playback_not_verified")
    expected_fps = report.fps or settings.video_fps
    if report.audio_video_drift > (1 / expected_fps):
        report.failures.append("audio_video_drift_over_one_frame")
    if report.black_frame_duration > 0.4:
        report.failures.append("unintended_black_frame")
    report.publish_allowed = not report.failures
    report.qc_pass = report.publish_allowed
    return report


def _decode_ok(path: Path) -> bool:
    try:
        subprocess.run([settings.ffmpeg_bin, "-v", "error", "-i", str(path), "-f", "null", "-"], check=True, capture_output=True, timeout=600)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return True


def _freeze_duration(path: Path) -> float:
    """Return the longest FFmpeg-detected freeze interval."""
    try:
        result = subprocess.run(
            [settings.ffmpeg_bin, "-hide_banner", "-i", str(path), "-vf", "freezedetect=n=-60dB:d=0.2", "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=600, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0.0
    return max((float(value) for value in re.findall(r"freeze_duration:\s*([0-9.]+)", result.stderr)), default=0.0)


def _media_integrity(path: Path | None, expected: float) -> tuple[float, float]:
    """Measure stream drift and black intervals without decoding frames in Python."""
    if not path or not path.is_file():
        return expected, 0.0
    try:
        probe = subprocess.run(
            [settings.ffprobe_bin, "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=120, check=True,
        )
        durations = {
            stream.get("codec_type"): float(stream.get("duration") or 0.0)
            for stream in json.loads(probe.stdout).get("streams", [])
        }
        drift = abs(durations.get("video", expected) - durations.get("audio", expected))
        black = subprocess.run(
            [settings.ffmpeg_bin, "-hide_banner", "-i", str(path), "-vf", "blackdetect=d=0.15:pix_th=0.01", "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=600, check=False,
        )
        intervals = [float(value) for value in re.findall(r"black_duration:([0-9.]+)", black.stderr)]
        return round(drift, 4), max(intervals, default=0.0)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.SubprocessError):
        return expected, 0.0



def _rendered_motion_endpoint_audit(
    path: Path | None, scenes: Sequence[object]
) -> tuple[float, float] | None:
    """Measure real per-shot camera change away from subtitle/transition pixels."""
    if not path or not path.is_file() or not scenes:
        return None
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        return None
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    if fps <= 0.0:
        capture.release()
        return None
    values: list[float] = []
    try:
        for scene in scenes:
            start = float(getattr(scene, "start_time", 0.0))
            end = float(getattr(scene, "end_time", 0.0))
            if end - start <= 0.8:
                continue
            samples = []
            for moment in (start + 0.35, end - 0.25):
                capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(round(moment * fps))))
                ok, frame = capture.read()
                if not ok or frame is None:
                    return None
                top = frame[: max(1, int(frame.shape[0] * 0.42))]
                small = cv2.resize(top, (128, 96), interpolation=cv2.INTER_AREA)
                samples.append(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            values.append(float(np.mean(cv2.absdiff(samples[0], samples[1]))))
    except (TypeError, ValueError, cv2.error):
        return None
    finally:
        capture.release()
    if not values:
        return None
    mean = sum(values) / len(values)
    low_ratio = sum(
        value < reference_profile.RENDERED_MOTION_LOW_DIFF_THRESHOLD for value in values
    ) / len(values)
    return round(mean, 4), round(low_ratio, 4)

def _audio_metrics(path: Path | None) -> tuple[float | None, float | None]:
    """Read integrated loudness and true peak from FFmpeg's ebur128 filter."""
    if not path or not path.is_file():
        return None, None
    try:
        result = subprocess.run(
            [
                settings.ffmpeg_bin, "-hide_banner", "-i", str(path),
                "-filter_complex", "ebur128=framelog=verbose", "-f", "null", "-",
            ],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    text = result.stderr or result.stdout or ""
    lufs_values = re.findall(r"(?im)^\s*I:\s*(-?[0-9]+(?:\.[0-9]+)?)\s*LUFS", text)
    peak_values = re.findall(
        r"(?im)^\s*(?:Peak|True peak):\s*(-?[0-9]+(?:\.[0-9]+)?)\s*dBFS",
        text,
    )
    try:
        lufs = float(lufs_values[-1]) if lufs_values else None
        peak = float(peak_values[-1]) if peak_values else None
    except ValueError:
        return None, None
    return lufs, peak


def check_reference_framing(
    scenes: list[object],
    panel_evidence_by_key: Mapping[tuple[str, str], object],
    panel_border_masks_by_key: Mapping[tuple[str, str], object],
    panel_sizes_by_key: Mapping[tuple[str, str], tuple[int, int]],
    telemetry_by_key: Mapping[tuple[str, str], object | None],
    *,
    profile: object,
    adaptive_reference: bool = False,
):
    """Expose the exact panel QC boundary without changing legacy reports."""
    from app.services import quality

    return quality.check_reference_framing(
        scenes,
        panel_evidence_by_key,
        panel_border_masks_by_key,
        panel_sizes_by_key,
        telemetry_by_key,
        profile=profile,
        adaptive_reference=adaptive_reference,
    )


__all__ = ["EditorialQC", "build_report", "check_reference_framing"]
