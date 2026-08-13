# Real Chapter Narrative Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The named superpowers skills are not installed in this repository; the active execution protocol is Luna task-by-task work with Sol review gates.

**Goal:** Build an isolated, deterministic local review workflow that examines the complete ordered 23-panel chapter with Codex manual vision and produces a provenance-labeled Sharp Friend v1 narrative bundle without changing production evidence, database state, media, or provider behavior.

**Architecture:** Add pure validation and artifact helpers under a focused review module, then add a thin local review CLI that reads the existing ignored chapter ledger and writes an ignored bundle. The CLI separates immutable source verification, manual observation input, causal/narrative review data, display derivation, QC, and human approval state. Existing `sharp_friend_v1`, v2, evidence, naturalness, and display contracts are consumed explicitly; no provider or production persistence path is called.

**Tech Stack:** Existing Python 3 application, standard-library `dataclasses`, `pathlib`, `hashlib`, `json`, and `unicodedata`; existing Pillow for read-only image dimensions; current `pytest`, Ruff, compileall, and the repository’s manual-preview validator. No dependency additions, database migration, API/UI change, FFmpeg invocation, TTS, or network call.

## Global Constraints

- The manual run examines source orders `0..23` exactly once in ascending order; order `0` is inspected and recorded as title/front matter but excluded from story claims and the 23-panel narrative scope.
- Source orders `1..23` are the complete story scope; no panel may be sampled, skipped, reordered, randomly selected, or replaced by a filename guess.
- The provenance label is exactly `codex_manual_vision_reference_v1`; it is never `vision_evidence_v2`, `editorial_full_panel_evidence_v1`, `StoryAnalysis`, or `PanelRegion` production evidence.
- Manual observations never write production ORM rows, call a vision provider, call a TTS provider, configure credentials, or satisfy a production readiness gate.
- `sharp_friend_v1` is explicit; default v2 behavior and five-role compatibility remain unchanged.
- Spoken narration retains punctuation and casing. Display text is independently derived as uppercase, punctuation-free, one Unicode-alphanumeric word per nonempty cue.
- The initial 90–125 spoken-word target is advisory; no automatic rewrite or false pass is allowed outside the existing hard validator rules.
- Narration has 4–6 evidence-linked passages, nonfixed semantic roles, an explicit ending kind, and no copied dialogue, CTA, generic hype, invented identities/relations/facts, or unsupported certainty.
- Human approval is a later explicit `APPROVED_REFERENCE_ONLY` decision and is never fabricated by fixtures, QC, or the CLI.
- `publish_allowed=false` and `rights_status=internal review only` remain mandatory.
- Source images, contact sheets, MP4/WAV/MP3, databases, credentials, `.env`, absolute-path secrets, raw provider payloads, and runtime review bundles remain ignored and untracked.
- No subtitle timing, ASS/SRT, audio, video, FFmpeg, ORM/model/schema/migration, UI, publication, upload, or provider implementation is in scope.
- Every code-bearing task has collection-clean body-level RED, smallest GREEN, focused regression tests, and an atomic commit; the manual chapter review starts only after the software gates are green.

---

## Current interface map and ownership

The implementer starts from `9eecb6646a2a5a103a13681fd6c649a8af2a1716` and must verify the live paths before editing. The current code facts this plan relies on are:

- `scripts/review/render_codex_manual_preview.py:84` has `validate_edit_plan(plan, manifest) -> ValidatedPlan`, enforcing manifest orders `0..23`, shot orders `1..23`, `random_sampling=False`, `publish_allowed=False`, internal-review rights, normalized crops, and 50–60 second preview constraints. It is a media-preview validator and must not be expanded into the narrative bundle schema.
- `app/services/narrative_identity.py:get_narrative_identity(profile_id: str) -> NarrativeIdentityProfile` and `load_narrative_instruction(profile_id: str) -> tuple[str, str, str]` provide the verified Sharp Friend identity and hashes.
- `app/services/analyzer_contract.py:validate_analyzer_output(output, *, expected_panel_ids, narrative_profile_id=None) -> None` is the production validator. The review module may call it only with a fully constructed sanitized offline document during local validation/tests; it must never persist the result or relabel it as provider output.
- `app/services/editorial_qc.py:screen_narrative_naturalness(passages, claims, profile) -> NarrativeNaturalnessReport` screens text without rewriting it.
- `app/services/quality.py:check_narrative_naturalness(report) -> list[CheckResult]` converts stable narrative findings into checks.
- `app/services/timeline.py:normalize_display_text(text: str) -> str` is the current punctuation/symbol-free uppercase display normalizer.
- `tests/test_codex_manual_preview.py` is the existing pure manual-preview contract suite. New narrative-review tests must not mutate its v2 assertions or add runtime data to Git.
- `docs/STATUS.md` and `CHANGELOG.md` receive the exact implementation/manual-run handoff only after all software tests pass and the human review artifact is truthfully available.
- The verified local input is `data/panel-review-9c1-20260809/manifest.json`. Its 24 ordered entries resolve to `data/panel-review-9c1-20260809/ordered/*.jpg`, with source orders `0..23`, manifest checksums, dimensions, asset IDs, panel metadata, and declared internal-review rights. The 24 entries are the only authoritative input for Task 6; `data/codex-vision-preview-50-60s-v2/manifest-local.json` is an edit-preview manifest and is not a substitute source ledger.

