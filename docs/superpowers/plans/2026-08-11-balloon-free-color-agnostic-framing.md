# Balloon-Free Color-Agnostic Framing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. The named superpowers sub-skills are not installed in this environment; the active equivalent is Sol review plus Luna task execution. Execute one checked task at a time and stop at the stated review boundary.

**Goal:** Add deterministic color-agnostic, balloon-free reference framing that preserves protected visual evidence, uses an auditable fallback chain, and leaves legacy profile=None behavior unchanged.

**Architecture:** Keep visual_scoring.py as the typed panel-region evidence boundary and add the focused app/services/framing_analysis.py module for deterministic border masks and later candidate feasibility. Extend render.py prepare_reference_frame to consume that module, then let the existing editorial_visual_planner.py and editorial_qc.py enforce fallback and reference gates. Persist evidence as a versioned JSON sidecar inside PanelRegion.observation_json; do not add a migration unless a focused test proves the current JSON boundary cannot represent the required data.

**Tech Stack:** Python 3.11, SQLAlchemy ORM, Pydantic, Pillow, existing OpenCV/Tesseract optional signals, FFmpeg from /home/yusronrohmani/.local/bin, pytest, Ruff, Alembic only if a schema need is proven.

## Global Constraints

The following requirements are copied from the approved design and apply to every task:

- "speech balloon text must never appear in reference-mode output; target balloon-mask intersection ratio exactly 0"
- "do not use brightness/white as blank definition"
- "identify edge-connected candidate blank/padding by flood fill from all four borders"
- "distinguish border padding from meaningful internal art/background"
- "protect actual subject, face, action, effect, and continuity-critical context with typed regions and minimum coverage thresholds"
- "close-up is preferred over visible blank padding"
- "dynamic crop ceiling is quality/subject constrained, not blindly fixed at 1.35"
- "visible edge-connected blank target 0; if infeasible, minimize deterministically and record telemetry/fallback reason"
- "balloon intersection remains a hard zero: never silently allow it"
- "exact fallback: alternate ROI same panel -> tighter quality-safe crop -> different panel from same story beat -> stable visual_unavailable rejection"
- "missing/unreliable balloon geometry cannot silently pass"
- "unknown is an explicit persisted state, not an empty-mask inference; only
  reference readiness rejects unknown geometry"
- "preserve monotonic smooth motion and forbidden-shake rules"
- "no speech_bubble ROI in output selection/motion planning"
- "full-panel evidence, coverage, provenance, and no random sampling remain mandatory"
- "spoken_text punctuation-bearing and display_text separately derived punctuation-free uppercase one-word cues"
- "human editorial approval remains mandatory"
- "voice generation is explicitly deferred until the user chooses local or API execution"
- "publish_allowed remains false until rights are verified"
- "all source, tests, and renders run on the VPS; Windows is exact-history transport and push only"
- "no media/DB/credentials/runtime data enters Git"
- "no force push, tags, all branches, or unrelated remote writes"

Baseline and authority:

- Authoritative checkout: /home/yusronrohmani/manhwashorts through SSH alias google.
- Baseline for this implementation slice: clean main at
  940ab42d135626cfb096c3b3b3e7957d549e3923, the pushed Task 2 hardening
  commit. The historical planning baseline remains recorded in the prior
  checkpoint.
- Historical checkpoint: 635 passed in the full non-slow suite at f9221dd; it is evidence only and is not a fresh result for this planning commit.
- Every PowerShell SSH command in this plan ends with 2>&1.
- Current profile=None crop_to_vertical, legacy editorial_frame compositions, legacy build_ass, and preview behavior are compatibility surfaces. Tests must prove no reference change leaks into them.

## Current symbol map

These are the real baseline symbols inspected before writing this plan:

- app/services/reference_profile.py defines frozen ReferenceProfileConfig, REFERENCE_MATCHED_SHORTS_V1, canonical_profile_json, profile_hash, and resolve_reference_profile. The current reference values include base_frame_zoom_max=1.35 and max_blank_fraction=0.18.
- app/services/render.py defines crop_to_vertical(src, dest, width, height, focus_x, focus_y), frozen PreparedFrame(path, crop_box, blank_fraction, base_zoom), reference_frame_cache_key(..., profile), prepare_reference_frame(..., profile), editorial_frame(..., profile=None), and build_ass(..., profile=None).
- prepare_reference_frame currently searches 0.02 scale increments and scores _reference_content_stats. That helper uses a near-white RGB threshold; it is not a hard blank gate.
- app/services/visual_scoring.py defines PanelScoreWeights, VisualFeatures, PanelCandidate, analyze_panel(data, asset_id="", order_index=0, source_family=""), analyze_assets, selection_reasons, and plan_content_aware_scenes. Its current speech_balloon_dominance and blank_dominance values are heuristics, not masks.
- app/services/framing_analysis.py does not yet exist. Task 3 creates it as
  the sole owner of color-agnostic border-mask metrics and Task 4 extends the
  same focused module with candidate feasibility; it imports Task 1 visual
  evidence types and does not re-export or duplicate their validators.
- app/services/editorial_visual_planner.py defines plan(spans, candidates, profile=None, cited_asset_ids_by_section=None, citation_alignment_reasons_by_section=None), the reference _plan_reference path, ReferencePlanningError, and _reference_roi_key.
- app/services/editorial_qc.py defines build_report(..., profile=None); app/services/quality.py defines check_reference_profile, check_repetition_and_motion, check_subtitles, and profile-aware CheckResult values.
- app/services/pipeline.py defines run_analysis(db, project_id, actor_id=""), generate_script, build_timeline, and current evidence-to-asset mapping through _reference_citation_map. PanelRegion stores bounds, segmentation metadata, observation_json, chunk_index, evidence_refs_json, and coverage_map_hash.
- app/services/vision_adapter.py defines VisionObservationRequest, VisionChapterSynthesisRequest, VisionObservationProvider, VisionRequestInvalid, VisionResponseInvalid, and OpenAICompatibleVisionProvider.observe/synthesize. Its provider request must remain structured and fail-closed.
- The current vision adapter _build_payload asks for semantic observation keys
  but does not request nested visual geometry. Task 2 is the bounded extension
  point: it adds the versioned visual prompt/hash and validates nested
  visual_evidence without changing the v2 top-level key set.
- app/models.py defines StoryAnalysis JSON fields and PanelRegion.observation_json/evidence_refs_json; no balloon-specific database column exists.
- There is no root AGENTS.md in the repository. The only AGENTS.md found is outside this repository under the adjacent OmniVoice project, so no agent handoff file is part of this plan.

## Architecture and dependency graph

    SourceAsset + PanelRegion lineage
      -> run_analysis complete coverage
      -> balloon_free_visual_evidence_v1 prompt
      -> VisionObservationRequest/OpenAICompatibleVisionProvider.observe
      -> nested visual_evidence validation and PanelRegion persistence
      -> require_reference_ready_visual_evidence
      -> framing_analysis.build_color_agnostic_border_mask
      -> render.prepare_reference_frame static candidate window
      -> editorial_visual_planner.plan reference fallback
      -> TimelineScene evidence/alignment telemetry
      -> editorial_qc.build_report and quality checks
      -> stable monotonic FFmpeg motion
      -> review-only output with rights gate

Task dependencies:

- Task 1 establishes typed evidence and persistence shape; unknown is persistable.
- Task 2 acquires that geometry from every ordered vision observation.
- Task 3 consumes the acquired evidence and produces the color-agnostic border mask in framing_analysis.py.
- Task 4 extends framing_analysis.py with candidate feasibility and consumes both records and mask telemetry to produce feasible static frames.
- Task 5 consumes frame feasibility and planner citations to enforce fallback and QC.
- Task 6 consumes all prior interfaces and proves the isolated real-panel review path.

Interfaces between tasks:

- Task 1 produces PanelVisualEvidence, BalloonRegionEvidence,
  ProtectedRegionEvidence, VisualEvidenceError, parse_panel_visual_evidence,
  validate_panel_visual_evidence, require_reference_ready_visual_evidence,
  and panel_visual_evidence_json.
- Task 2 produces the versioned visual-evidence prompt/hash and the adapter
  observation contract consumed by run_analysis. It also updates the caller
  so visual mode is explicitly requested; adapter compatibility alone is not
  sufficient to activate acquisition.
- Task 3 produces framing_analysis.BorderMaskResult and framing_analysis.build_color_agnostic_border_mask.
- Task 4 produces framing_analysis.FramingTelemetry and PreparedFrame.telemetry, while retaining
  the existing PreparedFrame.path/crop_box/blank_fraction/base_zoom fields.
