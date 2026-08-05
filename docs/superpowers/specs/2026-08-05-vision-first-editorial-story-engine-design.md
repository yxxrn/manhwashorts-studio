# Vision-First Editorial Story Engine Design

Status: Approved design for implementation planning
Date: 2026-08-05
Decision owner: Sol High
Scope: design only. This correction changes no implementation, generates no audio or video, and touches no source assets.

## 1. Decision and invariants

Keep the Editorial Story Engine from approach 2. Replace its evidence stage with full vision-first chapter comprehension. Script approval is possible only after every source asset has been segmented into a reconciled ordered panel inventory, every inventory entry has been processed, and the evidence gates pass.

The design invariants are:

1. Analyze every detected canonical panel or strip region in deterministic source order. Never random-sample representative panels and never treat a sample as chapter coverage.
2. Run deterministic source-asset and strip segmentation before vision observations. Every detected region from every source strip enters the ordered inventory with lineage, bounds, confidence, and segmentation version.
3. Process long chapters as ordered sequential chunks with overlap, carry entity and character continuity across chunks, and perform a chapter-wide synthesis over the complete canonical observation set.
4. Persist a coverage manifest containing source-asset reconciliation, the segmented-panel denominator, total panels, processed panels, duplicates, unreadable or low-confidence panels, segmentation uncertainties, ordering uncertainties, character ambiguities, and claim-to-panel evidence references.
5. Block script approval when segmentation, reconciliation, coverage, or evidence is incomplete or too uncertain. Never silently fall back to a rule-based template recap.
6. Use the narrative identity Cinematic Story Detective: conversational American English, human sentence rhythm, causal storytelling, motives and consequences, and restrained evidence-grounded hidden clues. Do not use rigid chronology, fake hyperbole, generic CTA, or invented facts.
7. Use the normative, versioned analyzer instruction contract in section 6. Persist its version and computed hash with the job and outputs.
8. Separate spoken_text from display_text. spoken_text keeps punctuation for TTS prosody. display_text removes all Unicode punctuation, including apostrophes and hyphens, while preserving word timing alignment.
9. Require four equal-length English voice auditions only when no approved immutable voice profile exists, or after an explicit user request or voice configuration change. Persist the selected immutable profile for later chapters and renders. Final render blocks if no selected profile exists.
10. Correct even-pixel crop quantization; remove automatic sinusoidal shake, micro-shake, and orbit; cap normal zoom near 6 percent and impact zoom near 8 percent; use one smooth intent per shot, periodic static holds, and frame-to-frame center, scale, acceleration, and reversal QC.
11. The existing analyzer is text-only. Require a multimodal adapter and capability check, and explicitly block when no vision-capable provider is configured.
12. Keep rights and publication gates independent and hard. A rights failure leaves publish_allowed false.

These are acceptance criteria, not optional style guidance.

## 2. Architecture and data flow

    source assets and strips
      -> deterministic segmentation and source-asset reconciliation
      -> ordered segmented panel inventory and denominator
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

The capability gate runs before script approval. Segmentation is a mandatory denominator-building stage, not a convenience preprocessor. Chunks are contiguous source ranges with deterministic overlap; overlap supplies context and does not replace canonical observation of any panel. Chapter-wide synthesis consumes all reconciled inventory entries, canonical observations, the continuity ledger, and the evidence graph, never only chunk summaries.

Required state transitions are INVENTORIED, SEGMENTED, RECONCILED, VISION_CHECKED, OBSERVING, COVERAGE_COMPLETE, SCRIPT_APPROVED, VOICE_PROFILE_REQUIRED, VOICE_AUDITIONING, VOICE_PROFILE_SELECTED, VOICE_PROFILE_PERSISTED, VOICE_PROFILE_REUSED, MOTION_QC_PASSED, RENDER_QC_PASSED, and PUBLISH_ALLOWED. A blocked transition records its reason and stops. It does not produce a template recap.

A chapter with a persisted approved voice profile takes the reuse path after script approval. A chapter without one enters VOICE_PROFILE_REQUIRED and must complete the audition and selection lifecycle before final render. An explicit user request or voice configuration change invalidates reuse for the next render while preserving the old immutable profile.

## 3. Deterministic source segmentation and inventory

### 3.1 Segmentation stage

