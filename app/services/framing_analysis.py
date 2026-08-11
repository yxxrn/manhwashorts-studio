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
from dataclasses import dataclass
from math import floor
from typing import Any

from PIL import Image

from app.services.visual_scoring import PanelVisualEvidence, panel_visual_evidence_json

DETECTOR_VERSION = "COLOR_AGNOSTIC_BALLOON_FREE_V1:grid256:structure4"
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


def build_color_agnostic_border_mask(
    image: Image.Image,
    evidence: PanelVisualEvidence,
    *,
    grid_long_edge: int = 256,
) -> BorderMaskResult:
    """Build deterministic source-area masks for reference framing telemetry."""
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
