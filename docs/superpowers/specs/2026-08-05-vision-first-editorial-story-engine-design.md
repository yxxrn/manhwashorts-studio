# Vision-First Editorial Story Engine Design

Status: Approved design for implementation planning
Date: 2026-08-05
Decision owner: Sol High
Scope: design only. This commit changes no implementation, generates no audio or video, and touches no source assets.

## 1. Decision and invariants

Keep the Editorial Story Engine from approach 2. Replace its evidence stage with full vision-first chapter comprehension. Script approval is possible only after the complete ordered chapter has been understood and the evidence gates pass.

The design invariants are:

1. Analyze every panel in deterministic source order. Never random-sample representative panels and never treat a sample as chapter coverage.
2. Process long chapters as ordered sequential chunks with overlap, carry entity and character continuity across chunks, and perform a chapter-wide synthesis over the complete canonical observation set.
3. Persist a coverage manifest containing total panels, processed panels, duplicates, unreadable or low-confidence panels, ordering uncertainties, character ambiguities, and claim-to-panel evidence references.
4. Block script approval when coverage is incomplete or evidence is too uncertain. Never silently fall back to a rule-based template recap.
5. Use the narrative identity Cinematic Story Detective: conversational American English, human sentence rhythm, causal storytelling, motives and consequences, and restrained evidence-grounded hidden clues. Do not use rigid chronology, fake hyperbole, generic CTA, or invented facts.
6. Separate spoken_text from display_text. spoken_text keeps punctuation for TTS prosody. display_text removes all Unicode punctuation, including apostrophes and hyphens, while preserving word timing alignment.
7. Generate four equal-length English voice auditions from identical text with speed and loudness normalization plus a manifest. The user chooses a voice before final render.
8. Correct even-pixel crop quantization; remove automatic sinusoidal shake, micro-shake, and orbit; cap normal zoom near 6 percent and impact zoom near 8 percent; use one smooth intent per shot, periodic static holds, and frame-to-frame center, scale, acceleration, and reversal QC.
9. The existing analyzer is text-only. Require a multimodal adapter and capability check, and explicitly block when no vision-capable provider is configured.
10. Keep rights and publication gates independent and hard. A rights failure leaves publish_allowed false.

These are acceptance criteria, not optional style guidance.

## 2. Architecture and data flow

    source chapter
      -> deterministic panel inventory
      -> multimodal capability gate
      -> ordered sequential chunk planner
      -> panel-level vision observations
      -> cross-chunk entity and character continuity ledger
      -> coverage manifest and evidence graph
      -> chapter-wide narrative synthesis
      -> spoken_text and display_text timing contracts
      -> four voice auditions
      -> user voice selection
      -> shot and motion planning
      -> render and frame telemetry
      -> editorial QC and rights gate
      -> review artifact or publication decision

The capability gate runs before script approval. Chunks are contiguous source ranges with deterministic overlap; overlap supplies context and does not replace canonical observation of any panel. Chapter-wide synthesis consumes all canonical observations, the continuity ledger, and the evidence graph, never only chunk summaries.

Required state transitions are INVENTORIED, VISION_CHECKED, OBSERVING, COVERAGE_COMPLETE, SCRIPT_APPROVED, VOICE_AUDITIONED, VOICE_SELECTED, MOTION_QC_PASSED, RENDER_QC_PASSED, and PUBLISH_ALLOWED. A blocked transition records its reason and stops. It does not produce a template recap.

## 3. Full vision-first comprehension

### 3.1 Inventory and ordering

The source inventory supplies stable panel_id, source_index, chapter_id, source reference, dimensions, and a content fingerprint. If source order cannot be established, persist ordering_uncertainties and block before script approval. Persist an ordering fingerprint derived from ordered panel identifiers and source references.

Every source panel passes through the observation path. Duplicate detection is recorded for audit and evidence reconciliation, not used to skip coverage. Each duplicate retains its own source record and points to a canonical duplicate group. Chunk-overlap replays are recorded separately from source duplicates.

### 3.2 Chunks and continuity

The chunk planner uses a deterministic configured maximum that respects provider image and context limits. It persists chunk_id, source start and end indices, canonical panel IDs, overlap panel IDs, provider limits, ordering fingerprint, and plan version.

The continuity ledger carries entity and character identifiers, aliases and visual anchors, locations, relationships, motivation hypotheses, state transitions, unresolved identity questions, supporting panel IDs, and confidence. Conflicting overlap observations are retained as ambiguity or reconciliation evidence rather than silently overwritten. The ledger cannot invent continuity for an unobserved panel.

### 3.3 Multimodal adapter

