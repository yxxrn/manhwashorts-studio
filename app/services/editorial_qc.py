"""Post-render editorial QC and machine-readable report generation."""
from __future__ import annotations

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
    ending_has_payoff: bool = False
    ending_has_visual_evidence: bool = False
    full_playback_verified: bool = False
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
        running = running + duration if key == previous else duration
        longest_same = max(longest_same, running)
        previous = key
    return sum(durations) / len(durations), longest_same, len(crops), sum(durations)


def build_report(
    *, scenes: list[object], cues: list[object], duration: float, job_path: Path | None = None,
    rights_confidence: int = 5, source_cleanliness: int = 5,
) -> EditorialQC:
    average, longest, crops, total = _shot_metrics(scenes)
    frozen = _freeze_duration(job_path) if job_path and job_path.is_file() else 0.0
    single_words = sum(1 for cue in cues if len(str(cue.text).split()) == 1)
    caption_ratio = single_words / len(cues) if cues else 1.0
    report = EditorialQC(
        duration=round(duration, 3),
        average_shot_duration=round(average, 3),
        longest_static_segment=round(max(longest, frozen), 3),
        same_panel_same_crop_max=round(longest, 3),
        unique_crop_count=crops,
        single_word_caption_ratio=round(caption_ratio, 3),
        visual_evidence_confidence=1.0 if scenes else 0.0,
        original_commentary=5 if cues else 0,
        editorial_visual_transformation=5 if crops >= 4 else 2,
        episode_specificity=5 if len({s.asset_id for s in scenes if s.asset_id}) >= 5 else 2,
        template_repetition_risk=1 if crops >= 4 else 4,
        rights_confidence=rights_confidence,
        source_cleanliness=source_cleanliness,
        ending_has_payoff=any(getattr(s, "section", "") == "twist" for s in scenes),
        ending_has_visual_evidence=bool(scenes and scenes[-1].asset_id),
    )
    if duration < 60 or duration > 90:
        report.failures.append("duration_outside_60_90s")
    if not 1.2 <= average <= 2.4:
        report.failures.append("average_shot_duration_outside_1.2_2.4s")
    if longest > 2.5:
        report.failures.append("same_panel_same_crop_over_2.5s")
    if caption_ratio >= 0.15:
        report.failures.append("single_word_caption_ratio_ge_15pct")
    if rights_confidence != 5 or source_cleanliness != 5:
        report.failures.append("source_gate_failed")
    report.full_playback_verified = bool(job_path and job_path.is_file() and _decode_ok(job_path))
    if not report.full_playback_verified:
        report.failures.append("full_playback_not_verified")
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


__all__ = ["EditorialQC", "build_report"]