Owned paths are bounded by task. No task may silently add production pipeline, model, migration, provider, voice, render, UI, or database files.

---

## Task 1: Define immutable source-ledger and manual provenance contracts

**Files:**

- Create: `app/services/manual_narrative_review.py`
- Test: `tests/test_manual_narrative_review.py`

**Interfaces:**

- Consumes: JSON-like manifest mappings and existing source files; no ORM objects and no network responses.
- Produces:

  ```python
  @dataclass(frozen=True)
  class SourceLedgerEntry:
      source_order: int
      source_asset_id: str
      panel_id: str
      review_path: str
      source_storage_path: str
      sha256: str
      width: int
      height: int
      rights_status: str
      included_in_story: bool
      exclusion_reason: str

  @dataclass(frozen=True)
  class ManualReviewLedger:
      provenance_kind: str
      production_evidence: bool
      production_analysis: bool
      publish_allowed: bool
      rights_status: str
      entries: tuple[SourceLedgerEntry, ...]
      ledger_sha256: str

  class ManualReviewError(ValueError):
      code: str

  def load_source_ledger(path: Path, *, base_dir: Path) -> ManualReviewLedger:
      raise NotImplementedError

  def validate_source_ledger(
      ledger: ManualReviewLedger,
      *,
      base_dir: Path,
      expected_orders: Sequence[int] = tuple(range(24)),
  ) -> ManualReviewLedger:
      raise NotImplementedError

  def canonical_ledger_json(ledger: ManualReviewLedger, *, include_hash: bool = False) -> str:
      raise NotImplementedError
  ```

The canonical hash excludes `ledger_sha256` and uses sorted JSON keys, compact separators, UTF-8, and normalized path strings. The loader resolves only `review_path` values contained under the declared local review/data root; `source_storage_path` is retained as provenance metadata and is never opened when it is an absolute path from the historical VPS. Absolute review paths outside the root and path traversal fail with `review.source_path_invalid`.

- [ ] **Step 1: Write body-level RED tests without importing the missing implementation at collection time.**

  Add dynamic imports inside tests and assert the required stable failures:

  ```python
  def _module():
      return importlib.import_module("app.services.manual_narrative_review")

  def test_valid_ledger_requires_title_and_all_story_orders():
      module = _module()
      ledger = module.load_source_ledger(valid_manifest_path, base_dir=tmp_path)
      assert [entry.source_order for entry in ledger.entries] == list(range(24))
      assert ledger.entries[0].included_in_story is False
      assert ledger.entries[0].exclusion_reason == "title_front_matter"
      assert [entry.source_order for entry in ledger.entries[1:]] == list(range(1, 24))
      assert ledger.provenance_kind == "codex_manual_vision_reference_v1"
      assert ledger.production_evidence is False

  @pytest.mark.parametrize("mutation", ["duplicate", "missing", "out_of_order", "unknown_path", "checksum", "dimension"])
  def test_ledger_mutations_fail_closed(mutation, valid_manifest, tmp_path):
      module = _module()
      broken = mutate_manifest(valid_manifest, mutation)
      with pytest.raises(module.ManualReviewError, match=r"review\."):
          module.load_source_ledger(write_json(tmp_path / "ledger.json", broken), base_dir=tmp_path)
  ```

- [ ] **Step 2: Run the collection-clean RED command.**

  Run:

  ```powershell
  python -m pytest tests/test_manual_narrative_review.py -q
  ```

  Expected: collection succeeds; the intended body tests fail because `app.services.manual_narrative_review` and its interfaces do not exist. A collection/import/fixture failure is not an acceptable RED result.

- [ ] **Step 3: Implement the ledger dataclasses, canonical serializer, safe root-bound path resolution, SHA-256 verification, and title/story-order rules.**

  `validate_source_ledger` must require exactly 24 unique entries in order `0..23`, exactly one title exclusion at order `0`, exactly 23 included story entries at orders `1..23`, nonempty IDs/paths/rights, `publish_allowed=False`, `production_evidence=False`, `production_analysis=False`, and matching current file bytes/dimensions. Use Pillow only to read dimensions; do not rewrite images.

- [ ] **Step 4: Run GREEN and ledger regressions.**

  Run:

  ```powershell
  python -m pytest tests/test_manual_narrative_review.py -q
  ```

  Expected: all ledger tests pass, including duplicate/missing/order/path/checksum/dimension failures and deterministic repeated ledger hash.

