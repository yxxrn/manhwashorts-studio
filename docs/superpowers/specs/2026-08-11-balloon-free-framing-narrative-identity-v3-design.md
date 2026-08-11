# Design: Balloon-Free Framing and Narrative Identity v3

Status: Approved design checkpoint. This document records the design only; it
does not change the production contract until the slices below pass their own
RED/GREEN gates.

Date: 2026-08-11

Selector boundary: the visual behavior applies only when the project selects
the reference-matched profile. The narrative behavior applies only when the
project selects sharp_friend_v1. Legacy/default behavior remains unchanged
until a later implementation slice explicitly selects one of these profiles.

## 1. Context and current evidence

### Current implementation evidence

The VPS baseline is main at f9221dd546a24f6c18a7f891b2ded8e1c678c3f2 and was
clean when this checkpoint began. The last recorded verification at that
commit is 635 passed in the full non-slow suite. That is historical checkpoint
evidence, not a test rerun for this documentation-only commit.

The current reference framing path is in app/services/render.py:

- prepare_reference_frame searches deterministic crop scales from 1.0 through
  the selected profile base_frame_zoom_max, currently 1.35.
- _reference_content_stats classifies a sampled pixel as blank when all RGB
  channels are at least 245 and their spread is at most 10. This is a
  near-white pixel heuristic, not a color-agnostic blank definition.
- max_blank_fraction=0.18 contributes a candidate score bonus or penalty. It
  is not a hard feasibility gate. A selected crop may therefore remain above
  0.18.
- reference_frame_cache_key already includes focus, end focus, profile hash,
  base zoom, and max blank fraction. The new mask inputs must extend this key.
- editorial_frame routes reference frames through prepare_reference_frame and
  keeps the legacy crop_to_vertical path when no profile is selected.

The current balloon signal is only heuristic. visual_scoring.py
_layout_dominance derives speech_balloon_dominance from bright-pixel ratio,
edge density, and top/bottom variance. It does not persist a balloon polygon,
mask, or confidence-bearing region. PanelRegion currently provides
observation_json, evidence_refs_json, and coverage_map_hash, which are
available JSON persistence boundaries, but there is no dedicated balloon
geometry column.

Stable camera motion is already guarded by motion_director.py, including an
allowed curve set, forbidden legacy curve detection, monotonic curve sampling,
and zoom caps. shot_director.py and editorial_visual_planner.py select and
persist camera intent, curve, ROI, and alignment reasons. The new framing
contract must provide a static prepared window to that motion path; it must
not reintroduce shake, orbit, reversal, or per-frame crop detection.

The current narrative resource and validator are
app/prompts/vision_first_story_analyzer_v2.txt and
app/services/analyzer_contract.py. They correctly require full ordered panel
coverage, evidence-linked claims, qualified interpretations, and a
Cinematic Story Detective identity. They also force exactly five roles,
per-role word budgets, a 90-125 total, a mandatory question-ending
payoff_open_loop, and several checklist-shaped output requirements. Those
constraints are useful provider gates but can make prose sound assembled
rather than like a friendly human narrator.

### Repository and transport boundary

The authoritative source and test environment is
/home/yusronrohmani/manhwashorts on the VPS through SSH alias google. The VPS
has no usable GitHub SSH authentication. Windows is transport and publication
orchestration only, using the isolated clone
C:/Users/yxxrn/Documents/AutoManhwa/transport-publish-f9221dd-20260811.

All source, test, lint, compile, and render commands run on the VPS. Exact
history is transferred with a mechanically created Git bundle or equivalent
object transport, applied to the isolated Windows clone with fast-forward-only
updates, and pushed over HTTPS. No force push, tags, unrelated branches,
runtime artifacts, databases, credentials, or media are part of this design.

## 2. Design goals and non-goals

Goals:

1. Remove speech-balloon text from every reference-mode crop whenever geometry
   is known, with a hard zero intersection requirement.
2. Remove edge-connected low-information padding without erasing meaningful
   art, faces, action, effects, or continuity-critical context.
3. Keep all crop decisions deterministic, auditable, source-order aware, and
   compatible with the existing stable monotonic camera path.
4. Replace the rigid v2 narration shape with a versioned, evidence-grounded
   conversational identity that still has measurable screening and explicit
   human approval.
5. Keep spoken narration and one-word display subtitles as separate values.

Non-goals:

- Choosing a local or API voice provider, generating audio, auditioning voices,
  or changing TTS credentials. Voice generation is deliberately deferred until
  the user chooses local versus API execution.
- Copying source panels, speech-balloon text, channel marks, narration, music,
  or other third-party media.
