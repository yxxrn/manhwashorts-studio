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

from PIL import Image

from app.config import settings
from app.constants import MAX_SUBTITLE_CHARS_PER_LINE, SUBTITLE_SAFE_BOTTOM
from app.services import encoders
from app.services.timeline import CueSpec, wrap_caption


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
    effect: str = "kenburns_in"
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
    """Return duration/width/height/audio presence for a media file."""
    out = _run(
        [
            settings.ffprobe_bin, "-v", "error",
            "-show_entries", "format=duration:stream=width,height,codec_type",
            "-of", "default=noprint_wrappers=1",
            str(path),
        ],
        timeout=120,
        step="ffprobe",
    )
    info: dict = {"duration": 0.0, "width": 0, "height": 0, "has_audio": False}
    for line in out.splitlines():
        if line.startswith("duration=") and info["duration"] == 0.0:
            # ffprobe emits "duration=N/A" for some streams; leave the default.
            with contextlib.suppress(ValueError):
                info["duration"] = round(float(line.split("=", 1)[1]), 3)
        elif line.startswith("width=") and not info["width"]:
            info["width"] = int(float(line.split("=", 1)[1] or 0))
        elif line.startswith("height=") and not info["height"]:
            info["height"] = int(float(line.split("=", 1)[1] or 0))
        elif line == "codec_type=audio":
            info["has_audio"] = True
    return info


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


def _motion_filter(effect: str, width: int, height: int, duration: float, fps: int) -> str:
    """Build the zoompan/crop filter implementing a scene's camera move.

    The input image is already prepared at 1.15x the output size by
    ``crop_to_vertical``, so zoompan works on it directly. Pre-scaling to 2x
    here would push every frame to ~8 MP and made rendering unusably slow on a
    small VPS.
    """
    frames = max(2, int(round(duration * fps)))

    if effect == "static":
        return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"

    if effect == "kenburns_in":
        return (
            f"zoompan=z='min(1.0+0.12*on/{frames},1.12)'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={width}x{height}:fps={fps}"
        )
    if effect == "kenburns_out":
        return (
            f"zoompan=z='max(1.12-0.12*on/{frames},1.0)'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={width}x{height}:fps={fps}"
        )
    if effect == "pan_right":
        return (
            f"zoompan=z='1.08':x='(iw-iw/zoom)*on/{frames}'"
            f":y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={width}x{height}:fps={fps}"
        )
    if effect == "pan_left":
        return (
            f"zoompan=z='1.08':x='(iw-iw/zoom)*(1-on/{frames})'"
            f":y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={width}x{height}:fps={fps}"
        )
    # Unknown effect: fall back to a safe static frame.
    return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"


def render_scene_clip(
    scene: SceneInput,
    prepared_image: Path,
    dest: Path,
    width: int,
    height: int,
    fps: int,
    encoder: encoders.Selection | None = None,
    preview: bool = False,
) -> Path:
    """Render one silent scene clip.

    ``encoder`` selects CPU or GPU encoding; when omitted the configured default
    is resolved, so callers and tests can stay unaware of the choice.
    """
    selection = encoder or encoders.select()
    duration = scene.duration
    frames = max(2, int(round(duration * fps)))
    motion = _motion_filter(scene.effect, width, height, duration, fps)
    vf = f"{motion},format=yuv420p"

    # Short fade in/out on every clip smooths the joins.
    fade = min(0.25, duration / 4)
    if fade > 0.05:
        vf += f",fade=t=in:st=0:d={fade:.2f},fade=t=out:st={duration - fade:.2f}:d={fade:.2f}"

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
            "-loop", "1",
            "-i", str(prepared_image),
            "-vf", vf,
            "-r", str(fps),
            "-frames:v", str(frames),
            *encoders.video_args(selection, preview=preview),
            str(dest),
        ],
        timeout=600,
        step="scene_render",
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
) -> str:
    """Generate an ASS subtitle file positioned inside the Shorts safe area.

    Bottom margin keeps text clear of the YouTube UI overlay; a heavy outline
    keeps it readable over busy artwork.
    """
    font_size = max(36, int(height * 0.038))
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
        wrapped = "\\N".join(_ass_escape(line) for line in wrap_caption(cue.text, max_chars))
        lines.append(
            f"Dialogue: 0,{_ass_time(cue.start_time)},{_ass_time(cue.end_time)},"
            f"Caption,,0,0,0,,{wrapped}"
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
    width = request.width or settings.video_width
    height = request.height or settings.video_height
    fps = request.fps or settings.video_fps

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

    from app.services import storage

    work = storage.workspace_dir(request.project_id, "render")
    # Start clean so a retry never mixes clips from an earlier attempt.
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    report(5, "preparing images")
    clips: list[Path] = []
    for i, scene in enumerate(request.scenes):
        prepared = work / f"img{i:03d}.jpg"
        if scene.image_path and Path(scene.image_path).is_file():
            try:
                crop_to_vertical(
                    Path(scene.image_path), prepared, width, height, scene.focus_x, scene.focus_y
                )
            except Exception as exc:
                raise RenderError(
                    f"could not process image for scene {i + 1} "
                    f"({Path(scene.image_path).name}): {exc}",
                    code="image_prepare_failed",
                ) from exc
        else:
            placeholder_image(prepared, width, height, scene.overlay_text or "no image")

        clip = work / f"clip{i:03d}.mp4"
        render_scene_clip(
            scene, prepared, clip, width, height, fps,
            encoder=selection, preview=request.preview,
        )
        clips.append(clip)
        report(5 + int(45 * (i + 1) / len(request.scenes)), f"scene {i + 1}/{len(request.scenes)}")

    report(55, "joining scenes")
    concat_list = work / "clips.txt"
    concat_list.write_text(
        "".join(f"file '{c.as_posix()}'\n" for c in clips),
        encoding="utf-8",
    )
    silent = work / "silent.mp4"
    _run(
        [
            settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-c", "copy",
            str(silent),
        ],
        timeout=600,
        step="concat",
    )

    report(65, "burning subtitles")
    video_stage = silent
    if request.cues:
        ass_path = work / "captions.ass"
        font_name = "DejaVu Sans"
        ass_path.write_text(build_ass(request.cues, width, height, font_name), encoding="utf-8")
        burned = work / "burned.mp4"
        # libass draws on CPU frames, so the hardware upload (if any) has to come
        # after the subtitles filter rather than before it.
        burn_vf = encoders.apply_filter_suffix(
            selection, f"subtitles='{_escape_filter_path(ass_path)}'"
        )
        _run(
            [
                settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                *encoders.input_args(selection),
                "-i", str(silent),
                "-vf", burn_vf,
                *encoders.video_args(selection, preview=request.preview),
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
                f"[1:a][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]",
                "-map", "0:v", "-map", "[aout]",
            ]
        else:
            cmd += ["-map", "0:v", "-map", "1:a"]
        cmd += [
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-shortest",
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
