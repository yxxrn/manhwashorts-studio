"""Pre-publication quality checks (PRD FR-08).

Errors block publication; warnings can be overridden with a recorded reason.
This module answers "is this video safe and good enough to publish" and is the
only gate the publish endpoint trusts.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path

from app.config import settings
from app.constants import (
    MAX_SUBTITLE_CHARS_PER_LINE,
    MAX_SUBTITLE_LINES,
    CheckSeverity,
)
from app.models import Project, RenderJob, ScriptVersion, SourceAsset
from app.services import (
    editorial_timing,
    framing_analysis,
    motion_director,
    policy,
    reference_profile,
    subtitle_karaoke,
    visual_scoring,
)
from app.services.timeline import CueSpec, validate_cues


@dataclass
class CheckResult:
    code: str
    severity: str
    message: str
    passed: bool
    detail: dict = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return not self.passed and self.severity == CheckSeverity.ERROR


def _fail(code: str, severity: str, message: str, detail: dict | None = None) -> CheckResult:
    return CheckResult(code, severity, message, passed=False, detail=detail or {})


def _pass(code: str, message: str = "OK") -> CheckResult:
    return CheckResult(code, CheckSeverity.INFO, message, passed=True)


def check_reference_output_profile(info: dict, profile) -> list[CheckResult]:
    """Return stable QC failures for the reference final stream contract."""
    results: list[CheckResult] = []
    scalar_checks = (
        (
            "width",
            profile.final_width,
            "reference.output_resolution",
            "Reference output width is not 1080 pixels.",
        ),
        (
            "height",
            profile.final_height,
            "reference.output_resolution",
            "Reference output height is not 1920 pixels.",
        ),
        (
            "codec",
            profile.final_codec,
            "reference.output_codec",
            "Reference output is not H.264.",
        ),
        (
            "profile",
            profile.final_codec_profile,
            "reference.output_codec_profile",
            "Reference output is not H.264 High profile.",
        ),
        (
            "pix_fmt",
            profile.final_pixel_format,
            "reference.output_pix_fmt",
            "Reference output is not yuv420p.",
        ),
    )
    seen_codes: set[str] = set()
    for key, expected, code, message in scalar_checks:
        actual = info.get(key)
        if key in {"codec", "profile", "pix_fmt"}:
            matches = str(actual or "").lower() == str(expected).lower()
        else:
            matches = actual == expected
        if not matches and code not in seen_codes:
            results.append(_fail(code, CheckSeverity.ERROR, message, {"expected": expected, "actual": actual}))
            seen_codes.add(code)
    actual_fps = info.get("fps")
    if actual_fps is None or abs(float(actual_fps) - float(profile.final_fps)) > 0.01:
        results.append(
            _fail(
                "reference.output_fps",
                CheckSeverity.ERROR,
                f"Reference output frame rate is not {profile.final_fps} fps.",
                {"expected": profile.final_fps, "actual": actual_fps},
            )
        )
    return results


def check_script_approved(script: ScriptVersion | None) -> list[CheckResult]:
    if script is None:
        return [
            _fail(
                "script.missing",
                CheckSeverity.ERROR,
                "No script has been generated yet.",
            )
        ]
    if script.approved_at is None:
        return [
            _fail(
                "script.not_approved",
                CheckSeverity.ERROR,
                "The script must be reviewed and approved before publishing.",
            )
        ]
    return [_pass("script.approved", f"Script v{script.version} approved.")]


def check_voice_profile(segments: list) -> list[CheckResult]:
    """One immutable provider/model/voice/format contract per render."""
    if not segments:
        return []
    hashes = {str(getattr(segment, "voice_profile_hash", "")) for segment in segments}
    profiles = [getattr(segment, "voice_profile", {}) or {} for segment in segments]
    if "" in hashes or len(hashes) != 1:
        return [_fail(
            "voice.profile_changed", CheckSeverity.ERROR,
            "Voice profile hash changed or is missing between narration chunks.",
            {"profile_hashes": sorted(hashes)},
        )]
    identities = {
        tuple(profile.get(key, "") for key in ("provider", "model", "voice_id", "language", "speed"))
        for profile in profiles
    }
    formats = {
        (profile.get("sample_rate", 0), profile.get("channels", 0))
        for profile in profiles
    }
    results: list[CheckResult] = []
    if len(identities) != 1:
        results.append(_fail(
            "voice.identity_changed", CheckSeverity.ERROR,
            "Provider, model, voice, language, or speed changed between chunks.",
            {"identities": [list(identity) for identity in sorted(identities)]},
        ))
    if len(formats) != 1:
        results.append(_fail(
            "voice.format_changed", CheckSeverity.ERROR,
            "Sample rate or channel layout changed between narration chunks.",
            {"formats": [list(value) for value in sorted(formats)]},
        ))
    for left, right in zip(segments, segments[1:], strict=False):
        gap = round(float(right.start_time) - float(left.end_time), 3)
        if gap < -0.01 or gap > 0.5:
            results.append(_fail(
                "voice.chunk_boundary_invalid", CheckSeverity.ERROR,
                "Narration chunks contain an excessive gap or overlap.",
                {"gap": gap, "left": left.id, "right": right.id},
            ))
            break
    return results or [_pass("voice.profile_locked", "One immutable voice profile covers every chunk.")]


def check_editorial_warnings(script: ScriptVersion | None) -> list[CheckResult]:
    if script is None:
        return []
    return [
        _fail(item.get("code", "editorial.validation"), CheckSeverity.ERROR, item.get("message", "Editorial validation failed."), item)
        for item in (script.warnings or [])
        if item.get("severity") == "error"
    ]


def check_panel_alignment(scenes: list) -> list[CheckResult]:
    """Generated scenes carry alignment evidence; legacy/manual rows stay compatible."""
    audited = [
        scene for scene in scenes
        if getattr(scene, "asset_id", None) and getattr(scene, "alignment_reasons", None)
        and "no_candidate" not in getattr(scene, "alignment_reasons", [])
    ]
    if not audited:
        return []
    weak = [scene for scene in audited if float(getattr(scene, "alignment_score", 0.0)) < 0.15]
    rejected = [scene for scene in audited if getattr(scene, "visual_signature", "") == "" and getattr(scene, "asset_id", None)]
    results: list[CheckResult] = []
    if weak:
        results.append(_fail(
            "panel.alignment_below_threshold", CheckSeverity.ERROR,
            f"{len(weak)} scene(s) have insufficient panel-to-narration alignment.",
            {"scene_ids": [scene.id for scene in weak[:20]]},
        ))
    if rejected:
        results.append(_fail(
            "panel.debug_metadata_missing", CheckSeverity.ERROR,
            "Selected panels lack perceptual metadata required for repetition and gutter QC.",
        ))
    return results or [_pass("panel.alignment_ok", f"{len(audited)} scene selections carry alignment evidence.")]


def _reference_panel_key(scene: object) -> str:
    return str(
        getattr(scene, "panel_region_id", "")
        or getattr(scene, "panel_id", "")
        or getattr(scene, "asset_id", "")
        or ""
    )


def _reference_reuse_checks(scenes: list, profile) -> list[CheckResult]:
    results: list[CheckResult] = []
    counts = Counter(
        _reference_panel_key(scene)
        for scene in scenes
        if _reference_panel_key(scene)
    )
    over = {
        asset_id: count
        for asset_id, count in counts.items()
        if count > profile.max_canonical_panel_uses
    }
    if over:
        results.append(_fail(
            "reference.panel_reuse_over_2",
            CheckSeverity.ERROR,
            "A reference panel is used more than twice.",
            {"asset_counts": dict(sorted(over.items()))},
        ))
    for left, right in zip(scenes, scenes[1:], strict=False):
        if _reference_panel_key(left) != _reference_panel_key(right):
            continue
        results.append(_fail(
            "reference.panel_reuse_consecutive",
            CheckSeverity.ERROR,
            "Reference panels may not be reused in consecutive shots.",
        ))
        if (
            getattr(left, "roi_label", "") == getattr(right, "roi_label", "")
            and abs(float(getattr(left, "focus_x", 0.0)) - float(getattr(right, "focus_x", 0.0))) < 0.001
            and abs(float(getattr(left, "focus_y", 0.0)) - float(getattr(right, "focus_y", 0.0))) < 0.001
        ):
            results.append(_fail(
                "reference.panel_reuse_same_roi",
                CheckSeverity.ERROR,
                "A repeated reference panel must use a distinct ROI.",
            ))
        break
    positions: dict[str, list[object]] = {}
    for scene in scenes:
        panel_key = _reference_panel_key(scene)
        if panel_key:
            positions.setdefault(panel_key, []).append(scene)
    for repeated in positions.values():
        if len(repeated) != 2:
            continue
        first, second = repeated
        if (
            getattr(first, "roi_label", "") == getattr(second, "roi_label", "")
            and abs(float(getattr(first, "focus_x", 0.0)) - float(getattr(second, "focus_x", 0.0))) < 0.001
            and abs(float(getattr(first, "focus_y", 0.0)) - float(getattr(second, "focus_y", 0.0))) < 0.001
        ):
            results.append(_fail(
                "reference.panel_reuse_same_roi",
                CheckSeverity.ERROR,
                "A repeated reference panel must use a distinct ROI.",
            ))
    unique: dict[str, CheckResult] = {}
    for result in results:
        unique[result.code] = result
    return list(unique.values())



def _scene_field(scene: object, key: str, default=None):
    if isinstance(scene, Mapping):
        return scene.get(key, default)
    return getattr(scene, key, default)


def _reference_mask_identity(mask: framing_analysis.BorderMaskResult) -> str | None:
    try:
        return framing_analysis._mask_hash(
            mask.source_width,
            mask.source_height,
            mask.grid_width,
            mask.grid_height,
            mask.edge_connected_mask,
            mask.non_discardable_low_information_mask,
            mask.protected_mask,
        )
    except (AttributeError, TypeError, ValueError):
        return None


def _reference_lineage_failure(message: str) -> CheckResult:
    return _fail(
        "visual.panel_lineage_unavailable",
        CheckSeverity.ERROR,
        message,
    )


def _telemetry_field(telemetry: object, key: str, default=None):
    if isinstance(telemetry, Mapping):
        return telemetry.get(key, default)
    return getattr(telemetry, key, default)


def _telemetry_float(telemetry: object, key: str, default: float) -> float | None:
    try:
        return float(_telemetry_field(telemetry, key, default))
    except (TypeError, ValueError):
        return None


_REFERENCE_FRACTION_FIELDS = (
    "balloon_mask_intersection_ratio",
    "subject_coverage",
    "face_coverage",
    "action_coverage",
    "effect_coverage",
    "continuity_context_coverage",
    "edge_connected_blank_fraction",
    "protected_retained_fraction",
    "non_discardable_low_information_fraction",
    "mask_confidence",
)


def _telemetry_fraction(telemetry: object, key: str, default: float) -> float | None:
    value = _telemetry_float(telemetry, key, default)
    if value is None or not math.isfinite(value) or not 0.0 <= value <= 1.0:
        return None
    return value


def _canonical_snapshot(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _canonical_snapshot_matches(left: object, right: object) -> bool:
    try:
        if is_dataclass(right):
            right = asdict(right)
        return _canonical_snapshot(left) == _canonical_snapshot(right)
    except (TypeError, ValueError):
        return False


def _reference_contract_failure(message: str) -> CheckResult:
    return _fail(
        "visual.framing_contract_incompatible",
        CheckSeverity.ERROR,
        message,
    )


def check_reference_framing(
    scenes: Sequence[Mapping[str, object] | object],
    panel_evidence_by_key: Mapping[tuple[str, str], visual_scoring.PanelVisualEvidence],
    panel_border_masks_by_key: Mapping[
        tuple[str, str], framing_analysis.BorderMaskResult
    ],
    panel_sizes_by_key: Mapping[tuple[str, str], tuple[int, int]],
    telemetry_by_key: Mapping[
        tuple[str, str], framing_analysis.FramingTelemetry | None
    ],
    *,
    profile: object,
    adaptive_reference: bool = False,
) -> list[CheckResult]:
    """Validate the exact panel snapshot and the scene's accepted telemetry."""

    lineage: list[
        tuple[
            object,
            tuple[str, str],
            visual_scoring.PanelVisualEvidence,
            framing_analysis.BorderMaskResult,
            tuple[int, int],
            Mapping[str, object],
            str | None,
        ]
    ] = []
    for scene in scenes:
        source_asset_id = str(
            _scene_field(scene, "source_asset_id", "")
            or _scene_field(scene, "asset_id", "")
        )
        panel_region_id = str(_scene_field(scene, "panel_region_id", "") or "")
        panel_id = str(_scene_field(scene, "panel_id", "") or "")
        key = (source_asset_id, panel_region_id)
        evidence = panel_evidence_by_key.get(key)
        mask = panel_border_masks_by_key.get(key)
        panel_size = panel_sizes_by_key.get(key)
        external_telemetry = telemetry_by_key.get(key)
        scene_telemetry = _scene_field(scene, "framing_telemetry")
        scene_border_mask = _scene_field(scene, "border_mask")
        scene_panel_size = _scene_field(scene, "panel_size")
        scene_visual_evidence = _scene_field(scene, "visual_evidence")
        fallback_attempts = _scene_field(scene, "fallback_attempts")
        required_telemetry_fields = (
            "contract_version",
            "detector_version",
            "mask_sha256",
            "crop_box",
            "balloon_mask_intersection_ratio",
            "subject_coverage",
            "face_coverage",
            "action_coverage",
            "effect_coverage",
            "continuity_context_coverage",
            "edge_connected_blank_fraction",
            "fallback_reason",
            "rejection_code",
        )
        if (
            not source_asset_id
            or not panel_region_id
            or not panel_id
            or evidence is None
            or mask is None
            or panel_size is None
            or not isinstance(scene_telemetry, Mapping)
            or any(field not in scene_telemetry for field in required_telemetry_fields)
            or not isinstance(scene_border_mask, Mapping)
            or not isinstance(scene_visual_evidence, Mapping)
            or not isinstance(fallback_attempts, list)
            or tuple(scene_panel_size or ()) != tuple(panel_size)
            or not _scene_field(scene, "evidence_hash")
            or not _scene_field(scene, "source_asset_checksum")
            or not _scene_field(scene, "source_order")
            or not _scene_field(scene, "panel_bounds")
        ):
            return [_reference_lineage_failure("reference scene snapshot is incomplete")]
        try:
            visual_scoring.validate_panel_visual_evidence(evidence)
            local_hash = visual_scoring.visual_evidence_hash(evidence)
            snapshot_evidence = visual_scoring.parse_panel_visual_evidence(
                scene_visual_evidence
            )
            visual_scoring.validate_panel_visual_evidence(snapshot_evidence)
            snapshot_hash = visual_scoring.visual_evidence_hash(snapshot_evidence)
        except (visual_scoring.VisualEvidenceError, TypeError, ValueError):
            return [_reference_lineage_failure("reference panel evidence is invalid")]
        if (
            snapshot_hash != local_hash
            or snapshot_evidence.panel_id != panel_id
            or snapshot_evidence.source_asset_id != source_asset_id
            or snapshot_evidence.source_order != _scene_field(scene, "source_order")
            or evidence.panel_id != panel_id
            or evidence.source_asset_id != source_asset_id
            or evidence.source_order != _scene_field(scene, "source_order")
            or _scene_field(scene, "evidence_hash") != local_hash
            or scene_border_mask.get("mask_sha256") != mask.mask_sha256
            or scene_border_mask.get("detector_version") != mask.detector_version
            or not _canonical_snapshot_matches(scene_border_mask, mask)
            or (mask.source_width, mask.source_height) != tuple(panel_size)
            or _reference_mask_identity(mask) != mask.mask_sha256
        ):
            return [_reference_lineage_failure("reference panel snapshot does not match evidence")]
        try:
            bounds = tuple(_scene_field(scene, "panel_bounds"))
            if (
                len(bounds) != 4
                or any(isinstance(value, bool) or not isinstance(value, int) for value in bounds)
                or bounds[0] < 0
                or bounds[1] < 0
                or (bounds[2] - bounds[0], bounds[3] - bounds[1]) != tuple(panel_size)
            ):
                return [_reference_lineage_failure("reference panel bounds are invalid")]
        except (TypeError, ValueError):
            return [_reference_lineage_failure("reference panel bounds are invalid")]
        if not framing_analysis.detector_contract_matches(
            profile.framing_contract_version, mask.detector_version
        ) or not framing_analysis.detector_contract_matches(
            evidence.contract_version, mask.detector_version
        ):
            return [_reference_contract_failure("reference framing contract is incompatible")]
        scene_contract = _telemetry_field(scene_telemetry, "contract_version")
        if scene_contract != evidence.contract_version:
            return [_reference_contract_failure("scene telemetry contract is incompatible")]
        if (
            _telemetry_field(scene_telemetry, "mask_sha256") != mask.mask_sha256
            or _telemetry_field(scene_telemetry, "detector_version") != mask.detector_version
        ):
            return [_reference_lineage_failure("scene telemetry mask identity is stale")]
        roi = _scene_field(scene, "roi")
        scene_crop = tuple(_telemetry_field(scene_telemetry, "crop_box", ()))
        if (
            not isinstance(roi, Mapping)
            or tuple(roi.get("crop_box", ())) != scene_crop
        ):
            return [_reference_lineage_failure("scene telemetry crop does not match ROI")]
        selected_roi = _telemetry_field(scene_telemetry, "selected_roi")
        if selected_roi is not None and (
            not isinstance(selected_roi, Mapping)
            or tuple(selected_roi.get("crop_box", ())) != scene_crop
        ):
            return [_reference_lineage_failure("selected telemetry ROI does not match crop")]
        if (
            _telemetry_field(scene_telemetry, "evidence_hash", local_hash) != local_hash
            or _telemetry_field(
                scene_telemetry, "source_asset_checksum", _scene_field(scene, "source_asset_checksum")
            )
            != _scene_field(scene, "source_asset_checksum")
            or _telemetry_field(scene_telemetry, "source_order", _scene_field(scene, "source_order"))
            != _scene_field(scene, "source_order")
            or tuple(
                _telemetry_field(scene_telemetry, "panel_size", tuple(panel_size))
            )
            != tuple(panel_size)
        ):
            return [_reference_lineage_failure("scene telemetry lineage is stale")]
        if external_telemetry is not None and (
            _telemetry_field(external_telemetry, "mask_sha256") != mask.mask_sha256
            or _telemetry_field(external_telemetry, "detector_version") != mask.detector_version
            or _telemetry_field(external_telemetry, "contract_version")
            != evidence.contract_version
        ):
            return [_reference_lineage_failure("external telemetry identity is stale")]
        if any(
            key in scene_telemetry
            and _telemetry_fraction(scene_telemetry, key, 0.0) is None
            for key in _REFERENCE_FRACTION_FIELDS
        ):
            return [_reference_lineage_failure("reference telemetry contains invalid fractions")]
        if any(
            not isinstance(entry, Mapping)
            or entry.get("attempt_order") != index
            for index, entry in enumerate(fallback_attempts)
        ):
            return [_reference_lineage_failure("reference fallback ledger order is invalid")]
        accepted_entries = [
            entry
            for entry in fallback_attempts
            if isinstance(entry, Mapping) and entry.get("accepted") is True
        ]
        selected_roi_kind = (
            selected_roi.get("kind") if isinstance(selected_roi, Mapping) else None
        )
        selected_roi_label = (
            selected_roi.get("roi_label") if isinstance(selected_roi, Mapping) else None
        )
        scene_roi_label = roi.get("roi_label") if isinstance(roi, Mapping) else None
        accepted_entry = accepted_entries[0] if len(accepted_entries) == 1 else None
        expected_attempt_identity = (
            accepted_entry is not None
            and isinstance(roi, Mapping)
            and isinstance(selected_roi, Mapping)
            and tuple(accepted_entry.get("crop_box", ())) == scene_crop
            and accepted_entry.get("roi_label") == scene_roi_label == selected_roi_label
            and accepted_entry.get("roi_kind") == selected_roi_kind
            and accepted_entry.get("kind") in {selected_roi_kind, "alternate_panel"}
            and accepted_entry.get("panel_region_id") == panel_region_id
            and accepted_entry.get("panel_id") == panel_id
            and accepted_entry.get("source_asset_id") == source_asset_id
            and accepted_entry.get("source_order") == _scene_field(scene, "source_order")
            and accepted_entry.get("source_asset_checksum")
            == _scene_field(scene, "source_asset_checksum")
            and accepted_entry.get("evidence_hash") == local_hash
            and accepted_entry.get("detector_version") == mask.detector_version
            and accepted_entry.get("mask_sha256") == mask.mask_sha256
            and tuple(accepted_entry.get("panel_size", ())) == tuple(panel_size)
            and isinstance(accepted_entry.get("telemetry"), Mapping)
            and _canonical_snapshot_matches(
                accepted_entry.get("telemetry"), scene_telemetry
            )
        )
        if not expected_attempt_identity:
            return [_reference_lineage_failure("reference fallback ledger does not match accepted scene snapshot")]
        conservative_full_panel_ready = bool(
            adaptive_reference
            and visual_scoring.is_conservative_full_panel_visual_evidence(evidence)
            and isinstance(selected_roi, Mapping)
            and scene_crop == (0, 0, int(panel_size[0]), int(panel_size[1]))
        )
        readiness_code: str | None = None
        try:
            visual_scoring.require_reference_ready_visual_evidence(
                evidence,
                allow_conservative_full_panel=conservative_full_panel_ready,
            )
        except visual_scoring.VisualEvidenceError as exc:
            if exc.code == "visual.balloon_mask_unknown":
                readiness_code = exc.code
            else:
                return [
                    _reference_lineage_failure(
                        "reference panel evidence is not reference-ready"
                    )
                ]
        lineage.append(
            (scene, key, evidence, mask, panel_size, scene_telemetry, readiness_code)
        )

    results: list[CheckResult] = []
    for _scene, _key, _evidence, _mask, _panel_size, telemetry, readiness_code in lineage:
        if readiness_code == "visual.balloon_mask_unknown":
            results.append(
                _fail(
                    "visual.balloon_mask_unknown",
                    CheckSeverity.ERROR,
                    "Reference QC requires known balloon geometry.",
                )
            )
            continue
        balloon = _telemetry_fraction(telemetry, "balloon_mask_intersection_ratio", 1.0)
        subject = _telemetry_fraction(telemetry, "subject_coverage", 0.0)
        face = _telemetry_fraction(telemetry, "face_coverage", 0.0)
        action = _telemetry_fraction(telemetry, "action_coverage", 0.0)
        continuity = _telemetry_fraction(telemetry, "continuity_context_coverage", 0.0)
        effect = _telemetry_fraction(telemetry, "effect_coverage", 0.0)
        blank = _telemetry_fraction(telemetry, "edge_connected_blank_fraction", 1.0)
        if any(value is None for value in (balloon, subject, face, action, continuity, effect, blank)):
            results.append(_reference_lineage_failure("reference telemetry contains invalid numeric data"))
        elif balloon > 0.0:
            results.append(
                _fail(
                    "visual.balloon_mask_overlap",
                    CheckSeverity.ERROR,
                    "Reference crop intersects a speech-balloon mask.",
                )
            )
        elif adaptive_reference and (
            not isinstance(_telemetry_field(telemetry, "editorial_crop_quality"), Mapping)
            or int(_telemetry_field(telemetry, "editorial_crop_quality").get("face_cutoff_count", 0) or 0) > 0
            or int(_telemetry_field(telemetry, "editorial_crop_quality").get("face_margin_violation_count", 0) or 0) > 0
            or bool(_telemetry_field(telemetry, "editorial_crop_quality").get("face_omission", False))
            or bool(_telemetry_field(telemetry, "editorial_crop_quality").get("unjustified_detail_crop", False))
        ):
            results.append(_fail(
                "visual.editorial_composition_invalid",
                CheckSeverity.ERROR,
                "Adaptive reference crop violates the approved editorial composition contract.",
            ))
        elif not adaptive_reference and (
            subject < 0.98
            or face < 0.98
            or action < 0.95
            or continuity < 0.95
            or effect < 0.90
        ):
            results.append(
                _fail(
                    "visual.protected_coverage",
                    CheckSeverity.ERROR,
                    "Reference crop does not retain required protected visual regions.",
                )
            )
        elif _telemetry_field(telemetry, "rejection_code"):
            code = str(_telemetry_field(telemetry, "rejection_code"))
            if code == "visual.source_resolution_insufficient":
                code = "visual.visual_unavailable"
            results.append(
                _fail(
                    code,
                    CheckSeverity.ERROR,
                    "Reference crop telemetry contains a hard rejection.",
                )
            )
        elif blank > (
            reference_profile.review_frame_edge_blank_threshold(dict(telemetry))
            if adaptive_reference
            else float(profile.framing_blank_target_fraction)
        ):
            results.append(
                _fail(
                    "visual.blank_infeasible",
                    CheckSeverity.ERROR,
                    "Reference crop retains edge-connected blank area.",
                )
            )
    if not results:
        return [_pass("visual.reference_framing", "Exact panel framing evidence is valid.")]
    unique: dict[str, CheckResult] = {}
    for result in results:
        unique[result.code] = result
    return list(unique.values())


