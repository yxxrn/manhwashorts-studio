"""Pipeline orchestration (PRD sections 6 and 10).

Each function here is one stage of the journey and is safe to re-run: stages
replace their own outputs rather than appending, so a user can regenerate the
script, the voice-over, or the timeline independently without corrupting the
others.

Stage order:

    ingest -> analyse -> script -> approve -> voice -> timeline
           -> subtitles -> quality -> render -> publish
"""

from __future__ import annotations

import hashlib
import io
import json
import secrets
import time
from collections.abc import Mapping, Sequence
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


def run_legacy_text_analysis(db: Session, project_id: str, actor_id: str = "") -> StoryAnalysis:
    """Extract story facts from all text assets, replacing any prior analysis."""
    project = get_project(db, project_id)
    assets = project_assets(db, project_id)
    sources = text_sources(assets)
    if not sources:
        raise PipelineError(
            "No text material to analyse. Paste a recap or upload a TXT/MD/PDF/DOCX first."
        )

    # BYOK: a verified workspace key wins over env config, which wins over rules.
    analyzer, decision = resolver_svc.resolve_analyzer(db, project.workspace_id)
    result = analyzer.analyze(sources)
    if decision.reason:
        result.low_confidence_notes.append(f"Analysis: {decision.reason}.")

    # One analysis row per project: replace rather than accumulate.
    for old in db.scalars(select(StoryAnalysis).where(StoryAnalysis.project_id == project_id)):
        db.delete(old)

    row = StoryAnalysis(
        project_id=project_id,
        characters=[
            {"name": c.name, "role": c.role, "aliases": c.aliases, "mentions": c.mentions,
             "source_index": c.source_index}
            for c in result.characters
        ],
        locations=result.locations,
        events=[
            {"order": e.order, "text": e.text, "kind": e.kind, "source_index": e.source_index}
            for e in result.events
        ],
        main_conflict=result.main_conflict,
        twist=result.twist,
        cliffhanger=result.cliffhanger,
        pronunciation_candidates=result.pronunciation_candidates,
        low_confidence_notes=result.low_confidence_notes,
    )
    db.add(row)
    project.status = ProjectStatus.GENERATING
    audit(
        db,
        "analysis.run",
        "project",
        project_id,
        actor_id,
        generator=result.generator,
        provider_source=decision.source,
        provider=decision.provider,
        model=decision.model,
    )
    db.flush()
    return row


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


def run_analysis(
    db: Session,
    project_id: str,
    actor_id: str = "",
    *,
    narrative_profile_id: str | None = None,
) -> StoryAnalysis:
    """Run only the complete, fail-closed vision-first analysis flow."""

    project = get_project(db, project_id)
    selected_profile = None
    if narrative_profile_id is not None:
        try:
            selected_profile = narrative_identity.get_narrative_identity(
                narrative_profile_id
            )
            narrative_identity.load_narrative_instruction(narrative_profile_id)
        except narrative_identity.NarrativeIdentityError:
            raise PipelineError("narrative_profile_invalid") from None
    assets = image_assets(project_assets(db, project_id))
    run_id = secrets.token_hex(16)
    for old in db.scalars(select(StoryAnalysis).where(StoryAnalysis.project_id == project_id)):
        db.delete(old)
    db.flush()
    row = StoryAnalysis(
        project_id=project_id,
        analysis_run_id=run_id,
        state="PROCESSING",
        instruction_version=analyzer_contract.PROMPT_VERSION,
    )
    db.add(row)
    project.status = ProjectStatus.GENERATING
    db.flush()

    try:
        try:
            instruction_version, instruction_sha256, instruction_text = (
                analyzer_contract.load_analyzer_instruction(
                    narrative_profile_id=narrative_profile_id
                )
            )
        except analyzer_contract.AnalyzerContractError:
            raise _AnalysisBlocked("analyzer_contract_invalid", stage="instruction_load") from None
        try:
            visual_instruction_version, visual_instruction_sha256, _ = (
                visual_scoring.load_visual_evidence_instruction()
            )
        except Exception:
            raise _AnalysisBlocked("analyzer_contract_invalid", stage="visual_instruction_load") from None
        row.instruction_version = instruction_version
        row.instruction_sha256 = instruction_sha256

        if not assets:
            raise _AnalysisBlocked("vision_capability_missing", stage="image_input")
        try:
            inputs, asset_by_id = _build_source_inputs(assets)
            coverage = segmentation.build_complete_coverage_map(
                inputs,
                segmentation_version=segmentation.SEGMENTATION_VERSION,
            )
        except _AnalysisBlocked:
            raise
        except Exception:
            raise _AnalysisBlocked("coverage_incomplete", stage="coverage_build") from None

        overview_errors = segmentation.verify_segmentation_completeness(
            _coverage_overviews(inputs, coverage), coverage
        )
        coverage_errors = tuple(sorted(set(coverage.reconciliation_errors + overview_errors)))
        row.coverage_manifest_json = _coverage_manifest(inputs, coverage)
        if (
            coverage_errors
            or coverage.source_content_coverage_ratio != 1.0
            or coverage.unresolved_material_area != 0
        ):
            raise _AnalysisBlocked(
                "coverage_incomplete",
                stage="coverage_reconcile",
                error_count=len(coverage_errors),
                coverage_map_hash=coverage.map_sha256,
            )

        panel_regions = _persist_panel_regions(db, row, coverage, asset_by_id)
        if not panel_regions:
            raise _AnalysisBlocked("coverage_incomplete", stage="panel_persistence")
        chunks = build_observation_chunks(panel_regions)
        input_by_asset = {item.source_asset_id: item for item in inputs}

        try:
            provider, capability = resolver_svc.resolve_vision(db, project.workspace_id)
        except Exception:
            raise _AnalysisBlocked("vision_capability_missing", stage="vision_resolve") from None
        if provider is None or capability is None or not capability.available:
            code = getattr(capability, "blocking_reason", None)
            if code not in _VISION_BLOCKING_CODES:
                code = "vision_capability_missing"
            row.provider_type = getattr(capability, "provider_type", None)
            row.provider_name = getattr(capability, "provider_name", None)
            row.model_name = getattr(capability, "model", None)
            raise _AnalysisBlocked(str(code), stage="vision_capability")
        row.provider_type = capability.provider_type
        row.provider_name = capability.provider_name
        row.model_name = capability.model
        panel_transports = {
            panel.panel_id: _panel_transport(
                panel,
                input_by_asset[panel.source_asset_id],
                coverage,
            )
            for panel in panel_regions
        }

        semantic, chunk_ledger, first_chunk = _observe_chunks(
            provider,
            chunks,
            panel_transports,
            analysis_run_id=run_id,
            instruction_version=instruction_version,
            instruction_sha256=instruction_sha256,
            visual_instruction_version=visual_instruction_version,
            visual_instruction_sha256=visual_instruction_sha256,
        )
        enriched, chain_observations = _enrich_observations(
            panel_regions, semantic, first_chunk, coverage
        )
        duplicate_observations = sum(len(chunk) for chunk in chunks) - len(enriched)
        manifest = _coverage_manifest(
            inputs,
            coverage,
            processed_panels=len(enriched),
            duplicate_observations=duplicate_observations,
        )
        row.coverage_manifest_json = manifest
        synthesis_chunks = tuple(
            {
                "chunk_id": item["chunk_id"],
                "panel_ids": list(item["panel_ids"]),
                "observation_ids": list(item["observation_ids"]),
                "overlap_with_previous": list(item["overlap_with_previous"]),
                "overlap_with_next": list(item["overlap_with_next"]),
            }
            for item in chunk_ledger
        )
        expected_panel_ids = tuple(panel.panel_id for panel in panel_regions)
        synthesis_request = VisionChapterSynthesisRequest(
            analysis_run_id=run_id,
            instruction_version=instruction_version,
            instruction_sha256=instruction_sha256,
            instruction_text=instruction_text,
            expected_panel_ids=expected_panel_ids,
            coverage_manifest=manifest,
            ordered_observations=tuple(enriched[panel_id] for panel_id in expected_panel_ids),
            chunks=synthesis_chunks,
            narrative_profile_id=(
                selected_profile.profile_id if selected_profile is not None else None
            ),
            narrative_profile_version=(
                selected_profile.profile_version if selected_profile is not None else None
            ),
            narrative_profile_sha256=(
                selected_profile.contract_sha256 if selected_profile is not None else None
            ),
        )
        try:
            synthesis_output = provider.synthesize(synthesis_request)
        except analyzer_contract.AnalyzerContractError:
            raise _AnalysisBlocked("analyzer_contract_invalid", stage="synthesis_request") from None
        except VisionResponseInvalid:
            raise _AnalysisBlocked("vision_response_invalid", stage="synthesis_response") from None
        except VisionProviderRequestFailed:
            raise _AnalysisBlocked("vision_provider_request_failed", stage="synthesis_provider") from None
        except VisionCapabilityError:
            raise _AnalysisBlocked("vision_provider_request_failed", stage="synthesis_provider") from None
        except Exception:
            raise _AnalysisBlocked("vision_provider_request_failed", stage="synthesis_provider") from None

        synthesis_output = _classify_synthesis_output(
            synthesis_output,
            expected_panel_ids,
            synthesis_chunks,
        )
        try:
            analyzer_contract.validate_analyzer_output(
                synthesis_output,
                expected_panel_ids=expected_panel_ids,
                narrative_profile_id=narrative_profile_id,
            )
        except analyzer_contract.AnalyzerContractError:
            raise _AnalysisBlocked("analysis_incomplete", stage="analyzer_validation") from None

        claims = synthesis_output["evidence_graph"]["claims"]
        panel_chain = [
            {
                "panel_id": panel.panel_id,
                "source_asset_id": panel.source_asset_id,
                "source_order": panel.source_order,
                "bounds": _panel_region_bounds(panel),
            }
            for panel in panel_regions
        ]
        reconciled, chain_errors = segmentation.reconcile_coverage_chain(
            coverage,
            panel_chain,
            chain_observations,
            chunk_ledger,
            claims,
        )
        if not reconciled:
            if any(error.startswith("chain.chunk") for error in chain_errors):
                raise _AnalysisBlocked("analysis_chunk_link_missing", stage="chain_reconcile")
            if any(error.startswith("chain.claim") for error in chain_errors):
                raise _AnalysisBlocked("analysis_claim_evidence_missing", stage="chain_reconcile")
            raise _AnalysisBlocked("analysis_incomplete", stage="chain_reconcile")

        claim_refs = {
            claim["claim_id"]: list(claim["evidence_panel_ids"])
            for claim in claims
            if isinstance(claim, Mapping)
        }
        manifest["claim_to_panel_refs"] = claim_refs
        row.coverage_manifest_json = manifest
        row.continuity_ledger_json = synthesis_output["continuity_ledger"]
        row.evidence_graph_json = dict(synthesis_output["evidence_graph"])
        row.evidence_graph_json["script_passages"] = list(synthesis_output["script_passages"])
        row.story_spine_json = dict(synthesis_output["narrative_outline"]["story_spine"])
        row.reconciliation_json = {
            "coverage_map_hash": coverage.map_sha256,
            "coverage_map_version": coverage.version,
            "canonical_panel_count": coverage.panel_count,
            "processed_panel_count": len(enriched),
            "duplicate_overlap_observations": duplicate_observations,
            "chain_reconciled": True,
            "chain_errors": list(chain_errors),
            "narrative_screening_warning_codes": [],
        }
        if selected_profile is not None:
            row.reconciliation_json["narrative_identity"] = {
                "profile_id": selected_profile.profile_id,
                "version": selected_profile.profile_version,
                "sha256": selected_profile.contract_sha256,
            }
            row.reconciliation_json["narrative_ending_kind"] = synthesis_output[
                "narrative_outline"
            ]["ending_kind"]
        row.blocking_reasons_json = None
        _derive_legacy_fields(row, synthesis_output)
        row.state = "RECONCILED"
        project.status = ProjectStatus.REVIEW
        audit(
            db,
            "analysis.run",
            "project",
            project_id,
            actor_id,
            generator="vision_first",
            provider=capability.provider_name,
            model=capability.model,
            state=row.state,
            panel_count=coverage.panel_count,
            processed_panel_count=len(enriched),
        )
        db.flush()
        return row
    except _AnalysisBlocked as blocked:
        return _persist_blocked_analysis(
            db,
            project,
            row,
            [blocked.code],
            [blocked.finding],
        )


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


