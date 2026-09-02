> **RIGHTS POLICY NOTE:** Rights fields remain part of asset/audit payloads. With
> the default `MS_REQUIRE_RIGHTS_DECLARATION=false`, rights findings are non-blocking;
> `rights.*` becomes blocking only when enforcement is intentionally enabled.

# API reference

Base URL: `http://127.0.0.1:8000`. Interactive docs at `/docs` (Swagger) and
`/redoc`.

All `/api/projects/*` routes require an authenticated session cookie. A project
belonging to another user returns **404**, not 403 — the API does not confirm
that an id exists.

Errors use `{"detail": "human readable message"}`. Validation failures are 422
with the offending field named.

## Suwayomi source connector

Suwayomi runs as an optional localhost sidecar; agents still talk only to ManhwaShorts.

- `GET /api/sources/suwayomi/status` — sidecar health, installed/searchable source counts, and `needs_extension_setup`.
- `POST /api/sources/suwayomi/search` — search installed Suwayomi sources by title, optionally constrained by language or source id.
- `POST /api/projects/{project_id}/sources/suwayomi/import` — resolve an exact title and chapter range, fetch ordered chapter pages, and ingest them as normal project image assets.

Example import body:

```json
{
  "title": "Infinite Mage",
  "chapter_from": 20,
  "chapter_to": 25,
  "language": "en"
}
```

Decimal chapters inside the requested range are retained in reading order. The import is idempotent by source provenance + page identity, refuses ambiguous equally suitable sources with HTTP 409, and refuses to mutate a corpus after vision analysis exists. Rights default to undeclared unless a normal `rights` declaration is supplied in the request.

Suwayomi itself intentionally ships no default online extensions. A fresh sidecar can therefore be healthy while reporting `searchable_sources: 0` and `needs_extension_setup: true`.

## Conventions

| Code | Meaning |
|---|---|
| 200 / 201 | Success |
| 401 | Not authenticated, or session invalid/expired |
| 404 | Not found, or not yours |
| 409 | Conflict (duplicate email, no output file) |
| 410 | Rendered file no longer on disk |
| 422 | Validation failure, or a pipeline/policy refusal |
| 503 | A required optional sidecar/external dependency is unavailable |

## System

### `GET /api/health`

Reports whether this machine can actually render.

```json
{
  "status": "ok",
  "version": "1.0.0",
  "environment": "local",
  "ffmpeg": true,
  "tts_provider": "espeak",
  "llm_provider": "rules",
  "youtube_enabled": false,
  "problems": []
}
```

`status` is `degraded` when `problems` is non-empty (missing FFmpeg, missing
subtitle font, an FFmpeg build without `zoompan` or `libass`).

### `GET /api/voices`

```json
{"provider": "espeak", "voices": [{"id": "en", "label": "American English (espeak)"}, {"id": "id", "label": "Indonesian (espeak)"}]}
```

### `GET /api/encoders`

Which video encoders work on this machine. Unauthenticated: static machine
capability, no user data. Conceptual guide in [GPU.md](GPU.md).

Each backend is probed by encoding one real frame, because every FFmpeg build
lists `h264_nvenc` whether or not a GPU is present. Cached per process.

```json
{
  "configured": "auto",
  "gpu_available": false,
  "active": {
    "encoder": "cpu", "label": "CPU (libx264)", "codec": "libx264",
    "hardware": false, "requested": "auto", "fell_back": false,
    "reason": "no working GPU encoder found; using CPU"
  },
  "encoders": [
    {"key": "nvenc", "label": "NVIDIA GPU (NVENC)", "codec": "h264_nvenc",
     "hardware": true, "available": false,
     "detail": "NVIDIA driver not loadable. Install the driver, or check nvidia-smi works",
     "notes": "Needs an NVIDIA GPU (GTX 900+) with a driver the FFmpeg build can load."}
  ]
}
```

`detail` is the useful field when a backend is unavailable: it carries the actual
FFmpeg failure, not a guess.

## BYOK credentials

Bring your own key for the AI stages. Conceptual guide in [BYOK.md](BYOK.md).

A stored key is **never** returned by any route. Responses carry `key_hint`
(last four characters) so you can tell two keys apart.

### `GET /api/credentials/providers`

Supported providers, for building the form. Unauthenticated: static metadata,
no user data.

