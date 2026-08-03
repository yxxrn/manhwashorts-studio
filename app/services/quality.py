"""Pre-publication quality checks (PRD FR-08).

Errors block publication; warnings can be overridden with a recorded reason.
This module answers "is this video safe and good enough to publish" and is the
only gate the publish endpoint trusts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.constants import (
    MAX_SUBTITLE_CHARS_PER_LINE,
    MAX_SUBTITLE_LINES,
    CheckSeverity,
)
from app.models import Project, RenderJob, ScriptVersion, SourceAsset
from app.services import editorial_timing, policy
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
) -> list[CheckResult]:
    """Full pre-publication sweep, combining policy and technical checks."""
    results: list[CheckResult] = []

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
    results += check_narration_language(script, project.language)
    results += check_audio(audio_segments)
    results += check_scenes(scenes, assets)
    results += check_subtitles(cues)

    effective_duration = duration if duration is not None else (job.duration if job else 0.0)
    if effective_duration or job:
        results += check_duration(effective_duration, float(project.target_duration))
    if job:
        results += check_output(job)

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
