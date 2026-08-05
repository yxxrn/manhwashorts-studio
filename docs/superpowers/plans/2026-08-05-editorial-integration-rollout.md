# Editorial Integration and Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate the approved vision, subtitle/voice, motion, render-QC, and rights gates into one auditable editorial workflow with explicit state, feature flags, review stop points, and a safe rollback path.

**Architecture:** Consume the stable contracts from Plans 13. Add one cross-subsystem gate evaluator, expose status through the existing API/UI, keep legacy behavior available only as an explicitly named workflow, and treat rights/source as the final hard blocker. Use p0-real3 only as rights-blocked review material; never claim publication success without a rights-cleared source report.

**Tech Stack:** Existing app/config.py, app/services/pipeline.py, app/services/render.py gate boundaries, existing API routers/schemas/templates/static assets, pytest, slow FFmpeg tests, Ruff, compileall, and the current documentation set.

## Global Constraints

- Implement docs/superpowers/specs/2026-08-05-vision-first-editorial-story-engine-design.md and the approved plans in their stated order. Do not add alternative architecture or automatic fallback.
- Plan 1 must be green and reviewed before Plan 2; Plan 2 must reach explicit voice selection before final render; Plan 3 must reach motion contact-sheet/video review before this plans final integration.
- Gate order is segmentation -> source-space reconciliation -> vision capability/observation -> evidence/story reconciliation -> script approval -> selected voice profile -> timeline/subtitle -> motion plan/QC -> render profile/QC -> rights/source. A failed earlier gate blocks later work.
- A preview or review artifact can exist when the rights gate fails, but publish_allowed remains false and the rights failure stays visible. A rights-blocked artifact is not a publication proof.
- The current p0-real3 source is rights-blocked test material only. Generated MP4, audio, contact sheets, manifests, databases, WAL files, sidecars, user data, credentials, and temporary files remain untracked runtime outputs.
- Since no verified vision credential/model exists at planning time, the real run must report vision_capability_missing and stop before inventing observations. Do not fabricate coverage, evidence, script, or final success.
- Preserve existing unrelated edits. Do not reset, checkout, push, or stage broad directories.
- Use TDD, exact allowlists, bite-sized commits, and named rollback points.

## Dependencies and Ownership

- Plans 1, 2, and 3 are ordered prerequisites. Their subsystem files are not rewritten here; Plan 4 owns only cross-subsystem gate wiring, configuration, status/UI, integration tests, and docs after those commits.
- Plan 4 may make a subsequent integration-only edit to app/services/pipeline.py, app/services/render.py, app/routers/pipeline.py, or app/schemas.py after subsystem owners finish. It must call their public contracts rather than duplicate logic.
- Plan 4 owns app/config.py feature flags, new app/services/editorial_gates.py, integration tests, app/templates/index.html status hooks, app/static/app.js and app/static/app.css status hooks, and docs/STATUS.md, docs/P0_EDITORIAL.md, docs/RELEASE_RUNBOOK.md, CHANGELOG.md, and directly affected architecture docs.
- No Plan 4 task owns app/services/segmentation.py, app/services/vision_adapter.py, app/services/timeline.py, app/services/voice_profiles.py, app/services/motion_qc.py, or their unit tests.
- Each task ends in an independently reviewable commit. The final Plan 4 commit contains only integration source, tests, and docs.

## Stable Gate Interfaces

Add app/services/editorial_gates.py with these stable records and functions.

    from dataclasses import dataclass
    from typing import Mapping, Sequence

    @dataclass(frozen=True)
    class EditorialGateStatus:
        project_id: str
        state: str
        segmentation_state: str
        reconciliation_state: str
        vision_state: str
        evidence_state: str
        script_state: str
        voice_profile_state: str
        timeline_state: str
        motion_state: str
        render_state: str
        rights_state: str
        blocking_reasons: tuple[str, ...]
        counts: Mapping[str, int]
        publish_allowed: bool

    def evaluate_editorial_gates(
        *,
        segmentation: Mapping[str, object],
        reconciliation: Mapping[str, object],
        vision: Mapping[str, object],
        evidence: Mapping[str, object],
        script: Mapping[str, object],
        voice_profile: Mapping[str, object],
        timeline: Mapping[str, object],
        motion: Mapping[str, object],
        render: Mapping[str, object],
        rights: Mapping[str, object],
        project_id: str,
    ) -> EditorialGateStatus:
        ...

    def require_final_render_ready(status: EditorialGateStatus) -> None:
        ...

    def resolve_legacy_workflow(workflow_name: str) -> str:
        ...

The evaluator must return the first blocking gate in order plus all already-known blocking reasons. require_final_render_ready raises the existing validation error type with machine-readable codes and never invokes FFmpeg when a prerequisite is blocked.

## Task 1: Define failing ordered-gate and rights tests

