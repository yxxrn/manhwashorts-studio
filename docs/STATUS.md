# Current status

Updated: 2026-08-11

## Implementation planning amendment - 2026-08-11

- Approved the docs-only design in
  docs/superpowers/specs/2026-08-11-balloon-free-framing-narrative-identity-v3-design.md
  for COLOR_AGNOSTIC_BALLOON_FREE_V1 framing and sharp_friend_v1 narration.
- The current implementation baseline for this slice is clean main at
  aa11bdbd500beca00ad7481b85731f17297e8e58. The historical full non-slow
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
- Visual Plan Tasks 1-3 are green and published before the current parent
  `aa11bdbd500beca00ad7481b85731f17297e8e58`. The amendment adds a standalone
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

Current state: **development / review-only**. Visual Plan Task 5 hardening is
committed at 61258817101a10a3b11916f653d89aca21088fe2; Task 6 is the next
fallback/QC consumer of panel lineage and feasibility telemetry. Reference
output remains unavailable until all lineage, balloon, protected-region, and
rights gates are fulfilled. VPS GitHub SSH is unavailable, so approved
commits are published through the isolated Windows transport workflow.
