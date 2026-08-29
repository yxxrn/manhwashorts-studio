> **HISTORICAL RECORD — DO NOT EXECUTE AS CURRENT RUNBOOK.** This document
> preserves a past checkpoint and may contain removed test paths, superseded SHAs,
> old gate behavior, and machine-specific commands. Current agents must follow
> `../AGENTS.md`, `docs/STATUS.md`, `docs/ARCHITECTURE.md`, and
> `docs/MAINTAINER_GUIDE.md` instead.

# ManhwaShorts production-completion plan

## Frozen release-candidate acceptance - 2026-08-23

The corrected wall-clock limit is `<=90` minutes from ingestion through an
upload-ready MP4 and all strict QC. After Video 1 is accepted, freeze the
release-candidate SHA and production configuration. The fresh `The Novel’s
Extra` run must use that exact pair and the normal entrypoint with zero code,
threshold/configuration, manual DB/artifact, or editorial intervention. Record
the start/finish SHA and configuration fingerprint, exact command, stage
timings, provider/cache counts, and complete QC. Failure rejects the candidate:
fix only a generic invariant with RED/GREEN regression proof, publish a new
SHA, recreate fresh state, and restart the proof from the beginning.

Historical narration admission remains 115-125 words and 50-60 seconds.

## Mandatory production-time acceptance - 2026-08-23

One valid production run must complete from chapter/input ingestion through
upload-ready MP4 and all strict QC in `<=90` minutes wall-clock. A run over 90
minutes is not production-ready; quality, grounding, lineage, visual, subtitle,
audio, and rights gates remain unchanged. Instrument every stage and the
critical path, including provider requests/retries/concurrency/cache hits and
resource peaks. Persist/reuse prepared manifests, feasibility/ROI/crop data,
timeline inputs, provider responses, and deterministic artifacts with explicit
versioned invalidation. Direct preflights must pass before another expensive
full run. This timing gate is currently unproven.

## Latest verified checkpoint - 2026-08-23: cached visual metadata reuse

Rollback parent: `c3243aae75eacfe7ac5732f36e334272f853b42f`.

The warm review retry restored the prepared manifest but failed before visual
cache lookup because the runner treated every metadata-only input as an
immediate materialization error. The RED regression captured the exact
ordering. GREEN moves the guard after canonical ordered source/prompt cache
lookup: valid cached visual evidence is reused without a provider call, while
cache misses still fail closed with
`cloud.prepared_manifest_requires_materialization`.

Verification: 162/162 focused tests (137 cloud, 13 visual-repair, 12
prepared-manifest), Ruff, compileall, and diff-check. No provider/TTS/render
request or MP4/QC artifact is claimed. Publish this checkpoint, then resume
the existing cached project job without repeating visual/story stages.

## Latest verified checkpoint - 2026-08-23: warm prepared manifest

Rollback parent: `ecd8a67cca65dd5f6c5ce117f0096d552111c46c`.

Review-only resume now restores the durable prepared-panel manifest before
cold preparation and persists a manifest after a genuine cold fallback. This
addresses the measured repeated 701-panel materialization without weakening
any source, evidence, feasibility, lineage, duration, or publish gate.
GREEN is 150/150 focused cloud/visual-repair tests (137 + 13), Ruff,
compileall, and diff-check. No new provider/TTS/render request or MP4/QC
artifact is claimed. Publish, then run the versioned repair boundary.

## Latest verified checkpoint - 2026-08-23: versioned visual repair

Rollback parent: `28ca2e37914a37f389210afe1aa333a923e48077`.

The cached review retained the accepted 701-observation, 122-word,
53.04-second narration but ended `visual.narrative_repair_ungrounded` at a
36-panel/71-ROI ledger with two missing sections and zero new provider/TTS
requests. The repair cache previously reused v1 semantics after section
closure had tightened. GREEN now bumps the repair contract to v2 and prompt
to v3 and makes the cache key explicitly versioned. Focused proof is 149/149
(136 cloud, 13 visual-repair), plus Ruff, compileall, and diff-check. No MP4,
TTS, or QC result is claimed. Publish this checkpoint, then resume the repair
boundary without repeating visual/story analysis.

## Latest verified checkpoint - 2026-08-23

