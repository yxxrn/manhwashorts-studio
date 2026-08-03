# Current status

Updated: 2026-08-04

## Implemented

- FastAPI UI/API with local SQLite and content-addressed storage.
- Rights metadata on every ingested asset.
- Rules-based analysis and five-beat English script generation.
- Project defaults: English text, American English voice, `en-US`,
  `the-explainer-american`.
- Explicit Indonesian opt-in.
- Generic offline/HTTP TTS adapter with fixed per-render voice settings.
- Deterministic panel scoring, ROI focus, camera motion, transitions, and
  split-focus/panel-stack compositions.
- Audio-master timeline and subtitle timing.
- CPU FFmpeg render: 1080×1920, 30 FPS, H.264/AAC.
- Persisted QC report, immutable override history, render leases, stale-job
  recovery, resource metrics, and cleanup.
- Private-by-default publication gate with rights and approval checks.
- BYOK encryption and provider discovery.
- Full test, lint, compile, and real-FFmpeg validation on Google execution host.

## Current runtime boundary

- Production execution target: Google VPS through the `google` SSH alias.
- Local machine: orchestration, source transfer, and requested artifact delivery.
- No production media or credentials belong in Git.
- OmniVoice Studio remains an external voice experiment. It is not a core
  dependency, default provider, or release gate.

## Not complete

- A real, clean panel source with verified publication rights.
- Final commercial TTS provider selection.
- Production-ready public upload credentials and channel policy.
- Scheduling UI, external queue, multi-channel workspaces, analytics, and full
  music/SFX workflow.

## Release gate

A render is review-only until all are true:

1. Source owner and licence/permission are recorded.
2. Script is approved.
3. QC has no blocking errors.
4. Playback, codecs, dimensions, duration, audio, subtitle pixels, drift, and
   black-frame checks pass.
5. Publication is explicitly confirmed by the user.

Current state: **development / review-only**.
