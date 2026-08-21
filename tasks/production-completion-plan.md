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
zero provider requests. The source/test diff is still unpublished at parent
`24971e742653aeae48a2b15757adccf44a5dedb9`; commit and exact-object push are
the immediate next action before one bounded cached repair request.