The current app/services/analysis.py analyzer is text-only and cannot satisfy this evidence stage alone. The planned adapter accepts image-bearing requests and returns structured visual observations. Its capability check must verify configured provider, image input, structured observation support, selected model and chunk limits, and recordable provider/model identity.

No vision-capable provider, text-only provider, unavailable model, rejected image, or unsupported response contract blocks with an explicit reason such as vision_capability_missing or vision_input_unsupported. Text generated after a valid vision observation may be used by later stages, but text-only analysis is never chapter coverage. Provider errors, empty responses, and low-confidence responses never become a rule-based recap.

A canonical panel observation contains panel_id, source_index, readable status, confidence, visible characters and entity anchors, actions, expressions, objects, locations, scene transitions, visible text or dialogue regions when available, visual relationships and state changes, unresolved ambiguities, evidence spans, provider/model, request fingerprint, and observation version. What is seen is separate from what is inferred; every inference carries confidence and supporting panel IDs.

## 4. Coverage manifest and evidence graph

The persisted manifest is the source of truth for approval. Its normative information shape is:

    {
      "chapter_id": "string",
      "inventory_version": "string",
      "ordering_fingerprint": "string",
      "total_panels": 0,
      "processed_panels": 0,
      "duplicates": [
        {"panel_id": "string", "duplicate_group_id": "string",
         "canonical_panel_id": "string", "source_index": 0}
      ],
      "unreadable_or_low_confidence_panels": [
        {"panel_id": "string", "source_index": 0,
         "reason": "string", "confidence": 0.0}
      ],
      "ordering_uncertainties": [{"source_indices": [0, 1], "reason": "string"}],
      "character_ambiguities": [
        {"panel_ids": ["string"], "entity_id": "string",
         "reason": "string", "confidence": 0.0}
      ],
      "claim_to_panel_evidence_refs": [
        {"claim_id": "string", "panel_ids": ["string"],
         "evidence_spans": ["string"], "confidence": 0.0}
      ],
      "chunk_plan": {"chunks": [], "overlap_panels": []},
      "coverage_complete": false,
      "evidence_sufficient": false,
      "block_reasons": []
    }

processed_panels counts unique source panel IDs with valid canonical observations. Unreadable or below-policy-confidence panels remain visible and do not satisfy coverage. Duplicate and overlap metadata must reconcile total source panels with considered observations.

Every factual or causal claim reaching the script has a claim_id, claim type, confidence, and claim-to-panel references. A qualified inference still needs panel evidence. A claim without evidence, or with unresolved identity that changes its meaning, blocks script approval.

## 5. Narrative synthesis

Cinematic Story Detective permits a clear consequence, mystery, or revealing visual to open the narration and then backfill causes. It does not require rigid panel chronology, but every transition and claim must be explainable from the complete evidence graph and continuity ledger. Unknown or ambiguous facts are qualified or omitted.

The script metadata records claim IDs and evidence references for each passage. Approval requires coverage_complete, evidence_sufficient, no blocking ordering uncertainty, no material character ambiguity, evidence for every factual or causal claim, and narrative identity checks. A failed check is an auditable block, not an automatic template rewrite.

## 6. Spoken and displayed text

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

## 7. Voice auditions

Generate four English auditions from one identical sample text:

1. calm documentarian
2. conversational analyst
3. cinematic storyteller
4. sharp mystery narrator

Every sample uses identical text, target duration, speed normalization, and loudness normalization. The manifest records sample text ID and hash, exact text, target and measured duration, speed and loudness parameters and measurements, provider/model/voice IDs, timestamp, artifact hash/path, and pass or block reasons. Equal length is verified from measured artifacts.

The user must select one audition before a final render can enter VOICE_SELECTED. Voice audition samples do not represent chapter coverage; they only compare voice characteristics. Their success cannot satisfy panel, evidence, or script gates.

## 8. Motion design and QC

Motion has one smooth intent per shot: hold, one directional move, one reveal, or one bounded impact move. It does not combine unrelated oscillations.

The crop-coordinate helper uses:

    even_coord(value, maximum) =
        2 * floor(clamp(value, 0, maximum) / 2)

Clamp after quantization to the valid crop range. Use a corresponding even-size helper for crop/output dimensions. Do not use the incorrect form that multiplies an already pixel-valued coordinate by two. Test odd/even, zero, maximum, and render-level crop validity.

Automatic sinusoidal shake, micro-shake, and orbit are removed. No normal path injects sine or cosine center oscillation. Normal zoom is at most approximately 1.06 and impact zoom at most approximately 1.08. Each shot declares intent, direction, focal target, and reason. Periodic static holds prevent continuous motion. Legacy plans containing removed modes fail validation or are explicitly migrated before render; they are not silently accepted.