Rollback parent: `ac70d9903587f86627272542a9260b1188ec51a0`.

The first post-fix cached review reached 36 feasible panels and 71 feasible
ROIs, then failed the strict visual repair after three bounded `other`
requests as `cloud.narrative_not_grounded`. The new local diagnostic keeps
only analyzer field/count metadata and supplies field-specific retry guidance;
it does not weaken admission or retain provider prose. GREEN is 148/148
focused cloud/visual-repair tests, Ruff, compileall, and diff-check. No MP4,
TTS, or QC result is claimed. Next: publish, reuse visual/story caches, and
resume the bounded repair boundary.

## Latest verified checkpoint - 2026-08-22

Rollback parent: `1a3c5102cc60f8676b7de3cdca1f16661e4a66aa`.

The persisted review-source discrepancy is fixed offline. The post-script
review path now reuses non-empty prepared `CloudPanelInput` payloads and the
existing exact candidate builder; the segmented-asset DB loader remains only
for empty-payload legacy callers. RED reproduced `visual.visual_unavailable`;
GREEN is 147/147 focused cloud/visual-repair tests, Ruff, compileall, and
diff-check. The cached runtime has 701 prepared/visual rows, and this slice
spent zero provider/TTS requests. No MP4 or QC result is claimed. Next: push
this checkpoint, rerun the cached review driver without visual/story repeat,
then stop at and diagnose the next strict render/QC boundary.

Status: active persistent goal, last verified 2026-08-22. This plan is an
operational handoff, not permission to weaken a quality gate.

## Definition of done

Two real final MP4s are produced through normal entrypoints and independently
verified: the Oracle project `22876a6014a842f48bfca58c10a592b5` and the fresh
chapter at `B:\Project\manhwashorts-studio\The Novel’s Extra`. Each must have
grounded narration, configured TTS, real word timing, two-line karaoke, strict
visual/audio/lineage/QC evidence, a warm idempotent resume, and an absolute
artifact path plus SHA-256. Oracle, GitHub main, and local main must be aligned
and tracked worktrees clean. No video is externally published.

## Execution phases and gates

1. **Paused closure publication.** Review the six-path Oracle diff against
   `5c4f492678eedb5787d526a19a9742fd53bb27d1`; run focused closure, cloud,
   related, Ruff, compileall, diff, allowlist, and secret checks; commit and
   push only after GREEN. No provider call before this gate.
2. **Cached narration repair.** Reuse the verified 701-panel visual/story
   cache. Make one bounded same-model repair request with zero visual/story
   repetition. Admit only exact claim/panel/section closure, anti-copy,
   grounding, causal order, identity, 115-125 words, 50-60 seconds, and display
   derivation. Persist the normal analysis/script identity and request ledger.
3. **Oracle video 1.** Render silent first, then configured
   `grok-voice-latest` / `the-explainer-american`. Verify actual audio timing,
   karaoke, H.264/yuv420p, AAC, 1080x1920, 50-60 seconds, motion, blank-space,
   balloon, subtitle, lineage, black-frame, and artifact-integrity gates.
   Prove a warm normal-entrypoint resume reuses valid caches and does not
   regenerate stages unnecessarily.
4. **Whole-program audit.** Inspect ingest, segmentation, visual/story map,
   narration, repair, persistence, cache invalidation, TTS, alignment, render,
   QC, CLI, retry, resume, concurrency, and secret boundaries. Fix only
   evidence-backed defects with RED/GREEN tests and bounded commits.
5. **Secure synchronization.** Copy only project-scoped runtime data needed
   for local operation using encrypted transport and a consistent SQLite backup.
   Keep secrets in a dedicated ignored private directory, create a redacted
   transfer manifest with sizes/hashes, and never commit runtime data.
6. **Fresh local video 2.** Synchronize local main safely, preserve `final_test`
   and `The Novel’s Extra`, then run the normal workflow from the fresh chapter:
   ingest, color-agnostic segmentation, all-panel vision, story reduce,
   grounded narration, TTS, timing, karaoke, render, QC, and warm resume.
7. **Release proof.** Run focused and full feasible suites, Ruff, compileall,
   diff/no-churn, staged secret scans, actual-frame/audio inspection, and
   parity checks. Update AGENTS, STATUS, handoff, architecture/performance,
   and this plan/todo with exact evidence before marking the goal complete.