- [ ] **Step 5: Commit the independently reviewable ledger slice.**

  ```powershell
  git add app/services/manual_narrative_review.py tests/test_manual_narrative_review.py
  git diff --cached --check
  git commit -m "feat: validate manual narrative source ledger"
  ```

---

## Task 2: Add safe ignored bundle I/O and independent display derivation

**Files:**

- Modify: `app/services/manual_narrative_review.py`
- Test: `tests/test_manual_narrative_review.py`

**Interfaces:**

- Consumes: `ManualReviewLedger`, sanitized JSON-compatible review records, and `timeline.normalize_display_text`.
- Produces:

  ```python
  BUNDLE_FILES: tuple[str, ...] = (
      "source_ledger.json",
      "panel_understanding.json",
      "chapter_map.json",
      "narrative_review.json",
      "narration_spoken.txt",
      "display_cues.json",
      "qc_report.json",
  )

  def derive_display_cues(spoken_text: str) -> tuple[dict[str, object], ...]:
      raise NotImplementedError

  def write_review_bundle(root: Path, bundle: Mapping[str, object], *, ledger: ManualReviewLedger) -> Path:
      raise NotImplementedError

  def read_review_bundle(root: Path, *, ledger: ManualReviewLedger) -> dict[str, object]:
      raise NotImplementedError
  ```

`write_review_bundle` creates only the ignored bundle root and JSON/text files; it rejects image, audio, video, database, credential, `.env`, and absolute-path payload values. It writes via a temporary file inside the same root followed by an atomic replace and verifies the read-back canonical hash. `read_review_bundle` rejects missing/extra canonical files, malformed JSON, hash mismatch, traversal/absolute-path values, and provenance values other than `codex_manual_vision_reference_v1`.

- [ ] **Step 1: Add body RED tests for bundle and spoken/display separation.**

  ```python
  def test_display_derivation_does_not_mutate_spoken_text():
      module = _module()
      spoken = "Why can't Jin-Woo move?"
      before = spoken[:]
      cues = module.derive_display_cues(spoken)
      assert [cue["display_text"] for cue in cues] == ["WHY", "CANT", "JINWOO", "MOVE"]
      assert spoken == before
      assert all(text.isalnum() and text == text.upper() for text in [cue["display_text"] for cue in cues])

  def test_bundle_rejects_production_provenance_and_media_payload(tmp_path, valid_ledger):
      module = _module()
      bundle = valid_bundle(valid_ledger)
      bundle["provenance_kind"] = "vision_evidence_v2"
      with pytest.raises(module.ManualReviewError, match="review.provenance_invalid"):
          module.write_review_bundle(tmp_path / "bundle", bundle, ledger=valid_ledger)
  ```

- [ ] **Step 2: Run RED.**

  ```powershell
  python -m pytest tests/test_manual_narrative_review.py -q
  ```

  Expected: collection-clean failures only for the new bundle writer/reader and display helper.

- [ ] **Step 3: Implement bundle I/O and display derivation.**

  Use the existing `normalize_display_text` for each nonempty whitespace token. Omit punctuation-only tokens. Store no timing because this slice produces no audio. The writer must keep the spoken text byte-for-byte, derive display cues after it is fixed, and never write a display value back into `narration_spoken.txt`.

- [ ] **Step 4: Run GREEN plus path and serialization regressions.**

  ```powershell
  python -m pytest tests/test_manual_narrative_review.py -q
  ```

  Expected: deterministic read/write round trip, atomic bundle output, exact cue mapping, rejection of production provenance, media payload, missing file, extra file, hash drift, absolute path, and traversal.

- [ ] **Step 5: Commit the bundle/display slice.**

  ```powershell
  git add app/services/manual_narrative_review.py tests/test_manual_narrative_review.py
  git diff --cached --check
  git commit -m "feat: write safe manual narrative review bundles"
  ```

---

## Task 3: Define offline panel observations, chapter map, and Sharp Friend review contract

**Files:**

- Modify: `app/services/manual_narrative_review.py`
- Test: `tests/test_manual_narrative_review.py`

**Interfaces:**

- Consumes: validated `ManualReviewLedger` and manually authored sanitized records for all 24 orders.
- Produces:

  ```python
  @dataclass(frozen=True)
  class ManualPanelObservation:
      source_order: int
      source_asset_id: str
      panel_id: str
      visible_summary: str
      visible_entities: tuple[str, ...]
      actions: tuple[str, ...]
      setting_or_continuity: str
      dialogue_present: bool
      dialogue_paraphrase: str
      uncertainties: tuple[str, ...]
      confidence: str
      evidence_status: str = "manual_visual_review"

  @dataclass(frozen=True)
  class ManualNarrativeReview:
      panel_observations: tuple[ManualPanelObservation, ...]
      chapter_map: Mapping[str, object]
      passages: tuple[Mapping[str, object], ...]
      ending_kind: str
      unresolved_question: str
      spoken_text: str

  def validate_panel_observations(
      ledger: ManualReviewLedger,
      observations: Sequence[Mapping[str, object]],
  ) -> tuple[ManualPanelObservation, ...]:
      raise NotImplementedError

  def validate_chapter_map(
      observations: Sequence[ManualPanelObservation],
      chapter_map: Mapping[str, object],
  ) -> None:
      raise NotImplementedError

  def build_manual_analyzer_projection(
      observations: Sequence[ManualPanelObservation],
      chapter_map: Mapping[str, object],
      review: ManualNarrativeReview,
  ) -> dict[str, object]:
      raise NotImplementedError

  def validate_manual_narrative(
      review: ManualNarrativeReview,
      *,
      ledger: ManualReviewLedger,
  ) -> None:
      raise NotImplementedError
  ```

