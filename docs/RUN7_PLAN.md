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
