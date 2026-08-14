# Current status

Updated: 2026-08-14

## Production long-strip segmentation - 2026-08-14

- Implemented the color-agnostic source-strip reconciliation boundary on
  `codex/color-agnostic-strip-segmentation` from rollback parent
  `c5a170a551f0ff22cecc23653563bb3c649dcfcd`. The new
  `app.services.strip_segmentation` contract preserves ordered source
  lineage, integer bounds, original checksums, complete top-to-bottom coverage,
  deterministic candidate ranking, bounded pixel analysis, and local canonical
  analysis hashes. `strips.slice_strip` keeps the complete source extent and
  now uses `color-agnostic-gutter-v2` structure/context candidates; the legacy
  ingest API and normal portrait behavior remain compatible.
- Detector candidates use within-row structure/texture, row-to-row colour
  continuity, sustained bands, and edge/context contrast rather than a
  white/black brightness assumption. Flat sky/wall-like artwork without strong
  separator context is not accepted as a gutter. High-confidence deterministic
  gutters may reconcile without a provider; artwork-connected or otherwise
  ambiguous strips remain one auditable span with
  `segmentation.ambiguous_boundary` and `NEEDS_REVIEW`. A provider-protected
  boundary is rejected as `segmentation.protected_boundary`; malformed source,
  checksum, overlap, gap, coordinate, hash, OCR-only geometry, and pixel-budget
  inputs fail closed without dropping or duplicating pixels.
- `CloudStageRunner.assess_strip_boundaries` uses the pinned
  `strip-boundary-assessment-v1` prompt (canonical LF SHA-256
  `b01302bc92536a9ded8581687b094ef88e5688fb184fd750b2496a10ef93d073`) and
  sends every candidate plus overlapping source tiles. Provider responses are
  untrusted: they cannot supply hashes, must echo source lineage, use supplied
  coordinates, set `random_sampling=false`, and provide validated protected
  regions. Local code owns all hashes and rejection decisions. A missing BYOK
  model still permits only the local high-confidence path; the cloud batch CLI
  itself stops at `cloud.credential_missing` rather than falling back.
- `prepare_project_panels` reconciles source families before constructing
  visual inputs. When ingestion already produced multiple pieces, the pieces
  are rebuilt transiently from their exact bounds for one boundary assessment;
  the reconstructed bytes are never persisted or substituted for source
  assets. Ambiguous reports write sanitized JSON plus a thumbnail under the
  ignored `data/segmentation-review/` directory. `CloudBatchService` records
  segmentation state before visual/story/narration stages and preserves
  isolated `NEEDS_REVIEW`/`FAILED` resume behavior. The operator command is:
  `python scripts/run_cloud_multimodal_batch.py --project-id PROJECT_ID
  --segmentation-review-dir data/segmentation-review --state-dir
  data/cloud-multimodal-jobs --model MODEL_ID`.
- TDD evidence: collection-clean initial boundary RED was `9 failed, 0
  collection errors` for the absent detector/reconciliation boundary. Final
  focused cloud integration is `13 passed`; strip/segmentation/coverage
  regressions are `52 passed`; the full dependency-complete non-slow gate is
  `909 selected, 908 passed, 1 existing skip, 0 failed` (15 slow tests
  deselected). Ruff, compileall, diff-check, and no-churn comparisons are
  green. The run used only an external disposable dependency environment and
  Windows compatibility shim; neither is part of the repository.
- No real cloud request, source-media render, voice/TTS/audio, UI, database
  migration, rights bypass, or publication occurred. Review artifacts remain
  ignored; `publish_allowed` and the existing approval/voice gates are
  unchanged. Next gate: configure a verified BYOK model for a real chapter;
  unresolved segmentation must remain `NEEDS_REVIEW` before visual evidence.

## Cloud multimodal mass-production MVP - 2026-08-14

- Implemented on branch `codex/cloud-multimodal-mass-production` from clean
  main `93fd8c99700125a5af20322718d5e1593bd4271a`. The new generic
  `app.services.cloud_multimodal` boundary uses the existing verified BYOK
  OpenAI-compatible vision adapter for three separate, pinned stages:
  `balloon-free-visual-evidence-v1` (`7abd1a456903fa5b46dc047b9d24cee02578fa7da027ebc14cde18a264bd2534`),
  `cloud-causal-map-v1` (`c0494942be104bacd664feb696b64e1909fea3ccae4e2a25e8bbfcefd6aa7db2`),
  and `vision-first-story-analyzer-v3`
  (`b93961d980c0ace1354611b2b78951400945def2ed13f6aa4f43557f5780869b`).
  Sharp Friend v1 remains locally verified at profile hash
  `134b544c9e2f74ca0b8c64ff55a27c831e76f77a08f26fc2a463112cb0678b3e`.
- Stage A consumes every ordered panel and reconciles provider geometry into
  local canonical evidence hashes; provider-supplied hashes are rejected.
  Unknown balloon geometry, OCR-only known geometry, foreign/duplicate panels,
  invalid boxes/confidence, incomplete source coverage, causal-map gaps,
  unmapped narrative claims, copied dialogue/CTA/hype, and duration/QC failures
  fail closed. Stage B consumes the complete ordered visual result with
  `random_sampling=false`; Stage C validates the existing Sharp Friend v1
  analyzer contract and derives uppercase punctuation-free display words
  independently from punctuation-bearing spoken text.
- `prepare_project_panels` reuses current segmentation, source bytes, integer
  panel bounds, source checksums, and coverage hashes. `persist_cloud_chapter`
  writes a reconciled `StoryAnalysis` and `ScriptVersion` only after all three
  stages pass, through the existing v3 validation/persistence path. It never
  relabels legacy evidence and leaves `editorial_review_confirmed=false`.
- `CloudBatchService` and `scripts/run_cloud_multimodal_batch.py` support
  repeated project jobs, isolated `FAILED`/`NEEDS_REVIEW` records, atomic JSON
  resume state, stale source/model/prompt cache rejection, bounded retries,
  configurable request budgets/rate spacing/estimated cost, and a deterministic
  concurrency cap. Example after configuring an existing verified BYOK LLM
  credential (no secret is passed on the command line):
  `python scripts/run_cloud_multimodal_batch.py --project-id PROJECT_ID
  --state-dir data/cloud-multimodal-jobs --model MODEL_ID`.
  The command stops at `READY_TO_RENDER`/review-only because authoritative voice
  word timings are absent; `regular_render_allowed` remains false and
  `cloud.voice_timing_required` blocks final audio/video rendering. No local
  fallback, provider call, TTS, audio, or media was run in this checkpoint.
- A frozen `VoiceProfile` contract now records provider/model/version,
  voice/reference identity, locale, speed/style/stability, approval state, and
  a local profile hash. Changing an approved identity raises
  `voice_profile_reapproval_required`; no voice provider was selected or called.
- TDD evidence: collection-clean initial RED was `8 collected, 8 body
  failures` for the absent cloud/voice boundaries. Focused final GREEN is
  `13 passed`. The related non-slow vision/analyzer/QC/pipeline/render/voice
  matrix is `142 passed, 13 deselected`. The full authoritative disposable
  non-slow suite is `888 passed, 1 existing skip, 15 deselected, 0 failed`.
  The 15 deselected tests are the existing slow-marked pipeline/render tests;
  the Windows dependency-complete run used an external path-normalization and
  LF snapshot shim only, never committed. Ruff, compileall, and diff checks
  are green. The next gate is real BYOK configuration plus a real chapter
  run; source rights, explicit editorial approval, voice/TTS/audio, final
  render, and publication remain blocked/deferred.

## Regular production render: sentence-chunked karaoke and evidence-gated framing - 2026-08-14

