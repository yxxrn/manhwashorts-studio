"""Video encoder selection: CPU or GPU (v1.2).

Encoding is the slowest part of a render and the only part that a GPU can
meaningfully accelerate. This module decides *which* encoder FFmpeg should use
and produces the right flags, so `render.py` never has to care whether the work
lands on a CPU or a GPU.

Backends, in the order `auto` prefers them:

| Backend      | Hardware                        | FFmpeg encoder |
|--------------|---------------------------------|----------------|
| ``nvenc``    | NVIDIA GTX 900-series and newer | ``h264_nvenc`` |
| ``qsv``      | Intel iGPU with Quick Sync      | ``h264_qsv``   |
| ``vaapi``    | AMD (Mesa) and Intel on Linux   | ``h264_vaapi`` |
| ``videotoolbox`` | Apple Silicon / macOS       | ``h264_videotoolbox`` |
| ``cpu``      | anything                        | ``libx264``    |

Two rules shape the whole design:

**Detection must prove the encoder works, not just that it is compiled in.**
Every FFmpeg build ships `h264_nvenc` whether or not an NVIDIA card is present,
and `ffmpeg -encoders` lists it regardless. So probing runs a real one-frame
encode and checks the exit status. Anything less produces a config that looks
fine and fails at render time.

**Falling back must be loud.** If a user picks `nvenc` and the GPU is missing,
we render on the CPU rather than failing — but the reason is recorded and
surfaced, because silently taking 20x longer is its own kind of bug.

Quality note: hardware encoders use a bitrate/quality knob that is not the same
scale as x264's CRF, so each backend carries its own tuned settings rather than
a shared number that would mean different things per vendor.
"""

from __future__ import annotations

import functools
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.config import settings

#: A probe should be near-instant; a hang here would stall every render.
PROBE_TIMEOUT = 25


@dataclass(frozen=True)
class EncoderSpec:
    """How to drive one encoder backend."""

    key: str
    label: str
    codec: str
    #: True when frames are encoded on a GPU.
    hardware: bool
    #: Flags placed before ``-i`` (device setup for VAAPI/QSV).
    input_args: tuple[str, ...] = ()
    #: Flags placed after the filter chain, before the output path.
    output_args: tuple[str, ...] = ()
    #: Appended to any video filter chain (hardware upload for VAAPI).
    filter_suffix: str = ""
    #: Pixel format the filters must produce for this encoder.
    pix_fmt: str = "yuv420p"
    notes: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "codec": self.codec,
            "hardware": self.hardware,
            "notes": self.notes,
        }


#: CPU baseline. veryfast/CRF20 is the v1.0 setting and the quality reference
#: every hardware preset below is tuned to approximate.
CPU = EncoderSpec(
    key="cpu",
    label="CPU (libx264)",
    codec="libx264",
    hardware=False,
    output_args=("-c:v", "libx264", "-preset", "veryfast", "-crf", "20"),
    notes="Works everywhere. Slowest, but the most predictable quality per byte.",
)

#: NVIDIA. p4 is the balanced preset; CQ 23 lands near x264 CRF 20 in size.
NVENC = EncoderSpec(
    key="nvenc",
    label="NVIDIA GPU (NVENC)",
    codec="h264_nvenc",
    hardware=True,
    output_args=(
        "-c:v", "h264_nvenc",
        "-preset", "p4",
        "-tune", "hq",
        "-rc", "vbr",
        "-cq", "23",
        "-b:v", "0",
    ),
    notes="Needs an NVIDIA GPU (GTX 900+) with a driver the FFmpeg build can load.",
)

#: Intel Quick Sync. global_quality is QSV's CRF analogue.
QSV = EncoderSpec(
    key="qsv",
    label="Intel Quick Sync (QSV)",
    codec="h264_qsv",
    hardware=True,
    input_args=("-init_hw_device", "qsv=hw", "-filter_hw_device", "hw"),
    output_args=(
        "-c:v", "h264_qsv",
        "-preset", "medium",
        "-global_quality", "23",
    ),
    notes="Needs an Intel CPU with an enabled integrated GPU.",
)

