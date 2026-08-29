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
source assets
  → ingest / source metadata
  → visual + story analysis
  → grounded script / approval
  → TTS + measured word timing
  → timeline / visual planning / subtitles
  → render
  → post-render QC
  → automatic thumbnail package
  → upload-ready artifacts
```

## Service boundaries

```text
app/routers/                       HTTP auth/validation/delegation
app/services/pipeline.py           stable pipeline facade
app/services/pipeline_stages/      stage implementations
app/services/cloud_multimodal.py   stable cloud runner facade/orchestration
app/services/cloud_runner_parts/   cloud stage implementations
app/services/*_contracts.py        dependency-light shared contracts
app/db_base.py                     SQLAlchemy declarative Base
app/models.py                      persistence models
app/services/render.py             FFmpeg/Pillow media engine
app/services/quality.py            QC evaluation
app/services/policy.py             policy findings, including rights audit
app/services/thumbnail.py          upload-ready automatic thumbnails
```

Routers should not implement business rules. New pipeline behavior goes behind the
facade in the relevant stage module. New cloud behavior goes into the relevant
cloud runner part. Shared types move toward a contract module rather than creating
service-to-service cycles.

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

The renderer maintains the production media contract (currently vertical H.264/AAC,
1080x1920 and 60 fps for the approved production profile). Motion/transition logic,
subtitle timing, and panel lineage are persisted and QC'd rather than inferred from
the final MP4 alone.

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
- Runtime/private files are outside source control.

See `MAINTAINER_GUIDE.md` for safe extension rules and `RELEASE_RUNBOOK.md` for the
verification gate.