## Fixed acceptance decisions

- Whole ordered chapter understanding; no random sampling or fabricated facts.
- One pinned multimodal model for visual/story/narration stages and encrypted
  BYOK; provider payloads and credentials never enter logs or Git.
- Dialogue is paraphrased in third-person narration; copied or near-copied
  source dialogue fails closed.
- Spoken narration remains punctuated and immutable; display text is separately
  derived, punctuation-free, readable, and excludes voice tags.
- Subtitle blocks stay at most two lines with active-word yellow highlight and
  scale bump; timing follows actual voice or a clearly non-authoritative review
  boundary only before voice exists.
- Balloon exclusion, protected-region retention, blank-space, chronology,
  lineage, identity, rights, and publish gates remain strict.
- Review upscale policy is deterministic and disclosed; it never claims native
  quality or enables publication by itself.

## Resume and evidence record

Every checkpoint records rollback parent, commit and remote SHAs, command,
wall time, provider request/retry/cache counts, peak RSS where measured, state
transition, absolute artifacts, SHA-256, and QC result. Runtime DB/WAL, cache,
media, input folders, `data`, `ms_env.sh`, and credentials remain protected
untracked paths. If a stage fails, preserve its sanitized error report and
resume from the last valid durable stage; do not rerun valid visual/story
calls or make an unbounded retry loop.

Current checkpoint: closure fix is uncommitted on Oracle at the six tracked
paths listed in the persistent handoff; focused closure 5/5, cloud 122/122,
related 275/275, Ruff and compileall are GREEN, and provider requests consumed
by this slice are zero. The next command after publication is the single
bounded cached narration-repair command documented in `AGENTS.md` and the P0
handoff.

## Latest closure addendum — 2026-08-22

The v1 single-claim passage-context predicate was proven too narrow for the
persisted p3 passage, whose claims span two canonical sections. The completed
offline correction is closure v2: union only the exact passage's trusted
claim ancestry, retain per-position claim evidence, and preserve all
fail-closed foreign/duplicate/stale/identity checks. GREEN evidence is closure
5/5, cloud 123/123, related analyzer/story/narrative 211/211,
segmentation/vision 134/134, Ruff/compileall/diff-check/no-churn clean, and
zero provider requests. The source/test/docs checkpoint is published as
`bd6f7d791d033f36f62c725b724fdcad9fdc2b8b` with parent
`24971e742653aeae48a2b15757adccf44a5dedb9`; the immediate next action is one
bounded cached repair request.

## Micro-compaction addendum — 2026-08-22

The first authorized repair request after closure publication used one request
and zero retries, then failed locally with sanitized metrics 128 words,
55.65 seconds, and `micro_compaction_no_safe_operation`. The bounded local
fix is policy v2: standard auxiliary/negative contractions only, stop at 125,
preserve meaning/lineage, and include policy/result identity in the cache.
GREEN is compaction 4/4, cloud 124/124, related 211/211, and static/no-churn
clean. The source/test/docs correction is published as
`a40e51b79808bc8520cf422bce0f0af838f8fe7e`. The one subsequent bounded repair
request used one request and zero retries, then failed at hard
`aggregate_word_count` with 112 words/48.7 seconds. Do not pad or relax the
contract; preserve the failure and stop provider calls for this attempt.


## Micro-compaction v3 repair checkpoint — 2026-08-22

After published f4fa6e77bde41758e5b02e62dfb241aa5bbf0cf6, one newly authorized bounded same-model repair request used exactly one request, zero retries, and no visual/story calls. Sanitized response metadata was eight strings, 126 words, and 54.78 seconds; local admission failed closed as cloud.narrative_repair_micro_compaction_unavailable with predicate micro_compaction_no_safe_operation. Provider prose was not retained.

The RED regression reproduced the 126-word narrow overshoot. GREEN adds five audited conversational auxiliary contractions, bumps the policy identity to narration-micro-compaction-v3, and retains the hard 115-125-word and 50-60-second gates. The new 126-to-125 reconciliation regression passes. The full cloud/related matrix is 336/336; the complete compaction-focused selection is 6/6; Ruff, compileall, diff-check, no-churn, allowlist, and secret-shape scans are clean. No padding, truncation, lineage change, or gate relaxation was introduced.

