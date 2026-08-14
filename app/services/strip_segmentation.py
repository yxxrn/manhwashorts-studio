"""Fail-closed reconciliation for long vertical source strips.

The legacy :mod:`app.services.strips` API still returns deterministic slices for
callers that need its historical behaviour. This module is the mass-production
boundary: it accepts only complete source-space partitions, permits only
high-confidence structural gutters without a provider, and turns ambiguous
boundaries into an auditable review state.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from math import isfinite
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from app.services import strips

SEGMENTATION_VERSION = "color-agnostic-strip-reconciliation-v1"
BOUNDARY_PROMPT_VERSION = "strip-boundary-assessment-v1"
MAX_ANALYSIS_PIXELS = 8_000_000
_PROTECTED_KINDS = {"balloon", "face", "subject", "action", "effect", "continuity"}
_HASH_FIELDS = {"analysis_hash", "boundary_hash", "evidence_hash", "mask_sha256"}
Bounds = tuple[int, int, int, int]


class StripSegmentationError(RuntimeError):
    """Safe, stable error at the source-segmentation boundary."""

    def __init__(self, code: str, message: str = "strip segmentation failed", *, reviewable: bool = True):
        self.code = code
        self.reviewable = reviewable
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class BoundaryCandidate:
    position: int
    confidence: float
    score: float
    run_top: int
    run_bottom: int
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BoundaryRequest:
    """Provider input in source coordinates, including overlapping image tiles."""

    source_asset_id: str
    source_checksum: str
    width: int
    height: int
    candidates: tuple[BoundaryCandidate, ...]
    tiles: tuple[dict[str, Any], ...]
    detector_version: str = strips.COLOR_AGNOSTIC_DETECTOR_VERSION

    def as_payload(self) -> dict[str, Any]:
        return {
            "contract_version": BOUNDARY_PROMPT_VERSION,
            "source_asset_id": self.source_asset_id,
            "source_checksum": self.source_checksum,
            "source_dimensions": [self.width, self.height],
            "detector_version": self.detector_version,
            "candidate_boundaries": [candidate.as_dict() for candidate in self.candidates],
            "overlapping_source_tiles": [dict(tile) for tile in self.tiles],
            "random_sampling": False,
        }


@dataclass(frozen=True)
class StripSegmentationResult:
    source_asset_id: str
    source_checksum: str
    width: int
    height: int
    spans: tuple[Bounds, ...]
    candidates: tuple[BoundaryCandidate, ...]
    selected_cuts: tuple[int, ...]
    rejected_cuts: tuple[dict[str, Any], ...]
    status: str
    review_code: str
    report: dict[str, Any]
    detector_version: str
    analysis_hash: str
    payload_sha256: str = ""
    provider_assessment: dict[str, Any] | None = None
    override: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["candidates"] = [candidate.as_dict() for candidate in self.candidates]
        value["spans"] = [list(span) for span in self.spans]
        value["selected_cuts"] = list(self.selected_cuts)
        return value


@dataclass(frozen=True)
class SourceSegmentationResult:
    ordered_inputs: tuple[Any, ...]
    reports: tuple[StripSegmentationResult, ...]
    status: str
    coverage_ratio: float
    analysis_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "ordered_source_asset_ids": [item.source_asset_id for item in self.ordered_inputs],
            "reports": [report.as_dict() for report in self.reports],
            "status": self.status,
            "coverage_ratio": self.coverage_ratio,
            "analysis_hash": self.analysis_hash,
        }


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _rect(value: Any) -> Bounds:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise StripSegmentationError("segmentation.provider_coordinate_invalid")
    if not all(isinstance(item, int) and not isinstance(item, bool) for item in value):
        raise StripSegmentationError("segmentation.provider_coordinate_invalid")
    result = tuple(value)
    if result[0] < 0 or result[1] < 0 or result[2] <= result[0] or result[3] <= result[1]:
        raise StripSegmentationError("segmentation.provider_coordinate_invalid")
    return result


def _area(bounds: Bounds) -> int:
    return (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])


def _intersection(left: Bounds, right: Bounds) -> Bounds | None:
    result = (
        max(left[0], right[0]),
        max(left[1], right[1]),
        min(left[2], right[2]),
        min(left[3], right[3]),
    )
    return result if result[2] > result[0] and result[3] > result[1] else None


def _union_area(rectangles: Sequence[Bounds]) -> int:
    if not rectangles:
        return 0
    x_edges = sorted({edge for rect in rectangles for edge in (rect[0], rect[2])})
    y_edges = sorted({edge for rect in rectangles for edge in (rect[1], rect[3])})
    total = 0
    for x0, x1 in zip(x_edges, x_edges[1:], strict=False):
        for y0, y1 in zip(y_edges, y_edges[1:], strict=False):
            cell = (x0, y0, x1, y1)
            if any(_intersection(cell, rect) for rect in rectangles):
                total += _area(cell)
    return total


def _finite_fraction(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StripSegmentationError("segmentation.provider_geometry_invalid")
    result = float(value)
    if not isfinite(result) or not 0.0 <= result <= 1.0:
        raise StripSegmentationError("segmentation.provider_geometry_invalid")
    return result


def _part_count(width: int, height: int, max_parts: int) -> int:
    frame_height = width / strips.TARGET_RATIO
    if frame_height <= 0:
        return 1
    return max(1, min(max_parts, round(height / frame_height)))


def _candidate_pool(
    image: Image.Image,
    *,
    max_parts: int,
    min_segment_px: int,
) -> tuple[tuple[BoundaryCandidate, ...], tuple[int, ...]]:
    width, height = image.size
    parts = _part_count(width, height, max_parts)
    if parts <= 1:
        return (), ()
    frame_height = width / strips.TARGET_RATIO
    radius = max(min_segment_px, int(frame_height * 0.2))
    local = strips.color_agnostic_separator_candidates(image, max_pixels=MAX_ANALYSIS_PIXELS)
    values: dict[int, BoundaryCandidate] = {candidate.position: BoundaryCandidate(
        position=candidate.position,
        confidence=candidate.confidence,
        score=candidate.score,
        run_top=candidate.run_top,
        run_bottom=candidate.run_bottom,
        reason=candidate.reason,
    ) for candidate in local}
    ideals = tuple(round(height * index / parts) for index in range(1, parts))
    for ideal in ideals:
        nearby = [candidate for candidate in local if abs(candidate.position - ideal) <= radius]
        if nearby:
            continue
        values.setdefault(
            ideal,
            BoundaryCandidate(
                position=ideal,
                confidence=0.0,
                score=0.0,
                run_top=ideal,
                run_bottom=ideal,
                reason="target_geometry_candidate_without_local_separator",
            ),
        )
    ordered = tuple(sorted(values.values(), key=lambda candidate: (-candidate.confidence, candidate.position)))
    return ordered, ideals


def _tiles(image: Image.Image, *, tile_height: int = 1024, overlap: int = 64) -> tuple[dict[str, Any], ...]:
    width, height = image.size
    result: list[dict[str, Any]] = []
    start = 0
    index = 0
    while start < height:
        end = min(height, start + tile_height)
        crop = image.crop((0, start, width, end))
        output = io.BytesIO()
        crop.save(output, format="PNG", optimize=False)
        result.append(
            {
                "tile_index": index,
                "y0": start,
                "y1": end,
                "overlap_above": 0 if start == 0 else overlap,
                "overlap_below": 0 if end == height else overlap,
                "payload_b64": base64.b64encode(output.getvalue()).decode("ascii"),
            }
        )
        if end == height:
            break
        start = end - overlap
        index += 1
    return tuple(result)


def _provider_payload_without_hashes(value: Any) -> None:
    if isinstance(value, Mapping):
        if any(key in _HASH_FIELDS or str(key).endswith("_hash") for key in value):
            raise StripSegmentationError("segmentation.provider_hash_forbidden")
        for nested in value.values():
            _provider_payload_without_hashes(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _provider_payload_without_hashes(nested)


def _validate_protected_region(raw: Any, *, width: int, height: int) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise StripSegmentationError("segmentation.provider_geometry_invalid")
    kind = str(raw.get("kind", ""))
    if kind not in _PROTECTED_KINDS:
        raise StripSegmentationError("segmentation.provider_geometry_invalid")
    source = str(raw.get("evidence_source", "")).strip()
    if not source or "ocr_text_only" in source.lower():
        raise StripSegmentationError("segmentation.provider_geometry_invalid")
    bounds = _rect(raw.get("bounds"))
    if bounds[2] > width or bounds[3] > height:
        raise StripSegmentationError("segmentation.provider_coordinate_invalid")
    return {
        "region_id": str(raw.get("region_id", "")).strip() or "provider-region",
        "kind": kind,
        "bounds": list(bounds),
        "confidence": _finite_fraction(raw.get("confidence")),
        "evidence_source": source,
    }


def _validate_provider_assessment(
    raw: Any,
    request: BoundaryRequest,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise StripSegmentationError("segmentation.provider_response_invalid")
    _provider_payload_without_hashes(raw)
    if raw.get("source_asset_id") != request.source_asset_id or raw.get("source_checksum") != request.source_checksum:
        raise StripSegmentationError("segmentation.provider_lineage_invalid")
    if raw.get("random_sampling") is not False:
        raise StripSegmentationError("segmentation.provider_sampling_invalid")
    raw_boundaries = raw.get("boundaries")
    if not isinstance(raw_boundaries, list):
        raise StripSegmentationError("segmentation.provider_response_invalid")
    allowed = {candidate.position for candidate in request.candidates}
    boundaries: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw_boundary in raw_boundaries:
        if not isinstance(raw_boundary, Mapping):
            raise StripSegmentationError("segmentation.provider_response_invalid")
        y = raw_boundary.get("y")
        if isinstance(y, bool) or not isinstance(y, int) or y <= 0 or y >= request.height:
            raise StripSegmentationError("segmentation.provider_coordinate_invalid")
        if y not in allowed or y in seen:
            raise StripSegmentationError("segmentation.provider_coordinate_invalid")
        seen.add(y)
        regions_raw = raw_boundary.get("protected_regions", [])
        if not isinstance(regions_raw, list):
            raise StripSegmentationError("segmentation.provider_geometry_invalid")
        protected = tuple(
            _validate_protected_region(region, width=request.width, height=request.height)
            for region in regions_raw
        )
        accepted = raw_boundary.get("accepted") is True
        reason = str(raw_boundary.get("reason", "")).strip()
        if not reason:
            raise StripSegmentationError("segmentation.provider_response_invalid")
        blocked = any(region["bounds"][1] < y < region["bounds"][3] for region in protected)
        boundaries.append(
            {
                "y": y,
                "accepted": accepted and not blocked,
                "confidence": _finite_fraction(raw_boundary.get("confidence")),
                "reason": "segmentation.protected_boundary" if blocked else reason,
                "protected_regions": list(protected),
            }
        )
    return {"boundaries": boundaries, "source_asset_id": request.source_asset_id}


def _make_result(
    *,
    source_asset_id: str,
    source_checksum: str,
    width: int,
    height: int,
    spans: tuple[Bounds, ...],
    candidates: tuple[BoundaryCandidate, ...],
    selected_cuts: tuple[int, ...],
    rejected_cuts: tuple[dict[str, Any], ...],
    status: str,
    review_code: str,
    detector_version: str,
    payload_sha256: str = "",
    provider_assessment: dict[str, Any] | None = None,
    override: dict[str, Any] | None = None,
) -> StripSegmentationResult:
    report = {
        "source_asset_id": source_asset_id,
        "source_checksum": source_checksum,
        "source_dimensions": [width, height],
        "payload_sha256": payload_sha256,
        "detector_version": detector_version,
        "separator_detector_version": strips.COLOR_AGNOSTIC_DETECTOR_VERSION,
        "candidate_boundaries": [candidate.as_dict() for candidate in candidates],
        "selected_cuts": list(selected_cuts),
        "rejected_cuts": [dict(item) for item in rejected_cuts],
        "coverage_spans": [list(span) for span in spans],
        "coverage_complete": spans == ((0, height),) or (
            bool(spans)
            and spans[0][0] == 0
            and spans[-1][1] == height
            and all(left[1] == right[0] for left, right in zip(spans, spans[1:], strict=False))
        ),
        "actionable_reason": (
            "confirm a boundary or explicitly approve one canonical tall scene"
            if status == "NEEDS_REVIEW"
            else "none"
        ),
    }
    if override is not None:
        report["override"] = dict(override)
    identity = {
        "source_asset_id": source_asset_id,
        "source_checksum": source_checksum,
        "payload_sha256": payload_sha256,
        "width": width,
        "height": height,
        "spans": [list(span) for span in spans],
        "candidates": [candidate.as_dict() for candidate in candidates],
        "selected_cuts": list(selected_cuts),
        "rejected_cuts": [dict(item) for item in rejected_cuts],
        "status": status,
        "review_code": review_code,
        "detector_version": detector_version,
        "separator_detector_version": strips.COLOR_AGNOSTIC_DETECTOR_VERSION,
        "provider_assessment": provider_assessment,
        "override": override,
    }
    return StripSegmentationResult(
        source_asset_id=source_asset_id,
        source_checksum=source_checksum,
        width=width,
        height=height,
        spans=spans,
        candidates=candidates,
        selected_cuts=selected_cuts,
        rejected_cuts=tuple(dict(item) for item in rejected_cuts),
        status=status,
        review_code=review_code,
        report=report,
        detector_version=detector_version,
        analysis_hash=_hash(identity),
        payload_sha256=payload_sha256,
        provider_assessment=provider_assessment,
        override=override,
    )


def reconcile_strip(
    data: bytes,
    *,
    source_asset_id: str,
    original_checksum: str | None = None,
    min_strip_ratio: float = 2.5,
    max_parts: int = 12,
    min_segment_px: int = 200,
    boundary_assessor: Callable[[BoundaryRequest], Mapping[str, Any]] | None = None,
    max_pixels: int = MAX_ANALYSIS_PIXELS,
) -> StripSegmentationResult:
    """Reconcile one source image without silently cutting ambiguous artwork."""
    if not isinstance(source_asset_id, str) or not source_asset_id.strip():
        raise StripSegmentationError("segmentation.lineage_invalid", reviewable=False)
    payload_sha256 = hashlib.sha256(data).hexdigest()
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            source = image.convert("RGB")
    except (OSError, UnidentifiedImageError, ValueError):
        raise StripSegmentationError("segmentation.source_decode_failed", reviewable=False) from None
    width, height = source.size
    if width <= 0 or height <= 0:
        raise StripSegmentationError("segmentation.source_dimensions_invalid", reviewable=False)
    if width * height > max_pixels:
        raise StripSegmentationError("segmentation.pixel_budget_exceeded", reviewable=True)
    checksum = original_checksum or hashlib.sha256(data).hexdigest()
    ratio = height / width
    if ratio < min_strip_ratio:
        return _make_result(
            source_asset_id=source_asset_id,
            source_checksum=checksum,
            width=width,
            height=height,
            spans=((0, height),),
            candidates=(),
            selected_cuts=(),
            rejected_cuts=(),
            status="RECONCILED",
            review_code="",
            detector_version=SEGMENTATION_VERSION,
            payload_sha256=payload_sha256,
        )

    candidates, ideals = _candidate_pool(source, max_parts=max_parts, min_segment_px=min_segment_px)
    if not ideals:
        return _make_result(
            source_asset_id=source_asset_id,
            source_checksum=checksum,
            width=width,
            height=height,
            spans=((0, height),),
            candidates=candidates,
            selected_cuts=(),
            rejected_cuts=(),
            status="RECONCILED",
            review_code="",
            detector_version=SEGMENTATION_VERSION,
            payload_sha256=payload_sha256,
        )
    request = BoundaryRequest(
        source_asset_id=source_asset_id,
        source_checksum=checksum,
        width=width,
        height=height,
        candidates=candidates,
        tiles=_tiles(source),
    )
    provider_assessment: dict[str, Any] | None = None
    if boundary_assessor is not None:
        try:
            provider_assessment = _validate_provider_assessment(boundary_assessor(request), request)
        except StripSegmentationError:
            raise
        except Exception:
            raise StripSegmentationError("segmentation.provider_request_failed") from None
    by_position = {candidate.position: candidate for candidate in candidates}
    provider_by_position = {
        item["y"]: item for item in (provider_assessment or {}).get("boundaries", ())
    }
    selected: list[int] = []
    rejected: list[dict[str, Any]] = []
    for ideal in ideals:
        nearby = [candidate for candidate in candidates if abs(candidate.position - ideal) <= max(min_segment_px, int(width / strips.TARGET_RATIO * 0.2))]
        if provider_assessment is not None:
            nearby = [candidate for candidate in nearby if candidate.position in provider_by_position]
            nearby = [candidate for candidate in nearby if provider_by_position[candidate.position]["accepted"]]
            nearby.sort(
                key=lambda candidate: (
                    -provider_by_position[candidate.position]["confidence"],
                    abs(candidate.position - ideal),
                    candidate.position,
                )
            )
        else:
            nearby = [candidate for candidate in nearby if candidate.confidence >= 0.7]
            nearby.sort(key=lambda candidate: (-candidate.confidence, abs(candidate.position - ideal), candidate.position))
        choice = next(
            (
                candidate
                for candidate in nearby
                if not selected or candidate.position - selected[-1] >= min_segment_px
            ),
            None,
        )
        if choice is None:
            reasons = [
                item["reason"]
                for item in (provider_assessment or {}).get("boundaries", ())
                if abs(item["y"] - ideal) <= max(min_segment_px, int(width / strips.TARGET_RATIO * 0.2))
            ]
            rejected.append(
                {
                    "ideal": ideal,
                    "reason": "segmentation.protected_boundary" if "segmentation.protected_boundary" in reasons else "segmentation.ambiguous_boundary",
                    "candidate_positions": [candidate.position for candidate in by_position.values() if abs(candidate.position - ideal) <= max(min_segment_px, int(width / strips.TARGET_RATIO * 0.2))],
                }
            )
            continue
        selected.append(choice.position)
    boundaries = [0, *selected, height]
    valid_partition = bool(selected) and all(
        right - left >= min_segment_px
        for left, right in zip(boundaries, boundaries[1:], strict=False)
    ) and len(set(selected)) == len(selected)
    if len(selected) != len(ideals) or not valid_partition:
        review_code = (
            "segmentation.protected_boundary"
            if any(item["reason"] == "segmentation.protected_boundary" for item in rejected)
            else "segmentation.ambiguous_boundary"
        )
        return _make_result(
            source_asset_id=source_asset_id,
            source_checksum=checksum,
            width=width,
            height=height,
            spans=((0, height),),
            candidates=candidates,
            selected_cuts=tuple(selected),
            rejected_cuts=tuple(rejected),
            status="NEEDS_REVIEW",
            review_code=review_code,
            detector_version=SEGMENTATION_VERSION,
            payload_sha256=payload_sha256,
            provider_assessment=provider_assessment,
        )
    spans = tuple((boundaries[index], boundaries[index + 1]) for index in range(len(boundaries) - 1))
    return _make_result(
        source_asset_id=source_asset_id,
        source_checksum=checksum,
        width=width,
        height=height,
        spans=spans,
        candidates=candidates,
        selected_cuts=tuple(selected),
        rejected_cuts=tuple(rejected),
        status="RECONCILED",
        review_code="",
        detector_version=SEGMENTATION_VERSION,
        payload_sha256=payload_sha256,
        provider_assessment=provider_assessment,
    )


def _validate_source_input_lineage(inputs: Sequence[Any]) -> float:
    grouped: dict[tuple[str, int, int], list[Bounds]] = {}
    checksums: dict[tuple[str, int, int], set[str]] = {}
    seen_asset_ids: set[str] = set()
    for item in inputs:
        if (
            not isinstance(item.source_asset_id, str)
            or not item.source_asset_id.strip()
            or isinstance(item.original_width, bool)
            or not isinstance(item.original_width, int)
            or item.original_width <= 0
            or isinstance(item.original_height, bool)
            or not isinstance(item.original_height, int)
            or item.original_height <= 0
        ):
            raise StripSegmentationError("segmentation.lineage_invalid", reviewable=False)
        if item.source_asset_id in seen_asset_ids:
            raise StripSegmentationError("segmentation.lineage_duplicate")
        seen_asset_ids.add(item.source_asset_id)
        try:
            bounds = _rect(item.source_bounds)
        except StripSegmentationError:
            raise StripSegmentationError("segmentation.lineage_invalid", reviewable=False) from None
        if bounds[2] > item.original_width or bounds[3] > item.original_height:
            raise StripSegmentationError("segmentation.lineage_invalid", reviewable=False)
        lineage = str(getattr(item, "source_family", "") or item.source_asset_id)
        key = (lineage, item.original_width, item.original_height)
        grouped.setdefault(key, []).append(bounds)
        checksums.setdefault(key, set()).add(str(item.original_checksum or ""))
    expected = 0
    accounted = 0
    for key, bounds in grouped.items():
        _, width, height = key
        if len(checksums[key]) != 1 or "" in checksums[key]:
            raise StripSegmentationError("segmentation.source_checksum_mismatch")
        expected += width * height
        for left_index, left in enumerate(bounds):
            if any(_intersection(left, right) for right in bounds[left_index + 1:]):
                raise StripSegmentationError("segmentation.coverage_overlap")
        accounted += _union_area(bounds)
    if expected == 0:
        raise StripSegmentationError("segmentation.coverage_incomplete")
    ratio = accounted / expected
    if accounted != expected:
        raise StripSegmentationError("segmentation.coverage_incomplete")
    return ratio


def _source_group_key(item: Any) -> tuple[str, int, int]:
    return (
        str(getattr(item, "source_family", "") or item.source_asset_id),
        int(item.original_width),
        int(item.original_height),
    )


def _reconstruct_source_group(
    items: Sequence[Any],
) -> tuple[bytes, str, str] | None:
    """Rebuild a sliced source in memory for boundary validation.

    The returned bytes are transient validation input only. They are never
    persisted as an asset and never replace the already-ingested panel files.
    """
    if len(items) <= 1:
        return None
    width = int(items[0].original_width)
    height = int(items[0].original_height)
    full_bounds = (0, 0, width, height)
    bounds = tuple(_rect(item.source_bounds) for item in items)
    if all(value == full_bounds for value in bounds):
        return None
    checksum_values = {str(item.original_checksum or "") for item in items}
    if len(checksum_values) != 1 or "" in checksum_values:
        raise StripSegmentationError("segmentation.source_checksum_mismatch")
    canvas = Image.new("RGB", (width, height))
    for item, item_bounds in zip(items, bounds, strict=True):
        expected_size = (item_bounds[2] - item_bounds[0], item_bounds[3] - item_bounds[1])
        try:
            with Image.open(io.BytesIO(item.payload)) as image:
                image.load()
                decoded = image.convert("RGB")
        except (OSError, UnidentifiedImageError, ValueError):
            raise StripSegmentationError("segmentation.source_decode_failed") from None
        if decoded.size != expected_size:
            raise StripSegmentationError("segmentation.lineage_invalid", reviewable=False)
        canvas.paste(decoded, (item_bounds[0], item_bounds[1]))
    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=False)
    lineage = str(getattr(items[0], "source_family", "") or items[0].source_asset_id)
    return output.getvalue(), f"source-family:{lineage}", next(iter(checksum_values))


def reconcile_sources(
    inputs: Sequence[Any],
    *,
    boundary_assessor: Callable[[BoundaryRequest], Mapping[str, Any]] | None = None,
    review_root: Path | None = None,
) -> SourceSegmentationResult:
    """Reconcile multiple files in file order and each file top-to-bottom."""
    coverage_ratio = _validate_source_input_lineage(inputs)
    ordered = tuple(sorted(inputs, key=lambda item: (item.strip_order, item.region_order, item.source_bounds[1], item.source_asset_id)))
    reports: list[StripSegmentationResult] = []
    groups: dict[tuple[str, int, int], list[Any]] = {}
    for item in ordered:
        groups.setdefault(_source_group_key(item), []).append(item)
    for group in groups.values():
        reconstructed = _reconstruct_source_group(group)
        if reconstructed is None:
            item = group[0]
            payload = item.payload
            source_asset_id = item.source_asset_id
            checksum = item.original_checksum
        else:
            payload, source_asset_id, checksum = reconstructed
        result = reconcile_strip(
            payload,
            source_asset_id=source_asset_id,
            original_checksum=checksum,
            boundary_assessor=boundary_assessor,
        )
        reports.append(result)
        if result.status == "NEEDS_REVIEW" and review_root is not None:
            write_review_artifact(result, payload, review_root)
    status = "NEEDS_REVIEW" if coverage_ratio != 1.0 or any(report.status != "RECONCILED" for report in reports) else "RECONCILED"
    return SourceSegmentationResult(
        ordered_inputs=ordered,
        reports=tuple(reports),
        status=status,
        coverage_ratio=coverage_ratio,
        analysis_hash=_hash(
            {
                "version": SEGMENTATION_VERSION,
                "coverage_ratio": coverage_ratio,
                "reports": [report.analysis_hash for report in reports],
                "source_asset_ids": [item.source_asset_id for item in ordered],
            }
        ),
    )


def apply_manual_override(
    result: StripSegmentationResult,
    *,
    cuts: Sequence[int],
    actor_id: str,
    reason: str,
) -> StripSegmentationResult:
    """Approve a deterministic partition with explicit human provenance."""
    if not actor_id.strip() or not reason.strip():
        raise StripSegmentationError("segmentation.override_audit_invalid", reviewable=False)
    values = tuple(cuts)
    if any(isinstance(cut, bool) or not isinstance(cut, int) or cut <= 0 or cut >= result.height for cut in values):
        raise StripSegmentationError("segmentation.override_coordinate_invalid", reviewable=False)
    if tuple(sorted(set(values))) != values:
        raise StripSegmentationError("segmentation.override_coordinate_invalid", reviewable=False)
    bounds = (0, *values, result.height)
    if any(right - left < 1 for left, right in zip(bounds, bounds[1:], strict=False)):
        raise StripSegmentationError("segmentation.override_coordinate_invalid", reviewable=False)
    override = {
        "actor_id": actor_id,
        "reason": reason,
        "provenance": "manual_override",
        "prior_analysis_hash": result.analysis_hash,
    }
    spans = tuple((bounds[index], bounds[index + 1]) for index in range(len(bounds) - 1))
    return _make_result(
        source_asset_id=result.source_asset_id,
        source_checksum=result.source_checksum,
        width=result.width,
        height=result.height,
        spans=spans,
        candidates=result.candidates,
        selected_cuts=values,
        rejected_cuts=result.rejected_cuts,
        status="RECONCILED",
        review_code="",
        detector_version=result.detector_version,
        payload_sha256=result.payload_sha256,
        provider_assessment=result.provider_assessment,
        override=override,
    )


def write_review_artifact(result: StripSegmentationResult, data: bytes, root: Path) -> tuple[Path, Path]:
    """Write a small JSON report and thumbnail outside tracked source paths."""
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", result.source_asset_id).strip("._") or "source"
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    report_path = directory / f"{safe_id}-{result.analysis_hash[:12]}.json"
    thumbnail_path = directory / f"{safe_id}-{result.analysis_hash[:12]}.jpg"
    report = dict(result.report)
    report.update(
        {
            "analysis_hash": result.analysis_hash,
            "status": result.status,
            "review_code": result.review_code,
            "provider_assessment": result.provider_assessment,
        }
    )
    report["thumbnail_file"] = thumbnail_path.name
    report_path.write_text(_canonical(report) + "\n", encoding="utf-8")
    try:
        with Image.open(io.BytesIO(data)) as image:
            preview = image.convert("RGB")
            preview.thumbnail((360, 640), Image.Resampling.LANCZOS)
            preview.save(thumbnail_path, format="JPEG", quality=82, optimize=False)
    except (OSError, UnidentifiedImageError, ValueError):
        raise StripSegmentationError("segmentation.review_artifact_failed") from None
    return report_path, thumbnail_path


__all__ = [
    "BOUNDARY_PROMPT_VERSION",
    "BoundaryCandidate",
    "BoundaryRequest",
    "SEGMENTATION_VERSION",
    "SourceSegmentationResult",
    "StripSegmentationError",
    "StripSegmentationResult",
    "apply_manual_override",
    "reconcile_sources",
    "reconcile_strip",
    "write_review_artifact",
]
