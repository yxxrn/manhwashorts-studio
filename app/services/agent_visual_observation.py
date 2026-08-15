"""Review-only agent-vision observation boundary.

When the executing agent itself supports vision, it may perform the panel
observation directly (no external provider call) and persist balloon/protected
geometry through the normal evidence contract. This pathway only supplies
geometry; every framing gate consumes it unchanged.

Hard rules:

- Review-only: requires an explicit silent-review acknowledgment and rejects any
  publish request (``agent_observation.publish_forbidden``).
- Provenance honesty: persisted evidence is labeled
  ``agent_visual_geometry_v1`` and the mask reason is prefixed with the agent
  label. It never masquerades as provider geometry.
- Local canonical hash only: supplied ``evidence_hash`` / ``contract_version`` /
  ``evidence_source`` / lineage fields are rejected.
- No unknown status: an agent observation must be affirmative
  (``known_empty`` or ``known_nonempty``).
- Surgical persistence: only ``observation_json['visual_evidence']`` is
  replaced; every other persisted observation field is preserved.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PanelRegion, SourceAsset, StoryAnalysis
from app.services import visual_scoring

AGENT_OBSERVATION_CONTRACT_VERSION = "agent_visual_observation_v1"
AGENT_EVIDENCE_SOURCE = "agent_visual_geometry_v1"

_ALLOWED_BALLOON_MASK_STATUSES = {"known_empty", "known_nonempty"}
_ALLOWED_BALLOON_KINDS = {"speech_balloon"}
_ALLOWED_PROTECTED_KINDS = {
    "background",
    "subject",
    "face",
    "action",
    "effect",
    "continuity_context",
}
_FORBIDDEN_SIDECAR_KEYS = {"evidence_hash", "contract_version", "evidence_source"}
_REGION_KEYS = {
    "region_id",
    "kind",
    "normalized_bbox",
    "normalized_polygon",
    "confidence",
    "mask_status",
}
_PROTECTED_REGION_KEYS = {"region_id", "kind", "normalized_bbox", "normalized_polygon", "confidence", "required", "minimum_coverage"}
_TOP_LEVEL_KEYS = {
    "panel_id",
    "balloon_mask_status",
    "mask_confidence",
    "mask_reason",
    "balloon_regions",
    "protected_regions",
}


class AgentObservationError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


def _fail(code: str, message: str) -> AgentObservationError:
    return AgentObservationError(code, message)


def _unit_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail("agent_observation.geometry_invalid", f"{field} must be numeric")
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise _fail("agent_observation.geometry_invalid", f"{field} is outside zero to one")
    return number


def _unit_bbox(value: object) -> list[float] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise _fail("agent_observation.geometry_invalid", "normalized_bbox must contain four values")
    bbox = [_unit_number(item, "bbox") for item in value]
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        raise _fail("agent_observation.geometry_invalid", "normalized_bbox is degenerate")
    return bbox


def _unit_polygon(value: object) -> list[list[float]] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise _fail("agent_observation.geometry_invalid", "normalized_polygon must be a sequence")
    points: list[list[float]] = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise _fail("agent_observation.geometry_invalid", "polygon points must be pairs")
        points.append([_unit_number(point[0], "polygon_x"), _unit_number(point[1], "polygon_y")])
    if points and len(points) < 3:
        raise _fail("agent_observation.geometry_invalid", "polygon requires at least three points")
    return points or None


def validate_agent_panel_observation(
    raw: Mapping[str, Any],
    *,
    panel_id: str,
    source_asset_id: str,
    source_order: int,
    agent_label: str,
) -> dict[str, Any]:
    """Validate one agent observation and return the locally hashed sidecar payload."""

    if not isinstance(raw, Mapping):
        raise _fail("agent_observation.geometry_invalid", "observation is not an object")
    if not isinstance(agent_label, str) or not agent_label.strip():
        raise _fail("agent_observation.agent_label_required", "agent label is required")
    supplied_forbidden = sorted(set(raw) & _FORBIDDEN_SIDECAR_KEYS)
    if supplied_forbidden:
        raise _fail(
            "agent_observation.supplied_contract_forbidden",
            "agent observations cannot supply hash, contract, or source fields",
        )
    if "panel_id" in raw and str(raw["panel_id"]) != panel_id:
        raise _fail(
            "agent_observation.supplied_lineage_forbidden",
            "observation panel_id does not match its target panel",
        )
    for key in ("source_asset_id", "source_order"):
        if key in raw:
            raise _fail(
                "agent_observation.supplied_lineage_forbidden",
                f"lineage field {key} is owned by the persisted panel",
            )
    unexpected = sorted(set(raw) - _TOP_LEVEL_KEYS)
    if unexpected:
        raise _fail("agent_observation.geometry_invalid", f"unexpected observation keys: {unexpected}")
    status = raw.get("balloon_mask_status")
    if status not in _ALLOWED_BALLOON_MASK_STATUSES:
        raise _fail(
            "agent_observation.unknown_status_forbidden",
            "agent observations must affirmatively establish balloon geometry",
        )
    mask_confidence = raw.get("mask_confidence")
    if isinstance(mask_confidence, bool) or not isinstance(mask_confidence, (int, float)) or not 0.0 < float(mask_confidence) <= 1.0:
        raise _fail("agent_observation.geometry_invalid", "mask_confidence must be in (0, 1]")
    mask_reason = raw.get("mask_reason")
    if not isinstance(mask_reason, str) or not mask_reason.strip():
        raise _fail("agent_observation.reason_required", "mask_reason is required")

    balloon_regions: list[dict[str, Any]] = []
    for region in raw.get("balloon_regions") or ():
        if not isinstance(region, Mapping) or set(region) != _REGION_KEYS:
            raise _fail("agent_observation.geometry_invalid", "balloon region keys are incomplete")
        if region["kind"] not in _ALLOWED_BALLOON_KINDS:
            raise _fail("agent_observation.geometry_invalid", "balloon kind must be speech_balloon")
        if region["mask_status"] != "known_nonempty":
            raise _fail(
                "agent_observation.geometry_invalid",
                "agent balloon regions must be known_nonempty",
            )
        balloon_regions.append(
            {
                "region_id": str(region["region_id"]),
                "kind": region["kind"],
                "normalized_bbox": _unit_bbox(region["normalized_bbox"]),
                "normalized_polygon": _unit_polygon(region["normalized_polygon"]),
                "confidence": _unit_number(region["confidence"], "confidence"),
                "evidence_source": AGENT_EVIDENCE_SOURCE,
                "mask_status": "known_nonempty",
            }
        )
    if status == "known_nonempty" and not balloon_regions:
        raise _fail(
            "agent_observation.geometry_invalid",
            "known_nonempty observation requires balloon regions",
        )
    if status == "known_empty" and balloon_regions:
        raise _fail(
            "agent_observation.geometry_invalid",
            "known_empty observation cannot contain balloon regions",
        )

    protected_regions: list[dict[str, Any]] = []
    for region in raw.get("protected_regions") or ():
        if not isinstance(region, Mapping) or set(region) != _PROTECTED_REGION_KEYS:
            raise _fail("agent_observation.geometry_invalid", "protected region keys are incomplete")
        if region["kind"] not in _ALLOWED_PROTECTED_KINDS:
            raise _fail("agent_observation.geometry_invalid", "protected region kind is unsupported")
        if not isinstance(region["required"], bool):
            raise _fail("agent_observation.geometry_invalid", "required must be boolean")
        protected_regions.append(
            {
                "region_id": str(region["region_id"]),
                "kind": region["kind"],
                "normalized_bbox": _unit_bbox(region["normalized_bbox"]),
                "normalized_polygon": _unit_polygon(region["normalized_polygon"]),
                "confidence": _unit_number(region["confidence"], "confidence"),
                "evidence_source": AGENT_EVIDENCE_SOURCE,
                "required": region["required"],
                "minimum_coverage": _unit_number(region["minimum_coverage"], "minimum_coverage"),
            }
        )

    evidence = visual_scoring.PanelVisualEvidence(
        contract_version=visual_scoring.VISUAL_EVIDENCE_CONTRACT_VERSION,
        panel_id=panel_id,
        source_asset_id=source_asset_id,
        source_order=source_order,
        balloon_regions=tuple(
            visual_scoring.BalloonRegionEvidence(
                region_id=region["region_id"],
                kind=region["kind"],
                normalized_bbox=tuple(region["normalized_bbox"]) if region["normalized_bbox"] else None,
                normalized_polygon=tuple(tuple(point) for point in region["normalized_polygon"]) if region["normalized_polygon"] else (),
                confidence=region["confidence"],
                evidence_source=region["evidence_source"],
                mask_status=region["mask_status"],
            )
            for region in balloon_regions
        ),
        protected_regions=tuple(
            visual_scoring.ProtectedRegionEvidence(
                region_id=region["region_id"],
                kind=region["kind"],
                normalized_bbox=tuple(region["normalized_bbox"]) if region["normalized_bbox"] else None,
                normalized_polygon=tuple(tuple(point) for point in region["normalized_polygon"]) if region["normalized_polygon"] else (),
                confidence=region["confidence"],
                evidence_source=region["evidence_source"],
                required=region["required"],
                minimum_coverage=region["minimum_coverage"],
            )
            for region in protected_regions
        ),
        balloon_mask_status=status,
        mask_confidence=float(mask_confidence),
        evidence_source=AGENT_EVIDENCE_SOURCE,
        mask_reason=f"agent:{agent_label.strip()}; {mask_reason.strip()}",
    )
    return visual_scoring.panel_visual_evidence_json(evidence)


def apply_agent_panel_observations(
    db: Session,
    project_id: str,
    observations: Sequence[Mapping[str, Any]],
    *,
    agent_label: str,
    silent_reference_review: bool,
    publish_allowed: bool,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Persist agent observations into PanelRegion visual evidence (review-only)."""

    if publish_allowed:
        raise _fail(
            "agent_observation.publish_forbidden",
            "agent observations are review-only and can never be published",
        )
    if not silent_reference_review:
        raise _fail(
            "agent_observation.silent_review_required",
            "agent observations require an explicit silent reference review acknowledgment",
        )
    if not isinstance(agent_label, str) or not agent_label.strip():
        raise _fail("agent_observation.agent_label_required", "agent label is required")
    observations = tuple(observations or ())
    if not observations:
        raise _fail("agent_observation.geometry_invalid", "no observations supplied")
    panel_ids = tuple(str(observation.get("panel_id", "")) for observation in observations)
    if not all(panel_ids) or len(set(panel_ids)) != len(panel_ids):
        raise _fail("agent_observation.duplicate_panel", "observations must name distinct panels")

    story_id = db.scalar(
        select(StoryAnalysis.id)
        .where(StoryAnalysis.project_id == project_id)
        .order_by(StoryAnalysis.created_at.desc(), StoryAnalysis.id.desc())
        .limit(1)
    )
    if story_id is None:
        raise _fail("visual.panel_lineage_unavailable", "project has no story analysis")
    regions = {
        str(region.panel_id): region
        for region in db.scalars(
            select(PanelRegion).where(
                PanelRegion.story_analysis_id == story_id,
                PanelRegion.panel_id.in_(panel_ids),
            )
        )
    }
    assets = {
        str(asset.id): asset
        for asset in db.scalars(
            select(SourceAsset).where(
                SourceAsset.project_id == project_id,
                SourceAsset.id.in_(
                    {str(region.source_asset_id) for region in regions.values()}
                ),
            )
        )
    }

    applied: list[str] = []
    entries: list[dict[str, Any]] = []
    for observation in observations:
        panel_id = str(observation["panel_id"])
        region = regions.get(panel_id)
        if region is None:
            raise _fail(
                "visual.panel_lineage_unavailable",
                f"panel {panel_id} is not part of the latest story analysis",
            )
        asset = assets.get(str(region.source_asset_id))
        current_checksum = str(getattr(asset, "original_checksum", "") or getattr(asset, "checksum", "") or "")
        if asset is None or not current_checksum or current_checksum != str(region.source_asset_checksum or ""):
            raise _fail(
                "visual.panel_lineage_unavailable",
                f"panel {panel_id} source checksum is stale",
            )
        payload = validate_agent_panel_observation(
            observation,
            panel_id=panel_id,
            source_asset_id=str(asset.id),
            source_order=int(region.source_order),
            agent_label=agent_label,
        )
        merged = dict(region.observation_json or {})
        merged["visual_evidence"] = payload
        region.observation_json = merged
        applied.append(panel_id)
        entries.append(
            {
                "panel_id": panel_id,
                "panel_region_id": str(region.id),
                "source_order": int(region.source_order),
                "balloon_mask_status": payload["balloon_mask_status"],
                "balloon_region_count": len(payload["balloon_regions"]),
                "protected_region_count": len(payload["protected_regions"]),
                "evidence_hash": payload["evidence_hash"],
                "evidence_source": AGENT_EVIDENCE_SOURCE,
            }
        )

    ledger: dict[str, Any] = {
        "contract_version": AGENT_OBSERVATION_CONTRACT_VERSION,
        "agent_label": agent_label.strip(),
        "project_id": project_id,
        "story_analysis_id": str(story_id),
        "publish_allowed": False,
        "production_evidence": False,
        "entries": entries,
    }
    ledger_path: Path | None = None
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        ledger_path = Path(output_dir) / "agent-observation-ledger.json"
        ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    db.flush()
    return {
        "applied": applied,
        "entries": entries,
        "publish_allowed": False,
        "ledger_path": ledger_path,
    }


__all__ = [
    "AGENT_EVIDENCE_SOURCE",
    "AGENT_OBSERVATION_CONTRACT_VERSION",
    "AgentObservationError",
    "apply_agent_panel_observations",
    "validate_agent_panel_observation",
]
