# Motion Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make planned and rendered motion deterministic, physically legible, and bounded: correct even-pixel crop quantization, remove automatic sinusoidal camera movement, enforce small intentional zooms and static holds, and block editorial QC when sampled motion telemetry violates the contract.

**Architecture:** Preserve the existing shot and render interfaces while replacing unsafe crop math and legacy oscillation modes. Add pure telemetry auditing in app/services/motion_qc.py, expose frame/shot intervals and violation reasons, and wire one blocking motion result into the existing render and editorial QC sidecars. Preview and final profiles use the same motion plan; only encode settings differ.

**Tech Stack:** Existing Python motion_director, shot/render services, FFmpeg filter generation, app/services/editorial_qc.py, app/services/quality.py, pytest, and existing slow FFmpeg fixtures. No new media dependency and no automatic shake/orbit effect.

## Global Constraints

- Implement docs/superpowers/specs/2026-08-05-vision-first-editorial-story-engine-design.md and the approved Sol decisions.
- Correct quantization with a bounded even floor: floor(value / 2) multiplied by 2, then clamp to legal bounds. Never use trunc(value) multiplied by 2.
- Remove automatic shake_zoom, impact_shake, explosion, micro_shake, and orbit sinusoidal center oscillation. Existing plans using them must fail validation or go through an explicit migration path.
- Normal zoom is at most 1.06 and impact zoom is at most 1.08. Every shot has one smooth intent, periodic static holds, and no automatic sin or cos center motion.
- Motion QC samples planned or rendered center, scale, displacement, acceleration, reversal, and static-hold coverage. Violations identify shot and frame intervals and are blocking.
- Preserve rights/source gates. A motion pass never makes a rights-blocked artifact publishable.
- Do not create, stage, commit, or delete runtime media, databases, user data, credentials, or unrelated changes.
- Use TDD, one focused commit per task, and preserve the current dirty worktree state.

## Dependencies and Ownership

- Plan 1 must provide a reconciled story/timeline input; Plan 2 provides subtitle/voice contracts. Plan 3 does not change either contract.
- Plan 3 owns app/services/motion_director.py, app/services/render.py motion helpers, new app/services/motion_qc.py, motion branches in app/services/editorial_qc.py and app/services/quality.py, and motion tests/fixtures.
- Plan 4 consumes MotionQCResult and owns only cross-subsystem gate orchestration and rollout documentation.
- Execute Plan 3 after Plans 1 and 2 review boundaries are known. If a file has existing unrelated edits, patch only the motion-owned functions and preserve the rest.

## Stable Interfaces

Add these records and functions in app/services/motion_qc.py.

    from dataclasses import dataclass
    from typing import Literal, Sequence

    @dataclass(frozen=True)
    class MotionSample:
        shot_id: str
        frame_index: int
        center_x: float
        center_y: float
        scale: float
        intent: Literal["hold", "pan", "zoom", "impact"]
        fps: int

    @dataclass(frozen=True)
    class MotionViolation:
        code: str
        shot_id: str
        first_frame: int
        last_frame: int
        detail: str

    @dataclass(frozen=True)
    class MotionQCResult:
        passed: bool
        samples: tuple[MotionSample, ...]
        max_displacement_per_frame: float
        max_scale_delta_per_frame: float
        max_acceleration: float
        reversal_count: int
        static_hold_coverage: float
        violations: tuple[MotionViolation, ...]

    def audit_motion_samples(
        samples: Sequence[MotionSample],
        *,
        normal_zoom_limit: float = 1.06,
        impact_zoom_limit: float = 1.08,
        minimum_static_hold_coverage: float,
    ) -> MotionQCResult:
        ...

    def audit_motion_plan(
        plan: Sequence[Mapping[str, object]],
        *,
        fps: int,
        minimum_static_hold_coverage: float,
    ) -> MotionQCResult:
        ...

Use integer frame indexes in reports and stable sorted violation order.

## Task 1: Define failing even-pixel crop tests

Files:
- Add tests/test_motion_quantization.py.
- Extend current render fixtures only with small source-controlled values.
- Do not change production code before the red assertions.

