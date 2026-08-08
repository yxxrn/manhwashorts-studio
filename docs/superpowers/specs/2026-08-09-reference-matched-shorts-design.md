
# Reference-Matched Shorts Editorial Profile Design

Status: Approved profile-specific addendum for implementation planning
Date: 2026-08-09
Decision owner: Sol High
Parent design: docs/superpowers/specs/2026-08-05-vision-first-editorial-story-engine-design.md
Scope: design and implementation contract only; this document changes no production behavior by itself.

## 1. Purpose and reference provenance

This addendum defines a selectable editorial profile for a reference-matched English YouTube Short. The reference is https://youtube.com/shorts/dskwm3t1QIA. It was inspected as an approximately 41-second, 9:16 Short. The implementation matches the reference's motion grammar, pacing, framing, and caption grammar as measurable profile constraints.

Reference matching means matching editorial behavior, not copying protected material. The system MUST NOT copy the reference's source media, narration, audio, channel marks, cover, or the top black YouTube auto-caption. The generated chapter retains the original channel identity, source provenance, and rights metadata. Similarity to the reference never proves ownership, licensing, fair use, monetization eligibility, or publication permission. The existing rights/source hard gate remains independent and authoritative; a rights failure keeps publish_allowed=false.

This profile is an explicit configuration choice. Selection/configuration may occur early, before analysis, and the project or render persists the selected profile ID, canonical profile JSON, and immutable profile hash. Final-render readiness is later and requires complete coverage, a vision-capable provider, evidence reconciliation, human script approval, an approved voice profile, profile caption and motion QC, final render QC, and the independent rights gate. The profile does not silently change legacy/default behavior.

## 2. Profile identity and selection

The immutable profile identifier is reference_matched_shorts_v1. Its canonical configuration is UTF-8, LF-normalized, compact JSON with sorted keys. The profile hash is SHA-256 of that canonical JSON. A render request must carry profile_id, profile_version, and profile_hash.

The profile may be selected before analysis, but a final render using it is ready only after:

- complete source-space coverage and reconciliation from the parent vision-first design;
- a vision-capable provider capability check;
- human script approval;
- an approved immutable voice profile, selected from the four required English auditions;
- reference-profile validation of captions, shot cadence, motion, and render settings;
- the independent rights/source gate.

A missing provider, human approval, voice selection, motion QC result, render QC result, or rights decision blocks the relevant transition. No profile flag bypasses a parent-design gate.

## 3. Measurable reference profile contract

The reference profile uses these immutable thresholds:

    {
      "profile_id": "reference_matched_shorts_v1",
      "profile_version": "1.0.0",
      "duration_seconds": {"min": 38.0, "max": 50.0},
      "shot_count": {"min": 28, "max": 36},
      "shot_hold_seconds": {"min": 0.9, "max": 1.5},
      "shot_emphasis_seconds": {"min": 1.6, "max": 2.2},
      "hold_shot_ratio_min": 0.85,
      "emphasis_shot_ratio_max": 0.15,
      "mean_shot_seconds": {"min": 1.05, "max": 1.65},
      "hard_cut_ratio_min": 0.85,
      "section_transition_seconds": {"min": 0.12, "max": 0.18},
      "normal_zoom_max": 1.06,
      "impact_zoom_max": 1.08,
      "caption_words_per_cue": 1,
      "caption_uppercase": true,
      "caption_unicode_punctuation_allowed": false,
      "caption_top_sentence_allowed": false,
      "caption_font_weight": "bold",
      "caption_primary_color": "white",
      "caption_outline_color": "black",
      "caption_anchor": {"x": 0.50, "y": 0.64},
      "caption_safe_region": {"x_min": 0.15, "x_max": 0.85, "y_min": 0.50, "y_max": 0.75},
      "caption_outline_pixels": 6,
      "caption_shadow_color": "black",
      "caption_shadow_alpha_max": 0.35,
      "caption_alignment": 5,
      "max_canonical_panel_uses": 2,
      "consecutive_panel_reuse_allowed": false,
      "final_width": 1080,
      "final_height": 1920,
      "final_fps": 30,
      "final_codec": "h264",
      "final_codec_profile": "High",
      "final_pixel_format": "yuv420p",
      "audio_lufs_target": -14.0,
      "audio_true_peak_max_db": -1.5,
      "unlicensed_music_sfx_allowed": false
    }