#: VAAPI covers AMD via Mesa, and Intel as an alternative to QSV. Frames must be
#: uploaded to the GPU, hence the filter suffix and nv12 format.
VAAPI = EncoderSpec(
    key="vaapi",
    label="AMD / Intel VAAPI",
    codec="h264_vaapi",
    hardware=True,
    input_args=("-vaapi_device", "/dev/dri/renderD128"),
    output_args=("-c:v", "h264_vaapi", "-qp", "23"),
    filter_suffix="format=nv12,hwupload",
    pix_fmt="nv12",
    notes="Linux AMD (Mesa) or Intel. Requires a /dev/dri/renderD* node.",
)

#: Apple Silicon and Intel Macs.
VIDEOTOOLBOX = EncoderSpec(
    key="videotoolbox",
    label="Apple VideoToolbox",
    codec="h264_videotoolbox",
    hardware=True,
    output_args=("-c:v", "h264_videotoolbox", "-q:v", "55"),
    notes="macOS only, including Apple Silicon.",
)

#: Probe order for ``auto``. NVENC first because it is usually the fastest and
#: the least ambiguous to detect; VAAPI before QSV on the same Intel chip is a
#: coin toss, so QSV (the vendor path) is tried first.
_ALL: tuple[EncoderSpec, ...] = (NVENC, QSV, VAAPI, VIDEOTOOLBOX, CPU)
_BY_KEY: dict[str, EncoderSpec] = {spec.key: spec for spec in _ALL}


@dataclass
class Selection:
    """The encoder chosen for a render, and how we got there."""

    spec: EncoderSpec
    requested: str
    #: True when the request could not be honoured and we fell back.
    fell_back: bool = False
    reason: str = ""
    probe_log: str = ""

    @property
    def key(self) -> str:
        return self.spec.key

    @property
    def hardware(self) -> bool:
        return self.spec.hardware

    def as_dict(self) -> dict[str, object]:
        return {
            "encoder": self.spec.key,
            "label": self.spec.label,
            "codec": self.spec.codec,
            "hardware": self.spec.hardware,
            "requested": self.requested,
            "fell_back": self.fell_back,
            "reason": self.reason,
        }


def known_encoders() -> tuple[EncoderSpec, ...]:
    return _ALL


def get_spec(key: str) -> EncoderSpec:
    """Look up a backend by key. Raises ValueError for anything unknown."""
    normalised = (key or "").strip().lower()
    if normalised in ("", "auto"):
        raise ValueError("'auto' is resolved by select(), not a concrete spec")
    if normalised not in _BY_KEY:
        raise ValueError(
            f"unknown encoder '{key}'. Choose from: auto, "
            + ", ".join(_BY_KEY)
        )
    return _BY_KEY[normalised]


