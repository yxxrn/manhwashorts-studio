# Vision-First Editorial Story Engine Design

Status: Approved design for implementation planning
Date: 2026-08-05
Decision owner: Sol High
Scope: design only. This correction changes no implementation, generates no audio or video, and touches no source assets.

## 1. Decision and invariants

Keep the Editorial Story Engine from approach 2. Replace its evidence stage with full vision-first chapter comprehension. Script approval is possible only after every source asset and strip has a complete source-space coverage map, the map has been reconciled into an ordered segmented inventory, every inventory entry has been processed, and all evidence gates pass.

The design invariants are:

1. Analyze every canonical panel or content region in deterministic reading order. Never random-sample representative panels and never treat a sample as chapter coverage.
2. Run deterministic source-space mapping and segmentation before vision-story observations. Every source-space region is classified as canonical panel/content region, verified gutter/non-story region, or unresolved/material region.
3. No source pixels or content bands may be silently unassigned. Material or unresolved regions block segmentation reconciliation and script approval.
4. Use deterministic overlapping strip tiles for very long images so detector/provider size limits cannot truncate the top, bottom, or tile seams.
5. Process long chapters as ordered sequential chunks with overlap, carry entity and character continuity across chunks, and perform a chapter-wide synthesis over the complete canonical observation set.
6. Persist source assets, strips, tile ranges and overlaps, coverage-map version/hash, source-space coverage ratio, panel/content area, verified gutter/non-story area, unresolved material area, the segmented-panel denominator, and full lineage reconciliation.
7. Require source_content_coverage_ratio=1.0 for accounted source space and unresolved_material_area=0 before coverage can complete. Explicit deterministic masks for verified gutters or non-story regions are allowed only with reason and evidence.
8. Block script approval when mapping, segmentation, reconciliation, coverage, or evidence is incomplete or too uncertain. Never silently fall back to a rule-based template recap.
9. Use the narrative identity Cinematic Story Detective: conversational American English, human sentence rhythm, causal storytelling, motives and consequences, and restrained evidence-grounded hidden clues. Do not use rigid chronology, fake hyperbole, generic CTA, or invented facts.
10. Use the normative, versioned analyzer instruction contract in section 7. Persist its version and computed hash with the job and outputs.
11. Separate spoken_text from display_text. spoken_text keeps punctuation for TTS prosody. display_text removes all Unicode punctuation, including apostrophes and hyphens, while preserving word timing alignment.
12. Require four equal-length English voice auditions only when no approved immutable voice profile exists, or after an explicit user request or voice configuration change. Persist the selected immutable profile for later chapters and renders. Final render blocks if no selected profile exists.
13. Correct even-pixel crop quantization; remove automatic sinusoidal shake, micro-shake, and orbit; cap normal zoom near 6 percent and impact zoom near 8 percent; use one smooth intent per shot, periodic static holds, and frame-to-frame center, scale, acceleration, and reversal QC.
14. The existing analyzer is text-only. Require a multimodal adapter and capability check, and explicitly block when no vision-capable provider is configured.
15. Keep rights and publication gates independent and hard. A rights failure leaves publish_allowed false.

These are acceptance criteria, not optional style guidance.

## 2. Architecture and data flow

    source assets and strips
      -> full-strip overview and deterministic tiled source-space coverage map
      -> region classification and segmentation completeness verifier
      -> ordered segmented panel/content inventory and denominator
      -> source-asset/strip/region reconciliation
      -> multimodal capability gate
      -> ordered sequential chunk planner
      -> panel-level vision observations
      -> cross-chunk entity and character continuity ledger
      -> coverage manifest and evidence graph
      -> chapter-wide narrative synthesis using the instruction contract
      -> spoken_text and display_text timing contracts
      -> voice-profile requirement check
      -> four auditions only when profile is missing or explicitly invalidated
      -> user voice-profile selection and immutable profile persistence
      -> shot and motion planning
      -> render and frame telemetry
      -> editorial QC and rights gate
      -> review artifact or publication decision

The capability gate runs before script approval. Source-space coverage mapping is a mandatory denominator-building stage, not a convenience preprocessor. Chunks are contiguous source ranges with deterministic overlap; overlap supplies context and does not replace canonical observation of any panel. Chapter-wide synthesis consumes all reconciled inventory entries, canonical observations, the continuity ledger, and the evidence graph, never only chunk summaries.

Required state transitions are INVENTORIED, SOURCE_MAPPED, SEGMENTED, SEGMENTATION_COMPLETENESS_VERIFIED, RECONCILED, VISION_CHECKED, OBSERVING, COVERAGE_COMPLETE, SCRIPT_APPROVED, VOICE_PROFILE_REQUIRED, VOICE_AUDITIONING, VOICE_PROFILE_SELECTED, VOICE_PROFILE_PERSISTED, VOICE_PROFILE_REUSED, MOTION_QC_PASSED, RENDER_QC_PASSED, and PUBLISH_ALLOWED. A blocked transition records its reason and stops. It does not produce a template recap.

A chapter with a persisted approved voice profile takes the reuse path after script approval. A chapter without one enters VOICE_PROFILE_REQUIRED and must complete the audition and selection lifecycle before final render. An explicit user request or voice configuration change invalidates reuse for the next render while preserving the old immutable profile.

## 3. Complete source-space coverage mapping

### 3.1 Source assets, strips, and reading order