A shot is a hold shot when its content duration is between 0.9 and 1.5 seconds inclusive. An emphasis shot is between 1.6 and 2.2 seconds inclusive and must have metadata reason in {reveal, consequence, reaction, impact_emphasis}. A duration outside both ranges fails profile QC. At least 85 percent of shots must be hold shots, no more than 15 percent may be emphasis shots, and the arithmetic mean of shot content durations must be between 1.05 and 1.65 seconds inclusive. The measured final media duration from ffprobe must be between 38.0 and 50.0 seconds inclusive.

Hard-cut ratio is hard_cut_count divided by all inter-shot joins. It must be at least 0.85. A section transition is permitted only when a persisted semantic section boundary exists in the story spine or a causal reveal requires it; each transition duration must be 0.12 to 0.18 seconds inclusive. A profile render has no intro, logo card, or reference cover.

The selected profile explicitly supersedes all current duration conflicts while it is active. quality.check_duration currently blocks a duration below 60 seconds with duration.too_short; editorial_qc currently uses minimum_duration=45 and emits duration_outside_60_90s; editorial_qc also rejects average shot duration outside 2.3-3.3 seconds. For reference_matched_shorts_v1, dispatch uses the 38.0-50.0 duration range, the 28-36 shot count, the 0.9-1.5 hold distribution, the 1.6-2.2 emphasis range, and the 1.05-1.65 mean gate defined above. The legacy/default profile keeps the existing 60-second, 60-90-second, and 2.3-3.3-second behavior unchanged.

Final audio is normalized toward -14 LUFS integrated with true peak <= -1.5 dBTP. Unlicensed music and SFX are forbidden. Audio failure is a profile QC block and does not override the rights/source gate.

## 4. Narration and story identity

Narration is English spoken narration. The script model keeps spoken_text punctuation for TTS prosody and stores separate display_text for visible captions. The profile never sends display_text to TTS.

The narrative identity remains Cinematic Story Detective: a clever friend with controlled tension, human sentence rhythm, cause and effect, reaction, intention, irony, escalation, reveal, and a final unresolved question. The writer may open on a consequence or revealing visual and backfill a supported cause, but chronology and evidence remain stable.

The script MUST:

- consume the complete ordered panel and observation ledger after coverage reconciliation;
- cite panel evidence for every factual, causal, or interpretive claim;
- qualify uncertainty rather than inventing motive, identity, dialogue, or outcome;
- prefer causal transitions and varied sentence rhythm;
- avoid stiff panel-by-panel inventory;
- avoid the phrases then we see and generic variants of after that as repeated chronology scaffolding;
- avoid fake hype, unsupported superlatives, generic hooks, generic CTAs, and invented facts;
- end on a cliffhanger, loop, or unresolved question rather than a generic CTA.

The script output contains narrative passages, claim IDs, evidence panel IDs, clause IDs, clause role, and selected visual role. Automated anti-template and naturalness checks are screening gates; human editorial review remains mandatory.

The profile QC contract requires cta_text, logo, and intro to be absent from emitted script and shot metadata. The final passage must be an evidence-backed cliffhanger or loop with an unresolved question or consequence; a generic CTA or unsupported ending emits reference.ending_not_evidence_backed and blocks final-render readiness.

## 5. Complete vision-first comprehension

The parent design's full source-space gate remains exact:

- every uploaded source asset and strip is inventoried in deterministic source order;
- every source-space region is classified as canonical panel/content, verified gutter/non-story with evidence, or unresolved/material;
- source_content_coverage_ratio equals exactly 1.0 for accounted source space;
- unresolved_material_area equals 0;
- segmentation completeness verification passes;
- the source-asset -> region -> panel -> observation -> chunk -> synthesis -> claim chain reconciles;
- every canonical panel is observed, including duplicate source rows;
- no random sampling, representative-panel shortcut, text-only fallback, or template recap is allowed.

The reference profile consumes the parent CoverageMap, ordered PanelRegion inventory, coverage manifest, continuity ledger, evidence graph, and chapter-wide story spine. It cannot create a second denominator or declare coverage from a selected visual subset.

The four English voice auditions remain required when no approved immutable profile exists, or after explicit re-audition or configuration invalidation. Audition samples compare timbre only; they do not represent chapter coverage and cannot satisfy any source, panel, observation, evidence, or script gate.

## 6. Panel-to-clause and visual selection contract

Every narration clause has a clause_id and exactly one primary visual role from:

- action: an evidence-backed action or physical change;
- reaction: a face, body response, or consequence reaction;
- object_detail: an object, mark, setting detail, or clue;
- dialogue_face: visible dialogue context or expression;
- reveal: a newly supported discovery or reversal;
- establishing: a location or relationship orientation.

