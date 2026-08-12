# Codex Preview Motion Variants Design

## Context

The current Codex manual preview uses a small horizontal crop displacement for every shot, alternating left and right. Although this satisfies the minimum monotonic-motion rule, it makes the full render look like repeated slide-ins and does not exercise the project's documented motion vocabulary. This slice improves the preview renderer only so the user can review a more representative motion-comic treatment before voice and production integration.

## Design

The edit plan remains the source of truth. Each shot declares exactly one deterministic motion intent, and the renderer maps that intent to an FFmpeg crop/zoom expression. Supported intents are `hold`, `pan_left`, `pan_right`, `pan_up`, `pan_down`, `diagonal`, `push_in`, and `pull_out`.

Motion parameters are fixed constants in the renderer. Pan and diagonal motions move linearly in one direction across the prepared image. Push and pull use bounded monotonic scale changes and recenter the crop around the prepared frame. Hold keeps a stable crop with no intentional camera displacement. No random calls, reversal, oscillation, orbit, whip, shake, or crossfade are introduced. Existing hard cuts, 60 FPS output, 1080x1920 geometry, yuv420p, Barber Chop subtitles, duration schedule, 69-frame audit, manual provenance, and review-only rights gates remain unchanged.

The validator rejects unknown motion intents before any FFmpeg work. The edit plan is updated with a deterministic mixed sequence so one full render visibly exercises multiple motion types. The new output is written to a separate ignored runtime directory; prior v2/v3 artifacts remain available for comparison.

## Testing

Add pure tests for supported motion validation and deterministic filter generation. Ensure different intents produce different filters, `hold` produces a stable crop, and unsupported intents fail with a stable preview motion error. Retain existing contract tests for source order, duration, captions, output geometry, and publication/rights gates.

After implementation, run Ruff, compileall, focused preview tests, render the full 54.2-second 60 FPS preview, probe codec/geometry/frame rate/duration, run black-frame detection, confirm 69 audit frames, inspect the generated contact sheet, and record the new hash and motion provenance in `docs/STATUS.md` and `CHANGELOG.md`. Runtime media remains ignored and no push or publication occurs.