The mapping stage receives every source asset and strip, including long vertical strips, pages, composite images, and any source representation that can contain multiple panels or content bands. It creates a complete source-space map before a panel detector is allowed to define the denominator.

A source asset record contains:

- source_asset_id
- chapter_id
- source_asset_order
- source URI or repository reference
- source dimensions and content fingerprint
- strip identifiers and source-strip order
- coverage_map_version and coverage_map_hash
- segmentation_version
- mapping status and confidence
- material uncertainty reasons

A strip record contains source_asset_id, strip_id, strip_order, source dimensions, source-space bounds, tile plan, coverage-map reference, and ordering evidence.

Source reading order is deterministic and derived from source asset order, strip order, and source-space position. If a trusted source order exists, it is retained as evidence. If order must be inferred, the uncertainty is recorded and material uncertainty blocks reconciliation.

### 3.2 Deterministic overlapping tiles

Very long strips are processed with deterministic overlapping tiles. The tile planner persists tile_id, source_asset_id, strip_id, source-space start and end ranges, pixel bounds, overlap with the previous and next tile, tile order, tile dimensions, and tile-plan version.

Tile ranges cover the complete strip from top to bottom. Overlap is sufficient to expose boundaries and content at seams, but no tile is treated as a representative sample. The overview pass and the tile pass are both ordered and deterministic. Detector/provider size limits cannot truncate the top, bottom, or a seam because tile coverage is reconciled against the full strip bounds.

Tile areas are unioned in source space for all area and ratio calculations. Overlap is never double-counted as additional content.

### 3.3 Region classification

Every source-space region in the coverage map is assigned exactly one accountable classification:

- canonical_panel_or_content_region: story-bearing panel or content region that must enter the ordered inventory
- verified_gutter_or_non_story_region: deterministic mask for a gutter, separator, border, or other verified non-story area, with reason and evidence
- unresolved_or_material_region: unknown, ambiguous, likely story-bearing, or materially uncertain area

No source pixels or content bands may be silently unassigned. A verified gutter/non-story classification is an explicit deterministic mask, not an absence of detection. It records mask_id, bounds, reason, evidence reference, classifier version, and confidence.

The map stores source-space area for each classification. Area is calculated on the unioned source-space map, not a sum of overlapping tiles. A map is complete only when:

    source_content_coverage_ratio =
        accounted_source_space_area / total_source_space_area = 1.0

Accounted source space is canonical panel/content area plus verified gutter/non-story area. unresolved_material_area must equal 0. Any unresolved or material area, missing band, unassigned pixel range, or uncertain boundary blocks SOURCE_MAPPED, SEGMENTATION_COMPLETENESS_VERIFIED, RECONCILED, and script approval.

### 3.4 Segmentation completeness verifier

A segmentation completeness verifier compares a full-strip overview map with the unioned segmented-region mosaic. It checks source-space bounds, panel/content bands, gutters, tile seams, top and bottom edges, and overlaps. It flags likely missed regions, unexplained area, inconsistent classifications, and mosaic gaps.

The verifier is a gate, not random sampling. It examines the complete overview and complete mosaic in source order. A mismatch creates a persisted completeness finding with source_asset_id, strip_id, source-space bounds, map versions/hashes, area difference, reason, and materiality. A material finding prevents reconciliation.

## 4. Deterministic segmentation and inventory

The segmentation stage turns every canonical_panel_or_content_region into an ordered inventory entry. It never truncates the list, applies a maximum-count cap, or chooses representative regions.

A strip-region record contains:

- source_asset_id
- strip_id and strip_region_id
- panel_id
- strip_order and region_order
- source_index and deterministic source order key
- region_bounds in source pixel coordinates
- normalized region bounds
- coverage-map version/hash
- segmentation_version and segmentation_confidence
- duplicate-group metadata when applicable
- boundary and order uncertainty references

Every canonical panel or content region from every source strip enters the ordered inventory. Every verified gutter/non-story mask remains in the coverage map and reconciliation record even though it does not enter the panel denominator. Every unresolved/material region remains in the map and blocks until resolved or explicitly handled by the blocking policy.

The coverage denominator is the persisted segmented panel inventory, not a raw image count, a tile count, a provider response count, or a detected-only count. total_panels equals the number of canonical inventory entries after deterministic segmentation, including source rows whose content is later found to be duplicated. Duplicate groups are recorded separately and do not silently remove source regions from the denominator.

## 5. Full lineage reconciliation

The system reconciles the complete source-space map at every boundary:

    source_asset_id and strip_id
      -> tile ranges and overlaps
      -> source-space map classification
      -> strip_region_id and region bounds
      -> panel_id and ordered inventory entry
      -> canonical observation
      -> chunk membership and overlap replay
      -> chapter synthesis references
      -> narrative claim evidence references

A reconciliation record lists expected source assets, strips, tiles, classified map areas, inventory regions, observed regions, chunk regions, synthesized region references, claim references, orphaned IDs, unassigned IDs, missing-edge reasons, map version/hash, and reconciliation status. No source asset, strip, pixel band, region, or panel may disappear between ingest, mapping, segmentation, observation, chunking, synthesis, and claims.

RECONCILED is reachable only when every source-space edge is accounted for, source_content_coverage_ratio is 1.0, unresolved_material_area is 0, the completeness verifier passes, and the panel denominator reconciles. A duplicate or overlap replay is recorded, not silently dropped.