A clause may include supporting panel IDs, but it must have one primary selected panel and one primary role. A selection record contains clause_id, panel_id, source_asset_id, source_order, roi, role, evidence_refs, and reason.

A canonical panel may be used at most twice in the profile. A second use must select a meaningfully different ROI and narrative function, must not be consecutive, and must disclose reuse_reason in shot metadata. Consecutive use of the same canonical panel fails. A duplicate source row has its own panel ID and denominator entry even when it belongs to a duplicate group.

Panel chronology is stable by persisted source_order. A clause may begin with a consequence only when its evidence graph links the consequence to the supported prior cause; it may not reorder panels to conceal missing coverage.

## 7. Caption and timed-text contract

Each visible caption cue displays exactly one lexical word:

- display_text is uppercase;
- display_text contains no Unicode punctuation code point in any Unicode category beginning with P;
- display_text contains exactly one lexical token after normalization;
- cue order and timing map one-to-one to the spoken word-token sequence;
- spoken_text retains punctuation and is the TTS payload;
- the visible cue is centered in the middle/lower-middle safe region, never at the top;
- text is bold white with a 6-pixel black outline at 1080x1920 and restrained shadow alpha no greater than 0.35;
- no top black sentence caption is emitted, burned, or exported;
- every cue starts at or after 0.0 and ends at or before measured media duration.

The safe region is normalized x=0.15..0.85 and y=0.50..0.75. The default anchor is x=0.50, y=0.64. The renderer must report the actual caption bounding box and anchor in the subtitle sidecar.

Token derivation must preserve timing. If punctuation separates lexical words, the punctuation is removed while the lexical words retain their original token spans. If punctuation joins a single lexical token, the token remains one cue without punctuation. The token map records spoken token, display token, source character span, start, end, and punctuation code points removed.

## 8. Motion, framing, and transitions

The camera uses subject-aware, smooth, deterministic reframes, pans, and zooms. One shot declares one monotonic intent: hold, push, pull, pan, reveal, or impact_emphasis. A focal target and reason are required.

The profile forbids shake, micro_shake, impact_shake, explosion shake, sinusoidal center motion, orbit, whip, random offsets, and hidden oscillation. Normal zoom never exceeds 1.06. Impact emphasis never exceeds 1.08. Center movement has deterministic easing, no reversal inside a monotonic shot, and periodic static holds are present in the shot list.

The crop helper uses even quantization:

    even_coord(value, maximum) = 2 * floor(clamp(value, 0, maximum) / 2)

The implementation must clamp to legal bounds after quantization. It must not multiply an already pixel-valued coordinate by two. Frame telemetry reports center, scale, displacement, acceleration, reversal count, intent, static-hold coverage, and shot/frame intervals. Any forbidden mode, ceiling violation, unexpected reversal, or unexplained acceleration blocks motion QC.

## 9. Current conflicts and profile precedence

The reference profile explicitly supersedes the following constraints only when reference_matched_shorts_v1 is selected:

- editorial_qc currently rejects a single-word caption ratio greater than or equal to 15%; this profile requires one lexical word in every cue, so the profile-specific QC replaces that rejection with the exact one-word/no-punctuation checks;
- shot_director currently permits micro_shake and impact_shake and longer pacing; this profile rejects those modes and applies the 28-36 shot and duration distribution gates;
- the old Plan 2 caption contract groups four to seven words; this profile requires one lexical word per cue;
- quality.check_duration and the current editorial_qc duration/average-shot limits; this profile uses its own 38-50 second and 1.05-1.65 second mean gates.

Legacy/default profiles remain backward-compatible. Their current four-to-seven-word caption grouping, existing pacing, and existing motion behavior are not removed by this addendum. A profile-aware dispatch must make the selected profile explicit in every QC result; it must never weaken a legacy gate by accident or apply the reference constraints to an unselected render.

## 10. State machine and gates

The profile adds these states after the parent states:

- REFERENCE_PROFILE_SELECTED
- REFERENCE_SHOT_PLAN_READY
- REFERENCE_CAPTION_QC_PASSED
- REFERENCE_MOTION_QC_PASSED
- REFERENCE_PROFILE_QC_PASSED