def current_script(db: Session, project_id: str) -> ScriptVersion | None:
    """The script the pipeline should act on: approved if any, else latest."""
    return approved_script_row(db, project_id) or latest_script_row(db, project_id)


def _script_for_media(db: Session, project_id: str) -> ScriptVersion:
    """Return the current script only when a vision draft is explicitly approved."""
    latest = latest_script_row(db, project_id)
    if latest is None:
        raise PipelineError("generate a script first")
    if latest.generator == "vision_evidence_v2":
        approved = approved_script_row(db, project_id)
        if (
            latest.approved_at is None
            or not latest.approved_by
            or approved is None
            or approved.id != latest.id
        ):
            raise PipelineError("latest evidence-backed script must be explicitly approved")
        return latest
    current = current_script(db, project_id)
    if current is None:
        raise PipelineError("generate a script first")
    return current


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

    row = latest_analysis(db, project_id)
    if row is None:
        return None
    manifest = row.coverage_manifest_json if isinstance(row.coverage_manifest_json, Mapping) else {}
    reconciliation = row.reconciliation_json if isinstance(row.reconciliation_json, Mapping) else {}
    graph = row.evidence_graph_json if isinstance(row.evidence_graph_json, Mapping) else {}
    blocking = row.blocking_reasons_json if isinstance(row.blocking_reasons_json, Mapping) else {}
    safe_findings = []
    for finding in blocking.get("findings", []) if isinstance(blocking.get("findings", []), list) else []:
        if not isinstance(finding, Mapping):
            continue
        safe_findings.append(
            {
                key: value
                for key, value in finding.items()
                if key in _SAFE_STATUS_FINDING_KEYS
                and isinstance(value, (str, int, float, bool))
            }
        )
    return {
        "state": row.state,
        "run_id": row.analysis_run_id,
        "provider_type": row.provider_type,
        "provider_name": row.provider_name,
        "model": row.model_name,
        "instruction_version": row.instruction_version,
        "instruction_sha256": row.instruction_sha256,
        "coverage_map_version": manifest.get("coverage_map_version"),
        "coverage_map_hash": manifest.get("coverage_map_hash"),
        "total_panels": manifest.get("total_panels", 0),
        "processed_panels": manifest.get("processed_panels", 0),
        "source_content_coverage_ratio": manifest.get("source_content_coverage_ratio", 0.0),
        "unresolved_material_area": manifest.get("unresolved_material_area", 0),
        "reconciliation_complete": manifest.get("reconciliation_complete", False),
        "chain_reconciled": reconciliation.get("chain_reconciled", False),
        "claim_count": len(graph.get("claims", [])) if isinstance(graph.get("claims"), list) else 0,
        "passage_count": len(graph.get("script_passages", [])) if isinstance(graph.get("script_passages"), list) else 0,
        "narrative_profile_id": (
            reconciliation.get("narrative_identity", {}).get("profile_id")
            if isinstance(reconciliation.get("narrative_identity"), Mapping)
            else None
        ),
        "narrative_profile_version": (
            reconciliation.get("narrative_identity", {}).get("version")
            if isinstance(reconciliation.get("narrative_identity"), Mapping)
            else None
        ),
        "narrative_profile_sha256": (
            reconciliation.get("narrative_identity", {}).get("sha256")
            if isinstance(reconciliation.get("narrative_identity"), Mapping)
            else None
        ),
        "narrative_screening_warning_codes": [
            code
            for code in reconciliation.get("narrative_screening_warning_codes", [])
            if isinstance(code, str)
        ]
        if isinstance(reconciliation.get("narrative_screening_warning_codes"), list)
        else [],
        "blocking_codes": [
            code for code in blocking.get("codes", [])
            if isinstance(code, str) and code in _VISION_BLOCKING_CODES
        ] if isinstance(blocking.get("codes", []), list) else [],
        "findings": safe_findings,
    }


# --- stage: script ---------------------------------------------------------


def generate_script(
    db: Session,
    project_id: str,
    *,
    keep_locked: bool = True,
    hook_count: int = 3,
    seed: int | None = None,
    actor_id: str = "",
    narrative_profile_id: str | None = None,
) -> ScriptVersion:
    """Materialize provider passages from the latest reconciled evidence."""
    project = get_project(db, project_id)
    row = latest_analysis(db, project_id)
    if row is None:
        raise PipelineError("run vision analysis before generating a script")
    if row.state != "RECONCILED":
        raise PipelineError("script generation requires reconciled vision analysis")
    profile = _requested_narrative_profile(row, narrative_profile_id)
    output, panels = _validated_persisted_vision_output(db, row)
    previous = latest_script_row(db, project_id)
    _ = (keep_locked, hook_count, seed, actor_id)
    claim_map = {
        claim["claim_id"]: claim
        for claim in output["evidence_graph"]["claims"]
        if isinstance(claim, Mapping)
    }
    panel_orders = {panel.panel_id: panel.source_order for panel in panels}
    sections: list[dict[str, Any]] = []
    passage_count = len(output["script_passages"])
    duration_estimator = (
        script_svc.estimate_narration_duration
        if profile is not None
        else script_svc.estimate_duration
    )
    for index, passage in enumerate(output["script_passages"]):
        role = passage["editorial_role"]
        claim_ids = list(passage["claim_ids"])
        evidence_panel_ids = list(passage["evidence_panel_ids"])
        evidence = [
            {
                "claim_id": claim_id,
                "panel_ids": list(claim_map[claim_id]["evidence_panel_ids"]),
            }
            for claim_id in claim_ids
        ]
        if profile is None:
            section_name = _VISION_ROLE_TO_SECTION[role]
        else:
            if index == 0:
                section_name = ScriptSection.HOOK.value
            elif index == 1:
                section_name = ScriptSection.SETUP.value
            elif index == passage_count - 1:
                section_name = ScriptSection.CTA.value
            elif index == passage_count - 2 and passage_count >= 5:
                section_name = ScriptSection.TWIST.value
            else:
                section_name = ScriptSection.CONFLICT.value
        sections.append(
            {
                "section": section_name,
                "text": passage["text"],
                "locked": False,
                "editorial_role": role,
                "claim_ids": claim_ids,
                "evidence_panel_ids": evidence_panel_ids,
                "evidence": evidence,
                "estimated_duration": duration_estimator(
                    passage["text"], project.narration_style
                ),
                "citations": sorted({panel_orders[panel_id] for panel_id in evidence_panel_ids}),
            }
        )

    warnings: list[dict[str, Any]] = []
    if profile is not None:
        claims_for_screen = {
            claim["claim_id"]: claim
            for claim in output["evidence_graph"]["claims"]
            if isinstance(claim, Mapping) and isinstance(claim.get("claim_id"), str)
        }
        report = editorial_qc.screen_narrative_naturalness(
            output["script_passages"], claims_for_screen, profile
        )
        for check in quality_svc.check_narrative_naturalness(report):
            if not check.passed:
                warnings.append(
                    {
                        "code": check.code,
                        "severity": check.severity,
                        "message": check.message,
                        "detail": dict(check.detail),
                    }
                )
        reconciliation = dict(row.reconciliation_json or {})
        reconciliation["narrative_screening_warning_codes"] = [
            warning["code"] for warning in warnings
        ]
        row.reconciliation_json = reconciliation

    script_text = "\n".join(section["text"] for section in sections)
    if profile is not None:
        estimated_duration = script_svc.estimate_narration_duration(
            script_text,
            project.narration_style,
        )
        script_word_count = script_svc.narration_word_count(script_text)
    else:
        estimated_duration = round(
            sum(section["estimated_duration"] for section in sections),
            2,
        )
        script_word_count = script_svc.word_count(script_text)

    version = (previous.version + 1) if previous else 1
    script_row = ScriptVersion(
        project_id=project_id,
        version=version,
        sections=sections,
        hook_options=[output["script_passages"][0]["text"]],
        selected_hook=0,
        estimated_duration=estimated_duration,
        word_count=script_word_count,
        warnings=warnings,
        generator="vision_evidence_v3" if profile is not None else "vision_evidence_v2",
        editorial_metadata={
            "analysis_id": row.id,
            "analysis_run_id": row.analysis_run_id,
            "instruction_version": row.instruction_version,
            "instruction_sha256": row.instruction_sha256,
            "human_review_required": True,
            "editorial_review_confirmed": False,
            "editorial_review_actor": "",
        },
    )
    if profile is not None:
        script_row.editorial_metadata["narrative_identity"] = {
            "profile_id": profile.profile_id,
            "version": profile.profile_version,
            "sha256": profile.contract_sha256,
        }
        script_row.editorial_metadata["duration_contract"] = (
            script_svc.narration_duration_contract(project.narration_style)
        )
    db.add(script_row)
    row.state = "SCRIPT_DRAFT"
    project.status = ProjectStatus.REVIEW
    audit(db, "script.generate", "project", project_id, actor_id, version=version)
    db.flush()
    return script_row