- The regular `app.services.pipeline.build_render_request` /
  `app.services.render.render_video` path now consumes the shared
  `sentence_chunked_word_karaoke_v2` contract when an explicit reference
  profile is selected. It preserves punctuation-bearing spoken text, requires
  persisted provider word timings, and derives an independent uppercase,
  punctuation-free display surface. Complete deterministic semantic chunks
  stay visible while the active word is yellow at `1.08`; inactive words are
  white; Barber Chop is bold italic at `0.04 * 1920 = 77px`, with a hard two-line
  maximum and 120px side margins. Missing audio/word timing fails with
  `subtitle.word_timing_missing`; no provider timing is invented. `profile=None`
  continues through the legacy ASS path.
- Regular reference renders now require persisted Task5/Task7 panel lineage,
  exact panel crop/evidence/mask/ROI/telemetry snapshots, `publish_allowed=false`,
  and the existing hard balloon/protected-region/framing gates before FFmpeg.
  Profile/detector or crop/mask mismatches fail closed with stable visual codes;
  there is no silent legacy fallback. Final profile output normalizes full-range
  image input to TV-range `yuv420p` before the H.264 High gate. The regular
  manifest is `regular_render_manifest_v1` and now records subtitle contract
  metadata plus measured max lines/active-word events, source timing lineage,
  per-shot evidence/mask hashes, ROI, telemetry, fallback ledger, and rejection
  fields.
- The production boundary was verified with a deterministic **synthetic**
  23-panel typed-evidence fixture through `render_video` (not the review script).
  Artifact:
  `data/regular-render-karaoke-production-synthetic-20260814/regular-production-synthetic-50s-silent.mp4`
  is exactly 50.000000 seconds, 1080x1920, 60fps, H.264 High/yuv420p,
  video-only, SHA-256
  `572c7bbd8a38160781419a492b2f2ab5479d52c6a83df5311d14bd871281a2d5`,
  17,863,299 bytes. `ffprobe.json`, `blackdetect.txt`, the 10-frame contact
  sheet, 20-frame subtitle-boundary sheet, representative frame audit, regular
  manifest, and `synthetic_render_summary.json` are in the same ignored
  directory. Output QC is zero blackdetect intervals, 0 audio streams, 11
  active-word events, measured maximum 1 line, and `publish_allowed=false`.
- The real chapter was not relabeled or rendered as production. The current
  `data/sample.db` has 24 source assets but `story_analyses=0`; the reviewed
  bundle remains `codex_manual_vision_reference_v1` with
  `production_evidence=false` and `PENDING_EDITORIAL_REVIEW`. A real order-1
  regular-path probe stopped before FFmpeg with
  `visual.balloon_mask_unknown`. The ignored blocker report is
  `data/regular-render-karaoke-production-synthetic-20260814/real-chapter-readiness.json`;
  it records the database SHA-256, `PRAGMA integrity_check=ok`, schema counts,
  and `ready_for_real_chapter_regular_render=false`.
- TDD evidence for this slice: initial regular boundary RED was collection-clean
  with `5 collected, 4 failed, 1 passed`; the later output-format RED was one
  body failure (`yuvj420p` at the final gate), then GREEN after the explicit
  range/pixel-format filter. Final focused production/reference matrix is
  `109 passed` plus the final targeted regular timing-boundary regression;
  the related reference/framing/subtitle/motion/pipeline matrix
  is `91 passed, 1 skipped, 14 deselected`. The authoritative LF-preserving
  disposable full non-slow run collected `867`: `866 passed, 1 existing skip,
  0 failed`. The primary Windows checkout still reports only the known
  environment presentation failure in
  `test_v3_prompt_resource_is_lf_utf8_and_normative` because
  `core.autocrlf=true` exposes the committed LF prompt as CRLF. `tests/test_pipeline.py`
  retains its existing vision-only draft-gate failures when explicitly selected;
  no gate was weakened.
- Ruff, `compileall -q app`, `git diff --check`, and line-ending/no-churn
  comparisons are clean. No voice, TTS, music, SFX, provider, DB/schema,
  subtitle/video publication, UI, rights bypass, or deployment action occurred.
  Rollback point is `0db8aea`; the implementation branch is
  `codex/regular-render-karaoke-production`. Main remains unchanged until the
  real chapter has current persisted typed visual evidence and passes the
  regular reference QC gate.

## Two-line semantic karaoke silent preview - 2026-08-14

- Corrected the local Sharp Friend preview from the `76fd6f1` baseline so a
  sentence is held as a complete display block until its next deterministic
  semantic/pause chunk, while the authoritative word cue still highlights
  only the active word in yellow with the existing `1.08` scale bump. The new
  contract is `sentence_chunked_word_karaoke_v2`: 19 chunks, 122 word
  dialogues, punctuation-free uppercase/alphanumeric display text, and no
  chunk shorter than `1.229508` seconds. Long sentences split only at
  deterministic punctuation/semantic boundaries; chunks require at least two
  words and two-line wrapping rejects one-word orphan lines.
- The ASS surface now has a hard maximum of two lines, `WrapStyle: 2`, Barber
  Chop bold italic styling, a computed 77px font (`0.04 * 1920`), and 120px
  left/right safe margins. The final ignored artifact is
  `data/real-chapter-narrative-preview-20260814-2line/real-chapter-narrative-preview-50s-sentence-karaoke-2line-silent.mp4`:
  1080x1920, 60 FPS, H.264 High, `yuv420p`, exactly 50.000000 seconds,
  3000 frames, no audio stream, SHA-256
  `208c1fa4925546076da70dbb3c4f7f918f11eaaf31dee152500526ce25646432`,
  16,166,247 bytes. The 69-frame chronology sheet, 36-frame before/after
  chunk-boundary sheet, longest-caption frame, ffprobe, blackdetect, and
  `subtitle-2line-qc.json` are in that ignored directory.
- Final artifact QC measures `max_lines=2`, `122/122` yellow active-word
  events, no punctuation display, no one-word wrapped lines, 18 audited chunk
  boundaries, zero blackdetect findings, all story source orders `1..23`
  exactly once, and `publish_allowed=false`. Visual inspection of the full
  contact sheet, longest two-line caption, and representative boundary frames
  found no subtitle overflow, obvious subject obstruction, balloon remnant, or
  distracting edge blank-space regression.
- TDD RED was collection-clean after fixture correction: `6 passed, 2 failed`
  on the new body tests for missing semantic chunking and the old 54px/zero
  margin style. GREEN is `8/8` for the sentence-karaoke file and `21/21` for
  the subtitle/manual-preview/Luna compatibility matrix. The broader selected
  matrix is `73/73` with the disposable dependency-complete environment and
  external Windows compatibility shim. The exact LF-preserving non-slow run
  collected `867`: `866 passed`, `1` existing skip, `0 failed`. The primary
  checkout reports one environment-only v3 prompt CRLF presentation failure
  under `core.autocrlf=true`; the LF-preserving clone is the authoritative
  full-suite evidence and does not modify the tracked prompt.
- Ruff, `compileall -q app`, `git diff --check`, and the semantic/no-churn
  diff comparison are clean. No voice, TTS, music, SFX, provider, DB, UI,
  publication, or deployment action occurred. Approval remains
  `PENDING_EDITORIAL_REVIEW`, provenance remains
  `codex_manual_vision_reference_v1`, and rights remain internal-review-only.
  The remaining gate is human visual/editorial approval of this new local
  artifact; voice generation remains deferred.

## Sentence-held karaoke silent preview - 2026-08-14

- Updated the local manual-review preview surface so a complete
  punctuation-free sentence remains visible until the next sentence, while
  the authoritative word-level cue changes only the active word: active text
  is yellow with a deterministic `1.08` scale bump and all other words remain
  white. The block uses Barber Chop, bold italic styling, centered anchor
  `(0.50, 0.56)`, black outline/shadow, and deterministic wrapping capped at
  36 characters across three lines. Spoken narration remains punctuation-
  bearing and unchanged; only the independently derived display surface is
  normalized.
