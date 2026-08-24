"""Fail-closed reconciliation for long vertical source strips.

The legacy :mod:`app.services.strips` API still returns deterministic slices for
callers that need its historical behaviour. This module is the mass-production
boundary: it accepts only complete source-space partitions, permits only
high-confidence structural gutters without a provider, and turns ambiguous
boundaries into an auditable review state.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from math import ceil, isfinite
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from app.services import strips

SEGMENTATION_VERSION = "color-agnostic-strip-reconciliation-v2"
BOUNDARY_PROMPT_VERSION = "strip-boundary-assessment-v1"
MAX_ANALYSIS_PIXELS = 20_000_000
BOUNDARY_REQUEST_MAX_BYTES = 2_000_000
BOUNDARY_TILE_MAX_COUNT = 12
BOUNDARY_TILE_MAX_WIDTH = 512
BOUNDARY_TILE_MAX_HEIGHT = 768
BOUNDARY_TILE_JPEG_QUALITY = 68
MAX_SEGMENT_FRAME_MULTIPLIER = 2.0
_PROTECTED_KINDS = {"balloon", "face", "subject", "action", "effect", "continuity"}
_HASH_FIELDS = {"analysis_hash", "boundary_hash", "evidence_hash", "mask_sha256"}
Bounds = tuple[int, int, int, int]


class StripSegmentationError(RuntimeError):
    """Safe, stable error at the source-segmentation boundary."""

    def __init__(
        self,
        code: str,
        message: str = "strip segmentation failed",
        *,
        reviewable: bool = True,
        safe_metadata: Mapping[str, Any] | None = None,
    ):
        self.code = code
        self.reviewable = reviewable
        self.safe_metadata = dict(safe_metadata or {})
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
        # Raw tile bytes are request content, not JSON metadata.  The adapter
        # receives them separately as multimodal image parts; keeping them out
        # of this payload prevents accidental persistence/serialization.
        metadata_tiles = [
            {key: value for key, value in tile.items() if key != "payload"}
            for tile in self.tiles
        ]
        return {
            "contract_version": BOUNDARY_PROMPT_VERSION,
            "source_asset_id": self.source_asset_id,
            "source_checksum": self.source_checksum,
            "source_dimensions": [self.width, self.height],
            "detector_version": self.detector_version,
            "candidate_boundaries": [candidate.as_dict() for candidate in self.candidates],
            "overlapping_source_tiles": metadata_tiles,
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
    radius = max(min_segment_px, int(frame_height * 0.2))
    offsets = (-radius, -(radius // 2), 0, radius // 2, radius)
    for ideal in ideals:
        nearby = [candidate for candidate in local if abs(candidate.position - ideal) <= radius]
        if nearby:
            values.setdefault(ideal, BoundaryCandidate(
                position=ideal,
                confidence=0.0,
                score=0.0,
                run_top=ideal,
                run_bottom=ideal,
                reason="target_geometry_candidate_without_local_separator",
            ))
        for offset in offsets:
            position = max(min_segment_px, min(height - min_segment_px, ideal + offset))
            if position <= 0 or position >= height:
                continue
            values.setdefault(
                position,
                BoundaryCandidate(
                    position=position,
                    confidence=0.0,
                    score=0.0,
                    run_top=position,
                    run_bottom=position,
                    reason="target_geometry_candidate_near_ideal",
                ),
            )
    ordered = tuple(sorted(values.values(), key=lambda candidate: (-candidate.confidence, candidate.position)))
    return ordered, ideals


def _select_geometry_valid_partition(
    candidates: Sequence[BoundaryCandidate],
    *,
    width: int,
    height: int,
    min_segment_px: int,
    target_parts: int,
    max_parts: int,
) -> tuple[int, ...]:
    """Select a deterministic complete partition from trusted candidates.

    Target positions are a ranking signal, not a requirement that every real
    panel have equal height.  The hard geometry contract remains explicit:
    every span must be at least ``min_segment_px`` and at most two target
    frame-heights, and enough cuts must exist to cover the source without an
    oversized terminal span.  Dynamic programming keeps the result bounded and
    stable when provider boundaries are nonuniform.
    """
    if not candidates:
        return ()
    frame_height = width / strips.TARGET_RATIO
    max_segment_px = max(
        min_segment_px,
        int(round(frame_height * MAX_SEGMENT_FRAME_MULTIPLIER)),
    )
    required_cuts = max(1, ceil(height / max_segment_px) - 1)
    ordered = tuple(sorted(candidates, key=lambda item: item.position))
    max_cuts = min(max_parts - 1, len(ordered))
    if required_cuts > max_cuts:
        return ()

    states: list[dict[int, tuple[int, ...]]] = [{} for _ in ordered]

    def prefix_key(path: tuple[int, ...]) -> tuple[float, float, tuple[int, ...]]:
        spans = (path[0], *tuple(right - left for left, right in zip(path, path[1:], strict=False)))
        confidence = sum(
            next(item.confidence for item in ordered if item.position == position)
            for position in path
        )
        return (
            sum(abs(span - frame_height) for span in spans),
            -confidence,
            path,
        )

    for index, candidate in enumerate(ordered):
        if min_segment_px <= candidate.position <= max_segment_px:
            states[index][1] = (candidate.position,)
        for previous_index, previous in enumerate(ordered[:index]):
            gap = candidate.position - previous.position
            if not min_segment_px <= gap <= max_segment_px:
                continue
            for cuts, path in states[previous_index].items():
                if cuts >= max_cuts:
                    continue
                extended = (*path, candidate.position)
                current = states[index].get(cuts + 1)
                if current is None or prefix_key(extended) < prefix_key(current):
                    states[index][cuts + 1] = extended

    ranked: list[tuple[tuple[object, ...], tuple[int, ...]]] = []
    for state in states:
        for path in state.values():
            final_span = height - path[-1]
            if not min_segment_px <= final_span <= max_segment_px:
                continue
            spans = (path[0], *tuple(right - left for left, right in zip(path, path[1:], strict=False)), final_span)
            confidence = sum(
                next(item.confidence for item in ordered if item.position == position)
                for position in path
            )
            ranked.append(
                (
                    (
                        abs(len(spans) - target_parts),
                        sum(abs(span - frame_height) for span in spans),
                        -confidence,
                        path,
                    ),
                    path,
                )
            )
    if not ranked:
        return ()
    ranked.sort(key=lambda item: item[0])
    return ranked[0][1]


def _tiles(image: Image.Image, *, tile_height: int = 2048, overlap: int = 128) -> tuple[dict[str, Any], ...]:
    width, height = image.size
    if width <= 0 or height <= 0:
        raise StripSegmentationError("segmentation.source_dimensions_invalid", reviewable=False)
    tile_height = max(1, int(tile_height))
    overlap = max(0, int(overlap))
    if (height + tile_height - 1) // tile_height > BOUNDARY_TILE_MAX_COUNT:
        tile_height = (height + BOUNDARY_TILE_MAX_COUNT - 1) // BOUNDARY_TILE_MAX_COUNT
    overlap = min(overlap, max(0, tile_height - 1))
    result: list[dict[str, Any]] = []
    start = 0
    index = 0
    while start < height:
        end = min(height, start + tile_height)
        crop = image.crop((0, start, width, end))
        preview = crop.convert("RGB")
        preview.thumbnail(
            (BOUNDARY_TILE_MAX_WIDTH, BOUNDARY_TILE_MAX_HEIGHT),
            Image.Resampling.LANCZOS,
        )
        output = io.BytesIO()
        preview.save(
            output,
            format="JPEG",
            quality=BOUNDARY_TILE_JPEG_QUALITY,
            optimize=True,
            progressive=False,
            subsampling=2,
        )
        payload = output.getvalue()
        result.append(
            {
                "tile_index": index,
                "y0": start,
                "y1": end,
                "overlap_above": 0 if start == 0 else overlap,
                "overlap_below": 0 if end == height else overlap,
                "mime_type": "image/jpeg",
                "encoded_width": preview.width,
                "encoded_height": preview.height,
                # Ephemeral bytes consumed by a multimodal adapter.  This
                # field is intentionally removed by BoundaryRequest.as_payload
                # and is never written to a report/checkpoint.
                "payload": payload,
                "payload_sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
        if end == height:
            break
        start = end - overlap
        index += 1
    payload_bytes = sum(len(tile["payload"]) for tile in result)
    if payload_bytes > BOUNDARY_REQUEST_MAX_BYTES:
        raise StripSegmentationError("segmentation.tile_payload_budget_exceeded")
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
        if "y" in raw_boundary and "position" in raw_boundary and raw_boundary["y"] != raw_boundary["position"]:
            raise StripSegmentationError("segmentation.provider_coordinate_invalid")
        if "y" not in raw_boundary and "position" in raw_boundary:
            raw_boundary = {**raw_boundary, "y": raw_boundary["position"]}
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


def _assess_with_retry(
    boundary_assessor: Callable[[BoundaryRequest], Mapping[str, Any]],
    request: BoundaryRequest,
    attempts: int,
) -> dict[str, Any]:
    """Validate one provider response.

    Transport retry ownership lives in the provider adapter/runner.  Keeping
    this boundary single-shot prevents nested retry multiplication and ensures
    schema, lineage, and geometry failures are permanent for this source.
    ``attempts`` remains an API-compatible argument for older callers.
    """
    del attempts
    raw = boundary_assessor(request)
    return _validate_provider_assessment(raw, request)


def _safe_provider_error(exc: Exception) -> tuple[str, bool, dict[str, Any]]:
    """Map provider failures while retaining only typed, non-sensitive facts."""

    code = str(getattr(exc, "code", "") or "")
    mapping = {
        "cloud.provider_request_failed": "segmentation.provider_request_failed",
        "cloud.provider_request_invalid": "segmentation.provider_request_invalid",
        "cloud.provider_response_invalid": "segmentation.provider_response_invalid",
        "vision_provider_request_failed": "segmentation.provider_request_failed",
        "vision_request_invalid": "segmentation.provider_request_invalid",
        "vision_response_invalid": "segmentation.provider_response_invalid",
    }
    mapped = mapping.get(code, "segmentation.provider_request_failed")
    raw = getattr(exc, "safe_metadata", {})
    metadata = dict(raw) if isinstance(raw, Mapping) else {}
    for name in ("status_code", "retry_after_s", "timeout"):
        value = getattr(exc, name, None)
        if isinstance(value, (int, float, bool)) and not isinstance(value, bool) or (
            name == "timeout" and isinstance(value, bool)
        ):
            metadata.setdefault(name, value)
    subtype = getattr(exc, "transport_subtype", None)
    if isinstance(subtype, str) and subtype:
        metadata.setdefault("transport_subtype", subtype)
    if "provider_error_category" not in metadata:
        if metadata.get("timeout"):
            metadata["provider_error_category"] = "timeout"
        elif metadata.get("status_code") == 429:
            metadata["provider_error_category"] = "rate_limit"
        elif isinstance(metadata.get("status_code"), int) and metadata["status_code"] >= 500:
            metadata["provider_error_category"] = "server"
        else:
            metadata["provider_error_category"] = "transport"
    metadata.setdefault("provider_error_code", code or type(exc).__name__)
    metadata["retryable"] = bool(getattr(exc, "retryable", False))
    return mapped, bool(getattr(exc, "reviewable", True)), metadata


def _checkpoint_identity_hash(identity: Mapping[str, Any] | None) -> str:
    return _hash({"segmentation_version": SEGMENTATION_VERSION, "identity": dict(identity or {})})


def _checkpoint_path(
    root: Path,
    *,
    source_asset_id: str,
    source_checksum: str,
    identity: Mapping[str, Any],
) -> Path:
    key = _hash(
        {
            "source_asset_id": source_asset_id,
            "source_checksum": source_checksum,
            "segmentation_version": SEGMENTATION_VERSION,
            "identity_hash": _checkpoint_identity_hash(identity),
        }
    )
    return Path(root) / f"{key}.json"


def _load_source_checkpoint(
    group: Sequence[Any],
    *,
    payload: bytes,
    source_asset_id: str,
    source_checksum: str,
    root: Path | None,
    identity: Mapping[str, Any] | None,
) -> StripSegmentationResult | None:
    if root is None or identity is None:
        return None
    path = _checkpoint_path(
        root,
        source_asset_id=source_asset_id,
        source_checksum=source_checksum,
        identity=identity,
    )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return None
    if not isinstance(raw, Mapping):
        return None
    if (
        raw.get("checkpoint_version") != 1
        or raw.get("source_asset_id") != source_asset_id
        or raw.get("source_checksum") != source_checksum
        or raw.get("segmentation_version") != SEGMENTATION_VERSION
        or raw.get("identity_hash") != _checkpoint_identity_hash(identity)
    ):
        return None
    report = raw.get("report")
    if not isinstance(report, Mapping):
        return None
    ordered_ids = [item.source_asset_id for item in group]
    expected_hash = _hash(
        {
            "version": SEGMENTATION_VERSION,
            "coverage_ratio": 1.0,
            "reports": [report.get("analysis_hash")],
            "source_asset_ids": ordered_ids,
        }
    )
    cached = {
        "ordered_source_asset_ids": ordered_ids,
        "reports": [dict(report)],
        "status": "RECONCILED",
        "coverage_ratio": 1.0,
        "analysis_hash": expected_hash,
    }
    try:
        restored = restore_cached_reconciliation(group, cached)
    except StripSegmentationError:
        return None
    return restored.reports[0]


def _write_source_checkpoint(
    result: StripSegmentationResult,
    *,
    root: Path | None,
    identity: Mapping[str, Any] | None,
) -> None:
    if root is None or identity is None:
        return
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = _checkpoint_path(
        root,
        source_asset_id=result.source_asset_id,
        source_checksum=result.source_checksum,
        identity=identity,
    )
    value = {
        "checkpoint_version": 1,
        "source_asset_id": result.source_asset_id,
        "source_checksum": result.source_checksum,
        "segmentation_version": SEGMENTATION_VERSION,
        "identity_hash": _checkpoint_identity_hash(identity),
        "report": result.as_dict(),
    }
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(_canonical(value), encoding="utf-8")
    temporary.replace(path)


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
    provider_retry_attempts: int = 2,
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
            provider_assessment = _assess_with_retry(
                boundary_assessor,
                request,
                provider_retry_attempts,
            )
        except StripSegmentationError:
            raise
        except Exception as exc:
            code, reviewable, metadata = _safe_provider_error(exc)
            raise StripSegmentationError(
                code,
                reviewable=reviewable,
                safe_metadata=metadata,
            ) from None
    by_position = {candidate.position: candidate for candidate in candidates}
    provider_by_position = {
        item["y"]: item for item in (provider_assessment or {}).get("boundaries", ())
    }
    if provider_assessment is not None:
        selectable = [
            candidate
            for candidate in candidates
            if candidate.position in provider_by_position
            and provider_by_position[candidate.position]["accepted"]
        ]
    else:
        selectable = [candidate for candidate in candidates if candidate.confidence >= 0.7]
    selected = list(
        _select_geometry_valid_partition(
            selectable,
            width=width,
            height=height,
            min_segment_px=min_segment_px,
            target_parts=len(ideals) + 1,
            max_parts=max_parts,
        )
    )
    rejected: list[dict[str, Any]] = []
    if not selected:
        radius = max(min_segment_px, int(width / strips.TARGET_RATIO * 0.2))
        protected_reasons = {
            item["reason"]
            for item in (provider_assessment or {}).get("boundaries", ())
            if item.get("reason") == "segmentation.protected_boundary"
        }
        for ideal in ideals:
            rejected.append(
                {
                    "ideal": ideal,
                    "reason": "segmentation.protected_boundary" if protected_reasons else "segmentation.ambiguous_boundary",
                    "candidate_positions": [
                        candidate.position
                        for candidate in by_position.values()
                        if abs(candidate.position - ideal) <= radius
                    ],
                }
            )
    boundaries = [0, *selected, height]
    frame_height = width / strips.TARGET_RATIO
    max_segment_px = max(min_segment_px, int(round(frame_height * MAX_SEGMENT_FRAME_MULTIPLIER)))
    valid_partition = bool(selected) and all(
        min_segment_px <= right - left <= max_segment_px
        for left, right in zip(boundaries, boundaries[1:], strict=False)
    ) and len(selected) >= max(1, ceil(height / max_segment_px) - 1) and len(selected) <= max_parts - 1 and len(set(selected)) == len(selected)
    if not valid_partition:
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
    provider_retry_attempts: int = 2,
    review_root: Path | None = None,
    checkpoint_root: Path | None = None,
    checkpoint_identity: Mapping[str, Any] | None = None,
    on_reconciled: Callable[[tuple[Any, ...], StripSegmentationResult], None] | None = None,
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
        result = _load_source_checkpoint(
            group,
            payload=payload,
            source_asset_id=source_asset_id,
            source_checksum=checksum,
            root=checkpoint_root,
            identity=checkpoint_identity,
        )
        if result is None:
            try:
                result = reconcile_strip(
                    payload,
                    source_asset_id=source_asset_id,
                    original_checksum=checksum,
                    boundary_assessor=boundary_assessor,
                    provider_retry_attempts=provider_retry_attempts,
                )
            except StripSegmentationError as exc:
                metadata = dict(exc.safe_metadata)
                if checkpoint_root is not None:
                    metadata["durable_progress"] = bool(tuple(Path(checkpoint_root).glob("*.json")))
                    metadata["checkpoint_root"] = "segmentation-checkpoints"
                raise StripSegmentationError(
                    exc.code,
                    reviewable=exc.reviewable,
                    safe_metadata=metadata,
                ) from None
            if result.status == "RECONCILED":
                _write_source_checkpoint(
                    result,
                    root=checkpoint_root,
                    identity=checkpoint_identity,
                )
        reports.append(result)
        if result.status == "NEEDS_REVIEW" and review_root is not None:
            write_review_artifact(result, payload, review_root)
        if result.status == "RECONCILED" and on_reconciled is not None:
            on_reconciled(tuple(group), result)
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


def restore_cached_reconciliation(
    inputs: Sequence[Any],
    cached: Mapping[str, Any],
) -> SourceSegmentationResult:
    """Restore a verified local segmentation checkpoint without provider calls."""

    def invalid() -> StripSegmentationError:
        return StripSegmentationError("segmentation.cache_invalid", reviewable=True)

    if not isinstance(cached, Mapping):
        raise invalid()
    ordered = tuple(
        sorted(
            inputs,
            key=lambda item: (
                item.strip_order,
                item.region_order,
                item.source_bounds[1],
                item.source_asset_id,
            ),
        )
    )
    ordered_ids = cached.get("ordered_source_asset_ids")
    if ordered_ids != [item.source_asset_id for item in ordered]:
        raise invalid()
    if cached.get("status") != "RECONCILED" or cached.get("coverage_ratio") != 1.0:
        raise invalid()
    reports_raw = cached.get("reports")
    if not isinstance(reports_raw, list):
        raise invalid()

    groups: dict[tuple[str, int, int], list[Any]] = {}
    for item in ordered:
        groups.setdefault(_source_group_key(item), []).append(item)
    if len(reports_raw) != len(groups):
        raise invalid()

    reports: list[StripSegmentationResult] = []
    for group, raw in zip(groups.values(), reports_raw, strict=True):
        if not isinstance(raw, Mapping):
            raise invalid()
        reconstructed = _reconstruct_source_group(group)
        if reconstructed is None:
            payload = group[0].payload
            expected_asset_id = group[0].source_asset_id
            expected_checksum = group[0].original_checksum
        else:
            payload, expected_asset_id, expected_checksum = reconstructed
        try:
            with Image.open(io.BytesIO(payload)) as image:
                image.load()
                width, height = image.size
        except (OSError, UnidentifiedImageError, ValueError):
            raise invalid() from None
        if (
            raw.get("source_asset_id") != expected_asset_id
            or raw.get("source_checksum") != expected_checksum
            or raw.get("width") != width
            or raw.get("height") != height
            or raw.get("status") != "RECONCILED"
            or raw.get("review_code") != ""
            or raw.get("detector_version") != SEGMENTATION_VERSION
            or raw.get("payload_sha256") != hashlib.sha256(payload).hexdigest()
        ):
            raise invalid()
        spans_raw = raw.get("spans")
        candidates_raw = raw.get("candidates")
        selected_raw = raw.get("selected_cuts")
        rejected_raw = raw.get("rejected_cuts")
        if not isinstance(spans_raw, list) or not isinstance(candidates_raw, list):
            raise invalid()
        if not isinstance(selected_raw, list) or not isinstance(rejected_raw, (list, tuple)):
            raise invalid()
        try:
            spans = tuple(
                (span[0], span[1])
                for span in spans_raw
                if isinstance(span, (list, tuple))
                and len(span) == 2
                and all(isinstance(value, int) and not isinstance(value, bool) for value in span)
                and span[1] > span[0]
            )
            if len(spans) != len(spans_raw):
                raise ValueError
            selected_cuts = tuple(
                value
                for value in selected_raw
                if isinstance(value, int) and not isinstance(value, bool)
            )
            if len(selected_cuts) != len(selected_raw):
                raise ValueError
            candidates = tuple(
                BoundaryCandidate(
                    position=item["position"],
                    confidence=item["confidence"],
                    score=item["score"],
                    run_top=item["run_top"],
                    run_bottom=item["run_bottom"],
                    reason=item["reason"],
                )
                for item in candidates_raw
                if isinstance(item, Mapping)
            )
            if len(candidates) != len(candidates_raw):
                raise ValueError
            if any(
                isinstance(value, bool)
                or not isinstance(value, int)
                for candidate in candidates
                for value in (
                    candidate.position,
                    candidate.run_top,
                    candidate.run_bottom,
                )
            ):
                raise ValueError
            if any(not isinstance(item, Mapping) for item in rejected_raw):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise invalid() from None
        if (
            not spans
            or spans[0][0] != 0
            or spans[-1][1] != height
            or any(left[1] != right[0] for left, right in zip(spans, spans[1:], strict=False))
            or selected_cuts != tuple(span[1] for span in spans[:-1])
        ):
            raise invalid()
        provider_assessment = raw.get("provider_assessment")
        if provider_assessment is not None and not isinstance(provider_assessment, Mapping):
            raise invalid()
        override = raw.get("override")
        if override is not None and not isinstance(override, Mapping):
            raise invalid()
        restored = _make_result(
            source_asset_id=expected_asset_id,
            source_checksum=expected_checksum,
            width=width,
            height=height,
            spans=spans,
            candidates=candidates,
            selected_cuts=selected_cuts,
            rejected_cuts=tuple(dict(item) for item in rejected_raw),
            status="RECONCILED",
            review_code="",
            detector_version=SEGMENTATION_VERSION,
            payload_sha256=hashlib.sha256(payload).hexdigest(),
            provider_assessment=dict(provider_assessment) if provider_assessment is not None else None,
            override=dict(override) if override is not None else None,
        )
        if (
            raw.get("analysis_hash") != restored.analysis_hash
            or json.loads(_canonical(raw)) != json.loads(_canonical(restored.as_dict()))
        ):
            raise invalid()
        reports.append(restored)

    expected_hash = _hash(
        {
            "version": SEGMENTATION_VERSION,
            "coverage_ratio": 1.0,
            "reports": [report.analysis_hash for report in reports],
            "source_asset_ids": [item.source_asset_id for item in ordered],
        }
    )
    restored_analysis_hash = expected_hash
    if cached.get("analysis_hash") != expected_hash:
        prior_report_hashes: list[str] = []
        has_review_override = False
        for raw, report in zip(reports_raw, reports, strict=True):
            override = raw.get("override")
            if override is None:
                prior_report_hashes.append(report.analysis_hash)
                continue
            if (
                not isinstance(override, Mapping)
                or override.get("provenance") != "review_only_auto_override"
                or not isinstance(override.get("prior_analysis_hash"), str)
                or len(override["prior_analysis_hash"]) != 64
                or any(character not in "0123456789abcdef" for character in override["prior_analysis_hash"].lower())
            ):
                raise invalid()
            prior_report_hashes.append(override["prior_analysis_hash"])
            has_review_override = True
        if not has_review_override:
            raise invalid()
        prior_hash = _hash(
            {
                "version": SEGMENTATION_VERSION,
                "coverage_ratio": 1.0,
                "reports": prior_report_hashes,
                "source_asset_ids": [item.source_asset_id for item in ordered],
            }
        )
        restored_analysis_hash = _hash(
            {
                "version": SEGMENTATION_VERSION,
                "prior_analysis_hash": prior_hash,
                "reports": [report.analysis_hash for report in reports],
                "provenance": "review_only_auto_override",
            }
        )
        if cached.get("analysis_hash") != restored_analysis_hash:
            raise invalid()
    restored = SourceSegmentationResult(
        ordered_inputs=ordered,
        reports=tuple(reports),
        status="RECONCILED",
        coverage_ratio=1.0,
        analysis_hash=restored_analysis_hash,
    )
    if json.loads(_canonical(restored.as_dict())) != json.loads(_canonical(cached)):
        raise invalid()
    return restored


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


def apply_review_only_auto_override(
    result: StripSegmentationResult,
    *,
    actor_id: str,
    reason: str,
    confidence_floor: float = 0.80,
    min_segment_px: int = 200,
    max_parts: int = 12,
) -> StripSegmentationResult:
    """Keep only provider-confirmed cuts for an explicit silent review run.

    This is intentionally narrower than :func:`apply_manual_override`: it is
    available only to the review-preview workflow, never to normal/final
    rendering. Ambiguous artwork remains contiguous when no provider-approved
    cut is safe. A protected boundary, malformed assessment, or lineage
    mismatch stays fail-closed instead of being relabeled as reconciled.
    """

    if not actor_id.strip() or not reason.strip():
        raise StripSegmentationError("segmentation.review_auto_override_unsafe", reviewable=False)
    if result.status != "NEEDS_REVIEW" or result.review_code not in {
        "segmentation.ambiguous_boundary",
        "segmentation.protected_boundary",
    }:
        raise StripSegmentationError("segmentation.review_auto_override_unsafe", reviewable=True)
    if (
        isinstance(confidence_floor, bool)
        or not isinstance(confidence_floor, (int, float))
        or not isfinite(float(confidence_floor))
        or not 0.0 <= float(confidence_floor) <= 1.0
        or isinstance(min_segment_px, bool)
        or not isinstance(min_segment_px, int)
        or min_segment_px <= 0
        or isinstance(max_parts, bool)
        or not isinstance(max_parts, int)
        or max_parts < 1
    ):
        raise StripSegmentationError("segmentation.review_auto_override_unsafe", reviewable=False)

    assessment = result.provider_assessment
    if not isinstance(assessment, Mapping) or assessment.get("source_asset_id") != result.source_asset_id:
        raise StripSegmentationError("segmentation.review_auto_override_unsafe", reviewable=True)
    raw_boundaries = assessment.get("boundaries")
    if not isinstance(raw_boundaries, list):
        raise StripSegmentationError("segmentation.review_auto_override_unsafe", reviewable=True)

    candidate_positions = {candidate.position for candidate in result.candidates}
    confirmed: list[tuple[int, float]] = []
    protected_positions: list[int] = []
    for raw in raw_boundaries:
        if not isinstance(raw, Mapping):
            raise StripSegmentationError("segmentation.review_auto_override_unsafe", reviewable=True)
        y = raw.get("y")
        confidence = raw.get("confidence")
        boundary_reason = str(raw.get("reason", "")).strip()
        protected_regions = raw.get("protected_regions", [])
        if (
            isinstance(y, bool)
            or not isinstance(y, int)
            or y <= 0
            or y >= result.height
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not isfinite(float(confidence))
            or not boundary_reason
            or not isinstance(protected_regions, list)
        ):
            raise StripSegmentationError("segmentation.review_auto_override_unsafe", reviewable=True)
        if y not in candidate_positions:
            raise StripSegmentationError("segmentation.review_auto_override_unsafe", reviewable=True)
        protected_boundary = boundary_reason == "segmentation.protected_boundary"
        for protected in protected_regions:
            if not isinstance(protected, Mapping):
                raise StripSegmentationError("segmentation.review_auto_override_unsafe", reviewable=True)
            bounds = protected.get("bounds")
            if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
                raise StripSegmentationError("segmentation.review_auto_override_unsafe", reviewable=True)
            try:
                top, bottom = int(bounds[1]), int(bounds[3])
            except (TypeError, ValueError):
                raise StripSegmentationError("segmentation.review_auto_override_unsafe", reviewable=True) from None
            if top < y < bottom:
                protected_boundary = True
        if protected_boundary:
            protected_positions.append(y)
        elif raw.get("accepted") is True and float(confidence) >= float(confidence_floor):
            confirmed.append((y, float(confidence)))

    selected: list[int] = []
    for y, _confidence in sorted(confirmed, key=lambda item: (-item[1], item[0])):
        if len(selected) >= max_parts - 1:
            break
        if any(abs(y - existing) < min_segment_px for existing in selected):
            continue
        if y < min_segment_px or result.height - y < min_segment_px:
            continue
        selected.append(y)
    selected.sort()
    boundaries = (0, *selected, result.height)
    spans = tuple((left, right) for left, right in zip(boundaries, boundaries[1:], strict=False))
    if any(right - left < min_segment_px for left, right in spans):
        raise StripSegmentationError("segmentation.review_auto_override_unsafe", reviewable=True)
    override = {
        "actor_id": actor_id,
        "reason": reason,
        "provenance": "review_only_auto_override",
        "prior_analysis_hash": result.analysis_hash,
        "provider_confirmed_cuts": list(selected),
        "protected_boundaries_retained_contiguous": sorted(set(protected_positions)),
        "confidence_floor": float(confidence_floor),
        "ambiguous_boundaries_remain_contiguous": True,
    }
    return _make_result(
        source_asset_id=result.source_asset_id,
        source_checksum=result.source_checksum,
        width=result.width,
        height=result.height,
        spans=spans,
        candidates=result.candidates,
        selected_cuts=tuple(selected),
        rejected_cuts=result.rejected_cuts,
        status="RECONCILED",
        review_code="",
        detector_version=result.detector_version,
        payload_sha256=result.payload_sha256,
        provider_assessment=dict(assessment),
        override=override,
    )


def apply_review_only_overrides(
    result: SourceSegmentationResult,
    *,
    actor_id: str,
    reason: str,
    confidence_floor: float = 0.80,
) -> SourceSegmentationResult:
    """Reconcile only ambiguous reports for the opt-in silent review path."""

    reports = tuple(
        apply_review_only_auto_override(
            report,
            actor_id=actor_id,
            reason=reason,
            confidence_floor=confidence_floor,
        )
        if report.status == "NEEDS_REVIEW"
        and report.review_code in {"segmentation.ambiguous_boundary", "segmentation.protected_boundary"}
        else report
        for report in result.reports
    )
    status = (
        "RECONCILED"
        if result.coverage_ratio == 1.0 and all(report.status == "RECONCILED" for report in reports)
        else "NEEDS_REVIEW"
    )
    return SourceSegmentationResult(
        ordered_inputs=result.ordered_inputs,
        reports=reports,
        status=status,
        coverage_ratio=result.coverage_ratio,
        analysis_hash=_hash(
            {
                "version": SEGMENTATION_VERSION,
                "prior_analysis_hash": result.analysis_hash,
                "reports": [report.analysis_hash for report in reports],
                "provenance": "review_only_auto_override",
            }
        ),
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
    "apply_review_only_auto_override",
    "apply_review_only_overrides",
    "reconcile_sources",
    "reconcile_strip",
    "restore_cached_reconciliation",
    "write_review_artifact",
]
