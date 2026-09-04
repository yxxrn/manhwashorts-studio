# Run 6 Optimization and Quality Plan

This plan is intentionally deferred until Run 5 is fully released. Run 6 must benchmark performance changes separately from quality experiments and preserve all fail-closed evidence, lineage, framing, subtitle, render, and post-render QC contracts.

## Performance priorities

1. Profile and reduce the ~206s final render path, especially redundant scene encode/join/subtitle/audio passes, without changing final 1080x1920/60 FPS/H.264/AAC acceptance.
2. Persist/reuse exact ROI pixel-refinement preflight by immutable panel/evidence/profile/ROI identity so timeline rebuilds do not repeat expensive pixel checks.
3. Change panel transport from per-panel parallel work to source-group work where each source image is decoded once and its panel crops are derived from that shared decode.
4. Evaluate bounded-parallel TTS for the five independent production passages only if provider ordering/rate semantics remain deterministic and retry-local.
5. Cache grounded metadata/title generation by approved-script/content/profile identity.

## Thumbnail quality experiment

- Production headline must be novel relative to prior production headline history. Exact duplicates are forbidden.
- Strong near-duplicates must be rejected or heavily penalized using normalized lexical/semantic similarity, while the surviving headline must remain grounded in the current story and hook-worthy.
- Persist the selected production headline in a history/registry so future runs can enforce novelty across projects, not only inside one run.
- Final text placement is one choice from `top`, `middle`, or `bottom`; do not generate three copies just to vary placement.
- Choose placement pseudo-randomly from only the positions that pass text-safe, face, protected-subject, and important-action overlap checks. Seed by immutable production identity so retries are reproducible.
- Final text color must be chosen from exactly: yellow, red, blue, green.
- Color choice is panel-aware: score contrast/readability and visual harmony against the selected thumbnail crop, then choose the strongest safe color rather than defaulting to yellow.
- Existing overflow, line-count, face-overlap, luminance, file-integrity, and thumbnail QC gates remain blocking.

## Additional quality experiments

- Reduce `subtitle.too_fast` warnings through phrase/timing presentation changes without changing authoritative spoken-word timing or narration content.
- Reduce repeated camera curves and repeated ROI patterns while preserving stable motion and avoiding jitter/restless-camera behavior.
- Investigate `asset_cooldown_exception` cases and prefer more visually diverse safe panels when evidence capacity permits.
- Treat all quality changes as experiments first; compare QC/visual metrics and human review against Run 5 before promoting them to the default production contract.
