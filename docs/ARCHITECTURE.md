# Architecture

How the pieces fit together, and why they are arranged this way.

## Design principles

1. **Audio is the clock.** Scene and subtitle timings are derived from measured
   voice-over durations, never the reverse. This is why video length always
   tracks narration instead of drifting.
2. **Stages are idempotent.** Re-running a stage replaces its own output rather
   than appending. You can regenerate the script, the voice, or the timeline
   independently without corrupting the others.
3. **One place decides publishability.** `services/policy.py` and
   `services/quality.py` are the only authorities. The API, the worker, and the
   tests all ask the same question and get the same answer.
4. **Providers are swappable.** TTS, LLM, YouTube, and storage each sit behind a
   small interface with an offline default, so a fresh clone runs with no
   credentials.
5. **Approval is structural.** There is no code path from "rendered" to
   "published" that skips a human. Editing a script clears its approval.

## Layers

```
┌─────────────────────────────────────────────────┐
│ app/templates + app/static     (server-rendered │
│                                 SPA, no build)  │
├─────────────────────────────────────────────────┤
│ app/routers      auth · projects · pipeline ·   │
│                  publish                        │
│   thin: validate, authorise, delegate           │
├─────────────────────────────────────────────────┤
│ app/services                                    │
│   pipeline.py    orchestration + audit          │
│   ingest  analysis  script  tts                 │
│   timeline  render  quality  policy             │
│   publish  youtube  storage                     │
├─────────────────────────────────────────────────┤
│ app/models.py    SQLAlchemy · 15 tables         │
│ app/db.py        engine, sessions, WAL pragmas  │
├─────────────────────────────────────────────────┤
│ SQLite (or Postgres) · filesystem · FFmpeg      │
└─────────────────────────────────────────────────┘
```

Routers never contain business logic. If an endpoint needs a decision made, it
calls a service, and the service raises `PipelineError` with a message written
for the user. `_guard()` in each router turns that into a 422.

## The pipeline in detail

### 1. Ingest (`services/ingest.py`)

Accepts pasted text, TXT/MD/PDF/DOCX, and JPEG/PNG/WebP. Two things matter here:

**Content sniffing.** A client-supplied `Content-Type` is not trusted. Images are
re-parsed with Pillow's `verify()`, so a renamed executable cannot enter the
pipeline as a "panel".

**Rights declaration.** Every asset carries owner, licence basis, permission
reference, and date. `RightsDeclaration.status` returns `DECLARED` only when the
box is ticked **and** an owner and a concrete licence are present — ticking alone
is not enough.

There is deliberately no remote fetching anywhere in this module.

### 2. Analysis (`services/analysis.py`)

Extracts characters, locations, ordered events, conflict, twist, and cliffhanger.

`RulesAnalyzer` is the offline default: proper-noun frequency for characters, cue
phrases for beat classification (`ternyata` → twist, `melawan` → conflict). Every
extracted fact carries the index of the source asset it came from, which is what
lets the quality gate verify claims trace back to real material.

`unwrap_paragraphs()` repairs hard-wrapped text before sentence splitting.
Without it, a recap wrapped at 75 columns gets split mid-sentence and the events
come out as fragments.

`LLMAnalyzer` uses an OpenAI-compatible endpoint with a system prompt that
forbids invention and requires uncertainty to go into `low_confidence_notes`. It
falls back to rules on any error rather than failing the request.

### 3. Script (`services/script.py`)

Builds five beats against a timing budget:

| Beat | Share of target | 60s example |
|---|---|---|
| hook | 5% | 0–3s |
| setup | 15% | 3–12s |
| conflict | 47% | 12–40s |
| twist | 25% | 40–55s |
| CTA | 8% | 55–60s |

Two mechanisms matter:

**`_pack_to_budget()`** fills each beat up to its allowance. Trimming alone left
beats under-filled and the finished Short well short of target.

**`summarise_clause()`** compresses source sentences by stripping discourse
openers, subordinate tails, and function words. This is not cosmetic: without it
the generator is extractive, narration comes out ~60% verbatim, and the
transformative-use gate correctly blocks the render.

Locked sections survive regeneration, which is what makes the review loop usable.

### 4. Voice-over (`services/tts.py`)

One clip per beat. `EspeakProvider` normalises loudness with `loudnorm` so
concatenated segments do not jump in volume.

`estimate_word_timings()` distributes the measured clip duration across words
weighted by character length, with extra weight for trailing punctuation. That is
accurate enough for karaoke-style captions without a forced aligner.

Timings are stored **clip-relative**. `pipeline.spans_from_segments()` shifts them
onto the master timeline. Getting this wrong made every cue restart at zero.

### 5. Timeline (`services/timeline.py`)

`lay_out_audio()` places segments end to end with a 0.18s gap between beats.