def _run_probe(args: list[str]) -> tuple[bool, str]:
    """Run a probe command, returning (succeeded, log tail)."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        return False, f"{settings.ffmpeg_bin} not found"
    except subprocess.TimeoutExpired:
        return False, "probe timed out"
    if proc.returncode == 0:
        return True, ""
    log = (proc.stderr or proc.stdout or "").strip()
    # Keep the HEAD, not the tail. FFmpeg reports the root cause first
    # ("Cannot load libcuda.so.1") and then emits cascading thread/muxer noise;
    # truncating from the end throws away the only useful line.
    return False, log[:1200]


@functools.lru_cache(maxsize=1)
def _encoder_listing() -> str:
    """Cached output of ``ffmpeg -encoders``."""
    if not shutil.which(settings.ffmpeg_bin):
        return ""
    try:
        proc = subprocess.run(
            [settings.ffmpeg_bin, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout or ""


def compiled_in(spec: EncoderSpec) -> bool:
    """Whether the FFmpeg binary lists this encoder at all.

    Necessary but nowhere near sufficient: distro builds advertise `h264_nvenc`
    on machines with no NVIDIA hardware, which is exactly why ``probe()`` runs a
    real encode instead of trusting this.
    """
    return f" {spec.codec}" in _encoder_listing()


@functools.lru_cache(maxsize=16)
def probe(key: str) -> tuple[bool, str]:
    """Encode one real frame to prove this backend actually works.

    Cached, because probing spawns a process and the answer cannot change while
    the app is running. Returns (works, reason-if-not).
    """
    if key == CPU.key:
        # libx264 is always present in any build we support; a probe would only
        # add startup latency.
        return (shutil.which(settings.ffmpeg_bin) is not None, "ffmpeg not found")

    try:
        spec = get_spec(key)
    except ValueError as exc:
        return False, str(exc)

    if spec.key == VAAPI.key and not _vaapi_device_exists():
        return False, (
            "no /dev/dri/renderD* render node. On Linux add your user to the "
            "'render' group, or pass the device into the container"
        )

    # Encode one real frame to a throwaway file. Deliberately NOT "-f null -":
    # the null muxer discards frames, so a broken hardware encoder can exit 0
    # while producing nothing, and the real error gets masked by a confusing
    # "Nothing was written into output file" message.
    with tempfile.TemporaryDirectory(prefix="ms-encprobe-") as tmp:
        target = Path(tmp) / "probe.mp4"
        args = [
            settings.ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y",
            *spec.input_args,
            "-f", "lavfi", "-i", "color=c=black:s=320x240:d=0.1",
            "-frames:v", "1",
        ]
        if spec.filter_suffix:
            args += ["-vf", spec.filter_suffix]
        args += [*spec.output_args, str(target)]

        ok, log = _run_probe(args)
        # An encoder that "succeeds" but writes an empty file is not usable.
        if ok and (not target.is_file() or target.stat().st_size == 0):
            ok, log = False, "encoder ran but produced no output"

    if ok:
        return True, ""
    return False, _explain_failure(spec, log)


def _vaapi_device_exists() -> bool:
    """VAAPI needs a render node, not just a card node.

    `/dev/dri/card0` exists on machines with only a virtual VGA adapter (QEMU
    cirrus, for instance) and cannot encode anything.
    """
    dri = Path("/dev/dri")
    if not dri.is_dir():
        return False
    return any(child.name.startswith("renderD") for child in dri.iterdir())


def _explain_failure(spec: EncoderSpec, log: str) -> str:
    """Turn an FFmpeg error into something a user can act on."""
    lowered = log.lower()
    if spec.key == NVENC.key:
        if "cannot load" in lowered or "libcuda" in lowered or "driver" in lowered:
            return "NVIDIA driver not loadable. Install the driver, or check nvidia-smi works"
        if "no capable devices" in lowered or "no such device" in lowered:
            return "no NVIDIA GPU visible to this process"
    if spec.key == QSV.key and ("device creation failed" in lowered or "not found" in lowered):
        return "no Intel Quick Sync device. Confirm the iGPU is enabled in BIOS"
    if spec.key == VAAPI.key and ("failed to initialise" in lowered or "no such file" in lowered):
        return "VAAPI device could not be opened. Check /dev/dri permissions"
    if "unknown encoder" in lowered or "not found" in lowered:
        return f"this FFmpeg build has no {spec.codec}"

    # Fall back to the FIRST line that names the encoder or an error, since
    # FFmpeg's later lines are cascading consequences rather than the cause.
    for line in log.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if spec.codec in stripped or "error" in stripped.lower():
            return stripped[:200]
    return log.splitlines()[0][:200] if log else "probe failed"


def available() -> list[EncoderSpec]:
    """Every backend that passes a real probe on this machine."""
    return [spec for spec in _ALL if probe(spec.key)[0]]


def detect() -> EncoderSpec:
    """Best working backend, preferring GPU. Always returns something."""
    for spec in _ALL:
        if spec.hardware and probe(spec.key)[0]:
            return spec
    return CPU


def select(requested: str | None = None) -> Selection:
    """Resolve a request (or ``auto``) into a working encoder.

    Never raises for a hardware problem: an unavailable GPU falls back to the
    CPU with ``fell_back=True`` and a reason, because a slow render beats a
    failed one. An unknown *name*, though, is a config error and does raise —
    silently ignoring a typo like ``nvnec`` would leave the user believing they
    had GPU encoding.
    """
    requested = (requested or settings.video_encoder or "auto").strip().lower()

    if requested == "auto":
        spec = detect()
        if spec.hardware:
            return Selection(
                spec=spec,
                requested="auto",
                reason=f"auto-detected {spec.label}",
            )
        return Selection(
            spec=CPU,
            requested="auto",
            reason="no working GPU encoder found; using CPU",
        )

    spec = get_spec(requested)  # raises on a typo
    works, why = probe(spec.key)
    if works:
        return Selection(spec=spec, requested=requested, reason=f"using {spec.label}")

    return Selection(
        spec=CPU,
        requested=requested,
        fell_back=True,
        reason=f"{spec.label} unavailable ({why}); fell back to CPU",
        probe_log=why,
    )


def video_args(selection: Selection, *, preview: bool = False) -> list[str]:
    """Output flags for the chosen encoder.

    Preview renders trade quality for speed: they exist to check timing and
    captions, and nobody publishes them.
    """
    spec = selection.spec
    args = list(spec.output_args)

    if preview:
        if spec.key == CPU.key:
            args = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28"]
        elif spec.key == NVENC.key:
            args = ["-c:v", "h264_nvenc", "-preset", "p1", "-rc", "vbr", "-cq", "30", "-b:v", "0"]
        elif spec.key == QSV.key:
            args = ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "30"]
        elif spec.key == VAAPI.key:
            args = ["-c:v", "h264_vaapi", "-qp", "30"]
        elif spec.key == VIDEOTOOLBOX.key:
            args = ["-c:v", "h264_videotoolbox", "-q:v", "40"]

    # VAAPI keeps frames in GPU memory, so -pix_fmt would fight the hwupload.
    if not spec.filter_suffix:
        args += ["-pix_fmt", spec.pix_fmt]
    return args


def input_args(selection: Selection) -> list[str]:
    """Device-setup flags that must precede ``-i``."""
    return list(selection.spec.input_args)


def apply_filter_suffix(selection: Selection, filter_chain: str) -> str:
    """Append the hardware upload step to a filter chain, if the backend needs it.

    VAAPI encodes from GPU surfaces, so the CPU-side chain has to end with
    ``format=nv12,hwupload``. Other backends accept normal software frames.
    """
    suffix = selection.spec.filter_suffix
    if not suffix:
        return filter_chain
    if not filter_chain:
        return suffix
    # The CPU chain may already end in format=yuv420p; the hardware format wins.
    trimmed = filter_chain
    for redundant in (",format=yuv420p", ",format=nv12"):
        if trimmed.endswith(redundant):
            trimmed = trimmed[: -len(redundant)]
    return f"{trimmed},{suffix}"


def describe() -> dict[str, object]:
    """Encoder capability report for the API and the settings UI."""
    entries = []
    for spec in _ALL:
        works, why = probe(spec.key)
        entry = spec.as_dict()
        entry["available"] = works
        entry["detail"] = "" if works else why
        entries.append(entry)

    active = select()
    return {
        "configured": settings.video_encoder,
        "active": active.as_dict(),
        "encoders": entries,
        "gpu_available": any(e["available"] and e["hardware"] for e in entries),
    }