`validate_panel_observations` requires exactly 24 records matching ledger IDs/order, a title exclusion record with no story claim, nonempty visible summary/actions/continuity for every story panel, only `manual_visual_review` evidence status, and no raw source-text fields. `validate_chapter_map` requires the exact story order set `1..23`, every beat/edge to reference existing story orders, and every causal interpretation to contain a nonempty qualification. `build_manual_analyzer_projection` deterministically maps the validated observations, chapter map, and review into the six v3 top-level keys: `analysis_version`, `panels`, `claims`, `narrative_outline`, `script_passages`, and `continuity`. Its `panels` preserve the ordered manual panel IDs and summaries; its `claims` carry only sanitized claim IDs, evidence panel IDs, and qualifications; its `narrative_outline` is `{story_spine, ending_kind}`; and its `script_passages` carry passage IDs, semantic roles, spoken text, claim IDs, evidence references, and qualifications. `validate_manual_narrative` requires 4–6 passages, unique IDs/roles, nonempty claim/evidence references, exact ending-kind behavior, and the current Sharp Friend hard screens. It calls `validate_analyzer_output(..., narrative_profile_id="sharp_friend_v1")` on that local sanitized projection with the full expected panel sequence; it never persists or labels the projection as provider evidence.

- [ ] **Step 1: Write RED fixtures and tests for whole-panel coverage and narrative gates.**

  ```python
  def test_observations_require_all_orders_and_exact_lineage(valid_ledger):
      module = _module()
      observations = valid_observations(valid_ledger)
      observations[-1]["source_order"] = 22
      with pytest.raises(module.ManualReviewError, match="review.panel_coverage_invalid"):
          module.validate_panel_observations(valid_ledger, observations)

  @pytest.mark.parametrize("mutation,code", [
      ("missing_evidence", "review.evidence_missing"),
      ("foreign_panel", "review.evidence_foreign"),
      ("copied_dialogue", "narrative.balloon_dialogue_copied"),
      ("cta", "narrative.cta"),
      ("hype", "narrative.generic_hype"),
      ("question_consequence", "narrative.ending_invalid"),
  ])
  def test_manual_narrative_rejects_known_bad_contract(mutation, code, valid_review, valid_ledger):
      module = _module()
      broken = mutate_review(valid_review, mutation)
      with pytest.raises(module.ManualReviewError, match=re.escape(code)):
          module.validate_manual_narrative(broken, ledger=valid_ledger)

  def test_manual_narrative_allows_four_and_six_passages_and_non_question_consequence(valid_ledger):
      module = _module()
      for review in (valid_review_with_passage_count(4, "consequence"), valid_review_with_passage_count(6, "cliffhanger")):
          module.validate_manual_narrative(review, ledger=valid_ledger)
  ```

- [ ] **Step 2: Run RED.**

  ```powershell
  python -m pytest tests/test_manual_narrative_review.py -q
  ```

  Expected: collection-clean intended failures for missing observation/narrative validators or their not-yet-defined stable codes. The fixtures must not fail because a source image or provider is unavailable.

- [ ] **Step 3: Implement observation/chapter/narrative validators using existing contracts.**

  Use stable safe codes: `review.panel_coverage_invalid`, `review.panel_lineage_invalid`, `review.source_text_leak`, `review.evidence_missing`, `review.evidence_foreign`, `review.chapter_map_invalid`, `narrative.unsupported_claim`, `narrative.interpretation_unqualified`, `narrative.balloon_dialogue_copied`, `narrative.cta`, `narrative.generic_hype`, `narrative.ending_invalid`, and `narrative.display_derivation_invalid`. Do not include the offending text in exception messages. The local projection must retain the completed v3 six top-level keys and must not call the legacy five-role path.

- [ ] **Step 4: Run GREEN and compatibility matrix.**

  ```powershell
  python -m pytest tests/test_manual_narrative_review.py tests/test_narrative_identity.py tests/test_narrative_pipeline.py tests/test_narrative_qc.py tests/test_narrative_review.py tests/test_vision_synthesis.py tests/test_analyzer_contract.py -q
  ```

  Expected: all new review tests and existing v1/v2/v3 tests pass; no production database, provider, voice, subtitle, or render call occurs.