Rendered telemetry samples frame-to-frame center and scale and derives displacement/direction, scale delta, acceleration/deceleration, reversal count and locations, maximum scale, static-hold coverage, and intent continuity. Unexpected reversals, acceleration spikes, ceiling violations, missing intent, or removed oscillation signatures block render QC with shot, frame interval, measurement, threshold, and reason.

## 9. Planned schemas

### CapabilityReport

    {
      "configured": true,
      "vision_capable": true,
      "provider_id": "string",
      "model_id": "string",
      "accepts_images": true,
      "structured_observation_version": "string",
      "limits": {"max_images": 0, "max_context": 0},
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
      "coverage": {"complete": false, "block_reasons": []},
      "narrative": {"passed": false, "reasons": []},
      "timed_text": {"passed": false, "reasons": []},
      "voice_auditions": {"passed": false, "reasons": []},
      "motion": {"passed": false, "reasons": []},
      "rights": {"publish_allowed": false, "reasons": []},
      "failures": []
    }

The QC report is additive; it does not replace the coverage manifest, evidence references, audition manifest, or motion telemetry.

## 10. Planned affected modules

These are planned implementation boundaries only. No implementation file is changed by this design commit.

| Module | Planned responsibility |
| --- | --- |
| app/services/analysis.py | Retain text utilities where useful, but never treat text-only analysis as chapter evidence. |
| app/services/vision_adapter.py | New boundary for capability discovery, image requests, structured observations, request fingerprints, and explicit failures. |
| app/services/pipeline.py | Orchestrate inventory, capability gate, ordered chunks, synthesis, auditions, selection, render, QC, and rights states. |
| app/services/editorial_visual_planner.py | Map evidence-backed narrative beats to source visuals with panel auditability. |
| app/services/script.py | Generate Cinematic Story Detective narration, claims, spoken_text, and display_text inputs. |
| app/services/timeline.py | Preserve timings, punctuation-free display derivation, cue grouping, and duration bounds. |
| app/services/tts.py | Generate normalized auditions and enforce user selection before final render. |
| app/services/motion_director.py | Emit one-intent plans, zoom ceilings, holds, and no oscillation modes. |
| app/services/render.py | Apply crop quantization and emit frame motion telemetry. |
| app/services/editorial_qc.py | Evaluate coverage, evidence, text, auditions, motion, and blocking reasons. |
| app/services/visual_scoring.py and app/services/quality.py | Preserve deterministic visual/reuse QC without weakening evidence gates. |
| app/services/encoders.py | Preserve final media contract checks after editorial gates. |
| app/services/publish.py | Keep rights clearance as an independent hard blocker and expose publish_allowed. |
| tests/ | Add unit, fixture, integration, and end-to-end regressions for every contract and failure mode. |

## 11. Failure modes and explicit blocking

- No vision-capable provider: block with vision_capability_missing before script approval.
- Text-only provider, image rejection, unavailable model, or unsupported structured response: block with a specific capability reason; never recap by template.
- Chunk failure, invalid observation, or missing panel: persist affected panels, mark coverage incomplete, and block.
- Unreadable or low-confidence panel: persist it and block when complete or sufficiently certain comprehension is not possible.
- Unestablished source order: persist ordering_uncertainties and block.
- Material character/entity ambiguity: persist character_ambiguities and block the affected claim or script; do not guess.
- Claim without panel evidence: block with claim_without_evidence.
- Reconciliation mismatch: block with coverage_manifest_inconsistent.
- Punctuation transformation or timing mismatch: block timed-text QC.
- Missing or unequal audition: block audition completion and final voice selection.
- No user voice selection: block final render.
- Motion ceiling, intent, acceleration, or reversal failure: block motion QC.
- Unlicensed or uncleared source: retain source_gate_failed and publish_allowed false.

Every failure is visible in job state, manifest, QC report, and structured logs. There is no catch-all path that creates generic recap copy.

## 12. Observability and QC

Emit structured events for inventory creation, capability check, chunk start/completion, each panel observation, continuity updates, manifest finalization, each synthesized claim, script QC, each normalized audition, voice selection, motion validation, frame telemetry, render QC, and rights QC.

Metrics include total_panels, processed_panels, duplicate_panels, unreadable_or_low_confidence_panels, ordering_uncertainties, character_ambiguities, evidence_refs_without_panels, chunk_failures, audition_duration_spread, motion_reversal_count, and blocking_reason_counts.

Persist configuration/schema versions, source ordering fingerprint, chunk plan, provider/model identity, instruction version, request fingerprints, and artifact hashes. Never persist secrets or raw credentials. The operator can trace every claim to panels and every motion failure to frame intervals. QC is fail-closed for this design; rights must pass separately for publish_allowed.

## 13. Test strategy

