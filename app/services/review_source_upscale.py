"""Auditable, review-only source preparation for low-resolution silent previews."""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Mapping
from dataclasses import dataclass
from math import isclose, isfinite
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

REVIEW_SOURCE_UPSCALE_POLICY_ID = "review_silent_source_upscale_v1"
ORIGINAL_SOURCE_MATERIALIZATION = "original_source_v1"
PERSISTED_PANEL_CROP_MATERIALIZATION = "persisted_panel_crop_v1"


class ReviewSourceUpscaleError(ValueError):
    """Safe, stable errors for the opt-in review-only preparation boundary."""

    def __init__(self, message: str, code: str = "review.upscale_invalid") -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ReviewSourceUpscalePolicy:
    policy_id: str = REVIEW_SOURCE_UPSCALE_POLICY_ID
    target_width: int = 1080
    target_height: int = 1920
    minimum_source_resolution_factor: float = 1.15
    max_scale: float = 1.50
    allow_low_source_resolution_warning: bool = True
    resample_filter: str = "LANCZOS"
    version: str = "1.3.0"


REVIEW_SILENT_SOURCE_UPSCALE_V1 = ReviewSourceUpscalePolicy()


def resolve_original_source_path(
    source_root: Path,
    *,
    source_checksum: str,
    source_dimensions: tuple[int, int],
) -> Path:
    """Find the immutable original input matching persisted source lineage.

    Segmented assets intentionally store cropped bytes while their panel bounds
    remain in the original-strip coordinate space.  Review-only callers may
    provide the original input directory; this resolver accepts a file only
    after matching both its byte checksum and decoded dimensions.  It never
    infers the original from a slice filename.
    """
    root = Path(source_root)
    if not root.is_dir():
        raise ReviewSourceUpscaleError(
            "the review source directory is unavailable",
            "review.upscale_source_root_invalid",
        )
    if (
        not isinstance(source_checksum, str)
        or len(source_checksum) != 64
        or any(character not in "0123456789abcdef" for character in source_checksum.lower())
        or len(source_dimensions) != 2
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in source_dimensions
        )
    ):
        raise ReviewSourceUpscaleError(
            "the original source lineage is invalid",
            "review.upscale_source_lineage_invalid",
        )
    suffixes = {".jpg", ".jpeg", ".png", ".webp"}
    for candidate in sorted(
        (path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in suffixes),
        key=lambda path: path.relative_to(root).as_posix(),
    ):
        try:
            data = candidate.read_bytes()
        except OSError:
            continue
        if hashlib.sha256(data).hexdigest() != source_checksum:
            continue
        try:
            with Image.open(io.BytesIO(data)) as image:
                if tuple(image.size) != tuple(source_dimensions):
                    raise ReviewSourceUpscaleError(
                        "the original source dimensions do not match persisted lineage",
                        "review.upscale_source_geometry_invalid",
                    )
        except ReviewSourceUpscaleError:
            raise
        except (OSError, UnidentifiedImageError, ValueError):
            raise ReviewSourceUpscaleError(
                "the original source cannot be decoded",
                "review.upscale_source_invalid",
            ) from None
        return candidate
    raise ReviewSourceUpscaleError(
        "the original source bytes are unavailable",
        "review.upscale_source_missing",
    )