def check_adaptive_reference_profile(
    scenes: list, duration: float, contract: Mapping[str, object]
) -> list[CheckResult]:
    """Validate an explicitly approved short-form adaptive reference timeline."""
    results: list[CheckResult] = []
    try:
        lower = float(contract["target_duration_min_s"])
        upper = float(contract["target_duration_max_s"])
    except (KeyError, TypeError, ValueError):
        return [_fail("reference.adaptive_contract_invalid", CheckSeverity.ERROR, "Adaptive reference duration contract is invalid.")]
    if not lower <= duration <= upper:
        results.append(_fail("reference.adaptive_duration_outside_contract", CheckSeverity.ERROR, f"Adaptive reference output must be between {lower:.2f} and {upper:.2f} seconds."))
    durations = [max(0.0, float(scene.end_time) - float(scene.start_time)) for scene in scenes]
    minimum_required = max(1, math.ceil(max(0.0, duration) / reference_profile.REVIEW_MAX_SHOT_SECONDS))
    if len(scenes) < minimum_required:
        results.append(_fail("reference.adaptive_shot_density_low", CheckSeverity.ERROR, "Adaptive reference output does not meet the four-second visual cadence ceiling."))
    if any(value <= 0.50 or value > reference_profile.REVIEW_MAX_SHOT_SECONDS + 1e-9 for value in durations):
        results.append(_fail("reference.adaptive_shot_duration_invalid", CheckSeverity.ERROR, "Adaptive reference shots must remain above 0.5 seconds and at or below four seconds."))
    keys = [
        str(getattr(scene, "panel_region_id", "") or getattr(scene, "panel_id", "") or getattr(scene, "asset_id", ""))
        for scene in scenes
    ]
    if any(key and key in keys[max(0, index - reference_profile.REVIEW_PANEL_REUSE_WINDOW_SHOTS):index] for index, key in enumerate(keys)):
        results.append(_fail("reference.adaptive_panel_repeat", CheckSeverity.ERROR, "Adaptive reference output repeats a panel inside the review reuse window."))
    if len(scenes) > 1 and any(getattr(scene, "transition", "cut") != "fade" for scene in scenes[1:]):
        results.append(_fail("reference.adaptive_transition_policy", CheckSeverity.ERROR, "Adaptive reference shot boundaries must use the approved fade transition policy."))
    return results or [_pass("reference.adaptive_profile", "Approved adaptive reference pacing contract is valid.")]


