# ManhwaShorts Studio

Production-oriented pipeline for turning a chapter folder into a reviewable and QC-gated vertical manhwa recap.

```text
chapter images
  → ingest + segmentation
  → resumable multimodal visual/story analysis
  → grounded five-beat narration
  → explicit human/trusted-agent script approval
  → TTS + measured word timing
  → exact-panel / ROI visual planning
  → motion + subtitles + render
  → pre/post-render QC
  → final MP4 + thumbnail package
  → optional YouTube Studio browser publish + custom thumbnail
```

The current design is built around **durable resume state, evidence lineage, deterministic visual planning, and fail-closed production gates**. Expensive cloud analysis is reused when its checkpoint identity is still valid; a later local failure should not force a full provider rerun.

## Current production contract

- Standard final duration: **50–60 seconds**; new projects target **55 seconds** by default.
- Output profile: **1080×1920, 60 FPS, H.264/AAC**.
- Narration structure: `hook → setup → conflict → twist → CTA`.
- Every approved narration section remains traceable to persisted source evidence.
- Final visual planning uses exact persisted panels/ROIs rather than arbitrary source-page crops.
- Reference visuals are capped at **4 seconds per shot** in the production cadence contract.
- Panel changes use editorial fades; in-panel motion uses deterministic push/pull/pan/focus movement.
- Repetition, duplicate ROI reuse, unsafe framing, face cutoff, static holds, timing drift, black frames, subtitle timing, and media integrity are QC'd.
- Audio timing is authoritative for voiced production; timeline and subtitles follow measured TTS timing.
- Production requires the exact approved script hash + version.
- A successful final package must pass both pre-render and post-render blocking QC.
- Source acquisition may be manual or use the optional localhost Suwayomi sidecar; imported pages enter the same ordered source/evidence pipeline.
- Local-agent orchestration can continue through YouTube Studio browser publishing. Each channel uses an isolated persistent Chrome profile outside Git.
- Publish visibility is request-driven: omitted privacy is `private`; explicit `unlisted`/`public` is honored. Per-account `trust_channel_defaults` can skip static language/category UI work while title, description, tags, thumbnail, audience, and visibility remain uploader-controlled.

## Reliability and resume behavior

The pipeline is intentionally resumable. Durable state is stored under ignored runtime paths and is keyed to the relevant source/script/media identities.

- Segmentation and cloud multimodal stages write checkpoints instead of relying on one long in-memory run.
- Valid cached analysis can be reused after interruption or process restart.
- Script changes invalidate downstream voice/timeline/render identities instead of silently reusing stale media.
- Valid TTS, timeline, render, and thumbnail stages are reused when their production identity still matches.
- A stricter/new QC contract can invalidate an old render and rebuild only the necessary downstream stages.
- Provider credentials, chapter sources, generated media, and local runtime state are never supposed to enter Git history.

Expected runtime-only paths:

```text
data/
manhwa/
.env
```

Do not stage those paths.

## Requirements

- Python **3.11+**
- FFmpeg + FFprobe with `libass`
- A subtitle font
- A configured cloud multimodal provider for the production review workflow
- A configured TTS provider for voiced production
- Java **21+** only when using the bundled Suwayomi source sidecar

GPU encoding is optional. `MS_VIDEO_ENCODER=auto` probes supported encoders and falls back to CPU when necessary.

## Install

`.env` is the single deployment config on Linux and Windows. A fresh machine is
bootstrapped with one command after cloning the repository.

Linux/Ubuntu production host:

```bash
./bootstrap.sh
```

Windows native PowerShell/cmd host:

```text
bootstrap.cmd
```

Both paths prepare Python, FFmpeg, Tesseract, Chrome, Java/Suwayomi, the required
Asura Scans + Read Comics Online sources, Pocket TTS with `alba`, the database,
and a final machine-doctor check. Linux installs boot-persistent systemd services;
Windows creates per-user startup launchers. Pocket prefers INT8 when supported and
falls back to local FP32 instead of failing bootstrap.

To carry an existing deployment config to a new machine:

```bash
./bootstrap.sh --config /secure/path/manhwashorts.env
```

