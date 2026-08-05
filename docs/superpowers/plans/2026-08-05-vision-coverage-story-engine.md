# Vision Coverage and Story Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the text-only evidence stage with a fail-closed, vision-first chapter analysis pipeline that accounts for every source-space pixel and every canonical panel before producing a Cinematic Story Detective script.

**Architecture:** Extend ingest lineage and StoryAnalysis persistence, add one PanelRegion persistence boundary, build deterministic source-space segmentation and reconciliation in app/services/segmentation.py, adapt one multimodal provider through app/services/vision_adapter.py, and make app/services/pipeline.py consume only reconciled vision evidence. Rules-based analysis remains available only through an explicitly named legacy workflow and is never a fallback.

**Tech Stack:** Existing Python application, SQLAlchemy/Alembic, Pydantic schemas, Pillow/OpenCV already pinned in the repository, existing HTTP provider abstractions, pytest, Ruff, and current database test fixtures.

## Global Constraints

- Implement only docs/superpowers/specs/2026-08-05-vision-first-editorial-story-engine-design.md.
- Do not add an image or OCR dependency. Do not use random sampling, representative-panel shortcuts, pasted recap text, or silent text-only fallback.
- Preserve source checksum, dimensions, source bounds, strip order, tile order, trim/gutter classification, and coverage-map lineage from ingest through claims.
- Every source-space region must be classified as canonical panel/content, verified gutter/non-story, or unresolved/material. No source pixels or content bands may silently disappear.
- A coverage run cannot become RECONCILED or permit script approval while material area is unresolved, a source asset is missing, an observation is absent, or a claim lacks panel evidence.
- Use test-first vertical slices. Each task has a red command, implementation, green command, and an independently reviewable commit.
- Generated images, audio, video, databases, WAL files, credentials, and runtime user data stay outside Git.
- If a pre-existing file is dirty, inspect and preserve its changes. Never reset, checkout, or overwrite unrelated work.

## Dependencies and Ownership

Plan 1 is the prerequisite for Plans 2, 3, and 4.

- Plan 1 owns app/models.py, one Alembic migration, app/services/strips.py, app/services/ingest.py, app/services/segmentation.py, app/services/vision_adapter.py, app/services/analyzer_contract.py, app/prompts/vision_first_story_analyzer_v1.txt, app/services/resolver.py, the vision branch of app/services/pipeline.py, the evidence-aware script and approval path, app/routers/pipeline.py, app/schemas.py, and Plan 1 tests.
- Plan 2 consumes the persisted analysis and story contracts and owns subtitle and voice-profile files only.
- Plan 3 owns motion planning and motion QC files only.
- Plan 4 owns cross-subsystem gate orchestration, feature flags, UI status, rollout documentation, and integration tests.
- Do not modify Plan 2 or Plan 3 files while executing Plan 1. Plan 4 wires these interfaces after their stop points.

## Stable Interfaces

Create these interfaces before wiring callers. Keep field names stable across persistence, API schemas, sidecars, and tests.

    from dataclasses import dataclass
    from typing import Any, Literal, Mapping, Protocol, Sequence
    from sqlalchemy.orm import Session

    @dataclass(frozen=True)
    class SourceAssetInput:
        source_asset_id: str
        original_checksum: str
        original_width: int
        original_height: int
        source_bounds: tuple[int, int, int, int]
        strip_order: int
        region_order: int
        payload: bytes

    @dataclass(frozen=True)
    class CoverageTile:
        source_asset_id: str
        tile_index: int
        y0: int
        y1: int
        overlap_above: int
        overlap_below: int
        tile_sha256: str

    @dataclass(frozen=True)
    class VisionCapabilityReport:
        provider_type: str
        provider_name: str
        model: str | None
        image_input: bool
        structured_json: bool
        available: bool
        blocking_reason: str | None

    @dataclass(frozen=True)
    class VisionObservationRequest:
        analysis_run_id: str
        instruction_version: str
        instruction_sha256: str
        chunk_index: int
        panels: tuple[Mapping[str, Any], ...]

    class VisionObservationProvider(Protocol):
        def capability(self) -> VisionCapabilityReport: ...
        def observe(self, request: VisionObservationRequest) -> list[Mapping[str, Any]]: ...

    def resolve_vision(
        db: Session,
        workspace_id: str,
    ) -> tuple[VisionObservationProvider | None, VisionCapabilityReport]:
        ...

PanelRegion observations and evidence references use persisted IDs, never image filenames or transient list positions.