- Adding visual effects, shake, procedural overlays, or a rights bypass.
- Treating OCR text alone as visual evidence or silently accepting an unknown
  balloon mask.
- Removing the existing v2 or legacy contracts before a separately approved
  migration.

## 3. Approved visual contract: COLOR_AGNOSTIC_BALLOON_FREE_V1

COLOR_AGNOSTIC_BALLOON_FREE_V1 is a reference-mode framing and QC contract.
It is selected explicitly and is not applied to legacy/default projects.

### 3.1 Hard output rules

- The rendered crop must have balloon_mask_intersection_ratio exactly 0.0.
  Any positive intersection, including a boundary-overlap pixel after
  rasterization, is a blocking failure. There is no silent allow path.
- A speech_bubble or speech_balloon ROI is an exclusion mask, never an output
  selection or motion target.
- Visible edge-connected low-information padding has target fraction 0.0.
  If that target is infeasible without violating protected subject/action
  coverage or native-resolution limits, the crop is still deterministic but
  records the infeasibility telemetry and fallback reason. This is an
  auditable quality target, not permission to cut meaningful artwork.
- A missing or unreliable balloon mask is not the same as an empty mask.
  Unknown geometry blocks the reference crop; it may not be treated as empty.
- The prepared window is static for a shot. Existing smooth monotonic camera
  motion runs inside that window, with no per-frame detector, shake,
  micro-shake, orbit, whip, sinusoidal oscillation, or reversal.
- Close-up is preferred over visible blank padding only after subject/action
  and continuity guards pass.

### 3.2 Color-agnostic low-information detection

Brightness or whiteness is not the blank definition. A bright pixel may be
important art, and a dark, gray, colored, or mildly graded border may be
padding. Brightness can be retained as diagnostic telemetry but cannot decide
the mask.

The deterministic detector operates on a fixed-size feature grid derived from
the original source before resizing. The grid is capped at 256 cells along
the longer dimension and uses the same grid dimensions for every candidate of
that source. Each cell records normalized:

1. local rank-normalized variance and entropy,
2. gradient and edge density,
3. texture energy at two fixed scales, and
4. saliency from typed vision regions, face/action/effect evidence, and
   continuity context.

The first three feature families are invariant to absolute color choice.
For a versioned calibration, a cell is low-information when at least three of
the four structure tests are below their fixed thresholds:

- rank-normalized variance at most 0.08,
- normalized entropy at most 0.20,
- edge density at most 0.08, and
- two-scale texture energy at most 0.08.

Cells with protected saliency are never classified as discardable solely from
those low-information tests. The thresholds, grid size, connectivity, and
feature normalization are part of the contract version and the framing
telemetry; changing them changes the contract hash.

Run an eight-neighbor flood fill from all four source borders over candidate
low-information cells. Only the border-connected union is padding. An
internal low-information island surrounded by art remains meaningful
background unless the visual evidence explicitly classifies it as a
non-story region. This distinguishes page gutters from a white costume,
black shadow, gray sky, colored wall, effect field, or a meaningful empty
composition.

The detector must recognize solid white, black, gray, arbitrary-color, and
mild-gradient edge padding in fixtures without using a color threshold.
Meaningful white or black art surrounded by non-padding context must remain
protected in the same test matrix.

### 3.3 Protected regions and feasibility

The vision evidence contract supplies typed regions for subject, face, action,
effect, and continuity_context. Each region has a normalized polygon or
bounding box, confidence, source, and requiredness. The crop candidate must
meet these minimum retained-area ratios:

- required subject or face: at least 0.98 of the evidence area,
- required action or continuity_context: at least 0.95,
- required effect: at least 0.90.

A candidate that fails a required region is infeasible even if its blank score
is excellent. The ranking prefers higher retained protected saliency and lower
edge-connected blank area, then lower crop enlargement, then the stable
source-space box order. It never ranks a crop by a guessed story detail.

The dynamic crop ceiling is quality- and subject-constrained:

  max_zoom = min(profile.base_frame_zoom_max,
                 source_resolution_zoom_cap,
                 protected_region_zoom_cap)

The existing reference profile ceiling is 1.35, but it is only an upper
bound. source_resolution_zoom_cap is the greatest candidate zoom for which the
crop width and height remain at least the final frame dimensions divided by
the existing 1.15 oversample factor. protected_region_zoom_cap is the
greatest candidate zoom that still meets every required-region ratio and keeps
each required face/action polygon inside a 0.03 normalized safe-area margin.
If no candidate satisfies native resolution and protection, the implementation
must use the stated fallback order and eventually reject; it must not exceed
1.35 or silently pixelate a small source.