def check_standard_reference_profile(
    scenes: list, duration: float, profile
) -> list[CheckResult]:
    """Validate the long-hold, evidence-unique final-production cadence."""
    results: list[CheckResult] = []
    if not profile.duration_min_s <= duration <= profile.duration_max_s:
        results.append(_fail(
            "reference.standard_duration_outside_50_60s",
            CheckSeverity.ERROR,
            "Standard reference production must remain between 50 and 60 seconds.",
        ))
    durations = [
        max(0.0, float(scene.end_time) - float(scene.start_time))
        for scene in scenes
    ]
    minimum_required = max(
        1, math.ceil(max(0.0, duration) / reference_profile.REVIEW_MAX_SHOT_SECONDS)
    )
    if len(scenes) < minimum_required:
        results.append(_fail(
            "reference.standard_shot_density_low",
            CheckSeverity.ERROR,
            "Standard reference production does not meet the four-second visual cadence ceiling.",
        ))
    if any(
        value <= 0.50 or value > reference_profile.REVIEW_MAX_SHOT_SECONDS + 1e-9
        for value in durations
    ):
        results.append(_fail(
            "reference.standard_shot_duration_invalid",
            CheckSeverity.ERROR,
            "Standard reference shots must remain above 0.5 seconds and at or below four seconds.",
        ))
    positions: dict[str, list[object]] = {}
    for scene in scenes:
        key = _reference_panel_key(scene)
        if key:
            positions.setdefault(key, []).append(scene)
    for left, right in zip(scenes, scenes[1:], strict=False):
        left_key = _reference_panel_key(left)
        if left_key and left_key == _reference_panel_key(right):
            results.append(_fail(
                "reference.standard_panel_reuse_consecutive",
                CheckSeverity.ERROR,
                "Standard reference production may not reuse the same panel in consecutive shots.",
            ))
            break
    invalid_repeats: list[str] = []
    for key, repeated_scenes in positions.items():
        if len(repeated_scenes) > int(profile.max_canonical_panel_uses):
            invalid_repeats.append(key)
            continue
        seen_rois: set[tuple[object, ...]] = set()
        for scene in repeated_scenes:
            roi_key = (
                str(getattr(scene, "roi_label", "") or ""),
                round(float(getattr(scene, "focus_x", 0.0)), 3),
                round(float(getattr(scene, "focus_y", 0.0)), 3),
                round(float(getattr(scene, "focus_end_x", 0.0)), 3),
                round(float(getattr(scene, "focus_end_y", 0.0)), 3),
            )
            if roi_key in seen_rois:
                invalid_repeats.append(key)
                break
            seen_rois.add(roi_key)
    if invalid_repeats:
        results.append(_fail(
            "reference.standard_panel_repeat",
            CheckSeverity.ERROR,
            "Standard reference panel reuse must stay within the profile cap and use distinct safe ROIs.",
            {"panel_keys": sorted(set(invalid_repeats))[:20]},
        ))
    if len(scenes) > 1 and any(
        getattr(scene, "transition", "cut") != "fade" for scene in scenes[1:]
    ):
        results.append(_fail(
            "reference.standard_transition_policy",
            CheckSeverity.ERROR,
            "Standard reference shot boundaries must use the approved fade transition policy.",
        ))
    stable_modes = {"slow_push", "slow_pull", "guided_pan", "focus_shift"}
    modes = [str(getattr(scene, "motion_mode", "") or "") for scene in scenes]
    if any(mode not in stable_modes for mode in modes):
        results.append(_fail(
            "reference.standard_motion_path_invalid",
            CheckSeverity.ERROR,
            "Standard reference motion must use a stable living-frame path.",
        ))
    if len(scenes) >= 8:
        counts = Counter(modes)
        if len(counts) < reference_profile.REVIEW_MOTION_MIN_MODE_DIVERSITY:
            results.append(_fail(
                "reference.standard_motion_diversity_low",
                CheckSeverity.ERROR,
                "Standard reference motion is too repetitive to read as intentional animation.",
            ))
        if max(counts.values(), default=0) / max(1, len(modes)) > reference_profile.REVIEW_MOTION_MAX_DOMINANT_MODE_RATIO + 1e-9:
            results.append(_fail(
                "reference.standard_motion_dominant",
                CheckSeverity.ERROR,
                "One motion mode dominates the standard reference sequence.",
            ))
    imperceptible = []
    for index, scene in enumerate(scenes):
        mode = str(getattr(scene, "motion_mode", "") or "")
        focus_travel = math.hypot(
            float(getattr(scene, "focus_end_x", 0.5)) - float(getattr(scene, "focus_x", 0.5)),
            float(getattr(scene, "focus_end_y", 0.5)) - float(getattr(scene, "focus_y", 0.5)),
        )
        zoom_ok = (
            mode in {"slow_push", "slow_pull"}
            and reference_profile.REVIEW_MOTION_ZOOM_DELTA >= reference_profile.REVIEW_MOTION_MIN_ZOOM_DELTA - 1e-9
        )
        focus_ok = focus_travel >= reference_profile.REVIEW_MOTION_MIN_FOCUS_TRAVEL - 1e-9
        if not (zoom_ok or focus_ok):
            imperceptible.append(index)
    if imperceptible:
        results.append(_fail(
            "reference.standard_motion_imperceptible",
            CheckSeverity.ERROR,
            "Standard reference motion travel is too small to be visible at normal playback speed.",
            {"scene_indexes": imperceptible[:20]},
        ))
    return results or [_pass(
        "reference.standard_profile",
        "Standard evidence-unique reference pacing contract is valid.",
    )]