- [ ] **Step 5: Commit the offline contract slice.**

  ```powershell
  git add app/services/manual_narrative_review.py tests/test_manual_narrative_review.py
  git diff --cached --check
  git commit -m "feat: validate manual chapter narrative contract"
  ```

---

## Task 4: Add deterministic QC report and explicit revision lifecycle

**Files:**

- Modify: `app/services/manual_narrative_review.py`
- Test: `tests/test_manual_narrative_review.py`

**Interfaces:**

- Consumes: validated ledger, observations, chapter map, narrative review, and display cues.
- Produces:

  ```python
  REVIEW_STATES = frozenset({
      "DRAFT", "QC_BLOCKED", "PENDING_EDITORIAL_REVIEW",
      "APPROVED_REFERENCE_ONLY", "REJECTED", "REVISED",
  })

  @dataclass(frozen=True)
  class ReviewQCReport:
      blocking_findings: tuple[str, ...]
      warnings: tuple[str, ...]
      metrics: Mapping[str, float | int]
      report_sha256: str
      review_state: str

  def build_review_qc(
      review: ManualNarrativeReview,
      *,
      ledger: ManualReviewLedger,
      display_cues: Sequence[Mapping[str, object]],
  ) -> ReviewQCReport:
      raise NotImplementedError

  def approve_reference_review(
      bundle: Mapping[str, object], *, reviewer: str, reviewed_at: str
  ) -> dict[str, object]:
      raise NotImplementedError

  def reject_reference_review(
      bundle: Mapping[str, object], *, reviewer: str, reason: str
  ) -> dict[str, object]:
      raise NotImplementedError

  def revise_reference_review(
      bundle: Mapping[str, object], *, revision_id: str
  ) -> dict[str, object]:
      raise NotImplementedError
  ```

`build_review_qc` computes sentence percentiles/variance, repeated sentence/opening ratios, connector diversity, causal-transition coverage, contractions, claim/evidence coverage, qualification coverage, word/duration estimates, ending checks, and hard screens. It returns `PENDING_EDITORIAL_REVIEW` only when blockers are empty. `approve_reference_review` requires a nonempty reviewer, timestamp, exact revision hash, and clear QC; it writes only a new returned mapping with `APPROVED_REFERENCE_ONLY`, never a database row. Editing/revision clears approval and creates a new revision identity. The lifecycle never emits `SCRIPT_APPROVED`.

- [ ] **Step 1: Write RED lifecycle/QC tests.**

  ```python
  def test_qc_separates_blockers_from_warnings(valid_review, valid_ledger):
      module = _module()
      report = module.build_review_qc(valid_review, ledger=valid_ledger, display_cues=module.derive_display_cues(valid_review.spoken_text))
      assert report.review_state == "PENDING_EDITORIAL_REVIEW"
      assert "narrative.template_risk" in report.warnings or not report.warnings
      assert not report.blocking_findings

  def test_approval_requires_human_and_never_creates_production_approval(valid_bundle):
      module = _module()
      with pytest.raises(module.ManualReviewError, match="review.approval_invalid"):
          module.approve_reference_review(valid_bundle, reviewer="", reviewed_at="2026-08-13T00:00:00Z")
      approved = module.approve_reference_review(valid_bundle, reviewer="editor-1", reviewed_at="2026-08-13T00:00:00Z")
      assert approved["approval_state"] == "APPROVED_REFERENCE_ONLY"
      assert approved["production_evidence"] is False
      assert approved["publish_allowed"] is False
      assert "SCRIPT_APPROVED" not in approved.values()
  ```

- [ ] **Step 2: Run RED.**

  ```powershell
  python -m pytest tests/test_manual_narrative_review.py -q
  ```

  Expected: collection-clean body failures for QC and lifecycle behavior only.

- [ ] **Step 3: Implement the deterministic QC metrics and immutable returned-mapping lifecycle.**

  Reuse existing pure naturalness calculations where possible, but keep the review provenance code separate. Hash canonical sanitized report data, never raw text or absolute paths. Block malformed or unsupported states before approval; reject nonempty reasons missing on rejection; revision always clears prior approval.

- [ ] **Step 4: Run GREEN and review-contract matrix.**

  ```powershell
  python -m pytest tests/test_manual_narrative_review.py tests/test_narrative_identity.py tests/test_narrative_pipeline.py tests/test_narrative_qc.py tests/test_narrative_review.py -q
  ```

  Expected: QC/lifecycle tests pass, existing Sharp Friend review behavior remains green, and no production approval/database state changes.

- [ ] **Step 5: Commit the QC/lifecycle slice.**

  ```powershell
  git add app/services/manual_narrative_review.py tests/test_manual_narrative_review.py
  git diff --cached --check
  git commit -m "feat: gate manual narrative review lifecycle"
  ```

---

## Task 5: Build the two-phase local manual-review runner

**Files:**