Candidate scales use deterministic 0.02 increments from 1.00 through the
computed ceiling, plus the exact ceiling when it is not on that increment.
The winning box is clamped to source bounds and even dimensions where the
downstream motion path requires them. The tie-break is, in order:
balloon exclusion success, protected-area score, edge-connected blank
fraction, focus proximity, lower zoom, top coordinate, left coordinate.

### 3.4 Required fallback order

If no candidate satisfies all hard requirements, use exactly this order:

1. an alternate ROI on the same panel that has its own valid evidence and
   distinct source-space geometry;
2. a tighter quality-safe crop of that panel, still respecting all required
   regions, source resolution, and balloon zero intersection;
3. a different renderable panel from the same story beat, with an explicit
   evidence-context alignment reason;
4. a stable visual_unavailable rejection.

The renderer must not silently return a crop containing a known or unknown
speech balloon. A rejection records the first failed hard constraint and the
fallback attempts in safe telemetry. It never reveals provider payloads or
credentials.

### 3.5 Required framing telemetry

Every reference shot records a versioned framing result with:

- source_asset_id, panel_id, story beat, and source order;
- chosen crop_box in original pixels and normalized coordinates;
- base_zoom and the computed source/protected zoom ceilings;
- edge_connected_blank_fraction and balloon_mask_intersection_ratio;
- subject_coverage, face_coverage, action_coverage, effect_coverage, and
  continuity_context_coverage;
- mask confidence and mask source for each relevant region;
- candidate count, selected ROI identity, fallback reason, and rejection code;
- the visual contract version/hash and the evidence hash used for selection.

The cache key includes all source hashes, region geometry and confidence,
mask-status values, focus inputs, profile hash, detector version, and target
geometry. A cached crop from a different mask or evidence version is invalid.

## 4. Evidence contract

### 4.1 Typed region records

The runtime contract defines immutable records equivalent to:

- BalloonRegionEvidence: region_id, kind, normalized_bbox,
  normalized_polygon, confidence, evidence_source, mask_status.
- ProtectedRegionEvidence: region_id, kind, normalized_bbox,
  normalized_polygon, confidence, evidence_source, required,
  minimum_coverage.
- PanelVisualEvidence: contract_version, panel_id, source_asset_id,
  source_order, balloon_regions, protected_regions, balloon_mask_status,
  mask_confidence, evidence_hash.

kind is one of speech_balloon, speech_caption, subject, face, action, effect,
continuity_context, or other explicitly reviewed visual content. The
normalized polygon is optional only when a valid bbox is available; at least
one geometry form is required for a known nonempty region.

mask_status is one of known_nonempty, known_empty, or unknown. known_empty is
valid only when the full vision observation or a human-reviewed visual record
explicitly establishes that no balloon geometry exists. An absent field,
missing provider result, or low-confidence detector is unknown and blocks.

evidence_source is one of vision, ocr_geometry_adapter, human_review, or
none. OCR geometry can refine a vision result, but OCR text without geometry
cannot make a balloon mask known.

### 4.2 Persistence and provenance

Persist the records inside the existing PanelRegion observation_json when its
JSON boundary is sufficient, together with contract version and canonical
evidence hash. Keep evidence_refs_json for claim/panel linkage. Do not add a
schema migration merely to duplicate JSON fields. A migration is justified
only if a proven query/index requirement cannot be met by the current JSON
boundary and the migration is separately reviewed.

The record must reconcile to the complete segmented source inventory: every
source asset, panel region, observation, chunk, synthesis claim, and selected
crop retains its source ID, source order, original checksum, dimensions,
source bounds, and coverage map hash. No random sampling, representative-panel
shortcut, text-only fallback, or unexplained source disappearance is allowed.

The canonical hash is compact sorted-key UTF-8 JSON over the version, lineage,
geometry, mask status, confidence, evidence source, and protected region
requirements. It is persisted with the analysis and copied into framing
telemetry. Hash mismatches block use.

### 4.3 Stable visual evidence errors

The implementation uses stable safe codes:

- visual.balloon_mask_unknown
- visual.balloon_geometry_invalid
- visual.balloon_mask_overlap
- visual.blank_infeasible
- visual.subject_coverage_insufficient
- visual.action_coverage_insufficient
- visual.effect_coverage_insufficient
- visual.source_resolution_insufficient
- visual.evidence_hash_mismatch
- visual.visual_unavailable

Findings contain only source order, region IDs, measured ratios, and bounded
reason codes. They never contain image bytes, OCR payloads, provider secrets,
raw provider responses, or storage paths.
## 5. Approved narrative contract: sharp_friend_v1

