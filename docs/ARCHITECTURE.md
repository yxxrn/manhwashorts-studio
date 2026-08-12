# Architecture

Current implementation of ManhwaShorts Studio.

## Invariants

1. **Audio is the clock.** Scene and subtitle timing follow measured narration.
2. **Stages are idempotent.** Re-running one stage replaces its own output.
3. **Rights and quality decide release.** No router, worker, or upload path bypasses
   the shared policy/QC services.
4. **Providers are replaceable.** Offline defaults keep a fresh checkout usable;
   BYOK and HTTP adapters do not leak provider details into rendering.
5. **Approval is structural.** Editing a script clears approval; publication needs
   explicit confirmation.
6. **English/American English is the product default.** Other languages require
   explicit project configuration.

## Data flow

```text
text/panels
    │
    ▼
ingest + rights metadata
    │
    ▼
analysis → five-beat script → approval
    │                 │
    │                 ▼
    └──────────────► TTS clips + word timing
                          │
                          ▼
                 timeline + subtitles
                          │
                          ▼
                 motion director
                          │
                          ▼
                   FFmpeg render
                          │
                          ▼
                QC + policy + review
                          │
                          ▼
                 private-by-default publish
```

## Layers

```text
app/templates + app/static     server-rendered UI, no frontend build
app/routers                    auth, projects, pipeline, credentials, publish
app/services                   ingest, analysis, script, TTS, timeline, render,
                               quality, policy, publish, YouTube, storage
app/models.py + app/db.py      SQLAlchemy models and sessions
SQLite/filesystem/FFmpeg       persistence and media toolchain
```

Routers validate, authorize, and delegate. Business decisions stay in services.

## Ingest and rights

Accepted sources: pasted text, TXT/MD/PDF/DOCX, JPEG/PNG/WebP. Content is sniffed
and images are verified with Pillow. Each asset stores owner, licence basis,
permission reference/date, usage limits, checksum, and rights status.

`UNKNOWN`, missing owner, or missing permission basis cannot become a publication
cleared asset. User-provided and fixture panels remain review-only until rights
are verified.

## Script and voice

The rules generator emits five beats: hook, setup, conflict, twist, CTA. New
projects default to:

```text
language: en
voice_id: the-explainer-american
locale: en-US
```

Indonesian is explicit opt-in. TTS provider/model/voice/locale/speed/settings are
recorded per audio segment. A configured external provider fails loudly; it does
not silently downgrade to a different production voice.

The core project uses a generic HTTP TTS contract. OmniVoice Studio is an external
experiment only and is intentionally absent from the core dependency graph.

## Motion director

`app/services/motion_director.py` builds a deterministic plan from section tags,
ROI/focus targets, duration, prior motion history, and seed. Modes include:

```text
hold · slow_push · slow_pull · guided_pan · focus_shift · panel_reveal
split_focus · panel_stack · impact · whip_transition · atmospheric
static_emphasis
```

Rules avoid repetitive crops, cooldown source families, and reserve strong effects
for meaningful beats. Every shot persists asset, family, ROI, mode, reason, curve,
transition, and timing.

## Renderer

Pillow prepares vertical crops and effects. FFmpeg composes the timeline and muxes
the audio master. Output contract:

```text
1080×1920 · 60 FPS default · H.264 · AAC
```

Scene failure fails the job. A concat-graph failure may use a hard-cut fallback;
partial output is never presented as final.

## Reliability

- Atomic render claim with lease token and heartbeat.
- Expired jobs are requeued before worker polling.
- Per-job scratch directory and cleanup on failure.
- Test database isolation and destructive-operation guards.
- Output checksum and QC artifacts stored with the render.

## Release state

Technical pipeline: implemented and exercised. Production state: blocked until a
real source has a verified rights declaration. See [STATUS.md](STATUS.md) and
[RELEASE_RUNBOOK.md](RELEASE_RUNBOOK.md).
