"""Deterministic color-agnostic border analysis for reference framing.

This module owns only the low-information mask and its cache identity.  Typed
visual evidence remains owned by ``visual_scoring``; reference crop feasibility
and fallback decisions are deliberately handled by the later framing task.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import deque
from dataclasses import dataclass, replace
from math import floor
from typing import Any

from PIL import Image

from app.services.visual_scoring import (
    PanelVisualEvidence,
    VisualEvidenceError,
    is_conservative_full_panel_visual_evidence,
    panel_visual_evidence_json,
    require_reference_ready_visual_evidence,
    validate_panel_visual_evidence,
)

DETECTOR_VERSION = "COLOR_AGNOSTIC_BALLOON_FREE_V1:grid256:structure4"


def detector_contract_matches(contract_version: str, detector_version: str) -> bool:
    return detector_version == contract_version or detector_version.startswith(
        f"{contract_version}:"
    )


_LOW_INFORMATION_THRESHOLDS = (0.08, 0.20, 0.08, 0.08)
_NEIGHBOUR_OFFSETS = (
    (-1, -1),
    (0, -1),
    (1, -1),
    (-1, 0),
    (1, 0),
    (-1, 1),
    (0, 1),
    (1, 1),
)


@dataclass(frozen=True)
class BorderMaskResult:
    detector_version: str
    source_width: int
    source_height: int
    grid_width: int
    grid_height: int
    edge_connected_mask: tuple[tuple[bool, ...], ...]
    non_discardable_low_information_mask: tuple[tuple[bool, ...], ...]
    protected_mask: tuple[tuple[bool, ...], ...]
    edge_connected_blank_fraction: float
    non_discardable_low_information_fraction: float
    protected_retained_fraction: float
    mask_sha256: str


@dataclass(frozen=True)
class FramingTelemetry:
    """Auditable result for one deterministic reference crop candidate."""

    contract_version: str
    detector_version: str
    mask_sha256: str
    crop_box: tuple[int, int, int, int]
    base_zoom: float
    source_resolution_zoom_cap: float
    protected_region_zoom_cap: float
    edge_connected_blank_fraction: float
    non_discardable_low_information_fraction: float
    protected_retained_fraction: float
    balloon_mask_intersection_ratio: float
    subject_coverage: float
    face_coverage: float
    action_coverage: float
    effect_coverage: float
    continuity_context_coverage: float
    mask_confidence: float
    mask_source: str
    fallback_reason: str = ""
    rejection_code: str | None = None


def _source_cell_bounds(index: int, grid_size: int, source_size: int) -> tuple[int, int]:
    start = floor(index * source_size / grid_size)
    end = floor((index + 1) * source_size / grid_size)
    return start, max(start + 1, end)


def _source_cells(
    source_width: int,
    source_height: int,
    grid_width: int,
    grid_height: int,
) -> tuple[tuple[tuple[tuple[int, int], tuple[int, int]], ...], ...]:
    return tuple(
        tuple(
            (
                _source_cell_bounds(x, grid_width, source_width),
                _source_cell_bounds(y, grid_height, source_height),
            )
            for x in range(grid_width)
        )
        for y in range(grid_height)
    )


def _rounded_fraction(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _region_bounds(region: Any) -> tuple[float, float, float, float] | None:
    bbox = getattr(region, "normalized_bbox", None)
    if bbox is not None:
        return tuple(float(value) for value in bbox)  # type: ignore[return-value]
    polygon = tuple(getattr(region, "normalized_polygon", ()) or ())
    if not polygon:
        return None
    xs = [float(point[0]) for point in polygon]
    ys = [float(point[1]) for point in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def _region_geometry_boxes(region: Any) -> tuple[tuple[float, float, float, float], ...]:
    """Return every persisted geometry envelope, conservatively.

    Balloon evidence may carry both a bbox and a polygon.  They are both
    authoritative geometry, so a disagreement must not turn a visible
    balloon into an apparently empty crop by trusting only one representation.
    """
    boxes: list[tuple[float, float, float, float]] = []
    bbox = getattr(region, "normalized_bbox", None)
    if bbox is not None:
        boxes.append(tuple(float(value) for value in bbox))  # type: ignore[arg-type]
    polygon = tuple(getattr(region, "normalized_polygon", ()) or ())
    if polygon:
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
        polygon_box = (min(xs), min(ys), max(xs), max(ys))
        if polygon_box not in boxes:
            boxes.append(polygon_box)
    return tuple(boxes)


def _overlaps(
    cell: tuple[tuple[int, int], tuple[int, int]],
    region_box: tuple[float, float, float, float],
    source_width: int,
    source_height: int,
) -> bool:
    (x0, x1), (y0, y1) = cell
    rx0, ry0, rx1, ry1 = region_box
    return (
        x1 > rx0 * source_width
        and x0 < rx1 * source_width
        and y1 > ry0 * source_height
        and y0 < ry1 * source_height
    )


def rasterize_protected_regions(
    evidence: PanelVisualEvidence,
    cells: tuple[tuple[tuple[tuple[int, int], tuple[int, int]], ...], ...],
) -> tuple[tuple[bool, ...], ...]:
    """Rasterize typed protected geometry conservatively onto source cells."""
    source_width = max(1, max(cell[0][1] for row in cells for cell in row))
    source_height = max(1, max(cell[1][1] for row in cells for cell in row))
    regions = tuple(getattr(evidence, "protected_regions", ()) or ())
    boxes = tuple(
        box for region in regions if (box := _region_bounds(region)) is not None
    )
    if not boxes:
        return tuple(tuple(False for _cell in row) for row in cells)
    return tuple(
        tuple(
            any(_overlaps(cell, box, source_width, source_height) for box in boxes)
            for cell in row
        )
        for row in cells
    )


def _pixel(values: tuple[int, ...], width: int, height: int, x: int, y: int) -> int:
    return values[max(0, min(height - 1, y)) * width + max(0, min(width - 1, x))]


def _structure_metrics(values: tuple[int, ...], width: int, height: int, x: int, y: int) -> tuple[float, ...]:
    window = [
        _pixel(values, width, height, x + dx, y + dy)
        for dy in (-1, 0, 1)
        for dx in (-1, 0, 1)
    ]
    mean = sum(window) / len(window)
    variance = min(1.0, sum((value - mean) ** 2 for value in window) / (255.0**2 / 4.0))
    bins = [0] * 8
    for value in window:
        bins[min(7, value * 8 // 256)] += 1
    entropy = 0.0
    for count in bins:
        if count:
            probability = count / len(window)
            entropy -= probability * math.log2(probability)
    entropy = min(1.0, entropy / 3.0)
    neighbours = [
        abs(_pixel(values, width, height, x + dx, y + dy) - values[y * width + x])
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))
    ]
    edge_density = sum(delta >= 12 for delta in neighbours) / len(neighbours)
    two_scale = [
        abs(
            _pixel(values, width, height, x + dx * 4, y + dy * 4)
            - _pixel(values, width, height, x + dx, y + dy)
        )
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))
    ]
    texture_energy = min(1.0, sum(two_scale) / (len(two_scale) * 255.0))
    return variance, entropy, edge_density, texture_energy


def classify_low_information_cells(
    image: Image.Image,
    grid_width: int,
    grid_height: int,
) -> tuple[tuple[bool, ...], ...]:
    """Classify structural uniformity without brightness or color rules."""
    luminance = image.convert("L").resize((grid_width, grid_height), Image.Resampling.BILINEAR)
    raw_values = (
        luminance.get_flattened_data()
        if hasattr(luminance, "get_flattened_data")
        else luminance.getdata()
    )
    values = tuple(int(value) for value in raw_values)
    return tuple(
        tuple(
            sum(metric <= threshold for metric, threshold in zip(
                _structure_metrics(values, grid_width, grid_height, x, y),
                _LOW_INFORMATION_THRESHOLDS,
                strict=True,
            )) >= 3
            for x in range(grid_width)
        )
        for y in range(grid_height)
    )


def flood_border_cells(
    low_information: tuple[tuple[bool, ...], ...],
    protected: tuple[tuple[bool, ...], ...],
    *,
    connectivity: int = 8,
) -> tuple[tuple[bool, ...], ...]:
    """Return only low-information cells connected to any image border."""
    if connectivity != 8:
        raise ValueError("visual.mask_connectivity_invalid")
    height = len(low_information)
    width = len(low_information[0]) if height else 0
    marked = [[False] * width for _ in range(height)]
    queue: deque[tuple[int, int]] = deque()

    def enqueue(x: int, y: int) -> None:
        if 0 <= x < width and 0 <= y < height and low_information[y][x] and not protected[y][x] and not marked[y][x]:
            marked[y][x] = True
            queue.append((x, y))

    for x in range(width):
        enqueue(x, 0)
        enqueue(x, height - 1)
    for y in range(height):
        enqueue(0, y)
        enqueue(width - 1, y)
    while queue:
        x, y = queue.popleft()
        for dx, dy in _NEIGHBOUR_OFFSETS:
            enqueue(x + dx, y + dy)
    return tuple(tuple(row) for row in marked)


def _source_area_fraction(
    mask: tuple[tuple[bool, ...], ...],
    cells: tuple[tuple[tuple[tuple[int, int], tuple[int, int]], ...], ...],
    source_width: int,
    source_height: int,
) -> float:
    area = sum(
        (x1 - x0) * (y1 - y0)
        for mask_row, cell_row in zip(mask, cells, strict=True)
        for enabled, ((x0, x1), (y0, y1)) in zip(mask_row, cell_row, strict=True)
        if enabled
    )
    return _rounded_fraction(area, source_width * source_height)


def _protected_retained_fraction(
    protected: tuple[tuple[bool, ...], ...],
    edge_connected: tuple[tuple[bool, ...], ...],
    cells: tuple[tuple[tuple[tuple[int, int], tuple[int, int]], ...], ...],
    source_width: int,
    source_height: int,
) -> float:
    total = 0
    retained = 0
    for protected_row, edge_row, cell_row in zip(protected, edge_connected, cells, strict=True):
        for is_protected, is_edge, ((x0, x1), (y0, y1)) in zip(
            protected_row, edge_row, cell_row, strict=True
        ):
            if is_protected:
                cell_area = (x1 - x0) * (y1 - y0)
                total += cell_area
                if not is_edge:
                    retained += cell_area
    if not total:
        return 1.0
    return _rounded_fraction(retained, total)


def _mask_hash(
    source_width: int,
    source_height: int,
    grid_width: int,
    grid_height: int,
    edge_connected: tuple[tuple[bool, ...], ...],
    non_discardable: tuple[tuple[bool, ...], ...],
    protected: tuple[tuple[bool, ...], ...],
) -> str:
    payload = {
        "detector_version": DETECTOR_VERSION,
        "source_dimensions": [source_width, source_height],
        "grid_dimensions": [grid_width, grid_height],
        "edge_connected_mask": edge_connected,
        "non_discardable_low_information_mask": non_discardable,
        "protected_mask": protected,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _reference_evidence(
    evidence: PanelVisualEvidence | Any,
    *,
    allow_conservative_full_panel: bool = False,
) -> PanelVisualEvidence:
    if evidence is None:
        raise VisualEvidenceError(
            "visual.panel_lineage_unavailable",
            "reference framing requires persisted panel visual evidence",
        )
    try:
        if isinstance(evidence, PanelVisualEvidence):
            validate_panel_visual_evidence(evidence)
            return require_reference_ready_visual_evidence(
                evidence,
                allow_conservative_full_panel=allow_conservative_full_panel,
            )
        return require_reference_ready_visual_evidence(
            evidence,
            allow_conservative_full_panel=allow_conservative_full_panel,
        )
    except VisualEvidenceError as exc:
        if exc.code == "visual.balloon_mask_unknown":
            raise
        raise VisualEvidenceError(
            "visual.panel_lineage_unavailable",
            "panel visual evidence is not valid for reference framing",
        ) from exc
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise VisualEvidenceError(
            "visual.panel_lineage_unavailable",
            "panel visual evidence is not valid for reference framing",
        ) from exc


def _region_area(box: tuple[float, float, float, float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _intersection_area(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    return max(0.0, min(first[2], second[2]) - max(first[0], second[0])) * max(
        0.0, min(first[3], second[3]) - max(first[1], second[1])
    )


def _normalised_box(
    crop_box: tuple[int, int, int, int], source_size: tuple[int, int]
) -> tuple[float, float, float, float]:
    source_width, source_height = source_size
    left, top, right, bottom = crop_box
    return (
        left / source_width,
        top / source_height,
        right / source_width,
        bottom / source_height,
    )


def _protected_coverage(
    evidence: PanelVisualEvidence,
    crop_box: tuple[int, int, int, int],
    source_size: tuple[int, int],
) -> dict[str, float]:
    crop = _normalised_box(crop_box, source_size)
    coverage: dict[str, float] = {
        "subject": 1.0,
        "face": 1.0,
        "action": 1.0,
        "effect": 1.0,
        "continuity_context": 1.0,
    }
    for region in evidence.protected_regions:
        if region.kind not in coverage:
            continue
        region_box = _region_bounds(region)
        if region_box is None:
            coverage[region.kind] = 0.0
            continue
        area = _region_area(region_box)
        retained = _intersection_area(region_box, crop)
        coverage[region.kind] = min(coverage[region.kind], retained / area if area else 0.0)
    return coverage


_REQUIRED_PROTECTED_COVERAGE = {
    "subject": 0.98,
    "face": 0.98,
    "action": 0.95,
    "continuity_context": 0.95,
    "effect": 0.90,
}


def _required_crop_fraction(
    centre: float,
    start: float,
    end: float,
    required_fraction: float,
) -> float:
    span = max(0.0, end - start)
    if span <= 0.0:
        return 1.0
    retained_span = span * min(1.0, max(0.0, required_fraction))
    left_constraint = centre - (end - retained_span)
    right_constraint = (start + retained_span) - centre
    return min(1.0, max(0.0, 2.0 * max(left_constraint, right_constraint)))


def _protected_area_fraction(
    evidence: PanelVisualEvidence,
    crop_box: tuple[int, int, int, int],
    source_size: tuple[int, int],
) -> float:
    crop = _normalised_box(crop_box, source_size)
    total_area = 0.0
    retained_area = 0.0
    for region in evidence.protected_regions:
        region_box = _region_bounds(region)
        if region_box is None:
            continue
        area = _region_area(region_box)
        total_area += area
        retained_area += _intersection_area(region_box, crop)
    return round(retained_area / total_area, 6) if total_area else 1.0


def _protected_region_zoom_cap(
    evidence: PanelVisualEvidence,
    crop_box: tuple[int, int, int, int],
    source_size: tuple[int, int],
    target_size: tuple[int, int],
    source_resolution_cap: float,
) -> float:
    source_width, source_height = source_size
    base_width, base_height = _reference_base_dimensions(source_size, target_size)
    crop_left, crop_top, crop_right, crop_bottom = crop_box
    centre_x = ((crop_left + crop_right) / 2.0) / source_width
    centre_y = ((crop_top + crop_bottom) / 2.0) / source_height
    protected_cap = source_resolution_cap
    for region in evidence.protected_regions:
        region_box = _region_bounds(region)
        if region_box is None:
            continue
        kind = getattr(region, "kind", "")
        required = max(
            float(getattr(region, "minimum_coverage", 0.0)),
            _REQUIRED_PROTECTED_COVERAGE.get(kind, 0.0),
        )
        if required <= 0.0:
            continue
        x0, y0, x1, y1 = region_box
        width_fraction = _required_crop_fraction(centre_x, x0, x1, required)
        height_fraction = _required_crop_fraction(centre_y, y0, y1, required)
        if width_fraction > 0.0:
            protected_cap = min(
                protected_cap,
                base_width / (source_width * width_fraction),
            )
        if height_fraction > 0.0:
            protected_cap = min(
                protected_cap,
                base_height / (source_height * height_fraction),
            )
    return round(max(0.0, protected_cap), 6)


def _balloon_intersection_ratio(
    evidence: PanelVisualEvidence,
    crop_box: tuple[int, int, int, int],
    source_size: tuple[int, int],
) -> float:
    crop = _normalised_box(crop_box, source_size)
    crop_area = _region_area(crop)
    overlap = sum(
        _intersection_area(region_box, crop)
        for region in evidence.balloon_regions
        for region_box in _region_geometry_boxes(region)
    )
    return min(1.0, overlap / crop_area) if crop_area else 1.0


def _mask_crop_fraction(
    border_mask: BorderMaskResult,
    crop_box: tuple[int, int, int, int],
) -> float:
    left, top, right, bottom = crop_box
    crop_area = max(1, (right - left) * (bottom - top))
    total = 0
    for y, row in enumerate(border_mask.edge_connected_mask):
        cell_y0, cell_y1 = _source_cell_bounds(y, border_mask.grid_height, border_mask.source_height)
        for x, enabled in enumerate(row):
            if not enabled:
                continue
            cell_x0, cell_x1 = _source_cell_bounds(x, border_mask.grid_width, border_mask.source_width)
            total += max(0, min(right, cell_x1) - max(left, cell_x0)) * max(
                0, min(bottom, cell_y1) - max(top, cell_y0)
            )
    return _rounded_fraction(total, crop_area)


def _reference_base_dimensions(
    source_size: tuple[int, int], target_size: tuple[int, int]
) -> tuple[int, int]:
    source_width, source_height = source_size
    target_width, target_height = target_size
    target_ratio = target_width / target_height
    if source_width / source_height > target_ratio:
        return max(2, round(source_height * target_ratio)), source_height
    return source_width, max(2, round(source_width / target_ratio))


def candidate_is_feasible(
    crop_box: tuple[int, int, int, int],
    evidence: PanelVisualEvidence,
    border_mask: BorderMaskResult,
    source_size: tuple[int, int],
    target_size: tuple[int, int],
    *,
    allow_source_resolution_warning: bool = False,
    review_aggressive_crop: bool = False,
    blank_target_fraction: float | None = None,
    allow_conservative_full_panel: bool = False,
) -> tuple[bool, FramingTelemetry]:
    """Evaluate one static crop against the hard reference framing contract.

    The optional resolution warning is reserved for the explicit silent
    review upscale policy. It never relaxes lineage, balloon, protected
    region, or blank-space constraints.
    """
    parsed = _reference_evidence(
        evidence,
        allow_conservative_full_panel=allow_conservative_full_panel,
    )
    source_width, source_height = source_size
    target_width, target_height = target_size
    left, top, right, bottom = crop_box
    crop_width = right - left
    crop_height = bottom - top
    if (
        border_mask.source_width != source_width
        or border_mask.source_height != source_height
        or left < 0
        or top < 0
        or right > source_width
        or bottom > source_height
        or crop_width <= 0
        or crop_height <= 0
    ):
        raise VisualEvidenceError(
            "visual.panel_lineage_unavailable",
            "framing candidate does not match its panel source",
        )
    if is_conservative_full_panel_visual_evidence(parsed) and (
        not allow_conservative_full_panel
        or crop_box != (0, 0, source_width, source_height)
    ):
        telemetry = FramingTelemetry(
            contract_version=parsed.contract_version,
            detector_version=border_mask.detector_version,
            mask_sha256=border_mask.mask_sha256,
            crop_box=crop_box,
            base_zoom=1.0,
            source_resolution_zoom_cap=1.0,
            protected_region_zoom_cap=1.0,
            edge_connected_blank_fraction=_mask_crop_fraction(border_mask, crop_box),
            non_discardable_low_information_fraction=border_mask.non_discardable_low_information_fraction,
            protected_retained_fraction=1.0,
            balloon_mask_intersection_ratio=0.0,
            subject_coverage=1.0,
            face_coverage=1.0,
            action_coverage=1.0,
            effect_coverage=1.0,
            continuity_context_coverage=1.0,
            mask_confidence=parsed.mask_confidence,
            mask_source=parsed.evidence_source,
            rejection_code="visual.conservative_full_panel_requires_full_source",
        )
        return False, telemetry
    source_cap = min(
        source_width / max(1.0, target_width / 1.15),
        source_height / max(1.0, target_height / 1.15),
    )
    base_width, base_height = _reference_base_dimensions(source_size, target_size)
    base_zoom = max(base_width / crop_width, base_height / crop_height)
    coverage = _protected_coverage(parsed, crop_box, source_size)
    balloon_ratio = _balloon_intersection_ratio(parsed, crop_box, source_size)
    protected_area = _protected_area_fraction(parsed, crop_box, source_size)
    protected_cap = _protected_region_zoom_cap(
        parsed,
        crop_box,
        source_size,
        target_size,
        source_cap,
    )
    telemetry = FramingTelemetry(
        contract_version=parsed.contract_version,
        detector_version=border_mask.detector_version,
        mask_sha256=border_mask.mask_sha256,
        crop_box=crop_box,
        base_zoom=round(base_zoom, 6),
        source_resolution_zoom_cap=round(source_cap, 6),
        protected_region_zoom_cap=protected_cap,
        edge_connected_blank_fraction=_mask_crop_fraction(border_mask, crop_box),
        non_discardable_low_information_fraction=border_mask.non_discardable_low_information_fraction,
        protected_retained_fraction=protected_area,
        balloon_mask_intersection_ratio=round(balloon_ratio, 6),
        subject_coverage=round(coverage["subject"], 6),
        face_coverage=round(coverage["face"], 6),
        action_coverage=round(coverage["action"], 6),
        effect_coverage=round(coverage["effect"], 6),
        continuity_context_coverage=round(coverage["continuity_context"], 6),
        mask_confidence=round(parsed.mask_confidence, 6),
        mask_source=parsed.evidence_source,
    )
    if balloon_ratio > 0.0:
        return False, replace(telemetry, rejection_code="visual.balloon_mask_overlap")
    source_resolution_insufficient = (
        base_zoom > source_cap + 1e-9
        or crop_width < target_width / 1.15
        or crop_height < target_height / 1.15
    )
    if (
        source_resolution_insufficient
        and not allow_source_resolution_warning
        and not review_aggressive_crop
    ):
        return False, replace(telemetry, rejection_code="visual.source_resolution_insufficient")
    if source_resolution_insufficient:
        telemetry = replace(telemetry, fallback_reason="review.low_source_resolution")
    if blank_target_fraction is not None:
        if not math.isfinite(float(blank_target_fraction)) or not 0.0 <= float(
            blank_target_fraction
        ) <= 1.0:
            raise VisualEvidenceError(
                "visual.framing_contract_incompatible",
                "blank-space target is outside the profile contract",
            )
        if telemetry.edge_connected_blank_fraction > float(blank_target_fraction) + 1e-9:
            return False, replace(
                telemetry,
                fallback_reason="visual.blank_infeasible",
                rejection_code="visual.blank_infeasible",
            )
    coverage_minima = (
        ("subject", 0.98),
        ("face", 0.98),
        ("action", 0.95),
        ("continuity_context", 0.95),
        ("effect", 0.90),
    )
    if review_aggressive_crop:
        # Silent review may crop to the dominant subject when the source panel
        # is a full webtoon page whose whitespace gutters would otherwise push
        # every 9:16 ROI past the edge-blank target. Protected coverage is
        # relaxed, never removed: faces keep the strictest floor.
        coverage_minima = (
            ("subject", 0.40),
            ("face", 0.60),
            ("action", 0.45),
            ("continuity_context", 0.35),
            ("effect", 0.40),
        )
    for kind, minimum in coverage_minima:
        if coverage[kind] < minimum:
            return False, replace(telemetry, rejection_code=f"visual.protected_{kind}_coverage")
    required_protected_regions = any(
        max(
            float(getattr(region, "minimum_coverage", 0.0)),
            _REQUIRED_PROTECTED_COVERAGE.get(getattr(region, "kind", ""), 0.0),
        )
        > 0.0
        for region in parsed.protected_regions
    )
    if base_zoom > protected_cap + 1e-9 and (
        not allow_source_resolution_warning or required_protected_regions
    ) and not review_aggressive_crop:
        return False, replace(telemetry, rejection_code="visual.protected_zoom_insufficient")
    return True, telemetry


def build_color_agnostic_border_mask(
    image: Image.Image,
    evidence: PanelVisualEvidence | Any,
    *,
    grid_long_edge: int = 256,
    allow_conservative_full_panel: bool = False,
) -> BorderMaskResult:
    """Build deterministic source-area masks for reference framing telemetry."""
    evidence = _reference_evidence(
        evidence,
        allow_conservative_full_panel=allow_conservative_full_panel,
    )
    if image.width <= 0 or image.height <= 0 or grid_long_edge <= 0:
        raise ValueError("visual.mask_dimensions_invalid")
    scale = grid_long_edge / max(image.width, image.height)
    grid_width = min(image.width, max(1, round(image.width * scale)))
    grid_height = min(image.height, max(1, round(image.height * scale)))
    cells = _source_cells(image.width, image.height, grid_width, grid_height)
    protected = rasterize_protected_regions(evidence, cells)
    low_information = classify_low_information_cells(image, grid_width, grid_height)
    edge_connected = flood_border_cells(low_information, protected, connectivity=8)
    non_discardable = tuple(
        tuple(low and not edge for low, edge in zip(low_row, edge_row, strict=True))
        for low_row, edge_row in zip(low_information, edge_connected, strict=True)
    )
    return BorderMaskResult(
        detector_version=DETECTOR_VERSION,
        source_width=image.width,
        source_height=image.height,
        grid_width=grid_width,
        grid_height=grid_height,
        edge_connected_mask=edge_connected,
        non_discardable_low_information_mask=non_discardable,
        protected_mask=protected,
        edge_connected_blank_fraction=_source_area_fraction(
            edge_connected, cells, image.width, image.height
        ),
        non_discardable_low_information_fraction=_source_area_fraction(
            non_discardable, cells, image.width, image.height
        ),
        protected_retained_fraction=_protected_retained_fraction(
            protected, edge_connected, cells, image.width, image.height
        ),
        mask_sha256=_mask_hash(
            image.width,
            image.height,
            grid_width,
            grid_height,
            edge_connected,
            non_discardable,
            protected,
        ),
    )


def canonical_protected_geometry(evidence: PanelVisualEvidence) -> tuple[str, ...]:
    """Return serializer-backed protected geometry for cache identity."""
    serialized = panel_visual_evidence_json(evidence)["protected_regions"]
    return tuple(
        json.dumps(region, sort_keys=True, separators=(",", ":"))
        for region in serialized
    )
