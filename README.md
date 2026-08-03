# ManhwaShorts Studio

Auto-generate YouTube Shorts that recap manhwa chapters. Give it your recap text
and panels you have the right to use; it produces a narrated, subtitled 9:16 MP4
ready for review and upload.

**Version 1.0** — runs locally end to end, no cloud services required.

```
material → analysis → script → voice-over → timeline → subtitles
         → quality gate → render → approve → upload
```

Nothing publishes without your explicit approval. That is the point of the
design, not a limitation.

---

## What it actually does

| Stage | What happens | Editable |
|---|---|---|
| Ingest | Text, PDF, DOCX, MD, or images, each with a rights declaration | yes |
| Analysis | Extracts characters, events, conflict, twist, cliffhanger | yes |
| Script | Five-beat structure (hook, setup, conflict, twist, CTA) with 3 hook variants | yes |
| Voice-over | Per-section TTS with word-level timings | yes, per section |
| Timeline | Scenes derived from audio, Ken Burns / pan effects, 9:16 crop | yes, per scene |
| Subtitles | Karaoke-timed cues inside the Shorts safe area, SRT export | yes, per cue |
| Quality | Blocking errors and overridable warnings | overrides recorded |
| Render | FFmpeg → H.264 + AAC, 1080×1920, burned-in captions | retryable |
| Publish | YouTube upload, private by default | double-gated |

## Requirements

- Python 3.11+
- FFmpeg with `zoompan` and `libass` (Ubuntu: `apt install ffmpeg`)
- espeak-ng for local TTS (`apt install espeak-ng`)
- DejaVu fonts for subtitles (`apt install fonts-dejavu-core`)
- ~500 MB disk for a working project

No GPU, no API keys, no paid services needed for the default configuration.

## Quick start