## 6. Full vision-first comprehension

### 6.1 Ordered inventory and observations

The final inventory entry has stable source_asset_id, strip_id, strip_region_id, panel_id, source_index, source order key, source reference, region_bounds, dimensions, content fingerprint, coverage-map version/hash, segmentation_version, segmentation_confidence, and lineage status. Persist an ordering fingerprint derived from all ordered inventory identifiers and source references.

Every inventory entry passes through the observation path. Duplicate detection is recorded for audit and evidence reconciliation, not used to skip coverage. Each duplicate retains its own source record and points to a canonical duplicate group. Chunk-overlap replays are recorded separately from source duplicates.

### 6.2 Ordered chunks and continuity

The chunk planner uses a deterministic configured maximum that respects provider image and context limits. It persists chunk_id, source start and end indices, canonical panel IDs, overlap panel IDs, provider limits, ordering fingerprint, and plan version.

The continuity ledger carries entity and character identifiers, aliases and visual anchors, locations, relationships, motivation hypotheses, state transitions, unresolved identity questions, supporting panel IDs, and confidence. Conflicting overlap observations are retained as ambiguity or reconciliation evidence rather than silently overwritten. The ledger cannot invent continuity for an unobserved panel.

### 6.3 Multimodal adapter

The current app/services/analysis.py analyzer is text-only and cannot satisfy this evidence stage alone. The planned adapter accepts image-bearing requests and returns structured visual observations. Its capability check must verify configured provider, image input, structured observation support, selected model and chunk limits, and recordable provider/model identity.

No vision-capable provider, text-only provider, unavailable model, rejected image, or unsupported response contract blocks with an explicit reason such as vision_capability_missing or vision_input_unsupported. Text generated after a valid vision observation may be used by later stages, but text-only analysis is never chapter coverage. Provider errors, empty responses, and low-confidence responses never become a rule-based recap.

A canonical panel observation contains source_asset_id, strip_region_id, panel_id, source_index, readable status, confidence, visible characters and entity anchors, actions, expressions, objects, locations, scene transitions, visible text or dialogue regions when available, visual relationships and state changes, unresolved ambiguities, evidence spans, provider/model, request fingerprint, coverage-map version/hash, and observation version. What is seen is separate from what is inferred; every inference carries confidence and supporting panel IDs.

## 7. Coverage manifest and evidence graph

The persisted manifest is the source of truth for source-space coverage, denominator, and approval. Its normative information shape is:

    {
      "chapter_id": "string",
      "inventory_version": "string",
      "coverage_map_version": "string",
      "coverage_map_hash": "string",
      "segmentation_version": "string",
      "ordering_fingerprint": "string",
      "total_source_assets": 0,
      "total_strips": 0,
      "total_tiles": 0,
      "total_panels": 0,
      "processed_panels": 0,
      "source_space_area": 0,
      "panel_content_area": 0,
      "verified_gutter_non_story_area": 0,
      "unresolved_material_area": 0,
      "source_content_coverage_ratio": 1.0,
      "tile_ranges_and_overlaps": [],
      "segmentation_completeness_verifier": {
        "passed": false,
        "overview_hash": "string",
        "mosaic_hash": "string",
        "findings": []
      },
      "source_asset_reconciliation": [
        {
          "source_asset_id": "string",
          "strip_ids": ["string"],
          "tile_ids": ["string"],
          "map_region_ids": ["string"],
          "inventory_panel_ids": ["string"],
          "observed_panel_ids": ["string"],
          "chunk_panel_ids": ["string"],
          "synthesized_panel_ids": ["string"],
          "claim_panel_ids": ["string"],
          "orphaned_region_ids": [],
          "unassigned_source_ranges": [],
          "block_reasons": []
        }
      ],
      "segmentation_uncertainties": [
        {
          "source_asset_id": "string",
          "strip_id": "string",
          "source_space_bounds": {},
          "reason": "string",
          "material": true,
          "confidence": 0.0
        }
      ],
      "duplicates": [
        {
          "panel_id": "string",
          "duplicate_group_id": "string",
          "canonical_panel_id": "string",
          "source_index": 0
        }
      ],
      "unreadable_or_low_confidence_panels": [
        {
          "panel_id": "string",
          "source_index": 0,
          "reason": "string",
          "confidence": 0.0
        }
      ],
      "ordering_uncertainties": [
        {"source_indices": [0, 1], "reason": "string", "material": true}
      ],
      "character_ambiguities": [
        {
          "panel_ids": ["string"],
          "entity_id": "string",
          "reason": "string",
          "confidence": 0.0
        }
      ],
      "claim_to_panel_evidence_refs": [
        {
          "claim_id": "string",
          "panel_ids": ["string"],
          "evidence_spans": ["string"],
          "confidence": 0.0
        }
      ],
      "chunk_plan": {"chunks": [], "overlap_panels": []},
      "coverage_complete": false,
      "evidence_sufficient": false,
      "reconciliation_complete": false,
      "block_reasons": []
    }

processed_panels counts unique source panel IDs with valid canonical observations, where panel IDs come from the persisted segmented denominator. Unreadable or below-policy-confidence panels remain visible and do not satisfy coverage. Duplicate and overlap metadata must reconcile source regions with considered observations.

Every factual or causal claim reaching the script has a claim_id, claim type, confidence, and claim-to-panel references. A qualified inference still needs panel evidence. A claim without evidence, or with unresolved identity that changes its meaning, blocks script approval.

