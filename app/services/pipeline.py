"""Pipeline orchestration (PRD sections 6 and 10).

Each function here is one stage of the journey and is safe to re-run: stages
replace their own outputs rather than appending, so a user can regenerate the
script, the voice-over, or the timeline independently without corrupting the
others.

Stage order:

    ingest -> analyse -> script -> approve -> voice -> timeline
           -> subtitles -> quality -> render -> publish
"""

# ruff: noqa: F401 -- facade exports stage runtime dependencies intentionally.
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import secrets
import sys
import time
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from copy import copy as shallow_copy
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

try:  # resource is POSIX-only; Windows still runs the pipeline without RSS data.
    import resource
except ImportError:  # pragma: no cover - exercised by native Windows
    class _ResourceCompat:
        RUSAGE_SELF = 0

        @staticmethod
        def getrusage(_kind: int) -> SimpleNamespace:
            return SimpleNamespace(ru_maxrss=0)

    resource = _ResourceCompat()

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.constants import (
    MAX_SUBTITLE_CHARS_PER_LINE,
    STANDARD_FINAL_DURATION_MAX_SECONDS,
    STANDARD_FINAL_DURATION_MIN_SECONDS,
    JobStatus,
    ProjectStatus,
    ScriptSection,
)
from app.models import (
    AudioSegment,
    AuditLog,
    PanelRegion,
    Project,
    QCHistorySnapshot,
    QCOverrideEvent,
    QualityCheck,
    RenderJob,
    ScriptVersion,
    SourceAsset,
    StoryAnalysis,
    SubtitleCue,
    TimelineScene,
)
from app.services import analysis as analysis_svc
from app.services import (
    analyzer_contract,
    editorial_qc,
    editorial_timing,
    framing_analysis,
    narrative_identity,
    pipeline_stages,
    reference_profile,
    reference_visual_review,
    review_source_upscale,
    segmentation,
    storage,
    subtitle_karaoke,
    visual_scoring,
)
from app.services import director as director_svc
from app.services import policy as policy_svc
from app.services import quality as quality_svc
from app.services import resolver as resolver_svc
from app.services import script as script_svc
from app.services import (
    thumbnail as thumbnail_svc,
)
from app.services import timeline as timeline_svc
from app.services import tts as tts_svc
from app.services.vision_adapter import (
    VisionCapabilityError,
    VisionChapterSynthesisRequest,
    VisionObservationRequest,
    VisionProviderRequestFailed,
    VisionResponseInvalid,
    validate_visual_evidence_observation,
)


class PipelineError(RuntimeError):
    """Raised when a stage cannot proceed. Message is user-facing."""


_REVIEW_FAILURE_CODE_PATTERN = re.compile(
    r"\b(?:cloud|visual|reference|review|subtitle|render|ffmpeg|encoder|quality|audio|timeline|media)\.[a-z0-9_.-]+\b"
)


def _review_failure_code(exc: BaseException) -> str:
    """Preserve a stable nested code without exposing error text."""

    explicit = str(getattr(exc, "code", "") or "").strip()
    if explicit and explicit != "review.preview_failed":
        return explicit
    match = _REVIEW_FAILURE_CODE_PATTERN.search(str(exc))
    return match.group(0) if match else "review.preview_failed"


def _now() -> datetime:
    return datetime.now(UTC)


def audit(
    db: Session,
    action: str,
    entity_type: str,
    entity_id: str,
    actor_id: str = "",
    **detail,
) -> None:
    """Append an audit entry. Never records secrets."""
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail,
        )
    )


def get_project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise PipelineError(f"project {project_id} not found")
    return project


def project_assets(db: Session, project_id: str) -> list[SourceAsset]:
    return list(
        db.scalars(
            select(SourceAsset)
            .where(SourceAsset.project_id == project_id)
            .order_by(SourceAsset.order_index, SourceAsset.created_at)
        )
    )


def text_sources(assets: list[SourceAsset]) -> list[tuple[int, str]]:
    """Numbered text sources for the analyzer, indexed by asset position."""
    return [(i, a.extracted_text) for i, a in enumerate(assets) if a.extracted_text.strip()]


def image_assets(assets: list[SourceAsset]) -> list[SourceAsset]:
    from app.constants import AssetType

    # Do not drop blank/gutter slices here. A sliced strip still needs every
    # piece for source-lineage coverage; the segmentation stage classifies
    # gutters (region_class "verified_gutter") so they never become panels.
    return [a for a in assets if a.type == AssetType.IMAGE]


# --- stage: analyse --------------------------------------------------------


def run_legacy_text_analysis(db: Session, project_id: str, actor_id: str='') -> StoryAnalysis:
    """Extract story facts from all text assets, replacing any prior analysis."""
    return pipeline_stages.analysis.run_legacy_text_analysis(sys.modules[__name__], db, project_id, actor_id)


_VISION_OBSERVATION_KEYS = frozenset(
    {
        "panel_id",
        "visible_facts",
        "dialogue_or_ocr",
        "inferences",
        "uncertainties",
        "entities",
        "state_changes",
        "causal_links",
        "evidence_refs",
    }
)
_VISION_BLOCKING_CODES = frozenset(
    {
        "vision_capability_missing",
        "vision_provider_unsupported",
        "coverage_incomplete",
        "analysis_observation_missing",
        "analysis_chunk_link_missing",
        "analysis_claim_evidence_missing",
        "analysis_incomplete",
        "analyzer_contract_invalid",
        "vision_provider_request_failed",
        "vision_response_invalid",
    }
)


class _AnalysisBlocked(RuntimeError):
    """Internal fail-closed stage result; details are always safe metadata."""

    def __init__(self, code: str, **finding: Any) -> None:
        self.code = code if code in _VISION_BLOCKING_CODES else "analysis_incomplete"
        self.finding = {"code": self.code, **finding}
        super().__init__(self.code)


def _panel_region_bounds(panel: PanelRegion | Mapping[str, Any]) -> tuple[int, int, int, int]:
    if isinstance(panel, Mapping):
        raw_bounds = panel.get("bounds", panel.get("bounds_json", panel.get("region_bounds")))
    else:
        raw_bounds = getattr(panel, "bounds_json", None)
    if isinstance(raw_bounds, Mapping):
        values = (
            raw_bounds.get("x"),
            raw_bounds.get("y"),
            raw_bounds.get("width"),
            raw_bounds.get("height"),
        )
        if not all(isinstance(value, int) and not isinstance(value, bool) for value in values):
            raise ValueError("bounds must contain integer x, y, width, and height")
        x, y, width, height = values
        if width <= 0 or height <= 0 or x < 0 or y < 0:
            raise ValueError("bounds must have positive dimensions")
        return x, y, x + width, y + height
    if isinstance(raw_bounds, (tuple, list)) and len(raw_bounds) == 4:
        if not all(isinstance(value, int) and not isinstance(value, bool) for value in raw_bounds):
            raise ValueError("bounds coordinates must be integers")
        x0, y0, x1, y1 = raw_bounds
        if x1 <= x0 or y1 <= y0 or x0 < 0 or y0 < 0:
            raise ValueError("bounds must have positive dimensions")
        return x0, y0, x1, y1
    raise ValueError("bounds are required")


def build_observation_chunks(
    panel_regions: Sequence[PanelRegion],
    *,
    chunk_size: int = 12,
    overlap: int = 2,
) -> list[tuple[PanelRegion, ...]]:
    """Build deterministic ordered chunks with an exact adjacent overlap."""

    if (
        isinstance(chunk_size, bool)
        or not isinstance(chunk_size, int)
        or chunk_size <= 0
    ):
        raise ValueError("chunk_size must be positive")
    if isinstance(overlap, bool) or not isinstance(overlap, int) or overlap < 0:
        raise ValueError("overlap must be non-negative")
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    ordered = sorted(
        panel_regions,
        key=lambda panel: (
            getattr(panel, "source_order", -1),
            getattr(panel, "panel_id", ""),
        ),
    )
    seen_orders: set[int] = set()
    seen_panel_ids: set[str] = set()
    for panel in ordered:
        if not isinstance(panel, PanelRegion):
            raise ValueError("panel_regions must contain PanelRegion rows")
        source_order = panel.source_order
        panel_id = panel.panel_id
        if (
            isinstance(source_order, bool)
            or not isinstance(source_order, int)
            or source_order < 0
        ):
            raise ValueError("source_order must be a non-negative integer")
        if source_order in seen_orders:
            raise ValueError("source_order must be unique")
        if not isinstance(panel_id, str) or not panel_id.strip():
            raise ValueError("panel_id must be non-empty")
        if panel_id in seen_panel_ids:
            raise ValueError("panel_id must be unique")
        if panel.region_class != "canonical_panel":
            raise ValueError("only canonical_panel regions may be observed")
        if not isinstance(panel.source_asset_id, str) or not panel.source_asset_id.strip():
            raise ValueError("source_asset_id must be non-empty")
        _panel_region_bounds(panel)
        seen_orders.add(source_order)
        seen_panel_ids.add(panel_id)

    if not ordered:
        return []
    if len(ordered) <= chunk_size:
        return [tuple(ordered)]

    step = chunk_size - overlap
    chunks: list[tuple[PanelRegion, ...]] = []
    start = 0
    while start < len(ordered):
        end = min(start + chunk_size, len(ordered))
        chunk = tuple(ordered[start:end])
        if chunks and chunk == chunks[-1]:
            break
        chunks.append(chunk)
        if end == len(ordered):
            break
        next_start = start + step
        if next_start <= start:
            raise ValueError("chunk planner did not advance")
        start = next_start
    return chunks


def _deduplicate_codes(codes: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for code in codes:
        if code not in seen:
            result.append(code)
            seen.add(code)
    return result


def _persist_blocked_analysis(
    db: Session,
    project: Project,
    row: StoryAnalysis,
    codes: Sequence[str],
    findings: Sequence[Mapping[str, Any]],
) -> StoryAnalysis:
    safe_codes = _deduplicate_codes(
        [code if code in _VISION_BLOCKING_CODES else "analysis_incomplete" for code in codes]
    )
    safe_findings = [
        {str(key): value for key, value in finding.items() if key in {"code", "stage", "count", "error_count", "panel_count", "chunk_index", "coverage_map_hash", "provider_type", "provider_name", "model"}}
        for finding in findings
        if isinstance(finding, Mapping)
    ]
    row.state = "BLOCKED"
    row.blocking_reasons_json = {"codes": safe_codes, "findings": safe_findings}
    project.status = ProjectStatus.FAILED
    audit(
        db,
        "analysis.blocked",
        "project",
        project.id,
        detail_codes=safe_codes,
        state=row.state,
        panel_count=int(row.coverage_manifest_json.get("total_canonical_panels", 0))
        if isinstance(row.coverage_manifest_json, Mapping)
        else 0,
    )
    db.flush()
    return row


def _asset_source_bounds(asset: SourceAsset) -> tuple[int, int, int, int]:
    raw_bounds = asset.source_bounds_json
    if isinstance(raw_bounds, Mapping) and all(
        key in raw_bounds for key in ("x", "y", "width", "height")
    ):
        x, y, width, height = (
            raw_bounds["x"],
            raw_bounds["y"],
            raw_bounds["width"],
            raw_bounds["height"],
        )
    else:
        x, y = 0, 0
        width, height = asset.width, asset.height
        if width <= 0 or height <= 0:
            width, height = asset.original_width, asset.original_height
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in (x, y, width, height)):
        raise ValueError("source bounds are not integer lineage")
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ValueError("source bounds are not positive")
    return x, y, x + width, y + height


def _review_reference_source_path(
    asset: SourceAsset,
    *,
    source_root: Path | None,
) -> Path:
    """Resolve the full original strip for an explicit silent review.

    Segmented ``SourceAsset`` rows point at cropped storage bytes, while their
    persisted bounds remain global to the original strip. Review mode may
    supply the immutable input directory so checksum, dimensions, and panel
    bounds all refer to the same source coordinate space. Without that
    directory only a byte-identical, full-dimension stored source is accepted.
    """
    source_checksum = str(
        getattr(asset, "original_checksum", "")
        or getattr(asset, "checksum", "")
        or ""
    )
    source_dimensions = (
        int(getattr(asset, "original_width", 0) or getattr(asset, "width", 0) or 0),
        int(getattr(asset, "original_height", 0) or getattr(asset, "height", 0) or 0),
    )
    if not source_checksum or min(source_dimensions) <= 0:
        raise PipelineError(
            "visual.panel_lineage_unavailable: original source lineage is incomplete"
        )
    if source_root is not None:
        try:
            return review_source_upscale.resolve_original_source_path(
                source_root,
                source_checksum=source_checksum,
                source_dimensions=source_dimensions,
            )
        except review_source_upscale.ReviewSourceUpscaleError as exc:
            # A resumed silent review may retain only its output directory,
            # not the original input folder.  Storage is a safe fallback only
            # for a missing/invalid root: the no-root resolver below still
            # requires byte-identical content and decoded dimensions.  A
            # checksum/geometry/lineage failure remains fail-closed.
            if exc.code not in {
                "review.upscale_source_root_invalid",
                "review.upscale_source_missing",
            }:
                raise PipelineError(f"{exc.code}: {exc}") from exc
    path = storage.path_for(asset.storage_key)
    if not path.is_file() or asset.checksum != source_checksum:
        raise PipelineError(
            "visual.panel_lineage_unavailable: original source bytes are unavailable"
        )
    try:
        with Image.open(path) as source:
            if tuple(source.size) != source_dimensions:
                raise PipelineError(
                    "visual.panel_lineage_unavailable: stored source is a segmented crop"
                )
    except PipelineError:
        raise
    except (OSError, UnidentifiedImageError, ValueError):
        raise PipelineError(
            "visual.panel_lineage_unavailable: original source cannot be decoded"
        ) from None
    return path


