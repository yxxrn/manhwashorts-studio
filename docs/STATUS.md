# Current status

Updated: 2026-08-11

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