- [ ] Add pure tests for coordinate quantization: value 11.9 becomes 10, value 10 becomes 10, negative values clamp to 0, and an odd legal upper bound is reduced to its largest even value.
- [ ] Add crop-size tests for odd, zero, oversized, and negative input. Assert width and height are positive even integers within legal bounds.
- [ ] Add a regression that inspects generated crop/filter parameters and fails when trunc(value) multiplied by 2 is used.
- [ ] Run:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_motion_quantization.py -q

  Expected RED: tests collect and fail on the existing truncation/odd-size behavior.
- [ ] Commit only the red tests:

    git add tests/test_motion_quantization.py
    git diff --cached --check
    git commit -m "test: define even crop quantization contract"

## Task 2: Correct crop math and reject legacy oscillation modes

Files:
- Modify app/services/render.py crop helpers.
- Modify app/services/motion_director.py plan validation/filter construction.
- Add tests/test_motion_legacy_modes.py.
- Add or extend tests/test_render.py.

Implement the quantizer with explicit bounds:

    def even_pixel_coordinate(value: float, *, minimum: int, maximum: int) -> int:
        bounded = max(float(minimum), min(float(maximum), value))
        quantized = 2 * math.floor(bounded / 2.0)
        return max(minimum, min(maximum - (maximum % 2), quantized))

    def even_crop_size(value: float, *, minimum: int, maximum: int) -> int:
        if minimum <= 0 or maximum < minimum:
            raise ValueError("invalid crop bounds")
        quantized = 2 * math.floor(max(float(minimum), min(float(maximum), value)) / 2.0)
        return max(2, min(maximum - (maximum % 2), quantized))

- [ ] Run the red commands:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_motion_quantization.py tests/test_motion_legacy_modes.py tests/test_render.py -q

  Expected RED: crop tests fail and legacy-mode tests find the current automatic oscillation strings or mode names.
- [ ] Replace all crop quantization callers with the bounded even helpers. Assert legal crop sizes before building the FFmpeg filter.
- [ ] Define REMOVED_OSCILLATION_MODES as a stable frozenset containing shake_zoom, impact_shake, explosion, micro_shake, and orbit. Validation raises a machine-readable legacy_motion_mode error with shot ID and mode, unless an explicit migration function rewrites the plan to a hold, pan, zoom, or impact intent and records that migration.
- [ ] Remove automatic sinusoidal center movement. Filter strings and motion plans must contain no sin or cos expressions and no implicit periodic shake.
- [ ] Enforce normal scale at most 1.06 and impact scale at most 1.08. Reject a shot with more than one motion intent. Add deterministic periodic static holds based on frame ranges, not a random or sinusoidal schedule.
- [ ] Run:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_motion_quantization.py tests/test_motion_legacy_modes.py tests/test_render.py -q

  Expected GREEN: even bounds, legal filters, legacy rejection/migration, no oscillation, zoom limits, one-intent, and static-hold tests pass.
- [ ] Commit:

    git add app/services/render.py app/services/motion_director.py tests/test_motion_quantization.py tests/test_motion_legacy_modes.py tests/test_render.py
    git diff --cached --check
    git commit -m "feat: stabilize crop and remove camera oscillation"

## Task 3: Add deterministic motion telemetry and blocking QC

Files:
- Add app/services/motion_qc.py.
- Modify motion telemetry construction in app/services/motion_director.py.
- Wire the result into app/services/editorial_qc.py, app/services/quality.py, and the render sidecar path.
- Add tests/test_motion_qc.py and tests/test_motion_qc_integration.py.

- [ ] Write pure-math red tests for:
  - per-frame center displacement and scale delta;
  - acceleration from successive displacement deltas;
  - direction reversals;
  - static-hold frame coverage;
  - normal and impact scale violations;
  - more than one intent per shot;
  - a missing or non-monotonic frame sequence.
- [ ] Assert each violation includes code, shot_id, first_frame, last_frame, and a detail string suitable for QC review.
- [ ] Run:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_motion_qc.py tests/test_motion_qc_integration.py -q

  Expected RED: collection succeeds and fails because motion_qc.py and the blocking sidecar field are absent.