def check_reference_profile(scenes: list, duration: float, profile) -> list[CheckResult]:
    """Apply the empirical reference duration, cadence, and cut gates."""
    results: list[CheckResult] = []
    if not profile.duration_min_s <= duration <= profile.duration_max_s:
        results.append(_fail(
            "reference.duration_outside_50_60s",
            CheckSeverity.ERROR,
            "Reference output duration must be between 50 and 60 seconds.",
        ))
    if not profile.shot_min <= len(scenes) <= profile.shot_max:
        results.append(_fail(
            "reference.shot_count_outside_36_52",
            CheckSeverity.ERROR,
            "Reference output must contain 36 to 52 shots.",
        ))
    durations = [
        max(0.0, float(scene.end_time) - float(scene.start_time))
        for scene in scenes
    ]
    normal = [
        value for value in durations
        if profile.hold_min_s <= value <= profile.hold_max_s
    ]
    emphasis = [
        value for value in durations
        if profile.emphasis_min_s <= value <= profile.emphasis_max_s
    ]
    if len(normal) + len(emphasis) != len(durations):
        results.append(_fail(
            "reference.shot_duration_outside_0.65_2.20s",
            CheckSeverity.ERROR,
            "Every reference shot must be in the normal or emphasis duration band.",
        ))
    if durations and len(normal) / len(durations) < profile.hold_ratio_min:
        results.append(_fail(
            "reference.hold_ratio_below_70pct",
            CheckSeverity.ERROR,
            "At least 70 percent of reference shots must use the normal hold band.",
        ))
    if durations and len(normal) / len(durations) > profile.hold_ratio_max:
        results.append(_fail(
            "reference.hold_ratio_over_80pct",
            CheckSeverity.ERROR,
            "At most 80 percent of reference shots may use the normal hold band.",
        ))
    if durations and len(emphasis) / len(durations) < profile.emphasis_ratio_min:
        results.append(_fail(
            "reference.emphasis_ratio_below_20pct",
            CheckSeverity.ERROR,
            "At least 20 percent of reference shots must use the emphasis band.",
        ))
    if durations and len(emphasis) / len(durations) > profile.emphasis_ratio_max:
        results.append(_fail(
            "reference.emphasis_ratio_over_30pct",
            CheckSeverity.ERROR,
            "At most 30 percent of reference shots may use the emphasis band.",
        ))
    if durations:
        mean = sum(durations) / len(durations)
        if not profile.mean_shot_min_s <= mean <= profile.mean_shot_max_s:
            results.append(_fail(
                "reference.mean_shot_duration_outside_1.15_1.40s",
                CheckSeverity.ERROR,
                "Reference mean shot duration must be between 1.15 and 1.40 seconds.",
            ))
        hard_cuts = sum(
            1
            for index, scene in enumerate(scenes)
            if getattr(scene, "transition", "") == ("none" if index == 0 else "cut")
        )
        if hard_cuts / len(scenes) < profile.hard_cut_ratio_min:
            results.append(_fail(
                "reference.hard_cut_ratio_below_85pct",
                CheckSeverity.ERROR,
                "At least 85 percent of reference transitions must be hard cuts.",
            ))
    return results


