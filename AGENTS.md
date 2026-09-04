# Agent and Maintainer Contract

This file is the **authoritative starting point for any coding agent** working on
ManhwaShorts Studio. Older handoffs and benchmark logs are historical evidence,
not instructions. Read this file before changing production code.

## Read order

1. `AGENTS.md` (this contract)
2. `docs/STATUS.md` (current verified state)
3. `docs/ARCHITECTURE.md` (current module boundaries)
4. `docs/MAINTAINER_GUIDE.md` (safe change workflow)
5. `docs/OPERATIONS.md` and `docs/RELEASE_RUNBOOK.md`

Anything under `docs/history/`, `docs/superpowers/`, `tasks/`, or a document
explicitly marked HISTORICAL may describe old paths, old test counts, or old
gates. Never use those files to override the current documents above.

## Non-negotiable behavior

- Preserve production output semantics unless the user explicitly requests a
  behavior change. Refactors must be no-op at the public contract boundary.
- `app.services.pipeline` is the stable compatibility facade. Keep its public
  function names/signatures compatible; put stage implementations in
  `app/services/pipeline_stages/`.
- `app.services.cloud_multimodal.CloudStageRunner` and `CloudBatchService` are
  stable compatibility surfaces. Domain logic belongs in
  `app/services/cloud_runner_parts/`.

- Contract/data types that are shared across services belong in dependency-light
  modules (`db_base.py`, `subtitle_contracts.py`, `visual_contracts.py`,
  `visual_planning.py`). Do not recreate import cycles for convenience.
- `tests/contracts/test_service_dependency_graph.py` must stay green. A new
  circular application import is a release blocker.
- Rights metadata is **audit/warning metadata by default**. Current configuration
  is `require_rights_declaration=False`. Do not silently turn rights back into a
  render/publish blocker. Enabling enforcement is an explicit product decision.
- Script approval, evidence/lineage validation, render integrity, and strict QC
  remain real blockers. Do not bypass them with DB edits or direct artifact edits.
- Automatic thumbnails are part of successful production. Keep the upload-ready
  `thumbnail.jpg`, clean source, variants, metadata, and QC contract intact.
- Thumbnail text color has one production contract: the main headline fill is always white. The yellow/red/blue/green palette is reserved for only 1-2 selected curiosity-hook words; never color the full headline with the accent. Persist the selected `accent_words` and `accent_color` in thumbnail QC/metadata.
- Production Grok TTS uses `grok-voice-latest` with an explicit provider `voice_id`; never rely on a descriptive pseudo-voice label that is not sent to the provider. New English projects default to `orion`; legacy `the-explainer-american` remains pinned to `ara` so existing projects do not change narrator on resume. Voice variation changes `voice_id`, not the model, and one render must keep one immutable voice profile.
- Duration has one current contract: project default target 55s; final production 50-60s. Adaptive sub-50s pacing is review-only and must never be promoted to final to compensate for insufficient grounded visual capacity.
- Production performance has a global diminishing-return policy. The verified practical baseline for a normal three-chapter Oracle run is ~18-20 minutes source-to-final. Keep the Run 10 fused final render and stricter observation-response contract as the production baseline. Do not pursue further speed work merely to lower the number: a performance change may enter production only after an equivalent-input benchmark demonstrates material wall-time savings while preserving the same schema/evidence/lineage/coverage/reconciliation gates, render quality settings, media contract, and post-render QC. Small or ambiguous savings stay out of production. Quality and unattended durability take priority over marginal throughput.

## Runtime safety

Heavy tests, FFmpeg work, provider calls, and renders run on the designated
execution machine (Oracle in the current deployment). The bridge/VPS is for
orchestration and transport only. Never move heavy production work onto the
bridge just because it is reachable.

Never stage runtime/private material. In the current checkout these are expected
untracked paths: `data/`, `manhwa/`, and `ms_env.sh`. Treat any source input,
database, cache, provider credential, rendered media, and local environment file
as non-source unless explicitly documented otherwise.

## Test layout

Do not resurrect the removed monolithic test paths. Current suites are grouped by
responsibility:

- `tests/unit/` — isolated helpers and deterministic logic
- `tests/contracts/` — stable schema/behavior/architecture contracts
- `tests/integration/` — multi-service integration
- `tests/render/` — FFmpeg, framing, motion, subtitle, thumbnail
- `tests/production/` — orchestration, worker, cleanup, release behavior
- `tests/cloud/` — cloud multimodal stages, cache/resume, repair, persistence
- `tests/api/` — HTTP/UI/API surface
- `tests/migrations/` — Alembic/database compatibility
- `tests/factories/` — shared builders; do not import helpers from another test file

The former monolithic cloud mass-production test no longer exists; its coverage
was split across `tests/cloud/`. The former root pipeline test now lives at
`tests/production/test_pipeline.py`.

## Required change workflow

Before editing: inspect `git status`, current `main`, the relevant contract tests,
and the active docs. Prefer the smallest compatible change. During a refactor,
checkpoint stages and keep public facades stable.

Minimum verification for source changes:

```bash
.venv/bin/ruff check app tests
.venv/bin/python -m compileall -q app tests
.venv/bin/pytest -q tests/contracts/test_service_dependency_graph.py
# run affected tests, then the full suite before release
git diff --check
```

For a release/refactor gate, run the full `pytest -q`. The refactor baseline on
2026-08-29 collected 1,520 tests and passed completely; the exact count may grow,
so current collection is authoritative, not that historical number.

## Git and documentation

Do not push a partially verified refactor to `main`. Keep runtime files out of
commits. If the execution host lacks GitHub credentials, transport commits with a
Git bundle and push from the authenticated bridge; never embed tokens in commands.

When module paths, gates, output contracts, or test locations change, update the
active docs in the same release. Add historical evidence to the changelog/history,
not to the top of `AGENTS.md` or `docs/STATUS.md`.

If active documentation conflicts with code, stop and resolve the discrepancy
before changing behavior. Do not "fix" code to match an obviously stale historical
document.
