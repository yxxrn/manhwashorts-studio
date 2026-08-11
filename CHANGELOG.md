# Changelog

Notable changes per release. Dates are ISO 8601.

## Unreleased - Phase 2 editorial gates

- Completed the approved implementation plans for balloon-free color-agnostic
  framing and sharp_friend_v1 narrative identity. Voice provider choice,
  auditions, and audio generation remain deferred until explicit user selection.
- Corrected the plans so unknown balloon geometry persists for audit and blocks
  only reference readiness, while a dedicated ordered vision acquisition task
  supplies the versioned visual-evidence prompt, adapter validation, mocks, and
  snapshot before blank detection.

- Added deterministic speech-balloon, UI-overlay, and blank-dominance penalties
  without requiring OCR, with auditable chronology and source-family reasons.
- Enforced per-asset reuse caps, four-mode normal motion diversity, hard action
  cuts, bounded section transitions, uppercase punctuation-free one-word display
  cues, and media-end clamping while preserving spoken TTS punctuation.
- Final renders use the fixed 1080x1920/30fps H.264 High yuv420p path and
  normalize audio toward -14 LUFS with a -1.5 dBTP true-peak ceiling.
- Rights/source checks remain hard blockers; unlicensed music and SFX are not
  selected for render.
- Versioned the vision analyzer to v2 with exactly five evidence-linked
  narration roles, deterministic word bounds, grounded open-loop payoffs, and
  context-aware channel-CTA/repetition guards.
- Added evidence-gated script generation and explicit human approval: only the
  latest reconciled vision analysis can materialize five provider passages,
  with safe analysis status summaries and no public text/rules fallback.
- Added four-voice neural auditions using one deterministic 45-65 word,
  punctuation-preserving excerpt across all five editorial roles, isolated
  content-addressed WAVs, safe project-scoped download URLs, and no espeak/null
  fallback. No neural provider is configured on the current VPS, so no real
  audition samples were rendered.
- Wired the reference render surface through the selected project voice,
  compact one-word caption styling, bounded camera zoom, profile-aware output
  QC, and explicit H.264 High/yuv420p final validation; previews and legacy
  subtitle rendering remain unchanged without the profile.
- Fixed the script-approval UI to send the required explicit confirmation
  JSON body while preserving the backend human-approval gate.
## Unreleased — Complete motion-comic pipeline

- Set the project default to English narration with American English voice; Indonesian remains explicit opt-in.

- Persisted source-family metadata, dramatic word events, impact locks, and
  audio-driven anticipation cuts.
- Added deterministic local effects with low/medium/high intensity, per-effect
  disable flags, split-focus/panel-stack validation, and safe fallbacks.
- Added append-only QC snapshots, black-frame/drift blocking, QC history API/UI,
  worker lease recovery, failed-scratch cleanup, and render resource metrics.
- Added release verification coverage for timing, effects, FFmpeg playback,
  H.264/AAC output, and worker recovery.

## Unreleased — Shot Director

- Added a modular Shot Director between panel selection and FFmpeg rendering.
- Long narration beats now split into directed 1.25–3 second shots, with ROI-to-ROI
  camera interpolation, motion diversity, and short anticipation cuts for dramatic beats.
- Added semantic camera curves for dialogue, thinking, reveal, action/attack,
  explosion, and victory shots; the English text and American English voice defaults remain enforced.
- Split visual direction into `roi_detection` → `shot_director` → `camera_planner`.
- Shot Director now persists ROI labels, end focal points, camera intent, narration
  lead/follow timing, and camera curves; Camera Planner only executes curves.

## [1.7.0] — 2026-08-02

- Added 60–90 second production duration contract; default target is 75s.
- Added `editorial_visual_planner` for Director → ROI → micro-shot planning and
  analysis overlays.
- Added phrase-level caption constraints and post-render editorial QC JSON,
  shot list, subtitle list, panel mapping, and source rights report.
- Added freeze detection, third-party watermark source gate, and test-only
  `NOT_FOR_PUBLICATION` handling.

## [1.6.5] — 2026-08-02

- Added 100–300 ms Director micro-offsets before reveal/action beats.
- Added exact timing locks for impact/explosion beats when word timings exist.
- Added a human-editor audit for repeated ROI, repeated camera curves, and long
  holds; issues are recorded in the audit log before rendering.
- Added regression coverage for anticipation, exact impact timing, and boredom
  checks.

## [1.6.4] — 2026-08-02

