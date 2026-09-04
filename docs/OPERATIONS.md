# Operations

Run heavy work on the designated execution host. In the current deployment the
bridge/VPS is orchestration/transport only and Oracle is the execution machine for
provider calls, FFmpeg, renders, and full regression tests.

## Start and environment

```bash
.venv/bin/python -c "from app.services.render import check_environment; print(check_environment() or 'OK')"
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Keep the service behind an authenticated tunnel/SSH forward rather than exposing a
raw development server publicly. A standalone render worker is available with
`.venv/bin/python scripts/worker.py`.

## Unattended production runner

For a fresh source-to-final run that must be safe to leave unattended, use the blessed launcher rather than an ad-hoc Python script:

```bash
scripts/manhwashorts production-run --run-id <id> --title "<title>" --chapter-from <n> --chapter-to <n> --source-id <suwayomi-source-id> --language en
```

The launcher requires `ms_env.sh`, takes an exclusive per-run lock, runs production-environment/machine/disk/source/vision/TTS preflight **before** creating or importing a project, and checkpoints every completed stage under `data/production-runs/<run-id>.json`. Re-running the same command resumes the same corpus and reuses validated source, analysis/provider caches, approved script, render identity, and final artifacts. Provider/transport retries are bounded; evidence, lineage, QC, and deterministic capability failures remain fail-closed. A PASS run is an idempotent no-op on later invocations.

## Production TTS baseline

The production HTTP TTS model is `grok-voice-latest`. The Grok `/v1/tts` request must include the actual provider `voice_id`; the old descriptive value `the-explainer-american` is a compatibility alias only and remains pinned to `ara` for old-project compatibility. The provider language parameter is `en` for English; narrator accent/timbre is selected by the voice, not by pretending the provider language field is a voice selector.

New projects default to `orion`. The maintained variation shortlist is `orion`, `luna`, `ara`, `lux`, and `altair`; the `/api/voices` surface also exposes the complete built-in list confirmed by the active provider documentation. Unattended runs can select one explicitly with `--voice-id <id>`. Keep one voice/model identity for every section in a render; provider failure remains fail-closed with no silent narrator fallback.

## Production workflow

1. Ingest ordered source material manually or through the optional Suwayomi sidecar; preserve source provenance and rights metadata.
2. Run the visual/story analysis flow and inspect any review boundary.
3. Generate/edit the grounded script and approve the exact script identity. Normal final target is 55s and the accepted final window is 50-60s.
4. Generate voice/timing and timeline through the normal pipeline facade.
5. Run quality checks; resolve blocking evidence/media/QC failures.
6. Queue or resume final render rather than duplicating a valid successful job.
7. Verify final media integrity and post-render QC.
8. Verify the automatic thumbnail package and thumbnail QC.
   Thumbnail contract v5 keeps the main headline white and applies one contrast-selected accent color only to 1-2 hook words. Full-headline accent coloring is invalid production output.
9. Upload/publish only through the supported explicit operator/API boundary using an authenticated persistent Chrome account. Omitted visibility is Private; explicit Unlisted/Public is honored and Studio result/visibility must be verified.

A sub-50-second `coherent_capacity_adaptive_v1` result is a review/diagnostic artifact, not a final-production exception. Increase grounded visual/story capacity rather than stretching a few panels or silently accepting a short final.

## Rights behavior

Rights fields/status remain useful audit metadata. The current default is
`MS_REQUIRE_RIGHTS_DECLARATION=false`; missing declarations do **not** block
render/publish under that default. Do not turn enforcement on as an incidental
operational fix. If enforcement is intentionally enabled, policy findings become
blocking according to `app/services/policy.py`.

## Verification commands

Fast/targeted checks:

```bash
.venv/bin/ruff check app tests
.venv/bin/python -m compileall -q app tests
.venv/bin/pytest -q tests/contracts/test_service_dependency_graph.py
.venv/bin/pytest -q <affected test paths>
git diff --check
```

Cross-service/refactor/release changes also require `.venv/bin/pytest -q`.
Use current test paths documented in `MAINTAINER_GUIDE.md`; do not revive removed
historical monolithic test files.

## Recovery

- Check for an already-running long process before starting another one.
- Reuse durable visual/story/narration/render state when identity checks say it is
  current. Do not repeat expensive provider work solely because a chat resumed.
- Expired render leases are recovered through the normal worker boundary.
- Retry preserves previous job/audit records.
- Never repair state by hand-editing the DB, QC JSON, or final artifact.
- If YouTube publishing fails, keep/reuse the same `youtube_account_id`; fix browser authentication/Studio issues and retry the failed Publication rather than re-rendering. Thumbnail-only failure is non-blocking and is corrected manually in Studio if browser persistence retry also failed.

## Repository hygiene

Before commit/push:

```bash
git status --short
git diff --check
git diff --cached --stat
```

`data/`, `manhwa/`, and `ms_env.sh` are expected runtime-only paths in the current
checkout. Never stage them. Secrets/tokens must not appear in shell commands,
patches, logs, or documentation.

If the execution host lacks GitHub credentials, create a Git bundle for the source
commits and push it from the authenticated bridge. Do not install/copy a personal
token into the execution host just to make a push succeed.

## Global production performance policy

The accepted production baseline is approximately **18-20 minutes for a normal three-chapter Oracle run from source import through final render, QC, thumbnail, and metadata**. This is a practical operating target, not an SLA or a gate: provider latency and corpus size can move an individual run above or below it.

### Optional final-video watermark

Projects expose `watermark_enabled` and `watermark_text`. The unattended runner mirrors them with `--watermark/--no-watermark` and `--watermark-text`. When enabled, the watermark is rendered lower-center at the final stage with translucent white Caacupe One text, synthetic bold, and subtle dark outline/shadow, using the bundled watermark font asset (not the subtitle font) through the existing ASS/libass overlay path in the same encode pass as subtitles. It is disabled for previews/review artifacts. The current RuruShorts channel watermark text is `@Rurushortss`. Caacupe One is bundled under `assets/fonts/` with its OFL under `licenses/fonts/`; its checksum participates in render identity. Watermark settings plus the bundled font checksum are included in render identity, so changing the toggle, text, or font asset forces a new final render instead of reusing a stale MP4. `watermark.json` is rewritten from the final render manifest after checksum and removed automatically when watermark is disabled.

Run 10 established the default performance baseline: keep the fused xfade/subtitle final encode and the stricter observation-response instruction. Further optimization is intentionally subject to diminishing-return discipline. Do not change quality settings, loosen evidence/lineage/coverage/reconciliation validation, weaken QC, increase unsafe provider pressure, or complicate the production path merely to save marginal time.

A new optimization may replace the baseline only when an **equivalent-input benchmark** shows a **material and repeatable wall-time improvement** and the same output/quality contracts still pass. Small, noisy, corpus-only, or ambiguous gains are rejected. Prefer unattended durability and consistent output quality over shaving additional minutes from an already acceptable run.

## Oracle measured render budget

The current Oracle host has 20 logical CPUs. Run 7 measured the full-equivalent final render at worker/thread budgets 1/10, 2/5, and 3/3; one scene worker with 10 x264 threads was fastest. Keep the repository default at automatic (`0`) for portability, and set `MS_RENDER_WORKERS=1` plus `MS_RENDER_X264_THREADS=10` in the Oracle runtime environment. Preserve libx264 preset slow, CRF 18, High profile, 1080x1920, and 60 FPS.

When launching production scripts outside the installed service wrapper, source the runtime environment first. A missing provider environment must fail closed as `vision_capability_missing`; do not bypass that gate or repeat source ingest merely to recover the launcher.
