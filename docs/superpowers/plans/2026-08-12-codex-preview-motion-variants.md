# Codex Preview Motion Variants Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans, task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the 60 FPS Codex manual preview use deterministic mixed motion intents instead of one repeated horizontal slide.

**Architecture:** Keep the edit plan as the source of truth. Add a validated motion-intent set and a pure FFmpeg-filter builder to the manual renderer; each shot maps to one monotonic pan, diagonal, hold, push-in, or pull-out. Preserve hard cuts, Barber Chop subtitles, 54.2-second timing, review-only gates, and ignored runtime media.

**Tech Stack:** Python 3.11, Pillow, FFmpeg, pytest, Ruff.

## Global Constraints

- Motion is deterministic, monotonic, and one-directional per shot.
- No reversal, oscillation, random motion, orbit, whip, shake, crossfade, voice, or audio.
- Output remains 1080x1920, 60 FPS by default, H.264 High, yuv420p.
- Source orders 1..23 remain chronological and exactly once.
- `publish_allowed=false` and `rights_status="internal review only"` remain enforced.
- Barber Chop must load from `assets/fonts/BarberChop.otf`.
- Runtime media, edit plans, audit frames, and sidecars remain under ignored `data/`.

---

### Task 1: Add failing motion contract tests

**Files:**
- Modify: `tests/test_codex_manual_preview.py`

- [ ] Add tests asserting the renderer exposes `SUPPORTED_MOTIONS` containing `hold`, `pan_left`, `pan_right`, `pan_up`, `pan_down`, `diagonal`, `push_in`, and `pull_out`.
- [ ] Add tests calling `build_motion_filter(motion, duration)` and asserting different supported intents produce different filters, `hold` is stable, and an unknown intent raises `ValueError` containing `preview.motion_invalid`.
- [ ] Run `python -m pytest tests/test_codex_manual_preview.py -q` and confirm the new tests fail because the interface is absent.

### Task 2: Implement deterministic motion filter builder

**Files:**
- Modify: `scripts/review/render_codex_manual_preview.py`

- [ ] Add `SUPPORTED_MOTIONS` and `build_motion_filter(motion: str, duration: float) -> str`.
- [ ] Use fixed prepared-image geometry and bounded values. Pan intents change only one axis; diagonal changes both; push/pull change crop size monotonically; hold keeps a fixed crop.
- [ ] Reject unsupported motion before source preparation or FFmpeg execution.
- [ ] Replace the hard-coded alternating `x_expr`/`y_expr` in `render_preview()` with `build_motion_filter()`.
- [ ] Keep `format=yuv420p`, the configured plan FPS, hard-cut concat, and existing Barber Chop subtitle filter unchanged.
- [ ] Run focused tests and Ruff; all must pass.

### Task 3: Update the ignored edit plan with mixed intents

**Files:**
- Modify runtime only: `data/codex-vision-preview-50-60s-v2/edit-plan.json`

- [ ] Keep all durations, crops, source orders, captions, FPS, rights, and provenance fields unchanged.
- [ ] Replace the repeated pan alternation with this deterministic sequence in shot order 1..23:
  `push_in, pan_right, hold, pan_up, diagonal, pan_left, push_in, pan_down, hold, pull_out, pan_right, diagonal, push_in, pan_left, pan_down, hold, pull_out, pan_right, diagonal, push_in, pan_up, pan_left, pull_out`.
- [ ] Run `--validate-only` and confirm 23 shots, source orders 1..23, duration 54.2, and `publish_allowed=false`.

### Task 4: Render and verify the mixed-motion preview

**Files:**
- Create runtime only: `data/codex-vision-preview-motion-v4/`
- Modify: `docs/STATUS.md`, `CHANGELOG.md`

- [ ] Render using the local manifest and mixed edit plan into the separate v4 directory.
- [ ] Probe the output and require H.264 High, 1080x1920, 60/1 FPS, yuv420p, 54.2 seconds, and no audio.
- [ ] Run black-frame detection and confirm no findings; confirm 69 audit frames and inspect the contact sheet.
- [ ] Record the new SHA-256, motion sequence, output path, and review-only boundary in status/changelog.

### Task 5: Final checks and commit

- [ ] Run `python -m pytest tests/test_codex_manual_preview.py -q`.
- [ ] Run `python -m ruff check scripts/review/render_codex_manual_preview.py tests/test_codex_manual_preview.py`.
- [ ] Run `python -m compileall -q scripts/review/render_codex_manual_preview.py tests/test_codex_manual_preview.py` and `git diff --check`.
- [ ] Confirm no runtime `data/` files are staged.
- [ ] Commit source/test/docs only with `git commit -m "feat: add varied preview motion intents"`.
- [ ] Do not push or publish.