- The new contract is `sentence_held_word_karaoke_v1`. The ignored plan at
  `data/real-chapter-narrative-preview-20260814/edit-plan.json` contains 11
  sentence groups and 122 word intervals. The final ignored review artifact
  is `data/real-chapter-narrative-preview-20260814/real-chapter-narrative-preview-50s-sentence-karaoke-silent.mp4`:
  1080x1920, 60 FPS, H.264 High, `yuv420p`, exactly 50.000000 seconds,
  3000 frames, no audio stream, SHA-256
  `cf494544e638b4b2809df336bf5d0b7388c475f2ac74d38a15b9f4372a820e58`,
  16,075,313 bytes. The 69-frame chronology/contact sheet and representative
  word-transition pixel samples are in the same ignored directory; the
  machine-readable report is `sentence-karaoke-qc.json` with SHA-256
  `bc5816fbec454e3c08d03e8e0450df218e145cb390e7338daee5eba2321989b2`.
- TDD RED was collection-clean: the five new sentence-karaoke tests failed
  in their bodies because the grouping and ASS builder were absent. GREEN is
  13/13 for the new/preview focused suite, 47/47 for the broader manual
  narrative/preview matrix, 10/10 for the subtitle-display compatibility
  tests, and 3/3 for the reference ASS/profile/motion compatibility checks.
  Ruff, compileall, `git diff --check`, and the no-churn comparison are clean.
- The final disposable dependency-complete Windows run used an LF-normalized
  detached verification worktree plus the external `resource`/SQLite URL
  compatibility shim and collected 864 non-slow tests: 863 passed, one
  existing skip, and zero failures. A preliminary run in this primary
  `core.autocrlf=true` checkout exposed one environment-only failure in
  `test_v3_prompt_resource_is_lf_utf8_and_normative` because the committed LF
  prompt blob is presented as CRLF; the normalized verification worktree
  removes that checkout artifact without changing tracked source. No
  unrelated source was changed.
- Provenance remains `codex_manual_vision_reference_v1`, approval remains
  `PENDING_EDITORIAL_REVIEW`, `publish_allowed=false`, and rights remain
  internal-review-only. No TTS, voice, music, SFX, provider, DB, UI,
  publication, or deployment action occurred. The next gate is Sol/user
  visual and editorial approval; voice generation remains deferred.

## Real chapter Sharp Friend reference review - software green, editorial review pending - 2026-08-14

- Executed the approved plan
  `docs/superpowers/plans/2026-08-13-real-chapter-narrative-review.md` on
  `codex/real-chapter-narrative-review-impl`. The implementation checkpoints
  are `dfc0b689`, `8192cfc`, `53df791`, `9f4747e`, `241d618`, `efd7c7f`, and
  `a14e06f`; all are local-only review tooling and validation changes. The
  branch is pushed; this handoff is ready for the authorized fast-forward to
  `main`.
- The exact input is
  `data/panel-review-9c1-20260809/manifest.json`. Every local panel image was
  opened individually in order: source order `0` is title/front matter and is
  excluded with reason `title_front_matter`; story orders `1..23` were all
  examined exactly once. The immutable ledger has 24 entries and internal
  canonical SHA-256
  `4f49b408c451c453e9246848aff16da75cf0ac35b09963484a8a4250bc263de5`.
- The ignored review bundle is
  `B:/Project/manhwashorts-studio/data/real-chapter-narrative-review-20260814`.
  Its key artifact hashes are: `source_ledger.json`
  `2b7bf563f44476cb7ebaea7dd98144da4f3a839be2c5b043beb68fb6ebbea4cc`,
  `narrative_review.json`
  `637d826245dea321b534e14371cc339cd553432f937960d5c9fac5ad8cb03fef`,
  `qc_report.json`
  `b5b53616053dea24f9d89ddcccf96458eab6a66f793d28fff0db281962b8033c`,
  `narration_spoken.txt`
  `7fa9299d2982108bf6ce0e9cb1c63dd70b57e8a37e7751c2e80c13cf29588515`, and
  `display_cues.json`
  `c64eee7bbd655434f305065db0036102860da80cdf21fdf5217a8958ebc3dc31`.
- The sanitized review is labeled exactly
  `codex_manual_vision_reference_v1`; `production_evidence=false`,
  `publish_allowed=false`, rights are `internal review only`, and
  `approval_state=PENDING_EDITORIAL_REVIEW`. It contains seven causal beats,
  five Sharp Friend passages, `ending_kind=open_question`, and a punctuated
  English spoken script. `display_cues.json` independently derives 125
  uppercase, punctuation-free one-word cues; the spoken file is unchanged.
- Deterministic QC has zero blocking findings and one explicit advisory,
  `narrative.word_count_target_warning`: the QC tokenizer reports 128 words
  because apostrophes are split, while the spoken/display whitespace token
  count is 125. The report records 51.2 seconds estimated duration, five
  passages, report SHA-256
  `18bc6edd687c0a1a0178f33ae5b3d0e6272feae77e28a2eccdeaa02c81726121`, and
  remains pending human editorial review rather than being marked approved.
- Verification after the real bundle workflow fix: the focused manual,
  runner, and preview matrix is `42/42` passed; the Sharp Friend/v1/v2/v3
  compatibility matrix is `131/131` passed; the current non-slow suite is
  `859` selected (`15` slow tests deselected), `858` passed, `1` existing
  Task9C1 real-panel skip, and `0` failed. Ruff, `compileall`,
  `git diff --check`, no-churn comparison, and staged secret-scope review are
  clean. Tests used the disposable external environment
  `C:/Users/yxxrn/Documents/AutoManhwa/sharp-friend-v1-verify-venv` plus its
  external Windows compatibility shim; neither is in Git.
- No provider/network vision call, production `vision_evidence_v2` record,
  voice/TTS/audio, subtitle/video render, UI, DB/schema/migration, source
  image/media commit, publication, or deployment occurred. The next action
  is Sol/user human review of the ordered images, sanitized observations,
  spoken script, display cues, and QC report. Voice generation remains
  explicitly deferred until that review and a separate provider decision.

## Slice E Task 5 - pipeline identity persistence - 2026-08-13

- Wired the explicit `sharp_friend_v1` identity through `run_analysis`, the
  persisted reconciliation snapshot, `generate_script`, `approve_script`,
  the public `/analysis` and `/script` request bodies, and the safe analysis
  status response. v2 callers still omit the profile and retain the existing
  five-role `vision_evidence_v2` path; an explicitly unknown profile or a
  stale persisted identity fails closed.
- The v3 path preserves the provider's punctuation-bearing passage text
  byte-for-byte, accepts four-to-six flexible passages, maps only the legacy
  section slots needed by existing schemas, stores safe identity metadata and
  review flags, and never calls a legacy/template generator or media stage.
  Visual evidence sidecars are structurally checked locally while the shared
  legacy validator continues to receive its unchanged v2 observation shape.
- TDD evidence: the Task 5 focused file is 14/14 passed. The combined
  narrative/analyzer/vision/API compatibility matrix is 327/327 passed.
  The full PATH-correct disposable-environment command
  `.venv`-equivalent `sharp-friend-v1-verify-venv` ran 825 collected tests:
  824 passed, 1 existing Task9C1 real-panel skip, and 0 failed. Ruff,
  compileall, diff checks, and the external Windows compatibility shim audit
  are clean. Prompt SHA is
  `b93961d980c0ace1354611b2b78951400945def2ed13f6aa4f43557f5780869b` and
  Sharp Friend profile SHA is
  `134b544c9e2f74ca0b8c64ff55a27c831e76f77a08f26fc2a463112cb0678b3e`.
- This checkpoint starts from rollback parent `d26606d` and is followed by
  Task 6 review-only approval coverage. No provider call, credential,
  database/schema/migration, voice/TTS, subtitle, audio, render, or UI
  behavior was added.

