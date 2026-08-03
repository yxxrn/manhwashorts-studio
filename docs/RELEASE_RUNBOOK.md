# Motion-Comic Release Runbook

## Preconditions

1. Use only panels with documented rights. `NOT_FOR_PUBLICATION` fixtures are test-only.
2. Run on the VPS execution host. Do not persist production media in the local checkout.
3. Verify FFmpeg, FFprobe, subtitle font, and local TTS:

```bash
.venv/bin/python -c "from app.services.render import check_environment; print(check_environment() or 'OK')"
```

## Pipeline

1. Ingest text and panels with rights metadata.
2. Generate analysis, script, voice, timeline, subtitles.
3. Review/approve the script.
4. Run QC. Blocking errors cannot be overridden.
5. Review QC history and any warning overrides. Every override needs an actor and reason.
6. Queue a final render. Confirm actual encoder, fallback reason, wall time, RSS, and scratch size.
7. Confirm `final.qc.json`, playback, duration, H.264/AAC, 1080x1920, and checksum.
8. Publish only after explicit per-request confirmation. Private is the default.

## Recovery

- Worker startup requeues expired render leases.
- Retry preserves the previous job record and creates a new attempt.
- Failed render scratch directories are removed automatically.
- Missing audio/assets: regenerate the affected stage; never present a partial render.
- QC history is append-only; do not delete snapshots or override events.

## Release blockers

- Any rights/source-cleanliness failure.
- Failed playback, black frame over 0.4s, or audio/video drift over one frame.
- No clean-source sample with a rights declaration.
- Unreviewed script or unresolved blocking QC error.

## Verification

```bash
.venv/bin/ruff check app tests scripts
.venv/bin/python -m compileall -q app tests scripts alembic
unset MS_DATABASE_URL
.venv/bin/python -m pytest -q -m 'not slow'
.venv/bin/python -m pytest -q
git diff --check
```