- Added the Director Layer before Shot Sequencer and Camera Planner.
- Added story-beat timing: visual-before, visual-sync, and visual-after.
- Added emotion-driven beat intents for approach, suspense, dialogue, reveal,
  attack, impact, explosion, and victory.
- Added Director regression tests and real-case validation: 18 shots, 12 source
  panels, 4 emotional intents, and 8 camera curves.

## [1.6.3] — 2026-08-02

- Added the Shot Sequencer: one selected panel can now produce multiple
  cinematic ROI shots before the director switches panels.
- Added semantic ROI labels for face, eyes, speech bubble, hands, weapon,
  monster, magic effect, and detail; duplicate labels are suppressed.
- Preserved narration-aware camera intent, ROI travel, shot pacing, and panel
  transition behavior.
- Added real-case validation: 13 shots from the existing 5-beat project, 6
  source panels, 13 distinct ROI labels/camera targets.

## [1.6.2] — 2026-08-02

- Made new projects default to English narration and The Explainer no. 4.
- Locked all English renders to `the-explainer-american`; Indonesian remains an
  explicit `language: "id"` opt-in.
- Set the default narration speed to `0.90` and corrected the shared-reference
  OmniVoice path to use the configured English language.

## [1.6.1] — 2026-08-01

- Added optional OpenCV and Tesseract-backed face/OCR signals to the visual
  scorer; missing OCR remains non-blocking.
- Added coarse perceptual signatures and stronger fresh-panel penalties so one
  high-scoring image cannot dominate a whole sequence.
- Fixed timeline gap absorption in content-aware planning.
- Real-panel validation selected 7 content-aware shots with semantic camera
  plans and focal points before render.

## [1.6.0] — 2026-08-01

- Added content-aware visual panel scoring with face, expression, action,
  weapon, monster, effects, motion-line, impact, close-up, composition, density,
  empty/scenery/transition penalties, OCR, and focal-point extraction.
- Added semantic narration matching, nearby higher-interest panel replacement,
  repetition suppression, and configurable score weights.
- Added semantic camera planning: dialogue zoom, thinking pan, reveal push,
  action punch zoom, and explosion shake zoom.
- Replaced chronological-only timeline assignment with scored panel planning.
- Added `docs/VISUAL_SELECTION.md` and regression coverage.

## [1.5.10] — 2026-08-01

- Slowed default narrator generation to `0.90` for more comfortable delivery.
- Installed and selected Barber Chop for subtitles; increased subtitle size.
- Fixed the actual animation bug: still-image input now enters FFmpeg at the
  output frame rate instead of 1 fps, so crop motion updates every frame.
- Kept one-active-word karaoke: uppercase, yellow, only the spoken word.

## [1.5.9] — 2026-08-01

- Restored the proven no. 4 shared-reference voice path after the continuous
  low-step experiment degraded timbre.
- Karaoke now displays **only the currently spoken word**, uppercase, yellow,
  with no previously spoken words left on screen.
- Increased crop motion visibility while retaining integer-pixel anti-shimmer.
- Added final gates for speech transcription, exactly-one-word karaoke, frame
  variation, decode, and upload checksum.

## [1.5.8] — 2026-08-01

- Subtitle text is now progressively revealed word-by-word in uppercase.
- Active spoken word remains yellow; already spoken words remain white.
- Subtitle size increased for phone readability.
- Reworked crop motion visibility to 18% with integer-pixel movement; frame
  regression confirms animation without shimmer.

## [1.5.7] — 2026-08-01

- Continuous OmniVoice session now generates the complete narration once,
  then splits at measured pause markers; this prevents mid-video timbre drift.
- Replaced `zoompan` with deterministic integer-pixel crop animation; motion
  remains active while frame repeat ratio stays below the visual regression cap.
- Final gate checks audio transcription, frame variation, subtitle timings,
  decode, and upload checksum before delivery.

## [1.5.6] — 2026-08-01

- Fixed mid-video timbre drift: HTTP TTS now creates one shared reference
  clip, then generates every section against that same reference.
- Replaced jitter-prone `zoompan` motion with deterministic integer-pixel crop
  motion at 30 fps; animation stays active without frame micro-jitter.
- Added audio, frame-variation, decode, subtitle, and upload verification to
  the final render check.

## [1.5.5] — 2026-08-01

- Locked OmniVoice seed, quality steps, and guidance across every section so
  one narrator does not change timbre mid-video.
