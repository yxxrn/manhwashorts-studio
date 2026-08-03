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
│   timeline  roi_detection  shot_director        │
│   camera_planner  render  quality  policy       │
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

`ByokAnalyzer` does the same through a user-supplied key (see BYOK below). Both
share `parse_llm_json`, which length-caps every field and validates every enum:
a model reply is untrusted input and a malformed one must not reach the database.

### Visual direction

```text
panels → visual scoring → roi_detection → shot_director → camera_planner → render
```

`visual_scoring` detects panel signals and focal points. `roi_detection` ranks
those regions. `shot_director` owns editorial decisions: ROI order, shot length,
ROI changes, panel switches, anticipation, narration timing, and camera intent.
`camera_planner` only validates the chosen camera curve and translates it into
renderer fields. It never chooses a panel, ROI, duration, cut, or lead/follow
relationship.

## BYOK: bring your own key

Three modules, each with one job:

| Module | Responsibility |
|---|---|
| `services/providers.py` | Talks to vendors. One adapter per API *shape*, not per vendor. |
| `services/credentials.py` | Encrypts, stores, and retrieves keys. No HTTP, no vendor knowledge. |
| `services/resolver.py` | Decides which provider a stage uses, and reports why. |

See [`TTS_OPTIONS.md`](TTS_OPTIONS.md) for current voice-provider research and the selection gate.

**Why adapters are keyed on shape.** Most vendors speak the OpenAI wire format,
so `OpenAICompatibleLLM` is parameterised by base URL and covers nine of them.
Anthropic (`x-api-key` + `/messages`) and Google (key as query param +
`generateContent`) differ enough to need their own classes. Adding another
OpenAI-compatible vendor is a table entry, not a class.

**Model discovery is the verification step.** `list_models` is the only method
needed to save a credential: a provider that returns a model list has accepted
the key. This avoids a separate "test key" code path that could drift from the
real one, and it means the UI offers exactly the models that key can reach rather
than a hardcoded guess that goes stale.

**Resolution order**, in one place so stages cannot disagree:

1. Verified BYOK credential with a model selected, for this workspace.
2. Environment configuration (`MS_LLM_*`, `MS_TTS_HTTP_*`).
3. Offline engine (rules, espeak-ng).

Rule 3 is why the app never hard-fails for want of a key, and it is covered by
tests so the v1.0 offline path cannot silently regress.

Every resolution returns a `Resolution` describing the choice and a human-readable
`reason`, surfaced by `GET /api/credentials/active` and in analysis notes. Silent
provider selection is how users end up surprised by a bill or by robotic audio.

**Failure policy differs by stage on purpose.** Analysis degrades to the offline
analyser and records why. Narration raises instead: the user chose to pay for a
specific voice, and quietly substituting espeak into a video they are about to
publish is the worse outcome.

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

Project default: English narration with an American English voice (`en-US`). Indonesian is an explicit project-level opt-in only.

One clip per beat. The default is offline espeak-ng; verified BYOK credentials
can select a stable cloud voice without changing the render pipeline.

`OpenAI Speech`, `ElevenLabs`, and custom OpenAI-compatible endpoints are
adapters, not project-specific engines. The selected provider, model, voice ID,
and settings are part of the render decision and must remain locked for a
consistent narrator.

Production voice selection uses BYOK (`docs/BYOK.md`) or a generic HTTP endpoint configured through `MS_TTS_HTTP_*`. A configured paid provider fails loudly rather than silently downgrading to espeak. Keep one provider/model/voice ID and fixed voice settings for every beat; see [`TTS_OPTIONS.md`](TTS_OPTIONS.md).

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

### 6b. Encoder selection (`services/encoders.py`)

Encoding is the only stage a GPU meaningfully accelerates, so the choice is
isolated in one module and `render.py` never branches on hardware. It asks for
three things — input flags, output flags, and a filter suffix — and passes them
through unchanged.

**Detection runs a real encode.** Every FFmpeg build advertises `h264_nvenc`
whether or not an NVIDIA card exists, so `ffmpeg -encoders` proves nothing. Each
backend is probed by encoding one frame to a temp file and checking the file is
non-empty. Two details were learned the hard way:

- `-f null -` is unusable for probing. The null muxer discards frames, so a broken
  hardware encoder can exit 0 having produced nothing.
- FFmpeg reports the root cause on its *first* line (`Cannot load libcuda.so.1`)
  then emits cascading thread and muxer noise. Truncating the log from the end
  throws away the only useful line.

**Resolution happens once per render, not per scene.** Probing spawns a
subprocess; doing it per clip would add one per scene. More importantly, a
mid-render switch could mix codecs across scene clips, and the concat stage uses
`-c copy`, which cannot join dissimilar streams.

**Fallback is loud.** An unavailable GPU falls back to `libx264` and records
`encoder_fell_back` plus the reason on the job. A render that silently takes 20x
longer than the user expected is its own kind of bug. A misspelled encoder *name*,
by contrast, raises — otherwise a typo would masquerade as working GPU support.

**VAAPI is the awkward one.** It encodes from GPU surfaces, so the CPU-side filter
chain must end with `format=nv12,hwupload` and `-pix_fmt` must be omitted or it
fights the upload. That is why `apply_filter_suffix` exists rather than each call
site appending flags itself. libass draws on CPU frames, so the upload has to come
*after* the subtitles filter.

Jobs store the *request* (`auto`, `nvenc`, …) rather than a resolved encoder,
because the worker may run on a different machine than the API. The probe has to
happen where the encoding happens.

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

## Deployment boundary

UpCloud is the execution host. The local checkout is an orchestrator and test source only. Database, source assets, TTS, FFmpeg, scratch files, and outputs belong under `/opt/manhwashorts` on UpCloud. Do not run production renders against local `data/`.

The local checkout currently contains historical generated data from earlier runs; this is not required by the runtime architecture and is scheduled for cleanup after a verified backup decision.

## Motion-comic implementation status

The ordered implementation plan, active contracts, verified sample, benchmark, and remaining work live in [`MOTION_COMIC.md`](MOTION_COMIC.md). Resume point: **Stage 8 — Documentation and release gate**.

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