Files:
- Add tests/test_editorial_gates.py.
- Add tests/test_editorial_gate_order.py.
- Add tests/test_rights_gate_preservation.py.

- [ ] Create passing fixtures for each individual gate and a fixture with every gate green except rights.
- [ ] Assert the exact order: segmentation, reconciliation, vision, evidence, script, voice profile, timeline, motion, render, rights.
- [ ] Assert a segmentation failure prevents vision and later work, a missing voice profile prevents final render, and a motion failure does not erase a rights failure.
- [ ] Assert the rights fixture returns publish_allowed false even when all editorial/render gates are green.
- [ ] Assert resolve_legacy_workflow accepts only an explicit legacy workflow name and rejects an implicit fallback request.
- [ ] Run:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_editorial_gates.py tests/test_editorial_gate_order.py tests/test_rights_gate_preservation.py -q

  Expected RED: collection succeeds and fails because editorial_gates.py and the ordered gate contract are absent.
- [ ] Commit only the red tests:

    git add tests/test_editorial_gates.py tests/test_editorial_gate_order.py tests/test_rights_gate_preservation.py
    git diff --cached --check
    git commit -m "test: define ordered editorial gate contract"

## Task 2: Implement the cross-subsystem gate evaluator

Files:
- Add app/services/editorial_gates.py.
- Modify the final preflight caller in app/services/pipeline.py or the existing render gate module only after Plans 13 are committed.
- Add tests/test_editorial_gate_integration.py.

- [ ] Run the red command from Task 1 and inspect the current pipeline/render preflight call path before changing it.
- [ ] Implement evaluate_editorial_gates using the stable signature and deterministic gate order. A missing or malformed status is a blocking reason, never an implicit pass.
- [ ] Map subsystem reasons without discarding detail: coverage incomplete, source-space unresolved, vision_capability_missing, evidence_missing, script_not_approved, voice_profile_missing, subtitle_invalid, motion_qc_failed, render_qc_failed, and source_gate_failed.
- [ ] Preserve rights/source blockers in status and in any sidecar aggregation. publish_allowed is true only when every required gate, including rights, is green.
- [ ] Implement require_final_render_ready before timeline/audio/video work. It may permit a review preview through an explicit preview path, but final profile rendering must stop on any blocking reason.
- [ ] Wire the pipeline and render entry points to call the evaluator once with the persisted subsystem reports. Do not duplicate segmentation, voice, or motion logic.
- [ ] Add an integration test that uses fake subsystem reports and spies on the audio/video executor. The spy must not be called when an earlier gate fails.
- [ ] Run:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_editorial_gates.py tests/test_editorial_gate_order.py tests/test_rights_gate_preservation.py tests/test_editorial_gate_integration.py -q

  Expected GREEN: ordered blocking, reason preservation, preview/final distinction, rights behavior, and no-early-render tests pass.
- [ ] Commit:

    git add app/services/editorial_gates.py app/services/pipeline.py app/services/render.py tests/test_editorial_gate_integration.py
    git diff --cached --check
    git commit -m "feat: integrate ordered editorial gates"

## Task 3: Add explicit flags, shadow review, and no-fallback configuration

Files:
- Modify app/config.py using the projects existing settings pattern.
- Add tests/test_editorial_flags.py.
- Add tests/test_legacy_workflow_boundary.py.

Use these named settings with safe defaults:

    vision_first_story_engine: bool = True
    vision_shadow_mode: bool = False
    vision_provider_type: str = ""
    vision_model: str = ""
    vision_chunk_size: int = 12
    vision_chunk_overlap: int = 2
    require_human_script_approval: bool = True
    require_selected_voice_profile: bool = True
    require_motion_qc: bool = True
    require_rights_gate: bool = True

- [ ] Write red tests for default fail-closed behavior, shadow-review visibility, explicit legacy workflow selection, and rejection of an implicit text fallback.
- [ ] Run:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_editorial_flags.py tests/test_legacy_workflow_boundary.py -q

  Expected RED: current configuration has no complete flag contract and legacy selection is not explicit.
- [ ] Implement settings using the existing configuration loader. Never store credentials in these settings or expose provider secrets through status.
- [ ] In shadow mode, run the configured review path only when vision capability is available and label all results shadow_only. A missing provider remains visible as vision_capability_missing; shadow mode must not turn it into a text fallback.
- [ ] Make legacy text analysis callable only through an explicit workflow name such as legacy_text_rules. The default and vision-first paths reject it as a fallback.
- [ ] Run:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_editorial_flags.py tests/test_legacy_workflow_boundary.py -q

  Expected GREEN: defaults, flag overrides, shadow labels, explicit legacy workflow, and no-fallback tests pass.
- [ ] Commit:

    git add app/config.py tests/test_editorial_flags.py tests/test_legacy_workflow_boundary.py
    git diff --cached --check
    git commit -m "feat: make editorial rollout flags explicit"

