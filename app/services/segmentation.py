"""Deterministic, complete source-space mapping for vision-first analysis.

This module deliberately stops at source-space coverage. It does not sample,
call a provider, perform OCR, or infer story content.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from PIL import Image, UnidentifiedImageError

SEGMENTATION_VERSION = "vision-coverage-v1"
RegionClass = Literal["canonical_panel", "verified_gutter", "unresolved_material"]
Bounds = tuple[int, int, int, int]


@dataclass(frozen=True)
class SourceAssetInput:
    """Original source bytes plus immutable source-space lineage."""

    source_asset_id: str
    original_checksum: str
    original_width: int
    original_height: int
    source_bounds: Bounds
    strip_order: int
    region_order: int
    payload: bytes


@dataclass(frozen=True)
class CoverageTile:
    """One deterministic, overlapping source-space decode tile."""

    source_asset_id: str
    tile_index: int
    y0: int
    y1: int
    overlap_above: int
    overlap_below: int
    tile_sha256: str


@dataclass(frozen=True)
class CoverageRegion:
    """A non-overlapping source-space region with an auditable classification."""

    region_id: str
    source_asset_id: str
    source_order: int
    bounds: Bounds
    region_class: RegionClass
    area: int
    confidence: float
    evidence: str


@dataclass(frozen=True)
class CoverageMap:
    """Complete source-space coverage and deterministic reconciliation metadata."""

    version: str
    map_sha256: str
    source_asset_ids: tuple[str, ...]
    tiles: tuple[CoverageTile, ...]
    regions: tuple[CoverageRegion, ...]
    source_content_coverage_ratio: float
    canonical_panel_area: int
    verified_gutter_area: int
    unresolved_material_area: int
    panel_count: int
    reconciliation_errors: tuple[str, ...]


@dataclass(frozen=True)
class _Candidate:
    source_asset_id: str
    asset_key: tuple[int, int, int, int, str]
    lineage_key: tuple[str, int, int]
    bounds: Bounds
    region_class: RegionClass
    confidence: float
    evidence: str


def _compact_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_compact_json(value)).hexdigest()


def _rect(bounds: Any) -> Bounds:
    if not isinstance(bounds, (tuple, list)) or len(bounds) != 4:
        raise ValueError("bounds must contain x0, y0, x1, y1")
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in bounds):
        raise ValueError("bounds coordinates must be integers")
    result = tuple(bounds)
    x0, y0, x1, y1 = result
    if x1 <= x0 or y1 <= y0:
        raise ValueError("bounds must have positive width and height")
    return result


def _safe_rect(asset: SourceAssetInput) -> Bounds:
    try:
        return _rect(asset.source_bounds)
    except ValueError:
        width = max(int(asset.original_width), 1)
        height = max(int(asset.original_height), 1)
        return (0, 0, width, height)


def _rect_area(bounds: Bounds) -> int:
    return (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])


def _rect_intersection(left: Bounds, right: Bounds) -> Bounds | None:
    result = (
        max(left[0], right[0]),
        max(left[1], right[1]),
        min(left[2], right[2]),
        min(left[3], right[3]),
    )
    return result if result[2] > result[0] and result[3] > result[1] else None


def _union_area(rectangles: Sequence[Bounds]) -> int:
    valid = [rectangle for rectangle in rectangles if rectangle[2] > rectangle[0]]
    if not valid:
        return 0
    x_edges = sorted({edge for rectangle in valid for edge in (rectangle[0], rectangle[2])})
    y_edges = sorted({edge for rectangle in valid for edge in (rectangle[1], rectangle[3])})
    area = 0
    for x0, x1 in zip(x_edges, x_edges[1:], strict=False):
        for y0, y1 in zip(y_edges, y_edges[1:], strict=False):
            cell = (x0, y0, x1, y1)
            if any(_rect_intersection(cell, rectangle) for rectangle in valid):
                area += (x1 - x0) * (y1 - y0)
    return area


def plan_overlapping_tiles(
    source_asset_id: str,
    width: int,
    height: int,
    *,
    tile_height: int = 2048,
    overlap: int = 128,
) -> tuple[CoverageTile, ...]:
    """Plan ordered full-width tiles without dropping seams or image ends."""

    if not source_asset_id:
        raise ValueError("source_asset_id must be non-empty")
    for name, value in (
        ("width", width),
        ("height", height),
        ("tile_height", tile_height),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{name} must be positive")
    if (
        not isinstance(overlap, int)
        or isinstance(overlap, bool)
        or overlap < 0
        or overlap >= tile_height
    ):
        raise ValueError("overlap must satisfy 0 <= overlap < tile_height")

    tiles: list[CoverageTile] = []
    start = 0
    index = 0
    while start < height:
        end = min(start + tile_height, height)
        overlap_above = 0 if start == 0 else overlap
        overlap_below = 0 if end == height else overlap
        metadata = {
            "height": height,
            "overlap_above": overlap_above,
            "overlap_below": overlap_below,
            "source_asset_id": source_asset_id,
            "tile_index": index,
            "tile_height": tile_height,
            "width": width,
            "y0": start,
            "y1": end,
        }
        tiles.append(
            CoverageTile(
                source_asset_id=source_asset_id,
                tile_index=index,
                y0=start,
                y1=end,
                overlap_above=overlap_above,
                overlap_below=overlap_below,
                tile_sha256=_sha256(metadata),
            )
        )
        if end == height:
            break
        next_start = end - overlap
        if next_start <= start:
            raise RuntimeError("tile planner did not advance")
        start = next_start
        index += 1
    return tuple(tiles)


def _asset_key(asset: SourceAssetInput) -> tuple[int, int, int, int, str]:
    bounds = _safe_rect(asset)
    return (asset.strip_order, asset.region_order, bounds[1], bounds[0], asset.source_asset_id)


def _lineage_key(asset: SourceAssetInput) -> tuple[str, int, int]:
    return (
        asset.original_checksum or asset.source_asset_id,
        asset.original_width,
        asset.original_height,
    )


def _invalid_candidate(
    asset: SourceAssetInput,
    reason: str,
) -> tuple[list[_Candidate], str]:
    bounds = _safe_rect(asset)
    candidate = _Candidate(
        source_asset_id=asset.source_asset_id,
        asset_key=_asset_key(asset),
        lineage_key=_lineage_key(asset),
        bounds=bounds,
        region_class="unresolved_material",
        confidence=0.0,
        evidence=f"coverage.unresolved_material:{reason}",
    )
    return [candidate], f"coverage.unresolved_material:{asset.source_asset_id}:{reason}"


def _row_classification(image: Image.Image) -> list[tuple[RegionClass, float, str]]:
    width, height = image.size
    pixels = image.load()
    rows: list[tuple[RegionClass, float, str]] = []
    for y in range(height):
        brightness: list[float] = []
        for x in range(width):
            red, green, blue = pixels[x, y]
            brightness.append((red + green + blue) / 3.0)
        mean = sum(brightness) / width
        variance = sum((value - mean) ** 2 for value in brightness) / width
        is_extreme = (mean >= 236.0 or mean <= 22.0) and variance <= 25.0
        if is_extreme:
            kind = "near_white" if mean >= 236.0 else "near_black"
            distance = (mean - 236.0) if mean >= 236.0 else (22.0 - mean)
            confidence = min(1.0, 0.7 + max(distance, 0.0) / 255.0)
            evidence = (
                f"coverage.gutter.extreme_flat:{kind};"
                f"mean={mean:.3f};variance={variance:.3f}"
            )
            rows.append(("verified_gutter", confidence, evidence))
        else:
            evidence = (
                "coverage.content.full_width_decoded;"
                f"mean={mean:.3f};variance={variance:.3f}"
            )
            rows.append(("canonical_panel", 0.9, evidence))
    return rows


def _decode_candidates(
    asset: SourceAssetInput,
) -> tuple[list[_Candidate], tuple[str, ...]]:
    try:
        bounds = _rect(asset.source_bounds)
    except ValueError:
        return _invalid_candidate(asset, "bounds_invalid")

    if (
        asset.original_width != bounds[2] - bounds[0]
        or asset.original_height != bounds[3] - bounds[1]
        or asset.original_width <= 0
        or asset.original_height <= 0
    ):
        return _invalid_candidate(asset, "dimension_bounds_mismatch")

    try:
        with Image.open(io.BytesIO(asset.payload)) as decoded:
            decoded.load()
            if decoded.size != (asset.original_width, asset.original_height):
                return _invalid_candidate(asset, "decoded_dimensions_mismatch")
            image = decoded.convert("RGB")
    except (OSError, UnidentifiedImageError, ValueError):
        return _invalid_candidate(asset, "decode_failed")

    rows = _row_classification(image)
    candidates: list[_Candidate] = []
    start = 0
    current_class, current_confidence, current_evidence = rows[0]
    for index in range(1, len(rows) + 1):
        if index < len(rows) and rows[index][0] == current_class:
            continue
        end = index
        x0, y0, x1, _ = bounds
        candidates.append(
            _Candidate(
                source_asset_id=asset.source_asset_id,
                asset_key=_asset_key(asset),
                lineage_key=_lineage_key(asset),
                bounds=(x0, y0 + start, x1, y0 + end),
                region_class=current_class,
                confidence=current_confidence,
                evidence=(
                    f"{current_evidence};rows={y0 + start}-{y0 + end}"
                ),
            )
        )
        if index == len(rows):
            break
        start = index
        current_class, current_confidence, current_evidence = rows[index]
    return candidates, ()


def _merge_cells(cells: Sequence[tuple[Bounds, _Candidate]]) -> list[tuple[Bounds, _Candidate]]:
    horizontal: list[tuple[Bounds, _Candidate]] = []
    grouped: dict[tuple[Any, ...], list[Bounds]] = {}
    owners: dict[tuple[Any, ...], _Candidate] = {}
    for bounds, owner in cells:
        key = (
           bounds[1],
           bounds[3],
           owner.source_asset_id,
            owner.lineage_key,
           owner.region_class,
            owner.confidence,
            owner.evidence,
        )
        grouped.setdefault(key, []).append(bounds)
        owners[key] = owner
    for key, bounds_list in grouped.items():
        bounds_list.sort()
        current = bounds_list[0]
        for candidate in bounds_list[1:]:
            if candidate[0] == current[2] and candidate[1:3] == current[1:3]:
                current = (current[0], current[1], candidate[2], current[3])
            else:
                horizontal.append((current, owners[key]))
                current = candidate
        horizontal.append((current, owners[key]))

    vertical: list[tuple[Bounds, _Candidate]] = []
    grouped_vertical: dict[tuple[Any, ...], list[Bounds]] = {}
    owners_vertical: dict[tuple[Any, ...], _Candidate] = {}
    for bounds, owner in horizontal:
        key = (
           bounds[0],
           bounds[2],
           owner.source_asset_id,
            owner.lineage_key,
           owner.region_class,
            owner.confidence,
            owner.evidence,
        )
        grouped_vertical.setdefault(key, []).append(bounds)
        owners_vertical[key] = owner
    for key, bounds_list in grouped_vertical.items():
        bounds_list.sort(key=lambda value: (value[1], value[0]))
        current = bounds_list[0]
        for candidate in bounds_list[1:]:
            if candidate[1] == current[3] and candidate[0] == current[0] and candidate[2] == current[2]:
                current = (current[0], current[1], current[2], candidate[3])
            else:
                vertical.append((current, owners_vertical[key]))
                current = candidate
        vertical.append((current, owners_vertical[key]))
    return vertical


def _partition_candidate_group(
    candidates: Sequence[_Candidate],
) -> tuple[list[tuple[Bounds, _Candidate]], int]:
    if not candidates:
        return [], 0
    x_edges = sorted({edge for candidate in candidates for edge in (candidate.bounds[0], candidate.bounds[2])})
    y_edges = sorted({edge for candidate in candidates for edge in (candidate.bounds[1], candidate.bounds[3])})
    cells: list[tuple[Bounds, _Candidate]] = []
    for x0, x1 in zip(x_edges, x_edges[1:], strict=False):
        for y0, y1 in zip(y_edges, y_edges[1:], strict=False):
            cell = (x0, y0, x1, y1)
            covering = [
                candidate
                for candidate in candidates
                if _rect_intersection(cell, candidate.bounds) == cell
            ]
            if not covering:
                continue
            covering.sort(key=lambda candidate: candidate.asset_key)
            classes = {candidate.region_class for candidate in covering}
            if len(classes) > 1:
                owner = covering[0]
                owner = _Candidate(
                    source_asset_id=owner.source_asset_id,
                    asset_key=owner.asset_key,
                    lineage_key=owner.lineage_key,
                    bounds=cell,
                    region_class="unresolved_material",
                    confidence=0.0,
                    evidence=(
                        "coverage.overlap_class_conflict:"
                        + ",".join(
                            f"{candidate.source_asset_id}={candidate.region_class}"
                            for candidate in covering
                        )
                    ),
                )
            else:
                owner = covering[0]
            cells.append((cell, owner))
    merged = _merge_cells(cells)
    return merged, sum(_rect_area(bounds) for bounds, _ in merged)


def _partition_candidates(
    candidates: Sequence[_Candidate],
) -> tuple[list[tuple[Bounds, _Candidate]], int]:
    grouped: dict[tuple[str, int, int], list[_Candidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.lineage_key, []).append(candidate)
    partition: list[tuple[Bounds, _Candidate]] = []
    for lineage_key in sorted(grouped):
        group_partition, _ = _partition_candidate_group(grouped[lineage_key])
        partition.extend(group_partition)
    return partition, sum(_rect_area(bounds) for bounds, _ in partition)


def _region_sort_key(item: tuple[Bounds, _Candidate]) -> tuple[Any, ...]:
    bounds, owner = item
    return (
        owner.asset_key,
        bounds[1],
        bounds[0],
        bounds[3],
        bounds[2],
        owner.region_class,
        owner.source_asset_id,
    )


def build_complete_coverage_map(
    assets: Sequence[SourceAssetInput],
    *,
    segmentation_version: str,
    ) -> CoverageMap:
    """Decode every source asset and return a complete, hashed partition."""

    if not segmentation_version:
        raise ValueError("segmentation_version must be non-empty")
    ordered_assets = tuple(sorted(assets, key=_asset_key))
    source_asset_ids = tuple(asset.source_asset_id for asset in ordered_assets)
    candidates: list[_Candidate] = []
    errors: list[str] = []
    duplicate_ids = {
        source_asset_id
        for source_asset_id in source_asset_ids
        if source_asset_ids.count(source_asset_id) > 1
    }
    errors.extend(
        f"coverage.duplicate_source_asset:{source_asset_id}"
        for source_asset_id in sorted(duplicate_ids)
    )

    tiles: list[CoverageTile] = []
    source_bounds_by_lineage: dict[tuple[str, int, int], list[Bounds]] = {}
    asset_metadata: list[dict[str, Any]] = []
    for asset in ordered_assets:
        bounds = _safe_rect(asset)
        source_bounds_by_lineage.setdefault(_lineage_key(asset), []).append(bounds)
        asset_metadata.append(
            {
                "original_checksum": asset.original_checksum,
                "original_height": asset.original_height,
                "original_width": asset.original_width,
                "region_order": asset.region_order,
                "source_asset_id": asset.source_asset_id,
                "source_bounds": list(bounds),
                "strip_order": asset.strip_order,
            }
        )
        try:
            tiles.extend(
                plan_overlapping_tiles(
                    asset.source_asset_id,
                    asset.original_width,
                    asset.original_height,
                )
            )
        except ValueError as exc:
            invalid, error = _invalid_candidate(asset, f"tile_plan_invalid:{exc}")
            candidates.extend(invalid)
            errors.append(error)
            continue
        decoded, decode_errors = _decode_candidates(asset)
        candidates.extend(decoded)
        errors.extend(decode_errors)

    partition, partition_area = _partition_candidates(candidates)
    total_area = sum(
        _union_area(bounds)
        for bounds in source_bounds_by_lineage.values()
    )
    if partition_area != total_area:
        errors.append(
            f"coverage.partition_gap:accounted={partition_area}:expected={total_area}"
        )

    region_items = sorted(partition, key=_region_sort_key)
    regions: list[CoverageRegion] = []
    for source_order, (bounds, owner) in enumerate(region_items):
        region_id = "region-" + _sha256(
            {
                "bounds": list(bounds),
                "evidence": owner.evidence,
                "region_class": owner.region_class,
                "source_asset_id": owner.source_asset_id,
                "source_order": source_order,
            }
        )[:20]
        regions.append(
            CoverageRegion(
                region_id=region_id,
                source_asset_id=owner.source_asset_id,
                source_order=source_order,
                bounds=bounds,
                region_class=owner.region_class,
                area=_rect_area(bounds),
                confidence=owner.confidence,
                evidence=owner.evidence,
            )
        )

    canonical_area = sum(region.area for region in regions if region.region_class == "canonical_panel")
    gutter_area = sum(region.area for region in regions if region.region_class == "verified_gutter")
    unresolved_area = sum(
        region.area for region in regions if region.region_class == "unresolved_material"
    )
    if unresolved_area:
        errors.append(f"coverage.unresolved_material:area={unresolved_area}")
    if any(region.evidence.startswith("coverage.overlap_class_conflict") for region in regions):
        errors.append("coverage.overlap_class_conflict")
    accounted_area = canonical_area + gutter_area
    ratio = 0.0 if total_area == 0 else accounted_area / total_area
    if accounted_area == total_area:
        ratio = 1.0
    reconciliation_errors = tuple(sorted(set(errors)))
    tiles_tuple = tuple(tiles)
    regions_tuple = tuple(regions)
    hash_payload = {
        "regions": [
            {
                "area": region.area,
                "bounds": list(region.bounds),
                "confidence": region.confidence,
                "evidence": region.evidence,
                "region_class": region.region_class,
                "region_id": region.region_id,
                "source_asset_id": region.source_asset_id,
                "source_order": region.source_order,
            }
            for region in regions_tuple
        ],
        "segmentation_version": segmentation_version,
        "source_asset_ids": list(source_asset_ids),
        "source_assets": asset_metadata,
        "tiles": [
            {
                "overlap_above": tile.overlap_above,
                "overlap_below": tile.overlap_below,
                "source_asset_id": tile.source_asset_id,
                "tile_index": tile.tile_index,
                "tile_sha256": tile.tile_sha256,
                "y0": tile.y0,
                "y1": tile.y1,
            }
            for tile in tiles_tuple
        ],
    }
    map_sha256 = _sha256(hash_payload)
    return CoverageMap(
        version=segmentation_version,
        map_sha256=map_sha256,
        source_asset_ids=source_asset_ids,
        tiles=tiles_tuple,
        regions=regions_tuple,
        source_content_coverage_ratio=ratio,
        canonical_panel_area=canonical_area,
        verified_gutter_area=gutter_area,
        unresolved_material_area=unresolved_area,
        panel_count=sum(
            region.region_class == "canonical_panel" for region in regions_tuple
        ),
        reconciliation_errors=reconciliation_errors,
    )


def _overview_band(value: Any) -> tuple[Bounds | None, str | None]:
    if isinstance(value, Mapping):
        raw_bounds = value.get("bounds")
        region_class = value.get("region_class", value.get("class"))
    else:
        raw_bounds = value
        region_class = None
    try:
        bounds = _rect(raw_bounds)
    except (TypeError, ValueError):
        return None, None
    return bounds, str(region_class) if region_class is not None else None


def verify_segmentation_completeness(
    full_strip_overviews: Mapping[str, Any],
    coverage_map: CoverageMap,
) -> tuple[str, ...]:
    """Compare every full overview with the complete segmented region mosaic."""

    errors: list[str] = []
    expected_ids = set(coverage_map.source_asset_ids)
    overview_ids = set(full_strip_overviews)
    for source_asset_id in sorted(expected_ids - overview_ids):
        errors.append(f"coverage.source_asset_missing:{source_asset_id}")
    for source_asset_id in sorted(overview_ids - expected_ids):
        errors.append(f"coverage.source_asset_extra:{source_asset_id}")

    for source_asset_id in sorted(expected_ids & overview_ids):
        overview = full_strip_overviews[source_asset_id]
        if not isinstance(overview, Mapping):
            errors.append(f"coverage.unexplained_band:{source_asset_id}:overview_invalid")
            continue
        try:
            overview_bounds = _rect(overview.get("bounds"))
        except (TypeError, ValueError):
            errors.append(f"coverage.unexplained_band:{source_asset_id}:bounds_invalid")
            continue
        regions = [
            region
            for region in coverage_map.regions
            if region.source_asset_id == source_asset_id
        ]
        if not regions:
            errors.append(f"coverage.source_asset_missing:{source_asset_id}")
            continue
        map_bounds = (
            min(region.bounds[0] for region in regions),
            min(region.bounds[1] for region in regions),
            max(region.bounds[2] for region in regions),
            max(region.bounds[3] for region in regions),
        )
        if map_bounds[1] != overview_bounds[1]:
            errors.append(f"coverage.top_truncation:{source_asset_id}")
        if map_bounds[3] != overview_bounds[3]:
            errors.append(f"coverage.bottom_truncation:{source_asset_id}")

        raw_bands = overview.get("bands", ())
        if not isinstance(raw_bands, (tuple, list)):
            raw_bands = ()
        bands: list[tuple[Bounds, str | None]] = []
        for raw_band in raw_bands:
            bounds, region_class = _overview_band(raw_band)
            if bounds is None:
                errors.append(f"coverage.unexplained_band:{source_asset_id}:invalid")
                continue
            bands.append((bounds, region_class))
        overview_area = _rect_area(overview_bounds)
        band_area = _union_area([bounds for bounds, _ in bands])
        if band_area < overview_area:
            errors.append(f"coverage.seam_gap:{source_asset_id}")
        if band_area > overview_area:
            errors.append(f"coverage.unexplained_band:{source_asset_id}:outside")

        for left_index, (left_bounds, left_class) in enumerate(bands):
            for right_bounds, right_class in bands[left_index + 1 :]:
                if (
                    _rect_intersection(left_bounds, right_bounds) is not None
                    and left_class != right_class
                ):
                    errors.append(f"coverage.overlap_class_conflict:{source_asset_id}")
        for band_bounds, band_class in bands:
            if not any(
                region.bounds == band_bounds
                and region.region_class == band_class
                for region in regions
            ):
                errors.append(f"coverage.unexplained_band:{source_asset_id}:{band_bounds}")
        for region in regions:
            if not any(
                region.bounds == band_bounds and region.region_class == band_class
                for band_bounds, band_class in bands
            ):
                errors.append(f"coverage.unexplained_band:{source_asset_id}:{region.bounds}")

    return tuple(sorted(set(errors)))


def reconcile_coverage_chain(
    coverage_map: CoverageMap,
    panel_regions: Sequence[Mapping[str, Any]],
    observations: Sequence[Mapping[str, Any]],
    chunks: Sequence[Mapping[str, Any]],
    claims: Sequence[Mapping[str, Any]],
) -> tuple[bool, tuple[str, ...]]:
    """Validate the deterministic region -> observation -> chunk -> claim chain."""

    errors: list[str] = []
    source_ids = set(coverage_map.source_asset_ids)
    panel_by_id: dict[str, Mapping[str, Any]] = {}
    source_orders: list[int] = []
    expected_panel_keys = {
        (region.source_asset_id, region.bounds)
        for region in coverage_map.regions
        if region.region_class == "canonical_panel"
    }
    actual_panel_keys: set[tuple[str, Bounds]] = set()

    for panel in panel_regions:
        panel_id = panel.get("panel_id")
        source_asset_id = panel.get("source_asset_id")
        source_order = panel.get("source_order")
        if not panel_id:
            errors.append("chain.panel_missing:id")
            continue
        if panel_id in panel_by_id:
            errors.append(f"chain.duplicate_panel:{panel_id}")
        panel_by_id[str(panel_id)] = panel
        if not isinstance(source_order, int):
            errors.append(f"chain.panel_missing:source_order:{panel_id}")
        else:
            if source_order in source_orders:
                errors.append(f"chain.duplicate_source_order:{source_order}")
            source_orders.append(source_order)
        if source_asset_id not in source_ids:
            errors.append(f"chain.source_asset_missing:{source_asset_id}")
        bounds_value = panel.get("bounds")
        try:
            panel_bounds = _rect(bounds_value)
        except (TypeError, ValueError):
            errors.append(f"chain.panel_missing:bounds:{panel_id}")
        else:
            if source_asset_id in source_ids:
                actual_panel_keys.add((str(source_asset_id), panel_bounds))

    if source_orders != sorted(source_orders):
        errors.append("chain.out_of_order_source_order")
    for missing_source_asset_id, missing_bounds in sorted(expected_panel_keys - actual_panel_keys):
        errors.append(
            "chain.panel_missing:"
            f"{missing_source_asset_id}:{missing_bounds[0]},{missing_bounds[1]},"
            f"{missing_bounds[2]},{missing_bounds[3]}"
        )

    observation_ids: set[str] = set()
    for observation in observations:
        observation_id = observation.get("observation_id")
        panel_id = observation.get("panel_id")
        if not observation_id or not panel_id:
            errors.append(f"chain.orphan_observation:{observation_id}")
            if observation_id and panel_id not in panel_by_id:
                errors.append(f"chain.observation_panel_missing:{observation_id}:{panel_id}")
            continue
        if observation_id in observation_ids:
            errors.append(f"chain.duplicate_observation:{observation_id}")
        observation_ids.add(str(observation_id))
        if panel_id not in panel_by_id:
            errors.append(f"chain.orphan_observation:{observation_id}")
            errors.append(f"chain.observation_panel_missing:{observation_id}:{panel_id}")

    chunk_ids: set[str] = set()
    for chunk in chunks:
        chunk_id = chunk.get("chunk_id")
        if not chunk_id:
            errors.append("chain.chunk_missing:id")
            continue
        if chunk_id in chunk_ids:
            errors.append(f"chain.duplicate_chunk:{chunk_id}")
        chunk_ids.add(str(chunk_id))
        observation_refs = chunk.get("observation_ids")
        if not isinstance(observation_refs, (tuple, list)):
            errors.append(f"chain.chunk_observation_missing:{chunk_id}")
            continue
        for observation_id in observation_refs:
            if observation_id not in observation_ids:
                errors.append(f"chain.chunk_observation_missing:{chunk_id}:{observation_id}")

    for claim in claims:
        claim_id = claim.get("claim_id")
        if not claim_id:
            errors.append("chain.claim_missing:id")
            continue
        panel_refs = claim.get("evidence_panel_ids")
        if not isinstance(panel_refs, (tuple, list)) or not panel_refs:
            errors.append(f"chain.claim_panel_evidence_missing:{claim_id}")
            continue
        for panel_id in panel_refs:
            if panel_id not in panel_by_id:
                errors.append(f"chain.claim_panel_missing:{claim_id}:{panel_id}")

    if coverage_map.source_content_coverage_ratio != 1.0:
        errors.append("coverage.incomplete_ratio")
    if coverage_map.unresolved_material_area != 0:
        errors.append("coverage.unresolved_material")
    errors.extend(coverage_map.reconciliation_errors)
    ordered_errors = tuple(sorted(set(errors)))
    return not ordered_errors, ordered_errors


__all__ = [
    "CoverageMap",
    "CoverageRegion",
    "CoverageTile",
    "SourceAssetInput",
    "build_complete_coverage_map",
    "plan_overlapping_tiles",
    "reconcile_coverage_chain",
    "verify_segmentation_completeness",
]
