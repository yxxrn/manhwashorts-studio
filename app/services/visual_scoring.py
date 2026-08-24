"""Content-aware panel scoring and shot planning.

Geometry only decides where a panel exists. This module inspects pixels, optional
face/OCR signals, edge density, composition, and repetition. Weights stay in one
dataclass so tuning does not touch the planner.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageStat

VISUAL_EVIDENCE_CONTRACT_VERSION = "COLOR_AGNOSTIC_BALLOON_FREE_V1"
_MASK_STATUSES = frozenset({"unknown", "known_empty", "known_nonempty"})
_BALLOON_KINDS = frozenset({"speech_balloon"})
_PROTECTED_KINDS = frozenset({"background", "subject", "face", "action", "effect", "continuity_context"})


# Keep Pillow 12+ and older supported without a hard dependency upgrade.
def _pixels(image: Image.Image) -> list[int]:
    return list(image.get_flattened_data()) if hasattr(image, "get_flattened_data") else list(image.getdata())


@dataclass(frozen=True)
class PanelScoreWeights:
    face: float = 2.4
    expression: float = 1.2
    action: float = 1.7
    weapon: float = 1.4
    monster: float = 1.6
    effects: float = 1.5
    motion_lines: float = 1.0
    impact: float = 1.5
    close_up: float = 1.1
    composition: float = 1.4
    object_density: float = 1.3
    semantic: float = 2.0
    continuity: float = 0.35
    empty: float = 2.2
    scenery: float = 1.8
    transition: float = 2.0
    speech_balloon: float = 2.0
    ui_overlay: float = 1.4
    blank_dominance: float = 2.2
    repeated: float = 1.6


WEIGHTS = PanelScoreWeights()


def asset_use_cap(shot_count: int) -> int:
    """Return the deterministic per-asset reuse ceiling for a shot list."""
    return max(2, int(math.floor(max(0, shot_count) * 0.12)))


@dataclass(frozen=True)
class VisualFeatures:
    face_visibility: float = 0.0
    facial_expression: float = 0.0
    action_pose: float = 0.0
    weapons: float = 0.0
    monsters: float = 0.0
    visual_effects: float = 0.0
    motion_lines: float = 0.0
    impact_frame: float = 0.0
    close_up: float = 0.0
    dramatic_composition: float = 0.0
    object_density: float = 0.0
    empty_background: float = 0.0
    scenery_only: float = 0.0
    transition: float = 0.0
    speech_balloon_dominance: float = 0.0
    ui_overlay_dominance: float = 0.0
    blank_dominance: float = 0.0
    ocr_text: str = ""
    semantic_tags: frozenset[str] = frozenset()
    focal_points: tuple[tuple[float, float], ...] = ((0.5, 0.4),)
    face_points: tuple[tuple[float, float], ...] = ()
    visual_signature: str = ""


@dataclass(frozen=True)
class PanelCandidate:
    asset_id: str
    order_index: int
    features: VisualFeatures
    visual_score: float
    semantic_score: float = 0.0
    source_family: str = ""


class VisualEvidenceError(ValueError):
    """Stable fail-closed error for typed visual evidence."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


VISUAL_EVIDENCE_PROMPT_VERSION = "balloon-free-visual-evidence-v1"
VISUAL_EVIDENCE_REPAIR_PROMPT_VERSION = "balloon-free-visual-evidence-repair-v1"
CONSERVATIVE_FULL_PANEL_EVIDENCE_SOURCE = "conservative_full_panel_v1"


def load_visual_evidence_instruction() -> tuple[str, str, str]:
    """Load the committed visual geometry instruction and its local digest."""

    prompt_path = (
        Path(__file__).resolve().parents[1]
        / "prompts"
        / "balloon_free_visual_evidence_v1.txt"
    )
    try:
        text = prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _visual_error(
            "visual.prompt_missing", "the visual evidence instruction is unavailable"
        ) from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return VISUAL_EVIDENCE_PROMPT_VERSION, digest, normalized


def load_visual_evidence_repair_instruction() -> tuple[str, str, str]:
    """Load the bounded semantic-facts repair instruction and digest."""

    _base_version, _base_digest, base = load_visual_evidence_instruction()
    prompt_path = (
        Path(__file__).resolve().parents[1]
        / "prompts"
        / "balloon_free_visual_evidence_repair_v1.txt"
    )
    try:
        suffix = prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _visual_error(
            "visual.prompt_missing", "the visual evidence repair instruction is unavailable"
        ) from exc
    normalized_suffix = suffix.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    normalized = base.rstrip("\n") + "\n\n" + normalized_suffix + "\n"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return VISUAL_EVIDENCE_REPAIR_PROMPT_VERSION, digest, normalized


@dataclass(frozen=True)
class BalloonRegionEvidence:
    region_id: str
    kind: str
    normalized_bbox: tuple[float, float, float, float] | None
    normalized_polygon: tuple[tuple[float, float], ...]
    confidence: float
    evidence_source: str
    mask_status: str


@dataclass(frozen=True)
class ProtectedRegionEvidence:
    region_id: str
    kind: str
    normalized_bbox: tuple[float, float, float, float] | None
    normalized_polygon: tuple[tuple[float, float], ...]
    confidence: float
    evidence_source: str
    required: bool
    minimum_coverage: float