- Task 5 consumes evidence_by_asset and produces stable planner/QC findings.
- Task 6 consumes all prior sidecars and produces only isolated review artifacts;
  it does not produce audio or publication state.

## Task 1: Persist typed visual region evidence

**Files:**
- Modify: app/services/visual_scoring.py at the existing dataclass and feature-extraction boundary.
- Modify: app/services/pipeline.py at run_analysis observation persistence.
- Create: tests/test_balloon_evidence.py.
- Modify: docs/STATUS.md.
- Modify: CHANGELOG.md.

**Interfaces:**
- **Consumes:** existing PanelRegion.observation_json, source_order, panel_id, source_asset_id, coverage_map_hash, and the full-panel analyzer output.
- **Produces:** immutable typed records and JSON functions used by Tasks 2-5.

The sidecar is stored under observation_json["visual_evidence"]. It does not
change the analyzer's required observation keys and does not turn OCR text into
visual geometry.

- [ ] **Step 1: Write body-failing tests for the typed records and mask states**

Use runtime attribute probes so collection succeeds on the current baseline:

    from importlib import import_module

    import pytest

    visual_scoring = import_module("app.services.visual_scoring")

    def test_typed_visual_evidence_boundary_exists():
        assert getattr(visual_scoring, "BalloonRegionEvidence", None) is not None
        assert getattr(visual_scoring, "ProtectedRegionEvidence", None) is not None
        assert getattr(visual_scoring, "PanelVisualEvidence", None) is not None
        assert getattr(visual_scoring, "parse_panel_visual_evidence", None) is not None

    def test_unknown_balloon_mask_persists_but_blocks_reference_consumption():
        evidence_type = getattr(visual_scoring, "PanelVisualEvidence", None)
        validate = getattr(visual_scoring, "validate_panel_visual_evidence", None)
        require_ready = getattr(visual_scoring, "require_reference_ready_visual_evidence", None)
        assert evidence_type is not None and validate is not None and require_ready is not None
        evidence = evidence_type(
            contract_version="COLOR_AGNOSTIC_BALLOON_FREE_V1",
            panel_id="panel-1",
            source_asset_id="asset-1",
            source_order=1,
            balloon_regions=(),
            protected_regions=(),
            balloon_mask_status="unknown",
            mask_confidence=0.0,
            evidence_source="vision",
            mask_reason="provider could not determine geometry",
            evidence_hash="",
        )
        validate(evidence)
        serialized = visual_scoring.panel_visual_evidence_json(evidence)
        assert serialized["balloon_mask_status"] == "unknown"
        with pytest.raises(Exception) as caught:
            require_ready(evidence)
        assert "visual.balloon_mask_unknown" in str(caught.value)

    def test_known_empty_requires_affirmative_provenance():
        evidence_type = getattr(visual_scoring, "PanelVisualEvidence", None)
        validate = getattr(visual_scoring, "validate_panel_visual_evidence", None)
        assert evidence_type is not None and validate is not None
        evidence = evidence_type(
            contract_version="COLOR_AGNOSTIC_BALLOON_FREE_V1",
            panel_id="panel-1",
            source_asset_id="asset-1",
            source_order=1,
            balloon_regions=(),
            protected_regions=(),
            balloon_mask_status="known_empty",
            mask_confidence=0.0,
            evidence_source="",
            mask_reason="",
            evidence_hash="",
        )
        with pytest.raises(Exception) as caught:
            validate(evidence)
        assert "visual.balloon_mask_empty_unproven" in str(caught.value)

    def test_known_balloon_geometry_requires_normalized_geometry_and_confidence():
        parse = getattr(visual_scoring, "parse_panel_visual_evidence", None)
        assert parse is not None
        with pytest.raises(Exception) as caught:
            parse({
                "contract_version": "COLOR_AGNOSTIC_BALLOON_FREE_V1",
                "panel_id": "panel-1",
                "source_asset_id": "asset-1",
                "source_order": 1,
                "balloon_mask_status": "known_nonempty",
                "mask_confidence": 0.9,
                "evidence_source": "vision",
                "balloon_regions": [
                    {"region_id": "balloon-1", "kind": "speech_balloon",
                     "confidence": 0.9, "evidence_source": "vision",
                     "mask_status": "known_nonempty"}
                ],
                "protected_regions": [],
            })
        assert "visual.balloon_geometry_invalid" in str(caught.value)

    def test_visual_evidence_round_trips_without_mutating_analyzer_observation():
        parse = getattr(visual_scoring, "parse_panel_visual_evidence", None)
        to_json = getattr(visual_scoring, "panel_visual_evidence_json", None)
        assert parse is not None and to_json is not None
        raw = {
            "contract_version": "COLOR_AGNOSTIC_BALLOON_FREE_V1",
            "panel_id": "panel-1",
            "source_asset_id": "asset-1",
            "source_order": 1,
            "balloon_mask_status": "known_empty",
            "mask_confidence": 1.0,
            "evidence_source": "vision",
            "mask_reason": "vision adapter confirmed no balloon geometry",
            "balloon_regions": [],
            "protected_regions": [],
        }
        original = dict(raw)
        parsed = parse(raw)
        assert to_json(parsed)["panel_id"] == "panel-1"
        assert raw == original

Expected RED command:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_balloon_evidence.py -q

Expected RED: the test module collects, then the four test bodies fail because
the typed constructors/validator are absent on the baseline. An import/setup
error is not an acceptable RED result.

- [ ] **Step 2: Implement the smallest typed evidence boundary**

Add these exact records and validation boundary to app/services/visual_scoring.py.
The file already imports dataclass, Mapping, Image, and the feature types:

    NormalizedBBox = tuple[float, float, float, float]
    NormalizedPolygon = tuple[tuple[float, float], ...]

    class VisualEvidenceError(ValueError):
        code = "visual.evidence_invalid"

        def __init__(self, code: str, message: str) -> None:
            self.code = code
            super().__init__(f"{code}: {message}")

    @dataclass(frozen=True)
    class BalloonRegionEvidence:
        region_id: str
        kind: str
        normalized_bbox: NormalizedBBox | None
        normalized_polygon: NormalizedPolygon
        confidence: float
        evidence_source: str
        mask_status: str

    @dataclass(frozen=True)
    class ProtectedRegionEvidence:
        region_id: str
        kind: str
        normalized_bbox: NormalizedBBox | None
        normalized_polygon: NormalizedPolygon
        confidence: float
        evidence_source: str
        required: bool
        minimum_coverage: float

    @dataclass(frozen=True)
    class PanelVisualEvidence:
        contract_version: str
        panel_id: str
        source_asset_id: str
        source_order: int
        balloon_regions: tuple[BalloonRegionEvidence, ...]
        protected_regions: tuple[ProtectedRegionEvidence, ...]
        balloon_mask_status: str
        mask_confidence: float
        evidence_source: str
        mask_reason: str
        evidence_hash: str

    def validate_panel_visual_evidence(evidence: PanelVisualEvidence) -> None:
        if evidence.contract_version != "COLOR_AGNOSTIC_BALLOON_FREE_V1":
            raise VisualEvidenceError("visual.evidence_version_invalid", "unsupported version")
        if evidence.balloon_mask_status not in {"unknown", "known_empty", "known_nonempty"}:
            raise VisualEvidenceError("visual.balloon_mask_status_invalid", "unsupported mask state")
        if not evidence.panel_id or not evidence.source_asset_id or evidence.source_order < 0:
            raise VisualEvidenceError("visual.evidence_lineage_invalid", "lineage is incomplete")
        if not 0.0 <= evidence.mask_confidence <= 1.0:
            raise VisualEvidenceError("visual.evidence_confidence_invalid", "confidence is outside 0..1")
        if evidence.balloon_mask_status == "unknown" and not evidence.mask_reason:
            raise VisualEvidenceError("visual.balloon_mask_reason_missing", "unknown geometry needs a reason")
        if evidence.balloon_mask_status == "known_empty" and (
            evidence.mask_confidence <= 0.0 or not evidence.evidence_source or not evidence.mask_reason
        ):
            raise VisualEvidenceError("visual.balloon_mask_empty_unproven", "known_empty requires affirmative evidence")
        for region in evidence.balloon_regions:
            if region.mask_status == "known_nonempty" and not region.normalized_polygon and region.normalized_bbox is None:
                raise VisualEvidenceError("visual.balloon_geometry_invalid", "known geometry is empty")
            if region.mask_status not in {"unknown", "known_nonempty"}:
                raise VisualEvidenceError("visual.balloon_mask_status_invalid", "unsupported region state")

    def require_reference_ready_visual_evidence(
        evidence: PanelVisualEvidence,
    ) -> None:
        validate_panel_visual_evidence(evidence)
        if evidence.balloon_mask_status == "unknown":
            raise VisualEvidenceError("visual.balloon_mask_unknown", "reference geometry is unavailable")
        if evidence.balloon_mask_status == "known_nonempty":
            for region in evidence.balloon_regions:
                if region.mask_status == "unknown":
                    raise VisualEvidenceError("visual.balloon_mask_unknown", "reference region geometry is unavailable")

    def unknown_visual_evidence(
        *, panel_id: str, source_asset_id: str, source_order: int,
        reason: str, evidence_source: str,
    ) -> PanelVisualEvidence:
        return PanelVisualEvidence(
            contract_version="COLOR_AGNOSTIC_BALLOON_FREE_V1",
            panel_id=panel_id,
            source_asset_id=source_asset_id,
            source_order=source_order,
            balloon_regions=(),
            protected_regions=(),
            balloon_mask_status="unknown",
            mask_confidence=0.0,
            evidence_source=evidence_source,
            mask_reason=reason,
            evidence_hash="",
        )

    When the observation provider has no reliable geometry, the persistence
    boundary calls unknown_visual_evidence with the real panel lineage and a
    nonempty reason. It never calls a known_empty constructor as a default.
    parse_panel_visual_evidence converts JSON lists to tuples, checks every
    normalized coordinate is in 0..1, checks polygon bounds, checks confidence
    and allowed enum strings, and calls the structural validator. The structural
    validator accepts unknown and preserves it; require_reference_ready_visual_evidence
    is the separate reference-mode feasibility gate. panel_visual_evidence_json
    emits compact JSON-compatible dictionaries without dropping evidence_source,
    mask_reason, or mask_status, including unknown. known_empty is valid only with affirmative
    provider/adapter provenance and positive confidence; an empty region list by
    itself never proves known_empty. It hashes canonical sorted-key JSON after
    lineage and geometry validation. No function reads provider secrets or source paths.