## Slice E Task 6 - narrative review gate - 2026-08-13

- Added `tests/test_narrative_review.py` for the final review boundary. It
  proves consequence and open-question endings can be explicitly approved,
  user edits are screened again, all ordered panel evidence remains linked,
  and invalid CTA, generic-hype, copied-dialogue, claim, qualification, and
  ending contracts fail before a `ScriptVersion` is materialized or approved.
- Spoken passage text remains punctuation-bearing and does not gain a
  `display_text` field. Display derivation remains the later timeline
  representation; this slice does not invoke voice generation, TTS, timeline,
  rendering, or any provider.
- Task 6 TDD RED was run in an isolated detached checkout at parent `d26606d`:
  12 collected, 0 passed, 12 intended body failures, collection clean. GREEN
  is 12/12 in the live worktree. The combined Slice E/analyzer/vision/API
  matrix is 327/327 passed; the final non-slow suite is 825 collected,
  824 passed, 1 existing Task9C1 real-panel skip, 0 failed. No provider,
  credential, DB/schema/migration, media, subtitle, voice, or audio behavior
  was added. The next approved boundary is deferred provider/voice work only
  after a separate product decision; publish rights remain blocked.

## Sharp Friend v1 narrative identity - implementation closed - 2026-08-13

- Completed the committed Slice D plan on
  `feature/codex-manual-preview-v2`. `sharp_friend_v1` is an explicit opt-in
  analyzer identity with the v3 prompt resource, frozen profile registry,
  mechanically verified prompt/profile hashes, flexible four-to-six passage
  validation, ending-kind rules, shared all-panel evidence gates, CTA/hype/
  copied-dialogue rejection, and unchanged default v2 behavior.
- Prompt SHA-256 is
  `b93961d980c0ace1354611b2b78951400945def2ed13f6aa4f43557f5780869b`;
  canonical Sharp Friend profile SHA-256 is
  `134b544c9e2f74ca0b8c64ff55a27c831e76f77a08f26fc2a463112cb0678b3e`.
- TDD evidence: Task 1 prompt RED was 1 collected/1 intended failure and
  GREEN was 1 passed; Task 2 profile RED was 6 collected with 5 intended
  missing-module failures plus the prompt pass and GREEN was 6 passed; Task 3
  dispatch RED was 2 intended signature failures and GREEN was included in the
  focused matrix; Task 4/5 RED was collection-clean with 24 collected, 8
  existing passes, and 16 intended validator failures, followed by 25/25
  Sharp Friend tests green. The final focused analyzer/v1/v2 matrix is 96/96
  passed; the related profile/API/preview matrix is 98/98 passed.
- The release-wide non-slow suite is green: 787 collected, 786 passed, 1
  existing skip, 0 failed. The local Windows run used a disposable environment
  installed from the committed `requirements-dev.txt`; the only external
  compatibility shims normalized Windows SQLite URL separators and supplied
  the POSIX-only optional RSS `resource` module. Neither shim is in the repo.
- The prior single release failure was a stale assertion, not a production
  default defect. Commit `03dc2d7` intentionally made new projects use the
  60-FPS `reference_matched_shorts_v2`; `app/schemas.py`, `app/models.py`, and
  the changelog agree, while explicit `reference_matched_shorts_v1` remains
  supported. The directly affected integration regression now asserts the v2
  default and preserves explicit-v1 selection.
- Implementation checkpoints are `111d2a1`, `3655fd8`, `16f4a77`, `b34aa2f`,
  `00084ab`, and `b2f1fef`; the default-profile gate correction and this status
  closure are included in the release closure commit. The rollback point for the Sharp Friend
  implementation is `5eafd18c4e29819a11bdfbbf55834ce7a022ef47`.
- No provider call, database/schema/migration, voice/TTS, subtitle, audio, or
  render behavior was added in Slice D. Slice E Task 5/6 synthesis/API and
  explicit human-review wiring is recorded above; voice generation remains
  deferred.

## Slice E Task 4 - naturalness screening - 2026-08-13

- Added the non-rewriting `NarrativeNaturalnessReport` screen in
  `app/services/editorial_qc.py` and its shared `CheckResult` conversion in
  `app/services/quality.py`. It records sentence percentiles/variance,
  repeated sentence/opening ratios, connector and causal coverage, contractions,
  evidence/qualification ratios, and safe CTA/hype findings without storing or
  rewriting passage text.
- Blocking codes cover missing/unsupported evidence, unqualified interpretation,
  copied balloon dialogue, CTA, and generic hype. `narrative.template_risk` and
  `narrative.rhythm_warning` remain visible warnings only; there is no
  contraction quota, fixed sentence shape, or per-role word budget.
- TDD evidence: 9 collected/9 passed in `tests/test_narrative_qc.py`; the
  related naturalness/Sharp Friend/v2/analyzer/vision-evidence matrix is
  118/118 passed. Ruff, compileall, and diff checks are clean. The synthesis
  transport checkpoint is `dabbfe7`; this Task 4 checkpoint is not yet a
  pipeline/API or media change.
- The next task named by this historical checkpoint, Slice E Task 5, is now
  recorded above as green. Slice E Task 6 review-only approval coverage is the
  remaining boundary in this local release; voice/TTS/audio/render/provider
  selection remain deferred and publish rights remain blocked.

## Current local manual preview checkpoint - 2026-08-13

- Revalidated the corrected preview from local HEAD
  `53042f466ef576fb755eecb917037979ac2d4ffe` on
  `feature/codex-manual-preview-v2` using
  `python scripts/review/render_codex_manual_preview.py` with the committed
  manifest and edit plan. The deterministic rerender completed with
  `RENDER_RC=0`.
- The review artifact is
  `data/codex-vision-preview-motion-v4/codex-vision-preview-54s-silent.mp4`:
  54.200000 seconds, 1080x1920, 60/1 FPS, H.264 High, yuv420p, video-only,
  15,138,101 bytes, SHA-256
  `68115f8379079144b697201fd56f48fe862739a73e10556eca77fb110e4c2750`.
  The plan uses source orders 1..23 exactly once in chronology, and the
  refreshed audit contains 69 start/mid/end frames with no blackdetect
  findings.
- This is a local manual-review checkpoint only. It keeps
  `publish_allowed=false`, `rights_status=internal review only`, and no voice
  or audio generation. Older VPS/production-pipeline sections below are
  historical context, not a current production-readiness or publication claim.

## Codex preview motion variants - 2026-08-12

- Added deterministic mixed motion intents to the manual preview renderer and
  edit plan: `push_in`, `pan_right`, `hold`, `pan_up`, `diagonal`, `pan_left`,
  `pan_down`, and `pull_out`. Each shot has one monotonic intent; no reversal,
  oscillation, shake, random motion, or crossfade was added.
- Rendered comparison output at
  `data/codex-vision-preview-motion-v4/codex-vision-preview-54s-silent.mp4`:
  54.2 seconds, 60 FPS, H.264 High, 1080x1920, yuv420p, video-only, 69 audit
  frames, and no blackdetect findings. Push/pull use real centered zoom via
  `scale`+`crop`; pans move a visible 60px. SHA-256:
  `68115f8379079144b697201fd56f48fe862739a73e10556eca77fb110e4c2750`.
- This remains a manual review artifact with `publish_allowed=false` and
  `rights_status=internal review only`; the previous v3 output remains intact
  for comparison.

## Codex manual-vision preview v2 - 2026-08-12

- Completed the approved local review-only slice with the generic renderer at
  `scripts/review/render_codex_manual_preview.py` and regression coverage in
  `tests/test_codex_manual_preview.py`. The validator enforces the v2 contract,
  exact source orders 1..23, normalized crops, uppercase punctuation-free
  captions, 50-60 second duration, no random sampling, and
  `publish_allowed=false`.