### Task 1: Establish failing lineage and no-sampling tests

**Files:**
- Add tests/test_vision_coverage.py.
- Add tests/fixtures/vision_coverage.py.
- Do not change production code until the red assertions are observed.

- [ ] Add a fixture factory with three source strips, distinct checksums, dimensions, source bounds, and ordered content bands.
- [ ] Add an all-panel test that feeds the fixture to the future segmentation boundary and asserts every strip and every ordered content band is represented.
- [ ] Add a lineage assertion for original checksum, dimensions, bounds, strip order, and region order after ingest. It must expose the current independent-slice gap.
- [ ] Add a no-random test by monkeypatching random and asserting analysis planning does not call it.
- [ ] Run:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_vision_coverage.py -q

  Expected RED: tests collect and fail on missing coverage, complete lineage, and vision-planner behavior; there is no collection error.
- [ ] Commit only the tests and fixture:

    git add tests/test_vision_coverage.py tests/fixtures/vision_coverage.py
    git diff --cached --check
    git commit -m "test: define complete vision coverage lineage"

### Task 2: Add the focused persistence boundary and migration

**Files:**
- Modify app/models.py.
- Add alembic/versions/b7c4d8e91f20_add_vision_coverage_boundary.py.
- Add tests/test_vision_migration.py.

Extend StoryAnalysis with nullable fields that keep historical rows non-reconciled:
analysis_run_id, state, provider_type, provider_name, model_name, instruction_version, instruction_sha256, coverage_manifest_json, continuity_ledger_json, evidence_graph_json, story_spine_json, blocking_reasons_json, and reconciliation_json.

Add PanelRegion linked to StoryAnalysis and SourceAsset with:
id, story_analysis_id, source_asset_id, source_asset_checksum, original_width, original_height, strip_region_id, panel_id, source_order, bounds_json, region_class, segmentation_confidence, segmentation_version, coverage_map_hash, observation_json, chunk_index, evidence_refs_json, and the project timestamp field.

Extend SourceAsset with original_checksum, original_width, original_height, source_bounds_json, strip_order, region_order, trim_classification, and coverage_map_hash. Use existing table names, key types, timestamp convention, and cascade policy.

- [ ] Write migration tests first. Upgrade must assert all columns, table constraints, and indexes; insert linked StoryAnalysis, SourceAsset, and PanelRegion rows; read them back. Downgrade must remove only this revision objects.
- [ ] Run:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_vision_migration.py -q

  Expected RED: collection succeeds and fails because the migration and PanelRegion model are absent.
- [ ] Add the single Alembic revision with the actual predecessor revision from the current repository. Add a unique index on PanelRegion(story_analysis_id, source_order), plus source_asset_id and region_class indexes.
- [ ] Preserve old rows as non-reconciled; do not make a historical text analysis appear vision-complete.
- [ ] Run:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_models.py tests/test_vision_migration.py -q

  Expected GREEN: model, upgrade, round-trip, index, and downgrade tests pass.
- [ ] Commit:

    git add app/models.py alembic/versions/b7c4d8e91f20_add_vision_coverage_boundary.py tests/test_vision_migration.py
    git diff --cached --check
    git commit -m "feat: persist vision coverage lineage"

### Task 3: Implement deterministic source-space segmentation and reconciliation

**Files:**
- Add app/services/segmentation.py.
- Modify app/services/strips.py to return lineage metadata without discarding original bounds.
- Modify app/services/ingest.py to persist parent checksum, dimensions, and source bounds for derived assets.
- Add tests/test_segmentation.py.
- Add tests/test_segmentation_reconciliation.py.

Implement these public records and functions:

    @dataclass(frozen=True)
    class CoverageRegion:
        region_id: str
        source_asset_id: str
        source_order: int
        bounds: tuple[int, int, int, int]
        region_class: Literal["canonical_panel", "verified_gutter", "unresolved_material"]
        area: int
        confidence: float
        evidence: str

    @dataclass(frozen=True)
    class CoverageMap:
        version: str
        map_sha256: str
        source_asset_ids: tuple[str, ...]
        tiles: tuple[CoverageTile, ...]
        regions: tuple[CoverageRegion, ...]
        source_content_coverage_ratio: float
        canonical_panel_area: int
        verified_gutter_area: int
        unresolved_material_area: int
        panel_count: int
        reconciliation_errors: tuple[str, ...]

    def plan_overlapping_tiles(
        source_asset_id: str,
        width: int,
        height: int,
        *,
        tile_height: int = 2048,
        overlap: int = 128,
    ) -> tuple[CoverageTile, ...]:
        ...

    def build_complete_coverage_map(
        assets: Sequence[SourceAssetInput],
        *,
        segmentation_version: str,
    ) -> CoverageMap:
        ...

    def verify_segmentation_completeness(
        full_strip_overviews: Mapping[str, Any],
        coverage_map: CoverageMap,
    ) -> tuple[str, ...]:
        ...

    def reconcile_coverage_chain(
        coverage_map: CoverageMap,
        panel_regions: Sequence[Mapping[str, Any]],
        observations: Sequence[Mapping[str, Any]],
        chunks: Sequence[Mapping[str, Any]],
        claims: Sequence[Mapping[str, Any]],
    ) -> tuple[bool, tuple[str, ...]]:
        ...

