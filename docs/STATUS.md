# Current status

Updated: 2026-08-11

## Implementation planning amendment - 2026-08-11

- Approved the docs-only design in
  docs/superpowers/specs/2026-08-11-balloon-free-framing-narrative-identity-v3-design.md
  for COLOR_AGNOSTIC_BALLOON_FREE_V1 framing and sharp_friend_v1 narration.
- Planning baseline is clean main at
  7fe75cd3c7b19ade96bc39f3f00a84aa2b06865f. The recorded historical full
  non-slow result is 635 passed; it was not rerun for this docs-only change.
- The plans are amended at baseline
  f7c7b065ca9436c86070fd717e99ac55af2819d3. Plan 1 now has six tasks:
  typed states, provider geometry acquisition, detector, crop, fallback/QC,
  and silent review. Plan 2 remains six tasks.
- The correction preserves unknown visual geometry for lineage/audit and
  rejects it only at reference readiness. It also adds the missing provider
  prompt, adapter validation, mock, and snapshot acquisition boundary before
  color-agnostic detection.
- Implementation planning is complete in
  docs/superpowers/plans/2026-08-11-balloon-free-color-agnostic-framing.md and
  docs/superpowers/plans/2026-08-11-sharp-friend-narrative-identity-v3.md.
- Task 1 typed states/persistence is green and published at
  `a45084688ebbe4b2b21ad1ea251b884f1fcee8ab`. The next atomic task is Visual
  Plan Task 2: provider geometry acquisition with explicit visual-mode pipeline
  wiring; reference readiness remains blocked until that evidence exists.
- Voice choice, provider configuration, auditions, audio generation, and
  final voice rendering remain explicitly deferred until the user chooses
  local or API execution. Rights/source checks keep publish_allowed=false.
- Source and test execution remains VPS-only. Because VPS GitHub SSH auth is
  unavailable, exact history is published through the isolated Windows
  transport clone; runtime data, media, databases, credentials, and review
  artifacts remain outside Git.

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
- CPU FFmpeg render: 1080×1920, 30 FPS, H.264/AAC.
- Persisted QC report, immutable override history, render leases, stale-job
  recovery, resource metrics, and cleanup.
- Phase 2 editorial gates: deterministic panel penalties and asset reuse caps,
  chronology/source-family audit reasons, four-mode motion diversity, action
  hard cuts, 0.12-0.18s section transitions, and one-word display captions.
- Private-by-default publication gate with rights and approval checks.
- BYOK encryption and provider discovery.
- Vision-first analyzer v2 contract with complete chapter evidence gates and
  five-role, word-bounded, evidence-grounded narration.
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

## Current runtime boundary

- Production execution target: Google VPS through the `google` SSH alias.
- Local machine: orchestration, source transfer, and requested artifact delivery.
- No production media or credentials belong in Git.
- OmniVoice Studio remains an external voice experiment. It is not a core
  dependency, default provider, or release gate.

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
6. Final delivery uses 1080x1920 30fps H.264 High/yuv420p with final audio
   normalization toward -14 LUFS and true peak at or below -1.5 dBTP.
7. No unlicensed music or SFX is attached; rights/source checks remain hard blockers.

Current state: **development / review-only**. Task 1 production persistence is
published and fully verified; Task 2 provider acquisition is the next gated
slice, and reference output remains unavailable until its geometry contract is
fulfilled. VPS GitHub SSH is unavailable, so approved commits are published
through the isolated Windows transport workflow.
