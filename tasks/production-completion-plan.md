# ManhwaShorts production-completion plan

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
