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
- Local-agent orchestration can continue through YouTube Studio browser publishing. Browser session state is kept in a dedicated persistent Chrome profile outside Git; the legacy Data API publisher is archived.

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
ms_env.sh
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

```bash
git clone https://github.com/yxxrn/manhwashorts-studio.git
cd manhwashorts-studio
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

For development and the full regression suite, install `requirements-dev.txt` instead.

Optional Suwayomi source sidecar (one-time setup):

```bash
python3 scripts/setup_suwayomi.py
```

The setup downloads the pinned official Suwayomi JAR, verifies its SHA-256, and leaves the binary outside Git. ManhwaShorts then starts/stops that sidecar automatically on `127.0.0.1:4567`. Suwayomi ships no default online extensions; configure an extension store/source once before title search. The REST API reports this explicitly as `needs_extension_setup`.

Optional API/UI server:

```bash
.venv/bin/python -m uvicorn app.main:app --reload
```

Then open `http://127.0.0.1:8000`.

## Operator workflow

The production-oriented operator console is the preferred boundary for chapter review/resume work.

Windows launcher:

```text
run_operator.cmd
```

Cross-platform bootstrap:

```bash
python scripts/bootstrap_operator_cli.py
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
  --env-file /path/to/private-ms-env.sh \
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
  --env-file /path/to/private-ms-env.sh \
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
| `MS_TTS_PROVIDER` | `espeak` | Local/offline default; production may use configured HTTP TTS |
| `MS_LLM_PROVIDER` | `rules` | Offline rules default; cloud review uses configured provider/BYOK |
| `MS_VIDEO_ENCODER` | `auto` | CPU/GPU encoder selection |
| `MS_REQUIRE_RIGHTS_DECLARATION` | `false` | Optional blocking rights-enforcement switch |
| `MS_ALLOW_PUBLIC_PUBLISH` | `false` | Public upload remains disabled unless explicitly enabled |

The offline `rules`/`espeak` defaults keep a fresh clone operable for development. They do **not** bypass production approval, evidence, media, or QC gates.

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

- [Current status](docs/STATUS.md)
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

ManhwaShorts Studio does not implement website scrapers itself. Its optional Suwayomi connector can import pages from user-configured Suwayomi/Mihon sources, while preserving them as ordinary ordered source assets. It does not remove watermarks, generate replacement artwork, or automatically authorize publication rights. Imported material remains subject to the same rights metadata and policy gates as manual uploads.

The repository code and third-party assets/services have separate licences. Verify the rights for source art, fonts, models, voices, and provider services before commercial publication.

## Repository

Maintainer: [yxxrn](https://github.com/yxxrn)

Repository: [github.com/yxxrn/manhwashorts-studio](https://github.com/yxxrn/manhwashorts-studio)
