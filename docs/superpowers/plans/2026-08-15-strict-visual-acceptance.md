# Strict Visual Acceptance Implementation Plan

> **For agentic workers:** Implement inline with test-driven development and commit each independently proven task.

**Goal:** Make preview delivery fail closed when karaoke text exceeds the safe rectangle, the requested font is not the checked-in font, or removable edge whitespace remains.

**Architecture:** Extend the existing deterministic subtitle layout and framing telemetry rather than creating a second renderer. Produce measured manifests from the exact font/crops used, and make preview bundling validate those measurements before reporting success.

**Tech Stack:** Python, Pillow, FFmpeg/libass, pytest, JSON sidecars.

## Global Constraints

- Exact checked-in BarberChop font for measurement and rendering; no silent fallback.
- Horizontal safe margin 120 px at 1080x1920; maximum two subtitle lines.
- Blank target 3%; maximum 5% only with a protected-art evidence exception.
- Preserve balloon, protected-art, lineage, narration, and timing contracts.
- Do not commit source panels, media, databases, caches, or credentials.

---

### Task 1: Pixel-safe exact-font karaoke

**Files:**
- Modify: `tests/test_regular_render_karaoke.py`
- Modify: `app/services/render.py`
- Modify: `app/services/subtitle_karaoke.py`

**Produces:** An exact-font layout manifest and ASS events whose maximum active-state width is no greater than `width - 2 * safe_margin_px`.

- [ ] Add regression tests proving missing/unloadable font blocks, ASS uses the configured family consistently, absolute positioning cannot bypass margins, and the reported maximum width includes active-word scaling.
- [ ] Run focused tests and confirm the current implementation fails.
- [ ] Resolve the checked-in font once, measure every active state, split semantic chunks until all states fit, remove unsafe absolute positioning, and expose measured layout evidence.
- [ ] Run focused karaoke and render tests; commit and push.

### Task 2: Measured whitespace and preview QC

**Files:**
- Modify: `tests/test_reference_visual_review.py`
- Modify: `tests/test_review_source_upscale.py`
- Modify: `app/services/render.py`
- Modify: `app/services/review_preview.py`
- Modify: `app/services/pipeline.py`

**Produces:** Per-shot blank telemetry with a 3% target, evidence-limited 5% exception, tighter-crop/alternative-panel retry, and QC derived from render evidence.

- [ ] Add regressions proving a 16% crop is rejected, a 3-5% exception requires protected-art evidence, and QC cannot substitute contract constants for measurements.
- [ ] Run focused tests and confirm failure.
- [ ] Tighten crop selection, persist measured facts, and block preview bundling on missing/violating evidence.
- [ ] Run focused framing/QC tests; commit and push.

### Task 3: Replacement preview acceptance

**Files:**
- Modify: `AGENTS.md`
- Modify: `docs/STATUS.md`
- Runtime only: `data/_final_acceptance_strict/`

**Produces:** A new 50-60 second silent MP4, contact sheet, probe, and measured QC report.

- [ ] Run the existing `final_test` production-preview path into a new isolated output directory.
- [ ] Verify FFprobe, subtitle layout evidence, per-shot blank values, and contact sheet/key frames.
- [ ] If a measured gate fails, correct only that blocker and repeat once; do not weaken thresholds.
- [ ] Update handoff with proven/unproven state, run relevant tests and `git diff --check`, then commit/push. Merge to `main` only after visual acceptance.