- Disabled zoom/pan motion in production by default; still webtoon panels now
  use stable pixel crops plus fades, eliminating zoompan micro-jitter.
- Kept word-level karaoke timing derived from the measured audio clips.

## [1.5.4] — 2026-08-01

- Selected no. 4 English narrator delivery as the default OmniVoice instruct
  preset: male, young adult, moderate pitch, American accent.
- Added safe HTTP-TTS audio mastering presets; production uses `expressive`.
- Mastering fails loudly instead of shipping an invalid/empty clip.
- Regenerated timeline from measured audio before subtitle burn, preserving
  word-level karaoke highlighting and smoother panel motion.

## [1.5.3] — 2026-08-01

- **Smoother panel motion** — still images now enter FFmpeg at one frame per
  second, zoom/pan coordinates are quantized to even pixels, and the motion
  filter remains responsible for the full 30 fps output. This removes source
  frame duplication and subpixel chroma micro-jitter while preserving movement.
- Added all 24 official English OmniVoice featured archetypes to the sample
  selection workflow: gender, age, pitch, accent/style, and stable instruct.
- Rendered a 128.54s voice montage for direct listening.

Motion regression render passed: 1080x1920, 29.6s, H.264 + AAC, checksum
`7b66ccb0587597a4acb5e6153ee5967747c0b63b4a7aeb1c6c9270ba6f090696`.

## [1.5.2] — 2026-08-01

- Restored the requested **Indonesian default** for new projects and voice IDs.
- Language remains an explicit `en`/`id` choice; no image-language detection.
- OmniVoice Indonesian HTTP smoke test passed: HTTP 200, valid 24 kHz WAV.
- English remains available by creating a project with `language: "en"`.

## [1.5.1] — 2026-08-01

- Default project language and voice changed to **English** for the overseas
  target. Explicit `en`/`id` selection only; no image-language autodetection.
- OmniVoice HTTP TTS now receives the project language from the explicit voice
  choice instead of a static server environment value.
- Rebuilt the English validation timeline after script edits, preventing stale
  subtitle/audio cues from surviving a script update.

## [1.5.0] — 2026-08-01

### Added

- **OmniVoice local TTS on UpCloud** — isolated `/opt/OmniVoice-Studio` venv,
  CPU inference, systemd service on loopback port 3900, OpenAI-compatible adapter
  in ManhwaShorts. Deep self-check passed; real English speech endpoint returned
  HTTP 200 and a valid 24 kHz WAV. CPU quality is better than espeak but slow:
  about 20–51 seconds for 2.2–2.3 seconds of audio on this 12-vCPU host.
- **English TTS adapter mode** — `tts_http_protocol=openai`, model, language,
  voice, response format, and instruct settings. No network leaves UpCloud for
  synthesis.
- **Anton subtitles** — configurable `subtitle_font_name`, with Anton installed
  on UpCloud for a relaxed but legible Shorts style.
- **Karaoke word highlighting** — the active spoken word turns yellow while the
  full caption remains visible; timing derives from measured audio spans without
  a new forced-aligner dependency.
- **More varied panel motion** — `push_up`, `push_down`, and `pan_diagonal`, in
  addition to Ken Burns and horizontal pans.
- Registration toggle test and ASS karaoke/motion regression tests.

### Changed

- Default project language remains **Indonesian** (`id`). English was used only
  for the validation render requested for overseas-target testing.
- UpCloud production `.env` now uses local OmniVoice, Anton, and registration
  closed. The local machine remains orchestration-only.

### Validation

Real material: 4 webtoon pages → 12 auto-sliced panels → 8 scenes → English
OmniVoice voice-over → Anton karaoke captions → varied motion → final MP4.

```
38.933s · 1080x1920 · H.264 + AAC · 11.0 MB
render: 16s after TTS
sha256: 5dc49f31c5c716dc093c0f024f8ba45eafac7e48d20544279144e19513e73319
```

Verified download from Uguu: same size and SHA-256.

## [1.4.0] — 2026-08-01

Webtoon pages are one long vertical strip. This release stops throwing most of
them away, and fixes a persistence bug found while proving it on real pages.

### Added

- **`app/services/strips.py`** — slices a tall page into consecutive 9:16 pieces
  instead of cropping it once. Each cut is nudged to the most gutter-like row
  nearby (flat *and* near-white/near-black), so cuts land between panels rather
  than through faces and speech balloons. Pillow only, no new dependencies.