def _load_review_panel_source(
    asset: SourceAsset,
    region: PanelRegion,
    *,
    source_root: Path | None,
    allow_persisted_panel_crop_fallback: bool,
    resolved_source_path: Path | None = None,
) -> tuple[
    Image.Image,
    tuple[int, int],
    tuple[int, int, int, int],
    str,
    Path | None,
]:
    """Load an exact review source, with an explicit crop-only fallback.

    The fallback is limited to silent review. It is admitted only when the
    full original resolver reports missing bytes and the stored asset bytes
    match their own checksum and the persisted panel geometry exactly. It
    never rewrites the original source checksum and is recorded in the
    source-upscale manifest for downstream QC.
    """
    bounds = _panel_region_bounds(region)
    try:
        source_path = resolved_source_path or _review_reference_source_path(
            asset, source_root=source_root
        )
        with Image.open(source_path) as source:
            source.load()
            image = source.convert("RGB")
        return (
            image,
            tuple(int(value) for value in image.size),
            bounds,
            review_source_upscale.ORIGINAL_SOURCE_MATERIALIZATION,
            source_path,
        )
    except PipelineError as exc:
        if not allow_persisted_panel_crop_fallback or not any(
            str(exc).startswith(prefix)
            for prefix in (
                "review.upscale_source_missing:",
                "review.upscale_source_root_invalid:",
            )
        ):
            raise
        # A resumed review may only know its ignored output directory rather
        # than the original input folder.  If storage still contains the
        # byte-identical full source, use it before attempting the crop-only
        # fallback.  The resolver rechecks checksum and decoded dimensions;
        # no segmented crop is promoted to original-source lineage.
        try:
            source_path = _review_reference_source_path(asset, source_root=None)
            with Image.open(source_path) as source:
                source.load()
                image = source.convert("RGB")
            return (
                image,
                tuple(int(value) for value in image.size),
                bounds,
                review_source_upscale.ORIGINAL_SOURCE_MATERIALIZATION,
                source_path,
            )
        except PipelineError:
            pass
    raw = storage.read_bytes(asset.storage_key)
    try:
        image, local_bounds = review_source_upscale.resolve_persisted_panel_crop(
            raw,
            asset_checksum=str(getattr(asset, "checksum", "") or ""),
            panel_bounds=bounds,
        )
    except review_source_upscale.ReviewSourceUpscaleError:
        raise
    return (
        image,
        tuple(int(value) for value in image.size),
        local_bounds,
        review_source_upscale.PERSISTED_PANEL_CROP_MATERIALIZATION,
        None,
    )


def _build_source_inputs(
    assets: Sequence[SourceAsset],
) -> tuple[tuple[segmentation.SourceAssetInput, ...], dict[str, SourceAsset]]:
    inputs: list[segmentation.SourceAssetInput] = []
    asset_by_id: dict[str, SourceAsset] = {}
    for asset in assets:
        source_bounds = _asset_source_bounds(asset)
        original_width = asset.original_width or (source_bounds[2] - source_bounds[0])
        original_height = asset.original_height or (source_bounds[3] - source_bounds[1])
        decoded_width = asset.width or (source_bounds[2] - source_bounds[0])
        decoded_height = asset.height or (source_bounds[3] - source_bounds[1])
        if original_width <= 0 or original_height <= 0:
            raise ValueError("original dimensions are not positive")
        payload = storage.read_bytes(asset.storage_key)
        inputs.append(
            segmentation.SourceAssetInput(
                source_asset_id=asset.id,
                original_checksum=asset.original_checksum or asset.checksum or asset.id,
                original_width=original_width,
                original_height=original_height,
                source_bounds=source_bounds,
                strip_order=asset.strip_order,
                region_order=asset.region_order,
                payload=payload,
                decoded_width=decoded_width,
                decoded_height=decoded_height,
                source_family=str(asset.source_family or ""),
            )
        )
        asset_by_id[asset.id] = asset
    return tuple(inputs), asset_by_id