def update_script(
    db: Session,
    script_id: str,
    sections: list[dict],
    *,
    selected_hook: int | None = None,
    actor_id: str = "",
) -> ScriptVersion:
    """Apply user edits. Editing clears approval so review cannot be bypassed."""
    script = db.get(ScriptVersion, script_id)
    if script is None:
        raise PipelineError("script version not found")
    project = get_project(db, script.project_id)
    use_narration_duration_contract = script.generator == "vision_evidence_v3"
    duration_estimator = (
        script_svc.estimate_narration_duration
        if use_narration_duration_contract
        else script_svc.estimate_duration
    )

    valid_sections = {s.value for s in ScriptSection}
    cleaned: list[dict] = []
    for section in sections:
        name = section.get("section")
        if name not in valid_sections:
            raise PipelineError(f"unknown script section: {name!r}")
        text = str(section.get("text", "")).strip()
        cleaned.append(
            {
                "section": name,
                "text": text,
                "locked": bool(section.get("locked", False)),
                "editorial_role": str(section.get("editorial_role", "")),
                "claim_ids": list(section.get("claim_ids", []) or []),
                "evidence_panel_ids": list(section.get("evidence_panel_ids", []) or []),
                "evidence": list(section.get("evidence", []) or []),
                "estimated_duration": duration_estimator(
                    text, project.narration_style
                ),
                "citations": list(section.get("citations", []) or []),
            }
        )

    script.sections = cleaned
    if selected_hook is not None:
        script.selected_hook = max(0, min(selected_hook, max(0, len(script.hook_options) - 1)))
    if use_narration_duration_contract:
        script.estimated_duration = script_svc.estimate_narration_duration(
            script.plain_text,
            project.narration_style,
        )
        script.word_count = script_svc.narration_word_count(script.plain_text)
    else:
        script.estimated_duration = round(
            sum(s["estimated_duration"] for s in cleaned), 2
        )
        script.word_count = script_svc.word_count(script.plain_text)
    metadata = dict(script.editorial_metadata or {})
    metadata["human_review_required"] = True
    metadata["editorial_review_confirmed"] = False
    metadata["editorial_review_actor"] = ""
    if use_narration_duration_contract:
        metadata["duration_contract"] = script_svc.narration_duration_contract(
            project.narration_style
        )
    script.editorial_metadata = metadata

    # Any edit invalidates a previous approval.
    script.approved_at = None
    script.approved_by = ""
    analysis = latest_analysis(db, script.project_id)
    if analysis is not None and (
        not metadata.get("analysis_id") or metadata.get("analysis_id") == analysis.id
    ):
        analysis.state = "SCRIPT_DRAFT"
    audit(db, "script.update", "script_version", script.id, actor_id)
    db.flush()
    return script


def approve_script(
    db: Session,
    script_id: str,
    actor_id: str = "",
    *,
    editorial_review_confirmed: bool = False,
) -> ScriptVersion:
    """Approve only a current, explicitly confirmed evidence-backed script."""
    script = db.get(ScriptVersion, script_id)
    if script is None:
        raise PipelineError("script version not found")
    if not actor_id.strip():
        raise PipelineError("an editorial review actor is required")
    if editorial_review_confirmed is not True:
        raise PipelineError("explicit editorial review confirmation is required")
    metadata = script.editorial_metadata if isinstance(script.editorial_metadata, Mapping) else {}
    analysis = latest_analysis(db, script.project_id)
    if analysis is None or analysis.state != "SCRIPT_DRAFT":
        raise PipelineError("script approval requires the linked SCRIPT_DRAFT analysis")
    if (
        metadata.get("analysis_id") != analysis.id
        or metadata.get("analysis_run_id") != analysis.analysis_run_id
    ):
        raise PipelineError("script is not linked to the latest analysis")
    output, panels = _validated_persisted_vision_output(
        db,
        analysis,
        required_state="SCRIPT_DRAFT",
    )
    claim_map = {
        claim["claim_id"]: claim
        for claim in output["evidence_graph"]["claims"]
        if isinstance(claim, Mapping)
    }
    panel_ids = {panel.panel_id for panel in panels}
    profile = _narrative_identity_from_analysis(analysis)
    script_identity = metadata.get("narrative_identity")
    if profile is not None:
        if (
            script.generator != "vision_evidence_v3"
            or not isinstance(script_identity, Mapping)
            or script_identity.get("profile_id") != profile.profile_id
            or script_identity.get("version") != profile.profile_version
            or script_identity.get("sha256") != profile.contract_sha256
        ):
            raise PipelineError("narrative_identity_invalid")
        if metadata.get("human_review_required") is not True:
            raise PipelineError("narrative_identity_invalid")
        if not 4 <= len(script.sections or []) <= 6:
            raise PipelineError("script must contain four to six evidence-backed sections")
        for section in script.sections:
            if (
                not isinstance(section, Mapping)
                or not str(section.get("editorial_role", "")).strip()
                or not str(section.get("text", "")).strip()
            ):
                raise PipelineError("script section roles or text are invalid")
            claim_ids = section.get("claim_ids")
            evidence_panel_ids = section.get("evidence_panel_ids")
            if (
                not isinstance(claim_ids, list)
                or not claim_ids
                or not isinstance(evidence_panel_ids, list)
                or not evidence_panel_ids
                or not set(claim_ids) <= set(claim_map)
                or not set(evidence_panel_ids) <= panel_ids
            ):
                raise PipelineError("script section evidence is incomplete")
            required_evidence = set().union(
                *(set(claim_map[claim_id]["evidence_panel_ids"]) for claim_id in claim_ids)
            )
            if not required_evidence <= set(evidence_panel_ids):
                raise PipelineError("script section evidence does not cover its claims")
        current_passages = [
            {
                "text": section["text"],
                "claim_ids": list(section["claim_ids"]),
                "evidence_panel_ids": list(section["evidence_panel_ids"]),
            }
            for section in script.sections
        ]
        report = editorial_qc.screen_narrative_naturalness(
            current_passages, claim_map, profile
        )
        refreshed_warnings = [
            {
                "code": check.code,
                "severity": check.severity,
                "message": check.message,
                "detail": dict(check.detail),
            }
            for check in quality_svc.check_narrative_naturalness(report)
            if not check.passed
        ]
        if any(item["severity"] == "error" for item in refreshed_warnings):
            raise PipelineError("narrative editorial screening failed")
        script.warnings = refreshed_warnings
    else:
        if len(script.sections or []) != len(_VISION_SCRIPT_ROLES):
            raise PipelineError("script must contain exactly five evidence-backed sections")
        for expected_role, section in zip(_VISION_SCRIPT_ROLES, script.sections, strict=True):
            if (
                section.get("section") != _VISION_ROLE_TO_SECTION[expected_role]
                or section.get("editorial_role") != expected_role
                or not str(section.get("text", "")).strip()
            ):
                raise PipelineError("script section roles or text are invalid")
            claim_ids = section.get("claim_ids")
            evidence_panel_ids = section.get("evidence_panel_ids")
            if (
                not isinstance(claim_ids, list)
                or not claim_ids
                or not isinstance(evidence_panel_ids, list)
                or not evidence_panel_ids
                or not set(claim_ids) <= set(claim_map)
                or not set(evidence_panel_ids) <= panel_ids
            ):
                raise PipelineError("script section evidence is incomplete")
            required_evidence = set().union(
                *(set(claim_map[claim_id]["evidence_panel_ids"]) for claim_id in claim_ids)
            )
            if not required_evidence <= set(evidence_panel_ids):
                raise PipelineError("script section evidence does not cover its claims")
    blocking = [w for w in (script.warnings or []) if w.get("severity") == "error"]
    if blocking:
        raise PipelineError(
            "Fix these before approving: "
            + "; ".join(w.get("message", w.get("code", "")) for w in blocking)
        )
    if not script.plain_text.strip():
        raise PipelineError("script is empty")

    script.approved_at = _now()
    script.approved_by = actor_id
    script.editorial_metadata = {
        **metadata,
        "human_review_required": True,
        "editorial_review_confirmed": True,
        "editorial_review_actor": actor_id,
    }
    analysis.state = "SCRIPT_APPROVED"
    audit(db, "script.approve", "script_version", script.id, actor_id, version=script.version)
    db.flush()
    return script