- **`ingest_image_parts` / `ingest_upload_parts`** — one uploaded page can now
  become several assets, numbered `_p01`, `_p02`, … so lexical order matches
  reading order. Slicing never bypasses content verification, and any slicing
  failure falls back to keeping the original image.
- **Settings**: `strip_slice_enabled` (kill switch), `strip_slice_min_ratio`
  (2.5, clear of both a normal portrait panel and 9:16 itself),
  `strip_slice_max_parts` (12).

### Fixed

- **The database commit landed after the response was sent.** `get_db` committed
  in its post-`yield` teardown, and since FastAPI 0.106 that runs *after* the
  client already has its reply. So `POST /api/auth/register` returned 201 and the
  caller's very next request opened a fresh session that could not see the
  uncommitted row — `401 Account not found`. Measured against a live server:
  **12/12 failures with no delay, 0/6 with a 1.5s delay**, and a separate
  read-only connection confirmed the row was absent at the exact moment the 201
  arrived. Browsers never noticed (a human is slower than a millisecond) and the
  test suite never noticed (`TestClient` completes teardown before returning), so
  only a fast programmatic client — the AI-agent path this project targets —
  could hit it. Now `app/routing.py::CommitRoute` commits after the handler and
  **before** replying, for every writing route. A handler that raised still rolls
  back.
- **Piece count no longer assumes rounding down is safer.** Flooring keeps every
  piece at least a frame tall, which sounds right but crops *height* — on a
  720x3667 page that discards 30% of the story, while rounding up costs ~5% of
  the side margins. Both candidates are now scored and the better one wins.
- **`getdata()` deprecation** — replaced with `tobytes()`, which works on every
  Pillow version (`getdata()` is removed in Pillow 14 and its replacement does
  not exist on older ones). Slicing output verified byte-identical.

### Measured on real webtoon pages

Four pages at 1:4.60–1:6.07 became 12 panels, and retention of page content rose
from **29–39% to 86–95%**:

```
720x4372  1:6.07  ->  3 pieces   29.3% -> 87.8%
720x3667  1:5.09  ->  3 pieces   34.9% -> 94.6%
720x3309  1:4.60  ->  3 pieces   38.7% -> 86.2%
720x3642  1:5.06  ->  3 pieces   35.1% -> 94.8%
```

Rendered end to end over REST with no UI: 45.1s video, 1080x1920, 8 scenes.

### Tests

248 passing (was 226). 22 new: 16 covering slice geometry, gutter snapping,
retention, the part-count chooser, the kill switch and ingest/upload integration;
6 covering commit visibility, including a structural check that every writing
route uses `CommitRoute` so a future router cannot silently reintroduce the bug.

## [1.3.1] — 2026-08-01

Operational release: remote access, automatic disk cleanup, and two fixes on the
path an AI agent uses to drive the app.

### Added

- **`app/services/cleanup.py`** — age-based cleanup for `data/tmp` and
  reference-checked cleanup for `data/output`, plus a `max_data_gb` soft limit
  that triggers a more aggressive pass. Stdlib only; SQLAlchemy is imported
  lazily so the module stays cheap enough to run from the lifespan hook.
- **Cleanup runs on startup** and is exposed as a CLI:
  `python -m app.cleanup [--usage|--dry-run|--force]`.
- **`disk_usage` in `GET /api/health`** so growth is visible without a separate
  admin endpoint, with a `DiskUsageOut` schema to match.
- **`docs/AGENT.md`** — how to drive the whole pipeline over REST, including the
  two gotchas below and the guardrails that deliberately stay in an agent's way.
- **Cloudflare tunnel** wiring so the UI is reachable from a browser.

### Fixed

- **Secure cookies locked out local API clients.** Setting
  `MS_ENVIRONMENT=production` marked every session cookie `Secure`, and a client
  on `http://127.0.0.1:8000` will not send a `Secure` cookie back — so login
  returned `200` and the very next call returned `401`. This broke exactly the
  agent-driven path the project is built for. `Secure` is now decided per request
  from `X-Forwarded-Proto` (which Cloudflare sets) rather than from a global
  setting, so the browser over HTTPS and a loopback agent over HTTP both work at
  once, each with the correct flag.
- **Session cookies were not marked `Secure` at all** before that, despite being
  served over HTTPS through the tunnel.
