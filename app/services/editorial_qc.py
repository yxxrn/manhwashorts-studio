"""Post-render editorial QC and machine-readable report generation."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class EditorialQC:
    duration: float = 0.0
    resolution: str = "1080x1920"
    fps: int = 30
    average_shot_duration: float = 0.0
    longest_static_segment: float = 0.0
    same_panel_same_crop_max: float = 0.0
    unique_crop_count: int = 0
    single_word_caption_ratio: float = 0.0
    visual_evidence_confidence: float = 0.0
    editorial_overlay_density: float = 0.0
    template_similarity: float = 0.0
    original_commentary: int = 0
    editorial_visual_transformation: int = 0
    episode_specificity: int = 0
    template_repetition_risk: int = 0
    rights_confidence: int = 0
    source_cleanliness: int = 0
    source_families: list[str] = field(default_factory=list)
    ending_has_payoff: bool = False
    ending_has_visual_evidence: bool = False
    full_playback_verified: bool = False
    audio_video_drift: float = 0.0
    black_frame_duration: float = 0.0
    publish_allowed: bool = False
    qc_pass: bool = False
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _shot_metrics(scenes: list[object]) -> tuple[float, float, int, float]:
    if not scenes:
        return 0.0, 0.0, 0, 0.0
    durations = [max(0.0, s.end_time - s.start_time) for s in scenes]
    crops = {(round(s.focus_x, 3), round(s.focus_y, 3), round(s.focus_end_x, 3), round(s.focus_end_y, 3)) for s in scenes}
    longest_same = 0.0
    running = 0.0
    previous = None
    for scene, duration in zip(scenes, durations, strict=True):
        key = (scene.asset_id, round(scene.focus_x, 3), round(scene.focus_y, 3), round(scene.focus_end_x, 3), round(scene.focus_end_y, 3))
        if key == previous:
            running += duration
            longest_same = max(longest_same, running)
        else:
            running = 0.0
        longest_same = max(longest_same, running)
        previous = key
    return sum(durations) / len(durations), longest_same, len(crops), sum(durations)


def build_report(
    *, scenes: list[object], cues: list[object], duration: float, job_path: Path | None = None,
    rights_confidence: int = 5, source_cleanliness: int = 5,
) -> EditorialQC:
    average, longest_same, crops, total = _shot_metrics(scenes)
    frozen = _freeze_duration(job_path) if job_path and job_path.is_file() else 0.0
    single_words = sum(1 for cue in cues if len(str(cue.text).split()) == 1)
    caption_ratio = single_words / len(cues) if cues else 1.0
    report = EditorialQC(
        duration=round(duration, 3),
        average_shot_duration=round(average, 3),
        longest_static_segment=round(frozen, 3),
        same_panel_same_crop_max=round(longest_same, 3),
        unique_crop_count=crops,
        single_word_caption_ratio=round(caption_ratio, 3),
        visual_evidence_confidence=1.0 if scenes else 0.0,
        original_commentary=5 if cues else 0,
        editorial_visual_transformation=5 if crops >= 4 else 2,
        episode_specificity=5 if len({s.asset_id for s in scenes if s.asset_id}) >= 5 else 2,
        template_repetition_risk=1 if crops >= 4 else 4,
        rights_confidence=rights_confidence,
        source_cleanliness=source_cleanliness,
        source_families=sorted({getattr(s, "source_family", "") for s in scenes if getattr(s, "source_family", "")}),
        ending_has_payoff=any(getattr(s, "section", "") == "twist" for s in scenes),
        ending_has_visual_evidence=bool(scenes and scenes[-1].asset_id),
    )
    report.audio_video_drift, report.black_frame_duration = _media_integrity(job_path, duration)
    if duration < 60 or duration > 90:
        report.failures.append("duration_outside_60_90s")
    if not 1.2 <= average <= 2.4:
        report.failures.append("average_shot_duration_outside_1.2_2.4s")
    if longest_same > 2.5:
        report.failures.append("same_panel_same_crop_over_2.5s")
    if caption_ratio >= 0.15:
        report.failures.append("single_word_caption_ratio_ge_15pct")
    valid_modes = {
        "hold", "slow_push", "slow_pull", "guided_pan", "focus_shift", "panel_reveal",
        "split_focus", "panel_stack", "impact", "whip_transition", "atmospheric",
        "static_emphasis",
    }
    invalid_motion = [s for s in scenes if getattr(s, "motion_mode", "hold") not in valid_modes]
    if invalid_motion:
        report.failures.append("invalid_motion_plan")
    for left, right in zip(scenes, scenes[1:], strict=False):
        if getattr(left, "motion_mode", "") == getattr(right, "motion_mode", "") == "impact":
            report.failures.append("strong_effects_consecutive")
            break
    if any(not getattr(s, "motion_reason", "").strip() for s in scenes):
        report.failures.append("motion_reason_missing")
    if rights_confidence != 5 or source_cleanliness != 5:
        report.failures.append("source_gate_failed")
    report.full_playback_verified = bool(job_path and job_path.is_file() and _decode_ok(job_path))
    if not report.full_playback_verified:
        report.failures.append("full_playback_not_verified")
    if report.audio_video_drift > (1 / 30):
        report.failures.append("audio_video_drift_over_one_frame")
    if report.black_frame_duration > 0.4:
        report.failures.append("unintended_black_frame")
    report.publish_allowed = not report.failures
    report.qc_pass = report.publish_allowed
    return report


def _decode_ok(path: Path) -> bool:
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], check=True, capture_output=True, timeout=600)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return True


def _freeze_duration(path: Path) -> float:
    """Return the longest FFmpeg-detected freeze interval."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(path), "-vf", "freezedetect=n=-60dB:d=0.2", "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=600, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0.0
    return max((float(value) for value in re.findall(r"freeze_duration:\s*([0-9.]+)", result.stderr)), default=0.0)


def _media_integrity(path: Path | None, expected: float) -> tuple[float, float]:
    """Measure stream drift and black intervals without decoding frames in Python."""
    if not path or not path.is_file():
        return expected, 0.0
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=120, check=True,
        )
        durations = {
            stream.get("codec_type"): float(stream.get("duration") or 0.0)
            for stream in json.loads(probe.stdout).get("streams", [])
        }
        drift = abs(durations.get("video", expected) - durations.get("audio", expected))
        black = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(path), "-vf", "blackdetect=d=0.15:pix_th=0.01", "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=600, check=False,
        )
        intervals = [float(value) for value in re.findall(r"black_duration:([0-9.]+)", black.stderr)]
        return round(drift, 4), max(intervals, default=0.0)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.SubprocessError):
        return expected, 0.0


__all__ = ["EditorialQC", "build_report"]