```bash
# system dependencies (Debian/Ubuntu)
sudo apt-get install -y ffmpeg espeak-ng fonts-dejavu-core

# project
git clone git@github.com:yxxrn/manhwashorts-studio.git
cd manhwashorts-studio
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

# verify the environment can render
.venv/bin/python -c "from app.services.render import check_environment; print(check_environment() or 'environment OK')"

# run
.venv/bin/python -m uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>. Register an account (local only, stored in your
own SQLite file), then work down the numbered steps in the UI.

### Try it with the demo project

```bash
.venv/bin/python scripts/make_fixtures.py          # synthetic test panels
.venv/bin/python scripts/seed_demo.py --render     # seed + full render
```

This creates `demo@manhwashorts.local` / `demo12345` and renders a real MP4 to
`data/output/<project-id>/final.mp4`. Everything it uses is generated locally —
no third-party artwork.

## Configuration

Copy `.env.example` to `.env`. All values are optional; the defaults run offline.

**Project rule:** new renders default to English text and American English voice-over (`en-US`; `the-explainer-american`). Indonesian requires explicit project opt-in.

The settings that change behaviour most:

| Variable | Default | Effect |
|---|---|---|
| `MS_TTS_PROVIDER` | `espeak` | `espeak` (offline), `http` (external API), `null` (silence) |
| `MS_LLM_PROVIDER` | `rules` | `rules` (offline heuristics) or `openai_compatible` |
| `MS_YOUTUBE_ENABLED` | `false` | `false` keeps uploads in dry-run mode |
| `MS_REQUIRE_RIGHTS_DECLARATION` | `true` | The main copyright safeguard |
| `MS_ALLOW_PUBLIC_PUBLISH` | `false` | Public uploads need this **and** per-request confirmation |

### Better narration quality

espeak-ng is intelligible but robotic. For publishable audio, point
`MS_TTS_PROVIDER=http` at a real TTS service, or record your own voice-over and
upload it per section.

Likewise, `MS_LLM_PROVIDER=rules` writes competent but plain summaries. An
LLM endpoint produces noticeably better hooks and paraphrasing:

```bash
MS_LLM_PROVIDER=openai_compatible
MS_LLM_BASE_URL=https://api.openai.com/v1
MS_LLM_API_KEY=sk-...
```

## Copyright, honestly

This tool helps you **record** permissions and keep recaps transformative. It
does not grant you any rights, and it cannot tell you whether your use qualifies
as fair use or fair dealing — that depends on your jurisdiction, the amount you
use, and how transformative your commentary is. When in doubt, get permission or
consult a lawyer.

What the app enforces:

- Every asset needs an owner and a licence basis before publication. Ticking a
  box alone is not enough.
- Narration that is ≥50% verbatim from your source is **blocked**; ≥25% raises a
  warning.
- More than 8 panels from one chapter warns that the video may read as a
  reproduction rather than commentary.
- Public uploads require config opt-in **and** explicit per-request confirmation.
- Every approval, override, and upload is written to an audit log.

What it deliberately does **not** do:

- Scrape manhwa sites. Material only enters by your upload.
- Remove watermarks.
- Upload anything you have not approved.
- Train models on your material.

## Testing

```bash
.venv/bin/python -m pytest -m "not slow"   # fast: units + API (~25s)
.venv/bin/python -m pytest                 # everything, incl. real renders
.venv/bin/python -m ruff check .           # lint
```

The slow tests run FFmpeg for real and assert on the resulting file: dimensions,
codecs, duration matching the narration, and caption pixels actually present in
the frame.

## Project layout

```
app/
  config.py         settings; persists secret + Fernet keys
  constants.py      enums, beat structure, timing budgets
  models.py         SQLAlchemy models (15 tables)
  schemas.py        request/response validation
  deps.py           session auth, ownership guards
  security.py       scrypt hashing, Fernet encryption
  main.py           FastAPI app
  routers/          auth, credentials, projects, pipeline, publish
  services/
    ingest.py       upload handling + content sniffing
    analysis.py     story extraction (rules | LLM | BYOK)
    script.py       five-beat script generation
    tts.py          espeak | http | null | BYOK providers
    providers.py    BYOK vendor adapters + model discovery
    credentials.py  encrypted key storage
    resolver.py     picks BYOK vs env vs offline per stage
    timeline.py     scene planning, subtitle timing
    render.py       FFmpeg pipeline
    encoders.py     CPU/GPU encoder detection and flags
    policy.py       rights + transformative-use gates
    quality.py      pre-publication checks
    pipeline.py     stage orchestration
    publish.py      upload flow
    youtube.py      Data/Analytics API (+ dry run)
    storage.py      content-addressed local storage
scripts/
  make_fixtures.py  synthetic test panels
  seed_demo.py      demo project
  worker.py         standalone render worker
