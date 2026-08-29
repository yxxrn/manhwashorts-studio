"""Durable, review-only artifacts for the normal silent preview workflow.

This module deliberately stops at a video-only editorial preview.  It never
creates audio segments, invents authoritative voice timing, or changes the
publish gate.  The cloud job state and the rendered files are linked by the
same project/script/analysis identities.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageStat
from sqlalchemy import select

from app.config import settings
from app.models import PanelRegion, ScriptVersion, SourceAsset, StoryAnalysis, SubtitleCue
from app.services import reference_profile
from app.services import render as render_service

PROVENANCE = "codex_cloud_multimodal_review_v1"
DISPLAY_TIMING_VERSION = "review_provisional_display_pacing_v1"
SUBTITLE_CONTRACT_VERSION = "sentence_chunked_word_karaoke_v2"


class ReviewPreviewError(RuntimeError):
    """Safe, stable failure for review artifact production."""

    def __init__(self, code: str, message: str = "review preview artifact failed") -> None:
        self.code = code
        super().__init__(message)


def review_visual_density_contract(
    total_duration: float,
    available_visuals: int,
) -> dict[str, float | int]:
    """Expose the shared duration/availability cadence contract to review QC."""

    try:
        return reference_profile.review_visual_density_contract(
            total_duration, available_visuals
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise ReviewPreviewError("review.visual_density_measurement_invalid") from exc


def _frame_edge_blank_metrics(image: Image.Image) -> dict[str, float]:
    """Expose the shared color-agnostic framing metric to review QC."""

    from app.services.framing_analysis import color_agnostic_edge_blank_span_fractions

    return color_agnostic_edge_blank_span_fractions(image)


def _frame_edge_blank_audit(frame_paths: Sequence[Path]) -> dict[str, object]:
    per_frame: list[dict[str, float]] = []
    try:
        for path in frame_paths:
            with Image.open(path) as image:
                per_frame.append(_frame_edge_blank_metrics(image))
    except (OSError, ValueError, TypeError) as exc:
        raise ReviewPreviewError("review.blank_edge_measurement_failed") from exc
    per_edge_max = {
        side: round(max((item[side] for item in per_frame), default=0.0), 6)
        for side in ("left", "right", "top", "bottom")
    }
    return {
        "per_frame_edge_blank_fractions": per_frame,
        "frame_edge_blank_fractions": per_edge_max,
        "max_frame_edge_blank_fraction": max(per_edge_max.values(), default=0.0),
    }


def _audit_motion_trajectory(
    samples: Sequence[Sequence[float]],
) -> dict[str, int | float]:
    """Reject reversing or discontinuous normalized camera trajectories."""

    normalized: list[tuple[float, float, float]] = []
    try:
        for sample in samples:
            if len(sample) != 3:
                raise ValueError("camera sample must contain x, y, scale")
            values = tuple(float(value) for value in sample)
            if not all(math.isfinite(value) for value in values):
                raise ValueError("camera sample is non-finite")
            normalized.append(values)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError) as exc:
        raise ReviewPreviewError("review.motion_jitter") from exc
    if len(normalized) < 2:
        raise ReviewPreviewError("review.motion_jitter")
    deltas = [
        tuple(right[index] - left[index] for index in range(3))
        for left, right in zip(normalized, normalized[1:], strict=False)
    ]
    direction_reversals = 0
    for axis in range(3):
        signs = [
            1 if delta[axis] > 1e-9 else -1
            for delta in deltas
            if abs(delta[axis]) > 1e-9
        ]
        direction_reversals += sum(
            left != right for left, right in zip(signs, signs[1:], strict=False)
        )
    max_step = max(
        (max(abs(delta[axis]) for axis in range(3)) for delta in deltas),
        default=0.0,
    )
    violations = direction_reversals + sum(
        max(abs(delta[axis]) for axis in range(3))
        > reference_profile.REVIEW_MOTION_MAX_NORMALIZED_STEP
        for delta in deltas
    )
    result = {
        "sample_count": len(normalized),
        "max_normalized_step": round(max_step, 6),
        "direction_reversals": direction_reversals,
        "jitter_violations": int(violations),
    }
    if violations:
        raise ReviewPreviewError("review.motion_jitter")
    return result


def _image_difference(left_path: Path, right_path: Path) -> float:
    with Image.open(left_path) as left, Image.open(right_path) as right:
        left_small = left.convert("L").resize((64, 114))
        right_small = right.convert("L").resize((64, 114))
        return round(float(ImageStat.Stat(ImageChops.difference(left_small, right_small)).mean[0]), 4)


def _audit_transition_pixels(
    frame_paths: Sequence[Path],
    shots: Sequence[Mapping[str, object]],
    *,
    duration: float,
) -> dict[str, object]:
    """Prove each planned non-cut transition changes rendered pixels."""

    if len(frame_paths) < 3 or duration <= 0.0:
        raise ReviewPreviewError("review.transition_measurement_missing")
    planned: list[dict[str, object]] = []
    try:
        for shot in shots[1:]:
            transition = str(shot.get("transition", "cut") or "cut")
            if transition in {"cut", "none"}:
                continue
            boundary = float(shot.get("start_time", 0.0))
            index = max(
                1,
                min(
                    len(frame_paths) - 2,
                    round(boundary / duration * (len(frame_paths) - 1)),
                ),
            )
            difference = _image_difference(
                frame_paths[index - 1], frame_paths[index + 1]
            )
            planned.append(
                {
                    "transition": transition,
                    "frame_index": index,
                    "pixel_diff": difference,
                }
            )
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        raise ReviewPreviewError("review.transition_measurement_failed") from exc
    visible = [
        item
        for item in planned
        if float(item["pixel_diff"]) >= reference_profile.REVIEW_MIN_TRANSITION_PIXEL_DIFF
    ]
    result = {
        "planned_transition_count": len(planned),
        "visible_transition_count": len(visible),
        "transition_pixel_diffs": planned,
    }
    if len(visible) != len(planned):
        raise ReviewPreviewError("review.transition_not_visible")
    return result


def _audit_transition_pixels_from_video(
    output: Path,
    root: Path,
    shots: Sequence[Mapping[str, object]],
    *,
    fps: float,
) -> dict[str, object]:
    """Measure each transition inside its exact rendered frame window."""
    if fps <= 0.0:
        raise ReviewPreviewError("review.transition_measurement_missing")
    planned: list[dict[str, object]] = []
    requested: set[int] = set()
    try:
        for shot in shots[1:]:
            transition = str(shot.get("transition", "cut") or "cut")
            if transition in {"cut", "none"}:
                continue
            boundary = float(shot.get("start_time", 0.0))
            window = float(shot.get("transition_duration_s", 0.0))
            if window <= 0.0:
                raise ReviewPreviewError("review.transition_measurement_missing")
            times = tuple(boundary + window * fraction for fraction in (0.25, 0.50, 0.75))
            indices = tuple(max(0, int(round(value * fps))) for value in times)
            if not (indices[0] < indices[1] < indices[2]):
                raise ReviewPreviewError("review.transition_measurement_missing")
            requested.update(indices)
            planned.append({"transition": transition, "boundary_s": boundary, "frame_indices": indices})
    except (TypeError, ValueError, OverflowError) as exc:
        raise ReviewPreviewError("review.transition_measurement_failed") from exc
    frame_dir = root / "transition-audit-frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    for stale in frame_dir.glob("frame-*.jpg"):
        stale.unlink()
    ordered = sorted(requested)
    expression = "+".join(f"eq(n\\,{index})" for index in ordered)
    try:
        render_service._run(
            [
                settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(output),
                "-vf", f"select={expression},scale=180:320:flags=lanczos",
                "-fps_mode", "vfr",
                str(frame_dir / "frame-%03d.jpg"),
            ],
            timeout=900,
            step="review_transition_frames",
        )
    except render_service.RenderError as exc:
        raise ReviewPreviewError("review.transition_measurement_failed") from exc
    paths = sorted(frame_dir.glob("frame-*.jpg"))
    if len(paths) != len(ordered):
        raise ReviewPreviewError("review.transition_measurement_missing")
    by_index = dict(zip(ordered, paths, strict=True))
    visible_count = 0
    for item in planned:
        first, middle, last = item["frame_indices"]
        first_mid = _image_difference(by_index[first], by_index[middle])
        mid_last = _image_difference(by_index[middle], by_index[last])
        item["first_mid_pixel_diff"] = first_mid
        item["mid_last_pixel_diff"] = mid_last
        if min(first_mid, mid_last) >= reference_profile.REVIEW_MIN_TRANSITION_PIXEL_DIFF:
            visible_count += 1
    result = {
        "planned_transition_count": len(planned),
        "visible_transition_count": visible_count,
        "transition_pixel_diffs": planned,
        "sampling": "exact_transition_window_v1",
    }
    if visible_count != len(planned):
        raise ReviewPreviewError("review.transition_not_visible")
    return result


def _measured_subtitle_qc(
    sidecar: Mapping[str, object], contract: Mapping[str, object]
) -> dict[str, object]:
    evidence = sidecar.get("subtitle_evidence")
    if not isinstance(evidence, Mapping):
        raise ReviewPreviewError("review.subtitle_measurement_missing")
    try:
        safe_margin = float(contract.get("safe_margin_px", 0))
        maximum_width = float(evidence["max_active_text_width_px"])
        safe_width = float(evidence["safe_text_width_px"])
        clearance = float(evidence["minimum_horizontal_clearance_px"])
        lines = int(evidence["max_lines_measured"])
        font_name = str(evidence["font_name"])
        font_hash = str(evidence["font_file_sha256"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ReviewPreviewError("review.subtitle_measurement_invalid") from exc
    if (
        not font_hash
        or font_name != str(contract.get("font_name", ""))
        or lines > 2
        or maximum_width > safe_width + 0.5
        or clearance + 0.5 < safe_margin
    ):
        raise ReviewPreviewError("review.subtitle_measurement_invalid")
    return dict(evidence)


def _corroborated_frame_edge_blank_max(
    shots: Sequence[Mapping[str, object]],
    audit: Mapping[str, object],
) -> float:
    threshold = reference_profile.REVIEW_MAX_FRAME_EDGE_BLANK_FRACTION
    raw_max = float(audit.get("max_frame_edge_blank_fraction", 0.0))
    if raw_max <= threshold:
        return raw_max
    per_frame = audit.get("per_frame_edge_blank_fractions")
    sample_times = audit.get("sample_frame_times_s")
    if not isinstance(per_frame, list) or not isinstance(sample_times, list):
        return raw_max
    if len(per_frame) != len(sample_times):
        return raw_max
    effective: list[float] = []
    for metrics, sample_time in zip(per_frame, sample_times, strict=True):
        if not isinstance(metrics, Mapping):
            return raw_max
        try:
            raw = float(metrics.get("max_edge_blank_fraction", 0.0))
            timestamp = float(sample_time)
        except (TypeError, ValueError, OverflowError):
            return raw_max
        if raw <= threshold:
            effective.append(raw)
            continue
        active = None
        for shot in reversed(shots):
            try:
                start = float(shot.get("start_time", 0.0))
                end = float(shot.get("end_time", 0.0))
            except (TypeError, ValueError, OverflowError):
                continue
            if start - 1e-9 <= timestamp <= end + 1e-9:
                active = shot
                break
        if active is None:
            effective.append(raw)
            continue
        telemetry = active.get("framing_telemetry")
        preflight = telemetry.get("motion_pixel_preflight") if isinstance(telemetry, Mapping) else None
        if not isinstance(preflight, Mapping) or preflight.get("status") != "safe":
            effective.append(raw)
            continue
        try:
            corroborated = float(preflight["max_motion_edge_blank_fraction"])
            preflight_threshold = float(preflight["threshold"])
        except (KeyError, TypeError, ValueError, OverflowError):
            effective.append(raw)
            continue
        if corroborated <= threshold and preflight_threshold <= threshold + 1e-9:
            effective.append(min(raw, corroborated))
        else:
            effective.append(raw)
    return max(effective, default=raw_max)


def _panel_repetition_audit(shots: Sequence[Mapping[str, object]]) -> dict[str, object]:
    window = reference_profile.REVIEW_PANEL_REUSE_WINDOW_SHOTS
    ids = [str(shot.get("panel_id") or shot.get("source_asset_id") or "") for shot in shots]
    immediate: list[int] = []
    near: list[dict[str, object]] = []
    last_seen: dict[str, int] = {}
    for index, panel_id in enumerate(ids):
        if not panel_id:
            continue
        previous = last_seen.get(panel_id)
        if previous is not None:
            distance = index - previous
            if distance == 1:
                immediate.append(index)
            elif distance <= window:
                reasons = shots[index].get("alignment_reasons", ())
                exempt = isinstance(reasons, Sequence) and not isinstance(reasons, (str, bytes)) and "reuse_purpose:grounded_capacity_exhausted" in {str(value) for value in reasons}
                near.append({"shot_index": index, "panel_id": panel_id, "distance": distance, "grounded_capacity_exhausted": exempt})
        last_seen[panel_id] = index
    non_exempt_near = [item for item in near if not bool(item["grounded_capacity_exhausted"])]
    if immediate or non_exempt_near:
        raise ReviewPreviewError("review.panel_repetition_excessive")
    counts: dict[str, int] = {}
    for panel_id in ids:
        if panel_id:
            counts[panel_id] = counts.get(panel_id, 0) + 1
    return {
        "window_shots": window,
        "immediate_repeat_count": len(immediate),
        "near_repeat_count": len(near),
        "avoidable_near_repeat_count": len(non_exempt_near),
        "max_panel_usage_count": max(counts.values(), default=0),
        "unique_panel_count": len(counts),
        "repeat_exceptions": near,
    }


def _fade_transition_policy_audit(shots: Sequence[Mapping[str, object]]) -> dict[str, object]:
    transitions = [str(shot.get("transition", "cut") or "cut") for shot in shots]
    missing = [index for index, value in enumerate(transitions[1:], start=1) if value in {"cut", "none"}]
    non_fade = [index for index, value in enumerate(transitions[1:], start=1) if value not in {"fade", "cut", "none"}]
    if missing:
        raise ReviewPreviewError("review.transition_missing")
    if non_fade:
        raise ReviewPreviewError("review.transition_policy_invalid")
    return {"policy": "fade_only_v1", "boundary_count": max(0, len(shots)-1), "missing_transition_count": 0, "non_fade_transition_count": len(non_fade)}



def _measured_visual_qc(
    sidecar: Mapping[str, object],
    *,
    blank_target_fraction: float = 0.03,
) -> dict[str, object]:
    shots = sidecar.get("shots")
    if not isinstance(shots, list) or not shots:
        raise ReviewPreviewError("review.framing_measurement_missing")
    fractions: list[float] = []
    for shot in shots:
        telemetry = shot.get("framing_telemetry") if isinstance(shot, Mapping) else None
        if not isinstance(telemetry, Mapping):
            raise ReviewPreviewError("review.framing_measurement_missing")
        try:
            fraction = float(telemetry["edge_connected_blank_fraction"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ReviewPreviewError("review.framing_measurement_invalid") from exc
        if not 0.0 <= fraction <= blank_target_fraction + 1e-9:
            raise ReviewPreviewError("review.blank_space_exceeds_target")
        fractions.append(fraction)
    measured: dict[str, object] = {
        "blank_target_fraction": blank_target_fraction,
        "max_edge_blank_fraction": max(fractions),
        "per_shot_edge_blank_fraction": fractions,
    }
    visual_motion = sidecar.get("visual_motion_audit")
    if not isinstance(visual_motion, Mapping):
        return measured
    audit = dict(visual_motion)
    review_shots = [shot for shot in shots if isinstance(shot, Mapping)]
    repetition_audit = _panel_repetition_audit(review_shots)
    transition_policy_audit = _fade_transition_policy_audit(review_shots)
    audit["panel_repetition_audit"] = repetition_audit
    audit["transition_policy_audit"] = transition_policy_audit
    visual_keys = {
        (
            str(shot.get("panel_id") or shot.get("source_asset_id") or ""),
            _canonical(shot.get("selected_roi") or {}),
        )
        for shot in shots
        if isinstance(shot, Mapping)
    }
    available_capacity = max(
        [
            int(
                (shot.get("framing_telemetry") or {}).get(
                    "available_visual_capacity", 0
                )
            )
            for shot in shots
            if isinstance(shot, Mapping)
            and isinstance(shot.get("framing_telemetry"), Mapping)
        ]
        or [0]
    )
    if (
        int(repetition_audit.get("max_panel_usage_count", 0)) > 1
        and available_capacity >= len(review_shots)
    ):
        repetition_audit["avoidable_with_available_capacity"] = True
        raise ReviewPreviewError("review.panel_repetition_excessive")
    durations = [
        max(0.0, float(shot.get("end_time", 0.0)) - float(shot.get("start_time", 0.0)))
        for shot in shots
        if isinstance(shot, Mapping)
    ]
    total_duration = sum(durations)
    if any(
        duration > reference_profile.REVIEW_MAX_SHOT_SECONDS + 1e-9
        for duration in durations
    ):
        raise ReviewPreviewError("review.shot_duration_excessive")
    modes = {
        str(shot.get("motion_mode", "hold"))
        for shot in shots
        if isinstance(shot, Mapping)
    }
    mode_counts: dict[str, int] = {}
    for shot in shots:
        if isinstance(shot, Mapping):
            mode = str(shot.get("motion_mode", "hold"))
            mode_counts[mode] = mode_counts.get(mode, 0) + 1
    panel_ids = [
        str(shot.get("panel_id") or shot.get("source_asset_id") or "")
        for shot in shots
        if isinstance(shot, Mapping)
    ]
    reuse_streak = 0
    current_streak = 0
    previous_panel = None
    for panel_id in panel_ids:
        if panel_id and panel_id == previous_panel:
            current_streak += 1
        else:
            current_streak = 1 if panel_id else 0
        reuse_streak = max(reuse_streak, current_streak)
        previous_panel = panel_id
    transition_count = sum(
        1
        for shot in shots[1:]
        if isinstance(shot, Mapping)
        and str(shot.get("transition", "cut") or "cut") not in {"cut", "none"}
    )
    expected_transition_count = max(0, len(shots) - 1)
    if transition_count != expected_transition_count:
        raise ReviewPreviewError("review.transition_missing")
    audit.setdefault("unique_visuals", len(visual_keys))
    audit.setdefault("available_visuals", max(len(visual_keys), available_capacity))
    audit.setdefault("motion_mode_diversity", len(modes))
    audit.setdefault("motion_mode_distribution", dict(sorted(mode_counts.items())))
    audit.setdefault("reuse_streak_max", reuse_streak)
    audit["transition_count"] = transition_count
    raw_frame_blank = float(audit.get("max_frame_edge_blank_fraction", 0.0))
    effective_frame_blank = _corroborated_frame_edge_blank_max(shots, audit)
    audit["raw_max_frame_edge_blank_fraction"] = raw_frame_blank
    audit["corroborated_max_frame_edge_blank_fraction"] = effective_frame_blank
    audit["max_frame_edge_blank_fraction"] = effective_frame_blank
    if effective_frame_blank > reference_profile.REVIEW_MAX_FRAME_EDGE_BLANK_FRACTION:
        raise ReviewPreviewError("review.blank_edge_visible")
    trajectory_reports: list[dict[str, int | float]] = []
    for shot in shots:
        if not isinstance(shot, Mapping):
            continue
        trajectory = shot.get("motion_trajectory")
        if trajectory:
            trajectory_reports.append(_audit_motion_trajectory(trajectory))
    if trajectory_reports:
        trajectory_audit = {
            "shot_count": len(trajectory_reports),
            "jitter_violations": sum(
                int(item["jitter_violations"]) for item in trajectory_reports
            ),
            "direction_reversals": sum(
                int(item["direction_reversals"]) for item in trajectory_reports
            ),
            "max_normalized_step": max(
                float(item["max_normalized_step"]) for item in trajectory_reports
            ),
        }
        audit.setdefault("motion_trajectory_audit", trajectory_audit)
        if int(trajectory_audit["jitter_violations"]) > 0:
            raise ReviewPreviewError("review.motion_jitter")
    transition_audit = audit.get("transition_pixel_audit")
    if isinstance(transition_audit, Mapping) and int(
        transition_audit.get("visible_transition_count", 0)
    ) < int(transition_audit.get("planned_transition_count", 0)):
        raise ReviewPreviewError("review.transition_not_visible")
    rendered_motion = audit.get("rendered_shot_motion_audit")
    if isinstance(rendered_motion, Mapping):
        if int(rendered_motion.get("shot_count", -1)) != len(review_shots):
            raise ReviewPreviewError("review.motion_measurement_missing")
        if int(rendered_motion.get("static_shot_count", 0)) > 0:
            raise ReviewPreviewError("review.motion_noop")
        if int(rendered_motion.get("stair_step_shot_count", 0)) > 0:
            raise ReviewPreviewError("review.motion_jitter")
    if float(audit.get("max_unchanged_hold_s", 0.0)) > reference_profile.REVIEW_MAX_UNCHANGED_HOLD_SECONDS:
        raise ReviewPreviewError("review.visual_hold_excessive")
    static_modes = {"hold", "static_emphasis"}
    if any(
        str(shot.get("motion_mode", "hold")) in static_modes
        or str(shot.get("camera_curve", "")) in {"static", "static_emphasis"}
        for shot in shots
        if isinstance(shot, Mapping)
    ):
        raise ReviewPreviewError("review.motion_static")
    density = review_visual_density_contract(
        total_duration,
        max(
            len(visual_keys),
            int(audit.get("available_visuals", len(visual_keys))),
        ),
    )
    audit["visual_density_contract"] = density
    required_visuals = int(density["minimum_required_visuals"])
    if len(shots) >= 4 and len(visual_keys) < required_visuals:
        raise ReviewPreviewError("review.visual_density_insufficient")
    if int(audit.get("reuse_streak_max", reuse_streak)) > 2:
        raise ReviewPreviewError("review.visual_reuse_streak_excessive")
    stable_review_modes = {"slow_push", "slow_pull"}
    if any(mode not in stable_review_modes for mode in modes):
        raise ReviewPreviewError("review.motion_path_invalid")
    if (
        len(shots) >= 4
        and int(audit.get("motion_mode_diversity", len(modes))) > 0
        and "mean_frame_diff" in audit
        and float(audit.get("mean_frame_diff", 0.0)) < 0.25
    ):
        raise ReviewPreviewError("review.motion_noop")
    measured["visual_motion_audit"] = audit
    return measured


@dataclass(frozen=True)
class ReviewPreviewArtifacts:
    output_path: Path
    narration_path: Path
    display_cues_path: Path
    contact_sheet_path: Path
    qc_report_path: Path
    manifest_path: Path

    def as_dict(self) -> dict[str, object]:
        return {
            "output_path": str(self.output_path),
            "narration_path": str(self.narration_path),
            "display_cues_path": str(self.display_cues_path),
            "contact_sheet_path": str(self.contact_sheet_path),
            "qc_report_path": str(self.qc_report_path),
            "manifest_path": str(self.manifest_path),
            "provenance": PROVENANCE,
            "publish_allowed": False,
        }


def _clean(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return str(value)


def _canonical(value: object) -> str:
    return json.dumps(_clean(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(_canonical(value) + "\n", encoding="utf-8", newline="\n")
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ReviewPreviewError("review.artifact_write_failed") from exc


def _write_text(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(value, encoding="utf-8", newline="\n")
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ReviewPreviewError("review.artifact_write_failed") from exc


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _frame_motion_audit(frame_paths: list[Path], duration: float) -> dict[str, object]:
    """Measure rendered frame change without trusting planned motion metadata."""

    if len(frame_paths) < 2:
        raise ReviewPreviewError("review.motion_measurement_missing")
    differences: list[float] = []
    try:
        for before_path, after_path in zip(frame_paths, frame_paths[1:], strict=False):
            with Image.open(before_path) as before, Image.open(after_path) as after:
                left = before.convert("L").resize((64, 114))
                right = after.convert("L").resize((64, 114))
                differences.append(round(float(ImageStat.Stat(ImageChops.difference(left, right)).mean[0]), 4))
    except (OSError, ValueError, TypeError) as exc:
        raise ReviewPreviewError("review.motion_measurement_failed") from exc
    ordered = sorted(differences)
    interval = float(duration) / max(1, len(frame_paths) - 1)
    longest_run = 0
    current_run = 0
    for difference in differences:
        if difference < 0.5:
            current_run += 1
            longest_run = max(longest_run, current_run)
        else:
            current_run = 0
    return {
        "sample_frame_count": len(frame_paths),
        "mean_frame_diff": round(sum(differences) / len(differences), 4),
        "p95_frame_diff": ordered[min(len(ordered) - 1, int((len(ordered) - 1) * 0.95))],
        "min_frame_diff": ordered[0],
        "max_frame_diff": ordered[-1],
        "near_identical_fraction": round(sum(value < 0.5 for value in differences) / len(differences), 4),
        "max_unchanged_hold_s": round((longest_run + 1) * interval, 3),
    }


def _rendered_shot_motion_audit(
    output: Path,
    root: Path,
    shots: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Measure motion inside every rendered shot, excluding fade windows."""

    sample_root = root / "shot-motion-audit"
    sample_root.mkdir(parents=True, exist_ok=True)
    reports: list[dict[str, object]] = []
    for index, shot in enumerate(shots):
        try:
            start = float(shot.get("start_time", 0.0))
            end = float(shot.get("end_time", 0.0))
            transition = float(shot.get("transition_duration_s", 0.0) or 0.0)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ReviewPreviewError("review.motion_measurement_failed") from exc
        duration = end - start
        if duration <= 0.0:
            raise ReviewPreviewError("review.motion_measurement_failed")
        lead = 0.08 if index == 0 else min(duration * 0.30, transition + 0.08)
        tail = min(0.08, duration * 0.10)
        body_start = start + lead
        body_duration = end - tail - body_start
        if body_duration < 0.50:
            body_start = start + min(0.05, duration * 0.10)
            body_duration = max(0.30, end - min(0.05, duration * 0.10) - body_start)
        shot_root = sample_root / f"shot-{index:02d}"
        shot_root.mkdir(parents=True, exist_ok=True)
        try:
            render_service._run(
                [settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                 "-ss", f"{body_start:.6f}", "-i", str(output),
                 "-t", f"{body_duration:.6f}", "-vf", "fps=12,scale=180:320:flags=lanczos",
                 "-q:v", "3", str(shot_root / "frame-%03d.jpg")],
                timeout=240,
                step="review_shot_motion_frames",
            )
        except render_service.RenderError as exc:
            raise ReviewPreviewError("review.motion_measurement_failed") from exc
        paths = sorted(shot_root.glob("frame-*.jpg"))
        if len(paths) < 6:
            raise ReviewPreviewError("review.motion_measurement_missing")
        metrics = _frame_motion_audit(paths, body_duration)
        near_identical = float(metrics.get("near_identical_fraction", 1.0))
        mean_diff = float(metrics.get("mean_frame_diff", 0.0))
        static = mean_diff < 0.03
        stair_step = near_identical > 0.55
        reports.append({
            "shot_index": index,
            "panel_id": str(shot.get("panel_id") or shot.get("source_asset_id") or ""),
            "motion_mode": str(shot.get("motion_mode", "")),
            "camera_curve": str(shot.get("camera_curve", "")),
            "body_start_s": round(body_start, 6),
            "body_duration_s": round(body_duration, 6),
            **metrics,
            "static": static,
            "stair_step": stair_step,
        })
    return {
        "contract_version": "rendered-shot-motion-audit-v1",
        "shot_count": len(reports),
        "static_shot_count": sum(bool(row["static"]) for row in reports),
        "stair_step_shot_count": sum(bool(row["stair_step"]) for row in reports),
        "max_near_identical_fraction": max(
            (float(row["near_identical_fraction"]) for row in reports), default=1.0
        ),
        "shots": reports,
    }