```json
{
  "llm": [
    {"key": "openai", "label": "OpenAI", "kind": "llm",
     "default_base_url": "https://api.openai.com/v1",
     "console_url": "https://platform.openai.com/api-keys",
     "custom_endpoint": false, "notes": ""}
  ],
  "tts": [{"key": "elevenlabs", "label": "ElevenLabs", "kind": "tts", "...": "..."}]
}
```

### `POST /api/credentials/test`

Verify a key and fetch its models **without saving**. A rejected key returns
`200` with `ok: false` — it is an expected outcome, not a server error.

```bash
curl -s -b cookies.txt -X POST localhost:8000/api/credentials/test \
  -H 'Content-Type: application/json' \
  -d '{"kind":"llm","provider":"openai","api_key":"sk-..."}'
```

```json
{"ok": true, "message": "key accepted, 42 model(s) available",
 "models": [{"id": "gpt-4o-mini", "label": "gpt-4o-mini"}]}
```

### `POST /api/credentials`

Save a key. It is verified against the provider first: a key that cannot list
models is rejected with `400` rather than stored to fail later mid-render.

| Field      | Required | Notes                                                |
|------------|----------|------------------------------------------------------|
| `kind`     | yes      | `llm` or `tts`                                       |
| `provider` | yes      | key from the catalogue                               |
| `api_key`  | yes      | min 8 chars; encrypted immediately, never echoed     |
| `base_url` | for custom | required when `custom_endpoint` is true            |
| `model`    | no       | must be one the key offers; unset = not used yet     |
| `label`    | no       | defaults to the provider label                       |
| `verify`   | no       | default `true`; `false` stores as `unverified`        |

Re-posting the same `kind` + `provider` **replaces** the key rather than adding a
duplicate, so rotating a key is the same call as adding one.

```json
{
  "id": "9f2c...", "kind": "llm", "provider": "openai", "label": "OpenAI",
  "key_hint": "...4f2a", "base_url": "https://api.openai.com/v1",
  "model": "gpt-4o-mini",
  "available_models": [{"id": "gpt-4o-mini", "label": "gpt-4o-mini"}],
  "status": "verified", "status_message": "key accepted, 42 model(s) available",
  "verified_at": "2026-08-01T02:10:00Z", "last_used_at": null,
  "is_default": true, "is_active": true, "created_at": "2026-08-01T02:10:00Z"
}
```

`400` — key rejected, unknown provider, missing base URL for a custom endpoint,
or a model the key does not offer.

### `GET /api/credentials`

All credentials for the caller's workspace, newest first. Keys never included.

### `GET /api/credentials/active`

Which provider each stage will really use, and why. Use this to confirm whether
a render will hit your paid key or the offline engine.

```json
{
  "llm": {"source": "byok", "provider": "openai", "model": "gpt-4o-mini",
          "label": "OpenAI", "credential_id": "9f2c...",
          "reason": "using your OpenAI key (...4f2a)"},
  "tts": {"source": "local", "provider": "espeak", "model": "", "label": "espeak-ng",
          "credential_id": "", "reason": "no speech key configured; using offline espeak-ng"}
}
```

`source` is `byok`, `env`, or `local`.

### `POST /api/credentials/{id}/refresh`

Re-fetch the model list with the stored key. Doubles as a health check: a key
revoked upstream comes back `status: invalid`. If your selected model has been
retired, the selection is cleared and `status_message` says so.

### `POST /api/credentials/{id}/model`

```bash
curl -s -b cookies.txt -X POST localhost:8000/api/credentials/$ID/model \
  -H 'Content-Type: application/json' -d '{"model":"gpt-4o"}'
```

`400` if the key does not offer that model — the app never substitutes a
different (billable) model silently.

### `POST /api/credentials/{id}/default`

Make this credential the active one for its capability. Requires `verified`.

### `DELETE /api/credentials/{id}`

Deletes the row and its ciphertext. If it was the default, another verified key
for the same capability is promoted; otherwise the stage reverts to offline.

## Auth

### `POST /api/auth/register`

```bash
curl -c cookies.txt -X POST localhost:8000/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"atleast8chars","name":"You"}'
```

201 with `{id, email, name}` and sets the session cookie. Creates a default
workspace. 409 if the email is taken. Note that reserved TLDs such as `.test` are
rejected by the email validator.

### `POST /api/auth/login`

Same body minus `name`. Wrong password and unknown account return the **same**
401 message, so the endpoint does not reveal which accounts exist.

### `POST /api/auth/logout` · `GET /api/auth/me` · `GET /api/auth/workspace`