- Historical v2 output remains at
  `data/codex-vision-preview-50-60s-v2/codex-vision-preview-54s-silent.mp4`:
  54.2 seconds, 23 chronological shots, video-only, H.264 High, 1080x1920,
  30 FPS, and `yuv420p`. FFmpeg black-frame detection produced no findings.
  This plan remains accepted as a historical v2 contract and is still
  replayable by the renderer.
- The new default-rate rerender is
  `data/codex-vision-preview-60fps-v3/codex-vision-preview-54s-silent.mp4`:
  54.2 seconds, 23 chronological shots, video-only, H.264 High, 1080x1920,
  60 FPS, and `yuv420p`. Its SHA-256 is
  `3a71492527bcfe568e334daa5e889b1acfb7cfdc6ab863ba2931a90294a325e8`;
  black-frame detection produced no findings and the audit contains 69 frames.
  New/default renders use the 60 FPS profile hash
  `b19ce34537481428cc2c423ab35ab65fed1ff1941e2e753d04c018b7b392a870`.
- The mandatory visual audit contains 69 deterministic start/mid/end frames
  and a contact sheet. The v2 sidecar records the exact SHA-256
  `54c282e30ceb9d668df37d2e70238f27980b07ccd55a4b4d4691159ded025f46`, manual
  provenance from all six ordered contact sheets. Subtitles use the
  project-standard `Barber Chop` font loaded explicitly from
  `assets/fonts/BarberChop.otf`; `rights_status` remains
  `internal review only`, and `publish_allowed=false`.
- This is local execution against explicitly copied prepared JPGs; runtime
  media, audit frames, contact sheets, manifests, and sidecars remain under
  ignored `data/` and are not committed. The result is not provider-generated
  StoryAnalysis/PanelRegion evidence, not a readiness claim, and not
  publishable. Human visual acceptance remains a separate review boundary.

## Visual Task 7 exact-panel silent visual review wiring - 2026-08-11

- Implemented the live reference review boundary from rollback parent
  21db23590b73e6d9683fd5b0eb5b7a1ec59cab77. The second-pass RED run was
  collection-clean: 19 tests collected, 11 passed, and 8 intended body
  failures for unreferenced-panel leakage, duplicate ROI geometry, stale
  checksum preflight, exact persisted-ROI preparation, accepted-only mask
  identity, silent cue mutation, publish_allowed enforcement, and compact
  sidecar identity.
- The final follow-up RED run was collection-clean: 25 tests collected,
  22 passed, and 3 intended failures covering a cue crossing a hard-cut
  boundary in the pipeline and direct renderer plus malformed selected ROI
  handling. GREEN evidence is 25 Task7 review tests, 127 tests in the
  focused reference/profile/framing/render/motion/subtitle matrix, 14
  panel-lineage regressions, and 753/753 tests in the PATH-correct exact
  non-slow suite. Ruff, compileall, git diff --check, and the normal versus
  ignore-space-at-eol diff comparison are clean; only existing Pillow and
  Alembic deprecation warnings remain.
- Pure panel-keyed candidate construction, eligibility mapping, ROI
  enumeration, accepted-ledger identity checks, and planned-shot validation
  now live in app/services/reference_visual_review.py. pipeline.py retains
  database/image loading, transaction ordering, thin wrappers, and
  SceneInput orchestration; its current diff is 466 additions/83 deletions
  (net +383), materially below the pre-extraction addition. The exact live
  path passes only reference_panel_candidates and never uses an asset-level
  evidence map.
- Reference review now excludes unreferenced panels, rejects stale source
  checksums before planner calls, deduplicates source-space ROIs, validates
  the selected ROI without reselection, persists the full mask only on the
  accepted fallback entry, preserves compact mask identity in the sidecar,
  rejects cue rewriting and publish_allowed=true, and keeps legacy
  profile=None behavior unchanged. Stable lineage failures remain
  visual.panel_lineage_unavailable; readiness/coverage failures are not
  fabricated or repaired.
- Readiness remains false at
  data/task7-readiness/task7-readiness-false.json and .txt: both inspected
  SQLite databases have no current StoryAnalysis/PanelRegion evidence, so no
  real-panel Task7 render was claimed. No provider, TTS, audio, media, or
  runtime database was changed.
- Silent-review cues are now required to be one uppercase alphanumeric
  word fully contained in one persisted scene interval; the pipeline and
  direct renderer reject cross-cut cues before any encoder/FFmpeg work and
  never rewrite SubtitleCue rows. The selected-ROI validator checks its
  mapping type before dereferencing it. The authorized fixture update in
  tests/test_reference_profile_integration.py remains limited to truthful
  lineage-matched synthetic evidence/crops. Sol's release gate is green and
  this work is released for publication. Next step: exact-object main-only
  publication; voice remains deferred.


## Visual Task 6 accepted-ledger/QC hardening follow-up - 2026-08-11

- Sol review follow-up RED was collection-clean: 42 focused tests collected,
  35 passed, and 7 body failures covering accepted fallback-ledger tampering,
  complete border-mask snapshot tampering, nonfinite/out-of-range framing
  fractions, and alternate-panel ROI phase traversal.
- GREEN is 42 focused integration tests and 100 related reference, framing,
  motion, visual-scoring, editorial-QC, and QC-history tests. The full
  non-slow collection is 728 tests and exited 0. Ruff, compileall,
  git diff --check, and the normal/ignore-space-at-eol diff comparison are
  clean; existing Pillow and Alembic deprecation warnings remain only.
- QC now requires a list-ordered fallback_attempts ledger with exactly one
  accepted entry whose panel/evidence/checksum, ROI kind/label/crop,
  detector/mask identity, and complete embedded telemetry exactly match the
  scene snapshot. It compares the complete canonical border-mask snapshot
  with asdict(BorderMaskResult), not only copied identity fields.
- Reference QC rejects nonfinite and out-of-range framing fractions with the
  stable visual.panel_lineage_unavailable boundary. Alternate exact panels
  now run primary, alternate_roi, and tighter_crop phases under the
  alternate_panel ledger kind before visual.visual_unavailable.
- Rollback parent: 064453c20c4d4591794fde49b8efcbbb761fb78d. Next atomic task:
  Visual Task 7 live panel-candidate construction and validation.

## Visual Task 6 exact-panel telemetry hardening - 2026-08-11

- Hardened the published Task 6 boundary from review parent
  064453c20c4d4591794fde49b8efcbbb761fb78d. RED was collection-clean:
  35 collected, 25 prior tests passed, and 10 new body failures covering
  shared-asset panel capacity, bounds, contract error precedence, fallback
  phase ordering/reasons, shot telemetry, and reused-panel QC tampering.
- GREEN is 35 focused integration tests and 93 related reference, framing,
  motion, visual-scoring, editorial-QC, and QC-history tests. The full
  non-slow collection is 721 tests; the final run exited 0 with no failures.
  Existing Pillow and Alembic deprecation warnings remain only.
- Explicit candidates now build an identity-neutral internal timing skeleton
  and count the exact (source_asset_id, panel_region_id, panel_id) candidates;
  panels from one SourceAsset are not collapsed and their lineage is never
  emitted as the internal timing identity. Panel bounds require nonnegative
  origins and dimensions exactly equal to panel_size.
- Detector/profile contract mismatches fail with
  visual.framing_contract_incompatible. Invalid lineage, dimensions, hashes,
  or snapshots remain visual.panel_lineage_unavailable. The closed fallback
  phases are primary, alternate_roi, tighter_crop, alternate_panel, and the
  final planner rejection is visual.visual_unavailable. Caller tuple order
  cannot reorder these phases; same-panel successes receive precise fallback
  reasons rather than an alternate-panel label.
- Each selected shot now carries canonical framing_telemetry with the accepted
  crop/ROI, evidence and mask identities, candidate count, attempt order, and
  selection context. QC consumes that scene-exact telemetry for every reuse;
  the temporary external telemetry map is only an identity cross-check and
  cannot override a scene's crop or accepted telemetry. Lineage and contract
  checks precede unknown-balloon/readiness and framing gates.
