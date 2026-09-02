# Architecture

Current implementation architecture. Historical architecture/benchmark logs do not
override this document.

## Core invariants

1. Audio/measured media timing is the timeline clock for voiced production.
2. Stages are resumable/idempotent and replace or reuse their own durable output.
3. Public orchestration facades stay compatible while internal modules may evolve.
4. Evidence, lineage, script approval, media validity, and strict QC fail closed.
5. Rights metadata is audited but is non-blocking by default; enforcement is an
   explicit configuration/product decision.
6. No service layer may create a circular application import.
7. A successful production package includes the final MP4 and automatic thumbnail
   package when thumbnail generation is enabled.

## Data flow

```text
manual source assets OR optional Suwayomi import
  → ordered ingest / strip segmentation / source metadata
  → durable multimodal visual evidence + story map
  → grounded narration + bounded narration/visual repair
  → exact script approval identity
  → TTS + measured word timing
  → exact-panel/ROI timeline + subtitles + deterministic motion/fades
  → final render → post-render QC
  → automatic thumbnail package
  → optional verified YouTube Studio browser publish
```

Every downstream stage carries enough identity/lineage to reuse valid work and reject stale artifacts after source/script/contract changes.

## Service boundaries

```text
app/routers/                       HTTP auth/validation/delegation
app/services/pipeline.py           stable pipeline facade
app/services/pipeline_stages/      stage implementations
app/services/cloud_multimodal.py   stable cloud runner facade/orchestration
app/services/cloud_runner_parts/   visual/story/narration/repair/checkpoint parts
app/services/suwayomi.py           optional localhost source-sidecar adapter
app/services/youtube_accounts.py   persistent Chrome account registry/settings
app/services/youtube_browser.py    verified YouTube Studio browser automation
app/services/publish.py            project publication orchestration/persistence
app/services/*_contracts.py        dependency-light shared contracts
app/services/render.py             FFmpeg/Pillow media engine
app/services/quality.py            QC evaluation
app/services/policy.py             policy findings, including optional rights enforcement
app/services/thumbnail.py          upload-ready automatic thumbnails
```

Routers do not own business rules. Source connectors ingest into ordinary source assets; they do not bypass pipeline/evidence policy. Browser automation owns Studio interaction while `publish.py` owns project/database/idempotency semantics.

## Pipeline facade

`app.services.pipeline` deliberately remains the import surface for routers,
workers, scripts, and tests. It delegates stage bodies to `pipeline_stages` while
retaining legacy monkeypatch/call boundaries. Removing a wrapper because it looks
small is not automatically an improvement; compatibility is part of the design.

## Cloud runner

`CloudStageRunner` composes provider, visual, story-map, narration, narration
repair, and visual-repair mixins. `CloudBatchService` composes durable batch/project
orchestration. `cloud_runner_parts.runtime` binds the facade namespace at call time
so parts can reuse stable dependencies without importing the high-level facade
backwards.

## Visual contracts and motion

Visual scoring, ROI selection, shot planning, and rendering share dependency-light
visual contracts. Avoid importing `render.py` into planning/scoring layers. Subtitle
word/sentence group types live in `subtitle_contracts.py` for the same reason.

The renderer maintains the production media contract (vertical H.264/AAC, 1080x1920 and 60 fps for the approved profile). Evidence-first planning persists exact panel/ROI lineage, caps production reference shots at 4 seconds, applies deterministic push/pull/pan/focus motion plus editorial fades, and QC-checks repetition, unsafe framing/face cutoff, static holds, jitter, subtitles, A/V timing, black frames, and media integrity.

## Thumbnail contract

Successful production calls `thumbnail.generate_thumbnail_package`. It reconstructs
clean imagery from source/panel lineage instead of screenshotting burned subtitles,
ranks visual/headline pairs, renders outline/shadow text without a wide background
banner, and writes `thumbnail.jpg`, `thumbnail_clean.jpg`, variants, metadata, and QC.

## Policy and rights

`policy.check_rights` always exposes the current policy state. With
`require_rights_declaration=False`, it records `rights.enforcement_disabled` as a
non-blocking finding. Rights metadata is still preserved for audit and future
policy changes. Do not conflate "metadata retained" with "publication blocked".

## Reliability and architecture guard

- Render jobs use leases/recovery and keep failed attempts auditable.
- Content/file integrity uses streaming SHA-256 where large files are involved.
- Resume paths reuse valid script/audio/timeline/render/thumbnail identities.
- `tests/contracts/test_service_dependency_graph.py` asserts there are no circular
  application imports.
- Runtime/private files are outside source control, including source media, provider state, and authenticated Chrome profiles.
- Browser publication uses one isolated persistent profile per account and verifies the final Studio row/visibility before recording success; thumbnail failure remains a separate non-blocking outcome.

See `MAINTAINER_GUIDE.md` for safe extension rules and `RELEASE_RUNBOOK.md` for the
verification gate.