Logout clears the cookie. The other two return the current user / workspace.

## Projects

### `POST /api/projects`

```json
{
  "title": "Rian Chapter 12",
  "manhwa_title": "Peringkat Terakhir",
  "chapter": "12",
  "content_type": "chapter_recap",
  "language": "en",
  "spoiler_level": "medium",
  "narration_style": "dramatic",
  "target_duration": 60,
  "voice_id": "the-explainer-american",
  "cta_text": "Komentar di bawah.",
  "banned_words": [],
  "pronunciations": {"Rian": "Ri-an"}
}
```

Only `title` is required. `target_duration` must be 10–90 and defaults to 55. Normal final production is accepted only at 50–60 seconds.

Enums: `content_type` ∈ `chapter_recap | character_profile | fun_facts | theory |
cliffhanger`; `spoiler_level` ∈ `minimal | medium | full`; `narration_style` ∈
`dramatic | casual | mysterious | fast | informative`.

### `GET /api/projects` · `GET /api/projects/{id}` · `PATCH /api/projects/{id}`

List (add `?include_archived=true`), fetch, and partial update.

`status` moves through: `draft → generating → review → rendering → ready →
scheduled → published`, or `failed`.

### `POST /api/projects/{id}/duplicate`

Copies settings and assets (with their rights declarations) into a new draft.
Assets are content-addressed, so blobs are shared rather than duplicated.

### `DELETE /api/projects/{id}`

Deletes the project and every blob no other project references.

```json
{"detail": "Project deleted. 7 stored file(s) removed."}
```

## Source material

### `POST /api/projects/{id}/assets/text`

```json
{
  "text": "Bab ini dibuka dengan…",
  "title": "recap_ch12.txt",
  "rights": {
    "rights_owner": "You",
    "license_type": "owned",
    "source_name": "Written in-house",
    "permission_reference": "",
    "attribution": "You",
    "declared": true
  }
}
```

Minimum 40 characters. `license_type` ∈ `owned | licensed | permission_granted |
public_domain | creative_commons | unknown`.

**`declared: true` alone is not enough.** Without `rights_owner` and a licence
other than `unknown`, the asset stays `undeclared` and will block rendering.

### `POST /api/projects/{id}/assets/upload`

Multipart. One rights declaration applies to all files in the request.

```bash
curl -b cookies.txt -X POST localhost:8000/api/projects/$PJ/assets/upload \
  -F "files=@panel01.jpg" -F "files=@panel02.jpg" \
  -F "rights_owner=You" -F "license_type=owned" -F "declared=true"
```

Accepts JPG, PNG, WebP, TXT, MD, PDF, DOCX. Max 25 MB each. Images are re-parsed
with Pillow, so a renamed non-image is rejected with 422 regardless of its
extension or declared MIME type.

### `PATCH /api/projects/{id}/assets/{asset_id}/rights`

Correct a declaration after upload. Same body as the `rights` object above.

### `GET /api/projects/{id}/assets` · `DELETE …/assets/{asset_id}`

## Pipeline

### `POST /api/projects/{id}/draft?seed=42`

Runs analysis → script → voice-over → timeline in one call. This is the "draft in
under 10 minutes" path. It stops before approval and rendering.

```json
{
  "script_id": "7f6ad5c4…",
  "script_version": 1,
  "estimated_duration": 43.92,
  "audio_duration": 48.37,
  "segments": 5,
  "scenes": 9,
  "cues": 16,
  "warnings": []
}
```

`seed` makes generation reproducible — useful for tests and for comparing hooks.

### Analysis — `POST` / `GET` / `PATCH /api/projects/{id}/analysis`

`POST` extracts story facts, replacing any previous analysis. `PATCH` applies your
corrections and sets `edited_by_user: true`.

422 with "no text material to analyse" if the project has no text asset.

### Script

`POST /api/projects/{id}/script` — generate the next version.

```json
{"keep_locked": true, "hook_count": 3, "seed": 42}
```

`GET …/script` returns the latest; `GET …/scripts` returns all versions.

```json
{
  "version": 1,
  "sections": [
    {"section": "hook", "text": "…", "locked": false,
     "estimated_duration": 4.3, "citations": [0]}
  ],
  "hook_options": ["…", "…", "…"],
  "estimated_duration": 43.92,
  "approved_at": null
}
```

`PATCH …/script` applies edits. **Editing clears approval** — the review cannot be
bypassed. Unknown section names are rejected.