- Create: `scripts/review/run_real_chapter_narrative_review.py`
- Modify: `app/services/manual_narrative_review.py` only if a narrow orchestration helper is required by the tested CLI boundary
- Test: `tests/test_real_chapter_narrative_review.py`

**Interfaces:**

- Consumes: the existing ignored source ledger/review bundle and the validated Task 1–4 interfaces.
- Produces: a local ignored `source_ledger.json`, `panel_understanding.json`, `chapter_map.json`, `narrative_review.json`, `narration_spoken.txt`, `display_cues.json`, and `qc_report.json`; no media.

The CLI has two explicit phases so software verification never pretends to perform visual analysis:

```text
prepare --manifest data/panel-review-9c1-20260809/manifest.json --output data/real-chapter-narrative-review-20260813
  -> verifies source files and writes immutable source_ledger.json plus a blank ordered observation template

  finalize --bundle data/real-chapter-narrative-review-20260813 --observations observations.json
           --chapter-map chapter-map.json --narrative narrative.json
  -> validates all 24 observations, causal map, Sharp Friend passages, display cues, QC, and PENDING_EDITORIAL_REVIEW
```

`prepare` reads every existing panel image through the ledger’s exact `review_path` resolved beneath the local manifest directory, verifies SHA and dimensions, and writes the ordered template. It records `source_storage_path` as historical provenance but never opens a remote absolute path and never falls back to it when the local review file is missing. It never samples, opens a production provider, generates text, or renders. The executor must use the local visual-review workflow to inspect each file in order; it may create an untracked contact sheet only as a temporary convenience, but the canonical ledger remains the source of order and identity.

`finalize` accepts only sanitized manual records. It never accepts a provider payload, `vision_evidence_v2`, a `StoryAnalysis` ID, a production `PanelRegion` ID, a filename-only record, or a claimed production hash. It writes no production database state.

- [ ] **Step 1: Write collection-safe RED tests for CLI phase separation.**

  ```python
  def test_prepare_verifies_all_24_files_without_creating_media(tmp_path, valid_manifest):
      result = run_cli("prepare", "--manifest", valid_manifest, "--output", tmp_path / "review")
      assert result.returncode == 0
      assert json.loads((tmp_path / "review/source_ledger.json").read_text())["entries"][0]["source_order"] == 0
      assert not list((tmp_path / "review").glob("*.mp4"))
      assert not list((tmp_path / "review").glob("*.wav"))

  def test_finalize_rejects_missing_order_and_production_provenance(tmp_path, valid_bundle_inputs):
      observations = valid_bundle_inputs.observations[:-1]
       result = run_cli(
           "finalize",
           "--bundle", valid_bundle_inputs.root,
           "--observations", write_json(tmp_path / "obs.json", observations),
           "--chapter-map", write_json(tmp_path / "chapter-map.json", valid_bundle_inputs.chapter_map),
           "--narrative", write_json(tmp_path / "narrative.json", valid_bundle_inputs.narrative),
       )
      assert result.returncode != 0
      assert "review.panel_coverage_invalid" in result.stderr
  ```

- [ ] **Step 2: Run RED.**

  ```powershell
  python -m pytest tests/test_real_chapter_narrative_review.py -q
  ```

  Expected: collection succeeds and body tests fail because the runner does not yet exist; no fixture may call a provider or require live credentials.

- [ ] **Step 3: Implement `prepare` with exact local ledger resolution and `finalize` with pure helper orchestration.**

  The runner must resolve the existing chapter by an explicit manifest path supplied by the user/executor. It must not search randomly, infer source order from filenames, or assume a provider. It refuses output roots inside the repository’s tracked source tree unless the path is ignored runtime data. It prints only stable status/error codes and sanitized paths under the declared review root.

- [ ] **Step 4: Run GREEN on synthetic fixtures and prove no-go boundaries.**

  ```powershell
  python -m pytest tests/test_real_chapter_narrative_review.py tests/test_codex_manual_preview.py -q
  python -m compileall -q app scripts/review
  python -m ruff check app/services/manual_narrative_review.py scripts/review/run_real_chapter_narrative_review.py tests/test_manual_narrative_review.py tests/test_real_chapter_narrative_review.py
  git diff --check
  ```

  Expected: all focused tests pass; no generated media, database, credentials, provider network call, TTS call, FFmpeg invocation, or production ORM mutation occurs.

- [ ] **Step 5: Commit the runner slice.**

  ```powershell
  git add app/services/manual_narrative_review.py scripts/review/run_real_chapter_narrative_review.py tests/test_manual_narrative_review.py tests/test_real_chapter_narrative_review.py
  git diff --cached --check
  git commit -m "feat: add local real chapter narrative review runner"
  ```

---

## Task 6: Execute the real 23-panel reference review after software gates

**Files:**

- Runtime ignored only: the exact existing chapter ledger and local review bundle root selected in Task 5.
- Modify after completion: `docs/STATUS.md`, `CHANGELOG.md`