## 8. Normative analyzer instruction contract

The analyzer instruction is a versioned contract, not style prose. Its canonical body is stored as a UTF-8 LF-normalized resource with contract ID vision_first_editorial_story_engine.analyzer and version 1.0.0. The implementation computes SHA-256 over that exact canonical body and persists instruction_contract_version and instruction_contract_hash in the job manifest, every observation request record, the coverage manifest, the narrative output, and the QC report. A version or hash mismatch is a blocking contract error.

The contract body is normative and uses MUST language:

    1. First observe all ordered inventory panels after the complete source-space map and
       reconciliation pass. For every panel, separate visible_fact, dialogue_or_OCR,
       inference, and uncertainty. Preserve source_asset_id, strip_region_id, panel_id,
       source_index, region bounds, map version/hash, and evidence references.
    2. Track entities, aliases, motives, state changes, and causal links across sequential
       overlapping chunks. Carry the continuity ledger forward and record conflicts instead
       of guessing.
    3. Do not draft a recap or script until complete source-space coverage, segmentation
       completeness, chapter coverage, and source-asset/region/panel/observation/chunk/
       synthesis reconciliation have passed.
    4. Build the story spine in this order of reasoning:
       who wants what -> obstacle -> decision -> consequence -> changed stakes
       -> unresolved question.
    5. Then write conversational American English as Cinematic Story Detective: a clever
       friend with controlled tension, motives, consequences, and hidden clues; varied
       sentence rhythm; and causal transitions. Do not use rigid then/after-that chronology,
       a generic hook or CTA, fake hyperbole, or invented facts.
    6. Qualify every interpretation and link it to supporting panel evidence. Do not present
       an inference, motive, identity, or hidden clue as a visible fact.
    7. Output structured observations, the continuity ledger, the evidence graph, the
       coverage manifest, a narrative outline, and script passages with claim IDs. Do not
       return a free-form recap in place of any required structure.

The adapter rejects output that omits required structures, claim IDs, panel evidence, uncertainty fields, source-space reconciliation, or coverage state. The instruction contract is persisted with generated artifacts and is part of reproducibility and review.

## 9. Narrative synthesis

Cinematic Story Detective permits a clear consequence, mystery, or revealing visual to open the narration and then backfill causes. It does not require rigid panel chronology, but every transition and claim must be explainable from the complete evidence graph, story spine, and continuity ledger. Unknown or ambiguous facts are qualified or omitted.

The script metadata records claim IDs and evidence references for each passage. Approval requires complete source-space mapping, segmentation completeness, reconciliation, coverage_complete, evidence_sufficient, no material segmentation or ordering uncertainty, no material character ambiguity, evidence for every factual or causal claim, a completed story spine, the required structured output, and narrative identity checks. A failed check is an auditable block, not an automatic template rewrite.

Automated anti-template and naturalness checks may detect generic hooks, generic CTAs, repeated then/after-that transitions, unsupported superlatives, missing causal links, and missing claim IDs. They are screening gates and do not replace human editorial review of sentence rhythm, clarity, tension, and evidence grounding.

## 10. Spoken and displayed text

The timed text contract is:

    {
      "cue_id": "string",
      "spoken_text": "He cannot see the mark.",
      "display_text": "He cannot see the mark",
      "word_timings": [{"word": "He", "start": 0.0, "end": 0.3}],
      "start": 0.0,
      "end": 1.8,
      "claim_ids": ["claim-001"]
    }

spoken_text is authoritative for TTS and retains punctuation for prosody. display_text is derived from the same timed word-token sequence and is never sent to TTS.

display_text removes every Unicode punctuation character, not only an ASCII list. This includes apostrophes, hyphens, quotation marks, dashes, and ellipses. If punctuation would join lexical tokens, the timed token boundary remains whitespace. No words are added, merged, reordered, or dropped; each display token retains its spoken timing span.

Existing timing gates remain active: readable 4 to 7 word groups, at most two lines, semantic boundary handling, and cue ends no later than media duration. These are checked on display_text while spoken_text remains punctuation-preserving.

## 11. Voice profile and audition lifecycle

The voice lifecycle is profile-based rather than chapter-based.

### 11.1 Required and reuse paths

If no approved immutable voice profile exists for the project or render configuration, enter VOICE_PROFILE_REQUIRED and generate the four auditions:

1. calm documentarian
2. conversational analyst
3. cinematic storyteller
4. sharp mystery narrator

Every sample uses identical English text, target duration, speed normalization, and loudness normalization. The user selects one audition. The selected voice and normalized settings are persisted as an immutable approved voice profile with a profile ID, content hash, provider/model/voice identifiers, normalized settings, selection record, and creation metadata.

Later chapters and renders reuse that immutable profile without re-auditioning. Four new auditions are required only when no approved profile exists, when the user explicitly requests re-audition, or when a configured voice/provider/speed/loudness change invalidates the current profile for the requested render. The old profile remains immutable and auditable; a new profile is a new selection, not an in-place mutation.

A final render blocks if there is no selected approved immutable profile. A chapter cannot bypass this gate merely because an earlier chapter had auditions.

### 11.2 State transitions

The full lifecycle is:

