# GPU rendering

Encoding is the slowest part of a render and the only part a GPU can meaningfully
speed up. Since v1.2 you can pick the encoder, or let the app detect one.

Nothing here is required. Without a GPU the app uses `libx264` on the CPU, which
is the v1.0 behaviour and stays fully supported.

## The choice

Per render, in the UI (**6. Render → Encoder**) or via the API:

| Option | Meaning |
|---|---|
| `auto` (default) | Use a working GPU if there is one, otherwise CPU |
| `cpu` | Force `libx264`. Slowest, most predictable file sizes |
| `nvenc` | NVIDIA GPU |
| `qsv` | Intel Quick Sync (integrated graphics) |
| `vaapi` | AMD via Mesa, or Intel as an alternative to QSV |
| `videotoolbox` | Apple Silicon / macOS |

Set a server-wide default with `MS_VIDEO_ENCODER=auto` in `.env`.

## What the app will and will not do

**An unavailable GPU never fails a render.** Ask for `nvenc` on a machine with no
NVIDIA card and the render still completes on the CPU. The job records
`encoder_fell_back: true` with the reason, the UI shows a warning, and the audit
log keeps it. A slow render beats a failed one, but a silent 20x slowdown would
be its own bug.

**A misspelled encoder is rejected.** `nvnec` returns `422` rather than quietly
becoming a CPU render, because otherwise you would believe you had GPU encoding.

**Detection proves the encoder works.** Every FFmpeg build lists `h264_nvenc`
whether or not a GPU is present, so `ffmpeg -encoders` is not evidence. The app
encodes one real frame to a temporary file and checks it is non-empty. Results
are cached for the process lifetime.

## Checking what your machine can do

```bash
curl -s localhost:8000/api/encoders | jq .
```

```json
{
  "configured": "auto",
  "gpu_available": false,
  "active": {"encoder": "cpu", "label": "CPU (libx264)", "hardware": false,
             "requested": "auto", "fell_back": false,
             "reason": "no working GPU encoder found; using CPU"},
  "encoders": [
    {"key": "nvenc", "label": "NVIDIA GPU (NVENC)", "available": false,
     "detail": "NVIDIA driver not loadable. Install the driver, or check nvidia-smi works"}
  ]
}
```

The `detail` field is the useful part: it tells you *why* a backend is
unavailable, which is usually a driver or permissions problem rather than missing
hardware.

## Requirements per backend

### NVIDIA (NVENC)

GTX 900-series or newer, plus a driver FFmpeg can load.

```bash
nvidia-smi                 # must list your GPU
ffmpeg -encoders | grep nvenc
```

`Cannot load libcuda.so.1` means the driver is missing or not visible to the
process, even when the card is physically present. In Docker you need
`--gpus all` and the NVIDIA Container Toolkit.

Concurrent session limits apply: consumer cards historically cap NVENC sessions
(older drivers allowed 2–3), so a single worker is the safe configuration.

### Intel Quick Sync (QSV)

An Intel CPU with the integrated GPU enabled in BIOS — easy to miss on desktops
with a discrete card, where the iGPU is often disabled.

### AMD and Intel on Linux (VAAPI)

Needs a **render node**, not just a card node:

```bash
ls /dev/dri/          # renderD128 must exist, card0 alone is not enough
sudo usermod -aG render,video $USER   # then log out and back in
```

`/dev/dri/card0` exists on VMs with a virtual VGA adapter (QEMU cirrus, for
example) which cannot encode anything. That is why the app checks specifically
for `renderD*`.

In Docker: `--device /dev/dri:/dev/dri`.

### Apple (VideoToolbox)

Works on macOS out of the box, including Apple Silicon. Standard Linux FFmpeg
builds do not include it.

## Speed and quality

Expected on a 60-second Short, measured against the CPU baseline on this project:

| Encoder | Relative speed | Notes |
|---|---|---|
| `libx264 veryfast` | 1x (reference) | ~2x realtime on 2 vCPU |
| NVENC | 5–15x faster | Encoding stops being the bottleneck |
| QSV | 4–10x faster | |
| VAAPI | 4–10x faster | |
| VideoToolbox | 5–10x faster | |

Two honest caveats:

**Hardware encoders produce larger files at similar visual quality.** They
optimise for throughput, not compression. Expect 10–30% more bytes than x264 at
comparable quality. For Shorts, where files are a few MB, this rarely matters.

**Quality knobs are not comparable across vendors.** x264 CRF 20, NVENC CQ 23,
QSV `global_quality 23`, and VAAPI QP 23 are different scales. Each backend
carries its own tuned settings rather than a shared number that would mean
different things per vendor.

**The GPU only accelerates encoding.** Image preparation (Pillow), the Ken Burns
`zoompan` filter, and subtitle rendering (libass) all stay on the CPU. On a fast
GPU those become the new bottleneck, so do not expect the full theoretical
speedup.

## Docker

NVIDIA:

```bash
docker run --gpus all -v ./data:/app/data -e MS_VIDEO_ENCODER=auto manhwashorts
```

AMD / Intel VAAPI:

```bash
docker run --device /dev/dri:/dev/dri -v ./data:/app/data manhwashorts
```

## Troubleshooting

**`gpu_available: false` but I have a GPU** — read the `detail` field from
`/api/encoders`. Usually a driver that FFmpeg cannot load, a missing render node,
or a container without the device passed in.

**Render fell back to CPU** — `encoder_fell_back` and `encoder_reason` are stored
on the job and shown in the UI. The reason is the FFmpeg failure, not a guess.

**Renders are not much faster with a GPU** — encoding is no longer the
bottleneck; `zoompan` and libass are. Check `top` during a render.

**Files got bigger after switching to GPU** — expected. Lower the quality number
(`-cq` / `global_quality` / `-qp`) if size matters more than speed.

**Corrupted or green output** — usually a driver bug. Try `cpu` to confirm the
pipeline itself is fine, then update the GPU driver.
