"""Pre-publication quality checks (PRD FR-08).

Errors block publication; warnings can be overridden with a recorded reason.
This module answers "is this video safe and good enough to publish" and is the
only gate the publish endpoint trusts.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
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
    motion_director,
    policy,
    reference_profile,
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
                "Reference output frame rate is not 30 fps.",
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


def _reference_reuse_checks(scenes: list, profile) -> list[CheckResult]:
    results: list[CheckResult] = []
    counts = Counter(
        str(getattr(scene, "asset_id", ""))
        for scene in scenes
        if getattr(scene, "asset_id", "")
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
        if getattr(left, "asset_id", None) != getattr(right, "asset_id", None):
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
        asset_id = str(getattr(scene, "asset_id", "") or "")
        if asset_id:
            positions.setdefault(asset_id, []).append(scene)
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


def check_reference_profile(scenes: list, duration: float, profile) -> list[CheckResult]:
    """Apply the empirical reference duration, cadence, and cut gates."""
    results: list[CheckResult] = []
    if not profile.duration_min_s <= duration <= profile.duration_max_s:
        results.append(_fail(
            "reference.duration_outside_38_50s",
            CheckSeverity.ERROR,
            "Reference output duration must be between 38 and 50 seconds.",
        ))
    if not profile.shot_min <= len(scenes) <= profile.shot_max:
        results.append(_fail(
            "reference.shot_count_outside_28_36",
            CheckSeverity.ERROR,
            "Reference output must contain 28 to 36 shots.",
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
    durations = [max(0.0, float(scene.end_time) - float(scene.start_time)) for scene in scenes]
    total = sum(durations)
    signatures = [getattr(scene, "visual_signature", "") or getattr(scene, "asset_id", "") for scene in scenes]
    results: list[CheckResult] = []
    for issue in motion_director.audit_camera_sequence(scenes):
        results.append(_fail(
            issue,
            CheckSeverity.ERROR,
            "Camera motion violates the stable monotonic motion contract.",
        ))
    asset_counts = Counter(
        getattr(scene, "asset_id", "")
        for scene in scenes
        if getattr(scene, "asset_id", "")
    )
    if asset_counts:
        reuse_cap = visual_scoring.asset_use_cap(len(scenes))
        over_cap = {
            asset_id: count
            for asset_id, count in asset_counts.items()
            if count > reuse_cap
        }
        if len(asset_counts) > 1 and over_cap:
            results.append(_fail(
                "visual.asset_reuse_cap", CheckSeverity.ERROR,
                f"Asset reuse exceeds the {reuse_cap}-shot cap for {len(over_cap)} asset(s).",
                {
                    "cap": reuse_cap,
                    "shot_count": len(scenes),
                    "asset_counts": dict(sorted(over_cap.items())),
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
    results += check_repetition_and_motion(scenes, profile=profile)
    results += check_subtitles(cues)

    effective_duration = duration if duration is not None else (job.duration if job else 0.0)
    if effective_duration or job:
        if profile is not None:
            results += check_reference_profile(scenes, effective_duration, profile)
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