## Task 4: Expose auditable status through API and UI

Files:
- Modify the existing pipeline/status router and schema files.
- Modify app/templates/index.html.
- Modify app/static/app.js and app/static/app.css.
- Add tests/test_editorial_status_api.py.
- Add tests/test_editorial_status_ui.py if the current UI test harness supports static assertions.

Status must expose:
project_id, state, each gate state, blocking_reasons, counts for source assets/panels/observations/chunks/claims, source_content_coverage_ratio, unresolved_material_area, instruction version and digest, voice profile state, motion summary, render summary, rights state, and publish_allowed. Do not expose image bytes, audio bytes, authorization headers, or credential values.

- [ ] Write red API tests for missing coverage, missing vision capability, missing voice profile, motion failure, and rights-blocked status. Assert machine-readable reasons and counts.
- [ ] Write a red UI test or static contract test asserting the page has visible labels for coverage, vision capability, evidence, script approval, voice profile, motion QC, render QC, rights, and publish status.
- [ ] Run:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_editorial_status_api.py tests/test_editorial_status_ui.py -q

  Expected RED: the current response/UI lacks ordered gate detail.
- [ ] Add a status endpoint consistent with existing router naming, such as GET /api/projects/{project_id}/editorial-status. Add a final-preflight endpoint that returns a structured block instead of starting a render.
- [ ] Render blocking reasons and counts in the existing UI without inventing green badges. A rights-blocked status must visibly say publish blocked.
- [ ] Run:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_editorial_status_api.py tests/test_editorial_status_ui.py -q

  Expected GREEN: API fields, secret redaction, UI labels, rights block, and no-render preflight tests pass.
- [ ] Commit:

    git add app/routers/pipeline.py app/schemas.py app/templates/index.html app/static/app.js app/static/app.css tests/test_editorial_status_api.py tests/test_editorial_status_ui.py
    git diff --cached --check
    git commit -m "feat: expose editorial gate status"

## Task 5: Add the isolated p0-real3 artifact audit

Files:
- Add tests/test_editorial_artifact_audit.py.
- Add a source-controlled audit helper only if the existing test architecture needs one.
- Do not add p0-real3 media or runtime outputs to Git.

The audit requires these artifact categories when the corresponding gate allows creation:
coverage map and manifest, evidence graph, continuity ledger, story spine, claim-linked script, audition manifest, four audition samples, selected immutable profile, shot list, subtitle list, motion telemetry, QC report, rights/source report, contact sheet, and MP4 only when final render gates allow it.

- [ ] Write red tests for missing artifacts, mismatched hashes/IDs, claim references absent from the script, subtitle punctuation, voice selection without a user action, motion telemetry without shot IDs, and an MP4 marked publishable while source_gate_failed exists.
- [ ] Add a fixture manifest that uses p0-real3 metadata only and points all generated files to a temporary runtime directory.
- [ ] Run:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_editorial_artifact_audit.py -q

  Expected RED: artifact audit and rights-preservation checks are absent.
- [ ] Implement an audit that verifies:
  - every coverage map source asset reconciles to regions, observations, chunks, and claims;
  - the instruction version/digest and voice audition manifest IDs match persisted records;
  - every script claim has evidence references;
  - displayed subtitle strings have no Unicode punctuation and remain within current cue bounds;
  - the selected voice profile is immutable and user-selected;
  - motion telemetry names every shot and has no blocking violations;
  - final stream metadata is checked only for a final-profile MP4;
  - rights/source state controls publish_allowed.
- [ ] Ensure a rights-blocked review can still produce inspectable sidecars/contact sheet when earlier gates allow it, but the audit returns publish_allowed false and exactly includes source_gate_failed.
- [ ] Run:

    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_editorial_artifact_audit.py tests/test_editorial_gates.py -q

  Expected GREEN: artifact lineage, hash matching, subtitle, voice-selection, motion, and rights-blocking tests pass.
- [ ] Commit:

    git add tests/test_editorial_artifact_audit.py
    git diff --cached --check
    git commit -m "test: audit editorial review artifacts"

## Task 6: Update rollout documentation and verification matrix

Files:
- Modify docs/STATUS.md.
- Modify docs/P0_EDITORIAL.md.
- Modify docs/RELEASE_RUNBOOK.md.
- Modify CHANGELOG.md.
- Modify directly affected architecture documentation only when the gate contract is not already discoverable.