**Interfaces:**

- Consumes: green Task 1–5 code/tests, the verified current 24-entry source ledger, and the local panel files.
- Produces: the seven sanitized bundle files, QC report, and a handoff record; no source images/media are committed.

This is an execution task, not a software fixture. The executor must stop if the current local ledger cannot verify all 24 files or if any required panel is absent. It must not repair checksums, substitute another chapter, invoke a provider, or infer missing panels.

- [ ] **Step 1: Verify the clean main/branch and software gates before opening panels.**

  ```powershell
  git status --short --branch
  python -m pytest tests/test_manual_narrative_review.py tests/test_real_chapter_narrative_review.py tests/test_codex_manual_preview.py tests/test_narrative_identity.py tests/test_narrative_pipeline.py tests/test_narrative_qc.py tests/test_narrative_review.py tests/test_vision_synthesis.py -q
  python -m pytest -m "not slow" -q
  ```

  Expected: focused and complete non-slow suites are green or have only the already documented existing skip; the worktree is clean before runtime data is created.

- [ ] **Step 2: Prepare and verify the ordered ledger.**

  ```powershell
  python scripts/review/run_real_chapter_narrative_review.py prepare `
    --manifest data/panel-review-9c1-20260809/manifest.json `
    --output data/real-chapter-narrative-review-20260813
  ```

  Expected: exactly 24 verified records, order `0..23`, title exclusion recorded, story scope `1..23`, deterministic ledger hash, and no provider/media output. The executor records the exact manifest path and output path in the local handoff, never in committed source.

- [ ] **Step 3: Examine every panel in order and author sanitized observations.**

  Open the exact local files recorded by `source_ledger.json` in ascending order. The concrete input is `data/panel-review-9c1-20260809/manifest.json`; the executor resolves each entry's `review_path` against that manifest directory and prints the ordered absolute paths, dimensions, and SHA-256 values before visual inspection:

  ```powershell
  $manifest = Get-Content data/panel-review-9c1-20260809/manifest.json -Raw | ConvertFrom-Json
  $root = (Resolve-Path data/panel-review-9c1-20260809).Path
  $manifest.assets | Sort-Object source_order | ForEach-Object {
      $panel = Join-Path $root $_.review_path
      $hash = (Get-FileHash -LiteralPath $panel -Algorithm SHA256).Hash.ToLowerInvariant()
      if ($hash -ne $_.checksum) { throw "review.source_checksum_mismatch:$($_.source_order)" }
      [pscustomobject]@{ order = $_.source_order; path = $panel; width = $_.width; height = $_.height; sha256 = $hash }
  } | Format-Table -AutoSize
  ```

  The visual-review agent then opens each printed absolute image path, in order, one at a time; contact sheets may support orientation but never replace the 24 individual inspections. For each order `0..23`, write one observation record; for order `0`, record only title/front-matter exclusion. For orders `1..23`, record visible action/layout, supported descriptors, continuity, uncertainty, and dialogue presence/paraphrase without copying speech-balloon text. Preserve exact `source_asset_id`, `panel_id`, and `source_order` from the ledger.

- [ ] **Step 4: Reconcile the causal chapter map before prose.**

  Group ordered observations into chapter beats, record causal transitions and changed stakes, and reference exact story orders on every beat/edge. Mark interpretations with qualifications. Verify that the union of beat references is exactly `1..23` before drafting narration.

- [ ] **Step 5: Draft and QC 4–6 Sharp Friend passages.**

  Draft punctuation-bearing English spoken text from the reviewed map. Use the explicit `sharp_friend_v1` profile, varied sentence lengths, contractions, causal connectors, selective commentary, and a chapter-specific ending kind. Keep the initial target near 90–125 words but do not pad mechanically. Attach claim IDs and evidence refs to every passage. Run `finalize`; fix only the reviewed narrative input when QC identifies a real contract failure. Never make the validator rewrite text or invent evidence.

- [ ] **Step 6: Review the independently derived display surface.**

  Confirm `display_cues.json` contains one uppercase punctuation-free Unicode-alphanumeric word per nonempty spoken token and that the exact contents of `narration_spoken.txt` are unchanged. Do not create timings, subtitle files, audio, or video.

- [ ] **Step 7: Obtain explicit human reference-only decision.**

  Present `qc_report.json`, the sanitized narrative text, source ledger hash, and the ordered panel-review evidence to the human editor. If accepted, record `APPROVED_REFERENCE_ONLY` with reviewer label, timestamp, revision hash, `production_evidence=false`, and `publish_allowed=false`. If rejected, record `REJECTED` with a reason. If edited, create a new `REVISED` bundle and rerun QC; never retain approval across revisions.

- [ ] **Step 8: Update documentation only with truthful evidence.**

  Update `docs/STATUS.md` and `CHANGELOG.md` with exact code commit(s), software test counts, bundle path/hash, 24-order ledger coverage, title exclusion, QC state, approval state, provenance label, rights limitation, and explicit no-provider/no-voice/no-media status. Do not commit the bundle, images, contact sheets, or media.