sharp_friend_v1 is a versioned runtime identity, not an instruction for coding
agents. It is owned by production prompt loading, structured validation, and
human review. It is not placed in AGENTS.md.

The implementation introduces NarrativeIdentityProfile in the new module
app/services/narrative_identity.py because the current analyzer_contract.py is
a v2 validator with fixed five-role constants, not a reusable narrative
identity registry. The first profile has:

- profile_id sharp_friend_v1 and an immutable profile version;
- language en-US and conversational American English;
- identity: a clever, friendly, perceptive friend under controlled tension;
- a 90-125 word initial target for a roughly 38-50 second reference chapter,
  used without rigid per-role budgets;
- flexible output shape of four to six passages, or an equivalent provider
  structure whose clauses retain claim and panel evidence;
- ending_kind exactly one of cliffhanger, consequence, or open_question;
- no forced question mark unless ending_kind is open_question.

The profile instructs the vision/story agent to observe and reconcile every
ordered panel before writing. It requires a story spine of wants, obstacles,
decisions, consequences, changed stakes, and unresolved or consequential
direction, but it does not require those concepts to appear as a checklist or
in a fixed passage order.

The voice is:

- conversational, warm, perceptive, and human rather than announcer-like;
- controlled in tension, with selective emphasis instead of constant hype;
- causal, using varied sentence lengths, contractions, and natural connectors;
- willing to comment on an evidence-grounded irony, reaction, consequence, or
  hidden clue when the panel evidence supports it.

The voice is not:

- generic hype, fake intensity, a channel CTA, a fixed intro, a forced
  catchphrase, or copied dialogue from a source balloon;
- a rigid panel-by-panel inventory or repeated then/after-that chronology;
- an invented identity, motive, relationship, event, or certainty;
- a reason to turn an interpretation into a fact.

Every factual or interpretive claim still carries claim IDs and evidence panel
IDs. Interpretations use qualification when the visual evidence does not prove
intent. The full-panel coverage, reconciliation, provider capability,
human-review, rights, and final-render gates remain mandatory.

spoken_text remains punctuation-bearing and is the only narration input for
TTS. display_text is derived separately by the existing one-word contract:
uppercase Unicode alphanumeric tokens, punctuation and symbols removed, one
cue per nonempty spoken word, original timing alignment retained. The
narrative identity never writes display cues and never removes spoken
punctuation for the sake of prose.

The current v2 analyzer and five-role contract remain available for existing
profile selections. A project must name sharp_friend_v1 to receive this
flexible shape; no silent migration or fallback is allowed.

## 6. Narrative QC design

Narrative QC is a deterministic screening and evidence gate. It reports
metrics and stable findings; it never rewrites provider prose, inserts a CTA,
moves a sentence, or manufactures a claim. Human editorial review decides
whether a style warning is acceptable.

### 6.1 Hard failures

These findings block script materialization or approval:

- missing, foreign, or unsupported claim/evidence references;
- factual or interpretive text without a supporting panel claim;
- an unqualified interpretation where the evidence graph marks the claim as
  uncertain;
- invented identity, motive, event, or causal link;
- copied speech-balloon text when the source evidence marks it as excluded;
- channel engagement CTA, generic hype, or a fixed promotional ending;
- an invalid ending_kind or a question mark used as an open question without an
  evidence-grounded unresolved question;
- missing spoken_text or a display derivation that cannot preserve word order.

### 6.2 Measurable screening signals

The report records the following stable scalar metrics over the submitted
spoken_text. Metric thresholds are profile data and can produce warnings
without rewriting the text:

- sentence_length_p10, sentence_length_p50, sentence_length_p90, mean, and
  variance, computed from whitespace-token counts;
- repeated_normalized_sentence_ratio, where punctuation and case are removed
  before exact sentence comparison;
- repeated_opening_ngram_ratio for normalized first three lexical words;
- connector_diversity_count and causal_transition_coverage, counting distinct
  natural connectors and clauses linked to a claim-supported cause/effect;
- contraction_count and contraction_ratio, recorded to allow natural English but
  never imposed as a quota;
- generic_hype_hits and cta_hits from a versioned context-aware lexicon;
- claim_evidence_coverage_ratio and qualified_interpretation_coverage_ratio;
- passage_count, ending_kind, total_word_count, and source-panel coverage.

The initial profile uses 90-125 words as a target, not a set of five role
budgets. A warning may be raised for a total outside the target when the
selected render duration or human review explains it. Evidence, CTA, and
unsupported-fact failures remain hard regardless of word count.