- [ ] Test invalid dimensions, invalid overlap, and deterministic ranges. For height 5000, tile height 2048, and overlap 128, assert ranges (0, 2048), (1920, 3968), and (3840, 5000).
- [ ] Test that overlapping detections use union area, not summed area.
- [ ] Build the full source-space map before provider resolution. Every region is exactly canonical panel, verified gutter/non-story, or unresolved material.
- [ ] Permit a verified gutter only with a deterministic rule and reason/evidence. Trimming creates a source-space mask and never deletes denominator pixels.
- [ ] Persist tile ranges and overlaps, source-space coverage ratio, canonical panel area, verified gutter area, unresolved material area, panel count, segmentation version, and map hash.
- [ ] Define source_content_coverage_ratio as accounted source-space area divided by original source-space area. A map completes only at exact ratio 1.0 using integer area accounting and unresolved_material_area equal to zero. Report canonical content ratio separately.
- [ ] Compare the full-strip overview with the segmented region mosaic. Flag seam gaps, unexplained bands, top or bottom truncation, and source assets absent from the mosaic. This is a deterministic gate, not a sample.
- [ ] Use canonical JSON with sorted keys and stable separators for hashes. Include source IDs, original checksums, bounds, tile ranges, classes, and segmentation version.
- [ ] Make reconcile_coverage_chain reject missing assets, out-of-order regions, observations without panel IDs, chunks without observation IDs, and claims without panel evidence.
- [ ] Run the red command:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_segmentation.py tests/test_segmentation_reconciliation.py -q

  Expected RED: collection succeeds and assertions fail because the module and functions are absent.
- [ ] Implement the functions and wire strips/ingest to preserve the Task 2 lineage fields.
- [ ] Run:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_segmentation.py tests/test_segmentation_reconciliation.py tests/test_vision_coverage.py -q

  Expected GREEN: tile, union-area, complete-map, seam/top/bottom, no-silent-assignment, lineage, and reconciliation tests pass.
- [ ] Commit:

    git add app/services/segmentation.py app/services/strips.py app/services/ingest.py tests/test_segmentation.py tests/test_segmentation_reconciliation.py
    git diff --cached --check
    git commit -m "feat: map every source region before vision"

### Task 4: Add the vision adapter and explicit capability failures

**Files:**
- Add app/services/vision_adapter.py.
- Add tests/mock_provider.py.
- Add tests/test_vision_adapter.py.
- Add tests/test_vision_capability.py.

Implement:

    class VisionCapabilityError(RuntimeError):
        code = "vision_capability_missing"

    class OpenAICompatibleVisionProvider:
        def __init__(self, *, base_url: str, model: str, api_key: str) -> None: ...
        def capability(self) -> VisionCapabilityReport: ...
        def observe(self, request: VisionObservationRequest) -> list[Mapping[str, Any]]: ...

- [ ] Write tests first using an expanded local mock provider. Verify every ordered panel image, source order, panel ID, instruction version, and instruction digest is in the request.
- [ ] Verify structured observations contain panel_id, visible_facts, dialogue_or_ocr, inferences, uncertainties, entities, state_changes, causal_links, and evidence_refs.
- [ ] Verify a text-only provider reports image_input false, structured_json false, available false, and raises VisionCapabilityError with code vision_capability_missing without calling RulesAnalyzer.
- [ ] Run:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_vision_adapter.py tests/test_vision_capability.py -q

  Expected RED: collection succeeds and adapter, mock request, and explicit failure assertions fail.
- [ ] Implement the minimum OpenAI-compatible image-plus-structured-JSON request through the existing HTTP abstraction. Capability checks happen before network access. Validate every returned panel ID against the request.
- [ ] Never log raw headers, credentials, or provider payload fields that can contain secrets.
- [ ] Run:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_vision_adapter.py tests/test_vision_capability.py -q

  Expected GREEN: mock request, structured response, unsupported-provider, no-network, and no-secret-log tests pass.
