"""Durable, payload-free prepared-panel identities for warm cloud resumes.

The manifest is intentionally metadata-only.  It records the immutable source
and rendered-payload identities needed to validate a cached visual stage, but
never serializes image bytes or provider responses.  A caller that needs
pixels must explicitly perform a cold materialization and cannot accidentally
send the marker payload to a provider.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

MANIFEST_VERSION = "prepared-panel-manifest-v2"
LEGACY_MANIFEST_VERSION = "prepared-panel-manifest-v1"
PAYLOAD_MARKER_PREFIX = b"prepared-panel-manifest-v2:"


class PreparedPanelManifestError(ValueError):
    """Raised when a durable prepared-panel manifest cannot be trusted."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _require_hash(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise PreparedPanelManifestError(f"{field} must be a sha256")
    try:
        int(value, 16)
    except ValueError as exc:
        raise PreparedPanelManifestError(f"{field} must be a sha256") from exc
    return value


def _normalize_assets(source_assets: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in source_assets:
        if not isinstance(raw, Mapping):
            raise PreparedPanelManifestError("source asset metadata is malformed")
        asset_id = str(raw.get("source_asset_id", ""))
        if not asset_id or asset_id in seen:
            raise PreparedPanelManifestError("source asset metadata is duplicated")
        seen.add(asset_id)
        checksum = str(raw.get("source_checksum", ""))
        _require_hash(checksum, f"source asset {asset_id} checksum")
        dimensions = raw.get("original_dimensions")
        if (
            not isinstance(dimensions, (list, tuple))
            or len(dimensions) != 2
            or any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in dimensions)
        ):
            raise PreparedPanelManifestError("source asset dimensions are malformed")
        normalized.append(
            {
                "source_asset_id": asset_id,
                "source_checksum": checksum,
                "original_dimensions": [int(dimensions[0]), int(dimensions[1])],
                "strip_order": int(raw.get("strip_order", 0)),
                "region_order": int(raw.get("region_order", 0)),
                "source_family": str(raw.get("source_family", "")),
            }
        )
    return sorted(normalized, key=lambda item: (item["strip_order"], item["region_order"], item["source_asset_id"]))


def _normalize_panel_descriptors(
    panel_descriptors: Sequence[Mapping[str, Any]],
    *,
    require_prepared_order: bool = False,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    previous_source_order = -1
    for index, raw in enumerate(panel_descriptors):
        if not isinstance(raw, Mapping):
            raise PreparedPanelManifestError("prepared panel descriptor is malformed")
        panel_id = str(raw.get("panel_id", ""))
        asset_id = str(raw.get("source_asset_id", ""))
        if not panel_id or not asset_id or panel_id in seen:
            raise PreparedPanelManifestError("prepared panel identity is duplicated or empty")
        seen.add(panel_id)
        source_order = raw.get("source_order")
        if (
            isinstance(source_order, bool)
            or not isinstance(source_order, int)
            or source_order < 0
            or source_order <= previous_source_order
        ):
            raise PreparedPanelManifestError("prepared panel source order is not strictly increasing")
        previous_source_order = source_order
        prepared_order = raw.get("prepared_order")
        if prepared_order is None and not require_prepared_order:
            prepared_order = index
        if (
            isinstance(prepared_order, bool)
            or not isinstance(prepared_order, int)
            or prepared_order != index
        ):
            raise PreparedPanelManifestError("prepared panel execution order is not contiguous")
        source_checksum = str(raw.get("source_checksum", ""))
        identity_checksum = str(raw.get("identity_payload_checksum", ""))
        identity_descriptor_hash = str(raw.get("identity_descriptor_hash", ""))
        _require_hash(source_checksum, f"panel {panel_id} source checksum")
        if identity_checksum:
            _require_hash(identity_checksum, f"panel {panel_id} payload checksum")
        _require_hash(identity_descriptor_hash, f"panel {panel_id} identity hash")
        source_identity_hash = _require_hash(
            raw.get("source_identity_hash"),
            f"panel {panel_id} source identity hash",
        )
        bounds = raw.get("panel_bounds")
        dimensions = raw.get("source_dimensions")
        if (
            not isinstance(bounds, (list, tuple))
            or len(bounds) != 4
            or any(isinstance(item, bool) or not isinstance(item, int) for item in bounds)
            or bounds[0] < 0
            or bounds[1] < 0
            or bounds[2] <= bounds[0]
            or bounds[3] <= bounds[1]
        ):
            raise PreparedPanelManifestError(f"panel {panel_id} bounds are malformed")
        if (
            not isinstance(dimensions, (list, tuple))
            or len(dimensions) != 2
            or any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in dimensions)
            or bounds[2] > dimensions[0]
            or bounds[3] > dimensions[1]
        ):
            raise PreparedPanelManifestError(f"panel {panel_id} dimensions are malformed")
        normalized.append(
            {
                "panel_id": panel_id,
                "source_asset_id": asset_id,
                "source_order": source_order,
                "prepared_order": prepared_order,
                "source_checksum": source_checksum,
                "identity_payload_checksum": identity_checksum,
                "identity_descriptor_hash": identity_descriptor_hash,
                "source_identity_hash": source_identity_hash,
                "panel_bounds": [int(item) for item in bounds],
                "source_dimensions": [int(item) for item in dimensions],
                "strip_region_id": str(raw.get("strip_region_id", panel_id)),
                "coverage_map_version": str(raw.get("coverage_map_version", "")),
                "coverage_map_hash": str(raw.get("coverage_map_hash", "")),
                "segmentation_version": str(raw.get("segmentation_version", "")),
                "source_family": str(raw.get("source_family", "")),
            }
        )
    return normalized