- The reference_panel_candidates=None bridge remains the prior reference
  planner behavior without fabricated lineage or fallback claims, and
  profile=None behavior/report serialization remains unchanged. No pipeline,
  render, model, migration, media, database, voice, or narration code changed.
- Next atomic task: Visual Task 7 live panel-candidate construction and
  validation. Rollback point: 064453c20c4d4591794fde49b8efcbbb761fb78d.

## Visual Task 6 panel-keyed fallback/QC implementation - 2026-08-11

- Visual Task 6 is green and implemented at rollback parent
  482ee74eda6b2c0546fcc18c2cc439a5b53b9d5d. The RED run was collection-clean:
  23 collected, 17 passed, and 6 body failures for the intentionally absent
  ReferenceROIAlternative and ReferencePanelFallbackCandidate interfaces.
- The focused Task 6 integration suite now passes 25 tests. The related
  reference/profile/framing/motion/visual-scoring matrix passes 81 tests.
  The full non-slow collection is 711 tests; the final run exited 0 with no
  failures. Existing Pillow and Alembic deprecation warnings remain only.
- Explicit reference_panel_candidates uses the exact panel-keyed path. Each
  frozen candidate carries source asset and PanelRegion identity, source order,
  integer panel bounds, panel size, immutable checksum, locally authoritative
  evidence hash, typed visual evidence, a distinct BorderMaskResult, and
  ordered ROI alternatives. No asset-level evidence map or predeclared
  feasibility flag is accepted.
- Every ROI attempt calls framing_analysis.candidate_is_feasible with that
  ROI, its own typed evidence and mask, panel size, and the profile final
  target. The ordered fallback_attempts ledger retains panel/region identity,
  evidence hash, detector version, mask hash, crop box, telemetry, accepted
  status, and stable rejection/reason code. Stable failures include
  visual.panel_lineage_unavailable, visual.balloon_mask_unknown,
  visual.balloon_mask_overlap, visual.protected_coverage,
  visual.visual_unavailable, and visual.blank_infeasible.
- Panel-keyed QC validates lineage and mask/evidence identity before readiness,
  coverage, blank, or overlap checks. The transitional
  reference_panel_candidates=None bridge preserves the prior reference
  planner output without inventing lineage or fallback ledgers; profile=None
  remains unchanged. Task 7 must pass exact panel candidates from live
  PanelRegion rows before planning. No pipeline wiring, media, database,
  narration, voice, or actual render changed.
- Next atomic task: Visual Task 7 live panel-candidate construction and
  validation. Rollback point: 482ee74eda6b2c0546fcc18c2cc439a5b53b9d5d.

## Visual Task 6 panel-keyed fallback plan correction - 2026-08-11

- Sol review found that an asset-keyed visual evidence map cannot distinguish
  two PanelRegions from one SourceAsset because provider geometry is relative to
  the exact panel crop.
- The Visual Task 6 plan now defines frozen
  ReferencePanelFallbackCandidate records keyed by
  (source_asset_id, panel_region_id, panel_id), carrying source order, integer
  panel bounds, immutable source checksum, locally authoritative evidence hash,
  typed evidence, beat/section eligibility, and exact ROI alternatives.
  Multiple panels from one asset remain independent candidates.
- Each reference shot owns its ordered fallback_attempts ledger. The exact
  fallback order is same-panel alternate ROI, same-panel tighter crop,
  same-beat eligible PanelRegion, then visual.visual_unavailable. Missing or
  foreign lineage precedes balloon unknown/overlap/blank checks. No
  asset-level evidence fallback or result.fallback_attempts return object is
  planned.
- Task 7 now constructs these candidates from latest PanelRegion rows before
  planning, uses evidence_panel_ids first and citations only as source_order
  fallbacks, validates the exact selected panel in Task 4 binding, and carries
  panel-keyed evidence into render/QC. Legacy profile=None remains unchanged.
- Task 6 now requires every ReferencePanelFallbackCandidate to carry a
  positive panel-crop ROI box, exact panel_size, and a distinct
  framing_analysis.BorderMaskResult whose detector version, source dimensions,
  canonical masks, and mask_sha256 match that exact Task 4 crop. The planner
  must call candidate_is_feasible with the candidate's own evidence, mask,
  panel_size, and profile final target size for every attempt; no predeclared
  safe boolean or image reread is allowed.
- Task 7 must construct panel_size and BorderMaskResult separately for every
  PanelRegion before planning, including two panels from one SourceAsset, and
  QC consumes the same exact evidence/mask/telemetry identities.
- The preceding docs-only amendment parent
  9f958877db1521ff2e5f1865fe08dc05e5fa8370 is historical context only; the
  implementation parent and rollback for this correction are
  241e1ff4f61e71238cf59cf842a1c71c7fc2184a.
- This is a docs-only correction at rollback parent
  241e1ff4f61e71238cf59cf842a1c71c7fc2184a. The next atomic implementation is
  Visual Task 6 planner/QC; Task 7 remains silent and voice-free, and
  publish_allowed stays false until rights are verified.

## Implementation planning amendment - 2026-08-11

- Approved the docs-only design in
  docs/superpowers/specs/2026-08-11-balloon-free-framing-narrative-identity-v3-design.md
  for COLOR_AGNOSTIC_BALLOON_FREE_V1 framing and sharp_friend_v1 narration.
- The current implementation baseline for this correction is clean main at
  241e1ff4f61e71238cf59cf842a1c71c7fc2184a. The historical full non-slow
  result of 635 passed at f9221dd remains checkpoint evidence; it was not
  rerun for this docs-only amendment.
- The visual plan is amended into seven ordered tasks: typed states,
  provider geometry acquisition, detector, panel lineage/crop materialization,
  candidate feasibility, fallback/QC, and silent review. Plan 2 remains six
  narrative tasks.
- The correction preserves unknown visual geometry for lineage/audit and
  rejects it only at reference readiness. It also adds the missing provider
  prompt, adapter validation, mock, and snapshot acquisition boundary before
  color-agnostic detection.
- Implementation planning is complete in
  docs/superpowers/plans/2026-08-11-balloon-free-color-agnostic-framing.md and
  docs/superpowers/plans/2026-08-11-sharp-friend-narrative-identity-v3.md.
- Visual Plan Tasks 1-5 are green and published before the current correction
  parent `241e1ff4f61e71238cf59cf842a1c71c7fc2184a`. The amendment adds a standalone
  panel-lineage boundary because visual evidence coordinates are produced from
  `_encode_panel_payload(PanelRegion)` crops while TimelineScene and
  build_render_request previously retained/rendered only a full SourceAsset.
  Visual Task 4 now persists cited panel identity and materializes the
  evidence-aligned crop before reference feasibility. The next slice is
  Visual Task 5 candidate feasibility; it must consume the persisted crop and
  reject unknown balloon geometry at reference readiness. The amended sequence
  still names Visual Task 1 typed states/persistence as its first prerequisite;
  that contract is already present and retained unchanged.
- The Task 3/4 plan correction isolates detector code from the approximately
  924-line visual_scoring.py boundary, replaces brightness/percentile-rank
  assumptions with fixed/robust structure metrics, and makes internal
  low-information components diagnostic rather than discardable.
- Voice choice, provider configuration, auditions, audio generation, and
  final voice rendering remain explicitly deferred until the user chooses
  local or API execution. Rights/source checks keep publish_allowed=false.