- [ ] Commit:

    git add app/services/vision_adapter.py tests/mock_provider.py tests/test_vision_adapter.py tests/test_vision_capability.py
    git diff --cached --check
    git commit -m "feat: add fail-closed vision adapter"

### Task 5: Add the versioned analyzer instruction contract

**Files:**
- Add app/prompts/vision_first_story_analyzer_v1.txt.
- Add app/services/analyzer_contract.py.
- Add tests/test_analyzer_contract.py.
- Add tests/fixtures/vision_prompt_snapshot.sha256.

The prompt resource is the exact persisted instruction input. It must require:

- Observe every ordered panel before drafting a recap. For each panel separate visible fact, dialogue/OCR, inference, and uncertainty.
- Track entities, aliases, motives, state changes, and causal links across sequential overlapping chunks. Reconcile continuity after the final chunk.
- Do not draft a recap until the complete coverage manifest is reconciled and no material region is unresolved.
- Build the story spine as who wants what, obstacle, decision, consequence, changed stakes, unresolved question.
- Write conversational American English as Cinematic Story Detective: a clever friend under controlled tension, motives, consequences, hidden clues, varied human sentence rhythm, causal transitions, no rigid chronology, generic hook, generic CTA, fake hyperbole, or invented facts.
- Qualify interpretations and attach every factual or interpretive claim to panel evidence IDs.
- Return structured observations, continuity ledger, evidence graph, coverage manifest, narrative outline, and script passages with claim IDs.
- A missing output key is a provider failure, not permission to infer or template-fill.

- [ ] Write the contract test first. Assert required instructions, version vision-first-story-analyzer-v1, and normalized SHA-256 against the committed snapshot.
- [ ] Add anti-template tests with two distinct mock chapters. Assert source-specific entities and causal links, no fixed opening sentence, and no generic CTA. Record that automated naturalness checks screen output but do not replace human editorial review.
- [ ] Run:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_analyzer_contract.py -q

  Expected RED: collection succeeds and the resource, loader, hash snapshot, and contract assertions fail.
- [ ] Implement the loader exactly as a normalized UTF-8 hash boundary:

    PROMPT_VERSION = "vision-first-story-analyzer-v1"

    def load_analyzer_instruction() -> tuple[str, str, str]:
        text = PROMPT_PATH.read_text(encoding="utf-8")
        normalized = text.replace("\r\n", "\n")
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return PROMPT_VERSION, digest, normalized

  Persist version and digest in StoryAnalysis and the run manifest.
- [ ] Run:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_analyzer_contract.py -q

  Expected GREEN: prompt, hash snapshot, structured-output, anti-template, and human-review-disclaimer tests pass.
- [ ] Commit:

    git add app/prompts/vision_first_story_analyzer_v1.txt app/services/analyzer_contract.py tests/test_analyzer_contract.py tests/fixtures/vision_prompt_snapshot.sha256
    git diff --cached --check
    git commit -m "feat: version the vision story instruction contract"

### Task 6: Refactor resolver to expose only vision-first capability

**Files:**
- Modify app/services/resolver.py.
- Add tests/test_resolver_vision.py.
- Preserve existing TTS resolution for Plan 2.

- [ ] Add a red test for a text-only analyzer. resolve_vision must return an unavailable capability report, never a RulesAnalyzer adapter.
- [ ] Add a red test for the local multimodal mock provider and assert an available report.
- [ ] Run:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_resolver_vision.py -q

  Expected RED: collection succeeds and fails because resolve_vision is absent.
- [ ] Implement resolve_vision(db, workspace_id). Read provider type, model, and credential presence through the existing resolver/config boundary; strip secrets from the report. Unsupported providers are explicit failures.
- [ ] Keep resolve_analyzer for an explicitly named legacy workflow. No vision-first caller may invoke it.
- [ ] Run:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_resolver_vision.py tests/test_resolver.py -q

  Expected GREEN: vision capability and existing resolver tests pass.
- [ ] Commit:

    git add app/services/resolver.py tests/test_resolver_vision.py
    git diff --cached --check
    git commit -m "feat: resolve vision capability without fallback"

### Task 7: Make analysis consume complete ordered vision evidence

**Files:**
- Modify app/services/pipeline.py.
- Add tests/test_vision_pipeline.py.
- Add tests/test_story_evidence.py.