- [ ] **Step 3: Persist the sidecar at the existing pipeline boundary**

At the current run_analysis loop that creates or updates each PanelRegion,
keep the analyzer observation unchanged and merge only the validated sidecar:

    analyzer_observation = dict(region.observation_json or {})
    visual_evidence = visual_scoring.parse_panel_visual_evidence(
        analyzer_observation.pop("visual_evidence")
    )
    region.observation_json = {
        **analyzer_observation,
        "visual_evidence": visual_scoring.panel_visual_evidence_json(visual_evidence),
    }

The implementation must take the sidecar from the vision observation/evidence
record, not invent it from a filename or list position. If the provider cannot
obtain geometry, persist an explicit unknown record tied to the real panel
lineage; do not convert it to known_empty and do not block non-reference legacy
analysis. VisionObservationRequest and VisionChapterSynthesisRequest remain
compatible because the sidecar is nested and the existing adapter preserves
complete ordered mappings. Reference planning/QC must call
require_reference_ready_visual_evidence and block with visual.balloon_mask_unknown
when it consumes unknown evidence. Add tests that the adapter's required
top-level observation keys and panel order remain unchanged.

- [ ] **Step 4: Run focused regressions and lint**

Run:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_balloon_evidence.py tests/test_vision_adapter.py tests/test_vision_synthesis.py tests/test_vision_pipeline.py -q
    .venv/bin/ruff check app/services/visual_scoring.py app/services/pipeline.py tests/test_balloon_evidence.py
    .venv/bin/python -m compileall -q app
    git diff --check

Expected GREEN: all new typed-evidence tests and existing adapter/pipeline
tests pass. The full non-slow suite is also required before the commit.

- [ ] **Step 5: Update docs, commit, and push the green slice**

Update docs/STATUS.md with the exact tests, coverage of known_empty versus
unknown, the new evidence hash, commit SHA, clean Git state, and Task 2 geometry
acquisition as
the next atomic task. Add the same concise milestone to CHANGELOG.md.

Stage only the five owned paths, verify git diff --cached --name-only, and
commit with:

    git add -- app/services/visual_scoring.py app/services/pipeline.py tests/test_balloon_evidence.py docs/STATUS.md CHANGELOG.md
    git diff --cached --check
    git commit -m "feat: persist typed balloon visual evidence"

Run the full non-slow command before commit:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/python -m pytest -q -m "not slow"

Push proof is performed immediately after the green commit through the
Windows exact-history clone: record the VPS HEAD, create a Git bundle from the
current VPS tracking base, SFTP it, verify the local HTTPS main SHA is the
recorded parent, fetch the bundle into a clean clone, merge --ff-only, run
git push origin main:main, then verify
git ls-remote https://github.com/yxxrn/manhwashorts-studio.git refs/heads/main
equals the new commit. Never force-push. Rollback is the new commit SHA.

## Task 2: Acquire versioned balloon and protected-region geometry during observation

**Files:**
- Modify: app/services/vision_adapter.py at VisionObservationRequest, _build_payload, and _validate_observations.
- Modify: app/services/pipeline.py at _observe_chunks and the observation
  reconciliation/persistence boundary.
- Modify: app/services/visual_scoring.py at the Task 1 visual contract loader/validator.
- Create: app/prompts/balloon_free_visual_evidence_v1.txt.
- Modify: tests/mock_provider.py, tests/test_vision_adapter.py, and
  tests/test_vision_pipeline.py.
- Create: tests/fixtures/visual_evidence_prompt_snapshot.sha256.
- Modify: docs/STATUS.md.
- Modify: CHANGELOG.md.

