# Final Production Silent Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. The active equivalent here is inline Luna execution with Sol-style review checkpoints; those named superpowers skills are not assumed to be installed.

**Goal:** Produce one real, review-only silent 50--60 second production preview from `final_test/` using the configured multimodal model and the regular renderer, while closing the missing local-operator context gate.

**Architecture:** Reuse the existing `OperatorCLI`, encrypted `credentials`, `resolver`, `CloudStageRunner`, `CloudBatchService`, strip segmentation, reference framing, and `render_video` contracts. Add only a local context bootstrap and a thin acceptance orchestrator/report writer where current CLI composition cannot express the isolated silent run. The provider remains an adapter; local validation owns lineage, hashes, QC, and safe errors.

**Tech Stack:** Python 3.11 `.venv`, SQLAlchemy/SQLite, Pillow, existing OpenAI-compatible vision adapter, FFmpeg/ffprobe, pytest, Ruff.

## Global Constraints

- All execution is local on `B:\Project\manhwashorts-studio`; preserve untracked `final_test/` and never use the VPS.
- No credential, raw provider payload, source image, runtime DB, generated media, or temporary environment enters Git.
- The provider must be configured through encrypted BYOK; local code computes canonical hashes and never trusts provider hashes.
- Use one pinned model identity across visual evidence, causal map, and Sharp Friend narration.
- Examine every ordered material panel; no random or sample selection.
- `publish_allowed=false`, `approval_state=PENDING_EDITORIAL_REVIEW`, and voice/TTS/audio/publication remain deferred.
- Legacy/profile=None and default v2 behavior remain compatible.
- Silent review timing is provisional pacing only and must not satisfy authoritative voiced-render timing gates.

## Current interfaces

- `app.services.operator_cli.resolve_operator_context(db) -> tuple[User, Workspace]` currently raises `operator.context_missing` when no active user exists; it creates a missing workspace only for an existing user.
- `OperatorCLI.setup_provider()` performs endpoint normalization, hidden-key input, model discovery, encrypted `credentials.save_credential(..., verify=True)`, and requires context before save.
- `operator_cli.run_capability_probe(db, workspace_id, consent=True, model=...)` creates a deterministic 48x48 PNG and calls `CloudStageRunner.run_visual_evidence` through `resolver.resolve_vision`.
- `cloud_multimodal.CloudStageRunner.run_visual_evidence`, `.run_story_map`, `.run_narration`, and `.run_chapter` are ordered stage boundaries with local reconciliation and request accounting.
- `cloud_multimodal.CloudBatchService.run_project(db, project_id, actor_id=...)` performs segmentation, resumable stages, DB persistence, and returns `READY_TO_RENDER` while `regular_render_allowed` remains false until voice timing.
- `render.render_video(request, ...)` already accepts `silent_reference_review=True`, exact reference scene lineage, sentence groups, and no-audio output; `subtitle_karaoke` owns two-line chunking and active-word styling.

## Task 1: Local operator context bootstrap

**Files:**

- Modify: `app/services/operator_cli.py`
- Test: `tests/test_operator_cli.py`
- Test: `tests/test_operator_context.py` (new if no existing context fixture can express fresh/existing DB cases)

**Interface:**

```python
def ensure_local_operator_context(db: Any) -> tuple[User, Workspace]:
    """Return the first active local operator and owned workspace, idempotently."""
```

- [ ] Write body-level RED tests for a fresh empty SQLite DB, existing user/workspace preservation, user-without-workspace creation, repeated calls, cancellation before provider setup, and audit origin `local_operator_cli`.
- [ ] Run `\.venv\Scripts\python.exe -m pytest tests/test_operator_context.py tests/test_operator_cli.py -q`; expected RED is the fresh context path raising `operator.context_missing`.
- [ ] Implement the helper with deterministic local display name/email values already accepted by `User`, reuse the oldest active user/workspace, create only missing rows, add the existing audit service event, and flush without touching credentials.
- [ ] Replace setup/test/select/import/status calls’ direct `resolve_operator_context` use with the helper; retain the existing function as a compatibility wrapper if tests import it.
- [ ] Rerun the same command and require all context/CLI tests green; confirm no key-bearing data is in audit details.

## Task 2: Real provider/model capability gate

**Files:**

- Modify: `app/services/operator_cli.py` only if a context-resume or model-selection correction is required
- Test: `tests/test_operator_cli.py`
- Test: `tests/test_vision_capability.py`
- Runtime artifact: ignored acceptance report only

- [ ] Add a regression that setup resumes after creating context and does not request endpoint/key a second time.
- [ ] Run the focused provider matrix with fake adapters first; expected RED is the missing-context setup path.
- [ ] Use the real interactive console boundary to configure the authorized endpoint/key, fetch models, require exact listed ID `ag/gemini-3.6-flash-high`, and run the explicit capability probe. Do not put the credential in a command, environment, fixture, log, or report.
- [ ] Record only sanitized result category, model ID, request count, estimated cost if available, selected BYOK profile label, and capability code. If the exact model is absent or the structured probe fails, stop with a blocker and do not substitute a model.
- [ ] Verify encrypted BYOK storage exists only in the local runtime DB, not plaintext files; do not remove unrelated user profiles. If a temporary profile was created and can be deleted without affecting existing records, delete it through `credentials` and verify the ciphertext row is gone; otherwise report its safe label for revocation.

## Task 3: Real chapter ingest and all-panel evidence

**Files:**

- Modify: `app/services/operator_cli.py` or `scripts/run_cloud_multimodal_batch.py` only if the existing entrypoint cannot pass the selected model/isolated output paths
- Test: `tests/test_cloud_multimodal.py`
- Test: `tests/test_operator_cli.py`
- Runtime: ignored job state/review bundle under `data/`