The v3 source/test/docs checkpoint is committed and published as `95965721b253468258483aeda9b64eb998153565` from parent `f4fa6e77bde41758e5b02e62dfb241aa5bbf0cf6`. Visual/story caches remain reusable; narration persistence, silent/voiced render, TTS, and QC remain closed until a repair result passes strict admission.


## 2026-08-22 - positional repair response-shape diagnostics checkpoint

- Published baseline: `2cd528ebc35748fbc72582f5a12acc9c6aea0868`, parent `b0053be96359aeb3f7a1b9a325fb0b8e3450b4c1`.
- The single same-scope repair attempt after the prior publication used exactly 1 provider request, 0 retries, and no visual/story repeat. It failed closed locally as `cloud.narrative_repair_position_contract_invalid`; no provider prose was retained.
- Root cause was a local observability gap: positional response-shape metrics were constructed only after top-level/array/count validation, so an early contract rejection persisted no array length, per-position counts, total, duration estimate, expected ranges, or failed predicate. This did not relax any quality, grounding, lineage, duration, or identity gate.
- TDD evidence: the new early-rejection regression was collection-clean RED before the fix; GREEN is the focused positional matrix (24 passed) and the related cloud/analyzer/script/manifest/narrative matrix (337 collected, 337 passed), plus Ruff, compileall, diff-check, and no line-ending churn.
- The corrected boundary persists only sanitized metadata for every early positional contract failure: container/key shape, rewrite count/types, per-position word counts, total words, duration estimate, expected ranges, slot-order identity, hard accepted ranges, and the exact failed predicate. It never stores provider text.
- Hard admission remains unchanged: trusted positional order/lineage, grounding, causal coherence, display derivation, 115-125 words, and 50-60 seconds remain fail-closed. No narration, MP4, TTS, or final QC artifact is claimed yet.
- Next resume point: inspect only the sanitized report from `2cd528e`, then issue a bounded same-model repair call as authorized. If strict admission succeeds, persist the narration through the normal entrypoint, render/QC silent video first, then proceed to configured TTS/alignment and voiced QC. Keep runtime data, caches, media, database files, and `ms_env.sh` untracked.


## 2026-08-22 - bounded repair shape report

- From published checkpoint `a3fa443151d4b84864dcab56186adb141e0e602a`, the next authorized same-scope repair used exactly 1 provider request and 0 retries, with no visual/story repeat.
- Sanitized response metrics: positional container `dict`, 8 rewrite items, all 8 items strings, total 140 words, estimated duration 60.87 seconds, and one top-level key. The stored metadata contains no provider prose.
- Local admission correctly failed closed with `cloud.narrative_repair_micro_compaction_unavailable`, predicate `micro_compaction_window`. The conservative local compactor accepts only 126-130 word candidates; it must not truncate or weaken the hard 115-125 word and 50-60 second gates. No source/test change is justified by this result.
- No narration, MP4, TTS, or final QC artifact is claimed. The next retry remains bounded and must be admitted only after positional shape, trusted lineage, grounding, causal order, display derivation, total words, and duration all pass.


## 2026-08-22 - bounded repair attempt with over-window candidate

- From published checkpoint `6e54fa2982e2754e4a4cd997f47922d8c675a313`, the next authorized same-scope repair used exactly 1 provider request and 0 retries, with no visual/story repeat.
- Sanitized response metrics: 8 rewrite strings, 135 total words, estimated duration 58.7 seconds, all items typed as strings, and no retained provider prose.
- Local admission correctly failed closed with `cloud.narrative_repair_micro_compaction_unavailable`, predicate `micro_compaction_window`. Duration is inside 50-60 seconds, but 135 words is outside both the conservative 126-130 compaction window and the hard 115-125 final bound. No truncation, padding, or gate relaxation is allowed.
- The next repair request remains the smallest path to a strict-valid candidate; visual/story caches remain reusable and no narration or media artifact is claimed yet.

## 2026-08-22 - Stage 1 safe synchronization and v6 prompt reconciliation