def check_repetition_and_motion(scenes: list, profile=None) -> list[CheckResult]:
    """Block dominant backgrounds, A-B-A-B loops, and unexplained static runs."""
    if not scenes:
        return []
    durations = [
        round(max(0.0, float(scene.end_time) - float(scene.start_time)), 3)
        for scene in scenes
    ]
    total = sum(durations)
    signatures = [getattr(scene, "visual_signature", "") or getattr(scene, "asset_id", "") for scene in scenes]
    results: list[CheckResult] = []
    for issue in motion_director.audit_camera_sequence(scenes):
        results.append(_fail(
            issue,
            CheckSeverity.ERROR,
            "Camera motion violates the stable monotonic motion contract.",
        ))
    # Exact-panel timelines can legitimately draw several distinct panel regions
    # from one source page/image. Treat the canonical panel identity as the
    # reusable visual unit whenever it exists; legacy/non-reference scenes still
    # fall back to the source asset identity. The reference-specific checks
    # below impose their stricter panel reuse/ROI contract on top of this.
    visual_identity_counts = Counter(
        (
            str(getattr(scene, "panel_region_id", "") or getattr(scene, "panel_id", ""))
            or str(getattr(scene, "asset_id", "") or "")
        )
        for scene in scenes
        if (
            getattr(scene, "panel_region_id", "")
            or getattr(scene, "panel_id", "")
            or getattr(scene, "asset_id", "")
        )
    )
    if visual_identity_counts:
        reuse_cap = visual_scoring.asset_use_cap(len(scenes))
        over_cap = {
            identity: count
            for identity, count in visual_identity_counts.items()
            if count > reuse_cap
        }
        if len(visual_identity_counts) > 1 and over_cap:
            results.append(_fail(
                "visual.asset_reuse_cap", CheckSeverity.ERROR,
                f"Visual reuse exceeds the {reuse_cap}-shot cap for {len(over_cap)} visual(s).",
                {
                    "cap": reuse_cap,
                    "shot_count": len(scenes),
                    "visual_identity_counts": dict(sorted(over_cap.items())),
                },
            ))
    if total > 0:
        dominance: dict[str, float] = {}
        for signature, duration in zip(signatures, durations, strict=True):
            if signature:
                dominance[signature] = dominance.get(signature, 0.0) + duration
        dominant = max(dominance.values(), default=0.0) / total
        if dominant > 0.35 and len(dominance) > 1:
            results.append(_fail(
                "visual.dominant_background_over_35pct", CheckSeverity.ERROR,
                f"One perceptual background occupies {dominant:.0%} of the timeline.",
                {"dominant_ratio": round(dominant, 3)},
            ))
    for index in range(len(signatures) - 3):
        window = signatures[index:index + 4]
        if window[0] and window[0] == window[2] and window[1] and window[1] == window[3] and window[0] != window[1]:
            results.append(_fail(
                "visual.alternating_pattern", CheckSeverity.ERROR,
                "A-B-A-B panel repetition has no documented editorial reason.",
                {"start_scene": index, "signatures": window},
            ))
            break
    static_duration = sum(duration for scene, duration in zip(scenes, durations, strict=True) if getattr(scene, "motion_mode", "hold") in {"hold", "static_emphasis"})
    if total and static_duration / total > 0.55 and len(set(signatures)) > 1:
        results.append(_fail(
            "visual.static_ratio_over_55pct", CheckSeverity.ERROR,
            f"Static/intentional-hold scenes occupy {static_duration / total:.0%} of the timeline.",
            {"static_ratio": round(static_duration / total, 3)},
        ))
    if profile is not None:
        results.extend(_reference_reuse_checks(scenes, profile))
    return results or [_pass("visual.repetition_ok", "No dominant-background or A-B-A-B repetition detected.")]