- VOICE_PROFILE_REQUIRED: no approved profile or explicit invalidation.
- VOICE_AUDITIONING: all four equal-text auditions are being generated and normalized.
- VOICE_PROFILE_SELECTED: the user selected one audition.
- VOICE_PROFILE_PERSISTED: the selected profile and manifest are immutable and available for reuse.
- VOICE_PROFILE_REUSED: an existing immutable profile is attached to the chapter/render.
- FINAL_RENDER_READY: coverage, script, timed text, profile, motion, render, and rights prerequisites are evaluated.

Missing audition, unequal measured duration, missing user selection, mutable profile data, or profile/config mismatch blocks the relevant transition.

### 11.3 Audition manifest

The manifest records sample text ID and hash, exact text, target and measured duration, speed and loudness parameters and measurements, provider/model/voice IDs, timestamp, artifact hash/path, selected profile ID, profile content hash, trigger reason, and pass or block reasons. Equal length is verified from measured artifacts.

Voice audition samples do not represent chapter coverage; they only compare voice characteristics. Their success cannot satisfy source-space mapping, segmentation, panel, evidence, or script gates.

## 12. Motion design and QC

Motion has one smooth intent per shot: hold, one directional move, one reveal, or one bounded impact move. It does not combine unrelated oscillations.

The crop-coordinate helper uses:

    even_coord(value, maximum) =
        2 * floor(clamp(value, 0, maximum) / 2)

Clamp after quantization to the valid crop range. Use a corresponding even-size helper for crop/output dimensions. Do not use the incorrect form that multiplies an already pixel-valued coordinate by two. Test odd/even, zero, maximum, and render-level crop validity.

Automatic sinusoidal shake, micro-shake, and orbit are removed. No normal path injects sine or cosine center oscillation. Normal zoom is at most approximately 1.06 and impact zoom at most approximately 1.08. Each shot declares intent, direction, focal target, and reason. Periodic static holds prevent continuous motion. Legacy plans containing removed modes fail validation or are explicitly migrated before render; they are not silently accepted.

Rendered telemetry samples frame-to-frame center and scale and derives displacement/direction, scale delta, acceleration/deceleration, reversal count and locations, maximum scale, static-hold coverage, and intent continuity. Unexpected reversals, acceleration spikes, ceiling violations, missing intent, or removed oscillation signatures block render QC with shot, frame interval, measurement, threshold, and reason.

## 13. Planned schemas

### SourceAssetRecord

    {
      "source_asset_id": "string",
      "chapter_id": "string",
      "source_asset_order": 0,
      "source_reference": "string",
      "dimensions": {"width": 0, "height": 0},
      "content_fingerprint": "string",
      "strip_ids": ["string"],
      "coverage_map_version": "string",
      "coverage_map_hash": "string",
      "segmentation_version": "string",
      "segmentation_confidence": 0.0,
      "mapping_status": "complete|ambiguous|blocked",
      "uncertainty_reasons": []
    }

### CoverageMapRecord

    {
      "source_asset_id": "string",
      "strip_id": "string",
      "coverage_map_version": "string",
      "coverage_map_hash": "string",
      "tile_ranges_and_overlaps": [
        {
          "tile_id": "string",
          "source_bounds": {},
          "previous_overlap": {},
          "next_overlap": {}
        }
      ],
      "classifications": [
        {
          "classification": "canonical_panel_or_content|verified_gutter_or_non_story|unresolved_or_material",
          "source_bounds": {},
          "area": 0,
          "mask_id": "string",
          "reason": "string",
          "evidence_refs": []
        }
      ],
      "source_content_coverage_ratio": 1.0,
      "panel_content_area": 0,
      "verified_gutter_non_story_area": 0,
      "unresolved_material_area": 0,
      "overview_hash": "string",
      "mosaic_hash": "string",
      "completeness_passed": false
    }

### StripRegionRecord

    {
      "source_asset_id": "string",
      "strip_id": "string",
      "strip_region_id": "string",
      "panel_id": "string",
      "strip_order": 0,
      "region_order": 0,
      "source_index": 0,
      "region_bounds": {"left": 0, "top": 0, "right": 0, "bottom": 0},
      "coverage_map_version": "string",
      "coverage_map_hash": "string",
      "segmentation_version": "string",
      "segmentation_confidence": 0.0,
      "boundary_uncertainties": []
    }

### CapabilityReport

    {
      "configured": true,
      "vision_capable": true,
      "provider_id": "string",
      "model_id": "string",
      "accepts_images": true,
      "structured_observation_version": "string",
      "limits": {"max_images": 0, "max_context": 0},
      "instruction_contract_version": "1.0.0",
      "instruction_contract_hash": "sha256-of-canonical-contract",
      "block_reasons": []
    }

### NarrativeClaim

    {
      "claim_id": "string",
      "text": "string",
      "claim_type": "observed|inferred|causal",
      "certainty": 0.0,
      "support_panel_ids": ["string"],
      "source_spans": ["string"],
      "qualified_language": false
    }

### VoiceProfile

    {
      "voice_profile_id": "string",
      "content_hash": "string",
      "provider_id": "string",
      "voice_id": "string",
      "normalized_settings": {},
      "selected_from_audition_id": "string",
      "immutable": true,
      "created_at": "string"
    }

### MotionPlan

    {
      "shot_id": "string",
      "intent": "hold|push|pull|pan|reveal|impact",
      "focus_start": {"x": 0.0, "y": 0.0},
      "focus_end": {"x": 0.0, "y": 0.0},
      "max_scale": 1.06,
      "reason": "string",
      "static_hold": false,
      "legacy_oscillation": false
    }