`plan_scenes()` splits long beats into multiple scenes so the video keeps moving,
cycling images when there are fewer panels than slots. Each span absorbs the
silence that follows it — otherwise the video is shorter than the audio by the sum
of the gaps and FFmpeg's `-shortest` clips the last line.

`build_cues()` chunks narration on real word timings, checking the actual wrapped
line count rather than a character budget. A 56-character string can still need
three 28-character lines once words break unevenly.

### 6. Render (`services/render.py`)

```
prepare images (crop 9:16 at focal point, oversample 1.15x)
  → per-scene clips (zoompan motion + fades)
  → concat
  → burn ASS subtitles
  → mux narration (+ optional music at -18dB)
  → probe + checksum
```

Two performance traps are worth knowing about:

**`-t` must not be an input option.** With `-loop 1 -t 4`, FFmpeg feeds 120
looped stills into `zoompan`, which expands *each* into `d` frames — 12,000 frames
and 400s of video for a 4s scene. Use `-frames:v` on the output.

**Do not pre-scale for zoompan.** `crop_to_vertical` already oversamples to
1.15×. An extra `scale=2160:3840` pushes every frame to ~8 MP; on 2 vCPU that
took 258s per 4s scene versus ~4s after removal.

Work happens in a scratch directory that is wiped on start and removed on
success, so a failed run never leaves a partial file where publish could find it.

### 7. Quality gate (`services/quality.py`)

Errors block; warnings are overridable with a recorded reason and actor.

Blocking: undeclared or rejected rights, ≥50% verbatim narration, unapproved
script, missing audio/scenes/render, zero-length scenes, overlapping cues,
duration over the Shorts limit, wrong aspect ratio.

Warning: 25–49% similarity, >8 panels from one chapter, uncited beats, empty
scenes, captions too fast or overflowing, duration well off target.

### 8. Publish (`services/publish.py`)

Re-verifies everything rather than trusting earlier state:

1. Public visibility needs config opt-in **and** per-request confirmation.
2. A successful final render must exist.
3. The file must still match its render-time SHA-256.
4. Quality checks are re-run; a stale pass is not accepted.

Uploads are idempotent: a retry reuses the pending `Publication` row, so
retrying never re-renders.

`DryRunProvider` is the default. It writes a JSON receipt locally and returns
`available=False` for analytics rather than a row of zeros that would look like
real data.

## Data model

15 tables. The chain that matters:

```
User → Workspace → Project
                     ├── SourceAsset      (+ rights provenance)
                     ├── StoryAnalysis
                     ├── ScriptVersion    (+ approval)
                     │     └── AudioSegment
                     ├── TimelineScene
                     ├── SubtitleCue
                     ├── QualityCheck     (+ override reason)
                     ├── RenderJob
                     └── Publication
                           └── VideoStat
AuditLog (append-only, references anything)
```

`ScriptVersion` is versioned rather than mutated, so approval history survives.

### A SQLAlchemy pitfall worth documenting

`Project.scripts` is a lazy relationship. Once read in a session it stays cached,
so a script added later **in the same transaction** is invisible — which broke
`generate_draft` (voice-over could not see the script that had just been
created). The fix is `pipeline.latest_script_row()` / `current_script()`, which
query directly. Avoid relationship reads for anything written in the same unit of
work.

## Security

| Concern | Approach |
|---|---|
| Passwords | `hashlib.scrypt`, N=2^15, per-password salt, explicit `maxmem` (OpenSSL's 32 MiB default is exactly the requirement and fails without it) |
| Sessions | Signed cookie (itsdangerous), HttpOnly, SameSite=Lax, Secure in production |
| Session key | Persisted to `data/.secret_key`; regenerating per process would log everyone out on restart |
| OAuth tokens | Fernet-encrypted at rest, never returned by the API, erased on disconnect |
| Uploads | Size cap, extension + content sniffing, path-traversal-safe storage keys |
| Ownership | Every project route resolves through `owned_project`; foreign ids return 404, not 403 |
| Audit | Append-only log of approvals, overrides, renders, uploads |

Not implemented in v1.0: login rate limiting, CSRF tokens, TLS. Keep the app on
loopback or behind a reverse proxy.

## Extension points

- **Better TTS** — implement `TTSProvider` (`synthesize` + `available`).
- **Better writing** — set `MS_LLM_PROVIDER=openai_compatible`.
- **S3 storage** — reimplement `services/storage.py`; the interface already
  mirrors object storage (`storage_key`, `put_bytes`, `path_for`).
- **Real queue** — `pipeline.next_queued_job()` and `execute_render()` are already
  worker-shaped; `execute_render` refuses jobs that are not still `QUEUED`, so
  multiple workers cannot double-render.
- **Postgres** — set `MS_DATABASE_URL`; the SQLite pragmas are guarded by backend
  check.