def _coverage_manifest(
    inputs: Sequence[segmentation.SourceAssetInput],
    coverage: segmentation.CoverageMap,
    *,
    processed_panels: int = 0,
    duplicate_observations: int = 0,
    claim_to_panel_refs: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    full_lineages: dict[tuple[str, int, int], int] = {}
    for item in inputs:
        key = (item.original_checksum or item.source_asset_id, item.original_width, item.original_height)
        full_lineages[key] = item.original_width * item.original_height
    original_area = sum(full_lineages.values())
    accounted_area = coverage.canonical_panel_area + coverage.verified_gutter_area
    tile_ranges = [
        {
            "source_asset_id": tile.source_asset_id,
            "tile_index": tile.tile_index,
            "y0": tile.y0,
            "y1": tile.y1,
            "overlap_above": tile.overlap_above,
            "overlap_below": tile.overlap_below,
            "tile_sha256": tile.tile_sha256,
        }
        for tile in coverage.tiles
    ]
    panel_ids = [
        region.region_id
        for region in coverage.regions
        if region.region_class == "canonical_panel"
    ]
    return {
        "total_assets": len(inputs),
        "original_source_space_area": original_area,
        "accounted_source_space_area": accounted_area,
        "source_content_coverage_ratio": coverage.source_content_coverage_ratio,
        "unresolved_material_area": coverage.unresolved_material_area,
        "material_unresolved_regions": [
            region.region_id
            for region in coverage.regions
            if region.region_class == "unresolved_material"
        ],
        "reconciliation_complete": not coverage.reconciliation_errors
        and coverage.source_content_coverage_ratio == 1.0
        and coverage.unresolved_material_area == 0,
        "total_panels": coverage.panel_count,
        "total_canonical_panels": coverage.panel_count,
        "persisted_canonical_panels": coverage.panel_count,
        "processed_panels": processed_panels,
        "duplicate_overlap_observations": duplicate_observations,
        "panel_ids": panel_ids,
        "unreadable_low_confidence_panels": [
            region.region_id
            for region in coverage.regions
            if region.region_class == "canonical_panel" and region.confidence < 0.5
        ],
        "ordering_uncertainties": [],
        "character_ambiguities": [],
        "tile_ranges": tile_ranges,
        "tile_overlap": [
            {
                "source_asset_id": tile.source_asset_id,
                "tile_index": tile.tile_index,
                "overlap_above": tile.overlap_above,
                "overlap_below": tile.overlap_below,
            }
            for tile in coverage.tiles
        ],
        "coverage_map_hash": coverage.map_sha256,
        "coverage_map_version": coverage.version,
        "claim_to_panel_refs": {
            claim_id: list(panel_ids)
            for claim_id, panel_ids in (claim_to_panel_refs or {}).items()
        },
    }


def _coverage_overviews(
    inputs: Sequence[segmentation.SourceAssetInput],
    coverage: segmentation.CoverageMap,
) -> dict[str, Any]:
    return {
        item.source_asset_id: {
            "bounds": list(item.source_bounds),
            "bands": [
                {
                    "bounds": list(region.bounds),
                    "region_class": region.region_class,
                }
                for region in coverage.regions
                if region.source_asset_id == item.source_asset_id
            ],
        }
        for item in inputs
    }


def _persist_panel_regions(
    db: Session,
    row: StoryAnalysis,
    coverage: segmentation.CoverageMap,
    asset_by_id: Mapping[str, SourceAsset],
) -> list[PanelRegion]:
    panel_rows: list[PanelRegion] = []
    for region in coverage.regions:
        if region.region_class != "canonical_panel":
            continue
        asset = asset_by_id.get(region.source_asset_id)
        if asset is None:
            raise _AnalysisBlocked("coverage_incomplete", stage="panel_persistence")
        panel_id = region.region_id
        panel_rows.append(
            PanelRegion(
                id=panel_id,
                story_analysis_id=row.id,
                source_asset_id=asset.id,
                source_asset_checksum=asset.original_checksum or asset.checksum,
                original_width=asset.original_width or asset.width,
                original_height=asset.original_height or asset.height,
                strip_region_id=panel_id,
                panel_id=panel_id,
                source_order=region.source_order,
                bounds_json={
                    "x": region.bounds[0],
                    "y": region.bounds[1],
                    "width": region.bounds[2] - region.bounds[0],
                    "height": region.bounds[3] - region.bounds[1],
                },
                region_class=region.region_class,
                segmentation_confidence=region.confidence,
                segmentation_version=coverage.version,
                coverage_map_hash=coverage.map_sha256,
            )
        )
    db.add_all(panel_rows)
    db.flush()
    return panel_rows


def _encode_panel_payload(
    panel: PanelRegion,
    source_input: segmentation.SourceAssetInput,
) -> bytes:
    global_bounds = _panel_region_bounds(panel)
    source_bounds = source_input.source_bounds
    local_bounds = (
        global_bounds[0] - source_bounds[0],
        global_bounds[1] - source_bounds[1],
        global_bounds[2] - source_bounds[0],
        global_bounds[3] - source_bounds[1],
    )
    decoded_width = source_input.decoded_width or (source_bounds[2] - source_bounds[0])
    decoded_height = source_input.decoded_height or (source_bounds[3] - source_bounds[1])
    if (
        local_bounds[0] < 0
        or local_bounds[1] < 0
        or local_bounds[2] > decoded_width
        or local_bounds[3] > decoded_height
        or local_bounds[2] <= local_bounds[0]
        or local_bounds[3] <= local_bounds[1]
    ):
        raise _AnalysisBlocked("coverage_incomplete", stage="panel_crop")
    try:
        with Image.open(io.BytesIO(source_input.payload)) as image:
            image.load()
            if image.size != (decoded_width, decoded_height):
                raise _AnalysisBlocked("coverage_incomplete", stage="panel_decode")
            cropped = image.convert("RGB").crop(local_bounds)
            expected_size = (
                global_bounds[2] - global_bounds[0],
                global_bounds[3] - global_bounds[1],
            )
            if cropped.size != expected_size:
                raise _AnalysisBlocked("coverage_incomplete", stage="panel_crop")
            output = io.BytesIO()
            cropped.save(output, format="PNG")
            payload = output.getvalue()
    except _AnalysisBlocked:
        raise
    except (OSError, UnidentifiedImageError, ValueError):
        raise _AnalysisBlocked("coverage_incomplete", stage="panel_decode") from None
    if not payload:
        raise _AnalysisBlocked("coverage_incomplete", stage="panel_crop")
    return payload


def _panel_transport(
    panel: PanelRegion,
    source_input: segmentation.SourceAssetInput,
    coverage: segmentation.CoverageMap,
) -> dict[str, Any]:
    bounds = _panel_region_bounds(panel)
    return {
        "panel_id": panel.panel_id,
        "source_asset_id": panel.source_asset_id,
        "strip_region_id": panel.strip_region_id,
        "source_order": panel.source_order,
        "region_bounds": {
            "x": bounds[0],
            "y": bounds[1],
            "width": bounds[2] - bounds[0],
            "height": bounds[3] - bounds[1],
        },
        "coverage_map_version": coverage.version,
        "coverage_map_hash": coverage.map_sha256,
        "mime_type": "image/png",
        "payload": _encode_panel_payload(panel, source_input),
    }


def _validate_observation_rows(
    value: Any,
    expected_panel_ids: Sequence[str],
    *,
    expected_panels: Mapping[str, Mapping[str, Any]] | None = None,
    require_visual_evidence: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != len(expected_panel_ids):
        raise _AnalysisBlocked("analysis_observation_missing", stage="observation_reconcile")
    rows: list[dict[str, Any]] = []
    expected_set = set(expected_panel_ids)
    expected_keys = _VISION_OBSERVATION_KEYS | (
        {"visual_evidence"} if require_visual_evidence else set()
    )
    for index, raw_row in enumerate(value):
        if not isinstance(raw_row, Mapping) or set(raw_row) != expected_keys:
            raise _AnalysisBlocked("analysis_observation_missing", stage="observation_reconcile")
        row = dict(raw_row)
        panel_id = row.get("panel_id")
        if panel_id != expected_panel_ids[index] or panel_id not in expected_set:
            raise _AnalysisBlocked("analysis_observation_missing", stage="observation_reconcile")
        for key in _VISION_OBSERVATION_KEYS - {"panel_id"}:
            if not isinstance(row.get(key), list):
                raise _AnalysisBlocked("vision_response_invalid", stage="observation_reconcile")
        refs = row["evidence_refs"]
        if not refs or panel_id not in refs or any(ref not in expected_set for ref in refs):
            raise _AnalysisBlocked("analysis_observation_missing", stage="observation_reconcile")
        if require_visual_evidence:
            panel = (expected_panels or {}).get(panel_id)
            if panel is None:
                raise _AnalysisBlocked("vision_response_invalid", stage="visual_lineage")
            try:
                row["visual_evidence"] = dict(
                    validate_visual_evidence_observation(
                        row["visual_evidence"],
                        expected_panel_id=panel_id,
                        expected_source_asset_id=str(panel["source_asset_id"]),
                        expected_source_order=int(panel["source_order"]),
                    )
                )
            except VisionResponseInvalid:
                raise _AnalysisBlocked("vision_response_invalid", stage="visual_evidence") from None
        rows.append(row)
    return rows


def _observe_chunks(
    provider: Any,
    chunks: Sequence[Sequence[PanelRegion]],
    panel_transports: Mapping[str, Mapping[str, Any]],
    *,
    analysis_run_id: str,
    instruction_version: str,
    instruction_sha256: str,
    visual_instruction_version: str | None = None,
    visual_instruction_sha256: str | None = None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    if (visual_instruction_version is None) != (visual_instruction_sha256 is None):
        raise _AnalysisBlocked("analyzer_contract_invalid", stage="visual_instruction")
    require_visual_evidence = visual_instruction_version is not None
    unique: dict[str, dict[str, Any]] = {}
    last_seen: dict[str, int] = {}
    first_chunk: dict[str, int] = {}
    ledger: list[dict[str, Any]] = []
    for chunk_index, chunk in enumerate(chunks):
        panel_ids = tuple(panel.panel_id for panel in chunk)
        request = VisionObservationRequest(
            analysis_run_id=analysis_run_id,
            instruction_version=instruction_version,
            instruction_sha256=instruction_sha256,
            chunk_index=chunk_index,
            panels=tuple(panel_transports[panel_id] for panel_id in panel_ids),
            visual_instruction_version=visual_instruction_version,
            visual_instruction_sha256=visual_instruction_sha256,
        )
        try:
            response = provider.observe(request)
        except VisionResponseInvalid:
            raise _AnalysisBlocked("vision_response_invalid", stage="observation_provider") from None
        except VisionProviderRequestFailed:
            raise _AnalysisBlocked("vision_provider_request_failed", stage="observation_provider") from None
        except VisionCapabilityError:
            raise _AnalysisBlocked("vision_provider_request_failed", stage="observation_provider") from None
        except Exception:
            raise _AnalysisBlocked("vision_provider_request_failed", stage="observation_provider") from None
        rows = _validate_observation_rows(
            response,
            panel_ids,
            expected_panels={panel_id: panel_transports[panel_id] for panel_id in panel_ids},
            require_visual_evidence=require_visual_evidence,
        )
        for row in rows:
            panel_id = row["panel_id"]
            if panel_id in unique:
                if last_seen[panel_id] + 1 != chunk_index or unique[panel_id] != row:
                    raise _AnalysisBlocked("analysis_observation_missing", stage="observation_overlap")
            else:
                unique[panel_id] = row
                first_chunk[panel_id] = chunk_index
            last_seen[panel_id] = chunk_index
        previous_ids = tuple(panel.panel_id for panel in chunks[chunk_index - 1]) if chunk_index else ()
        next_ids = tuple(panel.panel_id for panel in chunks[chunk_index + 1]) if chunk_index + 1 < len(chunks) else ()
        ledger.append(
            {
                "chunk_id": f"chunk-{chunk_index}",
                "panel_ids": list(panel_ids),
                "observation_ids": [f"observation-{panel_id}" for panel_id in panel_ids],
                "overlap_with_previous": [panel_id for panel_id in panel_ids if panel_id in previous_ids],
                "overlap_with_next": [panel_id for panel_id in panel_ids if panel_id in next_ids],
            }
        )
    expected = {panel.panel_id for chunk in chunks for panel in chunk}
    if set(unique) != expected:
        raise _AnalysisBlocked("analysis_observation_missing", stage="observation_reconcile")
    return unique, ledger, first_chunk


def _enrich_observations(
    panel_regions: Sequence[PanelRegion],
    semantic_observations: Mapping[str, Mapping[str, Any]],
    first_chunk: Mapping[str, int],
    coverage: segmentation.CoverageMap,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    enriched: dict[str, dict[str, Any]] = {}
    chain_rows: list[dict[str, Any]] = []
    for source_index, panel in enumerate(panel_regions):
        semantic = semantic_observations.get(panel.panel_id)
        if semantic is None:
            raise _AnalysisBlocked("analysis_observation_missing", stage="observation_enrich")
        bounds = _panel_region_bounds(panel)
        observation = {
            "panel_id": panel.panel_id,
            "source_asset_id": panel.source_asset_id,
            "strip_region_id": panel.strip_region_id,
            "source_index": source_index,
            "region_bounds": {
                "x": bounds[0],
                "y": bounds[1],
                "width": bounds[2] - bounds[0],
                "height": bounds[3] - bounds[1],
            },
            "coverage_map_version": coverage.version,
            "coverage_map_hash": coverage.map_sha256,
            "visible_facts": list(semantic["visible_facts"]),
            "dialogue_or_ocr": list(semantic["dialogue_or_ocr"]),
            "inferences": list(semantic["inferences"]),
            "uncertainties": list(semantic["uncertainties"]),
            "evidence_refs": list(semantic["evidence_refs"]),
        }
        semantic_observation = dict(observation)
        if "visual_evidence" in semantic:
            provider_evidence = semantic["visual_evidence"]
            if not isinstance(provider_evidence, Mapping):
                raise _AnalysisBlocked("vision_response_invalid", stage="visual_evidence")
            observation["visual_evidence"] = {
                **dict(provider_evidence),
                "contract_version": visual_scoring.VISUAL_EVIDENCE_CONTRACT_VERSION,
                "evidence_hash": "",
            }
        persisted_observation, _ = visual_scoring.ensure_panel_visual_evidence(
            observation,
            panel_id=panel.panel_id,
            source_asset_id=panel.source_asset_id,
            source_order=panel.source_order,
        )
        enriched[panel.panel_id] = semantic_observation
        panel.observation_json = persisted_observation
        panel.evidence_refs_json = list(observation["evidence_refs"])
        panel.chunk_index = first_chunk[panel.panel_id]
        chain_rows.append(
            {
                "observation_id": f"observation-{panel.panel_id}",
                "panel_id": panel.panel_id,
            }
        )
    return enriched, chain_rows


def _classify_synthesis_output(
    output: Any,
    expected_panel_ids: Sequence[str],
    expected_chunks: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    if not isinstance(output, Mapping):
        raise _AnalysisBlocked("analysis_incomplete", stage="synthesis_structure")
    required = {
        "observations",
        "continuity_ledger",
        "evidence_graph",
        "coverage_manifest",
        "narrative_outline",
        "script_passages",
    }
    if not required.issubset(output):
        raise _AnalysisBlocked("analysis_incomplete", stage="synthesis_structure")
    observations = output.get("observations")
    if isinstance(observations, list):
        observation_ids = [
            item.get("panel_id") if isinstance(item, Mapping) else None
            for item in observations
        ]
        if tuple(observation_ids) != tuple(expected_panel_ids):
            raise _AnalysisBlocked("analysis_observation_missing", stage="synthesis_observations")
    else:
        raise _AnalysisBlocked("analysis_incomplete", stage="synthesis_observations")

    continuity = output.get("continuity_ledger")
    if not isinstance(continuity, Mapping) or not isinstance(continuity.get("chunks"), list):
        raise _AnalysisBlocked("analysis_incomplete", stage="synthesis_chunks")
    declared_chunks = continuity["chunks"]
    expected_chunk_ids = [tuple(chunk["panel_ids"]) for chunk in expected_chunks]
    actual_chunk_ids: list[tuple[str, ...]] = []
    for chunk in declared_chunks:
        if not isinstance(chunk, Mapping) or not isinstance(chunk.get("panel_ids"), list):
            raise _AnalysisBlocked("analysis_chunk_link_missing", stage="synthesis_chunks")
        panel_ids = tuple(chunk["panel_ids"])
        if not panel_ids or any(panel_id not in expected_panel_ids for panel_id in panel_ids):
            raise _AnalysisBlocked("analysis_chunk_link_missing", stage="synthesis_chunks")
        actual_chunk_ids.append(panel_ids)
    if actual_chunk_ids != expected_chunk_ids:
        raise _AnalysisBlocked("analysis_chunk_link_missing", stage="synthesis_chunks")

    graph = output.get("evidence_graph")
    if not isinstance(graph, Mapping) or not isinstance(graph.get("claims"), list):
        raise _AnalysisBlocked("analysis_incomplete", stage="synthesis_claims")
    expected_set = set(expected_panel_ids)
    for claim in graph["claims"]:
        if not isinstance(claim, Mapping):
            raise _AnalysisBlocked("analysis_claim_evidence_missing", stage="synthesis_claims")
        refs = claim.get("evidence_panel_ids")
        if not isinstance(refs, list) or not refs or any(ref not in expected_set for ref in refs):
            raise _AnalysisBlocked("analysis_claim_evidence_missing", stage="synthesis_claims")
    return output


def _derive_legacy_fields(row: StoryAnalysis, output: Mapping[str, Any]) -> None:
    ledger = output["continuity_ledger"]
    row.characters = [
        {
            "name": entity.get("canonical_name", ""),
            "role": "",
            "aliases": list(entity.get("aliases", []) or []),
            "mentions": len(entity.get("panel_ids", []) or []),
            "source_index": index,
        }
        for index, entity in enumerate(ledger.get("entities", []))
        if isinstance(entity, Mapping)
    ]
    row.locations = []
    row.events = list(ledger.get("state_changes", []) or []) + list(
        ledger.get("causal_links", []) or []
    )
    spine = output["narrative_outline"]["story_spine"]
    row.main_conflict = spine.get("obstacle", "")
    row.twist = spine.get("consequence", "")
    row.cliffhanger = spine.get("unresolved_question", "")
    row.pronunciation_candidates = []
    row.low_confidence_notes = [
        uncertainty
        for observation in output.get("observations", [])
        for uncertainty in observation.get("uncertainties", [])
        if isinstance(uncertainty, str)
    ]


def run_analysis(db: Session, project_id: str, actor_id: str='', *, narrative_profile_id: str | None=None) -> StoryAnalysis:
    """Run only the complete, fail-closed vision-first analysis flow."""
    return pipeline_stages.analysis.run_analysis(sys.modules[__name__], db, project_id, actor_id, narrative_profile_id=narrative_profile_id)


def _analysis_to_result(row: StoryAnalysis) -> analysis_svc.AnalysisResult:
    """Rebuild the dataclass from a stored row so edits are respected."""
    return analysis_svc.AnalysisResult(
        characters=[
            analysis_svc.Character(
                name=c.get("name", ""),
                mentions=int(c.get("mentions", 0) or 0),
                role=c.get("role", ""),
                aliases=list(c.get("aliases", []) or []),
                source_index=int(c.get("source_index", 0) or 0),
            )
            for c in (row.characters or [])
        ],
        locations=list(row.locations or []),
        events=[
            analysis_svc.StoryEvent(
                order=int(e.get("order", i) or i),
                text=e.get("text", ""),
                kind=e.get("kind", "event"),
                source_index=int(e.get("source_index", 0) or 0),
            )
            for i, e in enumerate(row.events or [])
        ],
        main_conflict=row.main_conflict,
        twist=row.twist,
        cliffhanger=row.cliffhanger,
        pronunciation_candidates=list(row.pronunciation_candidates or []),
        low_confidence_notes=list(row.low_confidence_notes or []),
    )


def latest_script_row(db: Session, project_id: str) -> ScriptVersion | None:
    """Highest-numbered script version, queried directly.

    ``Project.scripts`` is a lazy relationship: once it has been read in a
    session it stays cached, so a script added later in the same transaction
    (as in ``generate_draft``) would be invisible. Querying avoids that.
    """
    return db.scalars(
        select(ScriptVersion)
        .where(ScriptVersion.project_id == project_id)
        .order_by(ScriptVersion.version.desc())
        .limit(1)
    ).first()


def approved_script_row(db: Session, project_id: str) -> ScriptVersion | None:
    return db.scalars(
        select(ScriptVersion)
        .where(
            ScriptVersion.project_id == project_id,
            ScriptVersion.approved_at.is_not(None),
        )
        .order_by(ScriptVersion.version.desc())
        .limit(1)
    ).first()


def _script_content_hash(script: ScriptVersion) -> str:
    """Hash the exact script payload used by downstream media stages.

    Approval metadata is deliberately excluded: recording an approval must not
    change the identity that was approved.  Conversely, changing any spoken
    section, hook choice, generator, or version produces a new identity.
    """

    payload = {
        "version": int(script.version),
        "generator": str(script.generator or ""),
        "sections": script.sections or [],
        "hook_options": script.hook_options or [],
        "selected_hook": int(script.selected_hook or 0),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _approved_adaptive_reference_policy(script: ScriptVersion | None) -> dict[str, object] | None:
    """Return the approved adaptive review pacing contract, if this exact script owns one."""
    if script is None:
        return None
    raw_metadata = getattr(script, "editorial_metadata", {})
    metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
    approved_at = getattr(script, "approved_at", None)
    approved_by = str(getattr(script, "approved_by", "") or "")
    version = getattr(script, "version", None)
    if approved_at is None or not approved_by or version is None:
        return None
    if (
        metadata.get("approved_script_hash") != _script_content_hash(script)
        or metadata.get("approved_script_version") != version
    ):
        return None
    raw = metadata.get("duration_policy_contract")
    if not isinstance(raw, Mapping) or raw.get("adaptive") is not True:
        return None
    try:
        lower = float(raw["target_duration_min_s"])
        upper = float(raw["target_duration_max_s"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(lower) or not math.isfinite(upper) or lower <= 0.0 or upper < lower:
        return None
    return {**dict(raw), "target_duration_min_s": lower, "target_duration_max_s": upper}


def _script_requires_explicit_approval(script: ScriptVersion) -> bool:
    """Return whether an evidence-generated script needs operator approval.

    Evidence generators are versioned and will evolve.  Matching one literal
    version (the old ``vision_evidence_v2`` contract) silently bypasses review
    for newer evidence contracts, so the category and metadata are authoritative.
    """

    generator = str(script.generator or "").strip().casefold()
    metadata = script.editorial_metadata if isinstance(script.editorial_metadata, Mapping) else {}
    return (
        generator.startswith("vision_evidence_")
        or generator.startswith("evidence_")
        or metadata.get("approval_required") is True
        or metadata.get("evidence_generated") is True
        or metadata.get("human_review_required") is True
    )


def current_script(db: Session, project_id: str) -> ScriptVersion | None:
    """The newest script; media must never silently fall back to an older one."""
    return latest_script_row(db, project_id)


def _script_for_media(
    db: Session,
    project_id: str,
    *,
    allow_unapproved_review: bool = False,
) -> ScriptVersion:
    """Return the newest script only when its approval contract is satisfied."""
    latest = latest_script_row(db, project_id)
    if latest is None:
        raise PipelineError("generate a script first")
    if not _script_requires_explicit_approval(latest):
        return latest
    if allow_unapproved_review:
        return latest
    approved_hash = (latest.editorial_metadata or {}).get("approved_script_hash")
    expected_hash = _script_content_hash(latest)
    legacy_v2_approval = (
        str(latest.generator or "").casefold() == "vision_evidence_v2"
        and not approved_hash
        and "approved_script_version" not in (latest.editorial_metadata or {})
    )
    if (
        latest.approved_at is None
        or not latest.approved_by
        or (
            not legacy_v2_approval
            and (
                approved_hash != expected_hash
                or (latest.editorial_metadata or {}).get("approved_script_version")
                != latest.version
            )
        )
    ):
        raise PipelineError("latest evidence-backed script must be explicitly approved")
    return latest


def all_scripts(db: Session, project_id: str) -> list[ScriptVersion]:
    """Every script version, newest first (FR-04 version history)."""
    return list(
        db.scalars(
            select(ScriptVersion)
            .where(ScriptVersion.project_id == project_id)
            .order_by(ScriptVersion.version.desc())
        )
    )


def all_render_jobs(db: Session, project_id: str) -> list[RenderJob]:
    """Every render job, newest first."""
    return list(
        db.scalars(
            select(RenderJob)
            .where(RenderJob.project_id == project_id)
            .order_by(RenderJob.created_at.desc())
        )
    )


def latest_analysis(db: Session, project_id: str) -> StoryAnalysis | None:
    return db.scalars(
        select(StoryAnalysis)
        .where(StoryAnalysis.project_id == project_id)
        .order_by(StoryAnalysis.created_at.desc())
    ).first()


_VISION_SCRIPT_ROLES = (
    "hook",
    "setup",
    "escalation",
    "editorial_insight",
    "payoff_open_loop",
)
_VISION_ROLE_TO_SECTION = {
    "hook": ScriptSection.HOOK.value,
    "setup": ScriptSection.SETUP.value,
    "escalation": ScriptSection.CONFLICT.value,
    "editorial_insight": ScriptSection.TWIST.value,
    "payoff_open_loop": ScriptSection.CTA.value,
}
_SAFE_STATUS_FINDING_KEYS = frozenset(
    {
        "code",
        "stage",
        "count",
        "error_count",
        "panel_count",
        "chunk_index",
        "coverage_map_hash",
        "provider_type",
        "provider_name",
        "model",
    }
)


def _validated_persisted_vision_output(
    db: Session,
    row: StoryAnalysis,
    *,
    required_state: str | None = None,
) -> tuple[dict[str, Any], list[PanelRegion]]:
    """Reconstruct and validate provider evidence without repairing it."""

    if required_state is not None and row.state != required_state:
        raise PipelineError(f"analysis must be in {required_state} state")
    if isinstance(row.blocking_reasons_json, Mapping) and row.blocking_reasons_json.get(
        "codes"
    ):
        raise PipelineError("analysis has blocking reasons")

    profile = _narrative_identity_from_analysis(row)
    try:
        version, digest, _ = analyzer_contract.load_analyzer_instruction(
            narrative_profile_id=profile.profile_id if profile is not None else None
        )
    except analyzer_contract.AnalyzerContractError:
        raise PipelineError("current analyzer instruction is unavailable") from None
    if row.instruction_version != version or row.instruction_sha256 != digest:
        raise PipelineError("analysis uses an outdated analyzer instruction")

    manifest = row.coverage_manifest_json
    if not isinstance(manifest, Mapping):
        raise PipelineError("analysis coverage manifest is missing")
    required_manifest = (
        "total_panels",
        "processed_panels",
        "panel_ids",
        "source_content_coverage_ratio",
        "unresolved_material_area",
        "material_unresolved_regions",
        "reconciliation_complete",
        "coverage_map_version",
        "coverage_map_hash",
    )
    if any(key not in manifest for key in required_manifest):
        raise PipelineError("analysis coverage manifest is incomplete")

    panels = list(
        db.scalars(
            select(PanelRegion)
            .where(PanelRegion.story_analysis_id == row.id)
            .order_by(PanelRegion.source_order, PanelRegion.id)
        )
    )
    if not panels:
        raise PipelineError("analysis has no persisted panel evidence")
    expected_panel_ids = tuple(panel.panel_id for panel in panels)
    source_orders = [panel.source_order for panel in panels]
    if (
        len(set(expected_panel_ids)) != len(expected_panel_ids)
        or any(not panel_id for panel_id in expected_panel_ids)
        or len(set(source_orders)) != len(source_orders)
        or any(a >= b for a, b in zip(source_orders, source_orders[1:]))  # noqa: B905 - adjacent pairs, strict raises
    ):
        raise PipelineError("persisted panel evidence is not ordered")
    if manifest.get("total_panels") != len(panels) or manifest.get(
        "processed_panels"
    ) != len(panels):
        raise PipelineError("coverage panel counts are incomplete")
    if tuple(manifest.get("panel_ids", ())) != expected_panel_ids:
        raise PipelineError("coverage panel inventory is not reconciled")
    for count_key in (
        "total_canonical_panels",
        "persisted_canonical_panels",
        "processed_canonical_panel_count",
    ):
        if count_key in manifest and manifest[count_key] != len(panels):
            raise PipelineError("persisted canonical panel counts do not match")
    if (
        manifest.get("source_content_coverage_ratio") != 1.0
        or manifest.get("unresolved_material_area") != 0
        or manifest.get("material_unresolved_regions") != []
        or manifest.get("reconciliation_complete") is not True
    ):
        raise PipelineError("analysis coverage is not complete")

    reconciliation = row.reconciliation_json
    if not isinstance(reconciliation, Mapping):
        raise PipelineError("analysis reconciliation is missing")
    if reconciliation.get("chain_reconciled") is not True:
        raise PipelineError("analysis evidence chain is not reconciled")
    if (
        reconciliation.get("coverage_map_version")
        != manifest.get("coverage_map_version")
        or reconciliation.get("coverage_map_hash") != manifest.get("coverage_map_hash")
        or reconciliation.get("canonical_panel_count") != len(panels)
        or reconciliation.get("processed_panel_count") != len(panels)
    ):
        raise PipelineError("analysis reconciliation does not match coverage")

    observations: list[dict[str, Any]] = []
    for source_index, panel in enumerate(panels):
        observation = panel.observation_json
        bounds = panel.bounds_json
        if not isinstance(observation, Mapping) or not isinstance(bounds, Mapping):
            raise PipelineError("persisted panel observation is malformed")
        expected_bounds = {
            "x": bounds.get("x"),
            "y": bounds.get("y"),
            "width": bounds.get("width"),
            "height": bounds.get("height"),
        }
        if (
            observation.get("panel_id") != panel.panel_id
            or observation.get("source_asset_id") != panel.source_asset_id
            or observation.get("strip_region_id") != panel.strip_region_id
            or observation.get("source_index") != source_index
            or observation.get("region_bounds") != expected_bounds
            or observation.get("coverage_map_version")
            != manifest.get("coverage_map_version")
            or observation.get("coverage_map_hash") != manifest.get("coverage_map_hash")
            or observation.get("evidence_refs") != list(panel.evidence_refs_json or [])
        ):
            raise PipelineError("persisted panel lineage is inconsistent")
        observations.append(dict(observation))

    continuity = row.continuity_ledger_json
    graph_raw = row.evidence_graph_json
    story_spine = row.story_spine_json
    if not isinstance(continuity, Mapping) or not isinstance(graph_raw, Mapping):
        raise PipelineError("analysis evidence ledger is incomplete")
    if not isinstance(story_spine, Mapping):
        raise PipelineError("analysis story spine is missing")
    passages = graph_raw.get("script_passages")
    if not isinstance(passages, list):
        raise PipelineError("analysis script passages are missing")
    evidence_graph = dict(graph_raw)
    evidence_graph.pop("script_passages", None)
    output = {
        "observations": observations,
        "continuity_ledger": dict(continuity),
        "evidence_graph": evidence_graph,
        "coverage_manifest": dict(manifest),
        "narrative_outline": {"story_spine": dict(story_spine)},
        "script_passages": [dict(passage) for passage in passages if isinstance(passage, Mapping)],
    }
    if profile is not None:
        ending_kind = reconciliation.get("narrative_ending_kind")
        if not isinstance(ending_kind, str) or not ending_kind.strip():
            raise PipelineError("narrative_identity_invalid")
        output["narrative_outline"]["ending_kind"] = ending_kind
    validator_output = dict(output)
    validator_observations: list[dict[str, Any]] = []
    for observation, panel in zip(output["observations"], panels, strict=True):
        visual_raw = observation.get("visual_evidence")
        if visual_raw is not None:
            try:
                visual_evidence = visual_scoring.parse_panel_visual_evidence(visual_raw)
            except Exception:
                raise PipelineError("persisted visual evidence is invalid") from None
            if (
                visual_evidence.panel_id != panel.panel_id
                or visual_evidence.source_asset_id != panel.source_asset_id
                or visual_evidence.source_order != panel.source_order
            ):
                raise PipelineError("persisted visual evidence lineage is inconsistent")
        plain_observation = dict(observation)
        plain_observation.pop("visual_evidence", None)
        validator_observations.append(plain_observation)
    validator_output["observations"] = validator_observations
    try:
        analyzer_contract.validate_analyzer_output(
            validator_output,
            expected_panel_ids=expected_panel_ids,
            narrative_profile_id=profile.profile_id if profile is not None else None,
        )
    except analyzer_contract.AnalyzerContractError:
        raise PipelineError("persisted vision evidence is invalid") from None
    if len(output["script_passages"]) != len(passages):
        raise PipelineError("persisted script passages are malformed")
    return output, panels


def _narrative_identity_from_analysis(
    analysis: StoryAnalysis,
) -> narrative_identity.NarrativeIdentityProfile | None:
    """Resolve and locally re-hash the persisted opt-in narrative identity."""

    reconciliation = analysis.reconciliation_json
    if not isinstance(reconciliation, Mapping) or "narrative_identity" not in reconciliation:
        return None
    raw = reconciliation.get("narrative_identity")
    if not isinstance(raw, Mapping):
        raise PipelineError("narrative_identity_invalid")
    profile_id = raw.get("profile_id")
    version = raw.get("version")
    stored_hash = raw.get("sha256")
    if not all(isinstance(value, str) and value.strip() for value in (profile_id, version, stored_hash)):
        raise PipelineError("narrative_identity_invalid")
    try:
        profile = narrative_identity.get_narrative_identity(profile_id)
        _prompt_version, prompt_sha256, _prompt_text = (
            narrative_identity.load_narrative_instruction(profile_id)
        )
        canonical = narrative_identity.canonical_profile_contract_json(
            profile, prompt_sha256
        )
        computed_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except narrative_identity.NarrativeIdentityError:
        raise PipelineError("narrative_identity_invalid") from None
    if (
        version != profile.profile_version
        or stored_hash != computed_hash
        or computed_hash != profile.contract_sha256
    ):
        raise PipelineError("narrative_identity_invalid")
    return profile


def _requested_narrative_profile(
    analysis: StoryAnalysis,
    narrative_profile_id: str | None,
) -> narrative_identity.NarrativeIdentityProfile | None:
    persisted = _narrative_identity_from_analysis(analysis)
    if narrative_profile_id is None:
        return persisted
    try:
        requested = narrative_identity.get_narrative_identity(narrative_profile_id)
        _prompt_version, prompt_sha256, _prompt_text = (
            narrative_identity.load_narrative_instruction(narrative_profile_id)
        )
        computed_hash = hashlib.sha256(
            narrative_identity.canonical_profile_contract_json(
                requested, prompt_sha256
            ).encode("utf-8")
        ).hexdigest()
    except narrative_identity.NarrativeIdentityError:
        raise PipelineError("narrative_profile_invalid") from None
    if computed_hash != requested.contract_sha256:
        raise PipelineError("narrative_profile_invalid")
    if (
        persisted is None
        or persisted.profile_id != requested.profile_id
        or persisted.profile_version != requested.profile_version
        or persisted.contract_sha256 != requested.contract_sha256
    ):
        raise PipelineError("narrative_profile_mismatch")
    return requested


def analysis_status(db: Session, project_id: str) -> dict[str, Any] | None:
    """Return a safe scalar/count summary of the latest analysis."""
    return pipeline_stages.analysis.analysis_status(sys.modules[__name__], db, project_id)


# --- stage: script ---------------------------------------------------------


def generate_script(db: Session, project_id: str, *, analysis_id: str | None=None, keep_locked: bool=True, hook_count: int=3, seed: int | None=None, actor_id: str='', narrative_profile_id: str | None=None) -> ScriptVersion:
    """Materialize provider passages from the latest reconciled evidence."""
    return pipeline_stages.script.generate_script(sys.modules[__name__], db, project_id, analysis_id=analysis_id, keep_locked=keep_locked, hook_count=hook_count, seed=seed, actor_id=actor_id, narrative_profile_id=narrative_profile_id)


def update_script(db: Session, script_id: str, sections: list[dict], *, selected_hook: int | None=None, actor_id: str='') -> ScriptVersion:
    """Apply user edits. Editing clears approval so review cannot be bypassed."""
    return pipeline_stages.script.update_script(sys.modules[__name__], db, script_id, sections, selected_hook=selected_hook, actor_id=actor_id)


def approve_script(db: Session, script_id: str, actor_id: str='', *, editorial_review_confirmed: bool=False) -> ScriptVersion:
    """Approve only a current, explicitly confirmed evidence-backed script."""
    return pipeline_stages.script.approve_script(sys.modules[__name__], db, script_id, actor_id, editorial_review_confirmed=editorial_review_confirmed)


# --- stage: voice-over -----------------------------------------------------


def generate_voiceover(db: Session, project_id: str, *, speed: float=1.15, provider_name: str | None=None, actor_id: str='', duration_bounds_s: tuple[float, float] | None=None) -> list[AudioSegment]:
    """Synthesise one clip per script section, replacing any previous audio."""
    return pipeline_stages.media.generate_voiceover(sys.modules[__name__], db, project_id, speed=speed, provider_name=provider_name, actor_id=actor_id, duration_bounds_s=duration_bounds_s)


def audio_segments(db: Session, script_id: str) -> list[AudioSegment]:
    return list(
        db.scalars(
            select(AudioSegment)
            .where(AudioSegment.script_version_id == script_id)
            .order_by(AudioSegment.order_index)
        )
    )


def spans_from_segments(segments: list[AudioSegment]) -> list[timeline_svc.AudioSpan]:
    """Rebuild timeline spans from stored segments.

    Segment ``start_time``/``end_time`` are absolute, but ``word_timings`` are
    stored relative to their own clip (that is what the TTS provider returns).
    They must be shifted onto the master timeline here, otherwise every span's
    subtitles restart at zero and overlap each other.
    """
    spans: list[timeline_svc.AudioSpan] = []
    for s in segments:
        shifted = [
            {
                "word": t.get("word", ""),
                "start": round(float(t.get("start", 0.0)) + s.start_time, 3),
                "end": round(float(t.get("end", 0.0)) + s.start_time, 3),
            }
            for t in (s.word_timings or [])
        ]
        events = [
            {
                **event,
                "start": round(float(event.get("start", 0.0)) + s.start_time, 3),
                "end": round(float(event.get("end", 0.0)) + s.start_time, 3),
            }
            for event in (s.dramatic_events or [])
        ]
        spans.append(
            timeline_svc.AudioSpan(
                section=s.section,
                text=getattr(s, "spoken_text", "") or s.text,
                start_time=s.start_time,
                end_time=s.end_time,
                word_timings=shifted,
                dramatic_events=events,
                impact_lock=any(event.get("impact_lock") for event in events),
            )
        )
    return spans


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _panel_bounds_json(bounds: tuple[int, int, int, int]) -> dict[str, int]:
    return {
        "x": bounds[0],
        "y": bounds[1],
        "width": bounds[2] - bounds[0],
        "height": bounds[3] - bounds[1],
    }


_reference_roi_alternatives = reference_visual_review.enumerate_reference_roi_alternatives


def _build_reference_panel_fallback_candidates(
    *,
    panel_regions: Sequence[PanelRegion],
    panel_candidates_by_region_id: Mapping[str, object],
    panel_crops_by_region_id: Mapping[str, Image.Image],
    section_evidence_panel_ids: Mapping[str, Sequence[str]],
    section_citations: Mapping[str, Sequence[int]],
    beats_by_section: Mapping[str, Sequence[str]],
    profile: object,
    source_upscale_manifests_by_region_id: Mapping[str, Mapping[str, Any]] | None = None,
    allow_missing_explicit: bool = False,
    allow_conservative_full_panel: bool = False,
) -> tuple[object, ...]:
    """Compatibility wrapper for the exact panel-keyed review builder."""
    try:
        return reference_visual_review.build_reference_panel_fallback_candidates(
            panel_regions=panel_regions,
            panel_candidates_by_region_id=panel_candidates_by_region_id,
            panel_crops_by_region_id=panel_crops_by_region_id,
            allow_missing_explicit=allow_missing_explicit,
            section_evidence_panel_ids=section_evidence_panel_ids,
            section_citations=section_citations,
            beats_by_section=beats_by_section,
            profile=profile,
            source_upscale_manifests_by_region_id=source_upscale_manifests_by_region_id,
            allow_conservative_full_panel=allow_conservative_full_panel,
        )
    except reference_visual_review.ReferenceReviewError as exc:
        raise PipelineError(f"{exc.code}: {exc}") from exc


def _load_reference_panel_fallback_candidates(
    db: Session,
    project_id: str,
    script: ScriptVersion,
    images: Sequence[SourceAsset],
    profile: object,
    *,
    review_source_upscale_policy: review_source_upscale.ReviewSourceUpscalePolicy | None = None,
    section_evidence_panel_ids: Mapping[str, Sequence[str]] | None = None,
    section_citations: Mapping[str, Sequence[int]] | None = None,
    beats_by_section: Mapping[str, Sequence[str]] | None = None,
    review_source_root: Path | None = None,
    allow_persisted_panel_crop_fallback: bool = False,
    allow_conservative_full_panel: bool = False,
) -> tuple[object, ...]:
    """Read each exact persisted panel crop before planner selection."""
    analysis = latest_analysis(db, project_id)
    if analysis is None:
        return ()
    regions = list(
        db.scalars(
            select(PanelRegion)
            .where(
                PanelRegion.story_analysis_id == analysis.id,
                PanelRegion.source_order >= 0,
            )
            .order_by(PanelRegion.source_order, PanelRegion.panel_id, PanelRegion.id)
        )
    )
    # Order zero is front matter unless the persisted narration grounded
    # explicit claim evidence on it; dropping a cited panel here would make
    # the script's own evidence unresolvable at render time.
    default_evidence, default_citations, default_beats = (
        reference_visual_review.section_evidence_maps(script)
    )
    effective_evidence = section_evidence_panel_ids or default_evidence
    effective_citations = section_citations or default_citations
    cited_panel_ids = {
        str(value)
        for ids in effective_evidence.values()
        for value in (ids or ())
        if str(value).strip()
    }
    cited_source_orders = {
        int(value)
        for citations in effective_citations.values()
        for value in (citations or ())
        if isinstance(value, int) and not isinstance(value, bool)
    }
    if cited_panel_ids or cited_source_orders:
        # Eligibility is explicit and panel-keyed. Materializing unrelated
        # regions cannot change the builder result, but it needlessly decodes
        # and scores an entire chapter. Restrict the expensive source path to
        # the exact panel IDs/source orders that can be selected.
        regions = [
            region
            for region in regions
            if str(getattr(region, "panel_id", "")) in cited_panel_ids
            or int(region.source_order) in cited_source_orders
        ]
    else:
        # Preserve legacy callers that intentionally request the whole story
        # registry without explicit evidence/citations.
        regions = [region for region in regions if int(region.source_order) > 0]
    if not regions:
        return ()
    assets = {asset.id: asset for asset in images}
    panel_crops: dict[str, Image.Image] = {}
    panel_candidates: dict[str, object] = {}
    panel_regions_for_builder: list[PanelRegion] = []
    skipped_panel_ids: set[str] = set()
    source_upscale_manifests: dict[str, Mapping[str, Any]] = {}
    resolved_source_paths: dict[str, Path] = {}
    for region in regions:
        asset = assets.get(region.source_asset_id)
        if asset is None:
            raise PipelineError(
                "visual.panel_lineage_unavailable: panel source asset is unavailable"
            )
        current_checksum = str(getattr(asset, "original_checksum", "") or getattr(asset, "checksum", "") or "")
        region_checksum = str(getattr(region, "source_asset_checksum", "") or "")
        if not current_checksum or region_checksum != current_checksum:
            raise PipelineError(
                "visual.panel_lineage_unavailable: panel source checksum is stale"
            )
        try:
            if review_source_upscale_policy is None:
                source = Image.open(io.BytesIO(storage.read_bytes(asset.storage_key)))
                source.load()
                source_dimensions = tuple(int(value) for value in source.size)
                bounds = _panel_region_bounds(region)
                source_materialization = (
                    review_source_upscale.ORIGINAL_SOURCE_MATERIALIZATION
                )
            else:
                (
                    source,
                    source_dimensions,
                    bounds,
                    source_materialization,
                    source_path,
                ) = _load_review_panel_source(
                    asset,
                    region,
                    source_root=review_source_root,
                    allow_persisted_panel_crop_fallback=(
                        allow_persisted_panel_crop_fallback
                    ),
                    resolved_source_path=resolved_source_paths.get(str(asset.id)),
                )
                if source_path is not None:
                    resolved_source_paths[str(asset.id)] = source_path
            # Clamp stale/legacy panel bounds to the source asset. Regions whose
            # bounds lie completely outside the asset are corrupt and cannot be
            # framed meaningfully; skip them instead of cropping garbage text.
            src_w, src_h = source_dimensions
            if bounds[0] >= src_w or bounds[1] >= src_h or bounds[2] <= 0 or bounds[3] <= 0:
                skipped_panel_ids.add(str(region.panel_id))
                continue
            clamped = (
                max(0, bounds[0]),
                max(0, bounds[1]),
                min(src_w, bounds[2]),
                min(src_h, bounds[3]),
            )
            crop = source.convert("RGB").crop(clamped)
            source.close()
            if crop.size != (clamped[2] - clamped[0], clamped[3] - clamped[1]):
                raise ValueError("panel crop dimensions changed")
            # Keep the source-crop geometry contract identical to narrative
            # repair. Upscaling cannot turn a segmentation sliver into a
            # meaningful review visual.
            if not reference_profile.review_panel_source_geometry_is_renderable(crop.size):
                skipped_panel_ids.add(str(region.panel_id))
                continue
            prepared_crop = crop
            builder_region = region
            if review_source_upscale_policy is not None:
                prepared_crop, manifest = review_source_upscale.prepare_review_panel(
                    crop,
                    policy=review_source_upscale_policy,
                    source_asset_id=str(asset.id),
                    panel_region_id=str(region.id),
                    source_asset_checksum=current_checksum,
                    source_panel_bounds=clamped,
                    source_dimensions=source_dimensions,
                    source_materialization=source_materialization,
                )
                builder_region = shallow_copy(region)
                builder_region.bounds_json = _panel_bounds_json(
                    review_source_upscale.transform_panel_bounds(clamped, manifest)
                )
                source_upscale_manifests[str(region.id)] = manifest
            encoded = io.BytesIO()
            prepared_crop.save(encoded, format="PNG")
            panel_crops[str(region.id)] = prepared_crop.copy()
            panel_regions_for_builder.append(builder_region)
            panel_candidates[str(region.id)] = visual_scoring.analyze_panel(
                encoded.getvalue(),
                asset_id=str(asset.id),
                order_index=int(region.source_order),
                source_family=str(asset.source_family or ""),
            )
            crop.close()
            if prepared_crop is not crop:
                prepared_crop.close()
        except review_source_upscale.ReviewSourceUpscaleError as exc:
            if (
                allow_persisted_panel_crop_fallback
                and exc.code == "review.panel_crop_fallback_geometry_invalid"
            ):
                continue
            raise PipelineError(f"{exc.code}: {exc}") from exc
        except (OSError, UnidentifiedImageError, ValueError, storage.StorageError):
            # An empty exact registry still routes through the explicit planner
            # boundary, which raises visual.panel_lineage_unavailable. This keeps
            # pre-Task7 callers from receiving a source-path traceback.
            return ()
    return _build_reference_panel_fallback_candidates(
        panel_regions=panel_regions_for_builder or regions,
        panel_candidates_by_region_id=panel_candidates,
        panel_crops_by_region_id=panel_crops,
        section_evidence_panel_ids=effective_evidence,
        section_citations=effective_citations,
        beats_by_section=beats_by_section or default_beats,
        profile=profile,
        source_upscale_manifests_by_region_id=source_upscale_manifests,
        # A cited panel the loader itself skipped as render-unready (degenerate
        # bounds, sliver crop) must fall back to the section's other evidence
        # instead of failing the whole lineage as a missing explicit id.
        allow_missing_explicit=(
            review_source_upscale_policy is not None
            or bool(skipped_panel_ids & cited_panel_ids)
        ),
        allow_conservative_full_panel=allow_conservative_full_panel,
    )


def _bind_reference_panel_regions(
    db: Session,
    project_id: str,
    script: ScriptVersion,
    images: list[SourceAsset],
    planned: list[dict[str, Any]],
    *,
    candidate_registry: Mapping[str, object] | None = None,
    review_source_upscale_policy: review_source_upscale.ReviewSourceUpscalePolicy | None = None,
) -> list[dict[str, Any]]:
    """Bind every reference shot to its cited, persisted panel region."""
    if candidate_registry is not None:
        analysis = latest_analysis(db, project_id)
        if analysis is None:
            raise PipelineError("visual.panel_lineage_unavailable: no approved panel analysis")
        regions = list(
            db.scalars(
                select(PanelRegion)
                .where(PanelRegion.story_analysis_id == analysis.id)
                .order_by(PanelRegion.source_order, PanelRegion.panel_id, PanelRegion.id)
            )
        )
        if review_source_upscale_policy is not None:
            prepared_regions: list[PanelRegion] = []
            for region in regions:
                candidate = candidate_registry.get(str(region.id))
                manifest = getattr(candidate, "source_upscale_manifest", None)
                if isinstance(manifest, Mapping):
                    prepared_region = shallow_copy(region)
                    prepared_region.bounds_json = _panel_bounds_json(
                        tuple(int(value) for value in candidate.panel_bounds)
                    )
                    prepared_regions.append(prepared_region)
                else:
                    prepared_regions.append(region)
            regions = prepared_regions
        try:
            return reference_visual_review.bind_reference_panel_shots(
                planned,
                candidate_registry=candidate_registry,
                regions=regions,
                assets=images,
                allow_conservative_full_panel=review_source_upscale_policy is not None,
            )
        except reference_visual_review.ReferenceReviewError as exc:
            raise PipelineError(f"{exc.code}: {exc}") from exc
    analysis = latest_analysis(db, project_id)
    if analysis is None:
        raise PipelineError("visual.panel_lineage_unavailable: no approved panel analysis")
    regions = list(
        db.scalars(
            select(PanelRegion)
            .where(PanelRegion.story_analysis_id == analysis.id)
            .order_by(PanelRegion.source_order, PanelRegion.panel_id, PanelRegion.id)
        )
    )
    by_panel: dict[str, PanelRegion] = {}
    by_source_order: dict[int, PanelRegion] = {}
    for region in regions:
        if not region.panel_id or region.panel_id in by_panel:
            raise PipelineError(
                "visual.panel_lineage_unavailable: duplicate panel evidence identity"
            )
        if region.source_order in by_source_order:
            raise PipelineError(
                "visual.panel_lineage_unavailable: duplicate panel source order"
            )
        by_panel[region.panel_id] = region
        by_source_order[region.source_order] = region

    assets_by_id = {asset.id: asset for asset in images}
    sections: dict[str, list[PanelRegion]] = {}
    for section in script.sections or []:
        section_name = str(section.get("section", ""))
        panel_ids = list(section.get("evidence_panel_ids") or [])
        citations = list(section.get("citations") or [])
        candidates: list[PanelRegion] = []
        if panel_ids:
            if len(set(map(str, panel_ids))) != len(panel_ids):
                raise PipelineError(
                    "visual.panel_lineage_unavailable: duplicate cited panel identity"
                )
            for panel_id in panel_ids:
                region = by_panel.get(str(panel_id))
                if region is None or region.source_asset_id not in assets_by_id:
                    raise PipelineError(
                        "visual.panel_lineage_unavailable: cited panel is missing or foreign"
                    )
                candidates.append(region)
        else:
            if any(isinstance(citation, bool) or not isinstance(citation, int) for citation in citations):
                raise PipelineError(
                    "visual.panel_lineage_unavailable: citation is not a source order"
                )
            if len(set(citations)) != len(citations):
                raise PipelineError(
                    "visual.panel_lineage_unavailable: duplicate source-order citation"
                )
            for citation in citations:
                region = by_source_order.get(citation)
                if region is None or region.source_asset_id not in assets_by_id:
                    raise PipelineError(
                        "visual.panel_lineage_unavailable: source-order citation is missing"
                    )
                candidates.append(region)
        if not candidates:
            raise PipelineError(
                "visual.panel_lineage_unavailable: section has no panel evidence"
            )
        sections[section_name] = sorted(
            candidates, key=lambda region: (region.source_order, region.panel_id, region.id)
        )

    cursors: dict[tuple[str, str], int] = {}
    bound: list[dict[str, Any]] = []
    for shot in planned:
        section_name = str(shot.get("section", ""))
        asset_id = shot.get("asset_id")
        if not isinstance(asset_id, str) or asset_id not in assets_by_id or section_name not in sections:
            raise PipelineError(
                "visual.panel_lineage_unavailable: planned shot has no cited panel"
            )
        candidates = [
            region for region in sections[section_name] if region.source_asset_id == asset_id
        ]
        if not candidates:
            raise PipelineError(
                "visual.panel_lineage_unavailable: cited panel belongs to another asset"
            )
        cursor_key = (section_name, asset_id)
        cursor = cursors.get(cursor_key, 0)
        region = candidates[cursor % len(candidates)]
        cursors[cursor_key] = cursor + 1
        asset = assets_by_id[asset_id]
        bounds = _panel_region_bounds(region)
        source_width = int(asset.original_width or asset.width or 0)
        source_height = int(asset.original_height or asset.height or 0)
        if (
            source_width <= 0
            or source_height <= 0
            or bounds[0] < 0
            or bounds[1] < 0
            or bounds[2] > source_width
            or bounds[3] > source_height
        ):
            raise PipelineError(
                "visual.panel_lineage_unavailable: panel bounds are outside source asset"
            )
        asset_checksum = asset.original_checksum or asset.checksum
        if region.source_asset_checksum and region.source_asset_checksum != asset_checksum:
            raise PipelineError(
                "visual.panel_lineage_unavailable: source asset checksum changed"
            )
        snapshot = reference_visual_review.validated_visual_snapshot(region)
        bound.append(
            {
                **shot,
                "panel_region_id": region.id,
                "panel_id": region.panel_id,
                "panel_bounds": bounds,
                "visual_evidence": snapshot,
                "source_asset_checksum": asset_checksum,
            }
        )
    return bound


# --- stage: timeline and subtitles ----------------------------------------


def _review_provisional_spans(
    script: ScriptVersion,
    duration_s: float,
) -> list[timeline_svc.AudioSpan]:
    """Build explicitly non-authoritative display pacing for silent review."""
    duration_s = float(duration_s)
    if not math.isfinite(duration_s) or duration_s <= 0.0:
        raise PipelineError(
            "review.provisional_duration_invalid: silent review duration must be positive"
        )
    sections: list[tuple[str, str, float]] = []
    for raw in getattr(script, "sections", ()) or ():
        if not isinstance(raw, Mapping):
            continue
        section = str(raw.get("section", "")).strip()
        text = str(raw.get("spoken_text") or raw.get("text") or "").strip()
        if not section or not text:
            continue
        try:
            requested = float(raw.get("estimated_duration", 0.0) or 0.0)
        except (TypeError, ValueError):
            requested = 0.0
        weight = requested if requested > 0.0 else float(max(1, len(text.split())))
        sections.append((section, text, weight))
    if not sections:
        raise PipelineError(
            "review.provisional_script_missing: spoken script sections are required"
        )
    total_weight = sum(weight for _, _, weight in sections)
    spans: list[timeline_svc.AudioSpan] = []
    cursor = 0.0
    for index, (section, text, weight) in enumerate(sections):
        end = (
            float(duration_s)
            if index == len(sections) - 1
            else round(cursor + float(duration_s) * weight / total_weight, 3)
        )
        tokens = text.split()
        word_timings: list[dict[str, object]] = []
        slice_duration = max(0.001, end - cursor)
        for token_index, token in enumerate(tokens):
            start = cursor + slice_duration * token_index / len(tokens)
            token_end = cursor + slice_duration * (token_index + 1) / len(tokens)
            word_timings.append(
                {
                    "word": token,
                    "spoken_token_index": token_index,
                    "start": round(start, 3),
                    "end": round(token_end, 3),
                }
            )
        spans.append(
            timeline_svc.AudioSpan(
                section=section,
                text=text,
                start_time=round(cursor, 3),
                end_time=round(end, 3),
                word_timings=word_timings,
            )
        )
        cursor = end
    return spans


def _reference_duration_bounds(profile: object, *, silent_reference_review: bool) -> tuple[float, float]:
    """Return review-only pacing bounds without changing voiced profile gates."""

    if silent_reference_review:
        return STANDARD_FINAL_DURATION_MIN_SECONDS, STANDARD_FINAL_DURATION_MAX_SECONDS
    return float(profile.duration_min_s), float(profile.duration_max_s)


def _enforce_silent_review_transition_contract(planned: list[dict]) -> None:
    """Persist one visible transition at every silent-review boundary."""
    from app.services import editorial_visual_planner

    if not planned:
        return
    planned[0]["transition"] = "none"
    if len(planned) == 1:
        return
    schedule = editorial_visual_planner._review_transition_schedule(planned)
    for index in range(1, len(planned)):
        planned[index]["transition"] = schedule[index]


def build_timeline(db: Session, project_id: str, actor_id: str='', *, silent_reference_review: bool=False, review_source_upscale_policy: str | None=None, provisional_duration_s: float | None=None, provisional_duration_bounds_s: tuple[float, float] | None=None, reference_section_panel_ids: Mapping[str, Sequence[str]] | None=None, reference_section_citations: Mapping[str, Sequence[int]] | None=None, reference_beats_by_section: Mapping[str, Sequence[str]] | None=None, review_source_root: Path | None=None, allow_conservative_full_panel: bool=False, adaptive_reference_production: bool=False, adaptive_reference_duration_bounds_s: tuple[float, float] | None=None, standard_reference_production: bool=False) -> list[TimelineScene]:
    """Derive scenes/cues from voice timing or explicit silent-review pacing."""
    return pipeline_stages.media.build_timeline(sys.modules[__name__], db, project_id, actor_id, silent_reference_review=silent_reference_review, review_source_upscale_policy=review_source_upscale_policy, provisional_duration_s=provisional_duration_s, provisional_duration_bounds_s=provisional_duration_bounds_s, reference_section_panel_ids=reference_section_panel_ids, reference_section_citations=reference_section_citations, reference_beats_by_section=reference_beats_by_section, review_source_root=review_source_root, allow_conservative_full_panel=allow_conservative_full_panel, adaptive_reference_production=adaptive_reference_production, adaptive_reference_duration_bounds_s=adaptive_reference_duration_bounds_s, standard_reference_production=standard_reference_production)


def project_scenes(db: Session, project_id: str) -> list[TimelineScene]:
    return list(
        db.scalars(
            select(TimelineScene)
            .where(TimelineScene.project_id == project_id)
            .order_by(TimelineScene.order_index)
        )
    )


def project_cues(db: Session, project_id: str) -> list[SubtitleCue]:
    return list(
        db.scalars(
            select(SubtitleCue)
            .where(SubtitleCue.project_id == project_id)
            .order_by(SubtitleCue.order_index)
        )
    )


def _reference_rgb_content_hash(image: Image.Image) -> str:
    """Hash only normalized RGB dimensions and bytes, never PNG metadata."""
    rgb = image.convert("RGB")
    payload = (
        rgb.width.to_bytes(8, "big", signed=False)
        + rgb.height.to_bytes(8, "big", signed=False)
        + rgb.tobytes()
    )
    return hashlib.sha256(payload).hexdigest()


def _materialize_reference_panel_crop(
    db: Session,
    asset: SourceAsset,
    scene: TimelineScene,
    destination: Path,
    review_source_upscale_policy: review_source_upscale.ReviewSourceUpscalePolicy | None = None,
    review_source_root: Path | None = None,
) -> Path:
    """Materialize a persisted panel snapshot in its original source space."""
    region_id = getattr(scene, "panel_region_id", None)
    panel_id = getattr(scene, "panel_id", "")
    scene_bounds = getattr(scene, "panel_bounds_json", None)
    scene_evidence = getattr(scene, "visual_evidence_json", None)
    scene_checksum = getattr(scene, "source_asset_checksum", "")
    if not region_id or not panel_id or not isinstance(scene_bounds, Mapping):
        raise PipelineError(
            "visual.panel_lineage_unavailable: scene panel snapshot is incomplete"
        )
    try:
        bounds = (
            int(scene_bounds["x"]),
            int(scene_bounds["y"]),
            int(scene_bounds["x"]) + int(scene_bounds["width"]),
            int(scene_bounds["y"]) + int(scene_bounds["height"]),
        )
    except (KeyError, TypeError, ValueError):
        raise PipelineError(
            "visual.panel_lineage_unavailable: scene panel bounds are malformed"
        ) from None
    if bounds[0] < 0 or bounds[1] < 0 or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        raise PipelineError(
            "visual.panel_lineage_unavailable: scene panel bounds are invalid"
        )

    region = db.get(PanelRegion, region_id)
    if region is None or region.id != region_id or region.panel_id != panel_id:
        raise PipelineError(
            "visual.panel_lineage_unavailable: panel region snapshot is stale"
        )
    asset_checksum = asset.original_checksum or asset.checksum
    if region.source_asset_id != asset.id or not scene_checksum or scene_checksum != asset_checksum:
        raise PipelineError(
            "visual.panel_lineage_unavailable: source asset lineage is stale"
        )
    if region.source_asset_checksum and region.source_asset_checksum != asset_checksum:
        raise PipelineError(
            "visual.panel_lineage_unavailable: persisted asset checksum is stale"
        )
    source_bounds = _panel_region_bounds(region)
    accepted_manifest = None
    source_materialization = review_source_upscale.ORIGINAL_SOURCE_MATERIALIZATION
    if review_source_upscale_policy is not None:
        ledger = list(getattr(scene, "rejected_candidates", []) or [])
        accepted = [
            entry
            for entry in ledger
            if isinstance(entry, Mapping) and entry.get("accepted") is True
        ]
        if len(accepted) != 1 or not isinstance(
            accepted[0].get("source_upscale_manifest"), Mapping
        ):
            raise PipelineError(
                "review.upscale_manifest_invalid: accepted source-upscale manifest is missing"
            )
        accepted_manifest = review_source_upscale.normalize_review_manifest_materialization(
            dict(accepted[0]["source_upscale_manifest"])
        )
        source_materialization = str(
            accepted_manifest.get(
                "source_materialization",
                review_source_upscale.ORIGINAL_SOURCE_MATERIALIZATION,
            )
        )
        try:
            review_source_upscale.validate_review_manifest_dimensions(
                accepted_manifest,
                (bounds[2] - bounds[0], bounds[3] - bounds[1]),
            )
        except review_source_upscale.ReviewSourceUpscaleError as exc:
            raise PipelineError(f"{exc.code}: {exc}") from exc
        accepted_source_bounds = tuple(
            int(value) for value in accepted_manifest.get("source_panel_bounds", ())
        )
        expected_source_bounds = source_bounds
        if source_materialization == review_source_upscale.PERSISTED_PANEL_CROP_MATERIALIZATION:
            if len(accepted_source_bounds) != 4 or accepted_source_bounds[:2] != (0, 0):
                raise PipelineError(
                    "visual.panel_lineage_unavailable: persisted crop source bounds are stale"
                )
            expected_source_bounds = accepted_source_bounds
            source_bounds = accepted_source_bounds
        if (
            accepted_source_bounds != expected_source_bounds
            or tuple(accepted_manifest.get("prepared_panel_bounds", ())) != bounds
            or accepted_manifest.get("source_asset_id") != str(asset.id)
            or accepted_manifest.get("panel_region_id") != str(region.id)
            or accepted_manifest.get("source_asset_checksum") != asset_checksum
        ):
            raise PipelineError(
                "visual.panel_lineage_unavailable: source-upscale lineage is stale"
            )
    elif source_bounds != bounds:
        raise PipelineError(
            "visual.panel_lineage_unavailable: panel bounds snapshot is stale"
        )
    current_snapshot = reference_visual_review.validated_visual_snapshot(region)
    if not isinstance(scene_evidence, Mapping):
        raise PipelineError(
            "visual.panel_lineage_unavailable: visual evidence snapshot is missing"
        )
    try:
        stored_evidence = visual_scoring.parse_panel_visual_evidence(scene_evidence)
        stored_snapshot = visual_scoring.panel_visual_evidence_json(stored_evidence)
    except visual_scoring.VisualEvidenceError as exc:
        raise PipelineError(f"visual.panel_lineage_unavailable: {exc.code}") from exc
    if stored_snapshot != current_snapshot:
        raise PipelineError(
            "visual.panel_lineage_unavailable: visual evidence snapshot is stale"
        )
    if (
        review_source_upscale_policy is not None
        and source_materialization
        == review_source_upscale.PERSISTED_PANEL_CROP_MATERIALIZATION
    ):
        try:
            raw = storage.read_bytes(asset.storage_key)
            source_image, local_bounds = review_source_upscale.resolve_persisted_panel_crop(
                raw,
                asset_checksum=str(getattr(asset, "checksum", "") or ""),
                panel_bounds=source_bounds,
            )
        except review_source_upscale.ReviewSourceUpscaleError as exc:
            raise PipelineError(f"{exc.code}: {exc}") from exc
        if local_bounds != source_bounds:
            source_image.close()
            raise PipelineError(
                "visual.panel_lineage_unavailable: persisted crop source bounds changed"
            )
        source_context = nullcontext(source_image)
    else:
        source_path = (
            _review_reference_source_path(asset, source_root=review_source_root)
            if review_source_upscale_policy is not None
            else storage.path_for(asset.storage_key)
        )
        if not source_path.is_file():
            raise PipelineError(
                "visual.panel_lineage_unavailable: source asset file is unavailable"
            )
        source_context = Image.open(source_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source_context as source:
            source.load()
            source_width, source_height = source.size
            if source_bounds[2] > source_width or source_bounds[3] > source_height:
                raise PipelineError(
                    "visual.panel_lineage_unavailable: panel exceeds source dimensions"
                )
            cropped = source.convert("RGB").crop(source_bounds)
            if review_source_upscale_policy is not None:
                prepared, generated_manifest = review_source_upscale.prepare_review_panel(
                    cropped,
                    policy=review_source_upscale_policy,
                    source_asset_id=str(asset.id),
                    panel_region_id=str(region.id),
                    source_asset_checksum=asset_checksum,
                    source_panel_bounds=source_bounds,
                    source_dimensions=(source_width, source_height),
                    source_materialization=source_materialization,
                )
                if _canonical_json(generated_manifest) != _canonical_json(accepted_manifest):
                    raise PipelineError(
                        "review.upscale_manifest_invalid: source-upscale manifest changed"
                    )
                try:
                    review_source_upscale.validate_review_manifest(
                        accepted_manifest, prepared
                    )
                except review_source_upscale.ReviewSourceUpscaleError as exc:
                    raise PipelineError(f"{exc.code}: {exc}") from exc
            else:
                prepared = cropped
            expected_size = prepared.size
            if prepared.size != (bounds[2] - bounds[0], bounds[3] - bounds[1]):
                raise PipelineError(
                    "visual.panel_lineage_unavailable: panel crop dimensions changed"
                )
            expected_hash = _reference_rgb_content_hash(prepared)
            prepared.save(destination, format="PNG")
            try:
                with Image.open(destination) as written:
                    written.load()
                    normalized = written.convert("RGB")
                    if normalized.size != expected_size:
                        raise PipelineError(
                            "visual.panel_lineage_unavailable: materialized panel dimensions changed"
                        )
                    if _reference_rgb_content_hash(normalized) != expected_hash:
                        raise PipelineError(
                            "visual.panel_lineage_unavailable: materialized panel checksum mismatch"
                        )
            except PipelineError:
                raise
            except (OSError, UnidentifiedImageError, ValueError):
                raise PipelineError(
                    "visual.panel_lineage_unavailable: materialized panel integrity check failed"
                ) from None
    except PipelineError:
        raise
    except (OSError, UnidentifiedImageError, ValueError):
        raise PipelineError(
            "visual.panel_lineage_unavailable: source panel crop failed"
        ) from None
    return destination


def cue_specs(cues: list[SubtitleCue]) -> list[timeline_svc.CueSpec]:
    return [
        timeline_svc.CueSpec(
            order_index=c.order_index,
            text=c.text,
            start_time=c.start_time,
            end_time=c.end_time,
        )
        for c in cues
    ]


# --- stage: quality -------------------------------------------------------


def run_quality_checks(db: Session, project_id: str, job: RenderJob | None=None, actor_id: str='') -> list[quality_svc.CheckResult]:
    """Run every gate and persist the results for the review UI."""
    return pipeline_stages.quality.run_quality_checks(sys.modules[__name__], db, project_id, job, actor_id)


def project_quality_checks(db: Session, project_id: str) -> list[QualityCheck]:
    """Stored check results for the review UI, errors first."""
    return list(
        db.scalars(
            select(QualityCheck)
            .where(QualityCheck.project_id == project_id)
            .order_by(QualityCheck.severity, QualityCheck.code)
        )
    )


def project_qc_overrides(db: Session, project_id: str) -> list[QCOverrideEvent]:
    return list(
        db.scalars(
            select(QCOverrideEvent)
            .where(QCOverrideEvent.project_id == project_id)
            .order_by(QCOverrideEvent.created_at)
        )
    )


def project_qc_history(db: Session, project_id: str) -> list[QCHistorySnapshot]:
    return list(
        db.scalars(
            select(QCHistorySnapshot)
            .where(QCHistorySnapshot.project_id == project_id)
            .order_by(QCHistorySnapshot.created_at)
        )
    )


def override_warning(db: Session, project_id: str, code: str, reason: str, actor_id: str='') -> QualityCheck:
    """Record an explicit, attributed override for a non-blocking warning."""
    return pipeline_stages.quality.override_warning(sys.modules[__name__], db, project_id, code, reason, actor_id)


# --- stage: render --------------------------------------------------------


def enqueue_render(
    db: Session,
    project_id: str,
    kind: str = "final",
    actor_id: str = "",
    encoder: str = "auto",
    profile: str = "Auto",
    allow_nonpublishable_artifact: bool = False,
) -> RenderJob:
    """Queue a render. Final renders require passing quality checks.

    ``encoder`` is stored on the job rather than resolved now: the worker may run
    on a different machine than the API, so the GPU probe has to happen where the
    encoding actually happens.
    """
    project = get_project(db, project_id)
    if kind not in {"preview", "final"}:
        raise PipelineError("render kind must be 'preview' or 'final'")

    # Reject an unknown name here so the user finds out at request time rather
    # than discovering a silent CPU fallback after the render.
    from app.services import encoders as encoders_svc

    requested = (encoder or "auto").strip().lower()
    if profile not in {"Auto", "Calm", "Balanced", "Dynamic", "No motion"}:
        raise PipelineError("unknown render profile")
    if requested != "auto":
        try:
            encoders_svc.get_spec(requested)
        except ValueError as exc:
            raise PipelineError(str(exc)) from exc

    scenes = project_scenes(db, project_id)
    if not scenes:
        raise PipelineError("build the timeline before rendering")

    if kind == "final":
        results = run_quality_checks(db, project_id, actor_id=actor_id)
        blocking = [r for r in results if r.blocking]
        nonpublishable_codes = {"rights.undeclared_assets"}
        can_render_nonpublishable = bool(
            allow_nonpublishable_artifact
            and blocking
            and all(result.code in nonpublishable_codes for result in blocking)
        )
        if blocking and not can_render_nonpublishable:
            raise PipelineError(
                "Quality checks must pass before a final render: "
                + "; ".join(r.message for r in blocking[:3])
            )
        if can_render_nonpublishable:
            audit(
                db, "render.enqueue_nonpublishable", "project", project_id, actor_id,
                blocking_codes=[result.code for result in blocking],
                publish_allowed=False,
            )

    job = RenderJob(
        project_id=project_id,
        kind=kind,
        status=JobStatus.QUEUED,
        stage="queued",
        encoder_requested=requested,
        render_profile=profile,
    )
    db.add(job)
    project.status = ProjectStatus.RENDERING
    project.error_message = ""
    audit(db, "render.enqueue", "project", project_id, actor_id, kind=kind, encoder=requested)
    db.flush()
    return job


def _audio_stage_ready(db: Session, script: ScriptVersion) -> bool:
    spoken_sections = [
        str(section.get("text", "")).strip()
        for section in (script.sections or [])
        if isinstance(section, Mapping) and str(section.get("text", "")).strip()
    ]
    segments = audio_segments(db, script.id)
    return (
        bool(spoken_sections)
        and len(segments) == len(spoken_sections)
        and [segment.order_index for segment in segments] == list(range(len(segments)))
        and all(
            segment.storage_key
            and storage.exists(segment.storage_key)
            and float(segment.duration) > 0.0
            and isinstance(segment.word_timings, list)
            for segment in segments
        )
    )


def _timeline_stage_ready(db: Session, project_id: str) -> bool:
    scenes = project_scenes(db, project_id)
    cues = project_cues(db, project_id)
    return bool(scenes and cues and all(scene.end_time > scene.start_time for scene in scenes))


def _render_stage_ready(
    db: Session, project_id: str, script_hash: str
) -> RenderJob | None:
    """Return an idempotent final artifact only when its exact script is known."""

    script = latest_script_row(db, project_id)
    metadata = script.editorial_metadata if script and isinstance(script.editorial_metadata, Mapping) else {}
    production = metadata.get("production") if isinstance(metadata.get("production"), Mapping) else {}
    job_id = production.get("render_job_id")
    if production.get("script_hash") != script_hash or not isinstance(job_id, str) or not job_id:
        return None
    job = db.get(RenderJob, job_id)
    if job is None or job.kind != "final" or job.status != JobStatus.SUCCEEDED:
        return None
    if not job.output_key or not Path(job.output_key).is_file():
        return None
    return job


def _ensure_final_thumbnail(
    db: Session,
    job: RenderJob,
    *,
    script: ScriptVersion | None = None,
    required: bool = False,
) -> dict[str, Any] | None:
    """Build or reuse the upload-ready thumbnail package for a final render."""
    if not settings.auto_thumbnail_enabled or job.kind != "final":
        return None
    if not job.output_key:
        if required:
            raise PipelineError("thumbnail.video_missing: final render has no output path")
        return None
    active_script = script or current_script(db, job.project_id)
    if active_script is None:
        if required:
            raise PipelineError("thumbnail.script_missing: approved script is unavailable")
        return None
    assets: dict[str, SourceAsset | None] = {}

    def resolve_asset_path(asset_id: str) -> Path | None:
        if asset_id not in assets:
            assets[asset_id] = db.get(SourceAsset, asset_id)
        asset = assets[asset_id]
        if asset is None or not asset.storage_key or not storage.exists(asset.storage_key):
            return None
        return storage.path_for(asset.storage_key)

    try:
        manifest = thumbnail_svc.generate_thumbnail_package(
            video_path=Path(job.output_key),
            output_dir=Path(job.output_key).parent,
            script=active_script,
            scenes=project_scenes(db, job.project_id),
            resolve_asset_path=resolve_asset_path,
        )
    except thumbnail_svc.ThumbnailError as exc:
        audit(db, "thumbnail.failed", "render_job", job.id, error=str(exc))
        if required:
            raise PipelineError(str(exc)) from exc
        return None
    audit(
        db, "thumbnail.succeeded", "render_job", job.id,
        headline=manifest.get("headline", ""),
        thumbnail_path=manifest.get("thumbnail_path", ""),
        variant_count=len(manifest.get("variants", [])),
    )
    return manifest


def _persist_production_metadata(
    db: Session,
    script: ScriptVersion,
    metadata: Mapping[str, Any],
    production: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist one immutable production checkpoint envelope and commit it."""
    next_metadata = {**dict(metadata), "production": dict(production)}
    script.editorial_metadata = next_metadata
    db.flush()
    db.commit()
    return next_metadata


def run_production(db: Session, project_id: str, *, actor_id: str='', approved_script_hash: str='', approved_script_version: int | None=None, speed: float=1.15, provider_name: str | None=None, encoder: str='auto', profile: str='Auto') -> RenderJob:
    """Run the explicit, local production path through post-render QC.

    This is intentionally separate from the review-only cloud workflow.  The
    caller must provide the hash and version that the operator approved.  Each
    boundary is durable and re-used only when it still belongs to that exact
    script; a changed script therefore cannot inherit an older voice, timeline,
    or render artifact.
    """
    return pipeline_stages.production.run_production(sys.modules[__name__], db, project_id, actor_id=actor_id, approved_script_hash=approved_script_hash, approved_script_version=approved_script_version, speed=speed, provider_name=provider_name, encoder=encoder, profile=profile)


# Alias retained for callers that describe this boundary as a production run.
production_run = run_production


def latest_render(db: Session, project_id: str, kind: str = "final") -> RenderJob | None:
    return db.scalars(
        select(RenderJob)
        .where(RenderJob.project_id == project_id, RenderJob.kind == kind)
        .order_by(RenderJob.created_at.desc())
    ).first()


def successful_render(db: Session, project_id: str) -> RenderJob | None:
    return db.scalars(
        select(RenderJob)
        .where(
            RenderJob.project_id == project_id,
            RenderJob.kind == "final",
            RenderJob.status == JobStatus.SUCCEEDED,
        )
        .order_by(RenderJob.completed_at.desc())
    ).first()



def _reference_scene_inputs(
    db: Session,
    project_id: str,
    scenes: Sequence[TimelineScene],
    profile: object,
    *,
    require_fallback_ledger: bool,
    review_source_upscale_policy: review_source_upscale.ReviewSourceUpscalePolicy | None = None,
    review_source_root: Path | None = None,
) -> list[object]:
    """Reconstruct exact panel crops and accepted Task 6 identity for render."""
    from app.services import render as render_svc

    workspace = storage.workspace_dir(project_id, "reference-review-panels")
    inputs: list[object] = []
    for index, scene in enumerate(scenes):
        asset_id = getattr(scene, "asset_id", None)
        if not isinstance(asset_id, str) or not asset_id:
            raise PipelineError(
                "visual.panel_lineage_unavailable: reference scene has no source asset"
            )
        asset = db.get(SourceAsset, asset_id)
        if asset is None:
            raise PipelineError(
                "visual.panel_lineage_unavailable: reference scene asset is unavailable"
            )
        destination = workspace / f"scene-{index:04d}.png"
        image_path = _materialize_reference_panel_crop(
            db,
            asset,
            scene,
            destination,
            review_source_upscale_policy=review_source_upscale_policy,
            review_source_root=review_source_root,
        )
        ledger = list(getattr(scene, "rejected_candidates", []) or [])
        if require_fallback_ledger and not ledger:
            raise PipelineError(
                "visual.panel_lineage_unavailable: accepted fallback ledger is missing"
            )
        accepted = [
            entry for entry in ledger
            if isinstance(entry, Mapping) and entry.get("accepted") is True
        ]
        if require_fallback_ledger and len(accepted) != 1:
            raise PipelineError(
                "visual.panel_lineage_unavailable: accepted fallback ledger is invalid"
            )
        accepted_entry = accepted[0] if accepted else {}
        source_upscale_manifest = (
            accepted_entry.get("source_upscale_manifest")
            if isinstance(accepted_entry, Mapping)
            else None
        )
        telemetry = (
            accepted_entry.get("telemetry")
            if isinstance(accepted_entry, Mapping)
            else None
        )
        selected_roi = (
            telemetry.get("selected_roi")
            if isinstance(telemetry, Mapping)
            else None
        )
        panel_bounds_json = getattr(scene, "panel_bounds_json", None)
        panel_size = None
        if isinstance(panel_bounds_json, Mapping):
            panel_size = (
                int(panel_bounds_json.get("width", 0)),
                int(panel_bounds_json.get("height", 0)),
            )
        border_mask = (
            accepted_entry.get("border_mask")
            if isinstance(accepted_entry, Mapping)
            else None
        )
        scene_evidence = getattr(scene, "visual_evidence_json", None)
        evidence_hash = (
            accepted_entry.get("evidence_hash", "")
            if isinstance(accepted_entry, Mapping)
            else ""
        )
        source_order = (
            accepted_entry.get("source_order")
            if isinstance(accepted_entry, Mapping)
            else None
        )
        if require_fallback_ledger:
            if (
                not isinstance(panel_size, tuple)
                or panel_size[0] <= 0
                or panel_size[1] <= 0
                or not isinstance(border_mask, Mapping)
                or not isinstance(scene_evidence, Mapping)
                or not isinstance(telemetry, Mapping)
                or accepted_entry.get("panel_region_id") != getattr(scene, "panel_region_id", None)
                or accepted_entry.get("panel_id") != getattr(scene, "panel_id", "")
                or accepted_entry.get("source_asset_id") != asset.id
                or accepted_entry.get("source_asset_checksum") != (asset.original_checksum or asset.checksum)
            ):
                raise PipelineError(
                    "visual.panel_lineage_unavailable: accepted fallback lineage is stale"
                )
            try:
                parsed_evidence = visual_scoring.parse_panel_visual_evidence(scene_evidence)
                with Image.open(image_path) as panel_image:
                    actual_mask = framing_analysis.build_color_agnostic_border_mask(
                        panel_image,
                        parsed_evidence,
                        grid_long_edge=int(profile.framing_mask_grid_long_edge),
                    )
                if _canonical_json(asdict(actual_mask)) != _canonical_json(border_mask):
                    raise ValueError("materialized mask snapshot does not match accepted ledger")
                reference_visual_review.validate_accepted_fallback_ledger(
                    ledger,
                    panel_region_id=str(getattr(scene, "panel_region_id", "")),
                    panel_id=str(getattr(scene, "panel_id", "")),
                    source_asset_id=str(asset.id),
                    source_asset_checksum=str(asset.original_checksum or asset.checksum),
                    source_order=int(source_order),
                    panel_size=panel_size,
                    evidence=parsed_evidence,
                    border_mask=actual_mask,
                    selected_roi=selected_roi,
                    framing_telemetry=telemetry,
                )
            except reference_visual_review.ReferenceReviewError as exc:
                raise PipelineError(f"{exc.code}: {exc}") from exc
            except (OSError, ValueError, TypeError, visual_scoring.VisualEvidenceError) as exc:
                raise PipelineError(
                    "visual.panel_lineage_unavailable: accepted mask snapshot is invalid"
                ) from exc
        inputs.append(
            render_svc.SceneInput(
                image_path=image_path,
                start_time=float(scene.start_time),
                end_time=float(scene.end_time),
                focus_x=float(scene.focus_x),
                focus_y=float(scene.focus_y),
                focus_end_x=float(scene.focus_end_x),
                focus_end_y=float(scene.focus_end_y),
                camera_curve=scene.camera_curve,
                motion_mode=scene.motion_mode,
                motion_intensity=scene.motion_intensity,
                motion_reason=scene.motion_reason,
                effect=scene.effect,
                disabled_effects=list(scene.disabled_effects or []),
                # Preserve the persisted director decision.  The review path
                # must not erase a bounded fade before the renderer/QC sees it.
                transition=getattr(scene, "transition", "cut") or "cut",
                overlay_text="",
                panel_region_id=getattr(scene, "panel_region_id", None),
                panel_id=getattr(scene, "panel_id", ""),
                panel_bounds=(
                    (
                        int(panel_bounds_json["x"]),
                        int(panel_bounds_json["y"]),
                        int(panel_bounds_json["x"]) + int(panel_bounds_json["width"]),
                        int(panel_bounds_json["y"]) + int(panel_bounds_json["height"]),
                    )
                    if isinstance(panel_bounds_json, Mapping)
                    else None
                ),
                visual_evidence=scene_evidence,
                source_asset_checksum=getattr(scene, "source_asset_checksum", ""),
                source_asset_id=asset.id,
                source_order=source_order,
                panel_size=panel_size,
                evidence_hash=evidence_hash,
                border_mask=border_mask,
                selected_roi=selected_roi,
                fallback_attempts=ledger,
                framing_telemetry=telemetry,
                publish_allowed=False,
                review_source_upscale_manifest=source_upscale_manifest,
            )
        )
    return inputs


def _silent_review_media_duration(scenes: Sequence[object]) -> float:
    """Match the renderer's rounded per-scene duration contract for review."""

    return round(
        sum(
            max(
                0.1,
                round(
                    float(scene.end_time) - float(scene.start_time),
                    3,
                ),
            )
            for scene in scenes
        ),
        3,
    )


def _build_silent_reference_request(
    db: Session,
    job: RenderJob,
    project: Project,
    profile: object,
    *,
    output_override: Path | None,
    review_source_upscale_policy: review_source_upscale.ReviewSourceUpscalePolicy | None = None,
    review_source_root: Path | None = None,
):
    from app.services import render as render_svc

    scenes = project_scenes(db, job.project_id)
    if not scenes:
        raise PipelineError("no scenes to render")
    scene_inputs = _reference_scene_inputs(
        db,
        job.project_id,
        scenes,
        profile,
        require_fallback_ledger=True,
        review_source_upscale_policy=review_source_upscale_policy,
        review_source_root=review_source_root,
    )
    # The regular renderer encodes the sum of SceneInput.duration values. Use
    # that same rounded duration to build provisional word groups; absolute
    # timeline end times can differ by accumulated sub-millisecond rounding.
    media_duration = _silent_review_media_duration(scenes)
    if any(scene.publish_allowed is not False for scene in scene_inputs):
        raise PipelineError(
            "reference.publish_not_allowed: publish_allowed must be false for silent review scenes"
        )
    if review_source_upscale_policy is not None:
        script = _script_for_media(
            db,
            job.project_id,
            allow_unapproved_review=True,
        )
        provisional_spans = _review_provisional_spans(script, media_duration)
        sentence_groups: list[object] = []
        for span_index, span in enumerate(provisional_spans):
            timed_cues = [
                {
                    "spoken_token_index": index,
                    "word": timing["word"],
                    "start": timing["start"],
                    "end": timing["end"],
                }
                for index, timing in enumerate(span.word_timings)
            ]
            try:
                sentence_groups.extend(
                    subtitle_karaoke.build_sentence_caption_groups(
                        span.text,
                        timed_cues,
                        group_prefix=f"review-provisional-{span_index + 1}",
                    )
                )
            except ValueError as exc:
                raise PipelineError(str(exc)) from exc
        try:
            sentence_groups = list(
                render_svc.fit_sentence_karaoke_groups(
                    sentence_groups,
                    profile.final_width,
                    profile.final_height,
                    max_chars=subtitle_karaoke.CAPTION_MAX_CHARS,
                    max_lines=subtitle_karaoke.CAPTION_MAX_LINES,
                    active_scale=subtitle_karaoke.CAPTION_ACTIVE_SCALE,
                    font_height_ratio=subtitle_karaoke.CAPTION_FONT_HEIGHT_RATIO,
                    safe_margin_px=subtitle_karaoke.CAPTION_SAFE_MARGIN_PX,
                )
            )
        except render_svc.RenderError as exc:
            raise PipelineError(f"{exc.code}: {exc}") from exc
        persisted_cues = timeline_svc.build_cues(
            provisional_spans,
            media_duration=media_duration,
        )
        cues = [
            cue
            for cue in persisted_cues
            if any(
                float(scene.start_time) - 1e-9 <= float(cue.start_time)
                and float(cue.end_time) <= float(scene.end_time) + 1e-9
                for scene in scenes
            )
        ]
        subtitle_timing_source = "review_provisional_display_pacing_v1"
    else:
        persisted_cues = project_cues(db, job.project_id)
        cues = cue_specs(persisted_cues)
        sentence_groups = []
        subtitle_timing_source = ""
    warnings = timeline_svc.validate_cues(
        cues,
        max_chars=MAX_SUBTITLE_CHARS_PER_LINE,
        max_lines=1,
        media_duration=media_duration,
    )
    if any(item.get("severity") == "error" for item in warnings):
        raise PipelineError("reference.subtitle_invalid: persisted display cues are invalid")
    for cue in cues:
        text = str(cue.text or "")
        if (
            not text
            or text != text.upper()
            or not text.isalnum()
            or cue.start_time < 0.0
            or cue.end_time <= cue.start_time
            or cue.end_time > media_duration + 1e-9
        ):
            raise PipelineError(
                "reference.subtitle_invalid: persisted display cues are invalid"
            )
    try:
        render_svc.validate_silent_reference_cues(
            cues, scene_inputs, media_duration=media_duration
        )
    except render_svc.RenderError as exc:
        raise PipelineError(f"{exc.code}: {exc}") from exc

    output = Path(output_override) if output_override is not None else storage.output_path(
        job.project_id, "reference-visual-review-silent.mp4"
    )
    return render_svc.RenderRequest(
        project_id=job.project_id,
        scenes=scene_inputs,
        audio_path=None,
        cues=cues,
        output_path=output,
        title_text="",
        preview=False,
        profile=profile,
        music_path=None,
        encoder=job.encoder_requested or None,
        silent_reference_review=True,
        output_override=output,
        sidecar_path=output.with_suffix(".review.json"),
        sentence_groups=sentence_groups,
        subtitle_contract_version=subtitle_karaoke.SUBTITLE_CONTRACT_VERSION,
        subtitle_timing_source=subtitle_timing_source,
        subtitle_contract=subtitle_karaoke.contract_manifest(profile),
        review_source_upscale_policy=(
            review_source_upscale_policy.policy_id
            if review_source_upscale_policy is not None
            else None
        ),
        allow_conservative_full_panel=True,
    )


def render_silent_review_preview(db: Session, project_id: str, *, actor_id: str='', review_source_upscale_policy: str=review_source_upscale.REVIEW_SOURCE_UPSCALE_POLICY_ID, review_source_root: Path, output_dir: Path | None=None) -> tuple[RenderJob, object]:
    """Render and persist one video-only review attempt through the regular path."""
    return pipeline_stages.rendering.render_silent_review_preview(sys.modules[__name__], db, project_id, actor_id=actor_id, review_source_upscale_policy=review_source_upscale_policy, review_source_root=review_source_root, output_dir=output_dir)

def build_render_request(db: Session, job: RenderJob, *, silent_reference_review: bool=False, output_override: Path | None=None, review_source_upscale_policy: str | None=None, review_source_root: Path | None=None):
    """Assemble a RenderRequest from persisted state."""
    return pipeline_stages.rendering.build_render_request(sys.modules[__name__], db, job, silent_reference_review=silent_reference_review, output_override=output_override, review_source_upscale_policy=review_source_upscale_policy, review_source_root=review_source_root)


def execute_render(db: Session, job_id: str) -> RenderJob:
    """Run a queued render to completion. Called by the worker."""
    return pipeline_stages.rendering.execute_render(sys.modules[__name__], db, job_id)


def retry_render(db: Session, job_id: str, actor_id: str='') -> RenderJob:
    """Queue a fresh attempt, preserving the failed job for the audit trail."""
    return pipeline_stages.rendering.retry_render(sys.modules[__name__], db, job_id, actor_id)


def next_queued_job(db: Session) -> RenderJob | None:
    return db.scalars(
        select(RenderJob)
        .where(RenderJob.status == JobStatus.QUEUED)
        .order_by(RenderJob.created_at)
        .limit(1)
    ).first()


def claim_render_job(db: Session, job_id: str, lease_seconds: int=1800) -> bool:
    """Claim a queued job or reclaim an expired running lease."""
    return pipeline_stages.rendering.claim_render_job(sys.modules[__name__], db, job_id, lease_seconds)


def recover_stale_jobs(db: Session) -> int:
    """Requeue expired workers, retaining the audit trail."""
    return pipeline_stages.rendering.recover_stale_jobs(sys.modules[__name__], db)


# --- convenience: full draft ----------------------------------------------


def generate_draft(db: Session, project_id: str, actor_id: str='', seed: int | None=None) -> dict:
    """Materialize a vision-evidence script draft and stop before media stages.

    Voice-over, timeline, cues, and rendering require explicit human approval.
    This path never starts analysis or falls back to a text/rules workflow.
    """
    return pipeline_stages.script.generate_draft(sys.modules[__name__], db, project_id, actor_id, seed)
