"""Video rendering with FFmpeg (PRD FR-09).

Pipeline per render:

1. Prepare each scene image: crop to 9:16 around its focal point, upscale to
   1080x1920, apply a Ken Burns / pan move.
2. Concatenate scene clips with fades.
3. Mix the narration track (plus optional music).
4. Burn in subtitles from a generated ASS file.
5. Probe the result and checksum it.

Rendering happens in a scratch directory so a failed run leaves no partial
artifact where the publish step could pick it up.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from pathlib import Path
from random import Random
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageEnhance, ImageFilter, ImageFont, ImageOps

from app.config import settings
from app.constants import MAX_SUBTITLE_CHARS_PER_LINE, SUBTITLE_SAFE_BOTTOM
from app.services import (
    encoders,
    framing_analysis,
    motion_director,
    subtitle_karaoke,
    visual_scoring,
)
from app.services.reference_profile import ReferenceProfileConfig, profile_hash
from app.services.timeline import CueSpec, wrap_caption

if TYPE_CHECKING:
    from app.services.visual_scoring import PanelVisualEvidence

_SECTION_TRANSITION_MIN = 0.12
_SECTION_TRANSITION_MAX = 0.18


class RenderError(RuntimeError):
    """Raised when a render step fails. Message is safe to show the user."""

    def __init__(
        self,
        message: str,
        code: str = "render_failed",
        log_tail: str = "",
        telemetry: framing_analysis.FramingTelemetry | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.log_tail = log_tail
        self.telemetry = telemetry


@dataclass(frozen=True)
class KaraokeWord:
    """One punctuation-free display word with authoritative word timing."""

    text: str
    start_time: float
    end_time: float

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Z0-9]+", self.text):
            raise ValueError("subtitle.display_punctuation: display words must be uppercase alphanumeric")
        if (
            not math.isfinite(self.start_time)
            or not math.isfinite(self.end_time)
            or self.start_time < 0.0
            or self.end_time <= self.start_time
        ):
            raise ValueError("subtitle.word_timing_invalid: word timing must be finite, nonnegative, and ordered")


@dataclass(frozen=True)
class KaraokeSentenceGroup:
    """A complete display sentence whose active word changes by timing."""

    group_id: str
    words: tuple[KaraokeWord, ...]
    start_time: float
    end_time: float

    def __post_init__(self) -> None:
        if not self.group_id or not self.words:
            raise ValueError("subtitle.sentence_group_invalid: group requires an id and words")
        if (
            not math.isfinite(self.start_time)
            or not math.isfinite(self.end_time)
            or self.start_time < 0.0
            or self.end_time <= self.start_time
            or self.start_time > self.words[0].start_time
            or self.end_time < self.words[-1].end_time
        ):
            raise ValueError("subtitle.sentence_timing_invalid: group timing does not contain words")
        if any(
            left.end_time > right.start_time
            for left, right in zip(self.words, self.words[1:], strict=False)
        ):
            raise ValueError("subtitle.word_timing_overlap: sentence word timings overlap")


@dataclass
class SceneInput:
    """Everything the renderer needs to draw one scene."""

    image_path: Path | None
    start_time: float
    end_time: float
    focus_x: float = 0.5
    focus_y: float = 0.4
    focus_end_x: float = 0.5
    focus_end_y: float = 0.4
    camera_curve: str = "slow_push_in"
    motion_mode: str = "hold"
    motion_intensity: str = "low"
    motion_reason: str = ""
    effect: str = "kenburns_in"
    disabled_effects: list[str] = field(default_factory=list)
    transition: str = "cut"
    overlay_text: str = ""
    panel_region_id: str | None = None
    panel_id: str = ""
    panel_bounds: tuple[int, int, int, int] | None = None
    visual_evidence: Mapping[str, Any] | None = None
    source_asset_checksum: str = ""
    source_asset_id: str = ""
    source_order: int | None = None
    panel_size: tuple[int, int] | None = None
    evidence_hash: str = ""
    border_mask: Mapping[str, Any] | None = None
    selected_roi: Mapping[str, Any] | None = None
    fallback_attempts: list[Mapping[str, Any]] = field(default_factory=list)
    framing_telemetry: Mapping[str, Any] | None = None
    publish_allowed: bool = True
    review_source_upscale_manifest: Mapping[str, Any] | None = None

    @property
    def duration(self) -> float:
        return max(0.1, round(self.end_time - self.start_time, 3))


@dataclass
class RenderRequest:
    project_id: str
    scenes: list[SceneInput]
    audio_path: Path | None
    cues: list[CueSpec] = field(default_factory=list)
    output_path: Path | None = None
    width: int = 0
    height: int = 0
    fps: int = 0
    music_path: Path | None = None
    music_gain_db: float = -18.0
    title_text: str = ""
    preview: bool = False
    #: auto | cpu | nvenc | qsv | vaapi | videotoolbox. None uses the configured
    #: default. An unavailable GPU falls back to CPU rather than failing.
    encoder: str | None = None
    profile: ReferenceProfileConfig | None = None
    silent_reference_review: bool = False
    output_override: Path | None = None
    sidecar_path: Path | None = None
    sentence_groups: list[KaraokeSentenceGroup] = field(default_factory=list)
    subtitle_contract_version: str = ""
    subtitle_timing_source: str = ""
    subtitle_contract: Mapping[str, Any] | None = None
    persisted_reference_framing: bool = False
    review_source_upscale_policy: str | None = None


@dataclass
class RenderResult:
    output_path: Path
    subtitle_path: Path | None
    thumbnail_path: Path | None
    duration: float
    width: int
    height: int
    checksum: str
    size_bytes: int
    #: Which encoder actually did the work, so the UI can report CPU vs GPU.
    encoder: str = "cpu"
    encoder_label: str = ""
    encoder_hardware: bool = False
    #: Set when a requested GPU was unavailable and we fell back.
    encoder_fell_back: bool = False
    encoder_reason: str = ""
    scratch_bytes: int = 0
    sidecar_path: Path | None = None
    manifest_path: Path | None = None


def _run(cmd: list[str], timeout: int = 900, step: str = "ffmpeg") -> str:
    """Run a subprocess, raising RenderError with a trimmed log on failure."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True)
        return proc.stderr or proc.stdout
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or "")[-1500:]
        raise RenderError(
            f"{step} failed (exit {exc.returncode}). Last output: {tail[-300:] or 'none'}",
            code=f"{step}_failed",
            log_tail=tail,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RenderError(f"{step} timed out after {timeout}s", code=f"{step}_timeout") from exc
    except FileNotFoundError as exc:
        raise RenderError(
            f"{cmd[0]} not found. Install FFmpeg: sudo apt-get install ffmpeg",
            code="ffmpeg_missing",
        ) from exc


def probe(path: Path) -> dict:
    """Return media dimensions, stream profile, and audio presence."""
    out = _run(
        [
            settings.ffprobe_bin, "-v", "error",
            "-show_entries",
            "format=duration:stream=width,height,codec_type,codec_name,profile,pix_fmt,r_frame_rate",
            "-of", "default=noprint_wrappers=1",
            str(path),
        ],
        timeout=120,
        step="ffprobe",
    )
    info: dict = {
        "duration": 0.0,
        "width": 0,
        "height": 0,
        "has_audio": False,
        "codec": "",
        "profile": "",
        "pix_fmt": "",
        "fps": 0.0,
    }
    for line in out.splitlines():
        if line.startswith("duration=") and info["duration"] == 0.0:
            # ffprobe emits "duration=N/A" for some streams; leave the default.
            with contextlib.suppress(ValueError):
                info["duration"] = round(float(line.split("=", 1)[1]), 3)
        elif line.startswith("width=") and not info["width"]:
            info["width"] = int(float(line.split("=", 1)[1] or 0))
        elif line.startswith("height=") and not info["height"]:
            info["height"] = int(float(line.split("=", 1)[1] or 0))
        elif line.startswith("codec_name=") and not info["codec"]:
            info["codec"] = line.split("=", 1)[1]
        elif line.startswith("profile=") and not info["profile"]:
            info["profile"] = line.split("=", 1)[1]
        elif line.startswith("pix_fmt=") and not info["pix_fmt"]:
            info["pix_fmt"] = line.split("=", 1)[1]
        elif line.startswith("r_frame_rate=") and not info["fps"]:
            value = line.split("=", 1)[1]
            with contextlib.suppress(ValueError, ZeroDivisionError):
                numerator, denominator = value.split("/", 1)
                info["fps"] = round(float(numerator) / float(denominator), 3)
        elif line == "codec_type=audio":
            info["has_audio"] = True
    return info


def _validate_reference_encoder(selection, profile: ReferenceProfileConfig) -> None:
    """Reject final encoders that cannot produce the reference video contract."""
    args = encoders.video_args(selection, preview=False, final=True)
    try:
        profile_index = args.index("-profile:v")
        profile_value = args[profile_index + 1]
    except (ValueError, IndexError) as exc:
        raise RenderError(
            "reference.encoder_profile: encoder must explicitly emit H.264 High profile",
            code="reference.encoder_profile",
        ) from exc
    if str(profile_value).lower() != str(profile.final_codec_profile).lower():
        raise RenderError(
            "reference.encoder_profile: encoder must explicitly emit H.264 High profile",
            code="reference.encoder_profile",
        )
    try:
        pixel_index = args.index("-pix_fmt")
        pixel_value = args[pixel_index + 1]
    except (ValueError, IndexError) as exc:
        raise RenderError(
            "reference.encoder_pixel_format: encoder must emit yuv420p",
            code="reference.encoder_pixel_format",
        ) from exc
    if pixel_value != profile.final_pixel_format:
        raise RenderError(
            "reference.encoder_pixel_format: encoder must emit yuv420p",
            code="reference.encoder_pixel_format",
        )


def validate_reference_output(info: dict, profile: ReferenceProfileConfig) -> None:
    """Apply the profile-aware output QC gate before publishing a final file."""
    from app.services import quality

    failures = [
        result for result in quality.check_reference_output_profile(info, profile)
        if not result.passed
    ]
    if failures:
        codes = ", ".join(result.code for result in failures)
        raise RenderError(
            f"reference.output_profile: {codes}",
            code="reference.output_profile",
        )


def _reference_final_video_filter(
    filter_chain: str,
    profile: ReferenceProfileConfig | None,
    *,
    preview: bool,
) -> str:
    """Normalize full-range image input to the final reference pixel contract."""
    if profile is None or preview:
        return filter_chain
    return f"{filter_chain},scale=in_range=full:out_range=tv,format={profile.final_pixel_format}"


# --- image preparation -----------------------------------------------------


def crop_to_vertical(
    src: Path, dest: Path, width: int, height: int, focus_x: float, focus_y: float
) -> Path:
    """Crop and scale an image to exactly ``width`` x ``height``.

    The crop window is centred on the focal point but clamped to stay inside
    the frame, so a face near an edge is not sliced off.
    """
    target_ratio = width / height
    with Image.open(src) as img:
        img = img.convert("RGB")
        src_w, src_h = img.size
        src_ratio = src_w / src_h

        if src_ratio > target_ratio:
            # Source is wider: full height, crop width.
            crop_w = int(round(src_h * target_ratio))
            crop_h = src_h
        else:
            # Source is taller: full width, crop height.
            crop_w = src_w
            crop_h = int(round(src_w / target_ratio))

        crop_w = max(1, min(crop_w, src_w))
        crop_h = max(1, min(crop_h, src_h))

        centre_x = focus_x * src_w
        centre_y = focus_y * src_h
        left = int(round(centre_x - crop_w / 2))
        top = int(round(centre_y - crop_h / 2))
        left = max(0, min(left, src_w - crop_w))
        top = max(0, min(top, src_h - crop_h))

        cropped = img.crop((left, top, left + crop_w, top + crop_h))
        # Render the move at 1.15x so pan/zoom has pixels to work with.
        oversample = (int(width * 1.15), int(height * 1.15))
        resized = cropped.resize(oversample, Image.Resampling.LANCZOS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        resized.save(dest, "JPEG", quality=94)
    return dest


@dataclass(frozen=True)
class PreparedFrame:
    """The static reference frame selected before camera motion is applied."""

    path: Path
    crop_box: tuple[int, int, int, int]
    blank_fraction: float
    base_zoom: float
    telemetry: framing_analysis.FramingTelemetry | None = None


def reference_frame_cache_key(
    image_path: Path,
    width: int,
    height: int,
    focus_x: float,
    focus_y: float,
    end_x: float,
    end_y: float,
    profile: ReferenceProfileConfig | None,
    *,
    border_mask: framing_analysis.BorderMaskResult | None = None,
    evidence: PanelVisualEvidence | None = None,
) -> tuple:
    """Return a deterministic key for all inputs to static frame preparation."""
    base_key = (
        str(image_path),
        int(width),
        int(height),
        round(float(focus_x), 4),
        round(float(focus_y), 4),
        round(float(end_x), 4),
        round(float(end_y), 4),
        profile_hash(profile) if profile is not None else None,
        profile.base_frame_zoom_max if profile is not None else None,
        profile.max_blank_fraction if profile is not None else None,
    )
    if profile is None:
        return base_key
    if (border_mask is None) != (evidence is None):
        raise ValueError("visual.cache_identity_incomplete")
    if border_mask is None or evidence is None:
        return base_key
    return base_key + (
        border_mask.detector_version,
        border_mask.mask_sha256,
        evidence.balloon_mask_status,
        evidence.evidence_hash,
        framing_analysis.canonical_protected_geometry(evidence),
    )


def _reference_even(value: int) -> int:
    return max(2, int(value) // 2 * 2)


def _reference_legacy_box(
    src_w: int,
    src_h: int,
    width: int,
    height: int,
    focus_x: float,
    focus_y: float,
) -> tuple[int, int, int, int]:
    target_ratio = width / height
    if src_w / src_h > target_ratio:
        crop_w = int(round(src_h * target_ratio))
        crop_h = src_h
    else:
        crop_w = src_w
        crop_h = int(round(src_w / target_ratio))
    crop_w = max(1, min(crop_w, src_w))
    crop_h = max(1, min(crop_h, src_h))
    centre_x = float(focus_x) * src_w
    centre_y = float(focus_y) * src_h
    left = int(round(centre_x - crop_w / 2))
    top = int(round(centre_y - crop_h / 2))
    left = max(0, min(left, src_w - crop_w))
    top = max(0, min(top, src_h - crop_h))
    return left, top, left + crop_w, top + crop_h


def reference_panel_crop_box(
    panel_size: tuple[int, int],
    target_size: tuple[int, int],
    focus_x: float,
    focus_y: float,
    *,
    scale: float = 1.0,
) -> tuple[int, int, int, int]:
    """Return one deterministic panel-local crop box for review planning."""
    source_width, source_height = (int(panel_size[0]), int(panel_size[1]))
    target_width, target_height = (int(target_size[0]), int(target_size[1]))
    if (
        source_width <= 0
        or source_height <= 0
        or target_width <= 0
        or target_height <= 0
        or float(scale) <= 0.0
    ):
        raise ValueError("reference crop geometry is invalid")
    ratio = target_width / target_height
    crop_width = min(source_width, max(1, round(source_height * ratio * float(scale))))
    crop_height = min(source_height, max(1, round(crop_width / ratio)))
    if crop_height > source_height:
        crop_height = source_height
        crop_width = min(source_width, max(1, round(crop_height * ratio)))
    centre_x = max(0.0, min(1.0, float(focus_x))) * source_width
    centre_y = max(0.0, min(1.0, float(focus_y))) * source_height
    left = max(0, min(round(centre_x - crop_width / 2), source_width - crop_width))
    top = max(0, min(round(centre_y - crop_height / 2), source_height - crop_height))
    return left, top, left + crop_width, top + crop_height


def _reference_content_stats(
    image: Image.Image,
    focus_x: float,
    focus_y: float,
) -> tuple[float, float, float]:
    """Measure content and content near the requested focus without OCR."""
    sample = image.resize((96, 172), Image.Resampling.BILINEAR).convert("RGB")
    pixels = list(sample.getdata())
    nonblank = [
        not (red >= 245 and green >= 245 and blue >= 245 and
             max(red, green, blue) - min(red, green, blue) <= 10)
        for red, green, blue in pixels
    ]
    content_fraction = sum(nonblank) / len(nonblank)
    focus_x = max(0.0, min(1.0, float(focus_x)))
    focus_y = max(0.0, min(1.0, float(focus_y)))
    centre_x = int(round(focus_x * (sample.width - 1)))
    centre_y = int(round(focus_y * (sample.height - 1)))
    radius_x = max(4, sample.width // 8)
    radius_y = max(4, sample.height // 8)
    patch = [
        nonblank[y * sample.width + x]
        for y in range(max(0, centre_y - radius_y), min(sample.height, centre_y + radius_y + 1))
        for x in range(max(0, centre_x - radius_x), min(sample.width, centre_x + radius_x + 1))
    ]
    focus_content = sum(patch) / len(patch) if patch else 0.0
    return 1.0 - content_fraction, content_fraction, focus_content


def _reference_scales(max_zoom: float) -> tuple[float, ...]:
    maximum = max(1.0, round(float(max_zoom), 2))
    values: list[float] = []
    index = 0
    while 1.0 + index * 0.02 <= maximum + 1e-9:
        values.append(round(1.0 + index * 0.02, 2))
        index += 1
    if not values or values[-1] != maximum:
        values.append(maximum)
    return tuple(values)


def _reference_prepare_fallback(
    src: Path,
    dest: Path,
    width: int,
    height: int,
    focus_x: float,
    focus_y: float,
) -> PreparedFrame:
    crop_to_vertical(src, dest, width, height, focus_x, focus_y)
    with Image.open(src) as image:
        box = _reference_legacy_box(image.width, image.height, width, height, focus_x, focus_y)
    with Image.open(dest) as prepared:
        blank_fraction, _content, _focus_content = _reference_content_stats(
            prepared, 0.5, 0.5
        )
    return PreparedFrame(dest, box, blank_fraction, 1.0)


def prepare_reference_frame(
    src: Path,
    dest: Path,
    width: int,
    height: int,
    focus_x: float,
    focus_y: float,
    profile: ReferenceProfileConfig | None,
    *,
    evidence: PanelVisualEvidence | Mapping[str, Any] | None = None,
    border_mask: framing_analysis.BorderMaskResult | None = None,
) -> PreparedFrame:
    """Select one static content-aware frame for the reference profile."""
    if profile is None:
        return _reference_prepare_fallback(src, dest, width, height, focus_x, focus_y)
    if evidence is None:
        raise RenderError(
            "visual.panel_lineage_unavailable: reference framing requires panel visual evidence",
            code="visual.panel_lineage_unavailable",
        )
    try:
        if isinstance(evidence, visual_scoring.PanelVisualEvidence):
            parsed_evidence = evidence
            visual_scoring.validate_panel_visual_evidence(parsed_evidence)
        else:
            parsed_evidence = visual_scoring.parse_panel_visual_evidence(evidence)
        parsed_evidence = visual_scoring.require_reference_ready_visual_evidence(parsed_evidence)
    except visual_scoring.VisualEvidenceError as exc:
        code = (
            "visual.balloon_mask_unknown"
            if exc.code == "visual.balloon_mask_unknown"
            else "visual.panel_lineage_unavailable"
        )
        raise RenderError(
            f"{code}: reference framing evidence is unavailable",
            code=code,
        ) from exc
    try:
        with Image.open(src) as original:
            image = original.convert("RGB")
            src_w, src_h = image.size
            if src_w < 2 or src_h < 2:
                raise ValueError("degenerate source geometry")
            if border_mask is None:
                border_mask = framing_analysis.build_color_agnostic_border_mask(
                    image,
                    parsed_evidence,
                    grid_long_edge=profile.framing_mask_grid_long_edge,
                )
            elif border_mask.source_width != src_w or border_mask.source_height != src_h:
                raise RenderError(
                    "reference framing mask does not match panel source",
                    code="visual.panel_lineage_unavailable",
                )
            if not framing_analysis.detector_contract_matches(
                profile.framing_contract_version,
                border_mask.detector_version,
            ):
                raise RenderError(
                    "visual.framing_contract_incompatible: detector/profile mismatch",
                    code="visual.framing_contract_incompatible",
                )
            target_ratio = width / height
            if src_w / src_h > target_ratio:
                base_w = max(2, min(src_w, int(round(src_h * target_ratio))))
                base_h = src_h
            else:
                base_w = src_w
                base_h = max(2, min(src_h, int(round(src_w / target_ratio))))
            output_size = (
                _reference_even(round(width * 1.15)),
                _reference_even(round(height * 1.15)),
            )
            candidates: list[tuple[tuple[float, ...], framing_analysis.FramingTelemetry]] = []
            last_telemetry: framing_analysis.FramingTelemetry | None = None
            focus_x = max(0.0, min(1.0, float(focus_x)))
            focus_y = max(0.0, min(1.0, float(focus_y)))
            for scale in _reference_scales(profile.base_frame_zoom_max):
                crop_w = max(2, min(base_w, _reference_even(round(base_w / scale))))
                crop_h = max(2, min(base_h, _reference_even(round(base_h / scale))))
                centre_x = focus_x * src_w
                centre_y = focus_y * src_h
                left = int(round(centre_x - crop_w / 2))
                top = int(round(centre_y - crop_h / 2))
                left = max(0, min(left, src_w - crop_w))
                top = max(0, min(top, src_h - crop_h))
                box = (left, top, left + crop_w, top + crop_h)
                box_centre_x = (left + crop_w / 2) / src_w
                box_centre_y = (top + crop_h / 2) / src_h
                focus_distance = min(
                    1.0,
                    ((box_centre_x - focus_x) ** 2 + (box_centre_y - focus_y) ** 2) ** 0.5,
                )
                focus_score = 1.0 - focus_distance
                feasible, telemetry = framing_analysis.candidate_is_feasible(
                    box,
                    parsed_evidence,
                    border_mask,
                    (src_w, src_h),
                    (width, height),
                )
                last_telemetry = telemetry
                if feasible:
                    balloon_zero = (
                        1.0
                        if telemetry.balloon_mask_intersection_ratio
                        <= profile.framing_balloon_intersection_max + 1e-9
                        else 0.0
                    )
                    rank = (
                        balloon_zero,
                        telemetry.protected_retained_fraction,
                        1.0 - telemetry.edge_connected_blank_fraction,
                        focus_score,
                        -telemetry.base_zoom,
                        float(top),
                        float(left),
                    )
                    candidates.append((rank, telemetry))
            if not candidates:
                rejection_code = (
                    last_telemetry.rejection_code
                    if last_telemetry is not None and last_telemetry.rejection_code
                    else "visual.crop_candidate_infeasible"
                )
                raise RenderError(
                    f"{rejection_code}: no reference framing candidate satisfies protected geometry",
                    code=rejection_code,
                    telemetry=last_telemetry,
                )
            _rank, telemetry = max(candidates, key=lambda item: item[0])
            box = telemetry.crop_box
            if telemetry.edge_connected_blank_fraction > profile.framing_blank_target_fraction + 1e-9:
                telemetry = replace(
                    telemetry,
                    fallback_reason="visual.blank_infeasible",
                    rejection_code="visual.blank_infeasible",
                )
                raise RenderError(
                    "visual.blank_infeasible: crop exceeds the strict edge-blank target",
                    code="visual.blank_infeasible",
                    telemetry=telemetry,
                )
            prepared = image.crop(box).resize(output_size, Image.Resampling.LANCZOS)
            dest.parent.mkdir(parents=True, exist_ok=True)
            prepared.save(dest, "JPEG", quality=94)
            with Image.open(dest) as saved:
                saved.load()
            return PreparedFrame(
                dest,
                box,
                telemetry.edge_connected_blank_fraction,
                round(min(float(profile.base_frame_zoom_max), telemetry.base_zoom), 3),
                telemetry,
            )
    except RenderError:
        raise
    except visual_scoring.VisualEvidenceError as exc:
        code = (
            "visual.balloon_mask_unknown"
            if exc.code == "visual.balloon_mask_unknown"
            else "visual.panel_lineage_unavailable"
        )
        raise RenderError(f"{code}: reference framing evidence is unavailable", code=code) from exc
    except (OSError, ValueError, ZeroDivisionError) as exc:
        raise RenderError(
            "visual.panel_lineage_unavailable: reference source is unavailable",
            code="visual.panel_lineage_unavailable",
        ) from exc


def editorial_frame(
    src: Path, dest: Path, width: int, height: int,
    focus_x: float, focus_y: float, end_x: float, end_y: float, mode: str,
    profile: ReferenceProfileConfig | None = None,
    *,
    evidence: PanelVisualEvidence | Mapping[str, Any] | None = None,
    border_mask: framing_analysis.BorderMaskResult | None = None,
) -> Path:
    """Build deterministic CPU compositing without altering panel geometry."""
    if profile is not None:
        return prepare_reference_frame(
            src,
            dest,
            width,
            height,
            focus_x,
            focus_y,
            profile,
            evidence=evidence,
            border_mask=border_mask,
        ).path
    if mode not in {"split_focus", "panel_stack"}:
        return crop_to_vertical(src, dest, width, height, focus_x, focus_y)
    W, H = round(width * 1.15), round(height * 1.15)
    with Image.open(src) as original:
        image = original.convert("RGB")
        bg = ImageOps.fit(image, (W, H), centering=(focus_x, focus_y)).filter(ImageFilter.GaussianBlur(14))
        bg = ImageEnhance.Brightness(bg).enhance(0.42)
        canvas = bg.copy()
        border = max(4, W // 180)
        if mode == "split_focus":
            half = (W - border) // 2
            left = ImageOps.fit(image, (half, H), centering=(focus_x, focus_y))
            right = ImageOps.fit(image, (half, H), centering=(end_x, end_y))
            canvas.paste(left, (0, 0))
            canvas.paste(right, (half + border, 0))
            ImageOps.expand(left, border=border, fill="white")
        else:
            main_h = int(H * 0.72)
            detail_h = H - main_h - border
            main = ImageOps.fit(image, (W, main_h), centering=(focus_x, focus_y))
            detail = ImageOps.fit(image, (W, detail_h), centering=(end_x, end_y))
            canvas.paste(main, (0, 0))
            canvas.paste(detail, (0, main_h + border))
        dest.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(dest, "JPEG", quality=94)
    return dest


def placeholder_image(dest: Path, width: int, height: int, text: str = "") -> Path:
    """Solid dark frame used when a scene has no image."""
    from PIL import ImageDraw

    img = Image.new("RGB", (int(width * 1.15), int(height * 1.15)), (18, 18, 24))
    if text:
        draw = ImageDraw.Draw(img)
        draw.text((60, int(height * 0.45)), text[:60], fill=(140, 140, 160))
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=90)
    return dest


def _motion_filter(
    effect: str, width: int, height: int, duration: float, fps: int,
    focus_x: float = 0.5, focus_y: float = 0.4,
    focus_end_x: float = 0.5, focus_end_y: float = 0.4,
    profile: ReferenceProfileConfig | None = None,
) -> str:
    """Build one smooth, bounded crop trajectory with even coordinates."""
    frames = max(2, int(round(duration * fps)))
    last = frames - 1
    static = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
    safe_effect = motion_director.safe_camera_curve(effect)
    if safe_effect == "static":
        return static

    # Interpolate between ranked ROIs. Smoothstep never reverses direction.
    progress = f"(n/{last})"
    smooth = f"({progress}*{progress}*(3-2*{progress}))"
    fx = f"((1-{smooth})*{max(0.05, min(0.95, focus_x))}+{smooth}*{max(0.05, min(0.95, focus_end_x))})"
    fy = f"((1-{smooth})*{max(0.05, min(0.95, focus_y))}+{smooth}*{max(0.05, min(0.95, focus_end_y))})"
    normal_delta = (profile.normal_zoom_max - 1.0) if profile else 0.06
    impact_delta = (profile.impact_zoom_max - 1.0) if profile else 0.08
    if safe_effect == "slow_push_in":
        z = f"(1+{normal_delta:.2f}*{smooth})"
    elif safe_effect == "slow_pull_out":
        z = f"({1.0 + normal_delta:.2f}-{normal_delta:.2f}*{smooth})"
    elif safe_effect in {"push_in", "reveal"}:
        z = f"(1+{impact_delta:.2f}*{smooth})"
    elif safe_effect == "static_emphasis":
        z = f"(1+{normal_delta * 0.45:.3f}*{smooth})"
    elif safe_effect == "atmospheric":
        z = f"(1+{normal_delta * 0.55:.3f}*{smooth})"
    else:
        z = f"(1+{normal_delta:.2f}*{smooth})"
    crop_w = f"floor(iw/{z}/2)*2"
    crop_h = f"floor(ih/{z}/2)*2"
    x_raw = f"floor(((iw-{crop_w})*{fx})/2)*2"
    y_raw = f"floor(((ih-{crop_h})*{fy})/2)*2"
    x = f"max(0,min(iw-{crop_w},{x_raw}))"
    y = f"max(0,min(ih-{crop_h},{y_raw}))"
    return f"crop=w='{crop_w}':h='{crop_h}':x='{x}':y='{y}',scale={width}:{height}:flags=lanczos"


def _procedural_effect(
    mode: str,
    intensity: str,
    profile: ReferenceProfileConfig | None = None,
) -> str:
    """Small deterministic accents; never injects content into the panel."""
    if profile is not None:
        return "null"
    if mode == "atmospheric":
        return "eq=saturation=0.78:contrast=1.04,vignette=PI/5"
    if mode == "impact":
        contrast = {"low": 1.06, "medium": 1.12, "high": 1.18}.get(intensity, 1.12)
        return f"eq=contrast={contrast}:brightness=0.025"
    if mode == "static_emphasis":
        return "vignette=PI/8"
    return "null"


_LOCAL_EFFECTS = {
    "atmospheric": ("smoke_fog", "rain"),
    "impact": ("glow", "flash", "embers"),
    "guided_pan": ("speed_lines", "dust"),
    "panel_stack": ("speed_lines", "dust"),
}


def local_effects(
    mode: str,
    disabled: list[str] | None = None,
    profile: ReferenceProfileConfig | None = None,
) -> tuple[str, ...]:
    if profile is not None:
        return ()
    disabled_set = {str(item).strip().lower() for item in (disabled or [])}
    return tuple(effect for effect in _LOCAL_EFFECTS.get(mode, ()) if effect not in disabled_set)


def apply_local_effects(image: Image.Image, effects: tuple[str, ...], intensity: str = "low", seed: int = 42) -> Image.Image:
    """Deterministic edge-safe accents; source panel geometry stays unchanged."""
    if not effects:
        return image
    from PIL import ImageDraw

    out = image.convert("RGBA").copy()
    draw = ImageDraw.Draw(out, "RGBA")
    rng = Random(seed)
    alpha = {"low": 28, "medium": 48, "high": 72}.get(intensity, 28)
    width, height = out.size
    margin_x, margin_y = max(8, width // 12), max(8, height // 12)
    count = {"low": 10, "medium": 18, "high": 28}.get(intensity, 10)
    if "speed_lines" in effects:
        for _ in range(count):
            y = rng.randint(margin_y, max(margin_y, height - margin_y))
            draw.line((0, y, margin_x, max(0, y - rng.randint(8, 40))), fill=(255, 255, 255, alpha), width=2)
    if "dust" in effects or "embers" in effects:
        colour = (220, 180, 120, alpha) if "embers" in effects else (190, 190, 190, alpha)
        for _ in range(count):
            x = rng.choice([rng.randint(0, margin_x), rng.randint(width - margin_x, width - 1)])
            y = rng.randint(margin_y, max(margin_y, height - margin_y * 2))
            radius = rng.randint(2, 6)
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=colour)
    if "smoke_fog" in effects:
        draw.ellipse((0, height // 3, margin_x * 2, height // 2), fill=(220, 220, 220, alpha // 2))
    if "rain" in effects:
        for _ in range(count):
            x, y = rng.randint(0, width - 1), rng.randint(margin_y, max(margin_y, height - margin_y * 2))
            draw.line((x, y, x - 4, y + 18), fill=(170, 200, 235, alpha), width=1)
    if "glow" in effects:
        glow = Image.new("RGBA", out.size, (255, 220, 120, alpha // 2))
        out = Image.alpha_composite(out, glow)
    if "flash" in effects:
        flash = Image.new("RGBA", out.size, (255, 245, 220, alpha // 2))
        out = Image.alpha_composite(out, flash)
    return out.convert("RGB")


def render_scene_clip(
    scene: SceneInput,
    prepared_image: Path,
    dest: Path,
    width: int,
    height: int,
    fps: int,
    encoder: encoders.Selection | None = None,
    preview: bool = False,
    profile: ReferenceProfileConfig | None = None,
) -> Path:
    """Render one silent scene clip.

    ``encoder`` selects CPU or GPU encoding; when omitted the configured default
    is resolved, so callers and tests can stay unaware of the choice.
    """
    selection = encoder or encoders.select()
    duration = scene.duration
    frames = max(2, int(round(duration * fps)))
    motion = (
        _motion_filter(
            scene.camera_curve or scene.effect, width, height, duration, fps,
            scene.focus_x, scene.focus_y, scene.focus_end_x, scene.focus_end_y,
            profile=profile,
        )
        if settings.motion_enabled
        else f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
    )
    vf = f"{motion},{_procedural_effect(scene.motion_mode, scene.motion_intensity, profile)},format=yuv420p"

    # The Shot Director owns transition intent. Do not fade every clip: that
    # creates a black flash between ROI cuts and makes the edit feel mechanical.
    if scene.transition == "fade":
        fade = min(_SECTION_TRANSITION_MAX, max(_SECTION_TRANSITION_MIN, duration / 4))
        if fade > 0.05:
            vf += f",fade=t=in:st=0:d={fade:.2f}"

    # VAAPI encodes from GPU surfaces, so the chain must end with an upload.
    vf = encoders.apply_filter_suffix(selection, vf)

    _run(
        [
            settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
            *encoders.input_args(selection),
            # -t must NOT be an input option here: zoompan expands every input
            # frame into d frames, so limiting the input to `duration` seconds
            # of looped stills multiplies the output length. Cap the output with
            # -frames:v instead, which yields exactly the frames we want.
            "-framerate", str(fps), "-loop", "1",
            "-i", str(prepared_image),
            "-vf", vf,
            "-r", str(fps),
            "-frames:v", str(frames),
            *encoders.video_args(selection, preview=preview, final=not preview),
            str(dest),
        ],
        timeout=600,
        step="scene_render",
    )
    return dest


def join_scene_clips(
    clips: list[Path], scenes: list[SceneInput], dest: Path, fps: int,
    encoder: encoders.Selection | None = None, preview: bool = False,
) -> Path:
    """Join directed clips with exact cuts or duration-preserving dissolves.

    A fade is built from the outgoing tail and incoming head, then concatenated
    with the untouched bodies. No chained ``xfade`` timestamps, black flash, or
    cumulative duration drift. The Shot Director still owns which boundaries fade.
    """
    if not clips or len(clips) != len(scenes):
        raise RenderError("scene clips and scene plan do not match", code="join_mismatch")
    selection = encoder or encoders.select()
    durations = [scene.duration for scene in scenes]
    frame_counts = [max(1, int(round(duration * fps))) for duration in durations]
    transitions = [
        min(max(1, int(round(_SECTION_TRANSITION_MAX * fps))), frame_counts[index], frame_counts[index + 1])
        if index + 1 < len(scenes) and scenes[index + 1].transition == "fade"
        else 0
        for index in range(len(scenes))
    ]
    graph: list[str] = []
    segments: list[str] = []

    def part(index: int, start_frame: int, end_frame: int) -> str | None:
        if end_frame <= start_frame:
            return None
        label = f"part{len(segments)}"
        graph.append(
            f"[{index}:v]trim=start_frame={start_frame}:end_frame={end_frame},"
            f"settb=AVTB,setpts=PTS-STARTPTS,fps={fps},format=yuv420p,setsar=1[{label}]"
        )
        segments.append(label)
        return label

    for index, frame_count in enumerate(frame_counts):
        before = transitions[index - 1] if index else 0.0
        after = transitions[index]
        part(index, int(before), max(int(before), frame_count - int(after)))
        if after:
            tail = f"tail{index}"
            head = f"head{index + 1}"
            transition = f"transition{index}"
            graph.extend(
                [
                    f"[{index}:v]trim=start_frame={frame_count - after}:end_frame={frame_count},"
                    f"settb=AVTB,setpts=PTS-STARTPTS,fps={fps},format=yuv420p,setsar=1[{tail}]",
                    f"[{index + 1}:v]trim=start_frame=0:end_frame={after},"
                    f"settb=AVTB,setpts=PTS-STARTPTS,fps={fps},format=yuv420p,setsar=1[{head}]",
                    f"[{tail}][{head}]xfade=transition=fade:duration={after / fps:.6f}:"
                    f"offset=0[{transition}]",
                ]
            )
            segments.append(transition)

    if not segments:
        raise RenderError("scene clips have no renderable duration", code="join_empty")
    joined = "joined"
    graph.append(f"{''.join(f'[{label}]' for label in segments)}concat=n={len(segments)}:v=1:a=0[{joined}]")
    # xfade overlaps two source tails by its duration. Restore those frames at
    # the end, then trim to the audio-locked plan length.
    total = sum(durations)
    overlap = sum(transitions) / fps
    graph.append(
        f"[{joined}]tpad=stop_mode=clone:stop_duration={overlap:.6f},"
        f"trim=duration={total:.3f}[joined_exact]"
    )
    filter_graph = encoders.apply_filter_suffix(selection, ";".join(graph))
    cmd = [settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error"]
    for clip in clips:
        cmd += ["-i", str(clip)]
    cmd += [
        "-filter_complex", filter_graph, "-map", "[joined_exact]", "-an",
        *encoders.video_args(selection, preview=preview, final=not preview), str(dest),
    ]
    try:
        _run(cmd, timeout=900, step="concat")
    except RenderError:
        # Safe fallback: preserve every frame with hard cuts when an xfade graph
        # cannot be built for a particular clip combination.
        manifest = dest.with_suffix(".concat.txt")
        manifest.write_text("\n".join(f"file '{clip}'" for clip in clips) + "\n", encoding="utf-8")
        _run(
            [settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
             "-f", "concat", "-safe", "0", "-i", str(manifest),
             "-c", "copy", "-movflags", "+faststart", str(dest)],
            timeout=900, step="concat_fallback",
        )
    return dest


# --- subtitles -------------------------------------------------------------


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, rem = divmod(int(seconds), 3600)
    minutes, secs = divmod(rem, 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis == 100:
        secs += 1
        centis = 0
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def build_ass(
    cues: list[CueSpec],
    width: int,
    height: int,
    font_name: str = "DejaVu Sans",
    max_chars: int = MAX_SUBTITLE_CHARS_PER_LINE,
    profile: ReferenceProfileConfig | None = None,
) -> str:
    """Generate an ASS subtitle file positioned inside the Shorts safe area.

    Bottom margin keeps text clear of the YouTube UI overlay; a heavy outline
    keeps it readable over busy artwork.
    """
    if profile is not None:
        font_size = max(1, round(height * profile.caption_font_height_ratio))
        anchor_x = round(width * profile.caption_anchor[0])
        anchor_y = round(height * profile.caption_anchor[1])
        italic = -1 if profile.caption_italic else 0
        shadow_alpha = round((1.0 - profile.caption_shadow_alpha_max) * 255)
        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font_name},{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H{shadow_alpha:02X}000000,-1,{italic},0,0,100,100,0,0,1,{profile.caption_outline_pixels},2,{profile.caption_alignment},0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        lines: list[str] = []
        for cue in cues:
            display_text = cue.text.strip()
            if (
                not display_text
                or len(display_text.split()) != profile.caption_words_per_cue
                or display_text != display_text.upper()
                or not display_text.isalnum()
                or cue.end_time <= cue.start_time
            ):
                raise RenderError(
                    "reference subtitle must be one uppercase punctuation-free word",
                    code="reference.subtitle_invalid",
                )
            lines.append(
                f"Dialogue: 0,{_ass_time(cue.start_time)},{_ass_time(cue.end_time)},"
                f"Caption,,0,0,0,,{{\\pos({anchor_x},{anchor_y})}}"
                f"{_ass_escape(display_text)}"
            )
        return header + "\n".join(lines) + "\n"

    font_size = max(42, int(height * 0.045))
    margin_v = int(height * SUBTITLE_SAFE_BOTTOM)
    margin_h = int(width * 0.08)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines: list[str] = []
    for cue in cues:
        if not cue.text.strip() or cue.end_time <= cue.start_time:
            continue
        words = re.findall(r"\S+", cue.text)
        if not words:
            continue
        duration = cue.end_time - cue.start_time
        weights = [max(1, len(re.sub(r"[^\w']", "", word))) for word in words]
        total = sum(weights)
        offsets = [cue.start_time]
        for weight in weights:
            offsets.append(offsets[-1] + duration * weight / total)

        # Reveal words progressively. The current spoken word is yellow; words
        # already spoken stay white. Uppercase improves phone-size scanability.
        display_text = cue.text.upper()
        wrapped_words = wrap_caption(display_text, max_chars)
        for index, (start, end) in enumerate(zip(offsets, offsets[1:], strict=False)):
            rendered: list[str] = []
            word_index = 0
            for line in wrapped_words:
                parts: list[str] = []
                for word in line.split():
                    colour = "\\c&H0000FFFF&" if word_index == index else "\\c&H00FFFFFF&"
                    parts.append(f"{{{colour}}}{_ass_escape(word)}")
                    word_index += 1
                if parts:
                    rendered.append(" ".join(parts) + "{\\c&H00FFFFFF&}")
            highlighted = "\\N".join(rendered)
            lines.append(
                f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},"
                f"Caption,,0,0,0,,{highlighted}"
            )
    return header + "\n".join(lines) + "\n"


def _load_karaoke_layout_font(font_size: int) -> object | None:
    try:
        font_path = Path(settings.subtitle_font)
        if font_path.is_file():
            return ImageFont.truetype(str(font_path), font_size)
    except OSError:
        pass
    return None


def _require_karaoke_layout_font(font_size: int, font_name: str) -> object:
    """Load the exact checked-in font and reject aliases/fallbacks."""

    font = _load_karaoke_layout_font(font_size)
    if font is None:
        raise RenderError(
            "subtitle font file is unavailable",
            code="reference.subtitle_font_unavailable",
        )
    try:
        embedded_family = str(font.getname()[0])
    except (AttributeError, OSError, TypeError, IndexError) as exc:
        raise RenderError(
            "subtitle font identity cannot be verified",
            code="reference.subtitle_font_unavailable",
        ) from exc
    if font_name != embedded_family:
        raise RenderError(
            f"subtitle_font_mismatch: requested {font_name!r}, embedded family is {embedded_family!r}",
            code="reference.subtitle_font_mismatch",
        )
    return font


def _karaoke_line_layout(
    words: Sequence[str],
    *,
    layout_font: object | None,
    safe_text_width: int,
    max_chars: int,
    max_lines: int,
    active_scale: float,
) -> tuple[str, ...]:
    """Return a deterministic one/two-line layout using real font metrics."""

    def line_metrics(start: int, end: int) -> tuple[str, float]:
        text = " ".join(words[start:end])
        if layout_font is None:
            return text, float(len(text))
        getlength = layout_font.getlength
        base = float(getlength(text))
        active_bump = (active_scale - 1.0) * max(
            (float(getlength(word)) for word in words[start:end]),
            default=0.0,
        )
        return text, base + active_bump

    def acceptable(start: int, end: int) -> bool:
        text, width_value = line_metrics(start, end)
        return len(text) <= max_chars and width_value <= safe_text_width

    if acceptable(0, len(words)):
        return (" ".join(words),)
    if max_lines < 2:
        raise RenderError(
            "subtitle overflow: rendered sentence cannot fit the safe width",
            code="reference.subtitle_overflow",
        )
    candidates: list[tuple[tuple[float, float, tuple[int, ...]], tuple[str, ...]]] = []
    for split in range(2, len(words) - 1):
        if not acceptable(0, split) or not acceptable(split, len(words)):
            continue
        first, first_width = line_metrics(0, split)
        second, second_width = line_metrics(split, len(words))
        candidates.append(
            (
                (
                    max(first_width, second_width),
                    abs(first_width - second_width),
                    (split,),
                ),
                (first, second),
            )
        )
    if not candidates:
        raise RenderError(
            "subtitle overflow: rendered sentence cannot fit the safe width",
            code="reference.subtitle_overflow",
        )
    return min(candidates, key=lambda candidate: candidate[0])[1]


def fit_sentence_karaoke_groups(
    groups: Sequence[KaraokeSentenceGroup],
    width: int,
    height: int,
    *,
    max_chars: int = subtitle_karaoke.CAPTION_MAX_CHARS,
    max_lines: int = subtitle_karaoke.CAPTION_MAX_LINES,
    active_scale: float = subtitle_karaoke.CAPTION_ACTIVE_SCALE,
    font_height_ratio: float = subtitle_karaoke.CAPTION_FONT_HEIGHT_RATIO,
    outline_pixels: int = 6,
    safe_margin_px: int = subtitle_karaoke.CAPTION_SAFE_MARGIN_PX,
) -> tuple[KaraokeSentenceGroup, ...]:
    """Split logical groups that pass char limits but cannot fit actual pixels.

    The operation only changes display chunk boundaries. Word text and timing
    remain immutable, and every resulting chunk contains at least two words.
    """

    if width <= 0 or height <= 0 or max_chars <= 0 or not 0 < max_lines <= 2:
        raise RenderError(
            "subtitle layout dimensions are invalid",
            code="reference.subtitle_layout_invalid",
        )
    if not 1.0 < active_scale <= 1.25 or not math.isfinite(active_scale):
        raise RenderError("subtitle active scale is invalid", code="reference.subtitle_scale_invalid")
    if not 0.0 < font_height_ratio <= 0.2 or not math.isfinite(font_height_ratio):
        raise RenderError("subtitle font ratio is invalid", code="reference.subtitle_layout_invalid")
    if safe_margin_px * 2 >= width or outline_pixels < 0:
        raise RenderError("subtitle style is invalid", code="reference.subtitle_layout_invalid")

    font_size = max(1, round(height * font_height_ratio))
    layout_font = _require_karaoke_layout_font(font_size, settings.subtitle_font_name)
    safe_text_width = width - (2 * safe_margin_px) - (2 * outline_pixels)

    def layout(words: Sequence[KaraokeWord]) -> tuple[str, ...]:
        return _karaoke_line_layout(
            [word.text for word in words],
            layout_font=layout_font,
            safe_text_width=safe_text_width,
            max_chars=max_chars,
            max_lines=max_lines,
            active_scale=active_scale,
        )

    def width_of(words: Sequence[KaraokeWord]) -> float:
        lines = layout(words)
        if layout_font is None:
            return float(max(map(len, lines), default=0))
        getlength = layout_font.getlength
        return max(
            float(getlength(line))
            + (active_scale - 1.0)
            * max((float(getlength(word)) for word in line.split()), default=0.0)
            for line in lines
        )

    def split_group(group: KaraokeSentenceGroup) -> tuple[KaraokeSentenceGroup, ...]:
        try:
            layout(group.words)
            return (group,)
        except RenderError as exc:
            if exc.code != "reference.subtitle_overflow":
                raise

        words = tuple(group.words)
        if len(words) < 4:
            raise RenderError(
                "subtitle overflow: rendered sentence cannot fit the safe width",
                code="reference.subtitle_overflow",
            )
        memo: dict[int, tuple[tuple[KaraokeWord, ...], ...] | None] = {}

        def solve(start: int) -> tuple[tuple[KaraokeWord, ...], ...] | None:
            if start == len(words):
                return ()
            if start in memo:
                return memo[start]
            candidates: list[tuple[tuple[float, float, tuple[int, ...]], tuple[tuple[KaraokeWord, ...], ...]]] = []
            for end in range(start + 2, len(words) + 1):
                if len(words) - end == 1:
                    continue
                chunk = words[start:end]
                try:
                    layout(chunk)
                except RenderError as exc:
                    if exc.code == "reference.subtitle_overflow":
                        continue
                    raise
                display_lines = wrap_caption(
                    " ".join(str(word.text) for word in chunk),
                    max_chars,
                )
                if len(display_lines) > max_lines or (
                    len(display_lines) > 1
                    and any(len(line.split()) < 2 for line in display_lines)
                ):
                    # The shared display contract (validate_sentence_groups)
                    # requires every rendered line to keep at least two words.
                    continue
                remainder = solve(end)
                if remainder is None:
                    continue
                parts = (chunk,) + remainder
                target = len(words) / len(parts)
                score = (
                    float(len(parts)),
                    max(width_of(part) for part in parts),
                    sum(abs(len(part) - target) for part in parts),
                    tuple(len(part) for part in parts),
                )
                candidates.append((score, parts))
            memo[start] = min(candidates, key=lambda item: item[0])[1] if candidates else None
            return memo[start]

        parts = solve(0)
        if parts is None:
            raise RenderError(
                "subtitle overflow: rendered sentence cannot fit the safe width",
                code="reference.subtitle_overflow",
            )
        return tuple(
            KaraokeSentenceGroup(
                group_id=f"{group.group_id}-chunk-{index}",
                words=part,
                start_time=group.start_time if index == 1 else part[0].start_time,
                end_time=group.end_time if index == len(parts) else part[-1].end_time,
            )
            for index, part in enumerate(parts, start=1)
        )

    logical_groups: list[KaraokeSentenceGroup] = []
    for group in groups:
        sentence_id = re.sub(r"-chunk-\d+$", "", group.group_id)
        if logical_groups:
            previous = logical_groups[-1]
            previous_id = re.sub(r"-chunk-\d+$", "", previous.group_id)
            if sentence_id == previous_id:
                logical_groups[-1] = KaraokeSentenceGroup(
                    group_id=previous.group_id,
                    words=previous.words + group.words,
                    start_time=previous.start_time,
                    end_time=group.end_time,
                )
                continue
        logical_groups.append(group)

    prepared: list[KaraokeSentenceGroup] = []
    for group in logical_groups:
        prepared.extend(split_group(group))
    return tuple(prepared)


def build_sentence_karaoke_ass(
    groups: Sequence[KaraokeSentenceGroup],
    width: int,
    height: int,
    *,
    font_name: str | None = None,
    max_chars: int = subtitle_karaoke.CAPTION_MAX_CHARS,
    max_lines: int = 2,
    active_scale: float = 1.08,
    anchor: tuple[float, float] = (0.50, 0.56),
    font_height_ratio: float = 0.04,
    italic: bool = True,
    outline_pixels: int = 6,
    shadow_alpha_max: float = 0.35,
    alignment: int = 5,
    safe_margin_px: int = 120,
) -> str:
    """Build sentence-held ASS karaoke from authoritative word timings.

    Every event contains the complete punctuation-free sentence. Only the word
    whose interval owns that event is yellow and slightly enlarged; the next
    sentence replaces the complete block at its own first word boundary.
    """
    font_name = font_name or settings.subtitle_font_name
    if width <= 0 or height <= 0 or max_chars <= 0 or not 0 < max_lines <= 2:
        raise RenderError("subtitle layout dimensions are invalid", code="reference.subtitle_layout_invalid")
    if not 1.0 < active_scale <= 1.25 or not math.isfinite(active_scale):
        raise RenderError("subtitle active scale is invalid", code="reference.subtitle_scale_invalid")
    if not 0.0 <= anchor[0] <= 1.0 or not 0.0 <= anchor[1] <= 1.0:
        raise RenderError("subtitle anchor is invalid", code="reference.subtitle_layout_invalid")
    if not 0.0 < font_height_ratio <= 0.2 or not math.isfinite(font_height_ratio):
        raise RenderError("subtitle font ratio is invalid", code="reference.subtitle_layout_invalid")
    if (
        not 0 <= alignment <= 9
        or outline_pixels < 0
        or not 0.0 <= shadow_alpha_max <= 1.0
        or safe_margin_px < 0
        or safe_margin_px * 2 >= width
    ):
        raise RenderError("subtitle style is invalid", code="reference.subtitle_layout_invalid")

    font_size = max(1, round(height * font_height_ratio))
    italic_flag = -1 if italic else 0
    shadow_alpha = round((1.0 - shadow_alpha_max) * 255)
    active_percent = round(active_scale * 100)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font_name},{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H{shadow_alpha:02X}000000,-1,{italic_flag},0,0,100,100,0,0,1,{outline_pixels},2,{alignment},{safe_margin_px},{safe_margin_px},0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines: list[str] = []
    previous_group_end = 0.0
    prepared_groups = fit_sentence_karaoke_groups(
        groups,
        width,
        height,
        max_chars=max_chars,
        max_lines=max_lines,
        active_scale=active_scale,
        font_height_ratio=font_height_ratio,
        outline_pixels=outline_pixels,
        safe_margin_px=safe_margin_px,
    )
    layout_font = _require_karaoke_layout_font(font_size, font_name)
    safe_text_width = width - (2 * safe_margin_px) - (2 * outline_pixels)

    for group in prepared_groups:
        if group.start_time < previous_group_end:
            raise RenderError(
                "subtitle sentence groups overlap",
                code="reference.subtitle_timing_invalid",
            )
        previous_group_end = group.end_time
        display_words = [word.text for word in group.words]
        if len(display_words) < 2:
            raise RenderError(
                "subtitle overflow: sentence chunks require at least two words",
                code="reference.subtitle_overflow",
            )
        wrapped = _karaoke_line_layout(
            display_words,
            layout_font=layout_font,
            safe_text_width=safe_text_width,
            max_chars=max_chars,
            max_lines=max_lines,
            active_scale=active_scale,
        )
        if len(wrapped) > max_lines or any(len(line.split()) < 2 for line in wrapped[1:]):
            raise RenderError(
                "subtitle overflow: sentence exceeds the configured line budget",
                code="reference.subtitle_overflow",
            )
        for active_index, word in enumerate(group.words):
            rendered: list[str] = []
            word_index = 0
            for line in wrapped:
                parts: list[str] = []
                for display_word in line.split():
                    if word_index == active_index:
                        tag = f"\\c&H0000FFFF&\\fscx{active_percent}\\fscy{active_percent}"
                    else:
                        tag = "\\c&H00FFFFFF&\\fscx100\\fscy100"
                    parts.append(
                        f"{{{tag}}}{_ass_escape(display_word)}"
                        "{\\c&H00FFFFFF&\\fscx100\\fscy100}"
                    )
                    word_index += 1
                rendered.append(" ".join(parts))
            display_text = "\\N".join(rendered)
            lines.append(
                f"Dialogue: 0,{_ass_time(word.start_time)},{_ass_time(word.end_time)},"
                f"Caption,,0,0,0,,{display_text}"
            )
    return header + "\n".join(lines) + "\n"


def _subtitle_manifest_evidence(
    groups: Sequence[KaraokeSentenceGroup],
    *,
    profile: ReferenceProfileConfig | None = None,
    timing_source: str = "audio_segment.word_timings",
) -> dict[str, Any]:
    """Summarize measured subtitle facts for regular-render audit manifests."""
    width, height = 1080, 1920
    font_size = round(height * subtitle_karaoke.CAPTION_FONT_HEIGHT_RATIO)
    font_name = settings.subtitle_font_name
    font = _require_karaoke_layout_font(font_size, font_name)
    safe_width = width - (2 * subtitle_karaoke.CAPTION_SAFE_MARGIN_PX) - 12
    fitted = fit_sentence_karaoke_groups(
        groups,
        width,
        height,
        max_chars=subtitle_karaoke.CAPTION_MAX_CHARS,
        max_lines=subtitle_karaoke.CAPTION_MAX_LINES,
        active_scale=subtitle_karaoke.CAPTION_ACTIVE_SCALE,
        font_height_ratio=subtitle_karaoke.CAPTION_FONT_HEIGHT_RATIO,
        safe_margin_px=subtitle_karaoke.CAPTION_SAFE_MARGIN_PX,
    )
    measured_widths: list[float] = []
    measured_lines: list[tuple[str, ...]] = []
    for group in fitted:
        lines = _karaoke_line_layout(
            [word.text for word in group.words],
            layout_font=font,
            safe_text_width=safe_width,
            max_chars=subtitle_karaoke.CAPTION_MAX_CHARS,
            max_lines=subtitle_karaoke.CAPTION_MAX_LINES,
            active_scale=subtitle_karaoke.CAPTION_ACTIVE_SCALE,
        )
        measured_lines.append(lines)
        for line in lines:
            words = line.split()
            widest_active = max((float(font.getlength(word)) for word in words), default=0.0)
            measured_widths.append(
                float(font.getlength(line))
                + (subtitle_karaoke.CAPTION_ACTIVE_SCALE - 1.0) * widest_active
            )
    maximum_width = max(measured_widths, default=0.0)
    font_path = Path(settings.subtitle_font)
    return {
        "max_lines_measured": max((len(lines) for lines in measured_lines), default=0),
        "active_word_events": sum(len(group.words) for group in groups),
        "display_word_count": sum(len(group.words) for group in groups),
        "timing_source": timing_source,
        "spoken_text_immutable": True,
        "contract_version": subtitle_karaoke.SUBTITLE_CONTRACT_VERSION,
        "profile_id": getattr(profile, "profile_id", None),
        "font_name": font_name,
        "font_file_sha256": hashlib.sha256(font_path.read_bytes()).hexdigest(),
        "max_active_text_width_px": round(maximum_width, 3),
        "safe_text_width_px": safe_width,
        "minimum_horizontal_clearance_px": round((width - maximum_width) / 2.0, 3),
    }


_REFERENCE_TELEMETRY_FIELDS = (
    "contract_version",
    "detector_version",
    "mask_sha256",
    "crop_box",
    "base_zoom",
    "source_resolution_zoom_cap",
    "protected_region_zoom_cap",
    "edge_connected_blank_fraction",
    "non_discardable_low_information_fraction",
    "protected_retained_fraction",
    "balloon_mask_intersection_ratio",
    "subject_coverage",
    "face_coverage",
    "action_coverage",
    "effect_coverage",
    "continuity_context_coverage",
    "mask_confidence",
    "mask_source",
    "fallback_reason",
    "rejection_code",
)


def _reference_canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _reference_border_mask_from_mapping(
    value: framing_analysis.BorderMaskResult | Mapping[str, Any] | None,
) -> framing_analysis.BorderMaskResult:
    if isinstance(value, framing_analysis.BorderMaskResult):
        mask = value
    elif isinstance(value, Mapping):
        try:
            rows = ("edge_connected_mask", "non_discardable_low_information_mask", "protected_mask")
            parsed_rows: dict[str, tuple[tuple[bool, ...], ...]] = {}
            for name in rows:
                raw_rows = value[name]
                if not isinstance(raw_rows, (list, tuple)):
                    raise ValueError("mask rows are invalid")
                converted: list[tuple[bool, ...]] = []
                for raw_row in raw_rows:
                    if not isinstance(raw_row, (list, tuple)) or any(
                        not isinstance(flag, bool) for flag in raw_row
                    ):
                        raise ValueError("mask cells are invalid")
                    converted.append(tuple(raw_row))
                parsed_rows[name] = tuple(converted)
            mask = framing_analysis.BorderMaskResult(
                detector_version=str(value["detector_version"]),
                source_width=int(value["source_width"]),
                source_height=int(value["source_height"]),
                grid_width=int(value["grid_width"]),
                grid_height=int(value["grid_height"]),
                edge_connected_mask=parsed_rows[rows[0]],
                non_discardable_low_information_mask=parsed_rows[rows[1]],
                protected_mask=parsed_rows[rows[2]],
                edge_connected_blank_fraction=float(value["edge_connected_blank_fraction"]),
                non_discardable_low_information_fraction=float(
                    value["non_discardable_low_information_fraction"]
                ),
                protected_retained_fraction=float(value["protected_retained_fraction"]),
                mask_sha256=str(value["mask_sha256"]),
            )
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise RenderError(
                "visual.panel_lineage_unavailable: reference border mask is malformed",
                code="visual.panel_lineage_unavailable",
            ) from exc
    else:
        raise RenderError(
            "visual.panel_lineage_unavailable: reference border mask is missing",
            code="visual.panel_lineage_unavailable",
        )
    try:
        expected_hash = framing_analysis._mask_hash(
            mask.source_width,
            mask.source_height,
            mask.grid_width,
            mask.grid_height,
            mask.edge_connected_mask,
            mask.non_discardable_low_information_mask,
            mask.protected_mask,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise RenderError(
            "visual.panel_lineage_unavailable: reference border mask identity is invalid",
            code="visual.panel_lineage_unavailable",
        ) from exc
    if mask.mask_sha256 != expected_hash:
        raise RenderError(
            "visual.panel_lineage_unavailable: reference border mask identity is stale",
            code="visual.panel_lineage_unavailable",
        )
    return mask


def _reference_telemetry_mapping(value: object) -> Mapping[str, Any]:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, Mapping):
        return value
    raise RenderError(
        "visual.panel_lineage_unavailable: reference framing telemetry is missing",
        code="visual.panel_lineage_unavailable",
    )


def _prepare_exact_reference_frame(
    *,
    scene: SceneInput,
    dest: Path,
    width: int,
    height: int,
    profile: ReferenceProfileConfig,
    allow_source_resolution_warning: bool = False,
) -> Path:
    """Prepare the persisted ROI exactly, without candidate search or reselection."""
    if scene.publish_allowed is not False:
        raise RenderError(
            "reference.publish_not_allowed: publish_allowed must be false for silent review scenes",
            code="reference.publish_not_allowed",
        )
    if not scene.image_path or not Path(scene.image_path).is_file():
        raise RenderError(
            "visual.panel_lineage_unavailable: reference panel crop is missing",
            code="visual.panel_lineage_unavailable",
        )
    try:
        evidence = (
            scene.visual_evidence
            if isinstance(scene.visual_evidence, visual_scoring.PanelVisualEvidence)
            else visual_scoring.parse_panel_visual_evidence(scene.visual_evidence or {})
        )
        evidence = visual_scoring.require_reference_ready_visual_evidence(evidence)
        local_hash = visual_scoring.visual_evidence_hash(evidence)
    except visual_scoring.VisualEvidenceError as exc:
        code = (
            "visual.balloon_mask_unknown"
            if exc.code == "visual.balloon_mask_unknown"
            else "visual.panel_lineage_unavailable"
        )
        raise RenderError(f"{code}: reference visual evidence is unavailable", code=code) from exc
    if not scene.evidence_hash or scene.evidence_hash != local_hash:
        raise RenderError(
            "visual.panel_lineage_unavailable: reference evidence hash is stale",
            code="visual.panel_lineage_unavailable",
        )
    mask = _reference_border_mask_from_mapping(scene.border_mask)
    try:
        with Image.open(scene.image_path) as source:
            source.load()
            panel = source.convert("RGB")
    except (OSError, ValueError) as exc:
        raise RenderError(
            "visual.panel_lineage_unavailable: reference panel crop is unreadable",
            code="visual.panel_lineage_unavailable",
        ) from exc
    panel_size = scene.panel_size
    if (
        not isinstance(panel_size, tuple)
        or len(panel_size) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in panel_size)
        or panel.size != panel_size
        or (mask.source_width, mask.source_height) != panel_size
    ):
        raise RenderError(
            "visual.panel_lineage_unavailable: reference panel dimensions are stale",
            code="visual.panel_lineage_unavailable",
        )
    if not framing_analysis.detector_contract_matches(
        profile.framing_contract_version, mask.detector_version
    ):
        raise RenderError(
            "visual.framing_contract_incompatible: detector/profile mismatch",
            code="visual.framing_contract_incompatible",
        )
    try:
        actual_mask = framing_analysis.build_color_agnostic_border_mask(
            panel,
            evidence,
            grid_long_edge=profile.framing_mask_grid_long_edge,
        )
    except (OSError, ValueError, visual_scoring.VisualEvidenceError) as exc:
        raise RenderError(
            "visual.panel_lineage_unavailable: reference border mask cannot be rebuilt",
            code="visual.panel_lineage_unavailable",
        ) from exc
    if _reference_canonical_json(asdict(actual_mask)) != _reference_canonical_json(asdict(mask)):
        raise RenderError(
            "visual.panel_lineage_unavailable: reference border mask snapshot is stale",
            code="visual.panel_lineage_unavailable",
        )
    selected_roi = scene.selected_roi
    if not isinstance(selected_roi, Mapping):
        raise RenderError(
            "visual.panel_lineage_unavailable: selected reference ROI is missing",
            code="visual.panel_lineage_unavailable",
        )
    if "speech_bubble" in _reference_canonical_json(selected_roi).lower():
        raise RenderError(
            "visual.balloon_mask_overlap: speech-bubble ROI is not renderable",
            code="visual.balloon_mask_overlap",
        )
    try:
        raw_box = selected_roi["crop_box"]
        if (
            not isinstance(raw_box, (list, tuple))
            or len(raw_box) != 4
            or any(isinstance(item, bool) or not isinstance(item, int) for item in raw_box)
        ):
            raise ValueError("selected ROI box is invalid")
        crop_box = tuple(raw_box)
        if (
            crop_box[0] < 0
            or crop_box[1] < 0
            or crop_box[2] <= crop_box[0]
            or crop_box[3] <= crop_box[1]
            or crop_box[2] > panel.width
            or crop_box[3] > panel.height
        ):
            raise ValueError("selected ROI box is invalid")
    except (KeyError, TypeError, ValueError) as exc:
        raise RenderError(
            "visual.panel_lineage_unavailable: selected reference ROI is invalid",
            code="visual.panel_lineage_unavailable",
        ) from exc
    try:
        feasibility_kwargs: dict[str, object] = {}
        if allow_source_resolution_warning:
            feasibility_kwargs["allow_source_resolution_warning"] = True
        if scene.publish_allowed is False:
            # Silent review plans with relaxed protected coverage so a
            # dominant-subject crop can fit a full webtoon page; the render
            # re-check must use the same contract or it will reject the very
            # ROI the planner accepted.
            feasibility_kwargs["review_aggressive_crop"] = True
        feasible, telemetry = framing_analysis.candidate_is_feasible(
            crop_box,
            evidence,
            mask,
            panel_size,
            (width, height),
            **feasibility_kwargs,
        )
    except visual_scoring.VisualEvidenceError as exc:
        raise RenderError(str(exc), code=exc.code) from exc
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise RenderError(
            "visual.panel_lineage_unavailable: persisted ROI feasibility failed",
            code="visual.panel_lineage_unavailable",
        ) from exc
    telemetry_map = _reference_telemetry_mapping(telemetry)
    if not feasible:
        rejection_code = telemetry_map.get("rejection_code") or "visual.visual_unavailable"
        raise RenderError(
            f"{rejection_code}: persisted reference ROI is no longer feasible",
            code=str(rejection_code),
            telemetry=telemetry if isinstance(telemetry, framing_analysis.FramingTelemetry) else None,
        )
    persisted_telemetry = _reference_telemetry_mapping(scene.framing_telemetry)
    for field_name in _REFERENCE_TELEMETRY_FIELDS:
        if field_name not in persisted_telemetry or field_name not in telemetry_map:
            raise RenderError(
                "visual.panel_lineage_unavailable: reference telemetry is incomplete",
                code="visual.panel_lineage_unavailable",
            )
        if _reference_canonical_json(persisted_telemetry[field_name]) != _reference_canonical_json(
            telemetry_map[field_name]
        ):
            raise RenderError(
                "visual.panel_lineage_unavailable: reference telemetry is stale",
                code="visual.panel_lineage_unavailable",
            )
    persisted_selected = persisted_telemetry.get("selected_roi")
    if persisted_selected is not None:
        for field_name in ("kind", "roi_label", "crop_box"):
            if persisted_selected.get(field_name) != selected_roi.get(field_name):
                raise RenderError(
                    "visual.panel_lineage_unavailable: reference telemetry ROI is stale",
                    code="visual.panel_lineage_unavailable",
                )
    output_size = (_reference_even(round(width * 1.15)), _reference_even(round(height * 1.15)))
    prepared = panel.crop(crop_box).resize(output_size, Image.Resampling.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    prepared.save(dest, "JPEG", quality=94)
    return dest


def _compact_border_mask_identity(value: object) -> dict[str, Any]:
    if isinstance(value, framing_analysis.BorderMaskResult):
        value = asdict(value)
    if not isinstance(value, Mapping):
        return {}
    return {
        key: value.get(key)
        for key in ("detector_version", "mask_sha256", "source_width", "source_height")
    }


def _reference_json_safe(value: object) -> object:
    """Normalize in-memory review identities before writing canonical JSON."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RenderError(
                "visual.panel_lineage_unavailable: review sidecar contains non-finite telemetry",
                code="visual.panel_lineage_unavailable",
            )
        return value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _reference_json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _reference_json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_reference_json_safe(item) for item in value]
    raise RenderError(
        "visual.panel_lineage_unavailable: review sidecar contains a non-JSON identity",
        code="visual.panel_lineage_unavailable",
    )


def _reference_review_sidecar(request: RenderRequest, info: Mapping[str, Any]) -> dict[str, Any]:
    if request.profile is None:
        raise RenderError(
            "silent reference review requires the reference profile",
            code="reference.silent_profile_required",
        )
    shots: list[dict[str, Any]] = []
    for index, scene in enumerate(request.scenes):
        if scene.publish_allowed is not False:
            raise RenderError(
                "reference.publish_not_allowed: publish_allowed must be false for silent review scenes",
                code="reference.publish_not_allowed",
            )
        attempts: list[dict[str, Any]] = []
        for attempt in scene.fallback_attempts:
            if not isinstance(attempt, Mapping):
                raise RenderError(
                    "visual.panel_lineage_unavailable: fallback ledger is malformed",
                    code="visual.panel_lineage_unavailable",
                )
            compact = dict(attempt)
            compact.pop("border_mask", None)
            attempts.append(compact)
        shots.append(
            {
                "order_index": index,
                "start_time": scene.start_time,
                "end_time": scene.end_time,
                "source_asset_id": scene.source_asset_id,
                "source_order": scene.source_order,
                "panel_region_id": scene.panel_region_id,
                "panel_id": scene.panel_id,
                "panel_size": scene.panel_size,
                "source_asset_checksum": scene.source_asset_checksum,
                "evidence_hash": scene.evidence_hash,
                "border_mask": _compact_border_mask_identity(scene.border_mask),
                "selected_roi": scene.selected_roi,
                "fallback_attempts": attempts,
                "framing_telemetry": scene.framing_telemetry,
                "source_upscale_manifest": scene.review_source_upscale_manifest,
                "reason": scene.motion_reason,
                "rejection_code": None,
            }
        )
    subtitle_evidence: dict[str, Any] | None = None
    if getattr(request, "sentence_groups", None):
        subtitle_evidence = _subtitle_manifest_evidence(
            request.sentence_groups,
            profile=request.profile,
            timing_source=getattr(request, "subtitle_timing_source", "review_provisional_display_pacing_v1"),
        )
    return _reference_json_safe({
        "schema_version": "reference_visual_review_v1",
        "project_id": request.project_id,
        "profile_id": request.profile.profile_id,
        "publish_allowed": False,
        "audio_stream_expected": False,
        "audio_stream_present": bool(info.get("has_audio")),
        "subtitle_evidence": subtitle_evidence,
        "source_upscale_policy": getattr(request, "review_source_upscale_policy", None),
        "source_upscale_resolution_states": sorted(
            {
                str(
                    manifest.get("resolution_state", "")
                )
                for manifest in (
                    scene.review_source_upscale_manifest
                    for scene in request.scenes
                    if isinstance(scene.review_source_upscale_manifest, Mapping)
                )
                if manifest.get("resolution_state")
            }
        ),
        "shots": shots,
    })


def _escape_filter_path(path: Path) -> str:
    """Escape a path for use inside an FFmpeg filter argument."""
    text = str(path)
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "\\'")
    text = text.replace("[", "\\[").replace("]", "\\]")
    text = text.replace(",", "\\,")
    return text


# --- main entry point ------------------------------------------------------


def validate_silent_reference_cues(
    cues: Sequence[CueSpec],
    scenes: Sequence[SceneInput],
    *,
    media_duration: float | None = None,
) -> None:
    """Fail closed when a silent-review cue crosses a persisted hard cut."""
    if not scenes:
        raise RenderError(
            "reference.subtitle_invalid: silent review has no scenes",
            code="reference.subtitle_invalid",
        )
    epsilon = 1e-9
    duration = (
        float(media_duration)
        if media_duration is not None
        else max(float(scene.end_time) for scene in scenes)
    )
    for cue in cues:
        text = str(cue.text or "")
        try:
            start_time = float(cue.start_time)
            end_time = float(cue.end_time)
        except (TypeError, ValueError) as exc:
            raise RenderError(
                "reference.subtitle_invalid: persisted display cues are invalid",
                code="reference.subtitle_invalid",
            ) from exc
        if (
            not text
            or text != text.upper()
            or not text.isalnum()
            or len(text) > MAX_SUBTITLE_CHARS_PER_LINE
            or start_time < -epsilon
            or end_time <= start_time
            or end_time > duration + epsilon
        ):
            raise RenderError(
                "reference.subtitle_invalid: persisted display cues are invalid",
                code="reference.subtitle_invalid",
            )
        if not any(
            float(scene.start_time) - epsilon <= start_time
            and end_time <= float(scene.end_time) + epsilon
            for scene in scenes
        ):
            raise RenderError(
                "reference.subtitle_invalid: cue crosses a scene boundary",
                code="reference.subtitle_invalid",
            )


def render_video(request: RenderRequest, progress=None) -> RenderResult:
    """Render a complete Short. ``progress(pct, stage)`` is called as it runs."""
    if request.preview:
        width = request.width or settings.video_width
        height = request.height or settings.video_height
        fps = request.fps or settings.video_fps
    elif request.profile is not None:
        width = request.profile.final_width
        height = request.profile.final_height
        fps = request.profile.final_fps
    else:
        # Final delivery is a fixed vertical contract; previews may stay small.
        width, height, fps = settings.video_width, settings.video_height, settings.video_fps

    if width % 2 or height % 2:
        raise RenderError("video dimensions must be even for H.264", code="bad_dimensions")
    if not request.scenes:
        raise RenderError("nothing to render: the timeline has no scenes", code="no_scenes")
    if request.review_source_upscale_policy is not None:
        from app.services import review_source_upscale

        if request.profile is None:
            raise RenderError(
                "review.upscale_requires_reference_profile: source upscale requires reference mode",
                code="review.upscale_requires_reference_profile",
            )
        try:
            review_source_upscale.validate_review_upscale_request(
                request.review_source_upscale_policy,
                silent_reference_review=request.silent_reference_review,
                publish_allowed=any(
                    scene.publish_allowed is not False for scene in request.scenes
                ),
            )
        except review_source_upscale.ReviewSourceUpscaleError as exc:
            raise RenderError(str(exc), code=exc.code) from exc
        if any(
            not isinstance(scene.review_source_upscale_manifest, Mapping)
            for scene in request.scenes
        ):
            raise RenderError(
                "review.upscale_manifest_invalid: every silent review scene needs a source-upscale manifest",
                code="review.upscale_manifest_invalid",
            )
    if request.profile is not None and not request.silent_reference_review and not request.persisted_reference_framing:
        raise RenderError(
            "visual.panel_lineage_unavailable: regular reference render requires persisted panel framing",
            code="visual.panel_lineage_unavailable",
        )
    if request.profile is not None and request.persisted_reference_framing and not request.sentence_groups:
        # Preserve visual-lineage precedence when timing is absent, while still
        # rejecting the request before any scene encoder work begins.
        for index, scene in enumerate(request.scenes):
            if not scene.image_path or not Path(scene.image_path).is_file() or scene.visual_evidence is None:
                raise RenderError(
                    f"visual.panel_lineage_unavailable: reference scene {index + 1} is missing panel visual lineage",
                    code="visual.panel_lineage_unavailable",
                )
            try:
                evidence = (
                    scene.visual_evidence
                    if isinstance(scene.visual_evidence, visual_scoring.PanelVisualEvidence)
                    else visual_scoring.parse_panel_visual_evidence(scene.visual_evidence)
                )
                visual_scoring.require_reference_ready_visual_evidence(evidence)
                _reference_border_mask_from_mapping(scene.border_mask)
            except RenderError:
                raise
            except visual_scoring.VisualEvidenceError as exc:
                code = (
                    "visual.balloon_mask_unknown"
                    if exc.code == "visual.balloon_mask_unknown"
                    else "visual.panel_lineage_unavailable"
                )
                raise RenderError(f"{code}: reference scene visual evidence is unavailable", code=code) from exc
        raise RenderError(
            "subtitle.word_timing_missing: regular reference render requires authoritative word timing",
            code="subtitle.word_timing_missing",
        )
    if request.silent_reference_review and request.profile is not None:
        validate_silent_reference_cues(request.cues, request.scenes)

    def report(pct: int, stage: str) -> None:
        if progress:
            progress(pct, stage)

    # Resolve the encoder ONCE per render. Probing per scene would spawn a
    # subprocess for every clip, and a mid-render switch could mix codecs in the
    # concat stream, which "-c copy" cannot join.
    selection = encoders.select(request.encoder)
    if request.profile is not None and not request.preview:
        _validate_reference_encoder(selection, request.profile)

    from app.services import storage

    work = storage.workspace_dir(request.project_id, "render")
    # Start clean so a retry never mixes clips from an earlier attempt.
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    report(5, "preparing images")
    clips: list[Path] = []
    prepared_cache: dict[tuple, Path] = {}
    for i, scene in enumerate(request.scenes):
        prepared = work / f"img{i:03d}.jpg"
        scene_evidence: PanelVisualEvidence | None = None
        scene_border_mask: framing_analysis.BorderMaskResult | None = None
        if request.profile is not None:
            if request.silent_reference_review and scene.publish_allowed is not False:
                raise RenderError(
                    "reference.publish_not_allowed: publish_allowed must be false for silent review scenes",
                    code="reference.publish_not_allowed",
                )
            if not scene.image_path or not Path(scene.image_path).is_file() or scene.visual_evidence is None:
                raise RenderError(
                    f"visual.panel_lineage_unavailable: reference scene {i + 1} is missing panel visual lineage",
                    code="visual.panel_lineage_unavailable",
                )
            try:
                scene_evidence = (
                    scene.visual_evidence
                    if isinstance(scene.visual_evidence, visual_scoring.PanelVisualEvidence)
                    else visual_scoring.parse_panel_visual_evidence(scene.visual_evidence)
                )
                scene_evidence = visual_scoring.require_reference_ready_visual_evidence(scene_evidence)
                if request.silent_reference_review or request.persisted_reference_framing:
                    scene_border_mask = _reference_border_mask_from_mapping(scene.border_mask)
                else:
                    with Image.open(scene.image_path) as panel_image:
                        scene_border_mask = framing_analysis.build_color_agnostic_border_mask(
                            panel_image,
                            scene_evidence,
                            grid_long_edge=request.profile.framing_mask_grid_long_edge,
                        )
            except RenderError:
                raise
            except visual_scoring.VisualEvidenceError as exc:
                code = (
                    "visual.balloon_mask_unknown"
                    if exc.code == "visual.balloon_mask_unknown"
                    else "visual.panel_lineage_unavailable"
                )
                raise RenderError("reference scene visual evidence is unavailable", code=code) from exc
        cache_key = (
            reference_frame_cache_key(
                Path(scene.image_path) if scene.image_path else Path(""),
                width,
                height,
                scene.focus_x,
                scene.focus_y,
                scene.focus_end_x,
                scene.focus_end_y,
                request.profile,
                border_mask=scene_border_mask,
                evidence=scene_evidence,
            ),
            scene.motion_mode,
            scene.motion_intensity,
            tuple(sorted(scene.disabled_effects)),
            _reference_canonical_json(scene.selected_roi) if request.silent_reference_review else "",
            _reference_canonical_json(scene.framing_telemetry) if request.silent_reference_review else "",
        )
        cached = prepared_cache.get(cache_key)
        if cached and cached.is_file():
            shutil.copyfile(cached, prepared)
        elif scene.image_path and Path(scene.image_path).is_file():
            try:
                if request.silent_reference_review or request.persisted_reference_framing:
                    exact_prepare_kwargs: dict[str, object] = {
                        "scene": scene,
                        "dest": prepared,
                        "width": width,
                        "height": height,
                        "profile": request.profile,
                    }
                    if (
                        request.silent_reference_review
                        and request.review_source_upscale_policy
                        == "review_silent_source_upscale_v1"
                        and isinstance(scene.review_source_upscale_manifest, Mapping)
                        and scene.review_source_upscale_manifest.get("resolution_state")
                        == "LOW_SOURCE_RESOLUTION"
                        and scene.review_source_upscale_manifest.get("non_native_warning")
                        == "review.low_source_resolution"
                    ):
                        exact_prepare_kwargs["allow_source_resolution_warning"] = True
                    _prepare_exact_reference_frame(**exact_prepare_kwargs)
                else:
                    editorial_frame(
                        Path(scene.image_path), prepared, width, height,
                        scene.focus_x, scene.focus_y, scene.focus_end_x, scene.focus_end_y,
                        scene.motion_mode,
                        profile=request.profile,
                        evidence=scene_evidence,
                        border_mask=scene_border_mask,
                    )
            except RenderError:
                raise
            except Exception as exc:
                raise RenderError(
                    f"could not process image for scene {i + 1} "
                    f"({Path(scene.image_path).name}): {exc}",
                    code="image_prepare_failed",
                ) from exc
        else:
            placeholder_image(prepared, width, height, scene.overlay_text or "no image")
        effects = [] if request.silent_reference_review else local_effects(
            scene.motion_mode, scene.disabled_effects, request.profile
        )
        if effects:
            with Image.open(prepared) as prepared_image:
                effected = apply_local_effects(
                    prepared_image,
                    effects,
                    scene.motion_intensity,
                    seed=42 + i,
                )
                effected.save(prepared, "JPEG", quality=94)
        prepared_cache[cache_key] = prepared

        clip = work / f"clip{i:03d}.mp4"
        render_scene_clip(
            scene, prepared, clip, width, height, fps,
            encoder=selection, preview=request.preview, profile=request.profile,
        )
        clips.append(clip)
        report(5 + int(45 * (i + 1) / len(request.scenes)), f"scene {i + 1}/{len(request.scenes)}")

    report(55, "joining scenes")
    silent = work / "silent.mp4"
    join_scene_clips(clips, request.scenes, silent, fps, selection, request.preview)

    report(65, "burning subtitles")
    video_stage = silent
    if request.sentence_groups:
        failures = subtitle_karaoke.validate_sentence_groups(
            request.sentence_groups,
            duration=sum(scene.duration for scene in request.scenes),
        )
        if failures:
            raise RenderError(
                f"{failures[0]}: sentence karaoke contract is invalid",
                code=failures[0],
            )
        ass_text = build_sentence_karaoke_ass(
            request.sentence_groups,
            width,
            height,
            font_name=settings.subtitle_font_name or "Barber Chop",
            max_chars=subtitle_karaoke.CAPTION_MAX_CHARS,
            max_lines=subtitle_karaoke.CAPTION_MAX_LINES,
            active_scale=subtitle_karaoke.CAPTION_ACTIVE_SCALE,
            font_height_ratio=subtitle_karaoke.CAPTION_FONT_HEIGHT_RATIO,
            safe_margin_px=subtitle_karaoke.CAPTION_SAFE_MARGIN_PX,
        )
    elif request.subtitle_contract_version:
        raise RenderError(
            "subtitle.word_timing_missing: regular reference render requires sentence groups",
            code="subtitle.word_timing_missing",
        )
    else:
        ass_text = build_ass(
            request.cues,
            width,
            height,
            settings.subtitle_font_name,
            profile=request.profile,
        )
    if request.cues or request.sentence_groups:
        ass_path = work / "captions.ass"
        ass_path.write_text(ass_text, encoding="utf-8")
        burned = work / "burned.mp4"
        # libass draws on CPU frames, so the hardware upload (if any) has to come
        # after the subtitles filter rather than before it.
        subtitle_filter = f"subtitles='{_escape_filter_path(ass_path)}'"
        font_path = Path(settings.subtitle_font)
        if font_path.is_file():
            subtitle_filter += f":fontsdir='{_escape_filter_path(font_path.parent)}'"
        burn_vf = encoders.apply_filter_suffix(selection, subtitle_filter)
        burn_vf = _reference_final_video_filter(
            burn_vf,
            request.profile,
            preview=request.preview,
        )
        _run(
            [
                settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                *encoders.input_args(selection),
                "-i", str(silent),
                "-vf", burn_vf,
                *encoders.video_args(selection, preview=request.preview, final=not request.preview),
                str(burned),
            ],
            timeout=900,
            step="subtitle_burn",
        )
        video_stage = burned

    report(80, "mixing audio")
    output = request.output_path or storage.output_path(
        request.project_id, "preview.mp4" if request.preview else "final.mp4"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = work / "muxed.mp4"

    if request.audio_path and Path(request.audio_path).is_file():
        master_duration = probe(video_stage)["duration"]
        normalizer = "" if request.preview else ",loudnorm=I=-14:TP=-1.5:LRA=11"
        cmd = [
            settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(video_stage),
            "-i", str(request.audio_path),
        ]
        if request.music_path and Path(request.music_path).is_file():
            cmd += ["-stream_loop", "-1", "-i", str(request.music_path)]
            # Narration stays dominant; music sits well under it.
            cmd += [
                "-filter_complex",
                f"[2:a]volume={request.music_gain_db}dB[bg];"
                f"[1:a][bg]amix=inputs=2:duration=first:dropout_transition=2,"
                f"apad,atrim=duration={master_duration:.6f}{normalizer}[aout]",
                "-map", "0:v", "-map", "[aout]",
            ]
        else:
            cmd += [
                "-filter_complex", f"[1:a]apad,atrim=duration={master_duration:.6f}{normalizer}[aout]",
                "-map", "0:v", "-map", "[aout]",
            ]
        cmd += [
            "-t", f"{master_duration:.6f}",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
            str(tmp_out),
        ]
        _run(cmd, timeout=900, step="mux")
    else:
        _run(
            [
                settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(video_stage),
                "-c", "copy", "-movflags", "+faststart",
                str(tmp_out),
            ],
            timeout=600,
            step="mux",
        )

    report(92, "finalising")
    if request.profile is not None and not request.preview:
        validate_reference_output(probe(tmp_out), request.profile)
    shutil.move(str(tmp_out), str(output))

    info = probe(output)
    sidecar_path: Path | None = None
    manifest_path: Path | None = None
    if request.silent_reference_review:
        if request.profile is None:
            raise RenderError(
                "silent reference review requires the reference profile",
                code="reference.silent_profile_required",
            )
        if info.get("has_audio"):
            raise RenderError(
                "silent reference review unexpectedly contains audio",
                code="reference.audio_unexpected",
            )
        sidecar_path = request.sidecar_path or output.with_suffix(".review.json")
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar = _reference_review_sidecar(request, info)
        try:
            sidecar_text = json.dumps(
                sidecar,
                allow_nan=False,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise RenderError(
                "visual.panel_lineage_unavailable: review sidecar is not JSON-safe",
                code="visual.panel_lineage_unavailable",
            ) from exc
        sidecar_path.write_text(sidecar_text + "\n", encoding="utf-8")
    elif request.profile is not None and request.persisted_reference_framing:
        manifest_path = request.sidecar_path or output.with_suffix(".render.json")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": "regular_render_manifest_v1",
            "project_id": request.project_id,
            "profile_id": request.profile.profile_id,
            "subtitle_contract": request.subtitle_contract or {},
            "subtitle_contract_version": request.subtitle_contract_version,
            "subtitle_timing_source": request.subtitle_timing_source,
            "subtitle_evidence": _subtitle_manifest_evidence(
                request.sentence_groups,
                profile=request.profile,
                timing_source=request.subtitle_timing_source,
            ),
            "publish_allowed": False,
            "visual_contract_version": request.profile.framing_contract_version,
            "output": {
                "duration": info.get("duration"),
                "width": info.get("width"),
                "height": info.get("height"),
                "fps": info.get("fps"),
                "codec": info.get("codec"),
                "profile": info.get("profile"),
                "pix_fmt": info.get("pix_fmt"),
                "audio_stream_expected": bool(request.audio_path),
            },
            "shots": [
                {
                    "index": index,
                    "start_time": scene.start_time,
                    "end_time": scene.end_time,
                    "source_asset_id": scene.source_asset_id,
                    "source_order": scene.source_order,
                    "panel_region_id": scene.panel_region_id,
                    "panel_id": scene.panel_id,
                    "panel_size": scene.panel_size,
                    "source_asset_checksum": scene.source_asset_checksum,
                    "evidence_hash": scene.evidence_hash,
                    "border_mask": _compact_border_mask_identity(scene.border_mask),
                    "selected_roi": scene.selected_roi,
                    "framing_telemetry": scene.framing_telemetry,
                    "fallback_attempts": [
                        {
                            key: value
                            for key, value in attempt.items()
                            if key != "border_mask"
                        }
                        for attempt in scene.fallback_attempts
                        if isinstance(attempt, Mapping)
                    ],
                }
                for index, scene in enumerate(request.scenes)
            ],
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    thumbnail = None
    try:
        thumbnail = output.with_suffix(".jpg")
        _run(
            [
                settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(output), "-ss", "0.5", "-vframes", "1",
                "-q:v", "3",
                str(thumbnail),
            ],
            timeout=120,
            step="thumbnail",
        )
    except RenderError:
        thumbnail = None  # A missing cover frame is not worth failing the render.

    srt_path: Path | None = None
    if request.cues:
        from app.services.timeline import to_srt

        srt_path = output.with_suffix(".srt")
        srt_path.write_text(to_srt(request.cues), encoding="utf-8")

    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    report(100, "done")

    scratch_bytes = sum(path.stat().st_size for path in work.rglob("*") if path.is_file())
    # Free the scratch space; the artifacts we need are already copied out.
    shutil.rmtree(work, ignore_errors=True)

    return RenderResult(
        output_path=output,
        subtitle_path=srt_path,
        thumbnail_path=thumbnail,
        duration=info["duration"],
        width=info["width"] or width,
        height=info["height"] or height,
        checksum=checksum,
        size_bytes=output.stat().st_size,
        encoder=selection.key,
        encoder_label=selection.spec.label,
        encoder_hardware=selection.hardware,
        encoder_fell_back=selection.fell_back,
        encoder_reason=selection.reason,
        scratch_bytes=scratch_bytes,
        sidecar_path=sidecar_path,
        manifest_path=manifest_path,
    )


def ffmpeg_available() -> bool:
    return shutil.which(settings.ffmpeg_bin) is not None


def font_available() -> bool:
    return Path(settings.subtitle_font).is_file()


def check_environment() -> list[str]:
    """Return a list of human-readable environment problems."""
    problems: list[str] = []
    if not ffmpeg_available():
        problems.append(
            f"{settings.ffmpeg_bin} not found. Install with: sudo apt-get install ffmpeg"
        )
    if not shutil.which(settings.ffprobe_bin):
        problems.append(f"{settings.ffprobe_bin} not found (part of the ffmpeg package)")
    if not font_available():
        problems.append(
            f"subtitle font missing at {settings.subtitle_font}. "
            "Install with: sudo apt-get install fonts-dejavu-core"
        )
    if ffmpeg_available():
        try:
            out = _run([settings.ffmpeg_bin, "-hide_banner", "-filters"], timeout=60, step="ffmpeg")
            if not re.search(r"\bzoompan\b", out):
                problems.append("this FFmpeg build lacks the zoompan filter (needed for Ken Burns)")
            if not re.search(r"\bsubtitles\b", out):
                problems.append(
                    "this FFmpeg build lacks the subtitles filter "
                    "(needs libass) so captions cannot be burned in"
                )
        except RenderError as exc:
            problems.append(str(exc))
    return problems