- Reconciliation baseline: latest published main is `2ae54ad03a66f257c3d690de5d881c52da7b412c` (fix: tighten positional repair word target; parent `de40f6d6a1885294d22c0bf70fd5802f06ccce5a`). Oracle HEAD and live GitHub main were verified identical at that SHA before this documentation checkpoint. No provider, TTS, render, or media work was performed in Stage 1.
- Prompt contract v6 is the published repair boundary: `vision-first-story-analyzer-v3-targeted-position-repair-v6` in `app/services/cloud_multimodal.py`. The versioned instruction now requires the provider to recount the complete vector and revise until it totals 115-125 words, never return a vector above 125 words, and treats the local micro-compactor strictly as a 126-130-word safety net. `tests/test_cloud_multimodal_mass_production.py` pins the v6 identity and the three new instruction clauses. No admission, lineage, grounding, duration, or identity gate changed.
- Provider calls for the three same-scope positional repair requests recorded on 2026-08-22: 3 total, retries: 0, with zero visual/story repetition (contract-shape diagnostics from `2cd528e`; 140-word/60.87-second rejection from `a3fa443`; 135-word/58.7-second rejection from `6e54fa2`).
- Latest durable rejected candidate: 8 rewrite strings, 135 total words, 58.7 seconds estimated duration; local admission failed closed with `cloud.narrative_repair_micro_compaction_unavailable`, predicate `micro_compaction_window`. No truncation, padding, or gate relaxation was applied.
- The visual/story cache for the 701 production panels remains reusable; the next repair retry needs no visual or story provider call.
- No accepted narration, TTS, MP4, or QC artifact exists at this checkpoint.
- Stage 1 local synchronization of the Windows worktree `B:\Project\manhwashorts-studio`: local `main` fast-forwarded `b6f72cd` -> `2ae54ad` with `git merge --ff-only origin/main` after proving `b6f72cd` is a strict ancestor and that no incoming tracked path collides with untracked input. Recoverable archive ref `archive/stage1-20260822-pre-sync` = `b6f72cd`; branch `codex/final-production-silent-acceptance` remains pushed at `b6f72cd`. `final_test/` and `The Novel’s Extra/` remain untracked and unstaged. No reset, clean, forced checkout, or force-push was used.
- Validation on Oracle for this checkpoint: focused positional/repair/micro-compaction selection 64 passed with 62 deselected; related cloud/analyzer/script/manifest/narrative matrix 308 passed with 7 pre-existing Pillow deprecation warnings; Ruff, compileall, `git diff --check`, line-ending/no-churn, and secret-shape scans are clean.
- Next boundary (Stage 2, not started): one bounded same-model v6 repair retry from the published SHA using only the cached 701-panel visual/story evidence, with strict admission before any persistence, render, TTS, or QC step.

## 2026-08-22 - Stage 2 strict-valid narration accepted and persisted

- Source SHA at start: `247f2686442596768a98b03354f9dd98f857f323`. Four RED->GREEN fixes were published before acceptance, each with focused regressions and green matrices:
  - `d69fba4ec8512bbafffbf2ba98e22d79cd97c3d9` - `run_job` now repairs a structurally usable but contract-failing cached narration through the public `run_narration_repair_candidate` boundary before admission, so persistence can never receive a contract-failing narration. RED: `test_run_job_repairs_structurally_usable_dialogue_copy_narration`.
  - `ad094b95aaa1434ecf9c22e1ffdf675b8ee8dc99` - repair prompt v7 caps every rewrite at 15 words.
  - `f2b52ea46912747130c372ec42076b3ce4f0e54f` - repair prompt v8 centers drafting at exactly 14-15 words per position, aiming for 15.
  - `e41698976a66be5cc6c891d5939596e454d39587` - the targeted position repair validates its full-scope vector in one request; the previous per-chunk validation rejected every in-window vector as `field=claim_evidence` because claims referenced panels outside the chunk. RED: `test_targeted_position_repair_validates_full_scope_in_one_request`.
  - `1ded65168fad19eadb5fd9a3a516380c5604cabc` - scope reconciliation compares repaired evidence against the trusted claim-evidence closure instead of the durable candidate's stale passage evidence. RED: `test_position_repair_admits_trusted_evidence_closure_scope`.