The segmentation stage runs before any vision-story observation. It receives every source asset and strip, including long vertical strips, pages, composite images, and any source representation that can contain multiple panels or regions. It detects canonical panel or strip regions, assigns stable lineage, and records the segmentation decision.

A source asset record contains:

- source_asset_id
- chapter_id
- source_asset_order
- source URI or repository reference
- source dimensions and content fingerprint
- strip identifiers and source-strip order
- segmentation_version
- segmentation_confidence
- segmentation_status
- material uncertainty reasons

A strip-region record contains:

- source_asset_id
- strip_region_id
- panel_id
- strip_order and region_order
- source order key
- region_bounds in source pixel coordinates
- normalized region bounds
- segmentation_version and segmentation_confidence
- duplicate-group metadata when applicable
- boundary and order uncertainty references

The source order key is deterministic and derived from source asset order, strip order, and region order. If a source provides a trusted panel order, it is retained as evidence; if order or boundaries must be inferred, the uncertainty is recorded rather than hidden.

Every detected canonical panel or region from every source strip enters the ordered inventory. There is no representative-panel path, random sampler, or maximum-count truncation. If a segmentation provider reports an uncertain extra region, merged region, split boundary, missing region, or uncertain order, the affected source asset and region remain in the manifest and the job blocks when the uncertainty is material to comprehension.

### 3.2 Denominator and lineage reconciliation

The coverage denominator is the persisted segmented panel inventory, not a raw image count and not a provider response count. total_panels equals the number of canonical inventory entries after deterministic segmentation, including source rows whose content is later found to be duplicated. Duplicate groups are recorded separately and do not silently remove source regions from the denominator.

The system reconciles lineage at every boundary:

    source_asset_id
      -> strip_region_id and panel_id
      -> ordered inventory entry
      -> canonical observation
      -> chunk membership and overlap replay
      -> evidence graph and chapter synthesis
      -> script claim references

No source asset, strip, or panel region may disappear between ingest, segmentation, observation, chunking, and synthesis. A reconciliation record lists expected regions, inventory regions, observed regions, chunk regions, synthesized region references, orphaned IDs, unassigned IDs, and missing-edge reasons. RECONCILED is reachable only when every required edge is accounted for or a blocking uncertainty is persisted.

## 4. Full vision-first comprehension

### 4.1 Inventory and ordering

The final inventory entry has stable source_asset_id, strip_region_id, panel_id, source_index, source order key, source reference, region_bounds, dimensions, content fingerprint, segmentation_version, segmentation_confidence, and lineage status. If source order cannot be established, persist ordering_uncertainties and block before script approval. Persist an ordering fingerprint derived from all ordered inventory identifiers and source references.

Every inventory entry passes through the observation path. Duplicate detection is recorded for audit and evidence reconciliation, not used to skip coverage. Each duplicate retains its own source record and points to a canonical duplicate group. Chunk-overlap replays are recorded separately from source duplicates.

### 4.2 Chunks and continuity

The chunk planner uses a deterministic configured maximum that respects provider image and context limits. It persists chunk_id, source start and end indices, canonical panel IDs, overlap panel IDs, provider limits, ordering fingerprint, and plan version.

The continuity ledger carries entity and character identifiers, aliases and visual anchors, locations, relationships, motivation hypotheses, state transitions, unresolved identity questions, supporting panel IDs, and confidence. Conflicting overlap observations are retained as ambiguity or reconciliation evidence rather than silently overwritten. The ledger cannot invent continuity for an unobserved panel.

### 4.3 Multimodal adapter

The current app/services/analysis.py analyzer is text-only and cannot satisfy this evidence stage alone. The planned adapter accepts image-bearing requests and returns structured visual observations. Its capability check must verify configured provider, image input, structured observation support, selected model and chunk limits, and recordable provider/model identity.

No vision-capable provider, text-only provider, unavailable model, rejected image, or unsupported response contract blocks with an explicit reason such as vision_capability_missing or vision_input_unsupported. Text generated after a valid vision observation may be used by later stages, but text-only analysis is never chapter coverage. Provider errors, empty responses, and low-confidence responses never become a rule-based recap.