Unit tests must cover complete deterministic enumeration with no random sampler, duplicates and overlap accounting, contiguous chunk planning, continuity conflicts, manifest reconciliation, missing claim evidence, absent/text-only capability, provider failure and no-template behavior, all Unicode punctuation removal with unchanged timed tokens, existing 4 to 7 word and two-line caption gates, identical audition text and measured equal duration, the even-pixel formula, removed oscillation modes, scale ceilings, missing intent, and reversal/acceleration QC.

Integration fixtures must cover a complete short chapter, a long chapter with overlapping chunks and an entity transition, duplicates plus unreadable/low-confidence panels, uncertain order, material character ambiguity, spoken/display timing alignment, four auditions that cannot mark coverage complete, bounded motion telemetry, and a rights failure that leaves publish_allowed false.

The acceptance run persists the coverage manifest, evidence references, script outputs, audition manifest, motion telemetry, QC report, and artifact hashes. The report states exact panel counts, gate results, and selected voice before any final render assertion.

## 14. Acceptance criteria

1. Every source panel is processed in deterministic order or is represented in a persisted blocked manifest.
2. Long chapters use sequential overlapping chunks, continuity, and chapter-wide synthesis over complete evidence.
3. The manifest contains all required counts, uncertainty lists, and claim-to-panel references.
4. Incomplete coverage, insufficient confidence, material ambiguity, missing evidence, missing vision capability, and malformed provider output block script approval.
5. No code path silently falls back to a rule-based template recap.
6. Narration satisfies Cinematic Story Detective and the stated language, rhythm, causal, evidence, and anti-invention constraints.
7. spoken_text preserves punctuation for TTS; display_text has no Unicode punctuation and preserves word timings.
8. Four identical-text, equal-length normalized auditions are manifested, user choice is required before final render, and the record states that auditions do not represent chapter coverage.
9. Motion uses the corrected formula, no automatic shake/micro-shake/orbit, the 1.06/1.08 ceilings, one intent, static holds, and frame telemetry QC.
10. Operators can trace claims and motion failures to evidence.
11. Rights clearance remains an independent hard blocker.
12. Rollout and rollback preserve the Git and worktree constraints.

## 15. Rollout

Stage 0 defines contracts, capability checks, fixtures, and a vision-first feature boundary; no publication path is enabled.

Stage 1 runs ordered inventory, chunks, continuity, and synthesis on controlled fixtures. Any missing provider, panel, or evidence blocks rather than recaps.

Stage 2 runs vision-first in review-only shadow against selected jobs. The existing text analyzer may be compared but cannot satisfy coverage or silently take over.

Stage 3 enables opt-in production with complete manifest, evidence-grounded script, four auditions, user selection, motion QC, render QC, and rights QC. Review artifacts may exist with publish_allowed false.

Stage 4 makes full vision-first comprehension the default evidence stage and retires the rule-based recap path from this workflow. Any separate non-vision workflow must be explicitly named and never selected by error recovery.

## 16. Rollback

Disable the vision-first workflow at a controlled job boundary and mark jobs for manual review. Do not automatically substitute a template recap. Preserve manifests and artifacts for diagnosis.

If implementation commits are reverted, revert only named implementation commits in the authoritative VPS checkout. Preserve source, media, databases, credentials, user data, and unrelated work. A rollback is complete only when capability or coverage failure remains visible and cannot bypass the rights gate.

## 17. Git/worktree constraints

- Work in /home/yusronrohmani/manhwashorts on the VPS.
- Inspect Git status first; if dirty, stop and report.
- This task owns only docs/superpowers/specs/2026-08-05-vision-first-editorial-story-engine-design.md.
- Do not edit implementation, generate audio, render video, alter source assets, change databases or user data, or touch credentials/environment files.
- Do not push. Commit only this design document with an intentional message.
- Preserve existing work; never use destructive reset or checkout commands.
- Future implementation uses an isolated worktree or explicitly approved clean VPS surface and keeps generated runtime state out of source commits.

## 18. Explicit non-goals

- Implementing the engine, adapter, schemas, motion changes, QC, or tests in this design-only commit.
- Generating audio, auditions, preview media, or final video now.
- Selecting a provider, voice, prompt wording, or chapter-specific editorial outcome beyond these approved invariants.
- Replacing approach 2.
- Random panel sampling, representative-panel coverage, text-only evidence shortcuts, or silent template fallback.
- Inferring facts, identities, motives, or hidden clues without panel evidence.
- Treating voice auditions as chapter coverage.
- Bypassing rights or resolving conflicting speech balloons without rights-cleared or text-clean assets.
- Changing unrelated modules, documentation, source media, databases, or user and OmniVoice data.
- Pushing to any remote Git repository.