Style-risk thresholds are intentionally advisory: a high repeated sentence
ratio, nearly identical opening n-grams, very low connector diversity, or
near-zero sentence-length variance produces narrative.template_risk or
narrative.rhythm_warning. The checker must show the metric and offending
passage IDs, not shorten or rewrite the script. This prevents a new
checklist-shaped template from replacing the old one.

Stable codes include:

- narrative.evidence_missing
- narrative.interpretation_unqualified
- narrative.unsupported_claim
- narrative.balloon_dialogue_copied
- narrative.cta
- narrative.generic_hype
- narrative.template_risk
- narrative.rhythm_warning
- narrative.ending_invalid
- narrative.display_derivation_invalid

### 6.3 Human-readable fixtures

Accepted fixture:

"The offer sounds merciful until the injured fighter notices what it would
cost. When the answer is no, the interruption becomes their only opening. That
is why the clash matters: it buys time, while someone else waits for the next
mistake."

This fixture uses contractions when useful, varied sentence lengths, a causal
connector, a consequence ending rather than a forced question, and claim
references for the offer, refusal, interruption, escape opportunity, and
waiting group.

Rejected fixture:

"Then we see the hero unleash an unstoppable attack, and you need to
subscribe for more epic battles."

It is rejected for rigid inventory language, unsupported identity, generic
hype, and a channel CTA. A second rejected fixture may use fully grounded
facts but repeat the same opening and sentence rhythm across every passage; it
receives a template-risk warning or rejection according to the selected
profile, without any automatic rewrite.

The test corpus must include a grounded non-question cliffhanger, a grounded
open question, contractions, short and long sentences, selective commentary,
and a source dialogue sentence that is rejected when copied into narration.

## 7. Architecture and data flow

The complete evidence-to-script path is:

  all ordered panels
    -> coverage and visual evidence reconciliation
    -> narrative outline and causal claim graph
    -> sharp_friend_v1 profile rendering
    -> deterministic contract and naturalness screening
    -> human editorial review and explicit approval
    -> display_text derivation
    -> later voice generation

The visual path is:

  vision regions and masks
    -> source-space balloon/protected masks
    -> deterministic candidate crops
    -> feasibility and fallback ranking
    -> static prepared window
    -> stable monotonic camera motion
    -> balloon/blank/subject/action QC
    -> render-sidecar telemetry

The evidence record remains the authority across both paths. A crop cannot
invent evidence, and a narration passage cannot hide a crop rejection. Source
order, source asset ID, panel ID, coverage map hash, and evidence hash are
carried through every stage.

Voice generation is explicitly deferred. This checkpoint does not select
local versus API voices, configure credentials, generate auditions, render
audio, normalize audio, or alter TTS behavior. Once the user chooses the
voice route and the script is explicitly approved, the existing spoken/display
separation and neural-provider gates apply.

## 8. Sequential vertical slices

Each slice is independently reviewable, touches no more than five
source/test/doc files where practical, and ends at a commit/push checkpoint.
All commands run on the VPS with the PATH-correct FFmpeg runtime. The Windows
clone is used only for exact-history transport and HTTPS publication.

### Slice A: typed balloon and protected-region evidence

Owned paths:

- app/services/visual_scoring.py, existing feature-extraction boundary;
- app/services/pipeline.py, existing observation/evidence persistence boundary;
- tests/test_balloon_evidence.py, new focused contract tests;
- docs/STATUS.md;
- CHANGELOG.md.

No model or migration is expected: PanelRegion.observation_json and
evidence_refs_json are the verified JSON boundaries. A migration is a separate
decision only if a test proves JSON persistence cannot satisfy the contract.

RED command:

  PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest \
    tests/test_balloon_evidence.py -q

Expected RED is absence of the typed records, mask-status validation, or
persistence contract. Collection must succeed and failures must be behavioral.

GREEN/full verification:

  PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest \
    tests/test_balloon_evidence.py tests/test_vision_pipeline.py \
    tests/test_segmentation.py -q
  .venv/bin/ruff check app/services/visual_scoring.py app/services/pipeline.py \
    tests/test_balloon_evidence.py
  .venv/bin/python -m compileall -q app
  git diff --check

Acceptance: every reconciled panel has a typed mask status; unknown is
blocking, known geometry is normalized and hashable, all required protected
regions retain their minimum coverage, source lineage survives persistence,
and no random/text-only path can produce a known-empty mask. Update STATUS and
CHANGELOG with exact test results, commit SHA, Git state, and Slice B as next
task. Commit message: feat: persist balloon and protected visual evidence.
Push that commit through the exact-history Windows transport. Rollback is this
slice commit.

### Slice B: color-agnostic crop feasibility and telemetry

Owned paths:

- app/services/render.py, existing prepare_reference_frame and cache path;
- app/services/reference_profile.py, profile contract/hash fields;
- tests/test_reference_framing.py, existing framing suite plus regressions;
- docs/STATUS.md;
- CHANGELOG.md.

RED command:

  PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest \
    tests/test_reference_framing.py -q

Add fixtures for white, black, gray, arbitrary-color, and mild-gradient border
padding; meaningful white/black art; unknown masks; and typed balloon overlap.
The current near-white detector should fail the color-agnostic or hard-zero
cases for the correct behavioral reasons.

GREEN/full verification:

  PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest \
    tests/test_reference_framing.py tests/test_motion_stability.py \
    tests/test_reference_profile.py -q
  .venv/bin/ruff check app/services/render.py app/services/reference_profile.py \
    tests/test_reference_framing.py
  .venv/bin/python -m compileall -q app
  git diff --check

Acceptance: a static candidate window reaches balloon intersection exactly
zero, edge-connected blank zero when feasible, protected-region minimums, and
the dynamic source/subject zoom ceiling; infeasible cases follow the exact
four-step fallback and produce stable telemetry. Cache keys change with mask
geometry/status/hash. profile=None remains byte/behavior compatible, motion is
monotonic, and forbidden curves never execute. Update docs and push the
committed slice. Commit message: feat: enforce color agnostic balloon free framing.
Rollback is this slice commit.

### Slice C: panel fallback and reference QC integration

Owned paths:

- app/services/editorial_visual_planner.py;
- app/services/editorial_qc.py;
- app/services/quality.py;
- tests/test_reference_profile_integration.py;
- CHANGELOG.md.

RED command:

  PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest \
    tests/test_reference_profile_integration.py -q

GREEN/full verification:

  PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest \
    tests/test_reference_profile_integration.py tests/test_visual_scoring.py \
    tests/test_motion_stability.py tests/test_quality.py -q
  .venv/bin/ruff check app/services/editorial_visual_planner.py \
    app/services/editorial_qc.py app/services/quality.py \
    tests/test_reference_profile_integration.py
  .venv/bin/python -m compileall -q app
  git diff --check

Acceptance: planner tries alternate ROI, tighter safe crop, same-beat panel,
then visual_unavailable in that order; sparse evidence anchors fall back only
with explicit evidence_context_fallback reasons. Reference QC blocks unknown
balloon masks, positive balloon intersection, subject/action loss, and
non-monotonic or forbidden motion. It separately reports edge blank
infeasibility without turning that soft target into silent balloon allowance.
Legacy/default QC remains unchanged. Update CHANGELOG with exact evidence and
push. Commit message: feat: gate reference framing and panel fallback.
Rollback is this slice commit.

### Slice D: sharp_friend_v1 profile and prompt v3 contract

Owned paths:

- app/services/narrative_identity.py, new runtime profile registry justified
  by the fixed v2 constants in analyzer_contract.py;
- app/services/analyzer_contract.py;
- app/prompts/vision_first_story_analyzer_v3.txt, new repository prompt resource;
- tests/test_narrative_identity.py, new focused tests;
- docs/STATUS.md.

RED command:

  PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest \
    tests/test_narrative_identity.py -q

GREEN/full verification:

  PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest \
    tests/test_narrative_identity.py tests/test_analyzer_contract.py \
    tests/test_analyzer_contract_v2.py -q
  .venv/bin/ruff check app/services/narrative_identity.py \
    app/services/analyzer_contract.py tests/test_narrative_identity.py
  .venv/bin/python -m compileall -q app
  git diff --check

Acceptance: sharp_friend_v1 has a committed version/hash, loads its prompt
through the existing normalized UTF-8 resource convention, observes every
panel before prose, allows flexible passages and ending kinds, preserves
qualified evidence, allows natural contractions/connectors, and rejects
unsupported facts, copied balloons, hype, CTAs, and forced templates. Existing
v1/v2 selections and snapshot tests remain green. Update STATUS with the
profile hash, tests, commit SHA, Git state, next Slice E, and rollback. Commit
message: feat: define sharp friend narrative identity. Push immediately after
the green gate.

### Slice E: naturalness QC and synthesis wiring

Owned paths:

- app/services/analyzer_contract.py;
- app/services/vision_adapter.py;
- app/services/pipeline.py;
- tests/test_narrative_qc.py, new focused tests;
- CHANGELOG.md.

RED command:

  PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest \
    tests/test_narrative_qc.py -q

GREEN/full verification:

  PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest \
    tests/test_narrative_qc.py tests/test_vision_synthesis.py \
    tests/test_vision_pipeline.py tests/test_script_evidence_gate.py -q
  .venv/bin/ruff check app/services/analyzer_contract.py \
    app/services/vision_adapter.py app/services/pipeline.py \
    tests/test_narrative_qc.py
  .venv/bin/python -m compileall -q app
  git diff --check

