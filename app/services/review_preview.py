"""Durable, review-only artifacts for the normal silent preview workflow.

This module deliberately stops at a video-only editorial preview.  It never
creates audio segments, invents authoritative voice timing, or changes the
publish gate.  The cloud job state and the rendered files are linked by the
same project/script/analysis identities.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageStat
from sqlalchemy import select

from app.config import settings
from app.models import PanelRegion, ScriptVersion, SourceAsset, StoryAnalysis, SubtitleCue
from app.services import render as render_service

PROVENANCE = "codex_cloud_multimodal_review_v1"
DISPLAY_TIMING_VERSION = "review_provisional_display_pacing_v1"
SUBTITLE_CONTRACT_VERSION = "sentence_chunked_word_karaoke_v2"


class ReviewPreviewError(RuntimeError):
    """Safe, stable failure for review artifact production."""

    def __init__(self, code: str, message: str = "review preview artifact failed") -> None:
        self.code = code
        super().__init__(message)


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
    durations = [
        max(0.0, float(shot.get("end_time", 0.0)) - float(shot.get("start_time", 0.0)))
        for shot in shots
        if isinstance(shot, Mapping)
    ]
    total_duration = sum(durations)
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
        if isinstance(shot, Mapping) and shot.get("transition") == "fade"
    )
    audit.setdefault("unique_visuals", len(visual_keys))
    audit.setdefault("available_visuals", max(len(visual_keys), available_capacity))
    audit.setdefault("motion_mode_diversity", len(modes))
    audit.setdefault("motion_mode_distribution", dict(sorted(mode_counts.items())))
    audit.setdefault("reuse_streak_max", reuse_streak)
    audit.setdefault("transition_count", transition_count)
    if float(audit.get("max_unchanged_hold_s", 0.0)) > 4.0:
        raise ReviewPreviewError("review.visual_hold_excessive")
    required_visuals = min(
        int(audit.get("available_visuals", len(visual_keys))),
        max(4, int(round(total_duration / 5.5))) if total_duration >= 45.0 else 4,
    )
    if len(shots) >= 4 and len(visual_keys) < required_visuals:
        raise ReviewPreviewError("review.visual_diversity_insufficient")
    if int(audit.get("reuse_streak_max", reuse_streak)) > 2:
        raise ReviewPreviewError("review.visual_reuse_streak_excessive")
    if len(shots) >= 4 and int(audit.get("motion_mode_diversity", len(modes))) < 4:
        raise ReviewPreviewError("review.motion_mode_diversity_insufficient")
    if len(shots) >= 4 and int(audit.get("transition_count", transition_count)) < 1:
        raise ReviewPreviewError("review.transition_missing")
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


def _render_audit(output: Path, root: Path, duration: float) -> tuple[Path, Path, str, dict[str, object]]:
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
    return root / "ffprobe.json", contact_sheet, root / "blackdetect.txt", _frame_motion_audit(paths, duration)


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
    ffprobe_path, contact_sheet, blackdetect_path, frame_motion = _render_audit(
        output, output_dir, float(result.duration)
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
