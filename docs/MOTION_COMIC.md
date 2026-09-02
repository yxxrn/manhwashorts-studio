# Motion-comic pipeline

Current implementation and verification contract. This document describes the
code that exists now; planning history belongs in Git history, not here.

## Product contract

- Input: authorized panels + commentary text.
- Default text: English.
- Default voice: American English, `en-US`, `the-explainer-american`.
- Timeline clock: measured narration audio.
- Output: `1080×1920`, `60 FPS` by default, H.264/AAC.
- Project target default: `55s` (schema range `10–90s`). Standard final production must remain within `50–60s`. A shorter adaptive duration may be used only for review/diagnosis when grounded visual capacity is insufficient; it cannot pass the final production gate.
- Publication/render readiness: exact approval + evidence/media integrity + strict QC. Rights metadata is non-blocking by default unless enforcement is explicitly enabled.
- Determinism: identical input and seed produce the same plan.
- No generative replacement, scraping, watermark removal, or automatic public upload.

## Stages

```text
ingest → rights → analysis → script → approval → voice
      → timeline → subtitles → motion → render → QC → review/publish
```

## Analysis and script

The offline rules path extracts characters, locations, events, conflict, twist,
and cliffhanger. The script generator produces five beats:

```text
hook → setup → conflict → twist → CTA
```

Each generated claim remains traceable to source material. LLM/BYOK providers can
improve rewriting; they do not bypass evidence/lineage/QC validation. Rights enforcement follows deployment policy, and approval remains an explicit persisted identity (manual or the explicit trusted-agent publish path).

## Motion director

`app/services/motion_director.py` creates a deterministic `MotionPlan` from:

```text
section · narration tags · ROI/focus · duration · motion history · seed
```

Supported modes:

```text
hold · slow_push · slow_pull · guided_pan · focus_shift · panel_reveal
split_focus · panel_stack · impact · whip_transition · atmospheric
static_emphasis
```

Every shot records asset, source family, ROI label, motion mode, reason, camera
curve, transition, start, and end. Director rules prefer alternate assets/families,
avoid repetitive crops, and reserve strong effects for meaningful beats.

## Rendering

Pillow handles verified image preparation, safe vertical crop, focus motion,
split-focus, panel stacks, atmospheric treatment, and deterministic caching.
FFmpeg handles timeline composition, subtitles, audio mux, and final encoding.

A scene failure fails the job. A concat graph failure may fall back to hard cuts.
Partial output is never marked final.

## Profiles

The render API accepts:

```json
{"kind":"final","encoder":"cpu","profile":"Balanced"}
```

Profiles: `Auto`, `Calm`, `Balanced`, `Dynamic`, and `No motion`.

## QC contract

Persisted artifacts:

```text
final.qc.json · shot_list.json · subtitle_list.json
panel_to_script_mapping.json · panel_catalog.json · contact_sheet.jpg · source_rights_report.json · checksum
```

Blocking checks include:

- invalid/unknown duration, duration above the configured Shorts ceiling, or profile-specific duration contract failure;
- profile-specific shot/average-duration contract failure (adaptive/reference shots remain capped at four seconds);
- excessive same-panel/same-crop hold;
- single-word caption ratio `>=15%`;
- invalid or repetitive motion;
- missing motion reason;
- playback/decode failure;
- audio/video drift over one frame;
- prolonged black frame;
- source/evidence failure, plus rights failure only when rights enforcement is explicitly enabled.

QC fields include `audio_video_drift`, `black_frame_duration`,
`full_playback_verified`, `publish_allowed`, and `rights_confidence`.

Warning overrides are append-only:

```text
POST /api/projects/{project_id}/quality/override
GET  /api/projects/{project_id}/quality/overrides
```

Every override records actor, reason, code, timestamp, and before/after state.
Blocking errors cannot be overridden.

## Reliability

- Atomic render claim.
- Lease token, expiry, and heartbeat.
- Stale-job recovery before worker polling.
- Per-job scratch directory.
- Failed-scratch cleanup.
- Test database isolation.
- Output checksum and media probes.

## Verification

```bash
.venv/bin/ruff check app tests scripts alembic
.venv/bin/python -m compileall -q app tests scripts alembic
.venv/bin/python -m pytest -q -m 'not slow'
.venv/bin/python -m pytest -q
git diff --check
```

The slow suite executes real FFmpeg and checks codecs, dimensions, duration,
caption pixels, audio, drift, and black frames.

## Release state

Technical pipeline: implemented and exercised on the designated execution host (Oracle in the current deployment).
Production publication: rights metadata is audited and non-blocking by default; approval and strict QC remain blocking. Synthetic
fixtures and user-provided archives are review material, not rights evidence.

See [STATUS.md](STATUS.md), [OPERATIONS.md](OPERATIONS.md), and
[RELEASE_RUNBOOK.md](RELEASE_RUNBOOK.md).
