"""Pure reference-review helpers shared by Task 7 orchestration and rendering."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from math import isfinite
from pathlib import Path
from typing import Any

from PIL import Image

from app.config import settings
from app.services import (
    editorial_visual_planner,
    framing_analysis,
    reference_profile,
    visual_scoring,
)

_PIXEL_PREFLIGHT_CACHE_VERSION = "reference-pixel-preflight-v1"

def _pixel_preflight_cache_key(
    image: Image.Image, roi: editorial_visual_planner.ReferenceROIAlternative,
    evidence: visual_scoring.PanelVisualEvidence, border_mask: framing_analysis.BorderMaskResult,
    panel_size: tuple[int, int], profile: object, allow_conservative_full_panel: bool,
) -> str:
    pixels = hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()
    payload = {
        "version": _PIXEL_PREFLIGHT_CACHE_VERSION, "pixels": pixels,
        "image_size": list(image.size), "panel_size": list(panel_size),
        "roi": asdict(roi), "evidence_hash": visual_scoring.visual_evidence_hash(evidence),
        "mask": asdict(border_mask), "profile_id": str(getattr(profile, "profile_id", "")),
        "profile_version": str(getattr(profile, "profile_version", getattr(profile, "version", ""))),
        "framing_contract_version": str(getattr(profile, "framing_contract_version", "")),
        "target": [int(profile.final_width), int(profile.final_height)],
        "allow_conservative_full_panel": bool(allow_conservative_full_panel),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _pixel_preflight_cache_path(key: str) -> Path:
    return Path(settings.data_dir) / "reference-pixel-preflight-cache" / f"{key}.json"

def _load_pixel_preflight_cache(key: str) -> bool | None:
    path = _pixel_preflight_cache_path(key)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, Mapping) and data.get("version") == _PIXEL_PREFLIGHT_CACHE_VERSION and isinstance(data.get("safe"), bool):
        return bool(data["safe"])
    return None

def _store_pixel_preflight_cache(key: str, safe: bool) -> None:
    path = _pixel_preflight_cache_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"version": _PIXEL_PREFLIGHT_CACHE_VERSION, "safe": bool(safe)}, sort_keys=True), encoding="utf-8")
    temporary.replace(path)

class ReferenceReviewError(ValueError):
    """Safe validation error for exact panel review boundaries."""

    def __init__(self, message: str, code: str = "visual.panel_lineage_unavailable") -> None:
        super().__init__(message)
        self.code = code


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _box(value: object) -> tuple[int, int, int, int]:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise ReferenceReviewError("crop box is invalid")
    result = tuple(int(item) for item in value)
    if any(isinstance(item, bool) for item in value) or result[0] < 0 or result[1] < 0:
        raise ReferenceReviewError("crop box is invalid")
    if result[2] <= result[0] or result[3] <= result[1]:
        raise ReferenceReviewError("crop box is invalid")
    return result  # type: ignore[return-value]


def enumerate_reference_roi_alternatives(
    panel_size: tuple[int, int],
    candidate: object,
    profile: object,
    *,
    image: Image.Image | None = None,
    border_mask: framing_analysis.BorderMaskResult | None = None,
    measure_edge_blank: bool = True,
) -> tuple[editorial_visual_planner.ReferenceROIAlternative, ...]:
    """Build deduplicated panel-local ROI phases using render's box geometry."""
    from app.services import render

    focal_points = tuple(getattr(getattr(candidate, "features", None), "focal_points", ()) or ())
    primary_focus = focal_points[0] if focal_points else (0.5, 0.5)
    alternate_focus = focal_points[1] if len(focal_points) > 1 else (
        1.0 - float(primary_focus[0]),
        float(primary_focus[1]),
    )
    target_size = (int(profile.final_width), int(profile.final_height))
    alternatives: list[editorial_visual_planner.ReferenceROIAlternative] = []
    seen_boxes: set[tuple[int, int, int, int]] = set()

    def add(
        kind: str,
        label: str,
        focus: tuple[float, float],
        scale: float,
        travel: tuple[float, float] = (0.0, 0.0),
    ) -> None:
        crop_box = render.reference_panel_crop_box(
            panel_size, target_size, float(focus[0]), float(focus[1]), scale=scale
        )
        if crop_box in seen_boxes:
            return
        seen_boxes.add(crop_box)
        # start at the focal point, end at a small directional offset so pan
        # and focus-shift curves have real travel to animate.
        end_x = min(0.95, max(0.05, float(focus[0]) + travel[0]))
        end_y = min(0.95, max(0.05, float(focus[1]) + travel[1]))
        focus_tuple = (
            float(focus[0]),
            float(focus[1]),
            end_x,
            end_y,
        )
        edge_blank_fraction = None
        if image is not None and measure_edge_blank:
            final_view = image.crop(crop_box).resize(
                target_size, Image.Resampling.LANCZOS
            )
            edge_blank_fraction = framing_analysis.color_agnostic_edge_blank_fractions(
                framing_analysis.reference_tv_range_preview(final_view)
            )["max_edge_blank_fraction"]
        alternatives.append(
            editorial_visual_planner.ReferenceROIAlternative(
                kind=kind,
                roi_label=label,
                crop_box=crop_box,
                focus=focus_tuple,
                edge_blank_fraction=edge_blank_fraction,
            )
        )

    add("primary", "panel_primary", (float(primary_focus[0]), float(primary_focus[1])), 1.0)
    add("alternate_roi", "panel_alternate", (float(alternate_focus[0]), float(alternate_focus[1])), 1.0, travel=(-0.12, 0.0))
    add("tighter_crop", "panel_tighter", (float(primary_focus[0]), float(primary_focus[1])), 0.88, travel=(0.0, 0.06))
    add("aggressive_crop", "panel_aggressive", (float(primary_focus[0]), float(primary_focus[1])), 0.60, travel=(0.10, 0.0))
    add("aggressive_crop", "panel_aggressive_2", (float(primary_focus[0]), float(primary_focus[1])), 0.45, travel=(-0.08, 0.05))
    add("aggressive_crop", "panel_aggressive_3", (float(primary_focus[0]), float(primary_focus[1])), 0.32, travel=(0.06, -0.04))
    add("aggressive_crop", "panel_aggressive_4", (float(primary_focus[0]), float(primary_focus[1])), 0.24, travel=(-0.05, -0.03))

    # A provider focal point is often too sparse for a very tall page: it can
    # land in a quiet gutter even when another viewport on the same immutable
    # panel is frameable.  Add a small, deterministic content scan so the
    # existing blank/balloon/protected feasibility gate can choose a safer
    # window.  These are still panel-local ROIs with the same evidence and
    # lineage; the scan never admits a crop by itself.
    if image is not None and panel_size[1] > panel_size[0]:
        scan_x = (0.20, 0.50, 0.80)
        scan_y = (0.14, 0.38, 0.62, 0.86)
        for row, focus_y in enumerate(scan_y):
            for column, focus_x in enumerate(scan_x):
                travel_x = 0.04 if focus_x < 0.5 else -0.04
                travel_y = 0.03 if focus_y < 0.5 else -0.03
                add(
                    "alternate_roi",
                    f"content_scan_{row:02d}_{column:02d}",
                    (focus_x, focus_y),
                    1.0,
                    travel=(travel_x, travel_y),
                )
    hard_blank = reference_profile.REVIEW_MAX_FRAME_EDGE_BLANK_FRACTION
    # The expensive multi-focus rescue is triggered only when every normal ROI
    # still fails the authoritative source-local border-mask 8% hard gate. The
    # pixel-preview edge metric remains diagnostic/ranking data; it must not
    # decide whether rescue runs because TV-range resampling can produce false
    # negatives near otherwise admissible panel edges.
    if border_mask is not None and panel_size[1] > panel_size[0]:
        has_hard_safe_view = any(
            framing_analysis.edge_connected_blank_fraction_for_crop(
                border_mask, tuple(int(value) for value in roi.crop_box)
            )
            <= hard_blank + 1e-9
            for roi in alternatives
        )
        if not has_hard_safe_view:
            rescue_x = (0.38, 0.50, 0.62)
            rescue_y = (0.44, 0.60, 0.76)
            rescue_scales = (0.60, 0.45, 0.32, 0.24)
            for scale_index, scale in enumerate(rescue_scales):
                for row, focus_y in enumerate(rescue_y):
                    for column, focus_x in enumerate(rescue_x):
                        add(
                            "aggressive_crop",
                            f"content_rescue_{scale_index:02d}_{row:02d}_{column:02d}",
                            (focus_x, focus_y),
                            scale,
                        )
    return tuple(alternatives)