def check_render_qc_artifact(job: RenderJob | None) -> list[CheckResult]:
    """The post-render editorial report is a real publish gate, not decoration."""
    if job is None or not job.output_key:
        return []
    report_path = Path(job.output_key).parent / "final.qc.json"
    if not report_path.is_file():
        return [_fail("qc.report_missing", CheckSeverity.ERROR, "Final QC report is missing; publication is blocked.")]
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [_fail("qc.report_unreadable", CheckSeverity.ERROR, f"Final QC report cannot be read: {exc}")]
    failures = list(report.get("failures", []))
    if failures or report.get("publish_allowed") is not True:
        reason = ", ".join(failures) or "publish_allowed=false"
        return [_fail("qc.editorial_hard_gate", CheckSeverity.ERROR, f"Editorial QC report blocks publication: {reason}.", {"failures": failures})]
    return [_pass("qc.editorial_hard_gate", "Editorial QC report allows publication.")]


def check_audio(segments: list) -> list[CheckResult]:
    """Voice-over must exist and be audible."""
    from app.services import storage

    if not segments:
        return [
            _fail(
                "audio.missing",
                CheckSeverity.ERROR,
                "No voice-over has been generated.",
            )
        ]
    missing = [s for s in segments if not storage.exists(s.storage_key)]
    if missing:
        return [
            _fail(
                "audio.files_missing",
                CheckSeverity.ERROR,
                f"{len(missing)} audio segment file(s) are missing from storage. Regenerate them.",
            )
        ]
    silent = [s for s in segments if s.duration <= 0.2]
    results = [_pass("audio.present", f"{len(segments)} segments present.")]
    if silent:
        results.append(
            _fail(
                "audio.silent_segment",
                CheckSeverity.WARNING,
                f"{len(silent)} segment(s) are shorter than 0.2s and may be silent.",
            )
        )
    return results


