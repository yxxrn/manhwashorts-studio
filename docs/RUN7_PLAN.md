# Run 7 Optimization and Quality Plan

Run 7 must treat Run 6 as a mixed result: source-group transport, thumbnail novelty/layout/color, preflight caching, and metadata recovery caching are successful; unconstrained parallel libx264 scene encoding is not; that experiment was reverted before the Run 6 release commit.

## Performance priorities

1. Benchmark final CPU scene encoding with explicit x264 thread budgets and worker counts 1, 2, and 3 on the Oracle 20-logical-CPU host. Preserve `preset=slow`, CRF 18, High profile, 1080x1920/60 FPS, frame counts, transitions, subtitles, audio, and final QC. Select by total render wall time, not by worker count.
2. Profile cold frameability internally (OCR, face detection, visual scoring, exact ROI safety) because Run 6 regressed to 1.095s/evaluated panel versus Run 5 0.883s/panel.
3. Keep source-group panel transport and its one-decode-per-source invariant; benchmark source decode count and panel throughput again on the next corpus.
4. Keep exact pixel-preflight and metadata/title identity caches; report cold and warm hit/miss behavior separately.
5. Keep observation concurrency at 3. The new split-repair is recovery-only after the normal bounded retry contract, not a reason to increase provider concurrency.

## Quality experiments

- Reduce `subtitle.too_fast` warnings through display grouping/presentation changes only; spoken narration and authoritative word timing remain unchanged.
- Preserve persisted thumbnail headline novelty. Prefer more story-specific hooks over generic fallback wording when grounded options exist.
- Continue safe pseudo-random top/middle/bottom placement and panel-aware yellow/red/blue/green scoring. Do not force color rotation; evaluate actual cross-run diversity before changing the scorer.
- Revisit repeated camera-curve/ROI and asset-cooldown warnings only when alternate evidence-safe panels exist; do not trade stability or evidence lineage for variety.

## Measured Run 7 outcome

- Fresh production project `07aaf1a7333d4aebbc9afca3525cc35d` used Infinite Mage chapters 159-163 from Asura Scans. Source acquisition created 312 assets from 70 downloaded pages.
- Source resolve/download/ingest measured 0.623s / 75.654s / 65.058s.
- Identical-input render benchmark selected one scene worker with a 10-thread x264 budget: 198.874s versus 318.951s for two workers at five threads each and 276.027s for three workers at three threads each. Quality remained libx264, preset slow, CRF 18, High profile.
- Frameability profiling found the expensive work in ROI and border-mask evaluation rather than cache I/O. Removing diagnostic 1080x1920 preview resampling from admission-only frameability and bounding mask-cell scans reduced the Run 6 cold replay from 252.280s to 136.633s (-45.8%) while preserving every generic and section-safe panel ID. Warm replay was 0.066s.
- Fresh Run 7 frameability measured 135.351s. Provider observation remained the dominant analysis cost at 1118.683s for 53 chunks and 60 actual calls under concurrency 3.
- Sentence-karaoke production no longer runs the obsolete one-word display-speed QC in parallel. Run 7 final QC has no subtitle failure codes and authoritative audio word timing remains unchanged.
- Thumbnail selection produced the story-specific LLM headline `WHITE ELIXIR REVEALS SCAM WHY?`. Generic unanchored emergency fallbacks no longer compete when grounded LLM candidates exist.
- Final media is 50.667s, 1080x1920, 60 FPS, H.264/AAC. Post-render QC, thumbnail QC, and manual-upload metadata all pass. Publication count is zero.
- The run is correctly marked recovery rather than clean-uninterrupted: the initial launcher did not source `ms_env.sh`, so the vision capability gate failed before provider work. Resuming the same project with the configured provider required no re-download or re-ingest and did not bypass a gate.