- **Cleanup ignored orphaned scratch.** `cleanup_tmp` only looked at age, so
  `data/tmp/<project_id>/` left behind by a deleted project was kept until the
  retention window expired. In practice 139 of 140 directories were orphans —
  **99% of `data/tmp`, 1.2 GB** — all zero days old and therefore untouched.
  Orphans are now deleted immediately, since a project that no longer exists can
  never use them. Directories for live projects still wait out the window,
  because a render may be in flight. If the database cannot be read, orphan
  deletion is skipped rather than guessed.
- **`GET /api/health` silently lost its schema.** Adding `disk_usage` dropped
  `response_model=HealthOut`, so the endpoint was no longer validated and an
  agent parsing it had no guaranteed shape. Restored, with `disk_usage` declared.

### Changed

- `.gitignore` now excludes `*.deb` so downloaded installers cannot be committed.

### Tests

226 passing (was 217). 9 new agent-contract tests: session survival over plain
HTTP, `Secure` flag driven by `X-Forwarded-Proto` (including a comma-separated
proxy chain), the health response model, a full create → upload → draft →
approve → quality pass over REST, and OpenAPI discoverability.

### Measured on the 2 vCPU / 3.6 GB box

```
idle     143 MB   (uvicorn 97 MB + cloudflared 39 MB)
render   602 MB peak, ffmpeg 389 MB, load 1.49/2
         102s for a 49.5s video (~2x realtime)
agent    draft 1.7s · render 45s · whole REST flow under a minute
```

Encoding is CPU-bound, so more RAM would not help. Memory returns to idle after
a render, with no leak.

## [1.3.0] — 2026-08-01

### Added — neobrutalism UI, pastel palette

Complete visual rebuild plus twelve features that existed in the API but had no
way to reach them from the interface.

- **Neobrutalism styling**: thick black borders, hard offset shadows with no blur
  radius, flat pastel fills, buttons that visibly sink when pressed.
- **Pastel palette on a single ink colour.** All text is `#1a1a2e`; pastels are
  only ever backgrounds behind it. Every pair clears **WCAG 2.1 AAA (7:1)**, not
  just AA — the lowest is coral at 8.4:1. Ratios are computed from the CSS
  variables in a test, so a future colour change cannot quietly break contrast.
- **Step navigation**: eight chips jump to any stage, moving focus as well as
  scroll so keyboard and screen-reader users follow.
- **Colour-coded stages** so the eight-step flow is scannable while scrolling.
- **`docs/UI.md`** covering the design language, measured contrast table,
  performance decisions, and the invariants to keep when extending it.

### Added — twelve features the UI could not previously reach

An audit compared every API route against the frontend. These were unreachable:

| Feature | Why it matters |
|---|---|
| Analysis view + editor (FR-03) | The script is generated from this data, so correcting a misdetected twist here is the cheapest way to improve the video |
| Script version history | See what changed between takes |
| Render history | Which encoder ran, which attempt failed, why |
| Publish readiness check | Know before uploading, not after |
| Publication history + retry | A failed upload was previously invisible |
| Analytics sync | Reports honestly when no data exists |
| YouTube channel list + disconnect | Connecting worked; reviewing did not |
| Encoder capability table | Shows *why* a GPU is unavailable |
| Project duplicate | Reuse settings for the next chapter |
| Project delete | Was API-only |
| Project metadata display | Confirm the right chapter at a glance |
| Source text character counter | The 40-character minimum was invisible |

A test now asserts every pipeline stage is reachable from the UI, so a new
endpoint cannot ship without a way to use it.

### Added — UX guards for slow hardware

- **Double-submit protection.** On a slow machine a user who sees no feedback
  clicks again, queueing a second render. Every async action routes through
  `withBusy()`, which disables the control, shows a spinner, and refuses re-entry.
- **Nothing waits silently.** Buttons show a spinner and a verb
  (“Menganalisa…”, “Menyimpan…”) while working.
- **Destructive actions confirm and say what is lost.**
- **Empty states instruct** rather than showing a blank list.
- **Lazy panels**: encoder probing and channel listing run on first open of the
  settings section, not at boot.
- **Parallel fetches**: opening a project was eight sequential round trips before
  the UI settled; the independent ones now run concurrently.

### Changed

- Top bar shows the active encoder (CPU/GPU) alongside health, so it is visible
  without scrolling.
- BYOK moved into a unified **Pengaturan** section together with YouTube channels
  and the encoder table.
- Script step now shows version, generator, word count, and estimated duration.

### Fixed

Three real bugs, all found by checking the UI against the actual schemas rather
than assuming:

- **`script.similarity_score` never existed.** The script panel read it and
  always rendered “0%”. The ratio is computed by the policy gate and reported as
  a quality check, so it is shown in step 6 instead of invented in step 4.
- **Publish readiness read the wrong key.** The endpoint returns `reason`
  (singular) plus a `checks` summary; the UI read `reasons`, so the blocking
  explanation never appeared.
- **A stale element id would have crashed the handler chain.** Renaming the BYOK
  toggle to `settings-toggle` left `app.js` calling `$('byok-toggle')`, which
  returns `null` — that throws at load and kills every listener registered after
  it. Now enforced by a test that every `$('id')` exists in the template.

### Accessibility

- Every control has a label, wrapping label, or `aria-label` — enforced by test.
- Sections are `aria-labelledby` their heading.
- 3px offset focus ring everywhere; `outline: none` appears nowhere.
- `prefers-reduced-motion` disables all animation.
- Colour is never the only signal; severity is always spelled out in text.

Full WCAG conformance still needs manual testing with real assistive technology
and expert review. What is verified is the measurable part.

### Performance

- No `backdrop-filter`, no `filter: blur()`, no gradients on large surfaces, no
  web fonts, no CDN requests, no framework runtime.
- Transitions touch only `transform` and `box-shadow`, never `width`, `height`,
  `top`, or `all`, which force layout every frame.
- `content-visibility: auto` on long lists so off-screen rows skip layout and
  paint.
- Tests enforce the first two, so the jank cannot creep back.

### Tests

217 passing (was 177). 40 new UI contract tests: element wiring, API field
existence, XSS guards, measured contrast ratios, labelling, focus visibility,
live regions, feature coverage, and the performance rules above. They read the
real CSS and JS files, so they fail if the contract drifts.

## [1.2.0] — 2026-08-01

### Added — CPU/GPU encoder choice

Pick the video encoder per render, or let the app detect one. Encoding is the
slowest stage and the only one a GPU meaningfully accelerates.

- **Five backends**: `libx264` (CPU), NVENC (NVIDIA), Quick Sync (Intel), VAAPI
  (AMD/Intel on Linux), VideoToolbox (Apple). Plus `auto`, which prefers a
  working GPU.
- **New `app/services/encoders.py`** owning encoder selection and flag
  construction, so `render.py` never has to know whether work lands on a CPU or
  a GPU.
- **`GET /api/encoders`** reports every backend, whether it works here, and *why
  not* when it does not — usually a driver or permissions problem rather than
  missing hardware.
- **Encoder picker in the UI** (6. Render). Unavailable backends stay visible but
  disabled, with the reason in a tooltip; hiding them would leave users guessing.
- **`/api/health`** now reports `video_encoder` and `gpu_encoding`.
- **`MS_VIDEO_ENCODER`** sets a server-wide default.
- **Per-job persistence**: `encoder_requested` vs `encoder` (what actually ran),
  plus `encoder_hardware`, `encoder_fell_back`, `encoder_reason`. A retry reuses
  the original choice so it reproduces the same run.
- **`docs/GPU.md`** covering requirements per vendor, Docker device passthrough,
  realistic speedups, and troubleshooting.
- **Migration `92dae0b434f1`**, verified on a database that already had
  `render_jobs` rows. Historic jobs are backfilled as CPU renders, which is what
  they were.

### Changed

- Preview renders now use per-backend fast presets rather than one CPU setting,
  so a preview is cheap on every encoder.
- The encoder is resolved **once per render**, not per scene. Probing per clip
  would spawn a subprocess for every scene, and a mid-render switch could mix
  codecs in the concat stream, which `-c copy` cannot join.
- Render jobs store the encoder request rather than a resolved encoder, because
  the worker may run on a different machine than the API — the GPU probe has to
  happen where encoding happens.

### Behaviour worth knowing

- **An unavailable GPU never fails a render.** It falls back to CPU, records
  `encoder_fell_back` with the reason, warns in the UI, and keeps it in the audit
  log. A slow render beats a failed one, but a silent 20x slowdown is its own bug.
- **A misspelled encoder is rejected** with `422` instead of quietly becoming a
  CPU render, which would leave you believing you had GPU encoding.
- **Detection proves the encoder works.** Every FFmpeg build advertises
  `h264_nvenc` whether or not a GPU exists, so the app encodes one real frame to
  a temp file and checks it is non-empty. Results are cached per process.
- **VAAPI needs a render node**, not just `/dev/dri/card0` — that exists on VMs
  with a virtual VGA adapter which cannot encode at all.