def resolve_persisted_panel_crop(
    data: bytes,
    *,
    asset_checksum: str,
    panel_bounds: tuple[int, int, int, int],
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """Resolve an exact stored panel crop when the full strip is unavailable.

    This is an explicit silent-review-only data boundary.  It accepts a crop
    only when the stored asset bytes match the persisted asset checksum and
    the decoded dimensions exactly match the persisted panel box dimensions.
    The original source checksum is never rewritten or treated as the crop
    checksum; callers record the separate materialization mode in the review
    manifest and keep publish/voiced rendering on the full-source path.
    """
    if (
        not isinstance(data, (bytes, bytearray))
        or not isinstance(asset_checksum, str)
        or len(asset_checksum) != 64
        or any(character not in "0123456789abcdef" for character in asset_checksum.lower())
    ):
        raise ReviewSourceUpscaleError(
            "persisted panel crop checksum is invalid",
            "review.panel_crop_fallback_checksum_invalid",
        )
    if (
        not isinstance(panel_bounds, tuple)
        or len(panel_bounds) != 4
        or any(isinstance(value, bool) or not isinstance(value, int) for value in panel_bounds)
        or panel_bounds[2] <= panel_bounds[0]
        or panel_bounds[3] <= panel_bounds[1]
    ):
        raise ReviewSourceUpscaleError(
            "persisted panel crop bounds are invalid",
            "review.panel_crop_fallback_geometry_invalid",
        )
    raw = bytes(data)
    if hashlib.sha256(raw).hexdigest() != asset_checksum.lower():
        raise ReviewSourceUpscaleError(
            "persisted panel crop bytes do not match the asset checksum",
            "review.panel_crop_fallback_checksum_invalid",
        )
    expected_size = (panel_bounds[2] - panel_bounds[0], panel_bounds[3] - panel_bounds[1])
    try:
        with Image.open(io.BytesIO(raw)) as decoded:
            decoded.load()
            crop = decoded.convert("RGB")
    except (OSError, UnidentifiedImageError, ValueError):
        raise ReviewSourceUpscaleError(
            "persisted panel crop cannot be decoded",
            "review.panel_crop_fallback_decode_invalid",
        ) from None
    if crop.size != expected_size:
        crop.close()
        raise ReviewSourceUpscaleError(
            "persisted panel crop dimensions do not match its panel bounds",
            "review.panel_crop_fallback_geometry_invalid",
        )
    return crop, (0, 0, crop.width, crop.height)


def resolve_review_source_upscale_policy(
    policy_id: str | None,
) -> ReviewSourceUpscalePolicy | None:
    if policy_id is None or not str(policy_id).strip():
        return None
    if policy_id != REVIEW_SOURCE_UPSCALE_POLICY_ID:
        raise ReviewSourceUpscaleError(
            "the requested source-upscale policy is not supported",
            "review.upscale_policy_unknown",
        )
    return REVIEW_SILENT_SOURCE_UPSCALE_V1


def validate_review_upscale_request(
    policy_id: str | None,
    *,
    silent_reference_review: bool,
    publish_allowed: bool,
) -> ReviewSourceUpscalePolicy | None:
    policy = resolve_review_source_upscale_policy(policy_id)
    if policy is None:
        return None
    if not silent_reference_review:
        raise ReviewSourceUpscaleError(
            "source upscale is available only for an explicit silent review",
            "review.upscale_requires_silent_review",
        )
    if publish_allowed:
        raise ReviewSourceUpscaleError(
            "source upscale cannot be used for a publishable or voiced render",
            "review.upscale_publish_forbidden",
        )
    return policy


def canonical_rgb_hash(image: Image.Image) -> str:
    if not isinstance(image, Image.Image) or image.width <= 0 or image.height <= 0:
        raise ReviewSourceUpscaleError(
            "image dimensions are invalid", "review.upscale_image_invalid"
        )
    rgb = image.convert("RGB")
    payload = (
        rgb.width.to_bytes(8, "big", signed=False)
        + rgb.height.to_bytes(8, "big", signed=False)
        + rgb.tobytes()
    )
    return hashlib.sha256(payload).hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _manifest_hash(manifest: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def normalize_review_manifest_materialization(
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize legacy manifests whose materialization field was absent/null."""
    if not isinstance(manifest, Mapping):
        raise ReviewSourceUpscaleError(
            "review manifest is not an object", "review.upscale_manifest_invalid"
        )
    normalized = dict(manifest)
    if "source_materialization" not in normalized or normalized["source_materialization"] is None:
        normalized["source_materialization"] = ORIGINAL_SOURCE_MATERIALIZATION
        if "manifest_sha256" in normalized:
            normalized["manifest_sha256"] = _manifest_hash(normalized)
    return normalized


def _scaled_dimension(value: int, scale: float) -> int:
    return max(1, round(value * scale))


def transform_panel_bounds(
    bounds: tuple[int, int, int, int], manifest: Mapping[str, Any]
) -> tuple[int, int, int, int]:
    if (
        not isinstance(bounds, tuple)
        or len(bounds) != 4
        or any(isinstance(value, bool) or not isinstance(value, int) for value in bounds)
        or bounds[0] < 0
        or bounds[1] < 0
        or bounds[2] <= bounds[0]
        or bounds[3] <= bounds[1]
    ):
        raise ReviewSourceUpscaleError(
            "source panel bounds are invalid", "review.upscale_geometry_invalid"
        )
    original = manifest.get("original_dimensions")
    source_dimensions = manifest.get("source_dimensions", original)
    if (
        not isinstance(original, list)
        or len(original) != 2
        or not isinstance(source_dimensions, list)
        or len(source_dimensions) != 2
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in original + source_dimensions
        )
        or bounds[2] > int(source_dimensions[0])
        or bounds[3] > int(source_dimensions[1])
    ):
        raise ReviewSourceUpscaleError(
            "source panel bounds do not match the manifest",
            "review.upscale_geometry_invalid",
        )
    scale = manifest.get("scale_factor")
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not isfinite(float(scale)):
        raise ReviewSourceUpscaleError(
            "manifest scale is invalid", "review.upscale_manifest_invalid"
        )
    left = round(bounds[0] * float(scale))
    top = round(bounds[1] * float(scale))
    width = _scaled_dimension(bounds[2] - bounds[0], float(scale))
    height = _scaled_dimension(bounds[3] - bounds[1], float(scale))
    return left, top, left + width, top + height


def prepare_review_panel(
    image: Image.Image,
    *,
    policy: ReviewSourceUpscalePolicy,
    source_asset_id: str,
    panel_region_id: str,
    source_asset_checksum: str,
    source_panel_bounds: tuple[int, int, int, int] | None = None,
    source_dimensions: tuple[int, int] | None = None,
    source_materialization: str = ORIGINAL_SOURCE_MATERIALIZATION,
) -> tuple[Image.Image, dict[str, Any]]:
    if not isinstance(policy, ReviewSourceUpscalePolicy):
        raise ReviewSourceUpscaleError(
            "a validated review-upscale policy is required",
            "review.upscale_policy_required",
        )
    if not source_asset_id or not panel_region_id or not source_asset_checksum:
        raise ReviewSourceUpscaleError(
            "source lineage is incomplete", "review.upscale_lineage_invalid"
        )
    if source_materialization not in {
        ORIGINAL_SOURCE_MATERIALIZATION,
        PERSISTED_PANEL_CROP_MATERIALIZATION,
    }:
        raise ReviewSourceUpscaleError(
            "source materialization is unsupported",
            "review.upscale_lineage_invalid",
        )
    original = image.convert("RGB")
    if original.width <= 0 or original.height <= 0:
        raise ReviewSourceUpscaleError(
            "source panel dimensions are invalid", "review.upscale_image_invalid"
        )
    scale = max(
        1.0,
        policy.target_width / original.width,
        policy.target_height / (original.height * policy.minimum_source_resolution_factor),
    )
    over_automatic_cap = scale > policy.max_scale + 1e-9
    if over_automatic_cap and not policy.allow_low_source_resolution_warning:
        raise ReviewSourceUpscaleError(
            "the required scale exceeds the review policy cap",
            "review.upscale_limit_exceeded",
        )
    prepared_size = (
        _scaled_dimension(original.width, scale),
        _scaled_dimension(original.height, scale),
    )
    if scale == 1.0:
        prepared = original.copy()
    else:
        prepared = original.resize(prepared_size, Image.Resampling.LANCZOS)
    full_source_dimensions = source_dimensions or (original.width, original.height)
    if (
        len(full_source_dimensions) != 2
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in full_source_dimensions
        )
    ):
        raise ReviewSourceUpscaleError(
            "source dimensions are invalid", "review.upscale_geometry_invalid"
        )
    manifest: dict[str, Any] = {
        "policy_id": policy.policy_id,
        "policy_version": policy.version,
        "source_asset_id": source_asset_id,
        "panel_region_id": panel_region_id,
        "source_asset_checksum": source_asset_checksum,
        "source_materialization": source_materialization,
        "original_dimensions": [original.width, original.height],
        "source_dimensions": list(full_source_dimensions),
        "prepared_dimensions": [prepared.width, prepared.height],
        "target_dimensions": [policy.target_width, policy.target_height],
        "minimum_source_resolution_factor": policy.minimum_source_resolution_factor,
        "scale_factor": round(scale, 6),
        "automatic_scale_cap": policy.max_scale,
        "resample_filter": policy.resample_filter,
        "original_content_sha256": canonical_rgb_hash(original),
        "prepared_content_sha256": canonical_rgb_hash(prepared),
        "non_native_warning": (
            "review.low_source_resolution"
            if over_automatic_cap
            else "review.source_upscale_non_native"
            if scale > 1.0
            else ""
        ),
        "resolution_state": (
            "LOW_SOURCE_RESOLUTION"
            if over_automatic_cap
            else "UPSCALED"
            if scale > 1.0
            else "NATIVE"
        ),
    }
    if source_panel_bounds is not None:
        manifest["source_panel_bounds"] = list(source_panel_bounds)
        manifest["prepared_panel_bounds"] = list(
            transform_panel_bounds(source_panel_bounds, manifest)
        )
    manifest["manifest_sha256"] = _manifest_hash(manifest)
    return prepared, manifest


def validate_review_manifest(
    manifest: Mapping[str, Any], prepared: Image.Image
) -> ReviewSourceUpscalePolicy:
    if not isinstance(manifest, Mapping):
        raise ReviewSourceUpscaleError(
            "review manifest is not an object", "review.upscale_manifest_invalid"
        )
    policy = validate_review_manifest_dimensions(manifest, prepared.size)
    if not isinstance(prepared, Image.Image):
        raise ReviewSourceUpscaleError(
            "prepared image is invalid", "review.upscale_manifest_invalid"
        )
    if manifest.get("prepared_content_sha256") != canonical_rgb_hash(prepared):
        raise ReviewSourceUpscaleError(
            "prepared content hash does not match",
            "review.upscale_manifest_invalid",
        )
    return policy


def validate_review_manifest_dimensions(
    manifest: Mapping[str, Any], prepared_size: tuple[int, int]
) -> ReviewSourceUpscalePolicy:
    """Validate manifest identity and geometry without needing prepared pixels."""
    if not isinstance(manifest, Mapping):
        raise ReviewSourceUpscaleError(
            "review manifest is not an object", "review.upscale_manifest_invalid"
        )
    policy = resolve_review_source_upscale_policy(str(manifest.get("policy_id", "")))
    normalized = normalize_review_manifest_materialization(manifest)
    if policy is None or manifest.get("manifest_sha256") not in {
        _manifest_hash(manifest),
        _manifest_hash(normalized),
    }:
        raise ReviewSourceUpscaleError(
            "review manifest identity does not match",
            "review.upscale_manifest_invalid",
        )
    if normalized["source_materialization"] not in {
        ORIGINAL_SOURCE_MATERIALIZATION,
        PERSISTED_PANEL_CROP_MATERIALIZATION,
    }:
        raise ReviewSourceUpscaleError(
            "review manifest source materialization is unsupported",
            "review.upscale_manifest_invalid",
        )
    prepared_dimensions = manifest.get("prepared_dimensions")
    original_dimensions = manifest.get("original_dimensions")
    source_dimensions = manifest.get("source_dimensions", original_dimensions)
    scale = manifest.get("scale_factor")
    resolution_state = manifest.get("resolution_state")
    over_automatic_cap = (
        isinstance(scale, (int, float))
        and not isinstance(scale, bool)
        and float(scale) > policy.max_scale + 1e-9
    )
    if (
        not isinstance(prepared_dimensions, list)
        or tuple(prepared_dimensions) != tuple(prepared_size)
        or not isinstance(original_dimensions, list)
        or len(original_dimensions) != 2
        or not isinstance(source_dimensions, list)
        or len(source_dimensions) != 2
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in original_dimensions + source_dimensions
        )
        or isinstance(scale, bool)
        or not isinstance(scale, (int, float))
        or not isfinite(float(scale))
        or float(scale) < 1.0
        or (
            over_automatic_cap
            and (
                not policy.allow_low_source_resolution_warning
                or resolution_state != "LOW_SOURCE_RESOLUTION"
            )
        )
        or (
            not over_automatic_cap
            and resolution_state
            != ("UPSCALED" if float(scale) > 1.0 else "NATIVE")
        )
        or manifest.get("automatic_scale_cap") != policy.max_scale
        or manifest.get("target_dimensions") != [policy.target_width, policy.target_height]
        or manifest.get("minimum_source_resolution_factor")
        != policy.minimum_source_resolution_factor
        or manifest.get("policy_version") != policy.version
        or not isclose(
            prepared_size[0], _scaled_dimension(original_dimensions[0], float(scale)), abs_tol=0
        )
        or not isclose(
            prepared_size[1], _scaled_dimension(original_dimensions[1], float(scale)), abs_tol=0
        )
        or manifest.get("resample_filter") != policy.resample_filter
    ):
        raise ReviewSourceUpscaleError(
            "review manifest geometry is invalid", "review.upscale_manifest_invalid"
        )
    return policy