Acceptance: synthesis receives complete ordered evidence and the selected
identity profile, validates claim/evidence and mask exclusions, emits no
template prose, reports hard failures separately from style warnings, and
never rewrites passages. Human approval remains required before any later
voice path. Update CHANGELOG with metrics and fixture outcomes, commit, and
push. Commit message: feat: screen natural conversational narration.
Rollback is this slice commit.

### Slice F: isolated real-panel render and narration review

Owned paths:

- tests/test_reference_review.py, new review-bundle assertions;
- docs/STATUS.md;
- CHANGELOG.md.

Review outputs are isolated and untracked under the existing data review
boundary. They are not source files and are never staged.

RED command:

  PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest \
    tests/test_reference_review.py -q

GREEN/full verification:

  PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest \
    tests/test_reference_review.py tests/test_reference_framing.py \
    tests/test_narrative_qc.py -q
  .venv/bin/ruff check tests/test_reference_review.py
  .venv/bin/python -m compileall -q app
  git diff --check
  /home/yusronrohmani/.local/bin/ffprobe -v error -show_streams \
    -show_format isolated-reference-review.mp4

Acceptance: every source panel and selected shot has auditable lineage,
balloon intersection is zero, edge blank/subject/action metrics are reported,
motion is stable and monotonic, spoken narration remains punctuation-bearing,
display cues remain separately derived, and a human can review the contact
sheet and silent or approved-audio output. No voice is generated in this
slice. If a hard visual gate fails, record visual_unavailable rather than
overcrop. Update STATUS and CHANGELOG with exact artifact paths, tests,
commit SHA, Git state, next approved action, and rollback. Commit message:
test: audit balloon free reference review. Push only the docs/test commit
after artifact files are confirmed untracked and outside Git.

Every slice stages only its owned paths, runs a diff/secret scope review, and
records its rollback commit before the next slice starts.
## 9. Testing and verification matrix

### 9.1 Visual evidence and crop fixtures

The focused visual suite must include:

- edge-connected white, black, gray, arbitrary-color, and mild-gradient
  padding, each with the same structural low-information result;
- meaningful white and black clothing, effects, sky, shadow, and empty
  composition surrounded by non-padding art;
- internal low-information islands that must not be removed as border padding;
- known balloon polygons that overlap a candidate by one pixel, by a boundary,
  and by a large area; every overlap must fail with
  visual.balloon_mask_overlap;
- unknown balloon geometry, known_empty geometry, and low-confidence geometry
  as separate cases;
- face, subject, action, effect, and continuity regions near every crop edge;
- alternate ROI, tighter crop, same-beat panel, and final rejection outcomes in
  exact order;
- native-resolution/upscale failures and a source too small for the required
  safe area;
- cache-key changes for every mask, region, focus, profile, and version input.

### 9.2 Motion, render, and coverage gates

Tests must assert that the prepared frame is static and that sampled camera
center and scale remain deterministic, smooth, monotonic, and within the
selected profile caps across at least 120 frames. Forbidden curve strings,
sinusoidal expressions, shake modes, orbit, whip, and reversal must not appear
in reference filter construction. Existing motion_stability tests remain
authoritative.

Reference sequences must assert:

- every selected shot has a source asset, panel, beat, and evidence hash;
- every source panel is observed and reconciled exactly once in the evidence
  ledger before selection;
- any allowed second use has a distinct ROI and explicit reuse purpose;
- no balloon mask is selected as output or motion focus;
- speech/display separation preserves punctuation in spoken_text and produces
  exactly one uppercase punctuation-free Unicode-alphanumeric display word;
- no black-frame, drift, crop, or end-overflow regression is introduced.

### 9.3 Narrative fixtures and gates

The narrative suite must contain at least two distinct chapter fixtures with
different entities, causes, and consequences. It must prove that the output
changes with evidence and does not rely on a fixed opening. It must include:

- a grounded four-passage and a grounded six-passage script;
- contractions and varied sentence lengths;
- a consequence ending without a question mark;
- an evidence-grounded open question ending with a question mark;
- selective commentary and causal connectors;
- a repeated opening, repeated sentence, generic hype, CTA, copied balloon
  dialogue, invented motive, and unqualified interpretation negative case;
- claim/evidence coverage and full-panel reconciliation failures;
- a style-risk warning case that remains human-reviewable and is not rewritten.

Automated naturalness checks are screening and evidence gates; they do not
replace human editorial review.

### 9.4 Commands and release proof