def resolve_panel_eligibility(
    ordered_regions: Sequence[object],
    section_evidence_panel_ids: Mapping[str, Sequence[str]],
    section_citations: Mapping[str, Sequence[int]],
    beats_by_section: Mapping[str, Sequence[str]],
    *,
    allow_missing_explicit: bool = False,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Map only explicitly cited panels into section/beat eligibility."""
    by_panel: dict[str, object] = {}
    by_source_order: dict[int, list[object]] = {}
    for region in ordered_regions:
        panel_id = str(getattr(region, "panel_id", ""))
        region_id = str(getattr(region, "id", ""))
        if not panel_id or not region_id or panel_id in by_panel:
            raise ReferenceReviewError("duplicate panel lineage identity")
        by_panel[panel_id] = region
        by_source_order.setdefault(int(getattr(region, "source_order", -1)), []).append(region)

    section_names = sorted(
        set(section_evidence_panel_ids) | set(section_citations) | set(beats_by_section)
    )
    if not section_names:
        raise ReferenceReviewError("no section evidence mapping")
    eligible_by_region = {str(region.id): set() for region in ordered_regions}
    beats_by_region = {str(region.id): set() for region in ordered_regions}
    for section in section_names:
        explicit_ids = tuple(str(value) for value in (section_evidence_panel_ids.get(section) or ()))
        selected: list[object] = []
        if explicit_ids:
            if len(set(explicit_ids)) != len(explicit_ids):
                raise ReferenceReviewError("duplicate explicit panel evidence")
            for panel_id in explicit_ids:
                region = by_panel.get(panel_id)
                if region is None:
                    if allow_missing_explicit:
                        continue
                    raise ReferenceReviewError("explicit panel evidence is missing")
                selected.append(region)
        else:
            citations = tuple(section_citations.get(section) or ())
            for citation in citations:
                if isinstance(citation, bool) or not isinstance(citation, int):
                    raise ReferenceReviewError("citation is not a source order")
                selected.extend(by_source_order.get(citation, ()))
            if citations and not selected:
                raise ReferenceReviewError("cited source order is unavailable")
        if not selected:
            if allow_missing_explicit:
                # Every explicit panel for this section was skipped by a local
                # framing guard (e.g. sliver crops below the minimum height).
                # The section cannot be mapped truthfully; omit it instead of
                # failing the whole review render.
                continue
            raise ReferenceReviewError(f"section {section} has no truthful panel mapping")
        for region in selected:
            region_id = str(region.id)
            eligible_by_region[region_id].add(section)
            beats_by_region[region_id].update(
                str(beat) for beat in (beats_by_section.get(section) or ()) if str(beat)
            )
    return eligible_by_region, beats_by_region



def _panel_bounds(region: object) -> tuple[int, int, int, int]:
    raw = getattr(region, "bounds_json", None)
    if not isinstance(raw, Mapping):
        raise ReferenceReviewError("panel bounds are missing")
    try:
        values = tuple(int(raw[key]) for key in ("x", "y", "width", "height"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ReferenceReviewError("panel bounds are malformed") from exc
    x, y, width, height = values
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ReferenceReviewError("panel bounds are invalid")
    return x, y, x + width, y + height


def _narrative_profile_id(script: object) -> str:
    metadata = getattr(script, "editorial_metadata", None)
    if not isinstance(metadata, Mapping):
        return ""
    identity = metadata.get("narrative_identity")
    return str(identity.get("profile_id", "")) if isinstance(identity, Mapping) else ""


def section_evidence_maps(script: object) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[int, ...]], dict[str, tuple[str, ...]]]:
    """Extract immutable panel evidence while preserving repeated canonical sections."""
    sections = tuple(getattr(script, "sections", ()) or ())
    evidence_rows: dict[str, list[str]] = {}
    citation_rows: dict[str, list[int]] = {}
    for section in sections:
        if not isinstance(section, Mapping):
            continue
        name = str(section.get("section", ""))
        if not name:
            continue
        evidence_rows.setdefault(name, [])
        citation_rows.setdefault(name, [])
        for panel_id in section.get("evidence_panel_ids") or ():
            value = str(panel_id)
            if value and value not in evidence_rows[name]:
                evidence_rows[name].append(value)
        for citation in section.get("citations") or ():
            if isinstance(citation, int) and not isinstance(citation, bool) and citation not in citation_rows[name]:
                citation_rows[name].append(citation)
    evidence = {name: tuple(values) for name, values in evidence_rows.items()}
    citations = {name: tuple(values) for name, values in citation_rows.items()}
    beats = dict.fromkeys(set(evidence) | set(citations), ())
    return evidence, citations, beats


def section_direct_claim_evidence_map(script: object) -> dict[str, tuple[str, ...]]:
    """Return claim-native evidence separately from broadened passage visuals."""
    if _narrative_profile_id(script) != "retention_story_v1":
        return {}
    rows: dict[str, list[str]] = {}
    for section in tuple(getattr(script, "sections", ()) or ()):
        if not isinstance(section, Mapping):
            continue
        name = str(section.get("section", ""))
        if not name:
            continue
        rows.setdefault(name, [])
        for claim in section.get("evidence") or ():
            if not isinstance(claim, Mapping):
                continue
            for panel_id in claim.get("panel_ids") or ():
                value = str(panel_id)
                if value and value not in rows[name]:
                    rows[name].append(value)
    return {name: tuple(values) for name, values in rows.items()}


def section_claim_text_map(
    script: object, evidence_graph: Mapping[str, object] | None
) -> dict[str, tuple[str, ...]]:
    """Resolve claim text per retention section without broadening claim lineage."""
    if _narrative_profile_id(script) != "retention_story_v1" or not isinstance(evidence_graph, Mapping):
        return {}
    claims: dict[str, str] = {}
    for claim in evidence_graph.get("claims") or ():
        if not isinstance(claim, Mapping):
            continue
        claim_id = str(claim.get("claim_id", ""))
        text = str(claim.get("text", "")).strip()
        if claim_id and text:
            claims[claim_id] = text
    rows: dict[str, tuple[str, ...]] = {}
    for section in tuple(getattr(script, "sections", ()) or ()):
        if not isinstance(section, Mapping):
            continue
        name = str(section.get("section", ""))
        texts = tuple(claims[str(cid)] for cid in section.get("claim_ids") or () if str(cid) in claims)
        if name and texts:
            rows[name] = texts
    return rows


def section_story_text_map(script: object) -> dict[str, tuple[str, ...]]:
    """Expose spoken beat text only for the opt-in retention narrative profile."""
    if _narrative_profile_id(script) != "retention_story_v1":
        return {}
    rows: dict[str, list[str]] = {}
    for section in tuple(getattr(script, "sections", ()) or ()):
        if not isinstance(section, Mapping):
            continue
        name = str(section.get("section", ""))
        text = str(section.get("text", "")).strip()
        if name and text:
            rows.setdefault(name, []).append(text)
    return {name: tuple(values) for name, values in rows.items()}


def enumerate_conservative_full_panel_roi_alternatives(
    panel_size: tuple[int, int],
) -> tuple[editorial_visual_planner.ReferenceROIAlternative, ...]:
    """Return the sole ROI allowed for explicit unknown-geometry fallback."""

    width, height = panel_size
    return (
        editorial_visual_planner.ReferenceROIAlternative(
            kind="primary",
            roi_label="conservative_full_panel",
            crop_box=(0, 0, width, height),
            focus=(0.5, 0.5, 0.5, 0.5),
        ),
    )


def _roi_passes_exact_pixel_preflight(
    *,
    image: Image.Image,
    roi: editorial_visual_planner.ReferenceROIAlternative,
    evidence: visual_scoring.PanelVisualEvidence,
    border_mask: framing_analysis.BorderMaskResult,
    panel_size: tuple[int, int],
    profile: object,
    allow_conservative_full_panel: bool,
) -> bool:
    """Mirror render-time pixel blank refinement before standard production selection."""
    from app.services import render

    ready = visual_scoring.require_reference_ready_visual_evidence(
        evidence,
        allow_conservative_full_panel=allow_conservative_full_panel,
    )
    feasible, telemetry = editorial_visual_planner._review_framing_candidate_is_feasible(
        roi.crop_box,
        ready,
        border_mask,
        panel_size,
        (int(profile.final_width), int(profile.final_height)),
        review_aggressive_crop=True,
        standard_blank_target=reference_profile.REVIEW_MAX_FRAME_EDGE_BLANK_FRACTION,
        allow_conservative_full_panel=allow_conservative_full_panel,
    )
    if not feasible:
        return False
    cache_key = _pixel_preflight_cache_key(
        image, roi, evidence, border_mask, panel_size, profile, allow_conservative_full_panel
    )
    cached = _load_pixel_preflight_cache(cache_key)
    if cached is not None:
        return cached
    fallback_reason = (
        telemetry.get("fallback_reason")
        if isinstance(telemetry, Mapping)
        else getattr(telemetry, "fallback_reason", None)
    )
    blank_threshold = (
        reference_profile.REVIEW_COHERENCE_RESCUE_MAX_FRAME_EDGE_BLANK_FRACTION
        if fallback_reason == reference_profile.REVIEW_COHERENCE_RESCUE_REASON
        else reference_profile.REVIEW_MAX_FRAME_EDGE_BLANK_FRACTION
    )
    try:
        render._refine_review_pixel_blank_crop(
            image,
            roi.crop_box,
            evidence=evidence,
            mask=border_mask,
            panel_size=panel_size,
            target_size=(int(profile.final_width), int(profile.final_height)),
            profile=profile,
            feasibility_kwargs={"review_aggressive_crop": True},
            initial_telemetry=telemetry,
            blank_threshold=float(blank_threshold),
        )
    except render.RenderError as exc:
        if exc.code == "visual.blank_infeasible":
            _store_pixel_preflight_cache(cache_key, False)
            return False
        raise ReferenceReviewError(str(exc), exc.code) from exc
    _store_pixel_preflight_cache(cache_key, True)
    return True


_RETENTION_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "at", "for", "from",
    "with", "his", "her", "their", "this", "that", "these", "those", "is", "are", "was", "were",
    "be", "been", "being", "he", "she", "they", "it", "as", "by", "into", "while", "when", "after",
    "before", "now", "then", "just", "still", "only", "all", "can", "could", "would", "will",
})


def _retention_tokens(value: object) -> set[str]:
    text = str(value or "").casefold()
    result: set[str] = set()
    for raw in re.findall(r"[^\W_]+", text, flags=re.UNICODE):
        token = raw
        if token.endswith("ies") and len(token) >= 6:
            token = f"{token[:-3]}y"
        elif token.endswith("s") and len(token) >= 5 and not token.endswith("ss"):
            token = token[:-1]
        if len(token) >= 3 and token not in _RETENTION_STOPWORDS:
            result.add(token)
    return result


def _retention_story_relevance(
    region: object,
    sections: Sequence[str],
    story_text_by_section: Mapping[str, Sequence[str]],
    direct_evidence_by_section: Mapping[str, Sequence[str]] | None = None,
    claim_text_by_section: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, float]:
    raw = getattr(region, "observation_json", None)
    if not isinstance(raw, Mapping) or not story_text_by_section:
        return {}
    panel_id = str(getattr(region, "panel_id", ""))
    direct_evidence_by_section = direct_evidence_by_section or {}
    claim_text_by_section = claim_text_by_section or {}
    evidence_parts: list[str] = []
    for key in ("visible_facts", "dialogue_or_ocr", "inferences"):
        values = raw.get(key)
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            evidence_parts.extend(str(value) for value in values if str(value).strip())
    evidence_tokens = _retention_tokens(" ".join(evidence_parts))
    if not evidence_tokens:
        return {}
    result: dict[str, float] = {}
    for section in sections:
        beat_text = " ".join(str(value) for value in story_text_by_section.get(section, ()) if str(value).strip())
        story_tokens = _retention_tokens(beat_text)
        if not story_tokens:
            continue
        other_story_tokens: set[str] = set()
        for other_section, values in story_text_by_section.items():
            if str(other_section) == str(section):
                continue
            other_story_tokens.update(
                _retention_tokens(" ".join(str(value) for value in values if str(value).strip()))
            )
        section_specific_tokens = story_tokens - other_story_tokens
        if section_specific_tokens:
            story_tokens = section_specific_tokens
        overlap = len(story_tokens & evidence_tokens)
        claim_text = " ".join(
            str(value) for value in claim_text_by_section.get(str(section), ()) if str(value).strip()
        )
        claim_tokens = _retention_tokens(claim_text)
        claim_overlap = len(claim_tokens & evidence_tokens)
        # Passage text is primary. Claim text is a secondary bridge for
        # equivalent story facts whose concrete names do not repeat in every
        # passage (for example Snow Plum Pill vs. spiritual elixir).
        score = overlap / max(1.0, len(story_tokens) ** 0.5)
        score += 0.5 * claim_overlap / max(1.0, len(claim_tokens) ** 0.5)
        if panel_id and panel_id in set(direct_evidence_by_section.get(str(section), ())):
            score += 4.0
        result[str(section)] = round(float(score), 6)
    return result


def expand_retention_section_evidence(
    script: object,
    regions: Sequence[object],
    existing: Mapping[str, Sequence[str]],
    *,
    max_semantic_per_section: int = 16,
    claim_text_by_section: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, tuple[str, ...]]:
    """Add top grounded full-ledger matches without bypassing visual gates."""
    if _narrative_profile_id(script) != "retention_story_v1":
        return {str(k): tuple(v) for k, v in existing.items()}
    story = section_story_text_map(script)
    direct = section_direct_claim_evidence_map(script)
    output = {str(k): list(dict.fromkeys(str(x) for x in v if str(x))) for k, v in existing.items()}
    for section in story:
        output.setdefault(section, [])
        ranked: list[tuple[float, int, str]] = []
        for region in regions:
            panel_id = str(getattr(region, "panel_id", ""))
            source_order = int(getattr(region, "source_order", -1))
            if not panel_id or source_order <= 0:
                continue
            score = _retention_story_relevance(
                region, (section,), story, direct, claim_text_by_section
            ).get(section, 0.0)
            if score > 0.0:
                ranked.append((float(score), source_order, panel_id))
        for _score, _order, panel_id in sorted(
            ranked, key=lambda row: (-row[0], row[1], row[2])
        )[: max(1, int(max_semantic_per_section))]:
            if panel_id not in output[section]:
                output[section].append(panel_id)
    return {name: tuple(values) for name, values in output.items()}




def build_reference_panel_fallback_candidates(
    *,
    panel_regions: Sequence[object],
    panel_candidates_by_region_id: Mapping[str, object],
    panel_crops_by_region_id: Mapping[str, Image.Image],
    section_evidence_panel_ids: Mapping[str, Sequence[str]],
    section_citations: Mapping[str, Sequence[int]],
    beats_by_section: Mapping[str, Sequence[str]],
    profile: object,
    story_text_by_section: Mapping[str, Sequence[str]] | None = None,
    direct_evidence_by_section: Mapping[str, Sequence[str]] | None = None,
    claim_text_by_section: Mapping[str, Sequence[str]] | None = None,
    source_upscale_manifests_by_region_id: Mapping[str, Mapping[str, Any]] | None = None,
    allow_missing_explicit: bool = False,
    allow_conservative_full_panel: bool = False,
    pixel_refinement_preflight: bool = False,
) -> tuple[editorial_visual_planner.ReferencePanelFallbackCandidate, ...]:
    """Build exact panel candidates without database or asset-level fallback."""
    ordered_regions = tuple(
        sorted(
            panel_regions,
            key=lambda region: (
                int(getattr(region, "source_order", -1)),
                str(getattr(region, "panel_id", "")),
                str(getattr(region, "id", "")),
            ),
        )
    )
    try:
        eligible_by_region, beats_by_region = resolve_panel_eligibility(
            ordered_regions,
            section_evidence_panel_ids,
            section_citations,
            beats_by_section,
            allow_missing_explicit=allow_missing_explicit,
        )
        built: list[editorial_visual_planner.ReferencePanelFallbackCandidate] = []
        for region in ordered_regions:
            region_id = str(region.id)
            if not eligible_by_region.get(region_id):
                continue
            candidate = panel_candidates_by_region_id.get(region_id)
            crop = panel_crops_by_region_id.get(region_id)
            if candidate is None or not isinstance(crop, Image.Image):
                raise ReferenceReviewError("exact panel candidate or crop is missing")
            bounds = _panel_bounds(region)
            expected_size = (bounds[2] - bounds[0], bounds[3] - bounds[1])
            if crop.size != expected_size or crop.width <= 0 or crop.height <= 0:
                raise ReferenceReviewError("panel crop dimensions do not match persisted bounds")
            if (
                candidate.asset_id != region.source_asset_id
                or int(candidate.order_index) != int(region.source_order)
            ):
                raise ReferenceReviewError("panel candidate lineage does not match region")
            raw_observation = getattr(region, "observation_json", None)
            raw_evidence = (
                raw_observation.get("visual_evidence")
                if isinstance(raw_observation, Mapping)
                else None
            )
            if not isinstance(raw_evidence, Mapping):
                raise ReferenceReviewError("panel visual evidence is missing")
            evidence = _review_ready_evidence(
                region,
                visual_scoring.parse_panel_visual_evidence(raw_evidence),
                allow_conservative_full_panel=allow_conservative_full_panel,
            )
            if evidence is None:
                continue
            # Blank-dominant panels (near-empty splash/title/corrupt crops) are
            # never frameable content. Skip them before feasibility so they
            # cannot be selected and read as a lingering title card.
            if _panel_is_blank_dominant(crop):
                continue
            # Landscape covers/thumbnails (wider than tall) are not story panels;
            # skip them so the cover never appears mid-video.
            if crop.width > crop.height * 1.2:
                continue
            if (
                evidence.panel_id != region.panel_id
                or evidence.source_asset_id != region.source_asset_id
                or evidence.source_order != region.source_order
            ):
                raise ReferenceReviewError("panel visual evidence lineage does not match region")
            evidence_hash = visual_scoring.visual_evidence_hash(evidence)
            checksum = str(getattr(region, "source_asset_checksum", "") or "")
            if not checksum:
                raise ReferenceReviewError("panel source checksum is missing")
            mask = framing_analysis.build_color_agnostic_border_mask(
                crop,
                evidence,
                grid_long_edge=int(profile.framing_mask_grid_long_edge),
                allow_conservative_full_panel=allow_conservative_full_panel,
            )
            roi_alternatives = (
                enumerate_conservative_full_panel_roi_alternatives(expected_size)
                if visual_scoring.is_conservative_full_panel_visual_evidence(evidence)
                else enumerate_reference_roi_alternatives(
                    expected_size,
                    candidate,
                    profile,
                    image=crop,
                    border_mask=mask,
                )
            )
            if pixel_refinement_preflight:
                roi_alternatives = tuple(
                    roi
                    for roi in roi_alternatives
                    if _roi_passes_exact_pixel_preflight(
                        image=crop,
                        roi=roi,
                        evidence=evidence,
                        border_mask=mask,
                        panel_size=expected_size,
                        profile=profile,
                        allow_conservative_full_panel=allow_conservative_full_panel,
                    )
                )
                if not roi_alternatives:
                    continue
                if not any(roi.kind == "primary" for roi in roi_alternatives):
                    promoted = replace(roi_alternatives[0], kind="primary")
                    roi_alternatives = (promoted, *roi_alternatives[1:])
            built.append(
                editorial_visual_planner.ReferencePanelFallbackCandidate(
                    source_asset_id=str(region.source_asset_id),
                    panel_region_id=region_id,
                    panel_id=str(region.panel_id),
                    source_order=int(region.source_order),
                    panel_bounds=bounds,
                    panel_size=expected_size,
                    border_mask=mask,
                    source_asset_checksum=checksum,
                    visual_evidence=evidence,
                    evidence_hash=evidence_hash,
                    eligible_sections=tuple(sorted(eligible_by_region[region_id])),
                    eligible_beats=tuple(sorted(beats_by_region[region_id])),
                    roi_alternatives=roi_alternatives,
                    panel_candidate=candidate,
                    story_relevance_by_section=_retention_story_relevance(
                        region,
                        tuple(sorted(eligible_by_region[region_id])),
                        story_text_by_section or {},
                        direct_evidence_by_section,
                        claim_text_by_section,
                    ),
                    source_upscale_manifest=(
                        dict((source_upscale_manifests_by_region_id or {}).get(region_id, {}))
                        or None
                    ),
                )
            )
        return tuple(built)
    except ReferenceReviewError:
        raise
    except (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        visual_scoring.VisualEvidenceError,
    ) as exc:
        raise ReferenceReviewError("malformed panel candidate") from exc



def _panel_is_blank_dominant(crop) -> bool:
    """True when a panel is mostly empty (splash, title card, corrupt crop)."""
    try:
        gray = crop.convert("L")
        small = gray.resize((48, 48))
        pixels = list(small.get_flattened_data())
        if not pixels:
            return True
        white = sum(1 for value in pixels if value > 235)
        dark = sum(1 for value in pixels if value < 40)
        return (white / len(pixels)) > 0.72 and (dark / len(pixels)) < 0.05
    except Exception:
        return False


def _has_visible_facts(observation: object) -> bool:
    if not isinstance(observation, Mapping):
        return False
    values = observation.get("visible_facts")
    if not isinstance(values, list) or not values:
        return False
    for value in values:
        if isinstance(value, str) and value.strip():
            continue
        if isinstance(value, Mapping) and any(
            isinstance(candidate, str) and candidate.strip()
            for candidate in value.values()
        ):
            continue
        return False
    return True


def _review_ready_evidence(
    region: object,
    evidence: visual_scoring.PanelVisualEvidence,
    *,
    allow_conservative_full_panel: bool,
) -> visual_scoring.PanelVisualEvidence | None:
    if evidence.balloon_mask_status != "unknown":
        return evidence
    if visual_scoring.is_conservative_full_panel_visual_evidence(evidence):
        return evidence if allow_conservative_full_panel else None
    if not allow_conservative_full_panel:
        return None
    observation = getattr(region, "observation_json", None)
    if not isinstance(observation, Mapping) or not _has_visible_facts(observation):
        return None
    return visual_scoring.conservative_full_panel_visual_evidence(
        panel_id=str(getattr(region, "panel_id", "")),
        source_asset_id=str(getattr(region, "source_asset_id", "")),
        source_order=int(getattr(region, "source_order", -1)),
        reason="provider geometry remained unknown; review-only whole-panel fallback",
    )


def panel_reference_roi_safety(
    region: object,
    crop: Image.Image,
    candidate: object,
    profile: object,
    *,
    editorial_sections: Sequence[str] = (),
    allow_conservative_full_panel: bool = True,
) -> tuple[bool, tuple[str, ...]]:
    """Return generic framing safety plus section-specific editorial safety."""
    if not reference_profile.review_panel_source_geometry_is_renderable(crop.size):
        return False, ()
    if _panel_is_blank_dominant(crop) or crop.width > crop.height * 1.2:
        return False, ()
    observation = getattr(region, "observation_json", None)
    raw = observation.get("visual_evidence") if isinstance(observation, Mapping) else None
    if not isinstance(raw, Mapping):
        return False, ()
    try:
        evidence = visual_scoring.parse_panel_visual_evidence(raw)
        ready = _review_ready_evidence(
            region, evidence,
            allow_conservative_full_panel=allow_conservative_full_panel,
        )
        if ready is None:
            return False, ()
        mask = framing_analysis.build_color_agnostic_border_mask(
            crop,
            ready,
            grid_long_edge=int(profile.framing_mask_grid_long_edge),
            allow_conservative_full_panel=allow_conservative_full_panel,
        )
        rois = enumerate_reference_roi_alternatives(
            crop.size, candidate, profile, image=crop, border_mask=mask,
            measure_edge_blank=False,
        )
        sections = tuple(dict.fromkeys(str(value) for value in editorial_sections if str(value)))
        safe_sections: set[str] = set()
        generic_feasible = False
        editorial_candidate = type(
            "EditorialSafetyCandidate",
            (),
            {"panel_size": crop.size, "visual_evidence": ready, "panel_candidate": candidate},
        )()
        for roi in rois:
            feasible, telemetry = editorial_visual_planner._review_framing_candidate_is_feasible(
                roi.crop_box,
                ready,
                mask,
                crop.size,
                (int(profile.final_width), int(profile.final_height)),
                review_aggressive_crop=True,
                standard_blank_target=reference_profile.REVIEW_MAX_FRAME_EDGE_BLANK_FRACTION,
                allow_conservative_full_panel=allow_conservative_full_panel,
            )
            if not feasible:
                continue
            generic_feasible = True
            for section in sections:
                metrics = editorial_visual_planner._review_crop_editorial_metrics(
                    editorial_candidate, roi, telemetry, section=section, beat=""
                )
                if editorial_visual_planner._review_editorial_rejection_code(metrics) is None:
                    safe_sections.add(section)
            if generic_feasible and len(safe_sections) == len(sections):
                break
        return generic_feasible, tuple(section for section in sections if section in safe_sections)
    except (AttributeError, TypeError, ValueError, visual_scoring.VisualEvidenceError):
        return False, ()


def panel_has_feasible_reference_roi(
    region: object,
    crop: Image.Image,
    candidate: object,
    profile: object,
    *,
    allow_conservative_full_panel: bool = True,
) -> bool:
    """Return whether an exact panel has at least one production-safe 9:16 ROI."""
    feasible, _sections = panel_reference_roi_safety(
        region, crop, candidate, profile,
        allow_conservative_full_panel=allow_conservative_full_panel,
    )
    return feasible


def validated_visual_snapshot(
    region: object,
    *,
    allow_conservative_full_panel: bool = False,
) -> dict[str, Any]:
    observation = getattr(region, "observation_json", None)
    raw = observation.get("visual_evidence") if isinstance(observation, Mapping) else None
    if not isinstance(raw, Mapping):
        raise ReferenceReviewError("panel visual evidence is missing")
    try:
        evidence = visual_scoring.parse_panel_visual_evidence(raw)
        evidence = _review_ready_evidence(
            region,
            evidence,
            allow_conservative_full_panel=allow_conservative_full_panel,
        )
        if evidence is None:
            raise visual_scoring.VisualEvidenceError(
                "visual.balloon_mask_unknown",
                "review framing requires known or explicit conservative geometry",
            )
        if (
            evidence.panel_id != region.panel_id
            or evidence.source_asset_id != region.source_asset_id
            or evidence.source_order != region.source_order
        ):
            raise visual_scoring.VisualEvidenceError(
                "visual.lineage_mismatch", "visual evidence lineage does not match panel region"
            )
        return visual_scoring.panel_visual_evidence_json(evidence)
    except visual_scoring.VisualEvidenceError as exc:
        raise ReferenceReviewError(str(exc), "visual.panel_lineage_unavailable") from exc


def bind_reference_panel_shots(
    planned: Sequence[Mapping[str, Any]],
    *,
    candidate_registry: Mapping[str, object],
    regions: Sequence[object],
    assets: Sequence[object],
    allow_conservative_full_panel: bool = False,
) -> list[dict[str, Any]]:
    """Validate and bind planner-selected exact panels without database access."""
    by_region = {str(region.id): region for region in regions}
    assets_by_id = {str(asset.id): asset for asset in assets}
    bound: list[dict[str, Any]] = []
    for shot in planned:
        region_id = str(shot.get("panel_region_id", ""))
        candidate = candidate_registry.get(region_id)
        region = by_region.get(region_id)
        asset_id = shot.get("asset_id")
        if candidate is None or region is None or asset_id not in assets_by_id:
            raise ReferenceReviewError("planner selected an unknown panel")
        asset = assets_by_id[str(asset_id)]
        try:
            bounds = _panel_bounds(region)
            expected_size = (bounds[2] - bounds[0], bounds[3] - bounds[1])
            asset_checksum = str(getattr(asset, "original_checksum", "") or getattr(asset, "checksum", "") or "")
            if (
                candidate.panel_region_id != region_id
                or candidate.panel_id != region.panel_id
                or candidate.source_asset_id != asset.id
                or shot.get("panel_id") != candidate.panel_id
                or shot.get("source_order") != candidate.source_order
                or shot.get("asset_id") != candidate.source_asset_id
                or tuple(shot.get("panel_bounds", ())) != candidate.panel_bounds
                or tuple(shot.get("panel_size", ())) != expected_size
                or candidate.panel_bounds != bounds
                or candidate.source_asset_checksum != asset_checksum
                or region.source_asset_checksum
                and region.source_asset_checksum != asset_checksum
            ):
                raise ValueError("planner panel lineage does not match persisted region")
            snapshot = validated_visual_snapshot(
                region,
                allow_conservative_full_panel=allow_conservative_full_panel,
            )
            if _canonical(snapshot) != _canonical(
                visual_scoring.panel_visual_evidence_json(candidate.visual_evidence)
            ):
                raise ValueError("planner evidence snapshot does not match persisted region")
            if _canonical(shot.get("visual_evidence")) != _canonical(snapshot):
                raise ValueError("planner evidence snapshot is not canonical")
            if _canonical(shot.get("border_mask")) != _canonical(asdict(candidate.border_mask)):
                raise ValueError("planner border mask snapshot does not match candidate")
            attempts = shot.get("fallback_attempts")
            if not isinstance(attempts, list):
                raise ValueError("planner fallback ledger is missing")
            accepted = [
                entry for entry in attempts
                if isinstance(entry, Mapping) and entry.get("accepted") is True
            ]
            if len(accepted) != 1:
                raise ValueError("planner fallback ledger must contain one accepted entry")
            accepted_entry = accepted[0]
            roi = shot.get("roi")
            telemetry = shot.get("framing_telemetry")
            if not isinstance(roi, Mapping) or not isinstance(telemetry, Mapping):
                raise ValueError("planner accepted ROI or telemetry is missing")
            comparisons = {
                "panel_region_id": region_id,
                "panel_id": candidate.panel_id,
                "source_asset_id": candidate.source_asset_id,
                "source_order": candidate.source_order,
                "source_asset_checksum": candidate.source_asset_checksum,
                "panel_size": list(expected_size),
                "evidence_hash": candidate.evidence_hash,
                "roi_label": roi.get("roi_label"),
                "crop_box": list(roi.get("crop_box", ())),
                "roi_kind": roi.get("kind"),
                "detector_version": candidate.border_mask.detector_version,
                "mask_sha256": candidate.border_mask.mask_sha256,
            }
            for key, expected in comparisons.items():
                if accepted_entry.get(key) != expected:
                    raise ValueError(f"accepted fallback ledger mismatch: {key}")
            if _canonical(accepted_entry.get("telemetry")) != _canonical(telemetry):
                raise ValueError("accepted fallback telemetry does not match scene")
            nested_roi = telemetry.get("selected_roi")
            if not isinstance(nested_roi, Mapping):
                raise ValueError("accepted fallback ROI is missing")
            for key in ("kind", "roi_label", "crop_box"):
                if nested_roi.get(key) != roi.get(key):
                    raise ValueError("accepted fallback ROI does not match telemetry")
            if candidate.eligible_sections and shot.get("section") not in candidate.eligible_sections:
                raise ValueError("planner selected a panel outside section eligibility")
        except ReferenceReviewError:
            raise
        except (AttributeError, KeyError, TypeError, ValueError, visual_scoring.VisualEvidenceError) as exc:
            raise ReferenceReviewError("planner panel snapshot is invalid") from exc
        bound.append(
            {
                **shot,
                "panel_region_id": region.id,
                "panel_id": region.panel_id,
                "panel_bounds": bounds,
                "panel_size": expected_size,
                "visual_evidence": snapshot,
                "source_asset_checksum": asset_checksum,
                "rejected_candidates": attempts,
            }
        )
    return bound


def attach_accepted_mask_snapshot(
    shot: Mapping[str, Any], candidate_registry: Mapping[str, object]
) -> list[dict[str, Any]]:
    """Persist the full mask only on the single accepted fallback attempt."""
    ledger = list(shot.get("fallback_attempts") or ())
    accepted = [entry for entry in ledger if isinstance(entry, Mapping) and entry.get("accepted") is True]
    if len(accepted) != 1:
        raise ReferenceReviewError("accepted fallback ledger is invalid")
    accepted_entry = accepted[0]
    region_id = str(accepted_entry.get("panel_region_id") or shot.get("panel_region_id") or "")
    candidate = candidate_registry.get(region_id)
    if candidate is None:
        raise ReferenceReviewError("accepted fallback panel is not in the candidate registry")
    result: list[dict[str, Any]] = []
    for entry in ledger:
        if not isinstance(entry, Mapping):
            raise ReferenceReviewError("fallback ledger entry is invalid")
        copied = dict(entry)
        if copied.get("accepted") is True:
            copied["border_mask"] = asdict(candidate.border_mask)
            copied["detector_version"] = candidate.border_mask.detector_version
            copied["mask_sha256"] = candidate.border_mask.mask_sha256
            copied["mask_source_width"] = candidate.border_mask.source_width
            copied["mask_source_height"] = candidate.border_mask.source_height
            if candidate.source_upscale_manifest is not None:
                copied["source_upscale_manifest"] = dict(
                    candidate.source_upscale_manifest
                )
        else:
            copied.pop("border_mask", None)
            copied.pop("source_upscale_manifest", None)
        result.append(copied)
    return result


def validate_accepted_fallback_ledger(
    ledger: Sequence[Mapping[str, Any]],
    *,
    panel_region_id: str,
    panel_id: str,
    source_asset_id: str,
    source_asset_checksum: str,
    source_order: int,
    panel_size: tuple[int, int],
    evidence: visual_scoring.PanelVisualEvidence,
    border_mask: framing_analysis.BorderMaskResult,
    selected_roi: Mapping[str, Any],
    framing_telemetry: Mapping[str, Any],
    allow_conservative_full_panel: bool = False,
) -> Mapping[str, Any]:
    """Revalidate one persisted accepted attempt against current pixels and evidence."""
    try:
        visual_scoring.validate_panel_visual_evidence(evidence)
        visual_scoring.require_reference_ready_visual_evidence(
            evidence,
            allow_conservative_full_panel=allow_conservative_full_panel,
        )
        local_hash = visual_scoring.visual_evidence_hash(evidence)
    except visual_scoring.VisualEvidenceError as exc:
        raise ReferenceReviewError(str(exc), exc.code) from exc
    if not isinstance(ledger, (list, tuple)) or any(
        not isinstance(item, Mapping) for item in ledger
    ):
        raise ReferenceReviewError("accepted fallback ledger is invalid")
    accepted = [entry for entry in ledger if entry.get("accepted") is True]
    if len(accepted) != 1:
        raise ReferenceReviewError("accepted fallback ledger is invalid")
    entry = accepted[0]
    expected_identity = {
        "panel_region_id": panel_region_id,
        "panel_id": panel_id,
        "source_asset_id": source_asset_id,
        "source_asset_checksum": source_asset_checksum,
        "source_order": source_order,
        "panel_size": list(panel_size),
        "evidence_hash": local_hash,
    }
    if any(entry.get(key) != value for key, value in expected_identity.items()):
        raise ReferenceReviewError("accepted fallback lineage is stale")
    if _canonical(entry.get("border_mask")) != _canonical(asdict(border_mask)):
        raise ReferenceReviewError("accepted fallback mask snapshot is stale")
    entry_telemetry = entry.get("telemetry")
    if not isinstance(entry_telemetry, Mapping) or _canonical(entry_telemetry) != _canonical(framing_telemetry):
        raise ReferenceReviewError("accepted fallback telemetry is stale")
    for field in (
        "edge_connected_blank_fraction",
        "non_discardable_low_information_fraction",
        "protected_retained_fraction",
        "balloon_mask_intersection_ratio",
        "subject_coverage",
        "face_coverage",
        "action_coverage",
        "effect_coverage",
        "continuity_context_coverage",
        "mask_confidence",
    ):
        value = entry_telemetry.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
            or not 0.0 <= float(value) <= 1.0
        ):
            raise ReferenceReviewError("accepted fallback telemetry is malformed")
    if not isinstance(selected_roi, Mapping):
        raise ReferenceReviewError("accepted fallback ROI is missing")
    nested_roi = entry_telemetry.get("selected_roi")
    if not isinstance(nested_roi, Mapping):
        raise ReferenceReviewError("accepted fallback ROI is missing")
    for key in ("kind", "roi_label", "crop_box"):
        if nested_roi.get(key) != selected_roi.get(key):
            raise ReferenceReviewError("accepted fallback ROI is stale")
    if entry.get("roi_label") != selected_roi.get("roi_label"):
        raise ReferenceReviewError("accepted fallback ROI label is stale")
    if entry.get("crop_box") != selected_roi.get("crop_box"):
        raise ReferenceReviewError("accepted fallback crop is stale")
    if entry.get("detector_version") != border_mask.detector_version:
        raise ReferenceReviewError("accepted fallback detector identity is stale")
    if entry.get("mask_sha256") != border_mask.mask_sha256:
        raise ReferenceReviewError("accepted fallback mask identity is stale")
    return entry