Required signatures:

    def build_observation_chunks(
        panel_regions: Sequence[PanelRegion],
        *,
        chunk_size: int = 12,
        overlap: int = 2,
    ) -> list[tuple[PanelRegion, ...]]:
        ...

    def run_analysis(
        db: Session,
        project_id: str,
        actor_id: str = "",
    ) -> StoryAnalysis:
        ...

    def generate_script(
        db: Session,
        project_id: str,
        *,
        keep_locked: bool = True,
        hook_count: int = 3,
        seed: int | None = None,
        actor_id: str = "",
    ) -> ScriptVersion:
        ...

- [ ] Add a fixture with multiple assets and one long strip. Assert region order is source asset order then source order, and sequential chunks overlap exactly two panels.
- [ ] Add a provider-spy test. Every canonical panel ID must be observed; overlap IDs may recur only in the adjacent overlap chunk.
- [ ] Add reconciliation tests for missing observation, unresolved material, missing chunk link, and claim without panel evidence. Each has a distinct blocking reason.
- [ ] Add a no-provider test. run_analysis persists BLOCKED with vision_capability_missing and never instantiates RulesAnalyzer.
- [ ] Add a no-template-fallback test. Incomplete structured output persists analysis_incomplete and creates no script version.
- [ ] Run:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_vision_pipeline.py tests/test_story_evidence.py -q

  Expected RED: collection succeeds and fails because the pipeline still resolves text analysis and does not persist the evidence chain.
- [ ] Implement the ordered flow:

    assets = load_image_assets_in_source_order(db, project_id)
    inputs = build_source_asset_inputs(assets)
    coverage = build_complete_coverage_map(inputs, segmentation_version=SEGMENTATION_VERSION)
    if coverage.reconciliation_errors or coverage.unresolved_material_area:
        return persist_blocked_analysis(db, project_id, "coverage_incomplete", coverage)

    provider, capability = resolve_vision(db, workspace_id_for_project(db, project_id))
    if not capability.available or provider is None:
        return persist_blocked_analysis(db, project_id, capability.blocking_reason or "vision_capability_missing", coverage)

    regions = persist_panel_regions(db, project_id, coverage)
    chunks = build_observation_chunks(regions)
    observations = observe_all_chunks(provider, chunks, coverage)
    evidence = reconcile_observations_and_build_evidence(coverage, regions, observations, chunks)
    if evidence.blocking_reasons:
        return persist_blocked_analysis(db, project_id, evidence.blocking_reasons, coverage, evidence)

    story_spine = build_story_spine(evidence)
    return persist_reconciled_analysis(db, project_id, coverage, evidence, story_spine)

  Use repository session/model helpers, but preserve this ordering and fail-closed branches.
- [ ] Persist total assets, total source-space area, accounted area, canonical panel count, processed panel count, duplicate observations, unreadable/low-confidence panels, ordering uncertainties, character ambiguities, tile ranges, overlap, map hash, and claim-to-panel references.
- [ ] Build continuity ledger and evidence graph from sequential overlapping observations. Claims contain stable IDs, panel IDs, confidence, visible/evidence basis, and uncertainty qualifiers.
- [ ] Build the story spine before prose. The script writer receives only reconciled evidence and story spine.
- [ ] Keep RulesAnalyzer callable only through an explicit run_legacy_text_analysis entry point.
- [ ] Run:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_vision_pipeline.py tests/test_story_evidence.py tests/test_vision_coverage.py -q

  Expected GREEN: ordering, overlapping chunks, persistence, blocked state, no-provider, no-template, evidence-reference, and story-spine tests pass.
- [ ] Commit:

    git add app/services/pipeline.py tests/test_vision_pipeline.py tests/test_story_evidence.py
    git diff --cached --check
    git commit -m "feat: run analysis from reconciled vision evidence"

### Task 8: Gate scripts and expose auditable analysis status

**Files:**
- Modify the script-generation boundary in app/services/pipeline.py.
- Modify app/routers/pipeline.py and app/schemas.py.
- Add tests/test_script_evidence_gate.py.
- Add tests/test_vision_status_api.py.

Required behavior:
- generate_script creates a draft only from RECONCILED vision analysis with complete coverage, valid instruction version/hash, complete evidence graph, story spine, and claim-to-panel references.
- approve_script additionally requires explicit human editorial approval. Automated anti-template checks are screening evidence only.
- Status exposes state, blocking reasons, source asset count, total/persisted/processed panel counts, source-space coverage ratio, unresolved material area, evidence/claim counts, instruction version/hash, and claim references without secrets.
- Distinguish RECONCILED, SCRIPT_DRAFT, SCRIPT_APPROVED, and blocked states. Historical text-only rows never satisfy the new gate.