### EditorialQCReport

    {
      "source_mapping": {"complete": false, "block_reasons": []},
      "segmentation": {"complete": false, "block_reasons": []},
      "coverage": {"complete": false, "block_reasons": []},
      "narrative": {"passed": false, "reasons": []},
      "timed_text": {"passed": false, "reasons": []},
      "voice_profile": {"passed": false, "reasons": []},
      "motion": {"passed": false, "reasons": []},
      "rights": {"publish_allowed": false, "reasons": []},
      "failures": []
    }

The QC report is additive; it does not replace the source-space map, reconciliation, coverage manifest, instruction contract record, evidence references, audition manifest, voice profile, or motion telemetry.

## 14. Planned affected modules

These are planned implementation boundaries only. No implementation file is changed by this design correction.

| Module | Planned responsibility |
| --- | --- |
| app/services/ingest.py | Preserve source-asset identity, source order, strip inputs, and content lineage. |
| app/services/segmentation.py | New boundary for full-strip overview, tiled source-space mapping, region classification, completeness verification, deterministic segmentation, and reconciliation. |
| app/services/analysis.py | Retain text utilities where useful, but never treat text-only analysis as chapter evidence. |
| app/services/vision_adapter.py | New boundary for capability discovery, image requests, structured observations, instruction contract version/hash, and explicit failures. |
| app/services/pipeline.py | Orchestrate mapping, segmentation, reconciliation, capability gate, ordered chunks, synthesis, voice-profile lifecycle, render, QC, and rights states. |
| app/services/editorial_visual_planner.py | Map evidence-backed narrative beats to reconciled source visuals with panel auditability. |
| app/services/script.py | Build the story spine, generate Cinematic Story Detective narration, claims, spoken_text, and display_text inputs. |
| app/services/timeline.py | Preserve timings, punctuation-free display derivation, cue grouping, and duration bounds. |
| app/services/tts.py | Generate auditions only when required and create immutable reusable voice profiles. |
| app/services/motion_director.py | Emit one-intent plans, zoom ceilings, holds, and no oscillation modes. |
| app/services/render.py | Apply crop quantization and emit frame motion telemetry. |
| app/services/editorial_qc.py | Evaluate mapping, segmentation, reconciliation, coverage, evidence, text, voice profile, auditions, motion, and blocking reasons. |
| app/services/visual_scoring.py and app/services/quality.py | Preserve deterministic visual/reuse QC without weakening evidence gates. |
| app/services/encoders.py | Preserve final media contract checks after editorial gates. |
| app/services/publish.py | Keep rights clearance as an independent hard blocker and expose publish_allowed. |
| tests/ | Add source-map, segmentation, prompt-contract, anti-template, lifecycle, integration, and end-to-end regressions for every contract and failure mode. |

## 15. Failure modes and explicit blocking

- No source assets or strips: block with source_inventory_empty.
- Full-strip overview or tile plan does not cover the complete source bounds: block with source_space_tile_coverage_incomplete.
- A source-space range has no classification: block with source_space_unassigned.
- A material or unresolved region remains: block with unresolved_material_area.
- source_content_coverage_ratio is below 1.0: block with source_content_coverage_incomplete.
- A gutter/non-story mask lacks deterministic bounds, reason, or evidence: block with non_story_mask_unverified.
- The completeness verifier finds a mosaic gap, unexplained area, missed likely panel, top/bottom truncation, or tile-seam mismatch: block with segmentation_completeness_failed.
- Segmentation cannot identify a material boundary or source order: persist uncertainty and block with segmentation_ambiguous or segmentation_order_uncertain.
- A source asset, strip, tile, or region is unassigned, orphaned, or missing at any lineage edge: block with source_lineage_reconciliation_failed.
- Segmentation or coverage-map version/hash or region bounds are missing: block with segmentation_contract_invalid.
- No vision-capable provider: block with vision_capability_missing before script approval.
- Text-only provider, image rejection, unavailable model, or unsupported structured response: block with a specific capability reason; never recap by template.
- Chunk failure, invalid observation, or missing panel: persist affected panels, mark coverage incomplete, and block.
- Unreadable or low-confidence panel: persist it and block when complete or sufficiently certain comprehension is not possible.
- Instruction contract missing, version mismatch, hash mismatch, or required output omitted: block with analyzer_contract_invalid.
- Story spine, claim evidence, or structured narrative output missing: block with narrative_contract_invalid.
- Unestablished source order or material character/entity ambiguity: persist uncertainty and block; do not guess.
- Claim without panel evidence or broken source-space-to-claim lineage: block with claim_without_evidence.
- Reconciliation or segmented denominator mismatch: block with coverage_manifest_inconsistent.
- Punctuation transformation or timing mismatch: block timed-text QC.
- No approved voice profile, missing required audition, unequal measured duration, no user selection, mutable profile, or profile/config mismatch: block the voice lifecycle or final render.
- Motion ceiling, intent, acceleration, or reversal failure: block motion QC.
- Unlicensed or uncleared source: retain source_gate_failed and publish_allowed false.

Every failure is visible in job state, map/manifest, QC report, and structured logs. There is no catch-all path that creates generic recap copy.

## 16. Observability and QC