`POST …/script/approve` marks it reviewed. 422 if any section is empty or a
blocking script warning is unresolved.

Locked sections survive regeneration when `keep_locked` is true.

### Voice-over — `POST` / `GET /api/projects/{id}/voice`

```json
{"speed": 1.0, "provider": null}
```

`speed` 0.5–2.0. Replaces previous audio and deletes the old files. 422 if no
script exists.

### Timeline — `POST` / `GET /api/projects/{id}/timeline`

`POST` derives scenes and cues from the current voice-over. `PATCH
…/timeline/{scene_id}` edits one scene:

```json
{"asset_id": "…", "focus_x": 0.5, "focus_y": 0.35, "effect": "pan_left"}
```

Effects: `kenburns_in | kenburns_out | pan_right | pan_left | static`.
`focus_x`/`focus_y` are 0–1 and drive the 9:16 crop. An asset from another project
is rejected; `end_time <= start_time` is rejected.

### Subtitles

`GET /api/projects/{id}/subtitles` — cues.
`PATCH …/subtitles/{cue_id}` — edit text or timing; sets `edited_by_user`.
`GET …/subtitles.srt` — SRT download.

### Quality

`POST /api/projects/{id}/quality` runs every gate and stores the results.

```json
{
  "total": 7, "errors": 0, "warnings": 1, "can_publish": true,
  "error_codes": [],
  "warning_codes": ["policy.high_similarity"],
  "checks": [{"code": "…", "severity": "warning", "message": "…", "passed": false}]
}
```

`POST …/quality/override` accepts a **warning** with a recorded reason:

```json
{"code": "policy.high_similarity", "reason": "sudah saya periksa manual"}
```

Reason must be ≥5 characters. Attempting to override an `error` returns 422.

Common blocking codes include `policy.not_transformative`,
`script.not_approved`, `audio.missing`, `timeline.no_scenes`,
`subtitle.overlap`, `duration.too_long`, `render.wrong_aspect`.

### Render

`POST /api/projects/{id}/render` with `{"kind": "final"}` or `"preview"`.

| Field | Default | Notes |
|---|---|---|
| `kind` | `final` | `preview` skips quality gating and encodes fast |
| `encoder` | `auto` | `auto \| cpu \| nvenc \| qsv \| vaapi \| videotoolbox` |

A **final** render requires quality checks to pass; blocking errors return 422
listing them. Returns a job immediately and renders in the background.

`encoder` is validated against a strict pattern, so a typo like `nvnec` returns
422 rather than silently becoming a CPU render. The choice is stored on the job
and resolved by whichever machine does the encoding, which may be a separate
worker. See [GPU.md](GPU.md).

`GET …/render/{job_id}` to poll:

```json
{
  "status": "running", "progress": 65, "stage": "burning subtitles",
  "duration": 0, "error_code": "", "error_message": ""
}
```

`status` ∈ `queued | running | succeeded | failed`. On success you also get
`duration`, `width`, `height`, `checksum`, `output_key`, `subtitle_key`, plus the
encoder that actually ran:

```json
{
  "encoder_requested": "nvenc", "encoder": "cpu",
  "encoder_hardware": false, "encoder_fell_back": true,
  "encoder_reason": "NVIDIA GPU (NVENC) unavailable (NVIDIA driver not loadable…); fell back to CPU"
}
```

`encoder_requested` and `encoder` differ exactly when a GPU was unavailable. The
render still succeeds — a missing GPU slows a render, it does not fail one.

`POST …/render/{job_id}/retry` queues a fresh attempt and preserves the failed
job for the audit trail.

`GET /api/projects/{id}/download/{job_id}` streams the MP4. 409 if the render did
not succeed; 410 if the file has since been removed.

## Local-agent publish orchestration

`POST /api/projects/{id}/run` accepts `until: "publish"` and resumes already-valid stages instead of rebuilding them. For an explicit user request to create and upload a recap, an agent may send `approval_mode: "trusted_agent"` with `confirm_publish_intent: true`; that approval is persisted as trusted-agent approval with `human_review_performed: false`. Without that exact publish intent, an unapproved script remains a stop boundary.

Rendering remains asynchronous. A first `/run` call may queue a render and return; poll `GET /api/projects/{id}/status` and call `/run` again after the render succeeds. The publish pass uses the selected `youtube_account_id`, generated metadata when fields are empty, and request visibility (default `private`).

