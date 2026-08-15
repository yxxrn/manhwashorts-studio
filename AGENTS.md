# ManhwaShorts production handoff

This file is an interruption-safe checkpoint for agents working in this repository. It is intentionally explicit about what is proven and what is not; it is not a release claim.

## Authority and current checkpoint

- Repository: `B:\Project\manhwashorts-studio` (local Windows workspace; the VPS is off for this work).
- Branch: `codex/final-production-silent-acceptance`.
- Checkpoint before the interruption commit: `10948efbf91a965aab83545e61ef9e403fa60b5a`.
- The branch is dirty with the production workflow changes listed by `git status --short`; do not reset or discard them.
- `final_test\` contains the user-provided source chapter and must never be committed.
- Runtime state and review outputs live outside Git, principally under `C:\Users\yxxrn\Documents\AutoManhwa\clean-room-acceptance-20260815-h-final` and the repository `data\` runtime area. Do not commit either.

## Proven versus unproven

Proven in this checkpoint: the real launcher reached the interactive menu; the authorized cloud model was selected in prior live runs; the current clean-room run ingested and reconciled the chapter (106 assets and 118 visual panels), produced a complete story map, and reached the narration stage. Focused unit coverage for the visual-aware repair entry path and the title-row registry fix is green.

Not proven: a new clean-room run has not yet produced the required silent MP4. The previous narration attempt stopped at `cloud.narrative_not_grounded`; the bounded repair path is being validated. Do not report `REVIEW_PREVIEW_READY`, playable output, or publication readiness until an MP4 exists and passes ffprobe, black-frame, subtitle, lineage, and visual review gates.

## Current blocker and running work

- Exact latest test blocker: pytest setup received `PermissionError: [WinError 5] Access is denied` while creating `B:\Project\manhwashorts-studio\data\test_runs\pid21520` and `.pytest_cache`; this occurred before test collection and is not a product failure.
- A diagnostic Python process may still be inspecting the isolated `h-final` state. Check processes before starting another DB operation; do not kill unrelated processes.
- No FFmpeg render is currently proven active. The first MP4 remains the shortest next gate.

## Fresh-agent resume

```powershell
Set-Location 'B:\Project\manhwashorts-studio'
git status --short
git branch --show-current
git log -1 --oneline
Get-Process python,ffmpeg,cmd -ErrorAction SilentlyContinue | Select-Object Id,ProcessName,StartTime,Path
& .venv\Scripts\python.exe -m pytest tests/test_cloud_multimodal_mass_production.py -q -k 'ephemeral_review_registry or initial_narration_grounding_failure'
```

If the test data directory is denied, use the repository's approved disposable test-run configuration or an elevated local verification shell; do not change product gates and do not delete the existing runtime DB. For clean-room work, use a new sibling directory under `C:\Users\yxxrn\Documents\AutoManhwa`, set `MS_DATA_DIR`, `MS_DATABASE_URL`, `MS_STORAGE_DIR`, and `MS_REVIEW_DIR` to that directory, and run the checked-in `run_operator.cmd` through the real CLI. Keep any temporary provider bridge in memory/ignored storage only; never place credentials in commands, logs, state, or Git.

## Required acceptance order

1. Make the repair path and lineage helper focused tests green.
2. Run a genuinely fresh clean-room CLI job from the real launcher; no replay, manual DB edit, provider cache reuse, or ad-hoc production bypass.
3. Obtain a silent 50–60 second regular-render MP4 from `final_test`, then verify 1080x1920, 60 FPS, H.264 High/yuv420p, zero audio, no black frames, chronological evidence-safe panel usage, max two subtitle lines, yellow active-word karaoke, and `publish_allowed=false`.
4. Inspect contact sheet and boundary/key frames. Only after the MP4 exists, complete the post-preview voice-package design, whole-program audit, second clean-room run, final docs, and release merge.

The final voiced workflow, TTS/provider selection, audio timing, publication, rights approval, and deployment remain deferred. Never invent voice timing or evidence to get past a gate.

## Rollback and scope

The safe rollback point for this interruption checkpoint is `10948efbf91a965aab83545e61ef9e403fa60b5a`; preserve the working tree and use a new commit to correct defects. Commit source/tests/docs only. Do not add `final_test`, media, databases, encrypted/unencrypted credentials, caches, temporary scripts, or provider payloads.