- **The GPU only accelerates encoding.** Image prep (Pillow), Ken Burns
  (`zoompan`), and subtitles (libass) stay on the CPU and become the new
  bottleneck on a fast GPU.

### Fixed

- **Encoder probe diagnostics were useless.** Two causes: `-f null -` let a
  broken hardware encoder exit 0 while writing nothing, masking the real error;
  and the error log was truncated from the *end*, discarding FFmpeg's root-cause
  first line (`Cannot load libcuda.so.1`) in favour of cascading thread noise.
  Probes now write a real file and keep the head of the log.
- **The generated migration would have failed on any populated database.**
  Autogenerate emitted five `NOT NULL` columns with no `server_default`, which
  existing `render_jobs` rows cannot satisfy. Added defaults and verified the
  upgrade against a database containing a row.

### Security

- The encoder name reaches an FFmpeg command line, so it is constrained by regex
  at the schema (`^(auto|cpu|nvenc|qsv|vaapi|videotoolbox)$`) and re-validated in
  `enqueue_render`. Values like `cpu; touch /tmp/x` are rejected, and a test
  confirms nothing reaches a shell.

### Tests

177 passing (was 134). 43 new encoder tests covering the catalogue, probing,
fallback, flag construction per backend, the HTTP surface, injection rejection,
and persistence. They pass with or without a GPU: tests that need a specific
backend construct the selection directly rather than requiring the hardware.

## [1.1.0] — 2026-08-01

### Added — BYOK (bring your own key)

Use your own API keys for the AI stages instead of relying on the server's
environment variables. The model list is fetched **from your key**, so the
choices offered are exactly what that key can reach.

- **New `provider_credentials` table** storing one credential per
  (workspace, capability, provider). Keys are Fernet-encrypted before they touch
  the database; the column holds a `gAAAAA…` token, never the key.
- **Two capabilities**: `llm` (chapter analysis, highlights, beats, script
  rewriting) and `tts` (voice narration). Split by capability rather than vendor,
  because one vendor can serve both roles and one role has many vendors.
- **13 providers.** LLM: OpenAI, Anthropic, Google AI Studio, OpenRouter, Groq,
  DeepSeek, Mistral, Together AI, xAI, and any OpenAI-compatible endpoint
  (Ollama, LM Studio, vLLM, LiteLLM). TTS: OpenAI Speech, ElevenLabs, and any
  `/audio/speech` endpoint.
- **Model discovery + verification in one step.** Saving a key calls the
  provider's model endpoint first. A key that cannot list models is rejected with
  `400` instead of being stored to fail later mid-render.
- **10 new endpoints** under `/api/credentials` — catalogue, test-without-saving,
  save, list, active-resolution, refresh, model select, set default, delete.
- **New settings panel in the UI** with provider picker, key field, endpoint
  override, a "test & fetch models" button, and per-credential model switching.
- **`GET /api/credentials/active`** reports which provider each stage will really
  use and why, so you are never guessing whether a render hits your paid key or
  the offline engine.
- **Alembic migration `f139cbb1f257`**, purely additive. Verified on a populated
  v1.0 database: existing rows survive, and downgrade removes only the new table.
- **`docs/BYOK.md`** covering setup, self-hosted endpoints, the security model,
  failure behaviour, cost control, and troubleshooting.
- **`tests/mock_provider.py`** — a local stand-in speaking the OpenAI wire
  format, so the BYOK suite runs with no network, no real key, and no spend.

### Changed

- Provider selection now resolves through `app/services/resolver.py` in a fixed
  order: **verified BYOK credential → environment config → offline engine**. The
  offline path is unchanged and still covered by tests, so an install with no
  keys behaves exactly as it did in v1.0.
- `run_analysis` and `generate_voiceover` record the provider source, vendor, and
  model in the audit log. The key itself is never recorded.
- Analysis results carry a note stating which engine produced them, visible in
  the UI, so a fallback is never silent.
- `analysis.py`: response parsing extracted into `parse_llm_json`, shared by the
  env-configured and BYOK analysers. It now tolerates ` ```json ` fences that
  several models emit despite being told not to, and validates every field.

### Fixed

- **Audit rows written during a credential operation could look missing.** The
  session runs with `autoflush=False`, so an added-but-unflushed `AuditLog` row
  was invisible to a later query in the same transaction. Credential auditing now
  flushes explicitly.

### Security

- Stored keys are never returned by any endpoint. Responses expose `key_hint`
  (last four characters) only, enough to tell two keys apart.
- Provider error text is scrubbed before it is surfaced, in case a vendor echoes
  the submitted key back in an error body.
- User-supplied endpoints are restricted to `http`/`https`, so `file://` and
  similar cannot be dialled by the server.