## Publishing

### Browser accounts

- `GET /api/youtube/browser/status?account_id=<id>` — Chrome/browser availability plus authentication state and the effective trust-defaults mode.
- `GET /api/youtube/browser/accounts` — default account plus all isolated persistent Chrome profiles.
- `POST /api/youtube/browser/accounts` — create account metadata/profile. Body: `account_id`, optional `label`, optional `trust_channel_defaults`.
- `PATCH /api/youtube/browser/accounts/{account_id}` — update `label`, `make_default`, and/or `trust_channel_defaults`. Send `null` for `trust_channel_defaults` to inherit the global fallback.

Google login itself is interactive in the selected persistent Chrome profile; credentials/cookies are not returned by these APIs. See [YOUTUBE_SETUP.md](YOUTUBE_SETUP.md).

### `GET /api/projects/{id}/metadata`

Drafts a hook-first title, description, and tags from the approved script. The title prefers a concrete grounded event, reveal, decision, threat, or mystery and rejects generic clickbait fallbacks. Always editable. The description includes a rights notice automatically.

### `GET /api/projects/{id}/publish/readiness`

Returns quality summary plus browser/account readiness. A publish is ready only when the final render is current, blocking QC is clear, the selected browser is available, and that account is authenticated.

### `POST /api/projects/{id}/publish`

```json
{
  "youtube_account_id": "main",
  "video_title": "Dia Menemukan Kekuatan yang Seharusnya Tersegel | Peringkat Terakhir #shorts",
  "description": "…",
  "tags": ["manhwa", "shorts"],
  "privacy_status": "private",
  "scheduled_at": null,
  "trust_channel_defaults": null
}
```

Empty metadata fields fall back to generated metadata. `privacy_status` defaults to `private`; explicit `unlisted` and `public` are selected and verified in YouTube Studio. The legacy `confirm_public` request field is still accepted for compatibility but is ignored. Scheduled publishing currently fails closed with `manual_schedule` until that Studio flow has a dedicated acceptance test.

Before upload the server re-verifies the successful final render, artifact checksum/integrity, and blocking quality gates. Browser success is not inferred from clicking Publish: the publisher verifies the matching Content/Shorts row, rejects Draft, requires the requested visibility, and records the verified video ID.

Thumbnail upload is best-effort. The publisher attempts it during the upload flow and can verify/retry persistence after the video exists. A final thumbnail failure does not turn an otherwise verified video publish into a failed upload; the publication records `thumbnail_status`/`thumbnail_error` and instructs manual Studio correction.

### `POST /api/publications/{id}/retry`

Retries only a failed video publication, reusing the same Publication row, account ID, metadata, visibility, and final render. An already-uploaded publication is refused rather than uploaded twice.

### `POST /api/publications/{id}/thumbnail/retry`

The route is retained for API compatibility but currently returns a pipeline refusal: standalone thumbnail retry belonged to the archived Data API publisher. Browser publishing handles thumbnail attempts inside the Studio workflow, and `thumbnail_retry_url` is `null`.

### Analytics

`POST /api/publications/{id}/stats/sync` records that analytics are unavailable and returns `available: false`; browser publishing intentionally does not fetch YouTube analytics. `GET /api/publications/{id}/stats` returns any persisted snapshot history without fabricating new zeros.

## Agent publish/resume example

The compact agent-oriented path uses resumable `/run` orchestration:

```bash
BASE=http://127.0.0.1:8000

# Login/register, create $PJ, then ingest manual assets or Suwayomi pages.
PUBLISH='{"until":"publish","approval_mode":"trusted_agent","confirm_publish_intent":true,"youtube_account_id":"main","privacy_status":"private"}'

# First call advances valid stages and may queue the asynchronous render.
curl -sS -b ck.txt -X POST "$BASE/api/projects/$PJ/run" \
  -H 'Content-Type: application/json' \
  -d "$PUBLISH"

# Poll until render_status is succeeded/failed.
curl -sS -b ck.txt "$BASE/api/projects/$PJ/status"

# After a successful render, send the same request again; it resumes and publishes.
curl -sS -b ck.txt -X POST "$BASE/api/projects/$PJ/run" \
  -H 'Content-Type: application/json' \
  -d "$PUBLISH"
```

For manual editorial control, call analysis/script/approval/voice/timeline/quality/render endpoints separately, then `POST /publish`. The same resume identities and publish verification rules apply.