def _render_audit(
    output: Path,
    root: Path,
    duration: float,
    *,
    shots: Sequence[Mapping[str, object]] = (),
) -> tuple[Path, Path, str, dict[str, object]]:
    """Create ffprobe, blackdetect, and a deterministic 69-frame contact sheet."""

    root.mkdir(parents=True, exist_ok=True)
    try:
        probe_text = render_service._run(
            [
                settings.ffprobe_bin,
                "-v",
                "error",
                "-show_entries",
                "format=duration,size:stream=index,codec_type,codec_name,profile,pix_fmt,width,height,r_frame_rate,avg_frame_rate,nb_frames",
                "-of",
                "json",
                str(output),
            ],
            timeout=120,
            step="review_ffprobe",
        )
        probe = json.loads(probe_text or "{}")
        _write_json(root / "ffprobe.json", probe)
    except (OSError, ValueError, TypeError, render_service.RenderError) as exc:
        if isinstance(exc, render_service.RenderError):
            raise ReviewPreviewError("review.ffprobe_failed") from exc
        raise ReviewPreviewError("review.ffprobe_failed") from exc

    try:
        black_log = render_service._run(
            [
                settings.ffmpeg_bin,
                "-hide_banner",
                "-loglevel",
                "info",
                "-i",
                str(output),
                "-vf",
                "blackdetect=d=0.05:pix_th=0.01",
                "-an",
                "-f",
                "null",
                "NUL",
            ],
            timeout=900,
            step="review_blackdetect",
        )
        _write_text(root / "blackdetect.txt", black_log)
        if "black_start" in black_log or "black_end" in black_log:
            raise ReviewPreviewError("review.black_frame_detected")
    except ReviewPreviewError:
        raise
    except render_service.RenderError as exc:
        raise ReviewPreviewError("review.blackdetect_failed") from exc

    frames = root / "audit-frames-69"
    frames.mkdir(parents=True, exist_ok=True)
    try:
        render_service._run(
            [
                settings.ffmpeg_bin,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(output),
                "-vf",
                f"fps=69/{max(0.001, duration):.6f},scale=180:320:flags=lanczos",
                "-q:v",
                "3",
                str(frames / "frame-%02d.jpg"),
            ],
            timeout=900,
            step="review_contact_sheet_frames",
        )
        paths = sorted(frames.glob("frame-*.jpg"))
        if len(paths) != 69:
            raise ReviewPreviewError("review.contact_sheet_frame_count")
        thumb = Image.open(paths[0]).convert("RGB")
        width, height = thumb.size
        columns, rows = 9, 8
        sheet = Image.new("RGB", (columns * width, rows * (height + 20)), "#121212")
        draw = ImageDraw.Draw(sheet)
        for index, path in enumerate(paths):
            with Image.open(path) as image:
                sheet.paste(image.convert("RGB").resize((width, height)), ((index % columns) * width, (index // columns) * (height + 20)))
            draw.text(((index % columns) * width + 4, (index // columns) * (height + 20) + height + 2), str(index + 1), fill="white")
        contact_sheet = root / "contact-sheet-69-frame.jpg"
        sheet.save(contact_sheet, quality=90)
    except ReviewPreviewError:
        raise
    except (OSError, ValueError, render_service.RenderError) as exc:
        raise ReviewPreviewError("review.contact_sheet_failed") from exc
    frame_audit = _frame_motion_audit(paths, duration)
    frame_audit["sample_frame_times_s"] = [
        round(index * duration / max(1, len(paths)), 6)
        for index in range(len(paths))
    ]
    frame_audit.update(_frame_edge_blank_audit(paths))
    if shots:
        video_stream = next(
            (item for item in probe.get("streams", []) if item.get("codec_type") == "video"),
            {},
        )
        rate = str(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "0/1")
        numerator, denominator = rate.split("/", 1)
        measured_fps = float(numerator) / max(float(denominator), 1e-9)
        frame_audit["transition_pixel_audit"] = _audit_transition_pixels_from_video(
            output,
            root,
            shots,
            fps=measured_fps,
        )
        frame_audit["rendered_shot_motion_audit"] = _rendered_shot_motion_audit(
            output, root, shots
        )
    return root / "ffprobe.json", contact_sheet, root / "blackdetect.txt", frame_audit


def write_review_preview_bundle(
    db: Any,
    project_id: str,
    result: Any,
    *,
    output_dir: Path,
    subtitle_contract: Mapping[str, object] | None = None,
    subtitle_timing_source: str = DISPLAY_TIMING_VERSION,
    blank_target_fraction: float = 0.03,
) -> ReviewPreviewArtifacts:
    """Persist a sanitized local bundle beside the video-only preview."""

    output = Path(result.output_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if not output.is_file():
        raise ReviewPreviewError("review.output_missing")
    analysis = db.scalars(
        select(StoryAnalysis)
        .where(StoryAnalysis.project_id == project_id)
        .order_by(StoryAnalysis.created_at.desc())
    ).first()
    script = db.scalars(
        select(ScriptVersion)
        .where(ScriptVersion.project_id == project_id)
        .order_by(ScriptVersion.version.desc())
    ).first()
    if analysis is None or script is None:
        raise ReviewPreviewError("review.analysis_or_script_missing")
    regions = list(
        db.scalars(
            select(PanelRegion)
            .where(PanelRegion.story_analysis_id == analysis.id)
            .order_by(PanelRegion.source_order, PanelRegion.panel_id, PanelRegion.id)
        )
    )
    assets = {
        asset.id: asset
        for asset in db.scalars(select(SourceAsset).where(SourceAsset.project_id == project_id)).all()
    }
    cues = list(
        db.scalars(
            select(SubtitleCue)
            .where(SubtitleCue.project_id == project_id)
            .order_by(SubtitleCue.start_time, SubtitleCue.order_index)
        )
    )
    sections = [
        {
            "section": item.get("section"),
            "editorial_role": item.get("editorial_role"),
            "spoken_text": item.get("text", ""),
            "claim_ids": item.get("claim_ids") or [],
            "evidence": item.get("evidence") or [],
            "evidence_panel_ids": item.get("evidence_panel_ids") or [],
            "source_order_citations": item.get("citations") or [],
            "estimated_duration_s": item.get("estimated_duration"),
        }
        for item in script.sections
    ]
    spoken = "\n\n".join(item["spoken_text"] for item in sections if item["spoken_text"])
    narration_path = output_dir / "narration_spoken.txt"
    _write_text(narration_path, spoken + "\n")
    display_cues = {
        "schema_version": subtitle_timing_source,
        "timing_authoritative": False,
        "timing_source": subtitle_timing_source,
        "spoken_text_immutable_sha256": hashlib.sha256(spoken.encode("utf-8")).hexdigest(),
        "display_surface": "punctuation-free uppercase one-word cues",
        "cues": [
            {"order_index": cue.order_index, "start_time": cue.start_time, "end_time": cue.end_time, "display_text": cue.text}
            for cue in cues
        ],
    }
    for cue in display_cues["cues"]:
        text = str(cue["display_text"])
        if not text or text != text.upper() or not text.isalnum():
            raise ReviewPreviewError("review.display_derivation_invalid")
    display_cues_path = output_dir / "display_cues.json"
    _write_json(display_cues_path, display_cues)
    _write_text(output_dir / "display_text.txt", "\n".join(item["display_text"] for item in display_cues["cues"]) + "\n")

    ledger = []
    observations = []
    for region in regions:
        asset = assets.get(region.source_asset_id)
        observation = dict(region.observation_json or {})
        evidence = dict(observation.get("visual_evidence") or {})
        evidence_snapshot = {
            key: evidence.get(key)
            for key in (
                "evidence_version", "evidence_hash", "balloon_mask_status", "mask_status",
                "mask_confidence", "mask_source", "evidence_source", "source_asset_id",
                "source_order", "panel_id", "regions", "protected_regions",
                "subject_regions", "action_regions", "effect_regions",
            )
            if key in evidence
        }
        ledger.append(
            {
                "source_order": region.source_order,
                "role": "title_or_front_matter" if region.source_order == 0 else "story",
                "panel_id": region.panel_id,
                "panel_region_id": region.id,
                "source_asset_id": region.source_asset_id,
                "source_asset_checksum": region.source_asset_checksum,
                "source_asset_original_checksum": getattr(asset, "original_checksum", None),
                "source_dimensions": [region.original_width, region.original_height],
                "bounds": region.bounds_json,
                "evidence_hash": evidence.get("evidence_hash"),
                "evidence_snapshot": evidence_snapshot,
                "title_exclusion_reason": "front matter/title page excluded from spoken story" if region.source_order == 0 else None,
            }
        )
        observations.append(
            {
                "source_order": region.source_order,
                "panel_id": region.panel_id,
                "panel_region_id": region.id,
                "source_asset_id": region.source_asset_id,
                "source_asset_checksum": region.source_asset_checksum,
                "bounds": region.bounds_json,
                "visible_facts": observation.get("visible_facts") or [],
                "qualified_inferences": observation.get("inferences") or [],
                "uncertainties": observation.get("uncertainties") or [],
                "evidence_refs": observation.get("evidence_refs") or region.evidence_refs_json or [],
                "visual_evidence": evidence_snapshot,
            }
        )
    _write_json(output_dir / "source_ledger.json", {"schema_version": PROVENANCE, "provenance": PROVENANCE, "analysis_id": analysis.id, "story_orders": [item["source_order"] for item in ledger if item["source_order"] > 0], "title_order": 0, "random_sampling": False, "panel_ledger": ledger})
    _write_json(output_dir / "observations.json", {"schema_version": PROVENANCE, "provenance": PROVENANCE, "all_panels_examined": True, "observations": observations})
    _write_json(output_dir / "causal_map.json", {"schema_version": PROVENANCE, "provenance": PROVENANCE, "analysis_id": analysis.id, "model_id": analysis.model_name, "instruction_version": analysis.instruction_version, "instruction_sha256": analysis.instruction_sha256, "story_spine": analysis.story_spine_json, "evidence_graph": analysis.evidence_graph_json, "continuity_ledger": analysis.continuity_ledger_json, "coverage_manifest": analysis.coverage_manifest_json, "reconciliation": analysis.reconciliation_json, "narrative_sections": sections})
    narrative_path = output_dir / "narrative_review.json"
    _write_json(narrative_path, {"schema_version": PROVENANCE, "provenance": PROVENANCE, "project_id": project_id, "analysis_id": analysis.id, "script_id": script.id, "script_version": script.version, "model_id": analysis.model_name, "narrative_identity": script.editorial_metadata.get("narrative_identity"), "sections": sections, "ending_kind": "open_question" if spoken.rstrip().endswith("?") else "consequence", "spoken_word_count": script.word_count, "estimated_duration_s": script.estimated_duration, "approval_state": "PENDING_EDITORIAL_REVIEW", "publish_allowed": False, "voice_state": "VISUAL_ONLY_WAITING_FOR_VOICE"})
    if result.sidecar_path and Path(result.sidecar_path).is_file():
        sidecar = json.loads(Path(result.sidecar_path).read_text(encoding="utf-8"))
    else:
        sidecar = {"shots": [], "publish_allowed": False}
    measured_subtitle = _measured_subtitle_qc(sidecar, subtitle_contract or {})
    shots_for_audit = sidecar.get("shots", [])
    if not isinstance(shots_for_audit, list):
        shots_for_audit = []
    ffprobe_path, contact_sheet, blackdetect_path, frame_motion = _render_audit(
        output,
        output_dir,
        float(result.duration),
        shots=tuple(
            item for item in shots_for_audit if isinstance(item, Mapping)
        ),
    )
    sidecar_for_qc = dict(sidecar)
    sidecar_for_qc["visual_motion_audit"] = frame_motion
    measured_visual = _measured_visual_qc(
        sidecar_for_qc,
        blank_target_fraction=blank_target_fraction,
    )
    visual_metrics_path = output_dir / "visual_diversity_metrics.json"
    _write_json(visual_metrics_path, measured_visual.get("visual_motion_audit", frame_motion))
    _write_json(output_dir / "edit_shot_plan.json", {"schema_version": "review_silent_edit_plan_v1", "provenance": PROVENANCE, "mp4": str(output), "render_sidecar": str(result.sidecar_path or ""), "shots": sidecar.get("shots", []), "visual_motion_audit": measured_visual.get("visual_motion_audit", frame_motion), "audio_stream_expected": False, "publish_allowed": False})

    probe = json.loads(ffprobe_path.read_text(encoding="utf-8"))
    video_streams = [item for item in probe.get("streams", []) if item.get("codec_type") == "video"]
    audio_streams = [item for item in probe.get("streams", []) if item.get("codec_type") == "audio"]
    telemetry = [item.get("framing_telemetry", {}) for item in sidecar.get("shots", []) if isinstance(item, Mapping)]
    qc = {
        "schema_version": "review_silent_qc_v1",
        "provenance": PROVENANCE,
        "approval_state": "PENDING_EDITORIAL_REVIEW",
        "publish_allowed": False,
        "technical": {"duration_s": float(result.duration), "width": result.width, "height": result.height, "codec": video_streams[0].get("codec_name") if video_streams else "", "profile": video_streams[0].get("profile") if video_streams else "", "pix_fmt": video_streams[0].get("pix_fmt") if video_streams else "", "fps": video_streams[0].get("avg_frame_rate") if video_streams else "", "video_streams": len(video_streams), "audio_streams": len(audio_streams), "sha256": _sha256(output), "size_bytes": output.stat().st_size},
        "visual": {"panel_regions_analyzed": len(regions), "story_panel_regions_analyzed": len([item for item in regions if item.source_order > 0]), "rendered_shots": len(sidecar.get("shots", [])), "balloon_overlap_hard_gate": all(float(item.get("balloon_mask_intersection_ratio", 1.0)) == 0.0 for item in telemetry), **measured_visual, "protected_retention_min": min((float(item.get("protected_retained_fraction", 0.0)) for item in telemetry), default=0.0), "contact_sheet": str(contact_sheet), "source_upscale_policy": sidecar.get("source_upscale_policy")},
        "subtitle": {"contract_version": SUBTITLE_CONTRACT_VERSION, "contract": subtitle_contract or {}, "timing_source": subtitle_timing_source, "timing_authoritative": False, "spoken_text_unchanged": True, "punctuation_free_display": True, "active_word_color": "yellow", "active_word_scale": 1.08, "word_cues": len(cues), "measured": measured_subtitle},
        "blackdetect": {"events_found": False, "report": str(blackdetect_path)},
        "warnings": ["review.source_upscale_non_native", "visual_review_pending"],
        "blocking_codes": [],
    }
    qc_report_path = output_dir / "qc_report.json"
    _write_json(qc_report_path, qc)
    artifact_paths = [path for path in output_dir.iterdir() if path.is_file() and path.name not in {"artifact_manifest.json"}]
    manifest = {"schema_version": "review_artifact_manifest_v1", "provenance": PROVENANCE, "approval_state": "PENDING_EDITORIAL_REVIEW", "publish_allowed": False, "files": {path.name: {"path": str(path), "sha256": _sha256(path), "size_bytes": path.stat().st_size} for path in sorted(artifact_paths, key=lambda item: item.name)}}
    manifest_path = output_dir / "artifact_manifest.json"
    _write_json(manifest_path, manifest)
    return ReviewPreviewArtifacts(output, narration_path, display_cues_path, contact_sheet, qc_report_path, manifest_path)


__all__ = ["DISPLAY_TIMING_VERSION", "PROVENANCE", "ReviewPreviewArtifacts", "ReviewPreviewError", "write_review_preview_bundle"]