def check_scenes(scenes: list, assets: list[SourceAsset]) -> list[CheckResult]:
    """Every scene needs a usable visual."""
    if not scenes:
        return [
            _fail(
                "timeline.no_scenes",
                CheckSeverity.ERROR,
                "The timeline has no scenes. Generate the timeline first.",
            )
        ]
    results: list[CheckResult] = []
    asset_ids = {a.id for a in assets}
    empty = [s for s in scenes if not s.asset_id or s.asset_id not in asset_ids]
    if empty:
        results.append(
            _fail(
                "timeline.empty_scenes",
                CheckSeverity.WARNING,
                f"{len(empty)} scene(s) have no image and will render as a blank frame.",
                {"scene_ids": [s.id for s in empty][:20]},
            )
        )
    zero = [s for s in scenes if s.duration <= 0.05]
    if zero:
        results.append(
            _fail(
                "timeline.zero_length_scene",
                CheckSeverity.ERROR,
                f"{len(zero)} scene(s) have zero duration.",
            )
        )
    if not results:
        results.append(_pass("timeline.ok", f"{len(scenes)} scenes."))
    return results


def check_subtitles(cues: list[CueSpec]) -> list[CheckResult]:
    if not cues:
        return [
            _fail(
                "subtitle.missing",
                CheckSeverity.WARNING,
                "No subtitles were generated. Shorts perform better with captions.",
            )
        ]
    results: list[CheckResult] = []
    for warning in validate_cues(cues, MAX_SUBTITLE_CHARS_PER_LINE, MAX_SUBTITLE_LINES):
        results.append(
            _fail(warning["code"], warning["severity"], warning["message"])
        )
    if not results:
        results.append(_pass("subtitle.ok", f"{len(cues)} cues within safe area."))
    return results


def check_sentence_karaoke(
    groups: Sequence[object] | None,
    *,
    duration: float,
    contract: Mapping[str, object] | None = None,
    timing_error: str | None = None,
) -> list[CheckResult]:
    """Validate the explicit regular-render sentence karaoke contract."""
    if timing_error:
        code = str(timing_error).split(":", 1)[0] or "subtitle.word_timing_invalid"
        return [_fail(code, CheckSeverity.ERROR, str(timing_error))]
    if contract and contract.get("contract_version") != subtitle_karaoke.SUBTITLE_CONTRACT_VERSION:
        return [_fail(
            "subtitle.contract_invalid",
            CheckSeverity.ERROR,
            "Regular render subtitle contract version is unsupported.",
        )]
    failures = subtitle_karaoke.validate_sentence_groups(groups or (), duration=duration)
    if failures:
        return [
            _fail(code, CheckSeverity.ERROR, f"Regular sentence karaoke contract failed: {code}.")
            for code in failures
        ]
    return [_pass("subtitle.sentence_karaoke", "Sentence-held word karaoke contract is valid.")]


def check_narration_language(script: ScriptVersion | None, language: str) -> list[CheckResult]:
    """Fail mixed English/Indonesian narration at the publication boundary."""
    if script is None:
        return []
    finding = editorial_timing.language_consistency(script.plain_text, language)
    if finding["passed"]:
        return [_pass("narration.language_consistent", f"Narration language: {language}.")]
    return [_fail(
        "narration.unintended_code_switch",
        CheckSeverity.ERROR,
        f"Narration marked {language} contains words from the other supported language.",
        finding,
    )]


def check_narrative_naturalness(report: object) -> list[CheckResult]:
    """Convert safe narrative screening codes to the shared QC result type."""

    warnings = tuple(getattr(report, "warnings", ()) or ())
    details = {
        "total_words": int(getattr(report, "total_words", 0)),
        "sentence_length_variance": float(
            getattr(report, "sentence_length_variance", 0.0)
        ),
        "claim_evidence_coverage_ratio": float(
            getattr(report, "claim_evidence_coverage_ratio", 0.0)
        ),
        "qualified_interpretation_coverage_ratio": float(
            getattr(report, "qualified_interpretation_coverage_ratio", 0.0)
        ),
        "visual_description_ratio": float(
            getattr(report, "visual_description_ratio", 0.0)
        ),
        "mechanical_opening_ratio": float(
            getattr(report, "mechanical_opening_ratio", 0.0)
        ),
    }
    results: list[CheckResult] = []
    blocking = {
        "narrative.evidence_missing": "Narrative passages are missing grounded panel evidence.",
        "narrative.interpretation_unqualified": "Interpretive narrative claims require a qualification.",
        "narrative.unsupported_claim": "Narrative passages reference an unsupported claim.",
        "narrative.balloon_dialogue_copied": "Narrative text copies speech-balloon dialogue.",
        "narrative.cta": "Narrative text contains channel call-to-action language.",
        "narrative.generic_hype": "Narrative text contains generic hype language.",
        "narrative.ai_slop": "Narrative text contains generic AI-style filler or empty intensity.",
        "narrative.visual_recap_prose": "Narrative prose is describing panels instead of telling the grounded story.",
        "narrative.ending_invalid": "Narrative ending does not match its ending kind.",
        "narrative.display_derivation_invalid": "Narrative display derivation is invalid.",
    }
    warning_messages = {
        "narrative.template_risk": "Narrative structure shows repeated template openings or sentences.",
        "narrative.rhythm_warning": "Narrative sentence rhythm is unusually uniform.",
        "narrative.mechanical_sequence": "Narrative passages rely too heavily on mechanical sequence openings.",
    }
    for code in warnings:
        if code in blocking:
            detail = dict(details)
            if code == "narrative.generic_hype":
                detail["markers"] = list(getattr(report, "generic_hype_hits", ()))
            if code == "narrative.cta":
                detail["markers"] = list(getattr(report, "cta_hits", ()))
            if code == "narrative.ai_slop":
                detail["markers"] = list(getattr(report, "ai_slop_hits", ()))
            if code == "narrative.visual_recap_prose":
                detail["markers"] = list(getattr(report, "reporter_prose_hits", ()))
            results.append(_fail(code, CheckSeverity.ERROR, blocking[code], detail))
        elif code in warning_messages:
            results.append(
                _fail(code, CheckSeverity.WARNING, warning_messages[code], details)
            )
    if not results:
        results.append(_pass("narrative.naturalness_ok", "Narrative screening passed."))
    return results