Emit structured events for source inventory, full-strip overview, tile planning, tile completion, source-space classification, map hashing, segmentation completeness verification, strip-region detection, reconciliation, capability check, chunk start/completion, each panel observation, continuity updates, manifest finalization, instruction contract version/hash, each synthesized claim, story-spine completion, script QC, audition generation when triggered, voice-profile selection/persistence/reuse, motion validation, frame telemetry, render QC, and rights QC.

Metrics include total_source_assets, total_strips, total_tiles, tile_overlap_area, source_space_area, panel_content_area, verified_gutter_non_story_area, unresolved_material_area, source_content_coverage_ratio, coverage_map_version, coverage_map_hash, overview_mosaic_area_delta, segmentation_completeness_failures, segmentation_ambiguous_regions, segmentation_order_uncertainties, unassigned_source_assets, orphaned_regions, inventory_regions_total, processed_panels, duplicate_panels, unreadable_or_low_confidence_panels, regions_missing_observation, regions_missing_chunk, regions_missing_synthesis, claims_missing_panel_lineage, ordering_uncertainties, character_ambiguities, evidence_refs_without_panels, chunk_failures, analyzer_contract_failures, anti_template_findings, audition_runs, audition_duration_spread, voice_profile_reuse_count, motion_reversal_count, and blocking_reason_counts.

Persist configuration/schema versions, tile ranges and overlaps, map version/hash, segmentation version/confidence, source ordering fingerprint, lineage reconciliation, chunk plan, provider/model identity, instruction contract version/hash, request fingerprints, voice-profile content hash, and artifact hashes. Never persist secrets or raw credentials. The operator can trace every claim to source asset, strip, region, and panel and every motion failure to frame intervals. QC is fail-closed for this design; rights must pass separately for publish_allowed.

Automated naturalness and anti-template results are observable review signals. They are not a substitute for human editorial review.

## 17. Test strategy

### 17.1 Source mapping, segmentation, and lineage tests

- Inventory every source asset and strip in deterministic reading order with no random sampler.
- Build deterministic overlapping tiles that cover the complete top, bottom, and seams of every long strip.
- Persist tile ranges, overlaps, coverage-map version/hash, source-space area, and classification areas.
- Classify every source-space region as canonical panel/content, verified gutter/non-story with reason/evidence, or unresolved/material.
- Prove that no source pixel or content band is unassigned and that source_content_coverage_ratio equals 1.0 only when all source space is accounted.
- Prove that unresolved_material_area greater than zero blocks reconciliation and script approval.
- Prove that total_panels equals the persisted segmented denominator and includes duplicate source rows.
- Compare a full-strip overview map against the segmented-region mosaic and block likely missed regions, gaps, edge truncation, and seam mismatches.
- Prove that every asset, strip, tile, map region, panel, observation, chunk, synthesis reference, and claim reference reconciles; any orphan, unassigned region, or missing edge blocks.
- Prove that ambiguous boundaries and uncertain order remain in the manifest and block when material.
- Prove that mapping and ordering fingerprints change when source lineage changes.

### 17.2 Vision, contract, and narrative tests

- Complete deterministic enumeration has no representative-panel path and no random sampler.
- Sequential chunks are contiguous and overlapping; continuity conflicts become explicit ambiguity.
- Capability checks reject absent and text-only providers.
- Instruction-contract snapshot tests verify the exact contract version, required MUST clauses, required source-map/reconciliation prerequisites, required story spine, and canonicalization/hash behavior.
- Structured-output tests require observations, visible facts, dialogue/OCR, inferences, uncertainties, continuity ledger, evidence graph, coverage manifest, narrative outline, and claim-ID script passages.
- Anti-template tests reject generic hooks/CTAs, fake hyperbole, repeated then/after-that chronology, unsupported claims, and missing causal transitions on deterministic fixtures.
- Naturalness tests check varied sentence rhythm and causal transitions on fixture outputs.
- The automated checks are screening gates only; tests explicitly document that human editorial review remains required.

### 17.3 Timed text, voice, and motion tests

- Unicode punctuation removal leaves no punctuation and preserves timed word tokens.
- Existing 4 to 7 word, two-line, semantic-boundary, and media-duration gates remain active.
- With no approved profile, four auditions use byte-identical text, normalized settings, and measured equal duration.
- With an approved immutable profile, later chapters reuse the profile without new auditions.
- An explicit re-audition request or voice configuration change triggers exactly the required new audition lifecycle.
- Missing profile or selection blocks final render.
- Profile content is immutable and profile hash is persisted.
- The even-pixel formula passes odd/even, zero, maximum, and render-level crop validity.
- Motion rejects shake, micro-shake, orbit, sine-based center oscillation, scale overages, missing intent, and unexpected reversals.

### 17.4 Integration and acceptance fixtures

Fixtures cover a complete short chapter, a long chapter with overlapping chunks and entity transition, multiple strips per asset, long strips crossing tile seams, duplicates plus unreadable/low-confidence panels, uncertain boundaries and order, material source-space gaps, verified gutter masks, material character ambiguity, spoken/display timing alignment, audit-triggered and profile-reuse voice lifecycles, bounded motion telemetry, anti-template narrative failures, and a rights failure that leaves publish_allowed false.

The acceptance run persists source-space maps, tile plans, reconciliation, the segmented coverage manifest, evidence references, instruction contract version/hash, script outputs, audition/profile manifest, motion telemetry, QC report, and artifact hashes. The report states exact asset, strip, tile, region, panel, gate, and voice-profile results before any final render assertion.