- Candidate pool: a bounded driver (`data/_stage2_narration_pool/pool_driver.py`, ignored) drove the canonical `run_narration_repair_candidate` boundary with unique request IDs and sanitized JSONL ledgers. Rounds 1-6 spent 98 narration_repair requests over 48 attempts and failed closed on honest predicates (word window 92-145, grounding, scope). Round 7 attempt 1 was accepted on the first request: 122 words, 53.04 seconds, 5 passages, 701 observations, micro-compaction not required, no provider prose retained. Diagnostic probes added 3 further requests. Zero vision/story provider calls were made; the 701-panel visual/story cache was reused throughout.
- Persistence through the normal entrypoint (`scripts/run_cloud_multimodal_batch.py --project-id 22876a6014a842f48bfca58c10a592b5 --state-dir data/p0-aws-acceptance/cloud-jobs`) reused the canonical repair-result cache with 0 provider calls and persisted: StoryAnalysis `db03ed6687dc48e4be83ed238e618a88` (canonical_panel_count 701, processed_panel_count 701, model_identity_hash `aa2fc9cdf79e7bf625bd028dd9164f0b652ed9e980aae77978f3f9b8121b418b`, narrative identity sharp_friend_v1 v1.0.0 sha256 `134b544c9e2f74ca0b8c64ff55a27c831e76f77a08f26fc2a463112cb0678b3e`), 701 PanelRegion rows with 701 distinct panel_ids, and ScriptVersion `2df45d26729f4945bc3fbc35f886aa3f` v3 (122 words, 53.04s, 5 sections, 25 citations, generator vision_evidence_v3). Audit `script.generate` at 2026-08-22T06:47:36Z. Job state READY_TO_RENDER with approval_required and voice_timing_required intact.
- Post-persistence proofs on the stored narration: `_narration_contract_failures` is empty, `contains_source_dialogue_copy` is false, display derivation is exact (123 punctuation-free uppercase words match the voice script), and repair lineage is recorded (slot_order_hash `ad8b62627f7595b1cc5e7ae5c01204f2adfb04c3386811b7cfb3733e9c8cfc1d`, passage_lineage_hash `91f5189105bd0e02ec3ec8b1290a3e6cc836542c37ac1395d071d0e165cad5c4`).
- No TTS, subtitles, audio, or video was generated. The next stage is voice timing/TTS after silent-render QC, through the existing approval gates.

## 2026-08-22 - Resumed production audit and review-cap correction at `a0fec40ce2b7e262b12f3824f5c05330bbd932b1`

- Reconciliation audit verified Oracle `main`, live GitHub `main`, and the Windows transport clone at `a0fec40ce2b7e262b12f3824f5c05330bbd932b1`; Oracle has no tracked dirty files. Protected runtime `data` and `ms_env.sh` remain untracked and were not read into output. The local Windows repository remains separate and untouched: `app/services/pipeline.py` is modified, while `final_test/` (40 files) and `The Novel’s Extra/` remain protected untracked inputs.
- Stage 2 is verified only through normal persistence: StoryAnalysis `db03ed6687dc48e4be83ed238e618a88`, ScriptVersion `2df45d26729f4945bc3fbc35f886aa3f`, 701/701 panel lineage, 122 words, 53.04 seconds, five passages, strict grounding and anti-dialogue-copy checks passing, and SQLite integrity `ok`. No provider/TTS call was made during this resumed audit.
- Stage 3 is not complete: no current MP4, valid audio bytes, subtitle bundle, FFprobe, blackdetect, contact sheet, or QC PASS exists. The database has five timing rows for the latest script, but their referenced audio files are absent; these rows are incomplete/stale evidence and must not be treated as successful TTS. The current render job is only `review-preview-queued`; job state remains `READY_TO_RENDER`.
- The review-only upscale contract had drifted to `max_scale=2.50`/version `1.2.0`. A RED regression reproduced the mismatch; the scoped GREEN correction sets `review_silent_source_upscale_v1` to deterministic Lanczos cap `1.50`/version `1.3.0`, with the existing low-resolution warning and hard balloon/protected/lineage gates unchanged. Focused upscale tests: 15 passed; affected profile/visual/integration matrix: 99 passed; Ruff, compileall, and `git diff --check`: clean.
- Oracle’s current storage contains only 19 source files matching current original checksums; the protected local `final_test/` source set must be copied into an ignored Oracle runtime location before a truthful render can start. No source images, media, database, cache, provider payload, or credential is eligible for Git.
- Next safe action: publish this source/test/docs checkpoint, securely copy only the 40-file `final_test/` input into protected Oracle runtime, run the review-only silent path through the normal service boundary, inspect actual video/QC, then proceed to configured TTS only after silent QC passes. Do not repeat the valid 701-panel visual/story stages.