- Visual Task 4 verification: RED collected 11 with 7 intended body failures
  and 4 existing migration tests passing. GREEN focused panel-lineage and
  migration tests are 15 passed; the combined panel/migration/vision-pipeline/
  reference-render matrix is 48 passed. The adapter/synthesis/analyzer/resolver
  matrix is 126 passed, the reference profile/framing/motion/scoring/QC matrix
  is 62 passed, and the mandated non-slow run is 682 passed with 15 deselected.
  Scoped Ruff, compileall, and diff-check pass. Migration
  `7776011fa52f` (`alembic/versions/7776011fa52f_persist_panel_lineage_into_reference_render.py`)
  is a linear child of `b7c4d8e91f20`; `panel_region_id` is an auditable stable
  nullable reference rather than a new FK so legacy SQLite upgrades remain
  additive. The rollback point is parent
  `aa11bdbd500beca00ad7481b85731f17297e8e58`; Task 5 is next.
- Source and test execution remains VPS-only. Because VPS GitHub SSH auth is
  unavailable, exact history is published through the isolated Windows
  transport clone; runtime data, media, databases, credentials, and review
  artifacts remain outside Git.

## Visual Task 4 hardening - 2026-08-11

- Post-review hardening closes two fail-open paths. In reference mode, a
  missing or empty `TimelineScene.asset_id` now raises the stable
  `visual.panel_lineage_unavailable` error before a `SceneInput` can be
  produced. Legacy `profile=None` scenes retain their existing assetless
  behavior.
- Materialized reference panel PNGs are now reopened and normalized to RGB;
  their dimensions and raw RGB bytes are checked against a local canonical
  SHA-256 computed before save. Save/read mismatch or corruption fails closed
  with `visual.panel_lineage_unavailable`; no sidecar or schema field was
  added.
- RED was collection-clean: 14 panel-lineage tests ran, 12 passed, and the
  two new behavioral regressions failed for the expected missing asset guard
  and missing written-crop integrity check. GREEN passed 28 focused
  reference/panel tests, 52 tests across the Task4 panel/migration/vision/
  reference-render matrix, and 171 related adapter/synthesis/analyzer/
  resolver/profile/framing/motion/scoring/QC tests. Scoped Ruff,
  `compileall`, and `git diff --check` passed. The full non-slow suite passed
  686 selected tests with 15 deselected.
- Sol authorized the single directly affected fixture expansion in
  `tests/test_reference_render_surface.py`: its reference-profile test now
  creates a real `SourceAsset`, `PanelRegion`, canonical unknown visual
  snapshot, and materializable crop instead of bypassing the new lineage
  contract. No other out-of-scope test or production path was changed.
- The rollback parent for this hardening slice is
  `41fc8a139d92e05f06e2bb3957f0f1a8d9992007`. Task 5 remains next; this slice
  does not consume balloon readiness, render media, voice, narration, or
  credentials.

## Visual Plan Task 3 - color-agnostic border analysis - 2026-08-11

- RED was collection-clean: `tests/test_color_agnostic_blank.py` collected 12
  tests and reported 12 body failures because `app/services/framing_analysis.py`
  and the extended cache identity did not exist.
- GREEN passed 12 focused detector tests and 33 tests across the Task 3 color,
  reference-framing, motion-stability, and reference-profile matrix. Scoped
  Ruff, `python -m compileall -q app`, `git diff --check`, and the key-shaped
  secret scan passed. The full `-m 'not slow'` run passed 671 selected tests.
- `DETECTOR_VERSION` is
  `COLOR_AGNOSTIC_BALLOON_FREE_V1:grid256:structure4`; the deterministic
  16x24/grid-8 audit example produced mask SHA-256
  `5a633ce9cebb5fc9c508a5e2361b78653b78e35b66b5c5da347c39c0ce79b21a`.
  Source-area fractions use integer floor cells and six-decimal final ratios;
  protected regions are retained and sealed internal low-information cells are
  diagnostic only.
- `render.reference_frame_cache_key` preserves the existing profile=None and
  profile-without-evidence tuples. Profile-mode evidence keys include detector
  version, mask hash, mask status, evidence hash, and serializer-backed
  protected geometry. This slice does not invoke detection from render call
  sites, infer sidecars, or claim reference readiness.
- Rollback point is `e0d8fdf523c095740a984d88798200ed3dd4707e`. The amended
  Visual Task 4 is next for panel lineage persistence and deterministic
  evidence-aligned crops; candidate feasibility is now Visual Task 5 and must
  consume this crop rather than applying panel coordinates to a full strip.

## Visual Plan Task 1 - typed visual evidence - 2026-08-11

- RED was deliberate and body-only: `PATH=/home/yusronrohmani/.local/bin:$PATH
  .venv/bin/pytest tests/test_balloon_evidence.py -q` collected 7 tests and
  reported 7 assertion failures because the typed visual-evidence boundary did
  not exist; there were no collection or setup failures.
- GREEN focused verification collected 58 and passed across
  `tests/test_balloon_evidence.py`, `tests/test_vision_adapter.py`,
  `tests/test_vision_synthesis.py`, and `tests/test_vision_pipeline.py`.
- The full PATH-correct non-slow run passed 642 tests. Scoped Ruff,
  compileall, and `git diff --check` also pass for this slice.
- `PanelVisualEvidence`, balloon/protected-region records, deterministic
  canonical JSON/SHA-256, lineage checks, and safe observation persistence are
  now implemented in `visual_scoring.py` and `pipeline.py`. Missing sidecars
  persist as explicit `unknown` records with real panel lineage; they are
  accepted for audit and rejected only by the reference-readiness gate with
  `visual.balloon_mask_unknown`. Affirmative `known_empty` and validated
  `known_nonempty` geometry remain fail-closed.
- Task 2 is next: acquire versioned balloon/protected geometry from every
  ordered vision observation. This slice intentionally does not add that
  provider prompt/adapter acquisition, and therefore does not claim reference
  framing readiness or visual acceptance.
- Task 2 planning has been corrected before implementation: provider output no
  longer requests or trusts `evidence_hash`; the local serializer owns it, and
  the production pipeline plus `tests/test_vision_pipeline.py` explicitly opt
  into and verify visual observation mode. The current implementation baseline
  is `a45084688ebbe4b2b21ad1ea251b884f1fcee8ab`.
- Rollback point for Task 2 is the clean published Task 1 commit
  `a45084688ebbe4b2b21ad1ea251b884f1fcee8ab`; its parent is
  `1dff696f1dc7f2bcc59b337d4cc38f53fee54434`.

## Implemented

- FastAPI UI/API with local SQLite and content-addressed storage.
- Rights metadata on every ingested asset.
- Rules-based analysis and five-beat English script generation.
- Project defaults: English text, American English voice, `en-US`,
  `the-explainer-american`.
- Explicit Indonesian opt-in.
- Generic offline/HTTP TTS adapter with fixed per-render voice settings.
- Deterministic panel scoring, ROI focus, camera motion, transitions, and
  split-focus/panel-stack compositions.
- Audio-master timeline and subtitle timing.
- Spoken narration keeps punctuation for TTS prosody while display subtitles
  are separate uppercase, punctuation-free, one-word Unicode-alphanumeric cues
  with source-word timing preserved through SRT, edits, and render inputs.
- CPU FFmpeg render: 1080×1920, 60 FPS by default, H.264/AAC.
- Persisted QC report, immutable override history, render leases, stale-job
  recovery, resource metrics, and cleanup.
- Phase 2 editorial gates: deterministic panel penalties and asset reuse caps,
  chronology/source-family audit reasons, four-mode motion diversity, action
  hard cuts, 0.12-0.18s section transitions, and one-word display captions.
- Private-by-default publication gate with rights and approval checks.
- BYOK encryption and provider discovery.
- Vision-first analyzer v2 contract with complete chapter evidence gates and
  five-role, word-bounded, evidence-grounded narration.
- Visual Plan Task 2 now acquires versioned balloon/protected-region sidecars
  during every production observation, preserves unknown geometry for audit,
  and computes the local canonical visual evidence hash; providers never
  supply or establish that hash.