**Interfaces:**

    VISUAL_EVIDENCE_PROMPT_VERSION = "balloon-free-visual-evidence-v1"

    def load_visual_evidence_instruction() -> tuple[str, str, str]:
        """Return committed version, SHA-256, and normalized LF prompt."""

    @dataclass(frozen=True)
    class VisionObservationRequest:
        analysis_run_id: str
        instruction_version: str
        instruction_sha256: str
        chunk_index: int
        panels: tuple[Mapping[str, Any], ...]
        visual_instruction_version: str | None = None
        visual_instruction_sha256: str | None = None

    def validate_visual_evidence_observation(
        observation: Mapping[str, Any],
        *,
        expected_panel_id: str,
        expected_source_asset_id: str,
        expected_source_order: int,
    ) -> Mapping[str, Any]:
        """Validate provider geometry and return locally normalized evidence."""

    def _observe_chunks(
        provider: VisionObservationProvider,
        chunks: Sequence[Sequence[PanelRegion]],
        panel_transports: Mapping[str, Mapping[str, Any]],
        *,
        analysis_run_id: str,
        instruction_version: str,
        instruction_sha256: str,
        visual_instruction_version: str,
        visual_instruction_sha256: str,
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
        """Production analysis opts into visual mode for every ordered chunk."""
        pass

- [ ] Preserve the existing five top-level analyzer observation keys and add only a nested visual_evidence mapping in the visual-observation mode. A v2 request without visual_instruction_version remains byte/behavior compatible.
- [ ] Require visual_evidence in every ordered panel observation when the visual contract is requested. Its lineage must equal the request panel_id, source_asset_id, and source_order; no filename or list position may substitute for lineage.
- [ ] Preserve unknown as a valid provider result. The provider nested mapping
  includes balloon_mask_status, balloon_regions, protected_regions,
  mask_confidence, evidence_source, mask_reason, panel_id, source_asset_id, and
  source_order. It does not request or require evidence_hash. After structural
  validation, the local Task 1 serializer computes and attaches evidence_hash.
  A provider-supplied nonempty evidence_hash is rejected as an unexpected
  provider field; it is never trusted as proof.
- [ ] Keep the provider schema separate from the persisted schema: the adapter
  validates exact legacy analyzer keys plus visual_evidence in visual mode, and
  the pipeline calls the local serializer before writing PanelRegion JSON.
  Legacy adapter callers with both visual instruction fields absent retain the
  old exact key set and byte/behavior compatibility.
- [ ] Add app/services/pipeline.py and tests/test_vision_pipeline.py to this
  task. The pipeline must load the committed visual instruction version/hash,
  populate both request fields for every chunk, require exactly one nested
  visual_evidence mapping per ordered panel, preserve the old top-level keys
  and panel order, and fail closed on missing, foreign, or malformed sidecars.
- [ ] Accept balloon_mask_status unknown with empty geometry only when evidence_source states an unavailable/insufficient geometry result. Accept known_empty only when the provider affirmatively reports reliable empty geometry with nonzero confidence and provenance. Accept known_nonempty only with valid normalized bbox or polygon geometry.
- [ ] Reject malformed claimed-known geometry, out-of-range coordinates, duplicate region IDs, blank provenance, confidence outside 0..1, lineage mismatch, and a text-only OCR result presented as known geometry. OCR boxes may be included as optional evidence_source metadata but OCR text alone never upgrades unknown.
- [ ] Keep visual evidence acquisition separate from the later sharp_friend narrative prompt. The v3 narrative plan consumes the persisted visual_evidence sidecar and does not rename, own, or replace this contract.

**RED:**

- [ ] Add a valid three-panel multimodal mock response with visual_evidence for each panel, including one known_nonempty panel, one affirmative known_empty panel, and one explicit unknown panel. Assert ordered request metadata, exact panel lineage, preservation of nested records, and absence of provider evidence_hash.
- [ ] Add a provider request assertion that the visual instruction version/hash are present and that the payload explicitly asks for balloon regions, protected subject/face/action/effect regions, normalized geometry, mask status, confidence, provenance, and lineage.
- [ ] Add invalid response cases for missing visual_evidence, foreign panel lineage, malformed known_nonempty bbox/polygon, known_empty without affirmative confidence/provenance, duplicate region IDs, and OCR-only geometry.
- [ ] Add a snapshot assertion for the normalized prompt SHA-256 and ensure the mock provider captures the prompt without secrets. Do not use a live network call.
- [ ] Add pipeline regressions proving every _observe_chunks request carries the
  committed visual instruction version/hash, every response contains exactly
  one lineage-matching visual_evidence mapping, old analyzer keys remain
  present and ordered, and locally serialized evidence_hash is deterministic.
  Include missing-sidecar, foreign-lineage, malformed-sidecar, and provider
  nonempty-hash rejection cases. A legacy adapter-only request with both visual
  fields omitted must continue to pass its existing v2 tests.
- [ ] Run:

      PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_vision_adapter.py tests/test_balloon_evidence.py tests/test_vision_pipeline.py -q

  Expected RED: the current adapter has no visual prompt fields, the current
  pipeline does not request visual mode, and the mock observations have no
  nested visual evidence. Existing v2 observe/synthesis cases must remain
  collection-clean.

**Implementation:**

- [ ] Add load_visual_evidence_instruction beside the Task 1 canonical serializer. Read app/prompts/balloon_free_visual_evidence_v1.txt, normalize CRLF to LF with one trailing LF, and hash UTF-8 bytes. The snapshot is generated from this normalized content, never guessed. The prompt must not contain evidence_hash as a provider output requirement.
- [ ] Add the two defaulted request fields shown above. When either visual field is supplied, require the exact committed version/hash pair before any HTTP call; when both are absent, keep the existing v2 payload path.
- [ ] Extend _build_payload to add the exact visual prompt as a separate structured instruction before images. The prompt must say:

    Observe every supplied panel in source order before any story writing.
    Return one observation for every requested panel.
    Include visual_evidence with balloon_mask_status, balloon_regions,
    protected_regions, mask_confidence, evidence_source, mask_reason,
    panel_id, source_asset_id, and source_order.
    Classify geometry as unknown when the provider cannot reliably determine it.
    Never infer known_empty from an empty list and never use OCR text alone
    as geometry. Do not omit, sample, randomize, or use filenames as evidence.

- [ ] Extend _validate_observations with a require_visual_evidence flag. Validate the existing top-level key set first, then call validate_visual_evidence_observation for each row when the flag is true. Return the nested mapping unchanged except for deterministic tuple/list normalization required by existing JSON persistence.
- [ ] Define the provider visual key set without evidence_hash:

      _PROVIDER_VISUAL_KEYS = frozenset({
          "balloon_mask_status", "balloon_regions", "protected_regions",
          "mask_confidence", "evidence_source", "mask_reason",
          "panel_id", "source_asset_id", "source_order",
      })

  Require `set(visual_evidence) == _PROVIDER_VISUAL_KEYS` in visual mode.
  If a provider sends a nonempty evidence_hash, fail with the stable response
  error rather than accepting it. Construct the Task 1 record with an empty
  hash, call `validate_panel_visual_evidence`, and persist the result from
  `panel_visual_evidence_json`, which computes the local SHA-256.
- [ ] Update _observe_chunks and its call site in pipeline.py exactly as
  follows:

      visual_version, visual_sha256, _ = (
          visual_scoring.load_visual_evidence_instruction()
      )
      request = VisionObservationRequest(
          analysis_run_id=analysis_run_id,
          instruction_version=instruction_version,
          instruction_sha256=instruction_sha256,
          chunk_index=chunk_index,
          panels=tuple(panel_transports[panel_id] for panel_id in panel_ids),
          visual_instruction_version=visual_version,
          visual_instruction_sha256=visual_sha256,
      )

  Call `_validate_observation_rows(response, panel_ids, require_visual_evidence=True)`
  in the production path. Reconcile the returned rows in the same source order,
  then call `ensure_panel_visual_evidence`/`panel_visual_evidence_json` with
  the real panel ID, source asset ID, and source order before assigning
  `PanelRegion.observation_json`. The pipeline must never call the reference
  readiness gate here; unknown remains parseable until a later reference
  consumer.
- [ ] Call the Task 1 structural validator for nested geometry, not require_reference_ready_visual_evidence. This keeps unknown parseable and lets reference crop/planner/QC be the only consumer gate.
- [ ] Update tests/mock_provider.py so its multimodal response has deterministic visual_evidence by panel ID and preserves existing BYOK/TTS behavior. The mock must never fabricate known_empty for an unknown case.
- [ ] Keep vision adapter exceptions safe and machine-readable: malformed geometry is VisionResponseInvalid with a stable detail code such as visual.balloon_geometry_invalid; no prompt, image payload, API key, or raw provider response is included.
- [ ] Keep visual_scoring.py focused: it is already about 924 lines after Task
  2. Add only the small versioned prompt loader and reuse the Task 1 canonical
  validators. Task 3 creates app/services/framing_analysis.py for the
  color-agnostic detector instead of growing this shared file; Task 4 extends
  that focused module for candidate feasibility.

**GREEN and checkpoint:**

- [ ] Run the focused adapter/evidence/pipeline tests, Task 7A synthesis, Task 7B pipeline, resolver vision, analyzer v1/v2, and BYOK tests with PATH=/home/yusronrohmani/.local/bin:$PATH.
- [ ] Run .venv/bin/ruff check app/services/vision_adapter.py app/services/visual_scoring.py app/services/pipeline.py tests/mock_provider.py tests/test_vision_adapter.py tests/test_vision_pipeline.py and .venv/bin/python -m compileall -q app/services/vision_adapter.py app/services/visual_scoring.py app/services/pipeline.py.
- [ ] Run git diff --check and compare the snapshot file to load_visual_evidence_instruction().
- [ ] Update STATUS/CHANGELOG with the provider acquisition contract, unknown-versus-known_empty behavior, and exact focused results.
- [ ] Stage only these Task 2 paths: app/services/vision_adapter.py, app/services/visual_scoring.py, app/services/pipeline.py, app/prompts/balloon_free_visual_evidence_v1.txt, tests/mock_provider.py, tests/test_vision_adapter.py, tests/test_vision_pipeline.py, tests/fixtures/visual_evidence_prompt_snapshot.sha256, docs/STATUS.md, and CHANGELOG.md. Commit `feat: acquire balloon visual evidence`; run the full non-slow suite; export/push the exact commit through the Windows transport. Rollback is this commit. Do not start Task 3 until GitHub SHA and VPS status are verified.

## Task 3: Detect color-agnostic border-connected low-information padding

**Files:**
- Create: app/services/framing_analysis.py as the focused detector/cache module.
- Modify: app/services/render.py to import framing_analysis and include detector identity in reference cache telemetry.
- Create: tests/test_color_agnostic_blank.py.
- Modify: docs/STATUS.md.
- Modify: CHANGELOG.md.

**Interfaces:**
- **Consumes:** `PanelVisualEvidence` and its validated typed protected regions from `app.services.visual_scoring`, plus a PIL Image. `framing_analysis.py` imports these types and validators; it does not re-export or duplicate them.
- **Produces:** `framing_analysis.BorderMaskResult`, `framing_analysis.build_color_agnostic_border_mask(image: Image.Image, evidence: PanelVisualEvidence, *, grid_long_edge: int = 256) -> BorderMaskResult`, and `framing_analysis.canonical_protected_geometry(evidence: PanelVisualEvidence) -> tuple[str, ...]`.
- **Render cache signature:** extend `render.reference_frame_cache_key(image_path, width, height, focus_x, focus_y, end_x, end_y, profile, *, border_mask: BorderMaskResult | None = None, evidence: PanelVisualEvidence | None = None) -> tuple` without changing the profile=None key when optional evidence is absent.
- **Cache contract:** `BorderMaskResult.mask_sha256` is derived locally from detector version, source/grid dimensions, and canonical bit masks. The reference cache key also includes the evidence mask status/hash and protected-region geometry.

Use the existing Task 1 serializer for the geometry component rather than
serializing dataclass reprs:

    def canonical_protected_geometry(evidence: PanelVisualEvidence) -> tuple[str, ...]:
        serialized = panel_visual_evidence_json(evidence)["protected_regions"]
        return tuple(
            json.dumps(region, sort_keys=True, separators=(",", ":"))
            for region in serialized
        )

- [ ] **Step 1: Add deterministic fixture tests**

Use PIL-only synthetic fixtures; do not use artwork or external files. The
test imports the new focused module and keeps Task 1 evidence construction in
the existing test helper:

    from math import floor

    from PIL import Image, ImageDraw

    from app.services import framing_analysis

    def colored_gutter(color):
        image = Image.new("RGB", (160, 240), color)
        ImageDraw.Draw(image).rectangle((30, 70, 130, 210), fill=(20, 30, 40))
        return image

    def gradient_gutter():
        image = Image.new("RGB", (160, 240))
        pixels = image.load()
        for y in range(240):
            value = 30 + round(y * 0.2)
            for x in range(160):
                pixels[x, y] = (value, 100, 180)
        ImageDraw.Draw(image).rectangle((45, 80, 115, 210), fill=(230, 230, 230))
        return image

    def source_cell(result, x_ratio, y_ratio):
        x = min(result.grid_width - 1, floor(x_ratio * result.grid_width))
        y = min(result.grid_height - 1, floor(y_ratio * result.grid_height))
        return result.edge_connected_mask[y][x], result.non_discardable_low_information_mask[y][x]

    def test_all_border_colors_and_mild_gradient_use_structure_not_brightness():
        for image in (
            colored_gutter((255, 255, 255)),
            colored_gutter((0, 0, 0)),
            colored_gutter((128, 128, 128)),
            colored_gutter((18, 92, 177)),
            gradient_gutter(),
        ):
            result = framing_analysis.build_color_agnostic_border_mask(
                image, evidence_for_no_balloon()
            )
            assert result.edge_connected_blank_fraction > 0.20

    def test_meaningful_light_and_dark_protected_art_is_retained():
        for background, fill, outline in (
            ((245, 245, 245), (250, 250, 250), (10, 10, 10)),
            ((15, 15, 15), (5, 5, 5), (245, 245, 245)),
        ):
            image = Image.new("RGB", (160, 240), background)
            draw = ImageDraw.Draw(image)
            draw.ellipse((20, 40, 140, 205), fill=fill, outline=outline, width=5)
            result = framing_analysis.build_color_agnostic_border_mask(
                image, evidence_for_protected_region((0.1, 0.15, 0.9, 0.9))
            )
            assert result.protected_retained_fraction >= 0.98
            assert source_cell(result, 0.5, 0.5)[0] is False

    def test_sealed_internal_low_information_is_diagnostic_not_discardable():
        image = colored_gutter((20, 20, 20))
        ImageDraw.Draw(image).rectangle((60, 110, 100, 150), fill=(125, 125, 125))
        result = framing_analysis.build_color_agnostic_border_mask(
            image, evidence_for_no_balloon()
        )
        edge, internal = source_cell(result, 0.5, 0.54)
        assert internal is True
        assert edge is False
        assert result.non_discardable_low_information_fraction > 0.0

    def test_source_area_mapping_is_integer_exact_and_ratios_are_six_decimals():
        image = colored_gutter((128, 128, 128))
        result = framing_analysis.build_color_agnostic_border_mask(
            image, evidence_for_no_balloon()
        )
        areas = []
        for y in range(result.grid_height):
            y0 = floor(y * image.height / result.grid_height)
            y1 = floor((y + 1) * image.height / result.grid_height)
            for x in range(result.grid_width):
                x0 = floor(x * image.width / result.grid_width)
                x1 = floor((x + 1) * image.width / result.grid_width)
                areas.append((x1 - x0) * (y1 - y0))
        assert sum(areas) == image.width * image.height
        edge_area = sum(
            area
            for area, row in zip(areas, [cell for row in result.edge_connected_mask for cell in row])
            if row
        )
        assert result.edge_connected_blank_fraction == round(
            edge_area / (image.width * image.height), 6
        )

    def test_mask_hash_is_deterministic_and_changes_with_canonical_mask():
        image = colored_gutter((18, 92, 177))
        first = framing_analysis.build_color_agnostic_border_mask(image, evidence_for_no_balloon())
        second = framing_analysis.build_color_agnostic_border_mask(image, evidence_for_no_balloon())
        assert first.mask_sha256 == second.mask_sha256
        assert len(first.mask_sha256) == 64

Expected RED: the test bodies fail because framing_analysis.py and its
detector result do not exist; fixture construction itself must pass.

- [ ] **Step 2: Implement the fixed-grid structure detector in framing_analysis.py**

Use this public record and function signature; `visual_scoring.py` remains the
owner of `PanelVisualEvidence` parsing and validation:

    import hashlib
    import json
    from dataclasses import dataclass
    from math import floor

    DETECTOR_VERSION = "COLOR_AGNOSTIC_BALLOON_FREE_V1:grid256:structure4"

    @dataclass(frozen=True)
    class BorderMaskResult:
        detector_version: str
        source_width: int
        source_height: int
        grid_width: int
        grid_height: int
        edge_connected_mask: tuple[tuple[bool, ...], ...]
        non_discardable_low_information_mask: tuple[tuple[bool, ...], ...]
        protected_mask: tuple[tuple[bool, ...], ...]
        edge_connected_blank_fraction: float
        non_discardable_low_information_fraction: float
        protected_retained_fraction: float
        mask_sha256: str

    def _source_cell_bounds(index, grid_size, source_size):
        start = floor(index * source_size / grid_size)
        end = floor((index + 1) * source_size / grid_size)
        return start, max(start + 1, end)

    def _rounded_fraction(numerator, denominator):
        return round(numerator / denominator, 6) if denominator else 0.0

    def build_color_agnostic_border_mask(
        image: Image.Image,
        evidence: PanelVisualEvidence,
        *,
        grid_long_edge: int = 256,
    ) -> BorderMaskResult:
        if image.width <= 0 or image.height <= 0 or grid_long_edge <= 0:
            raise ValueError("visual.mask_dimensions_invalid")
        scale = grid_long_edge / max(image.width, image.height)
        grid_width = min(image.width, max(1, round(image.width * scale)))
        grid_height = min(image.height, max(1, round(image.height * scale)))
        source_cells = tuple(
            tuple(
                (
                    _source_cell_bounds(x, grid_width, image.width),
                    _source_cell_bounds(y, grid_height, image.height),
                )
                for x in range(grid_width)
            )
            for y in range(grid_height)
        )
        protected_mask = rasterize_protected_regions(evidence, source_cells)
        low_information_mask = classify_low_information_cells(image, source_cells)
        edge_connected_mask = flood_border_cells(
            low_information_mask, protected_mask, connectivity=8
        )
        non_discardable_mask = tuple(
            tuple(low and not edge for low, edge in zip(low_row, edge_row))
            for low_row, edge_row in zip(low_information_mask, edge_connected_mask)
        )
        mask_payload = {
            "detector_version": DETECTOR_VERSION,
            "source_dimensions": [image.width, image.height],
            "grid_dimensions": [grid_width, grid_height],
            "edge_connected_mask": edge_connected_mask,
            "non_discardable_low_information_mask": non_discardable_mask,
            "protected_mask": protected_mask,
        }
        mask_sha256 = hashlib.sha256(
            json.dumps(mask_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return BorderMaskResult(
            detector_version=DETECTOR_VERSION,
            source_width=image.width,
            source_height=image.height,
            grid_width=grid_width,
            grid_height=grid_height,
            edge_connected_mask=edge_connected_mask,
            non_discardable_low_information_mask=non_discardable_mask,
            protected_mask=protected_mask,
            edge_connected_blank_fraction=source_area_fraction(
                edge_connected_mask, source_cells, image.size
            ),
            non_discardable_low_information_fraction=source_area_fraction(
                non_discardable_mask, source_cells, image.size
            ),
            protected_retained_fraction=protected_retained_fraction(
                protected_mask, source_cells, image.size
            ),
            mask_sha256=mask_sha256,
        )

`source_cells` must use exactly `x0=floor(i*source_width/grid_width)`,
`x1=floor((i+1)*source_width/grid_width)`, and the analogous y boundaries.
The grid dimensions are clamped no larger than their source dimensions, so
each interval is positive without changing the floor mapping. `source_area_fraction`
sums integer source pixel areas, and rounds only the final ratio to six
decimals; it never counts grid cells equally.

`classify_low_information_cells` computes luminance-window variance,
eight-bit entropy, gradient/edge density, and two-scale texture energy. Each
metric is normalized by a fixed physical bound or by `max(nonzero_p95,
epsilon)` and clipped to 0..1; it is never converted to an empirical percentile
rank. A cell is low-information only when at least three of these thresholds
are met: variance <= 0.08, entropy <= 0.20, edge density <= 0.08, and texture
energy <= 0.08. Do not use a raw mean, near-white test, or black/gray test to
classify a cell.

`rasterize_protected_regions` maps every typed subject, face, action, effect,
continuity_context, and background polygon/bbox to the same source-cell grid.
`flood_border_cells` starts from every top, bottom, left, and right border cell,
uses 8-neighbor connectivity, follows only low-information and unprotected
cells, and returns the ONLY discardable blank mask. Internal low-information
components are retained in `non_discardable_low_information_mask` for
diagnostics and are never cropped as blank. `protected_retained_fraction` is
retained protected source area divided by total protected source area, rounded
to six decimals, and is exactly 1.0 when there are no protected cells.

- [ ] **Step 3: Keep the legacy profile=None path unchanged**

In render.py, leave crop_to_vertical and the profile=None branch of
editorial_frame untouched. The profile branch calls
`framing_analysis.build_color_agnostic_border_mask` only when profile is not
None. Keep _reference_content_stats for legacy tests and non-profile fallback
telemetry, but do not use its near-white boolean as the reference hard mask.
Add `from app.services import framing_analysis` at the existing service import
boundary; do not re-export the detector from visual_scoring.py.
Extend the existing reference cache payload before candidate selection with
these exact fields:

    cache_payload.update(
        detector_version=border_mask.detector_version,
        mask_sha256=border_mask.mask_sha256,
        balloon_mask_status=evidence.balloon_mask_status,
        evidence_hash=evidence.evidence_hash,
        protected_geometry=framing_analysis.canonical_protected_geometry(evidence),
    )

The cache key must change when any of those fields changes, while legacy
profile=None cache keys and output bytes remain unchanged.

- [ ] **Step 4: Run the color and legacy matrix**

Run:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_color_agnostic_blank.py tests/test_reference_framing.py tests/test_motion_stability.py tests/test_reference_profile.py -q
    .venv/bin/ruff check app/services/framing_analysis.py app/services/render.py tests/test_color_agnostic_blank.py
    .venv/bin/python -m compileall -q app
    git diff --check

Expected GREEN: all color fixtures, meaningful art protections, border flood
fill, detector hash, legacy framing, and monotonic motion tests pass.
Also assert that changing detector_version, mask_sha256, balloon mask status,
evidence_hash, or protected geometry changes the profile-mode cache key while
the profile=None key remains byte-for-byte unchanged.

- [ ] **Step 5: Update docs, run full non-slow, commit, and push**

Record detector version, source-area threshold metrics, internal diagnostic
metrics, mask hash, legacy compatibility evidence, and Task 4 as next in
STATUS and CHANGELOG. Run:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/python -m pytest -q -m "not slow"

Commit only the five owned paths with:

    git add -- app/services/framing_analysis.py app/services/render.py tests/test_color_agnostic_blank.py docs/STATUS.md CHANGELOG.md
    git diff --cached --check
    git commit -m "feat: detect color agnostic border padding"

Push immediately with the exact-history Windows bundle workflow and verify
GitHub main equals the new SHA. Task 3 starts from baseline
`940ab42d135626cfb096c3b3b3e7957d549e3923`; rollback is this commit.

## Task 4: Rank feasible crop candidates with hard balloon exclusion

**Files:**
- Modify: app/services/framing_analysis.py beside BorderMaskResult and the detector.
- Modify: app/services/reference_profile.py.
- Modify: app/services/render.py.
- Modify: tests/test_reference_framing.py.
- Modify: docs/STATUS.md.
- Modify: CHANGELOG.md.

**Interfaces:**
- **Consumes:** `framing_analysis.BorderMaskResult`, `PanelVisualEvidence`, current focus inputs, and `ReferenceProfileConfig`.
- **Produces:** `framing_analysis.FramingTelemetry`, `framing_analysis.candidate_is_feasible(...) -> tuple[bool, FramingTelemetry]`, and `render.PreparedFrame.telemetry`, while retaining the existing path, crop_box, blank_fraction, and base_zoom fields.
- **Import boundary:** `render.py` imports `framing_analysis`; `framing_analysis.py` imports Task 1 visual evidence serializers/validators but never imports render.py, preventing a circular dependency.

- [ ] **Step 1: Add failing candidate tests**

Add tests that directly prove the old near-white score is insufficient:

    def test_reference_candidate_rejects_one_pixel_balloon_overlap():
        frame = make_frame_with_balloon_bbox((0.42, 0.42, 0.58, 0.54))
        with pytest.raises(RenderError, match="visual.balloon_mask_overlap"):
            prepare_reference_frame(
                frame.source, frame.dest, 1080, 1920, 0.5, 0.5,
                profile_with_balloon(frame.evidence)
            )

    def test_dynamic_zoom_respects_native_resolution_and_protected_face():
        prepared = prepare_reference_frame(
            small_source, destination, 1080, 1920, 0.5, 0.5,
            protected_face_profile
        )
        assert prepared.base_zoom <= prepared.telemetry.source_resolution_zoom_cap
        assert prepared.telemetry.subject_coverage >= 0.98

    def test_blank_target_zero_is_reported_when_infeasible_not_silently_passed():
        prepared = prepare_reference_frame(
            gutter_source, destination, 1080, 1920, 0.5, 0.5, profile
        )
        assert prepared.telemetry.edge_connected_blank_fraction >= 0.0
        if prepared.telemetry.edge_connected_blank_fraction > 0.0:
            assert prepared.telemetry.fallback_reason == "visual.blank_infeasible"

Expected RED: the existing PreparedFrame has no telemetry, balloon overlap
does not block, and the profile has no framing-contract fields.

- [ ] **Step 2: Extend the frozen profile and prepared-frame telemetry**

Add the following fields to ReferenceProfileConfig after max_blank_fraction
and include them in the existing asdict sorted-key hash:

    framing_contract_version: str
    framing_blank_target_fraction: float
    framing_balloon_intersection_max: float
    framing_mask_grid_long_edge: int
    framing_safe_area_margin: float

Set the reference instance to:

    framing_contract_version="COLOR_AGNOSTIC_BALLOON_FREE_V1"
    framing_blank_target_fraction=0.0
    framing_balloon_intersection_max=0.0
    framing_mask_grid_long_edge=256
    framing_safe_area_margin=0.03

Add the analysis telemetry record beside the detector in
app/services/framing_analysis.py. `render.py` imports this record and uses it
as `PreparedFrame.telemetry`; framing_analysis never imports render.py:

    @dataclass(frozen=True)
    class FramingTelemetry:
        contract_version: str
        detector_version: str
        mask_sha256: str
        crop_box: tuple[int, int, int, int]
        base_zoom: float
        source_resolution_zoom_cap: float
        protected_region_zoom_cap: float
        edge_connected_blank_fraction: float
        non_discardable_low_information_fraction: float
        protected_retained_fraction: float
        balloon_mask_intersection_ratio: float
        subject_coverage: float
        face_coverage: float
        action_coverage: float
        effect_coverage: float
        continuity_context_coverage: float
        mask_confidence: float
        mask_source: str
        fallback_reason: str
        rejection_code: str | None

    @dataclass(frozen=True)
    class PreparedFrame:
        path: Path
        crop_box: tuple[int, int, int, int]
        blank_fraction: float
        base_zoom: float
        telemetry: framing_analysis.FramingTelemetry | None = None

The default keeps existing non-reference callers source-compatible.

- [ ] **Step 3: Implement deterministic candidate feasibility**

Replace only the profile branch of prepare_reference_frame with a candidate
loop that calls the Task 3 detector and then
`framing_analysis.candidate_is_feasible`. For each 0.02 scale:

    def candidate_is_feasible(
        box: tuple[int, int, int, int],
        evidence: PanelVisualEvidence,
        border_mask: BorderMaskResult,
        source_size: tuple[int, int],
        target_size: tuple[int, int],
    ) -> tuple[bool, FramingTelemetry]:
        balloon_ratio = balloon_intersection_ratio(box, evidence)
        coverages = protected_coverages(box, evidence)
        source_cap = source_resolution_zoom_cap(source_size, target_size)
        feasible = (
            balloon_ratio == 0.0
            and coverages["subject"] >= 0.98
            and coverages["face"] >= 0.98
            and coverages["action"] >= 0.95
            and coverages["continuity_context"] >= 0.95
            and coverages["effect"] >= 0.90
            and base_zoom_for(box, source_size) <= source_cap
        )
        telemetry = FramingTelemetry(
            contract_version="COLOR_AGNOSTIC_BALLOON_FREE_V1",
            detector_version=border_mask.detector_version,
            mask_sha256=border_mask.mask_sha256,
            crop_box=box,
            base_zoom=base_zoom_for(box, source_size),
            source_resolution_zoom_cap=source_cap,
            protected_region_zoom_cap=protected_zoom_cap(coverages),
            edge_connected_blank_fraction=crop_blank_fraction(box, border_mask),
            non_discardable_low_information_fraction=border_mask.non_discardable_low_information_fraction,
            protected_retained_fraction=border_mask.protected_retained_fraction,
            balloon_mask_intersection_ratio=balloon_ratio,
            subject_coverage=coverages["subject"],
            face_coverage=coverages["face"],
            action_coverage=coverages["action"],
            effect_coverage=coverages["effect"],
            continuity_context_coverage=coverages["continuity_context"],
            mask_confidence=evidence.mask_confidence,
            mask_source=evidence.evidence_source,
            fallback_reason="",
            rejection_code=None if feasible else "visual.crop_candidate_infeasible",
        )
        return feasible, telemetry

A candidate is feasible only when balloon_mask_intersection_ratio == 0.0,
required subject/face retained area is at least 0.98, required action and
continuity context at least 0.95, required effects at least 0.90, and the
source-resolution crop dimensions are at least target dimensions divided by
1.15. The candidate ranking tuple is:

    (balloon_zero, protected_area, 1.0 - edge_blank, focus_score,
     -base_zoom, box[1], box[0])

Call require_reference_ready_visual_evidence before candidate ranking; it
rejects unknown mask status with visual.balloon_mask_unknown. If all candidates meet hard requirements but
edge blank is above zero, select the deterministic lowest-blank candidate and
set fallback_reason to visual.blank_infeasible. If no candidate meets a hard
requirement, raise RenderError with the stable rejection code and telemetry
for the last ordered fallback attempt.

Keep the static chosen box in the cache. Do not scan content per camera frame.

- [ ] **Step 4: Test profile hash and legacy behavior**

Update the existing profile canonical JSON/hash assertions for the five new
fields. Assert that changing each framing field changes profile_hash. Assert
that profile=None crop_to_vertical, editorial_frame, build_ass, and preview
requests retain their existing snapshots/behavior. Assert that a reference
ASS build still rejects invalid display cues independently of framing.

- [ ] **Step 5: Run, document, commit, and push**

Run:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_reference_framing.py tests/test_color_agnostic_blank.py tests/test_reference_profile.py tests/test_motion_stability.py -q
    .venv/bin/ruff check app/services/framing_analysis.py app/services/reference_profile.py app/services/render.py tests/test_reference_framing.py
    .venv/bin/python -m compileall -q app
    git diff --check
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/python -m pytest -q -m "not slow"

Update STATUS/CHANGELOG with profile hash, telemetry examples, and Task 5.
Stage only the six owned paths and commit:

    git add -- app/services/framing_analysis.py app/services/reference_profile.py app/services/render.py tests/test_reference_framing.py docs/STATUS.md CHANGELOG.md
    git diff --cached --check
    git commit -m "feat: rank balloon free reference crop candidates"

Push immediately by exact-history fast-forward and record rollback SHA.

## Task 5: Apply panel and beat fallback plus reference QC

**Files:**
- Modify: app/services/editorial_visual_planner.py.
- Modify: app/services/editorial_qc.py.
- Modify: app/services/quality.py.
- Modify: tests/test_reference_profile_integration.py.
- Modify: docs/STATUS.md.
- Modify: CHANGELOG.md.

The six paths are intentional: the two documentation files are mandatory
handoff records and the four source/test paths are the smallest coherent QC
boundary.

**Interfaces:**
- **Consumes:** PanelVisualEvidence, `framing_analysis.FramingTelemetry`, candidate evidence mapping, planner citations, and current plan(..., profile=...) signature.
- **Produces:** alignment reasons, fallback records, stable QC codes, and no speech_bubble target.

- [ ] **Step 1: Add failing fallback and QC tests**

Use synthetic candidates with a deterministic evidence map:

    def test_reference_fallback_order_is_auditable():
        result = planner.plan(
            spans, candidates, profile=REFERENCE_MATCHED_SHORTS_V1,
            cited_asset_ids_by_section={"hook": ("asset-1",)},
            visual_evidence_by_asset=evidence_map,
        )
        assert [attempt.kind for attempt in result.fallback_attempts] == [
            "alternate_roi", "tighter_crop", "same_beat_panel"
        ]
        assert result.alignment_reasons[-1] == "evidence_context_fallback:anchor:asset-1"

    def test_unknown_balloon_and_positive_overlap_are_blocking_reference_qc():
        failures = quality.check_reference_framing(
            scenes, evidence_by_asset, profile=REFERENCE_MATCHED_SHORTS_V1
        )
        assert {failure.code for failure in failures} >= {
            "visual.balloon_mask_unknown",
            "visual.balloon_mask_overlap",
        }

    def test_speech_bubble_roi_is_never_selected_or_motion_target():
        planned = planner.plan(
            spans, candidates, profile=profile,
            visual_evidence_by_asset=evidence_map
        )
        assert all(shot["roi_label"] != "speech_bubble" for shot in planned)
        assert all(shot["camera_intent"] != "speech_bubble" for shot in planned)

Expected RED: framing_analysis has no candidate feasibility boundary, the
plan has no visual_evidence_by_asset parameter or fallback attempt ledger, and
quality has no visual framing gate.

- [ ] **Step 2: Add explicit planner evidence inputs and fallback result**

First extract the current non-profile branch into the private compatibility
function `def _plan_legacy(span_list: list[object], candidates: list[object]) -> list[dict]`;
its body and output must remain byte/behavior compatible. Extend the reference
path without changing the legacy call:

    def plan(
        spans: Iterable[object],
        candidates: list[object],
        profile: object | None = None,
        cited_asset_ids_by_section: Mapping[str, Iterable[str]] | None = None,
        citation_alignment_reasons_by_section: Mapping[str, Iterable[str]] | None = None,
        visual_evidence_by_asset: Mapping[str, PanelVisualEvidence] | None = None,
    ) -> list[dict]:
        span_list = list(spans)
        if profile is not None:
            return _plan_reference(
                span_list,
                candidates,
                profile,
                cited_asset_ids_by_section,
                citation_alignment_reasons_by_section,
                visual_evidence_by_asset,
            )
        return _plan_legacy(span_list, candidates)

Before selecting a shot in reference mode, call
require_reference_ready_visual_evidence for each candidate; it rejects unknown
balloon mask before any ROI is selected. Also reject a speech_bubble ROI.
Preserve valid cited anchors. When a
section needs more shots than its mapped anchors can fill, select chronological
context candidates only from the same story progression and append
evidence_context_fallback:anchor:<panel_id>. Never append citation_alignment
to a context candidate.

Keep ReferencePlanningError and its safe code. If a same-panel alternate ROI
is available, attempt it first; then a tighter safe crop; then another
renderable candidate in the same beat; then raise visual.visual_unavailable.
No candidate may be silently relabeled as safe.

- [ ] **Step 3: Wire profile-aware QC in both report paths**

Add a focused helper in quality.py:

    def check_reference_framing(
        scenes: list[object],
        evidence_by_asset: Mapping[str, PanelVisualEvidence],
        *,
        profile: ReferenceProfileConfig,
    ) -> list[CheckResult]:
        failures: list[CheckResult] = []
        for asset_id, evidence in sorted(evidence_by_asset.items()):
            try:
                require_reference_ready_visual_evidence(evidence)
            except VisualEvidenceError as error:
                failures.append(_fail(error.code, CheckSeverity.ERROR, str(error)))
            if evidence.balloon_mask_status == "known_nonempty":
                failures.extend(_check_balloon_intersection(asset_id, scenes, evidence))
        failures.extend(_check_reference_coverage(scenes, profile))
        return sorted(failures, key=lambda failure: failure.code)

It emits visual.balloon_mask_unknown, visual.balloon_mask_overlap,
visual.subject_coverage_insufficient, visual.action_coverage_insufficient,
visual.blank_infeasible, and visual.visual_unavailable with CheckSeverity.ERROR
for hard failures. editorial_qc.build_report(..., profile=profile) adds the
same safe codes to its report without changing profile=None behavior. It also
calls motion_director.audit_camera_sequence so forbidden curves and reversals
remain blocking.

The helper calls `_check_balloon_intersection`, `_check_reference_coverage`,
and the existing `_fail(code, severity, message, detail)` boundary; it never
rewrites a candidate or downgrades an unknown mask to known_empty.

- [ ] **Step 4: Add chronology, rights, and motion regressions**

Assert every planned shot retains source asset, panel ID, story beat, and
evidence hash; every source order remains covered; repeated assets use distinct
ROIs and explicit reuse reasons; and no random call is made. Keep rights
checks and publish_allowed=false untouched. Sample at least 120 frames of each
reference curve and assert monotonic focus/scale and no forbidden tokens.
Assert legacy/default scenes still use their prior panel fallback and QC codes.

- [ ] **Step 5: Run, document, commit, and push**

Run:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_reference_profile_integration.py tests/test_reference_framing.py tests/test_motion_stability.py tests/test_visual_scoring.py tests/test_quality.py -q
    .venv/bin/ruff check app/services/editorial_visual_planner.py app/services/editorial_qc.py app/services/quality.py tests/test_reference_profile_integration.py
    .venv/bin/python -m compileall -q app
    git diff --check
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/python -m pytest -q -m "not slow"

Update both docs with exact stable codes, fallback evidence, and Task 6.
Commit only the six owned paths:

    git add -- app/services/editorial_visual_planner.py app/services/editorial_qc.py app/services/quality.py tests/test_reference_profile_integration.py docs/STATUS.md CHANGELOG.md
    git diff --cached --check
    git commit -m "feat: gate balloon free reference panel fallback"

Push immediately with the exact-history workflow and record the rollback SHA.

## Task 6: Integrate the isolated real-panel silent review render

**Files:**
- Modify: app/services/pipeline.py at build_timeline and reference evidence mapping.
- Modify: app/services/render.py at RenderRequest/profile-aware preparation and QC sidecars.
- Create: tests/test_reference_visual_review.py.
- Modify: docs/STATUS.md.
- Modify: CHANGELOG.md.

**Interfaces:**
- **Consumes:** approved script section evidence_panel_ids, PanelVisualEvidence, planner fallback results, PreparedFrame telemetry, and RenderRequest.profile.
- **Produces:** an untracked silent visual review bundle; no TTS, no audio provider, and no publication approval.

- [ ] **Step 1: Add a body-failing isolated review test**

The test must construct real rights-safe PIL images in a temporary directory,
three PanelRegion-like evidence records, 32 deterministic scenes, one-word
display cues, and a RenderRequest with audio_path=None and profile
REFERENCE_MATCHED_SHORTS_V1:

    def test_reference_review_bundle_is_visual_only(tmp_path, monkeypatch):
        request = RenderRequest(
            output_path=tmp_path / "review.mp4",
            scenes=reference_scenes(),
            cues=reference_cues(),
            audio_path=None,
            preview=False,
            profile=REFERENCE_MATCHED_SHORTS_V1,
            encoder="cpu",
            title_text="",
            music_path=None,
        )
        assert request.profile.profile_id == "reference_matched_shorts_v1"
        assert request.audio_path is None
        result = render.render(request)
        assert result.audio_stream is False
        assert result.qc["publish_allowed"] is False

Expected RED: the current pipeline does not pass typed visual evidence through
the reference timeline/render sidecar and no isolated review assertion exists.
The test must not call TTS, espeak, a network provider, or write outside
tmp_path.

- [ ] **Step 2: Add deterministic evidence-to-timeline mapping**

At build_timeline, preserve the current _reference_citation_map panel_id
first and integer source_order second rule. Add a map from renderable asset ID
to PanelVisualEvidence and pass it to planner.plan as visual_evidence_by_asset.
Keep the current ordering and no-random behavior:

    visual_evidence_by_asset = _reference_visual_evidence_map(
        db, project_id, script, images
    ) if profile is not None else None
    planned = editorial_visual_planner.plan(
        spans, scored, profile=profile,
        cited_asset_ids_by_section=citation_map if profile is not None else None,
        citation_alignment_reasons_by_section=citation_reasons if profile is not None else None,
        visual_evidence_by_asset=visual_evidence_by_asset,
    )

Invalid or missing evidence must raise PipelineError with a stable visual code
before deleting existing TimelineScene or SubtitleCue rows. Legacy profile=None
continues its current planner invocation.

- [ ] **Step 3: Persist framing telemetry in render QC**

Extend the existing RenderRequest/scene sidecar path to include the selected
FramingTelemetry fields in QC JSON and shot_list output. Keep width, height,
fps, H.264 High, yuv420p, hard cuts, audio normalization, and existing
reference output checks. When request.profile is None, serialize no new
reference telemetry and preserve legacy preview behavior.

- [ ] **Step 4: Run visual review verification on VPS**

Run:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_reference_visual_review.py tests/test_reference_profile_integration.py tests/test_reference_framing.py tests/test_motion_stability.py tests/test_subtitle_display_contract.py -q
    .venv/bin/ruff check app/services/pipeline.py app/services/render.py tests/test_reference_visual_review.py
    .venv/bin/python -m compileall -q app
    git diff --check
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/python -m pytest -q -m "not slow"
    /home/yusronrohmani/.local/bin/ffprobe -v error -show_streams -show_format review.mp4

The FFmpeg check must report geometry, frame rate, codec/profile/pixel format,
shot count, cue count, blank fraction, balloon intersection, source coverage,
no black frames, and publish_allowed=false. Voice generation and audio
generation remain absent.

- [ ] **Step 5: Update docs, commit, and push**

Record exact isolated bundle paths, test totals, probe metrics, rights
decision, commit SHA, clean state, and the next approved action. Stage only
the five paths and commit:

    git add -- app/services/pipeline.py app/services/render.py tests/test_reference_visual_review.py docs/STATUS.md CHANGELOG.md
    git diff --cached --check
    git commit -m "test: audit balloon free silent visual review"

Push immediately through the Windows exact-history clone. Remove only the
temporary bundle/patch after HTTPS ls-remote equals the commit. Keep the clean
transport clone for audit and rollback.

## Acceptance matrix and rollback

| Approved spec requirement | Plan task and proving assertion |
| --- | --- |
| Typed balloon/background/subject/action/effect records | Task 1 dataclasses, enum validation, JSON round-trip |
| Unknown versus known-empty masks | Task 1 persistence and Task 4 reference-readiness failures |
| Provider acquisition of balloon/protected geometry | Task 2 prompt, adapter, mock, and snapshot tests |
| Color-agnostic white/black/gray/arbitrary/gradient detection | Task 3 PIL fixtures |
| Meaningful light/dark art protection | Task 3 protected-area tests |
| Border flood fill and internal-background distinction | Task 3 mask topology tests |
| Exact source-space area mapping and six-decimal ratios | Task 3 floor-boundary and area-accounting test |
| Internal low-information diagnostic without discard | Task 3 sealed-island mask test |
| Deterministic detector/cache identity | Task 3 mask_sha256 and cache-key test |
| Balloon intersection exactly zero | Tasks 4-6 one-pixel and area-overlap failures |
| Subject/action/effect/continuity minimums | Task 4 candidate feasibility |
| Dynamic zoom/upscale guard | Task 4 native-resolution tests |
| Exact fallback order and stable visual_unavailable | Task 5 fallback ledger |
| No speech_bubble selection or motion | Task 5 planner assertions |
| Stable monotonic motion and no shake | Tasks 4-5 120-frame and filter tests |
| Full panel/story/claim coverage and rights gate | Tasks 1, 2, 5, and 6 lineage/rights assertions |
| Legacy profile=None behavior | Tasks 3 and 4 regression snapshots |
| Silent visual review with no voice/audio | Task 6 RenderRequest and FFprobe assertions |
| STATUS/CHANGELOG progress and immediate push | Every task's final step |
| No media/DB/credentials/runtime data in Git | Every task's staged allowlist and secret scan |

Each task is a rollback boundary. The next task starts only after its commit
is pushed and the VPS worktree is clean. A failed task is left uncommitted for
review or reverted by its own reviewed commit; no broad reset or destructive
cleanup is permitted.