def check_duration(duration: float, target: float) -> list[CheckResult]:
    """Shorts must stay within the platform ceiling."""
    results: list[CheckResult] = []
    if duration <= 0:
        return [
            _fail(
                "duration.unknown",
                CheckSeverity.ERROR,
                "Could not determine the video duration.",
            )
        ]
    if duration > settings.max_short_seconds:
        results.append(
            _fail(
                "duration.too_long",
                CheckSeverity.ERROR,
                f"Video is {duration:.1f}s, over the {settings.max_short_seconds}s "
                "Shorts limit. Trim the script or scenes.",
                {"duration": duration},
            )
        )
    elif duration > target * 1.15:
        results.append(
            _fail(
                "duration.over_target",
                CheckSeverity.WARNING,
                f"Video is {duration:.1f}s versus a {target:.0f}s target.",
            )
        )
    if duration < 60:
        results.append(
            _fail(
                "duration.too_short",
                CheckSeverity.WARNING,
                f"Video is only {duration:.1f}s; editorial target is 60–90s.",
            )
        )
    if not results:
        results.append(_pass("duration.ok", f"{duration:.1f}s"))
    return results


def check_output(job: RenderJob | None) -> list[CheckResult]:
    """Verify the rendered artifact exists and has the right shape."""
    from app.services import storage

    if job is None or not job.output_key:
        return [
            _fail(
                "render.missing",
                CheckSeverity.ERROR,
                "No rendered video is available. Run a final render first.",
            )
        ]
    path = Path(job.output_key)
    if not path.is_absolute():
        exists = storage.exists(job.output_key)
        path = storage.path_for(job.output_key) if exists else path
    if not path.is_file():
        return [
            _fail(
                "render.file_missing",
                CheckSeverity.ERROR,
                "The rendered file is missing from disk. Re-render before publishing.",
            )
        ]

    results: list[CheckResult] = []
    expected_ratio = settings.video_width / settings.video_height
    if job.width and job.height:
        ratio = job.width / job.height
        if abs(ratio - expected_ratio) > 0.02:
            results.append(
                _fail(
                    "render.wrong_aspect",
                    CheckSeverity.ERROR,
                    f"Video is {job.width}x{job.height} ({ratio:.3f}); "
                    f"Shorts needs 9:16 ({expected_ratio:.3f}).",
                )
            )
        if job.height < 1280:
            results.append(
                _fail(
                    "render.low_resolution",
                    CheckSeverity.WARNING,
                    f"Height is {job.height}px. 1920px is recommended for Shorts.",
                )
            )
    if not results:
        results.append(_pass("render.ok", f"{job.width}x{job.height}, {job.duration:.1f}s"))
    return results


def run_all(
    project: Project,
    assets: list[SourceAsset],
    script: ScriptVersion | None,
    audio_segments: list,
    scenes: list,
    cues: list[CueSpec],
    job: RenderJob | None = None,
    duration: float | None = None,
    caption_groups: Sequence[object] | None = None,
    subtitle_contract: Mapping[str, object] | None = None,
    subtitle_timing_error: str | None = None,
    adaptive_reference_contract: Mapping[str, object] | None = None,
    standard_reference_cadence: bool = False,
) -> list[CheckResult]:
    """Full pre-publication sweep, combining policy and technical checks."""
    results: list[CheckResult] = []
    profile = reference_profile.resolve_reference_profile(
        getattr(project, "template", None)
    )

    # Policy gates first: rights problems should be the loudest signal.
    script_text = script.plain_text if script else ""
    sections = script.sections if script else []
    for finding in policy.evaluate_project(project, assets, script_text, sections):
        results.append(
            CheckResult(
                code=finding.code,
                severity=finding.severity,
                message=finding.message,
                passed=False,
                detail=finding.detail,
            )
        )

    results += check_script_approved(script)
    results += check_editorial_warnings(script)
    results += check_voice_profile(audio_segments)
    results += check_narration_language(script, project.language)
    results += check_audio(audio_segments)
    results += check_scenes(scenes, assets)
    results += check_panel_alignment(scenes)
    results += check_repetition_and_motion(
        scenes,
        profile=None
        if adaptive_reference_contract is not None or standard_reference_cadence
        else profile,
    )
    sentence_karaoke_active = profile is not None and (
        caption_groups is not None or subtitle_timing_error is not None
    )
    if not sentence_karaoke_active:
        results += check_subtitles(cues)

    effective_duration = duration if duration is not None else (job.duration if job else 0.0)
    if effective_duration or job:
        if profile is not None:
            if adaptive_reference_contract is not None:
                results += check_adaptive_reference_profile(
                    scenes, effective_duration, adaptive_reference_contract
                )
            elif standard_reference_cadence:
                results += check_standard_reference_profile(
                    scenes, effective_duration, profile
                )
            else:
                results += check_reference_profile(scenes, effective_duration, profile)
            if caption_groups is not None or subtitle_timing_error is not None:
                results += check_sentence_karaoke(
                    caption_groups,
                    duration=effective_duration,
                    contract=subtitle_contract,
                    timing_error=subtitle_timing_error,
                )
        else:
            results += check_duration(effective_duration, float(project.target_duration))
    if job:
        results += check_output(job)
        results += check_render_qc_artifact(job)

    return results


def summarise(results: list[CheckResult]) -> dict:
    errors = [r for r in results if r.blocking]
    warnings = [r for r in results if not r.passed and r.severity == CheckSeverity.WARNING]
    return {
        "total": len(results),
        "errors": len(errors),
        "warnings": len(warnings),
        "can_publish": not errors,
        "error_codes": [r.code for r in errors],
        "warning_codes": [r.code for r in warnings],
    }