- Task 2 review hardening now rejects OCR-only provenance for every known
  balloon state, rejects partial visual instruction pairs before provider
  calls, and uses one requested-panel lineage lookup per response row.
- Evidence-gated script materialization now requires the latest RECONCILED
  analysis, revalidates persisted panel/claim evidence, and records explicit
  human approval before SCRIPT_APPROVED.
- Added the four-voice neural audition workflow: one deterministic,
  punctuation-preserving 45-65 word excerpt covers all five roles, four
  isolated content-addressed WAVs, safe project-scoped downloads, and no
  fallback or automatic voice selection.
- Analysis status exposes only safe state, provider, coverage, reconciliation,
  count, and blocking-code summaries; public analysis never falls back to text
  or rules when vision capability is unavailable.
- Full test, lint, compile, and real-FFmpeg validation on Google execution host.

## Visual Plan Task 5 checkpoint

- Visual Task 5 is green on VPS at the uncommitted rollback parent
  8f7f15bf44e525760948d9614be6f5099c1f7347: 45 passed in the focused
  framing/color-mask/profile/motion matrix, and 698 passed in the exact
  non-slow suite.
- The reference profile now includes the five framing fields
  (COLOR_AGNOSTIC_BALLOON_FREE_V1, zero blank target, zero balloon
  intersection, 256-cell long edge, and 0.03 safe-area margin). Its current
  canonical SHA-256 is
  3db66724059a502127852f613809e26e7792895f7bd974a94c2f34306b02208b.
- FramingTelemetry records the static crop box, base/source zoom caps,
  detector and mask hashes, protected-region coverage, balloon overlap,
  blank fractions, mask provenance, and stable fallback/rejection codes.
  Example uniform-panel telemetry is
  edge_connected_blank_fraction=1.0 with fallback_reason=visual.blank_infeasible;
  any nonzero balloon overlap remains a hard rejection.
- The directly affected tests/test_reference_profile.py canonical-field
  and per-field hash assertions were expanded because the published Task 5
  file list omitted that required profile-contract test. The real-panel
  smoke uses lineage-matched structural known-empty evidence as a test
  fixture; it does not claim provider geometry for production data.
- An earlier GREEN patch was authored in the Windows transport before the
  boundary correction. Only its production hunks were mechanically applied
  to VPS; all subsequent fixture, profile-test, and documentation work was
  performed directly on VPS. No media, DB, credentials, or runtime artifacts
  changed.
- The broader related pipeline command still exposes 13 unchanged slow legacy
  public-draft failures at the vision-only gate; the required non-slow suite
  is fully green and no compatibility fallback was added.
- Next atomic task: Visual Plan Task 6 fallback/QC integration consuming the
  persisted panel crop, typed evidence, and Task 5 feasibility telemetry.
  Rollback point: 8f7f15bf44e525760948d9614be6f5099c1f7347.

## Visual Plan Task 5 hardening checkpoint

- The deterministic ranking correction is committed at
  61258817101a10a3b11916f653d89aca21088fe2, with rollback parent
  8f7f15bf44e525760948d9614be6f5099c1f7347.
- RED was collection-clean: 19 passed and 5 body failures covering the
  protected-area/less-zoom ranking direction, larger tie-break coordinates,
  protected geometry cap, incompatible detector contract, and corrupt-source
  fail-closed behavior. GREEN is 50 focused tests, 36 related
  visual/reference-render tests, and 703 exact non-slow tests passed.
- Candidate ranking is now
  balloon-zero, protected retained area, one-minus edge blank, focus score,
  negative base zoom, then larger top and left coordinates. Protected zoom
  telemetry is the minimum of the source-resolution cap and deterministic
  geometry caps computed from the candidate-center crop needed to retain each
  required protected region fraction; it is never looser than source cap.
- Active reference preparation no longer falls back to legacy framing for
  undecodable or invalid sources. It emits
  visual.panel_lineage_unavailable; incompatible profile/detector contracts
  emit visual.framing_contract_incompatible. profile=None remains unchanged.
- Task 6 fallback/QC integration is next. No reference profile fields, media,
  DB, credentials, voice, or actual render changed.


- Production execution target: Google VPS through the `google` SSH alias.
- Local machine: orchestration, source transfer, and requested artifact delivery.
- No production media or credentials belong in Git.
- OmniVoice Studio remains an external voice experiment. It is not a core
  dependency, default provider, or release gate.

## Codex manual-vision preview checkpoint

- Sol visually inspected every ordered source panel through all six complete
  contact sheets. Coverage is source orders 0..23 with no random sampling;
  source order 0 is a title page and is the only panel excluded from the
  timeline.
- The VPS generated a new isolated 23-shot silent preview at
  `data/codex-vision-preview-20260811/codex-vision-preview-silent.mp4`.
  FFprobe reports H.264 video-only, 1080x1920, 30 FPS, and 36.033 seconds.
  SHA-256 is
  `2392a66cca39086cd69e0654a496a4ef1672b3025a7966518d885b4013b83ee9`;
  the downloaded Windows copy has exact hash parity.
- Every shot uses a manually reviewed close crop intended to remove visible
  speech balloons and edge padding, followed by a deterministic
  low-amplitude unidirectional pan. A 23-midpoint contact-sheet review found
  no visible speech-balloon text or edge-connected white padding; the first
  render was rejected because one balloon tail remained and was re-rendered
  with a corrected crop. FFmpeg black-frame detection emitted no findings.
- Captions are English uppercase phrases without punctuation. There is no
  voice or audio stream. The crop/source checksum ledger and provenance live
  at
  `data/codex-vision-preview-20260811/codex-manual-vision-review.json`.
- This checkpoint is honestly labelled `codex_manual_vision_review_v1`. It is
  a user-review preview, not provider-generated visual evidence, not a
  persisted StoryAnalysis/PanelRegion production run, and not proof that the
  Task 7 readiness gate is satisfied. `publish_allowed` remains false and the
  source rights remain internal-review-only.

Next approved manual-preview slice is documented in
`docs/superpowers/plans/2026-08-11-codex-manual-vision-preview-50-60s.md`.
It targets 54.2 seconds using the same 23 panels exactly once, adds a generic
tested review CLI, requires a 69-frame start/mid/end visual audit, and keeps
voice, publication, and provider-evidence claims deferred.

## Not complete

- A real, clean panel source with verified publication rights.
- A configured multimodal vision credential/provider for a real chapter run.
- An active neural BYOK TTS credential and real audition samples; the current
  VPS has no configured neural HTTP provider, so no samples were rendered.
- Reference-matched final rendering now carries the selected project voice,
  profile-specific one-word caption surface, stable zoom caps, and explicit
  H.264 High/yuv420p output QC; legacy preview/build_ass behavior remains
  unchanged when no profile is selected.
- Production-ready public upload credentials and channel policy.
- Scheduling UI, external queue, multi-channel workspaces, analytics, and full
  music/SFX workflow.

## Release gate

A render is review-only until all are true:

1. Source owner and licence/permission are recorded.
2. Script is approved.
3. QC has no blocking errors.
4. Playback, codecs, dimensions, duration, audio, subtitle pixels, drift, and
   black-frame checks pass.
5. Publication is explicitly confirmed by the user.
6. Final delivery uses 1080x1920 60fps by default (or the explicitly selected
   historical profile FPS) H.264 High/yuv420p with final audio
   normalization toward -14 LUFS and true peak at or below -1.5 dBTP.
7. No unlicensed music or SFX is attached; rights/source checks remain hard blockers.

Current state: **development / review-only**. Visual Plan Task 5 hardening is
committed at 61258817101a10a3b11916f653d89aca21088fe2; Task 6 is the next
fallback/QC consumer of panel lineage and feasibility telemetry. Reference
output remains unavailable until all lineage, balloon, protected-region, and
rights gates are fulfilled. VPS GitHub SSH is unavailable, so approved
commits are published through the isolated Windows transport workflow.