def _ledger_summary(ledger: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(ledger, Mapping):
        return None
    entries = ledger.get("entries")
    if not isinstance(entries, list):
        return None
    crop_hashes = sorted(
        str(entry.get("crop_identity_hash", ""))
        for entry in entries
        if isinstance(entry, Mapping) and str(entry.get("crop_identity_hash", ""))
    )
    ledger_hash = str(ledger.get("ledger_hash", ""))
    if ledger_hash:
        _require_hash(ledger_hash, "feasible ledger hash")
    return {
        "ledger_hash": ledger_hash,
        "entry_count": len(entries),
        "crop_identity_hashes": crop_hashes,
    }


@dataclass(frozen=True)
class PreparedPanelManifest:
    """Validated compact manifest; no panel image bytes are retained."""

    manifest_version: str
    source_asset_fingerprint: str
    source_assets: tuple[dict[str, Any], ...]
    panel_descriptors: tuple[dict[str, Any], ...]
    panel_identity_hashes: tuple[str, ...]
    source_identity_hash: str
    segmentation_state: dict[str, Any]
    feasible_visual_ledger: dict[str, Any] | None
    manifest_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": self.manifest_version,
            "source_asset_fingerprint": self.source_asset_fingerprint,
            "source_assets": [dict(item) for item in self.source_assets],
            "panel_descriptors": [dict(item) for item in self.panel_descriptors],
            "panel_identity_hashes": list(self.panel_identity_hashes),
            "source_identity_hash": self.source_identity_hash,
            "segmentation_state": dict(self.segmentation_state),
            "feasible_visual_ledger": (
                dict(self.feasible_visual_ledger)
                if self.feasible_visual_ledger is not None
                else None
            ),
            "manifest_hash": self.manifest_hash,
        }