- [ ] Implement audit_motion_samples. Use deterministic thresholds from the spec/config, preserve sample order, and compute reversal count without smoothing away a real reversal. A violation makes passed false.
- [ ] Implement audit_motion_plan by converting each planned keyframe interval to frame samples at its declared FPS. Include hold intervals explicitly, and record the static-hold fraction.
- [ ] Add telemetry to the existing render sidecar with shot IDs, frame intervals, center, scale, acceleration, intent, static-hold intervals, and result hash. Do not include source payloads.
- [ ] Add an editorial QC failure code motion_qc_failed with machine-readable violations. Quality aggregation must retain rights/source failures and must not replace them with motion status.
- [ ] Make preview and final render call the same audit before encoding. Final render refuses to start when motion QC fails.
- [ ] Run:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_motion_qc.py tests/test_motion_qc_integration.py tests/test_editorial_qc.py tests/test_quality.py -q

  Expected GREEN: pure math, telemetry, sidecar, blocking, rights-preservation, and existing QC tests pass.
- [ ] Commit:

    git add app/services/motion_qc.py app/services/motion_director.py app/services/editorial_qc.py app/services/quality.py tests/test_motion_qc.py tests/test_motion_qc_integration.py
    git diff --cached --check
    git commit -m "feat: add blocking motion telemetry QC"

## Task 4: Add the real-FFmpeg line-art shimmer and jitter regression

Files:
- Add a small source-controlled generator under tests/fixtures/motion_line_art.py or use the project’s existing synthetic fixture helper.
- Add tests/test_motion_ffmpeg.py marked slow.
- Do not commit generated MP4, PNG, audio, or sidecar output.

- [ ] Write a red slow test that renders a deterministic line-art panel with one hold, one pan, one zoom, and one impact segment through the normal filter path.
- [ ] Assert the output stream can be decoded at the requested FPS and dimensions, and motion telemetry has no automatic center oscillation.
- [ ] Add a deterministic frame-difference metric for line edges. The test must flag shimmer/jitter caused by odd crop quantization or repeated center reversal, while allowing the planned single-intent transition.
- [ ] Run the red slow command:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest -m slow tests/test_motion_ffmpeg.py -q

  Expected RED: the regression identifies the current crop or oscillation defect, or fails because the synthetic motion fixture is not yet wired.
- [ ] Implement only the fixture/test wiring and the production defect already covered by Tasks 2 and 3. Keep outputs in the test temporary directory and remove them through the fixture cleanup.
- [ ] Run:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest -m slow tests/test_motion_ffmpeg.py -q

  Expected GREEN: the synthetic line-art render decodes, crop sizes are even, no sinusoidal motion is present, and shimmer/jitter stays under the asserted deterministic threshold.
- [ ] Commit:

    git add tests/fixtures/motion_line_art.py tests/test_motion_ffmpeg.py
    git diff --cached --check
    git commit -m "test: cover real ffmpeg motion stability"

## Task 5: Verify Plan 3 and stop for visual review

- [ ] Run focused tests:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_motion_quantization.py tests/test_motion_legacy_modes.py tests/test_motion_qc.py tests/test_motion_qc_integration.py tests/test_editorial_qc.py tests/test_quality.py tests/test_render.py -q

  Expected GREEN: quantization, legacy mode, pure telemetry, blocking QC, render, and rights-preservation tests pass.
- [ ] Run the slow regression:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest -m slow tests/test_motion_ffmpeg.py -q

  Expected GREEN: the real-FFmpeg line-art regression passes.
- [ ] Run static checks:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/ruff check app tests
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/python -m compileall -q app tests
    git diff --check

  Expected GREEN: Ruff is clean, compileall exits zero, and diff-check prints no lines.
- [ ] Review the motion sidecar and contact sheet. Confirm every shot has one intent, normal/impact limits, static holds, no oscillatory filters, and violation intervals are empty.
- [ ] Commit the Plan 3 slice:

    git add app/services/motion_qc.py app/services/motion_director.py app/services/render.py app/services/editorial_qc.py app/services/quality.py tests/fixtures/motion_line_art.py tests/test_motion_quantization.py tests/test_motion_legacy_modes.py tests/test_motion_qc.py tests/test_motion_qc_integration.py tests/test_motion_ffmpeg.py
    git diff --cached --check
    git commit -m "feat: complete motion stability slice"

## Stop Point

Stop after focused and slow tests are green and after the motion contact sheet/video is available for human visual review. Report quantization examples, legacy-mode results, motion sample count, maximum displacement/scale delta/acceleration, reversal count, static-hold coverage, violation list, FFmpeg test result, and exact commit SHA. Do not start final rollout integration or push.

## Execution Handoff

This is an executable plan. Use the required superpowers:subagent-driven-development workflow or run it inline with superpowers:executing-plans, preserving the human visual-review stop point.