REFERENCE_PROFILE_SELECTED records an explicit profile/config choice and its canonical hash; it may occur before analysis. A selected profile can reach REFERENCE_SHOT_PLAN_READY only after complete vision evidence, human script approval, and voice-profile reuse or selection. REFERENCE_CAPTION_QC_PASSED requires one-word cues, uppercase, no punctuation, timing bounds, safe placement, and no top sentence caption. REFERENCE_MOTION_QC_PASSED requires cadence, hard cuts, transition, zoom, intent, hold, and telemetry gates. REFERENCE_PROFILE_QC_PASSED is the final-render readiness state and requires coverage, vision capability, evidence reconciliation, human approval, voice, captions, motion, final codec/audio checks, and rights checks.

Any failed gate persists a stable reason code and keeps publication blocked. The profile does not bypass source_gate_failed, license uncertainty, or a missing rights declaration. Similarity metrics are review evidence, never a publication authorization.

## 11. Data contracts

ReferenceProfileConfig is the sole source for the canonical JSON and SHA-256. Every value that participates in the hash is a field on this frozen object; there is no larger hand-maintained JSON object beside it.

    @dataclass(frozen=True)
    class ReferenceProfileConfig:
        profile_id: str
        version: str
        duration_min_s: float
        duration_max_s: float
        shot_min: int
        shot_max: int
        hold_min_s: float
        hold_max_s: float
        emphasis_min_s: float
        emphasis_max_s: float
        hold_ratio_min: float
        emphasis_ratio_max: float
        mean_shot_min_s: float
        mean_shot_max_s: float
        hard_cut_ratio_min: float
        transition_min_s: float
        transition_max_s: float
        normal_zoom_max: float
        impact_zoom_max: float
        caption_words_per_cue: int
        caption_uppercase: bool
        caption_unicode_punctuation_allowed: bool
        caption_top_sentence_allowed: bool
        caption_safe_region: tuple[float, float, float, float]
        caption_anchor: tuple[float, float]
        caption_font_weight: str
        caption_primary_color: str
        caption_outline_color: str
        caption_outline_pixels: int
        caption_shadow_color: str
        caption_shadow_alpha_max: float
        caption_alignment: int
        max_canonical_panel_uses: int
        consecutive_panel_reuse_allowed: bool
        final_width: int
        final_height: int
        final_fps: int
        final_codec: str
        final_codec_profile: str
        final_pixel_format: str
        audio_lufs_target: float
        audio_true_peak_max_db: float
        unlicensed_music_sfx_allowed: bool

ReferenceCaptionToken is:

    @dataclass(frozen=True)
    class ReferenceCaptionToken:
        cue_id: str
        spoken_token: str
        display_token: str
        start_s: float
        end_s: float
        removed_punctuation: tuple[str, ...]

ReferenceVisualSelection is:

    @dataclass(frozen=True)
    class ReferenceVisualSelection:
        clause_id: str
        panel_id: str
        source_asset_id: str
        source_order: int
        roi: tuple[float, float, float, float]
        role: str
        evidence_refs: tuple[str, ...]
        reason: str
        reuse_reason: str

ReferenceProfileQC is:

    @dataclass(frozen=True)
    class ReferenceProfileQC:
        profile_id: str
        profile_hash: str
        passed: bool
        failures: tuple[str, ...]
        metrics: Mapping[str, float]
        caption_metrics: Mapping[str, int | float]
        motion_metrics: Mapping[str, int | float]
        rights_publish_allowed: bool

The existing CoverageMap and reconcile_coverage_chain contracts remain authoritative. New contracts add profile metadata; they do not replace lineage or evidence fields.

## 12. Planned affected modules

Implementation boundaries are planned only; this document does not modify them:

- app/routers/projects.py: persist all IngestedAsset lineage fields into SourceAsset during upload.
- app/services/reference_profile.py: immutable profile config, canonical JSON/hash, selection, precedence, and metric validation.
- app/services/pipeline.py: profile-aware state transitions, complete evidence prerequisites, and render gating.
- app/services/analysis.py and app/services/vision_adapter.py: complete ordered observations, clause evidence, and profile instruction extension.
- app/prompts/reference_matched_shorts_analyzer_v1.txt: versioned prompt extension for causal narration and clause mapping.
- app/services/editorial_visual_planner.py: clause-to-panel role selection, ROI metadata, reuse constraints.
- app/services/timeline.py: one-word punctuation-free profile cue path while preserving legacy grouping.
- app/services/shot_director.py: 28-36 shot profile cadence, hard cuts, semantic transitions, and no forbidden modes.
- app/services/motion_director.py and app/services/motion_qc.py: smooth subject-aware motion and telemetry validation.
- app/services/render.py and app/services/encoders.py: profile subtitle styling, frame telemetry, final codec checks.
- app/services/editorial_qc.py and app/services/quality.py: profile-aware metric gates and additive sidecars.
- app/services/tts.py: reuse the Aug 5 immutable voice-profile lifecycle and audition only when required.
- tests/: profile, lineage, vision, narrative, captions, motion, voice, QC, render, and rights regressions.
- docs/STATUS.md, docs/P0_EDITORIAL.md, docs/RELEASE_RUNBOOK.md, CHANGELOG.md: rollout, review, and stop-point documentation.

