# Changelog

Notable changes per release. Dates are ISO 8601.

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

[1.5.0]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.5.0
[1.4.0]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.4.0
[1.3.1]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.3.1
[1.3.0]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.3.0
[1.2.0]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.2.0
[1.1.0]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.1.0
[1.0.0]: https://github.com/yxxrn/manhwashorts-studio/releases/tag/v1.0.0
