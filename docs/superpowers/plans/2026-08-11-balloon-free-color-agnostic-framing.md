# Balloon-Free Color-Agnostic Framing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. The named superpowers sub-skills are not installed in this environment; the active equivalent is Sol review plus Luna task execution. Execute one checked task at a time and stop at the stated review boundary.

**Goal:** Add deterministic color-agnostic, balloon-free reference framing that preserves protected visual evidence, uses an auditable fallback chain, and leaves legacy profile=None behavior unchanged.

**Architecture:** Keep visual_scoring.py as the typed panel-region evidence boundary and retain the focused app/services/framing_analysis.py module for deterministic border masks and later candidate feasibility. First carry the PanelRegion coordinate space, evidence, and immutable source checksum through TimelineScene and materialize a deterministic panel crop; only then let render.py prepare reference frames from that crop. The existing editorial_visual_planner.py and editorial_qc.py enforce fallback and reference gates after the lineage boundary. Persist evidence as a versioned JSON sidecar inside PanelRegion.observation_json; add a migration only when the timeline snapshot cannot be represented compatibly by the current schema.

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
- "panel-normalized visual evidence is applied only to its persisted PanelRegion crop; a full SourceAsset strip is never paired with panel coordinates"
- "missing, foreign, stale, or malformed panel lineage fails closed with visual.panel_lineage_unavailable"
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
  241e1ff4f61e71238cf59cf842a1c71c7fc2184a, the published Task 6/7 panel-lineage
  contract correction commit. The preceding docs-only amendment parent was
  historical 9f958877db1521ff2e5f1865fe08dc05e5fa8370; this commit is the
  implementation parent;
  do not copy a stale historical parent into a task command.
- Historical checkpoint: 635 passed in the full non-slow suite at f9221dd; it is evidence only and is not a fresh result for this planning commit.
- Every PowerShell SSH command in this plan ends with 2>&1.
- Current profile=None crop_to_vertical, legacy editorial_frame compositions, legacy build_ass, and preview behavior are compatibility surfaces. Tests must prove no reference change leaks into them.

## Current symbol map

These are the real baseline symbols inspected before writing this plan:

- app/services/reference_profile.py defines frozen ReferenceProfileConfig, REFERENCE_MATCHED_SHORTS_V1, canonical_profile_json, profile_hash, and resolve_reference_profile. The current reference values include base_frame_zoom_max=1.35 and max_blank_fraction=0.18.
- app/services/render.py defines crop_to_vertical(src, dest, width, height, focus_x, focus_y), frozen PreparedFrame(path, crop_box, blank_fraction, base_zoom), reference_frame_cache_key(..., profile, *, border_mask=None, evidence=None), prepare_reference_frame(..., profile), editorial_frame(..., profile=None), and build_ass(..., profile=None).
- prepare_reference_frame currently searches 0.02 scale increments and scores _reference_content_stats. That helper uses a near-white RGB threshold; it is not a hard blank gate.
- app/services/visual_scoring.py defines PanelScoreWeights, VisualFeatures, PanelCandidate, analyze_panel(data, asset_id="", order_index=0, source_family=""), analyze_assets, selection_reasons, and plan_content_aware_scenes. Its current speech_balloon_dominance and blank_dominance values are heuristics, not masks.
- app/services/framing_analysis.py now exists from Visual Task 3 as the sole
  owner of color-agnostic border-mask metrics and Task 5 extends the same
  focused module with candidate feasibility; it imports Task 1 visual evidence
  types and does not re-export or duplicate their validators.
- app/services/editorial_visual_planner.py defines the current plan(...)->list[dict] and _plan_reference path; Task 6 adds the frozen ReferencePanelFallbackCandidate sequence without changing the profile=None return.
- app/services/editorial_qc.py defines build_report(..., profile=None); app/services/quality.py defines check_reference_profile, check_repetition_and_motion, check_subtitles, and profile-aware CheckResult values.
- app/services/pipeline.py defines run_analysis(db, project_id, actor_id=""), generate_script, build_timeline, build_render_request, and current evidence-to-asset mapping through _reference_citation_map. build_timeline currently creates TimelineScene rows from timeline.SceneSpec without panel lineage; build_render_request currently maps each scene.asset_id back to the full SourceAsset path.
- app/services/timeline.py defines SceneSpec with asset_id, focus/camera fields, alignment telemetry, and no panel-region snapshot fields. app/services/render.py defines SceneInput with image_path and camera/effect fields, and RenderRequest.profile; neither currently carries panel_id, panel bounds, or typed visual evidence.
- app/models.py defines PanelRegion.source_asset_id, panel_id, source_order, bounds_json, source_asset_checksum, coverage_map_hash, and observation_json. TimelineScene currently stores only asset_id plus timing, motion, alignment, and overlay fields. app/db.py init_db() contains SQLite compatibility ALTERs for prior timeline/source columns, while Alembic is the migration owner; the live Alembic current revision is a4p0_editorial_voice_visual_contract. The new revision filename/revision ID must be inspected and generated from that live head during Task 4, never guessed in this plan.
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
      -> reference TimelineScene panel snapshot/crop boundary
      -> require_reference_ready_visual_evidence
      -> framing_analysis.build_color_agnostic_border_mask
      -> render.prepare_reference_frame static candidate window
      -> editorial_visual_planner.plan reference fallback
      -> editorial_qc.build_report and quality checks
      -> stable monotonic FFmpeg motion
      -> review-only output with rights gate

Task dependencies:

- Task 1 establishes typed evidence and persistence shape; unknown is persistable.
- Task 2 acquires that geometry from every ordered vision observation.
- Task 3 consumes the acquired evidence and produces the color-agnostic border mask in framing_analysis.py.
- Task 4 binds cited PanelRegions into TimelineScene snapshots and materializes evidence-aligned panel crops in render requests.
- Task 5 extends framing_analysis.py with candidate feasibility and consumes the materialized crop, evidence, and mask telemetry to produce feasible static frames.
- Task 6 consumes frame feasibility and planner citations to enforce fallback and QC.
- Task 7 consumes all prior interfaces and proves the isolated real-panel review path.

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
- Task 4 produces lineage-bearing timeline.SceneSpec/SceneInput records and a deterministic panel-crop boundary.
- Task 5 produces framing_analysis.FramingTelemetry and PreparedFrame.telemetry, while retaining
  the existing PreparedFrame.path/crop_box/blank_fraction/base_zoom fields.
- Task 6 consumes evidence_by_asset and produces stable planner/QC findings.
- Task 7 consumes all prior sidecars and produces only isolated review artifacts;
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
  color-agnostic detector instead of growing this shared file; Task 5 extends
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
GitHub main equals the new SHA. Tasks 1-5 are already complete before this correction; implementation after the published
amendment starts at `241e1ff4f61e71238cf59cf842a1c71c7fc2184a`, and Task 6 is the next atomic slice.

## Task 4: Persist panel lineage and materialize evidence-aligned panel crops

**Files:**