For every green slice, run the focused command, directly affected legacy
regressions, and:

  PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest -q -m "not slow"
  .venv/bin/ruff check <changed Python and test paths>
  .venv/bin/python -m compileall -q app
  git diff --check

The exact full-suite command must be recorded with collected, passed,
failed, skipped, and duration totals. Relevant real-FFmpeg checks must use
the VPS runtime at /home/yusronrohmani/.local/bin and report codec, profile,
pixel format, frame rate, geometry, audio, black-frame, and loudness facts.
No generated media, database, WAL, credentials, temporary patches, or review
bundles enter a commit.

Before each push, inspect the staged allowlist, run a filename and high-
confidence secret scan without printing values, verify the commit graph is a
fast-forward of the current HTTPS main, push only main:main, and confirm
HTTPS ls-remote returns the pushed SHA. The VPS worktree must remain clean.

## 10. Documentation and handoff policy

Every significant green slice updates STATUS, CHANGELOG, and the relevant
spec or plan evidence with:

- exact profile/version/hash or detector version;
- exact commands and test totals;
- commit SHA and Git branch/status;
- produced review artifact paths when applicable;
- the next task and its ownership boundary;
- the rollback commit and any known blocker.

The implementation agent must preserve the distinction between inspected,
changed locally, verified, committed, and pushed. A docs-only checkpoint must
not claim runtime behavior. A historical test total must be labeled as
historical and not as a fresh rerun.

All source, tests, provider checks, and renders run on the VPS. Windows
transports only exact Git objects and publication. No force push, tags, all
branches, or unrelated remote writes are permitted. No secrets or source
artwork is copied into Git. Rights/source checks remain hard blockers and
publish_allowed remains false until rights are verified.

## 11. Risks, rollback, and explicit stop conditions

Risks and mitigations:

- False blank classification: require border connectivity, multiple
  structure features, protected-region evidence, and color-diverse fixtures.
- Subject overcrop: enforce required-region coverage, safe margins, native
  resolution, and the exact fallback order.
- Incomplete balloon masks: distinguish unknown from empty and block unknown;
  never infer an empty mask from missing OCR.
- Small-source pixelation: apply the source-resolution ceiling and reject
  when no quality-safe candidate exists.
- Rigid naturalness QC: keep style metrics as warnings where evidence is
  valid, never rewrite prose, and require human review.
- Provider drift: persist prompt/profile/hash and fail on mismatched resources.
- Rights or source uncertainty: keep publication blocked even when visual and
  narrative gates pass.

Each slice has a standalone commit rollback point. Rollback means reverting or
resetting the deployment to the last reviewed slice commit through the normal
approved Git workflow; it does not delete source data or generated review
artifacts. No destructive cleanup is part of this design.

Stop the active slice and report to the reviewer when:

- a required typed mask cannot be represented truthfully;
- vision evidence is incomplete or the mask status is unknown;
- native resolution cannot meet protected-region requirements;
- the same panel would require a balloon-containing fallback;
- a proposed change would require a migration outside the approved ownership;
- a provider or prompt hash is unavailable;
- rights/source or human approval is missing.

No agent may convert one of these stops into a best-effort crop, invented
narration, silent fallback, or publication approval.

## 12. Self-review checklist for this design checkpoint

- The current detector, 1.0-1.35 search, 0.18 score-only behavior,
  heuristic balloon signal, v2 rigidity, baseline SHA, and historical 635
  checkpoint are explicitly identified.
- Balloon exclusion is a hard zero; blank minimization may be infeasible and
  is measured rather than silently ignored.
- Brightness is not the blank definition; border flood fill distinguishes
  padding from internal meaningful art.
- Subject, face, action, effect, continuity, native-resolution, and monotonic
  motion guards are specified.
- Unknown balloon geometry is distinct from known empty geometry and blocks.
- Fallback order is alternate ROI, tighter safe crop, same-beat panel, then
  visual_unavailable.
- Typed evidence, persistence boundary, hashes, provenance, no sampling, and
  stable error codes are defined.
- sharp_friend_v1 is runtime-owned, not AGENTS.md-owned; it allows natural
  contractions, varied rhythm, flexible passages, and consequence endings.
- Naturalness metrics screen and explain; they do not rewrite or replace human
  editorial review.
- Spoken punctuation and display one-word derivation remain separate.
- Voice provider choice, audio generation, and auditioning are explicitly
  deferred.
- Each implementation slice has bounded files, RED/GREEN commands, acceptance,
  docs, commit message, push checkpoint, and rollback.
- No production implementation is claimed by this docs-only checkpoint.
- No unfinished instruction remains in this document.