A canonical panel observation contains source_asset_id, strip_region_id, panel_id, source_index, readable status, confidence, visible characters and entity anchors, actions, expressions, objects, locations, scene transitions, visible text or dialogue regions when available, visual relationships and state changes, unresolved ambiguities, evidence spans, provider/model, request fingerprint, and observation version. What is seen is separate from what is inferred; every inference carries confidence and supporting panel IDs.

## 5. Coverage manifest and evidence graph

The persisted manifest is the source of truth for denominator and approval. Its normative information shape is:

    {
      "chapter_id": "string",
      "inventory_version": "string",
      "segmentation_version": "string",
      "ordering_fingerprint": "string",
      "total_source_assets": 0,
      "total_strips": 0,
      "total_panels": 0,
      "processed_panels": 0,
      "segmentation_confidence_summary": {},
      "source_asset_reconciliation": [
        {
          "source_asset_id": "string",
          "strip_ids": ["string"],
          "detected_region_ids": ["string"],
          "inventory_panel_ids": ["string"],
          "observed_panel_ids": ["string"],
          "chunk_panel_ids": ["string"],
          "synthesized_panel_ids": ["string"],
          "orphaned_region_ids": [],
          "unassigned_region_ids": [],
          "block_reasons": []
        }
      ],
      "segmentation_uncertainties": [
        {
          "source_asset_id": "string",
          "strip_region_id": "string",
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

processed_panels counts unique source panel IDs with valid canonical observations, where the panel IDs come from the persisted segmented denominator. Unreadable or below-policy-confidence panels remain visible and do not satisfy coverage. Duplicate and overlap metadata must reconcile total source panels with considered observations.

Every factual or causal claim reaching the script has a claim_id, claim type, confidence, and claim-to-panel references. A qualified inference still needs panel evidence. A claim without evidence, or with unresolved identity that changes its meaning, blocks script approval.

## 6. Normative analyzer instruction contract

The analyzer instruction is a versioned contract, not style prose. Its canonical body is stored as a UTF-8 LF-normalized resource with contract ID vision_first_editorial_story_engine.analyzer and version 1.0.0. The implementation computes SHA-256 over that exact canonical body and persists instruction_contract_version and instruction_contract_hash in the job manifest, every observation request record, the coverage manifest, the narrative output, and the QC report. A version or hash mismatch is a blocking contract error.

The contract body is normative and uses MUST language:

    1. First observe all ordered inventory panels before drafting any recap. For every panel,
       separate visible_fact, dialogue_or_OCR, inference, and uncertainty. Preserve
       source_asset_id, strip_region_id, panel_id, source_index, region bounds, and
       evidence references.
    2. Track entities, aliases, motives, state changes, and causal links across sequential
       overlapping chunks. Carry the continuity ledger forward and record conflicts instead
       of guessing.
    3. Do not draft a recap or script until complete chapter coverage and the source-asset,
       strip-region, inventory, observation, chunk, and synthesis reconciliation pass.
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

The adapter rejects output that omits required structures, claim IDs, panel evidence, uncertainty fields, or reconciliation state. The instruction contract is persisted with the generated artifacts and is part of reproducibility and review.

## 7. Narrative synthesis

Cinematic Story Detective permits a clear consequence, mystery, or revealing visual to open the narration and then backfill causes. It does not require rigid panel chronology, but every transition and claim must be explainable from the complete evidence graph, story spine, and continuity ledger. Unknown or ambiguous facts are qualified or omitted.

The script metadata records claim IDs and evidence references for each passage. Approval requires complete segmentation reconciliation, coverage_complete, evidence_sufficient, no material segmentation or ordering uncertainty, no material character ambiguity, evidence for every factual or causal claim, a completed story spine, the required structured output, and narrative identity checks. A failed check is an auditable block, not an automatic template rewrite.

Automated anti-template and naturalness checks may detect generic hooks, generic CTAs, repeated then/after-that transitions, unsupported superlatives, missing causal links, and missing claim IDs. They are screening gates and do not replace human editorial review of sentence rhythm, clarity, tension, and evidence grounding.

## 8. Spoken and displayed text

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

## 9. Voice profile and audition lifecycle

The voice lifecycle is profile-based rather than chapter-based.

### 9.1 Required and reuse paths

If no approved immutable voice profile exists for the project or render configuration, enter VOICE_PROFILE_REQUIRED and generate the four auditions:

1. calm documentarian
2. conversational analyst
3. cinematic storyteller
4. sharp mystery narrator

Every sample uses identical English text, target duration, speed normalization, and loudness normalization. The user selects one audition. The selected voice and normalized settings are persisted as an immutable approved voice profile with a profile ID, content hash, provider/model/voice identifiers, normalized settings, selection record, and creation metadata.

Later chapters and renders reuse that immutable profile without re-auditioning. Four new auditions are required only when no approved profile exists, when the user explicitly requests re-audition, or when a configured voice/provider/speed/loudness change invalidates the current profile for the requested render. The old profile remains immutable and auditable; a new profile is a new selection, not an in-place mutation.

A final render blocks if there is no selected approved immutable profile. A chapter cannot bypass this gate merely because an earlier chapter had auditions.

### 9.2 State transitions

The full lifecycle is:

- VOICE_PROFILE_REQUIRED: no approved profile or explicit invalidation.
- VOICE_AUDITIONING: all four equal-text auditions are being generated and normalized.
- VOICE_PROFILE_SELECTED: the user selected one audition.
- VOICE_PROFILE_PERSISTED: the selected profile and manifest are immutable and available for reuse.
- VOICE_PROFILE_REUSED: an existing immutable profile is attached to the chapter/render.
- FINAL_RENDER_READY: coverage, script, timed text, profile, motion, render, and rights prerequisites are evaluated.

Missing audition, unequal measured duration, missing user selection, mutable profile data, or profile/config mismatch blocks the relevant transition.

### 9.3 Audition manifest

The manifest records sample text ID and hash, exact text, target and measured duration, speed and loudness parameters and measurements, provider/model/voice IDs, timestamp, artifact hash/path, selected profile ID, profile content hash, trigger reason, and pass or block reasons. Equal length is verified from measured artifacts.

Voice audition samples do not represent chapter coverage; they only compare voice characteristics. Their success cannot satisfy panel, segmentation, evidence, or script gates.

## 10. Motion design and QC

Motion has one smooth intent per shot: hold, one directional move, one reveal, or one bounded impact move. It does not combine unrelated oscillations.

The crop-coordinate helper uses:

    even_coord(value, maximum) =
        2 * floor(clamp(value, 0, maximum) / 2)

Clamp after quantization to the valid crop range. Use a corresponding even-size helper for crop/output dimensions. Do not use the incorrect form that multiplies an already pixel-valued coordinate by two. Test odd/even, zero, maximum, and render-level crop validity.

Automatic sinusoidal shake, micro-shake, and orbit are removed. No normal path injects sine or cosine center oscillation. Normal zoom is at most approximately 1.06 and impact zoom at most approximately 1.08. Each shot declares intent, direction, focal target, and reason. Periodic static holds prevent continuous motion. Legacy plans containing removed modes fail validation or are explicitly migrated before render; they are not silently accepted.

Rendered telemetry samples frame-to-frame center and scale and derives displacement/direction, scale delta, acceleration/deceleration, reversal count and locations, maximum scale, static-hold coverage, and intent continuity. Unexpected reversals, acceleration spikes, ceiling violations, missing intent, or removed oscillation signatures block render QC with shot, frame interval, measurement, threshold, and reason.

## 11. Planned schemas

### SourceAssetRecord

    {
      "source_asset_id": "string",
      "chapter_id": "string",
      "source_asset_order": 0,
      "source_reference": "string",
      "dimensions": {"width": 0, "height": 0},
      "content_fingerprint": "string",
      "strip_ids": ["string"],
      "segmentation_version": "string",
      "segmentation_confidence": 0.0,
      "segmentation_status": "complete|ambiguous|blocked",
      "uncertainty_reasons": []
    }

### StripRegionRecord

    {
      "source_asset_id": "string",
      "strip_region_id": "string",
      "panel_id": "string",
      "strip_order": 0,
      "region_order": 0,
      "source_index": 0,
      "region_bounds": {"left": 0, "top": 0, "right": 0, "bottom": 0},
      "normalized_bounds": {"left": 0.0, "top": 0.0, "right": 1.0, "bottom": 1.0},
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
      "segmentation": {"complete": false, "block_reasons": []},
      "coverage": {"complete": false, "block_reasons": []},
      "narrative": {"passed": false, "reasons": []},
      "timed_text": {"passed": false, "reasons": []},
      "voice_profile": {"passed": false, "reasons": []},
      "motion": {"passed": false, "reasons": []},
      "rights": {"publish_allowed": false, "reasons": []},
      "failures": []
    }

The QC report is additive; it does not replace the source reconciliation, coverage manifest, instruction contract record, evidence references, audition manifest, voice profile, or motion telemetry.

## 12. Planned affected modules

These are planned implementation boundaries only. No implementation file is changed by this design correction.

| Module | Planned responsibility |
| --- | --- |
| app/services/ingest.py | Preserve source-asset identity, source order, strip inputs, and content lineage. |
| app/services/segmentation.py | New boundary for deterministic strip/panel segmentation, region bounds, confidence/version, and reconciliation. |
| app/services/analysis.py | Retain text utilities where useful, but never treat text-only analysis as chapter evidence. |
| app/services/vision_adapter.py | New boundary for capability discovery, image requests, structured observations, instruction contract version/hash, and explicit failures. |
| app/services/pipeline.py | Orchestrate segmentation, reconciliation, capability gate, ordered chunks, synthesis, voice-profile lifecycle, render, QC, and rights states. |
| app/services/editorial_visual_planner.py | Map evidence-backed narrative beats to reconciled source visuals with panel auditability. |
| app/services/script.py | Build the story spine, generate Cinematic Story Detective narration, claims, spoken_text, and display_text inputs. |
| app/services/timeline.py | Preserve timings, punctuation-free display derivation, cue grouping, and duration bounds. |
| app/services/tts.py | Generate auditions only when required and create immutable reusable voice profiles. |
| app/services/motion_director.py | Emit one-intent plans, zoom ceilings, holds, and no oscillation modes. |
| app/services/render.py | Apply crop quantization and emit frame motion telemetry. |
| app/services/editorial_qc.py | Evaluate segmentation, reconciliation, coverage, evidence, text, voice profile, auditions, motion, and blocking reasons. |
| app/services/visual_scoring.py and app/services/quality.py | Preserve deterministic visual/reuse QC without weakening evidence gates. |
| app/services/encoders.py | Preserve final media contract checks after editorial gates. |
| app/services/publish.py | Keep rights clearance as an independent hard blocker and expose publish_allowed. |
| tests/ | Add segmentation, prompt-contract, anti-template, lifecycle, integration, and end-to-end regressions for every contract and failure mode. |

## 13. Failure modes and explicit blocking

- No source assets or strips: block with source_inventory_empty.
- Segmentation cannot identify a material boundary or source order: persist segmentation_uncertainties or ordering_uncertainties and block with segmentation_ambiguous or segmentation_order_uncertain.
- A source asset, strip, or detected region is unassigned, orphaned, or missing at any lineage edge: block with source_lineage_reconciliation_failed.
- Segmentation version/confidence or region bounds are missing: block with segmentation_contract_invalid.
- No vision-capable provider: block with vision_capability_missing before script approval.
- Text-only provider, image rejection, unavailable model, or unsupported structured response: block with a specific capability reason; never recap by template.
- Chunk failure, invalid observation, or missing panel: persist affected panels, mark coverage incomplete, and block.
- Unreadable or low-confidence panel: persist it and block when complete or sufficiently certain comprehension is not possible.
- Instruction contract missing, version mismatch, hash mismatch, or required output omitted: block with analyzer_contract_invalid.
- Story spine, claim evidence, or structured narrative output missing: block with narrative_contract_invalid.
- Unestablished source order or material character/entity ambiguity: persist uncertainty and block; do not guess.
- Claim without panel evidence: block with claim_without_evidence.
- Reconciliation mismatch or segmented denominator mismatch: block with coverage_manifest_inconsistent.
- Punctuation transformation or timing mismatch: block timed-text QC.
- No approved voice profile, missing required audition, unequal measured duration, no user selection, mutable profile, or profile/config mismatch: block the voice lifecycle or final render.
- Motion ceiling, intent, acceleration, or reversal failure: block motion QC.
- Unlicensed or uncleared source: retain source_gate_failed and publish_allowed false.

Every failure is visible in job state, manifest, QC report, and structured logs. There is no catch-all path that creates generic recap copy.

## 14. Observability and QC

Emit structured events for source inventory, asset segmentation, strip-region detection, reconciliation, capability check, chunk start/completion, each panel observation, continuity updates, manifest finalization, instruction contract version/hash, each synthesized claim, story-spine completion, script QC, audition generation when triggered, voice-profile selection/persistence/reuse, motion validation, frame telemetry, render QC, and rights QC.

Metrics include total_source_assets, total_strips, segmented_regions_total, segmentation_ambiguous_regions, segmentation_order_uncertainties, unassigned_source_assets, orphaned_regions, inventory_regions_total, processed_panels, duplicate_panels, unreadable_or_low_confidence_panels, regions_missing_observation, regions_missing_chunk, regions_missing_synthesis, ordering_uncertainties, character_ambiguities, evidence_refs_without_panels, chunk_failures, analyzer_contract_failures, anti_template_findings, audition_runs, audition_duration_spread, voice_profile_reuse_count, motion_reversal_count, and blocking_reason_counts.

Persist configuration/schema versions, segmentation version and confidence, source ordering fingerprint, lineage reconciliation, chunk plan, provider/model identity, instruction contract version and hash, request fingerprints, voice-profile content hash, and artifact hashes. Never persist secrets or raw credentials. The operator can trace every claim to source asset and panel regions and every motion failure to frame intervals. QC is fail-closed for this design; rights must pass separately for publish_allowed.

Automated naturalness and anti-template results are observable review signals. They are not a substitute for human editorial review.

## 15. Test strategy

### 15.1 Segmentation and lineage tests

- Inventory every source asset and strip in deterministic order with no random sampler.
- Detect all canonical regions from every strip and persist source_asset_id, strip_region_id, panel_id, source order, region bounds, segmentation confidence, and segmentation version.
- Prove that total_panels equals the persisted segmented denominator and includes duplicate source rows.
- Prove that every asset and region reconciles through inventory, observation, chunking, and synthesis; any orphan, unassigned region, or missing edge blocks.
- Prove that ambiguous boundaries, merged or split regions, and uncertain order remain in the manifest and block when material.
- Prove that segmentation and ordering fingerprints change when source lineage changes.

### 15.2 Vision, contract, and narrative tests

- Complete deterministic enumeration has no representative-panel path and no random sampler.
- Sequential chunks are contiguous and overlapping; continuity conflicts become explicit ambiguity.
- Capability checks reject absent and text-only providers.
- Instruction-contract snapshot tests verify the exact contract version, required MUST clauses, required story spine, and canonicalization/hash behavior.
- Structured-output tests require observations, dialogue/OCR, visible facts, inferences, uncertainties, continuity ledger, evidence graph, coverage manifest, narrative outline, and claim-ID script passages.
- Anti-template tests reject generic hooks/CTAs, fake hyperbole, repeated then/after-that chronology, unsupported claims, and missing causal transitions on deterministic fixtures.
- Naturalness tests check varied sentence rhythm and causal transitions on fixture outputs.
- The automated checks are screening gates only; tests explicitly document that human editorial review remains required.

### 15.3 Timed text, voice, and motion tests

- Unicode punctuation removal leaves no punctuation and preserves timed word tokens.
- Existing 4 to 7 word, two-line, semantic-boundary, and media-duration gates remain active.
- With no approved profile, four auditions use byte-identical text, normalized settings, and measured equal duration.
- With an approved immutable profile, later chapters reuse the profile without new auditions.
- An explicit re-audition request or voice configuration change triggers exactly the required new audition lifecycle.
- Missing profile or selection blocks final render.
- Profile content is immutable and profile hash is persisted.
- The even-pixel formula passes odd/even, zero, maximum, and render-level crop validity.
- Motion rejects shake, micro-shake, orbit, sine-based center oscillation, scale overages, missing intent, and unexpected reversals.

### 15.4 Integration and acceptance fixtures

Fixtures cover a complete short chapter, a long chapter with overlapping chunks and entity transition, multiple strips per asset, duplicates plus unreadable/low-confidence panels, uncertain boundaries and order, material character ambiguity, spoken/display timing alignment, audit-triggered and profile-reuse voice lifecycles, bounded motion telemetry, anti-template narrative failures, and a rights failure that leaves publish_allowed false.

The acceptance run persists source reconciliation, the segmented coverage manifest, evidence references, instruction contract version/hash, script outputs, audition/profile manifest, motion telemetry, QC report, and artifact hashes. The report states exact asset, strip, region, panel, gate, and voice-profile results before any final render assertion.

## 16. Acceptance criteria

1. Every source asset and strip is deterministically segmented or represented in a persisted blocked manifest.
2. Every detected canonical panel or region enters the ordered inventory; no sample, truncation, orphan, or unassigned source region is silently dropped.
3. The coverage denominator is the persisted segmented panel inventory and reconciles from source assets through observations, chunks, and synthesis.
4. The manifest contains segmentation version/confidence, source asset and region lineage, all required counts, uncertainty lists, and claim-to-panel references.
5. Long chapters use sequential overlapping chunks, continuity, and chapter-wide synthesis over complete evidence.
6. Incomplete segmentation or reconciliation, insufficient confidence, material ambiguity, missing evidence, missing vision capability, malformed provider output, or analyzer-contract failure blocks script approval.
7. The versioned analyzer instruction contract is hashed and persisted, and its normative output and story-spine requirements are enforced by tests.
8. No code path silently falls back to a rule-based template recap. Automated anti-template checks do not replace human editorial review.
9. Narration satisfies Cinematic Story Detective and the stated language, rhythm, causal, evidence, and anti-invention constraints.
10. spoken_text preserves punctuation for TTS; display_text has no Unicode punctuation and preserves word timings.
11. Four identical-text, equal-length normalized auditions are generated only when no approved profile exists or explicit invalidation occurs. The selected immutable profile is persisted and reused for later chapters/renders without mandatory re-audition.
12. Final render blocks if no selected approved immutable voice profile exists.
13. Motion uses the corrected formula, no automatic shake/micro-shake/orbit, the 1.06/1.08 ceilings, one intent, static holds, and frame telemetry QC.
14. Operators can trace claims to source assets and panel regions, and motion failures to frame intervals.
15. Rights clearance remains an independent hard blocker.
16. Rollout and rollback preserve the Git and worktree constraints.

## 17. Rollout

Stage 0 defines source segmentation/reconciliation, analyzer contract, coverage schema, capability checks, voice-profile schema, fixtures, and a vision-first feature boundary; no publication path is enabled.

Stage 1 runs deterministic asset segmentation, ordered inventory, sequential chunks, continuity, reconciliation, contract validation, and synthesis on controlled fixtures. Any missing provider, source region, panel, or evidence blocks rather than recaps.

Stage 2 runs vision-first in review-only shadow against selected jobs. The existing text analyzer may be compared but cannot satisfy coverage or silently take over. Voice auditions run only for jobs without an approved profile or with explicit invalidation.

Stage 3 enables opt-in production with complete lineage manifest, evidence-grounded script, valid instruction hash, required voice-profile lifecycle, user selection, motion QC, render QC, and rights QC. Later chapters reuse the selected immutable profile.

Stage 4 makes full vision-first comprehension and profile reuse the default evidence/render path and retires the rule-based recap path from this workflow. Any separate non-vision workflow must be explicitly named and never selected by error recovery.

## 18. Rollback

Disable the vision-first workflow at a controlled job boundary and mark jobs for manual review. Do not automatically substitute a template recap. Preserve manifests, lineage, instruction hashes, profiles, and artifacts for diagnosis.

If implementation commits are reverted, revert only named implementation commits in the authoritative VPS checkout. Preserve source, media, databases, credentials, user data, and unrelated work. A rollback is complete only when segmentation, capability, coverage, or contract failure remains visible and cannot bypass the rights gate or voice-profile selection gate.

## 19. Git/worktree constraints

- Work in /home/yusronrohmani/manhwashorts on the VPS.
- Inspect Git status first; if dirty, stop and report.
- This task owns only docs/superpowers/specs/2026-08-05-vision-first-editorial-story-engine-design.md.
- Do not edit implementation, generate audio, render video, alter source assets, change databases or user data, or touch credentials/environment files.
- Do not push. Commit only this design document with an intentional corrective message.
- Preserve existing work; never use destructive reset or checkout commands.
- Future implementation uses an isolated worktree or explicitly approved clean VPS surface and keeps generated runtime state out of source commits.

## 20. Explicit non-goals

- Implementing segmentation, the analyzer adapter, the instruction contract, schemas, motion changes, QC, or tests in this design-only correction.
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