## 2026-08-22 - Review-only persisted panel-crop fallback checkpoint

- The review-only source resolver now has an explicit `persisted_panel_crop_v1` materialization path for panels whose immutable full original bytes are unavailable but whose stored asset bytes are an exact persisted crop. It is not a source-lineage rewrite and is never enabled for normal/final/publish rendering.
- Admission requires SHA-256 equality with the stored `SourceAsset.checksum`, decodable RGB pixels, and decoded dimensions exactly equal to the persisted region width and height. The fallback localizes coordinates to `(0, 0, width, height)` while preserving original `source_asset_checksum`, `source_order`, region identity, and evidence lineage. The manifest records the materialization kind, original/persisted dimensions, prepared checksum, policy/version, and deterministic Lanczos transform.
- Candidate construction and render-time materialization use the same typed manifest and revalidate bytes, bounds, evidence, telemetry, and prepared checksum; malformed/tampered bytes or geometry fail closed. Review-only upscaling remains opt-in, `publish_allowed=false`, capped at 1.50x with the existing low-resolution warning, balloon/protected/blank/lineage gates unchanged.
- RED/GREEN evidence: two exact-crop checksum/geometry regressions were RED before the resolver, then the focused upscale/materialization matrix passed 31 tests; Ruff, compileall, and `git diff --check` passed. Parent checkpoint: `6b582568c097a533c4d8f0d02617fef696b656b1`.
- Stage 3 is still unproven: no MP4, audio, subtitle, FFprobe, blackdetect, contact sheet, or QC PASS is claimed. The next action is a normal review-only service run using protected input/runtime storage, with no visual/story reanalysis.

## 2026-08-22 - Review-repair crop fallback forwarding checkpoint

- Runtime diagnosis after the published `persisted_panel_crop_v1` resolver found a narrow forwarding omission: `_repair_review_narrative` validated the explicit review upscale policy but did not pass `allow_persisted_panel_crop_fallback`, so the fallback loader re-raised `review.upscale_source_missing` before candidate construction. This was a local state/argument defect; no provider or TTS request was consumed.
- TDD evidence: the body-level regression was collection-clean and RED with `KeyError: allow_persisted_panel_crop_fallback`; adding the policy-derived forwarding flag made the focused cloud/review/lineage/reference matrix GREEN: 212 passed (130 cloud, 17 upscale, 14 panel-lineage, 6 reference-profile, 45 reference-profile integration). Ruff, compileall, and `git diff --check` are required before publication.
- The fix is review-only and policy-gated (`policy is not None`); normal/final/publish paths retain the default false behavior. The next action after publication is one normal service resume using existing visual/story/narration state, with no visual/story reanalysis; preserve exact lineage and stop on the next strict blocker.

## 2026-08-22 - Legacy review-manifest normalization checkpoint

- The first real review resume reached the regular render boundary with zero provider requests and failed closed on `review.upscale_manifest_invalid`: existing accepted timeline entries encoded `source_materialization: null`. This is legacy metadata for the original-source path, not permission to reinterpret a crop or bypass lineage.
- RED/GREEN evidence: `test_legacy_null_or_missing_materialization_defaults_to_original` failed on the published validator, then passed after a narrow normalizer/validator update. The focused review/cloud/lineage/reference matrix is 213 passed; Ruff, compileall, `git diff --check`, and secret-shape scan are clean.
- Only missing/null materialization is normalized to `original_source_v1`; explicit unknown values remain rejected. The render retry must use the persisted timeline and exact DB/storage overrides, with no visual/story/provider repeat; the previous render attempt consumed zero provider/TTS requests and left the job in `NEEDS_REVIEW` with `review.preview_failed`.

## 2026-08-22 - Conservative balloon geometry admission correction