## 18. Acceptance criteria

1. Every source asset and strip has a complete source-space coverage map in deterministic reading order.
2. Every source-space region is classified as canonical panel/content, verified gutter/non-story with reason/evidence, or unresolved/material; no source pixels or content bands disappear.
3. Very long strips use deterministic overlapping tiles and the persisted map proves top, bottom, and seam coverage.
4. The coverage map persists tile ranges/overlaps, source_content_coverage_ratio, panel/content area, verified gutter/non-story area, unresolved material area, and coverage-map version/hash.
5. source_content_coverage_ratio is 1.0 for accounted source space and unresolved_material_area is 0 before coverage can complete.
6. The full-strip overview versus segmented mosaic completeness verifier is a gate and flags likely missed regions; it is not random sampling.
7. The coverage denominator is the persisted segmented panel inventory and reconciles from source-space map through regions, panels, observations, chunks, synthesis, and claims.
8. Incomplete mapping or reconciliation, insufficient confidence, material ambiguity, missing evidence, missing vision capability, malformed provider output, or analyzer-contract failure blocks script approval.
9. The versioned analyzer instruction contract is hashed and persisted, and its normative output and story-spine requirements are enforced by tests.
10. No code path silently falls back to a rule-based template recap. Automated anti-template checks do not replace human editorial review.
11. Narration satisfies Cinematic Story Detective and the stated language, rhythm, causal, evidence, and anti-invention constraints.
12. spoken_text preserves punctuation for TTS; display_text has no Unicode punctuation and preserves word timings.
13. Four identical-text, equal-length normalized auditions are generated only when no approved profile exists or explicit invalidation occurs. The selected immutable profile is persisted and reused for later chapters/renders without mandatory re-audition.
14. Final render blocks if no selected approved immutable voice profile exists.
15. Motion uses the corrected formula, no automatic shake/micro-shake/orbit, the 1.06/1.08 ceilings, one intent, static holds, and frame telemetry QC.
16. Operators can trace claims to source assets and panel regions, and motion failures to frame intervals.
17. Rights clearance remains an independent hard blocker.
18. Rollout and rollback preserve the Git and worktree constraints.

## 19. Rollout

Stage 0 defines source-space mapping, tiled coverage, segmentation/reconciliation, analyzer contract, coverage schema, capability checks, voice-profile schema, fixtures, and a vision-first feature boundary; no publication path is enabled.

Stage 1 runs deterministic asset mapping, ordered inventory, completeness verification, sequential chunks, continuity, reconciliation, contract validation, and synthesis on controlled fixtures. Any missing provider, source region, panel, map area, or evidence blocks rather than recaps.

Stage 2 runs vision-first in review-only shadow against selected jobs. The existing text analyzer may be compared but cannot satisfy coverage or silently take over. Voice auditions run only for jobs without an approved profile or with explicit invalidation.

Stage 3 enables opt-in production with complete source-space map, lineage manifest, evidence-grounded script, valid instruction hash, required voice-profile lifecycle, user selection, motion QC, render QC, and rights QC. Later chapters reuse the selected immutable profile.

Stage 4 makes full source-space vision-first comprehension and profile reuse the default evidence/render path and retires the rule-based recap path from this workflow. Any separate non-vision workflow must be explicitly named and never selected by error recovery.

## 20. Rollback

Disable the vision-first workflow at a controlled job boundary and mark jobs for manual review. Do not automatically substitute a template recap. Preserve maps, manifests, lineage, instruction hashes, profiles, and artifacts for diagnosis.

If implementation commits are reverted, revert only named implementation commits in the authoritative VPS checkout. Preserve source, media, databases, credentials, user data, and unrelated work. A rollback is complete only when mapping, segmentation, capability, coverage, or contract failure remains visible and cannot bypass the rights gate or voice-profile selection gate.

## 21. Git/worktree constraints

- Work in /home/yusronrohmani/manhwashorts on the VPS.
- Inspect Git status first; if dirty, stop and report.
- This task owns only docs/superpowers/specs/2026-08-05-vision-first-editorial-story-engine-design.md.
- Do not edit implementation, generate audio, render video, alter source assets, change databases or user data, or touch credentials/environment files.
- Do not push. Commit only this design document with an intentional corrective message.
- Preserve existing work; never use destructive reset or checkout commands.
- Future implementation uses an isolated worktree or explicitly approved clean VPS surface and keeps generated runtime state out of source commits.

## 22. Explicit non-goals

- Implementing source mapping, segmentation, the analyzer adapter, the instruction contract, schemas, motion changes, QC, or tests in this design-only correction.
- Generating audio, auditions, preview media, or final video now.
- Selecting a provider, voice, prompt wording, or chapter-specific editorial outcome beyond these approved invariants.
- Replacing approach 2.
- Random panel sampling, representative-panel coverage, text-only evidence shortcuts, or silent template fallback.
- Inferring facts, identities, motives, or hidden clues without panel evidence.
- Treating voice auditions as chapter coverage.
- Requiring re-audition for every chapter when an approved immutable profile is unchanged.
- Bypassing rights or resolving conflicting speech balloons without rights-cleared or text-clean assets.
- Changing unrelated modules, documentation, source media, databases, or user and OmniVoice data.
- Pushing to any remote Git repository.