## 13. Observability and artifacts

Every profile run persists profile_id, profile_version, profile_hash, parent instruction contract version/hash, source coverage map hash, ordering fingerprint, voice profile hash, script approval actor/time, and render request hash.

The profile QC sidecar records:

- measured duration, shot count, mean/median/min/max shot duration;
- hold and emphasis counts and ratios;
- hard-cut count/ratio, semantic transition count and durations;
- caption cue count, one-word failures, uppercase failures, punctuation failures, top-caption failures, safe-region failures, and timing overflow;
- panel reuse counts, consecutive reuse failures, ROI/function differences, and undisclosed reasons;
- motion intent counts, forbidden mode findings, zoom ceilings, reversal count, static-hold ratio, frame intervals;
- codec, dimensions, frame rate, pixel format, audio measurements, rights status, and publish_allowed;
- cta_text, logo, intro, ending evidence references, and ending-kind result; any emitted cta_text/logo/intro or unsupported ending is a blocking failure.

The isolated review artifact set includes MP4 when render gates permit, shot list, subtitle list, caption token map, reference-profile QC JSON, motion telemetry, coverage manifest, evidence/story/claim sidecars, contact sheet, source-rights report, and the exact publish decision. Rights-blocked review artifacts must show publish_allowed=false.

## 14. Rollout and rollback

Stage 0 completes Task 3.1 lineage persistence before any reference-profile task. Stage 1 adds profile config and tests while the default profile remains unchanged. Stage 2 adds the vision/story and clause contracts behind an explicit profile selection and blocks when vision capability is missing. Stage 3 adds profile timeline, shot, motion, voice reuse, and QC sidecars. Stage 4 runs isolated rights-blocked review material and requires human visual/audio/editorial review. Stage 5 enables the profile only for explicit user selection.

Rollback disables reference_matched_shorts_v1 at configuration level, retains legacy/default behavior, and leaves immutable artifacts auditable. A failed stage does not rewrite source lineage, remove voice profiles, or bypass rights. Generated media and runtime data remain untracked and are not part of source commits.

## 15. Acceptance criteria and non-goals

Acceptance requires all of the following:

1. A profile selection is explicit, immutable, hashed, and absent from legacy/default runs.
2. Task 3.1 persists the full source lineage from upload results into SourceAsset.
3. Every reconciled panel is observed before story writing; no sampling, random path, or text fallback exists.
4. Every claim and narration clause has panel evidence and a visual role.
5. A selected reference profile produces 38-50 seconds, 28-36 shots, the stated duration distribution, at least 85 percent hard cuts, only semantic 0.12-0.18 second transitions, no intro/logo/CTA, and an evidence-backed cliffhanger/loop ending.
6. Every visible caption is exactly one uppercase punctuation-free lexical word, mapped to spoken timing, centered in the specified safe region, with the required styling, and no top sentence caption.
7. Motion is subject-aware, smooth, monotonic per shot, bounded at 1.06/1.08 zoom, free of forbidden oscillation/shake modes, and passes frame telemetry QC.
8. Four English auditions precede a missing-profile final render; a selected immutable voice profile is reused later; auditions are explicitly not chapter coverage.
9. Final media is 1080x1920, 30fps, H.264, yuv420p, normalized toward -14 LUFS with true peak <= -1.5 dBTP, with no unlicensed music/SFX and rights/source gates unchanged.
10. The isolated artifact set is complete and human review records remain separate from automated QC.
11. A rights failure keeps publish_allowed=false even when all similarity and technical gates pass.

Non-goals:

- copying or reconstructing the reference's media, narration, audio, channel marks, cover, or top black auto-caption;
- guaranteeing monetization, copyright clearance, or publication from visual similarity;
- removing legacy/default profile behavior;
- replacing human editorial review with anti-template or naturalness automation;
- treating four audition samples as chapter coverage;
- allowing text-only analysis, partial panel coverage, or a generic recap when vision or evidence gates fail;
- changing unrelated user data, OmniVoice data, source artwork, runtime outputs, or credentials.
