# Operations

Run the application on the execution host. Keep the local checkout as an
orchestrator; do not store production media or secrets in Git.

## Start

```bash
cd manhwashorts-studio
.venv/bin/python -c "from app.services.render import check_environment; print(check_environment() or 'environment OK')"
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` through an authenticated tunnel or SSH port
forward. Do not bind the application directly to the public internet.

Optional worker:

```bash
# terminal 1
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# terminal 2
.venv/bin/python scripts/worker.py
```

## Render workflow

1. Ingest text and panels.
2. Record owner, licence basis, permission reference, and usage limits.
3. Generate analysis and English script.
4. Edit and approve the script.
5. Run QC; resolve blocking errors.
6. Queue a final render.
7. Verify `final.qc.json`, playback, codecs, dimensions, duration, audio, captions,
   drift, black frames, and checksum.
8. Publish only after explicit confirmation. Private is the default.

## Default voice rule

```text
text: English
voice: American English
locale: en-US
voice_id: the-explainer-american
```

Keep provider, model, voice ID, locale, speed, and voice controls fixed across all
beats. Indonesian requires explicit `language=id` and an Indonesian voice ID.

## Environment

```bash
.venv/bin/python -c "from app.services.render import check_environment; print(check_environment() or 'OK')"
.venv/bin/ruff check app tests scripts alembic
.venv/bin/python -m compileall -q app tests scripts alembic
```

Required runtime tools: Python 3.11+, FFmpeg, FFprobe, a readable subtitle font.
`espeak-ng` is the offline review fallback; it is not a quality claim for final
publication.

## Recovery

- Expired render leases are recovered before worker polling.
- Retry creates a new attempt; prior job records remain auditable.
- Failed scratch directories are removed.
- Missing audio/assets: regenerate the affected stage; never present partial output.
- QC reports and override events are append-only.

## Troubleshooting

**Environment warning:** run `check_environment()` and fix the named dependency.

**Silent audio:** inspect the selected provider and segment records. A null TTS
provider is test-only; do not publish its output.

**TTS provider failure:** inspect provider/model/voice metadata. A configured HTTP
provider must fail loudly; do not silently switch voices.

**QC failure:** inspect `final.qc.json`, shot list, subtitle list, and rights report.
Warning overrides need an actor, reason, and audit event. Blocking errors cannot
be overridden.

**Slow CPU render:** use one worker, reuse cached assets, and reserve heavy TTS
for asynchronous final renders. GPU encoding affects encoding only; crop, effects,
and subtitles remain CPU work.

## Backup and cleanup

Back up the database plus project storage together. Never back up `.env` or raw
credentials into a repository. Remove stale scratch/output artifacts only after
confirming the corresponding job and audit records are retained.

## Release blockers

- Missing or unverified source rights.
- Unapproved script.
- Failed playback, black frame, or audio/video drift check.
- Duration or shot-timing QC failure.
- Public upload without explicit confirmation.

See [RELEASE_RUNBOOK.md](RELEASE_RUNBOOK.md) for the compact release checklist.
See [STATUS.md](STATUS.md) for current project state.