- [ ] **Step 9: Commit and publish the code/docs handoff only.**

  ```powershell
  git add app/services/manual_narrative_review.py scripts/review/run_real_chapter_narrative_review.py tests/test_manual_narrative_review.py tests/test_real_chapter_narrative_review.py docs/STATUS.md CHANGELOG.md
  git diff --cached --check
  git commit -m "feat: complete manual chapter narrative review boundary"
  git push origin main:main
  git status --short --branch
  ```

  Expected: only the allowed source/test/docs paths are committed; runtime data remains ignored; no provider, database, media, or publication action occurs. The final main-only push policy is fast-forward/no-force/no-tags/no-`--all`, with the plan branch retained if the task began there.

---

## Verification matrix and handoff gates

After every green code task, run its focused test file and `git diff --check`. Before Task 6 opens the real panels, run:

```powershell
python -m pytest tests/test_manual_narrative_review.py tests/test_real_chapter_narrative_review.py tests/test_codex_manual_preview.py -q
python -m pytest tests/test_narrative_identity.py tests/test_narrative_pipeline.py tests/test_narrative_qc.py tests/test_narrative_review.py tests/test_vision_synthesis.py tests/test_analyzer_contract.py -q
python -m pytest -m "not slow" -q
python -m ruff check app/services/manual_narrative_review.py scripts/review/run_real_chapter_narrative_review.py tests/test_manual_narrative_review.py tests/test_real_chapter_narrative_review.py
python -m compileall -q app scripts/review
git diff --check
git status --short --untracked-files=all
```

The expected full-suite baseline from STATUS is 825 collected, 824 passed, 1 documented existing Task9C1 real-panel skip, 0 failed; the executor must report actual current counts, not copy this historical value if it changes. Any full-suite failure caused by the new slice must be fixed before the manual run. Environment-invalid optional coverage is reported unavailable with the exact reason, never replaced with a fabricated percentage.

Secret scope review must scan only staged diffs for key-shaped values, private keys, bearer tokens, `.env` content, absolute runtime paths, and media/data additions. A runtime artifact accidentally visible to Git must be removed from the staged set and added to the existing ignore policy only in a separate explicitly authorized change; do not silently broaden this plan.

## Acceptance matrix

| Requirement | Deliverable | Gate |
|---|---|---|
| Every panel examined | `source_ledger.json`, 24 observations | exact `0..23` once, story `1..23` once |
| Title handling | order `0` exclusion | nonempty `title_front_matter`, no story claim |
| Manual provenance | bundle metadata | exact manual label, production flags false |
| Lineage/checksums | ledger helper | current bytes/dimensions/hash match |
| Causal chapter understanding | `chapter_map.json` | all beats/edges evidence-linked and qualified |
| Sharp Friend narration | `narrative_review.json` | 4–6 passages, v3 evidence/ending/hard screens |
| Spoken/display split | `.txt` plus `display_cues.json` | spoken unchanged, one uppercase word per cue |
| Naturalness/QC | `qc_report.json` | deterministic metrics, blockers vs warnings |
| Human lifecycle | revision state | explicit reference-only approval/rejection/revision |
| Artifact policy | ignored local root | no source images/media/DB/secrets in Git |
| Production safety | no production calls/rows | no provider, ORM, voice, render, publication |

## Rollback and handoff

Each task is independently reversible by its commit; runtime bundle directories are disposable local review data and are removed only by the human’s explicit cleanup decision. The implementation starts from `9eecb6646a2a5a103a13681fd6c649a8af2a1716`; a fresh agent resumes from the latest green task commit and reruns that task’s focused command before proceeding. The code/doc final push must be fast-forward only, main-only, no force, no tags, no `--all`, and must leave local `main` clean and equal to `origin/main`.

## Plan self-review

- [ ] The approved spec’s ledger, title exclusion, manual provenance, safe bundle, observation, causal map, Sharp Friend passage, spoken/display, QC, lifecycle, artifact, no-go, and handoff requirements each map to a task or global constraint.
- [ ] All implementation names used later are defined earlier: `ManualReviewLedger`, `SourceLedgerEntry`, `ManualReviewError`, `derive_display_cues`, `write_review_bundle`, `read_review_bundle`, `validate_panel_observations`, `validate_chapter_map`, `build_manual_analyzer_projection`, `validate_manual_narrative`, `ReviewQCReport`, and lifecycle functions.
- [ ] All examples use current Python/path/test conventions and do not invoke providers, TTS, FFmpeg, ORM, migrations, or media.
- [ ] No task treats manual records as production visual evidence or fabricates human approval.
- [ ] No placeholder markers, vague validation instructions, guessed source paths, or unresolved product choices remain.
- [ ] Runtime artifact policy preserves `data/` ignore behavior and commits only source/tests/docs.