- First silent render was generated but rejected during visual review: frame 36 retained a source speech balloon. Root cause was a trusted balloon record whose normalized bbox and polygon envelopes disagreed; bbox-only overlap returned zero for the selected ROI at source order 225.
- RED/GREEN: added `test_candidate_rejects_balloon_when_bbox_and_polygon_disagree`; conservative bbox+polygon evaluation passes the 76-test framing/review/upscale collection (75 passed, 1 existing skip). No provider/TTS call was made. The stale pre-fix MP4 cannot be used as Video 1 evidence.
- Next task is publish this strict correction, rerender only the silent review from persisted state, inspect actual frames/contact sheet, and continue to TTS only after corrected visual QC.

## 2026-08-22 - Sidecar serialization checkpoint

- [x] Publish `0cc17f536202a28ab09bce18b5952fe457e3d4d0` with the JSON-safe review sidecar boundary and its RED/GREEN regression (`52 passed` focused).
- [ ] Rerun the cached normal review path, verify sidecar/FFprobe/blackdetect/QC/contact sheet and inspect actual frames; only then cross the TTS boundary.
- [ ] Preserve the no-provider/no-TTS request count for this checkpoint and do not repeat valid visual/story stages.

## 2026-08-22 - Silent-review timing contract checkpoint `4613214`

- [x] Publish the review-only duration fix after the cached replay built 41 scenes and failed at `subtitle.timing_out_of_bounds` from absolute-end versus rounded-render-duration drift.
- [x] Keep the change narrow: `_silent_review_media_duration` mirrors renderer scene-duration rounding; default/voiced timing remains unchanged; durable review failures preserve `subtitle.*`.
- [x] Record RED/GREEN evidence: 30-scene `1.0004s` drift regression, subtitle-code regression, affected matrix `197 passed, 20 warnings`, Ruff/compileall/diff-check/secret scan clean; no provider/TTS request.
- [ ] Resume the cached normal review path, then require sidecar, FFprobe, blackdetect, strict QC, contact sheet, actual frame review, and `REVIEW_PREVIEW_READY` before TTS.

## 2026-08-22 - Strict blank-space checkpoint `ff2484b`

- [x] Diagnose the cached 41-shot technical preview: 34 shots exceeded profile blank target `0.03`, max `0.536224`, and final bundle correctly raised `review.blank_space_exceeds_target`.
- [x] Add the explicit profile-aware feasibility target and propagate it through planner, repair ledger, both reference render paths, and final review QC; retain strict `visual.blank_infeasible` rejection and telemetry.
- [x] GREEN affected matrix/static gates; no provider/TTS request and no accepted MP4. The timing fix remains published at `4613214`; this source/test correction is `ff2484b`.
- [ ] Rerun cached review, inspect the regenerated contact sheet/frames, and cross to TTS only after strict blank/balloon/protected/lineage/QC gates pass.

## 2026-08-22 - Cached visual-repair admission fix (pending publication)

- [x] Reproduce the local discrepancy: invalid repair-cache reconciliation raised `visual.narrative_repair_ungrounded` before the bounded repair provider path.
- [x] Fix only the cache boundary: typed cache validation failures are cache misses; valid matching entries still reuse; strict grounding/lineage/visual/duration gates remain unchanged.
- [x] RED/GREEN focused cloud/visual-repair matrix: `146 passed`; no real provider/TTS request consumed by this slice.
- [x] Record this source/test/docs checkpoint; next rerun the cached normal review path without repeating valid 701-panel visual/story stages. Require strict silent QC and `REVIEW_PREVIEW_READY` before TTS.

## 2026-08-22 - Visual-repair observability checkpoint

- [x] Offline-audit the 701-panel ledger: 277 candidates, 1,734 ROIs, 71 feasible ROIs, 36 panels; preserve all hard framing gates.
- [x] Add sanitized final visual-repair failure metrics and RED/GREEN regression; 145 focused visual/cloud tests pass with static/diff/secret gates.
- [ ] Publish the source/test checkpoint, then rerun only the bounded visual-repair stage from cached visual/story evidence; do not repeat 701-panel analysis.
- [ ] Accept no narration/render/TTS result until visual repair, strict silent QC, and `REVIEW_PREVIEW_READY` are proven.