def build_manifest(
    panels: Sequence[Any],
    segmentation_state: Mapping[str, Any],
    *,
    panel_identity_hashes: Sequence[str],
    source_identity_hash: str | None = None,
    source_assets: Sequence[Mapping[str, Any]],
    feasible_visual_ledger: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(segmentation_state, Mapping):
        raise PreparedPanelManifestError("segmentation state is malformed")
    normalized_hashes = tuple(_require_hash(value, "panel identity hash") for value in panel_identity_hashes)
    source_identity = source_identity_hash or _hash(list(normalized_hashes))
    _require_hash(source_identity, "source identity hash")
    if len(normalized_hashes) != len(panels):
        raise PreparedPanelManifestError("panel identity count does not match descriptors")
    descriptors: list[dict[str, Any]] = []
    for index, panel in enumerate(panels):
        descriptor = dict(panel.descriptor())
        descriptor["identity_payload_checksum"] = str(
            getattr(panel, "identity_payload_checksum", "") or panel.payload_checksum
        )
        descriptor["identity_descriptor_hash"] = str(
            getattr(panel, "identity_descriptor_hash", "") or normalized_hashes[index]
        )
        descriptor["prepared_order"] = int(
            getattr(panel, "prepared_order", None)
            if getattr(panel, "prepared_order", None) is not None
            else index
        )
        descriptor["source_identity_hash"] = source_identity
        descriptor["source_family"] = str(getattr(panel, "source_family", "") or "")
        descriptors.append(descriptor)
    return build_manifest_from_descriptors(
        descriptors,
        segmentation_state,
        panel_identity_hashes=normalized_hashes,
        source_identity_hash=source_identity,
        source_assets=source_assets,
        feasible_visual_ledger=feasible_visual_ledger,
    )


def build_manifest_from_descriptors(
    panel_descriptors: Sequence[Mapping[str, Any]],
    segmentation_state: Mapping[str, Any],
    *,
    panel_identity_hashes: Sequence[str],
    source_identity_hash: str,
    source_assets: Sequence[Mapping[str, Any]],
    feasible_visual_ledger: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a manifest from metadata when source pixels are already cached.

    ``identity_descriptor_hash`` is the previously reconciled visual-input
    identity.  It lets a warm resume validate the exact cached source without
    pretending that a marker payload is real image data.
    """

    if not isinstance(segmentation_state, Mapping):
        raise PreparedPanelManifestError("segmentation state is malformed")
    normalized_hashes = tuple(_require_hash(value, "panel identity hash") for value in panel_identity_hashes)
    if len(normalized_hashes) != len(panel_descriptors):
        raise PreparedPanelManifestError("panel identity count does not match descriptors")
    source_identity = _require_hash(source_identity_hash, "source identity hash")
    descriptors: list[dict[str, Any]] = []
    for index, raw in enumerate(panel_descriptors):
        if not isinstance(raw, Mapping):
            raise PreparedPanelManifestError("prepared panel descriptor is malformed")
        descriptor = dict(raw)
        descriptor["identity_descriptor_hash"] = str(
            descriptor.get("identity_descriptor_hash", "") or normalized_hashes[index]
        )
        descriptor["identity_payload_checksum"] = str(
            descriptor.get("identity_payload_checksum", "")
            or descriptor["identity_descriptor_hash"]
        )
        if descriptor.get("prepared_order") is None:
            descriptor["prepared_order"] = index
        descriptor["source_identity_hash"] = source_identity
        descriptor["metadata_only"] = True
        descriptors.append(descriptor)
    normalized_panels = _normalize_panel_descriptors(
        descriptors,
        require_prepared_order=True,
    )
    normalized_assets = _normalize_assets(source_assets)
    if len(normalized_hashes) != len(normalized_panels):
        raise PreparedPanelManifestError("panel identity count does not match descriptors")
    if any(item["identity_descriptor_hash"] != normalized_hashes[index] for index, item in enumerate(normalized_panels)):
        raise PreparedPanelManifestError("panel identity hash does not match descriptor")
    for panel in normalized_panels:
        matching_asset = next(
            (asset for asset in normalized_assets if asset["source_asset_id"] == panel["source_asset_id"]),
            None,
        )
        if matching_asset is None or matching_asset["source_checksum"] != panel["source_checksum"]:
            raise PreparedPanelManifestError("panel source asset identity mismatch")
    ledger_summary = _ledger_summary(feasible_visual_ledger)
    return _make_manifest(
        manifest_version=MANIFEST_VERSION,
        source_assets=normalized_assets,
        panels=normalized_panels,
        panel_identity_hashes=normalized_hashes,
        source_identity_hash=source_identity,
        segmentation_state=segmentation_state,
        feasible_visual_ledger=ledger_summary,
    )


def _manifest_core(
    *,
    manifest_version: str,
    source_asset_fingerprint: str,
    source_assets: Sequence[Mapping[str, Any]],
    panels: Sequence[Mapping[str, Any]],
    panel_identity_hashes: Sequence[str],
    source_identity_hash: str,
    segmentation_state: Mapping[str, Any],
    feasible_visual_ledger: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "manifest_version": manifest_version,
        "source_asset_fingerprint": source_asset_fingerprint,
        "source_assets": [dict(item) for item in source_assets],
        "panel_descriptors": [dict(item) for item in panels],
        "panel_identity_hashes": list(panel_identity_hashes),
        "source_identity_hash": source_identity_hash,
        "segmentation_state_hash": _hash(dict(segmentation_state)),
        "feasible_visual_ledger": (
            dict(feasible_visual_ledger)
            if feasible_visual_ledger is not None
            else None
        ),
    }


def _make_manifest(
    *,
    manifest_version: str,
    source_assets: Sequence[Mapping[str, Any]],
    panels: Sequence[Mapping[str, Any]],
    panel_identity_hashes: Sequence[str],
    source_identity_hash: str,
    segmentation_state: Mapping[str, Any],
    feasible_visual_ledger: Mapping[str, Any] | None,
) -> dict[str, Any]:
    source_fingerprint = _hash(list(source_assets))
    core = _manifest_core(
        manifest_version=manifest_version,
        source_asset_fingerprint=source_fingerprint,
        source_assets=source_assets,
        panels=panels,
        panel_identity_hashes=panel_identity_hashes,
        source_identity_hash=source_identity_hash,
        segmentation_state=segmentation_state,
        feasible_visual_ledger=feasible_visual_ledger,
    )
    return PreparedPanelManifest(
        manifest_version=manifest_version,
        source_asset_fingerprint=source_fingerprint,
        source_assets=tuple(dict(item) for item in source_assets),
        panel_descriptors=tuple(dict(item) for item in panels),
        panel_identity_hashes=tuple(panel_identity_hashes),
        source_identity_hash=source_identity_hash,
        segmentation_state=dict(segmentation_state),
        feasible_visual_ledger=(
            dict(feasible_visual_ledger)
            if feasible_visual_ledger is not None
            else None
        ),
        manifest_hash=_hash(core),
    ).as_dict()


def _validated_parts(
    value: Mapping[str, Any],
    *,
    require_prepared_order: bool,
) -> tuple[
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    tuple[str, ...],
    str,
    Mapping[str, Any],
    dict[str, Any] | None,
]:
    source_assets = tuple(_normalize_assets(value.get("source_assets", ())))
    panels = tuple(
        _normalize_panel_descriptors(
            value.get("panel_descriptors", ()),
            require_prepared_order=require_prepared_order,
        )
    )
    hashes = tuple(
        _require_hash(item, "panel identity hash")
        for item in value.get("panel_identity_hashes", ())
    )
    if len(hashes) != len(panels):
        raise PreparedPanelManifestError("panel identity count does not match descriptors")
    source_identity_hash = _require_hash(
        value.get("source_identity_hash"),
        "source identity hash",
    )
    if any(
        panel.get("source_identity_hash") != source_identity_hash
        or panel.get("identity_descriptor_hash") != hashes[index]
        for index, panel in enumerate(panels)
    ):
        raise PreparedPanelManifestError("panel identity hash does not match manifest")
    source_fingerprint = str(value.get("source_asset_fingerprint", ""))
    if source_fingerprint != _hash(list(source_assets)):
        raise PreparedPanelManifestError("source asset fingerprint mismatch")
    for panel in panels:
        matching_asset = next(
            (
                asset
                for asset in source_assets
                if asset["source_asset_id"] == panel["source_asset_id"]
            ),
            None,
        )
        if matching_asset is None or matching_asset["source_checksum"] != panel["source_checksum"]:
            raise PreparedPanelManifestError("panel source asset identity mismatch")
    segmentation_state = value.get("segmentation_state")
    if not isinstance(segmentation_state, Mapping):
        raise PreparedPanelManifestError("segmentation state is malformed")
    ledger_summary = value.get("feasible_visual_ledger")
    if ledger_summary is not None and not isinstance(ledger_summary, Mapping):
        raise PreparedPanelManifestError("feasible ledger summary is malformed")
    return (
        source_assets,
        panels,
        hashes,
        source_identity_hash,
        segmentation_state,
        dict(ledger_summary) if ledger_summary is not None else None,
    )


def migrate_legacy_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    """Migrate a v1 manifest using metadata only and preserve source lineage."""

    if not isinstance(value, Mapping) or value.get("manifest_version") != LEGACY_MANIFEST_VERSION:
        raise PreparedPanelManifestError("prepared manifest version is unsupported")
    (
        source_assets,
        panels,
        hashes,
        source_identity_hash,
        segmentation_state,
        ledger_summary,
    ) = _validated_parts(value, require_prepared_order=False)
    legacy_panels = [
        {key: item for key, item in panel.items() if key != "prepared_order"}
        for panel in panels
    ]
    legacy_core = _manifest_core(
        manifest_version=LEGACY_MANIFEST_VERSION,
        source_asset_fingerprint=str(value["source_asset_fingerprint"]),
        source_assets=source_assets,
        panels=legacy_panels,
        panel_identity_hashes=hashes,
        source_identity_hash=source_identity_hash,
        segmentation_state=segmentation_state,
        feasible_visual_ledger=ledger_summary,
    )
    manifest_hash = _require_hash(value.get("manifest_hash"), "manifest hash")
    if manifest_hash != _hash(legacy_core):
        raise PreparedPanelManifestError("prepared manifest hash mismatch")
    return _make_manifest(
        manifest_version=MANIFEST_VERSION,
        source_assets=source_assets,
        panels=panels,
        panel_identity_hashes=hashes,
        source_identity_hash=source_identity_hash,
        segmentation_state=segmentation_state,
        feasible_visual_ledger=ledger_summary,
    )


def validate_manifest(value: Mapping[str, Any]) -> PreparedPanelManifest:
    if not isinstance(value, Mapping):
        raise PreparedPanelManifestError("prepared manifest version is unsupported")
    if value.get("manifest_version") == LEGACY_MANIFEST_VERSION:
        value = migrate_legacy_manifest(value)
    if value.get("manifest_version") != MANIFEST_VERSION:
        raise PreparedPanelManifestError("prepared manifest version is unsupported")
    (
        source_assets,
        panels,
        hashes,
        source_identity_hash,
        segmentation_state,
        ledger_summary,
    ) = _validated_parts(value, require_prepared_order=True)
    core = _manifest_core(
        manifest_version=MANIFEST_VERSION,
        source_asset_fingerprint=str(value["source_asset_fingerprint"]),
        source_assets=source_assets,
        panels=panels,
        panel_identity_hashes=hashes,
        source_identity_hash=source_identity_hash,
        segmentation_state=segmentation_state,
        feasible_visual_ledger=ledger_summary,
    )
    manifest_hash = _require_hash(value.get("manifest_hash"), "manifest hash")
    if manifest_hash != _hash(core):
        raise PreparedPanelManifestError("prepared manifest hash mismatch")
    return PreparedPanelManifest(
        manifest_version=MANIFEST_VERSION,
        source_asset_fingerprint=str(value["source_asset_fingerprint"]),
        source_assets=source_assets,
        panel_descriptors=panels,
        panel_identity_hashes=hashes,
        source_identity_hash=source_identity_hash,
        segmentation_state=dict(segmentation_state),
        feasible_visual_ledger=ledger_summary,
        manifest_hash=manifest_hash,
    )


def restore_cloud_panels(manifest: PreparedPanelManifest, panel_type: type[Any]) -> tuple[Any, ...]:
    """Reconstruct metadata-only CloudPanelInput values from a validated manifest."""

    restored: list[Any] = []
    for descriptor, _identity_hash in zip(
        manifest.panel_descriptors,
        manifest.panel_identity_hashes,
        strict=True,
    ):
        payload_checksum = str(
            descriptor.get("identity_payload_checksum")
            or descriptor["identity_descriptor_hash"]
        )
        marker = PAYLOAD_MARKER_PREFIX + payload_checksum.encode("ascii")
        restored.append(
            panel_type(
                panel_id=descriptor["panel_id"],
                source_asset_id=descriptor["source_asset_id"],
                source_order=descriptor["source_order"],
                prepared_order=descriptor["prepared_order"],
                mime_type="image/png",
                payload=marker,
                source_checksum=descriptor["source_checksum"],
                source_family=descriptor["source_family"],
                panel_bounds=tuple(descriptor["panel_bounds"]),
                source_dimensions=tuple(descriptor["source_dimensions"]),
                strip_region_id=descriptor["strip_region_id"],
                coverage_map_version=descriptor["coverage_map_version"],
                coverage_map_hash=descriptor["coverage_map_hash"],
                segmentation_version=descriptor["segmentation_version"],
                identity_payload_checksum=payload_checksum,
                identity_descriptor_hash=descriptor["identity_descriptor_hash"],
                source_identity_hash=manifest.source_identity_hash,
                metadata_only=True,
            )
        )
    return tuple(restored)


def source_asset_metadata(assets: Sequence[Any]) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for asset in assets:
        checksum = str(getattr(asset, "original_checksum", "") or getattr(asset, "checksum", ""))
        rows.append(
            {
                "source_asset_id": str(asset.id),
                "source_checksum": checksum,
                "original_dimensions": [int(asset.original_width or asset.width), int(asset.original_height or asset.height)],
                "strip_order": int(getattr(asset, "strip_order", 0) or 0),
                "region_order": int(getattr(asset, "region_order", 0) or 0),
                "source_family": str(getattr(asset, "source_family", "") or ""),
            }
        )
    return tuple(rows)


def source_asset_fingerprint(source_assets: Sequence[Mapping[str, Any]]) -> str:
    return _hash(_normalize_assets(source_assets))


def require_source_assets_match(
    manifest: PreparedPanelManifest,
    source_assets: Sequence[Mapping[str, Any]],
) -> None:
    if source_asset_fingerprint(source_assets) != manifest.source_asset_fingerprint:
        raise PreparedPanelManifestError("current source asset fingerprint mismatch")


__all__ = [
    "MANIFEST_VERSION",
    "LEGACY_MANIFEST_VERSION",
    "PAYLOAD_MARKER_PREFIX",
    "PreparedPanelManifest",
    "PreparedPanelManifestError",
    "build_manifest",
    "build_manifest_from_descriptors",
    "migrate_legacy_manifest",
    "require_source_assets_match",
    "restore_cloud_panels",
    "source_asset_fingerprint",
    "source_asset_metadata",
    "validate_manifest",
]