- Modify: app/models.py at TimelineScene and its relationships.
- Modify: app/db.py in the additive SQLite compatibility section of init_db().
- Create: one new migration file in alembic/versions/. Before editing, run
  `.venv/bin/alembic current` and inspect the live `a4p0_editorial_voice_visual_contract`
  head; generate the revision from that head and record the actual filename and
  revision in STATUS/CHANGELOG. Do not guess a revision ID or overwrite an
  existing migration.
- Modify: app/services/timeline.py at SceneSpec.
- Modify: app/services/pipeline.py at _reference_citation_map, build_timeline,
  build_render_request, and the existing _panel_region_bounds boundary.
- Modify: app/services/render.py at SceneInput.
- Modify: tests/test_vision_migration.py for the additive schema contract.
- Create: tests/test_panel_lineage_render.py for binding, snapshot, and crop
  behavior.
- Modify: tests/test_pipeline.py only for directly affected reference timeline
  regressions; preserve every legacy assertion and profile=None path.
- Modify: docs/STATUS.md and CHANGELOG.md.

This is a standalone TDD/commit/push slice. It does not implement the Task 5
candidate detector, Task 6 fallback planner, Task 7 review render, narration,
voice, or any media generation.

**Current boundary and failure being corrected:**

- `app/services/pipeline.py::_encode_panel_payload(panel, source_input)` already
  crops a PanelRegion in the stored slice coordinate system for vision. Its
  `global_bounds = _panel_region_bounds(panel)` and local translation are the
  authoritative coordinate semantics.
- `build_timeline()` currently persists only `TimelineScene.asset_id` and the
  planned camera/timing fields. `build_render_request()` then resolves that ID
  to the full SourceAsset path. A panel-normalized balloon or protected mask
  would therefore be applied to the wrong image space.
- Task 4 makes the panel crop an explicit, auditable boundary. A reference scene
  cannot be rendered from a full strip after this task unless it is a legacy
  `profile=None` scene.