@dataclass(frozen=True)
class PanelVisualEvidence:
    contract_version: str
    panel_id: str
    source_asset_id: str
    source_order: int
    balloon_regions: tuple[BalloonRegionEvidence, ...]
    protected_regions: tuple[ProtectedRegionEvidence, ...]
    balloon_mask_status: str
    mask_confidence: float
    evidence_source: str
    mask_reason: str
    evidence_hash: str = ""


def _visual_error(code: str, message: str) -> VisualEvidenceError:
    return VisualEvidenceError(code, message)


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _normalised_bbox(value: object) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise _visual_error("visual.region_invalid", "normalized_bbox must contain four values")
    if not all(_number(item) for item in value):
        raise _visual_error("visual.region_invalid", "normalized_bbox values must be numeric")
    bbox = tuple(float(item) for item in value)
    if not all(0.0 <= item <= 1.0 for item in bbox) or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        raise _visual_error("visual.region_invalid", "normalized_bbox is outside the unit frame")
    return bbox  # type: ignore[return-value]


def _normalised_polygon(value: object) -> tuple[tuple[float, float], ...]:
    if value is None:
        return ()
    if not isinstance(value, (tuple, list)):
        raise _visual_error("visual.region_invalid", "normalized_polygon must be a sequence")
    points: list[tuple[float, float]] = []
    for point in value:
        if not isinstance(point, (tuple, list)) or len(point) != 2 or not all(_number(item) for item in point):
            raise _visual_error("visual.region_invalid", "polygon points must be numeric pairs")
        x, y = (float(item) for item in point)
        if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
            raise _visual_error("visual.region_invalid", "polygon point is outside the unit frame")
        points.append((x, y))
    if points and len(points) < 3:
        raise _visual_error("visual.region_invalid", "polygon requires at least three points")
    return tuple(points)


def _confidence(value: object, field: str) -> float:
    if not _number(value) or not 0.0 <= float(value) <= 1.0:
        raise _visual_error("visual.region_invalid", f"{field} must be between zero and one")
    return float(value)


def _validate_balloon_region(region: BalloonRegionEvidence) -> None:
    if not isinstance(region.region_id, str) or not region.region_id:
        raise _visual_error("visual.region_invalid", "balloon region identity is invalid")
    if not isinstance(region.kind, str) or region.kind not in _BALLOON_KINDS:
        raise _visual_error("visual.region_invalid", "balloon region identity or kind is invalid")
    if not isinstance(region.mask_status, str) or region.mask_status not in {"unknown", "known_nonempty"}:
        raise _visual_error("visual.region_invalid", "balloon region mask status is invalid")
    _normalised_bbox(region.normalized_bbox)
    polygon = _normalised_polygon(region.normalized_polygon)
    if region.mask_status == "known_nonempty" and region.normalized_bbox is None and not polygon:
        raise _visual_error("visual.balloon_geometry_invalid", "known geometry has no bbox or polygon")
    _confidence(region.confidence, "confidence")
    if not isinstance(region.evidence_source, str) or not region.evidence_source:
        raise _visual_error("visual.region_invalid", "balloon evidence source is empty")


def _validate_protected_region(region: ProtectedRegionEvidence) -> None:
    if not isinstance(region.region_id, str) or not region.region_id:
        raise _visual_error("visual.region_invalid", "protected region identity is invalid")
    if not isinstance(region.kind, str) or region.kind not in _PROTECTED_KINDS:
        raise _visual_error("visual.region_invalid", "protected region identity or kind is invalid")
    _normalised_bbox(region.normalized_bbox)
    _normalised_polygon(region.normalized_polygon)
    _confidence(region.confidence, "confidence")
    if not isinstance(region.evidence_source, str) or not region.evidence_source:
        raise _visual_error("visual.region_invalid", "protected evidence source is empty")
    if not isinstance(region.required, bool):
        raise _visual_error("visual.region_invalid", "required must be boolean")
    _confidence(region.minimum_coverage, "minimum_coverage")


def _visual_payload(evidence: PanelVisualEvidence, *, include_hash: bool) -> dict[str, Any]:
    return {
        "contract_version": evidence.contract_version,
        "panel_id": evidence.panel_id,
        "source_asset_id": evidence.source_asset_id,
        "source_order": evidence.source_order,
        "balloon_regions": [
            {
                "region_id": region.region_id,
                "kind": region.kind,
                "normalized_bbox": region.normalized_bbox,
                "normalized_polygon": region.normalized_polygon,
                "confidence": region.confidence,
                "evidence_source": region.evidence_source,
                "mask_status": region.mask_status,
            }
            for region in evidence.balloon_regions
        ],
        "protected_regions": [
            {
                "region_id": region.region_id,
                "kind": region.kind,
                "normalized_bbox": region.normalized_bbox,
                "normalized_polygon": region.normalized_polygon,
                "confidence": region.confidence,
                "evidence_source": region.evidence_source,
                "required": region.required,
                "minimum_coverage": region.minimum_coverage,
            }
            for region in evidence.protected_regions
        ],
        "balloon_mask_status": evidence.balloon_mask_status,
        "mask_confidence": evidence.mask_confidence,
        "evidence_source": evidence.evidence_source,
        "mask_reason": evidence.mask_reason,
        "evidence_hash": evidence.evidence_hash if include_hash else "",
    }