- [ ] Write red tests for incomplete coverage, missing claims, approval without human approval, and successful evidence-linked draft.
- [ ] Write a status API test for the exact public fields and an empty/redacted secret field set.
- [ ] Run:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_script_evidence_gate.py tests/test_vision_status_api.py -q

  Expected RED: current script generation accepts incomplete/text analysis and status lacks the coverage contract.
- [ ] Add helpers with stable signatures:

    def analysis_gate_reasons(analysis: StoryAnalysis) -> list[str]:
        ...

    def require_reconciled_analysis(analysis: StoryAnalysis) -> None:
        ...

    def script_claim_evidence_refs(script: ScriptVersion) -> dict[str, list[str]]:
        ...

  Raise the existing validation exception with machine-readable reasons.
- [ ] Add response fields using the project current schema version. Serialize claim IDs and source orders, never image payloads or credentials.
- [ ] Run:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_script_evidence_gate.py tests/test_vision_status_api.py tests/test_pipeline.py -q

  Expected GREEN: evidence gates, human approval, status fields, and current pipeline tests pass.
- [ ] Commit:

    git add app/services/pipeline.py app/routers/pipeline.py app/schemas.py tests/test_script_evidence_gate.py tests/test_vision_status_api.py
    git diff --cached --check
    git commit -m "feat: gate scripts on auditable vision evidence"

### Task 9: Verify Plan 1 and stop for review

- [ ] Run the complete focused suite:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_vision_coverage.py tests/test_vision_migration.py tests/test_segmentation.py tests/test_segmentation_reconciliation.py tests/test_vision_adapter.py tests/test_vision_capability.py tests/test_analyzer_contract.py tests/test_resolver_vision.py tests/test_vision_pipeline.py tests/test_story_evidence.py tests/test_script_evidence_gate.py tests/test_vision_status_api.py -q

  Expected GREEN: all-panel coverage, source-space completeness, tile seam/top/bottom, unsupported-provider, prompt hash, no-template, evidence, script-gate, and status tests pass.
- [ ] Run legacy regressions without treating legacy text analysis as vision evidence:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_models.py tests/test_resolver.py tests/test_pipeline.py -q

  Expected GREEN: existing tests pass or only explicitly updated assertions identify the new fail-closed vision path.
- [ ] Run static checks:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/ruff check app tests
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/python -m compileall -q app tests
    git diff --check

  Expected GREEN: Ruff is clean, compileall exits zero, and diff-check prints no lines.
- [ ] Manually review a persisted manifest and mock evidence graph. Confirm source asset -> coverage tile/map -> classified region -> panel -> observation -> chunk -> evidence claim -> script passage.
- [ ] Commit Plan 1 changes with the task commits preserved or with review evidence retained:

    git add app/models.py alembic/versions/b7c4d8e91f20_add_vision_coverage_boundary.py app/services/strips.py app/services/ingest.py app/services/segmentation.py app/services/vision_adapter.py app/services/analyzer_contract.py app/prompts/vision_first_story_analyzer_v1.txt app/services/resolver.py app/services/pipeline.py app/routers/pipeline.py app/schemas.py tests/fixtures/vision_coverage.py tests/fixtures/vision_prompt_snapshot.sha256 tests/test_vision_coverage.py tests/test_vision_migration.py tests/test_segmentation.py tests/test_segmentation_reconciliation.py tests/mock_provider.py tests/test_vision_adapter.py tests/test_vision_capability.py tests/test_analyzer_contract.py tests/test_resolver_vision.py tests/test_vision_pipeline.py tests/test_story_evidence.py tests/test_script_evidence_gate.py tests/test_vision_status_api.py
    git diff --cached --check
    git commit -m "feat: complete vision coverage story engine slice"

## Stop Point

Stop after the focused suite and static checks are green. Handoff must include coverage map/hash, source-space coverage ratio, unresolved material area, panel/observation/chunk/claim counts, provider capability report, prompt version/hash, blocking reasons, and exact commit SHA. Do not start voice auditions, motion work, final rendering, or rollout integration until Sol High reviews this vertical slice.

## Execution Handoff

This is an executable plan. Use the required superpowers:subagent-driven-development workflow or run it inline with superpowers:executing-plans, preserving the stop point.
