"""Pure reference-review helpers shared by Task 7 orchestration and rendering."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from math import isfinite
from typing import Any

from PIL import Image

from app.services import editorial_visual_planner, framing_analysis, visual_scoring


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
        if image is not None:
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


def section_evidence_maps(script: object) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[int, ...]], dict[str, tuple[str, ...]]]:
    """Extract immutable panel-keyed evidence maps from a persisted script."""
    sections = tuple(getattr(script, "sections", ()) or ())
    evidence = {
        str(section.get("section", "")): tuple(
            str(panel_id) for panel_id in (section.get("evidence_panel_ids") or ())
        )
        for section in sections
    }
    citations = {
        str(section.get("section", "")): tuple(
            citation for citation in (section.get("citations") or ())
        )
        for section in sections
    }
    beats = dict.fromkeys(set(evidence) | set(citations), ())
    return evidence, citations, beats


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


def build_reference_panel_fallback_candidates(
    *,
    panel_regions: Sequence[object],
    panel_candidates_by_region_id: Mapping[str, object],
    panel_crops_by_region_id: Mapping[str, Image.Image],
    section_evidence_panel_ids: Mapping[str, Sequence[str]],
    section_citations: Mapping[str, Sequence[int]],
    beats_by_section: Mapping[str, Sequence[str]],
    profile: object,
    source_upscale_manifests_by_region_id: Mapping[str, Mapping[str, Any]] | None = None,
    allow_missing_explicit: bool = False,
    allow_conservative_full_panel: bool = False,
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
                    roi_alternatives=(
                        enumerate_conservative_full_panel_roi_alternatives(expected_size)
                        if visual_scoring.is_conservative_full_panel_visual_evidence(evidence)
                        else enumerate_reference_roi_alternatives(
                            expected_size,
                            candidate,
                            profile,
                            image=crop,
                        )
                    ),
                    panel_candidate=candidate,
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
            print("BIND_DEBUG accepted count:", len(accepted), "entries:", json.dumps([{
                "roi_label": e.get("roi_label"), "crop": e.get("crop_box"),
                "attempt_order": e.get("attempt_order"), "kind": e.get("kind"),
            } for e in accepted], indent=None)[:600])
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
                import difflib
                _a = json.dumps(accepted_entry.get("telemetry"), indent=None, sort_keys=True)
                _b = json.dumps(telemetry, indent=None, sort_keys=True)
                print("TELEM_DIFF:\n", "\n".join(list(difflib.unified_diff(_a.splitlines(), _b.splitlines(), lineterm=""))[:24]))
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
) -> Mapping[str, Any]:
    """Revalidate one persisted accepted attempt against current pixels and evidence."""
    try:
        visual_scoring.validate_panel_visual_evidence(evidence)
        visual_scoring.require_reference_ready_visual_evidence(evidence)
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