**Interfaces produced and consumed:**

    from collections.abc import Mapping
    from typing import Any
    from PIL import Image
    from app.services import storage, visual_scoring

    # Add these fields to the existing app/services/timeline.py::SceneSpec
    panel_region_id: str | None = None
    panel_id: str = ""
    panel_bounds: tuple[int, int, int, int] | None = None
    visual_evidence: Mapping[str, Any] | None = None
    source_asset_checksum: str = ""

    # Add the same fields to app/services/render.py::SceneInput
    panel_region_id: str | None = None
    panel_id: str = ""
    panel_bounds: tuple[int, int, int, int] | None = None
    visual_evidence: Mapping[str, Any] | None = None
    source_asset_checksum: str = ""

    # app/services/pipeline.py, private boundary used by build_timeline
    def _bind_reference_panel_regions(
        db: Session,
        project_id: str,
        script: ScriptVersion,
        images: Sequence[SourceAsset],
        planned: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return planned shots with a real cited PanelRegion snapshot."""

    def _validated_visual_snapshot(region: PanelRegion) -> dict[str, Any]:
        """Return the canonical nested visual_evidence for one region."""

    # app/services/pipeline.py, private boundary used by build_render_request
    def _materialize_reference_panel_crop(
        db: Session,
        asset: SourceAsset,
        scene: TimelineScene,
        destination: Path,
    ) -> Path:
        """Validate the snapshot and write one deterministic panel crop."""

    def _scene_panel_bounds(scene: TimelineScene) -> tuple[int, int, int, int]:
        """Parse panel_bounds_json as global x0, y0, x1, y1 coordinates."""

The helper names above are the planned private boundaries. If the live code
already has an equivalent helper, extend that helper instead of adding a
second path. The public `build_timeline(db, project_id, actor_id="")` and
`build_render_request(db, job)` signatures remain unchanged.

**Timeline snapshot fields:**

Add additive, old-row-compatible fields to `TimelineScene`:

    panel_region_id: Mapped[str | None] = mapped_column(
        ForeignKey("panel_regions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    panel_id: Mapped[str] = mapped_column(String(80), default="")
    panel_bounds_json: Mapped[dict] = mapped_column(JSON, default=dict)
    visual_evidence_json: Mapped[dict] = mapped_column(JSON, default=dict)
    source_asset_checksum: Mapped[str] = mapped_column(String(64), default="")

If live SQLite/Alembic inspection proves an FK cannot be added safely to the
existing table, use a nullable stable `panel_region_id` string with the same
audited value and document that choice in STATUS; never drop or rewrite old
scene rows. In either representation, the JSON snapshot remains authoritative
for stale-lineage detection after a PanelRegion row is removed.

**Reference binding invariants:**

- Read the latest StoryAnalysis PanelRegion rows ordered by
  `(source_order, panel_id, id)` and the current ScriptVersion sections.
- For each reference shot, use the section's `evidence_panel_ids` first. If
  that list is absent, use integer `citations` only as `source_order` values;
  never interpret an integer citation as a SourceAsset ID. Resolve each cited
  order through the current PanelRegion rows.
- Require the chosen `region.source_asset_id == shot["asset_id"]`. A region from
  another asset, an unknown panel ID, an invalid source-order citation, or a
  missing current analysis raises `PipelineError` with stable code
  `visual.panel_lineage_unavailable` before any existing TimelineScene or
  SubtitleCue row is deleted.
- When more than one exact cited PanelRegion belongs to the shot asset, select
  by deterministic ordered cycling within `(section, asset_id)` using
  `(source_order, panel_id, id)`; never use list position from an unrelated
  collection, a filename, or random sampling. Preserve the selected panel ID
  and region ID on the shot.
- Persist integer bounds in `panel_bounds_json` as
  `{ "x": x, "y": y, "width": width, "height": height }`, where the values
  are the same global source coordinates accepted by `_panel_region_bounds`.
  Require positive integers and bounds inside the current SourceAsset's full
  dimensions.
- Persist `source_asset_checksum` as
  `asset.original_checksum or asset.checksum`, and require the PanelRegion
  checksum (when present) to agree. Persist the canonical parsed
  `visual_evidence_json` including its existing evidence hash. Unknown remains
  unknown; this task never creates `known_empty`, removes balloon regions, or
  trusts a provider-supplied hash.

**Reference render invariants:**

- In `build_render_request`, resolve the selected profile from the project. For
  reference mode, every scene must have a nonempty panel region ID, panel ID,
  bounds, visual evidence snapshot, and source checksum. A missing or malformed
  snapshot, a current asset checksum mismatch, a current PanelRegion identity
  mismatch, or a crop outside the asset fails with
  `PipelineError("visual.panel_lineage_unavailable")` and a safe finding.
- Structurally parse the snapshot through the Task 1 visual evidence parser.
  Unknown is allowed to pass this structural boundary and is handed to Task 5;
  `require_reference_ready_visual_evidence` is not called here.
- Read the full SourceAsset only to materialize a crop. Translate the persisted
  global bounds directly into the full asset image coordinate space, verify the
  cropped dimensions and a deterministic content checksum, save an internal
  PNG below the job's existing project-scoped render workspace, and set
  `SceneInput.image_path` to that crop. Use an internal numeric scene index for
  the filename; never interpolate panel IDs or user filenames into a path.
- Carry `panel_region_id`, `panel_id`, integer `panel_bounds`, parsed
  `visual_evidence`, and `source_asset_checksum` on SceneInput for Tasks 5–7.
  Do not crop per camera frame, mutate the SourceAsset, load a hidden sidecar,
  or infer lineage from filenames.
- In legacy/profile=None mode, retain the current full SourceAsset path,
  SceneInput fields/defaults, render bytes, and preview behavior. No panel
  snapshot is required for legacy scenes.

- [ ] **Step 1: Add collection-safe body-failing RED tests**

Add `tests/test_panel_lineage_render.py` using existing SQLite/session fixtures,
PIL temporary images, and real `SourceAsset`, `StoryAnalysis`, `PanelRegion`,
`ScriptVersion`, and `TimelineScene` models. Do not import a future symbol at
module import time. Probe new fields/helpers inside test bodies so RED is caused
by missing lineage behavior, not collection failure.

The first RED tests must prove:

    def test_reference_scene_requires_panel_snapshot_fields():
        scene = TimelineScene(project_id="p", asset_id="asset-1")
        assert getattr(scene, "panel_region_id", None) == "region-1"

    def test_reference_binding_does_not_treat_integer_citation_as_asset_id():
        # A section citation of 3 must resolve a PanelRegion.source_order == 3;
        # it must not look up SourceAsset.id == "3".
        result = build_reference_timeline_fixture(citation=3, asset_id="asset-3")
        assert result[0].panel_id == "panel-source-order-3"
        assert result[0].asset_id == "asset-3"

    def test_reference_render_materializes_exact_panel_bounds(tmp_path):
        request = build_reference_render_request_with_two_regions(tmp_path)
        scene_input = request.scenes[0]
        assert scene_input.panel_id == "panel-b"
        with Image.open(scene_input.image_path) as crop:
            assert crop.size == (80, 120)
            assert crop.getpixel((0, 0)) == (40, 80, 120)

Also add body-failing cases for a foreign region/asset, stale source checksum,
missing or malformed snapshot, unknown preservation, and unchanged legacy
profile=None request construction. The migration tests must collect against the
current schema and fail in assertions for absent columns/upgrade behavior.

Run the exact RED command before production edits:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_panel_lineage_render.py tests/test_vision_migration.py -q

Expected RED: collection succeeds; failures identify absent TimelineScene/
SceneSpec/SceneInput lineage fields, missing reference binding/crop validation,
or missing migration columns. No ImportError, fixture setup error, audio call,
provider call, or database outside the isolated test database is acceptable.

- [ ] **Step 2: Add the additive model and live-head migration**

Extend `TimelineScene` with the five snapshot fields above using safe defaults
for existing rows. Inspect the live Alembic head again, create exactly one new
revision whose `down_revision` is the observed
`a4p0_editorial_voice_visual_contract` (or the newly observed head if the
implementation starts after another published commit), and add only the five
timeline columns/index/FK required by the model. The upgrade must preserve old
rows with null/empty snapshot values; the downgrade must remove only this
revision's objects and leave prior motion/alignment columns intact.

Use the repository's existing migration style, for example:

    def upgrade() -> None:
        with op.batch_alter_table("timeline_scenes") as batch_op:
            batch_op.add_column(sa.Column("panel_region_id", sa.String(length=32), nullable=True))
            batch_op.add_column(sa.Column("panel_id", sa.String(length=80), nullable=False, server_default=""))
            batch_op.add_column(sa.Column("panel_bounds_json", sa.JSON(), nullable=False, server_default="{}"))
            batch_op.add_column(sa.Column("visual_evidence_json", sa.JSON(), nullable=False, server_default="{}"))
            batch_op.add_column(sa.Column("source_asset_checksum", sa.String(length=64), nullable=False, server_default=""))
        op.create_index("ix_timeline_scenes_panel_region_id", "timeline_scenes", ["panel_region_id"])

Do not copy this snippet without checking the live migration dialect and FK
conventions. The implementation must use the actual generated revision path and
must not edit an older revision. Add the same additive definitions to the
SQLite-only compatibility section of `app/db.py::init_db()` so a legacy local
database opened without Alembic gains the fields safely. No destructive reset,
autoupgrade of user data, or raw data rewrite is permitted.

- [ ] **Step 3: Carry cited PanelRegion lineage into reference SceneSpec rows**

Add these fields to the existing `SceneSpec` dataclass without changing the
ordering or defaults of legacy constructor arguments:

    panel_region_id: str | None = None
    panel_id: str = ""
    panel_bounds: tuple[int, int, int, int] | None = None
    visual_evidence: Mapping[str, Any] | None = None
    source_asset_checksum: str = ""

Implement `_bind_reference_panel_regions` at the pipeline boundary described
above. It must load the latest analysis and its PanelRegion rows, construct a
section-to-panel evidence map from `script.sections`, and return a copy of each
planned shot enriched with the selected region snapshot. The algorithm is:

    section_panel_ids: dict[str, tuple[str, ...]] = {}
    section_source_orders: dict[str, tuple[int, ...]] = {}
    for section in script.sections or []:
        name = str(section.get("section", ""))
        section_panel_ids[name] = tuple(
            str(panel_id) for panel_id in section.get("evidence_panel_ids", []) or []
        )
        section_source_orders[name] = tuple(
            citation for citation in section.get("citations", []) or []
            if isinstance(citation, int) and not isinstance(citation, bool)
        )
    regions = sorted(
        current_regions,
        key=lambda row: (row.source_order, row.panel_id, row.id),
    )
    by_panel_id = {row.panel_id: row for row in regions if row.panel_id}
    by_source_order = {row.source_order: row for row in regions}
    assets_by_id = {asset.id: asset for asset in images}
    cursors: dict[tuple[str, str], int] = {}
    bound: list[dict[str, Any]] = []
    for shot in planned:
        cited_ids = section_panel_ids[shot["section"]]
        candidates = [
            by_panel_id[panel_id]
            for panel_id in cited_ids
            if panel_id in by_panel_id
            and by_panel_id[panel_id].source_asset_id == shot["asset_id"]
        ]
        if not candidates:
            candidates = [
                by_source_order[citation]
                for citation in section_source_orders[shot["section"]]
                if citation in by_source_order
                and by_source_order[citation].source_asset_id == shot["asset_id"]
            ]
        if not candidates:
            raise PipelineError("visual.panel_lineage_unavailable")
        asset = assets_by_id.get(str(shot["asset_id"]))
        if asset is None:
            raise PipelineError("visual.panel_lineage_unavailable")
        key = (str(shot["section"]), str(shot["asset_id"]))
        index = cursors.get(key, 0)
        region = sorted(candidates, key=lambda row: (row.source_order, row.panel_id, row.id))[index % len(candidates)]
        cursors[key] = index + 1
        checksum = asset.original_checksum or asset.checksum
        if region.source_asset_checksum and region.source_asset_checksum != checksum:
            raise PipelineError("visual.panel_lineage_unavailable")
        bound.append({
            **shot,
            "panel_region_id": region.id,
            "panel_id": region.panel_id,
            "panel_bounds": _panel_region_bounds(region),
            "visual_evidence": _validated_visual_snapshot(region),
            "source_asset_checksum": checksum,
        })
    return bound

The implementation must not use a dict insertion order as an implicit
selection rule, and it must reject duplicate/ambiguous panel IDs rather than
silently selecting an unrelated row. `_validated_visual_snapshot` uses the
Task 1 structural parser and canonical serializer. It checks that the nested
visual evidence lineage matches `region.panel_id`, `region.source_asset_id`,
and `region.source_order`, while preserving unknown and its nonempty reason.

Its implementation uses the live Task 1 names and does not invent a second
parser:

    def _validated_visual_snapshot(region: PanelRegion) -> dict[str, Any]:
        if not isinstance(region.observation_json, Mapping):
            raise PipelineError("visual.panel_lineage_unavailable")
        raw = region.observation_json.get("visual_evidence")
        if not isinstance(raw, Mapping):
            raise PipelineError("visual.panel_lineage_unavailable")
        evidence = visual_scoring.parse_panel_visual_evidence(raw)
        if (
            evidence.panel_id != region.panel_id
            or evidence.source_asset_id != region.source_asset_id
            or evidence.source_order != region.source_order
        ):
            raise PipelineError("visual.panel_lineage_unavailable")
        visual_scoring.validate_panel_visual_evidence(evidence)
        return visual_scoring.panel_visual_evidence_json(evidence)

Call this binding after `editorial_visual_planner.plan(...)` succeeds and before
the current deletion loop for old TimelineScene/SubtitleCue rows. Populate the
new SceneSpec fields from the bound shot. Then instantiate TimelineScene with
the panel region ID, panel ID, integer bounds, canonical visual evidence, and
immutable source checksum. A binding error must leave existing rows untouched
and surface the stable `visual.panel_lineage_unavailable` code.

- [ ] **Step 4: Materialize and verify the evidence-aligned render crop**

Add the same five lineage fields to `SceneInput`. In reference mode,
`build_render_request` must validate every persisted scene before creating its
render request. Use the current SourceAsset storage path and the snapshot's
integer bounds; do not reuse the full strip after selecting a reference scene.

The core crop operation is deterministic and must have this shape after live
imports are adjusted to repository style:

    def _materialize_reference_panel_crop(
        db: Session,
        asset: SourceAsset,
        scene: TimelineScene,
        destination: Path,
    ) -> Path:
        expected = _scene_panel_bounds(scene)
        checksum = asset.original_checksum or asset.checksum
        if not scene.panel_region_id or not scene.panel_id or not scene.visual_evidence_json:
            raise PipelineError("visual.panel_lineage_unavailable")
        if scene.source_asset_checksum != checksum:
            raise PipelineError("visual.panel_lineage_unavailable")
        region = db.get(PanelRegion, scene.panel_region_id)
        if region is None or region.panel_id != scene.panel_id or region.source_asset_id != asset.id:
            raise PipelineError("visual.panel_lineage_unavailable")
        if region.source_asset_checksum and region.source_asset_checksum != checksum:
            raise PipelineError("visual.panel_lineage_unavailable")
        region_bounds = _panel_region_bounds(region)
        if region_bounds != expected:
            raise PipelineError("visual.panel_lineage_unavailable")
        evidence = visual_scoring.parse_panel_visual_evidence(scene.visual_evidence_json)
        if (
            evidence.panel_id != region.panel_id
            or evidence.source_asset_id != region.source_asset_id
            or evidence.source_order != region.source_order
            or visual_scoring.panel_visual_evidence_json(evidence) != scene.visual_evidence_json
        ):
            raise PipelineError("visual.panel_lineage_unavailable")
        with Image.open(storage.path_for(asset.storage_key)) as image:
            image.load()
            x0, y0, x1, y1 = expected
            if x0 < 0 or y0 < 0 or x1 > image.width or y1 > image.height:
                raise PipelineError("visual.panel_lineage_unavailable")
            crop = image.convert("RGB").crop((x0, y0, x1, y1))
            if crop.size != (x1 - x0, y1 - y0):
                raise PipelineError("visual.panel_lineage_unavailable")
            destination.parent.mkdir(parents=True, exist_ok=True)
            crop.save(destination, format="PNG")
        return destination

The exact helper may be split into validation and materialization to match
current pipeline style, but the public behavior is fixed: current asset ID,
checksum, panel ID, region ID, bounds, and canonical evidence must all agree.
The safe destination is the existing project/job render workspace plus a
numeric scene index. The helper must not include untrusted panel IDs,
filenames, or storage keys in a filesystem path.

Set `SceneInput.image_path` to this crop and copy the validated lineage fields
onto SceneInput. Keep the `RenderRequest.profile` and all motion fields intact;
Task 5 will consume the panel-sized image and evidence. A structurally valid
unknown mask is carried forward without changing its state. For profile=None,
retain the current `storage.path_for(asset.storage_key)` assignment and do not
create a crop or parse visual evidence.

- [ ] **Step 5: Complete the focused lineage and migration GREEN matrix**

Extend `tests/test_panel_lineage_render.py` with these independent assertions:

- Two cited PanelRegions on one SourceAsset bind to the cited panel, not the
  first or random row; repeated shots cycle in sorted source_order/panel_id
  order and remain deterministic across two sessions.
- A panel whose source asset differs from `shot["asset_id"]`, a foreign panel
  ID, a stale source checksum, a missing snapshot, malformed JSON, or bounds
  outside the asset raises `PipelineError` with
  `visual.panel_lineage_unavailable` before old rows are deleted.
- A known visual snapshot and an explicit unknown snapshot round-trip with the
  same canonical JSON/hash; no test accepts `[]` as known_empty.
- The materialized PNG dimensions and corner pixels equal the persisted bounds
  from the full source image, proving visual coordinates now refer to the crop.
- Legacy `profile=None` returns the original full-asset SceneInput and does not
  require panel fields.

Extend `tests/test_vision_migration.py` to run the new migration in an isolated
SQLite database, assert all five columns exist after upgrade, assert a prior
scene retains its old values, and assert downgrade removes only the new
columns. Exercise `app/db.py::init_db()` against a copied legacy schema so its
additive compatibility path is covered without touching a user database.

Run:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_panel_lineage_render.py tests/test_vision_migration.py tests/test_pipeline.py tests/test_vision_pipeline.py -q
    .venv/bin/ruff check app/models.py app/db.py app/services/timeline.py app/services/pipeline.py app/services/render.py tests/test_panel_lineage_render.py tests/test_vision_migration.py tests/test_pipeline.py
    .venv/bin/python -m compileall -q app/models.py app/db.py app/services/timeline.py app/services/pipeline.py app/services/render.py
    git diff --check

Expected GREEN: collection is clean, every focused lineage/migration assertion
passes, legacy pipeline assertions remain green, and no provider/TTS/render
process is started by the unit tests. Record the exact collected/pass count in
STATUS; do not replace a failing lineage assertion with a skip or fixture-only
shortcut.

- [ ] **Step 6: Run full verification, document, commit, and push Task 4**

Before committing, run the focused command above plus:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/python -m pytest tests/test_reference_profile.py tests/test_reference_framing.py tests/test_motion_stability.py tests/test_visual_scoring.py tests/test_quality.py tests/test_vision_adapter.py tests/test_vision_synthesis.py tests/test_resolver_vision.py tests/test_vision_pipeline.py -q
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/python -m pytest -q -m "not slow"
    .venv/bin/python -m compileall -q app
    git diff --check

Audit the staged diff for secrets, storage paths, image bytes, runtime DBs,
and files outside the Task 4 allowlist. Update STATUS and CHANGELOG with the
actual migration filename/revision, focused and full counts, the
`visual.panel_lineage_unavailable` behavior, legacy compatibility, the next
Task 5, and this commit's rollback SHA.

Stage exactly the Task 4 paths, including the actual migration filename
recorded during Step 2:

    migration_path=$(git diff --name-only --diff-filter=A -- alembic/versions)
    test "$(printf '%s\n' "$migration_path" | wc -l)" -eq 1
    git add -- app/models.py app/db.py "$migration_path" app/services/timeline.py app/services/pipeline.py app/services/render.py tests/test_vision_migration.py tests/test_panel_lineage_render.py tests/test_pipeline.py docs/STATUS.md CHANGELOG.md
    git diff --cached --check
    git commit -m "feat: preserve panel lineage into reference render"

Push the exact commit immediately through the clean Windows transport clone.
Verify GitHub `main` still equals the VPS parent, import the exact commit,
fast-forward `main:main` only, and verify HTTPS `ls-remote` equals the new VPS
SHA. Record the full SHA and leave both VPS and the transport worktree clean.
Rollback is this reviewed commit; do not reset or rewrite history.

## Task 5: Rank feasible crop candidates with hard balloon exclusion

**Files:**
- Modify: app/services/framing_analysis.py beside BorderMaskResult and the detector.
- Modify: app/services/reference_profile.py.
- Modify: app/services/render.py.
- Modify: tests/test_reference_framing.py.
- Modify: docs/STATUS.md.
- Modify: CHANGELOG.md.

**Interfaces:**
- **Consumes:** the Task 4 `SceneInput.image_path` panel crop, its
  `panel_id`/`panel_region_id`/`panel_bounds`, parsed `visual_evidence`,
  `source_asset_checksum`, `framing_analysis.BorderMaskResult`, current focus
  inputs, and `ReferenceProfileConfig`.
- **Produces:** `framing_analysis.FramingTelemetry`, `framing_analysis.candidate_is_feasible(...) -> tuple[bool, FramingTelemetry]`, and `render.PreparedFrame.telemetry`, while retaining the existing path, crop_box, blank_fraction, and base_zoom fields.
- **Import boundary:** `render.py` imports `framing_analysis`; `framing_analysis.py` imports Task 1 visual evidence serializers/validators but never imports render.py, preventing a circular dependency.

The Task 5 preparation signature may gain only optional keyword inputs so old
callers remain compatible:

    def prepare_reference_frame(
        src: Path,
        dest: Path,
        width: int,
        height: int,
        focus_x: float,
        focus_y: float,
        profile: ReferenceProfileConfig | None,
        *,
        evidence: PanelVisualEvidence | None = None,
        border_mask: BorderMaskResult | None = None,
    ) -> PreparedFrame:

When `profile` is active, `src` is the Task 4 materialized panel crop and the
evidence coordinates are in that crop's coordinate space. Passing a full source
strip with panel-normalized evidence is a stable
`visual.panel_lineage_unavailable` error, not a candidate to score.

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

Before detecting masks or ranking a crop, call
`validate_panel_visual_evidence` at the public
`build_color_agnostic_border_mask` boundary, then call
`require_reference_ready_visual_evidence`; it rejects unknown mask status with
`visual.balloon_mask_unknown`. If all candidates meet hard requirements but
edge blank is above zero, select the deterministic lowest-blank candidate and
set fallback_reason to visual.blank_infeasible. If no candidate meets a hard
requirement, raise RenderError with the stable rejection code and telemetry
for the last ordered fallback attempt.

Keep the static chosen box in the cache. Do not scan content per camera frame.
The detector and candidate helper consume the Task 4 panel crop and evidence
snapshot; they never apply panel-normalized geometry to a full SourceAsset
strip. A malformed or missing Task 4 snapshot is
`visual.panel_lineage_unavailable`; an unknown but structurally valid snapshot
is `visual.balloon_mask_unknown` at this consumer boundary.

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

Update STATUS/CHANGELOG with profile hash, telemetry examples, and Task 6.
Stage only the six owned paths and commit:

    git add -- app/services/framing_analysis.py app/services/reference_profile.py app/services/render.py tests/test_reference_framing.py docs/STATUS.md CHANGELOG.md
    git diff --cached --check
    git commit -m "feat: rank balloon free reference crop candidates"

Push immediately by exact-history fast-forward and record rollback SHA.

## Task 6: Apply exact panel/beat fallback plus reference QC

This is a standalone planner and quality slice. It begins after the published
Task 5 contract at amendment HEAD 241e1ff4f61e71238cf59cf842a1c71c7fc2184a.
It does not wire live pipeline candidate construction; that responsibility belongs
to Task 7. The current live planner was verified at the baseline: plan(...) returns
list[dict], and the profile=None branch must remain that same public return and
behavior.

**Files:**

- Modify: app/services/editorial_visual_planner.py.
- Modify: app/services/editorial_qc.py.
- Modify: app/services/quality.py.
- Modify: tests/test_reference_profile_integration.py.
- Modify: docs/STATUS.md.
- Modify: CHANGELOG.md.

These six paths are the smallest coherent Task 6 boundary. Task 6 must not
modify pipeline.py, render.py, database models, migrations, voice, narration, or
media. Task 7 owns live construction and persistence of the candidates defined
here.

### Exact panel-keyed interface

The current visual_scoring.PanelCandidate is asset-level render scoring data:
it carries asset_id, order_index, features, visual_score, semantic_score, and
source_family. It is not sufficient to carry panel-relative visual evidence.
Task 6 therefore adds a frozen panel wrapper in
app/services/editorial_visual_planner.py. The wrapper is allowed to contain
one or more PanelCandidate values for the same SourceAsset, but every wrapper
is one exact PanelRegion.

    @dataclass(frozen=True)
    class ReferenceROIAlternative:
        kind: str
        roi_label: str
        crop_box: tuple[int, int, int, int]
        focus: tuple[float, float, float, float]

    @dataclass(frozen=True)
    class ReferencePanelFallbackCandidate:
        source_asset_id: str
        panel_region_id: str
        panel_id: str
        source_order: int
        panel_bounds: tuple[int, int, int, int]
        panel_size: tuple[int, int]
        border_mask: framing_analysis.BorderMaskResult
        source_asset_checksum: str
        visual_evidence: PanelVisualEvidence
        evidence_hash: str
        eligible_sections: tuple[str, ...]
        eligible_beats: tuple[str, ...]
        roi_alternatives: tuple[ReferenceROIAlternative, ...]
        panel_candidate: visual_scoring.PanelCandidate

The real implementation must validate nonempty IDs, positive integer source
order and bounds, a required positive panel-crop coordinate box
(x0, y0, x1, y1) for every ROI alternative with
0 <= x0 < x1 <= panel_size[0] and 0 <= y0 < y1 <= panel_size[1], and panel_size
equal to the exact materialized crop dimensions. The
border_mask must have the same source dimensions as panel_size, a supported
detector/profile contract, and a locally recomputed mask identity containing
detector_version, source dimensions, canonical masks, and mask_sha256. The
source asset checksum and locally recomputed canonical visual evidence hash are
also required. A provider-supplied hash is untrusted: the local Task 1
serializer is authoritative. The wrapper's visual_evidence must be the exact
typed evidence for panel_id/panel_region_id, not evidence copied from another
PanelRegion on the same asset.

The profile-aware planner extends the live signature with one optional,
panel-keyed sequence after the existing citation arguments:

    def plan(
        spans: Iterable[object],
        candidates: list[object],
        profile: object | None = None,
        cited_asset_ids_by_section: Mapping[str, Iterable[str]] | None = None,
        citation_alignment_reasons_by_section: Mapping[str, Iterable[str]] | None = None,
        reference_panel_candidates: Sequence[ReferencePanelFallbackCandidate] | None = None,
    ) -> list[dict]:

When profile is None, the new argument is ignored and the existing list[dict]
legacy result is returned without changing ordering, serialization, motion, or
fallback behavior. When the reference profile is active, candidates are sorted
deterministically by source_order, panel_id, panel_region_id, and the planner
must consume the exact panel-keyed sequence; a one-value-per-asset evidence map
is not a valid input. Multiple panels with the same source_asset_id remain
distinct candidates.

### Shot records and fallback ledger

Every reference shot dictionary returned by the planner carries the selected
panel lineage directly:

    {
        "asset_id": str,
        "panel_region_id": str,
        "panel_id": str,
        "source_order": int,
        "source_asset_checksum": str,
        "panel_bounds": [int, int, int, int],
        "panel_size": [int, int],
        "border_mask": dict,
        "visual_evidence": dict,
        "evidence_hash": str,
        "section": str,
        "beat": str,
        "roi": dict,
        "alignment_reasons": list[str],
        "fallback_attempts": list[dict],
    }

Each fallback_attempts entry is an ordered immutable-at-write audit record with
attempt_order, panel_region_id, panel_id, source_asset_checksum, source_order,
beat, panel_size, roi/crop box, evidence_hash, detector_version, mask_sha256,
telemetry, kind, accepted, stable rejection/reason code, and a short reason.
A rejected attempt remains tied to the exact panel and mask it evaluated.

A reference shot uses this exact fallback order:

1. alternate safe ROI on the same exact PanelRegion;
2. a tighter feasible crop on that same exact PanelRegion;
3. another exact eligible PanelRegion in the same story beat/section
   progression, selected by deterministic source_order/panel_id/region ordering;
4. stable visual.visual_unavailable rejection when no exact panel candidate is
   feasible.

The planner must never transfer evidence from one panel to another, fall back by
filename or random choice, or call an arbitrary first panel for an asset.
Unknown visual geometry is never relabeled known_empty, and balloon overlap is
never relabeled safe. An alternate panel is eligible only when its own typed
evidence, checksum, bounds, and beat/section eligibility are validated.

For every fallback attempt, the planner calls the existing Task 5 boundary
with the exact typed values; it never reads an image, recomputes a mask, or
accepts a predeclared safe boolean:

    framing_analysis.candidate_is_feasible(
        roi.crop_box,
        candidate.visual_evidence,
        candidate.border_mask,
        candidate.panel_size,
        (profile.final_width, profile.final_height),
    )

The consumer sequence is structural lineage validation,
require_reference_ready_visual_evidence, this exact feasibility call, and only
then ranking/fallback. Failures are ordered as
visual.panel_lineage_unavailable, visual.balloon_mask_unknown,
visual.balloon_overlap, visual.protected_coverage, visual.blank_infeasible,
or visual.visual_unavailable as applicable. The Task 5 hard constraints remain
unchanged: zero balloon intersection, subject/face at least .98,
action/continuity at least .95, effect at least .90, source-resolution guard,
and deterministic telemetry. The ledger records the returned telemetry and
rejection code together with detector_version, mask_sha256, panel_size,
crop_box, and evidence_hash.

### Panel-exact QC boundary

Add a reference-only quality boundary that receives exact scene lineage rather
than an asset-level evidence map:

    def check_reference_framing(
        scenes: Sequence[Mapping[str, object]],
        panel_evidence_by_key: Mapping[tuple[str, str], PanelVisualEvidence],
        panel_border_masks_by_key: Mapping[
            tuple[str, str], framing_analysis.BorderMaskResult
        ],
        panel_sizes_by_key: Mapping[tuple[str, str], tuple[int, int]],
        telemetry_by_key: Mapping[
            tuple[str, str], FramingTelemetry | None
        ],
        *,
        profile: object,
    ) -> list[CheckResult]:

The key is (source_asset_id, panel_region_id); panel_id, checksum, bounds,
visual_evidence, panel_size, border_mask detector_version/mask_sha256, and
telemetry must agree with the scene snapshot and Task 4 crop. A missing,
foreign, stale, dimension-mismatched, hash-mismatched, or ambiguous key emits
visual.panel_lineage_unavailable before checking balloon readiness. A
structurally valid unknown evidence state emits visual.balloon_mask_unknown. Known geometry then checks zero balloon overlap,
protected coverage, edge-connected blank telemetry, crop zoom/source quality,
monotonic motion, and the existing reference pacing/reuse rules. The same
stable visual.* codes are exposed through editorial_qc.build_report and
quality.py without changing profile=None behavior.

This boundary must not inspect only scene.asset_id or a visual_evidence_by_asset
value. If the historical term visual_evidence_by_asset is mentioned in a
review fixture, it means the rejected design and must not be implemented.

### Dependency graph

Task 4 exact panel snapshot and crop
  -> Task 5 detector, readiness, candidate feasibility, and telemetry
  -> Task 6 ReferencePanelFallbackCandidate sequence
  -> Task 6 planner fallback ledger and panel-exact QC
  -> Task 7 pipeline candidate construction and exact timeline binding.

### TDD and implementation steps

- [ ] Add body-level tests in tests/test_reference_profile_integration.py for two
      PanelRegions with distinct evidence and geometry under one SourceAsset.
      The current planner must fail because the frozen panel candidate type,
      panel-keyed argument, exact fallback ledger, and panel-exact QC boundary
      do not yet exist. Keep imports collection-safe by probing new symbols in
      test bodies. RED command:

      PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest
      tests/test_reference_profile_integration.py -q

      Expected RED is collection-clean and body failures only; no provider,
      fixture, database, or media setup failure is acceptable.

- [ ] Add negative body-level tests for mismatched mask dimensions, mask
      SHA, detector/profile contract, and a predeclared-feasibility boolean.
      Add a two-panel same-asset fixture with distinct BorderMaskResult
      identities and assert every feasibility call receives the selected
      panel's own mask, panel_size, and final profile target size. These tests
      must fail closed before ranking; they must not read or synthesize image
      data in the planner.

- [ ] Implement ReferenceROIAlternative and
      ReferencePanelFallbackCandidate with frozen dataclass validation,
      canonical local hash verification, deterministic ordering, and exact
      panel lineage. Reuse PanelVisualEvidence validators from
      visual_scoring.py and framing_analysis.py; do not duplicate structural
      parsing. Run the focused integration test and confirm that known_empty,
      known_nonempty, and unknown remain distinct.

- [ ] Replace asset-level reference evidence selection with the panel-keyed
      sequence on the profile branch. Require exact panel/asset/checksum/bounds
      agreement before readiness or candidate ranking. Keep the legacy branch
      byte/behavior compatible and leave its list[dict] return unchanged.

- [ ] Implement the four-step fallback ledger. Each attempt must record the
      exact panel and its own evidence hash. Same-panel alternatives precede
      same-beat alternate panels; a final rejection carries
      visual.visual_unavailable. Tests must show a two-panel same-asset case
      never evaluates panel A's evidence while rendering panel B.

- [ ] Add deterministic panel-exact check_reference_framing integration in
      editorial_qc.py and quality.py. Test missing/foreign/stale lineage first,
      then unknown, overlap, blank, protected coverage, motion, pacing, and
      reuse. Add a profile=None regression proving existing report inputs and
      serialized output are unchanged.

- [ ] Run the Task 6 focused matrix:

      PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest
      tests/test_reference_profile_integration.py
      tests/test_reference_framing.py
      tests/test_motion_stability.py
      tests/test_visual_scoring.py
      tests/test_quality.py -q

      .venv/bin/ruff check app/services/editorial_visual_planner.py
      app/services/editorial_qc.py app/services/quality.py
      tests/test_reference_profile_integration.py

      .venv/bin/python -m compileall -q app/services/editorial_visual_planner.py
      app/services/editorial_qc.py app/services/quality.py

      .venv/bin/git diff --check

- [ ] Update docs/STATUS.md and CHANGELOG.md with the exact RED/GREEN totals,
      panel-keyed candidate and ledger behavior, stable error precedence,
      legacy compatibility, and the next Task 7 checkpoint. Include the
      amendment parent and a rollback SHA for this slice.

- [ ] Run the full non-slow suite before commit:

      PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/python -m pytest
      -q -m "not slow"

      Fix only Task 6 regressions; do not weaken or skip old tests. Confirm no
      media, database, credential, or runtime artifact is in the diff.

- [ ] Self-review exact six-path allowlist, panel lineage keys, no random calls,
      no asset-level evidence fallback, no result.fallback_attempts claim, no
      placeholder markers, and no secret-shaped values. Commit on VPS:

      feat: gate balloon free reference panel fallback

      Push that exact commit immediately through a clean Windows transport clone
      with main-only fast-forward, then verify VPS/GitHub parity and clean state.
      The Task 6 rollback point is the published amendment parent
      241e1ff4f61e71238cf59cf842a1c71c7fc2184a. The next slice is Task 7
      pipeline candidate construction and silent review.

## Task 7: Integrate the isolated real-panel silent review render

Task 7 starts only after Task 6's panel-keyed planner and QC are green. It
remains a standalone pipeline/render slice: no voice, narration generation,
audio provider, or publication approval. The current Task 4 timeline snapshot
and crop are the source of truth for every reference SceneInput.

**Files:**

- Modify: app/services/pipeline.py at reference candidate construction,
  build_timeline, and exact panel binding.
- Modify: app/services/render.py at reference SceneInput/QC sidecars.
- Create: tests/test_reference_visual_review.py.
- Modify: docs/STATUS.md.
- Modify: CHANGELOG.md.

### Candidate construction before planning

Before calling editorial_visual_planner.plan in reference mode, pipeline.py
must build a deterministic sequence of ReferencePanelFallbackCandidate objects
from the latest StoryAnalysis PanelRegion rows and the approved script's
section evidence. The candidate sequence is panel-keyed and can contain
multiple records with the same source_asset_id.

The planned helper has this exact boundary:

    def _build_reference_panel_fallback_candidates(
        *,
        panel_regions: Sequence[PanelRegion],
        panel_candidates: Sequence[visual_scoring.PanelCandidate],
        section_evidence_panel_ids: Mapping[str, Sequence[str]],
        section_citations: Mapping[str, Sequence[int]],
        beats_by_section: Mapping[str, Sequence[str]],
    ) -> tuple[ReferencePanelFallbackCandidate, ...]:

It sorts rows by source_order, panel_id, panel_region_id. For each section,
evidence_panel_ids are matched first by exact panel_id. Integer citations are
only source_order fallbacks; they are never interpreted as SourceAsset IDs.
When a source-order fallback maps more than one real PanelRegion, all exact
lineage candidates remain represented in deterministic order and the alignment
audit records source_order_fallback plus the selected panel ID. A missing
explicit panel ID, foreign source asset, stale checksum, malformed evidence,
or ambiguous binding that cannot be resolved to a real panel fails with
visual.panel_lineage_unavailable. No random sampling, filename matching, or
arbitrary first-panel selection is permitted.

Each candidate carries the actual source_asset_id, panel_region_id, panel_id,
integer panel_bounds, exact panel_size, a BorderMaskResult built from that
exact Task 4 materialized crop before plan(), source_asset_checksum, locally canonicalized
typed visual_evidence/evidence_hash, eligible beat/section metadata, and its
exact ROI alternatives. The pipeline must require source_asset_id equality
between the render PanelCandidate and the PanelRegion before yielding the
wrapper. Two PanelRegions from one SourceAsset receive separate masks and
separate mask_sha256 identities; a mask is never reused across panel crops.

### Planner output and exact Task 4 binding

Call the profile planner with reference_panel_candidates equal to that exact
sequence. The planner-selected shot already contains panel_region_id,
panel_id, source_order, panel_bounds, panel_size, source checksum, evidence
hash, border-mask identity, and its fallback_attempts ledger. Pipeline code
must not call a different PanelRegion
or recycle an asset-level candidate after planning.

Extend the existing Task 4 _bind_reference_panel_regions boundary to validate
the planner-selected panel_region_id/panel_id/source_asset_id/bounds/checksum
against the same latest PanelRegion row, then persist that exact snapshot.
The selected panel must be present in the section's eligible evidence set.
A missing selected panel lineage raises visual.panel_lineage_unavailable before
prior TimelineScene/SubtitleCue rows are deleted; planning and all lineage
validation complete first. Legacy profile=None keeps current binding and
deletion behavior.

At build_render_request, render.py consumes the persisted exact snapshot:
verify scene asset ID, panel region ID, panel ID, integer bounds, source checksum,
canonical visual evidence/hash, and the source asset record. Materialize the
panel-coordinate crop already guaranteed by Task 4 and pass the exact selected
panel lineage and telemetry into Task 5/6 reference preparation. A selected
panel's evidence is never evaluated against another panel or a full source
strip. Unknown remains structurally preserved until Task 5 readiness.

### QC, silent review, and audit

Render/QC sidecars are keyed by (source_asset_id, panel_region_id) and retain
panel_id, source_order, bounds, panel_size, source checksum,
evidence_hash, detector_version, mask_sha256, crop box, fallback_attempts,
FramingTelemetry, stable reason/rejection code, and publish_allowed. No
sidecar may collapse evidence to an asset-level value.
Task 7 still produces a silent visual review only; no TTS/audio path is called,
and publish_allowed remains false until source rights are verified.

The review test must use at least two PanelRegions under one SourceAsset with
different bounds and evidence, assert that selected and persisted lineage match,
prove integer citation 3 is handled only as source_order, and prove foreign or
stale lineage fails closed. It must also assert mismatched mask dimensions,
mask SHA, and detector/profile contract fail closed; two same-asset panels use
different masks; no predeclared feasibility boolean bypasses the exact
candidate_is_feasible call; legacy profile=None list/SceneInput
behavior, exact crop dimensions/pixels, deterministic repeated-panel reuse, no
consecutive same-panel reuse, no random calls, and the complete 32-shot/order
and coverage audit.

### TDD and implementation steps

- [ ] Add collection-safe body-failing tests/test_reference_visual_review.py
      for candidate construction before calling the planner, two distinct
      PanelRegions under one SourceAsset, exact selected-panel persistence,
      stale/foreign mismatch, and the silent RenderRequest boundary. The
      current pipeline has no panel-keyed candidate builder and cannot prove
      the required failures. RED command:

      PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest
      tests/test_reference_visual_review.py -q

      Expected RED is collection-clean body failures only; no media, provider,
      database, or setup error.

- [ ] Implement the deterministic candidate builder using latest StoryAnalysis
      PanelRegion rows. Preserve every exact evidence_panel_ids match; use
      citations as source_order only; attach alignment reasons for cited,
      source_order fallback, unavailable, or context fallback. No asset-level
      evidence map is allowed.

- [ ] Pass candidates before editorial_visual_planner.plan and validate the
      selected panel in _bind_reference_panel_regions. Persist exact Task 4
      snapshot fields and leave prior scene/cue rows untouched until planning
      succeeds. Fail closed with visual.panel_lineage_unavailable for missing,
      foreign, stale, or mismatched selections.

- [ ] Thread selected panel lineage through build_render_request/render.py
      sidecars and Task 5/6 QC. Assert panel-relative evidence is consumed only
      with the materialized panel crop. Keep legacy profile=None full-asset
      behavior byte/behavior compatible.

- [ ] Run the silent review verification:

      PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest
      tests/test_reference_visual_review.py
      tests/test_reference_profile_integration.py
      tests/test_reference_framing.py
      tests/test_motion_stability.py
      tests/test_subtitle_display_contract.py -q

      .venv/bin/ruff check app/services/pipeline.py app/services/render.py
      tests/test_reference_visual_review.py

      .venv/bin/python -m compileall -q app/services/pipeline.py app/services/render.py
      git diff --check

      PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/python -m pytest
      -q -m "not slow"

      ffprobe -v error -show_streams -show_format reference-visual-review.mp4

      The review report must show 32 planned shots, exact panel lineage,
      one-word display cues, hard cuts, monotonic stable motion, no black
      frames, no audio stream, rights-unverified publish_allowed=false, and
      no provider or voice call.

- [ ] Update docs/STATUS.md and CHANGELOG.md with exact RED/GREEN/full totals,
      panel candidate and binding evidence, isolated review paths, rights gate,
      rollback, and the next approved action. Commit only:

      git add -- app/services/pipeline.py app/services/render.py tests/test_reference_visual_review.py docs/STATUS.md CHANGELOG.md
      git diff --cached --check
      git commit -m "test: audit balloon free silent visual review"

      Push the exact object immediately through a clean Windows transport clone,
      main-only fast-forward, no force/tags/all branches, then verify GitHub
      SHA parity and clean VPS/transport state. The Task 7 rollback point is
      the Task 6 commit; voice generation remains deferred.

## Acceptance matrix and rollback

| Approved spec requirement | Plan task and proving assertion |
| --- | --- |
| Typed balloon/background/subject/action/effect records | Task 1 dataclasses, enum validation, JSON round-trip |
| Unknown versus known-empty masks | Task 1 persistence and Task 5 reference-readiness failures |
| Provider acquisition of balloon/protected geometry | Task 2 prompt, adapter, mock, and snapshot tests |
| Color-agnostic white/black/gray/arbitrary/gradient detection | Task 3 PIL fixtures |
| Meaningful light/dark art protection | Task 3 protected-area tests |
| Border flood fill and internal-background distinction | Task 3 mask topology tests |
| Exact source-space area mapping and six-decimal ratios | Task 3 floor-boundary and area-accounting test |
| Internal low-information diagnostic without discard | Task 3 sealed-island mask test |
| Deterministic detector/cache identity | Task 3 mask_sha256 and cache-key test |
| PanelRegion-to-timeline lineage and crop coordinate space | Task 4 cited-panel binding, snapshot, migration, and exact-pixel crop tests |
| Multiple PanelRegions under one SourceAsset remain distinct | Tasks 6-7 exact panel-keyed candidate, fallback, binding, and QC tests |
| Balloon intersection exactly zero | Tasks 5-7 one-pixel and area-overlap failures |
| Subject/action/effect/continuity minimums | Task 5 candidate feasibility |
| Dynamic zoom/upscale guard | Task 5 native-resolution tests |
| Exact fallback order and stable visual_unavailable | Task 6 exact panel-keyed fallback ledger with per-shot attempts |
| No speech_bubble selection or motion | Task 6 typed panel-candidate and exact-panel planner assertions |
| Stable monotonic motion and no shake | Tasks 5-7 120-frame and filter tests |
| Full panel/story/claim coverage and rights gate | Tasks 1, 2, 4, 6, and 7 lineage/rights assertions |
| Legacy profile=None behavior | Tasks 3, 4, and 5 regression snapshots |
| Silent visual review with no voice/audio | Task 7 RenderRequest and FFprobe assertions |
| STATUS/CHANGELOG progress and immediate push | Every task's final step |
| No media/DB/credentials/runtime data in Git | Every task's staged allowlist and secret scan |

Each task is a rollback boundary. The next task starts only after its commit
is pushed and the VPS worktree is clean. A failed task is left uncommitted for
review or reverted by its own reviewed commit; no broad reset or destructive
cleanup is permitted.
