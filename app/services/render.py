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
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from random import Random
from typing import TYPE_CHECKING

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from app.config import settings
from app.constants import MAX_SUBTITLE_CHARS_PER_LINE, SUBTITLE_SAFE_BOTTOM
from app.services import encoders, framing_analysis, motion_director
from app.services.reference_profile import ReferenceProfileConfig, profile_hash
from app.services.timeline import CueSpec, wrap_caption

if TYPE_CHECKING:
    from app.services.visual_scoring import PanelVisualEvidence

_SECTION_TRANSITION_MIN = 0.12
_SECTION_TRANSITION_MAX = 0.18


class RenderError(RuntimeError):
    """Raised when a render step fails. Message is safe to show the user."""

    def __init__(self, message: str, code: str = "render_failed", log_tail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.log_tail = log_tail


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
) -> PreparedFrame:
    """Select one static content-aware frame for the reference profile."""
    if profile is None:
        return _reference_prepare_fallback(src, dest, width, height, focus_x, focus_y)
    try:
        with Image.open(src) as original:
            image = original.convert("RGB")
            src_w, src_h = image.size
            if src_w < 2 or src_h < 2:
                raise ValueError("degenerate source geometry")
            target_ratio = width / height
            if src_w / src_h > target_ratio:
                base_w = int(round(src_h * target_ratio))
                base_h = src_h
            else:
                base_w = src_w
                base_h = int(round(src_w / target_ratio))
            base_w = max(2, min(base_w, src_w))
            base_h = max(2, min(base_h, src_h))
            output_size = (
                _reference_even(round(width * 1.15)),
                _reference_even(round(height * 1.15)),
            )
            candidates: list[tuple[float, float, tuple[int, int, int, int]]] = []
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
                cropped = image.crop(box)
                local_focus_x = (focus_x * src_w - left) / crop_w
                local_focus_y = (focus_y * src_h - top) / crop_h
                blank, content, focus_content = _reference_content_stats(
                    cropped, local_focus_x, local_focus_y
                )
                box_centre_x = (left + crop_w / 2) / src_w
                box_centre_y = (top + crop_h / 2) / src_h
                focus_distance = min(
                    1.0,
                    ((box_centre_x - focus_x) ** 2 + (box_centre_y - focus_y) ** 2) ** 0.5,
                )
                focus_score = 1.0 - focus_distance
                feasible_bonus = 0.75 if blank <= profile.max_blank_fraction else -blank
                scale_penalty = (scale - 1.0) / max(0.01, profile.base_frame_zoom_max - 1.0)
                score = (
                    feasible_bonus
                    + content * 0.56
                    + focus_content * 0.24
                    + focus_score * 0.15
                    + (1.0 - min(1.0, scale_penalty)) * 0.05
                )
                candidates.append((score, scale, box))
            if not candidates:
                raise ValueError("no deterministic framing candidates")
            _score, scale, box = max(candidates, key=lambda item: (item[0], item[1], item[2][1], item[2][0]))
            prepared = image.crop(box).resize(output_size, Image.Resampling.LANCZOS)
            dest.parent.mkdir(parents=True, exist_ok=True)
            prepared.save(dest, "JPEG", quality=94)
            with Image.open(dest) as saved:
                blank_fraction, _content, _focus_content = _reference_content_stats(
                    saved, 0.5, 0.5
                )
            actual_zoom = max(
                (box[2] - box[0]) and base_w / (box[2] - box[0]),
                (box[3] - box[1]) and base_h / (box[3] - box[1]),
            )
            return PreparedFrame(
                dest,
                box,
                blank_fraction,
                round(min(float(profile.base_frame_zoom_max), actual_zoom), 3),
            )
    except (OSError, ValueError, ZeroDivisionError):
        return _reference_prepare_fallback(src, dest, width, height, focus_x, focus_y)


def editorial_frame(
    src: Path, dest: Path, width: int, height: int,
    focus_x: float, focus_y: float, end_x: float, end_y: float, mode: str,
    profile: ReferenceProfileConfig | None = None,
) -> Path:
    """Build deterministic CPU compositing without altering panel geometry."""
    if profile is not None:
        return prepare_reference_frame(
            src, dest, width, height, focus_x, focus_y, profile
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
        z = "1.02"
    elif safe_effect == "atmospheric":
        z = "1.03"
    else:
        z = "1.04"
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
            ),
            scene.motion_mode, scene.motion_intensity, tuple(sorted(scene.disabled_effects)),
        )
        cached = prepared_cache.get(cache_key)
        if cached and cached.is_file():
            shutil.copyfile(cached, prepared)
        elif scene.image_path and Path(scene.image_path).is_file():
            try:
                editorial_frame(
                    Path(scene.image_path), prepared, width, height,
                    scene.focus_x, scene.focus_y, scene.focus_end_x, scene.focus_end_y,
                    scene.motion_mode,
                    profile=request.profile,
                )
            except Exception as exc:
                raise RenderError(
                    f"could not process image for scene {i + 1} "
                    f"({Path(scene.image_path).name}): {exc}",
                    code="image_prepare_failed",
                ) from exc
        else:
            placeholder_image(prepared, width, height, scene.overlay_text or "no image")
        effects = local_effects(scene.motion_mode, scene.disabled_effects, request.profile)
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
    if request.cues:
        ass_path = work / "captions.ass"
        ass_path.write_text(
            build_ass(
                request.cues,
                width,
                height,
                settings.subtitle_font_name,
                profile=request.profile,
            ),
            encoding="utf-8",
        )
        burned = work / "burned.mp4"
        # libass draws on CPU frames, so the hardware upload (if any) has to come
        # after the subtitles filter rather than before it.
        subtitle_filter = f"subtitles='{_escape_filter_path(ass_path)}'"
        font_path = Path(settings.subtitle_font)
        if font_path.is_file():
            subtitle_filter += f":fontsdir='{_escape_filter_path(font_path.parent)}'"
        burn_vf = encoders.apply_filter_suffix(selection, subtitle_filter)
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
