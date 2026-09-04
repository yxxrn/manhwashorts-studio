# Run 10 Optimization Plan

Run 9 is the first clean unattended production pass through the new resumable runner: Infinite Mage chapters 153-155, project `5a5a9cc33c9a49a69b11b6d5a822a16a`, 40 source pages, 179 assets, 298 panels, final duration 50.650s, and zero publications.

## Run 9 benchmark

- Total unattended wall: 1188.820s (~19m49s).
- Source import: 78.510s.
- Analysis total: 782.616s.
- Panel transport: 23.045s for 298 panels / 179 source decodes.
- Vision observation: 566.487s, 30 chunks, 34 provider calls, effective concurrency 2.977/3.
- Four observation calls were whole-chunk retries after invalid provider responses.
- Frameability: 53.123s for 82 cold misses.
- Remaining analysis wall outside transport/observation/frameability: 139.960s; instrument synthesis before changing it.
- Production total: 320.227s.
- TTS 19.700s; timeline 38.712s; pre-render QC 1.047s; enqueue 0.540s.
- Final render 243.370s; post-render QC 0.495s; thumbnail 34.219s; metadata 16.702s.

## Priorities, without relaxing gates

1. **Observation retry cost.** Benchmark stricter structured-response prompting and/or bounded targeted repair so one malformed field does not automatically resend a whole otherwise-valid 12-panel chunk. Schema, lineage, evidence, panel set, coverage, and reconciliation validation remain unchanged and fail closed.
2. **Multi-pass final render.** Benchmark fewer full-frame libx264 passes, especially combining join/xfade and subtitle burn where exact output semantics permit. Keep `preset=slow`, CRF 18, High profile, yuv420p, 1080x1920, 60 FPS, exact frame count/transitions/subtitles, and all post-render QC. Do not trade quality for speed.
3. **Timeline local work.** Profile exact ROI/pixel-preflight cost and reuse analysis/frameability geometry identities only when the immutable inputs match. Keep identical safe-capacity and pixel-level thresholds.
4. **Hide headline/metadata latency.** Precompute story-grounded text by approved-script identity while rendering, then finalize thumbnail/metadata only after successful final media/QC. Novelty, grounding, visual QC, and metadata contracts remain blocking.
5. **Instrument the 139.960s analysis residual.** Add synthesis/substage timing before algorithmic optimization; telemetry only.
6. **Split source-import telemetry.** Measure resolver, network download, and ingest/slicing separately before changing source acquisition. Preserve order, provenance, and content hashes.

Run 10 should change only candidates that beat the current benchmark under equivalent input while retaining the same production contracts. Experiments that fail equivalence/QC stay out of production.

## Run 10 actual result

- Infinite Mage chapters 150-152, project `320bfe65c68546dab6faa14add439b60`.
- Clean unattended PASS in 1077.260s (~17m57s), down 111.560s / 9.384% from Run 9.
- Source import: 83.226s for 40 pages / 185 assets.
- Analysis: 707.882s for 306 panels; observation 506.540s, 31 chunks / 33 provider calls, two invalid-response retries; frameability 89.134s; synthesis 56.883s.
- Production: 283.677s. TTS 26.112s; timeline 36.705s; render wrapper 189.742s; thumbnail 21.558s; metadata 26.936s.
- Final: 50.650s, 1080x1920, 60 FPS, H.264 High/yuv420p + AAC; final QC PASS, black frame 0, A/V drift 0, subtitle max two lines, zero publications.
- Selected headline: `WHAT LURKS BEHIND HEAVEN'S DOOR?`, placement bottom.

### Optimization verdict

- **Keep fused final render.** Identical Run 9 render input improved renderer core from 198.010s to 150.560s (-47.450s / ~24%) with the same 3039 frames, 50.650s duration, 1080x1920/60 FPS/H.264 High/yuv420p/audio contract, reference-output validator PASS, and SSIM 0.993367 versus the prior extra-generation output. Production Run 10 renderer core was 157.449s.
- **Keep stricter observation prompt, but classify the gain as modest.** Invalid retries fell from four in Run 9 to two in Run 10, while observation wall fell by ~59.95s despite a slightly larger panel corpus. The same fail-closed schema, lineage, evidence, coverage, and reconciliation validators remain authoritative.
- Synthesis is now measured directly at 56.883s. It is not large enough to justify risky algorithmic changes yet.
- The practical production target is now ~18-20 minutes per three chapters. Further optimizations should be accepted only when an equivalent-input benchmark shows material savings without relaxing any gate.