```text
bootstrap.cmd -ConfigPath "C:\secure\manhwashorts.env"
```

YouTube login can be bootstrapped in the same command with a Netscape
`cookies.txt` export. The cookie file is filtered to Google/YouTube domains,
verified against Studio headlessly, and is not retained as plaintext by the app.
Interactive Chrome login remains a fallback only for expired sessions or Google
security challenges.

Existing `.env`, runtime data, database, and authenticated browser profiles are
preserved on rerun. `install.sh` remains the lower-level Linux installer;
`bootstrap.sh` is the normal production entrypoint.

Useful lifecycle commands:

```bash
scripts/manhwashorts doctor
scripts/manhwashorts migrate
scripts/manhwashorts serve
scripts/manhwashorts youtube-account list
```

See `docs/FRESH_MACHINE.md` and `docs/YOUTUBE_SETUP.md` for migration and account
bootstrap details.

## Operator workflow

The production-oriented operator console is the preferred boundary for chapter review/resume work.

Windows launcher:

```text
run_operator.cmd
```

Cross-platform bootstrap:

```bash
python3 scripts/bootstrap_operator_cli.py
```

Direct entrypoint:

```bash
python scripts/run_operator_cli.py
```

The interactive console supports provider setup/test, model selection, one-chapter import, batch import, resume, and review-blocker inspection.

### Production repair without re-running everything

When review analysis is already durable but the latest script/visual repair must be refreshed for production:

```bash
python scripts/run_operator_cli.py \
  --mode repair-production \
  --project-id <project-id> \
  --actor-id <operator-id> \
  --source-root /path/to/chapter
```

A successful repair-production run stops at `READY_TO_RENDER` with voice still waiting for explicit production approval.

### Final production

Final production requires the exact latest approved script identity:

```bash
python scripts/run_operator_cli.py \
  --mode production \
  --project-id <project-id> \
  --actor-id <operator-id> \
  --approved-script-hash <sha256> \
  --approved-script-version <version>
```

Production then reuses or executes, in order:

```text
approved script → TTS → measured timing → timeline → preflight QC
                → final render → postflight QC → thumbnail package
```

If a matching successful render already exists and still passes the current QC contract, it is reused.

## Visual planning

The current visual path is evidence-first:

```text
persisted panel regions
  → visual evidence / scoring
  → section eligibility
  → ROI feasibility
  → shot allocation
  → camera intent + motion curve
  → render
```

Important behavior:

- weak gutters, degenerate crops, and invalid source bounds are rejected;
- face/subject protection is part of framing feasibility;
- unique panels are preferred before safe alternate ROIs are reused;
- repeated canonical panels must remain within the profile cap and use distinct safe ROIs;
- source order/section evidence constrains selection instead of letting a visually strong but unrelated panel win;
- small gaps between narration spans are bridged only inside the same section, not charged to the previous story section;
- motion is kept active without introducing frame-to-frame jitter from crop rounding;
- panel changes use fades while preserving the exact audio-locked duration.

This keeps final selection deterministic and inspectable rather than treating the final MP4 as the only source of truth.

## Output artifacts

Successful production writes an upload-ready package under:

```text
data/output/<project-id>/
```

Core artifacts include:

```text
final.mp4
final.qc.json
final.srt
shot_list.json
subtitle_list.json
panel_to_script_mapping.json
panel_catalog.json
source_rights_report.json
thumbnail.jpg / thumbnail_clean.jpg / thumbnail variants
thumbnail_meta.json
thumbnail.qc.json
```

The exact package may include additional manifests, contact sheets, checksums, or diagnostic files.

## Configuration

See `.env.example` for the full set. The most important production settings are:

| Variable | Default | Purpose |
|---|---:|---|
| `MS_DEFAULT_TARGET_SECONDS` | `55` | New-project target duration |
| `MS_MAX_SHORT_SECONDS` | `90` | Absolute project/media ceiling |
| `MS_TTS_PROVIDER` | `espeak` | Fallback provider when Pocket local-first is disabled/unavailable |
| `MS_TTS_LOCAL_FIRST` | `false` | Prefer local Pocket TTS before the configured fallback provider |
| `MS_TTS_POCKET_URL` | `http://127.0.0.1:8790` | Local Pocket TTS service base URL |
| `MS_TTS_POCKET_VOICE` | `alba` | Locked Pocket narrator voice for a production run |
| `MS_TTS_POCKET_PRODUCTION_SPEED` | `0.90` | Host-validated Pocket speed baseline before duration normalization |
| `MS_LLM_PROVIDER` | `rules` | Offline rules default; cloud review uses configured provider/BYOK |
| `MS_VIDEO_ENCODER` | `auto` | CPU/GPU encoder selection |
| `MS_REQUIRE_RIGHTS_DECLARATION` | `false` | Optional blocking rights-enforcement switch |

The offline `rules`/`espeak` defaults keep a fresh clone operable for development. They do **not** bypass production approval, evidence, media, or QC gates.

For local neural narration, run `scripts/setup_pocket_tts.sh`, then enable `MS_TTS_LOCAL_FIRST=true`. The production path locks one Pocket voice for the whole video and performs at most one full-session fallback to the previous provider if Pocket is unavailable or fails validation. The Oracle deployment uses INT8 Pocket TTS on localhost with `alba` as the default voice.

## Architecture

```text
FastAPI / operator CLI
        │
        ├── app.services.pipeline
        │      └── pipeline_stages/
        │             analysis · script · media · quality · production · rendering
        │
        ├── app.services.cloud_multimodal
        │      └── cloud_runner_parts/
        │             provider · visual · story · narration · repair · resume
        │
        ├── director / visual scoring / ROI / framing / camera planning
        ├── TTS / subtitles / render / thumbnail / policy / quality
        └── SQLAlchemy persistence + content-addressed/runtime storage

FFmpeg + Pillow handle deterministic media execution.
```

`app.services.pipeline` and `app.services.cloud_multimodal` are stable orchestration facades. Internal stage modules can evolve without making routers, workers, scripts, or tests depend on implementation details.

## Validation

Before merging production changes:

```bash
.venv/bin/ruff check app tests scripts alembic
.venv/bin/python -m compileall -q app tests scripts alembic
.venv/bin/python -m pytest -q -m 'not slow'
.venv/bin/python -m pytest -q
git diff --check
```

The slow/render suites exercise real FFmpeg behavior and verify codec/container output, framing, subtitles, audio/video drift, black frames, motion, production orchestration, resume behavior, and release gates.

## Documentation

- [Documentation index](docs/INDEX.md)
- [Current status](docs/STATUS.md)
- [Fresh-machine setup](docs/FRESH_MACHINE.md)
- [Agent API guide](docs/AGENT.md)
- [YouTube Studio publishing](docs/YOUTUBE_SETUP.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Operator CLI](docs/operator-cli.md)
- [Operations](docs/OPERATIONS.md)
- [Motion-comic pipeline](docs/MOTION_COMIC.md)
- [Visual selection](docs/VISUAL_SELECTION.md)
- [Maintainer guide](docs/MAINTAINER_GUIDE.md)
- [Release runbook](docs/RELEASE_RUNBOOK.md)
- [API reference](docs/API.md)
- [BYOK](docs/BYOK.md)
- [TTS options](docs/TTS_OPTIONS.md)
- [GPU rendering](docs/GPU.md)
- [Copyright / rights model](docs/COPYRIGHT.md)

## Scope and rights

ManhwaShorts Studio does not implement website scrapers itself. Its optional Suwayomi connector can import pages from user-configured Suwayomi/Mihon sources, while preserving them as ordinary ordered source assets. It does not remove watermarks, generate replacement artwork, or automatically authorize publication rights. Imported material retains the same rights metadata/audit model as manual uploads; rights enforcement is optional and disabled by default.

The repository code and third-party assets/services have separate licences. Verify the rights for source art, fonts, models, voices, and provider services before commercial publication.

## Repository

Maintainer: [yxxrn](https://github.com/yxxrn)

Repository: [github.com/yxxrn/manhwashorts-studio](https://github.com/yxxrn/manhwashorts-studio)