- Every credential route is workspace-scoped; another account gets a `400`, never
  data.
- Deleting a credential removes the row and its ciphertext rather than flagging it
  inactive.
- A test opens the SQLite file directly and asserts the plaintext key is not
  present anywhere in it.

**Known limitation, stated plainly:** anyone who can read both
`data/manhwashorts.db` and `data/.fernet_key` can decrypt the stored keys, and
both live in `data/` by default. This keeps a single-user local install simple; it
is not a secrets manager. See `docs/BYOK.md` for mitigations.

### Behaviour worth knowing

- **Analysis** degrades to the offline analyser if a provider call fails, and says
  so in the notes. A failed API call costs a weaker analysis, not a dead pipeline.
- **Narration** raises instead of degrading. You chose to pay for a specific
  voice; quietly substituting robotic espeak audio into a video you are about to
  publish would be the worse outcome.
- **Models are never substituted.** Requesting a model the key does not offer is
  an error, not a silent swap to something else that also bills you.
- A credential with no model selected counts as not configured, so the pipeline
  uses the offline engine rather than guessing.

### Tests

134 passing (was 94). 40 new BYOK tests covering adapters, storage, resolution
order, generation through a user key, the HTTP surface, and the security claims
above.

---

## [1.0.0] — 2026-07-31

First working release. Generates YouTube Shorts recapping manhwa chapters from
material you have the right to use.

### Added

- **Pipeline**: ingest → analysis → script → voice-over → timeline → subtitles →
  quality gate → render → publish.
- **Rendering**: 1080×1920 H.264 + AAC with Ken Burns motion and burned-in
  captions, produced by FFmpeg. Video duration tracks narration exactly, because
  audio is treated as the clock rather than the other way round.
- **Rights gate**: assets need an owner and a licence basis, not just a ticked
  box. Narration that is ≥50% verbatim from the source is refused.
- **Human-in-the-loop publishing**: public upload needs config opt-in *and*
  per-request confirmation, and the rendered file is checksummed again
  immediately before upload.
- **Quality gate** with blocking errors and overridable warnings; an override
  requires a recorded reason and actor.
- **Offline by default**: espeak-ng narration, rule-based summarising, SQLite,
  filesystem storage, and a dry-run YouTube provider that writes a local receipt
  instead of uploading. LLM, HTTP TTS, and real OAuth are opt-in.
- Web UI with no build step, standalone render worker, Alembic baseline, seed
  script, and full documentation.

### Notable fixes during development

- `ffmpeg -t` used as an **input** option before `zoompan` multiplied output
  length roughly 100×: a 4-second scene rendered 400 seconds of video. Switched to
  `-frames:v`; per-scene render went from 258s to 4.1s.
- Pre-scaling before `zoompan` pushed frames to ~8 MP for no benefit.
- Word timings are clip-relative and must be shifted onto the master timeline,
  otherwise every subtitle cue restarted at zero and overlapped.
- Scenes must absorb inter-beat silence or `-shortest` clips the final line.
- The rules script generator was extractive (60% verbatim) and its own policy gate
  correctly refused to render it. The generator now summarises.
- The session signing key was regenerated per process, logging everyone out on
  restart. It is now persisted to `data/.secret_key`.
- Lazy SQLAlchemy relationships are cached per session, so rows written earlier in
  the same transaction were invisible to later pipeline stages.

[1.7.0]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.7.0
[1.6.5]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.6.5
[1.6.4]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.6.4
[1.6.3]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.6.3
[1.6.2]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.6.2
[1.6.1]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.6.1
[1.6.0]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.6.0
[1.5.10]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.5.10
[1.5.9]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.5.9
[1.5.8]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.5.8
[1.5.7]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.5.7
[1.5.6]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.5.6
[1.5.5]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.5.5
[1.5.4]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.5.4
[1.5.3]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.5.3
[1.5.2]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.5.2
[1.5.1]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.5.1
[1.5.0]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.5.0
[1.4.0]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.4.0
[1.3.1]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.3.1
[1.3.0]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.3.0
[1.2.0]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.2.0
[1.1.0]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.1.0
[1.0.0]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.0.0