- [ ] Document the state sequence and exact block reasons: segmentation, reconciliation, vision_capability_missing, evidence, human script approval, voice_profile_missing, subtitle, motion_qc_failed, render_qc_failed, and source_gate_failed.
- [ ] Document that the real run currently stops at vision_capability_missing without a verified multimodal provider/model; do not present a fabricated final artifact.
- [ ] Document Plan 1 stop, Plan 2 wait-for-voice-selection stop, Plan 3 visual-review stop, and the final preflight stop before any push.
- [ ] Document the p0-real3 rights-blocked test policy and the required artifact audit. State that audition samples compare voice characteristics only and do not represent chapter coverage.
- [ ] Document rollback points at each task commit and the safe rollback method: revert only the integration commit after preserving runtime audit records; never reset a dirty shared checkout.
- [ ] Add the verification matrix with exact commands:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/alembic upgrade head
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest -m "not slow" -q
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest -m slow -q
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/ruff check app tests
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/python -m compileall -q app tests
    git diff --check

  Expected GREEN: migration succeeds, focused and full non-slow tests pass, slow FFmpeg tests pass, Ruff/compileall are clean, and diff-check prints no lines. A real run without vision credentials is expected to assert a controlled vision_capability_missing block rather than fake GREEN content.
- [ ] Review documentation for commands that would touch runtime/user data or push. Remove any such command before committing.
- [ ] Commit:

    git add docs/STATUS.md docs/P0_EDITORIAL.md docs/RELEASE_RUNBOOK.md CHANGELOG.md
    git diff --cached --check
    git commit -m "docs: record editorial integration rollout"

## Task 7: Final integration verification and stop before push

- [ ] Run the focused integration suite:

    cd /home/yusronrohmani/manhwashorts
    PATH=/home/yusronrohmani/.local/bin:$PATH .venv/bin/pytest tests/test_editorial_gates.py tests/test_editorial_gate_order.py tests/test_rights_gate_preservation.py tests/test_editorial_gate_integration.py tests/test_editorial_flags.py tests/test_legacy_workflow_boundary.py tests/test_editorial_status_api.py tests/test_editorial_status_ui.py tests/test_editorial_artifact_audit.py -q

  Expected GREEN: ordered gates, flags, status, UI, artifact lineage, rights preservation, and no-fallback tests pass.
- [ ] Run the Plan 13 focused suites from their stop points, then the full verification matrix. Do not skip slow FFmpeg tests when the environment is available.
- [ ] Run a controlled no-provider integration test using the expanded local mock configuration disabled. Expected result: state BLOCKED, reason vision_capability_missing, no generated story claims, no final render, and no publish permission.
- [ ] If a verified vision provider is configured later, run the real workflow only with a rights-cleared source. If p0-real3 is used, the expected final QC failure list includes source_gate_failed and publish_allowed is false.
- [ ] Audit the Git allowlist before staging:

    git status --short --untracked-files=all
    git diff --name-only
    git ls-files --others --exclude-standard

  Expected GREEN: generated outputs are untracked or ignored, and the intended commit list contains only source, tests, and docs.
- [ ] Stage exact Plan 4 files, inspect the staged list, and verify diff whitespace:

    git add app/config.py app/services/editorial_gates.py app/services/pipeline.py app/services/render.py app/routers/pipeline.py app/schemas.py app/templates/index.html app/static/app.js app/static/app.css tests/test_editorial_gates.py tests/test_editorial_gate_order.py tests/test_rights_gate_preservation.py tests/test_editorial_gate_integration.py tests/test_editorial_flags.py tests/test_legacy_workflow_boundary.py tests/test_editorial_status_api.py tests/test_editorial_status_ui.py tests/test_editorial_artifact_audit.py docs/STATUS.md docs/P0_EDITORIAL.md docs/RELEASE_RUNBOOK.md CHANGELOG.md
    git diff --cached --check
    git diff --cached --name-status

  Expected GREEN: only intended integration source, tests, and docs are staged; no data, media, database, temporary, credential, or user-data path appears.
- [ ] Commit the final integration slice:

    git commit -m "feat: complete editorial gate rollout"

- [ ] Record the full commit SHA and post-commit status. Do not push.

## Stop Point

Stop after the integration artifacts, verification matrix, and controlled no-provider result are reviewed. Report gate-by-gate status, blocking reasons, artifact paths, coverage/evidence/script/voice/motion/render metrics, rights report, exact tests and counts, exact changed files, and commit SHAs. Remain idle for Sol High review before any push or publication action.

## Rollback and Recovery

- Each task commit is independently revertible. Revert only the relevant task or final integration commit after preserving the review record.
- If a migration fails, stop before later tasks and restore only the migration task through a targeted revert; do not reset the shared checkout.
- If a subsystem gate regresses, disable the integration flag or stop at the affected gate; do not enable legacy fallback.
- Runtime artifacts remain outside Git and may be retained for review or removed only through the projects explicit runtime cleanup procedure.

## Execution Handoff

This is an executable plan. Use the required superpowers:subagent-driven-development workflow or run it inline with superpowers:executing-plans, preserving the no-push and Sol-review stop points.