def _canonical_visual_json(evidence: PanelVisualEvidence) -> str:
    return json.dumps(
        _visual_payload(evidence, include_hash=False),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def visual_evidence_hash(evidence: PanelVisualEvidence) -> str:
    _validate_panel_visual_evidence(evidence, verify_hash=False)
    return hashlib.sha256(_canonical_visual_json(evidence).encode("utf-8")).hexdigest()


def _validate_panel_visual_evidence(evidence: PanelVisualEvidence, *, verify_hash: bool) -> None:
    if not isinstance(evidence, PanelVisualEvidence):
        raise _visual_error("visual.evidence_invalid", "visual evidence has an unexpected type")
    if evidence.contract_version != VISUAL_EVIDENCE_CONTRACT_VERSION:
        raise _visual_error("visual.evidence_invalid", "visual evidence contract version is unsupported")
    if (
        not isinstance(evidence.panel_id, str)
        or not evidence.panel_id
        or not isinstance(evidence.source_asset_id, str)
        or not evidence.source_asset_id
        or not isinstance(evidence.source_order, int)
        or isinstance(evidence.source_order, bool)
        or evidence.source_order < 0
    ):
        raise _visual_error("visual.lineage_invalid", "panel lineage is incomplete")
    if evidence.balloon_mask_status not in _MASK_STATUSES:
        raise _visual_error("visual.evidence_invalid", "balloon mask status is unsupported")
    confidence = _confidence(evidence.mask_confidence, "mask_confidence")
    if not isinstance(evidence.evidence_source, str) or not isinstance(evidence.mask_reason, str):
        raise _visual_error("visual.evidence_invalid", "visual evidence provenance is invalid")
    if evidence.balloon_mask_status == "known_empty" and (
        confidence <= 0.0
        or not evidence.evidence_source
        or not evidence.mask_reason.strip()
        or evidence.balloon_regions
    ):
        raise _visual_error("visual.balloon_mask_empty_unproven", "empty geometry was not affirmatively established")
    if not isinstance(evidence.evidence_source, str) or not evidence.evidence_source:
        raise _visual_error("visual.evidence_invalid", "visual evidence source is empty")
    if evidence.balloon_mask_status == "unknown" and not evidence.mask_reason.strip():
        raise _visual_error("visual.evidence_invalid", "visual evidence reason is required")
    if evidence.balloon_mask_status == "known_nonempty" and not evidence.balloon_regions:
        raise _visual_error("visual.balloon_geometry_invalid", "nonempty geometry has no balloon regions")
    region_ids: set[str] = set()
    for region in evidence.balloon_regions:
        _validate_balloon_region(region)
        if region.region_id in region_ids:
            raise _visual_error("visual.region_invalid", "region ids must be unique")
        region_ids.add(region.region_id)
    for region in evidence.protected_regions:
        _validate_protected_region(region)
        if region.region_id in region_ids:
            raise _visual_error("visual.region_invalid", "region ids must be unique")
        region_ids.add(region.region_id)
    if evidence.balloon_mask_status == "known_nonempty" and any(
        region.mask_status != "known_nonempty" for region in evidence.balloon_regions
    ):
        raise _visual_error("visual.balloon_geometry_invalid", "known geometry contains an unknown region")
    if evidence.evidence_hash and verify_hash and evidence.evidence_hash != visual_evidence_hash(evidence):
        raise _visual_error("visual.evidence_hash_invalid", "visual evidence hash does not match its content")


def validate_panel_visual_evidence(evidence: PanelVisualEvidence) -> None:
    """Validate structure while allowing unknown geometry to be persisted."""

    _validate_panel_visual_evidence(evidence, verify_hash=True)


def panel_visual_evidence_json(evidence: PanelVisualEvidence) -> dict[str, Any]:
    validate_panel_visual_evidence(evidence)
    hashed = replace(evidence, evidence_hash=visual_evidence_hash(evidence))
    return _visual_payload(hashed, include_hash=True)


def _parse_balloon_region(raw: object) -> BalloonRegionEvidence:
    if not isinstance(raw, Mapping):
        raise _visual_error("visual.region_invalid", "balloon region is not an object")
    required = {"region_id", "kind", "normalized_bbox", "normalized_polygon", "confidence", "evidence_source", "mask_status"}
    if set(raw) != required:
        raise _visual_error("visual.region_invalid", "balloon region keys are incomplete")
    return BalloonRegionEvidence(
        region_id=raw["region_id"],
        kind=raw["kind"],
        normalized_bbox=_normalised_bbox(raw["normalized_bbox"]),
        normalized_polygon=_normalised_polygon(raw["normalized_polygon"]),
        confidence=raw["confidence"],
        evidence_source=raw["evidence_source"],
        mask_status=raw["mask_status"],
    )


def _parse_protected_region(raw: object) -> ProtectedRegionEvidence:
    if not isinstance(raw, Mapping):
        raise _visual_error("visual.region_invalid", "protected region is not an object")
    required = {"region_id", "kind", "normalized_bbox", "normalized_polygon", "confidence", "evidence_source", "required", "minimum_coverage"}
    if set(raw) != required:
        raise _visual_error("visual.region_invalid", "protected region keys are incomplete")
    return ProtectedRegionEvidence(
        region_id=raw["region_id"],
        kind=raw["kind"],
        normalized_bbox=_normalised_bbox(raw["normalized_bbox"]),
        normalized_polygon=_normalised_polygon(raw["normalized_polygon"]),
        confidence=raw["confidence"],
        evidence_source=raw["evidence_source"],
        required=raw["required"],
        minimum_coverage=raw["minimum_coverage"],
    )


def parse_panel_visual_evidence(raw: Mapping[str, Any]) -> PanelVisualEvidence:
    """Parse and validate serialized visual evidence without mutating input."""

    try:
        if not isinstance(raw, Mapping):
            raise _visual_error("visual.evidence_invalid", "visual evidence is not an object")
        required = {
            "contract_version", "panel_id", "source_asset_id", "source_order", "balloon_regions",
            "protected_regions", "balloon_mask_status", "mask_confidence", "evidence_source", "mask_reason",
        }
        allowed = required | {"evidence_hash"}
        if set(raw) != allowed and set(raw) != required:
            raise _visual_error("visual.evidence_invalid", "visual evidence keys are unexpected")
        if not isinstance(raw["balloon_regions"], (tuple, list)) or not isinstance(raw["protected_regions"], (tuple, list)):
            raise _visual_error("visual.evidence_invalid", "visual region collections are invalid")
        evidence = PanelVisualEvidence(
            contract_version=raw["contract_version"],
            panel_id=raw["panel_id"],
            source_asset_id=raw["source_asset_id"],
            source_order=raw["source_order"],
            balloon_regions=tuple(_parse_balloon_region(item) for item in raw["balloon_regions"]),
            protected_regions=tuple(_parse_protected_region(item) for item in raw["protected_regions"]),
            balloon_mask_status=raw["balloon_mask_status"],
            mask_confidence=raw["mask_confidence"],
            evidence_source=raw["evidence_source"],
            mask_reason=raw["mask_reason"],
            evidence_hash=raw.get("evidence_hash", ""),
        )
        validate_panel_visual_evidence(evidence)
        return evidence
    except VisualEvidenceError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise _visual_error("visual.evidence_invalid", "visual evidence could not be parsed") from exc


def unknown_visual_evidence(*, panel_id: str, source_asset_id: str, source_order: int, reason: str) -> PanelVisualEvidence:
    """Create a persisted unknown state when the provider has no geometry sidecar."""

    if not reason.strip():
        raise _visual_error("visual.evidence_invalid", "unknown visual evidence requires a reason")
    evidence = PanelVisualEvidence(
        contract_version=VISUAL_EVIDENCE_CONTRACT_VERSION,
        panel_id=panel_id,
        source_asset_id=source_asset_id,
        source_order=source_order,
        balloon_regions=(),
        protected_regions=(),
        balloon_mask_status="unknown",
        mask_confidence=0.0,
        evidence_source="vision_geometry_unavailable",
        mask_reason=reason,
    )
    return replace(evidence, evidence_hash=visual_evidence_hash(evidence))


def conservative_full_panel_visual_evidence(
    *, panel_id: str, source_asset_id: str, source_order: int, reason: str
) -> PanelVisualEvidence:
    """Mark unknown geometry for an audited, whole-panel conservative render.

    This does not claim that balloon geometry is known.  Downstream callers
    must explicitly opt into the conservative whole-panel contract; default
    reference crop/planner gates continue to reject ordinary ``unknown``
    evidence.
    """

    if not isinstance(reason, str) or not reason.strip():
        raise _visual_error(
            "visual.evidence_invalid",
            "conservative fallback requires a non-empty reason",
        )
    evidence = unknown_visual_evidence(
        panel_id=panel_id,
        source_asset_id=source_asset_id,
        source_order=source_order,
        reason=reason,
    )
    candidate = replace(
        evidence,
        evidence_source=CONSERVATIVE_FULL_PANEL_EVIDENCE_SOURCE,
        mask_reason=f"conservative whole-panel fallback: {reason.strip()}",
        evidence_hash="",
    )
    return replace(candidate, evidence_hash=visual_evidence_hash(candidate))


def is_conservative_full_panel_visual_evidence(
    evidence: PanelVisualEvidence | Mapping[str, Any],
) -> bool:
    """Return true only for the explicit unknown-geometry fallback record."""

    parsed = evidence if isinstance(evidence, PanelVisualEvidence) else parse_panel_visual_evidence(evidence)
    validate_panel_visual_evidence(parsed)
    return (
        parsed.evidence_source == CONSERVATIVE_FULL_PANEL_EVIDENCE_SOURCE
        and parsed.balloon_mask_status == "unknown"
        and not parsed.balloon_regions
    )


def ensure_panel_visual_evidence(
    observation: Mapping[str, Any] | None,
    *,
    panel_id: str,
    source_asset_id: str,
    source_order: int,
) -> tuple[dict[str, Any], PanelVisualEvidence]:
    """Attach a validated sidecar, or an explicit unknown record, to an observation."""

    merged = dict(observation or {})
    raw = merged.get("visual_evidence")
    if raw is None:
        evidence = unknown_visual_evidence(
            panel_id=panel_id,
            source_asset_id=source_asset_id,
            source_order=source_order,
            reason="visual geometry acquisition is not available in this analysis phase",
        )
    elif isinstance(raw, PanelVisualEvidence):
        validate_panel_visual_evidence(raw)
        evidence = raw
    else:
        evidence = parse_panel_visual_evidence(raw)
    if (
        evidence.panel_id != panel_id
        or evidence.source_asset_id != source_asset_id
        or evidence.source_order != source_order
    ):
        raise _visual_error("visual.lineage_invalid", "visual evidence lineage does not match its panel")
    merged["visual_evidence"] = panel_visual_evidence_json(evidence)
    return merged, parse_panel_visual_evidence(merged["visual_evidence"])


def require_reference_ready_visual_evidence(
    evidence: PanelVisualEvidence | Mapping[str, Any],
    *,
    allow_conservative_full_panel: bool = False,
) -> PanelVisualEvidence:
    """Reject unknown geometry only when a reference crop consumes the record."""

    parsed = evidence if isinstance(evidence, PanelVisualEvidence) else parse_panel_visual_evidence(evidence)
    validate_panel_visual_evidence(parsed)
    if parsed.balloon_mask_status == "unknown" and not (
        allow_conservative_full_panel
        and is_conservative_full_panel_visual_evidence(parsed)
    ):
        raise _visual_error("visual.balloon_mask_unknown", "reference framing requires known balloon geometry")
    return parsed


_ACTION = {"attack", "attacked", "attacks", "hit", "struck", "strike", "fight", "fought", "run", "jump", "fall", "battle", "chase", "serang", "menyerang", "memukul", "merampas", "menebas", "bertarung", "berlari", "melompat", "jatuh", "kejar", "mengejar"}
_REVEAL = {"reveal", "finally", "opened", "awakens", "appears", "appeared", "discovers", "ternyata", "akhirnya", "muncul", "terbuka", "bangkit", "menemukan"}
_EXPLOSION = {"explosion", "explode", "blast", "fire", "destroy", "impact", "ledakan", "meledak", "hancur", "menghancurkan", "dampak"}
_THINKING = {"think", "thinks", "remember", "wonder", "realize", "considers", "berpikir", "teringat", "bertanya", "menyadari", "mempertimbangkan"}
_WEAPON = {"sword", "axe", "blade", "weapon", "bow", "spear", "gun", "pedang", "kapak", "bilah", "senjata", "busur", "tombak", "pistol"}
_MONSTER = {"dragon", "monster", "demon", "beast", "boss", "ogre", "creature", "naga", "iblis", "binatang", "bos", "makhluk"}
_VICTORY = {"victory", "victorious", "wins", "won", "triumph", "defeated", "menang", "kemenangan", "mengalahkan", "ditaklukkan"}



def _clip(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def narration_tags(text: str) -> frozenset[str]:
    tokens = _tokens(text)
    tags: set[str] = set()
    if tokens & _ACTION:
        tags.add("action")
        if tokens & {"attack", "attacked", "attacks", "strike", "struck", "hit"}:
            tags.add("attack")
    if tokens & _REVEAL:
        tags.add("reveal")
    if tokens & _EXPLOSION:
        tags.add("explosion")
    if tokens & _THINKING:
        tags.add("thinking")
    if tokens & _WEAPON:
        tags.add("weapon")
    if tokens & _MONSTER:
        tags.add("monster")
    if {"dialogue", "says", "tells"} & tokens:
        tags.add("dialogue")
    if tokens & _VICTORY:
        tags.add("victory")
    return frozenset(tags)


def _ocr(image: Image.Image) -> str:
    try:
        import pytesseract

        return pytesseract.image_to_string(image, config="--psm 11")[:500].strip().lower()
    except (ImportError, OSError, RuntimeError, subprocess.SubprocessError):
        return ""


def _face_stats(image: Image.Image) -> tuple[float, float, list[tuple[float, float]]]:
    try:
        import cv2
        import numpy as np

        gray = np.asarray(image.convert("L"))
        cascade = cv2.CascadeClassifier(
            str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
        )
        faces = cascade.detectMultiScale(gray, 1.1, 4, minSize=(18, 18))
        if len(faces) == 0:
            return 0.0, 0.0, []
        width, height = image.size
        area = sum(float(fw * fh) for _, _, fw, fh in faces) / (width * height)
        expression = _clip(float(gray.std()) / 75.0)
        points = [(float(x + fw / 2) / width, float(y + fh / 2) / height) for x, y, fw, fh in faces]
        return _clip(area * 7.0), expression, points
    except (ImportError, OSError):
        return 0.0, 0.0, []


def _edge_features(image: Image.Image) -> tuple[float, float, float]:
    gray = image.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    values = _pixels(edges.resize((96, 96)))
    strong = sum(value > 42 for value in values) / max(1, len(values))
    variance = ImageStat.Stat(edges).var[0] ** 0.5 / 128.0
    horizontal = 0.0
    vertical = 0.0
    pixels = _pixels(gray.resize((96, 96)))
    for y in range(1, 95):
        for x in range(1, 95):
            index = y * 96 + x
            horizontal += abs(pixels[index] - pixels[index - 1]) > 55
            vertical += abs(pixels[index] - pixels[index - 96]) > 55
    total = 94 * 94
    return _clip(strong * 3.2), _clip((horizontal + vertical) / (2 * total) * 5), _clip(variance)


def _focal_points(image: Image.Image) -> tuple[tuple[float, float], ...]:
    gray = image.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    width, height = edges.size
    cells: list[tuple[float, float, float]] = []
    for row in range(3):
        for col in range(3):
            box = (col * width // 3, row * height // 3, (col + 1) * width // 3, (row + 1) * height // 3)
            energy = ImageStat.Stat(edges.crop(box)).mean[0]
            cells.append((energy, (col + 0.5) / 3, (row + 0.5) / 3))
    cells.sort(reverse=True)
    return tuple((x, y) for _, x, y in cells[:3]) or ((0.5, 0.4),)


def _layout_dominance(image: Image.Image) -> tuple[float, float, float]:
    """Estimate speech-balloon, UI, and blank dominance without OCR."""
    small = image.convert("RGB").resize((96, 96), Image.Resampling.BILINEAR)
    pixels = _pixels(small)
    total = max(1, len(pixels))
    bright_ratio = sum(
        1 for pixel in pixels
        if isinstance(pixel, tuple) and min(pixel) >= 238
    ) / total
    gray = small.convert("L")
    edge_density, _, _ = _edge_features(small)
    band_height = max(4, gray.height // 8)
    top = gray.crop((0, 0, gray.width, band_height))
    bottom = gray.crop((0, gray.height - band_height, gray.width, gray.height))
    top_variance = min(1.0, ImageStat.Stat(top).var[0] / (255.0 * 255.0))
    bottom_variance = min(1.0, ImageStat.Stat(bottom).var[0] / (255.0 * 255.0))
    border_brightness = (
        ImageStat.Stat(top).mean[0] + ImageStat.Stat(bottom).mean[0]
    ) / (2.0 * 255.0)
    speech_balloon = _clip(
        max(0.0, bright_ratio - 0.22) * 1.7
        + edge_density * 0.35
        + (1.0 - min(1.0, (top_variance + bottom_variance) / 2.0)) * 0.15
    )
    ui_overlay = _clip(
        (1.0 - min(1.0, (top_variance + bottom_variance) / 2.0)) * 0.55
        + border_brightness * 0.25
        + max(0.0, bright_ratio - 0.65) * 0.4
    )
    blank_dominance = _clip(
        max(0.0, bright_ratio - 0.8) * 2.0
        + (1.0 - edge_density) * 0.35
    )
    return speech_balloon, ui_overlay, blank_dominance


def _visual_signature(image: Image.Image) -> str:
    """Coarse perceptual signature for repeated-panel suppression."""
    pixels = _pixels(image.convert("L").resize((8, 8), Image.Resampling.BILINEAR))
    average = sum(pixels) / max(1, len(pixels))
    return "".join("1" if pixel >= average else "0" for pixel in pixels)


def analyze_panel(data: bytes, asset_id: str = "", order_index: int = 0, source_family: str = "") -> PanelCandidate:
    """Extract content features from one image and calculate its visual score."""
    with Image.open(io.BytesIO(data)) as source:
        image = source.convert("RGB")
        image.thumbnail((640, 640), Image.Resampling.LANCZOS)
    width, height = image.size
    gray = image.convert("L")
    mean = ImageStat.Stat(gray).mean[0] / 255.0
    variance = min(1.0, ImageStat.Stat(gray).var[0] / (255.0 * 255.0))
    edge_density, motion, texture = _edge_features(image)
    speech_balloon, ui_overlay, blank_dominance = _layout_dominance(image)
    face, expression, face_points = _face_stats(image)
    text = _ocr(image)
    tags = _tokens(text)
    weapons = _clip(0.85 if tags & _WEAPON else edge_density * 0.35)
    monsters = _clip(0.85 if tags & _MONSTER else 0.0)
    effects = _clip(motion * 0.55 + texture * 0.45)
    action = _clip(motion * 0.65 + edge_density * 0.35)
    impact = _clip(effects * 0.6 + motion * 0.4)
    empty = _clip((1.0 - edge_density) * (1.0 - variance) * 1.15)
    scenery = _clip(empty * 0.75 + (1.0 - face) * (1.0 - action) * 0.25)
    transition = _clip(empty if variance < 0.025 else 0.0)
    composition = _clip(texture * 0.45 + edge_density * 0.35 + (1.0 - abs(mean - 0.52)) * 0.2)
    features = VisualFeatures(
        face_visibility=face,
        facial_expression=expression,
        action_pose=action,
        weapons=weapons,
        monsters=monsters,
        visual_effects=effects,
        motion_lines=motion,
        impact_frame=impact,
        close_up=_clip(face * 1.15),
        dramatic_composition=composition,
        object_density=edge_density,
        empty_background=empty,
        scenery_only=scenery,
        transition=transition,
        ocr_text=text,
        semantic_tags=frozenset(tags),
        focal_points=tuple(face_points or _focal_points(image)),
        face_points=tuple(face_points),
        visual_signature=_visual_signature(image),
    )
    positive = sum(
        weight * value
        for weight, value in (
            (WEIGHTS.face, face), (WEIGHTS.expression, expression), (WEIGHTS.action, action),
            (WEIGHTS.weapon, weapons), (WEIGHTS.monster, monsters), (WEIGHTS.effects, effects),
            (WEIGHTS.motion_lines, motion), (WEIGHTS.impact, impact),
            (WEIGHTS.close_up, features.close_up), (WEIGHTS.composition, composition),
            (WEIGHTS.object_density, edge_density),
        )
    )
    penalty = (
        WEIGHTS.empty * empty
        + WEIGHTS.scenery * scenery
        + WEIGHTS.transition * transition
        + WEIGHTS.speech_balloon * speech_balloon
        + WEIGHTS.ui_overlay * ui_overlay
        + WEIGHTS.blank_dominance * blank_dominance
    )
    family = source_family or f"legacy-strip-{max(0, order_index) // 8}"
    return PanelCandidate(asset_id, order_index, features, round(max(0.0, positive - penalty), 3), source_family=family)


def analyze_assets(assets: Iterable[object], read: Callable[[str], bytes]) -> list[PanelCandidate]:
    return [
        analyze_panel(read(asset.storage_key), asset.id, asset.order_index, getattr(asset, "source_family", ""))
        for asset in assets
    ]


def semantic_score(candidate: PanelCandidate, narration: str) -> float:
    tags = narration_tags(narration)
    f = candidate.features
    score = 0.0
    if "action" in tags:
        score += 2.5 * (f.action_pose + f.impact_frame)
    if "reveal" in tags:
        score += 3.0 * (f.close_up + f.visual_effects)
    if "explosion" in tags:
        score += 3.0 * (f.visual_effects + f.impact_frame)
    if "thinking" in tags:
        score += 2.0 * (f.close_up + f.facial_expression)
    if "weapon" in tags:
        score += 2.5 * f.weapons
    if "monster" in tags:
        score += 2.5 * f.monsters
    if "dialogue" in tags:
        score += 1.5 * f.face_visibility
    return round(score, 3)


def select_panel(
    candidates: list[PanelCandidate], narration: str, previous_order: int | None = None,
    used_ids: set[str] | None = None, used_signatures: set[str] | None = None,
    nearby: int = 6, usage_counts: dict[str, int] | None = None,
    max_asset_uses: int | None = None,
 ) -> PanelCandidate | None:
    """Choose engagement first; continuity is only a small tie-breaker."""
    if not candidates:
        return None
    used_ids = used_ids or set()
    used_signatures = used_signatures or set()
    usage_counts = dict(usage_counts or {})
    eligible = [
        candidate for candidate in candidates
        if max_asset_uses is None or usage_counts.get(candidate.asset_id, 0) < max_asset_uses
    ]
    pool = eligible or candidates
    ranked: list[tuple[float, PanelCandidate]] = []
    for candidate in pool:
        semantic = semantic_score(candidate, narration)
        distance = abs(candidate.order_index - previous_order) if previous_order is not None else 0
        continuity = max(0.0, 1.0 - distance / max(1, nearby))
        chronology_penalty = 0.0
        chronology_bonus = 0.0
        if previous_order is not None:
            if candidate.order_index < previous_order:
                chronology_penalty = min(2.0, (previous_order - candidate.order_index) * 0.25)
            else:
                chronology_bonus = min(0.18, (candidate.order_index - previous_order) * 0.03)
        repeat_penalty = WEIGHTS.repeated * (
            1.0 if candidate.asset_id in used_ids else 0.0
        )
        # Reuse is allowed when the pool is exhausted, not when a fresh panel
        # exists. This prevents a high-scoring frame from dominating every beat.
        if candidate.asset_id in used_ids and len(used_ids) < len(pool):
            repeat_penalty += max(WEIGHTS.repeated * 2.5, candidate.visual_score * 0.45)
        if candidate.features.visual_signature and candidate.features.visual_signature in used_signatures:
            repeat_penalty += WEIGHTS.repeated * 0.75
        if max_asset_uses is not None and usage_counts.get(candidate.asset_id, 0) >= max_asset_uses:
            repeat_penalty += max(WEIGHTS.repeated * 4.0, candidate.visual_score * 0.8)
        value = (
            candidate.visual_score + semantic + WEIGHTS.continuity * continuity + chronology_bonus
            - repeat_penalty - chronology_penalty
        )
        ranked.append((value, PanelCandidate(candidate.asset_id, candidate.order_index, candidate.features, candidate.visual_score, semantic, candidate.source_family)))
    ranked.sort(key=lambda item: item[0], reverse=True)
    best = ranked[0][1]
    if previous_order is not None:
        nearby_candidates = [item for item in ranked if abs(item[1].order_index - previous_order) <= nearby]
        if nearby_candidates and nearby_candidates[0][0] >= ranked[0][0] - 1.5:
            best = nearby_candidates[0][1]
    return best


def selection_reasons(
    candidate: PanelCandidate,
    narration: str,
    previous_order: int | None = None,
    previous_source_family: str | None = None,
) -> list[str]:
    """Deterministic, auditable reasons for panel choice and progression."""
    tags = narration_tags(narration)
    reasons = [f"source_order:{candidate.order_index}", f"visual_score:{candidate.visual_score:.3f}"]
    if previous_order is None:
        reasons.append("chronology:initial")
    elif candidate.order_index >= previous_order:
        reasons.append("chronology:forward")
    else:
        reasons.append("chronology:backtrack")
    if candidate.source_family:
        reasons.append(f"source_family:{candidate.source_family}")
        if previous_source_family is None:
            reasons.append("source_family_progression:initial")
        elif candidate.source_family == previous_source_family:
            reasons.append("source_family_progression:repeat")
        else:
            reasons.append("source_family_progression:advance")
    for tag, value in (
        ("action", candidate.features.action_pose),
        ("face", candidate.features.face_visibility),
        ("weapon", candidate.features.weapons),
        ("monster", candidate.features.monsters),
        ("effect", candidate.features.visual_effects),
        ("reveal", candidate.features.close_up),
    ):
        if tag in tags and value > 0.15:
            reasons.append(f"{tag}_match:{value:.3f}")
    for code, value in (
        ("speech_balloon_dominance", candidate.features.speech_balloon_dominance),
        ("ui_overlay_dominance", candidate.features.ui_overlay_dominance),
        ("blank_dominance", candidate.features.blank_dominance),
    ):
        if value > 0.2:
            reasons.append(f"penalty:{code}:{value:.3f}")
    if candidate.features.ocr_text:
        reasons.append("ocr_text_available")
    else:
        reasons.append("ocr_not_required")
    return reasons


def camera_effect(narration: str, index: int) -> str:
    tags = narration_tags(narration)
    if "explosion" in tags:
        return "push_in"
    if "action" in tags:
        return "pan_diagonal"
    if "reveal" in tags:
        return "pan_vertical"
    if "thinking" in tags:
        return "pan_horizontal"
    if "dialogue" in tags:
        return "slow_push_in"
    return ("slow_push_in", "pan_horizontal", "static_emphasis")[index % 3]


def planned_focus(candidate: PanelCandidate | None, shot_index: int = 0) -> tuple[float, float]:
    if candidate is None:
        return 0.5, 0.4
    points = candidate.features.focal_points
    return points[shot_index % len(points)]


def plan_content_aware_scenes(
    spans: Iterable[object],
    candidates: list[PanelCandidate],
    min_scene_seconds: float = 2.0,
    max_scene_seconds: float = 6.0,
    preferred_asset_ids_by_section: Mapping[str, Iterable[str]] | None = None,
    max_asset_uses: int | None = None,
) -> list[dict]:
    """Plan directed shots; panel scoring remains the candidate provider."""
    from app.services.camera_planner import apply_camera_plans
    from app.services.shot_director import plan_shots

    return apply_camera_plans(
        plan_shots(
            list(spans),
            candidates,
            min_scene_seconds,
            max_scene_seconds,
            preferred_asset_ids_by_section=preferred_asset_ids_by_section,
            max_asset_uses=max_asset_uses,
        )
    )


def score_breakdown(candidate: PanelCandidate) -> dict[str, float | str]:
    f = candidate.features
    return {
        "visual_score": candidate.visual_score, "semantic_score": candidate.semantic_score,
        "face": round(f.face_visibility, 3), "expression": round(f.facial_expression, 3),
        "action": round(f.action_pose, 3), "weapons": round(f.weapons, 3),
        "monsters": round(f.monsters, 3), "effects": round(f.visual_effects, 3),
        "motion_lines": round(f.motion_lines, 3), "impact": round(f.impact_frame, 3),
        "close_up": round(f.close_up, 3), "composition": round(f.dramatic_composition, 3),
        "object_density": round(f.object_density, 3), "empty_penalty": round(f.empty_background, 3),
        "scenery_penalty": round(f.scenery_only, 3), "transition_penalty": round(f.transition, 3),
        "speech_balloon_penalty": round(f.speech_balloon_dominance, 3),
        "ui_overlay_penalty": round(f.ui_overlay_dominance, 3),
        "blank_dominance_penalty": round(f.blank_dominance, 3),
        "ocr": f.ocr_text,
    }


def diversity_penalty(previous: PanelCandidate | None, current: PanelCandidate) -> float:
    if previous is None:
        return 0.0
    a, b = previous.features, current.features
    return round(max(0.0, 1.0 - sum(abs(getattr(a, field) - getattr(b, field)) for field in ("face_visibility", "action_pose", "object_density", "dramatic_composition"))), 3)


def tune_weights(**changes: float) -> PanelScoreWeights:
    values = {field: getattr(WEIGHTS, field) for field in WEIGHTS.__dataclass_fields__}
    unknown = set(changes) - set(values)
    if unknown:
        raise ValueError(f"unknown visual weight(s): {', '.join(sorted(unknown))}")
    values.update(changes)
    return PanelScoreWeights(**values)


__all__ = ["PanelCandidate", "PanelScoreWeights", "VisualFeatures", "analyze_assets", "analyze_panel", "asset_use_cap", "camera_effect", "diversity_penalty", "narration_tags", "planned_focus", "plan_content_aware_scenes", "score_breakdown", "select_panel", "selection_reasons", "tune_weights"]

# ponytail: heuristic CV ceiling; upgrade to a local vision encoder when GPU
# inference is available, preserving this feature schema as the adapter boundary.