# --- stage: voice-over -----------------------------------------------------


def generate_voiceover(
    db: Session,
    project_id: str,
    *,
    speed: float = 1.15,
    provider_name: str | None = None,
    actor_id: str = "",
) -> list[AudioSegment]:
    """Synthesise one clip per script section, replacing any previous audio."""
    project = get_project(db, project_id)
    script = _script_for_media(db, project_id)

    # BYOK: a verified speech key wins unless the caller forced a local provider.
    provider, tts_decision = resolver_svc.resolve_tts(
        db, project.workspace_id, override=provider_name
    )
    if provider.name != "null" and not provider.available():
        raise PipelineError(f"selected voice provider is unavailable: {provider.name}; no fallback voice is allowed")
    editorial_errors = [warning for warning in (script.warnings or []) if warning.get("severity") == "error"]
    if editorial_errors:
        raise PipelineError("editorial validation failed before TTS: " + "; ".join(item.get("message", item.get("code", "")) for item in editorial_errors[:4]))
    work = storage.workspace_dir(project_id, "audio")

    # Remove old segments and their files so storage does not grow unbounded.
    existing = list(
        db.scalars(select(AudioSegment).where(AudioSegment.script_version_id == script.id))
    )
    for segment in existing:
        if not segment.user_uploaded:
            storage.delete(segment.storage_key)
        db.delete(segment)
    db.flush()

    prepared: list[tuple[int, dict, str]] = []
    for index, section in enumerate(script.sections or []):
        text = (section.get("text") or "").strip()
        if text:
            prepared.append((index, section, script_svc.apply_pronunciations(text, project.pronunciations or {})))
    if not prepared:
        raise PipelineError("script has no spoken text")

    requested_voice_id = project.voice_id
    try:
        if isinstance(provider, tts_svc.HttpProvider):
            clips = provider.synthesize_sections(
                [spoken for _, _, spoken in prepared], work, requested_voice_id, speed
            )
        else:
            clips = [
                provider.synthesize(
                    spoken, work / f"{index:02d}_{section['section']}.wav", requested_voice_id, speed
                )
                for index, section, spoken in prepared
            ]
    except tts_svc.TTSError as exc:
        raise PipelineError(f"voice-over failed: {exc}") from exc

    created: list[AudioSegment] = []
    profile_hashes = {clip.voice_profile_hash for clip in clips}
    if len(profile_hashes) != 1:
        raise PipelineError("voice profile changed between chunks; refusing mixed narrator output")
    profile_hash = next(iter(profile_hashes))
    for (index, section, spoken), clip in zip(prepared, clips, strict=True):
        text = (section.get("text") or "").strip()
        display_text = timeline_svc.normalize_display_text(text)
        stored = storage.put_file(f"projects/{project_id}/audio", clip.path, clip.path.name)
        segment = AudioSegment(
            script_version_id=script.id,
            section=section["section"],
            order_index=index,
            text=text,
            spoken_text=spoken,
            display_text=display_text,
            voice_id=clip.voice_id,
            provider=clip.provider,
            voice_profile_hash=profile_hash,
            voice_profile=clip.voice_profile,
            storage_key=stored.storage_key,
            duration=clip.duration,
            word_timings=clip.word_timings,
            dramatic_events=editorial_timing.dramatic_events(clip.word_timings, project.language),
        )
        db.add(segment)
        created.append(segment)

    if not created:
        raise PipelineError("script has no spoken text")

    # Lay segments onto the master timeline.
    cursor = 0.0
    gap = 0.18
    for i, segment in enumerate(created):
        segment.start_time = round(cursor, 3)
        segment.end_time = round(cursor + segment.duration, 3)
        cursor = segment.end_time + (gap if i < len(created) - 1 else 0.0)

    audit(
        db, "voice.generate", "project", project_id, actor_id,
        segments=len(created), provider=provider.name,
        provider_source=tts_decision.source, model=tts_decision.model,
    )
    db.flush()
    return created


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
                PanelRegion.source_order > 0,
            )
            .order_by(PanelRegion.source_order, PanelRegion.panel_id, PanelRegion.id)
        )
    )
    regions = [region for region in regions if int(region.source_order) > 0]
    if not regions:
        return ()
    assets = {asset.id: asset for asset in images}
    panel_crops: dict[str, Image.Image] = {}
    panel_candidates: dict[str, object] = {}
    panel_regions_for_builder: list[PanelRegion] = []
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
            else:
                source_path = resolved_source_paths.get(str(asset.id))
                if source_path is None:
                    source_path = _review_reference_source_path(
                        asset,
                        source_root=review_source_root,
                    )
                    resolved_source_paths[str(asset.id)] = source_path
                source = Image.open(source_path)
                source.load()
            source_dimensions = tuple(int(value) for value in source.size)
            bounds = _panel_region_bounds(region)
            # Clamp stale/legacy panel bounds to the source asset. Regions whose
            # bounds lie completely outside the asset are corrupt and cannot be
            # framed meaningfully; skip them instead of cropping garbage text.
            src_w, src_h = source_dimensions
            if bounds[0] >= src_w or bounds[1] >= src_h or bounds[2] <= 0 or bounds[3] <= 0:
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
            if crop.width < 32 or crop.height < 32:
                continue
            # Extremely thin slices (segmentation artifacts that cut a single
            # panel into horizontal slivers) cannot frame meaningful content
            # and read as jarring close-ups. Skip them.
            if crop.height < 400:
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
            raise PipelineError(f"{exc.code}: {exc}") from exc
        except (OSError, UnidentifiedImageError, ValueError, storage.StorageError):
            # An empty exact registry still routes through the explicit planner
            # boundary, which raises visual.panel_lineage_unavailable. This keeps
            # pre-Task7 callers from receiving a source-path traceback.
            return ()
    default_evidence, default_citations, default_beats = (
        reference_visual_review.section_evidence_maps(script)
    )
    return _build_reference_panel_fallback_candidates(
        panel_regions=panel_regions_for_builder or regions,
        panel_candidates_by_region_id=panel_candidates,
        panel_crops_by_region_id=panel_crops,
        section_evidence_panel_ids=section_evidence_panel_ids or default_evidence,
        section_citations=section_citations or default_citations,
        beats_by_section=beats_by_section or default_beats,
        profile=profile,
        source_upscale_manifests_by_region_id=source_upscale_manifests,
        allow_missing_explicit=review_source_upscale_policy is not None,
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
    if not 50.0 <= float(duration_s) <= 60.0:
        raise PipelineError(
            "review.provisional_duration_invalid: silent review duration must be 50-60 seconds"
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
        return 50.0, 60.0
    return float(profile.duration_min_s), float(profile.duration_max_s)


def build_timeline(
    db: Session,
    project_id: str,
    actor_id: str = "",
    *,
    silent_reference_review: bool = False,
    review_source_upscale_policy: str | None = None,
    provisional_duration_s: float | None = None,
    reference_section_panel_ids: Mapping[str, Sequence[str]] | None = None,
    reference_section_citations: Mapping[str, Sequence[int]] | None = None,
    reference_beats_by_section: Mapping[str, Sequence[str]] | None = None,
    review_source_root: Path | None = None,
) -> list[TimelineScene]:
    """Derive scenes/cues from voice timing or explicit silent-review pacing."""
    project = get_project(db, project_id)
    profile = reference_profile.resolve_reference_profile(project.template)
    script = _script_for_media(db, project_id)

    review_policy = None
    if silent_reference_review:
        if profile is None:
            raise PipelineError(
                "review.upscale_requires_reference_profile: silent review requires reference mode"
            )
        try:
            review_policy = review_source_upscale.validate_review_upscale_request(
                review_source_upscale_policy,
                silent_reference_review=True,
                publish_allowed=False,
            )
        except review_source_upscale.ReviewSourceUpscaleError as exc:
            raise PipelineError(str(exc)) from exc
        if review_policy is None:
            raise PipelineError(
                "review.upscale_policy_required: silent source review requires the explicit upscale policy"
            )
        audio_duration = float(
            provisional_duration_s
            if provisional_duration_s is not None
            else getattr(script, "estimated_duration", 0.0) or 0.0
        )
        if not audio_duration:
            audio_duration = 51.3
        spans = _review_provisional_spans(script, audio_duration)
        segments: list[object] = []
    else:
        segments = audio_segments(db, script.id)
        if not segments:
            raise PipelineError("generate the voice-over before building the timeline")
        audio_duration = max((float(segment.end_time) for segment in segments), default=0.0)
        spans = spans_from_segments(segments)
    duration_min_s, duration_max_s = (
        _reference_duration_bounds(profile, silent_reference_review=silent_reference_review)
        if profile is not None
        else (0.0, float("inf"))
    )
    if profile is not None and not duration_min_s <= audio_duration <= duration_max_s:
        raise PipelineError(
            f"{profile.profile_id} requires audio duration between "
            f"{duration_min_s:.1f} and {duration_max_s:.1f} seconds"
        )

    assets = project_assets(db, project_id)
    images = image_assets(assets)

    scored = visual_scoring.analyze_assets(images, storage.read_bytes)
    # Director decides story beats and visual timing first. The Shot Sequencer
    # then turns those beats into ROI shots; reference mode uses exact panel
    # candidates keyed by PanelRegion before any fallback is considered.
    from app.services import editorial_visual_planner

    candidate_registry: dict[str, object] = {}
    reference_candidates: tuple[object, ...] | None = None
    if profile is not None:
        if any(
            value is not None
            for value in (
                review_policy,
                reference_section_panel_ids,
                reference_section_citations,
                reference_beats_by_section,
            )
        ):
            reference_candidates = _load_reference_panel_fallback_candidates(
                db,
                project_id,
                script,
                images,
                profile,
                review_source_upscale_policy=review_policy,
                section_evidence_panel_ids=reference_section_panel_ids,
                section_citations=reference_section_citations,
                beats_by_section=reference_beats_by_section,
                review_source_root=review_source_root,
            )
        else:
            reference_candidates = _load_reference_panel_fallback_candidates(
                db, project_id, script, images, profile
            )
        candidate_registry = {
            str(candidate.panel_region_id): candidate
            for candidate in reference_candidates
        }
    try:
        planned = editorial_visual_planner.plan(
            spans,
            scored,
            profile=profile,
            cited_asset_ids_by_section=None if profile is not None else None,
            citation_alignment_reasons_by_section=None if profile is not None else None,
            reference_panel_candidates=reference_candidates,
            allow_source_resolution_warning=bool(
                review_policy is not None
                and review_policy.allow_low_source_resolution_warning
            ),
            allow_review_cadence_adaptation=silent_reference_review,
            allow_review_duration=silent_reference_review,
        )
    except editorial_visual_planner.ReferencePlanningError as exc:
        raise PipelineError(
            f"reference_planning_failed: {exc.code}: {exc}"
        ) from exc

    if profile is not None:
        planned = _bind_reference_panel_regions(
            db,
            project_id,
            script,
            images,
            planned,
            candidate_registry=candidate_registry,
            review_source_upscale_policy=review_policy,
        )
        for shot in planned:
            ledger = shot.get("fallback_attempts")
            if isinstance(ledger, list):
                try:
                    shot["rejected_candidates"] = reference_visual_review.attach_accepted_mask_snapshot(
                        shot, candidate_registry
                    )
                except reference_visual_review.ReferenceReviewError as exc:
                    raise PipelineError(f"{exc.code}: {exc}") from exc

    for old in db.scalars(select(TimelineScene).where(TimelineScene.project_id == project_id)):
        db.delete(old)
    for old_cue in db.scalars(select(SubtitleCue).where(SubtitleCue.project_id == project_id)):
        db.delete(old_cue)
    db.flush()
    # Audit remains observable, but sparse/low-information fixtures must still
    # render; the Director has already exhausted available ROI alternatives.
    editorial_issues = director_svc.audit_sequence(planned)
    if editorial_issues:
        audit(db, "director.audit", "project", project_id, actor_id, issues=editorial_issues)
    specs = [
        timeline_svc.SceneSpec(
            order_index=shot["order_index"],
            section=shot["section"],
            start_time=shot["start_time"],
            end_time=shot["end_time"],
            asset_id=shot["asset_id"],
            source_family=shot.get("source_family", ""),
            focus_x=shot["focus_x"],
            focus_y=shot["focus_y"],
            focus_end_x=shot.get("focus_end_x", shot["focus_x"]),
            focus_end_y=shot.get("focus_end_y", shot["focus_y"]),
            roi_label=shot.get("roi_label", ""),
            camera_curve=shot.get("camera_curve", shot["effect"]),
            motion_mode=shot.get("motion_mode", "hold"),
            motion_intensity=shot.get("motion_intensity", "low"),
            motion_reason=shot.get("motion_reason", ""),
            camera_intent=shot.get("camera_intent", "neutral"),
            narration_timing=shot.get("narration_timing", "narration_lead"),
            effect=shot["effect"],
            disabled_effects=shot.get("disabled_effects", []),
            overlay_text=shot.get("overlay_text", ""),
            transition=shot.get("transition", "fade" if shot["order_index"] else "none"),
            alignment_score=shot.get("alignment_score", 0.0),
            alignment_reasons=shot.get("alignment_reasons", []),
            rejected_candidates=shot.get("rejected_candidates", []),
            visual_signature=shot.get("visual_signature", ""),
            panel_region_id=shot.get("panel_region_id"),
            panel_id=shot.get("panel_id", ""),
            panel_bounds=shot.get("panel_bounds"),
            visual_evidence=shot.get("visual_evidence"),
            source_asset_checksum=shot.get("source_asset_checksum", ""),
        )
        for shot in planned
    ]
    scenes: list[TimelineScene] = []
    for spec in specs:
        scene = TimelineScene(
            project_id=project_id,
            asset_id=spec.asset_id,
            source_family=spec.source_family,
            order_index=spec.order_index,
            section=spec.section,
            start_time=spec.start_time,
            end_time=spec.end_time,
            focus_x=spec.focus_x,
            focus_y=spec.focus_y,
            focus_end_x=spec.focus_end_x,
            focus_end_y=spec.focus_end_y,
            roi_label=spec.roi_label,
            camera_curve=spec.camera_curve,
            motion_mode=spec.motion_mode,
            motion_intensity=spec.motion_intensity,
            motion_reason=spec.motion_reason,
            camera_intent=spec.camera_intent,
            narration_timing=spec.narration_timing,
            effect=spec.effect,
            disabled_effects=spec.disabled_effects,
            transition=spec.transition,
            alignment_score=getattr(spec, "alignment_score", 0.0),
            alignment_reasons=getattr(spec, "alignment_reasons", []),
            rejected_candidates=getattr(spec, "rejected_candidates", []),
            visual_signature=getattr(spec, "visual_signature", ""),
            panel_region_id=spec.panel_region_id,
            panel_id=spec.panel_id,
            panel_bounds_json=_panel_bounds_json(spec.panel_bounds)
            if spec.panel_bounds is not None
            else {},
            visual_evidence_json=dict(spec.visual_evidence or {}),
            source_asset_checksum=spec.source_asset_checksum,
        )
        db.add(scene)
        scenes.append(scene)

    for cue in timeline_svc.build_cues(spans, media_duration=max((span.end_time for span in spans), default=0.0)):
        db.add(
            SubtitleCue(
                project_id=project_id,
                order_index=cue.order_index,
                text=cue.text,
                start_time=cue.start_time,
                end_time=cue.end_time,
            )
        )

    audit(db, "timeline.build", "project", project_id, actor_id, scenes=len(scenes))
    db.flush()
    return scenes


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
        accepted_manifest = dict(accepted[0]["source_upscale_manifest"])
        try:
            review_source_upscale.validate_review_manifest_dimensions(
                accepted_manifest,
                (bounds[2] - bounds[0], bounds[3] - bounds[1]),
            )
        except review_source_upscale.ReviewSourceUpscaleError as exc:
            raise PipelineError(f"{exc.code}: {exc}") from exc
        if (
            tuple(accepted_manifest.get("source_panel_bounds", ())) != source_bounds
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
    source_path = (
        _review_reference_source_path(asset, source_root=review_source_root)
        if review_source_upscale_policy is not None
        else storage.path_for(asset.storage_key)
    )
    if not source_path.is_file():
        raise PipelineError(
            "visual.panel_lineage_unavailable: source asset file is unavailable"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source_path) as source:
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


def run_quality_checks(
    db: Session,
    project_id: str,
    job: RenderJob | None = None,
    actor_id: str = "",
) -> list[quality_svc.CheckResult]:
    """Run every gate and persist the results for the review UI."""
    project = get_project(db, project_id)
    assets = project_assets(db, project_id)
    script = current_script(db, project_id)
    segments = audio_segments(db, script.id) if script else []
    scenes = project_scenes(db, project_id)
    cues = cue_specs(project_cues(db, project_id))

    duration = 0.0
    if segments:
        duration = max(s.end_time for s in segments)
    if job and job.duration:
        duration = job.duration

    profile = reference_profile.resolve_reference_profile(project.template)
    caption_groups: tuple[object, ...] | None = None
    subtitle_contract: dict[str, object] | None = None
    subtitle_timing_error: str | None = None
    if profile is not None:
        subtitle_contract = subtitle_karaoke.contract_manifest(profile)
        try:
            caption_groups = subtitle_karaoke.build_sentence_groups_from_segments(segments)
        except ValueError as exc:
            subtitle_timing_error = str(exc)

    results = quality_svc.run_all(
        project,
        assets,
        script,
        segments,
        scenes,
        cues,
        job=job,
        duration=duration,
        caption_groups=caption_groups,
        subtitle_contract=subtitle_contract,
        subtitle_timing_error=subtitle_timing_error,
    )

    for old in db.scalars(select(QualityCheck).where(QualityCheck.project_id == project_id)):
        db.delete(old)
    db.flush()
    for result in results:
        db.add(
            QualityCheck(
                project_id=project_id,
                code=result.code,
                severity=result.severity,
                message=result.message,
                passed=result.passed,
            )
        )

    summary = quality_svc.summarise(results)
    db.add(
        QCHistorySnapshot(
            project_id=project_id,
            render_job_id=job.id if job else None,
            passed=not any(result.blocking for result in results),
            report={"checks": [asdict(result) for result in results], "summary": summary},
        )
    )
    audit(db, "quality.run", "project", project_id, actor_id, **summary)
    db.flush()
    return results


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


def override_warning(
    db: Session, project_id: str, code: str, reason: str, actor_id: str = ""
) -> QualityCheck:
    """Record an explicit, attributed override for a non-blocking warning."""
    if not reason.strip():
        raise PipelineError("an override reason is required")
    check = db.scalars(
        select(QualityCheck).where(
            QualityCheck.project_id == project_id, QualityCheck.code == code
        )
    ).first()
    if check is None:
        raise PipelineError(f"no quality check named {code!r} for this project")
    if check.severity == "error":
        raise PipelineError(f"{code} is a blocking error and cannot be overridden")
    check.override_reason = reason.strip()
    check.overridden_by = actor_id
    check.passed = True
    db.add(
        QCOverrideEvent(
            project_id=project_id,
            quality_code=code,
            actor_id=actor_id,
            reason=reason.strip(),
            before_passed=False,
            after_passed=True,
        )
    )
    audit(db, "quality.override", "project", project_id, actor_id, code=code, reason=reason.strip())
    db.flush()
    return check


# --- stage: render --------------------------------------------------------


def enqueue_render(
    db: Session,
    project_id: str,
    kind: str = "final",
    actor_id: str = "",
    encoder: str = "auto",
    profile: str = "Auto",
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
        if blocking:
            raise PipelineError(
                "Quality checks must pass before a final render: "
                + "; ".join(r.message for r in blocking[:3])
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
                transition="cut",
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
    media_duration = max(float(scene.end_time) for scene in scenes)
    if any(scene.publish_allowed is not False for scene in scene_inputs):
        raise PipelineError(
            "reference.publish_not_allowed: publish_allowed must be false for silent review scenes"
        )
    if review_source_upscale_policy is not None:
        script = _script_for_media(db, job.project_id)
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
    )


def render_silent_review_preview(
    db: Session,
    project_id: str,
    *,
    actor_id: str = "",
    review_source_upscale_policy: str = review_source_upscale.REVIEW_SOURCE_UPSCALE_POLICY_ID,
    review_source_root: Path,
    output_dir: Path | None = None,
) -> tuple[RenderJob, object]:
    """Render and persist one video-only review attempt through the regular path."""

    from app.services import render as render_svc
    from app.services import review_preview

    project = get_project(db, project_id)
    if not project_scenes(db, project_id):
        raise PipelineError("visual.panel_lineage_unavailable: build the review timeline first")
    root = Path(output_dir or settings.output_dir) / project_id / "review"
    output = root / "silent_preview.mp4"
    job = RenderJob(
        project_id=project_id,
        kind="preview",
        status=JobStatus.QUEUED,
        stage="review-preview-queued",
        encoder_requested="auto",
        render_profile="Auto",
    )
    db.add(job)
    db.flush()
    started = time.monotonic()
    try:
        request = build_render_request(
            db,
            job,
            silent_reference_review=True,
            output_override=output,
            review_source_upscale_policy=review_source_upscale_policy,
            review_source_root=Path(review_source_root),
        )
        result = render_svc.render_video(request)
        artifacts = review_preview.write_review_preview_bundle(
            db,
            project_id,
            result,
            output_dir=root,
            subtitle_contract=request.subtitle_contract,
            subtitle_timing_source=request.subtitle_timing_source,
        )
    except (render_svc.RenderError, PipelineError, review_preview.ReviewPreviewError) as exc:
        job.status = JobStatus.FAILED
        job.stage = "review-preview-failed"
        job.error_code = str(getattr(exc, "code", "review.preview_failed"))[:80]
        job.error_message = str(exc)[:1000]
        project.status = ProjectStatus.REVIEW
        db.flush()
        raise PipelineError(f"{job.error_code}: review preview failed") from exc
    job.status = JobStatus.SUCCEEDED
    job.progress = 100
    job.stage = "review-preview-ready"
    job.completed_at = _now()
    job.output_key = str(result.output_path)
    job.subtitle_key = str(result.subtitle_path) if result.subtitle_path else ""
    job.checksum = result.checksum
    job.duration = result.duration
    job.width = result.width
    job.height = result.height
    job.encoder = result.encoder
    job.encoder_hardware = result.encoder_hardware
    job.encoder_fell_back = result.encoder_fell_back
    job.encoder_reason = result.encoder_reason[:1000]
    job.render_wall_seconds = round(time.monotonic() - started, 3)
    project.status = ProjectStatus.REVIEW
    project.error_message = ""
    audit(
        db,
        "review.preview.ready",
        "render_job",
        job.id,
        actor_id,
        output=str(result.output_path),
        checksum=result.checksum,
        duration=result.duration,
        publish_allowed=False,
        voice_state="VISUAL_ONLY_WAITING_FOR_VOICE",
    )
    db.flush()
    return job, artifacts

def build_render_request(
    db: Session,
    job: RenderJob,
    *,
    silent_reference_review: bool = False,
    output_override: Path | None = None,
    review_source_upscale_policy: str | None = None,
    review_source_root: Path | None = None,
):
    """Assemble a RenderRequest from persisted state."""
    from app.services import render as render_svc

    project = get_project(db, job.project_id)
    editorial_profile = reference_profile.resolve_reference_profile(project.template)
    try:
        upscale_policy = review_source_upscale.validate_review_upscale_request(
            review_source_upscale_policy,
            silent_reference_review=silent_reference_review,
            publish_allowed=False,
        )
    except review_source_upscale.ReviewSourceUpscaleError as exc:
        raise PipelineError(str(exc)) from exc
    if upscale_policy is not None and editorial_profile is None:
        raise PipelineError(
            "review.upscale_requires_reference_profile: source upscale requires reference mode"
        )
    if silent_reference_review and editorial_profile is not None:
        return _build_silent_reference_request(
            db,
            job,
            project,
            editorial_profile,
            output_override=output_override,
            review_source_upscale_policy=upscale_policy,
            review_source_root=review_source_root,
        )
    script = current_script(db, job.project_id)
    if script is None:
        raise PipelineError("no script to render")

    segments = audio_segments(db, script.id)
    if not segments:
        if editorial_profile is not None:
            raise PipelineError(
                "subtitle.word_timing_missing: regular reference render requires authoritative audio timing"
            )
        raise PipelineError("no voice-over to render")

    sentence_groups: tuple[object, ...] = ()
    subtitle_contract: dict[str, object] | None = None
    subtitle_contract_version = ""
    subtitle_timing_source = ""

    scenes = project_scenes(db, job.project_id)
    if not scenes:
        raise PipelineError("no scenes to render")
    if editorial_profile is not None:
        for scene in scenes:
            if not scene.asset_id:
                raise PipelineError(
                    "visual.panel_lineage_unavailable: reference scene has no source asset"
                )
            asset = db.get(SourceAsset, scene.asset_id)
            if asset is None or not storage.exists(asset.storage_key):
                raise PipelineError(
                    "visual.panel_lineage_unavailable: reference scene asset is unavailable"
                )
        try:
            sentence_groups = subtitle_karaoke.build_sentence_groups_from_segments(segments)
        except ValueError as exc:
            raise PipelineError(str(exc)) from exc
        try:
            sentence_groups = render_svc.fit_sentence_karaoke_groups(
                sentence_groups,
                editorial_profile.final_width,
                editorial_profile.final_height,
                max_chars=subtitle_karaoke.CAPTION_MAX_CHARS,
                max_lines=subtitle_karaoke.CAPTION_MAX_LINES,
                active_scale=subtitle_karaoke.CAPTION_ACTIVE_SCALE,
                font_height_ratio=subtitle_karaoke.CAPTION_FONT_HEIGHT_RATIO,
                safe_margin_px=subtitle_karaoke.CAPTION_SAFE_MARGIN_PX,
            )
        except render_svc.RenderError as exc:
            raise PipelineError(f"{exc.code}: {exc}") from exc
        subtitle_contract = subtitle_karaoke.contract_manifest(editorial_profile)
        subtitle_contract_version = subtitle_karaoke.SUBTITLE_CONTRACT_VERSION
        subtitle_timing_source = "audio_segment.word_timings"

    # Concatenate the narration into one track.
    work = storage.workspace_dir(job.project_id, "audio")
    clip_paths = [storage.path_for(s.storage_key) for s in segments]
    missing = [p for p in clip_paths if not p.is_file()]
    if missing:
        raise PipelineError(
            f"{len(missing)} audio file(s) are missing. Regenerate the voice-over."
        )
    voice_path = work / "voice_master.wav"
    tts_svc.concat_audio(clip_paths, voice_path, gap=0.18)
    audio_duration = tts_svc.probe_duration(voice_path)

    # Each scene clip is rendered as a rounded number of configured-FPS frames. Clamp
    # subtitles to that actual joined-media duration, not only the audio probe;
    # otherwise a final partial frame can leave a cue past the MP4 end.
    scene_end_times = [scene.end_time for scene in scenes]
    scene_end_times[-1] = max(scene_end_times[-1], audio_duration)
    rendered_frames = sum(
        max(
            1,
            int(
                round(
                    max(0.1, round(end_time - scene.start_time, 3))
                    * settings.video_fps
                )
            ),
        )
        for scene, end_time in zip(scenes, scene_end_times, strict=True)
    )
    media_duration = round(
        min(audio_duration, rendered_frames / settings.video_fps), 3
    )

    persisted_cues = project_cues(db, job.project_id)
    cues = cue_specs(persisted_cues)
    for persisted, cue in zip(persisted_cues, cues, strict=True):
        cue.start_time = round(min(max(0.0, cue.start_time), media_duration), 3)
        cue.end_time = round(min(max(cue.start_time, cue.end_time), media_duration), 3)
        persisted.start_time = cue.start_time
        persisted.end_time = cue.end_time
    db.flush()

    scene_inputs: list = []
    music_path: Path | None = None
    audio_assets = [asset for asset in project_assets(db, job.project_id) if asset.type in {"audio", "music"} and asset.is_publishable and storage.exists(asset.storage_key)]
    if audio_assets:
        music_path = storage.path_for(audio_assets[0].storage_key)
    profile = job.render_profile or "Auto"
    panel_workspace = (
        storage.workspace_dir(job.project_id, "reference-panels")
        if editorial_profile is not None
        else None
    )
    for index, scene in enumerate(scenes):
        end_time = scene.end_time
        if index == len(scenes) - 1:
            end_time = max(end_time, audio_duration)
        start_time = scene.start_time
        motion_mode = scene.motion_mode
        camera_curve = scene.camera_curve
        if profile == "No motion" or profile == "Calm" and scene.camera_intent not in {"impact", "explosion"}:
            motion_mode, camera_curve = "hold", "static"
        elif profile == "Dynamic" and scene.camera_intent in {"action", "attack"}:
            motion_mode = "guided_pan" if scene.motion_mode == "hold" else scene.motion_mode
        image_path: Path | None = None
        if scene.asset_id:
            asset = db.get(SourceAsset, scene.asset_id)
            if asset and storage.exists(asset.storage_key):
                image_path = storage.path_for(asset.storage_key)
                if editorial_profile is not None:
                    image_path = _materialize_reference_panel_crop(
                        db,
                        asset,
                        scene,
                        panel_workspace / f"scene-{index:04d}.png",
                    )
            elif editorial_profile is not None:
                raise PipelineError(
                    "visual.panel_lineage_unavailable: reference scene asset is unavailable"
                )
        elif editorial_profile is not None:
            raise PipelineError(
                "visual.panel_lineage_unavailable: reference scene has no source asset"
            )
        reference_ledger = (
            list(getattr(scene, "rejected_candidates", []) or [])
            if editorial_profile is not None
            else []
        )
        accepted_reference = next(
            (
                entry
                for entry in reference_ledger
                if isinstance(entry, Mapping) and entry.get("accepted") is True
            ),
            {},
        )
        reference_telemetry = (
            accepted_reference.get("telemetry")
            if isinstance(accepted_reference, Mapping)
            else None
        )
        reference_roi = (
            reference_telemetry.get("selected_roi")
            if isinstance(reference_telemetry, Mapping)
            else None
        )
        reference_mask = (
            accepted_reference.get("border_mask")
            if isinstance(accepted_reference, Mapping)
            else None
        )

        scene_inputs.append(
            render_svc.SceneInput(
                image_path=image_path,
                start_time=start_time,
                end_time=end_time,
                focus_x=scene.focus_x,
                focus_y=scene.focus_y,
                focus_end_x=scene.focus_end_x,
                focus_end_y=scene.focus_end_y,
                camera_curve=camera_curve,
                motion_mode=motion_mode,
                motion_intensity=scene.motion_intensity,
                motion_reason=scene.motion_reason,
                effect=scene.effect,
                disabled_effects=scene.disabled_effects,
                transition=scene.transition,
                overlay_text=scene.overlay_text,
                panel_region_id=getattr(scene, "panel_region_id", None)
                if editorial_profile
                else None,
                panel_id=getattr(scene, "panel_id", "") if editorial_profile else "",
                panel_bounds=(
                    (
                        int(scene.panel_bounds_json["x"]),
                        int(scene.panel_bounds_json["y"]),
                        int(scene.panel_bounds_json["x"])
                        + int(scene.panel_bounds_json["width"]),
                        int(scene.panel_bounds_json["y"])
                        + int(scene.panel_bounds_json["height"]),
                    )
                    if editorial_profile
                    and isinstance(getattr(scene, "panel_bounds_json", None), Mapping)
                    else None
                ),
                visual_evidence=getattr(scene, "visual_evidence_json", None)
                if editorial_profile
                else None,
                source_asset_checksum=getattr(scene, "source_asset_checksum", "")
                if editorial_profile
                else "",
                source_asset_id=scene.asset_id if editorial_profile else "",
                source_order=(
                    accepted_reference.get("source_order")
                    if isinstance(accepted_reference, Mapping)
                    else None
                ),
                panel_size=(
                    (
                        int(scene.panel_bounds_json["width"]),
                        int(scene.panel_bounds_json["height"]),
                    )
                    if editorial_profile
                    and isinstance(getattr(scene, "panel_bounds_json", None), Mapping)
                    else None
                ),
                evidence_hash=(
                    accepted_reference.get("evidence_hash", "")
                    if isinstance(accepted_reference, Mapping)
                    else ""
                ),
                border_mask=reference_mask,
                selected_roi=reference_roi,
                fallback_attempts=reference_ledger,
                framing_telemetry=reference_telemetry,
                publish_allowed=not bool(editorial_profile),
            )
        )

    filename = "preview.mp4" if job.kind == "preview" else "final.mp4"
    return render_svc.RenderRequest(
        project_id=job.project_id,
        scenes=scene_inputs,
        audio_path=voice_path,
        cues=cues,
        output_path=storage.output_path(job.project_id, filename),
        preview=job.kind == "preview",
        title_text="" if editorial_profile else project.title,
        profile=editorial_profile,
        music_path=music_path,
        music_gain_db=-24.0,
        encoder=job.encoder_requested or None,
        sentence_groups=list(sentence_groups),
        subtitle_contract_version=subtitle_contract_version,
        subtitle_timing_source=subtitle_timing_source,
        subtitle_contract=subtitle_contract,
        persisted_reference_framing=bool(editorial_profile),
    )


def execute_render(db: Session, job_id: str) -> RenderJob:
    """Run a queued render to completion. Called by the worker."""
    from app.services import render as render_svc

    job = db.get(RenderJob, job_id)
    if job is None:
        raise PipelineError(f"render job {job_id} not found")

    # Claim atomically so API and standalone worker cannot double-render.
    if not claim_render_job(db, job_id):
        return job

    project = get_project(db, job.project_id)

    def progress(pct: int, stage: str) -> None:
        job.progress = max(0, min(100, int(pct)))
        job.stage = stage[:80]
        job.heartbeat_at = _now()
        job.lease_until = job.heartbeat_at + timedelta(seconds=1800)
        db.flush()
        db.commit()

    started_wall = time.monotonic()
    started_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    try:
        request = build_render_request(db, job)
        result = render_svc.render_video(request, progress=progress)
    except (render_svc.RenderError, PipelineError, tts_svc.TTSError) as exc:
        scratch = storage.workspace_dir(job.project_id, "render")
        if scratch.exists():
            import shutil
            shutil.rmtree(scratch, ignore_errors=True)
        job.status = JobStatus.FAILED
        job.completed_at = _now()
        job.error_code = getattr(exc, "code", "pipeline_error")
        job.error_message = str(exc)[:1000]
        job.log_tail = getattr(exc, "log_tail", "")[:4000]
        job.render_wall_seconds = round(time.monotonic() - started_wall, 3)
        job.peak_rss_bytes = max(0, int((resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - started_rss) * 1024))
        project.status = ProjectStatus.FAILED
        project.error_message = str(exc)[:1000]
        audit(db, "render.failed", "render_job", job.id, error=job.error_code)
        db.flush()
        db.commit()
        return job

    job.status = JobStatus.SUCCEEDED
    job.completed_at = _now()
    job.progress = 100
    job.stage = "done"
    job.output_key = str(result.output_path)
    job.subtitle_key = str(result.subtitle_path) if result.subtitle_path else ""
    job.thumbnail_key = str(result.thumbnail_path) if result.thumbnail_path else ""
    job.checksum = result.checksum
    job.duration = result.duration
    job.width = result.width
    job.height = result.height
    job.encoder = result.encoder
    job.encoder_hardware = result.encoder_hardware
    job.encoder_fell_back = result.encoder_fell_back
    job.encoder_reason = result.encoder_reason[:1000]
    job.render_wall_seconds = round(time.monotonic() - started_wall, 3)
    job.peak_rss_bytes = max(0, int((resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - started_rss) * 1024))
    job.scratch_bytes = result.scratch_bytes
    from app.services import editorial_qc

    scenes = project_scenes(db, job.project_id)
    cues = cue_specs(project_cues(db, job.project_id))
    assets = project_assets(db, job.project_id)
    source_findings = policy_svc.check_source_cleanliness(assets)
    test_only = any(
        "NOT_FOR_PUBLICATION" in (asset.original_filename or "").upper()
        or "NOT_FOR_PUBLICATION" in (asset.source_name or "").upper()
        for asset in assets
    )
    rights_confidence = 0 if test_only else (5 if all(asset.is_publishable for asset in assets) else 0)
    source_cleanliness = 0 if test_only or source_findings else 5
    if test_only and not any(getattr(f, "code", "") == "source.test_only" for f in source_findings):
        source_findings.append(policy_svc.PolicyFinding("source.test_only", policy_svc.CheckSeverity.ERROR, "NOT_FOR_PUBLICATION source is test-only."))
    render_profile = reference_profile.resolve_reference_profile(project.template)
    qc_scenes = scenes
    panel_evidence_by_key: dict[tuple[str, str], object] = {}
    panel_border_masks_by_key: dict[tuple[str, str], object] = {}
    panel_sizes_by_key: dict[tuple[str, str], tuple[int, int]] = {}
    telemetry_by_key: dict[tuple[str, str], object] = {}
    if render_profile is not None:
        enriched: list[object] = []
        for persisted_scene, input_scene in zip(scenes, request.scenes, strict=True):
            values = {
                key: value
                for key, value in vars(persisted_scene).items()
                if not key.startswith("_")
            }
            values.update(
                {
                    "source_asset_id": input_scene.source_asset_id,
                    "source_order": input_scene.source_order,
                    "panel_region_id": input_scene.panel_region_id,
                    "panel_id": input_scene.panel_id,
                    "panel_bounds": input_scene.panel_bounds,
                    "panel_size": input_scene.panel_size,
                    "visual_evidence": input_scene.visual_evidence,
                    "border_mask": input_scene.border_mask,
                    "selected_roi": input_scene.selected_roi,
                    "roi": input_scene.selected_roi,
                    "fallback_attempts": input_scene.fallback_attempts,
                    "framing_telemetry": input_scene.framing_telemetry,
                    "evidence_hash": input_scene.evidence_hash,
                    "source_asset_checksum": input_scene.source_asset_checksum,
                }
            )
            enriched.append(SimpleNamespace(**values))
            key = (str(input_scene.source_asset_id), str(input_scene.panel_region_id))
            if not key[0] or not key[1]:
                continue
            try:
                evidence = visual_scoring.parse_panel_visual_evidence(input_scene.visual_evidence or {})
                mask = render_svc._reference_border_mask_from_mapping(input_scene.border_mask)
            except (render_svc.RenderError, visual_scoring.VisualEvidenceError, TypeError, ValueError):
                continue
            panel_evidence_by_key[key] = evidence
            panel_border_masks_by_key[key] = mask
            if input_scene.panel_size is not None:
                panel_sizes_by_key[key] = tuple(input_scene.panel_size)
            telemetry_by_key[key] = input_scene.framing_telemetry
        qc_scenes = enriched
    qc = editorial_qc.build_report(
        scenes=qc_scenes,
        cues=cues,
        duration=result.duration,
        job_path=Path(result.output_path),
        rights_confidence=rights_confidence,
        source_cleanliness=source_cleanliness,
        voice_profile_count=len({segment.voice_profile_hash for segment in audio_segments(db, current_script(db, job.project_id).id) if segment.voice_profile_hash}),
        preview=job.kind == "preview",
        profile=render_profile,
        caption_groups=request.sentence_groups,
        subtitle_contract=request.subtitle_contract,
        subtitle_timing_error=None,
        panel_evidence_by_key=panel_evidence_by_key if render_profile is not None else None,
        panel_border_masks_by_key=panel_border_masks_by_key if render_profile is not None else None,
        panel_sizes_by_key=panel_sizes_by_key if render_profile is not None else None,
        telemetry_by_key=telemetry_by_key if render_profile is not None else None,
    )
    report_dir = Path(result.output_path).parent
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "final.qc.json").write_text(
        json.dumps(qc.as_dict(), indent=2), encoding="utf-8"
    )
    (report_dir / "shot_list.json").write_text(
        json.dumps([
            {
                "order_index": s.order_index,
                "asset_id": s.asset_id,
                "source_family": s.source_family,
                "roi_label": s.roi_label,
                "start_time": s.start_time,
                "end_time": s.end_time,
                "camera_intent": s.camera_intent,
                "camera_curve": s.camera_curve,
                "motion_mode": s.motion_mode,
                "motion_reason": s.motion_reason,
                "alignment_score": s.alignment_score,
                "alignment_reasons": s.alignment_reasons,
                "rejected_candidates": s.rejected_candidates,
                "visual_signature": s.visual_signature,
            }
            for s in scenes
        ], indent=2), encoding="utf-8"
    )
    (report_dir / "subtitle_list.json").write_text(
        json.dumps([asdict(c) for c in cues], indent=2), encoding="utf-8"
    )
    (report_dir / "panel_to_script_mapping.json").write_text(
        json.dumps([
            {
                "shot": s.order_index,
                "panel": s.asset_id,
                "section": s.section,
                "roi": s.roi_label,
                "alignment_score": s.alignment_score,
                "alignment_reasons": s.alignment_reasons,
                "rejected_candidates": s.rejected_candidates,
            }
            for s in scenes
        ], indent=2), encoding="utf-8"
    )
    (report_dir / "source_rights_report.json").write_text(
        json.dumps({
            "rights_confidence": rights_confidence,
            "source_cleanliness": source_cleanliness,
            "findings": [f.__dict__ for f in source_findings],
            "publishable": not source_findings and rights_confidence == 5,
        }, indent=2), encoding="utf-8"
    )
    (report_dir / "panel_catalog.json").write_text(
        json.dumps([
            {
                "asset_id": asset.id,
                "filename": asset.original_filename,
                "source_family": asset.source_family,
                "order_index": asset.order_index,
                "bbox": asset.panel_bbox,
                "quality": asset.panel_quality,
                "decision": asset.panel_decision,
            }
            for asset in assets
            if asset.type == "image"
        ], indent=2), encoding="utf-8"
    )
    # Contact sheet is a review artifact, not a publish asset.
    try:
        from PIL import Image, ImageDraw
        panel_assets = [asset for asset in assets if asset.type == "image" and asset.panel_decision != "reject"]
        thumbs = []
        for asset in panel_assets[:24]:
            path = storage.path_for(asset.storage_key)
            with Image.open(path) as image:
                thumb = image.convert("RGB")
                thumb.thumbnail((180, 260))
                card = Image.new("RGB", (200, 300), "white")
                card.paste(thumb, ((200 - thumb.width) // 2, 8))
                ImageDraw.Draw(card).text((8, 275), f"{asset.order_index}: {asset.original_filename[:24]}", fill="black")
                thumbs.append(card)
        if thumbs:
            columns = 4
            rows = (len(thumbs) + columns - 1) // columns
            sheet = Image.new("RGB", (columns * 200, rows * 300), "#d8d8d8")
            for index, thumb in enumerate(thumbs):
                sheet.paste(thumb, ((index % columns) * 200, (index // columns) * 300))
            sheet.save(report_dir / "contact_sheet.jpg", quality=88)
    except (OSError, ValueError):
        pass
    audit(db, "editorial.qc", "render_job", job.id, qc=qc.as_dict())
    project.status = ProjectStatus.READY if qc.qc_pass else ProjectStatus.REVIEW
    project.error_message = "" if qc.qc_pass else "; ".join(qc.failures)
    audit(
        db, "render.succeeded" if qc.qc_pass else "render.qc_blocked", "render_job", job.id,
        duration=result.duration, size=result.size_bytes,
        encoder=result.encoder, gpu=result.encoder_hardware,
        encoder_fell_back=result.encoder_fell_back,
    )
    db.flush()
    db.commit()
    return job


def retry_render(db: Session, job_id: str, actor_id: str = "") -> RenderJob:
    """Queue a fresh attempt, preserving the failed job for the audit trail."""
    old = db.get(RenderJob, job_id)
    if old is None:
        raise PipelineError("render job not found")
    if old.status == JobStatus.RUNNING:
        raise PipelineError("this render is still running")

    job = RenderJob(
        project_id=old.project_id,
        kind=old.kind,
        status=JobStatus.QUEUED,
        stage="queued",
        attempt=old.attempt + 1,
        # Keep the original encoder choice so a retry reproduces the same run.
        encoder_requested=old.encoder_requested or "auto",
    )
    db.add(job)
    project = get_project(db, old.project_id)
    project.status = ProjectStatus.RENDERING
    project.error_message = ""
    audit(db, "render.retry", "render_job", job.id, actor_id, previous=old.id, attempt=job.attempt)
    db.flush()
    return job


def next_queued_job(db: Session) -> RenderJob | None:
    return db.scalars(
        select(RenderJob)
        .where(RenderJob.status == JobStatus.QUEUED)
        .order_by(RenderJob.created_at)
        .limit(1)
    ).first()


def claim_render_job(db: Session, job_id: str, lease_seconds: int = 1800) -> bool:
    """Claim a queued job or reclaim an expired running lease."""
    now = _now()
    job = db.get(RenderJob, job_id)
    if job is None:
        return False
    reclaimable = job.status == JobStatus.RUNNING and job.lease_until and job.lease_until < now
    if job.status != JobStatus.QUEUED and not reclaimable:
        return False
    job.status = JobStatus.RUNNING
    job.started_at = job.started_at or now
    job.heartbeat_at = now
    job.lease_until = now + timedelta(seconds=lease_seconds)
    job.lease_token = secrets.token_hex(16)
    job.progress = 0
    job.error_code = ""
    job.error_message = ""
    db.flush()
    db.commit()
    return True


def recover_stale_jobs(db: Session) -> int:
    """Requeue expired workers, retaining the audit trail."""
    now = _now()
    stale = list(db.scalars(select(RenderJob).where(
        RenderJob.status == JobStatus.RUNNING,
        RenderJob.lease_until.is_not(None),
        RenderJob.lease_until < now,
    )))
    for job in stale:
        job.status = JobStatus.QUEUED
        job.stage = "recovered stale lease"
        job.lease_token = ""
        job.lease_until = None
        job.heartbeat_at = None
        job.attempt += 1
    db.flush()
    return len(stale)


# --- convenience: full draft ----------------------------------------------


def generate_draft(db: Session, project_id: str, actor_id: str = "", seed: int | None = None) -> dict:
    """Materialize a vision-evidence script draft and stop before media stages.

    Voice-over, timeline, cues, and rendering require explicit human approval.
    This path never starts analysis or falls back to a text/rules workflow.
    """
    row = latest_analysis(db, project_id)
    if row is None:
        raise PipelineError("run vision analysis before generating a draft")
    if row.state != "RECONCILED":
        raise PipelineError("draft generation requires reconciled vision analysis")
    script = generate_script(db, project_id, seed=seed, actor_id=actor_id)
    project = get_project(db, project_id)
    project.status = ProjectStatus.REVIEW
    db.flush()
    return {
        "script_id": script.id,
        "script_version": script.version,
        "estimated_duration": script.estimated_duration,
        "audio_duration": 0.0,
        "segments": 0,
        "scenes": 0,
        "cues": 0,
        "warnings": script.warnings,
    }