- [ ] Add a fake-provider integration test for deterministic `final_test`-style folder order, context bootstrap, stage isolation, and resume state.
- [ ] Run the fake-provider RED/GREEN matrix before any real media/provider call.
- [ ] Discover `final_test/` with `discover_chapter_folder`, validate all 20 input files, import through `import_chapter_folder`, and let `prepare_project_panels`/strip reconciliation create ordered regions without dropping bytes.
- [ ] Run `CloudBatchService.run_project` with the verified model identity and isolated `state_dir`/`review_root`. Require visual/story/narration stage hashes, complete coverage, `sharp_friend_v1`, no unknown balloon masks, no unsupported claims, and `READY_TO_RENDER` or a truthful review blocker.
- [ ] Verify request count/model/prompt hashes and persisted `StoryAnalysis`/script lineage. Do not hand-edit the DB or relabel legacy analysis.

## Task 4: Build review-only silent render request

**Files:**

- Modify: `app/services/pipeline.py` and/or `app/services/render.py` only at the existing reference silent-review boundary
- Test: `tests/test_reference_visual_review.py`
- Test: `tests/test_regular_render_karaoke.py`
- Runtime: ignored edit/shot plan and render audit bundle

- [ ] Write RED coverage for provisional pacing being labelled separately from voice timing, all material panels being represented in chronological scene/evidence mappings, `publish_allowed=False`, no audio inputs, and no call to `tts_svc`.
- [ ] Use the existing exact panel candidate/timeline builder to create selected ROIs and accepted framing telemetry; never reselect from an asset-level map or apply panel coordinates to a full strip.
- [ ] Derive provisional word timings from the approved narration duration/pacing only in the isolated request, pass sentence groups to `subtitle_karaoke.build_sentence_caption_groups`, and preserve the punctuated spoken text unchanged.
- [ ] Build `RenderRequest` with `silent_reference_review=True`, `audio_path=None`, `music_path=None`, 1080x1920/60 FPS, reference profile, and a new ignored output directory. Ensure the normal voiced-render gate still rejects this timing source.
- [ ] Run focused render/subtitle tests and require two-line maximum, 77px-equivalent font, 120px margin, yellow 1.08 active-word styling, punctuation-free display, crop/balloon/blank/protected QC, and zero audio.

## Task 5: Technical/visual audit and deterministic artifact bundle

**Files:**

- Create: `scripts/review/run_final_silent_acceptance.py` only if the CLI cannot produce the required compact bundle without duplication
- Test: `tests/test_final_silent_acceptance.py`
- Modify: `docs/STATUS.md`, `CHANGELOG.md`, `docs/operator-cli.md`

- [ ] Add collection-clean RED tests for artifact schema, path isolation, SHA-256 reporting, no secret/raw payload fields, ffprobe requirements, black-frame rejection, caption coverage, and pending approval state.
- [ ] Implement only a thin report writer that consumes existing stage/render outputs; it must write atomically under a new ignored run directory and never copy source images.
- [ ] Render with the regular production path, not the preview script, using CPU if required. Target 50--55 seconds and accept only 50--60 seconds.
- [ ] Run `ffprobe`, `blackdetect`, frame extraction, contact-sheet generation, and representative subtitle-boundary/crop audits. Inspect the contact sheet and key frames with the image viewer; iterate only on deterministic defects.
- [ ] Confirm 1080x1920, 60 FPS, H.264 High, yuv420p, zero audio streams, no black frames, complete chronology, no balloon/edge-blank/crop loss, and measured subtitle max two lines.
- [ ] Update docs with exact artifact paths/hashes, model/request result, tests, rollback, and explicit future voice status. Commit source/tests/docs only, then push the implementation branch and fast-forward `main` only after the real artifact passes.

## Task 6: Future manual voice handoff (plan-only)

**Files:** documentation only in this task; implementation is deferred.

- Export `voice_script.txt` with punctuation-bearing text and configurable emotion tags; derive clean spoken/display files independently.
- Add a `WAITING_FOR_VOICE` CLI state with export instructions and external WAV/MP3/M4A import.
- Validate audio, reconcile actual word timings, normalize, resync karaoke, and reject mismatches with auditable diagnostics.
- Pin `VoiceProfile` identity; a provider/model/voice change requires a new audition and approval.
- Add unit/integration tests for export, tag stripping, audio validation, alignment, replacement, retry, and final-render gate.
- Do not implement or generate voice before the silent preview is accepted.

## Verification matrix and release gate

Run after each green task:

```powershell
\.venv\Scripts\python.exe -m pytest tests/test_operator_context.py tests/test_operator_cli.py tests/test_vision_capability.py -q
\.venv\Scripts\python.exe -m pytest tests/test_cloud_multimodal.py tests/test_reference_visual_review.py tests/test_regular_render_karaoke.py -q
\.venv\Scripts\ruff.exe check app tests
\.venv\Scripts\python.exe -m compileall -q app scripts
git diff --check
```

Before release, run the dependency-complete non-slow suite with the repository’s
known Windows compatibility shim only if required by the existing tests, and
record the exact collected/passed/skipped counts. Scan the staged diff for
secret-shaped values and allow only source/tests/docs; runtime DB, `final_test/`,
job JSON, review images, MP4s, and credentials must remain untracked/ignored.

## Self-review checklist

- Every requested production stage maps to a current callable boundary.
- Local context creation is explicit, idempotent, audited, and does not bypass
  approval/rights/provider gates.
- Model selection is exact and capability-probed rather than inferred.
- Provisional silent timing cannot satisfy final voice timing.
- The future voice flow is specified but not implemented.
- No command contains a credential and no report can serialize raw provider data.
- No source image/media/runtime DB is committed.