docs/               architecture, API, operations, PRD
tests/              units, API, end-to-end
```

## Interface

Neobrutalism with a pastel palette: thick black borders, hard offset shadows,
flat pastel fills, buttons that visibly sink when pressed. Eight colour-coded
steps with a jump-to nav.

All text is a single near-black ink and every pastel sits behind it as a
background, so **every combination clears WCAG 2.1 AAA (7:1)** — the lowest is
8.4:1. Those ratios are computed from the CSS variables in a test, so a future
colour tweak cannot quietly break readability.

Built for weak hardware: no framework, no build step, no web fonts, no CDN. No
blur effects, and transitions only touch `transform` and `box-shadow` so they
never trigger layout. Long lists use `content-visibility` to skip off-screen
work. Async actions are guarded against double-submit and always show a spinner
with a verb, because a slow machine should never look frozen.

Details and the full contrast table: [docs/UI.md](docs/UI.md).

## GPU rendering (optional)

Encoding is the slowest stage, and the only one a GPU meaningfully accelerates.
Pick an encoder per render in the UI (**6. Render → Encoder**), or set a default
with `MS_VIDEO_ENCODER=auto`.

Supported: `libx264` (CPU, always works), NVENC (NVIDIA), Quick Sync (Intel),
VAAPI (AMD/Intel on Linux), VideoToolbox (Apple).

```bash
curl -s localhost:8000/api/encoders | jq .   # what this machine can actually do
```

Detection runs a real one-frame encode rather than trusting `ffmpeg -encoders`,
which lists `h264_nvenc` whether or not a GPU exists. An unavailable GPU never
fails a render: it falls back to CPU and records why, in the job and the UI.

Expect 5–15x faster encoding on a GPU, with 10–30% larger files at similar
quality. Image prep, Ken Burns, and subtitles stay on the CPU and become the new
bottleneck. Details in [docs/GPU.md](docs/GPU.md).

## Bring your own key (BYOK)

Optional. The app runs fully offline without any keys, using rule-based analysis
and espeak-ng narration.

Supply your own API keys to improve quality. Log in, open **Kunci AI kamu
(BYOK)**, pick a provider, paste the key, then press **Tes & ambil daftar model**
— the model list is fetched from your key, so you only see models that key can
actually reach. Choose one and save.

- **LLM** (analysis, highlights, script rewriting): OpenAI, Anthropic, Google AI
  Studio, OpenRouter, Groq, DeepSeek, Mistral, Together AI, xAI, or any
  OpenAI-compatible endpoint (Ollama, LM Studio, vLLM, LiteLLM).
- **TTS** (narration): OpenAI Speech, ElevenLabs, or any `/audio/speech` endpoint.

Keys are encrypted at rest with Fernet, never returned by the API, and never
logged. The panel always states which provider each stage will actually use, so
you know whether a render hits your paid key or the offline engine.

Details, security model, and troubleshooting: [docs/BYOK.md](docs/BYOK.md).

## Known limitations

Being direct about what this does not do:

- **Single workspace per user.** Multi-channel is P2 in the PRD.
- **Inline rendering.** Renders run in a FastAPI background task by default. A
  standalone worker (`scripts/worker.py`) exists, but there is no Redis queue,
  so concurrency is limited to one process.
- **No scheduling UI.** The API accepts `scheduled_at`, but there is no calendar
  view yet (PRD FR-11, P1).
- **espeak voice quality.** Fine for review, not for a published channel.
- **Rules-based summarising is literal.** It compresses your sentences rather
  than genuinely rewriting them, so expect a similarity warning around 25–35%.
  Adding an LLM key (BYOK) fixes this.
- **No spend tracking for BYOK.** The app caps input size and only calls a
  provider when you ask it to, but it cannot tell you what a run cost. Set limits
  in your provider's console.
- **BYOK keys are only as safe as `data/`.** They are encrypted, but the key file
  sits beside the database by default. Not a secrets manager — see
  [docs/BYOK.md](docs/BYOK.md).
- **Local storage only.** The storage layer mirrors an S3 interface, but only the
  filesystem backend is implemented.
- **Not hardened for public exposure.** No rate limiting on login, no CSRF
  tokens, no TLS. Keep it on `127.0.0.1` or behind a reverse proxy.
- **Analytics needs a live upload.** Dry-run mode reports "no data" rather than
  inventing numbers.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how the pipeline fits together
- [docs/BYOK.md](docs/BYOK.md) — bring your own key: setup and security model
- [docs/TTS_OPTIONS.md](docs/TTS_OPTIONS.md) — TTS provider comparison and voice-continuity rules
- [docs/GPU.md](docs/GPU.md) — CPU/GPU encoding, requirements, troubleshooting
- [docs/UI.md](docs/UI.md) — design language, contrast table, UX decisions
- [docs/AGENT.md](docs/AGENT.md) — driving the whole pipeline from an AI agent
- [docs/API.md](docs/API.md) — every endpoint with examples
- [CHANGELOG.md](CHANGELOG.md) — what changed in each release
- [docs/OPERATIONS.md](docs/OPERATIONS.md) — running, troubleshooting, backups
- [docs/YOUTUBE_SETUP.md](docs/YOUTUBE_SETUP.md) — OAuth configuration
- [docs/COPYRIGHT.md](docs/COPYRIGHT.md) — rights model in detail
- [docs/PRD.md](docs/PRD.md) — original product requirements

## License

Private project. All rights reserved.
